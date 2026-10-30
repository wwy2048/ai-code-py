from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import BusinessError
from app.models import User
from app.response import ErrorCode
from app.security import PasswordService
from app.session_store import SessionStore


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


def get_password_service(request: Request) -> PasswordService:
    return PasswordService(request.app.state.settings)


def get_session_store(request: Request) -> SessionStore:
    settings = request.app.state.settings
    return SessionStore(request.app.state.redis, settings.session_ttl_seconds)


DbSession = Annotated[AsyncSession, Depends(get_db_session)]
PasswordServiceDep = Annotated[PasswordService, Depends(get_password_service)]
SessionStoreDep = Annotated[SessionStore, Depends(get_session_store)]


async def get_current_user(
    request: Request,
    session: DbSession,
    session_store: SessionStoreDep,
) -> User:
    settings = request.app.state.settings
    session_id = request.cookies.get(settings.session_cookie_name)
    user_id = await session_store.get_user_id(session_id)
    if user_id is None:
        raise BusinessError(ErrorCode.NOT_LOGIN, '未登录')

    user = await session.scalar(select(User).where(User.id == user_id, User.is_delete == 0))
    if user is None:
        await session_store.delete(session_id)
        raise BusinessError(ErrorCode.NOT_LOGIN, '未登录')
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
