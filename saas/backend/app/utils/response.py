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
        detail = errors[0].get("msg", "") if errors else ""
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
        return _envelope(1001, f"{detail}", data=safe_errors, status_code=422)

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
