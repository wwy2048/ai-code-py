import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from conftest import AppContext
from sqlalchemy import select

from app.config import Settings
from app.generation.adapters.anthropic_model import AnthropicHtmlModel
from app.generation.adapters.compaction import ContextCompactor
from app.generation.adapters.conversation_store import ConversationStore, MessageKind
from app.generation.adapters.executor import AgentTurnExecutor
from app.generation.adapters.model import (
    ModelCallStatus,
    ModelReply,
    ModelRequest,
    SummaryResult,
    TextEmitter,
    ToolCall,
)
from app.generation.adapters.tools import ToolExecutionResult
from app.generation.application.runtime import GenerationRuntime
from app.generation.domain.messages import AssistantMessage, ToolResultMessage, UserMessage
from app.models import ChatHistory, ModelCallLog


async def _register_login_and_create_app(
    app_context: AppContext,
    *,
    account: str,
) -> tuple[int, int]:
    register = await app_context.client.post(
        "/api/user/register",
        json={
            "userAccount": account,
            "userPassword": "password123",
            "checkPassword": "password123",
        },
    )
    user_id = register.json()["data"]
    await app_context.client.post(
        "/api/user/login",
        json={"userAccount": account, "userPassword": "password123"},
    )
    app_id = (
        await app_context.client.post(
            "/api/app/create",
            json={"initPrompt": "创建一个页面"},
        )
    ).json()["data"]
    return user_id, app_id


async def _seed_turn(
    app_context: AppContext,
    *,
    app_id: int,
    user_id: int,
    turn_id: str,
    usage: tuple[int, int] | None = None,
    with_tool_pair: bool = False,
) -> None:
    async with app_context.session_factory() as session:
        conversation = ConversationStore(
            session,
            app_id=app_id,
            user_id=user_id,
            turn_id=turn_id,
        )
        await conversation.append_user(f"user-{turn_id}")
        if with_tool_pair:
            call = ToolCall(
                id=f"call-{turn_id}",
                name="write_file",
                arguments={"path": "index.html", "content": f"<h1>{turn_id}</h1>"},
            )
            await conversation.append_assistant(
                f"assistant-before-{turn_id}",
                batch_id=f"{turn_id}:1",
            )
            await conversation.append_tool_call(
                call,
                batch_id=f"{turn_id}:1",
            )
            await conversation.append_tool_result(
                call,
                ToolExecutionResult(
                    ok=True,
                    output=f"written-{turn_id}",
                    public_input={"path": "index.html"},
                    duration_ms=1,
                    size_bytes=10,
                ),
                batch_id=f"{turn_id}:1",
            )
        await conversation.append_assistant(f"assistant-{turn_id}")
        if usage is not None:
            await conversation.append_model_call(
                status=ModelCallStatus.SUCCESS,
                model_name="test-model",
                duration_ms=1,
                input_tokens=usage[0],
                output_tokens=usage[1],
            )
        await conversation.commit()


class RecordingSummarizer:
    model_name = "summary-model"

    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = responses or ["summary-1", "summary-2"]
        self.calls: list[dict[str, str | None]] = []

    async def summarize(
        self,
        *,
        previous_summary: str | None,
        transcript: str,
    ) -> SummaryResult:
        self.calls.append(
            {
                "previous_summary": previous_summary,
                "transcript": transcript,
            }
        )
        return SummaryResult(
            text=self.responses[len(self.calls) - 1],
            model_name=self.model_name,
            input_tokens=20,
            output_tokens=5,
        )


class FailingSummarizer:
    model_name = "failing-summary-model"

    async def summarize(
        self,
        *,
        previous_summary: str | None,
        transcript: str,
    ) -> SummaryResult:
        raise RuntimeError("summary failed")


async def test_compaction_triggers_at_budget_boundary_and_keeps_recent_turn(
    app_context: AppContext,
) -> None:
    user_id, app_id = await _register_login_and_create_app(
        app_context,
        account="compact-boundary-user",
    )
    await _seed_turn(
        app_context,
        app_id=app_id,
        user_id=user_id,
        turn_id="turn-1",
        usage=(700, 99),
    )
    summarizer = RecordingSummarizer(["summary-1"])
    compactor = ContextCompactor(context_window_tokens=1000, trigger_ratio=0.8)
    events: list[tuple[str, dict[str, Any]]] = []

    async def emit(event: str, data: dict[str, Any]) -> None:
        events.append((event, data))

    async with app_context.session_factory() as session:
        conversation = ConversationStore(
            session,
            app_id=app_id,
            user_id=user_id,
            turn_id="compaction-turn-1",
        )
        below = await compactor.maybe_compact(
            conversation,
            summarizer=summarizer,
            pending_text="",
            emit=emit,
        )
    assert below.triggered is False
    assert below.compacted is False
    assert events == []

    await _seed_turn(
        app_context,
        app_id=app_id,
        user_id=user_id,
        turn_id="turn-2",
        usage=(800, 0),
    )
    async with app_context.session_factory() as session:
        conversation = ConversationStore(
            session,
            app_id=app_id,
            user_id=user_id,
            turn_id="compaction-turn-2",
        )
        outcome = await compactor.maybe_compact(
            conversation,
            summarizer=summarizer,
            pending_text="",
            emit=emit,
        )
        active_messages = await conversation.load_active_model_messages()

    assert outcome.triggered is True
    assert outcome.compacted is True
    assert [event for event, _ in events] == ["compaction", "compaction"]
    assert [data["status"] for _, data in events] == ["started", "completed"]
    assert active_messages == [
        UserMessage(content="[会话压缩摘要]\nsummary-1"),
        UserMessage(content="user-turn-2"),
        AssistantMessage(content="assistant-turn-2"),
    ]

    async with app_context.session_factory() as session:
        rows = (
            await session.scalars(
                select(ChatHistory)
                .where(ChatHistory.app_id == app_id)
                .order_by(ChatHistory.sequence)
            )
        ).all()
    summary = next(row for row in rows if row.message_kind == MessageKind.COMPACTION)
    assert summary.visible is False
    assert summary.active is True
    assert summary.message == "summary-1"
    first_turn = [row for row in rows if row.turn_id == "turn-1"]
    second_turn = [row for row in rows if row.turn_id == "turn-2"]
    assert all(row.active is False and row.compacted_by == summary.id for row in first_turn)
    assert all(row.active is True for row in second_turn)
    async with app_context.session_factory() as session:
        summary_log = await session.scalar(
            select(ModelCallLog).where(
                ModelCallLog.app_id == app_id,
                ModelCallLog.call_kind == "compaction",
            )
        )
    assert summary_log is not None
    assert summary_log.status == "success"
    assert summary_log.model_name == "summary-model"
    assert summary_log.input_tokens == 20
    assert summary_log.output_tokens == 5


async def test_second_compaction_updates_summary_without_resummarizing_covered_turns(
    app_context: AppContext,
) -> None:
    user_id, app_id = await _register_login_and_create_app(
        app_context,
        account="compact-second-pass-user",
    )
    for turn_id in ("turn-1", "turn-2", "turn-3"):
        await _seed_turn(
            app_context,
            app_id=app_id,
            user_id=user_id,
            turn_id=turn_id,
            usage=(850, 50),
        )
    summarizer = RecordingSummarizer(["summary-1", "summary-2"])
    compactor = ContextCompactor(context_window_tokens=1000, trigger_ratio=0.8)
    events: list[dict[str, Any]] = []

    async def emit(event: str, data: dict[str, Any]) -> None:
        assert event == "compaction"
        events.append(data)

    async with app_context.session_factory() as session:
        first = await compactor.maybe_compact(
            ConversationStore(
                session,
                app_id=app_id,
                user_id=user_id,
                turn_id="compaction-1",
            ),
            summarizer=summarizer,
            pending_text="",
            emit=emit,
        )
    assert first.compacted is True

    await _seed_turn(
        app_context,
        app_id=app_id,
        user_id=user_id,
        turn_id="turn-4",
        usage=(850, 50),
    )
    async with app_context.session_factory() as session:
        conversation = ConversationStore(
            session,
            app_id=app_id,
            user_id=user_id,
            turn_id="compaction-2",
        )
        second = await compactor.maybe_compact(
            conversation,
            summarizer=summarizer,
            pending_text="",
            emit=emit,
        )
        active_messages = await conversation.load_active_model_messages()

    assert second.compacted is True
    assert len(summarizer.calls) == 2
    assert summarizer.calls[1]["previous_summary"] == "summary-1"
    assert "user-turn-1" not in str(summarizer.calls[1]["transcript"])
    assert "user-turn-2" not in str(summarizer.calls[1]["transcript"])
    assert "user-turn-3" in str(summarizer.calls[1]["transcript"])
    assert active_messages == [
        UserMessage(content="[会话压缩摘要]\nsummary-2"),
        UserMessage(content="user-turn-4"),
        AssistantMessage(content="assistant-turn-4"),
    ]

    async with app_context.session_factory() as session:
        compacted = (
            await session.scalars(
                select(ChatHistory).where(
                    ChatHistory.app_id == app_id,
                    ChatHistory.message_kind == MessageKind.COMPACTION,
                )
            )
        ).all()
    assert len(compacted) == 2
    assert sorted(row.active for row in compacted) == [False, True]


async def test_compaction_never_splits_a_tool_call_from_its_result(
    app_context: AppContext,
) -> None:
    user_id, app_id = await _register_login_and_create_app(
        app_context,
        account="compact-tool-pair-user",
    )
    await _seed_turn(
        app_context,
        app_id=app_id,
        user_id=user_id,
        turn_id="turn-1",
        usage=(100, 10),
    )
    await _seed_turn(
        app_context,
        app_id=app_id,
        user_id=user_id,
        turn_id="turn-2",
        usage=(850, 50),
        with_tool_pair=True,
    )
    await _seed_turn(
        app_context,
        app_id=app_id,
        user_id=user_id,
        turn_id="turn-3",
        usage=(850, 50),
    )
    summarizer = RecordingSummarizer(["summary-tools"])
    compactor = ContextCompactor(context_window_tokens=1000, trigger_ratio=0.8)

    async def emit(event: str, data: dict[str, Any]) -> None:
        return None

    async with app_context.session_factory() as session:
        outcome = await compactor.maybe_compact(
            ConversationStore(
                session,
                app_id=app_id,
                user_id=user_id,
                turn_id="compaction-tools",
            ),
            summarizer=summarizer,
            pending_text="",
            emit=emit,
        )

    assert outcome.compacted is True
    transcript = json.loads(str(summarizer.calls[0]["transcript"]))
    assert any(message.get("tool_calls") for message in transcript)
    assert any(message.get("role") == "tool_result" for message in transcript)

    async with app_context.session_factory() as session:
        rows = (
            await session.scalars(select(ChatHistory).where(ChatHistory.turn_id == "turn-2"))
        ).all()
    assert {row.message_kind for row in rows} >= {"tool_call", "tool_result"}
    assert all(row.active is False for row in rows)
    assert len({row.compacted_by for row in rows}) == 1
    assert next(iter({row.compacted_by for row in rows})) is not None


async def test_compaction_stops_before_latest_complete_turn_and_keeps_incomplete_tail(
    app_context: AppContext,
) -> None:
    user_id, app_id = await _register_login_and_create_app(
        app_context,
        account="compact-incomplete-tail-user",
    )
    await _seed_turn(
        app_context,
        app_id=app_id,
        user_id=user_id,
        turn_id="turn-complete-1",
        usage=(400, 10),
    )
    await _seed_turn(
        app_context,
        app_id=app_id,
        user_id=user_id,
        turn_id="turn-complete-2",
        usage=(500, 10),
    )
    async with app_context.session_factory() as session:
        conversation = ConversationStore(
            session,
            app_id=app_id,
            user_id=user_id,
            turn_id="turn-incomplete-3",
        )
        await conversation.append_user("user-turn-incomplete-3")
        await conversation.append_model_call(
            status=ModelCallStatus.SUCCESS,
            model_name="test-model",
            duration_ms=1,
            input_tokens=850,
            output_tokens=50,
        )
        await conversation.commit()
    compactor = ContextCompactor(context_window_tokens=1000, trigger_ratio=0.8)
    events: list[dict[str, Any]] = []

    async def emit(event: str, data: dict[str, Any]) -> None:
        events.append(data)

    async with app_context.session_factory() as session:
        outcome = await compactor.maybe_compact(
            ConversationStore(
                session,
                app_id=app_id,
                user_id=user_id,
                turn_id="compaction-incomplete-tail",
            ),
            summarizer=RecordingSummarizer(["unused-summary"]),
            pending_text="",
            emit=emit,
        )

    assert outcome.triggered is True
    assert outcome.compacted is True
    assert [event["status"] for event in events] == ["started", "completed"]
    async with app_context.session_factory() as session:
        rows = (
            await session.scalars(select(ChatHistory).where(ChatHistory.app_id == app_id))
        ).all()
    first_turn = [row for row in rows if row.turn_id == "turn-complete-1"]
    second_turn = [row for row in rows if row.turn_id == "turn-complete-2"]
    incomplete_tail = [row for row in rows if row.turn_id == "turn-incomplete-3"]
    summary = next(row for row in rows if row.message_kind == MessageKind.COMPACTION)
    assert all(row.active is False and row.compacted_by == summary.id for row in first_turn)
    assert all(row.active is True for row in second_turn)
    assert all(row.active is True for row in incomplete_tail)
    assert summary.active is True


async def test_provider_usage_fallback_includes_fixed_context_overhead(
    app_context: AppContext,
) -> None:
    user_id, app_id = await _register_login_and_create_app(
        app_context,
        account="compact-overhead-user",
    )
    await _seed_turn(
        app_context,
        app_id=app_id,
        user_id=user_id,
        turn_id="turn-1",
    )
    await _seed_turn(
        app_context,
        app_id=app_id,
        user_id=user_id,
        turn_id="turn-2",
    )
    summarizer = RecordingSummarizer(["summary-overhead"])
    compactor = ContextCompactor(
        context_window_tokens=1000,
        trigger_ratio=0.8,
        fixed_overhead_tokens=790,
    )

    async def emit(event: str, data: dict[str, Any]) -> None:
        return None

    async with app_context.session_factory() as session:
        outcome = await compactor.maybe_compact(
            ConversationStore(
                session,
                app_id=app_id,
                user_id=user_id,
                turn_id="compaction-overhead",
            ),
            summarizer=summarizer,
            pending_text="",
            emit=emit,
        )

    assert outcome.triggered is True
    assert outcome.compacted is True


class _SummaryTransportMessages:
    def __init__(self) -> None:
        self.request: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> Any:
        self.request = kwargs
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="transport-summary")],
            usage=SimpleNamespace(
                input_tokens=30,
                cache_read_input_tokens=10,
                output_tokens=7,
            ),
        )


class _SummaryTransportClient:
    def __init__(self) -> None:
        self.messages = _SummaryTransportMessages()


async def test_compaction_runs_through_anthropic_transport_and_persists_summary(
    app_context: AppContext,
) -> None:
    user_id, app_id = await _register_login_and_create_app(
        app_context,
        account="compact-transport-user",
    )
    await _seed_turn(
        app_context,
        app_id=app_id,
        user_id=user_id,
        turn_id="transport-turn-1",
        usage=(400, 10),
    )
    await _seed_turn(
        app_context,
        app_id=app_id,
        user_id=user_id,
        turn_id="transport-turn-2",
        usage=(850, 50),
    )
    client = _SummaryTransportClient()
    settings = Settings(  # type: ignore[call-arg]
        database_url="sqlite+aiosqlite://",
        anthropic_api_key="test-key",
        _env_file=None,
    )
    model = AnthropicHtmlModel(settings, client=client)  # type: ignore[arg-type]
    compactor = ContextCompactor(context_window_tokens=1000, trigger_ratio=0.8)

    async def emit(event: str, data: dict[str, Any]) -> None:
        return None

    async with app_context.session_factory() as session:
        conversation = ConversationStore(
            session,
            app_id=app_id,
            user_id=user_id,
            turn_id="compaction-transport",
        )
        outcome = await compactor.maybe_compact(
            conversation,
            summarizer=model,
            pending_text="",
            emit=emit,
        )
        active_messages = await conversation.load_active_model_messages()

    assert outcome.compacted is True
    assert client.messages.request is not None
    assert "tools" not in client.messages.request
    assert active_messages[0] == UserMessage(content="[会话压缩摘要]\ntransport-summary")
    async with app_context.session_factory() as session:
        summary_log = await session.scalar(
            select(ModelCallLog).where(
                ModelCallLog.app_id == app_id,
                ModelCallLog.call_kind == "compaction",
            )
        )
    assert summary_log is not None
    assert summary_log.input_tokens == 40
    assert summary_log.cached_input_tokens == 10
    assert summary_log.output_tokens == 7


async def test_compaction_failure_keeps_original_active_context(app_context: AppContext) -> None:
    user_id, app_id = await _register_login_and_create_app(
        app_context,
        account="compact-failure-user",
    )
    await _seed_turn(
        app_context,
        app_id=app_id,
        user_id=user_id,
        turn_id="turn-1",
        usage=(400, 10),
    )
    await _seed_turn(
        app_context,
        app_id=app_id,
        user_id=user_id,
        turn_id="turn-2",
        usage=(850, 50),
    )
    compactor = ContextCompactor(context_window_tokens=1000, trigger_ratio=0.8)
    events: list[dict[str, Any]] = []

    async def emit(event: str, data: dict[str, Any]) -> None:
        assert event == "compaction"
        events.append(data)

    async with app_context.session_factory() as session:
        conversation = ConversationStore(
            session,
            app_id=app_id,
            user_id=user_id,
            turn_id="compaction-failure",
        )
        outcome = await compactor.maybe_compact(
            conversation,
            summarizer=FailingSummarizer(),
            pending_text="",
            emit=emit,
        )
        active_messages = await conversation.load_active_model_messages()

    assert outcome.triggered is True
    assert outcome.compacted is False
    assert [event["status"] for event in events] == ["started", "failed"]
    assert active_messages == [
        UserMessage(content="user-turn-1"),
        AssistantMessage(content="assistant-turn-1"),
        UserMessage(content="user-turn-2"),
        AssistantMessage(content="assistant-turn-2"),
    ]
    async with app_context.session_factory() as session:
        summary_log = await session.scalar(
            select(ModelCallLog).where(
                ModelCallLog.app_id == app_id,
                ModelCallLog.call_kind == "compaction",
            )
        )
    assert summary_log is not None
    assert summary_log.status == "error"
    assert summary_log.error_type == "RuntimeError"


class CompactionAgentModel:
    model_name = "agent-model"

    def __init__(self) -> None:
        self.requests: list[tuple[object, ...]] = []
        self.summary_calls: list[dict[str, str | None]] = []

    async def summarize(
        self,
        *,
        previous_summary: str | None,
        transcript: str,
    ) -> SummaryResult:
        self.summary_calls.append(
            {
                "previous_summary": previous_summary,
                "transcript": transcript,
            }
        )
        return SummaryResult(text="summary-from-agent", model_name="summary-model")

    async def next_turn(
        self,
        request: ModelRequest,
        on_text: TextEmitter,
    ) -> ModelReply:
        self.requests.append(request.messages)
        if isinstance(request.messages[-1], ToolResultMessage):
            await on_text("完成。")
            return ModelReply(text="完成。")
        await on_text("写入页面。")
        return ModelReply(
            text="写入页面。",
            tool_calls=[
                ToolCall(
                    id="write-after-compaction",
                    name="write_file",
                    arguments={"path": "index.html", "content": "<h1>compacted</h1>"},
                )
            ],
        )


async def test_agent_emits_compaction_events_and_continues_with_summary_context(
    app_context: AppContext,
) -> None:
    user_id, app_id = await _register_login_and_create_app(
        app_context,
        account="compact-agent-user",
    )
    await _seed_turn(
        app_context,
        app_id=app_id,
        user_id=user_id,
        turn_id="turn-1",
        usage=(400, 10),
    )
    await _seed_turn(
        app_context,
        app_id=app_id,
        user_id=user_id,
        turn_id="turn-2",
        usage=(850, 50),
    )
    model = CompactionAgentModel()
    app_context.app.state.generation_runtime = GenerationRuntime(
        AgentTurnExecutor(
            app_context.session_factory,
            Path(app_context.app.state.settings.workspace_root),
            lambda _: model,
            context_compactor=ContextCompactor(
                context_window_tokens=1000,
                trigger_ratio=0.8,
            ),
        )
    )

    response = await app_context.client.post(
        "/api/app/chat/gen/code",
        json={"appId": app_id, "message": "继续修改", "requestId": "turn-after-compaction"},
    )

    assert '"status": "completed"' in response.text
    assert response.text.count("event: compaction") == 2
    assert model.summary_calls
    first_message = model.requests[0][0]
    last_message = model.requests[0][-1]
    assert isinstance(first_message, UserMessage)
    assert first_message.content == "[会话压缩摘要]\nsummary-from-agent"
    assert isinstance(last_message, UserMessage)
    assert last_message.content == "继续修改"
    assert all("user-turn-1" not in str(message) for message in model.requests[0])
    async with app_context.session_factory() as session:
        summary_record = await session.scalar(
            select(ChatHistory).where(
                ChatHistory.app_id == app_id,
                ChatHistory.message_kind == MessageKind.COMPACTION,
            )
        )
    assert summary_record is not None
    assert summary_record.visible is False
    assert summary_record.active is True
