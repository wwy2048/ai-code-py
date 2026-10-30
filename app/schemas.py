from datetime import datetime

from pydantic import BaseModel, ConfigDict
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
