from datetime import datetime

from fastapi import APIRouter

from app.deps import CurrentUser, DbSession
from app.response import success
from app.services.chat_history import list_visible_history

router = APIRouter(prefix='/chatHistory', tags=['chatHistory'])


@router.get('/app/{app_id}')
async def list_app_chat_history(
    app_id: int,
    session: DbSession,
    current_user: CurrentUser,
    pageSize: int = 10,
    lastCreateTime: datetime | None = None,
) -> dict:
    page = await list_visible_history(
        session,
        app_id=app_id,
        user_id=current_user.id,
        page_size=pageSize,
        last_create_time=lastCreateTime,
    )
    return success(page)
