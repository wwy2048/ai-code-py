import io
import zipfile
from pathlib import Path

import pytest
from conftest import AppContext, StaticCodeGenTypeRouter

from app import download as download_module
from app.codegen_router import CodeGenType


async def _register_login_and_create_app(
    app_context: AppContext,
    *,
    account: str,
    code_gen_type: CodeGenType = CodeGenType.HTML,
) -> int:
    await app_context.client.post(
        "/api/user/register",
        json={
            "userAccount": account,
            "userPassword": "password123",
            "checkPassword": "password123",
        },
    )
    await app_context.client.post(
        "/api/user/login",
        json={"userAccount": account, "userPassword": "password123"},
    )
    app_context.app.state.code_gen_type_router = StaticCodeGenTypeRouter(code_gen_type)
    app_id: int = (
        await app_context.client.post(
            "/api/app/create",
            json={"initPrompt": "创建一个页面"},
        )
    ).json()["data"]
    return app_id


def _workspace(app_context: AppContext, app_id: int, code_gen_type: CodeGenType) -> Path:
    return Path(app_context.app.state.settings.workspace_root) / f"{code_gen_type.value}_{app_id}"


def _zip_names(content: bytes) -> set[str]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return set(archive.namelist())


async def test_download_returns_owner_workspace_zip_and_ignores_generated_cache(
    app_context: AppContext,
) -> None:
    app_id = await _register_login_and_create_app(app_context, account="download-html-user")
    workspace = _workspace(app_context, app_id, CodeGenType.HTML)
    (workspace / "assets").mkdir(parents=True)
    (workspace / "index.html").write_text("<h1>download</h1>", encoding="utf-8")
    (workspace / "assets" / "logo.txt").write_text("logo", encoding="utf-8")
    (workspace / "node_modules").mkdir()
    (workspace / "node_modules" / "secret.js").write_text("ignored", encoding="utf-8")
    (workspace / "debug.tmp").write_text("ignored", encoding="utf-8")

    response = await app_context.client.get(f"/api/app/download/{app_id}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == f'attachment; filename="app-{app_id}.zip"'
    names = _zip_names(response.content)
    assert {"index.html", "assets/", "assets/logo.txt"} <= names
    assert "node_modules/secret.js" not in names
    assert "debug.tmp" not in names


@pytest.mark.parametrize(
    ("code_gen_type", "expected_names"),
    [
        (
            CodeGenType.MULTI_FILE,
            {"index.html", "style.css", "script.js"},
        ),
        (
            CodeGenType.VUE_PROJECT,
            {
                "index.html",
                "package.json",
                "vite.config.js",
                "src/App.vue",
                "src/main.js",
                "src/style.css",
            },
        ),
    ],
)
async def test_download_preserves_multi_file_and_vue_project_structure(
    app_context: AppContext,
    code_gen_type: CodeGenType,
    expected_names: set[str],
) -> None:
    app_id = await _register_login_and_create_app(
        app_context,
        account=f"download-{code_gen_type.value}",
        code_gen_type=code_gen_type,
    )
    workspace = _workspace(app_context, app_id, code_gen_type)
    if code_gen_type is CodeGenType.MULTI_FILE:
        for name in expected_names:
            (workspace / name).parent.mkdir(parents=True, exist_ok=True)
            (workspace / name).write_text(name, encoding="utf-8")
    else:
        (workspace / "src").mkdir(parents=True)
        for name in expected_names:
            (workspace / name).parent.mkdir(parents=True, exist_ok=True)
            (workspace / name).write_text(name, encoding="utf-8")
        (workspace / "node_modules").mkdir()
        (workspace / "node_modules" / "pkg.js").write_text("ignored", encoding="utf-8")
        (workspace / "dist").mkdir()
        (workspace / "dist" / "index.html").write_text("ignored", encoding="utf-8")

    response = await app_context.client.get(f"/api/app/download/{app_id}")

    assert response.status_code == 200
    names = _zip_names(response.content)
    assert expected_names <= names
    assert "node_modules/pkg.js" not in names
    assert "dist/index.html" not in names
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        if code_gen_type is CodeGenType.VUE_PROJECT:
            assert archive.read("src/App.vue") == b"src/App.vue"
            assert archive.read("package.json") == b"package.json"


async def test_download_rejects_non_owner_and_missing_or_empty_workspace(
    app_context: AppContext,
) -> None:
    app_id = await _register_login_and_create_app(app_context, account="download-owner")
    await app_context.client.post("/api/user/logout")
    await app_context.client.post(
        "/api/user/register",
        json={
            "userAccount": "download-intruder",
            "userPassword": "password123",
            "checkPassword": "password123",
        },
    )
    await app_context.client.post(
        "/api/user/login",
        json={"userAccount": "download-intruder", "userPassword": "password123"},
    )
    forbidden = await app_context.client.get(f"/api/app/download/{app_id}")
    assert forbidden.json()["code"] == 40101

    await app_context.client.post("/api/user/logout")
    await app_context.client.post(
        "/api/user/login",
        json={"userAccount": "download-owner", "userPassword": "password123"},
    )
    missing = await app_context.client.get(f"/api/app/download/{app_id}")
    assert missing.json()["code"] == 40400

    _workspace(app_context, app_id, CodeGenType.HTML).mkdir(parents=True)
    empty = await app_context.client.get(f"/api/app/download/{app_id}")
    assert empty.json()["code"] == 40400


async def test_download_rejects_workspace_symlink(app_context: AppContext) -> None:
    app_id = await _register_login_and_create_app(app_context, account="download-link-user")
    workspace = _workspace(app_context, app_id, CodeGenType.HTML)
    workspace.mkdir(parents=True)
    (workspace / "index.html").write_text("<h1>safe</h1>", encoding="utf-8")
    outside = Path(app_context.app.state.settings.workspace_root).parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    try:
        (workspace / "linked.txt").symlink_to(outside)
    except OSError:
        pytest.skip("当前 Windows 环境不允许创建符号链接")

    response = await app_context.client.get(f"/api/app/download/{app_id}")

    assert response.json()["code"] == 40000
    assert "符号链接" in response.json()["message"]


async def test_download_reports_nested_path_anomaly(
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_id = await _register_login_and_create_app(app_context, account="download-anomaly-user")
    workspace = _workspace(app_context, app_id, CodeGenType.HTML)
    workspace.mkdir(parents=True)
    (workspace / "index.html").write_text("<h1>safe</h1>", encoding="utf-8")
    monkeypatch.setattr(
        download_module,
        "_is_link",
        lambda path: path.name == "index.html",
    )

    response = await app_context.client.get(f"/api/app/download/{app_id}")

    assert response.json()["code"] == 40000
    assert "不允许" in response.json()["message"]
