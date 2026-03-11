"""Phase 14.1   API 

14.1.1  API         
14.1.2 
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

import app.database as _db_module
from app.database import close_shared_db, get_shared_db
from app.main import app


def _uid() -> str:
    return uuid.uuid4().hex[:8]


#   fixtures 

@pytest.fixture
async def client():
    #  DB 
    if _db_module._shared_db is not None:
        await close_shared_db()

    with patch("app.main.EngineManager") as MockEngine:
        mock_engine = AsyncMock()
        mock_engine.restore_workers_on_startup = AsyncMock(return_value=0)
        mock_engine.start_health_check = AsyncMock()
        mock_engine.shutdown = AsyncMock()
        MockEngine.return_value = mock_engine

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    await close_shared_db()


async def _admin_login(client: AsyncClient) -> str:
    """ token"""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    return body["data"]["token"]


async def _create_operator_and_login(
    client: AsyncClient, admin_token: str, suffix: str | None = None
) -> tuple[str, int]:
    """   (token, operator_id)"""
    uid = suffix or _uid()
    username = f"op_{uid}"
    password = "pass123456"

    # 
    resp = await client.post(
        "/api/v1/admin/operators",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"username": username, "password": password, "max_accounts": 5},
    )
    assert resp.json()["code"] == 0
    op_id = resp.json()["data"]["id"]

    # 
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert resp.json()["code"] == 0
    token = resp.json()["data"]["token"]
    return token, op_id


# 
# 14.1.1  API 
# 

@pytest.mark.asyncio
async def test_full_api_flow(client):
    """ happy path        """
    uid = _uid()

    # 1. 
    admin_token = await _admin_login(client)
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 2. 
    resp = await client.post(
        "/api/v1/admin/operators",
        headers=admin_headers,
        json={"username": f"flow_{uid}", "password": "pass123456", "max_accounts": 3},
    )
    assert resp.json()["code"] == 0
    op_id = resp.json()["data"]["id"]

    # 3. 
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": f"flow_{uid}", "password": "pass123456"},
    )
    assert resp.json()["code"] == 0
    op_token = resp.json()["data"]["token"]
    op_headers = {"Authorization": f"Bearer {op_token}"}

    # 4. 
    resp = await client.post(
        "/api/v1/accounts",
        headers=op_headers,
        json={
            "account_name": f"gambler_{uid}",
            "password": "gp123456",
            "platform_type": "JND28WEB",
        },
    )
    assert resp.json()["code"] == 0
    account = resp.json()["data"]
    account_id = account["id"]
    assert account["platform_type"] == "JND28WEB"
    assert account["password_masked"].endswith("****")

    # 5. 
    resp = await client.get("/api/v1/accounts", headers=op_headers)
    assert resp.json()["code"] == 0
    assert len(resp.json()["data"]) == 1

    # 6. 
    resp = await client.post(
        "/api/v1/strategies",
        headers=op_headers,
        json={
            "account_id": account_id,
            "name": f"flat_{uid}",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
        },
    )
    assert resp.json()["code"] == 0
    strategy = resp.json()["data"]
    strategy_id = strategy["id"]
    assert strategy["type"] == "flat"
    assert strategy["status"] == "stopped"

    # 7. 
    resp = await client.get("/api/v1/strategies", headers=op_headers)
    assert resp.json()["code"] == 0
    assert len(resp.json()["data"]) == 1

    # 8. 
    resp = await client.get("/api/v1/bet-orders", headers=op_headers)
    assert resp.json()["code"] == 0
    assert resp.json()["data"]["total"] == 0

    # 9. 
    resp = await client.get("/api/v1/dashboard", headers=op_headers)
    assert resp.json()["code"] == 0
    dash = resp.json()["data"]
    assert "balance" in dash
    assert "daily_pnl" in dash
    assert "total_pnl" in dash
    assert "running_strategies" in dash
    assert "recent_bets" in dash
    assert "unread_alerts" in dash

    # 10. 
    resp = await client.get("/api/v1/alerts", headers=op_headers)
    assert resp.json()["code"] == 0

    # 11. 
    resp = await client.get("/api/v1/alerts/unread-count", headers=op_headers)
    assert resp.json()["code"] == 0
    assert resp.json()["data"]["count"] == 0

    # 12. 
    resp = await client.get("/api/v1/admin/dashboard", headers=admin_headers)
    assert resp.json()["code"] == 0
    summaries = resp.json()["data"]["operator_summaries"]
    op_ids = [s["id"] for s in summaries]
    assert op_id in op_ids



# 
# 14.1.2 
# 


@pytest.fixture
async def two_operators(client):
    """ (client, headers_a, id_a, headers_b, id_b, admin_headers)"""
    uid = _uid()
    admin_token = await _admin_login(client)
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    token_a, id_a = await _create_operator_and_login(client, admin_token, f"a_{uid}")
    token_b, id_b = await _create_operator_and_login(client, admin_token, f"b_{uid}")

    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    return client, headers_a, id_a, headers_b, id_b, admin_headers


#  gambling_accounts  

@pytest.mark.asyncio
async def test_accounts_self_visible(two_operators):
    """"""
    client, headers_a, id_a, _, _, _ = two_operators
    uid = _uid()

    await client.post(
        "/api/v1/accounts", headers=headers_a,
        json={"account_name": f"acc_{uid}", "password": "pw1234", "platform_type": "JND28WEB"},
    )
    resp = await client.get("/api/v1/accounts", headers=headers_a)
    assert resp.json()["code"] == 0
    assert any(a["account_name"] == f"acc_{uid}" for a in resp.json()["data"])


@pytest.mark.asyncio
async def test_accounts_cross_invisible(two_operators):
    """ B  A """
    client, headers_a, _, headers_b, _, _ = two_operators
    uid = _uid()

    await client.post(
        "/api/v1/accounts", headers=headers_a,
        json={"account_name": f"acc_{uid}", "password": "pw1234", "platform_type": "JND28WEB"},
    )
    resp = await client.get("/api/v1/accounts", headers=headers_b)
    assert resp.json()["code"] == 0
    assert not any(a["account_name"] == f"acc_{uid}" for a in resp.json()["data"])


@pytest.mark.asyncio
async def test_accounts_cross_delete_404(two_operators):
    """ B  A   404"""
    client, headers_a, _, headers_b, _, _ = two_operators
    uid = _uid()

    resp = await client.post(
        "/api/v1/accounts", headers=headers_a,
        json={"account_name": f"acc_{uid}", "password": "pw1234", "platform_type": "JND28WEB"},
    )
    acc_id = resp.json()["data"]["id"]

    resp = await client.delete(f"/api/v1/accounts/{acc_id}", headers=headers_b)
    assert resp.status_code == 404 or resp.json()["code"] == 4001


#  strategies  

async def _create_account_for(client, headers) -> int:
    """ account_id"""
    uid = _uid()
    resp = await client.post(
        "/api/v1/accounts", headers=headers,
        json={"account_name": f"sa_{uid}", "password": "pw1234", "platform_type": "JND28WEB"},
    )
    return resp.json()["data"]["id"]


@pytest.mark.asyncio
async def test_strategies_self_visible(two_operators):
    """"""
    client, headers_a, _, _, _, _ = two_operators
    acc_id = await _create_account_for(client, headers_a)

    resp = await client.post(
        "/api/v1/strategies", headers=headers_a,
        json={"account_id": acc_id, "name": "s1", "type": "flat", "play_code": "DX1", "base_amount": 10},
    )
    assert resp.json()["code"] == 0
    sid = resp.json()["data"]["id"]

    resp = await client.get("/api/v1/strategies", headers=headers_a)
    assert any(s["id"] == sid for s in resp.json()["data"])


@pytest.mark.asyncio
async def test_strategies_cross_invisible(two_operators):
    """ B  A """
    client, headers_a, _, headers_b, _, _ = two_operators
    acc_id = await _create_account_for(client, headers_a)

    resp = await client.post(
        "/api/v1/strategies", headers=headers_a,
        json={"account_id": acc_id, "name": "s_hidden", "type": "flat", "play_code": "DX1", "base_amount": 10},
    )
    sid = resp.json()["data"]["id"]

    resp = await client.get("/api/v1/strategies", headers=headers_b)
    assert not any(s["id"] == sid for s in resp.json()["data"])


@pytest.mark.asyncio
async def test_strategies_cross_start_404(two_operators):
    """ B  A   404"""
    client, headers_a, _, headers_b, _, _ = two_operators
    acc_id = await _create_account_for(client, headers_a)

    resp = await client.post(
        "/api/v1/strategies", headers=headers_a,
        json={"account_id": acc_id, "name": "s_start", "type": "flat", "play_code": "DX1", "base_amount": 10},
    )
    sid = resp.json()["data"]["id"]

    resp = await client.post(f"/api/v1/strategies/{sid}/start", headers=headers_b)
    assert resp.status_code == 404 or resp.json()["code"] == 4001


#  bet_orders  

@pytest.mark.asyncio
async def test_bet_orders_self_visible(two_operators):
    """"""
    client, headers_a, _, _, _, _ = two_operators

    resp = await client.get("/api/v1/bet-orders", headers=headers_a)
    assert resp.json()["code"] == 0
    assert "items" in resp.json()["data"]


@pytest.mark.asyncio
async def test_bet_orders_cross_invisible(two_operators):
    """ B  A 

     DB  A  B 
    """
    client, headers_a, id_a, headers_b, id_b, _ = two_operators
    db = await get_shared_db()

    #  A 
    acc_id = await _create_account_for(client, headers_a)
    resp = await client.post(
        "/api/v1/strategies", headers=headers_a,
        json={"account_id": acc_id, "name": "s_bet", "type": "flat", "play_code": "DX1", "base_amount": 10},
    )
    sid = resp.json()["data"]["id"]

    # 
    uid = _uid()
    await db.execute(
        """INSERT INTO bet_orders
           (idempotent_id, operator_id, account_id, strategy_id, issue, key_code, amount, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (f"idem_{uid}", id_a, acc_id, sid, "20260101001", "DX1", 1000, "pending"),
    )
    await db.commit()

    # A 
    resp = await client.get("/api/v1/bet-orders", headers=headers_a)
    assert resp.json()["data"]["total"] >= 1

    # B 
    resp = await client.get("/api/v1/bet-orders", headers=headers_b)
    assert resp.json()["data"]["total"] == 0


@pytest.mark.asyncio
async def test_bet_orders_cross_detail_404(two_operators):
    """ B  A   404"""
    client, headers_a, id_a, headers_b, _, _ = two_operators
    db = await get_shared_db()

    acc_id = await _create_account_for(client, headers_a)
    resp = await client.post(
        "/api/v1/strategies", headers=headers_a,
        json={"account_id": acc_id, "name": "s_det", "type": "flat", "play_code": "DX1", "base_amount": 10},
    )
    sid = resp.json()["data"]["id"]

    uid = _uid()
    await db.execute(
        """INSERT INTO bet_orders
           (idempotent_id, operator_id, account_id, strategy_id, issue, key_code, amount, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (f"idem_{uid}", id_a, acc_id, sid, "20260101002", "DX1", 1000, "pending"),
    )
    await db.commit()

    cursor = await db.execute("SELECT id FROM bet_orders WHERE idempotent_id=?", (f"idem_{uid}",))
    order_id = (await cursor.fetchone())["id"]

    resp = await client.get(f"/api/v1/bet-orders/{order_id}", headers=headers_b)
    assert resp.status_code == 404 or resp.json()["code"] == 4001


#  alerts  

@pytest.mark.asyncio
async def test_alerts_self_visible(two_operators):
    """"""
    client, headers_a, id_a, _, _, _ = two_operators
    db = await get_shared_db()

    await db.execute(
        "INSERT INTO alerts (operator_id, type, level, title) VALUES (?, ?, ?, ?)",
        (id_a, "bet_fail", "warning", ""),
    )
    await db.commit()

    resp = await client.get("/api/v1/alerts", headers=headers_a)
    assert resp.json()["code"] == 0
    assert resp.json()["data"]["total"] >= 1


@pytest.mark.asyncio
async def test_alerts_cross_invisible(two_operators):
    """ B  A """
    client, headers_a, id_a, headers_b, _, _ = two_operators
    db = await get_shared_db()

    await db.execute(
        "INSERT INTO alerts (operator_id, type, level, title) VALUES (?, ?, ?, ?)",
        (id_a, "bet_fail", "warning", "A"),
    )
    await db.commit()

    resp = await client.get("/api/v1/alerts", headers=headers_b)
    assert resp.json()["code"] == 0
    # B  A 
    items = resp.json()["data"]["items"]
    assert not any(a.get("title") == "A" for a in items)


@pytest.mark.asyncio
async def test_alerts_cross_mark_read_404(two_operators):
    """ B  A   404"""
    client, _, id_a, headers_b, _, _ = two_operators
    db = await get_shared_db()

    await db.execute(
        "INSERT INTO alerts (operator_id, type, level, title) VALUES (?, ?, ?, ?)",
        (id_a, "bet_fail", "warning", "A2"),
    )
    await db.commit()

    cursor = await db.execute(
        "SELECT id FROM alerts WHERE operator_id=? ORDER BY id DESC LIMIT 1", (id_a,)
    )
    alert_id = (await cursor.fetchone())["id"]

    resp = await client.put(f"/api/v1/alerts/{alert_id}/read", headers=headers_b)
    assert resp.status_code == 404 or resp.json()["code"] == 4001


#  operators / 

@pytest.mark.asyncio
async def test_operators_non_admin_cannot_list(two_operators):
    """  403"""
    client, headers_a, _, _, _, _ = two_operators

    resp = await client.get("/api/v1/admin/operators", headers=headers_a)
    assert resp.status_code == 403
    assert resp.json()["code"] == 3001


@pytest.mark.asyncio
async def test_operators_non_admin_cannot_modify(two_operators):
    """  403"""
    client, headers_a, _, _, id_b, _ = two_operators

    resp = await client.put(
        f"/api/v1/admin/operators/{id_b}",
        headers=headers_a,
        json={"max_accounts": 99},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == 3001


@pytest.mark.asyncio
async def test_operators_admin_can_view_all(two_operators):
    """ DB """
    client, _, id_a, _, id_b, admin_headers = two_operators
    db = await get_shared_db()

    #  DB 
    cursor = await db.execute("SELECT id FROM operators WHERE id IN (?, ?)", (id_a, id_b))
    rows = await cursor.fetchall()
    found_ids = {r["id"] for r in rows}
    assert id_a in found_ids
    assert id_b in found_ids

    #  API 
    resp = await client.get("/api/v1/admin/operators?page=1&page_size=1", headers=admin_headers)
    assert resp.json()["code"] == 0
    assert resp.json()["data"]["total"] >= 3  # admin + A + B


#  audit_logs  API 

@pytest.mark.asyncio
async def test_audit_logs_isolation_via_admin(two_operators):
    """ API

     API 
     DB 
    """
    client, _, id_a, _, _, admin_headers = two_operators
    db = await get_shared_db()

    # 
    cursor = await db.execute(
        "SELECT COUNT(*) as cnt FROM audit_logs WHERE operator_id=1"
    )
    row = await cursor.fetchone()
    assert row["cnt"] >= 1  # 


#  reconcile_records  API DB  

@pytest.mark.asyncio
async def test_reconcile_records_isolation_db_level(two_operators):
    """ DB  operator_id 

    reconcile_records  account_id  operator_id 
     DB 
    """
    client, headers_a, id_a, headers_b, id_b, _ = two_operators
    db = await get_shared_db()

    #  A 
    acc_id_a = await _create_account_for(client, headers_a)

    #  A 
    await db.execute(
        """INSERT INTO reconcile_records
           (account_id, issue, local_bet_count, status)
           VALUES (?, ?, ?, ?)""",
        (acc_id_a, "20260101001", 5, "matched"),
    )
    await db.commit()

    #  account_id B  A 
    cursor = await db.execute(
        """SELECT r.* FROM reconcile_records r
           JOIN gambling_accounts a ON r.account_id = a.id
           WHERE a.operator_id = ?""",
        (id_b,),
    )
    rows = await cursor.fetchall()
    assert len(rows) == 0

    # A 
    cursor = await db.execute(
        """SELECT r.* FROM reconcile_records r
           JOIN gambling_accounts a ON r.account_id = a.id
           WHERE a.operator_id = ?""",
        (id_a,),
    )
    rows = await cursor.fetchall()
    assert len(rows) >= 1


#  lottery_results  

@pytest.mark.asyncio
async def test_lottery_results_global_readonly(two_operators):
    """lottery_results API 

    
    1. DB  lottery_results  DB 
    2.  API 
    """
    client, headers_a, _, headers_b, _, _ = two_operators
    db = await get_shared_db()

    #  API
    uid = _uid()
    await db.execute(
        "INSERT OR IGNORE INTO lottery_results (issue, open_result, sum_value) VALUES (?, ?, ?)",
        (f"LR_{uid}", "3,5,7", 15),
    )
    await db.commit()

    #  API POST/PUT   404/405
    for method in ["post", "put"]:
        resp = await getattr(client, method)(
            "/api/v1/lottery-results",
            headers=headers_a,
            json={"issue": "test", "open_result": "1,2,3", "sum_value": 6},
        )
        #  404  405
        assert resp.status_code in (404, 405, 422)


#  dashboard  

@pytest.mark.asyncio
async def test_dashboard_shows_own_data_only(two_operators):
    """"""
    client, headers_a, id_a, headers_b, id_b, _ = two_operators

    # A 
    acc_id = await _create_account_for(client, headers_a)
    await client.post(
        "/api/v1/strategies", headers=headers_a,
        json={"account_id": acc_id, "name": "dash_s", "type": "flat", "play_code": "DX1", "base_amount": 10},
    )

    # A 
    resp = await client.get("/api/v1/dashboard", headers=headers_a)
    assert resp.json()["code"] == 0

    # B  A 
    resp = await client.get("/api/v1/dashboard", headers=headers_b)
    assert resp.json()["code"] == 0
    dash_b = resp.json()["data"]
    assert len(dash_b["running_strategies"]) == 0
    assert dash_b["balance"] == 0


#  unread-count  

@pytest.mark.asyncio
async def test_unread_count_isolation(two_operators):
    """"""
    client, headers_a, id_a, headers_b, id_b, _ = two_operators
    db = await get_shared_db()

    #  A  3 
    for i in range(3):
        await db.execute(
            "INSERT INTO alerts (operator_id, type, level, title) VALUES (?, ?, ?, ?)",
            (id_a, "bet_fail", "warning", f"alert_{i}"),
        )
    await db.commit()

    resp = await client.get("/api/v1/alerts/unread-count", headers=headers_a)
    assert resp.json()["data"]["count"] >= 3

    resp = await client.get("/api/v1/alerts/unread-count", headers=headers_b)
    assert resp.json()["data"]["count"] == 0
