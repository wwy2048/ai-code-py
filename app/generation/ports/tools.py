from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.generation.domain.messages import ToolCall

ToolHandlerResult = tuple[str, int | None]
ToolHandler = Callable[[dict[str, Any]], ToolHandlerResult | Awaitable[ToolHandlerResult]]


@dataclass(frozen=True)
class ToolExecutionResult:
    ok: bool
    output: str
    public_input: dict[str, Any]
    duration_ms: int
    size_bytes: int | None = None


@dataclass(frozen=True)
class ToolExtension:
    """代码生成配置追加的工具 schema 与 handler 映射。"""

    definitions: tuple[dict[str, Any], ...] = ()
    handlers: Mapping[str, ToolHandler] = field(default_factory=dict)


class ToolExecutor(Protocol):
    definitions: list[dict[str, Any]]

    def public_input(self, call: ToolCall) -> dict[str, Any]: ...

    async def execute(self, call: ToolCall) -> ToolExecutionResult: ...
