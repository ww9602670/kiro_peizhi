"""FastAPI 

get_current_operator   token + jti + 
require_admin         
get_db_conn           
"""
from __future__ import annotations

from typing import Any

import jwt
from fastapi import Depends, Request

from app.database import get_shared_db
from app.utils.auth import decode_token, validate_jti_with_db
from app.utils.response import BizError


async def get_db_conn():
    """ app """
    return await get_shared_db()


async def get_current_operator(
    request: Request,
    db=Depends(get_db_conn),
) -> dict[str, Any]:
    """ Authorization header  token

    decode  jti   
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise BizError(2002, " Authorization header", status_code=401)

    token = auth_header[7:]

    # 1.  token
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise BizError(2001, "Token ", status_code=401)
    except jwt.InvalidTokenError:
        raise BizError(2002, "Token ", status_code=401)

    operator_id = payload.get("sub")
    jti = payload.get("jti")
    if not operator_id or not jti:
        raise BizError(2002, "Token ", status_code=401)

    operator_id = int(operator_id)

    # 2. jti  + DB
    if not await validate_jti_with_db(db, operator_id, jti):
        raise BizError(2002, "", status_code=401)

    # 3.  + 
    cursor = await db.execute(
        "SELECT * FROM operators WHERE id=?", (operator_id,)
    )
    row = await cursor.fetchone()
    if row is None:
        raise BizError(2002, "", status_code=401)

    operator = dict(row)

    if operator["status"] == "disabled":
        raise BizError(2002, "", status_code=401)
    if operator["status"] == "expired":
        raise BizError(2002, "", status_code=401)

    #  payload 
    operator["_payload"] = payload
    return operator


async def require_admin(
    operator: dict[str, Any] = Depends(get_current_operator),
) -> dict[str, Any]:
    """"""
    if operator.get("role") != "admin":
        raise BizError(3001, "", status_code=403)
    return operator
