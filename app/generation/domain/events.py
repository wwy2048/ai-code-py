from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar, Protocol, runtime_checkable

FOLLOWUP = "followup"
STEERING = "steering"
DEFAULT_PRIORITY = 100


class EventType(StrEnum):
    """生成上下文对外发布的稳定出站事件类型。"""

    TURN_STARTED = "turn_started"
    USER_MESSAGE_COMMITTED = "user_message_committed"
    TEXT_DELTA = "text_delta"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    COMPACTION = "compaction"
    SUBAGENT_STARTED = "subagent_started"
    SUBAGENT_FINISHED = "subagent_finished"
    SUBAGENT_FAILED = "subagent_failed"
    STEERING_CONSUMED = "steering_consumed"
    STEP_CANCELLED = "step_cancelled"
    REDIRECT_APPLIED = "redirect_applied"
    TURN_INTERRUPTED = "turn_interrupted"
    STREAM_GAP = "stream_gap"
    ERROR = "error"
    TURN_FINISHED = "turn_finished"


class InboundEventType(StrEnum):
    """下行事件类型：只经总线同步投递，不直接执行 Turn。"""

    USER_MESSAGE = "user_message"
    USER_INTERRUPT = "user_interrupt"


@dataclass(frozen=True)
class EventEnvelope:
    """跨应用层传递的不可变出站事件信封。"""

    type: EventType
    app_id: int
    turn_id: str
    sequence: int
    payload: dict[str, Any] = field(default_factory=dict)
    step_id: str | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    schema_version: int = 1
    command_id: str | None = None
    HANDLER_SHAPE: ClassVar[Any] = object


@runtime_checkable
class Mailbox(Protocol):
    """邮箱型消费者：offer 只做非阻塞接收。"""

    def offer(self, event: EventEnvelope) -> None: ...


class StreamEvent(EventEnvelope):
    """高频流事件：只允许邮箱型消费者订阅。"""

    HANDLER_SHAPE: ClassVar[Any] = Mailbox


@dataclass(frozen=True)
class InboundEvent:
    """入站事件基类；event_id 同时承担幂等和排队项标识。"""

    event_id: str
    type: InboundEventType
    app_id: int
    user_id: int
    code_gen_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    HANDLER_SHAPE: ClassVar[Any] = object

    @property
    def text(self) -> str:
        return str(self.payload.get("text", ""))


@dataclass(frozen=True)
class UserMessage(InboundEvent):
    """用户消息事件；intent 决定进入 followup 还是 steering 收件箱。"""

    intent: str = FOLLOWUP
    priority: int = DEFAULT_PRIORITY

    @classmethod
    def create(
        cls,
        *,
        event_id: str,
        app_id: int,
        user_id: int,
        code_gen_type: str,
        text: str,
        intent: str = FOLLOWUP,
        priority: int = DEFAULT_PRIORITY,
        direct: bool = False,
        target_turn_id: str | None = None,
    ) -> UserMessage:
        payload: dict[str, Any] = {"text": text, "direct": direct}
        if target_turn_id is not None:
            payload["target_turn_id"] = target_turn_id
        return cls(
            event_id=event_id,
            type=InboundEventType.USER_MESSAGE,
            app_id=app_id,
            user_id=user_id,
            code_gen_type=code_gen_type,
            payload=payload,
            intent=intent,
            priority=priority,
        )


@dataclass(frozen=True)
class UserInterrupt(InboundEvent):
    """打断/转向旁路事件；不进入收件箱。"""

    @classmethod
    def create(
        cls,
        *,
        event_id: str,
        app_id: int,
        user_id: int,
        code_gen_type: str,
        reason: str = "stop",
        text: str | None = None,
        target_turn_id: str | None = None,
    ) -> UserInterrupt:
        payload: dict[str, Any] = {"reason": reason}
        if text is not None:
            payload["text"] = text
        if target_turn_id is not None:
            payload["target_turn_id"] = target_turn_id
        return cls(
            event_id=event_id,
            type=InboundEventType.USER_INTERRUPT,
            app_id=app_id,
            user_id=user_id,
            code_gen_type=code_gen_type,
            payload=payload,
        )


Handler = Callable[[EventEnvelope], Awaitable[None]]


@dataclass(frozen=True)
class Subscription:
    name: str
    event_types: tuple[EventType, ...] | tuple[str, ...]
    handler: Mailbox | Handler

    def matches(self, event_type: EventType) -> bool:
        return event_type in self.event_types or "*" in self.event_types


EVENT_CLASSES: dict[str | EventType, type[EventEnvelope]] = {
    EventType.TEXT_DELTA: StreamEvent,
}


def event_class(event_type: str | EventType) -> type[EventEnvelope]:
    return EVENT_CLASSES.get(event_type, EventEnvelope)


def require_consumable(event_type: str | EventType, handler: object) -> None:
    shape = event_class(event_type).HANDLER_SHAPE
    if shape is not object and not isinstance(handler, shape):
        raise ValueError(f"事件 {event_type} 只允许 {getattr(shape, '__name__', shape)} 型消费者")


def validate_subscription(subscription: Subscription) -> None:
    for event_type in subscription.event_types:
        require_consumable(event_type, subscription.handler)
