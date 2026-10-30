import copy
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import AppContext, StaticCodeGenTypeRouter
from sqlalchemy import select

from app.codegen_router import CodeGenType
from app.models import ChatHistory
from app.runtime.agent import AgentTurnExecutor
from app.runtime.conversation import ConversationStore
from app.runtime.manager import GenerationManager
from app.runtime.model import ModelReply, TextEmitter, ToolCall
from app.runtime.vue_project import BuildResult, VueProjectBuilder


async def _register_login_and_create_app(
    app_context: AppContext,
    *,
    account: str,
    code_gen_type: CodeGenType = CodeGenType.HTML,
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
    app_context.app.state.code_gen_type_router = StaticCodeGenTypeRouter(code_gen_type)
    created = await app_context.client.post(
        "/api/app/create",
        json={"initPrompt": "创建一个页面"},
    )
    return user_id, created.json()["data"]


async def _add_history(
    app_context: AppContext,
    *,
    app_id: int,
    user_id: int,
    message: str,
    message_type: str,
    message_kind: str,
    sequence: int,
    active: bool = True,
    visible: bool = True,
    tool_call_id: str | None = None,
    payload: dict[str, Any] | None = None,
    create_time: datetime | None = None,
) -> int:
    async with app_context.session_factory() as session:
        record = ChatHistory(
            message=message,
            message_type=message_type,
            app_id=app_id,
            user_id=user_id,
            sequence=sequence,
            message_kind=message_kind,
            payload=payload,
            visible=visible,
            active=active,
            tool_call_id=tool_call_id,
            tool_name="write_file" if message_kind == "tool_call" else None,
            is_delete=0,
            **({"create_time": create_time} if create_time is not None else {}),
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)
        return record.id


async def test_active_context_projection_keeps_tool_pairs_and_excludes_inactive(
    app_context: AppContext,
) -> None:
    user_id, app_id = await _register_login_and_create_app(
        app_context,
        account="active-context-user",
    )
    other_app_id = (
        await app_context.client.post(
            "/api/app/create",
            json={"initPrompt": "另一个应用"},
        )
    ).json()["data"]
    await _add_history(
        app_context,
        app_id=other_app_id,
        user_id=user_id,
        message="不属于当前应用的上下文",
        message_type="user",
        message_kind="user",
        sequence=1,
    )
    await _add_history(
        app_context,
        app_id=app_id,
        user_id=user_id,
        message="初始需求",
        message_type="user",
        message_kind="user",
        sequence=1,
    )
    await _add_history(
        app_context,
        app_id=app_id,
        user_id=user_id,
        message='{"path": "index.html", "content": "<h1>旧版本</h1>"}',
        message_type="tool",
        message_kind="tool_call",
        sequence=2,
        visible=False,
        tool_call_id="call-write-1",
        payload={
            "arguments": {
                "path": "index.html",
                "content": "<h1>旧版本</h1>",
            }
        },
    )
    await _add_history(
        app_context,
        app_id=app_id,
        user_id=user_id,
        message="已写入 index.html",
        message_type="tool",
        message_kind="tool_result",
        sequence=3,
        visible=False,
        tool_call_id="call-write-1",
    )
    await _add_history(
        app_context,
        app_id=app_id,
        user_id=user_id,
        message="页面已生成。",
        message_type="ai",
        message_kind="assistant",
        sequence=4,
    )
    await _add_history(
        app_context,
        app_id=app_id,
        user_id=user_id,
        message="已被压缩的旧需求",
        message_type="user",
        message_kind="user",
        sequence=5,
        active=False,
    )
    await _add_history(
        app_context,
        app_id=app_id,
        user_id=user_id,
        message='{"path": "index.html"}',
        message_type="tool",
        message_kind="tool_call",
        sequence=6,
        visible=False,
        tool_call_id="orphan-call",
        payload={"arguments": {"path": "index.html"}},
    )
    await _add_history(
        app_context,
        app_id=app_id,
        user_id=user_id,
        message="孤立的工具结果",
        message_type="tool",
        message_kind="tool_result",
        sequence=7,
        visible=False,
        tool_call_id="orphan-result",
    )

    async with app_context.session_factory() as session:
        history = await ConversationStore(
            session,
            app_id=app_id,
            user_id=user_id,
            turn_id="turn-context-read",
        ).load_active_model_messages()

    assert history == [
        {"role": "user", "content": "初始需求"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-write-1",
                    "name": "write_file",
                    "arguments": {
                        "path": "index.html",
                        "content": "<h1>旧版本</h1>",
                    },
                }
            ],
        },
        {
            "role": "tool_result",
            "tool_call_id": "call-write-1",
            "name": "write_file",
            "content": "已写入 index.html",
            "is_error": False,
        },
        {"role": "assistant", "content": "页面已生成。"},
    ]


async def test_visible_history_uses_stable_composite_cursor_and_hides_tool_rows(
    app_context: AppContext,
) -> None:
    user_id, app_id = await _register_login_and_create_app(
        app_context,
        account="history-page-user",
    )
    same_time = datetime(2026, 10, 31, 12, 0, 0)
    visible_ids = []
    for index in range(1, 6):
        visible_ids.append(
            await _add_history(
                app_context,
                app_id=app_id,
                user_id=user_id,
                message=f"visible-{index}",
                message_type="user" if index % 2 else "ai",
                message_kind="user" if index % 2 else "assistant",
                sequence=index,
                create_time=same_time,
            )
        )
    await _add_history(
        app_context,
        app_id=app_id,
        user_id=user_id,
        message="隐藏工具调用",
        message_type="tool",
        message_kind="tool_call",
        sequence=10,
        visible=False,
        tool_call_id="hidden-call",
        payload={"arguments": {"path": "index.html"}},
        create_time=same_time,
    )
    await _add_history(
        app_context,
        app_id=app_id,
        user_id=user_id,
        message="隐藏压缩摘要",
        message_type="ai",
        message_kind="compaction",
        sequence=11,
        visible=False,
        create_time=same_time,
    )

    first = (
        await app_context.client.get(
            f"/api/chatHistory/app/{app_id}",
            params={"pageSize": 2},
        )
    ).json()["data"]["records"]
    assert [record["message"] for record in first] == ["visible-5", "visible-4"]
    assert all(record["messageKind"] not in {"tool_call", "compaction"} for record in first)

    cursor = first[-1]
    second = (
        await app_context.client.get(
            f"/api/chatHistory/app/{app_id}",
            params={
                "pageSize": 2,
                "lastCreateTime": cursor["createTime"],
                "lastId": cursor["id"],
            },
        )
    ).json()["data"]["records"]
    cursor = second[-1]
    third = (
        await app_context.client.get(
            f"/api/chatHistory/app/{app_id}",
            params={
                "pageSize": 2,
                "lastCreateTime": cursor["createTime"],
                "lastId": cursor["id"],
            },
        )
    ).json()["data"]["records"]

    assert [record["message"] for record in second] == ["visible-3", "visible-2"]
    assert [record["message"] for record in third] == ["visible-1"]
    paged_ids = [record["id"] for record in [*first, *second, *third]]
    assert len(paged_ids) == len(set(paged_ids)) == 5
    assert set(paged_ids) == set(visible_ids)


async def test_visible_history_rejects_other_users_app(app_context: AppContext) -> None:
    _, app_id = await _register_login_and_create_app(
        app_context,
        account="history-owner",
    )
    await app_context.client.post("/api/user/logout")
    await app_context.client.post(
        "/api/user/register",
        json={
            "userAccount": "history-intruder",
            "userPassword": "password123",
            "checkPassword": "password123",
        },
    )
    await app_context.client.post(
        "/api/user/login",
        json={"userAccount": "history-intruder", "userPassword": "password123"},
    )

    response = await app_context.client.get(f"/api/chatHistory/app/{app_id}")

    assert response.json()["code"] == 40101


class ContextAwareModel:
    def __init__(self, write_path: str) -> None:
        self._write_path = write_path
        self.requests: list[list[dict[str, Any]]] = []

    async def next_turn(
        self,
        messages: list[dict[str, Any]],
        on_text: TextEmitter,
    ) -> ModelReply:
        self.requests.append(copy.deepcopy(messages))
        if messages[-1].get("role") == "tool_result":
            await on_text("处理完成。")
            return ModelReply(text="处理完成。")

        user_count = sum(message.get("role") == "user" for message in messages)
        version = "initial" if user_count == 1 else "follow-up"
        await on_text(f"写入 {version} 版本。")
        return ModelReply(
            text=f"写入 {version} 版本。",
            tool_calls=[
                ToolCall(
                    id=f"write-{version}",
                    name="write_file",
                    arguments={
                        "path": self._write_path,
                        "content": f"<h1>{version}</h1>",
                    },
                ),
                ToolCall(
                    id=f"read-{version}",
                    name="read_file",
                    arguments={"path": self._write_path},
                ),
            ],
        )


class SuccessfulVueBuilder(VueProjectBuilder):
    def build(self, root: Path) -> BuildResult:
        dist = root / "dist"
        dist.mkdir(exist_ok=True)
        index = dist / "index.html"
        index.write_text("built", encoding="utf-8")
        return BuildResult(True, "build ok", index.stat().st_size)


@pytest.mark.parametrize(
    ("code_gen_type", "write_path"),
    [
        (CodeGenType.HTML, "index.html"),
        (CodeGenType.MULTI_FILE, "index.html"),
        (CodeGenType.VUE_PROJECT, "src/App.vue"),
    ],
)
async def test_follow_up_restores_prior_active_context_after_relogin(
    app_context: AppContext,
    code_gen_type: CodeGenType,
    write_path: str,
) -> None:
    account = f"restore-{code_gen_type.value}"
    _, app_id = await _register_login_and_create_app(
        app_context,
        account=account,
        code_gen_type=code_gen_type,
    )
    model = ContextAwareModel(write_path)
    app_context.app.state.generation_manager = GenerationManager(
        AgentTurnExecutor(
            app_context.session_factory,
            Path(app_context.app.state.settings.workspace_root),
            lambda _: model,
            vue_builder=SuccessfulVueBuilder(),
        )
    )

    first = await app_context.client.post(
        "/api/app/chat/gen/code",
        json={"appId": app_id, "message": "创建初始页面", "requestId": "restore-turn-1"},
    )
    assert first.status_code == 200
    assert "turn_finished" in first.text

    await app_context.client.post("/api/user/logout")
    relogin = await app_context.client.post(
        "/api/user/login",
        json={"userAccount": account, "userPassword": "password123"},
    )
    assert relogin.json()["code"] == 0

    second = await app_context.client.post(
        "/api/app/chat/gen/code",
        json={"appId": app_id, "message": "把标题改成新版本", "requestId": "restore-turn-2"},
    )
    assert second.status_code == 200
    assert '"status": "completed"' in second.text

    follow_up = model.requests[2]
    assert [message["role"] for message in follow_up] == [
        "user",
        "assistant",
        "tool_result",
        "tool_result",
        "assistant",
        "user",
    ]
    assert [call["id"] for call in follow_up[1]["tool_calls"]] == [
        "write-initial",
        "read-initial",
    ]
    assert follow_up[1]["content"] == "写入 initial 版本。"
    assert [result["tool_call_id"] for result in follow_up[2:4]] == [
        "write-initial",
        "read-initial",
    ]
    assert follow_up[-1]["role"] == "user"
    assert follow_up[-1]["content"] == "把标题改成新版本"

    workspace = Path(app_context.app.state.settings.workspace_root) / (
        f"{code_gen_type.value}_{app_id}"
    )
    assert (workspace / write_path).read_text(encoding="utf-8") == "<h1>follow-up</h1>"

    visible = (await app_context.client.get(f"/api/chatHistory/app/{app_id}")).json()["data"][
        "records"
    ]
    assert [record["messageType"] for record in visible] == ["ai", "user", "ai", "user"]
    assert all(record["messageKind"] in {"user", "assistant"} for record in visible)

    async with app_context.session_factory() as session:
        rows = (
            await session.scalars(
                select(ChatHistory)
                .where(ChatHistory.app_id == app_id)
                .order_by(ChatHistory.sequence)
            )
        ).all()
    assert any(row.message_kind == "tool_call" and not row.visible for row in rows)
    assert any(row.message_kind == "tool_result" and not row.visible for row in rows)
