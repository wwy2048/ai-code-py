from fastapi import APIRouter

from app.deps import AdminUser, DbSession
from app.response import success
from app.schemas import ModelCallConsumptionQueryRequest
from app.services.model_calls import list_model_call_consumption

router = APIRouter(prefix="/modelCall", tags=["modelCall"])


@router.post("/admin/consumption/page")
async def list_model_call_consumption_by_admin(
    payload: ModelCallConsumptionQueryRequest,
    session: DbSession,
    _: AdminUser,
) -> dict:
    return success(await list_model_call_consumption(session, payload))
