from datetime import datetime
from math import ceil
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import BusinessError
from app.models import User
from app.response import ErrorCode
from app.schemas import (
    LoginUserVO,
    UserAdminUpdateRequest,
    UserLoginRequest,
    UserQueryRequest,
    UserRegisterRequest,
    UserVO,
)
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


async def require_user(session: AsyncSession, user_id: int) -> User:
    user = await session.scalar(select(User).where(User.id == user_id, User.is_delete == 0))
    if user is None:
        raise BusinessError(ErrorCode.NOT_FOUND, "用户不存在")
    return user


async def list_users(session: AsyncSession, query: UserQueryRequest) -> dict:
    if not 1 <= query.page_size <= 20 or query.page_num < 1:
        raise BusinessError(ErrorCode.PARAMS_ERROR, "分页参数错误，每页最多查询 20 个用户")
    conditions = [User.is_delete == 0]
    filters: dict[str, Any] = {
        "id": User.id == query.id if query.id is not None else None,
        "userAccount": User.user_account.like(f"%{query.user_account}%")
        if query.user_account
        else None,
        "userName": User.user_name.like(f"%{query.user_name}%") if query.user_name else None,
        "userProfile": User.user_profile.like(f"%{query.user_profile}%")
        if query.user_profile
        else None,
        "userRole": User.user_role == query.user_role if query.user_role else None,
    }
    conditions.extend(condition for condition in filters.values() if condition is not None)
    sort_columns = {
        "id": User.id,
        "userAccount": User.user_account,
        "userName": User.user_name,
        "createTime": User.create_time,
        "updateTime": User.update_time,
    }
    sort_column = sort_columns.get(query.sort_field or "", User.id)
    order = sort_column.asc() if query.sort_order == "ascend" else sort_column.desc()
    total = await session.scalar(select(func.count(User.id)).where(*conditions)) or 0
    users = (
        await session.scalars(
            select(User)
            .where(*conditions)
            .order_by(order, User.id.desc())
            .offset((query.page_num - 1) * query.page_size)
            .limit(query.page_size)
        )
    ).all()
    records = [UserVO.model_validate(user).model_dump(mode="json", by_alias=True) for user in users]
    return {
        "records": records,
        "pageNumber": query.page_num,
        "pageSize": query.page_size,
        "totalPage": ceil(total / query.page_size) if total else 0,
        "totalRow": total,
        "optimizeCountQuery": False,
    }


async def get_user_vo(session: AsyncSession, user_id: int) -> dict:
    user = await require_user(session, user_id)
    return UserVO.model_validate(user).model_dump(mode="json", by_alias=True)


async def update_user_by_admin(
    session: AsyncSession,
    payload: UserAdminUpdateRequest,
) -> None:
    user = await require_user(session, payload.id)
    if payload.user_role is not None:
        if payload.user_role not in {"user", "admin"}:
            raise BusinessError(ErrorCode.PARAMS_ERROR, "用户角色不合法")
        user.user_role = payload.user_role
    if payload.user_name is not None:
        user.user_name = payload.user_name.strip() or None
    if payload.user_avatar is not None:
        user.user_avatar = payload.user_avatar.strip() or None
    if payload.user_profile is not None:
        user.user_profile = payload.user_profile.strip() or None
    user.edit_time = datetime.now()
    await session.commit()


async def delete_user_by_admin(session: AsyncSession, user_id: int, admin_id: int) -> None:
    if user_id == admin_id:
        raise BusinessError(ErrorCode.PARAMS_ERROR, "不能删除当前管理员账号")
    user = await require_user(session, user_id)
    user.is_delete = 1
    user.edit_time = datetime.now()
    await session.commit()
