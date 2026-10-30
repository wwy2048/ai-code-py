from datetime import datetime

from fastapi import APIRouter

from app.deps import AdminUser, CurrentUser, DbSession
from app.response import success
from app.schemas import ChatHistoryQueryRequest
from app.services.chat_history import get_turn_debug, list_all_history, list_visible_history

router = APIRouter(prefix="/chatHistory", tags=["chatHistory"])


@router.post("/admin/list/page/vo")
async def list_all_chat_history_by_page(
    payload: ChatHistoryQueryRequest,
    session: DbSession,
    _: AdminUser,
) -> dict:
    return success(await list_all_history(session, payload))


@router.get("/admin/turn/{app_id}/{turn_id}")
async def get_chat_turn_debug(
    app_id: int,
    turn_id: str,
    session: DbSession,
    _: AdminUser,
) -> dict:
    return success(await get_turn_debug(session, app_id=app_id, turn_id=turn_id))


@router.get("/app/{app_id}")
async def list_app_chat_history(
    app_id: int,
    session: DbSession,
    current_user: CurrentUser,
    pageSize: int = 10,
    lastCreateTime: datetime | None = None,
    lastId: int | None = None,
) -> dict:
    page = await list_visible_history(
        session,
        app_id=app_id,
        user_id=current_user.id,
        page_size=pageSize,
        last_create_time=lastCreateTime,
        last_id=lastId,
    )
    return success(page)
