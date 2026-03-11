"""Phase 9.1.6  


1. AlertService 12 /
2. send_system_alert
3. check_system_healthsystem_api_fail30%+ consecutive_fail 5 
4. API CRUDGET /alerts+PUT /alerts/{id}/readPUT /alerts/read-allGET /alerts/unread-count
5. 
6. 
"""
from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

import aiosqlite

from app.database import DDL_STATEMENTS, INSERT_DEFAULT_ADMIN, get_shared_db
from app.engine.alert import (
    ALERT_LEVEL_MAP,
    CONSECUTIVE_FAIL_THRESHOLD,
    SYSTEM_ALERT_TYPES,
    SYSTEM_API_FAIL_THRESHOLD,
    AlertService,
    _DEDUP_WINDOW,
)
from app.main import app
from app.models.db_ops import operator_create
from app.utils.auth import create_token, persist_jti, register_session


def _uid() -> str:
    return uuid.uuid4().hex[:8]


# 
# Fixtures
# 


@pytest.fixture
async def db():
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
async def operator(db):
    return await operator_create(db, username="test_op", password="pass123", created_by=1)


@pytest.fixture
def alert_service(db):
    return AlertService(db)


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _create_operator(username: str) -> tuple[str, int]:
    db = await get_shared_db()
    op = await operator_create(db, username=username, password="pass123456", created_by=1)
    token, jti, _ = create_token(op["id"], "operator")
    register_session(op["id"], jti)
    await persist_jti(db, op["id"], jti)
    return token, op["id"]


# 
# 1. AlertService 
# 


class TestAlertLevelMap:
    """ 12 """

    def test_all_12_types_present(self):
        assert len(ALERT_LEVEL_MAP) == 12

    @pytest.mark.parametrize(
        "alert_type,expected_level",
        [
            ("login_fail", "critical"),
            ("captcha_fail", "warning"),
            ("session_lost", "warning"),
            ("bet_fail", "warning"),
            ("reconcile_error", "critical"),
            ("balance_low", "warning"),
            ("stop_loss", "info"),
            ("take_profit", "info"),
            ("martin_reset", "info"),
            ("platform_limit", "warning"),
            ("system_api_fail", "critical"),
            ("consecutive_fail", "critical"),
        ],
    )
    def test_level_mapping(self, alert_type, expected_level):
        assert ALERT_LEVEL_MAP[alert_type] == expected_level

    def test_system_alert_types(self):
        assert SYSTEM_ALERT_TYPES == {"system_api_fail", "consecutive_fail"}


# 
# 2. send_system_alert
# 


class TestSendSystemAlert:
    """"""

    @pytest.mark.asyncio
    async def test_send_system_api_fail(self, db, alert_service):
        """system_api_fail  DB critical"""
        sent = await alert_service.send_system_alert(
            admin_operator_id=1,
            alert_type="system_api_fail",
            title=" API ",
            detail='{"fail_rate": 50.0}',
        )
        assert sent is True

        row = await (
            await db.execute("SELECT * FROM alerts WHERE type='system_api_fail'")
        ).fetchone()
        assert row is not None
        assert row["operator_id"] == 1
        assert row["level"] == "critical"
        assert row["title"] == " API "

    @pytest.mark.asyncio
    async def test_send_consecutive_fail(self, db, alert_service):
        """consecutive_fail  DB critical"""
        sent = await alert_service.send_system_alert(
            admin_operator_id=1,
            alert_type="consecutive_fail",
            title=" 42  5 ",
        )
        assert sent is True

        row = await (
            await db.execute("SELECT * FROM alerts WHERE type='consecutive_fail'")
        ).fetchone()
        assert row is not None
        assert row["level"] == "critical"

    @pytest.mark.asyncio
    async def test_system_alert_dedup(self, db, alert_service):
        """ 5 """
        r1 = await alert_service.send_system_alert(1, "system_api_fail", " #1")
        r2 = await alert_service.send_system_alert(1, "system_api_fail", " #2")
        assert r1 is True
        assert r2 is False

        rows = await (
            await db.execute("SELECT * FROM alerts WHERE type='system_api_fail'")
        ).fetchall()
        assert len(rows) == 1


# 
# 3. check_system_health
# 


class TestCheckSystemHealth:
    """"""

    @pytest.mark.asyncio
    async def test_system_api_fail_triggered(self, alert_service):
        """30%+  system_api_fail"""
        accounts = [{"id": i} for i in range(10)]
        # 4/10 = 40% 
        fail_counts = {0: 1, 1: 1, 2: 1, 3: 1}

        triggered = await alert_service.check_system_health(
            admin_operator_id=1,
            active_accounts=accounts,
            account_fail_counts=fail_counts,
            account_consecutive_bet_fails={},
        )
        assert "system_api_fail" in triggered

    @pytest.mark.asyncio
    async def test_system_api_fail_not_triggered_below_threshold(self, alert_service):
        """ 30% """
        accounts = [{"id": i} for i in range(10)]
        # 2/10 = 20% 
        fail_counts = {0: 1, 1: 1}

        triggered = await alert_service.check_system_health(
            admin_operator_id=1,
            active_accounts=accounts,
            account_fail_counts=fail_counts,
            account_consecutive_bet_fails={},
        )
        assert "system_api_fail" not in triggered

    @pytest.mark.asyncio
    async def test_system_api_fail_exact_threshold(self, alert_service):
        """ 30% """
        accounts = [{"id": i} for i in range(10)]
        # 3/10 = 30% 
        fail_counts = {0: 1, 1: 1, 2: 1}

        triggered = await alert_service.check_system_health(
            admin_operator_id=1,
            active_accounts=accounts,
            account_fail_counts=fail_counts,
            account_consecutive_bet_fails={},
        )
        assert "system_api_fail" in triggered

    @pytest.mark.asyncio
    async def test_system_api_fail_empty_accounts(self, alert_service):
        """"""
        triggered = await alert_service.check_system_health(
            admin_operator_id=1,
            active_accounts=[],
            account_fail_counts={},
            account_consecutive_bet_fails={},
        )
        assert "system_api_fail" not in triggered

    @pytest.mark.asyncio
    async def test_consecutive_fail_triggered(self, alert_service):
        """ 5  consecutive_fail"""
        triggered = await alert_service.check_system_health(
            admin_operator_id=1,
            active_accounts=[{"id": 1}],
            account_fail_counts={},
            account_consecutive_bet_fails={1: 5},
        )
        assert "consecutive_fail" in triggered

    @pytest.mark.asyncio
    async def test_consecutive_fail_below_threshold(self, alert_service):
        """ 4 """
        triggered = await alert_service.check_system_health(
            admin_operator_id=1,
            active_accounts=[{"id": 1}],
            account_fail_counts={},
            account_consecutive_bet_fails={1: 4},
        )
        assert "consecutive_fail" not in triggered

    @pytest.mark.asyncio
    async def test_both_alerts_triggered(self, alert_service):
        """"""
        accounts = [{"id": i} for i in range(3)]
        triggered = await alert_service.check_system_health(
            admin_operator_id=1,
            active_accounts=accounts,
            account_fail_counts={0: 1},  # 1/3 = 33%
            account_consecutive_bet_fails={2: 6},
        )
        assert "system_api_fail" in triggered
        assert "consecutive_fail" in triggered

    @pytest.mark.asyncio
    async def test_check_system_health_detail_json(self, db, alert_service):
        """system_api_fail  detail  JSON """
        accounts = [{"id": i} for i in range(5)]
        fail_counts = {0: 1, 1: 1}  # 2/5 = 40%

        await alert_service.check_system_health(
            admin_operator_id=1,
            active_accounts=accounts,
            account_fail_counts=fail_counts,
            account_consecutive_bet_fails={},
        )

        row = await (
            await db.execute("SELECT * FROM alerts WHERE type='system_api_fail'")
        ).fetchone()
        assert row is not None
        detail = json.loads(row["detail"])
        assert detail["fail_rate"] == 40.0
        assert detail["failed_accounts"] == 2
        assert detail["total_accounts"] == 5


# 
# 4.  send
# 


class TestOperatorAlertTypes:
    """ 9+1  send() """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "alert_type,title,detail",
        [
            ("login_fail", "", '{"reason": "", "retry_count": 3}'),
            ("captcha_fail", " 5 ", '{"fail_count": 5}'),
            ("session_lost", "", '{"reconnect_status": "retrying"}'),
            ("bet_fail", "", '{"issue": "202501010001", "error_code": 5}'),
            ("reconcile_error", "", '{"diff_amount": 500, "issue": "202501010001"}'),
            ("balance_low", "", '{"current_balance": 100, "required": 500}'),
            ("stop_loss", "", '{"strategy_name": "", "trigger_value": 10000}'),
            ("take_profit", "", '{"strategy_name": "", "trigger_value": 5000}'),
            ("martin_reset", "", '{"strategy_name": "", "round_loss": 3100}'),
            ("platform_limit", "", '{"play_code": "DX1", "limit": 50000}'),
        ],
    )
    async def test_operator_alert_write(self, db, operator, alert_service, alert_type, title, detail):
        sent = await alert_service.send(
            operator_id=operator["id"],
            alert_type=alert_type,
            title=title,
            detail=detail,
        )
        assert sent is True

        row = await (
            await db.execute(
                "SELECT * FROM alerts WHERE operator_id=? AND type=?",
                (operator["id"], alert_type),
            )
        ).fetchone()
        assert row is not None
        assert row["level"] == ALERT_LEVEL_MAP[alert_type]
        assert row["title"] == title
        assert row["detail"] == detail
        assert row["is_read"] == 0


# 
# 5. / Phase 4.5 
# 


class TestDedup:
    """"""

    @pytest.mark.asyncio
    async def test_dedup_after_window_expires(self, db, operator, alert_service):
        """5 """
        base_time = 1000000.0
        with patch("app.engine.alert.time.time", return_value=base_time):
            r1 = await alert_service.send(operator["id"], "bet_fail", " #1")
        assert r1 is True

        with patch("app.engine.alert.time.time", return_value=base_time + _DEDUP_WINDOW):
            r2 = await alert_service.send(operator["id"], "bet_fail", " #2")
        assert r2 is True

        rows = await (
            await db.execute("SELECT * FROM alerts WHERE operator_id=?", (operator["id"],))
        ).fetchall()
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_platform_limit_dedup_with_account(self, db, operator, alert_service):
        """platform_limit  account_id """
        r1 = await alert_service.send(operator["id"], "platform_limit", " #1", account_id=10)
        r2 = await alert_service.send(operator["id"], "platform_limit", " #2", account_id=10)
        r3 = await alert_service.send(operator["id"], "platform_limit", " #3", account_id=20)
        assert r1 is True
        assert r2 is False  #  account_id 
        assert r3 is True   #  account_id 


# 
# 6. API 
# 


class TestAlertsAPI:
    """ API CRUD """

    @pytest.mark.asyncio
    async def test_list_alerts_empty(self, client):
        uid = _uid()
        token, _ = await _create_operator(f"alert_empty_{uid}")
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.get("/api/v1/alerts", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["items"] == []
        assert body["data"]["total"] == 0

    @pytest.mark.asyncio
    async def test_list_alerts_with_data(self, client):
        uid = _uid()
        token, op_id = await _create_operator(f"alert_data_{uid}")
        headers = {"Authorization": f"Bearer {token}"}

        # 
        db = await get_shared_db()
        svc = AlertService(db)
        await svc.send(op_id, "login_fail", "")
        svc._dedup_cache.clear()
        await svc.send(op_id, "bet_fail", "")

        resp = await client.get("/api/v1/alerts", headers=headers)
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["total"] == 2
        assert len(body["data"]["items"]) == 2

    @pytest.mark.asyncio
    async def test_list_alerts_filter_unread(self, client):
        uid = _uid()
        token, op_id = await _create_operator(f"alert_filter_{uid}")
        headers = {"Authorization": f"Bearer {token}"}

        db = await get_shared_db()
        svc = AlertService(db)
        await svc.send(op_id, "login_fail", "")
        svc._dedup_cache.clear()
        await svc.send(op_id, "bet_fail", "")

        # 
        row = await (
            await db.execute("SELECT id FROM alerts WHERE operator_id=? ORDER BY id LIMIT 1", (op_id,))
        ).fetchone()
        await db.execute("UPDATE alerts SET is_read=1 WHERE id=?", (row["id"],))
        await db.commit()

        # 
        resp = await client.get("/api/v1/alerts?is_read=0", headers=headers)
        body = resp.json()
        assert body["data"]["total"] == 1

        # 
        resp = await client.get("/api/v1/alerts?is_read=1", headers=headers)
        body = resp.json()
        assert body["data"]["total"] == 1

    @pytest.mark.asyncio
    async def test_list_alerts_pagination(self, client):
        uid = _uid()
        token, op_id = await _create_operator(f"alert_page_{uid}")
        headers = {"Authorization": f"Bearer {token}"}

        db = await get_shared_db()
        svc = AlertService(db)
        for i in range(5):
            svc._dedup_cache.clear()
            await svc.send(op_id, "bet_fail", f" #{i}")

        resp = await client.get("/api/v1/alerts?page=1&page_size=2", headers=headers)
        body = resp.json()
        assert body["data"]["total"] == 5
        assert len(body["data"]["items"]) == 2
        assert body["data"]["page"] == 1
        assert body["data"]["page_size"] == 2

    @pytest.mark.asyncio
    async def test_mark_alert_read(self, client):
        uid = _uid()
        token, op_id = await _create_operator(f"alert_read_{uid}")
        headers = {"Authorization": f"Bearer {token}"}

        db = await get_shared_db()
        svc = AlertService(db)
        await svc.send(op_id, "login_fail", "")

        row = await (
            await db.execute("SELECT id FROM alerts WHERE operator_id=?", (op_id,))
        ).fetchone()
        alert_id = row["id"]

        resp = await client.put(f"/api/v1/alerts/{alert_id}/read", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["code"] == 0

        # 
        updated = await (await db.execute("SELECT is_read FROM alerts WHERE id=?", (alert_id,))).fetchone()
        assert updated["is_read"] == 1

    @pytest.mark.asyncio
    async def test_mark_nonexistent_alert_read(self, client):
        uid = _uid()
        token, _ = await _create_operator(f"alert_ne_{uid}")
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.put("/api/v1/alerts/99999/read", headers=headers)
        assert resp.status_code == 404
        assert resp.json()["code"] == 4001

    @pytest.mark.asyncio
    async def test_mark_all_read(self, client):
        uid = _uid()
        token, op_id = await _create_operator(f"alert_all_{uid}")
        headers = {"Authorization": f"Bearer {token}"}

        db = await get_shared_db()
        svc = AlertService(db)
        for i in range(3):
            svc._dedup_cache.clear()
            await svc.send(op_id, "bet_fail", f" #{i}")

        resp = await client.put("/api/v1/alerts/read-all", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["marked_count"] == 3

        # 
        count_row = await (
            await db.execute("SELECT COUNT(*) as cnt FROM alerts WHERE operator_id=? AND is_read=0", (op_id,))
        ).fetchone()
        assert count_row["cnt"] == 0

    @pytest.mark.asyncio
    async def test_unread_count(self, client):
        uid = _uid()
        token, op_id = await _create_operator(f"alert_cnt_{uid}")
        headers = {"Authorization": f"Bearer {token}"}

        db = await get_shared_db()
        svc = AlertService(db)
        for i in range(4):
            svc._dedup_cache.clear()
            await svc.send(op_id, "bet_fail", f" #{i}")

        resp = await client.get("/api/v1/alerts/unread-count", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["count"] == 4

    @pytest.mark.asyncio
    async def test_unread_count_after_read(self, client):
        uid = _uid()
        token, op_id = await _create_operator(f"alert_cnt2_{uid}")
        headers = {"Authorization": f"Bearer {token}"}

        db = await get_shared_db()
        svc = AlertService(db)
        await svc.send(op_id, "login_fail", "")
        svc._dedup_cache.clear()
        await svc.send(op_id, "bet_fail", "")

        # 
        await client.put("/api/v1/alerts/read-all", headers=headers)

        resp = await client.get("/api/v1/alerts/unread-count", headers=headers)
        assert resp.json()["data"]["count"] == 0

    @pytest.mark.asyncio
    async def test_no_auth_returns_401(self, client):
        resp = await client.get("/api/v1/alerts")
        assert resp.status_code == 401
        assert resp.json()["code"] == 2002


# 
# 7. 
# 


class TestDataIsolation:
    """"""

    @pytest.mark.asyncio
    async def test_list_isolation(self, client):
        """ A  B """
        uid = _uid()
        token_a, op_a = await _create_operator(f"iso_a_{uid}")
        token_b, op_b = await _create_operator(f"iso_b_{uid}")

        db = await get_shared_db()
        svc = AlertService(db)
        await svc.send(op_a, "login_fail", "A ")
        svc._dedup_cache.clear()
        await svc.send(op_b, "bet_fail", "B ")

        resp_a = await client.get("/api/v1/alerts", headers={"Authorization": f"Bearer {token_a}"})
        assert resp_a.json()["data"]["total"] == 1
        assert resp_a.json()["data"]["items"][0]["title"] == "A "

        resp_b = await client.get("/api/v1/alerts", headers={"Authorization": f"Bearer {token_b}"})
        assert resp_b.json()["data"]["total"] == 1
        assert resp_b.json()["data"]["items"][0]["title"] == "B "

    @pytest.mark.asyncio
    async def test_mark_read_isolation(self, client):
        """ A  B """
        uid = _uid()
        token_a, _ = await _create_operator(f"iso_rd_a_{uid}")
        token_b, op_b = await _create_operator(f"iso_rd_b_{uid}")

        db = await get_shared_db()
        svc = AlertService(db)
        await svc.send(op_b, "login_fail", "B ")

        row = await (
            await db.execute("SELECT id FROM alerts WHERE operator_id=?", (op_b,))
        ).fetchone()
        b_alert_id = row["id"]

        # A  B   404
        resp = await client.put(
            f"/api/v1/alerts/{b_alert_id}/read",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp.status_code == 404

        # B 
        check = await (await db.execute("SELECT is_read FROM alerts WHERE id=?", (b_alert_id,))).fetchone()
        assert check["is_read"] == 0

    @pytest.mark.asyncio
    async def test_unread_count_isolation(self, client):
        """ A  B """
        uid = _uid()
        token_a, op_a = await _create_operator(f"iso_cnt_a_{uid}")
        token_b, op_b = await _create_operator(f"iso_cnt_b_{uid}")

        db = await get_shared_db()
        svc = AlertService(db)
        await svc.send(op_a, "login_fail", "A ")
        svc._dedup_cache.clear()
        await svc.send(op_b, "bet_fail", "B  1")
        svc._dedup_cache.clear()
        await svc.send(op_b, "login_fail", "B  2")

        resp_a = await client.get("/api/v1/alerts/unread-count", headers={"Authorization": f"Bearer {token_a}"})
        assert resp_a.json()["data"]["count"] == 1

        resp_b = await client.get("/api/v1/alerts/unread-count", headers={"Authorization": f"Bearer {token_b}"})
        assert resp_b.json()["data"]["count"] == 2


# 
# 8. 
# 


class TestEnvelopeFormat:
    """"""

    @pytest.mark.asyncio
    async def test_list_envelope(self, client):
        uid = _uid()
        token, _ = await _create_operator(f"env_list_{uid}")
        resp = await client.get("/api/v1/alerts", headers={"Authorization": f"Bearer {token}"})
        body = resp.json()
        assert "code" in body
        assert "message" in body
        assert "data" in body

    @pytest.mark.asyncio
    async def test_unread_count_envelope(self, client):
        uid = _uid()
        token, _ = await _create_operator(f"env_cnt_{uid}")
        resp = await client.get("/api/v1/alerts/unread-count", headers={"Authorization": f"Bearer {token}"})
        body = resp.json()
        assert "code" in body
        assert "message" in body
        assert "data" in body

    @pytest.mark.asyncio
    async def test_read_all_envelope(self, client):
        uid = _uid()
        token, _ = await _create_operator(f"env_ra_{uid}")
        resp = await client.put("/api/v1/alerts/read-all", headers={"Authorization": f"Bearer {token}"})
        body = resp.json()
        assert "code" in body
        assert "message" in body
        assert "data" in body

    @pytest.mark.asyncio
    async def test_error_envelope(self, client):
        uid = _uid()
        token, _ = await _create_operator(f"env_err_{uid}")
        resp = await client.put(
            "/api/v1/alerts/99999/read",
            headers={"Authorization": f"Bearer {token}"},
        )
        body = resp.json()
        assert body["code"] == 4001
        assert "message" in body
        assert "data" in body
