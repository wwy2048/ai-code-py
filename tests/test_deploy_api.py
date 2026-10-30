import asyncio
from pathlib import Path
from urllib.parse import urlparse

import pytest
from conftest import AppContext, StaticCodeGenTypeRouter

from app.codegen_router import CodeGenType
from app.models import App


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
    app_id = (
        await app_context.client.post(
            "/api/app/create",
            json={"initPrompt": "创建一个页面"},
        )
    ).json()["data"]
    return user_id, app_id


def _workspace(app_context: AppContext, code_gen_type: CodeGenType, app_id: int) -> Path:
    return Path(app_context.app.state.settings.workspace_root) / f"{code_gen_type.value}_{app_id}"


def _list_directory(path: Path) -> list[Path]:
    return list(path.iterdir())


async def test_deploy_reuses_key_replaces_files_and_serves_static_content(
    app_context: AppContext,
) -> None:
    _, app_id = await _register_login_and_create_app(
        app_context,
        account="deploy-html-user",
    )
    workspace = _workspace(app_context, CodeGenType.HTML, app_id)
    workspace.mkdir(parents=True)
    (workspace / "index.html").write_text("<h1>版本一</h1>", encoding="utf-8")
    (workspace / "style.css").write_text("body { color: blue; }", encoding="utf-8")
    (workspace / "stale.txt").write_text("stale", encoding="utf-8")

    first = await app_context.client.post("/api/app/appDeploy", json={"appId": app_id})
    first_data = first.json()
    assert first_data["code"] == 0
    first_url = first_data["data"]
    deploy_key = urlparse(first_url).path.rstrip("/").split("/")[-1]
    assert len(deploy_key) == 6
    async with app_context.session_factory() as session:
        stored_app = await session.get(App, app_id)
    assert stored_app is not None
    assert stored_app.deploy_key == deploy_key
    assert stored_app.deployed_time is not None

    index = await app_context.client.get(first_url)
    assert index.status_code == 200
    assert "版本一" in index.text
    assert index.headers["content-type"].startswith("text/html")
    stylesheet = await app_context.client.get(f"{first_url}style.css")
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    redirect = await app_context.client.get(first_url.rstrip("/"), follow_redirects=False)
    assert redirect.status_code in {301, 302, 307, 308}

    (workspace / "index.html").write_text("<h1>版本二</h1>", encoding="utf-8")
    (workspace / "stale.txt").unlink()
    second = await app_context.client.post("/api/app/appDeploy", json={"appId": app_id})
    assert second.json()["data"] == first_url
    updated = await app_context.client.get(first_url)
    assert "版本二" in updated.text
    stale = await app_context.client.get(f"{first_url}stale.txt")
    assert stale.json()["code"] == 40400


async def test_deploy_rejects_non_owner_and_missing_source(app_context: AppContext) -> None:
    _, app_id = await _register_login_and_create_app(
        app_context,
        account="deploy-owner-user",
    )
    await app_context.client.post("/api/user/logout")
    await app_context.client.post(
        "/api/user/register",
        json={
            "userAccount": "deploy-intruder-user",
            "userPassword": "password123",
            "checkPassword": "password123",
        },
    )
    await app_context.client.post(
        "/api/user/login",
        json={"userAccount": "deploy-intruder-user", "userPassword": "password123"},
    )
    forbidden = await app_context.client.post("/api/app/appDeploy", json={"appId": app_id})
    assert forbidden.json()["code"] == 40101

    await app_context.client.post("/api/user/logout")
    await app_context.client.post(
        "/api/user/login",
        json={"userAccount": "deploy-owner-user", "userPassword": "password123"},
    )
    missing = await app_context.client.post("/api/app/appDeploy", json={"appId": app_id})
    assert missing.json()["code"] == 40400
    assert "源文件" in missing.json()["message"]


async def test_vue_deploy_requires_and_serves_dist(app_context: AppContext) -> None:
    _, app_id = await _register_login_and_create_app(
        app_context,
        account="deploy-vue-user",
        code_gen_type=CodeGenType.VUE_PROJECT,
    )
    workspace = _workspace(app_context, CodeGenType.VUE_PROJECT, app_id)
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "App.vue").write_text("<template>source</template>", encoding="utf-8")

    missing_dist = await app_context.client.post("/api/app/appDeploy", json={"appId": app_id})
    assert missing_dist.json()["code"] == 40400
    assert "dist" in missing_dist.json()["message"]

    dist = workspace / "dist"
    dist.mkdir(parents=True)
    missing_index = await app_context.client.post("/api/app/appDeploy", json={"appId": app_id})
    assert missing_index.json()["code"] == 40400
    assert "index.html" in missing_index.json()["message"]

    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<script src="/assets/app.js"></script>', encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('deployed')", encoding="utf-8")
    deployed = await app_context.client.post("/api/app/appDeploy", json={"appId": app_id})
    deploy_url = deployed.json()["data"]

    index = await app_context.client.get(deploy_url)
    assert index.status_code == 200
    assert "assets/app.js" in index.text
    script = await app_context.client.get(f"{deploy_url}assets/app.js")
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    source_file = await app_context.client.get(f"{deploy_url}src/App.vue")
    assert source_file.json()["code"] == 40400


async def test_deploy_key_generation_skips_existing_key(app_context: AppContext) -> None:
    _, first_app_id = await _register_login_and_create_app(
        app_context,
        account="deploy-key-user",
    )
    second_app_id = (
        await app_context.client.post(
            "/api/app/create",
            json={"initPrompt": "第二个页面"},
        )
    ).json()["data"]
    for app_id in (first_app_id, second_app_id):
        workspace = _workspace(app_context, CodeGenType.HTML, app_id)
        workspace.mkdir(parents=True)
        (workspace / "index.html").write_text(f"<h1>{app_id}</h1>", encoding="utf-8")

    keys = iter(("abc123", "abc123", "def456"))
    app_context.app.state.deploy_key_factory = lambda: next(keys)
    first, second = await asyncio.gather(
        app_context.client.post("/api/app/appDeploy", json={"appId": first_app_id}),
        app_context.client.post("/api/app/appDeploy", json={"appId": second_app_id}),
    )

    assert first.json()["data"].endswith("/abc123/")
    assert second.json()["data"].endswith("/def456/")


async def test_deployed_static_server_blocks_path_escape(app_context: AppContext) -> None:
    _, app_id = await _register_login_and_create_app(
        app_context,
        account="deploy-escape-user",
    )
    workspace = _workspace(app_context, CodeGenType.HTML, app_id)
    workspace.mkdir(parents=True)
    (workspace / "index.html").write_text("<h1>safe</h1>", encoding="utf-8")
    deployed = await app_context.client.post("/api/app/appDeploy", json={"appId": app_id})
    deploy_url = deployed.json()["data"]
    deploy_root = Path(app_context.app.state.settings.deploy_root)
    (deploy_root.parent / "secret.txt").write_text("must-not-leak", encoding="utf-8")

    escaped = await app_context.client.get(f"{deploy_url}%2e%2e/secret.txt")

    assert escaped.json()["code"] == 40400
    assert "must-not-leak" not in escaped.text


async def test_deploy_rejects_source_symlink_and_cleans_staging(app_context: AppContext) -> None:
    _, app_id = await _register_login_and_create_app(
        app_context,
        account="deploy-symlink-user",
    )
    workspace = _workspace(app_context, CodeGenType.HTML, app_id)
    workspace.mkdir(parents=True)
    (workspace / "index.html").write_text("<h1>safe</h1>", encoding="utf-8")
    outside = Path(app_context.app.state.settings.workspace_root).parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    try:
        (workspace / "linked.txt").symlink_to(outside)
    except OSError:
        pytest.skip("当前 Windows 环境不允许创建符号链接")

    response = await app_context.client.post("/api/app/appDeploy", json={"appId": app_id})

    assert response.json()["code"] == 40400
    deploy_root = Path(app_context.app.state.settings.deploy_root)
    leftovers = await asyncio.to_thread(_list_directory, deploy_root)
    assert [path for path in leftovers if path.name.endswith(".tmp")] == []
