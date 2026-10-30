from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, SmallInteger, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    """兼容现有 MySQL user 表的用户模型。"""

    __tablename__ = 'user'

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, 'sqlite'), primary_key=True, autoincrement=True
    )
    user_account: Mapped[str] = mapped_column(
        'userAccount', String(256), unique=True, nullable=False
    )
    user_password: Mapped[str] = mapped_column('userPassword', String(512), nullable=False)
    user_name: Mapped[str | None] = mapped_column('userName', String(256))
    user_avatar: Mapped[str | None] = mapped_column('userAvatar', String(1024))
    user_profile: Mapped[str | None] = mapped_column('userProfile', String(512))
    user_role: Mapped[str] = mapped_column(
        'userRole', String(256), nullable=False, server_default=text("'user'")
    )
    edit_time: Mapped[datetime] = mapped_column(
        'editTime', DateTime, nullable=False, server_default=func.now()
    )
    create_time: Mapped[datetime] = mapped_column(
        'createTime', DateTime, nullable=False, server_default=func.now()
    )
    update_time: Mapped[datetime] = mapped_column(
        'updateTime', DateTime, nullable=False, server_default=func.now()
    )
    is_delete: Mapped[int] = mapped_column(
        'isDelete', SmallInteger, nullable=False, server_default=text('0')
    )
