from app.generation.adapters.conversation_store import ConversationStore
from app.generation.domain.messages import AssistantMessage, ToolCall
from app.generation.ports.model import ModelCallStatus
from app.generation.ports.tools import ToolExecutionResult


class ConversationJournal:
    """把 TurnJournal 端口适配到 MySQL ChatHistory。"""

    def __init__(self, store: ConversationStore) -> None:
        self._store = store

    async def append_user(self, message: str) -> None:
        await self._store.append_user(message)

    async def append_assistant(self, message: AssistantMessage, *, batch_id: str | None) -> None:
        await self._store.append_assistant(message.content, batch_id=batch_id)

    async def append_assistant_placeholder(self, *, batch_id: str) -> None:
        await self._store.append_assistant_placeholder(batch_id=batch_id)

    async def append_tool_call(self, call: ToolCall, *, batch_id: str) -> None:
        await self._store.append_tool_call(call, batch_id=batch_id)

    async def append_tool_result(
        self,
        call: ToolCall,
        result: ToolExecutionResult,
        *,
        batch_id: str,
    ) -> None:
        await self._store.append_tool_result(call, result, batch_id=batch_id)

    async def append_model_call(
        self,
        *,
        status: ModelCallStatus,
        model_name: str,
        duration_ms: int,
        input_tokens: int = 0,
        output_tokens: int = 0,
        error_type: str | None = None,
        error_summary: str | None = None,
        retry_count: int = 0,
    ) -> None:
        await self._store.append_model_call(
            status=status,
            model_name=model_name,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            error_type=error_type,
            error_summary=error_summary,
            retry_count=retry_count,
        )

    async def commit(self) -> None:
        await self._store.commit()
