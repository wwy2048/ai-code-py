import asyncio
import logging
import secrets
import string
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cover import CoverScheduler
from app.deployment import (
    AppDeployer,
    DeploymentSourceError,
    DeploymentWriteError,
    is_valid_deploy_key,
)
from app.errors import BusinessError
from app.models import App
from app.response import ErrorCode
from app.services.apps import require_owned_app

logger = logging.getLogger(__name__)

_DEPLOY_KEY_ALPHABET = string.ascii_lowercase + string.digits
_DEPLOY_LOCK = asyncio.Lock()


def generate_deploy_key() -> str:
    return "".join(secrets.choice(_DEPLOY_KEY_ALPHABET) for _ in range(6))


async def _unique_deploy_key(
    session: AsyncSession,
    key_factory: Callable[[], str],
) -> str:
    for _ in range(20):
        candidate = key_factory()
        if not is_valid_deploy_key(candidate):
            continue
        exists = await session.scalar(select(App.id).where(App.deploy_key == candidate))
        if exists is None:
            return candidate
    raise BusinessError(ErrorCode.SYSTEM_ERROR, "无法生成唯一部署键")


async def deploy_app(
    session: AsyncSession,
    *,
    user_id: int,
    app_id: int,
    deployer: AppDeployer,
    cover_generator: CoverScheduler | None = None,
    key_factory: Callable[[], str] = generate_deploy_key,
) -> str:
    async with _DEPLOY_LOCK:
        return await _deploy_app_locked(
            session,
            user_id=user_id,
            app_id=app_id,
            deployer=deployer,
            cover_generator=cover_generator,
            key_factory=key_factory,
        )


async def _deploy_app_locked(
    session: AsyncSession,
    *,
    user_id: int,
    app_id: int,
    deployer: AppDeployer,
    cover_generator: CoverScheduler | None,
    key_factory: Callable[[], str],
) -> str:
    app = await require_owned_app(session, app_id, user_id)
    logger.info(
        "应用部署开始 user_id=%s app_id=%s code_gen_type=%s",
        user_id,
        app_id,
        app.code_gen_type or "html",
    )
    is_new_key = not app.deploy_key
    deploy_key = app.deploy_key or await _unique_deploy_key(session, key_factory)
    try:
        deploy_url = await asyncio.to_thread(deployer.deploy, app, deploy_key)
    except DeploymentSourceError as exc:
        logger.warning(
            "应用部署失败 user_id=%s app_id=%s reason=source error=%s",
            user_id,
            app_id,
            exc,
        )
        raise BusinessError(ErrorCode.NOT_FOUND, str(exc)) from exc
    except DeploymentWriteError as exc:
        logger.exception("应用部署失败 user_id=%s app_id=%s reason=write", user_id, app_id)
        raise BusinessError(ErrorCode.SYSTEM_ERROR, str(exc)) from exc

    app.deploy_key = deploy_key
    app.deployed_time = datetime.now()
    app.deploy_revision = (app.deploy_revision or 0) + 1
    app.edit_time = datetime.now()
    try:
        await session.commit()
    except Exception:
        logger.exception("应用部署提交失败 user_id=%s app_id=%s", user_id, app_id)
        await session.rollback()
        if is_new_key:
            await asyncio.to_thread(deployer.remove, deploy_key)
        raise
    if cover_generator is not None:
        cover_generator.schedule(app.id, deploy_url, app.deploy_revision)
    logger.info(
        "应用部署成功 user_id=%s app_id=%s deploy_key=%s deploy_revision=%s deploy_url=%s",
        user_id,
        app_id,
        deploy_key,
        app.deploy_revision,
        deploy_url,
    )
    return deploy_url
