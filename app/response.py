from enum import IntEnum
from typing import Any


class ErrorCode(IntEnum):
    SUCCESS = 0
    PARAMS_ERROR = 40000
    NOT_LOGIN = 40100
    NO_AUTH = 40101
    NOT_FOUND = 40400
    SYSTEM_ERROR = 50000


def envelope(code: ErrorCode, data: Any = None, message: str = "ok") -> dict[str, Any]:
    return {"code": int(code), "data": data, "message": message}


def success(data: Any = None, message: str = "ok") -> dict[str, Any]:
    return envelope(ErrorCode.SUCCESS, data, message)
