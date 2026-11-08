from decimal import Decimal
from math import ceil
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import BusinessError
from app.models import App, ModelCallLog, User
from app.response import ErrorCode
from app.schemas import ModelCallConsumptionQueryRequest


def _as_float(value: Any) -> float:
    return float(Decimal(str(value or 0)))


def _as_int(value: Any) -> int:
    return int(value or 0)


async def list_model_call_consumption(
    session: AsyncSession,
    query: ModelCallConsumptionQueryRequest,
) -> dict:
    if query.page_num < 1 or not 1 <= query.page_size <= 20:
        raise BusinessError(ErrorCode.PARAMS_ERROR, "分页参数错误，每页最多查询 20 条")

    conditions = []
    if query.user_id is not None:
        conditions.append(ModelCallLog.user_id == query.user_id)
    if query.app_id is not None:
        conditions.append(ModelCallLog.app_id == query.app_id)

    summary_row = (
        await session.execute(
            select(
                func.count(ModelCallLog.id).label("total_calls"),
                func.coalesce(func.sum(ModelCallLog.input_tokens), 0).label(
                    "total_input_tokens"
                ),
                func.coalesce(func.sum(ModelCallLog.cached_input_tokens), 0).label(
                    "total_cached_input_tokens"
                ),
                func.coalesce(func.sum(ModelCallLog.output_tokens), 0).label(
                    "total_output_tokens"
                ),
                func.coalesce(func.sum(ModelCallLog.cost_cny), 0).label("total_cost_cny"),
            ).where(*conditions)
        )
    ).one()

    grouped = (
        select(ModelCallLog.user_id, ModelCallLog.app_id)
        .where(*conditions)
        .group_by(ModelCallLog.user_id, ModelCallLog.app_id)
        .subquery()
    )
    total = await session.scalar(select(func.count()).select_from(grouped)) or 0

    rows = (
        await session.execute(
            select(
                ModelCallLog.user_id.label("user_id"),
                ModelCallLog.app_id.label("app_id"),
                func.count(ModelCallLog.id).label("call_count"),
                func.coalesce(func.sum(ModelCallLog.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(ModelCallLog.cached_input_tokens), 0).label(
                    "cached_input_tokens"
                ),
                func.coalesce(func.sum(ModelCallLog.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(ModelCallLog.cost_cny), 0).label("cost_cny"),
                func.max(ModelCallLog.create_time).label("last_call_time"),
            )
            .where(*conditions)
            .group_by(ModelCallLog.user_id, ModelCallLog.app_id)
            .order_by(
                func.sum(ModelCallLog.cost_cny).desc(),
                func.max(ModelCallLog.create_time).desc(),
                ModelCallLog.user_id.asc(),
                ModelCallLog.app_id.asc(),
            )
            .offset((query.page_num - 1) * query.page_size)
            .limit(query.page_size)
        )
    ).all()

    user_ids = {int(row.user_id) for row in rows}
    app_ids = {int(row.app_id) for row in rows}
    users = (
        (
            await session.execute(
                select(User.id, User.user_account, User.user_name).where(User.id.in_(user_ids))
            )
        ).all()
        if user_ids
        else []
    )
    apps = (
        (
            await session.execute(
                select(App.id, App.app_name).where(App.id.in_(app_ids))
            )
        ).all()
        if app_ids
        else []
    )
    user_map = {int(user.id): user for user in users}
    app_map = {int(app.id): app for app in apps}

    records = []
    for row in rows:
        user = user_map.get(int(row.user_id))
        app = app_map.get(int(row.app_id))
        last_call_time = row.last_call_time
        records.append(
            {
                "userId": int(row.user_id),
                "appId": int(row.app_id),
                "userAccount": user.user_account if user is not None else None,
                "userName": user.user_name if user is not None else None,
                "appName": app.app_name if app is not None else None,
                "callCount": _as_int(row.call_count),
                "inputTokens": _as_int(row.input_tokens),
                "cachedInputTokens": _as_int(row.cached_input_tokens),
                "outputTokens": _as_int(row.output_tokens),
                "costCny": _as_float(row.cost_cny),
                "lastCallTime": last_call_time.isoformat() if last_call_time else None,
            }
        )

    return {
        "summary": {
            "totalCalls": _as_int(summary_row.total_calls),
            "totalInputTokens": _as_int(summary_row.total_input_tokens),
            "totalCachedInputTokens": _as_int(summary_row.total_cached_input_tokens),
            "totalOutputTokens": _as_int(summary_row.total_output_tokens),
            "totalCostCny": _as_float(summary_row.total_cost_cny),
        },
        "records": records,
        "pageNumber": query.page_num,
        "pageSize": query.page_size,
        "totalPage": ceil(total / query.page_size) if total else 0,
        "totalRow": total,
        "optimizeCountQuery": False,
    }
