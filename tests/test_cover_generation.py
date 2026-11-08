import logging
from pathlib import Path
from typing import Any

from conftest import AppContext

from app.cover import CoverGenerator, CoverUploadError, ScreenshotCaptureError
from app.models import App


async def _register_login_and_create_app(app_context: AppContext, *, account: str) -> int:
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
    app_id: int = (
        await app_context.client.post(
            "/api/app/create",
            json={"initPrompt": "创建一个页面"},
        )
    ).json()["data"]
    return app_id


class RecordingCapturer:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, Path]] = []

    def capture(self, url: str, output_path: Path) -> None:
        self.calls.append((url, output_path))
        if self.error is not None:
            raise self.error
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"PNG")


class RecordingUploader:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[Path, str]] = []

    def upload(self, path: Path, object_key: str) -> str:
        self.calls.append((path, object_key))
        if self.error is not None:
            raise self.error
        return f"https://cos.example.com/{object_key}"

    def delete(self, object_key: str) -> None:
        return None


class RecordingScheduler:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str, int]] = []

    def schedule(self, app_id: int, url: str, deploy_revision: int) -> None:
        self.calls.append((app_id, url, deploy_revision))

    async def shutdown(self) -> None:
        return None


def _cover_generator(
    app_context: AppContext,
    capturer: RecordingCapturer,
    uploader: RecordingUploader,
) -> CoverGenerator:
    return CoverGenerator(
        app_context.session_factory,
        capturer=capturer,
        uploader=uploader,
        screenshots_root=Path(app_context.app.state.settings.screenshots_root),
    )


async def test_deploy_schedules_cover_generation_without_waiting(app_context: AppContext) -> None:
    app_id = await _register_login_and_create_app(app_context, account="cover-schedule-user")
    workspace = Path(app_context.app.state.settings.workspace_root) / f"html_{app_id}"
    workspace.mkdir(parents=True)
    (workspace / "index.html").write_text("<h1>cover</h1>", encoding="utf-8")
    scheduler = RecordingScheduler()
    app_context.app.state.cover_generator = scheduler

    response = await app_context.client.post("/api/app/appDeploy", json={"appId": app_id})

    assert response.json()["code"] == 0
    assert len(scheduler.calls) == 1
    assert scheduler.calls[0][0] == app_id
    assert scheduler.calls[0][1] == response.json()["data"]
    assert scheduler.calls[0][2] == 1


async def test_cover_generator_uploads_screenshot_and_updates_app(app_context: AppContext) -> None:
    app_id = await _register_login_and_create_app(app_context, account="cover-success-user")
    capturer = RecordingCapturer()
    uploader = RecordingUploader()
    generator = _cover_generator(app_context, capturer, uploader)

    generated = await generator.generate(app_id, "http://testserver/api/deploy/abc123/")

    assert generated is True
    assert len(capturer.calls) == 1
    assert len(uploader.calls) == 1
    local_path, object_key = uploader.calls[0]
    assert object_key.startswith("screenshots/")
    assert object_key.endswith(".png")
    assert not local_path.exists()
    async with app_context.session_factory() as session:
        app = await session.get(App, app_id)
    assert app is not None
    assert app.cover == f"https://cos.example.com/{object_key}"


async def test_deploy_background_task_updates_cover_for_current_deployment(
    app_context: AppContext,
) -> None:
    app_id = await _register_login_and_create_app(app_context, account="cover-deploy-success-user")
    workspace = Path(app_context.app.state.settings.workspace_root) / f"html_{app_id}"
    workspace.mkdir(parents=True)
    (workspace / "index.html").write_text("<h1>cover deploy</h1>", encoding="utf-8")
    capturer = RecordingCapturer()
    uploader = RecordingUploader()
    generator = _cover_generator(app_context, capturer, uploader)
    app_context.app.state.cover_generator = generator

    deployed = await app_context.client.post("/api/app/appDeploy", json={"appId": app_id})
    await generator.wait_for_idle()

    assert deployed.json()["code"] == 0
    async with app_context.session_factory() as session:
        app = await session.get(App, app_id)
    assert app is not None
    assert app.cover is not None
    assert app.cover.startswith("https://cos.example.com/screenshots/")


async def test_cover_generator_uses_internal_url_for_screenshot(app_context: AppContext) -> None:
    app_id = await _register_login_and_create_app(app_context, account="cover-internal-url-user")
    capturer = RecordingCapturer()
    uploader = RecordingUploader()
    generator = CoverGenerator(
        app_context.session_factory,
        capturer=capturer,
        uploader=uploader,
        screenshots_root=Path(app_context.app.state.settings.screenshots_root),
        public_url_base="https://public.example.com/api/deploy",
        screenshot_url_base="http://127.0.0.1/api/deploy",
    )

    generated = await generator.generate(
        app_id,
        "https://public.example.com/api/deploy/abc123/",
    )

    assert generated is True
    assert capturer.calls[0][0] == "http://127.0.0.1/api/deploy/abc123/"


async def test_cover_timeout_is_logged_and_does_not_update_cover(
    app_context: AppContext,
    caplog: Any,
) -> None:
    app_id = await _register_login_and_create_app(app_context, account="cover-timeout-user")
    generator = _cover_generator(
        app_context,
        RecordingCapturer(ScreenshotCaptureError("page_timeout")),
        RecordingUploader(),
    )

    with caplog.at_level(logging.ERROR, logger="app.cover"):
        generated = await generator.generate(app_id, "http://example.test/slow")

    assert generated is False
    assert "app_id=" in caplog.text
    assert "stage=capture" in caplog.text
    assert "ScreenshotCaptureError" in caplog.text
    async with app_context.session_factory() as session:
        app = await session.get(App, app_id)
    assert app is not None
    assert app.cover is None


async def test_cover_upload_failure_is_logged_and_keeps_deployed_app_available(
    app_context: AppContext,
    caplog: Any,
) -> None:
    app_id = await _register_login_and_create_app(app_context, account="cover-upload-fail-user")
    workspace = Path(app_context.app.state.settings.workspace_root) / f"html_{app_id}"
    workspace.mkdir(parents=True)
    (workspace / "index.html").write_text("<h1>deployed</h1>", encoding="utf-8")
    capturer = RecordingCapturer()
    generator = _cover_generator(
        app_context,
        capturer,
        RecordingUploader(error=CoverUploadError("cos_upload_failed")),
    )
    app_context.app.state.cover_generator = generator

    deployed = await app_context.client.post("/api/app/appDeploy", json={"appId": app_id})
    await generator.wait_for_idle()

    assert deployed.json()["code"] == 0
    assert "stage=upload" in caplog.text
    assert "CoverUploadError" in caplog.text
    preview = await app_context.client.get(deployed.json()["data"])
    assert "deployed" in preview.text
    assert not capturer.calls[0][1].exists()
