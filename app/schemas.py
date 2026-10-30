from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
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
