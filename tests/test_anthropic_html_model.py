from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

from app.config import Settings
from app.generation.adapters.anthropic_model import AnthropicHtmlModel
from app.generation.adapters.model import ModelCallError, ModelRequest
from app.generation.domain.messages import (
    AssistantMessage,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)


class FakeStream:
    def __init__(self, chunks: list[str], final: Any) -> None:
        self._chunks = chunks
        self._final = final

    @property
    def text_stream(self) -> AsyncIterator[str]:
        async def iterator() -> AsyncIterator[str]:
            for chunk in self._chunks:
                yield chunk

        return iterator()

    async def get_final_message(self) -> Any:
        return self._final

    async def __aenter__(self) -> FakeStream:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class FakeInferenceMessages:
    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = outcomes
        self.calls = 0
        self.request: dict[str, Any] | None = None

    def stream(self, **kwargs: Any) -> Any:
        self.request = kwargs
        outcome = self._outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def create(self, **kwargs: Any) -> Any:
        self.request = kwargs
        outcome = self._outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeInferenceClient:
    def __init__(self, outcomes: list[Any]) -> None:
        self.messages = FakeInferenceMessages(outcomes)


def _settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        database_url="sqlite+aiosqlite://",
        anthropic_api_key="test-key",
        model_retry_base_seconds=0,
        _env_file=None,
    )


def _request(*messages: object, tools: tuple[dict[str, Any], ...] = ()) -> ModelRequest:
    return ModelRequest(messages=messages, tools=tools)  # type: ignore[arg-type]


async def test_anthropic_html_model_streams_text_and_returns_tool_call() -> None:
    final = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="我将创建页面。"),
            SimpleNamespace(
                type="tool_use",
                id="call-1",
                name="write_file",
                input={"path": "index.html", "content": "<h1>Hello</h1>"},
            ),
        ],
        usage=SimpleNamespace(input_tokens=21, output_tokens=12),
    )
    client = FakeInferenceClient([FakeStream(["我将", "创建页面。"], final)])
    model = AnthropicHtmlModel(_settings(), client=client)  # type: ignore[arg-type]
    deltas: list[str] = []

    async def on_text(text: str) -> None:
        deltas.append(text)

    reply = await model.next_turn(_request(UserMessage(content="创建页面")), on_text)

    assert deltas == ["我将", "创建页面。"]
    assert reply.text == "我将创建页面。"
    assert reply.tool_calls[0].name == "write_file"
    assert reply.tool_calls[0].arguments["path"] == "index.html"
    assert reply.input_tokens == 21
    assert reply.output_tokens == 12
    assert reply.retry_count == 0


async def test_anthropic_html_model_retries_timeout_before_streaming() -> None:
    final = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="完成")],
        usage=SimpleNamespace(input_tokens=3, output_tokens=2),
    )
    client = FakeInferenceClient([TimeoutError("timeout"), FakeStream(["完成"], final)])
    model = AnthropicHtmlModel(_settings(), client=client)  # type: ignore[arg-type]

    async def on_text(text: str) -> None:
        return None

    reply = await model.next_turn(_request(UserMessage(content="创建")), on_text)

    assert reply.text == "完成"
    assert reply.retry_count == 1
    assert client.messages.calls == 2


async def test_anthropic_html_model_maps_tool_result_into_user_message() -> None:
    final = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="已完成")],
        usage=SimpleNamespace(input_tokens=4, output_tokens=1),
    )
    client = FakeInferenceClient([FakeStream(["已完成"], final)])
    model = AnthropicHtmlModel(_settings(), client=client)  # type: ignore[arg-type]

    async def on_text(text: str) -> None:
        return None

    await model.next_turn(
        _request(
            UserMessage(content="创建页面"),
            AssistantMessage(
                content="调用工具",
                tool_calls=(
                    ToolCall(
                        id="call-1",
                        name="write_file",
                        arguments={"path": "index.html", "content": "<h1>x</h1>"},
                    ),
                ),
            ),
            ToolResultMessage(
                tool_call_id="call-1",
                name="write_file",
                content="写入失败",
                is_error=True,
            ),
        ),
        on_text,
    )

    request = client.messages.request
    assert request is not None
    final_user_message = request["messages"][-1]
    assert final_user_message["role"] == "user"
    assert final_user_message["content"][0]["type"] == "tool_result"
    assert final_user_message["content"][0]["is_error"] is True


async def test_anthropic_model_summarizes_context_without_tools() -> None:
    response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="压缩后的摘要")],
        usage=SimpleNamespace(input_tokens=12, output_tokens=8),
    )
    client = FakeInferenceClient([response])
    model = AnthropicHtmlModel(_settings(), client=client)  # type: ignore[arg-type]

    summary = await model.summarize(
        previous_summary="旧摘要",
        transcript='[{"role":"user","content":"新需求"}]',
    )

    assert summary.text == "压缩后的摘要"
    assert summary.input_tokens == 12
    assert summary.output_tokens == 8
    request = client.messages.request
    assert request is not None
    assert "tools" not in request
    assert "旧摘要" in request["messages"][0]["content"]


async def test_anthropic_model_uses_dynamic_tools_from_runtime_messages() -> None:
    final = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="完成")],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    client = FakeInferenceClient([FakeStream(["完成"], final)])
    model = AnthropicHtmlModel(_settings(), client=client)  # type: ignore[arg-type]
    dynamic_tools = [{"name": "delegate_subagent", "description": "委派", "input_schema": {}}]

    async def on_text(text: str) -> None:
        return None

    await model.next_turn(
        _request(UserMessage(content="委派"), tools=tuple(dynamic_tools)),
        on_text,
    )

    request = client.messages.request
    assert request is not None
    assert request["tools"] == dynamic_tools


async def test_anthropic_model_reports_max_tokens_stop_reason() -> None:
    final = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="准备写入")],
        usage=SimpleNamespace(input_tokens=10, output_tokens=8000),
        stop_reason="max_tokens",
    )
    client = FakeInferenceClient([FakeStream(["准备写入"], final)])
    model = AnthropicHtmlModel(_settings(), client=client)  # type: ignore[arg-type]

    async def on_text(text: str) -> None:
        return None

    reply = await model.next_turn(_request(UserMessage(content="创建")), on_text)

    assert reply.stop_reason == "max_tokens"
    assert reply.output_tokens == 8000


class BrokenStream(FakeStream):
    @property
    def text_stream(self) -> AsyncIterator[str]:
        async def iterator() -> AsyncIterator[str]:
            yield "部分输出"
            raise ConnectionError("stream reset")

        return iterator()


async def test_anthropic_html_model_does_not_retry_after_text_was_emitted() -> None:
    final = SimpleNamespace(content=[], usage=SimpleNamespace(input_tokens=0, output_tokens=0))
    client = FakeInferenceClient([BrokenStream([], final)])
    model = AnthropicHtmlModel(_settings(), client=client)  # type: ignore[arg-type]

    async def on_text(text: str) -> None:
        return None

    try:
        await model.next_turn(_request(UserMessage(content="创建")), on_text)
    except ModelCallError as exc:
        assert exc.summary == "connection"
        assert exc.retry_count == 0
    else:
        raise AssertionError("预期 ModelCallError")
    assert client.messages.calls == 1


async def test_anthropic_html_model_reports_retries_after_exhaustion() -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_url="sqlite+aiosqlite://",
        anthropic_api_key="test-key",
        model_retry_base_seconds=0,
        model_max_retries=2,
        _env_file=None,
    )
    client = FakeInferenceClient([TimeoutError("timeout"), TimeoutError("timeout")])
    model = AnthropicHtmlModel(settings, client=client)  # type: ignore[arg-type]

    async def on_text(text: str) -> None:
        return None

    try:
        await model.next_turn(_request(UserMessage(content="创建")), on_text)
    except ModelCallError as exc:
        assert exc.retry_count == 1
    else:
        raise AssertionError("预期 ModelCallError")
