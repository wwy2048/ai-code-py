import json
import logging
from dataclasses import asdict
from typing import Any

from app.runtime.model import ModelReply, ToolCall

logger = logging.getLogger("app.runtime.agent_debug")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s\n%(message)s")
    )
    logger.addHandler(console_handler)


class AgentDebugLogger:
    """把 Agent 单轮输入、模型返回和工具执行输出到控制台。"""

    def __init__(self, *, enabled: bool, max_chars: int) -> None:
        self._enabled = enabled
        self._max_chars = max(1000, max_chars)

    def llm_request(
        self,
        *,
        turn_id: str,
        round_index: int,
        model_name: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> None:
        if not self._enabled:
            return
        clean_messages = [
            {key: value for key, value in message.items() if key != "tools"} for message in messages
        ]
        self._log(
            "LLM_REQUEST",
            {
                "turnId": turn_id,
                "round": round_index,
                "model": model_name,
                "messages": clean_messages,
                "tools": tools,
            },
        )

    def llm_response(
        self,
        *,
        turn_id: str,
        round_index: int,
        reply: ModelReply,
        duration_ms: int,
    ) -> None:
        if not self._enabled:
            return
        self._log(
            "LLM_RESPONSE",
            {
                "turnId": turn_id,
                "round": round_index,
                "model": reply.model_name,
                "durationMs": duration_ms,
                "inputTokens": reply.input_tokens,
                "outputTokens": reply.output_tokens,
                "retryCount": reply.retry_count,
                "stopReason": reply.stop_reason,
                "text": reply.text,
                "toolCalls": [asdict(call) for call in reply.tool_calls],
            },
        )

    def llm_error(
        self,
        *,
        turn_id: str,
        round_index: int,
        error: Exception,
        duration_ms: int,
    ) -> None:
        if not self._enabled:
            return
        self._log(
            "LLM_ERROR",
            {
                "turnId": turn_id,
                "round": round_index,
                "durationMs": duration_ms,
                "errorType": type(error).__name__,
                "error": str(error),
            },
        )

    def tool_started(self, *, turn_id: str, call: ToolCall) -> None:
        if not self._enabled:
            return
        self._log(
            "TOOL_CALL",
            {
                "turnId": turn_id,
                "toolCallId": call.id,
                "name": call.name,
                "arguments": call.arguments,
            },
        )

    def tool_finished(
        self,
        *,
        turn_id: str,
        call: ToolCall,
        ok: bool,
        output: str,
        duration_ms: int,
        size_bytes: int | None,
    ) -> None:
        if not self._enabled:
            return
        self._log(
            "TOOL_RESULT",
            {
                "turnId": turn_id,
                "toolCallId": call.id,
                "name": call.name,
                "ok": ok,
                "durationMs": duration_ms,
                "sizeBytes": size_bytes,
                "output": output,
            },
        )

    def _log(self, label: str, payload: dict[str, Any]) -> None:
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        if len(text) > self._max_chars:
            omitted = len(text) - self._max_chars
            text = f"{text[: self._max_chars]}\n... [日志截断 {omitted} 字符]"
        logger.info("[%s]\n%s", label, text)
