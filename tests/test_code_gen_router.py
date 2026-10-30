from types import SimpleNamespace
from typing import Any

import pytest

from app.codegen_router import (
    AnthropicCodeGenTypeRouteModel,
    CodeGenType,
    ModelBackedCodeGenTypeRouter,
)
from app.config import Settings


class ScriptedRouteModel:
    def __init__(self, result: str | Exception) -> None:
        self._result = result

    async def choose_code_gen_type(self, init_prompt: str, system_prompt: str) -> str:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


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
    def __init__(self) -> None:
        self.request: dict | None = None

    async def create(self, **kwargs: object) -> Any:
        self.request = kwargs
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="tool_use",
                    input={"codeGenType": "VUE_PROJECT"},
                )
            ]
        )


class FakeAnthropicClient:
    def __init__(self) -> None:
        self.messages = FakeAnthropicMessages()

    async def close(self) -> None:
        return None


async def test_anthropic_route_model_parses_tool_use_response() -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_url="sqlite+aiosqlite://",
        anthropic_api_key="test-key",
        _env_file=None,
    )
    client = FakeAnthropicClient()
    model = AnthropicCodeGenTypeRouteModel(settings, client=client)  # type: ignore[arg-type]

    result = await model.choose_code_gen_type("复杂管理后台", "选择类型")

    assert result == "VUE_PROJECT"
    assert client.messages.request is not None
    assert "tool_choice" not in client.messages.request
    await model.aclose()
