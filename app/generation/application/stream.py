from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.generation.application.control import TurnControl
from app.generation.domain.events import EventEnvelope


@dataclass
class TurnEvent:
    id: int
    event: str
    data: dict[str, Any]


@dataclass
class TurnJob:
    """单个 Turn 的重放缓冲；多个 SSE reader 各自维护游标。"""

    app_id: int
    user_id: int
    code_gen_type: str
    message: str
    request_id: str
    events: list[TurnEvent] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    completed: bool = False
    outcome: str | None = None
    next_sequence: int = 1
    max_events: int = 4096
    control: TurnControl = field(default_factory=TurnControl)
    _changed: asyncio.Event = field(default_factory=asyncio.Event)
    _first_retained_id: int = 1
    _subscription: object | None = None

    def reserve_sequence(self) -> int:
        sequence = self.next_sequence
        self.next_sequence += 1
        return sequence

    def offer(self, event: EventEnvelope) -> None:
        if event.app_id != self.app_id or event.turn_id != self.request_id:
            return
        data: dict[str, Any] = {
            "turnId": event.turn_id,
            "sequence": event.sequence,
            "schemaVersion": event.schema_version,
            "occurredAt": event.occurred_at.isoformat(),
            **event.payload,
        }
        if event.step_id is not None:
            data["stepId"] = event.step_id
        if event.command_id is not None:
            data["commandId"] = event.command_id
        self.events.append(TurnEvent(id=event.sequence, event=event.type.value, data=data))
        if len(self.events) > self.max_events:
            removed = self.events.pop(0)
            self._first_retained_id = max(self._first_retained_id, removed.id + 1)
        self._changed.set()

    async def finish(self, outcome: str) -> None:
        self.outcome = outcome
        self.completed = True
        self._changed.set()
        async with self.condition:
            self.condition.notify_all()

    async def stream(self, last_event_id: int | None) -> AsyncIterator[TurnEvent]:
        cursor = last_event_id or 0
        last_sequence = self.next_sequence - 1
        if self.completed and cursor >= last_sequence:
            final_event = next(
                (event for event in reversed(self.events) if event.event == "turn_finished"),
                None,
            )
            if final_event is not None:
                yield final_event
            return

        if cursor < self._first_retained_id - 1:
            gap_id = self._first_retained_id - 1
            yield TurnEvent(
                id=gap_id,
                event="stream_gap",
                data={
                    "turnId": self.request_id,
                    "sequence": gap_id,
                    "from": cursor + 1,
                    "to": gap_id,
                },
            )
            cursor = gap_id

        while True:
            pending = [event for event in self.events if event.id > cursor]
            if pending:
                for event in pending:
                    yield event
                cursor = pending[-1].id
                continue
            if self.completed:
                return
            self._changed.clear()
            pending = [event for event in self.events if event.id > cursor]
            if pending or self.completed:
                continue
            await self._changed.wait()


def encode_sse(event: TurnEvent) -> str:
    payload = json.dumps(event.data, ensure_ascii=False)
    return f"id: {event.id}\nevent: {event.event}\ndata: {payload}\n\n"
