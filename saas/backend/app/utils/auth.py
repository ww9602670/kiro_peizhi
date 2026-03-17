"""JWT 

- JWT  jti/ 
- ACTIVE_SESSIONS  + DB  current_jti
-  DB
-  ACTIVE_SESSIONS + DB current_jti
-  DB  ACTIVE_SESSIONS
- Token  24h
- expire_at - 30min  now  expire_at
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

#   
SECRET_KEY = os.environ.get("BOCAI_JWT_SECRET", "bocai-dev-secret-key-change-in-prod")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24
REFRESH_WINDOW_MINUTES = 30

#   
# operator_id  active jti
ACTIVE_SESSIONS: dict[int, str] = {}


def create_token(operator_id: int, role: str) -> tuple[str, str, datetime]:
    """ JWT Token

    Returns:
        (token_str, jti, expire_at)
    """
    jti = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    expire_at = now + timedelta(hours=TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": str(operator_id),
        "role": role,
        "jti": jti,
        "iat": now,
        "exp": expire_at,
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    return token, jti, expire_at


def decode_token(token: str) -> dict[str, Any]:
    """ JWT Token

    Raises:
        jwt.ExpiredSignatureError: token 
        jwt.InvalidTokenError: token 
    """
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


def register_session(operator_id: int, jti: str) -> None:
    """"""
    ACTIVE_SESSIONS[operator_id] = jti


def revoke_session(operator_id: int) -> None:
    """"""
    ACTIVE_SESSIONS.pop(operator_id, None)


def validate_jti(operator_id: int, jti: str) -> bool:
    """ jti """
    return ACTIVE_SESSIONS.get(operator_id) == jti


async def persist_jti(db, operator_id: int, jti: str | None) -> None:
    """ current_jti  operators """
    await db.execute(
        "UPDATE operators SET current_jti=?, updated_at=? WHERE id=?",
        (jti, datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"), operator_id),
    )
    await db.commit()


async def restore_sessions(db) -> None:
    """ DB  ACTIVE_SESSIONS """
    ACTIVE_SESSIONS.clear()
    cursor = await db.execute(
        "SELECT id, current_jti FROM operators WHERE current_jti IS NOT NULL"
    )
    rows = await cursor.fetchall()
    for row in rows:
        ACTIVE_SESSIONS[row["id"]] = row["current_jti"]


async def validate_jti_with_db(db, operator_id: int, jti: str) -> bool:
    """ DB

    Returns:
        True if jti matches active session.
    """
    # 1. 
    if validate_jti(operator_id, jti):
        return True
    # 2. DB 
    cursor = await db.execute(
        "SELECT current_jti FROM operators WHERE id=?", (operator_id,)
    )
    row = await cursor.fetchone()
    if row and row["current_jti"] == jti:
        # 
        ACTIVE_SESSIONS[operator_id] = jti
        return True
    return False


def check_refresh_window(payload: dict[str, Any]) -> str | None:
    """ token 

    Returns:
        None  
        "2003"  
        "2001"  decode 
    """
    now = datetime.now(timezone.utc)
    exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    window_start = exp - timedelta(minutes=REFRESH_WINDOW_MINUTES)

    if now < window_start:
        return "2003"  # 
    if now > exp:
        return "2001"  # 
    return None  # 
