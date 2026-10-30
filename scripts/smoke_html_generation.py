"""真实模型 HTML 生成冒烟脚本。

用法：
    ANTHROPIC_API_KEY=... uv run python scripts/smoke_html_generation.py
"""

import asyncio
import tempfile
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.db import Base, create_engine
from app.runtime.agent import AgentTurnExecutor
from app.runtime.anthropic_model import AnthropicHtmlModel


async def run_smoke() -> None:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise SystemExit('未配置 ANTHROPIC_API_KEY，无法执行真实模型冒烟')

    engine = create_engine('sqlite+aiosqlite://')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    with tempfile.TemporaryDirectory(prefix='ai-code-smoke-') as temp_dir:
        root = Path(temp_dir)
        executor = AgentTurnExecutor(
            session_factory,
            root,
            AnthropicHtmlModel(settings),
        )

        async def emit(event: str, payload: dict) -> None:
            print(f'[{event}] {payload}')

        await executor.execute(
            app_id=1,
            user_id=1,
            code_gen_type='html',
            prompt='创建一个现代简洁的个人作品集单页网站，深色主题',
            turn_id=f'smoke-{uuid.uuid4().hex}',
            emit=emit,
        )

        index_path = root / 'html_1' / 'index.html'
        if not index_path.is_file():
            raise SystemExit('真实模型未生成 index.html')
        content = index_path.read_text(encoding='utf-8')
        if '<html' not in content.lower():
            raise SystemExit('生成的 index.html 不含 html 根元素')
        print(f'冒烟通过：{index_path} ({len(content)} 字符)')

    await engine.dispose()


if __name__ == '__main__':
    asyncio.run(run_smoke())
