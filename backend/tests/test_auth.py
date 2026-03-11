"""Task 2.1.8  


- JWT /
- //
-  token  401
- /
- 
- 
-  30min 30min  2003
-  token  jti  DB
-  jti 
"""
import json
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.utils.auth import (
    ACTIVE_SESSIONS,
    create_token,
    decode_token,
    register_session,
    validate_jti,
    check_refresh_window,
)


#  helpers 

def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _login(client: AsyncClient, username="admin", password="admin123") -> tuple[dict, int]:
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    return resp.json(), resp.status_code


async def _create_operator(username="op1", password="pass123456", status="active", expire_date=None):
    """ DB  API"""
    import uuid
    from app.database import get_shared_db
    db = await get_shared_db()
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    #  INSERT OR IGNORE  UNIQUE  DB 
    cursor = await db.execute(
        """INSERT OR IGNORE INTO operators (username, password, role, status, expire_date, created_at, updated_at)
           VALUES (?, ?, 'operator', ?, ?, ?, ?)""",
        (username, password, status, expire_date, now, now),
    )
    await db.commit()
    if cursor.lastrowid == 0 or cursor.rowcount == 0:
        # Already exists, fetch the id
        row = await (await db.execute("SELECT id FROM operators WHERE username=?", (username,))).fetchone()
        # Update status/expire_date to match desired state
        await db.execute(
            "UPDATE operators SET status=?, expire_date=?, password=? WHERE username=?",
            (status, expire_date, password, username),
        )
        await db.commit()
        return row["id"]
    return cursor.lastrowid


# 
# JWT /
# 

class TestJWT:
    def test_create_token_returns_tuple(self):
        token, jti, expire_at = create_token(1, "operator")
        assert isinstance(token, str)
        assert isinstance(jti, str)
        assert len(jti) == 32  # uuid4 hex
        assert expire_at > datetime.now(timezone.utc)

    def test_decode_token_roundtrip(self):
        token, jti, _ = create_token(42, "admin")
        payload = decode_token(token)
        assert payload["sub"] == "42"  # sub is string
        assert payload["role"] == "admin"
        assert payload["jti"] == jti

    def test_decode_expired_token_raises(self):
        import jwt as pyjwt
        from app.utils.auth import SECRET_KEY, ALGORITHM

        payload = {
            "sub": "1",
            "role": "operator",
            "jti": "test",
            "iat": datetime.now(timezone.utc) - timedelta(hours=25),
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        }
        token = pyjwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
        with pytest.raises(pyjwt.ExpiredSignatureError):
            decode_token(token)

    def test_decode_invalid_token_raises(self):
        import jwt as pyjwt
        with pytest.raises(pyjwt.InvalidTokenError):
            decode_token("not.a.valid.token")


# 
# 
# 

class TestSessionControl:
    def test_register_and_validate(self):
        ACTIVE_SESSIONS.clear()
        register_session(1, "jti-aaa")
        assert validate_jti(1, "jti-aaa") is True
        assert validate_jti(1, "jti-bbb") is False

    def test_new_login_kicks_old(self):
        ACTIVE_SESSIONS.clear()
        register_session(1, "jti-old")
        register_session(1, "jti-new")
        assert validate_jti(1, "jti-old") is False
        assert validate_jti(1, "jti-new") is True


# 
# 
# 

class TestRefreshWindow:
    def test_within_window(self):
        """ 15   """
        exp = datetime.now(timezone.utc) + timedelta(minutes=15)
        payload = {"exp": exp.timestamp()}
        assert check_refresh_window(payload) is None

    def test_too_early(self):
        """ 2    2003"""
        exp = datetime.now(timezone.utc) + timedelta(hours=2)
        payload = {"exp": exp.timestamp()}
        assert check_refresh_window(payload) == "2003"

    def test_exactly_at_window_start(self):
        """  """
        exp = datetime.now(timezone.utc) + timedelta(minutes=30)
        payload = {"exp": exp.timestamp()}
        assert check_refresh_window(payload) is None

    def test_expired(self):
        """  2001"""
        exp = datetime.now(timezone.utc) - timedelta(minutes=1)
        payload = {"exp": exp.timestamp()}
        assert check_refresh_window(payload) == "2001"


# 
# API 
# 

@pytest.fixture
async def client():
    """httpx AsyncClient for FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestLoginEndpoint:
    async def test_login_success(self, client):
        body, status = await _login(client)
        assert status == 200
        assert body["code"] == 0
        assert "token" in body["data"]
        assert "expire_at" in body["data"]
        assert body["data"]["expire_at"].endswith("Z")

    async def test_login_wrong_password(self, client):
        body, status = await _login(client, password="wrongpass")
        assert status == 401
        assert body["code"] == 2002

    async def test_login_nonexistent_user(self, client):
        body, status = await _login(client, username="nobody", password="wrongpass")
        assert status == 401
        assert body["code"] == 2002

    async def test_login_disabled_account(self, client):
        """"""
        await _create_operator("disabled_op", "pass123456", status="disabled")
        body, status = await _login(client, "disabled_op", "pass123456")
        assert status == 401
        assert body["code"] == 2002
        assert "" in body["message"]

    async def test_login_expired_account(self, client):
        """"""
        await _create_operator("expired_op", "pass123456", status="expired")
        body, status = await _login(client, "expired_op", "pass123456")
        assert status == 401
        assert body["code"] == 2002
        assert "" in body["message"]

    async def test_login_auto_expire_by_date(self, client):
        """expire_date   status  expired"""
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        op_id = await _create_operator("expiring_op", "pass123456", expire_date=yesterday)

        body, status = await _login(client, "expiring_op", "pass123456")
        assert status == 401
        assert body["code"] == 2002
        assert "" in body["message"]

        #  DB  status 
        from app.database import get_shared_db
        db = await get_shared_db()
        cursor = await db.execute("SELECT status FROM operators WHERE id=?", (op_id,))
        row = await cursor.fetchone()
        assert row["status"] == "expired"

    async def test_login_failure_audit_log(self, client):
        """"""
        await _login(client, "nobody", "wrongpass")

        from app.database import get_shared_db
        db = await get_shared_db()
        cursor = await db.execute(
            "SELECT * FROM audit_logs WHERE action='login_fail' ORDER BY id DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        assert row is not None
        detail = json.loads(row["detail"])
        assert detail["username"] == "nobody"
        assert detail["reason"] == ""


class TestLogoutEndpoint:
    async def test_logout_success(self, client):
        body, _ = await _login(client)
        token = body["data"]["token"]

        resp = await client.post("/api/v1/auth/logout", headers=_auth_header(token))
        assert resp.status_code == 200
        assert resp.json()["code"] == 0

        #  token 
        resp2 = await client.post("/api/v1/auth/logout", headers=_auth_header(token))
        assert resp2.status_code == 401

    async def test_logout_without_token(self, client):
        resp = await client.post("/api/v1/auth/logout")
        assert resp.status_code == 401
        assert resp.json()["code"] == 2002


class TestSingleSessionKickout:
    async def test_new_login_invalidates_old_token(self, client):
        """ token  401"""
        body1, _ = await _login(client)
        old_token = body1["data"]["token"]

        body2, _ = await _login(client)
        new_token = body2["data"]["token"]

        #  token 
        resp_old = await client.post("/api/v1/auth/logout", headers=_auth_header(old_token))
        assert resp_old.status_code == 401
        assert resp_old.json()["code"] == 2002

        #  token 
        resp_new = await client.post("/api/v1/auth/logout", headers=_auth_header(new_token))
        assert resp_new.status_code == 200


class TestRefreshEndpoint:
    async def test_refresh_within_window(self, client):
        """ 30min """
        body, _ = await _login(client)
        token = body["data"]["token"]

        # Mock check_refresh_window to simulate being in the window
        with patch("app.api.auth.check_refresh_window", return_value=None):
            resp = await client.post("/api/v1/auth/refresh", headers=_auth_header(token))
            assert resp.status_code == 200
            data = resp.json()
            assert data["code"] == 0
            assert "token" in data["data"]
            new_token = data["data"]["token"]
            assert new_token != token

    async def test_refresh_too_early(self, client):
        """ 30min  2003"""
        body, _ = await _login(client)
        token = body["data"]["token"]

        resp = await client.post("/api/v1/auth/refresh", headers=_auth_header(token))
        #  token  24h 30min  2003
        assert resp.status_code == 400
        assert resp.json()["code"] == 2003

    async def test_refresh_invalidates_old_token(self, client):
        """ token """
        body, _ = await _login(client)
        old_token = body["data"]["token"]

        with patch("app.api.auth.check_refresh_window", return_value=None):
            resp = await client.post("/api/v1/auth/refresh", headers=_auth_header(old_token))
            new_token = resp.json()["data"]["token"]

        #  token 
        resp_old = await client.post("/api/v1/auth/logout", headers=_auth_header(old_token))
        assert resp_old.status_code == 401

        #  token 
        resp_new = await client.post("/api/v1/auth/logout", headers=_auth_header(new_token))
        assert resp_new.status_code == 200

    async def test_refresh_updates_db_jti(self, client):
        """ jti  DB"""
        body, _ = await _login(client)
        old_token = body["data"]["token"]
        old_payload = decode_token(old_token)

        with patch("app.api.auth.check_refresh_window", return_value=None):
            resp = await client.post("/api/v1/auth/refresh", headers=_auth_header(old_token))
            new_token = resp.json()["data"]["token"]
            new_payload = decode_token(new_token)

        from app.database import get_shared_db
        db = await get_shared_db()
        cursor = await db.execute("SELECT current_jti FROM operators WHERE id=?", (int(old_payload["sub"]),))
        row = await cursor.fetchone()
        assert row["current_jti"] == new_payload["jti"]
        assert row["current_jti"] != old_payload["jti"]

    async def test_refresh_without_token(self, client):
        resp = await client.post("/api/v1/auth/refresh")
        assert resp.status_code == 401


class TestProcessRestart:
    async def test_restart_recovers_sessions_from_db(self, client):
        """ DB  token jti  DB"""
        body, _ = await _login(client)
        token = body["data"]["token"]
        payload = decode_token(token)
        op_id = int(payload["sub"])

        # 
        ACTIVE_SESSIONS.clear()
        assert validate_jti(op_id, payload["jti"]) is False

        #  DB 
        from app.database import get_shared_db
        from app.utils.auth import restore_sessions
        db = await get_shared_db()
        await restore_sessions(db)

        #  token 
        assert validate_jti(op_id, payload["jti"]) is True

    async def test_restart_old_jti_invalid_after_new_login(self, client):
        """ ACTIVE_SESSIONS  DB  token  401"""
        body1, _ = await _login(client)
        old_token = body1["data"]["token"]

        body2, _ = await _login(client)
        new_token = body2["data"]["token"]

        # 
        ACTIVE_SESSIONS.clear()
        from app.database import get_shared_db
        from app.utils.auth import restore_sessions
        db = await get_shared_db()
        await restore_sessions(db)

        #  token DB  current_jti 
        resp_old = await client.post("/api/v1/auth/logout", headers=_auth_header(old_token))
        assert resp_old.status_code == 401

        #  token 
        resp_new = await client.post("/api/v1/auth/logout", headers=_auth_header(new_token))
        assert resp_new.status_code == 200
