from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class InterruptRequest:
    """一个安全点上待处理的控制请求。"""

    stop: bool
    redirect_texts: tuple[str, ...] = ()


class TurnControl:
    """单个 Turn 的控制状态，由 HTTP 控制命令和 worker 共享。"""

    def __init__(self) -> None:
        self._steering: deque[tuple[str, str | None]] = deque()
        self._redirects: list[tuple[str, str | None]] = []
        self._stop_requested = False
        self._step_task: asyncio.Task[Any] | None = None
        self._step_id: str | None = None
        self._accepting_controls = True

    @property
    def step_id(self) -> str | None:
        return self._step_id

    @property
    def accepting_controls(self) -> bool:
        return self._accepting_controls

    def close(self) -> None:
        self._accepting_controls = False

    def bind_step(self, task: asyncio.Task[Any], *, step_id: str) -> None:
        self._step_task = task
        self._step_id = step_id

    def clear_step(self, task: asyncio.Task[Any]) -> None:
        if self._step_task is task:
            self._step_task = None

    def steer(self, text: str, *, command_id: str | None = None) -> None:
        self._steering.append((text, command_id))

    def redirect(self, text: str, *, command_id: str | None = None) -> None:
        self._redirects.append((text, command_id))
        self._cancel_step()

    def stop(self, *, command_id: str | None = None) -> None:
        del command_id
        self._stop_requested = True
        self._cancel_step()

    def drain_steering(self) -> list[tuple[str, str | None]]:
        items = list(self._steering)
        self._steering.clear()
        return items

    def has_steering(self) -> bool:
        return bool(self._steering)

    def has_interrupt(self) -> bool:
        return self._stop_requested or bool(self._redirects)

    def consume_interrupt(self) -> InterruptRequest | None:
        if not self.has_interrupt():
            return None
        redirects = tuple(text for text, _ in self._redirects)
        stop = self._stop_requested
        self._redirects.clear()
        self._stop_requested = False
        return InterruptRequest(stop=stop, redirect_texts=redirects)

    def _cancel_step(self) -> None:
        task = self._step_task
        if task is not None and not task.done():
            task.cancel()
