from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from app.generation.domain.events import (
    EventEnvelope,
    InboundEvent,
    Mailbox,
    Subscription,
    validate_subscription,
)

logger = logging.getLogger(__name__)

Sink = Callable[[InboundEvent], Any]


class EventBus:
    """双向事件总线：入站同步投递，出站异步分派。"""

    def __init__(self) -> None:
        self._sinks: dict[int, Sink] = {}
        self._subscriptions: list[Subscription] = []

    # -------------------------------------------------- inbound
    def register(self, app_id: int, sink: Sink) -> None:
        self._sinks[app_id] = sink

    def unregister(self, app_id: int) -> None:
        self._sinks.pop(app_id, None)

    def publish(self, event: InboundEvent, *, to: int) -> Any:
        """同步投递到 App Agent；只入箱/走旁路，不执行 Turn。"""
        sink = self._sinks.get(to)
        if sink is None:
            raise KeyError(f"没有这个生成代理：{to!r}")
        return sink(event)

    # -------------------------------------------------- outbound
    def subscribe(self, subscription: Subscription) -> Subscription:
        validate_subscription(subscription)
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


__all__ = ["EventBus", "Mailbox", "Sink", "Subscription"]
