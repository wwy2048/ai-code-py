import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.response import ErrorCode, envelope

logger = logging.getLogger(__name__)


class BusinessError(Exception):
    """可安全返回给前端的业务异常。"""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def error_response(code: ErrorCode, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content=envelope(code, message=message),
    )


async def handle_business_error(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, BusinessError):
        raise exc
    return error_response(exc.code, exc.message)


async def handle_validation_error(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise exc
    return error_response(ErrorCode.PARAMS_ERROR, "请求参数错误")


async def handle_http_error(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        raise exc
    mapping = {
        401: (ErrorCode.NOT_LOGIN, "未登录"),
        403: (ErrorCode.NO_AUTH, "无权限"),
        404: (ErrorCode.NOT_FOUND, "请求数据不存在"),
        405: (ErrorCode.PARAMS_ERROR, "请求方法不允许"),
    }
    code, message = mapping.get(exc.status_code, (ErrorCode.PARAMS_ERROR, "请求参数错误"))
    return error_response(code, message)


async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("未处理的服务端异常", exc_info=exc)
    return error_response(ErrorCode.SYSTEM_ERROR, "系统内部异常")
