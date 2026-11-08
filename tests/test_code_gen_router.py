import logging
from types import SimpleNamespace
from typing import Any

import pytest

from app.codegen_router import (
    AnthropicCodeGenTypeRouteModel,
    CodeGenType,
    CodeGenTypeRouteModelError,
    CodeGenTypeRouteResult,
    ModelBackedCodeGenTypeRouter,
)
from app.config import Settings


class ScriptedRouteModel:
    def __init__(self, result: str | Exception, *, raw_response: str | None = None) -> None:
        self._result = result
        self._raw_response = raw_response or f'{{"selectedType": {result!r}}}'

    async def choose_code_gen_type(
        self, init_prompt: str, system_prompt: str
    ) -> CodeGenTypeRouteResult:
        if isinstance(self._result, Exception):
            raise self._result
        return CodeGenTypeRouteResult(
            selected_type=self._result,
            raw_response=self._raw_response,
            model="scripted-route-model",
        )


@pytest.mark.parametrize(
    ("model_output", "expected"),
    [
        ("HTML", CodeGenType.HTML),
        ("MULTI_FILE", CodeGenType.MULTI_FILE),
        ("VUE_PROJECT", CodeGenType.VUE_PROJECT),
        ("vue_project", CodeGenType.VUE_PROJECT),
    ],
)
async def test_router_accepts_all_supported_model_choices(
    model_output: str,
    expected: CodeGenType,
) -> None:
    router = ModelBackedCodeGenTypeRouter(ScriptedRouteModel(model_output))

    assert await router.route("创建一个复杂管理后台") == expected


@pytest.mark.parametrize(
    "model_behavior",
    [RuntimeError("模型不可用"), "UNKNOWN_TYPE"],
)
async def test_router_falls_back_to_html_when_model_is_unavailable(
    model_behavior: str | Exception,
) -> None:
    router = ModelBackedCodeGenTypeRouter(ScriptedRouteModel(model_behavior))

    assert await router.route("创建一个页面") == CodeGenType.HTML


class FakeAnthropicMessages:
    def __init__(self, response_text: str = '{"codeGenType":"vue_project"}') -> None:
        self.request: dict | None = None
        self._response_text = response_text

    async def create(self, **kwargs: object) -> Any:
        self.request = kwargs
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="text",
                    text=self._response_text,
                )
            ]
        )


class FakeAnthropicClient:
    def __init__(self, response_text: str = '{"codeGenType":"vue_project"}') -> None:
        self.messages = FakeAnthropicMessages(response_text)

    async def close(self) -> None:
        return None


async def test_anthropic_route_model_uses_strict_json_without_tools() -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_url="sqlite+aiosqlite://",
        anthropic_api_key="test-key",
        _env_file=None,
    )
    client = FakeAnthropicClient()
    route_model = AnthropicCodeGenTypeRouteModel(settings, client=client)  # type: ignore[arg-type]
    router = ModelBackedCodeGenTypeRouter(route_model)

    result = await router.route("复杂管理后台")

    assert result == CodeGenType.VUE_PROJECT
    assert client.messages.request is not None
    assert "tools" not in client.messages.request
    assert client.messages.request["max_tokens"] >= 512
    assert client.messages.request["temperature"] == 0
    assert client.messages.request["thinking"] == {"type": "disabled"}
    assert '{"codeGenType":"html"}' in client.messages.request["system"]
    await router.aclose()


class SequencedAnthropicMessages:
    def __init__(self, response_texts: list[str]) -> None:
        self.requests: list[dict[str, Any]] = []
        self._response_texts = response_texts

    async def create(self, **kwargs: object) -> Any:
        self.requests.append(kwargs)
        response_text = self._response_texts[len(self.requests) - 1]
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=response_text)]
        )


class SequencedAnthropicClient:
    def __init__(self, response_texts: list[str]) -> None:
        self.messages = SequencedAnthropicMessages(response_texts)

    async def close(self) -> None:
        return None


class FakeUpstreamError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.body = {"error": {"message": message}}
        self.request_id = "req-upstream-1"


class FailingAnthropicMessages:
    async def create(self, **kwargs: object) -> Any:
        raise FakeUpstreamError("error reading a body from connection: Operation timed out")


class FailingAnthropicClient:
    def __init__(self) -> None:
        self.messages = FailingAnthropicMessages()

    async def close(self) -> None:
        return None


async def test_anthropic_route_model_preserves_error_context_for_logging() -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_url="sqlite+aiosqlite://",
        anthropic_api_key="test-key",
        anthropic_base_url="https://example.test/anthropic",
        anthropic_timeout_seconds=12.5,
        _env_file=None,
    )
    model = AnthropicCodeGenTypeRouteModel(  # type: ignore[arg-type]
        settings,
        client=FailingAnthropicClient(),
    )

    with pytest.raises(CodeGenTypeRouteModelError) as exc_info:
        await model.choose_code_gen_type("创建一个 VUE 页面", "选择类型")

    error = exc_info.value
    assert error.model == settings.anthropic_model
    assert error.base_url == "https://example.test/anthropic"
    assert error.timeout_seconds == 12.5
    assert error.request_id == "req-upstream-1"
    assert error.raw_response is not None
    assert "Operation timed out" in error.raw_response
    assert isinstance(error.__cause__, FakeUpstreamError)
    await model.aclose()


@pytest.mark.parametrize(
    "init_prompt",
    [
        "用 Vue 3 做一个绿色个人主页",
        "请拆成 HTML、CSS、JavaScript 多个文件",
        "做一个简单的静态落地页",
    ],
)
async def test_router_falls_back_to_html_for_any_prompt_when_output_is_invalid(
    init_prompt: str,
) -> None:
    router = ModelBackedCodeGenTypeRouter(ScriptedRouteModel("UNKNOWN_TYPE"))

    assert await router.route(init_prompt) == CodeGenType.HTML


async def test_router_logs_raw_empty_output_when_result_is_invalid(
    caplog: pytest.LogCaptureFixture,
) -> None:
    router = ModelBackedCodeGenTypeRouter(
        ScriptedRouteModel(
            "",
            raw_response='{"content": [], "stop_reason": "end_turn"}',
        )
    )

    with caplog.at_level(logging.WARNING, logger="app.codegen_router"):
        assert await router.route("创建一个 VUE 页面") == CodeGenType.HTML

    assert "stage=parse_result" in caplog.text
    assert "raw_model_output=''" in caplog.text
    assert "end_turn" in caplog.text


async def test_router_logs_exception_type_when_model_call_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    router = ModelBackedCodeGenTypeRouter(ScriptedRouteModel(RuntimeError("上游超时")))

    with caplog.at_level(logging.WARNING, logger="app.codegen_router"):
        assert await router.route("创建一个页面") == CodeGenType.HTML

    assert "stage=model_call" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert "上游超时" in caplog.text
    assert "raw_model_response=<unavailable>" in caplog.text

async def test_router_logs_anthropic_error_context_from_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_url="sqlite+aiosqlite://",
        anthropic_api_key="test-key",
        anthropic_base_url="https://example.test/anthropic",
        anthropic_timeout_seconds=12.5,
        _env_file=None,
    )
    route_model = AnthropicCodeGenTypeRouteModel(  # type: ignore[arg-type]
        settings,
        client=FailingAnthropicClient(),
    )
    router = ModelBackedCodeGenTypeRouter(route_model)

    with caplog.at_level(logging.WARNING, logger="app.codegen_router"):
        assert await router.route("创建一个 VUE 页面") == CodeGenType.HTML

    assert "stage=model_call" in caplog.text
    assert "error_type=CodeGenTypeRouteModelError" in caplog.text
    assert "cause_type=FakeUpstreamError" in caplog.text
    assert "base_url=https://example.test/anthropic" in caplog.text
    assert "timeout_seconds=12.5" in caplog.text
    assert "request_id='req-upstream-1'" in caplog.text
    assert "Operation timed out" in caplog.text
    assert "prompt_preview='创建一个 VUE 页面'" in caplog.text
    await router.aclose()

@pytest.mark.parametrize(
    ("response_text", "expected"),
    [
        ('```json\n{"codeGenType":"vue_project"}\n```', CodeGenType.VUE_PROJECT),
        ('说明：\n{"codeGenType":"vue_project"}\n请选择。', CodeGenType.VUE_PROJECT),
        ('{"codeGenType":"VUE-PROJECT"}', CodeGenType.VUE_PROJECT),
        ('{"codeGenType":"multi-file"}', CodeGenType.MULTI_FILE),
    ],
)
async def test_route_model_tolerates_common_json_variants(
    response_text: str,
    expected: CodeGenType,
) -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_url="sqlite+aiosqlite://",
        anthropic_api_key="test-key",
        _env_file=None,
    )
    client = FakeAnthropicClient(response_text)
    route_model = AnthropicCodeGenTypeRouteModel(settings, client=client)  # type: ignore[arg-type]
    router = ModelBackedCodeGenTypeRouter(route_model)

    assert await router.route("创建一个页面") == expected
    await router.aclose()

async def test_route_model_repairs_invalid_json_once() -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_url="sqlite+aiosqlite://",
        anthropic_api_key="test-key",
        _env_file=None,
    )
    client = SequencedAnthropicClient(
        [
            "我不确定，也许是 Vue。",
            '{"codeGenType":"vue_project"}',
        ]
    )
    route_model = AnthropicCodeGenTypeRouteModel(settings, client=client)  # type: ignore[arg-type]
    router = ModelBackedCodeGenTypeRouter(route_model)

    assert await router.route("用 Vue 做一个主页") == CodeGenType.VUE_PROJECT
    assert len(client.messages.requests) == 2
    repair_prompt = client.messages.requests[1]["messages"][0]["content"]
    assert "我不确定，也许是 Vue。" in repair_prompt
    assert "只输出一个合法 JSON 对象" in repair_prompt
    await router.aclose()

async def test_route_model_stops_after_one_repair_and_falls_back(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_url="sqlite+aiosqlite://",
        anthropic_api_key="test-key",
        _env_file=None,
    )
    client = SequencedAnthropicClient(
        [
            "不是 JSON",
            "仍然不是 JSON",
            '{"codeGenType":"vue_project"}',
        ]
    )
    route_model = AnthropicCodeGenTypeRouteModel(settings, client=client)  # type: ignore[arg-type]
    router = ModelBackedCodeGenTypeRouter(route_model)

    with caplog.at_level(logging.WARNING, logger="app.codegen_router"):
        assert await router.route("做一个简单页面") == CodeGenType.HTML

    assert len(client.messages.requests) == 2
    assert "attempts=2" in caplog.text
    assert "--- attempt 1 ---" in caplog.text
    assert "--- attempt 2 ---" in caplog.text
    await router.aclose()
