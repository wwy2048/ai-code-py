from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.generation.application.projection import QueueProjection
from app.generation.application.stream import QueueEvent, TurnJob
from app.generation.domain.events import (
    STEERING,
    InboundEvent,
    InboundEventType,
    UserInterrupt,
    UserMessage,
)


@dataclass(frozen=True)
class InboxAck:
    status: str
    action: str
    event_id: str
    message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "action": self.action,
            "commandId": self.event_id,
            "message": self.message,
        }


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


@dataclass(frozen=True)
class QueueOperation:
    """同步队列操作请求；不进入入站事件总线。"""

    app_id: int
    action: str
    command_id: str
    user_id: int = 0
    code_gen_type: str = "html"
    item_id: str | None = None
    message: str | None = None
    clear_paused: bool = False


AgentRunner = Callable[["GenerationAgent", UserMessage], Awaitable[None]]


@dataclass
class GenerationAgent:
    """单个 App 的入站事件消费者、收件箱和常驻 worker。"""

    app_id: int
    runner: AgentRunner
    projection: QueueProjection = field(init=False)
    direct_inbox: deque[UserMessage] = field(default_factory=deque)
    followup_inbox: deque[UserMessage] = field(default_factory=deque)
    steering_inbox: deque[UserMessage] = field(default_factory=deque)
    hidden_inbox: deque[UserMessage] = field(default_factory=deque)
    paused_inbox: deque[UserMessage] = field(default_factory=deque)
    worker: asyncio.Task[None] | None = None
    active_turn: TurnJob | None = None
    _version: int = 0
    _wake: asyncio.Event = field(default_factory=asyncio.Event)
    _stopping: bool = False

    def __post_init__(self) -> None:
        self.projection = QueueProjection(self.app_id, self.queue_snapshot)

    # -------------------------------------------------- inbound sink
    def enqueue(self, event: InboundEvent) -> InboxAck:
        if event.type is InboundEventType.USER_INTERRUPT:
            return self._enqueue_interrupt(event)
        if not isinstance(event, UserMessage):
            raise TypeError(f"收件箱只接受 UserMessage，收到 {type(event).__name__}")

        status = "accepted"
        action = event.intent
        if event.payload.get("direct"):
            self.direct_inbox.append(event)
        elif event.intent == STEERING:
            action = "steer"
            if self.active_turn is not None and self.active_turn.control.accepting_controls:
                self.active_turn.control.steer(event.text, command_id=event.event_id)
                return InboxAck("accepted", action, event.event_id)
            self.steering_inbox.append(event)
            status = "degraded_to_followup"
        else:
            self.followup_inbox.append(event)
            self._notify_mutation()
        self._wake.set()
        self.ensure_worker()
        return InboxAck(status, action, event.event_id)

    def _enqueue_interrupt(self, event: InboundEvent) -> InboxAck:
        if not isinstance(event, UserInterrupt):
            raise TypeError("打断事件类型错误")
        if self.active_turn is None or not self.active_turn.control.accepting_controls:
            return InboxAck("not_active", "interrupt", event.event_id)
        reason = str(event.payload.get("reason", "stop"))
        text = event.payload.get("text")
        if reason == "redirect" and isinstance(text, str) and text:
            self.active_turn.control.redirect(text, command_id=event.event_id)
            action = "redirect"
        else:
            self.active_turn.control.stop(command_id=event.event_id)
            action = "interrupt"
        if self.pause_followups(notify=False):
            self._notify_mutation()
        return InboxAck("accepted", action, event.event_id)

    # -------------------------------------------------- queue operations
    def promote(self, message_id: str) -> QueueAck:
        found = self._remove_visible(message_id)
        if found is None:
            return QueueAck("not_found", "steer", message_id, self.queue_snapshot())
        event = found
        if self.active_turn is not None and self.active_turn.control.accepting_controls:
            self.active_turn.control.steer(event.text, command_id=event.event_id)
            status = "accepted"
        else:
            self.hidden_inbox.append(event)
            self._wake.set()
            self.ensure_worker()
            status = "degraded_to_followup"
        self._notify_mutation()
        return QueueAck(status, "steer", message_id, self.queue_snapshot())

    def delete(self, message_id: str) -> QueueAck:
        if self._remove_visible(message_id) is None:
            return QueueAck("not_found", "delete", message_id, self.queue_snapshot())
        self._notify_mutation()
        return QueueAck("accepted", "delete", message_id, self.queue_snapshot())

    def resume_paused(self) -> QueueAck:
        while self.paused_inbox:
            self.followup_inbox.append(self.paused_inbox.popleft())
        if self.followup_inbox:
            self._wake.set()
            self.ensure_worker()
        self._notify_mutation()
        return QueueAck("accepted", "resume", "", self.queue_snapshot())

    def clear_paused(self) -> QueueAck:
        self.paused_inbox.clear()
        self._notify_mutation()
        return QueueAck("accepted", "clear_paused", "", self.queue_snapshot())

    def pause_followups(self, *, notify: bool = True) -> bool:
        if not self.followup_inbox:
            return False
        while self.followup_inbox:
            self.paused_inbox.append(self.followup_inbox.popleft())
        if notify:
            self._notify_mutation()
        return True

    def requeue_message(self, event: UserMessage, *, paused: bool) -> None:
        """执行失败但消息尚未提交时，将原事件放回队列并清除 inflight 标记。"""
        self.projection.clear_inflight(event.event_id)
        if paused:
            self.paused_inbox.appendleft(event)
        else:
            self.hidden_inbox.appendleft(event)
            self._wake.set()
            self.ensure_worker()
        self._notify_mutation()

    # -------------------------------------------------- worker
    def ensure_worker(self) -> None:
        if self._stopping:
            return
        if self.worker is None or self.worker.done():
            self.worker = asyncio.create_task(
                self._worker_loop(), name=f"generation-agent:{self.app_id}"
            )

    async def _worker_loop(self) -> None:
        while not self._stopping:
            event = await self.next_message()
            await self.runner(self, event)

    async def next_message(self) -> UserMessage:
        while True:
            for inbox in (
                self.direct_inbox,
                self.steering_inbox,
                self.hidden_inbox,
                self.followup_inbox,
            ):
                if inbox:
                    event = inbox.popleft()
                    self.projection.mark_inflight(event.event_id)
                    self._version += 1
                    return event
            self._wake.clear()
            if any(
                (
                    self.direct_inbox,
                    self.steering_inbox,
                    self.hidden_inbox,
                    self.followup_inbox,
                )
            ):
                continue
            await self._wake.wait()

    async def stop(self) -> None:
        self._stopping = True
        if self.worker is not None:
            self.worker.cancel()
            try:
                await self.worker
            except asyncio.CancelledError:
                pass
            self.worker = None

    # -------------------------------------------------- projection
    def queue_snapshot(self) -> dict[str, Any]:
        return {
            "appId": self.app_id,
            "version": self._version,
            "paused": bool(self.paused_inbox),
            "activeTurnId": self.active_turn.request_id if self.active_turn is not None else None,
            "queuedItems": [self._serialize(event, "queued") for event in self.followup_inbox],
            "pausedItems": [self._serialize(event, "paused") for event in self.paused_inbox],
        }

    def _notify_mutation(self) -> QueueEvent:
        self._version += 1
        return self.projection.apply_mutation()

    def _remove_visible(self, message_id: str) -> UserMessage | None:
        for inbox in (self.followup_inbox, self.paused_inbox):
            for event in inbox:
                if event.event_id == message_id:
                    inbox.remove(event)
                    return event
        return None

    @staticmethod
    def _serialize(event: UserMessage, status: str) -> dict[str, Any]:
        return {
            "commandId": event.event_id,
            "message": event.text,
            "status": status,
        }
