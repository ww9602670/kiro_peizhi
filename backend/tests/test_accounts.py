"""Task 3.1.5   API 


- CRUD ////
-  max_accounts 
- 2+****<2  ****
- 
- 
"""
import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import accounts as accounts_api
from app.engine.adapters.base import BalanceInfo, InstallInfo, LoginResult
from app.main import app
from app.schemas.account import mask_password
from app.utils.auth import create_token, register_session, persist_jti
from app.database import get_shared_db


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _platform_url(platform_type: str = "JND28WEB") -> str:
    return f"https://{platform_type.lower()}.example.com"


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


@pytest.fixture(autouse=True)
def mock_platform_adapter(monkeypatch):
    class FakeAdapter:
        def __init__(self, platform_type="JND28WEB", platform_url=None):
            self.platform_type = platform_type
            self.platform_url = platform_url

        async def login(self, account_name, password, captcha_code=None):
            return LoginResult(success=True, token="platform-token")

        async def query_balance(self):
            return BalanceInfo(balance=123.45)

        async def get_current_install(self):
            return InstallInfo(
                issue="3403606",
                state=1,
                close_countdown_sec=30,
                pre_issue="3403605",
                pre_result="1,2,3",
                open_countdown_sec=40,
            )

        async def load_odds(self, issue):
            return {"DX1": 20530, "DS3": 19840}

        async def close(self):
            return None

    monkeypatch.setattr(
        accounts_api,
        "create_platform_adapter",
        lambda platform_type, platform_url=None: FakeAdapter(platform_type, platform_url),
    )


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
            "game_type": "JND28",
            "platform_url": _platform_url("JND28WEB"),
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["account_name"] == f"player_{uid}"
    assert data["password_masked"] == "te****"
    assert data["game_type"] == "JND28"
    assert data["allowed_strategy_platform_types"] == []
    assert data["platform_capabilities"] == []
    assert data["latest_verification_run_id"] is None
    assert data["effective_verification_run_id"] is None
    assert data["verification_stale"] is False
    assert data["summary_status_reason"] == "not_verified"
    assert data["status"] == "inactive"
    assert data["balance"] == 0.0
    assert data["kill_switch"] is False


@pytest.mark.asyncio
async def test_bind_luckysb_account_uses_factory(client):
    """LUCKYSB account payload should validate and persist as LUCKYSB."""
    uid = _uid()
    token, _ = await _create_operator(f"luckysb_{uid}", max_accounts=3)
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/accounts",
        headers=headers,
        json={
            "account_name": f"luckysb_{uid}",
            "password": "testpass123",
            "game_type": "LUCKYSB",
            "platform_url": "https://member.example.com",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["game_type"] == "LUCKYSB"
    assert body["data"]["allowed_strategy_platform_types"] == []
    assert body["data"]["summary_status_reason"] == "not_verified"
    assert body["data"]["platform_url"] == "https://member.example.com"


@pytest.mark.asyncio
async def test_bind_luckysb_account_requires_platform_url(client):
    """LUCKYSB accounts must provide the member-site URL."""
    uid = _uid()
    token, _ = await _create_operator(f"luckysb_url_{uid}", max_accounts=3)
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/accounts",
        headers=headers,
        json={
            "account_name": f"luckysb_url_{uid}",
            "password": "testpass123",
            "game_type": "LUCKYSB",
        },
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_bind_jnd_account_requires_platform_url(client):
    """JND accounts must provide the platform URL."""
    uid = _uid()
    token, _ = await _create_operator(f"jnd_url_{uid}", max_accounts=3)
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/accounts",
        headers=headers,
        json={
            "account_name": f"jnd_url_{uid}",
            "password": "testpass123",
            "game_type": "JND28",
        },
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_bind_account_duplicate(client):
    """ 409"""
    uid = _uid()
    token, _ = await _create_operator(f"dupbind_{uid}", max_accounts=3)
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "account_name": f"dup_{uid}",
        "password": "testpass",
        "game_type": "JND28",
        "platform_url": _platform_url("JND28WEB"),
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
        json={
            "account_name": f"acc1_{uid}",
            "password": "pass1",
            "game_type": "JND28",
            "platform_url": _platform_url("JND28WEB"),
        },
    )
    assert resp1.json()["code"] == 0

    # max_accounts=1
    resp2 = await client.post(
        "/api/v1/accounts",
        headers=headers,
        json={
            "account_name": f"acc2_{uid}",
            "password": "pass2",
            "game_type": "JND28",
            "platform_url": _platform_url("JND282"),
        },
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
        json={"account_name": "", "password": "", "game_type": "INVALID"},
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
            json={
                "account_name": f"list_{uid}_{i}",
                "password": "pass123",
                "game_type": "JND28",
                "platform_url": _platform_url("JND28WEB"),
            },
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
        json={
            "account_name": f"unbind_{uid}",
            "password": "pass123",
            "game_type": "JND28",
            "platform_url": _platform_url("JND28WEB"),
        },
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
async def test_account_verify(client):
    """Verify endpoint should return effective verification capabilities."""
    uid = _uid()
    token, _ = await _create_operator(f"loginop_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/accounts",
        headers=headers,
        json={
            "account_name": f"login_{uid}",
            "password": "pass123",
            "game_type": "JND28",
            "platform_url": _platform_url("JND282"),
        },
    )
    account_id = create_resp.json()["data"]["id"]

    resp = await client.post(f"/api/v1/accounts/{account_id}/verify", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["status"] == "online"
    assert body["data"]["last_login_at"] is not None
    assert body["data"]["latest_verification_run_id"] is not None
    assert body["data"]["effective_verification_run_id"] is not None
    assert body["data"]["latest_verification_run_id"] == body["data"]["effective_verification_run_id"]
    assert sorted(body["data"]["allowed_strategy_platform_types"]) == ["JND282", "JND28WEB"]
    assert len(body["data"]["platform_capabilities"]) == 2
    assert body["data"]["verification_stale"] is False
    assert body["data"]["summary_status_reason"] is None


@pytest.mark.asyncio
async def test_bind_account_does_not_require_platform_login(client, monkeypatch):
    """Bind should persist static account info even when remote login would fail."""
    uid = _uid()
    token, _ = await _create_operator(f"bindfail_{uid}", max_accounts=3)
    headers = {"Authorization": f"Bearer {token}"}

    monkeypatch.setattr(
        accounts_api,
        "_login_platform_account",
        AsyncMock(return_value=LoginResult(success=False, message="bad credentials")),
    )

    resp = await client.post(
        "/api/v1/accounts",
        headers=headers,
        json={
            "account_name": f"bad_{uid}",
            "password": "badpass123",
            "game_type": "JND28",
            "platform_url": "https://merchant.example.com",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["allowed_strategy_platform_types"] == []


@pytest.mark.asyncio
async def test_login_platform_account_retries_with_captcha():
    """Manual and bind flows should fall back to OCR captcha login."""
    adapter = AsyncMock()
    adapter.login = AsyncMock(
        side_effect=[
            LoginResult(success=False, message="missing token cookie"),
            LoginResult(success=True, token="captcha-token"),
        ]
    )
    adapter.get_captcha = AsyncMock(return_value=b"captcha-image")

    captcha_service = AsyncMock()
    captcha_service.recognize = AsyncMock(return_value="1234")
    captcha_service.shutdown = AsyncMock()

    result = await accounts_api._login_platform_account(
        adapter,
        "demo",
        "secret",
        captcha_service=captcha_service,
    )

    assert result.success is True
    assert result.token == "captcha-token"
    assert adapter.get_captcha.await_count == 1
    assert adapter.login.await_args_list[1].kwargs["captcha_code"] == "1234"


@pytest.mark.asyncio
async def test_verify_account_nonexistent(client):
    """ 404"""
    uid = _uid()
    token, _ = await _create_operator(f"loginne_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post("/api/v1/accounts/99999/verify", headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_login_alias_reuses_verify_semantics(client):
    uid = _uid()
    token, _ = await _create_operator(f"aliasop_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/accounts",
        headers=headers,
        json={
            "account_name": f"alias_{uid}",
            "password": "pass123",
            "game_type": "JND28",
            "platform_url": _platform_url("JND28WEB"),
        },
    )
    account_id = create_resp.json()["data"]["id"]

    resp = await client.post(
        f"/api/v1/accounts/{account_id}/login?platform_type=JND282",
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["latest_verification_run_id"] == body["effective_verification_run_id"]
    assert sorted(body["allowed_strategy_platform_types"]) == ["JND282", "JND28WEB"]


@pytest.mark.asyncio
async def test_verify_single_login_multi_platform_probe(client, monkeypatch):
    uid = _uid()
    token, _ = await _create_operator(f"singlelogin_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/accounts",
        headers=headers,
        json={
            "account_name": f"single_{uid}",
            "password": "pass123",
            "game_type": "JND28",
            "platform_url": _platform_url("JND28WEB"),
        },
    )
    account_id = create_resp.json()["data"]["id"]

    class CountingAdapter:
        def __init__(self):
            self.lottery_type = "JND28WEB"
            self.login_calls = 0
            self.install_calls = 0

        async def login(self, account_name, password, captcha_code=None):
            self.login_calls += 1
            return LoginResult(success=True, token="counting-token")

        async def query_balance(self):
            return BalanceInfo(balance=88.0)

        async def get_current_install(self):
            self.install_calls += 1
            return InstallInfo(
                issue=f"issue-{self.lottery_type}",
                state=1,
                close_countdown_sec=10,
                pre_issue="prev",
                pre_result="1,2,3",
                open_countdown_sec=12,
            )

        async def load_odds(self, issue):
            return {"DX1": 20100}

        async def close(self):
            return None

    adapter = CountingAdapter()
    monkeypatch.setattr(
        accounts_api,
        "create_platform_adapter",
        lambda platform_type, platform_url=None: adapter,
    )

    resp = await client.post(f"/api/v1/accounts/{account_id}/verify", headers=headers)
    assert resp.status_code == 200
    assert adapter.login_calls == 1
    assert adapter.install_calls == 2


@pytest.mark.asyncio
async def test_latest_run_failure_does_not_override_effective(client, monkeypatch):
    uid = _uid()
    token, _ = await _create_operator(f"latestop_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/accounts",
        headers=headers,
        json={
            "account_name": f"latest_{uid}",
            "password": "pass123",
            "game_type": "JND28",
            "platform_url": _platform_url("JND28WEB"),
        },
    )
    account_id = create_resp.json()["data"]["id"]

    first_verify = await client.post(f"/api/v1/accounts/{account_id}/verify", headers=headers)
    assert first_verify.status_code == 200
    effective_run_id = first_verify.json()["data"]["effective_verification_run_id"]
    assert effective_run_id is not None

    monkeypatch.setattr(
        accounts_api,
        "_login_platform_account",
        AsyncMock(return_value=LoginResult(success=False, message="invalid credentials")),
    )
    second_verify = await client.post(f"/api/v1/accounts/{account_id}/verify", headers=headers)
    assert second_verify.status_code == 400

    list_resp = await client.get("/api/v1/accounts", headers=headers)
    account = list_resp.json()["data"][0]
    assert account["effective_verification_run_id"] == effective_run_id
    assert account["latest_verification_run_id"] != effective_run_id
    assert account["latest_verification_run_id"] > effective_run_id
    assert sorted(account["allowed_strategy_platform_types"]) == ["JND282", "JND28WEB"]


@pytest.mark.asyncio
async def test_verification_stale_when_ttl_expired(client):
    uid = _uid()
    token, _ = await _create_operator(f"staleop_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/accounts",
        headers=headers,
        json={
            "account_name": f"stale_{uid}",
            "password": "pass123",
            "game_type": "JND28",
            "platform_url": _platform_url("JND28WEB"),
        },
    )
    account_id = create_resp.json()["data"]["id"]

    verify_resp = await client.post(f"/api/v1/accounts/{account_id}/verify", headers=headers)
    assert verify_resp.status_code == 200
    run_id = verify_resp.json()["data"]["effective_verification_run_id"]
    assert run_id is not None

    db = await get_shared_db()
    await db.execute(
        "UPDATE account_verification_runs SET started_at='2000-01-01 00:00:00', stale=0, stale_reason=NULL WHERE id=?",
        (run_id,),
    )
    await db.commit()

    list_resp = await client.get("/api/v1/accounts", headers=headers)
    assert list_resp.status_code == 200
    account = list_resp.json()["data"][0]
    assert account["verification_stale"] is True
    assert account["effective_verification_run_id"] is None
    assert account["allowed_strategy_platform_types"] == []
    assert account["summary_status_reason"] == "not_verified"


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
        json={
            "account_name": f"ks_{uid}",
            "password": "pass123",
            "game_type": "JND28",
            "platform_url": _platform_url("JND28WEB"),
        },
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
        json={
            "account_name": f"ksd_{uid}",
            "password": "pass123",
            "game_type": "JND28",
            "platform_url": _platform_url("JND28WEB"),
        },
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
        json={
            "account_name": f"iso_a_{uid}",
            "password": "pass123",
            "game_type": "JND28",
            "platform_url": _platform_url("JND28WEB"),
        },
    )

    # B 
    await client.post(
        "/api/v1/accounts",
        headers=headers_b,
        json={
            "account_name": f"iso_b_{uid}",
            "password": "pass456",
            "game_type": "JND28",
            "platform_url": _platform_url("JND282"),
        },
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
        json={
            "account_name": f"isodel_{uid}",
            "password": "pass123",
            "game_type": "JND28",
            "platform_url": _platform_url("JND28WEB"),
        },
    )
    b_account_id = create_resp.json()["data"]["id"]

    # A  B   404
    resp = await client.delete(f"/api/v1/accounts/{b_account_id}", headers=headers_a)
    assert resp.status_code == 404

    # B 
    resp_b = await client.get("/api/v1/accounts", headers=headers_b)
    assert len(resp_b.json()["data"]) == 1


@pytest.mark.asyncio
async def test_data_isolation_verify(client):
    """ A  B """
    uid = _uid()
    token_a, _ = await _create_operator(f"isologin_a_{uid}", max_accounts=3)
    token_b, _ = await _create_operator(f"isologin_b_{uid}", max_accounts=3)
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    create_resp = await client.post(
        "/api/v1/accounts",
        headers=headers_b,
        json={
            "account_name": f"isologin_{uid}",
            "password": "pass123",
            "game_type": "JND28",
            "platform_url": _platform_url("JND28WEB"),
        },
    )
    b_account_id = create_resp.json()["data"]["id"]

    # A  B   404
    resp = await client.post(f"/api/v1/accounts/{b_account_id}/verify", headers=headers_a)
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
        json={
            "account_name": f"isoks_{uid}",
            "password": "pass123",
            "game_type": "JND28",
            "platform_url": _platform_url("JND28WEB"),
        },
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
