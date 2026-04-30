import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import get_shared_db
from app.main import app
from app.models.db_ops import (
    account_create,
    account_strategy_permission_set,
    bet_order_create,
    operator_create,
    simulation_bet_order_create,
    simulation_strategy_stats_upsert,
    strategy_create,
)
from app.schemas.strategy import STRATEGY_PERMISSION_TYPES
from app.utils.auth import create_token, persist_jti, register_session


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _create_operator(username: str) -> tuple[str, int]:
    db = await get_shared_db()
    operator = await operator_create(
        db,
        username=username,
        password="pass123456",
        created_by=1,
    )
    token, jti, _ = create_token(operator["id"], "operator")
    register_session(operator["id"], jti)
    await persist_jti(db, operator["id"], jti)
    return token, operator["id"]


async def _create_account_and_strategy(operator_id: int, *, simulation: int = 0) -> tuple[int, int]:
    db = await get_shared_db()
    account = await account_create(
        db,
        operator_id=operator_id,
        account_name=f"acc_{_uid()}",
        password="pwd",
        platform_type="JND282",
    )
    await account_strategy_permission_set(
        db,
        operator_id=operator_id,
        account_id=account["id"],
        strategy_types=list(STRATEGY_PERMISSION_TYPES),
        created_by=1,
    )
    strategy = await strategy_create(
        db,
        operator_id=operator_id,
        account_id=account["id"],
        name=f"strat_{_uid()}",
        type="flat",
        play_code="DX1",
        base_amount=1000,
        simulation=simulation,
        platform_type="JND282",
    )
    return account["id"], strategy["id"]


async def _create_real_order(
    operator_id: int,
    account_id: int,
    strategy_id: int,
    *,
    status: str = "settled",
) -> dict:
    db = await get_shared_db()
    return await bet_order_create(
        db,
        idempotent_id=f"real-{_uid()}",
        operator_id=operator_id,
        account_id=account_id,
        strategy_id=strategy_id,
        issue="202604210001",
        key_code="DX1",
        amount=5000,
        odds=19800,
        status=status,
        simulation=0,
    )


async def _set_real_order_created_at(order_id: int, created_at: str) -> None:
    db = await get_shared_db()
    await db.execute(
        "UPDATE bet_orders SET created_at=?, bet_at=? WHERE id=?",
        (created_at, created_at, order_id),
    )
    await db.commit()


async def _create_simulation_order(operator_id: int, account_id: int, strategy_id: int) -> dict:
    db = await get_shared_db()
    return await simulation_bet_order_create(
        db,
        idempotent_id=f"sim-{_uid()}",
        operator_id=operator_id,
        account_id=account_id,
        strategy_id=strategy_id,
        issue="202604210002",
        platform_type="JND282",
        key_code="DX1",
        amount=7000,
        odds=19900,
        status="settled",
        pnl=1500,
        is_win=1,
    )


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as current:
        yield current


@pytest.mark.asyncio
async def test_list_bet_orders_uses_paged_shape(client):
    token, _ = await _create_operator(f"empty_{_uid()}")

    response = await client.get(
        "/api/v1/bet-orders",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["paged"]["items"] == []
    assert body["data"]["paged"]["total"] == 0
    assert body["data"]["summary"]["total_amount"] == 0
    assert body["data"]["summary"]["total_payout"] == 0


@pytest.mark.asyncio
async def test_list_real_bet_orders_reads_real_ledger_only(client):
    token, operator_id = await _create_operator(f"real_{_uid()}")
    account_id, strategy_id = await _create_account_and_strategy(operator_id)
    await _create_real_order(operator_id, account_id, strategy_id)
    await _create_simulation_order(operator_id, account_id, strategy_id)

    response = await client.get(
        "/api/v1/bet-orders?ledger=real",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["paged"]["total"] == 1
    item = body["data"]["paged"]["items"][0]
    assert item["simulation"] is False
    assert item["issue"] == "202604210001"
    assert body["data"]["summary"]["total_amount"] == 50.0


@pytest.mark.asyncio
async def test_list_bet_orders_defaults_to_24h_and_clamps_query_to_3_days(client):
    token, operator_id = await _create_operator(f"range_{_uid()}")
    account_id, strategy_id = await _create_account_and_strategy(operator_id)
    recent = await _create_real_order(operator_id, account_id, strategy_id, status="pending")
    two_days_old = await _create_real_order(operator_id, account_id, strategy_id, status="pending")
    four_days_old = await _create_real_order(operator_id, account_id, strategy_id, status="pending")

    now = datetime.now(timezone(timedelta(hours=8)))
    await _set_real_order_created_at(
        recent["id"],
        (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
    )
    await _set_real_order_created_at(
        two_days_old["id"],
        (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"),
    )
    await _set_real_order_created_at(
        four_days_old["id"],
        (now - timedelta(days=4)).strftime("%Y-%m-%d %H:%M:%S"),
    )

    default_response = await client.get(
        "/api/v1/bet-orders?ledger=real",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert default_response.status_code == 200
    assert default_response.json()["data"]["paged"]["total"] == 1

    old_date = (now - timedelta(days=10)).strftime("%Y-%m-%d")
    query_response = await client.get(
        f"/api/v1/bet-orders?ledger=real&date_from={old_date}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert query_response.status_code == 200
    assert query_response.json()["data"]["paged"]["total"] == 2


@pytest.mark.asyncio
async def test_list_simulation_bet_orders_reads_simulation_ledger_only(client):
    token, operator_id = await _create_operator(f"sim_{_uid()}")
    account_id, strategy_id = await _create_account_and_strategy(operator_id, simulation=1)
    await _create_real_order(operator_id, account_id, strategy_id)
    await _create_simulation_order(operator_id, account_id, strategy_id)

    response = await client.get(
        "/api/v1/bet-orders?ledger=simulation",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["paged"]["total"] == 1
    item = body["data"]["paged"]["items"][0]
    assert item["simulation"] is True
    assert item["issue"] == "202604210002"
    assert item["pnl"] == 15.0
    assert body["data"]["summary"]["total_amount"] == 70.0
    assert body["data"]["summary"]["total_payout"] == 85.0


@pytest.mark.asyncio
async def test_list_strategies_reads_simulation_strategy_stats_for_simulation_mode(client):
    token, operator_id = await _create_operator(f"stratsim_{_uid()}")
    account_id, strategy_id = await _create_account_and_strategy(operator_id, simulation=1)
    db = await get_shared_db()

    await db.execute(
        "UPDATE strategies SET daily_pnl=?, total_pnl=?, daily_pnl_date=? WHERE id=? AND operator_id=?",
        (99900, 88800, "1999-12-31", strategy_id, operator_id),
    )
    await db.commit()

    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    await simulation_strategy_stats_upsert(
        db,
        strategy_id=strategy_id,
        operator_id=operator_id,
        account_id=account_id,
        daily_pnl_delta=1234,
        total_pnl_delta=5678,
        daily_pnl_date=today,
    )

    response = await client.get(
        "/api/v1/strategies",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0

    item = next(x for x in body["data"] if x["id"] == strategy_id)
    assert item["simulation"] is True
    assert item["daily_pnl"] == 12.34
    assert item["total_pnl"] == 56.78


@pytest.mark.asyncio
async def test_get_bet_order_honors_ledger(client):
    token, operator_id = await _create_operator(f"detail_{_uid()}")
    account_id, strategy_id = await _create_account_and_strategy(operator_id, simulation=1)
    simulation_order = await _create_simulation_order(operator_id, account_id, strategy_id)

    response = await client.get(
        f"/api/v1/bet-orders/{simulation_order['id']}?ledger=simulation",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["simulation"] is True
    assert body["data"]["issue"] == "202604210002"

    missing = await client.get(
        f"/api/v1/bet-orders/{simulation_order['id']}?ledger=real",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert missing.status_code == 404
