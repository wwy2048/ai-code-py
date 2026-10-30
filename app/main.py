from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.health import router as health_router
from app.api.user import router as user_router
from app.config import Settings, get_settings
from app.db import create_engine
from app.errors import (
    BusinessError,
    handle_business_error,
    handle_http_error,
    handle_unexpected_error,
    handle_validation_error,
)


def create_app(
    settings: Settings | None = None,
    *,
    engine: AsyncEngine | None = None,
    redis_client: Redis | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    app_engine = engine or create_engine(app_settings.database_url)
    app_redis = redis_client or Redis.from_url(app_settings.redis_url, decode_responses=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await app.state.redis.aclose()
        await app.state.engine.dispose()

    app = FastAPI(title='ai-code-py', lifespan=lifespan)
    app.state.settings = app_settings
    app.state.engine = app_engine
    app.state.redis = app_redis
    app.state.session_factory = async_sessionmaker(app_engine, expire_on_commit=False)

    app.add_exception_handler(BusinessError, handle_business_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(Exception, handle_unexpected_error)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=True,
        allow_methods=['*'],
        allow_headers=['*'],
    )
    app.include_router(health_router, prefix=app_settings.api_prefix)
    app.include_router(user_router, prefix=app_settings.api_prefix)
    return app
