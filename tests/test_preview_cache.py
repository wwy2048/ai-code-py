from pathlib import Path

from conftest import AppContext


async def test_static_preview_disables_browser_cache(app_context: AppContext) -> None:
    workspace = Path(app_context.app.state.settings.workspace_root) / "html_1"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "index.html").write_text("<h1>preview</h1>", encoding="utf-8")
    (workspace / "app.js").write_text("console.log('preview')", encoding="utf-8")

    for path in (
        "/api/static/html_1/index.html",
        "/api/static/html_1/app.js",
        "/api/static/html_1/?__preview=1",
    ):
        response = await app_context.client.get(path)

        assert response.status_code == 200
        cache_control = response.headers.get("cache-control", "").lower()
        assert "no-store" in cache_control
        assert "no-cache" in cache_control
