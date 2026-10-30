import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from fakeredis.aioredis import FakeRedis
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.codegen_router import CodeGenType
from app.config import Settings
from app.db import Base
from app.db import create_engine as create_db_engine
from app.main import create_app
from app.runtime.model import ScriptedHtmlModel


class NoopCoverScheduler:
    def schedule(self, app_id: int, url: str, deploy_revision: int) -> None:
        return None

    async def shutdown(self) -> None:
        return None


class StaticCodeGenTypeRouter:
    def __init__(self, code_gen_type: CodeGenType = CodeGenType.HTML) -> None:
        self.code_gen_type = code_gen_type

    async def route(self, init_prompt: str) -> CodeGenType:
        return self.code_gen_type

    async def aclose(self) -> None:
        return None


@dataclass
class AppContext:
    app: FastAPI
    client: AsyncClient
    session_factory: async_sessionmaker[AsyncSession]


@pytest.fixture
async def app_context(tmp_path: Path) -> AsyncIterator[AppContext]:
    settings = Settings(
        database_url="sqlite+aiosqlite://",
        redis_url="redis://localhost:6379/15",
        session_cookie_secure=False,
        password_hash_memory_cost=1024,
        password_hash_time_cost=1,
        password_hash_parallelism=1,
        workspace_root=str(tmp_path / "codeOutput"),
        deploy_root=str(tmp_path / "code_deploy"),
        deploy_url_base="http://testserver/api/deploy",
        screenshots_root=str(tmp_path / "screenshots"),
        _env_file=None,  # type: ignore[call-arg]
    )
    await asyncio.to_thread(Path(settings.workspace_root).mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(Path(settings.deploy_root).mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(Path(settings.screenshots_root).mkdir, parents=True, exist_ok=True)
    engine = create_db_engine(settings.database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    redis_client = FakeRedis(decode_responses=True)
    app = create_app(
        settings,
        engine=engine,
        redis_client=redis_client,
        code_gen_type_router=StaticCodeGenTypeRouter(),
        agent_model_factory=lambda _: ScriptedHtmlModel(),
        cover_generator=NoopCoverScheduler(),
    )
    session_factory = app.state.session_factory

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
            yield AppContext(
                app=app,
                client=test_client,
                session_factory=session_factory,
            )


@pytest.fixture
async def client(app_context: AppContext) -> AsyncClient:
    return app_context.client
