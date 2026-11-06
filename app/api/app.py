import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import Response, StreamingResponse

from app.app_assets import AppAssetCleaner
from app.codegen_router import CodeGenTypeRouter
from app.deployment import AppDeployer
from app.deps import AdminUser, CurrentUser, DbSession
from app.download import WorkspaceArchiveError, build_workspace_zip
from app.errors import BusinessError
from app.generation.adapters.memory import MemoryStore
from app.generation.domain.commands import (
    ClearPausedFollowupsCommand,
    ControlCommand,
    DeleteQueuedCommand,
    EnqueueFollowupCommand,
    InterruptTurnCommand,
    QueueCommand,
    RedirectTurnCommand,
    ResumeFollowupsCommand,
    StartTurnCommand,
    SteerQueuedCommand,
    SteerTurnCommand,
)
from app.response import success
from app.schemas import (
    AppAdminUpdateRequest,
    AppCreateRequest,
    AppDeployRequest,
    AppQueryRequest,
    AppUpdateRequest,
    DeleteRequest,
    GenerationControlRequest,
    GenerationQueueRequest,
    GenerationRequest,
)
from app.services.apps import (
    create_app,
    delete_app,
    delete_app_by_admin,
    get_app_by_id_for_admin,
    get_owned_app,
    list_apps,
    require_owned_app,
    update_app,
    update_app_by_admin,
)
from app.services.deployments import deploy_app

router = APIRouter(prefix="/app", tags=["app"])


def _type_router(request: Request) -> CodeGenTypeRouter:
    return cast(CodeGenTypeRouter, request.app.state.code_gen_type_router)


def _asset_cleaner(request: Request) -> AppAssetCleaner:
    return cast(AppAssetCleaner, request.app.state.asset_cleaner)


def _deployer(request: Request) -> AppDeployer:
    return cast(AppDeployer, request.app.state.app_deployer)


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


@router.post("/admin/list/page/vo")
async def list_apps_by_admin(
    payload: AppQueryRequest,
    session: DbSession,
    _: AdminUser,
) -> dict:
    return success(await list_apps(session, payload))


@router.get("/admin/detail/{app_id}")
async def get_app_detail_by_admin(
    app_id: int,
    session: DbSession,
    _: AdminUser,
) -> dict:
    return success(await get_app_by_id_for_admin(session, app_id))


@router.put("/admin/update")
async def update_app_by_admin_endpoint(
    payload: AppAdminUpdateRequest,
    session: DbSession,
    _: AdminUser,
) -> dict:
    await update_app_by_admin(session, payload)
    return success(True)


@router.delete("/admin/delete")
async def delete_app_by_admin_endpoint(
    payload: DeleteRequest,
    request: Request,
    session: DbSession,
    _: AdminUser,
) -> dict:
    await delete_app_by_admin(session, payload.id, _asset_cleaner(request))
    return success(True)


@router.post("/appDeploy")
async def deploy_app_endpoint(
    payload: AppDeployRequest,
    request: Request,
    session: DbSession,
    current_user: CurrentUser,
) -> dict:
    deploy_url = await deploy_app(
        session,
        user_id=current_user.id,
        app_id=payload.app_id,
        deployer=_deployer(request),
        cover_generator=request.app.state.cover_generator,
        key_factory=request.app.state.deploy_key_factory,
    )
    return success(deploy_url)


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


@router.get("/download/{app_id}")
async def download_app_code(
    app_id: int,
    request: Request,
    session: DbSession,
    current_user: CurrentUser,
) -> Response:
    app = await require_owned_app(session, app_id, current_user.id)
    workspace_root = Path(request.app.state.settings.workspace_root)
    try:
        content = await asyncio.to_thread(build_workspace_zip, app, workspace_root)
    except WorkspaceArchiveError as exc:
        raise BusinessError(exc.code, str(exc)) from exc
    filename = f"app-{app.id}.zip"
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{app_id}/memory")
async def list_app_memory(
    app_id: int,
    request: Request,
    session: DbSession,
    current_user: CurrentUser,
) -> dict:
    await require_owned_app(session, app_id, current_user.id)
    settings = request.app.state.settings
    store = MemoryStore(
        session,
        app_id=app_id,
        user_id=current_user.id,
        max_chars=settings.app_memory_max_chars,
    )
    records = await store.list_records()
    return success(
        {
            "enabled": settings.app_memory_enabled,
            "maxChars": settings.app_memory_max_chars,
            "records": [MemoryStore.serialize(record) for record in records],
        }
    )


@router.delete("/{app_id}/memory")
async def clear_app_memory(
    app_id: int,
    request: Request,
    session: DbSession,
    current_user: CurrentUser,
) -> dict:
    await require_owned_app(session, app_id, current_user.id)
    await MemoryStore(
        session,
        app_id=app_id,
        user_id=current_user.id,
        max_chars=request.app.state.settings.app_memory_max_chars,
    ).clear()
    return success(True)


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
    runtime = request.app.state.generation_runtime
    job = await runtime.start_turn(
        StartTurnCommand(
            app_id=app.id,
            user_id=current_user.id,
            code_gen_type=app.code_gen_type or "html",
            message=payload.message,
            turn_id=payload.request_id,
        )
    )
    try:
        cursor = int(last_event_id) if last_event_id else None
    except ValueError:
        cursor = None

    async def stream_events() -> AsyncIterator[str]:
        async for event in runtime.stream(job, cursor):
            yield runtime.encode_sse(event)

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat/turn/control")
async def control_generation(
    payload: GenerationControlRequest,
    request: Request,
    session: DbSession,
    current_user: CurrentUser,
) -> dict:
    app = await require_owned_app(session, payload.app_id, current_user.id)
    runtime = request.app.state.generation_runtime
    app_id = app.id
    user_id = current_user.id
    code_gen_type = app.code_gen_type or "html"
    command: ControlCommand
    if payload.action == "steer":
        command = SteerTurnCommand(
            app_id=app_id,
            user_id=user_id,
            code_gen_type=code_gen_type,
            turn_id=payload.turn_id,
            command_id=payload.command_id,
            text=payload.message or "",
        )
    elif payload.action == "redirect":
        command = RedirectTurnCommand(
            app_id=app_id,
            user_id=user_id,
            code_gen_type=code_gen_type,
            turn_id=payload.turn_id,
            command_id=payload.command_id,
            text=payload.message or "",
        )
    else:
        command = InterruptTurnCommand(
            app_id=app_id,
            user_id=user_id,
            code_gen_type=code_gen_type,
            turn_id=payload.turn_id,
            command_id=payload.command_id,
        )
    ack = await runtime.control(command)
    return success(ack.as_dict())

@router.post("/chat/queue/control")
async def control_generation_queue(
    payload: GenerationQueueRequest,
    request: Request,
    session: DbSession,
    current_user: CurrentUser,
) -> dict:
    app = await require_owned_app(session, payload.app_id, current_user.id)
    runtime = request.app.state.generation_runtime
    command_id = payload.command_id
    queue_command: QueueCommand
    if payload.action == "enqueue":
        queue_command = EnqueueFollowupCommand(
            app_id=app.id,
            user_id=current_user.id,
            code_gen_type=app.code_gen_type or "html",
            message=payload.message or "",
            command_id=command_id,
            clear_paused=payload.clear_paused,
        )
    elif payload.action == "steer":
        queue_command = SteerQueuedCommand(
            app_id=app.id,
            command_id=command_id,
            item_id=payload.item_id or "",
        )
    elif payload.action == "delete":
        queue_command = DeleteQueuedCommand(
            app_id=app.id,
            command_id=command_id,
            item_id=payload.item_id or "",
        )
    elif payload.action == "resume":
        queue_command = ResumeFollowupsCommand(app_id=app.id, command_id=command_id)
    else:
        queue_command = ClearPausedFollowupsCommand(app_id=app.id, command_id=command_id)
    ack = await runtime.queue_control(queue_command)
    return success(ack.as_dict())


@router.get("/chat/queue/events")
async def stream_generation_queue(
    request: Request,
    session: DbSession,
    current_user: CurrentUser,
    app_id: int = Query(alias="appId"),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    app = await require_owned_app(session, app_id, current_user.id)
    runtime = request.app.state.generation_runtime
    try:
        cursor = int(last_event_id) if last_event_id else None
    except ValueError:
        cursor = None

    async def stream_queue_events() -> AsyncIterator[str]:
        async for event in runtime.stream_queue(app.id, cursor):
            yield runtime.encode_sse(event)

    return StreamingResponse(
        stream_queue_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
