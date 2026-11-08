from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class UserRegisterRequest(ApiModel):
    user_account: str
    user_password: str
    check_password: str


class UserLoginRequest(ApiModel):
    user_account: str
    user_password: str


class LoginUserVO(ApiModel):
    id: int
    user_account: str
    user_name: str | None
    user_avatar: str | None
    user_profile: str | None
    user_role: str
    create_time: datetime
    update_time: datetime


class AppCreateRequest(ApiModel):
    app_name: str | None = Field(default=None, max_length=256)
    init_prompt: str = Field(min_length=1, max_length=10000)
    code_gen_type: str | None = None


class AppUpdateRequest(ApiModel):
    id: int
    app_name: str = Field(min_length=1, max_length=256)


class AppAdminUpdateRequest(ApiModel):
    id: int
    app_name: str | None = Field(default=None, max_length=256)
    cover: str | None = Field(default=None, max_length=512)
    priority: int | None = Field(default=None, ge=0, le=99)


class AppQueryRequest(ApiModel):
    page_num: int = Field(default=1, ge=1)
    page_size: int = Field(default=10, ge=1, le=20)
    sort_field: str | None = None
    sort_order: str | None = None
    id: int | None = None
    app_name: str | None = None
    init_prompt: str | None = None
    code_gen_type: str | None = None
    user_id: int | None = None
    priority: int | None = None
    is_featured: bool | None = None


class DeleteRequest(ApiModel):
    id: int


class AppDeployRequest(ApiModel):
    app_id: int = Field(gt=0)


class UserVO(ApiModel):
    id: int
    user_account: str
    user_name: str | None
    user_avatar: str | None
    user_profile: str | None
    user_role: str
    create_time: datetime


class UserQueryRequest(ApiModel):
    page_num: int = Field(default=1, ge=1)
    page_size: int = Field(default=10, ge=1, le=20)
    sort_field: str | None = None
    sort_order: str | None = None
    id: int | None = None
    user_account: str | None = None
    user_name: str | None = None
    user_profile: str | None = None
    user_role: str | None = None


class UserAdminUpdateRequest(ApiModel):
    id: int
    user_name: str | None = Field(default=None, max_length=256)
    user_avatar: str | None = Field(default=None, max_length=1024)
    user_profile: str | None = Field(default=None, max_length=512)
    user_role: str | None = None


class ChatHistoryQueryRequest(ApiModel):
    page_num: int = Field(default=1, ge=1)
    page_size: int = Field(default=10, ge=1, le=20)
    sort_field: str | None = None
    sort_order: str | None = None
    id: int | None = None
    app_id: int | None = None
    user_id: int | None = None
    message_type: str | None = None
    message: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None


class ModelCallConsumptionQueryRequest(ApiModel):
    page_num: int = Field(default=1, ge=1)
    page_size: int = Field(default=10, ge=1, le=20)
    user_id: int | None = Field(default=None, gt=0)
    app_id: int | None = Field(default=None, gt=0)


class AppVO(ApiModel):
    id: int
    app_name: str | None
    cover: str | None
    init_prompt: str | None
    code_gen_type: str | None
    priority: int
    user_id: int
    create_time: datetime
    update_time: datetime
    deploy_key: str | None
    deployed_time: datetime | None
    user: UserVO | None = None


class GenerationRequest(ApiModel):
    app_id: int
    message: str = Field(min_length=1, max_length=10000)
    request_id: str = Field(min_length=1, max_length=64)

class GenerationControlRequest(ApiModel):
    app_id: int
    turn_id: str = Field(min_length=1, max_length=64)
    action: Literal["steer", "interrupt", "redirect"]
    message: str | None = Field(default=None, max_length=10000)
    command_id: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def require_message_for_text_actions(self) -> "GenerationControlRequest":
        if self.action in {"steer", "redirect"} and not (self.message or "").strip():
            raise ValueError(f"{self.action} 必须携带 message")
        return self


class GenerationQueueRequest(ApiModel):
    app_id: int
    action: Literal["enqueue", "steer", "delete", "resume", "clear_paused"]
    command_id: str = Field(min_length=1, max_length=64)
    item_id: str | None = Field(default=None, max_length=64)
    message: str | None = Field(default=None, max_length=10000)
    clear_paused: bool = False

    @model_validator(mode="after")
    def require_fields_for_action(self) -> "GenerationQueueRequest":
        if self.action == "enqueue" and not (self.message or "").strip():
            raise ValueError("enqueue 必须携带 message")
        if self.action in {"steer", "delete"} and not self.item_id:
            raise ValueError(f"{self.action} 必须携带 itemId")
        return self
