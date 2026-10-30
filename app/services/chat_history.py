from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import BusinessError
from app.models import ChatHistory
from app.response import ErrorCode
from app.services.apps import require_owned_app


def serialize_chat_history(record: ChatHistory) -> dict:
    return {
        'id': record.id,
        'message': record.message,
        'messageType': record.message_type,
        'appId': record.app_id,
        'userId': record.user_id,
        'createTime': record.create_time.isoformat(),
        'updateTime': record.update_time.isoformat(),
        'isDelete': record.is_delete,
        'turnId': record.turn_id,
        'sequence': record.sequence,
        'messageKind': record.message_kind,
        'visible': record.visible,
        'active': record.active,
    }


async def list_visible_history(
    session: AsyncSession,
    *,
    app_id: int,
    user_id: int,
    page_size: int,
    last_create_time: datetime | None,
) -> dict:
    await require_owned_app(session, app_id, user_id)
    if page_size < 1 or page_size > 20:
        raise BusinessError(ErrorCode.PARAMS_ERROR, 'pageSize 必须在 1 到 20 之间')

    conditions = [
        ChatHistory.app_id == app_id,
        ChatHistory.is_delete == 0,
        ChatHistory.visible.is_(True),
    ]
    if last_create_time is not None:
        conditions.append(ChatHistory.create_time < last_create_time)
    total = await session.scalar(select(func.count(ChatHistory.id)).where(*conditions)) or 0
    records = (
        await session.scalars(
            select(ChatHistory)
            .where(*conditions)
            .order_by(ChatHistory.create_time.desc(), ChatHistory.id.desc())
            .limit(page_size)
        )
    ).all()
    return {
        'records': [serialize_chat_history(record) for record in records],
        'pageNumber': 1,
        'pageSize': page_size,
        'totalPage': (total + page_size - 1) // page_size if total else 0,
        'totalRow': total,
        'optimizeCountQuery': False,
    }
