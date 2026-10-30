import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from math import ceil
from time import perf_counter
from typing import Any

from app.models import ChatHistory
from app.runtime.conversation import ConversationStore, MessageKind, project_model_messages
from app.runtime.model import ContextSummarizer, ModelCallStatus, SummaryResult

logger = logging.getLogger(__name__)

CompactionEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class CompactionOutcome:
    triggered: bool
    compacted: bool
    observed_tokens: int = 0
    threshold_tokens: int = 0
    compacted_messages: int = 0


class ContextCompactor:
    """在完整 Turn 边界压缩旧上下文，并保留最近一个完整 Turn。"""

    def __init__(
        self,
        *,
        context_window_tokens: int,
        trigger_ratio: float,
        fixed_overhead_tokens: int = 0,
    ) -> None:
        if context_window_tokens < 1:
            raise ValueError("contextWindowTokens 必须大于 0")
        if not 0 < trigger_ratio <= 1:
            raise ValueError("contextCompactionTriggerRatio 必须在 (0, 1] 内")
        if fixed_overhead_tokens < 0:
            raise ValueError("fixedOverheadTokens 不能小于 0")
        self._context_window_tokens = context_window_tokens
        self._threshold_tokens = max(1, int(context_window_tokens * trigger_ratio))
        self._fixed_overhead_tokens = fixed_overhead_tokens

    async def maybe_compact(
        self,
        conversation: ConversationStore,
        *,
        summarizer: ContextSummarizer | None,
        pending_text: str,
        emit: CompactionEmitter,
    ) -> CompactionOutcome:
        active_records = await conversation.load_active_records()
        provider_usage = await conversation.latest_provider_usage_tokens()
        if provider_usage > 0:
            observed_tokens = provider_usage + _estimate_text_tokens(pending_text)
        else:
            projected = project_model_messages(active_records)
            projected.append({"role": "user", "content": pending_text})
            observed_tokens = self._fixed_overhead_tokens + _estimate_messages_tokens(projected)

        if observed_tokens < self._threshold_tokens:
            return CompactionOutcome(
                triggered=False,
                compacted=False,
                observed_tokens=observed_tokens,
                threshold_tokens=self._threshold_tokens,
            )

        records_to_compact = self._select_turns_before_latest_complete(active_records)
        if not records_to_compact:
            return CompactionOutcome(
                triggered=True,
                compacted=False,
                observed_tokens=observed_tokens,
                threshold_tokens=self._threshold_tokens,
            )

        await emit(
            "compaction",
            {
                "status": "started",
                "observedTokens": observed_tokens,
                "thresholdTokens": self._threshold_tokens,
                "messageCount": len(records_to_compact),
            },
        )
        summary_started = perf_counter()
        summary_duration_ms = 0
        summary_result: SummaryResult | None = None
        try:
            if summarizer is None:
                raise RuntimeError("当前模型不支持上下文压缩")
            previous_summary = "\n\n".join(
                record.message
                for record in records_to_compact
                if record.message_kind == MessageKind.COMPACTION
            )
            transcript_records = [
                record
                for record in records_to_compact
                if record.message_kind != MessageKind.COMPACTION
            ]
            transcript = json.dumps(
                project_model_messages(transcript_records),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            summary_result = await summarizer.summarize(
                previous_summary=previous_summary or None,
                transcript=transcript,
            )
            summary_duration_ms = int((perf_counter() - summary_started) * 1000)
            summary = summary_result.text.strip()
            if not summary:
                raise ValueError("压缩摘要为空")
            await conversation.append_model_call(
                status=ModelCallStatus.SUCCESS,
                model_name=summary_result.model_name or summarizer.model_name,
                call_kind="compaction",
                duration_ms=summary_duration_ms,
                input_tokens=summary_result.input_tokens,
                output_tokens=summary_result.output_tokens,
                retry_count=summary_result.retry_count,
            )
            await conversation.append_compaction_summary(
                summary,
                compacted_records=records_to_compact,
                payload={
                    "observedTokens": observed_tokens,
                    "thresholdTokens": self._threshold_tokens,
                    "compactedMessages": len(records_to_compact),
                },
            )
            await conversation.commit()
        except Exception as exc:
            if summary_duration_ms == 0:
                summary_duration_ms = int((perf_counter() - summary_started) * 1000)
            await conversation.rollback()
            logger.warning("上下文压缩失败: %s", type(exc).__name__)
            await conversation.append_model_call(
                status=ModelCallStatus.ERROR,
                model_name=(
                    summary_result.model_name
                    if summary_result is not None
                    else getattr(summarizer, "model_name", "unknown")
                ),
                call_kind="compaction",
                duration_ms=summary_duration_ms,
                input_tokens=summary_result.input_tokens if summary_result is not None else 0,
                output_tokens=summary_result.output_tokens if summary_result is not None else 0,
                error_type=type(exc).__name__,
                error_summary=str(getattr(exc, "summary", type(exc).__name__))[:256],
                retry_count=int(getattr(exc, "retry_count", 0)),
            )
            await conversation.commit()
            await emit(
                "compaction",
                {
                    "status": "failed",
                    "observedTokens": observed_tokens,
                    "thresholdTokens": self._threshold_tokens,
                    "message": "上下文压缩失败，本轮将使用未压缩上下文",
                },
            )
            return CompactionOutcome(
                triggered=True,
                compacted=False,
                observed_tokens=observed_tokens,
                threshold_tokens=self._threshold_tokens,
            )

        await emit(
            "compaction",
            {
                "status": "completed",
                "observedTokens": observed_tokens,
                "thresholdTokens": self._threshold_tokens,
                "compactedMessages": len(records_to_compact),
                "summaryChars": len(summary),
            },
        )
        return CompactionOutcome(
            triggered=True,
            compacted=True,
            observed_tokens=observed_tokens,
            threshold_tokens=self._threshold_tokens,
            compacted_messages=len(records_to_compact),
        )

    @staticmethod
    def _select_turns_before_latest_complete(records: list[ChatHistory]) -> list[ChatHistory]:
        summaries = [record for record in records if record.message_kind == MessageKind.COMPACTION]
        turn_order: list[str] = []
        grouped: dict[str, list[ChatHistory]] = {}
        for record in records:
            if record.message_kind == MessageKind.COMPACTION:
                continue
            turn_key = record.turn_id or "legacy"
            if turn_key not in grouped:
                grouped[turn_key] = []
                turn_order.append(turn_key)
            grouped[turn_key].append(record)
        if len(turn_order) <= 1:
            return []
        latest_complete_index = next(
            (
                index
                for index in range(len(turn_order) - 1, -1, -1)
                if _is_complete_turn(grouped[turn_order[index]])
            ),
            None,
        )
        if latest_complete_index is None or latest_complete_index == 0:
            return []
        selected = list(summaries)
        for turn_key in turn_order[:latest_complete_index]:
            selected.extend(grouped[turn_key])
        return selected


def _is_complete_turn(records: list[ChatHistory]) -> bool:
    return any(
        record.message_kind == MessageKind.ASSISTANT
        or (record.message_kind == "legacy" and record.message_type == "ai")
        for record in records
    )


def _estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    serialized = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    return _estimate_text_tokens(serialized)


def _estimate_text_tokens(text: str) -> int:
    return ceil(len(text) / 2) if text else 0
