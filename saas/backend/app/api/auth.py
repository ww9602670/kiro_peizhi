"""

POST /auth/login    
POST /auth/refresh   Token
POST /auth/logout   
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import jwt
from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_current_operator, get_db_conn
from app.models.db_ops import (
    audit_log_create,
    operator_get_by_username,
    operator_update,
)
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.common import ApiResponse
from app.utils.auth import (
    check_refresh_window,
    create_token,
    decode_token,
    persist_jti,
    register_session,
    revoke_session,
    validate_jti_with_db,
)
from app.utils.response import BizError

router = APIRouter()


def _get_client_ip(request: Request) -> str:
    """ IP"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/auth/login")
async def login(
    body: LoginRequest,
    request: Request,
    db=Depends(get_db_conn),
):
    """"""
    ip = _get_client_ip(request)

    # 1. 
    operator = await operator_get_by_username(db, username=body.username)
    if operator is None:
        await _log_login_failure(db, ip, body.username, "")
        raise BizError(2002, "", status_code=401)

    # 2. 
    if operator["password"] != body.password:
        await _log_login_failure(db, ip, body.username, "", operator["id"])
        raise BizError(2002, "", status_code=401)

    # 3. Task 2.1.6
    if operator["expire_date"]:
        try:
            expire_d = date.fromisoformat(operator["expire_date"])
            if expire_d < date.today():
                await operator_update(db, operator_id=operator["id"], status="expired")
                await _log_login_failure(db, ip, body.username, "", operator["id"])
                raise BizError(2002, "", status_code=401)
        except ValueError:
            pass  # 

    # 4. 
    status = operator["status"]
    if status == "expired":
        await _log_login_failure(db, ip, body.username, "", operator["id"])
        raise BizError(2002, "", status_code=401)
    if status == "disabled":
        await _log_login_failure(db, ip, body.username, "", operator["id"])
        raise BizError(2002, "", status_code=401)

    # 5.  token + 
    token, jti, expire_at = create_token(operator["id"], operator["role"])
    register_session(operator["id"], jti)
    await persist_jti(db, operator["id"], jti)

    # 6. 
    await audit_log_create(
        db,
        operator_id=operator["id"],
        action="login",
        target_type="operator",
        target_id=operator["id"],
        detail=json.dumps({"ip": ip}),
        ip_address=ip,
    )

    expire_at_str = expire_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return ApiResponse[TokenResponse](
        data=TokenResponse(token=token, expire_at=expire_at_str)
    )


async def _log_login_failure(
    db, ip: str, username: str, reason: str, operator_id: int | None = None
):
    """"""
    await audit_log_create(
        db,
        operator_id=operator_id,
        action="login_fail",
        target_type="operator",
        detail=json.dumps({"ip": ip, "username": username, "reason": reason}),
        ip_address=ip,
    )


@router.post("/auth/refresh")
async def refresh(
    request: Request,
    db=Depends(get_db_conn),
):
    """ Token

    
    - expire_at - 30min  now  expire_at  
    - now < expire_at - 30min  2003
    - now > expire_at  2001
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise BizError(2002, " Authorization header", status_code=401)

    token = auth_header[7:]

    # 
    try:
        payload = jwt.decode(
            token,
            key=__import__("app.utils.auth", fromlist=["SECRET_KEY"]).SECRET_KEY,
            algorithms=["HS256"],
            options={"verify_exp": False},
        )
    except jwt.InvalidTokenError:
        raise BizError(2002, "Token ", status_code=401)

    operator_id = payload.get("sub")
    old_jti = payload.get("jti")
    if not operator_id or not old_jti:
        raise BizError(2002, "Token ", status_code=401)

    operator_id = int(operator_id)

    # jti  token
    if not await validate_jti_with_db(db, operator_id, old_jti):
        raise BizError(2002, "", status_code=401)

    # 
    window_check = check_refresh_window(payload)
    if window_check == "2003":
        raise BizError(2003, "Token ", status_code=400)
    if window_check == "2001":
        raise BizError(2001, "Token ", status_code=401)

    # 
    cursor = await db.execute("SELECT * FROM operators WHERE id=?", (operator_id,))
    row = await cursor.fetchone()
    if row is None:
        raise BizError(2002, "", status_code=401)
    operator = dict(row)
    if operator["status"] in ("disabled", "expired"):
        raise BizError(2002, f"{operator['status']}", status_code=401)

    #  token jti token 
    new_token, new_jti, new_expire_at = create_token(operator_id, operator["role"])
    register_session(operator_id, new_jti)
    await persist_jti(db, operator_id, new_jti)

    expire_at_str = new_expire_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return ApiResponse[TokenResponse](
        data=TokenResponse(token=new_token, expire_at=expire_at_str)
    )


@router.post("/auth/logout")
async def logout(
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """"""
    operator_id = operator["id"]
    revoke_session(operator_id)
    await persist_jti(db, operator_id, None)

    await audit_log_create(
        db,
        operator_id=operator_id,
        action="logout",
        target_type="operator",
        target_id=operator_id,
    )

    return ApiResponse(data=None)
