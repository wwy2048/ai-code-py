from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.generation.application.stream import QueueEvent, TurnJob
from app.generation.domain.commands import StartTurnCommand


@dataclass(frozen=True, slots=True)
class QueuedFollowup:
    """一条已提交但尚未被后端消费的用户 Followup。"""

    app_id: int
    user_id: int
    code_gen_type: str
    message: str
    command_id: str

    def as_start_turn(self) -> StartTurnCommand:
        return StartTurnCommand(
            app_id=self.app_id,
            user_id=self.user_id,
            code_gen_type=self.code_gen_type,
            message=self.message,
            turn_id=self.command_id,
        )


@dataclass(frozen=True, slots=True)
class ReadyTurn:
    """worker 下一条可执行的 Turn；followup 非空表示它来自排队项。"""

    command: StartTurnCommand
    followup: QueuedFollowup | None = None
    visible: bool = True


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


@dataclass(frozen=True)
class QueueAck:
    status: str
    action: str
    command_id: str
    queue: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "action": self.action,
            "commandId": self.command_id,
            "queue": self.queue,
        }


@dataclass
class AppSession:
    """单个 App 的待执行 Turn、暂停 Followup、活动 Turn 与常驻 worker。"""

    app_id: int
    turns: deque[ReadyTurn] = field(default_factory=deque)
    paused_followups: deque[QueuedFollowup] = field(default_factory=deque)
    worker: asyncio.Task[None] | None = None
    active_turn: TurnJob | None = None
    queue_events: list[QueueEvent] = field(default_factory=list)
    next_queue_sequence: int = 1
    queue_event_limit: int = 2048
    _wake: asyncio.Event = field(default_factory=asyncio.Event)
    _queue_wake: asyncio.Event = field(default_factory=asyncio.Event)

    def enqueue_turn(
        self,
        command: StartTurnCommand,
        *,
        followup: QueuedFollowup | None = None,
        visible: bool = True,
        front: bool = False,
    ) -> None:
        ready = ReadyTurn(command=command, followup=followup, visible=visible)
        if front:
            self.turns.appendleft(ready)
        else:
            self.turns.append(ready)
        self._wake.set()

    def enqueue_followup(
        self,
        item: QueuedFollowup,
        *,
        visible: bool = True,
        front: bool = False,
    ) -> None:
        self.enqueue_turn(item.as_start_turn(), followup=item, visible=visible, front=front)

    async def next_turn(self) -> ReadyTurn:
        while True:
            if self.turns:
                return self.turns.popleft()
            self._wake.clear()
            if self.turns:
                continue
            await self._wake.wait()

    def find_followup(self, command_id: str) -> tuple[QueuedFollowup, str] | None:
        for item in self.paused_followups:
            if item.command_id == command_id:
                return item, "paused"
        for ready in self.turns:
            ready_item = ready.followup
            if ready_item is not None and ready_item.command_id == command_id:
                return ready_item, "queued"
        return None

    def remove_followup(self, command_id: str) -> tuple[QueuedFollowup, str] | None:
        found = self.find_followup(command_id)
        if found is None:
            return None
        _, status = found
        if status == "paused":
            self.paused_followups = deque(
                item for item in self.paused_followups if item.command_id != command_id
            )
            return found
        self.turns = deque(
            ready
            for ready in self.turns
            if ready.followup is None or ready.followup.command_id != command_id
        )
        return found

    def clear_paused(self) -> list[QueuedFollowup]:
        removed = list(self.paused_followups)
        self.paused_followups.clear()
        return removed

    def resume_paused(self) -> list[QueuedFollowup]:
        resumed = list(self.paused_followups)
        self.paused_followups.clear()
        for item in resumed:
            self.enqueue_followup(item)
        return resumed

    def pause_ready_followups(self) -> list[QueuedFollowup]:
        paused: list[QueuedFollowup] = []
        remaining: deque[ReadyTurn] = deque()
        for ready in self.turns:
            if ready.followup is None:
                remaining.append(ready)
            else:
                paused.append(ready.followup)
        self.turns = remaining
        self.paused_followups.extend(paused)
        return paused

    def queue_snapshot(self) -> dict[str, Any]:
        queued_items = [
            self._serialize_followup(ready.followup, "queued")
            for ready in self.turns
            if ready.followup is not None and ready.visible
        ]
        paused_items = [
            self._serialize_followup(item, "paused") for item in self.paused_followups
        ]
        return {
            "appId": self.app_id,
            "version": self.next_queue_sequence - 1,
            "paused": bool(paused_items),
            "activeTurnId": self.active_turn.request_id if self.active_turn is not None else None,
            "queuedItems": queued_items,
            "pausedItems": paused_items,
        }

    def publish_queue_event(self, event: str, data: dict[str, Any]) -> QueueEvent:
        queue_event = QueueEvent(
            id=self.next_queue_sequence,
            event=event,
            data={
                "appId": self.app_id,
                "sequence": self.next_queue_sequence,
                **data,
            },
        )
        self.next_queue_sequence += 1
        self.queue_events.append(queue_event)
        if len(self.queue_events) > self.queue_event_limit:
            self.queue_events.pop(0)
        self._queue_wake.set()
        return queue_event

    async def stream_queue_events(self, cursor: int) -> AsyncIterator[QueueEvent]:
        while True:
            pending = [event for event in self.queue_events if event.id > cursor]
            if pending:
                for event in pending:
                    yield event
                cursor = pending[-1].id
                continue
            self._queue_wake.clear()
            pending = [event for event in self.queue_events if event.id > cursor]
            if pending:
                continue
            await self._queue_wake.wait()

    @staticmethod
    def _serialize_followup(item: QueuedFollowup | None, status: str) -> dict[str, Any]:
        if item is None:
            raise ValueError("排队项不能为空")
        return {
            "commandId": item.command_id,
            "message": item.message,
            "status": status,
        }
