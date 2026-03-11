"""Task 2.2.4   API 


- CRUD ///
-  403
- 
- 
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.utils.auth import create_token, register_session, persist_jti
from app.database import get_shared_db


def _uid() -> str:
    """"""
    return uuid.uuid4().hex[:8]


async def _get_admin_token() -> str:
    """ tokenadmin/admin123id=1"""
    db = await get_shared_db()
    token, jti, _ = create_token(1, "admin")
    register_session(1, jti)
    await persist_jti(db, 1, jti)
    return token


async def _create_operator_via_db(username: str) -> tuple[str, int]:
    """ DB  (token, operator_id)"""
    db = await get_shared_db()
    from app.models.db_ops import operator_create
    op = await operator_create(db, username=username, password="pass123456", created_by=1)
    token, jti, _ = create_token(op["id"], "operator")
    register_session(op["id"], jti)
    await persist_jti(db, op["id"], jti)
    return token, op["id"]


@pytest.fixture
async def client():
    """ app lifespan  AsyncClient"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def admin_headers():
    """ headers"""
    token = await _get_admin_token()
    return {"Authorization": f"Bearer {token}"}


# 
# 1. 
# 

@pytest.mark.asyncio
async def test_list_operators(client, admin_headers):
    """"""
    resp = await client.get("/api/v1/admin/operators", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert "page_size" in data
    assert data["total"] >= 1


@pytest.mark.asyncio
async def test_list_operators_pagination(client, admin_headers):
    """"""
    uid = _uid()
    for i in range(3):
        await client.post(
            "/api/v1/admin/operators",
            headers=admin_headers,
            json={"username": f"pgop_{uid}_{i}", "password": "pass123456"},
        )

    resp = await client.get(
        "/api/v1/admin/operators?page=1&page_size=2",
        headers=admin_headers,
    )
    body = resp.json()
    assert body["code"] == 0
    assert len(body["data"]["items"]) <= 2
    assert body["data"]["page"] == 1
    assert body["data"]["page_size"] == 2


# 
# 2. 
# 

@pytest.mark.asyncio
async def test_create_operator(client, admin_headers):
    """"""
    uid = _uid()
    resp = await client.post(
        "/api/v1/admin/operators",
        headers=admin_headers,
        json={
            "username": f"newop_{uid}",
            "password": "pass123456",
            "max_accounts": 3,
            "expire_date": "2026-12-31",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    op = body["data"]
    assert op["username"] == f"newop_{uid}"
    assert op["role"] == "operator"
    assert op["status"] == "active"
    assert op["max_accounts"] == 3
    assert op["expire_date"] == "2026-12-31"


@pytest.mark.asyncio
async def test_create_operator_defaults(client, admin_headers):
    """"""
    uid = _uid()
    resp = await client.post(
        "/api/v1/admin/operators",
        headers=admin_headers,
        json={"username": f"defop_{uid}", "password": "pass123456"},
    )
    body = resp.json()
    assert body["code"] == 0
    op = body["data"]
    assert op["max_accounts"] == 1
    assert op["expire_date"] is None


@pytest.mark.asyncio
async def test_create_operator_duplicate_username(client, admin_headers):
    """ 409"""
    uid = _uid()
    payload = {"username": f"dupuser_{uid}", "password": "pass123456"}
    await client.post("/api/v1/admin/operators", headers=admin_headers, json=payload)
    resp = await client.post("/api/v1/admin/operators", headers=admin_headers, json=payload)
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == 4002


@pytest.mark.asyncio
async def test_create_operator_validation_error(client, admin_headers):
    """ 422 """
    resp = await client.post(
        "/api/v1/admin/operators",
        headers=admin_headers,
        json={"username": "ab", "password": "123"},  # too short
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == 1001


# 
# 3. 
# 

@pytest.mark.asyncio
async def test_update_operator(client, admin_headers):
    """"""
    uid = _uid()
    create_resp = await client.post(
        "/api/v1/admin/operators",
        headers=admin_headers,
        json={"username": f"updtgt_{uid}", "password": "pass123456"},
    )
    op_id = create_resp.json()["data"]["id"]

    resp = await client.put(
        f"/api/v1/admin/operators/{op_id}",
        headers=admin_headers,
        json={"max_accounts": 10, "expire_date": "2027-06-30"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["max_accounts"] == 10
    assert body["data"]["expire_date"] == "2027-06-30"


@pytest.mark.asyncio
async def test_update_nonexistent_operator(client, admin_headers):
    """ 404"""
    resp = await client.put(
        "/api/v1/admin/operators/99999",
        headers=admin_headers,
        json={"max_accounts": 5},
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == 4001


# 
# 4. /
# 

@pytest.mark.asyncio
async def test_disable_operator(client, admin_headers):
    """"""
    uid = _uid()
    create_resp = await client.post(
        "/api/v1/admin/operators",
        headers=admin_headers,
        json={"username": f"distgt_{uid}", "password": "pass123456"},
    )
    op_id = create_resp.json()["data"]["id"]

    resp = await client.put(
        f"/api/v1/admin/operators/{op_id}/status",
        headers=admin_headers,
        json={"status": "disabled"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["status"] == "disabled"


@pytest.mark.asyncio
async def test_enable_operator(client, admin_headers):
    """"""
    uid = _uid()
    create_resp = await client.post(
        "/api/v1/admin/operators",
        headers=admin_headers,
        json={"username": f"entgt_{uid}", "password": "pass123456"},
    )
    op_id = create_resp.json()["data"]["id"]

    await client.put(
        f"/api/v1/admin/operators/{op_id}/status",
        headers=admin_headers,
        json={"status": "disabled"},
    )
    resp = await client.put(
        f"/api/v1/admin/operators/{op_id}/status",
        headers=admin_headers,
        json={"status": "active"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "active"


@pytest.mark.asyncio
async def test_cannot_disable_self(client, admin_headers):
    """"""
    resp = await client.put(
        "/api/v1/admin/operators/1/status",
        headers=admin_headers,
        json={"status": "disabled"},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == 4002


@pytest.mark.asyncio
async def test_invalid_status_value(client, admin_headers):
    """ 422"""
    uid = _uid()
    create_resp = await client.post(
        "/api/v1/admin/operators",
        headers=admin_headers,
        json={"username": f"invst_{uid}", "password": "pass123456"},
    )
    op_id = create_resp.json()["data"]["id"]

    resp = await client.put(
        f"/api/v1/admin/operators/{op_id}/status",
        headers=admin_headers,
        json={"status": "invalid_value"},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == 1001


# 
# 5. 
# 

@pytest.mark.asyncio
async def test_non_admin_cannot_list(client):
    """ 403"""
    uid = _uid()
    token, _ = await _create_operator_via_db(f"permlist_{uid}")
    headers = {"Authorization": f"Bearer {token}"}
    resp = await client.get("/api/v1/admin/operators", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["code"] == 3001


@pytest.mark.asyncio
async def test_non_admin_cannot_create(client):
    """"""
    uid = _uid()
    token, _ = await _create_operator_via_db(f"permcrt_{uid}")
    headers = {"Authorization": f"Bearer {token}"}
    resp = await client.post(
        "/api/v1/admin/operators",
        headers=headers,
        json={"username": f"shouldfail_{uid}", "password": "pass123456"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == 3001


@pytest.mark.asyncio
async def test_no_auth_returns_401(client):
    """ 401"""
    resp = await client.get("/api/v1/admin/operators")
    assert resp.status_code == 401
    assert resp.json()["code"] == 2002


# 
# 6. 
# 

@pytest.mark.asyncio
async def test_create_operator_audit_log(client, admin_headers):
    """"""
    db = await get_shared_db()
    uid = _uid()

    resp = await client.post(
        "/api/v1/admin/operators",
        headers=admin_headers,
        json={"username": f"auditcrt_{uid}", "password": "pass123456"},
    )
    assert resp.json()["code"] == 0

    cursor = await db.execute(
        "SELECT * FROM audit_logs WHERE action='create_operator' ORDER BY id DESC LIMIT 1"
    )
    log = dict(await cursor.fetchone())
    assert log["operator_id"] == 1  # admin
    assert log["target_type"] == "operator"
    assert log["action"] == "create_operator"


@pytest.mark.asyncio
async def test_update_operator_audit_log(client, admin_headers):
    """"""
    db = await get_shared_db()
    uid = _uid()

    create_resp = await client.post(
        "/api/v1/admin/operators",
        headers=admin_headers,
        json={"username": f"auditupd_{uid}", "password": "pass123456"},
    )
    op_id = create_resp.json()["data"]["id"]

    await client.put(
        f"/api/v1/admin/operators/{op_id}",
        headers=admin_headers,
        json={"max_accounts": 5},
    )

    cursor = await db.execute(
        "SELECT * FROM audit_logs WHERE action='update_operator' ORDER BY id DESC LIMIT 1"
    )
    log = dict(await cursor.fetchone())
    assert log["target_id"] == op_id


@pytest.mark.asyncio
async def test_disable_operator_audit_log(client, admin_headers):
    """"""
    db = await get_shared_db()
    uid = _uid()

    create_resp = await client.post(
        "/api/v1/admin/operators",
        headers=admin_headers,
        json={"username": f"auditdis_{uid}", "password": "pass123456"},
    )
    op_id = create_resp.json()["data"]["id"]

    await client.put(
        f"/api/v1/admin/operators/{op_id}/status",
        headers=admin_headers,
        json={"status": "disabled"},
    )

    cursor = await db.execute(
        "SELECT * FROM audit_logs WHERE action='disable_operator' ORDER BY id DESC LIMIT 1"
    )
    log = dict(await cursor.fetchone())
    assert log["target_id"] == op_id


@pytest.mark.asyncio
async def test_enable_operator_audit_log(client, admin_headers):
    """ enable_operator """
    db = await get_shared_db()
    uid = _uid()

    create_resp = await client.post(
        "/api/v1/admin/operators",
        headers=admin_headers,
        json={"username": f"auditen_{uid}", "password": "pass123456"},
    )
    op_id = create_resp.json()["data"]["id"]

    await client.put(
        f"/api/v1/admin/operators/{op_id}/status",
        headers=admin_headers,
        json={"status": "disabled"},
    )
    await client.put(
        f"/api/v1/admin/operators/{op_id}/status",
        headers=admin_headers,
        json={"status": "active"},
    )

    cursor = await db.execute(
        "SELECT * FROM audit_logs WHERE action='enable_operator' ORDER BY id DESC LIMIT 1"
    )
    log = dict(await cursor.fetchone())
    assert log["target_id"] == op_id
