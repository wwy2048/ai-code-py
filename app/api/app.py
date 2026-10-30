from collections.abc import AsyncIterator
from typing import cast

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse

from app.app_assets import AppAssetCleaner
from app.codegen_router import CodeGenTypeRouter
from app.deps import CurrentUser, DbSession
from app.response import success
from app.schemas import (
    AppCreateRequest,
    AppQueryRequest,
    AppUpdateRequest,
    DeleteRequest,
    GenerationRequest,
)
from app.services.apps import (
    create_app,
    delete_app,
    get_owned_app,
    list_apps,
    require_owned_app,
    update_app,
)

router = APIRouter(prefix="/app", tags=["app"])


def _type_router(request: Request) -> CodeGenTypeRouter:
    return cast(CodeGenTypeRouter, request.app.state.code_gen_type_router)


def _asset_cleaner(request: Request) -> AppAssetCleaner:
    return cast(AppAssetCleaner, request.app.state.asset_cleaner)


@router.post("/create")
async def add_app(
    payload: AppCreateRequest,
    request: Request,
    session: DbSession,
    current_user: CurrentUser,
) -> dict:
    app_id = await create_app(session, current_user.id, payload, _type_router(request))
    return success(app_id)


@router.put("/update")
async def update_app_endpoint(
    payload: AppUpdateRequest,
    session: DbSession,
    current_user: CurrentUser,
) -> dict:
    await update_app(session, current_user.id, payload)
    return success(True)


@router.delete("/delete")
async def delete_app_endpoint(
    payload: DeleteRequest,
    request: Request,
    session: DbSession,
    current_user: CurrentUser,
) -> dict:
    await delete_app(session, current_user.id, payload.id, _asset_cleaner(request))
    return success(True)


@router.get("/get")
async def get_app_vo_by_id(
    id: int,
    session: DbSession,
    current_user: CurrentUser,
) -> dict:
    app_vo = await get_owned_app(session, id, current_user.id)
    return success(app_vo.model_dump(mode="json", by_alias=True))


@router.get("/detail/{app_id}")
async def get_app_detail(
    app_id: int,
    session: DbSession,
    current_user: CurrentUser,
) -> dict:
    app_vo = await get_owned_app(session, app_id, current_user.id)
    return success(app_vo.model_dump(mode="json", by_alias=True))


@router.post("/my/list/page/vo")
async def list_my_apps(
    payload: AppQueryRequest,
    session: DbSession,
    current_user: CurrentUser,
) -> dict:
    return success(await list_apps(session, payload, current_user_id=current_user.id))


@router.post("/good/list/page/vo")
async def list_good_apps(payload: AppQueryRequest, session: DbSession) -> dict:
    return success(await list_apps(session, payload, featured_only=True))


@router.post("/chat/gen/code")
async def generate_code(
    payload: GenerationRequest,
    request: Request,
    session: DbSession,
    current_user: CurrentUser,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    app = await require_owned_app(session, payload.app_id, current_user.id)
    manager = request.app.state.generation_manager
    job = await manager.start_turn(
        app_id=app.id,
        user_id=current_user.id,
        code_gen_type=app.code_gen_type or "html",
        message=payload.message,
        request_id=payload.request_id,
    )
    try:
        cursor = int(last_event_id) if last_event_id else None
    except ValueError:
        cursor = None

    async def stream_events() -> AsyncIterator[str]:
        async for event in manager.stream(job, cursor):
            yield manager.encode_sse(event)

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
