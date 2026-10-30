import json
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChatHistory, ModelCallLog
from app.redaction import sanitize_sensitive_text
from app.runtime.messages import (
    assistant_tool_calls_message,
    tool_result_message,
    tool_use_block,
)
from app.runtime.model import ModelCallStatus, ToolCall
from app.runtime.tools import ToolExecutionResult


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

    async def load_active_model_messages(self) -> list[dict[str, Any]]:
        """加载当前 App 仍生效的运行时消息，并投影为模型协议消息。"""
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

    async def append_assistant(self, message: str) -> None:
        await self._append(
            message=message,
            message_type=MessageType.AI,
            message_kind=MessageKind.ASSISTANT,
            visible=True,
        )

    async def append_tool_call(
        self,
        call: ToolCall,
        *,
        batch_id: str,
        assistant_text: str,
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
                "assistantText": assistant_text,
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
        output_tokens: int = 0,
        error_type: str | None = None,
        error_summary: str | None = None,
        retry_count: int = 0,
    ) -> None:
        self._session.add(
            ModelCallLog(
                app_id=self._app_id,
                turn_id=self._turn_id,
                model_name=model_name,
                call_kind=call_kind,
                status=status.value,
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
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
            active=True,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            is_error=is_error,
            is_delete=0,
        )
        self._session.add(record)
        return record


def project_model_messages(records: list[ChatHistory]) -> list[dict[str, Any]]:
    """保持工具调用与结果成对，并丢弃无法安全回放的不完整消息。"""
    pending_calls: dict[tuple[str | None, str | None], list[ChatHistory]] = {}
    result_by_call: dict[int, ChatHistory] = {}
    calls_by_batch: dict[tuple[str | None, str], list[ChatHistory]] = {}
    batch_text: dict[tuple[str | None, str], str] = {}

    for record in records:
        key = (record.turn_id, record.tool_call_id)
        if record.message_kind == MessageKind.TOOL_CALL and record.tool_call_id:
            pending_calls.setdefault(key, []).append(record)
            batch_id = (record.payload or {}).get("batchId")
            if isinstance(batch_id, str) and batch_id:
                indexed_batch_key = (record.turn_id, batch_id)
                calls_by_batch.setdefault(indexed_batch_key, []).append(record)
                assistant_text = (record.payload or {}).get("assistantText")
                if isinstance(assistant_text, str):
                    batch_text.setdefault(indexed_batch_key, assistant_text)
        elif record.message_kind == MessageKind.TOOL_RESULT and record.tool_call_id:
            calls = pending_calls.get(key)
            if calls:
                result_by_call[id(calls.pop())] = record

    projected: list[dict[str, Any]] = []
    emitted_batches: set[tuple[str | None, str]] = set()
    for record in records:
        if record.message_kind == MessageKind.TOOL_CALL:
            batch_id = (record.payload or {}).get("batchId")
            batch_key = (
                (record.turn_id, batch_id) if isinstance(batch_id, str) and batch_id else None
            )
            if batch_key is not None and batch_key in emitted_batches:
                continue
            calls = calls_by_batch[batch_key] if batch_key is not None else [record]
            completed_calls = [
                call for call in calls if call.tool_call_id and id(call) in result_by_call
            ]
            if not completed_calls:
                continue
            if batch_key is not None:
                emitted_batches.add(batch_key)
            tool_calls = []
            for call in completed_calls:
                arguments = (call.payload or {}).get("arguments")
                tool_calls.append(
                    tool_use_block(
                        tool_call_id=call.tool_call_id or "",
                        name=call.tool_name or "",
                        arguments=arguments if isinstance(arguments, dict) else {},
                    )
                )
            projected.append(
                assistant_tool_calls_message(
                    tool_calls,
                    content=batch_text.get(batch_key, "") if batch_key is not None else "",
                )
            )
            for call in completed_calls:
                result = result_by_call[id(call)]
                projected.append(
                    tool_result_message(
                        tool_call_id=call.tool_call_id or "",
                        name=call.tool_name,
                        content=result.message,
                        is_error=result.is_error,
                    )
                )
        elif record.message_kind == MessageKind.TOOL_RESULT:
            continue
        elif record.message_kind == MessageKind.USER:
            projected.append({"role": "user", "content": record.message})
        elif record.message_kind == MessageKind.ASSISTANT:
            projected.append({"role": "assistant", "content": record.message})
        elif record.message_kind == MessageKind.COMPACTION:
            projected.append({"role": "user", "content": f"[会话压缩摘要]\n{record.message}"})
        elif record.message_kind == "legacy":
            if record.message_type == MessageType.USER:
                projected.append({"role": "user", "content": record.message})
            elif record.message_type == MessageType.AI:
                projected.append({"role": "assistant", "content": record.message})
    return projected
