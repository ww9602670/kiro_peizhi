"""Phase 11.1   API 


- 
- 
- 
-  A  B  404
- 
- key_code_name 
- 
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.database import get_shared_db
from app.models.db_ops import (
    bet_order_create,
    operator_create,
    account_create,
    strategy_create,
)
from app.utils.auth import create_token, register_session, persist_jti


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _create_operator(username: str) -> tuple[str, int]:
    db = await get_shared_db()
    op = await operator_create(
        db, username=username, password="pass123456", created_by=1,
    )
    token, jti, _ = create_token(op["id"], "operator")
    register_session(op["id"], jti)
    await persist_jti(db, op["id"], jti)
    return token, op["id"]


async def _setup_account_and_strategy(operator_id: int) -> tuple[int, int]:
    """ (account_id, strategy_id)"""
    db = await get_shared_db()
    acc = await account_create(
        db, operator_id=operator_id, account_name=f"acc_{_uid()}",
        password="pwd", platform_type="JND28WEB",
    )
    strat = await strategy_create(
        db, operator_id=operator_id, account_id=acc["id"],
        name="test_strat", type="flat", play_code="DX1", base_amount=1000,
    )
    return acc["id"], strat["id"]


async def _create_bet_order(
    operator_id: int, account_id: int, strategy_id: int,
    issue: str = "202603021001", key_code: str = "DX1",
    amount: int = 1000, status: str = "pending",
) -> dict:
    db = await get_shared_db()
    return await bet_order_create(
        db,
        idempotent_id=f"{issue}-{strategy_id}-{key_code}-{_uid()}",
        operator_id=operator_id,
        account_id=account_id,
        strategy_id=strategy_id,
        issue=issue,
        key_code=key_code,
        amount=amount,
        status=status,
    )


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# 
# 1. 
# 

@pytest.mark.asyncio
async def test_list_bet_orders_empty(client):
    """"""
    uid = _uid()
    token, _ = await _create_operator(f"empty_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.get("/api/v1/bet-orders", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["items"] == []
    assert body["data"]["total"] == 0


@pytest.mark.asyncio
async def test_list_bet_orders_with_data(client):
    """"""
    uid = _uid()
    token, op_id = await _create_operator(f"data_{uid}")
    acc_id, strat_id = await _setup_account_and_strategy(op_id)
    await _create_bet_order(op_id, acc_id, strat_id, key_code="DX1", amount=5000)
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.get("/api/v1/bet-orders", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["total"] == 1
    item = body["data"]["items"][0]
    assert item["key_code"] == "DX1"
    assert item["key_code_name"] == ""
    assert item["amount"] == 50.0  # 5000  50


# 
# 2. 
# 

@pytest.mark.asyncio
async def test_pagination(client):
    """"""
    uid = _uid()
    token, op_id = await _create_operator(f"page_{uid}")
    acc_id, strat_id = await _setup_account_and_strategy(op_id)
    for i in range(5):
        await _create_bet_order(op_id, acc_id, strat_id, issue=f"20260302{1000+i}")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.get("/api/v1/bet-orders?page=1&page_size=2", headers=headers)
    body = resp.json()
    assert body["data"]["total"] == 5
    assert len(body["data"]["items"]) == 2
    assert body["data"]["page"] == 1
    assert body["data"]["page_size"] == 2

    resp2 = await client.get("/api/v1/bet-orders?page=3&page_size=2", headers=headers)
    body2 = resp2.json()
    assert len(body2["data"]["items"]) == 1  # 31


# 
# 3. 
# 

@pytest.mark.asyncio
async def test_filter_by_strategy(client):
    """ ID """
    uid = _uid()
    token, op_id = await _create_operator(f"filter_{uid}")
    acc_id, strat_id = await _setup_account_and_strategy(op_id)
    db = await get_shared_db()
    strat2 = await strategy_create(
        db, operator_id=op_id, account_id=acc_id,
        name="strat2", type="flat", play_code="DS3", base_amount=500,
    )
    await _create_bet_order(op_id, acc_id, strat_id, key_code="DX1")
    await _create_bet_order(op_id, acc_id, strat2["id"], key_code="DS3")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.get(f"/api/v1/bet-orders?strategy_id={strat_id}", headers=headers)
    body = resp.json()
    assert body["data"]["total"] == 1
    assert body["data"]["items"][0]["key_code"] == "DX1"


# 
# 4. 
# 

@pytest.mark.asyncio
async def test_get_bet_order_by_id(client):
    """ ID """
    uid = _uid()
    token, op_id = await _create_operator(f"detail_{uid}")
    acc_id, strat_id = await _setup_account_and_strategy(op_id)
    order = await _create_bet_order(op_id, acc_id, strat_id, key_code="ZH7", amount=2000)
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.get(f"/api/v1/bet-orders/{order['id']}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["key_code"] == "ZH7"
    assert body["data"]["key_code_name"] == ""
    assert body["data"]["amount"] == 20.0  # 2000  20


@pytest.mark.asyncio
async def test_get_bet_order_not_found(client):
    """ 404"""
    uid = _uid()
    token, _ = await _create_operator(f"nf_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.get("/api/v1/bet-orders/99999", headers=headers)
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == 4001


# 
# 5. 
# 

@pytest.mark.asyncio
async def test_data_isolation_list(client):
    """ A  B """
    uid = _uid()
    token_a, op_a = await _create_operator(f"iso_a_{uid}")
    token_b, op_b = await _create_operator(f"iso_b_{uid}")
    acc_a, strat_a = await _setup_account_and_strategy(op_a)
    acc_b, strat_b = await _setup_account_and_strategy(op_b)

    await _create_bet_order(op_a, acc_a, strat_a, key_code="DX1")
    await _create_bet_order(op_b, acc_b, strat_b, key_code="DS3")

    # A 
    resp_a = await client.get("/api/v1/bet-orders", headers={"Authorization": f"Bearer {token_a}"})
    body_a = resp_a.json()
    assert body_a["data"]["total"] == 1
    assert body_a["data"]["items"][0]["key_code"] == "DX1"

    # B 
    resp_b = await client.get("/api/v1/bet-orders", headers={"Authorization": f"Bearer {token_b}"})
    body_b = resp_b.json()
    assert body_b["data"]["total"] == 1
    assert body_b["data"]["items"][0]["key_code"] == "DS3"


@pytest.mark.asyncio
async def test_data_isolation_detail(client):
    """ A  B  404"""
    uid = _uid()
    token_a, op_a = await _create_operator(f"xiso_a_{uid}")
    _, op_b = await _create_operator(f"xiso_b_{uid}")
    acc_b, strat_b = await _setup_account_and_strategy(op_b)
    order_b = await _create_bet_order(op_b, acc_b, strat_b)

    resp = await client.get(
        f"/api/v1/bet-orders/{order_b['id']}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == 4001


# 
# 6. 
# 

@pytest.mark.asyncio
async def test_unit_conversion(client):
    """ odds """
    uid = _uid()
    token, op_id = await _create_operator(f"unit_{uid}")
    acc_id, strat_id = await _setup_account_and_strategy(op_id)
    db = await get_shared_db()
    order = await bet_order_create(
        db,
        idempotent_id=f"unit-{_uid()}",
        operator_id=op_id,
        account_id=acc_id,
        strategy_id=strat_id,
        issue="202603021001",
        key_code="DX2",
        amount=10000,  # 100
        odds=19800,  # 1.98
        status="pending",
        simulation=0,
    )
    #  bet_success  pnl  settled
    await db.execute(
        "UPDATE bet_orders SET status='bet_success', pnl=9800, is_win=1, sum_value=10 WHERE id=?",
        (order["id"],),
    )
    await db.commit()
    await db.execute(
        "UPDATE bet_orders SET status='settled' WHERE id=?",
        (order["id"],),
    )
    await db.commit()

    headers = {"Authorization": f"Bearer {token}"}
    resp = await client.get(f"/api/v1/bet-orders/{order['id']}", headers=headers)
    body = resp.json()
    item = body["data"]
    assert item["amount"] == 100.0
    assert item["odds"] == 1.98
    assert item["pnl"] == 98.0
    assert item["key_code_name"] == ""
    assert item["is_win"] == 1
