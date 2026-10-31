import logging

from app.runtime.debug_log import AgentDebugLogger
from app.runtime.debug_log import logger as debug_logger
from app.runtime.model import ModelReply, ToolCall


class RecordingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def test_agent_debug_logger_prints_llm_and_tool_details() -> None:
    debug = AgentDebugLogger(enabled=True, max_chars=5000)
    call = ToolCall(
        id="call-debug",
        name="write_file",
        arguments={"path": "index.html", "content": "<h1>debug</h1>"},
    )

    handler = RecordingHandler()
    debug_logger.addHandler(handler)
    try:
        debug.llm_request(
            turn_id="turn-debug",
            round_index=1,
            model_name="deepseek-flash",
            messages=[{"role": "user", "content": "创建页面"}],
            tools=[{"name": "write_file"}],
        )
        debug.llm_response(
            turn_id="turn-debug",
            round_index=1,
            reply=ModelReply(text="准备写入", tool_calls=[call], output_tokens=10),
            duration_ms=123,
        )
        debug.tool_started(turn_id="turn-debug", call=call)
        debug.tool_finished(
            turn_id="turn-debug",
            call=call,
            ok=True,
            output="已写入 index.html",
            duration_ms=5,
            size_bytes=16,
        )
    finally:
        debug_logger.removeHandler(handler)

    text = "\n".join(handler.messages)
    assert "[LLM_REQUEST]" in text
    assert "[LLM_RESPONSE]" in text
    assert "[TOOL_CALL]" in text
    assert "[TOOL_RESULT]" in text
    assert '"content": "<h1>debug</h1>"' in text
    assert '"durationMs": 123' in text


def test_agent_debug_logger_can_be_disabled() -> None:
    debug = AgentDebugLogger(enabled=False, max_chars=5000)

    handler = RecordingHandler()
    debug_logger.addHandler(handler)
    try:
        debug.llm_request(
            turn_id="turn-debug",
            round_index=1,
            model_name="deepseek-flash",
            messages=[{"role": "user", "content": "不应输出"}],
            tools=[],
        )
    finally:
        debug_logger.removeHandler(handler)

    assert handler.messages == []
