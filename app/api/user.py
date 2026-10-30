from fastapi import APIRouter, Request, Response

from app.deps import CurrentUser, DbSession, PasswordServiceDep, SessionStoreDep
from app.response import success
from app.schemas import UserLoginRequest, UserRegisterRequest
from app.services.users import login_user, register_user, to_login_user_vo
from app.session_cookie import clear_session_cookie, read_session_id, set_session_cookie

router = APIRouter(prefix="/user", tags=["user"])


@router.post("/register")
async def user_register(
    payload: UserRegisterRequest,
    session: DbSession,
    password_service: PasswordServiceDep,
) -> dict:
    user_id = await register_user(session, payload, password_service)
    return success(user_id)


@router.post("/login")
async def user_login(
    payload: UserLoginRequest,
    request: Request,
    response: Response,
    session: DbSession,
    password_service: PasswordServiceDep,
    session_store: SessionStoreDep,
) -> dict:
    user = await login_user(session, payload, password_service)
    settings = request.app.state.settings
    await session_store.delete(read_session_id(request, settings))
    session_id = await session_store.create(user.id)
    set_session_cookie(response, settings, session_id)
    return success(to_login_user_vo(user).model_dump(by_alias=True))


@router.get("/get/login")
async def get_login_user(current_user: CurrentUser) -> dict:
    return success(to_login_user_vo(current_user).model_dump(by_alias=True))


@router.post("/logout")
async def user_logout(
    request: Request,
    response: Response,
    session_store: SessionStoreDep,
) -> dict:
    settings = request.app.state.settings
    await session_store.delete(read_session_id(request, settings))
    clear_session_cookie(response, settings)
    return success(True)
