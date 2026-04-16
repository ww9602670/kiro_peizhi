"""

 {code, message, data} 
- code=0
- BizError   code
- 422   code=1001
- 404  code=4001
- 500  code=5001
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class BizError(Exception):
    """ code + message + HTTP status_code"""

    def __init__(self, code: int, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


#  HTTP status   code  
_HTTP_CODE_MAP: dict[int, int] = {
    400: 1000,
    401: 2001,
    403: 3001,
    404: 4001,
    409: 4002,
    500: 5001,
}


def _envelope(code: int, message: str, data=None, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "data": data},
    )


def _get_field_label(err: dict) -> str:
    """从 Pydantic 验证错误中提取字段名并翻译为中文"""
    _FIELD_LABELS: dict[str, str] = {
        "username": "用户名",
        "password": "密码",
    }
    loc = err.get("loc", ())
    # loc 通常是 ("body", "field_name")
    field = loc[-1] if loc else ""
    if isinstance(field, str):
        return _FIELD_LABELS.get(field, field)
    return str(field)


def _translate_validation_msg(err: dict) -> str:
    """将 Pydantic 验证错误翻译为中文提示"""
    field = _get_field_label(err)
    err_type = err.get("type", "")
    ctx = err.get("ctx", {})

    if err_type == "string_too_short":
        min_len = ctx.get("min_length", "")
        return f"{field}至少需要{min_len}个字符"
    if err_type == "string_too_long":
        max_len = ctx.get("max_length", "")
        return f"{field}不能超过{max_len}个字符"
    if err_type == "missing":
        return f"请输入{field}"
    if err_type == "value_error":
        return f"{field}格式不正确"

    # 兜底：返回字段名 + 原始英文消息
    msg = err.get("msg", "参数校验失败")
    return f"{field}: {msg}" if field else msg


def register_exception_handlers(app: FastAPI) -> None:
    """ FastAPI app"""

    @app.exception_handler(BizError)
    async def biz_error_handler(_request: Request, exc: BizError) -> JSONResponse:
        return _envelope(exc.code, exc.message, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # 
        errors = exc.errors()
        detail = _translate_validation_msg(errors[0]) if errors else "请求参数校验失败"
        #  ctx 
        safe_errors = []
        for err in errors:
            safe_err = {k: v for k, v in err.items() if k != "ctx"}
            if "ctx" in err and isinstance(err["ctx"], dict):
                safe_err["ctx"] = {
                    k: str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v
                    for k, v in err["ctx"].items()
                }
            safe_errors.append(safe_err)
        return _envelope(1001, detail, data=safe_errors, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = _HTTP_CODE_MAP.get(exc.status_code, 5001)
        message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return _envelope(code, message, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def generic_exception_handler(
        _request: Request, exc: Exception
    ) -> JSONResponse:
        import traceback
        import os

        detail = None
        if os.environ.get("BOCAI_ENV") != "production":
            detail = traceback.format_exc()
        return _envelope(5001, f"{type(exc).__name__}", data=detail, status_code=500)
