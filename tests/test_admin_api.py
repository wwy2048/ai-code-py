from pathlib import Path

from conftest import AppContext, StaticCodeGenTypeRouter
from sqlalchemy import select

from app.codegen_router import CodeGenType
from app.models import App, User


async def _register_and_login(
    app_context: AppContext,
    account: str,
    *,
    role: str = "user",
) -> int:
    register = await app_context.client.post(
        "/api/user/register",
        json={
            "userAccount": account,
            "userPassword": "password123",
            "checkPassword": "password123",
        },
    )
    user_id: int = register.json()["data"]
    if role != "user":
        async with app_context.session_factory() as session:
            user = await session.get(User, user_id)
            assert user is not None
            user.user_role = role
            await session.commit()
    login = await app_context.client.post(
        "/api/user/login",
        json={"userAccount": account, "userPassword": "password123"},
    )
    assert login.json()["code"] == 0
    return user_id


async def test_admin_endpoints_reject_normal_user(app_context: AppContext) -> None:
    await _register_and_login(app_context, "ordinary-admin-check")

    responses = [
        await app_context.client.post("/api/user/list/page/vo", json={"pageNum": 1}),
        await app_context.client.get("/api/user/get/vo", params={"id": 1}),
        await app_context.client.post("/api/user/update", json={"id": 1, "userName": "x"}),
        await app_context.client.post("/api/user/delete", json={"id": 1}),
        await app_context.client.post("/api/app/admin/list/page/vo", json={"pageNum": 1}),
        await app_context.client.get("/api/app/admin/detail/1"),
        await app_context.client.put("/api/app/admin/update", json={"id": 1, "priority": 99}),
        await app_context.client.request("DELETE", "/api/app/admin/delete", json={"id": 1}),
    ]

    assert [response.json()["code"] for response in responses] == [40101] * len(responses)


async def test_admin_can_page_update_and_delete_users_without_password_leak(
    app_context: AppContext,
) -> None:
    admin_id = await _register_and_login(app_context, "database-admin", role="admin")
    target_id = await _register_and_login(app_context, "managed-user")
    await app_context.client.post(
        "/api/user/login",
        json={"userAccount": "database-admin", "userPassword": "password123"},
    )

    page = await app_context.client.post(
        "/api/user/list/page/vo",
        json={"pageNum": 1, "pageSize": 1, "userRole": "user"},
    )
    page_data = page.json()["data"]
    assert page_data["totalRow"] == 1
    assert page_data["records"][0]["id"] == target_id
    assert "userPassword" not in page_data["records"][0]

    updated = await app_context.client.post(
        "/api/user/update",
        json={
            "id": target_id,
            "userName": "受管用户",
            "userProfile": "管理员更新",
            "userRole": "admin",
        },
    )
    assert updated.json()["data"] is True
    detail = await app_context.client.get("/api/user/get/vo", params={"id": target_id})
    assert detail.json()["data"]["userRole"] == "admin"
    assert detail.json()["data"]["userName"] == "受管用户"
    assert "userPassword" not in detail.json()["data"]

    deleted = await app_context.client.post("/api/user/delete", json={"id": target_id})
    assert deleted.json()["data"] is True
    missing = await app_context.client.get("/api/user/get/vo", params={"id": target_id})
    assert missing.json()["code"] == 40400
    async with app_context.session_factory() as session:
        target = await session.get(User, target_id)
    assert target is not None
    assert target.is_delete == 1
    assert admin_id != target_id


async def test_admin_can_manage_all_apps_and_featured_order(
    app_context: AppContext,
) -> None:
    owner_id = await _register_and_login(app_context, "managed-app-owner")
    app_context.app.state.code_gen_type_router = StaticCodeGenTypeRouter(CodeGenType.HTML)
    first_id = (
        await app_context.client.post("/api/app/create", json={"initPrompt": "第一个应用"})
    ).json()["data"]
    second_id = (
        await app_context.client.post("/api/app/create", json={"initPrompt": "第二个应用"})
    ).json()["data"]
    first_workspace = Path(app_context.app.state.settings.workspace_root) / f"html_{first_id}"
    first_workspace.mkdir(parents=True)
    (first_workspace / "index.html").write_text("first", encoding="utf-8")
    await app_context.client.post("/api/user/logout")
    await _register_and_login(app_context, "app-admin", role="admin")

    page = await app_context.client.post(
        "/api/app/admin/list/page/vo",
        json={"pageNum": 1, "pageSize": 10, "userId": owner_id},
    )
    assert page.json()["data"]["totalRow"] == 2
    assert "user" in page.json()["data"]["records"][0]

    updated = await app_context.client.put(
        "/api/app/admin/update",
        json={
            "id": first_id,
            "appName": "精选应用",
            "cover": "https://cos.example.com/cover.png",
            "priority": 99,
        },
    )
    assert updated.json()["data"] is True
    detail = await app_context.client.get(f"/api/app/admin/detail/{first_id}")
    assert detail.json()["data"]["appName"] == "精选应用"
    assert detail.json()["data"]["priority"] == 99

    await app_context.client.put(
        "/api/app/admin/update",
        json={"id": second_id, "priority": 95},
    )
    featured = await app_context.client.post("/api/app/good/list/page/vo", json={"pageNum": 1})
    assert [record["id"] for record in featured.json()["data"]["records"]] == [first_id, second_id]

    deleted = await app_context.client.request(
        "DELETE",
        "/api/app/admin/delete",
        json={"id": first_id},
    )
    assert deleted.json()["data"] is True
    assert not first_workspace.exists()
    async with app_context.session_factory() as session:
        assert await session.get(App, first_id) is None
        remaining = await session.scalar(select(App.id).where(App.id == second_id))
    assert remaining == second_id


async def test_admin_user_list_pagination_validates_page_size(app_context: AppContext) -> None:
    await _register_and_login(app_context, "paging-admin", role="admin")

    too_large = await app_context.client.post(
        "/api/user/list/page/vo",
        json={"pageNum": 1, "pageSize": 21},
    )

    assert too_large.json()["code"] == 40000


async def test_role_change_affects_existing_session_on_next_request(
    app_context: AppContext,
) -> None:
    admin_id = await _register_and_login(app_context, "role-change-admin", role="admin")

    changed = await app_context.client.post(
        "/api/user/update",
        json={"id": admin_id, "userRole": "user"},
    )
    denied = await app_context.client.post("/api/user/list/page/vo", json={"pageNum": 1})

    assert changed.json()["data"] is True
    assert denied.json()["code"] == 40101


async def test_admin_app_pagination_returns_second_page(app_context: AppContext) -> None:
    owner_id = await _register_login_and_create_app_for_paging(app_context)
    await app_context.client.post("/api/user/logout")
    await _register_and_login(app_context, "paging-app-admin", role="admin")

    first = await app_context.client.post(
        "/api/app/admin/list/page/vo",
        json={"pageNum": 1, "pageSize": 2, "userId": owner_id},
    )
    second = await app_context.client.post(
        "/api/app/admin/list/page/vo",
        json={"pageNum": 2, "pageSize": 2, "userId": owner_id},
    )

    assert first.json()["data"]["totalRow"] == 3
    assert len(first.json()["data"]["records"]) == 2
    assert len(second.json()["data"]["records"]) == 1


async def _register_login_and_create_app_for_paging(app_context: AppContext) -> int:
    owner_id = await _register_and_login(app_context, "paging-app-owner")
    for index in range(3):
        await app_context.client.post(
            "/api/app/create",
            json={"initPrompt": f"分页应用 {index}"},
        )
    return owner_id
