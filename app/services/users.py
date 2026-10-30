from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import BusinessError
from app.models import User
from app.response import ErrorCode
from app.schemas import LoginUserVO, UserLoginRequest, UserRegisterRequest
from app.security import PasswordService


def _normalize_new_account(account: str) -> str:
    normalized = account.strip()
    if len(normalized) < 4 or len(normalized) > 256:
        raise BusinessError(ErrorCode.PARAMS_ERROR, "用户账号长度应为 4 到 256 位")
    return normalized


def _validate_registration_password(password: str) -> None:
    if len(password) < 8 or len(password) > 128:
        raise BusinessError(ErrorCode.PARAMS_ERROR, "密码长度应为 8 到 128 位")


def _validate_login_credentials(account: str, password: str) -> None:
    if len(account) < 4 or len(account) > 256:
        raise BusinessError(ErrorCode.PARAMS_ERROR, "用户账号长度应为 4 到 256 位")
    if len(password) < 8:
        raise BusinessError(ErrorCode.PARAMS_ERROR, "密码长度不能小于 8 位")


async def register_user(
    session: AsyncSession,
    request: UserRegisterRequest,
    password_service: PasswordService,
) -> int:
    account = _normalize_new_account(request.user_account)
    _validate_registration_password(request.user_password)
    if request.user_password != request.check_password:
        raise BusinessError(ErrorCode.PARAMS_ERROR, "两次输入的密码不一致")

    existing = await session.scalar(
        select(User.id).where(User.user_account == account, User.is_delete == 0)
    )
    if existing is not None:
        raise BusinessError(ErrorCode.PARAMS_ERROR, "账号名重复")

    user = User(
        user_account=account,
        user_password=password_service.hash(request.user_password),
        user_name=f"用户{account}",
        user_role="user",
        is_delete=0,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise BusinessError(ErrorCode.PARAMS_ERROR, "账号名重复") from exc
    await session.refresh(user)
    return user.id


async def login_user(
    session: AsyncSession,
    request: UserLoginRequest,
    password_service: PasswordService,
) -> User:
    account = request.user_account
    _validate_login_credentials(account, request.user_password)
    user = await session.scalar(
        select(User).where(User.user_account == account, User.is_delete == 0)
    )
    if user is None:
        raise BusinessError(ErrorCode.PARAMS_ERROR, "用户不存在或密码错误")

    verification = password_service.verify(user.user_password, request.user_password)
    if not verification.valid:
        raise BusinessError(ErrorCode.PARAMS_ERROR, "用户不存在或密码错误")

    if verification.needs_upgrade:
        user.user_password = password_service.hash(request.user_password)
        user.edit_time = datetime.now()
        await session.commit()
        await session.refresh(user)
    return user


def to_login_user_vo(user: User) -> LoginUserVO:
    return LoginUserVO.model_validate(user)
