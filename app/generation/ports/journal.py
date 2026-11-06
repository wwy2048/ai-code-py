from typing import Protocol

from app.generation.domain.messages import AssistantMessage, ToolCall
from app.generation.ports.model import ModelCallStatus
from app.generation.ports.tools import ToolExecutionResult


class TurnJournal(Protocol):
    """AgentLoop 使用的持久化端口；实现可以来自数据库、内存或测试替身。"""

    async def append_user(self, message: str) -> None: ...

    async def append_assistant(
        self, message: AssistantMessage, *, batch_id: str | None
    ) -> None: ...

    async def append_assistant_placeholder(self, *, batch_id: str) -> None: ...

    async def append_tool_call(self, call: ToolCall, *, batch_id: str) -> None: ...

    async def append_tool_result(
        self,
        call: ToolCall,
        result: ToolExecutionResult,
        *,
        batch_id: str,
    ) -> None: ...

    async def append_model_call(
        self,
        *,
        status: ModelCallStatus,
        model_name: str,
        duration_ms: int,
        input_tokens: int = 0,
        cached_input_tokens: int = 0,
        output_tokens: int = 0,
        error_type: str | None = None,
        error_summary: str | None = None,
        retry_count: int = 0,
    ) -> None: ...

    async def commit(self) -> None: ...


class NullTurnJournal:
    """子代理等不落库场景使用的空日志实现。"""

    async def append_user(self, message: str) -> None:
        return None

    async def append_assistant(self, message: AssistantMessage, *, batch_id: str | None) -> None:
        return None

    async def append_assistant_placeholder(self, *, batch_id: str) -> None:
        return None

    async def append_tool_call(self, call: ToolCall, *, batch_id: str) -> None:
        return None

    async def append_tool_result(
        self,
        call: ToolCall,
        result: ToolExecutionResult,
        *,
        batch_id: str,
    ) -> None:
        return None

    async def append_model_call(
        self,
        *,
        status: ModelCallStatus,
        model_name: str,
        duration_ms: int,
        input_tokens: int = 0,
        cached_input_tokens: int = 0,
        output_tokens: int = 0,
        error_type: str | None = None,
        error_summary: str | None = None,
        retry_count: int = 0,
    ) -> None:
        return None

    async def commit(self) -> None:
        return None
