import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from conftest import AppContext
from sqlalchemy import select

from app.generation.adapters.executor import AgentTurnExecutor
from app.generation.adapters.model import ModelReply, ModelRequest, TextEmitter
from app.generation.application.runtime import GenerationRuntime
from app.generation.domain.events import EventType
from app.generation.ports.model import AgentModel
from app.models import ChatHistory


def _parse_sse(body: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in body.strip().split("\n\n"):
        if not block.strip():
            continue
        event: dict[str, Any] = {"data": None}
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


def _install_model(context: AppContext, model: AgentModel) -> None:
    context.app.state.generation_runtime = GenerationRuntime(
        AgentTurnExecutor(
            context.session_factory,
            Path(context.app.state.settings.workspace_root),
            lambda _: model,
        )
    )


class QueueModel:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def next_turn(self, request: ModelRequest, on_text: TextEmitter) -> ModelReply:
        del request
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            await self.release.wait()
            await on_text("首个回合完成。")
            return ModelReply(text="首个回合完成。")
        text = f"第 {self.calls} 个回合完成。"
        await on_text(text)
        return ModelReply(text=text)


async def _wait_until(predicate: Callable[[], bool], max_wait: float = 3.0) -> None:
    async with asyncio.timeout(max_wait):
        while not predicate():  # noqa: ASYNC110
            await asyncio.sleep(0.01)


async def _collect_queue_events(
    runtime: GenerationRuntime,
    app_id: int,
    stop_when: Callable[[dict[str, Any]], bool],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    async def collect() -> None:
        async for event in runtime.stream_queue(app_id, 0):
            item = {"event": event.event, "data": event.data}
            events.append(item)
            if stop_when(item):
                return

    await asyncio.wait_for(collect(), timeout=3)
    return events


async def test_queue_stream_tracks_active_turn_start_and_finish(
    app_context: AppContext,
) -> None:
    _, app_id = await _prepare_app(app_context, "active-turn-state-user")
    model = QueueModel()
    _install_model(app_context, model)
    runtime = app_context.app.state.generation_runtime

    collector = asyncio.create_task(
        _collect_queue_events(
            runtime,
            app_id,
            lambda event: (
                event["event"] == "active_turn_changed"
                and event["data"]["activeTurn"]["active"] is False
            ),
        )
    )
    await asyncio.sleep(0)
    generation = asyncio.create_task(
        app_context.client.post(
            "/api/app/chat/gen/code",
            json={"appId": app_id, "message": "跟踪活动回合", "requestId": "active-turn-state"},
        )
    )
    await asyncio.wait_for(model.started.wait(), timeout=3)
    model.release.set()
    response = await asyncio.wait_for(generation, timeout=5)
    assert response.status_code == 200

    events = await collector
    running = next(
        event
        for event in events
        if event["event"] == "active_turn_changed" and event["data"]["activeTurn"]["active"] is True
    )
    finished = next(
        event
        for event in events
        if event["event"] == "active_turn_changed"
        and event["data"]["activeTurn"]["active"] is False
    )
    assert running["data"]["activeTurn"]["status"] == "running"
    assert running["data"]["activeTurn"]["replayableFromZero"] is True
    assert finished["data"]["activeTurn"]["turnId"] == "active-turn-state"
    assert finished["data"]["activeTurn"]["status"] == "completed"
    assert not any(event["event"] == "user_message_consumed" for event in events)
    assert (
        finished["data"]["activeTurn"]["firstEventId"]
        == finished["data"]["activeTurn"]["lastEventId"]
    )


async def test_followup_pauses_on_interrupt_then_resume_consumes_once(
    app_context: AppContext,
) -> None:
    _, app_id = await _prepare_app(app_context, "queue-resume-user")
    model = QueueModel()
    _install_model(app_context, model)
    runtime = app_context.app.state.generation_runtime

    generation = asyncio.create_task(
        app_context.client.post(
            "/api/app/chat/gen/code",
            json={"appId": app_id, "message": "先执行长任务", "requestId": "queue-turn-1"},
        )
    )
    await asyncio.wait_for(model.started.wait(), timeout=3)

    enqueue = await app_context.client.post(
        "/api/app/chat/queue/control",
        json={
            "appId": app_id,
            "action": "enqueue",
            "commandId": "queued-1",
            "message": "排队内容",
        },
    )
    assert enqueue.json()["data"]["queue"]["queuedItems"][0]["commandId"] == "queued-1"

    interrupt = await app_context.client.post(
        "/api/app/chat/turn/control",
        json={
            "appId": app_id,
            "turnId": "queue-turn-1",
            "action": "interrupt",
            "commandId": "stop-1",
        },
    )
    assert interrupt.json()["data"]["status"] == "accepted"
    model.release.set()
    await asyncio.wait_for(generation, timeout=5)

    paused_events = await _collect_queue_events(
        runtime,
        app_id,
        lambda event: event["event"] == "queue_snapshot" and len(event["data"]["pausedItems"]) == 1,
    )
    assert paused_events[0]["data"]["pausedItems"][0]["commandId"] == "queued-1"
    await asyncio.sleep(0.05)
    assert model.calls == 1

    resume = await app_context.client.post(
        "/api/app/chat/queue/control",
        json={"appId": app_id, "action": "resume", "commandId": "resume-1"},
    )
    assert resume.json()["data"]["status"] == "accepted"
    await _wait_until(lambda: model.calls >= 2)

    consumed_events = await _collect_queue_events(
        runtime,
        app_id,
        lambda event: (
            event["event"] == "user_message_consumed" and event["data"]["commandId"] == "queued-1"
        ),
    )
    consumed = next(
        event
        for event in consumed_events
        if event["event"] == "user_message_consumed" and event["data"]["commandId"] == "queued-1"
    )
    assert consumed["data"]["mode"] == "followup"
    assert consumed["data"]["text"] == "排队内容"

    async with app_context.session_factory() as session:
        rows = (
            await session.scalars(
                select(ChatHistory).where(
                    ChatHistory.app_id == app_id,
                    ChatHistory.message_kind == "user",
                    ChatHistory.message == "排队内容",
                )
            )
        ).all()
    assert len(rows) == 1
    assert rows[0].turn_id == "queued-1"


async def test_steer_queued_item_consumes_once_in_current_turn(app_context: AppContext) -> None:
    _, app_id = await _prepare_app(app_context, "queue-steer-user")
    model = QueueModel()
    _install_model(app_context, model)
    runtime = app_context.app.state.generation_runtime

    generation = asyncio.create_task(
        app_context.client.post(
            "/api/app/chat/gen/code",
            json={"appId": app_id, "message": "先执行长任务", "requestId": "steer-turn-1"},
        )
    )
    await asyncio.wait_for(model.started.wait(), timeout=3)

    await app_context.client.post(
        "/api/app/chat/queue/control",
        json={
            "appId": app_id,
            "action": "enqueue",
            "commandId": "queued-steer-1",
            "message": "插话内容",
        },
    )
    steer = await app_context.client.post(
        "/api/app/chat/queue/control",
        json={
            "appId": app_id,
            "action": "steer",
            "commandId": "steer-request-1",
            "itemId": "queued-steer-1",
        },
    )
    payload = steer.json()["data"]
    assert payload["status"] == "accepted"
    assert payload["queue"]["queuedItems"] == []

    model.release.set()
    response = await asyncio.wait_for(generation, timeout=5)
    events = _parse_sse(response.text)
    steering_event = next(
        event for event in events if event["event"] == EventType.STEERING_CONSUMED
    )
    assert steering_event["data"]["items"] == [{"commandId": "queued-steer-1", "text": "插话内容"}]

    queue_events = await _collect_queue_events(
        runtime,
        app_id,
        lambda event: (
            event["event"] == "user_message_consumed"
            and event["data"]["commandId"] == "queued-steer-1"
        ),
    )
    consumed = next(
        event
        for event in queue_events
        if event["event"] == "user_message_consumed"
        and event["data"]["commandId"] == "queued-steer-1"
    )
    assert consumed["data"]["mode"] == "steer"
    assert consumed["data"]["turnId"] == "steer-turn-1"

    async with app_context.session_factory() as session:
        rows = (
            await session.scalars(
                select(ChatHistory).where(
                    ChatHistory.app_id == app_id,
                    ChatHistory.message_kind == "user",
                    ChatHistory.message == "插话内容",
                )
            )
        ).all()
    assert len(rows) == 1


async def test_new_input_can_clear_paused_queue_before_sending(app_context: AppContext) -> None:
    _, app_id = await _prepare_app(app_context, "queue-clear-user")
    model = QueueModel()
    _install_model(app_context, model)

    generation = asyncio.create_task(
        app_context.client.post(
            "/api/app/chat/gen/code",
            json={"appId": app_id, "message": "先执行长任务", "requestId": "clear-turn-1"},
        )
    )
    await asyncio.wait_for(model.started.wait(), timeout=3)
    for index in (1, 2):
        await app_context.client.post(
            "/api/app/chat/queue/control",
            json={
                "appId": app_id,
                "action": "enqueue",
                "commandId": f"old-{index}",
                "message": f"旧排队 {index}",
            },
        )
    await app_context.client.post(
        "/api/app/chat/turn/control",
        json={
            "appId": app_id,
            "turnId": "clear-turn-1",
            "action": "interrupt",
            "commandId": "clear-stop-1",
        },
    )

    send = await app_context.client.post(
        "/api/app/chat/queue/control",
        json={
            "appId": app_id,
            "action": "enqueue",
            "commandId": "new-message-1",
            "message": "新消息",
            "clearPaused": True,
        },
    )
    snapshot = send.json()["data"]["queue"]
    assert snapshot["pausedItems"] == []
    assert [item["message"] for item in snapshot["queuedItems"]] == ["新消息"]

    model.release.set()
    await asyncio.wait_for(generation, timeout=5)
    await _wait_until(lambda: model.calls >= 2)

    async with app_context.session_factory() as session:
        users = (
            await session.scalars(
                select(ChatHistory).where(
                    ChatHistory.app_id == app_id,
                    ChatHistory.message_kind == "user",
                )
            )
        ).all()
    messages = [row.message for row in users]
    assert "新消息" in messages
    assert "旧排队 1" not in messages
    assert "旧排队 2" not in messages
