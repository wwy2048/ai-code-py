import json
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChatHistory
from app.runtime.model import ToolCall
from app.runtime.tools import ToolExecutionResult


class MessageType(StrEnum):
    USER = 'user'
    AI = 'ai'
    TOOL = 'tool'


class MessageKind(StrEnum):
    USER = 'user'
    ASSISTANT = 'assistant'
    TOOL_CALL = 'tool_call'
    TOOL_RESULT = 'tool_result'
    COMPACTION = 'compaction'


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
                select(func.max(ChatHistory.sequence)).where(
                    ChatHistory.app_id == self._app_id
                )
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

    async def append_assistant(self, message: str) -> None:
        await self._append(
            message=message,
            message_type=MessageType.AI,
            message_kind=MessageKind.ASSISTANT,
            visible=True,
        )

    async def append_tool_call(self, call: ToolCall) -> None:
        await self._append(
            message=json.dumps(call.arguments, ensure_ascii=False),
            message_type=MessageType.TOOL,
            message_kind=MessageKind.TOOL_CALL,
            visible=False,
            tool_call_id=call.id,
            tool_name=call.name,
            payload={'arguments': call.arguments},
        )

    async def append_tool_result(self, call: ToolCall, result: ToolExecutionResult) -> None:
        await self._append(
            message=result.output,
            message_type=MessageType.TOOL,
            message_kind=MessageKind.TOOL_RESULT,
            visible=False,
            tool_call_id=call.id,
            tool_name=call.name,
            payload={
                'output': result.output,
                'input': result.public_input,
                'durationMs': result.duration_ms,
                'sizeBytes': result.size_bytes,
            },
            is_error=not result.ok,
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
    ) -> None:
        self._session.add(
            ChatHistory(
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
        )
