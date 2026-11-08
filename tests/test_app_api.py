import logging
from pathlib import Path

import pytest
from conftest import AppContext, StaticCodeGenTypeRouter
from httpx import AsyncClient
from sqlalchemy import select

from app.app_assets import LocalAppAssetCleaner
from app.codegen_router import CodeGenType, CodeGenTypeRouteResult, ModelBackedCodeGenTypeRouter
from app.models import App, ChatHistory, ModelCallLog


async def _register_and_login(
    client: AsyncClient,
    account: str = "app-user",
    password: str = "password123",
) -> None:
    register = await client.post(
        "/api/user/register",
        json={
            "userAccount": account,
            "userPassword": password,
            "checkPassword": password,
        },
    )
    assert register.json()["code"] == 0
    login = await client.post(
        "/api/user/login",
        json={"userAccount": account, "userPassword": password},
    )
    assert login.json()["code"] == 0


async def test_user_can_create_update_and_read_own_app(app_context: AppContext) -> None:
    app_context.app.state.code_gen_type_router = StaticCodeGenTypeRouter(CodeGenType.VUE_PROJECT)
    await _register_and_login(app_context.client)

    created = await app_context.client.post(
        "/api/app/create",
        json={"initPrompt": "创建一个带数据管理的复杂管理后台"},
    )
    assert created.json()["code"] == 0
    app_id = created.json()["data"]

    detail = await app_context.client.get("/api/app/get", params={"id": app_id})
    assert detail.json()["code"] == 0
    detail_data = detail.json()["data"]
    assert detail_data["id"] == app_id
    assert detail_data["codeGenType"] == "vue_project"
    assert detail_data["user"]["userAccount"] == "app-user"

    updated = await app_context.client.put(
        "/api/app/update",
        json={"id": app_id, "appName": "管理后台"},
    )
    assert updated.json() == {"code": 0, "data": True, "message": "ok"}

    refreshed = await app_context.client.get("/api/app/get", params={"id": app_id})
    assert refreshed.json()["data"]["appName"] == "管理后台"

    page = await app_context.client.post(
        "/api/app/my/list/page/vo",
        json={"pageNum": 1, "pageSize": 6, "sortField": "createTime", "sortOrder": "desc"},
    )
    page_data = page.json()["data"]
    assert page_data["records"][0]["id"] == app_id
    assert page_data["pageNumber"] == 1
    assert page_data["pageSize"] == 6
    assert page_data["totalPage"] == 1
    assert page_data["totalRow"] == 1
    assert isinstance(page_data["optimizeCountQuery"], bool)


async def test_other_user_cannot_read_update_or_delete_app(app_context: AppContext) -> None:
    await _register_and_login(app_context.client, "owner-user")
    created = await app_context.client.post(
        "/api/app/create",
        json={"initPrompt": "创建一个简单页面"},
    )
    app_id = created.json()["data"]

    await app_context.client.post("/api/user/logout")
    await _register_and_login(app_context.client, "other-user")

    detail = await app_context.client.get("/api/app/get", params={"id": app_id})
    assert detail.json()["code"] == 40101

    update = await app_context.client.put(
        "/api/app/update",
        json={"id": app_id, "appName": "越权修改"},
    )
    assert update.json()["code"] == 40101

    delete = await app_context.client.request(
        "DELETE",
        "/api/app/delete",
        json={"id": app_id},
    )
    assert delete.json()["code"] == 40101


async def test_featured_apps_and_existing_workspace_are_publicly_previewable(
    app_context: AppContext,
) -> None:
    await _register_and_login(app_context.client, "preview-user")
    created = await app_context.client.post(
        "/api/app/create",
        json={"initPrompt": "创建一个作品展示页"},
    )
    app_id = created.json()["data"]

    second = await app_context.client.post(
        "/api/app/create",
        json={"initPrompt": "创建一个普通页面"},
    )
    second_id = second.json()["data"]

    async with app_context.session_factory() as session:
        app = await session.get(App, app_id)
        assert app is not None
        app.priority = 95
        second_app = await session.get(App, second_id)
        assert second_app is not None
        second_app.priority = 90
        await session.commit()

    workspace = Path(app_context.app.state.settings.workspace_root) / f"html_{app_id}"
    workspace.mkdir(parents=True)
    (workspace / "index.html").write_text("<h1>旧应用预览</h1>", encoding="utf-8")

    featured = await app_context.client.post(
        "/api/app/good/list/page/vo",
        json={"pageNum": 1, "pageSize": 6},
    )
    assert featured.json()["code"] == 0
    assert [record["id"] for record in featured.json()["data"]["records"]] == [app_id]
    assert featured.json()["data"]["records"][0]["user"]["userAccount"] == "preview-user"

    preview = await app_context.client.get(f"/api/static/html_{app_id}/index.html")
    assert preview.status_code == 200
    assert "旧应用预览" in preview.text


async def test_delete_app_removes_database_rows_and_artifacts(
    app_context: AppContext,
) -> None:
    await _register_and_login(app_context.client, "delete-user")
    created = await app_context.client.post(
        "/api/app/create",
        json={"initPrompt": "创建后删除"},
    )
    app_id = created.json()["data"]

    workspace_root = Path(app_context.app.state.settings.workspace_root)
    deployment = Path(app_context.app.state.settings.deploy_root) / "ABC123"
    code_gen_types = ("html", "multi_file", "vue_project")
    workspaces = [workspace_root / f"{code_gen_type}_{app_id}" for code_gen_type in code_gen_types]
    for workspace in workspaces:
        workspace.mkdir(parents=True)
        (workspace / "index.html").write_text("<h1>待删除</h1>", encoding="utf-8")
    deployment.mkdir(parents=True)
    (deployment / "index.html").write_text("<h1>已部署</h1>", encoding="utf-8")

    class RecordingCoverCleaner:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        def delete_cover(self, cover_url: str) -> None:
            self.deleted.append(cover_url)

    cover_cleaner = RecordingCoverCleaner()
    app_context.app.state.asset_cleaner = LocalAppAssetCleaner(
        app_context.app.state.settings,
        cover_cleaner=cover_cleaner,
    )
    cover_url = "https://bucket.cos.example.com/covers/deleted.png"

    async with app_context.session_factory() as session:
        app = await session.get(App, app_id)
        assert app is not None
        app.deploy_key = "ABC123"
        app.cover = cover_url
        session.add(
            ChatHistory(
                message="待删除对话",
                message_type="user",
                app_id=app_id,
                user_id=app.user_id,
                is_delete=0,
            )
        )
        session.add(
            ModelCallLog(
                app_id=app_id,
                turn_id="delete-turn",
                model_name="test",
                status="success",
                duration_ms=1,
            )
        )
        await session.commit()

    deleted = await app_context.client.request(
        "DELETE",
        "/api/app/delete",
        json={"id": app_id},
    )
    assert deleted.json() == {"code": 0, "data": True, "message": "ok"}
    assert all(not workspace.exists() for workspace in workspaces)
    assert not deployment.exists()
    assert cover_cleaner.deleted == [cover_url]

    async with app_context.session_factory() as session:
        assert await session.get(App, app_id) is None
        remaining_chat = await session.scalar(
            select(ChatHistory).where(ChatHistory.app_id == app_id)
        )
        assert remaining_chat is None
        remaining_logs = await session.scalar(
            select(ModelCallLog).where(ModelCallLog.app_id == app_id)
        )
        assert remaining_logs is None


async def test_creating_app_requires_login(app_context: AppContext) -> None:
    response = await app_context.client.post(
        "/api/app/create",
        json={"initPrompt": "未登录创建"},
    )

    assert response.json()["code"] == 40100


async def test_router_failure_persists_html_fallback(app_context: AppContext) -> None:
    class FailingRouteModel:
        async def choose_code_gen_type(
            self, init_prompt: str, system_prompt: str
        ) -> CodeGenTypeRouteResult:
            raise RuntimeError("模拟路由失败")

    app_context.app.state.code_gen_type_router = ModelBackedCodeGenTypeRouter(FailingRouteModel())
    await _register_and_login(app_context.client, "fallback-user")

    created = await app_context.client.post(
        "/api/app/create",
        json={"initPrompt": "创建一个简单页面"},
    )
    app_id = created.json()["data"]
    detail = await app_context.client.get("/api/app/get", params={"id": app_id})

    assert detail.json()["data"]["codeGenType"] == "html"


async def test_app_inputs_and_page_size_are_bounded(app_context: AppContext) -> None:
    await _register_and_login(app_context.client, "bounds-user")

    blank_prompt = await app_context.client.post(
        "/api/app/create",
        json={"initPrompt": "   "},
    )
    assert blank_prompt.json()["code"] == 40000

    long_name = await app_context.client.post(
        "/api/app/create",
        json={"initPrompt": "创建一个页面", "appName": "x" * 257},
    )
    assert long_name.json()["code"] == 40000

    too_large_page = await app_context.client.post(
        "/api/app/my/list/page/vo",
        json={"pageNum": 1, "pageSize": 21},
    )
    assert too_large_page.json()["code"] == 40000


async def test_asset_cleaner_never_removes_controlled_root(
    app_context: AppContext,
) -> None:
    settings = app_context.app.state.settings
    workspace_root = Path(settings.workspace_root)
    deploy_root = Path(settings.deploy_root)
    (workspace_root / "sentinel.txt").write_text("keep", encoding="utf-8")
    (deploy_root / "sentinel.txt").write_text("keep", encoding="utf-8")
    cleaner = LocalAppAssetCleaner(settings)

    cleaner.delete_app_artifacts(
        App(
            id=999,
            code_gen_type="html",
            deploy_key=".",
            cover=None,
            priority=0,
            user_id=1,
            is_delete=0,
        )
    )

    assert (workspace_root / "sentinel.txt").exists()
    assert (deploy_root / "sentinel.txt").exists()


async def test_repeated_delete_returns_not_found_after_first_success(
    app_context: AppContext,
) -> None:
    await _register_and_login(app_context.client, "repeat-delete-user")
    created = await app_context.client.post(
        "/api/app/create",
        json={"initPrompt": "重复删除"},
    )
    app_id = created.json()["data"]

    first = await app_context.client.request(
        "DELETE",
        "/api/app/delete",
        json={"id": app_id},
    )
    second = await app_context.client.request(
        "DELETE",
        "/api/app/delete",
        json={"id": app_id},
    )

    assert first.json()["code"] == 0
    assert second.json()["code"] == 40400


async def test_cleanup_failure_is_alerted_without_restoring_app(
    app_context: AppContext,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailingCleaner:
        def delete_app_artifacts(self, app: App) -> None:
            raise OSError("模拟文件清理失败")

    app_context.app.state.asset_cleaner = FailingCleaner()
    await _register_and_login(app_context.client, "cleanup-failure-user")
    created = await app_context.client.post(
        "/api/app/create",
        json={"initPrompt": "清理失败仍应删除数据库记录"},
    )
    app_id = created.json()["data"]

    with caplog.at_level(logging.ERROR, logger="app.services.apps"):
        deleted = await app_context.client.request(
            "DELETE",
            "/api/app/delete",
            json={"id": app_id},
        )

    assert deleted.json()["code"] == 0
    assert "清理本地产物失败" in caplog.text
    async with app_context.session_factory() as session:
        assert await session.get(App, app_id) is None
