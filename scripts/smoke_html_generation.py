"""真实模型 HTML 生成冒烟脚本。

用法：
    ANTHROPIC_API_KEY=... uv run python scripts/smoke_html_generation.py
"""

import argparse
import asyncio
import sys
import tempfile
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.db import Base, create_engine
from app.generation.adapters.executor import AgentTurnExecutor
from app.generation.adapters.factory import create_agent_model_factory
from app.generation.adapters.vue_browser import SeleniumVueBrowserValidator
from app.generation.adapters.vue_project import VueProjectBuilder
from app.generation.domain.turns import TurnRequest


async def run_smoke(code_gen_type: str) -> None:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise SystemExit("未配置 ANTHROPIC_API_KEY，无法执行真实模型冒烟")

    engine = create_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    with tempfile.TemporaryDirectory(prefix="ai-code-smoke-") as temp_dir:
        root = Path(temp_dir)
        browser_validator = (
            SeleniumVueBrowserValidator(
                timeout_seconds=settings.vue_browser_validation_timeout_seconds,
                wait_after_load_seconds=settings.vue_browser_validation_wait_seconds,
                min_css_bytes=settings.vue_browser_validation_min_css_bytes,
            )
            if settings.vue_browser_validation_enabled
            else None
        )
        executor = AgentTurnExecutor(
            session_factory,
            root,
            create_agent_model_factory(settings),
            vue_builder=VueProjectBuilder(browser_validator=browser_validator),
        )

        async def emit(event: str, payload: dict) -> None:
            print(f"[{event}] {payload}")

        await executor.execute(
            TurnRequest(
                app_id=1,
                user_id=1,
                code_gen_type=code_gen_type,
                message="创建一个现代简洁的个人作品集单页网站，深色主题",
                turn_id=f"smoke-{uuid.uuid4().hex}",
            ),
            emit=emit,
        )

        workspace = root / f"{code_gen_type}_1"
        index_path = (
            workspace / "dist" / "index.html"
            if code_gen_type == "vue_project"
            else workspace / "index.html"
        )
        if not index_path.is_file():
            raise SystemExit("真实模型未生成 index.html")
        content = index_path.read_text(encoding="utf-8")
        if "<html" not in content.lower():
            raise SystemExit("生成的 index.html 不含 html 根元素")
        if code_gen_type == "multi_file":
            for asset in ("style.css", "script.js"):
                if not (workspace / asset).is_file():
                    raise SystemExit(f"多文件冒烟缺少 {asset}")
            if "style.css" not in content or "script.js" not in content:
                raise SystemExit("index.html 未正确引用 CSS/JS")
        if code_gen_type == "vue_project":
            for fixed in (
                "package.json",
                "package-lock.json",
                "vite.config.js",
                "src/App.vue",
                "src/router/index.js",
                "src/router/routes.js",
            ):
                if not (workspace / fixed).is_file():
                    raise SystemExit(f"Vue 冒烟缺少固定模板文件 {fixed}")
        print(f"冒烟通过：{index_path} ({len(content)} 字符)")

    await engine.dispose()


if __name__ == "__main__":
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--type",
        choices=("html", "multi_file", "vue_project"),
        default="html",
    )
    args = parser.parse_args()
    asyncio.run(run_smoke(args.type))
