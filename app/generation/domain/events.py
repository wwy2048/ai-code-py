from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    """生成上下文对外发布的稳定事件类型。"""

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


@dataclass(frozen=True)
class EventEnvelope:
    """跨应用层传递的不可变事件信封。"""

    type: EventType
    app_id: int
    turn_id: str
    sequence: int
    payload: dict[str, Any] = field(default_factory=dict)
    step_id: str | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    schema_version: int = 1
    command_id: str | None = None
