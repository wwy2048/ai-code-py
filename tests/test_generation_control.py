import asyncio
import json
from pathlib import Path

from conftest import AppContext
from sqlalchemy import select

from app.generation.adapters.executor import AgentTurnExecutor
from app.generation.adapters.model import ModelReply, ModelRequest, TextEmitter, ToolCall
from app.generation.application.runtime import GenerationRuntime
from app.generation.domain.messages import UserMessage
from app.models import ChatHistory


def _parse_sse(body: str) -> list[dict]:
    events: list[dict] = []
    for block in body.strip().split("\n\n"):
        if not block.strip():
            continue
        event: dict = {"data": None}
        for line in block.splitlines():
            if line.startswith("id: "):
                event["id"] = int(line[4:])
            elif line.startswith("event: "):
                event["event"] = line[7:]
            elif line.startswith("data: "):
                event["data"] = json.loads(line[6:])
        events.append(event)
    return events


async def _prepare_app(context: AppContext, account: str) -> tuple[int, int]:
    register = await context.client.post(
        "/api/user/register",
        json={
            "userAccount": account,
            "userPassword": "password123",
            "checkPassword": "password123",
        },
    )
    user_id = register.json()["data"]
    login = await context.client.post(
        "/api/user/login",
        json={"userAccount": account, "userPassword": "password123"},
    )
    assert login.json()["code"] == 0
    created = await context.client.post(
        "/api/app/create",
        json={"initPrompt": "创建一个 HTML 页面"},
    )
    return user_id, created.json()["data"]


def _install_model(context: AppContext, model: object) -> None:
    context.app.state.generation_runtime = GenerationRuntime(
        AgentTurnExecutor(
            context.session_factory,
            Path(context.app.state.settings.workspace_root),
            lambda _: model,
        )
    )


class SteeringModel:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def next_turn(self, request: ModelRequest, on_text: TextEmitter) -> ModelReply:
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            await self.release.wait()
            text = "第一段内容。"
            await on_text(text)
            return ModelReply(
                text=text,
                tool_calls=[
                    ToolCall(
                        id="write-index",
                        name="write_file",
                        arguments={"path": "index.html", "content": "<h1>first</h1>"},
                    )
                ],
            )
        if any(
            isinstance(message, UserMessage) and "补充标题" in message.content
            for message in request.messages
        ):
            text = "已按插话补充标题。"
        else:
            text = "普通完成。"
        await on_text(text)
        return ModelReply(text=text)


class InterruptModel:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def next_turn(self, request: ModelRequest, on_text: TextEmitter) -> ModelReply:
        del request
        await on_text("这段内容不应进入持久化轨迹。")
        self.started.set()
        await self.release.wait()
        return ModelReply(text="不会完成")


class RedirectModel:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def next_turn(self, request: ModelRequest, on_text: TextEmitter) -> ModelReply:
        self.calls += 1
        if self.calls == 1:
            await on_text("旧计划。")
            self.started.set()
            await self.release.wait()
            return ModelReply(text="不会完成")
        texts = [
            message.content for message in request.messages if isinstance(message, UserMessage)
        ]
        assert texts[-1] == "改成蓝色主题"
        text = "新计划：蓝色主题。"
        await on_text(text)
        return ModelReply(text=text)


async def test_steering_is_consumed_at_next_model_step(app_context: AppContext) -> None:
    user_id, app_id = await _prepare_app(app_context, "steering-user")
    model = SteeringModel()
    _install_model(app_context, model)

    generation = asyncio.create_task(
        app_context.client.post(
            "/api/app/chat/gen/code",
            json={"appId": app_id, "message": "先创建页面", "requestId": "steer-turn"},
        )
    )
    await asyncio.wait_for(model.started.wait(), timeout=3)
    control = await app_context.client.post(
        "/api/app/chat/turn/control",
        json={
            "appId": app_id,
            "turnId": "steer-turn",
            "action": "steer",
            "message": "补充标题",
            "commandId": "steer-command-1",
        },
    )
    assert control.json()["data"]["status"] == "accepted"
    model.release.set()
    response = await asyncio.wait_for(generation, timeout=5)

    events = _parse_sse(response.text)
    assert "steering_consumed" in [event["event"] for event in events]
    assert events[-1]["event"] == "turn_finished"
    assert events[-1]["data"]["status"] == "completed"
    assert all(event["data"]["sequence"] == event["id"] for event in events)

    async with app_context.session_factory() as session:
        rows = (
            await session.scalars(
                select(ChatHistory)
                .where(ChatHistory.app_id == app_id)
                .order_by(ChatHistory.sequence)
            )
        ).all()
    assert any(row.message_kind == "user" and row.message == "补充标题" for row in rows)
    assert any(row.message_kind == "turn_lifecycle" and row.message == "finished" for row in rows)
    assert any(row.message == "已按插话补充标题。" for row in rows)


async def test_interrupt_cancels_model_step_and_finishes_interrupted(
    app_context: AppContext,
) -> None:
    user_id, app_id = await _prepare_app(app_context, "interrupt-user")
    model = InterruptModel()
    _install_model(app_context, model)

    generation = asyncio.create_task(
        app_context.client.post(
            "/api/app/chat/gen/code",
            json={"appId": app_id, "message": "生成很长的内容", "requestId": "stop-turn"},
        )
    )
    await asyncio.wait_for(model.started.wait(), timeout=3)
    control = await app_context.client.post(
        "/api/app/chat/turn/control",
        json={
            "appId": app_id,
            "turnId": "stop-turn",
            "action": "interrupt",
            "commandId": "stop-command-1",
        },
    )
    assert control.json()["data"]["status"] == "accepted"
    response = await asyncio.wait_for(generation, timeout=5)

    events = _parse_sse(response.text)
    event_types = [event["event"] for event in events]
    assert "step_cancelled" in event_types
    assert "turn_interrupted" in event_types
    assert events[-1]["event"] == "turn_finished"
    assert events[-1]["data"]["status"] == "interrupted"
    assert "error" not in event_types

    async with app_context.session_factory() as session:
        rows = (
            await session.scalars(
                select(ChatHistory)
                .where(ChatHistory.app_id == app_id)
                .order_by(ChatHistory.sequence)
            )
        ).all()
    assert not any(
        row.message_kind == "assistant" and row.visible and "不应进入持久化轨迹" in row.message
        for row in rows
    )
    assert any(
        row.message_kind == "assistant"
        and not row.visible
        and (row.payload or {}).get("phase") == "interrupted"
        for row in rows
    )
    assert any(
        row.message_kind == "turn_lifecycle" and (row.payload or {}).get("outcome") == "interrupted"
        for row in rows
    )


async def test_redirect_reenters_same_turn_with_new_instruction(
    app_context: AppContext,
) -> None:
    user_id, app_id = await _prepare_app(app_context, "redirect-user")
    model = RedirectModel()
    _install_model(app_context, model)

    generation = asyncio.create_task(
        app_context.client.post(
            "/api/app/chat/gen/code",
            json={"appId": app_id, "message": "创建红色页面", "requestId": "redirect-turn"},
        )
    )
    await asyncio.wait_for(model.started.wait(), timeout=3)
    control = await app_context.client.post(
        "/api/app/chat/turn/control",
        json={
            "appId": app_id,
            "turnId": "redirect-turn",
            "action": "redirect",
            "message": "改成蓝色主题",
            "commandId": "redirect-command-1",
        },
    )
    assert control.json()["data"]["status"] == "accepted"
    response = await asyncio.wait_for(generation, timeout=5)

    events = _parse_sse(response.text)
    event_types = [event["event"] for event in events]
    assert "step_cancelled" in event_types
    assert "redirect_applied" in event_types
    assert events[-1]["event"] == "turn_finished"
    assert events[-1]["data"]["status"] == "completed"
    assert any(
        event["event"] == "text_delta" and "新计划：蓝色主题" in event["data"].get("text", "")
        for event in events
    )

    async with app_context.session_factory() as session:
        rows = (
            await session.scalars(
                select(ChatHistory)
                .where(ChatHistory.app_id == app_id)
                .order_by(ChatHistory.sequence)
            )
        ).all()
    assert any(row.message_kind == "user" and row.message == "改成蓝色主题" for row in rows)
    assert sum(row.message_kind == "user" for row in rows) == 2


async def test_control_command_is_idempotent_and_stale_target_is_rejected(
    app_context: AppContext,
) -> None:
    user_id, app_id = await _prepare_app(app_context, "control-idempotent-user")
    model = InterruptModel()
    _install_model(app_context, model)

    generation = asyncio.create_task(
        app_context.client.post(
            "/api/app/chat/gen/code",
            json={"appId": app_id, "message": "生成", "requestId": "active-turn"},
        )
    )
    await asyncio.wait_for(model.started.wait(), timeout=3)

    stale = await app_context.client.post(
        "/api/app/chat/turn/control",
        json={
            "appId": app_id,
            "turnId": "other-turn",
            "action": "steer",
            "message": "错误目标",
            "commandId": "stale-command",
        },
    )
    assert stale.json()["data"]["status"] == "stale"

    payload = {
        "appId": app_id,
        "turnId": "active-turn",
        "action": "interrupt",
        "commandId": "duplicate-stop",
    }
    first = await app_context.client.post("/api/app/chat/turn/control", json=payload)
    second = await app_context.client.post("/api/app/chat/turn/control", json=payload)
    assert first.json()["data"] == second.json()["data"]
    await asyncio.wait_for(generation, timeout=5)


class RecordingJournal:
    def __init__(self) -> None:
        self.tool_calls: list[str] = []
        self.tool_results: list[tuple[str, str, bool]] = []
        self.placeholders: list[str] = []

    async def append_user(self, message: str) -> None:
        del message

    async def append_assistant(self, message: object, *, batch_id: str | None) -> None:
        del message, batch_id

    async def append_assistant_placeholder(self, *, batch_id: str) -> None:
        self.placeholders.append(batch_id)

    async def append_tool_call(self, call: object, *, batch_id: str) -> None:
        del batch_id
        self.tool_calls.append(call.id)

    async def append_tool_result(
        self,
        call: object,
        result: object,
        *,
        batch_id: str,
    ) -> None:
        del batch_id
        self.tool_results.append((call.id, result.output, result.ok))

    async def append_model_call(self, **kwargs: object) -> None:
        del kwargs

    async def commit(self) -> None:
        return None


class TwoToolModel:
    async def next_turn(self, request: ModelRequest, on_text: TextEmitter) -> ModelReply:
        del request, on_text
        return ModelReply(
            text="准备执行两个工具。",
            tool_calls=[
                ToolCall(id="tool-a", name="tool_a", arguments={}),
                ToolCall(id="tool-b", name="tool_b", arguments={}),
            ],
        )


class BlockingToolRegistry:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.executed: list[str] = []
        self.definitions: list[dict] = []

    def public_input(self, call: ToolCall) -> dict:
        del call
        return {}

    async def execute(self, call: ToolCall) -> object:
        from app.generation.adapters.tools import ToolExecutionResult

        self.executed.append(call.id)
        if call.id == "tool-a":
            self.started.set()
            await self.release.wait()
        return ToolExecutionResult(
            ok=True,
            output=f"{call.id} done",
            public_input={},
            duration_ms=1,
        )


async def test_stop_during_tool_waits_current_tool_and_skips_rest() -> None:
    from app.generation.application.control import TurnControl
    from app.generation.application.loop import AgentLoop, TurnState

    registry = BlockingToolRegistry()
    journal = RecordingJournal()
    control = TurnControl()
    loop = AgentLoop(
        model=TwoToolModel(),
        tools=registry,  # type: ignore[arg-type]
        journal=journal,  # type: ignore[arg-type]
        emit=lambda event, payload: asyncio.sleep(0),
        control=control,
    )
    task = asyncio.create_task(loop.run_phase(TurnState(turn_id="tool-stop-turn")))
    await asyncio.wait_for(registry.started.wait(), timeout=2)
    control.stop()
    registry.release.set()
    result = await asyncio.wait_for(task, timeout=2)

    assert result.interrupted is True
    assert registry.executed == ["tool-a"]
    assert journal.tool_calls == ["tool-a", "tool-b"]
    assert journal.tool_results == [
        ("tool-a", "tool-a done", True),
        ("tool-b", "工具调用因用户中断而未执行。", False),
    ]
    assert journal.placeholders


class FinalSteeringModel:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def next_turn(self, request: ModelRequest, on_text: TextEmitter) -> ModelReply:
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            await self.release.wait()
            text = "初稿。"
        else:
            assert any(
                isinstance(message, UserMessage) and message.content == "改成终稿"
                for message in request.messages
            )
            text = "终稿。"
        await on_text(text)
        return ModelReply(text=text)


async def test_steering_arriving_during_final_answer_continues_current_turn(
    app_context: AppContext,
) -> None:
    user_id, app_id = await _prepare_app(app_context, "final-steering-user")
    model = FinalSteeringModel()
    _install_model(app_context, model)

    generation = asyncio.create_task(
        app_context.client.post(
            "/api/app/chat/gen/code",
            json={"appId": app_id, "message": "先写初稿", "requestId": "final-steer-turn"},
        )
    )
    await asyncio.wait_for(model.started.wait(), timeout=3)
    control = await app_context.client.post(
        "/api/app/chat/turn/control",
        json={
            "appId": app_id,
            "turnId": "final-steer-turn",
            "action": "steer",
            "message": "改成终稿",
            "commandId": "final-steer-command",
        },
    )
    assert control.json()["data"]["status"] == "accepted"
    model.release.set()
    response = await asyncio.wait_for(generation, timeout=5)

    events = _parse_sse(response.text)
    assert [event["event"] for event in events].count("steering_consumed") == 1
    assert events[-1]["data"]["status"] == "completed"
    assert model.calls == 2
    assert any(
        event["event"] == "text_delta" and event["data"].get("text") == "终稿。" for event in events
    )
