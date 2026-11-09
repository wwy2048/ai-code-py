from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

from app.api.app import router as app_router
from app.api.chat_history import router as chat_history_router
from app.api.health import router as health_router
from app.api.model_call import router as model_call_router
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
from app.generation.adapters.compaction import ContextCompactor
from app.generation.adapters.executor import AgentTurnExecutor
from app.generation.adapters.factory import create_agent_model_factory
from app.generation.adapters.vue_browser import SeleniumVueBrowserValidator
from app.generation.adapters.vue_project import VueProjectBuilder
from app.generation.application.runtime import GenerationRuntime
from app.generation.ports.model import AgentModel
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
        await app.state.generation_runtime.shutdown()
        await app.state.cover_generator.shutdown()
        await app.state.code_gen_type_router.aclose()
        await app.state.redis.aclose()
        await app.state.engine.dispose()

    app = FastAPI(title="CluckAI API", lifespan=lifespan)

    @app.middleware("http")
    async def disable_static_preview_cache(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """预览产物每次重新加载都必须回源，避免 iframe 复用旧 HTML/CSS/JS。"""
        response = await call_next(request)
        if request.url.path.startswith(f"{app_settings.api_prefix}/static/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response
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
        public_url_base=app_settings.deploy_url_base,
        screenshot_url_base=app_settings.cover_screenshot_url_base,
    )
    vue_browser_validator = (
        SeleniumVueBrowserValidator(
            timeout_seconds=app_settings.vue_browser_validation_timeout_seconds,
            wait_after_load_seconds=app_settings.vue_browser_validation_wait_seconds,
            min_css_bytes=app_settings.vue_browser_validation_min_css_bytes,
        )
        if app_settings.vue_browser_validation_enabled
        else None
    )
    turn_executor = AgentTurnExecutor(
        app.state.session_factory,
        Path(app_settings.workspace_root),
        agent_model_factory or create_agent_model_factory(app_settings),
        vue_builder=VueProjectBuilder(browser_validator=vue_browser_validator),
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
        agent_debug_log_enabled=app_settings.agent_debug_log_enabled,
        agent_debug_log_max_chars=app_settings.agent_debug_log_max_chars,
        max_rounds_per_phase=app_settings.agent_max_rounds_per_phase,
        max_validation_repairs=app_settings.agent_max_validation_repairs,
    )
    app.state.generation_runtime = GenerationRuntime(
        turn_executor,
        event_buffer_limit=app_settings.generation_event_buffer_limit,
        finished_turn_ttl_seconds=app_settings.generation_finished_turn_ttl_seconds,
        turn_receipt_limit=app_settings.generation_turn_receipt_limit,
        agent_idle_ttl_seconds=app_settings.generation_agent_idle_ttl_seconds,
    )

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
    app.include_router(model_call_router, prefix=app_settings.api_prefix)
    return app


def run() -> None:
    """``ai-code-py`` 命令入口（等价于 ``uvicorn app.main:create_app --factory``）。"""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host=settings.server_host,
        port=settings.server_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
