from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.generation.domain.events import EventEnvelope, EventType

logger = logging.getLogger(__name__)


Handler = Callable[[EventEnvelope], Awaitable[None]]


@runtime_checkable
class Mailbox(Protocol):
    def offer(self, event: EventEnvelope) -> None: ...


@dataclass(frozen=True)
class Subscription:
    name: str
    event_types: tuple[EventType, ...] | tuple[str, ...]
    handler: Mailbox | Handler

    def matches(self, event_type: EventType) -> bool:
        return event_type in self.event_types or "*" in self.event_types


class EventBus:
    """进程内出站事件总线，只负责投递。"""

    def __init__(self) -> None:
        self._subscriptions: list[Subscription] = []

    def subscribe(self, subscription: Subscription) -> Subscription:
        self._subscriptions.append(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        if subscription in self._subscriptions:
            self._subscriptions.remove(subscription)

    async def emit(self, event: EventEnvelope) -> None:
        for subscription in list(self._subscriptions):
            if not subscription.matches(event.type):
                continue
            try:
                handler = subscription.handler
                if isinstance(handler, Mailbox):
                    handler.offer(event)
                else:
                    await handler(event)
            except Exception:
                logger.warning(
                    "事件订阅者 %s 处理 %s 失败",
                    subscription.name,
                    event.type,
                    exc_info=True,
                )
