import asyncio
import logging
from datetime import datetime
from math import ceil
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.app_assets import AppAssetCleaner
from app.codegen_router import CodeGenTypeRouter
from app.errors import BusinessError
from app.models import App, AppMemory, ChatHistory, ModelCallLog, User
from app.response import ErrorCode
from app.schemas import (
    AppAdminUpdateRequest,
    AppCreateRequest,
    AppQueryRequest,
    AppUpdateRequest,
    AppVO,
    UserVO,
)

logger = logging.getLogger(__name__)

_SORT_COLUMNS = {
    "appName": App.app_name,
    "createTime": App.create_time,
    "updateTime": App.update_time,
    "priority": App.priority,
}


async def require_owned_app(session: AsyncSession, app_id: int, user_id: int) -> App:
    if app_id <= 0:
        raise BusinessError(ErrorCode.PARAMS_ERROR, "应用ID不能为空")
    app = await session.scalar(select(App).where(App.id == app_id, App.is_delete == 0))
    if app is None:
        raise BusinessError(ErrorCode.NOT_FOUND, "应用不存在")
    if app.user_id != user_id:
        raise BusinessError(ErrorCode.NO_AUTH, "无权限操作该应用")
    return app


async def _user_map(session: AsyncSession, user_ids: set[int]) -> dict[int, UserVO]:
    if not user_ids:
        return {}
    users = (await session.scalars(select(User).where(User.id.in_(user_ids)))).all()
    return {user.id: UserVO.model_validate(user) for user in users}


async def to_app_vo(session: AsyncSession, app: App) -> AppVO:
    users = await _user_map(session, {app.user_id})
    return AppVO.model_validate(app).model_copy(update={"user": users.get(app.user_id)})


async def create_app(
    session: AsyncSession,
    user_id: int,
    payload: AppCreateRequest,
    router: CodeGenTypeRouter,
) -> int:
    if not payload.init_prompt.strip():
        raise BusinessError(ErrorCode.PARAMS_ERROR, "初始化 prompt 不能为空")
    code_gen_type = await router.route(payload.init_prompt)
    app = App(
        app_name=payload.app_name,
        init_prompt=payload.init_prompt,
        code_gen_type=code_gen_type.value,
        priority=0,
        user_id=user_id,
        is_delete=0,
    )
    session.add(app)
    await session.commit()
    await session.refresh(app)
    logger.info(
        "应用创建成功 app_id=%s user_id=%s code_gen_type=%s",
        app.id,
        user_id,
        code_gen_type.value,
    )
    return app.id


async def get_owned_app(session: AsyncSession, app_id: int, user_id: int) -> AppVO:
    app = await require_owned_app(session, app_id, user_id)
    return await to_app_vo(session, app)


async def update_app(
    session: AsyncSession,
    user_id: int,
    payload: AppUpdateRequest,
) -> None:
    if not payload.app_name.strip():
        raise BusinessError(ErrorCode.PARAMS_ERROR, "应用名称不能为空")
    app = await require_owned_app(session, payload.id, user_id)
    app.app_name = payload.app_name.strip()
    app.edit_time = datetime.now()
    await session.commit()
    logger.info("应用更新成功 app_id=%s user_id=%s", app.id, user_id)


def _validated_page(query: AppQueryRequest) -> None:
    if query.page_num < 1 or query.page_size < 1 or query.page_size > 20:
        raise BusinessError(ErrorCode.PARAMS_ERROR, "分页参数错误，每页最多查询 20 个应用")


def _apply_filters(
    query: AppQueryRequest,
    conditions: list,
    *,
    include_user_filter: bool = True,
    include_featured_filter: bool = True,
) -> None:
    if query.id is not None:
        conditions.append(App.id == query.id)
    if query.app_name:
        conditions.append(App.app_name.like(f"%{query.app_name}%"))
    if query.init_prompt:
        conditions.append(App.init_prompt.like(f"%{query.init_prompt}%"))
    if query.code_gen_type:
        conditions.append(App.code_gen_type == query.code_gen_type)
    if include_user_filter and query.user_id is not None:
        conditions.append(App.user_id == query.user_id)
    if include_featured_filter and query.priority is not None:
        conditions.append(App.priority == query.priority)
    if include_featured_filter and query.is_featured is not None:
        conditions.append(App.priority > 90 if query.is_featured else App.priority <= 90)


def _order_by(query: AppQueryRequest) -> ColumnElement[Any]:
    column = _SORT_COLUMNS.get(query.sort_field or "", App.update_time)
    return column.asc() if query.sort_order == "ascend" else column.desc()


async def list_apps(
    session: AsyncSession,
    query: AppQueryRequest,
    *,
    current_user_id: int | None = None,
    featured_only: bool = False,
) -> dict:
    _validated_page(query)
    conditions = [App.is_delete == 0]
    if current_user_id is not None:
        conditions.append(App.user_id == current_user_id)
    if featured_only:
        conditions.append(App.priority > 90)
    _apply_filters(
        query,
        conditions,
        include_user_filter=current_user_id is None,
        include_featured_filter=not featured_only,
    )

    total = await session.scalar(select(func.count(App.id)).where(*conditions)) or 0
    statement = select(App).where(*conditions)
    if featured_only:
        statement = statement.order_by(
            App.priority.desc(),
            App.update_time.desc(),
            App.id.desc(),
        )
    else:
        statement = statement.order_by(_order_by(query), App.id.desc())
    apps = (
        await session.scalars(
            statement.offset((query.page_num - 1) * query.page_size).limit(query.page_size)
        )
    ).all()
    users = await _user_map(session, {app.user_id for app in apps})
    records = [
        AppVO.model_validate(app)
        .model_copy(update={"user": users.get(app.user_id)})
        .model_dump(mode="json", by_alias=True)
        for app in apps
    ]
    return {
        "records": records,
        "pageNumber": query.page_num,
        "pageSize": query.page_size,
        "totalPage": ceil(total / query.page_size) if total else 0,
        "totalRow": total,
        "optimizeCountQuery": False,
    }


async def delete_app(
    session: AsyncSession,
    user_id: int,
    app_id: int,
    cleaner: AppAssetCleaner,
) -> None:
    app = await require_owned_app(session, app_id, user_id)
    await _delete_app_record(session, app, cleaner)
    logger.info("应用删除成功 app_id=%s user_id=%s", app_id, user_id)


async def _delete_app_record(
    session: AsyncSession,
    app: App,
    cleaner: AppAssetCleaner,
) -> None:
    app_id = app.id
    await session.execute(delete(ChatHistory).where(ChatHistory.app_id == app_id))
    await session.execute(delete(AppMemory).where(AppMemory.app_id == app_id))
    await session.execute(delete(ModelCallLog).where(ModelCallLog.app_id == app_id))
    await session.delete(app)
    await session.commit()
    try:
        await asyncio.to_thread(cleaner.delete_app_artifacts, app)
    except Exception:
        logger.exception("应用 %s 删除后清理本地产物失败", app_id)


async def get_app_by_id_for_admin(session: AsyncSession, app_id: int) -> dict:
    app = await session.scalar(select(App).where(App.id == app_id, App.is_delete == 0))
    if app is None:
        raise BusinessError(ErrorCode.NOT_FOUND, "应用不存在")
    return (await to_app_vo(session, app)).model_dump(mode="json", by_alias=True)


async def update_app_by_admin(
    session: AsyncSession,
    payload: AppAdminUpdateRequest,
) -> None:
    app = await session.scalar(
        select(App).where(App.id == payload.id, App.is_delete == 0)
    )
    if app is None:
        raise BusinessError(ErrorCode.NOT_FOUND, "应用不存在")
    if payload.app_name is not None:
        if not payload.app_name.strip():
            raise BusinessError(ErrorCode.PARAMS_ERROR, "应用名称不能为空")
        app.app_name = payload.app_name.strip()
    if payload.cover is not None:
        app.cover = payload.cover.strip() or None
    if payload.priority is not None:
        app.priority = payload.priority
    app.edit_time = datetime.now()
    await session.commit()
    logger.info("管理员更新应用成功 app_id=%s", app.id)


async def delete_app_by_admin(
    session: AsyncSession,
    app_id: int,
    cleaner: AppAssetCleaner,
) -> None:
    app = await session.scalar(select(App).where(App.id == app_id, App.is_delete == 0))
    if app is None:
        raise BusinessError(ErrorCode.NOT_FOUND, "应用不存在")
    await _delete_app_record(session, app, cleaner)
    logger.info("管理员删除应用成功 app_id=%s", app_id)
