import asyncio
import json
from pathlib import Path

from conftest import AppContext, StaticCodeGenTypeRouter
from sqlalchemy import func, select

from app.codegen_router import CodeGenType
from app.generation.adapters.executor import AgentTurnExecutor
from app.generation.adapters.model import (
    ModelReply,
    ModelRequest,
    ScriptedMultiFileModel,
    ScriptedVueModel,
    TextEmitter,
    ToolCall,
    UnavailableAgentModel,
)
from app.generation.adapters.vue_project import BuildResult, VueProjectBuilder
from app.generation.application.runtime import GenerationRuntime
from app.generation.domain.messages import ToolResultMessage, UserMessage
from app.generation.domain.turns import TurnRequest
from app.models import ChatHistory, ModelCallLog


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


async def _register_login_and_create_app(app_context: AppContext) -> tuple[int, int]:
    register = await app_context.client.post(
        "/api/user/register",
        json={
            "userAccount": "generator-user",
            "userPassword": "password123",
            "checkPassword": "password123",
        },
    )
    user_id = register.json()["data"]
    login = await app_context.client.post(
        "/api/user/login",
        json={"userAccount": "generator-user", "userPassword": "password123"},
    )
    assert login.json()["code"] == 0
    created = await app_context.client.post(
        "/api/app/create",
        json={"initPrompt": "创建一个 HTML 页面"},
    )
    return user_id, created.json()["data"]


async def test_html_generation_runs_tool_loop_streams_events_and_persists_session(
    app_context: AppContext,
) -> None:
    user_id, app_id = await _register_login_and_create_app(app_context)
    request_id = "turn-html-1"

    response = await app_context.client.post(
        "/api/app/chat/gen/code",
        json={
            "appId": app_id,
            "message": "生成一个蓝色主题的个人主页",
            "requestId": request_id,
        },
    )

    assert response.status_code == 200
    events = _parse_sse(response.text)
    event_types = [event["event"] for event in events]
    assert event_types[0] == "turn_started"
    assert "text_delta" in event_types
    assert "tool_started" in event_types
    assert "tool_finished" in event_types
    tool_finished = next(event for event in events if event["event"] == "tool_finished")
    assert tool_finished["data"]["sizeBytes"] > 0
    tool_started = next(event for event in events if event["event"] == "tool_started")
    assert tool_started["data"]["batchId"] == tool_finished["data"]["batchId"]
    assert event_types[-1] == "turn_finished"
    assert all(event["data"]["turnId"] == request_id for event in events)
    assert all(event["data"]["sequence"] == event["id"] for event in events)
    assert [event["id"] for event in events] == sorted(event["id"] for event in events)
    assert events[-1]["data"]["status"] == "completed"

    workspace = Path(app_context.app.state.settings.workspace_root) / f"html_{app_id}"
    html = (workspace / "index.html").read_text(encoding="utf-8")
    assert "蓝色主题的个人主页" in html
    assert "<!doctype html>" in html.lower()

    async with app_context.session_factory() as session:
        rows = (
            await session.scalars(
                select(ChatHistory)
                .where(ChatHistory.app_id == app_id)
                .order_by(ChatHistory.sequence)
            )
        ).all()
    assert {row.message_kind for row in rows} >= {"user", "assistant", "tool_call", "tool_result"}
    assert all(row.turn_id == request_id for row in rows)
    sequences = [row.sequence for row in rows]
    assert all(sequence is not None for sequence in sequences)
    assert sequences == sorted(sequence for sequence in sequences if sequence is not None)
    assert all(row.user_id == user_id for row in rows)

    async with app_context.session_factory() as session:
        model_logs = (
            await session.scalars(select(ModelCallLog).where(ModelCallLog.app_id == app_id))
        ).all()
    assert len(model_logs) == 2
    assert all(log.status == "success" for log in model_logs)
    assert all(log.duration_ms >= 0 for log in model_logs)
    assert all(log.retry_count == 0 for log in model_logs)

    visible_history = await app_context.client.get(f"/api/chatHistory/app/{app_id}")
    history_data = visible_history.json()["data"]
    visible_records = history_data["records"]
    assert [record["messageType"] for record in visible_records] == ["ai", "ai", "user"]
    assert visible_records[-1]["message"] == "生成一个蓝色主题的个人主页"
    assistant_messages = [
        record["message"] for record in visible_records if record["messageType"] == "ai"
    ]
    assert assistant_messages == [
        "页面已生成，可以直接在右侧预览。",
        "我将创建 index.html。",
    ]
    assert all("正在执行工具" not in message for message in assistant_messages)

    parts = history_data["partsByTurn"][request_id]
    assert [part["type"] for part in parts] == [
        "assistant_text",
        "tool_group",
        "assistant_text",
    ]
    assert parts[0]["content"] == "我将创建 index.html。"
    assert parts[1]["groupId"]
    assert parts[2]["content"] == "页面已生成，可以直接在右侧预览。"
    assert len(parts[1]["tools"]) == 1
    activity = parts[1]["tools"][0]
    assert activity["batchId"] == tool_started["data"]["batchId"]
    assert activity["toolName"] == "write_file"
    assert activity["status"] == "completed"
    assert activity["input"] == {"path": "index.html"}
    assert activity["outputPreview"]


async def test_same_request_id_does_not_create_duplicate_turn_and_can_resume(
    app_context: AppContext,
) -> None:
    _, app_id = await _register_login_and_create_app(app_context)
    payload = {
        "appId": app_id,
        "message": "生成一个页面",
        "requestId": "turn-idempotent-1",
    }

    first = await app_context.client.post("/api/app/chat/gen/code", json=payload)
    first_events = _parse_sse(first.text)
    resume_after = first_events[1]["id"]

    resumed = await app_context.client.post(
        "/api/app/chat/gen/code",
        json=payload,
        headers={"Last-Event-ID": str(resume_after)},
    )
    resumed_events = _parse_sse(resumed.text)

    assert resumed_events
    assert resumed_events[0]["id"] > resume_after
    assert resumed_events[-1]["event"] == "turn_finished"

    final_id = first_events[-1]["id"]
    completed_resume = await app_context.client.post(
        "/api/app/chat/gen/code",
        json=payload,
        headers={"Last-Event-ID": str(final_id)},
    )
    completed_events = _parse_sse(completed_resume.text)
    assert completed_events
    assert completed_events[-1]["event"] == "turn_finished"
    assert completed_events[-1]["data"]["sequence"] == completed_events[-1]["id"]

    async with app_context.session_factory() as session:
        user_messages = await session.scalar(
            select(func.count(ChatHistory.id)).where(
                ChatHistory.app_id == app_id,
                ChatHistory.message_kind == "user",
            )
        )
    assert user_messages == 1


async def test_same_request_id_with_different_message_is_rejected(
    app_context: AppContext,
) -> None:
    _, app_id = await _register_login_and_create_app(app_context)
    base_payload = {
        "appId": app_id,
        "message": "第一条消息",
        "requestId": "turn-conflict-1",
    }
    first = await app_context.client.post("/api/app/chat/gen/code", json=base_payload)
    assert first.status_code == 200

    conflict = await app_context.client.post(
        "/api/app/chat/gen/code",
        json={**base_payload, "message": "另一条消息"},
    )
    assert conflict.json()["code"] == 40000


async def test_generation_continues_without_an_active_subscriber(
    app_context: AppContext,
) -> None:
    user_id, app_id = await _register_login_and_create_app(app_context)
    manager = app_context.app.state.generation_runtime
    job = await manager.start_turn(
        TurnRequest(
            app_id=app_id,
            user_id=user_id,
            code_gen_type="html",
            message="后台继续执行",
            turn_id="turn-without-subscriber",
        )
    )

    async with asyncio.timeout(3):
        async with job.condition:
            while not job.completed:
                await job.condition.wait()

    workspace = Path(app_context.app.state.settings.workspace_root) / f"html_{app_id}"
    assert (workspace / "index.html").exists()
    assert job.events[-1].event == "turn_finished"


class PathEscapeModel:
    async def next_turn(self, request: ModelRequest, on_text: TextEmitter) -> ModelReply:
        if any(isinstance(message, ToolResultMessage) for message in request.messages):
            await on_text("越界工具已处理。")
            return ModelReply(text="越界工具已处理。")
        await on_text("尝试越界写入。")
        return ModelReply(
            text="尝试越界写入。",
            tool_calls=[
                ToolCall(
                    id="escape-call",
                    name="write_file",
                    arguments={"path": "../escape.html", "content": "<h1>escape</h1>"},
                )
            ],
        )


async def test_path_escape_is_reported_as_failed_tool_without_writing(
    app_context: AppContext,
) -> None:
    user_id, app_id = await _register_login_and_create_app(app_context)
    model = PathEscapeModel()
    executor = AgentTurnExecutor(
        app_context.session_factory,
        Path(app_context.app.state.settings.workspace_root),
        lambda _: model,
    )
    app_context.app.state.generation_runtime = GenerationRuntime(executor)

    response = await app_context.client.post(
        "/api/app/chat/gen/code",
        json={
            "appId": app_id,
            "message": "尝试越界",
            "requestId": "turn-path-escape",
        },
    )
    events = _parse_sse(response.text)
    tool_finished = next(event for event in events if event["event"] == "tool_finished")

    assert tool_finished["data"]["status"] == "failed"
    assert not (Path(app_context.app.state.settings.workspace_root).parent / "escape.html").exists()
    assert events[-1]["event"] == "turn_finished"
    history = (await app_context.client.get(f"/api/chatHistory/app/{app_id}")).json()["data"]
    parts = history["partsByTurn"]["turn-path-escape"]
    assert [part["type"] for part in parts] == [
        "assistant_text",
        "tool_group",
        "assistant_text",
    ]
    activity = parts[1]["tools"][0]
    assert activity["status"] == "failed"
    assert activity["isError"] is True
    assert activity["input"] == {"path": "../escape.html"}


class FailingModel:
    async def next_turn(self, request: ModelRequest, on_text: TextEmitter) -> ModelReply:
        raise RuntimeError("包含内部细节的模型错误")


async def test_model_failure_emits_error_then_failed_turn_without_leaking_detail(
    app_context: AppContext,
) -> None:
    user_id, app_id = await _register_login_and_create_app(app_context)
    executor = AgentTurnExecutor(
        app_context.session_factory,
        Path(app_context.app.state.settings.workspace_root),
        lambda _: FailingModel(),
    )
    app_context.app.state.generation_runtime = GenerationRuntime(executor)

    response = await app_context.client.post(
        "/api/app/chat/gen/code",
        json={
            "appId": app_id,
            "message": "触发错误",
            "requestId": "turn-error",
        },
    )
    events = _parse_sse(response.text)

    assert [event["event"] for event in events[-2:]] == ["error", "turn_finished"]
    assert events[-1]["data"]["status"] == "failed"
    assert "内部细节" not in response.text
    async with app_context.session_factory() as session:
        failed_log = await session.scalar(
            select(ModelCallLog).where(ModelCallLog.turn_id == "turn-error")
        )
    assert failed_log is not None
    assert failed_log.status == "error"
    assert failed_log.error_type == "RuntimeError"
    assert failed_log.error_summary == "RuntimeError"
    assert failed_log.retry_count == 0


class RecoveringToolModel:
    def __init__(self) -> None:
        self.calls = 0

    async def next_turn(
        self,
        request: ModelRequest,
        on_text: TextEmitter,
    ) -> ModelReply:
        self.calls += 1
        if self.calls == 1:
            await on_text("第一次工具参数有误。")
            return ModelReply(
                text="第一次工具参数有误。",
                tool_calls=[
                    ToolCall(id="bad-tool", name="write_file", arguments={"path": "index.html"})
                ],
            )
        tool_result = next(
            message
            for message in reversed(request.messages)
            if isinstance(message, ToolResultMessage)
        )
        assert tool_result.is_error is True
        await on_text("已收到错误并修正。")
        return ModelReply(
            text="已收到错误并修正。",
            tool_calls=[
                ToolCall(
                    id="good-tool",
                    name="write_file",
                    arguments={"path": "index.html", "content": "<h1>fixed</h1>"},
                )
            ],
        )


class TruncatedToolCallModel:
    def __init__(self) -> None:
        self.calls = 0

    async def next_turn(
        self,
        request: ModelRequest,
        on_text: TextEmitter,
    ) -> ModelReply:
        self.calls += 1
        if self.calls == 1:
            return ModelReply(
                text="准备写入完整页面。",
                tool_calls=[
                    ToolCall(
                        id="truncated-write",
                        name="write_file",
                        arguments={"path": "index.html"},
                    )
                ],
                output_tokens=8000,
                stop_reason="max_tokens",
            )
        if self.calls == 2:
            failed = next(
                message
                for message in reversed(request.messages)
                if isinstance(message, ToolResultMessage)
            )
            assert failed.is_error is True
            assert "max_tokens" in failed.content
            return ModelReply(
                tool_calls=[
                    ToolCall(
                        id="chunked-write",
                        name="write_file",
                        arguments={"path": "index.html", "content": "<h1>chunked</h1>"},
                    )
                ]
            )
        return ModelReply(text="已按小片段完成写入。")


class TruncatedTextModel:
    def __init__(self) -> None:
        self.calls = 0

    async def next_turn(
        self,
        request: ModelRequest,
        on_text: TextEmitter,
    ) -> ModelReply:
        self.calls += 1
        if self.calls == 1:
            text = "我将一次性输出完整页面。"
            await on_text(text)
            return ModelReply(
                text=text,
                output_tokens=8000,
                stop_reason="max_tokens",
            )
        if self.calls == 2:
            continuation = next(
                message
                for message in reversed(request.messages)
                if isinstance(message, UserMessage) and "[系统续写指令]" in message.content
            )
            assert "max_tokens" in continuation.content
            return ModelReply(
                tool_calls=[
                    ToolCall(
                        id="continued-write",
                        name="write_file",
                        arguments={"path": "index.html", "content": "<h1>continued</h1>"},
                    )
                ]
            )
        return ModelReply(text="已恢复并完成页面。")


class DeltaStreamingModel:
    async def next_turn(
        self,
        request: ModelRequest,
        on_text: TextEmitter,
    ) -> ModelReply:
        await on_text("你好")
        await on_text("，世界")
        return ModelReply(text="你好，世界")


class TwoBatchModel:
    def __init__(self) -> None:
        self.calls = 0

    async def next_turn(
        self,
        request: ModelRequest,
        on_text: TextEmitter,
    ) -> ModelReply:
        self.calls += 1
        if self.calls == 1:
            await on_text("先写页面骨架。")
            return ModelReply(
                text="先写页面骨架。",
                tool_calls=[
                    ToolCall(
                        id="batch-one-write",
                        name="write_file",
                        arguments={"path": "index.html", "content": "<h1>old</h1>"},
                    )
                ],
            )
        if self.calls == 2:
            await on_text("骨架完成后更新标题。")
            return ModelReply(
                text="骨架完成后更新标题。",
                tool_calls=[
                    ToolCall(
                        id="batch-two-edit",
                        name="edit_file",
                        arguments={
                            "path": "index.html",
                            "old_text": "<h1>old</h1>",
                            "new_text": "<h1>new</h1>",
                        },
                    )
                ],
            )
        await on_text("两次工具批次已按顺序完成。")
        return ModelReply(text="两次工具批次已按顺序完成。")


async def test_one_turn_preserves_multiple_tool_batches_in_sequence(
    app_context: AppContext,
) -> None:
    _, app_id = await _register_login_and_create_app(app_context)
    app_context.app.state.generation_runtime = GenerationRuntime(
        AgentTurnExecutor(
            app_context.session_factory,
            Path(app_context.app.state.settings.workspace_root),
            lambda _: TwoBatchModel(),
        )
    )

    response = await app_context.client.post(
        "/api/app/chat/gen/code",
        json={
            "appId": app_id,
            "message": "分两批修改页面",
            "requestId": "turn-two-batches",
        },
    )
    events = _parse_sse(response.text)
    started_batches = [
        event["data"]["batchId"] for event in events if event["event"] == "tool_started"
    ]
    assert len(started_batches) == 2
    assert len(set(started_batches)) == 2

    history_data = (await app_context.client.get(f"/api/chatHistory/app/{app_id}")).json()["data"]
    parts = history_data["partsByTurn"]["turn-two-batches"]
    assert [part["type"] for part in parts] == [
        "assistant_text",
        "tool_group",
        "assistant_text",
        "tool_group",
        "assistant_text",
    ]
    assert [part.get("content") for part in parts if part["type"] == "assistant_text"] == [
        "先写页面骨架。",
        "骨架完成后更新标题。",
        "两次工具批次已按顺序完成。",
    ]
    groups = [part for part in parts if part["type"] == "tool_group"]
    assert len({group["groupId"] for group in groups}) == 2
    assert [group["tools"][0]["batchId"] for group in groups] == started_batches
    assert [group["tools"][0]["toolName"] for group in groups] == ["write_file", "edit_file"]
    assert all(group["tools"][0]["status"] == "completed" for group in groups)

    visible_ai_messages = [
        record["message"] for record in history_data["records"] if record["messageType"] == "ai"
    ]
    assert visible_ai_messages == [
        "两次工具批次已按顺序完成。",
        "骨架完成后更新标题。",
        "先写页面骨架。",
    ]

    async with app_context.session_factory() as session:
        assistant_rows = (
            await session.scalars(
                select(ChatHistory)
                .where(
                    ChatHistory.app_id == app_id,
                    ChatHistory.message_kind == "assistant",
                )
                .order_by(ChatHistory.sequence.asc())
            )
        ).all()
    assert [row.message for row in assistant_rows] == [
        "先写页面骨架。",
        "骨架完成后更新标题。",
        "两次工具批次已按顺序完成。",
    ]
    assert all((row.payload or {}).get("batchId") for row in assistant_rows)


class ConsecutiveToolRoundsModel:
    def __init__(self) -> None:
        self.calls = 0

    async def next_turn(
        self,
        request: ModelRequest,
        on_text: TextEmitter,
    ) -> ModelReply:
        self.calls += 1
        if self.calls == 1:
            await on_text("先写页面骨架。")
            return ModelReply(
                text="先写页面骨架。",
                tool_calls=[
                    ToolCall(
                        id="consecutive-write",
                        name="write_file",
                        arguments={"path": "index.html", "content": "<h1>one</h1>"},
                    )
                ],
            )
        if self.calls == 2:
            return ModelReply(
                tool_calls=[
                    ToolCall(
                        id="consecutive-edit-two",
                        name="edit_file",
                        arguments={
                            "path": "index.html",
                            "old_text": "<h1>one</h1>",
                            "new_text": "<h1>two</h1>",
                        },
                    )
                ],
            )
        if self.calls == 3:
            return ModelReply(
                tool_calls=[
                    ToolCall(
                        id="consecutive-edit-three",
                        name="edit_file",
                        arguments={
                            "path": "index.html",
                            "old_text": "<h1>two</h1>",
                            "new_text": "<h1>three</h1>",
                        },
                    )
                ],
            )
        await on_text("连续工具调用完成。")
        return ModelReply(text="连续工具调用完成。")


async def test_consecutive_tool_rounds_without_text_share_one_group(
    app_context: AppContext,
) -> None:
    _, app_id = await _register_login_and_create_app(app_context)
    app_context.app.state.generation_runtime = GenerationRuntime(
        AgentTurnExecutor(
            app_context.session_factory,
            Path(app_context.app.state.settings.workspace_root),
            lambda _: ConsecutiveToolRoundsModel(),
        )
    )

    await app_context.client.post(
        "/api/app/chat/gen/code",
        json={
            "appId": app_id,
            "message": "连续执行几次工具",
            "requestId": "turn-consecutive-tools",
        },
    )
    history_data = (await app_context.client.get(f"/api/chatHistory/app/{app_id}")).json()["data"]
    parts = history_data["partsByTurn"]["turn-consecutive-tools"]

    assert [part["type"] for part in parts] == [
        "assistant_text",
        "tool_group",
        "assistant_text",
    ]
    group = parts[1]
    assert len(group["tools"]) == 3
    assert len({tool["batchId"] for tool in group["tools"]}) == 3
    assert [tool["toolName"] for tool in group["tools"]] == [
        "write_file",
        "edit_file",
        "edit_file",
    ]
    assert all(tool["status"] == "completed" for tool in group["tools"])


async def test_streamed_deltas_are_concatenated_in_persisted_assistant_message(
    app_context: AppContext,
) -> None:
    _, app_id = await _register_login_and_create_app(app_context)
    app_context.app.state.generation_runtime = GenerationRuntime(
        AgentTurnExecutor(
            app_context.session_factory,
            Path(app_context.app.state.settings.workspace_root),
            lambda _: DeltaStreamingModel(),
        )
    )

    response = await app_context.client.post(
        "/api/app/chat/gen/code",
        json={"appId": app_id, "message": "测试流式拼接", "requestId": "turn-delta-join"},
    )

    assert '"status": "completed"' in response.text
    async with app_context.session_factory() as session:
        assistant = await session.scalar(
            select(ChatHistory).where(
                ChatHistory.app_id == app_id,
                ChatHistory.message_kind == "assistant",
            )
        )
    assert assistant is not None
    assert assistant.message == "你好，世界"


async def test_truncated_tool_call_is_fed_back_without_executing_partial_arguments(
    app_context: AppContext,
) -> None:
    user_id, app_id = await _register_login_and_create_app(app_context)
    model = TruncatedToolCallModel()
    app_context.app.state.generation_runtime = GenerationRuntime(
        AgentTurnExecutor(
            app_context.session_factory,
            Path(app_context.app.state.settings.workspace_root),
            lambda _: model,
        )
    )

    response = await app_context.client.post(
        "/api/app/chat/gen/code",
        json={"appId": app_id, "message": "生成大页面", "requestId": "turn-truncated"},
    )
    events = _parse_sse(response.text)

    assert model.calls == 3
    assert '"status": "completed"' in response.text
    failed = next(
        event
        for event in events
        if event["event"] == "tool_finished" and event["data"]["status"] == "failed"
    )
    assert "max_tokens" in failed["data"]["preview"]
    workspace = Path(app_context.app.state.settings.workspace_root) / f"html_{app_id}"
    assert (workspace / "index.html").read_text(encoding="utf-8") == "<h1>chunked</h1>"


async def test_truncated_text_output_is_continued_and_recovers(
    app_context: AppContext,
) -> None:
    user_id, app_id = await _register_login_and_create_app(app_context)
    model = TruncatedTextModel()
    app_context.app.state.generation_runtime = GenerationRuntime(
        AgentTurnExecutor(
            app_context.session_factory,
            Path(app_context.app.state.settings.workspace_root),
            lambda _: model,
        )
    )

    response = await app_context.client.post(
        "/api/app/chat/gen/code",
        json={"appId": app_id, "message": "生成大页面", "requestId": "turn-truncated-text"},
    )
    events = _parse_sse(response.text)

    assert model.calls == 3
    assert events[-1]["event"] == "turn_finished"
    assert events[-1]["data"]["status"] == "completed"
    assert not any(event["event"] == "error" for event in events)
    workspace = Path(app_context.app.state.settings.workspace_root) / f"html_{app_id}"
    assert (workspace / "index.html").read_text(encoding="utf-8") == "<h1>continued</h1>"
    async with app_context.session_factory() as session:
        continuation = await session.scalar(
            select(ChatHistory).where(
                ChatHistory.app_id == app_id,
                ChatHistory.visible.is_(False),
                ChatHistory.message.contains("[系统续写指令]"),
            )
        )
    assert continuation is not None


async def test_tool_argument_error_is_returned_to_model_and_recovered(
    app_context: AppContext,
) -> None:
    user_id, app_id = await _register_login_and_create_app(app_context)
    executor = AgentTurnExecutor(
        app_context.session_factory,
        Path(app_context.app.state.settings.workspace_root),
        lambda _: RecoveringToolModel(),
    )
    app_context.app.state.generation_runtime = GenerationRuntime(executor)

    response = await app_context.client.post(
        "/api/app/chat/gen/code",
        json={
            "appId": app_id,
            "message": "测试工具参数修复",
            "requestId": "turn-tool-recovery",
        },
    )
    events = _parse_sse(response.text)
    finished = [event for event in events if event["event"] == "tool_finished"]

    assert [event["data"]["status"] for event in finished] == ["failed", "completed"]
    workspace = Path(app_context.app.state.settings.workspace_root) / f"html_{app_id}"
    assert (workspace / "index.html").read_text(encoding="utf-8") == "<h1>fixed</h1>"


async def test_unavailable_model_returns_readable_failed_turn(
    app_context: AppContext,
) -> None:
    user_id, app_id = await _register_login_and_create_app(app_context)
    executor = AgentTurnExecutor(
        app_context.session_factory,
        Path(app_context.app.state.settings.workspace_root),
        lambda _: UnavailableAgentModel(),
    )
    app_context.app.state.generation_runtime = GenerationRuntime(executor)

    response = await app_context.client.post(
        "/api/app/chat/gen/code",
        json={
            "appId": app_id,
            "message": "缺少模型密钥",
            "requestId": "turn-no-key",
        },
    )
    events = _parse_sse(response.text)

    assert [event["event"] for event in events[-2:]] == ["error", "turn_finished"]
    assert events[-1]["data"]["status"] == "failed"
    assert "ANTHROPIC_API_KEY" not in response.text


async def test_multi_file_generation_writes_three_files_and_previews(
    app_context: AppContext,
) -> None:
    app_context.app.state.code_gen_type_router = StaticCodeGenTypeRouter(CodeGenType.MULTI_FILE)
    app_context.app.state.generation_runtime = GenerationRuntime(
        AgentTurnExecutor(
            app_context.session_factory,
            Path(app_context.app.state.settings.workspace_root),
            lambda _: ScriptedMultiFileModel(),
        )
    )
    await app_context.client.post(
        "/api/user/register",
        json={
            "userAccount": "multi-user",
            "userPassword": "password123",
            "checkPassword": "password123",
        },
    )
    await app_context.client.post(
        "/api/user/login",
        json={"userAccount": "multi-user", "userPassword": "password123"},
    )
    created = await app_context.client.post(
        "/api/app/create",
        json={"initPrompt": "创建一个多文件网站"},
    )
    app_id = created.json()["data"]

    response = await app_context.client.post(
        "/api/app/chat/gen/code",
        json={
            "appId": app_id,
            "message": "创建多文件页面",
            "requestId": "turn-multi-file",
        },
    )
    events = _parse_sse(response.text)
    finished = [event for event in events if event["event"] == "tool_finished"]

    workspace = Path(app_context.app.state.settings.workspace_root) / f"multi_file_{app_id}"
    assert {path.name for path in workspace.iterdir()} == {
        "index.html",
        "style.css",
        "script.js",
    }
    assert len(finished) == 3
    assert all(event["data"]["status"] == "completed" for event in finished)
    event_types = [event["event"] for event in events]
    assert "text_delta" in event_types
    assert event_types[-1] == "turn_finished"
    preview = await app_context.client.get(f"/api/static/multi_file_{app_id}/index.html")
    assert preview.status_code == 200
    html = preview.text
    assert "style.css" in html
    assert "script.js" in html
    assert (
        await app_context.client.get(f"/api/static/multi_file_{app_id}/style.css")
    ).status_code == 200
    assert (
        await app_context.client.get(f"/api/static/multi_file_{app_id}/script.js")
    ).status_code == 200
    async with app_context.session_factory() as session:
        tool_rows = await session.scalar(
            select(func.count(ChatHistory.id)).where(
                ChatHistory.app_id == app_id,
                ChatHistory.message_kind == "tool_call",
            )
        )
    assert tool_rows == 3

    history_data = (await app_context.client.get(f"/api/chatHistory/app/{app_id}")).json()["data"]
    parts = history_data["partsByTurn"]["turn-multi-file"]
    assert [part["type"] for part in parts] == [
        "assistant_text",
        "tool_group",
        "assistant_text",
    ]
    assert [tool["input"]["path"] for tool in parts[1]["tools"]] == [
        "index.html",
        "style.css",
        "script.js",
    ]


class MultiFileEditModel:
    async def next_turn(
        self,
        request: ModelRequest,
        on_text: TextEmitter,
    ) -> ModelReply:
        has_tool_result = isinstance(request.messages[-1], ToolResultMessage)
        prompt = next(
            message.content
            for message in reversed(request.messages)
            if isinstance(message, UserMessage)
        )
        if has_tool_result:
            await on_text("处理完成。")
            return ModelReply(text="处理完成。")
        if "修改" in prompt:
            await on_text("修改既有 HTML 标题。")
            return ModelReply(
                text="修改既有 HTML 标题。",
                tool_calls=[
                    ToolCall(
                        id="edit-title",
                        name="edit_file",
                        arguments={
                            "path": "index.html",
                            "old_text": "<h1>Multi File</h1>",
                            "new_text": "<h1>Updated Multi File</h1>",
                        },
                    )
                ],
            )
        return await ScriptedMultiFileModel().next_turn(request, on_text)


async def test_multi_file_follow_up_edits_existing_files(
    app_context: AppContext,
) -> None:
    app_context.app.state.code_gen_type_router = StaticCodeGenTypeRouter(CodeGenType.MULTI_FILE)
    model = MultiFileEditModel()
    app_context.app.state.generation_runtime = GenerationRuntime(
        AgentTurnExecutor(
            app_context.session_factory,
            Path(app_context.app.state.settings.workspace_root),
            lambda _: model,
        )
    )
    await app_context.client.post(
        "/api/user/register",
        json={
            "userAccount": "multi-edit-user",
            "userPassword": "password123",
            "checkPassword": "password123",
        },
    )
    await app_context.client.post(
        "/api/user/login",
        json={"userAccount": "multi-edit-user", "userPassword": "password123"},
    )
    app_id = (
        await app_context.client.post(
            "/api/app/create",
            json={"initPrompt": "创建多文件页面"},
        )
    ).json()["data"]
    await app_context.client.post(
        "/api/app/chat/gen/code",
        json={"appId": app_id, "message": "初始页面", "requestId": "multi-turn-1"},
    )

    await app_context.client.post(
        "/api/app/chat/gen/code",
        json={"appId": app_id, "message": "修改标题", "requestId": "multi-turn-2"},
    )

    workspace = Path(app_context.app.state.settings.workspace_root) / f"multi_file_{app_id}"
    html = (workspace / "index.html").read_text(encoding="utf-8")
    assert "<h1>Updated Multi File</h1>" in html
    assert (workspace / "style.css").exists()
    assert (workspace / "script.js").exists()


class RepairingVueBuilder(VueProjectBuilder):
    def __init__(self) -> None:
        self.calls = 0

    def build(self, root: Path) -> BuildResult:
        self.calls += 1
        if self.calls == 1:
            return BuildResult(False, "Element is missing end tag")
        dist = root / "dist"
        dist.mkdir(exist_ok=True)
        index = dist / "index.html"
        index.write_text("built", encoding="utf-8")
        return BuildResult(True, "build ok", index.stat().st_size)


async def test_vue_build_failure_is_fed_back_for_repair(app_context: AppContext) -> None:
    app_context.app.state.code_gen_type_router = StaticCodeGenTypeRouter(CodeGenType.VUE_PROJECT)
    builder = RepairingVueBuilder()
    app_context.app.state.generation_runtime = GenerationRuntime(
        AgentTurnExecutor(
            app_context.session_factory,
            Path(app_context.app.state.settings.workspace_root),
            lambda _: ScriptedVueModel(),
            vue_builder=builder,
        )
    )
    await app_context.client.post(
        "/api/user/register",
        json={
            "userAccount": "repair-vue",
            "userPassword": "password123",
            "checkPassword": "password123",
        },
    )
    await app_context.client.post(
        "/api/user/login", json={"userAccount": "repair-vue", "userPassword": "password123"}
    )
    app_id = (await app_context.client.post("/api/app/create", json={"initPrompt": "Vue"})).json()[
        "data"
    ]
    response = await app_context.client.post(
        "/api/app/chat/gen/code",
        json={"appId": app_id, "message": "生成 Vue", "requestId": "vue-repair"},
    )
    events = _parse_sse(response.text)
    builds = [
        e
        for e in events
        if e["event"] == "tool_finished" and e["data"].get("name") == "build_project"
    ]
    assert [e["data"]["status"] for e in builds] == ["failed", "completed"]
    assert builder.calls == 2
    workspace = Path(app_context.app.state.settings.workspace_root) / f"vue_project_{app_id}"
    assert (workspace / "dist/index.html").is_file()
    assert "Fixed Vue" in (workspace / "src/App.vue").read_text(encoding="utf-8")
    async with app_context.session_factory() as session:
        visible_users = (
            await session.scalars(
                select(ChatHistory).where(
                    ChatHistory.app_id == app_id,
                    ChatHistory.message_type == "user",
                    ChatHistory.visible.is_(True),
                )
            )
        ).all()
    assert all("构建失败" not in row.message for row in visible_users)


class AlwaysFailingVueBuilder(VueProjectBuilder):
    def build(self, root: Path) -> BuildResult:
        return BuildResult(False, "build failed for rollback test")


async def test_failed_vue_turn_restores_previous_source(app_context: AppContext) -> None:
    app_context.app.state.code_gen_type_router = StaticCodeGenTypeRouter(CodeGenType.VUE_PROJECT)
    app_context.app.state.generation_runtime = GenerationRuntime(
        AgentTurnExecutor(
            app_context.session_factory,
            Path(app_context.app.state.settings.workspace_root),
            lambda _: ScriptedVueModel(),
            vue_builder=AlwaysFailingVueBuilder(),
        )
    )
    await app_context.client.post(
        "/api/user/register",
        json={
            "userAccount": "rollback-vue",
            "userPassword": "password123",
            "checkPassword": "password123",
        },
    )
    await app_context.client.post(
        "/api/user/login", json={"userAccount": "rollback-vue", "userPassword": "password123"}
    )
    app_id = (await app_context.client.post("/api/app/create", json={"initPrompt": "Vue"})).json()[
        "data"
    ]
    await app_context.client.post(
        "/api/app/chat/gen/code",
        json={"appId": app_id, "message": "生成 Vue", "requestId": "vue-rollback"},
    )

    workspace = Path(app_context.app.state.settings.workspace_root) / f"vue_project_{app_id}"
    app_source = (workspace / "src/App.vue").read_text(encoding="utf-8")
    assert "<router-view />" in app_source
    assert "Fixed Vue" not in app_source


class EndlessHtmlToolModel:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def next_turn(self, request: ModelRequest, on_text: TextEmitter) -> ModelReply:
        self.requests.append(request)
        if any(
            isinstance(message, UserMessage) and "[系统收口指令]" in message.content
            for message in request.messages
        ):
            text = "已达到工具预算，页面最终状态已保留。"
            await on_text(text)
            return ModelReply(text=text)
        if request.tools:
            return ModelReply(
                text="继续完善页面。",
                tool_calls=[
                    ToolCall(
                        id=f"endless-write-{len(self.requests)}",
                        name="write_file",
                        arguments={"path": "index.html", "content": "<h1>工具循环收口</h1>"},
                    )
                ],
            )
        text = "已达到工具预算，页面最终状态已保留。"
        await on_text(text)
        return ModelReply(text=text)


async def test_exhausted_html_turn_forces_final_text(app_context: AppContext) -> None:
    model = EndlessHtmlToolModel()
    app_context.app.state.generation_runtime = GenerationRuntime(
        AgentTurnExecutor(
            app_context.session_factory,
            Path(app_context.app.state.settings.workspace_root),
            lambda _: model,
            max_rounds_per_phase=2,
        )
    )
    await app_context.client.post(
        "/api/user/register",
        json={
            "userAccount": "finalize-user",
            "userPassword": "password123",
            "checkPassword": "password123",
        },
    )
    await app_context.client.post(
        "/api/user/login", json={"userAccount": "finalize-user", "userPassword": "password123"}
    )
    app_id = (await app_context.client.post("/api/app/create", json={"initPrompt": "HTML"})).json()[
        "data"
    ]

    response = await app_context.client.post(
        "/api/app/chat/gen/code",
        json={"appId": app_id, "message": "生成页面", "requestId": "finalize-turn"},
    )
    events = _parse_sse(response.text)
    text_deltas = [event["data"]["text"] for event in events if event["event"] == "text_delta"]

    assert model.requests[-1].tools == model.requests[0].tools
    assert text_deltas[-1] == "已达到工具预算，页面最终状态已保留。"
    assert events[-2]["event"] == "text_delta"
    assert events[-1]["event"] == "turn_finished"
    assert events[-1]["data"]["status"] == "completed"

    async with app_context.session_factory() as session:
        last_assistant = await session.scalar(
            select(ChatHistory)
            .where(
                ChatHistory.app_id == app_id,
                ChatHistory.message_kind == "assistant",
                ChatHistory.visible.is_(True),
            )
            .order_by(ChatHistory.sequence.desc())
            .limit(1)
        )
    assert last_assistant is not None
    assert last_assistant.message == "已达到工具预算，页面最终状态已保留。"


class EndlessVueToolModel:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def next_turn(self, request: ModelRequest, on_text: TextEmitter) -> ModelReply:
        self.requests.append(request)
        if any(
            isinstance(message, UserMessage) and "[系统收口指令]" in message.content
            for message in request.messages
        ):
            text = "Vue 工具预算已耗尽，当前通过校验的页面已保留。"
            await on_text(text)
            return ModelReply(text=text)
        if request.tools:
            return ModelReply(
                text="继续调整 Vue 页面。",
                tool_calls=[
                    ToolCall(
                        id=f"vue-write-{len(self.requests)}",
                        name="write_file",
                        arguments={
                            "path": "src/App.vue",
                            "content": "<template><router-view /></template>",
                        },
                    )
                ],
            )
        text = "Vue 工具预算已耗尽，当前通过校验的页面已保留。"
        await on_text(text)
        return ModelReply(text=text)


class AlwaysSuccessfulVueBuilder(VueProjectBuilder):
    def build(self, root: Path) -> BuildResult:
        dist = root / "dist"
        dist.mkdir(exist_ok=True)
        (dist / "index.html").write_text("built", encoding="utf-8")
        return BuildResult(True, "build ok", 5)


async def test_exhausted_vue_turn_forces_final_text_after_validation(
    app_context: AppContext,
) -> None:
    model = EndlessVueToolModel()
    app_context.app.state.code_gen_type_router = StaticCodeGenTypeRouter(CodeGenType.VUE_PROJECT)
    app_context.app.state.generation_runtime = GenerationRuntime(
        AgentTurnExecutor(
            app_context.session_factory,
            Path(app_context.app.state.settings.workspace_root),
            lambda _: model,
            vue_builder=AlwaysSuccessfulVueBuilder(),
            max_rounds_per_phase=2,
        )
    )
    await app_context.client.post(
        "/api/user/register",
        json={
            "userAccount": "finalize-vue-user",
            "userPassword": "password123",
            "checkPassword": "password123",
        },
    )
    await app_context.client.post(
        "/api/user/login",
        json={"userAccount": "finalize-vue-user", "userPassword": "password123"},
    )
    app_id = (await app_context.client.post("/api/app/create", json={"initPrompt": "Vue"})).json()[
        "data"
    ]

    response = await app_context.client.post(
        "/api/app/chat/gen/code",
        json={"appId": app_id, "message": "生成 Vue", "requestId": "finalize-vue-turn"},
    )
    events = _parse_sse(response.text)
    text_deltas = [event["data"]["text"] for event in events if event["event"] == "text_delta"]

    assert model.requests[-1].tools == model.requests[0].tools
    assert text_deltas[-1] == "Vue 工具预算已耗尽，当前通过校验的页面已保留。"
    assert events[-2]["event"] == "text_delta"
    assert events[-1]["event"] == "turn_finished"
    assert events[-1]["data"]["status"] == "completed"
