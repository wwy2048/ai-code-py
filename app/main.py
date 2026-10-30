from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

from app.api.app import router as app_router
from app.api.chat_history import router as chat_history_router
from app.api.health import router as health_router
from app.api.user import router as user_router
from app.app_assets import CosCoverCleaner, LocalAppAssetCleaner
from app.codegen_router import CodeGenTypeRouter, create_code_gen_type_router
from app.config import Settings, get_settings
from app.cover import CosCoverUploader, CoverGenerator, CoverScheduler, SeleniumScreenshotCapturer
from app.db import create_engine
from app.deployment import LocalAppDeployer
from app.errors import (
    BusinessError,
    handle_business_error,
    handle_http_error,
    handle_unexpected_error,
    handle_validation_error,
)
from app.runtime.agent import AgentTurnExecutor
from app.runtime.compaction import ContextCompactor
from app.runtime.factory import create_agent_model_factory
from app.runtime.manager import GenerationManager
from app.runtime.model import AgentModel
from app.services.deployments import generate_deploy_key


def create_app(
    settings: Settings | None = None,
    *,
    engine: AsyncEngine | None = None,
    redis_client: Redis | None = None,
    code_gen_type_router: CodeGenTypeRouter | None = None,
    agent_model_factory: Callable[[str], AgentModel] | None = None,
    cover_generator: CoverScheduler | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    app_engine = engine or create_engine(app_settings.database_url)
    app_redis = redis_client or Redis.from_url(app_settings.redis_url, decode_responses=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await app.state.cover_generator.shutdown()
        await app.state.code_gen_type_router.aclose()
        await app.state.redis.aclose()
        await app.state.engine.dispose()

    app = FastAPI(title="ai-code-py", lifespan=lifespan)
    app.state.settings = app_settings
    app.state.engine = app_engine
    app.state.redis = app_redis
    app.state.session_factory = async_sessionmaker(app_engine, expire_on_commit=False)
    app.state.code_gen_type_router = code_gen_type_router or create_code_gen_type_router(
        app_settings
    )
    app.state.asset_cleaner = LocalAppAssetCleaner(
        app_settings, cover_cleaner=CosCoverCleaner(app_settings)
    )
    app.state.app_deployer = LocalAppDeployer(app_settings)
    app.state.deploy_key_factory = generate_deploy_key
    app.state.cover_generator = cover_generator or CoverGenerator(
        app.state.session_factory,
        capturer=SeleniumScreenshotCapturer(app_settings),
        uploader=CosCoverUploader(app_settings),
        screenshots_root=Path(app_settings.screenshots_root),
        shutdown_timeout_seconds=app_settings.cover_shutdown_timeout_seconds,
    )
    turn_executor = AgentTurnExecutor(
        app.state.session_factory,
        Path(app_settings.workspace_root),
        agent_model_factory or create_agent_model_factory(app_settings),
        context_compactor=ContextCompactor(
            context_window_tokens=app_settings.model_context_window_tokens,
            trigger_ratio=app_settings.context_compaction_trigger_ratio,
            fixed_overhead_tokens=app_settings.context_compaction_fixed_overhead_tokens,
        ),
        memory_enabled=app_settings.app_memory_enabled,
        memory_max_chars=app_settings.app_memory_max_chars,
        subagent_enabled=app_settings.subagent_enabled,
        subagent_max_rounds=app_settings.subagent_max_rounds,
        subagent_timeout_seconds=app_settings.subagent_timeout_seconds,
    )
    app.state.generation_manager = GenerationManager(turn_executor)

    app.add_exception_handler(BusinessError, handle_business_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(Exception, handle_unexpected_error)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    workspace_root = Path(app_settings.workspace_root)
    workspace_root.mkdir(parents=True, exist_ok=True)
    deploy_root = Path(app_settings.deploy_root)
    deploy_root.mkdir(parents=True, exist_ok=True)
    app.mount(
        f"{app_settings.api_prefix}/static",
        StaticFiles(directory=workspace_root, html=True),
        name="static",
    )
    app.mount(
        f"{app_settings.api_prefix}/deploy",
        StaticFiles(directory=deploy_root, html=True),
        name="deploy",
    )
    app.include_router(health_router, prefix=app_settings.api_prefix)
    app.include_router(user_router, prefix=app_settings.api_prefix)
    app.include_router(app_router, prefix=app_settings.api_prefix)
    app.include_router(chat_history_router, prefix=app_settings.api_prefix)
    return app
