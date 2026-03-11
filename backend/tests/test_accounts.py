"""Task 3.1.5   API 


- CRUD ////
-  max_accounts 
- 2+****<2  ****
- 
- 
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.schemas.account import mask_password
from app.utils.auth import create_token, register_session, persist_jti
from app.database import get_shared_db


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _get_admin_token() -> str:
    db = await get_shared_db()
    token, jti, _ = create_token(1, "admin")
    register_session(1, jti)
    await persist_jti(db, 1, jti)
    return token


async def _create_operator(username: str, max_accounts: int = 1) -> tuple[str, int]:
    """ admin API  (token, operator_id)"""
    db = await get_shared_db()
    from app.models.db_ops import operator_create
    op = await operator_create(
        db, username=username, password="pass123456",
        max_accounts=max_accounts, created_by=1,
    )
    token, jti, _ = create_token(op["id"], "operator")
    register_session(op["id"], jti)
    await persist_jti(db, op["id"], jti)
    return token, op["id"]


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# 
# 1. 
# 

def test_mask_password_normal():
    """>=2 2+****"""
    assert mask_password("mypassword") == "my****"
    assert mask_password("ab") == "ab****"
    assert mask_password("abc") == "ab****"


def test_mask_password_short():
    """<2  ****"""
    assert mask_password("a") == "****"
    assert mask_password("") == "****"


# 
# 2. 
# 

@pytest.mark.asyncio
async def test_bind_account(client):
    """"""
    uid = _uid()
    token, _ = await _create_operator(f"bindop_{uid}", max_accounts=3)
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/accounts",
        headers=headers,
        json={
            "account_name": f"player_{uid}",
            "password": "testpass123",
            "platform_type": "JND28WEB",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["account_name"] == f"player_{uid}"
    assert data["password_masked"] == "te****"
    assert data["platform_type"] == "JND28WEB"
    assert data["status"] == "inactive"
    assert data["balance"] == 0.0
    assert data["kill_switch"] is False


@pytest.mark.asyncio
async def test_bind_account_duplicate(client):
    """ 409"""
    uid = _uid()
    token, _ = await _create_operator(f"dupbind_{uid}", max_accounts=3)
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "account_name": f"dup_{uid}",
        "password": "testpass",
        "platform_type": "JND28WEB",
    }

    await client.post("/api/v1/accounts", headers=headers, json=payload)
    resp = await client.post("/api/v1/accounts", headers=headers, json=payload)
    assert resp.status_code == 409
    assert resp.json()["code"] == 4002
    assert "" in resp.json()["message"]


@pytest.mark.asyncio
async def test_bind_account_max_limit(client):
    """ max_accounts  409"""
    uid = _uid()
    token, _ = await _create_operator(f"maxop_{uid}", max_accounts=1)
    headers = {"Authorization": f"Bearer {token}"}

    # 
    resp1 = await client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"account_name": f"acc1_{uid}", "password": "pass1", "platform_type": "JND28WEB"},
    )
    assert resp1.json()["code"] == 0

    # max_accounts=1
    resp2 = await client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"account_name": f"acc2_{uid}", "password": "pass2", "platform_type": "JND282"},
    )
    assert resp2.status_code == 409
    assert resp2.json()["code"] == 4002
    assert "" in resp2.json()["message"]


@pytest.mark.asyncio
async def test_bind_account_validation_error(client):
    """ 422 """
    uid = _uid()
    token, _ = await _create_operator(f"valop_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"account_name": "", "password": "", "platform_type": "INVALID"},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == 1001


# 
# 3. 
# 

@pytest.mark.asyncio
async def test_list_accounts(client):
    """"""
    uid = _uid()
    token, _ = await _create_operator(f"listop_{uid}", max_accounts=3)
    headers = {"Authorization": f"Bearer {token}"}

    # 
    for i in range(2):
        await client.post(
            "/api/v1/accounts",
            headers=headers,
            json={"account_name": f"list_{uid}_{i}", "password": "pass123", "platform_type": "JND28WEB"},
        )

    resp = await client.get("/api/v1/accounts", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert len(body["data"]) == 2
    # 
    for item in body["data"]:
        assert item["password_masked"] == "pa****"


@pytest.mark.asyncio
async def test_list_accounts_empty(client):
    """"""
    uid = _uid()
    token, _ = await _create_operator(f"emptyop_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.get("/api/v1/accounts", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["data"] == []


# 
# 4. 
# 

@pytest.mark.asyncio
async def test_unbind_account(client):
    """"""
    uid = _uid()
    token, _ = await _create_operator(f"unbindop_{uid}", max_accounts=3)
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"account_name": f"unbind_{uid}", "password": "pass123", "platform_type": "JND28WEB"},
    )
    account_id = create_resp.json()["data"]["id"]

    resp = await client.delete(f"/api/v1/accounts/{account_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["code"] == 0

    # 
    list_resp = await client.get("/api/v1/accounts", headers=headers)
    assert len(list_resp.json()["data"]) == 0


@pytest.mark.asyncio
async def test_unbind_nonexistent_account(client):
    """ 404"""
    uid = _uid()
    token, _ = await _create_operator(f"unbindne_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.delete("/api/v1/accounts/99999", headers=headers)
    assert resp.status_code == 404
    assert resp.json()["code"] == 4001


# 
# 5. 
# 

@pytest.mark.asyncio
async def test_manual_login(client):
    """ online"""
    uid = _uid()
    token, _ = await _create_operator(f"loginop_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"account_name": f"login_{uid}", "password": "pass123", "platform_type": "JND282"},
    )
    account_id = create_resp.json()["data"]["id"]

    resp = await client.post(f"/api/v1/accounts/{account_id}/login", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["status"] == "online"
    assert body["data"]["last_login_at"] is not None


@pytest.mark.asyncio
async def test_manual_login_nonexistent(client):
    """ 404"""
    uid = _uid()
    token, _ = await _create_operator(f"loginne_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post("/api/v1/accounts/99999/login", headers=headers)
    assert resp.status_code == 404


# 
# 6. 
# 

@pytest.mark.asyncio
async def test_kill_switch_enable(client):
    """"""
    uid = _uid()
    token, _ = await _create_operator(f"ksop_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"account_name": f"ks_{uid}", "password": "pass123", "platform_type": "JND28WEB"},
    )
    account_id = create_resp.json()["data"]["id"]

    resp = await client.post(
        f"/api/v1/accounts/{account_id}/kill-switch",
        headers=headers,
        json={"enabled": True},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["kill_switch"] is True


@pytest.mark.asyncio
async def test_kill_switch_disable(client):
    """"""
    uid = _uid()
    token, _ = await _create_operator(f"ksdis_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"account_name": f"ksd_{uid}", "password": "pass123", "platform_type": "JND28WEB"},
    )
    account_id = create_resp.json()["data"]["id"]

    # 
    await client.post(
        f"/api/v1/accounts/{account_id}/kill-switch",
        headers=headers,
        json={"enabled": True},
    )
    # 
    resp = await client.post(
        f"/api/v1/accounts/{account_id}/kill-switch",
        headers=headers,
        json={"enabled": False},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["kill_switch"] is False


@pytest.mark.asyncio
async def test_kill_switch_nonexistent(client):
    """ 404"""
    uid = _uid()
    token, _ = await _create_operator(f"ksne_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/accounts/99999/kill-switch",
        headers=headers,
        json={"enabled": True},
    )
    assert resp.status_code == 404


# 
# 7. 
# 

@pytest.mark.asyncio
async def test_data_isolation_list(client):
    """ A  B """
    uid = _uid()
    token_a, _ = await _create_operator(f"isoa_{uid}", max_accounts=3)
    token_b, _ = await _create_operator(f"isob_{uid}", max_accounts=3)
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # A 
    await client.post(
        "/api/v1/accounts",
        headers=headers_a,
        json={"account_name": f"iso_a_{uid}", "password": "pass123", "platform_type": "JND28WEB"},
    )

    # B 
    await client.post(
        "/api/v1/accounts",
        headers=headers_b,
        json={"account_name": f"iso_b_{uid}", "password": "pass456", "platform_type": "JND282"},
    )

    # A 
    resp_a = await client.get("/api/v1/accounts", headers=headers_a)
    assert len(resp_a.json()["data"]) == 1
    assert resp_a.json()["data"][0]["account_name"] == f"iso_a_{uid}"

    # B 
    resp_b = await client.get("/api/v1/accounts", headers=headers_b)
    assert len(resp_b.json()["data"]) == 1
    assert resp_b.json()["data"][0]["account_name"] == f"iso_b_{uid}"


@pytest.mark.asyncio
async def test_data_isolation_delete(client):
    """ A  B """
    uid = _uid()
    token_a, _ = await _create_operator(f"isodel_a_{uid}", max_accounts=3)
    token_b, _ = await _create_operator(f"isodel_b_{uid}", max_accounts=3)
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # B 
    create_resp = await client.post(
        "/api/v1/accounts",
        headers=headers_b,
        json={"account_name": f"isodel_{uid}", "password": "pass123", "platform_type": "JND28WEB"},
    )
    b_account_id = create_resp.json()["data"]["id"]

    # A  B   404
    resp = await client.delete(f"/api/v1/accounts/{b_account_id}", headers=headers_a)
    assert resp.status_code == 404

    # B 
    resp_b = await client.get("/api/v1/accounts", headers=headers_b)
    assert len(resp_b.json()["data"]) == 1


@pytest.mark.asyncio
async def test_data_isolation_login(client):
    """ A  B """
    uid = _uid()
    token_a, _ = await _create_operator(f"isologin_a_{uid}", max_accounts=3)
    token_b, _ = await _create_operator(f"isologin_b_{uid}", max_accounts=3)
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    create_resp = await client.post(
        "/api/v1/accounts",
        headers=headers_b,
        json={"account_name": f"isologin_{uid}", "password": "pass123", "platform_type": "JND28WEB"},
    )
    b_account_id = create_resp.json()["data"]["id"]

    # A  B   404
    resp = await client.post(f"/api/v1/accounts/{b_account_id}/login", headers=headers_a)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_data_isolation_kill_switch(client):
    """ A  B """
    uid = _uid()
    token_a, _ = await _create_operator(f"isoks_a_{uid}", max_accounts=3)
    token_b, _ = await _create_operator(f"isoks_b_{uid}", max_accounts=3)
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    create_resp = await client.post(
        "/api/v1/accounts",
        headers=headers_b,
        json={"account_name": f"isoks_{uid}", "password": "pass123", "platform_type": "JND28WEB"},
    )
    b_account_id = create_resp.json()["data"]["id"]

    # A  B   404
    resp = await client.post(
        f"/api/v1/accounts/{b_account_id}/kill-switch",
        headers=headers_a,
        json={"enabled": True},
    )
    assert resp.status_code == 404


# 
# 8. 
# 

@pytest.mark.asyncio
async def test_no_auth_returns_401(client):
    """ 401"""
    resp = await client.get("/api/v1/accounts")
    assert resp.status_code == 401
    assert resp.json()["code"] == 2002
