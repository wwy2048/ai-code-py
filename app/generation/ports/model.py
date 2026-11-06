from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from app.generation.domain.messages import AgentMessage, ToolCall

TextEmitter = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class ModelRequest:
    messages: tuple[AgentMessage, ...]
    tools: tuple[dict[str, object], ...] = ()
    model_name: str = ""


@dataclass(frozen=True)
class ModelReply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model_name: str = ""
    retry_count: int = 0
    stop_reason: str | None = None


@dataclass(frozen=True)
class SummaryResult:
    text: str
    model_name: str
    input_tokens: int = 0
    output_tokens: int = 0
    retry_count: int = 0


class ModelCallStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"


class ModelCallError(RuntimeError):
    def __init__(self, summary: str, retry_count: int) -> None:
        super().__init__(summary)
        self.summary = summary
        self.retry_count = retry_count


class AgentModel(Protocol):
    async def next_turn(
        self,
        request: ModelRequest,
        on_text: TextEmitter,
    ) -> ModelReply: ...


@runtime_checkable
class ContextSummarizer(Protocol):
    @property
    def model_name(self) -> str: ...

    async def summarize(
        self,
        *,
        previous_summary: str | None,
        transcript: str,
    ) -> SummaryResult: ...
