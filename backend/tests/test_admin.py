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
async def test_list_shared_market_uncovered_urls(client, admin_headers):
    """待审核共享网址可列出（默认 pending）"""
    db = await get_shared_db()
    from app.models.db_ops import shared_market_uncovered_url_touch

    uid = _uid()
    url = f"https://shared.example.com/{uid}"
    row = await shared_market_uncovered_url_touch(
        db,
        normalized_url=url,
        sample_raw_url=f"{url}?a=1",
        platform_type="JND28WEB",
        failure_reason="检测失败",
        seen_at="2026-04-30 10:00:00",
    )
    assert row["id"] is not None

    resp = await client.get(
        "/api/v1/admin/shared-market-uncovered-urls?status=pending",
        headers=admin_headers,
    )
    body = resp.json()
    assert body["code"] == 0
    items = body["data"]["items"]
    assert any(item["normalized_url"] == url for item in items)


@pytest.mark.asyncio
async def test_list_shared_market_groups(client, admin_headers):
    """可拉取可用共享组"""
    db = await get_shared_db()
    await db.execute(
        """INSERT INTO shared_market_groups
           (group_key, enabled, collector_platform_type, collector_account_name,
            collector_password_enc, freshness_threshold_sec, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "test-list-group",
            1,
            "JND28WEB",
            "collector",
            "abc",
            30,
            "2026-04-30 10:00:00",
            "2026-04-30 10:00:00",
        ),
    )
    await db.commit()

    resp = await client.get(
        "/api/v1/admin/shared-market-groups",
        headers=admin_headers,
    )
    body = resp.json()
    assert body["code"] == 0
    assert any(group["group_key"] == "test-list-group" for group in body["data"])


@pytest.mark.asyncio
async def test_admin_shared_market_uncovered_ignore_recheck(client, admin_headers):
    """可忽略并重置为待重检"""
    db = await get_shared_db()
    from app.models.db_ops import shared_market_uncovered_url_touch

    row = await shared_market_uncovered_url_touch(
        db,
        normalized_url="https://shared.example.com/ignore",
        sample_raw_url="https://shared.example.com/ignore?a=1",
        platform_type="JND28WEB",
        seen_at="2026-04-30 10:00:00",
    )

    ignore_resp = await client.post(
        f"/api/v1/admin/shared-market-uncovered-urls/{row['id']}/ignore",
        headers=admin_headers,
    )
    assert ignore_resp.status_code == 200
    ignore_body = ignore_resp.json()
    assert ignore_body["code"] == 0
    assert ignore_body["data"]["status"] == "ignored"

    recheck_resp = await client.post(
        f"/api/v1/admin/shared-market-uncovered-urls/{row['id']}/recheck",
        headers=admin_headers,
    )
    assert recheck_resp.status_code == 200
    recheck_body = recheck_resp.json()
    assert recheck_body["code"] == 0
    assert recheck_body["data"]["status"] == "pending"


@pytest.mark.asyncio
async def test_admin_join_shared_market_uncovered_to_group(client, admin_headers):
    """可将待审核网址加入共享组"""
    db = await get_shared_db()
    from app.models.db_ops import shared_market_uncovered_url_touch

    row = await shared_market_uncovered_url_touch(
        db,
        normalized_url="https://shared.example.com/join",
        sample_raw_url="https://shared.example.com/join?a=1",
        platform_type="JND28WEB",
        seen_at="2026-04-30 10:00:00",
    )

    cursor = await db.execute(
        """INSERT INTO shared_market_groups
           (group_key, enabled, collector_platform_type, collector_account_name,
            collector_password_enc, freshness_threshold_sec, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "test-group",
            1,
            "JND28WEB",
            "collector",
            "abc",
            30,
            "2026-04-30 10:00:00",
            "2026-04-30 10:00:00",
        ),
    )
    group_id = int(cursor.lastrowid)
    await db.commit()

    join_resp = await client.post(
        f"/api/v1/admin/shared-market-uncovered-urls/{row['id']}/join-shared-group",
        headers=admin_headers,
        json={"shared_group_id": group_id},
    )
    assert join_resp.status_code == 200
    join_body = join_resp.json()
    assert join_body["code"] == 0
    assert join_body["data"]["shared_group_id"] == group_id


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
async def test_admin_can_set_and_list_operator_strategy_permissions(client, admin_headers):
    uid = _uid()
    token, op_id = await _create_operator_via_db(f"permop_{uid}")
    operator_headers = {"Authorization": f"Bearer {token}"}
    db = await get_shared_db()
    from app.models.db_ops import account_create

    account = await account_create(
        db,
        operator_id=op_id,
        account_name=f"permacc_{uid}",
        password="pw1234",
        game_type="JND28",
        platform_url="https://jnd.example.com",
    )

    list_resp = await client.get(
        f"/api/v1/admin/operators/{op_id}/strategy-permissions",
        headers=admin_headers,
    )
    assert list_resp.json()["code"] == 0
    assert list_resp.json()["data"]["operator_id"] == op_id
    assert list_resp.json()["data"]["allowed_strategy_types"] == []

    update_resp = await client.put(
        f"/api/v1/admin/operators/{op_id}/strategy-permissions",
        headers=admin_headers,
        json={"strategy_types": ["flat", "martin", "flat"]},
    )
    assert update_resp.json()["code"] == 0
    assert update_resp.json()["data"]["allowed_strategy_types"] == ["flat", "martin"]

    second_account = await account_create(
        db,
        operator_id=op_id,
        account_name=f"permacc2_{uid}",
        password="pw1234",
        game_type="JND28",
        platform_url="https://jnd2.example.com",
    )

    account_resp = await client.get("/api/v1/accounts", headers=operator_headers)
    assert account_resp.json()["code"] == 0
    account_row = next(row for row in account_resp.json()["data"] if row["id"] == account["id"])
    assert account_row["allowed_strategy_types"] == ["flat", "martin"]
    second_row = next(row for row in account_resp.json()["data"] if row["id"] == second_account["id"])
    assert second_row["allowed_strategy_types"] == ["flat", "martin"]


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
