"""Task 8.2.3   API 


- /POST /admin/kill-switch
- /POST /accounts/{id}/kill-switch
- RiskController 
- RiskController 
- 
- 
- 
"""
from __future__ import annotations

import uuid

import pytest
import aiosqlite
from httpx import ASGITransport, AsyncClient

from app.database import DDL_STATEMENTS, INSERT_DEFAULT_ADMIN, get_shared_db
from app.engine.alert import AlertService
from app.engine.kill_switch import get_global_kill, set_global_kill
from app.engine.risk import RiskController
from app.engine.strategy_runner import BetSignal
from app.main import app
from app.models.db_ops import (
    account_create,
    account_update,
    operator_create,
    strategy_create,
    strategy_update,
)
from app.utils.auth import create_token, persist_jti, register_session


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _get_admin_token() -> str:
    db = await get_shared_db()
    token, jti, _ = create_token(1, "admin")
    register_session(1, jti)
    await persist_jti(db, 1, jti)
    return token


async def _create_operator(username: str, max_accounts: int = 3) -> tuple[str, int]:
    db = await get_shared_db()
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
        # 
        set_global_kill(False)
        yield c
        # 
        set_global_kill(False)


@pytest.fixture
async def admin_headers():
    token = await _get_admin_token()
    return {"Authorization": f"Bearer {token}"}


# 
# 1.  APIPOST /admin/kill-switch
# 

class TestGlobalKillSwitch:
    @pytest.mark.asyncio
    async def test_enable_global_kill(self, client, admin_headers):
        """"""
        resp = await client.post(
            "/api/v1/admin/kill-switch",
            headers=admin_headers,
            json={"enabled": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["enabled"] is True
        # 
        assert get_global_kill() is True

    @pytest.mark.asyncio
    async def test_disable_global_kill(self, client, admin_headers):
        """"""
        # 
        await client.post(
            "/api/v1/admin/kill-switch",
            headers=admin_headers,
            json={"enabled": True},
        )
        assert get_global_kill() is True

        # 
        resp = await client.post(
            "/api/v1/admin/kill-switch",
            headers=admin_headers,
            json={"enabled": False},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["enabled"] is False
        assert get_global_kill() is False

    @pytest.mark.asyncio
    async def test_global_kill_idempotent(self, client, admin_headers):
        """"""
        for _ in range(3):
            resp = await client.post(
                "/api/v1/admin/kill-switch",
                headers=admin_headers,
                json={"enabled": True},
            )
            assert resp.status_code == 200
            assert resp.json()["data"]["enabled"] is True
        assert get_global_kill() is True

    @pytest.mark.asyncio
    async def test_global_kill_requires_admin(self, client):
        """"""
        uid = _uid()
        token, _ = await _create_operator(f"nonadmin_{uid}")
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.post(
            "/api/v1/admin/kill-switch",
            headers=headers,
            json={"enabled": True},
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == 3001

    @pytest.mark.asyncio
    async def test_global_kill_no_auth(self, client):
        """ 401"""
        resp = await client.post(
            "/api/v1/admin/kill-switch",
            json={"enabled": True},
        )
        assert resp.status_code == 401
        assert resp.json()["code"] == 2002

    @pytest.mark.asyncio
    async def test_global_kill_audit_log(self, client, admin_headers):
        """"""
        db = await get_shared_db()

        # 
        await client.post(
            "/api/v1/admin/kill-switch",
            headers=admin_headers,
            json={"enabled": True},
        )
        row = await (await db.execute(
            "SELECT * FROM audit_logs WHERE action='global_kill_switch_on' ORDER BY id DESC LIMIT 1"
        )).fetchone()
        assert row is not None
        assert row["operator_id"] == 1
        assert row["target_type"] == "system"

        # 
        await client.post(
            "/api/v1/admin/kill-switch",
            headers=admin_headers,
            json={"enabled": False},
        )
        row = await (await db.execute(
            "SELECT * FROM audit_logs WHERE action='global_kill_switch_off' ORDER BY id DESC LIMIT 1"
        )).fetchone()
        assert row is not None

    @pytest.mark.asyncio
    async def test_global_kill_envelope_format(self, client, admin_headers):
        """"""
        resp = await client.post(
            "/api/v1/admin/kill-switch",
            headers=admin_headers,
            json={"enabled": True},
        )
        body = resp.json()
        assert "code" in body
        assert "message" in body
        assert "data" in body


# 
# 2.  APIPOST /accounts/{id}/kill-switch
# 

class TestAccountKillSwitch:
    @pytest.mark.asyncio
    async def test_enable_account_kill(self, client):
        """"""
        uid = _uid()
        token, _ = await _create_operator(f"aks_en_{uid}")
        headers = {"Authorization": f"Bearer {token}"}

        # 
        create_resp = await client.post(
            "/api/v1/accounts",
            headers=headers,
            json={"account_name": f"acc_{uid}", "password": "pw123", "platform_type": "JND28WEB"},
        )
        account_id = create_resp.json()["data"]["id"]

        # 
        resp = await client.post(
            f"/api/v1/accounts/{account_id}/kill-switch",
            headers=headers,
            json={"enabled": True},
        )
        assert resp.status_code == 200
        assert resp.json()["code"] == 0
        assert resp.json()["data"]["kill_switch"] is True

    @pytest.mark.asyncio
    async def test_disable_account_kill(self, client):
        """"""
        uid = _uid()
        token, _ = await _create_operator(f"aks_dis_{uid}")
        headers = {"Authorization": f"Bearer {token}"}

        create_resp = await client.post(
            "/api/v1/accounts",
            headers=headers,
            json={"account_name": f"acc_{uid}", "password": "pw123", "platform_type": "JND282"},
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
    async def test_account_kill_persisted_in_db(self, client):
        """"""
        uid = _uid()
        token, _ = await _create_operator(f"aks_db_{uid}")
        headers = {"Authorization": f"Bearer {token}"}

        create_resp = await client.post(
            "/api/v1/accounts",
            headers=headers,
            json={"account_name": f"acc_{uid}", "password": "pw123", "platform_type": "JND28WEB"},
        )
        account_id = create_resp.json()["data"]["id"]

        # 
        await client.post(
            f"/api/v1/accounts/{account_id}/kill-switch",
            headers=headers,
            json={"enabled": True},
        )

        #  API 
        list_resp = await client.get("/api/v1/accounts", headers=headers)
        accounts = list_resp.json()["data"]
        assert len(accounts) == 1
        assert accounts[0]["kill_switch"] is True

    @pytest.mark.asyncio
    async def test_account_kill_data_isolation(self, client):
        """ A  B """
        uid = _uid()
        token_a, _ = await _create_operator(f"aks_iso_a_{uid}")
        token_b, _ = await _create_operator(f"aks_iso_b_{uid}")
        headers_b = {"Authorization": f"Bearer {token_b}"}
        headers_a = {"Authorization": f"Bearer {token_a}"}

        # B 
        create_resp = await client.post(
            "/api/v1/accounts",
            headers=headers_b,
            json={"account_name": f"iso_{uid}", "password": "pw123", "platform_type": "JND28WEB"},
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
# 3. RiskController 
# 

class TestRiskControllerKillSwitch:
    """ RiskController /"""

    @pytest.fixture
    async def db(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys=ON")
        for stmt in DDL_STATEMENTS:
            await conn.execute(stmt)
        await conn.execute(INSERT_DEFAULT_ADMIN)
        await conn.commit()
        yield conn
        await conn.close()

    @pytest.fixture
    async def setup_data(self, db):
        op = await operator_create(
            db, username="risk_op", password="pass123",
            role="operator", status="active", created_by=1,
        )
        acct = await account_create(
            db, operator_id=op["id"], account_name="risk_acct",
            password="pw", platform_type="JND28WEB",
        )
        acct = await account_update(
            db, account_id=acct["id"], operator_id=op["id"],
            status="online", session_token="valid_token", balance=100_000,
        )
        strat = await strategy_create(
            db, operator_id=op["id"], account_id=acct["id"],
            name="", type="flat", play_code="DX1",
            base_amount=1000,
        )
        strat = await strategy_update(
            db, strategy_id=strat["id"], operator_id=op["id"], status="running",
        )
        return {"operator": op, "account": acct, "strategy": strat}

    @pytest.fixture
    async def alert_service(self, db):
        return AlertService(db)

    def _make_signal(self, strategy_id: int) -> BetSignal:
        return BetSignal(
            strategy_id=strategy_id,
            key_code="DX1",
            amount=1000,
            idempotent_id=f"20240101001-{strategy_id}-DX1",
        )

    @pytest.mark.asyncio
    async def test_global_kill_blocks_bet(self, db, setup_data, alert_service):
        """ True RiskController """
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            global_kill=True,
        )
        signal = self._make_signal(setup_data["strategy"]["id"])
        result = await risk.check(signal)
        assert result.passed is False
        assert "" in result.reason
        #  1 
        assert risk._check_log == ["kill_switch"]

    @pytest.mark.asyncio
    async def test_global_kill_false_allows_bet(self, db, setup_data, alert_service):
        """ False """
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            global_kill=False,
        )
        signal = self._make_signal(setup_data["strategy"]["id"])
        result = await risk.check(signal)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_account_kill_blocks_bet(self, db, setup_data, alert_service):
        """ kill_switch=1 RiskController """
        await account_update(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"],
            kill_switch=1,
        )
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            global_kill=False,
        )
        signal = self._make_signal(setup_data["strategy"]["id"])
        result = await risk.check(signal)
        assert result.passed is False
        assert "" in result.reason

    @pytest.mark.asyncio
    async def test_account_kill_cleared_allows_bet(self, db, setup_data, alert_service):
        """ kill_switch RiskController """
        # 
        await account_update(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"],
            kill_switch=1,
        )
        # 
        await account_update(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"],
            kill_switch=0,
        )
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            global_kill=False,
        )
        signal = self._make_signal(setup_data["strategy"]["id"])
        result = await risk.check(signal)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_both_kills_global_takes_priority(self, db, setup_data, alert_service):
        """ + """
        await account_update(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"],
            kill_switch=1,
        )
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            global_kill=True,
        )
        signal = self._make_signal(setup_data["strategy"]["id"])
        result = await risk.check(signal)
        assert result.passed is False
        assert "" in result.reason

    @pytest.mark.asyncio
    async def test_kill_switch_module_integration(self, db, setup_data, alert_service):
        """ kill_switch  get/set  RiskController """
        # 
        set_global_kill(True)
        assert get_global_kill() is True

        # RiskController 
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            global_kill=get_global_kill(),
        )
        signal = self._make_signal(setup_data["strategy"]["id"])
        result = await risk.check(signal)
        assert result.passed is False
        assert "" in result.reason

        # 
        set_global_kill(False)
        risk2 = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            global_kill=get_global_kill(),
        )
        result2 = await risk2.check(signal)
        assert result2.passed is True

        # 
        set_global_kill(False)
