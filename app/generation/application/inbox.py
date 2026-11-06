from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from app.generation.application.stream import TurnJob
from app.generation.domain.commands import StartTurnCommand


@dataclass(frozen=True)
class CommandAck:
    status: str
    action: str
    app_id: int
    turn_id: str
    command_id: str
    message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "action": self.action,
            "appId": self.app_id,
            "turnId": self.turn_id,
            "commandId": self.command_id,
            "message": self.message,
        }


@dataclass
class AppSession:
    """单个 App 的 followup 收件箱、活动 Turn 与常驻 worker。"""

    app_id: int
    followups: deque[StartTurnCommand] = field(default_factory=deque)
    worker: asyncio.Task[None] | None = None
    active_turn: TurnJob | None = None
    _wake: asyncio.Event = field(default_factory=asyncio.Event)

    def enqueue(self, command: StartTurnCommand) -> None:
        self.followups.append(command)
        self._wake.set()

    async def next_command(self) -> StartTurnCommand:
        while True:
            if self.followups:
                return self.followups.popleft()
            self._wake.clear()
            if self.followups:
                continue
            await self._wake.wait()
