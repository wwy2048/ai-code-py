from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

from app.generation.application.stream import QueueEvent
from app.generation.domain.events import EventEnvelope, EventType


class QueueProjection:
    """把收件箱状态和消息消费事实投影成可重放的 App 队列事件流。"""

    def __init__(
        self,
        app_id: int,
        snapshot_provider: Callable[[], dict[str, Any]],
        *,
        event_limit: int = 2048,
    ) -> None:
        self.app_id = app_id
        self._snapshot_provider = snapshot_provider
        self._event_limit = event_limit
        self._events: list[QueueEvent] = []
        self._next_sequence = 1
        self._inflight: set[str] = set()
        self._wake: asyncio.Event = asyncio.Event()

    def mark_inflight(self, message_id: str) -> None:
        self._inflight.add(message_id)

    def clear_inflight(self, message_id: str) -> None:
        self._inflight.discard(message_id)

    def apply_mutation(self) -> QueueEvent:
        return self._publish("queue_changed", self._snapshot_provider())

    async def on_event(self, event: EventEnvelope) -> None:
        if event.app_id != self.app_id:
            return
        if event.type is EventType.USER_MESSAGE_COMMITTED:
            command_id = event.command_id
            if command_id is None or command_id not in self._inflight:
                return
            self._inflight.discard(command_id)
            self._publish(
                "user_message_consumed",
                {
                    "commandId": command_id,
                    "turnId": event.turn_id,
                    "text": str(event.payload.get("text", "")),
                    "mode": "followup",
                    "queue": self._snapshot_provider(),
                },
            )
            return
        if event.type is EventType.STEERING_CONSUMED:
            items = event.payload.get("items", [])
            if not isinstance(items, list):
                return
            for item in items:
                if not isinstance(item, dict):
                    continue
                command_id = item.get("commandId")
                text = item.get("text")
                if isinstance(command_id, str) and isinstance(text, str):
                    self._inflight.discard(command_id)
                    self._publish(
                        "user_message_consumed",
                        {
                            "commandId": command_id,
                            "turnId": event.turn_id,
                            "text": text,
                            "mode": "steer",
                            "queue": self._snapshot_provider(),
                        },
                    )

    async def stream(self, last_event_id: int | None) -> AsyncIterator[QueueEvent]:
        current = self._next_sequence - 1
        cursor = last_event_id if last_event_id is not None else current
        yield QueueEvent(
            id=cursor,
            event="queue_snapshot",
            data=self._snapshot_provider(),
        )
        while True:
            pending = [event for event in self._events if event.id > cursor]
            if pending:
                for event in pending:
                    yield event
                cursor = pending[-1].id
                continue
            self._wake.clear()
            pending = [event for event in self._events if event.id > cursor]
            if pending:
                continue
            await self._wake.wait()

    def _publish(self, event: str, data: dict[str, Any]) -> QueueEvent:
        queue_event = QueueEvent(
            id=self._next_sequence,
            event=event,
            data={
                "appId": self.app_id,
                "sequence": self._next_sequence,
                **data,
            },
        )
        self._next_sequence += 1
        self._events.append(queue_event)
        if len(self._events) > self._event_limit:
            self._events.pop(0)
        self._wake.set()
        return queue_event
