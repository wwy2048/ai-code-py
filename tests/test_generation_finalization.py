from pathlib import Path
from typing import Any

from app.generation.adapters.model import ModelReply, ModelRequest, TextEmitter, ToolCall
from app.generation.adapters.tools import ToolRegistry
from app.generation.adapters.workspace import Workspace
from app.generation.application.loop import (
    FINALIZATION_FALLBACK_TEXT,
    AgentLoop,
    TurnState,
)
from app.generation.domain.messages import UserMessage
from app.generation.ports.journal import NullTurnJournal


class ExhaustedThenFinalModel:
    def __init__(self, *, fail_final: bool = False) -> None:
        self.requests: list[ModelRequest] = []
        self.fail_final = fail_final

    async def next_turn(self, request: ModelRequest, on_text: TextEmitter) -> ModelReply:
        self.requests.append(request)
        if request.tools:
            return ModelReply(
                text="继续修改。",
                tool_calls=[
                    ToolCall(
                        id=f"write-{len(self.requests)}",
                        name="write_file",
                        arguments={"path": "index.html", "content": "<h1>loop</h1>"},
                    )
                ],
            )
        if self.fail_final:
            raise RuntimeError("final model failed")
        await on_text("最终总结：页面已完成。")
        return ModelReply(text="最终总结：页面已完成。")


async def _run_exhausted_loop(
    tmp_path: Path,
    model: ExhaustedThenFinalModel,
) -> tuple[AgentLoop, TurnState, list[tuple[str, dict[str, Any]]]]:
    events: list[tuple[str, dict[str, Any]]] = []

    async def emit(event: str, payload: dict[str, Any]) -> None:
        events.append((event, payload))

    loop = AgentLoop(
        model=model,
        tools=ToolRegistry(Workspace(tmp_path)),
        journal=NullTurnJournal(),
        emit=emit,
        max_rounds=2,
    )
    state = TurnState(
        turn_id="finalize-turn",
        messages=[UserMessage(content="生成页面")],
    )
    phase = await loop.run_phase(state)
    assert phase.exhausted is True
    return loop, state, events


async def test_finalize_disables_tools_and_persists_final_text(tmp_path: Path) -> None:
    model = ExhaustedThenFinalModel()
    loop, state, events = await _run_exhausted_loop(tmp_path, model)

    result = await loop.finalize(state, reason="本轮工具调用预算已耗尽")

    assert result.interrupted is False
    assert result.final_text == "最终总结：页面已完成。"
    assert state.final_assistant_text == result.final_text
    assert state.persisted_assistant_text is True
    assert model.requests[-1].tools == ()
    assert isinstance(model.requests[-1].messages[-1], UserMessage)
    assert "系统收口指令" in model.requests[-1].messages[-1].content
    assert any(
        event == "text_delta" and payload.get("text") == result.final_text
        for event, payload in events
    )


async def test_finalize_falls_back_when_final_model_call_fails(tmp_path: Path) -> None:
    model = ExhaustedThenFinalModel(fail_final=True)
    loop, state, _ = await _run_exhausted_loop(tmp_path, model)

    result = await loop.finalize(state, reason="本轮工具调用预算已耗尽")

    assert result.interrupted is False
    assert result.final_text == FINALIZATION_FALLBACK_TEXT
    assert state.final_assistant_text == FINALIZATION_FALLBACK_TEXT
    assert state.persisted_assistant_text is True
