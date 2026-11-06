import json
from enum import StrEnum
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.generation.domain.messages import (
    AgentMessage,
    AssistantMessage,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from app.generation.ports.model import ModelCallStatus
from app.generation.ports.tools import ToolExecutionResult
from app.model_billing import calculate_cost_cny
from app.models import ChatHistory, ModelCallLog
from app.redaction import sanitize_sensitive_text


class MessageType(StrEnum):
    USER = "user"
    AI = "ai"
    TOOL = "tool"


class MessageKind(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    COMPACTION = "compaction"
    TURN_LIFECYCLE = "turn_lifecycle"


class ConversationStore:
    """封装运行时消息的语义化写入与稳定 sequence。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        app_id: int,
        user_id: int,
        turn_id: str,
    ) -> None:
        self._session = session
        self._app_id = app_id
        self._user_id = user_id
        self._turn_id = turn_id
        self._next_sequence: int | None = None

    async def _take_sequence(self) -> int:
        if self._next_sequence is None:
            current = await self._session.scalar(
                select(func.max(ChatHistory.sequence)).where(ChatHistory.app_id == self._app_id)
            )
            self._next_sequence = int(current or 0) + 1
        current = self._next_sequence
        self._next_sequence += 1
        return current

    async def append_user(self, message: str) -> None:
        await self._append(
            message=message,
            message_type=MessageType.USER,
            message_kind=MessageKind.USER,
            visible=True,
        )

    async def append_internal_user(self, message: str) -> None:
        """保存仅用于模型修复的内部提示，不进入前端可见历史。"""
        await self._append(
            message=message,
            message_type=MessageType.USER,
            message_kind=MessageKind.USER,
            visible=False,
        )

    async def load_active_model_messages(self) -> list[AgentMessage]:
        """加载当前 App 仍生效的运行时消息，并投影为类型化模型消息。"""
        records = await self.load_active_records()
        summary_records = [
            record for record in records if record.message_kind == MessageKind.COMPACTION
        ]
        other_records = [
            record for record in records if record.message_kind != MessageKind.COMPACTION
        ]
        return project_model_messages([*summary_records, *other_records])

    async def load_active_records(self) -> list[ChatHistory]:
        """按稳定顺序加载当前 App 仍生效的原始运行时记录。"""
        return list(
            (
                await self._session.scalars(
                    select(ChatHistory)
                    .where(
                        ChatHistory.app_id == self._app_id,
                        ChatHistory.is_delete == 0,
                        ChatHistory.active.is_(True),
                    )
                    .order_by(
                        ChatHistory.sequence.asc(),
                        ChatHistory.create_time.asc(),
                        ChatHistory.id.asc(),
                    )
                )
            ).all()
        )

    async def latest_provider_usage_tokens(self) -> int:
        """读取最近一次成功模型调用的 provider token usage。"""
        latest = await self._session.scalar(
            select(ModelCallLog)
            .where(
                ModelCallLog.app_id == self._app_id,
                ModelCallLog.status == ModelCallStatus.SUCCESS.value,
                ModelCallLog.call_kind == "generation",
            )
            .order_by(ModelCallLog.id.desc())
            .limit(1)
        )
        if latest is None:
            return 0
        return latest.input_tokens + latest.output_tokens

    async def append_compaction_summary(
        self,
        summary: str,
        *,
        compacted_records: list[ChatHistory],
        payload: dict[str, Any] | None = None,
    ) -> ChatHistory:
        """追加隐藏且 active 的摘要，并把被覆盖记录标记为非 active。"""
        record = await self._append(
            message=summary,
            message_type=MessageType.AI,
            message_kind=MessageKind.COMPACTION,
            visible=False,
            payload=payload,
        )
        await self._session.flush()
        for compacted in compacted_records:
            compacted.active = False
            compacted.compacted_by = record.id
        return record

    async def rollback(self) -> None:
        await self._session.rollback()

    async def append_assistant(
        self,
        message: str,
        *,
        batch_id: str | None = None,
    ) -> None:
        """保存一轮模型文本，并用 batch_id 关联同一轮的工具调用。"""
        await self._append(
            message=message,
            message_type=MessageType.AI,
            message_kind=MessageKind.ASSISTANT,
            visible=True,
            payload={"batchId": batch_id, "phase": "assistant_text"} if batch_id else None,
        )

    async def append_assistant_placeholder(self, *, batch_id: str) -> None:
        """写入隐藏的中断占位，保证后续模型消息配对完整。"""
        await self._append(
            message="[本轮已被用户中断，不再基于不完整的内容作答]",
            message_type=MessageType.AI,
            message_kind=MessageKind.ASSISTANT,
            visible=False,
            payload={"batchId": batch_id, "phase": "interrupted"},
        )

    async def append_turn_lifecycle(
        self,
        phase: str,
        *,
        outcome: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ChatHistory:
        """追加隐藏的 Turn 生命周期事实，不进入模型上下文。"""
        data = {"phase": phase, **({"outcome": outcome} if outcome else {}), **(payload or {})}
        return await self._append(
            message=phase,
            message_type=MessageType.AI,
            message_kind=MessageKind.TURN_LIFECYCLE,
            visible=False,
            active=False,
            payload=data,
        )

    async def append_tool_call(
        self,
        call: ToolCall,
        *,
        batch_id: str,
    ) -> None:
        await self._append(
            message=json.dumps(call.arguments, ensure_ascii=False),
            message_type=MessageType.TOOL,
            message_kind=MessageKind.TOOL_CALL,
            visible=False,
            tool_call_id=call.id,
            tool_name=call.name,
            payload={
                "arguments": call.arguments,
                "batchId": batch_id,
            },
        )

    async def append_tool_result(
        self,
        call: ToolCall,
        result: ToolExecutionResult,
        *,
        batch_id: str,
    ) -> None:
        await self._append(
            message=result.output,
            message_type=MessageType.TOOL,
            message_kind=MessageKind.TOOL_RESULT,
            visible=False,
            tool_call_id=call.id,
            tool_name=call.name,
            payload={
                "output": result.output,
                "input": result.public_input,
                "durationMs": result.duration_ms,
                "sizeBytes": result.size_bytes,
                "batchId": batch_id,
            },
            is_error=not result.ok,
        )

    async def append_model_call(
        self,
        *,
        status: ModelCallStatus,
        model_name: str,
        call_kind: str = "generation",
        duration_ms: int,
        input_tokens: int = 0,
        cached_input_tokens: int = 0,
        output_tokens: int = 0,
        error_type: str | None = None,
        error_summary: str | None = None,
        retry_count: int = 0,
    ) -> None:
        self._session.add(
            ModelCallLog(
                app_id=self._app_id,
                user_id=self._user_id,
                turn_id=self._turn_id,
                model_name=model_name,
                call_kind=call_kind,
                status=status.value,
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                cost_cny=calculate_cost_cny(
                    model_name,
                    input_tokens=input_tokens,
                    cached_input_tokens=cached_input_tokens,
                    output_tokens=output_tokens,
                ),
                error_type=error_type,
                error_summary=sanitize_sensitive_text(error_summary),
                retry_count=retry_count,
            )
        )

    async def commit(self) -> None:
        await self._session.commit()

    async def _append(
        self,
        *,
        message: str,
        message_type: MessageType,
        message_kind: MessageKind,
        visible: bool,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
        payload: dict[str, Any] | None = None,
        is_error: bool = False,
        active: bool = True,
    ) -> ChatHistory:
        record = ChatHistory(
            message=message,
            message_type=message_type.value,
            app_id=self._app_id,
            user_id=self._user_id,
            turn_id=self._turn_id,
            sequence=await self._take_sequence(),
            message_kind=message_kind.value,
            payload=payload,
            visible=visible,
            active=active,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            is_error=is_error,
            is_delete=0,
        )
        self._session.add(record)
        return record


def _record_batch_key(record: ChatHistory) -> tuple[str | None, str] | None:
    batch_id = (record.payload or {}).get("batchId")
    if isinstance(batch_id, str) and batch_id:
        return record.turn_id, batch_id
    return None


def _call_arguments(record: ChatHistory) -> dict[str, Any]:
    arguments = (record.payload or {}).get("arguments")
    return cast(dict[str, Any], arguments) if isinstance(arguments, dict) else {}


def project_model_messages(records: list[ChatHistory]) -> list[AgentMessage]:
    """把有序事件投影为类型化模型消息，并保持 assistant 文本与工具批次配对。"""
    result_by_call: dict[tuple[str | None, str], ChatHistory] = {}
    calls_by_batch: dict[tuple[str | None, str], list[ChatHistory]] = {}
    assistant_text_by_batch: dict[tuple[str | None, str], str] = {}

    for record in records:
        if record.message_kind == MessageKind.TURN_LIFECYCLE:
            continue
        if record.message_kind == MessageKind.TOOL_RESULT and record.tool_call_id:
            result_by_call[(record.turn_id, record.tool_call_id)] = record
        elif record.message_kind == MessageKind.TOOL_CALL and record.tool_call_id:
            batch_key = _record_batch_key(record)
            if batch_key is not None:
                calls_by_batch.setdefault(batch_key, []).append(record)
        elif record.message_kind == MessageKind.ASSISTANT:
            batch_key = _record_batch_key(record)
            if batch_key is not None and record.message.strip():
                previous = assistant_text_by_batch.get(batch_key)
                assistant_text_by_batch[batch_key] = (
                    f"{previous}\n\n{record.message}" if previous else record.message
                )

    def completed_calls(calls: list[ChatHistory]) -> list[ChatHistory]:
        return [
            call
            for call in calls
            if call.tool_call_id and (call.turn_id, call.tool_call_id) in result_by_call
        ]

    projectable_batches = {
        batch_key for batch_key, calls in calls_by_batch.items() if completed_calls(calls)
    }

    projected: list[AgentMessage] = []
    emitted_batches: set[tuple[str | None, str]] = set()
    for record in records:
        if record.message_kind == MessageKind.TURN_LIFECYCLE:
            continue
        if record.message_kind == MessageKind.TOOL_CALL:
            batch_key = _record_batch_key(record)
            if batch_key is not None:
                if batch_key in emitted_batches or batch_key not in projectable_batches:
                    continue
                emitted_batches.add(batch_key)
                batch_calls = completed_calls(calls_by_batch[batch_key])
                content = assistant_text_by_batch.get(batch_key, "")
                if not content:
                    content = str((batch_calls[0].payload or {}).get("assistantText") or "")
                projected.append(
                    AssistantMessage(
                        content=content,
                        tool_calls=tuple(
                            ToolCall(
                                id=call.tool_call_id or "",
                                name=call.tool_name or "",
                                arguments=_call_arguments(call),
                            )
                            for call in batch_calls
                        ),
                    )
                )
                for call in batch_calls:
                    result = result_by_call[(call.turn_id, call.tool_call_id or "")]
                    projected.append(
                        ToolResultMessage(
                            tool_call_id=call.tool_call_id or "",
                            name=call.tool_name,
                            content=result.message,
                            is_error=result.is_error,
                        )
                    )
                continue
            legacy_result = result_by_call.get((record.turn_id, record.tool_call_id or ""))
            if legacy_result is None or not record.tool_call_id:
                continue
            projected.append(
                AssistantMessage(
                    content=str((record.payload or {}).get("assistantText") or ""),
                    tool_calls=(
                        ToolCall(
                            id=record.tool_call_id,
                            name=record.tool_name or "",
                            arguments=_call_arguments(record),
                        ),
                    ),
                )
            )
            projected.append(
                ToolResultMessage(
                    tool_call_id=record.tool_call_id,
                    name=record.tool_name,
                    content=legacy_result.message,
                    is_error=legacy_result.is_error,
                )
            )
        elif record.message_kind == MessageKind.TOOL_RESULT:
            continue
        elif record.message_kind == MessageKind.USER:
            projected.append(UserMessage(content=record.message))
        elif record.message_kind == MessageKind.ASSISTANT:
            batch_key = _record_batch_key(record)
            if batch_key in projectable_batches:
                # 该轮文本已合并到紧随其后的 assistant tool_calls 消息中。
                continue
            projected.append(AssistantMessage(content=record.message))
        elif record.message_kind == MessageKind.COMPACTION:
            projected.append(UserMessage(content=f"[会话压缩摘要]\n{record.message}"))
        elif record.message_kind == "legacy":
            if record.message_type == MessageType.USER:
                projected.append(UserMessage(content=record.message))
            elif record.message_type == MessageType.AI:
                projected.append(AssistantMessage(content=record.message))
    return projected
