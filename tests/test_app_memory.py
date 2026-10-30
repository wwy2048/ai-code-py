from pathlib import Path
from typing import Any

from conftest import AppContext
from sqlalchemy import func, select

from app.models import AppMemory
from app.runtime.agent import AgentTurnExecutor
from app.runtime.manager import GenerationManager
from app.runtime.memory import MemoryStore
from app.runtime.model import ModelReply, TextEmitter, ToolCall


async def _register_login_and_create_app(app_context: AppContext, account: str) -> tuple[int, int]:
    register = await app_context.client.post(
        "/api/user/register",
        json={
            "userAccount": account,
            "userPassword": "password123",
            "checkPassword": "password123",
        },
    )
    user_id: int = register.json()["data"]
    await app_context.client.post(
        "/api/user/login",
        json={"userAccount": account, "userPassword": "password123"},
    )
    app_id: int = (
        await app_context.client.post(
            "/api/app/create",
            json={"initPrompt": "创建页面"},
        )
    ).json()["data"]
    return user_id, app_id


class MemoryCaptureModel:
    def __init__(self) -> None:
        self.requests: list[list[dict[str, Any]]] = []

    async def next_turn(
        self,
        messages: list[dict[str, Any]],
        on_text: TextEmitter,
    ) -> ModelReply:
        self.requests.append(messages)
        if messages[-1].get("role") == "tool_result":
            return ModelReply(text="完成")
        return ModelReply(
            text="写入",
            tool_calls=[
                ToolCall(
                    id="memory-write",
                    name="write_file",
                    arguments={"path": "index.html", "content": "<h1>memory</h1>"},
                )
            ],
        )


async def test_memory_is_isolated_budgeted_and_not_injected_when_disabled(
    app_context: AppContext,
) -> None:
    user_id, first_app = await _register_login_and_create_app(app_context, "memory-user")
    second_app = (
        await app_context.client.post("/api/app/create", json={"initPrompt": "另一个应用"})
    ).json()["data"]
    async with app_context.session_factory() as session:
        store = MemoryStore(
            session,
            app_id=first_app,
            user_id=user_id,
            max_chars=120,
        )
        await store.save_from_turn("turn-1", "记住蓝色主题和顶部导航", "已完成蓝色页面")
        await store.commit()
        first_memories = await store.load_for_context()
    async with app_context.session_factory() as session:
        other_store = MemoryStore(
            session,
            app_id=second_app,
            user_id=user_id,
            max_chars=120,
        )
        assert await other_store.load_for_context() == []

    assert first_memories
    assert sum(len(memory) for memory in first_memories) <= 120
    assert all("蓝色主题" in memory for memory in first_memories)

    model = MemoryCaptureModel()
    executor = AgentTurnExecutor(
        app_context.session_factory,
        Path(app_context.app.state.settings.workspace_root),
        lambda _: model,
        memory_enabled=False,
        memory_max_chars=120,
    )
    app_context.app.state.generation_manager = GenerationManager(executor)
    await app_context.client.post(
        "/api/app/chat/gen/code",
        json={"appId": first_app, "message": "继续修改", "requestId": "memory-off"},
    )
    assert all("应用持久记忆" not in str(message) for message in model.requests[0])

    enabled_model = MemoryCaptureModel()
    app_context.app.state.generation_manager = GenerationManager(
        AgentTurnExecutor(
            app_context.session_factory,
            Path(app_context.app.state.settings.workspace_root),
            lambda _: enabled_model,
            memory_enabled=True,
            memory_max_chars=120,
        )
    )
    await app_context.client.post(
        "/api/app/chat/gen/code",
        json={"appId": first_app, "message": "使用记忆继续", "requestId": "memory-on"},
    )
    first_message = enabled_model.requests[0][0]
    assert first_message["role"] == "user"
    assert "应用持久记忆" in first_message["content"]
    assert "蓝色主题" in first_message["content"]


async def test_memory_api_owner_clear_and_app_delete_cleanup(app_context: AppContext) -> None:
    user_id, app_id = await _register_login_and_create_app(app_context, "memory-api-owner")
    async with app_context.session_factory() as session:
        store = MemoryStore(session, app_id=app_id, user_id=user_id, max_chars=1000)
        await store.save_from_turn("turn-api", "用户偏好", "已实现")
        await store.commit()

    listed = await app_context.client.get(f"/api/app/{app_id}/memory")
    assert listed.json()["code"] == 0
    assert len(listed.json()["data"]["records"]) == 1
    cleared = await app_context.client.delete(f"/api/app/{app_id}/memory")
    assert cleared.json()["data"] is True
    async with app_context.session_factory() as session:
        assert (
            await session.scalar(select(func.count(AppMemory.id)).where(AppMemory.app_id == app_id))
            == 0
        )
        store = MemoryStore(session, app_id=app_id, user_id=user_id, max_chars=1000)
        await store.save_from_turn("turn-delete", "删除前记忆", "完成")
        await store.commit()
    deleted = await app_context.client.request(
        "DELETE",
        "/api/app/delete",
        json={"id": app_id},
    )
    assert deleted.json()["data"] is True
    async with app_context.session_factory() as session:
        assert (
            await session.scalar(select(func.count(AppMemory.id)).where(AppMemory.app_id == app_id))
            == 0
        )
