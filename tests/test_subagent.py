import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.generation.adapters.model import ModelReply, ModelRequest, TextEmitter, ToolCall
from app.generation.adapters.subagent import SubagentRunner
from app.generation.adapters.workspace import Workspace
from app.generation.domain.messages import ToolResultMessage


class WritingSubagentModel:
    def __init__(self) -> None:
        self.calls = 0

    async def next_turn(self, request: ModelRequest, on_text: TextEmitter) -> ModelReply:
        self.calls += 1
        if self.calls == 1:
            return ModelReply(
                text="写入子目录",
                tool_calls=[
                    ToolCall(
                        id="sub-write",
                        name="write_file",
                        arguments={"path": "part.html", "content": "<section>sub</section>"},
                    )
                ],
            )
        return ModelReply(text="子代理摘要：已完成局部页面")


class NestedSubagentModel:
    def __init__(self) -> None:
        self.calls = 0

    async def next_turn(self, request: ModelRequest, on_text: TextEmitter) -> ModelReply:
        self.calls += 1
        if self.calls == 1:
            return ModelReply(
                tool_calls=[
                    ToolCall(
                        id="nested",
                        name="delegate_subagent",
                        arguments={"task": "再委派", "workdir": "."},
                    )
                ]
            )
        result = next(
            message for message in request.messages if isinstance(message, ToolResultMessage)
        )
        assert "未知工具: delegate_subagent" in result.content
        return ModelReply(text="未创建下一级子代理")


class SlowSubagentModel:
    async def next_turn(self, request: ModelRequest, on_text: TextEmitter) -> ModelReply:
        await asyncio.sleep(1)
        return ModelReply(text="太迟")


async def test_subagent_writes_in_scoped_directory_and_returns_summary(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    runner = SubagentRunner(
        workspace,
        lambda _: WritingSubagentModel(),
        code_gen_type="html",
        max_rounds=3,
        timeout_seconds=1,
    )
    events: list[tuple[str, dict[str, Any]]] = []

    async def emit(event: str, data: dict[str, Any]) -> None:
        events.append((event, data))

    summary = await runner.run(
        task="编写局部区块",
        workdir="sections",
        emit=emit,
        turn_id="turn-subagent",
    )

    assert summary == "子代理摘要：已完成局部页面"
    part = (tmp_path / "sections" / "part.html").read_text(encoding="utf-8")
    assert part == "<section>sub</section>"
    assert [event for event, _ in events] == ["subagent_started", "subagent_finished"]


@pytest.mark.parametrize("workdir", ["../outside", "/absolute", "."])
async def test_subagent_rejects_out_of_scope_directory(tmp_path: Path, workdir: str) -> None:
    runner = SubagentRunner(
        Workspace(tmp_path),
        lambda _: WritingSubagentModel(),
        code_gen_type="html",
        max_rounds=2,
        timeout_seconds=1,
    )

    async def emit(event: str, data: dict[str, Any]) -> None:
        return None

    with pytest.raises(ValueError):
        await runner.run(task="越界", workdir=workdir, emit=emit, turn_id="bad-scope")


async def test_subagent_cannot_create_nested_subagent(tmp_path: Path) -> None:
    runner = SubagentRunner(
        Workspace(tmp_path),
        lambda _: NestedSubagentModel(),
        code_gen_type="html",
        max_rounds=2,
        timeout_seconds=1,
    )

    async def emit(event: str, data: dict[str, Any]) -> None:
        return None

    summary = await runner.run(
        task="测试嵌套",
        workdir="nested",
        emit=emit,
        turn_id="nested-turn",
    )

    assert summary == "未创建下一级子代理"
    assert not (tmp_path / "nested" / "delegate_subagent").exists()


async def test_subagent_timeout_emits_failure(tmp_path: Path) -> None:
    runner = SubagentRunner(
        Workspace(tmp_path),
        lambda _: SlowSubagentModel(),
        code_gen_type="html",
        max_rounds=2,
        timeout_seconds=0.01,
    )
    events: list[str] = []

    async def emit(event: str, data: dict[str, Any]) -> None:
        events.append(event)

    with pytest.raises(ValueError, match="超时"):
        await runner.run(task="慢任务", workdir="slow", emit=emit, turn_id="slow-turn")
    assert events == ["subagent_started", "subagent_failed"]


async def test_subagent_concurrent_run_is_rejected(tmp_path: Path) -> None:
    class BlockingModel:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def next_turn(
            self,
            request: ModelRequest,
            on_text: TextEmitter,
        ) -> ModelReply:
            self.started.set()
            await self.release.wait()
            return ModelReply(text="完成")

    model = BlockingModel()
    runner = SubagentRunner(
        Workspace(tmp_path),
        lambda _: model,
        code_gen_type="html",
        max_rounds=2,
        timeout_seconds=2,
    )

    async def emit(event: str, data: dict[str, Any]) -> None:
        return None

    first = asyncio.create_task(runner.run(task="第一", workdir="one", emit=emit, turn_id="one"))
    await model.started.wait()
    with pytest.raises(ValueError, match="已有子代理"):
        await runner.run(task="第二", workdir="two", emit=emit, turn_id="two")
    model.release.set()
    assert await first == "完成"


async def test_subagent_stops_at_max_rounds(tmp_path: Path) -> None:
    class AlwaysToolModel:
        async def next_turn(
            self,
            request: ModelRequest,
            on_text: TextEmitter,
        ) -> ModelReply:
            return ModelReply(
                tool_calls=[
                    ToolCall(
                        id=f"write-{len(request.messages)}",
                        name="write_file",
                        arguments={"path": "loop.txt", "content": "loop"},
                    )
                ]
            )

    runner = SubagentRunner(
        Workspace(tmp_path),
        lambda _: AlwaysToolModel(),
        code_gen_type="html",
        max_rounds=2,
        timeout_seconds=1,
    )
    events: list[str] = []

    async def emit(event: str, data: dict[str, Any]) -> None:
        events.append(event)

    with pytest.raises(ValueError, match="最大执行轮数"):
        await runner.run(task="循环", workdir="loop", emit=emit, turn_id="loop-turn")
    assert events == ["subagent_started", "subagent_failed"]
