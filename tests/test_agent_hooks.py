from pathlib import Path
from typing import Any

from app.runtime.hooks import (
    AfterToolContext,
    BeforeToolContext,
    BeforeToolOutcome,
    HookBus,
    HookSet,
)
from app.runtime.journal import NullTurnJournal
from app.runtime.loop import AgentLoop, TurnState
from app.runtime.messages import ToolResultMessage, UserMessage
from app.runtime.model import ModelReply, ModelRequest, TextEmitter, ToolCall
from app.runtime.tools import ToolExecutionResult, ToolRegistry
from app.runtime.workspace import Workspace


class OneToolModel:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def next_turn(
        self,
        request: ModelRequest,
        on_text: TextEmitter,
    ) -> ModelReply:
        self.requests.append(request)
        if isinstance(request.messages[-1], ToolResultMessage):
            return ModelReply(text="完成")
        return ModelReply(
            text="写入页面",
            tool_calls=[
                ToolCall(
                    id="write-1",
                    name="write_file",
                    arguments={"path": "index.html", "content": "<h1>原始内容</h1>"},
                )
            ],
        )


async def test_before_tool_rewrites_call_and_after_tool_replaces_result(
    tmp_path: Path,
) -> None:
    model = OneToolModel()
    events: list[tuple[str, dict[str, Any]]] = []

    async def emit(event: str, payload: dict[str, Any]) -> None:
        events.append((event, payload))

    async def rewrite_call(context: BeforeToolContext) -> BeforeToolOutcome:
        return BeforeToolOutcome.allow(
            ToolCall(
                id=context.call.id,
                name=context.call.name,
                arguments={
                    "path": context.call.arguments["path"],
                    "content": "<h1>被 Hook 改写</h1>",
                },
            )
        )

    async def redact_result(context: AfterToolContext) -> ToolExecutionResult:
        return ToolExecutionResult(
            ok=True,
            output="工具结果已被 Hook 替换",
            public_input=context.result.public_input,
            duration_ms=context.result.duration_ms,
            size_bytes=context.result.size_bytes,
        )

    loop = AgentLoop(
        model=model,
        tools=ToolRegistry(Workspace(tmp_path)),
        journal=NullTurnJournal(),
        emit=emit,
        hooks=HookBus(
            HookSet(
                before_tool=(rewrite_call,),
                after_tool=(redact_result,),
            )
        ),
    )
    state = TurnState(
        turn_id="hook-turn",
        messages=[UserMessage(content="创建页面")],
    )

    result = await loop.run_phase(state)

    assert result.exhausted is False
    assert result.final_text == "完成"
    assert (tmp_path / "index.html").read_text(encoding="utf-8") == "<h1>被 Hook 改写</h1>"
    assert isinstance(model.requests[1].messages[-1], ToolResultMessage)
    assert model.requests[1].messages[-1].content == "工具结果已被 Hook 替换"
    tool_events = [event for event, _ in events if event.startswith("tool_")]
    assert tool_events == ["tool_started", "tool_finished"]


async def test_before_tool_denial_prevents_execution(tmp_path: Path) -> None:
    model = OneToolModel()

    async def deny_call(context: BeforeToolContext) -> BeforeToolOutcome:
        return BeforeToolOutcome.deny("策略拒绝写入")

    async def emit(event: str, payload: dict[str, Any]) -> None:
        return None

    loop = AgentLoop(
        model=model,
        tools=ToolRegistry(Workspace(tmp_path)),
        journal=NullTurnJournal(),
        emit=emit,
        hooks=HookBus(HookSet(before_tool=(deny_call,))),
    )
    state = TurnState(
        turn_id="deny-turn",
        messages=[UserMessage(content="创建页面")],
    )

    result = await loop.run_phase(state)

    assert result.final_text == "完成"
    assert not (tmp_path / "index.html").exists()
    tool_result = model.requests[1].messages[-1]
    assert isinstance(tool_result, ToolResultMessage)
    assert tool_result.is_error is True
    assert "策略拒绝写入" in tool_result.content


async def test_before_tool_hook_error_fails_closed(tmp_path: Path) -> None:
    model = OneToolModel()

    async def broken_hook(context: BeforeToolContext) -> BeforeToolOutcome:
        raise RuntimeError("hook broken")

    async def emit(event: str, payload: dict[str, Any]) -> None:
        return None

    loop = AgentLoop(
        model=model,
        tools=ToolRegistry(Workspace(tmp_path)),
        journal=NullTurnJournal(),
        emit=emit,
        hooks=HookBus(HookSet(before_tool=(broken_hook,))),
    )
    state = TurnState(
        turn_id="broken-hook-turn",
        messages=[UserMessage(content="创建页面")],
    )

    await loop.run_phase(state)

    assert not (tmp_path / "index.html").exists()
    tool_result = model.requests[1].messages[-1]
    assert isinstance(tool_result, ToolResultMessage)
    assert "Hook 拒绝" in tool_result.content
