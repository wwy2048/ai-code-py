from typing import cast

from fastapi import APIRouter, Request

from app.app_assets import AppAssetCleaner
from app.codegen_router import CodeGenTypeRouter
from app.deps import CurrentUser, DbSession
from app.response import success
from app.schemas import AppCreateRequest, AppQueryRequest, AppUpdateRequest, DeleteRequest
from app.services.apps import create_app, delete_app, get_owned_app, list_apps, update_app

router = APIRouter(prefix='/app', tags=['app'])


def _type_router(request: Request) -> CodeGenTypeRouter:
    return cast(CodeGenTypeRouter, request.app.state.code_gen_type_router)


def _asset_cleaner(request: Request) -> AppAssetCleaner:
    return cast(AppAssetCleaner, request.app.state.asset_cleaner)


@router.post('/create')
async def add_app(
    payload: AppCreateRequest,
    request: Request,
    session: DbSession,
    current_user: CurrentUser,
) -> dict:
    app_id = await create_app(session, current_user.id, payload, _type_router(request))
    return success(app_id)


@router.put('/update')
async def update_app_endpoint(
    payload: AppUpdateRequest,
    session: DbSession,
    current_user: CurrentUser,
) -> dict:
    await update_app(session, current_user.id, payload)
    return success(True)


@router.delete('/delete')
async def delete_app_endpoint(
    payload: DeleteRequest,
    request: Request,
    session: DbSession,
    current_user: CurrentUser,
) -> dict:
    await delete_app(session, current_user.id, payload.id, _asset_cleaner(request))
    return success(True)


@router.get('/get')
async def get_app_vo_by_id(
    id: int,
    session: DbSession,
    current_user: CurrentUser,
) -> dict:
    app_vo = await get_owned_app(session, id, current_user.id)
    return success(app_vo.model_dump(mode='json', by_alias=True))


@router.get('/detail/{app_id}')
async def get_app_detail(
    app_id: int,
    session: DbSession,
    current_user: CurrentUser,
) -> dict:
    app_vo = await get_owned_app(session, app_id, current_user.id)
    return success(app_vo.model_dump(mode='json', by_alias=True))


@router.post('/my/list/page/vo')
async def list_my_apps(
    payload: AppQueryRequest,
    session: DbSession,
    current_user: CurrentUser,
) -> dict:
    return success(await list_apps(session, payload, current_user_id=current_user.id))


@router.post('/good/list/page/vo')
async def list_good_apps(payload: AppQueryRequest, session: DbSession) -> dict:
    return success(await list_apps(session, payload, featured_only=True))
