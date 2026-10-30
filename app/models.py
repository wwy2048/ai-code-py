from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    """兼容现有 MySQL user 表的用户模型。"""

    __tablename__ = "user"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    user_account: Mapped[str] = mapped_column(
        "userAccount", String(256), unique=True, nullable=False
    )
    user_password: Mapped[str] = mapped_column("userPassword", String(512), nullable=False)
    user_name: Mapped[str | None] = mapped_column("userName", String(256))
    user_avatar: Mapped[str | None] = mapped_column("userAvatar", String(1024))
    user_profile: Mapped[str | None] = mapped_column("userProfile", String(512))
    user_role: Mapped[str] = mapped_column(
        "userRole", String(256), nullable=False, server_default=text("'user'")
    )
    edit_time: Mapped[datetime] = mapped_column(
        "editTime", DateTime, nullable=False, server_default=func.now()
    )
    create_time: Mapped[datetime] = mapped_column(
        "createTime", DateTime, nullable=False, server_default=func.now()
    )
    update_time: Mapped[datetime] = mapped_column(
        "updateTime", DateTime, nullable=False, server_default=func.now()
    )
    is_delete: Mapped[int] = mapped_column(
        "isDelete", SmallInteger, nullable=False, server_default=text("0")
    )


class App(Base):
    """兼容现有 MySQL app 表的应用模型。"""

    __tablename__ = "app"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    app_name: Mapped[str | None] = mapped_column("appName", String(256))
    cover: Mapped[str | None] = mapped_column("cover", String(512))
    init_prompt: Mapped[str | None] = mapped_column("initPrompt", Text)
    code_gen_type: Mapped[str | None] = mapped_column("codeGenType", String(64))
    deploy_key: Mapped[str | None] = mapped_column("deployKey", String(64), unique=True)
    deployed_time: Mapped[datetime | None] = mapped_column("deployedTime", DateTime)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    user_id: Mapped[int] = mapped_column("userId", BigInteger, nullable=False)
    edit_time: Mapped[datetime] = mapped_column(
        "editTime", DateTime, nullable=False, server_default=func.now()
    )
    create_time: Mapped[datetime] = mapped_column(
        "createTime", DateTime, nullable=False, server_default=func.now()
    )
    update_time: Mapped[datetime] = mapped_column(
        "updateTime", DateTime, nullable=False, server_default=func.now()
    )
    is_delete: Mapped[int] = mapped_column(
        "isDelete", SmallInteger, nullable=False, server_default=text("0")
    )


class ChatHistory(Base):
    """兼容现有 MySQL chat_history 表的基础模型。"""

    __tablename__ = "chat_history"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column("messageType", String(32), nullable=False)
    app_id: Mapped[int] = mapped_column("appId", BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column("userId", BigInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(
        "createTime", DateTime, nullable=False, server_default=func.now()
    )
    update_time: Mapped[datetime] = mapped_column(
        "updateTime", DateTime, nullable=False, server_default=func.now()
    )
    is_delete: Mapped[int] = mapped_column(
        "isDelete", SmallInteger, nullable=False, server_default=text("0")
    )
    turn_id: Mapped[str | None] = mapped_column("turn_id", String(64))
    sequence: Mapped[int | None] = mapped_column("sequence", Integer)
    message_kind: Mapped[str] = mapped_column(
        "message_kind", String(32), nullable=False, server_default=text("'legacy'")
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column("payload", JSON)
    visible: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("1"))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("1"))
    tool_call_id: Mapped[str | None] = mapped_column("tool_call_id", String(128))
    tool_name: Mapped[str | None] = mapped_column("tool_name", String(64))
    is_error: Mapped[bool] = mapped_column(
        "is_error", Boolean, nullable=False, server_default=text("0")
    )
    compacted_by: Mapped[int | None] = mapped_column("compacted_by", BigInteger)


class ModelCallLog(Base):
    """仅记录模型调用元数据，不保存提示词、工具正文或密钥。"""

    __tablename__ = "model_call_log"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    app_id: Mapped[int] = mapped_column("appId", BigInteger, nullable=False)
    turn_id: Mapped[str] = mapped_column("turn_id", String(64), nullable=False)
    model_name: Mapped[str] = mapped_column("model_name", String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    duration_ms: Mapped[int] = mapped_column("duration_ms", Integer, nullable=False)
    input_tokens: Mapped[int] = mapped_column(
        "input_tokens", Integer, nullable=False, server_default=text("0")
    )
    output_tokens: Mapped[int] = mapped_column(
        "output_tokens", Integer, nullable=False, server_default=text("0")
    )
    error_type: Mapped[str | None] = mapped_column("error_type", String(128))
    error_summary: Mapped[str | None] = mapped_column("error_summary", String(256))
    retry_count: Mapped[int] = mapped_column(
        "retry_count", Integer, nullable=False, server_default=text("0")
    )
    create_time: Mapped[datetime] = mapped_column(
        "createTime", DateTime, nullable=False, server_default=func.now()
    )
