from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from app.generation.domain.commands import StartTurnCommand

EventEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class TurnExecutionResult:
    """Turn 执行后的终态结果。"""

    status: str
    assistant_text: str = ""


class TurnController(Protocol):
    @property
    def accepting_controls(self) -> bool: ...

    @property
    def step_id(self) -> str | None: ...

    def close(self) -> None: ...

    def steer(self, text: str, *, command_id: str | None = None) -> None: ...

    def redirect(self, text: str, *, command_id: str | None = None) -> None: ...

    def stop(self, *, command_id: str | None = None) -> None: ...


class TurnExecutor(Protocol):
    async def execute(
        self,
        command: StartTurnCommand,
        *,
        emit: EventEmitter,
        control: TurnController,
    ) -> TurnExecutionResult: ...
