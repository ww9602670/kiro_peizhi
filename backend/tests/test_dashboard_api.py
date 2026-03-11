"""Phase 11.2   API 


- 
- 
- 
- 
"""
import uuid
from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.database import get_shared_db
from app.models.db_ops import (
    account_create,
    alert_create,
    bet_order_create,
    operator_create,
    strategy_create,
    strategy_update,
)
from app.utils.auth import create_token, register_session, persist_jti


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _get_admin_token() -> str:
    db = await get_shared_db()
    token, jti, _ = create_token(1, "admin")
    register_session(1, jti)
    await persist_jti(db, 1, jti)
    return token


async def _create_operator(username: str) -> tuple[str, int]:
    db = await get_shared_db()
    op = await operator_create(
        db, username=username, password="pass123456", created_by=1,
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

@pytest.mark.asyncio
async def test_operator_dashboard_empty(client):
    """"""
    uid = _uid()
    token, _ = await _create_operator(f"dash_empty_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.get("/api/v1/dashboard", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    d = body["data"]
    assert d["balance"] == 0.0
    assert d["daily_pnl"] == 0.0
    assert d["total_pnl"] == 0.0
    assert d["running_strategies"] == []
    assert d["recent_bets"] == []
    assert d["unread_alerts"] == 0


@pytest.mark.asyncio
async def test_operator_dashboard_with_data(client):
    """"""
    uid = _uid()
    token, op_id = await _create_operator(f"dash_data_{uid}")
    db = await get_shared_db()

    #  10000  = 100 
    acc = await account_create(
        db, operator_id=op_id, account_name=f"acc_{uid}",
        password="pwd", platform_type="JND28WEB",
    )
    await db.execute(
        "UPDATE gambling_accounts SET balance=10000 WHERE id=?", (acc["id"],)
    )
    await db.commit()

    # running + 
    today = datetime.utcnow().strftime("%Y-%m-%d")
    strat = await strategy_create(
        db, operator_id=op_id, account_id=acc["id"],
        name="test", type="flat", play_code="DX1", base_amount=1000,
    )
    await strategy_update(
        db, strategy_id=strat["id"], operator_id=op_id,
        status="running", daily_pnl=5000, total_pnl=20000, daily_pnl_date=today,
    )

    # 
    await bet_order_create(
        db, idempotent_id=f"dash-{uid}", operator_id=op_id,
        account_id=acc["id"], strategy_id=strat["id"],
        issue="202603021001", key_code="DX1", amount=1000,
    )

    # 
    await alert_create(
        db, operator_id=op_id, type="bet_fail",
        level="warning", title="",
    )

    headers = {"Authorization": f"Bearer {token}"}
    resp = await client.get("/api/v1/dashboard", headers=headers)
    body = resp.json()
    d = body["data"]
    assert d["balance"] == 100.0  # 10000  100
    assert d["daily_pnl"] == 50.0  # 5000  50
    assert d["total_pnl"] == 200.0  # 20000  200
    assert len(d["running_strategies"]) == 1
    assert d["running_strategies"][0]["name"] == "test"
    assert len(d["recent_bets"]) == 1
    assert d["unread_alerts"] == 1


# 
# 2. 
# 

@pytest.mark.asyncio
async def test_recent_bets(client):
    """ 20 """
    uid = _uid()
    token, op_id = await _create_operator(f"recent_{uid}")
    db = await get_shared_db()
    acc = await account_create(
        db, operator_id=op_id, account_name=f"acc_{uid}",
        password="pwd", platform_type="JND28WEB",
    )
    strat = await strategy_create(
        db, operator_id=op_id, account_id=acc["id"],
        name="s", type="flat", play_code="DX1", base_amount=100,
    )
    for i in range(25):
        await bet_order_create(
            db, idempotent_id=f"recent-{uid}-{i}", operator_id=op_id,
            account_id=acc["id"], strategy_id=strat["id"],
            issue=f"20260302{1000+i}", key_code="DX1", amount=100,
        )

    headers = {"Authorization": f"Bearer {token}"}
    resp = await client.get("/api/v1/dashboard/recent-bets", headers=headers)
    body = resp.json()
    assert body["code"] == 0
    assert len(body["data"]) == 20


# 
# 3. 
# 

@pytest.mark.asyncio
async def test_admin_dashboard(client):
    """"""
    uid = _uid()
    admin_token = await _get_admin_token()
    _, op_id = await _create_operator(f"adm_op_{uid}")
    db = await get_shared_db()

    acc = await account_create(
        db, operator_id=op_id, account_name=f"acc_{uid}",
        password="pwd", platform_type="JND28WEB",
    )
    today = datetime.utcnow().strftime("%Y-%m-%d")
    strat = await strategy_create(
        db, operator_id=op_id, account_id=acc["id"],
        name="s", type="flat", play_code="DX1", base_amount=100,
    )
    await strategy_update(
        db, strategy_id=strat["id"], operator_id=op_id,
        status="running", daily_pnl=3000, total_pnl=10000, daily_pnl_date=today,
    )

    headers = {"Authorization": f"Bearer {admin_token}"}
    resp = await client.get("/api/v1/admin/dashboard", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    d = body["data"]
    assert d["total_operators"] >= 2  # admin + created operator
    assert d["active_operators"] >= 1

    # 
    op_summary = next((s for s in d["operator_summaries"] if s["id"] == op_id), None)
    assert op_summary is not None
    assert op_summary["daily_pnl"] == 30.0  # 3000  30
    assert op_summary["total_pnl"] == 100.0  # 10000  100
    assert op_summary["running_strategies"] == 1


@pytest.mark.asyncio
async def test_admin_dashboard_requires_admin(client):
    """ 403"""
    uid = _uid()
    token, _ = await _create_operator(f"nonadm_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.get("/api/v1/admin/dashboard", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["code"] == 3001
