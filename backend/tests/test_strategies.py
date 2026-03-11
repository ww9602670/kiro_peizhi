"""Task 4.1.5   API 


- CRUD ///
- /
- 
-  round-trip4.1.4a
- 
- 
- 
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.database import get_shared_db
from app.models.db_ops import account_create, operator_create
from app.schemas.strategy import validate_state_transition
from app.utils.auth import create_token, register_session, persist_jti


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _create_operator_with_account(
    username: str, max_accounts: int = 3
) -> tuple[str, int, int]:
    """ +  (token, operator_id, account_id)"""
    db = await get_shared_db()
    op = await operator_create(
        db, username=username, password="pass123456",
        max_accounts=max_accounts, created_by=1,
    )
    token, jti, _ = create_token(op["id"], "operator")
    register_session(op["id"], jti)
    await persist_jti(db, op["id"], jti)

    acc = await account_create(
        db,
        operator_id=op["id"],
        account_name=f"acc_{username}",
        password="accpass",
        platform_type="JND28WEB",
    )
    return token, op["id"], acc["id"]


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# 
# 1. 
# 

class TestStateTransition:
    """validate_state_transition """

    def test_valid_stopped_to_running(self):
        assert validate_state_transition("stopped", "running") is True

    def test_valid_running_to_paused(self):
        assert validate_state_transition("running", "paused") is True

    def test_valid_running_to_stopped(self):
        assert validate_state_transition("running", "stopped") is True

    def test_valid_paused_to_running(self):
        assert validate_state_transition("paused", "running") is True

    def test_valid_paused_to_stopped(self):
        assert validate_state_transition("paused", "stopped") is True

    def test_valid_error_to_stopped(self):
        assert validate_state_transition("error", "stopped") is True

    def test_invalid_stopped_to_paused(self):
        assert validate_state_transition("stopped", "paused") is False

    def test_invalid_stopped_to_stopped(self):
        assert validate_state_transition("stopped", "stopped") is False

    def test_invalid_paused_to_paused(self):
        assert validate_state_transition("paused", "paused") is False

    def test_invalid_running_to_running(self):
        assert validate_state_transition("running", "running") is False

    def test_invalid_error_to_running(self):
        assert validate_state_transition("error", "running") is False

    def test_invalid_error_to_paused(self):
        assert validate_state_transition("error", "paused") is False


# 
# 2.  round-trip4.1.4a
# 

class TestOddsRoundTrip:
    """ 10000  round-trip """

    @pytest.mark.parametrize("odds_float", [
        1.0, 1.5, 1.98, 2.0, 2.053, 2.0530, 1.9834, 3.141, 9.999, 0.001, 100.0,
    ])
    def test_round_trip_error_within_threshold(self, odds_float: float):
        """round(odds_float * 10000) / 10000  < 0.0001"""
        odds_int = round(odds_float * 10000)
        assert odds_int > 0, "odds_int  > 0"
        restored = odds_int / 10000
        assert abs(restored - odds_float) < 0.0001

    def test_odds_stored_as_integer(self):
        """ odds_int  Python int """
        odds_int = round(2.0530 * 10000)
        assert isinstance(odds_int, int)
        assert odds_int == 20530


# 
# 3. 
# 

class TestAmountConversion:
    """"""

    def test_yuan_to_fen(self):
        from app.api.strategies import _yuan_to_fen
        assert _yuan_to_fen(10.0) == 1000
        assert _yuan_to_fen(0.01) == 1
        assert _yuan_to_fen(100.50) == 10050

    def test_fen_to_yuan(self):
        from app.api.strategies import _fen_to_yuan
        assert _fen_to_yuan(1000) == 10.0
        assert _fen_to_yuan(1) == 0.01
        assert _fen_to_yuan(0) == 0.0

    def test_round_trip(self):
        """ """
        from app.api.strategies import _yuan_to_fen, _fen_to_yuan
        for yuan in [1.0, 10.0, 0.01, 99.99, 100.50]:
            assert _fen_to_yuan(_yuan_to_fen(yuan)) == yuan


# 
# 4. Schema 
# 

class TestSchemaValidation:
    """StrategyCreate schema """

    def test_martin_requires_sequence(self):
        """"""
        from app.schemas.strategy import StrategyCreate
        with pytest.raises(Exception):
            StrategyCreate(
                account_id=1, name="test", type="martin",
                play_code="DX1", base_amount=10.0,
                martin_sequence=None,
            )

    def test_martin_sequence_must_be_positive(self):
        """ > 0"""
        from app.schemas.strategy import StrategyCreate
        with pytest.raises(Exception):
            StrategyCreate(
                account_id=1, name="test", type="martin",
                play_code="DX1", base_amount=10.0,
                martin_sequence=[1, 0, 4],
            )

    def test_flat_ignores_sequence(self):
        """"""
        from app.schemas.strategy import StrategyCreate
        s = StrategyCreate(
            account_id=1, name="test", type="flat",
            play_code="DX1", base_amount=10.0,
            martin_sequence=[1, 2, 4],
        )
        assert s.martin_sequence is None

    def test_martin_valid(self):
        """"""
        from app.schemas.strategy import StrategyCreate
        s = StrategyCreate(
            account_id=1, name="martin_test", type="martin",
            play_code="DX1", base_amount=10.0,
            martin_sequence=[1, 2, 4, 8, 16],
        )
        assert s.martin_sequence == [1, 2, 4, 8, 16]


# 
# 5.  API
# 

@pytest.mark.asyncio
async def test_create_flat_strategy(client):
    """"""
    uid = _uid()
    token, op_id, acc_id = await _create_operator_with_account(f"flat_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "flat_test",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
            "simulation": False,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["name"] == "flat_test"
    assert data["type"] == "flat"
    assert data["play_code"] == "DX1"
    assert data["base_amount"] == 10.0
    assert data["martin_sequence"] is None
    assert data["status"] == "stopped"
    assert data["simulation"] is False
    assert data["daily_pnl"] == 0.0
    assert data["total_pnl"] == 0.0


@pytest.mark.asyncio
async def test_create_martin_strategy(client):
    """"""
    uid = _uid()
    token, op_id, acc_id = await _create_operator_with_account(f"martin_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "martin_strat",
            "type": "martin",
            "play_code": "DX2",
            "base_amount": 5.0,
            "martin_sequence": [1, 2, 4, 8],
            "bet_timing": 45,
            "simulation": True,
            "stop_loss": 100.0,
            "take_profit": 50.0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["type"] == "martin"
    assert data["martin_sequence"] == [1, 2, 4, 8]
    assert data["base_amount"] == 5.0
    assert data["bet_timing"] == 45
    assert data["simulation"] is True
    assert data["stop_loss"] == 100.0
    assert data["take_profit"] == 50.0


@pytest.mark.asyncio
async def test_create_strategy_invalid_account(client):
    """ 404"""
    uid = _uid()
    token, _, _ = await _create_operator_with_account(f"invacct_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": 99999,
            "name": "test",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
        },
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == 4001


@pytest.mark.asyncio
async def test_create_martin_without_sequence(client):
    """ 422"""
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(f"noseq_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "test",
            "type": "martin",
            "play_code": "DX1",
            "base_amount": 10.0,
        },
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == 1001


# 
# 6. 
# 

@pytest.mark.asyncio
async def test_list_strategies(client):
    """"""
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(f"list_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    # 
    for i in range(2):
        await client.post(
            "/api/v1/strategies",
            headers=headers,
            json={
                "account_id": acc_id,
                "name": f"{i}",
                "type": "flat",
                "play_code": "DX1",
                "base_amount": 10.0,
            },
        )

    resp = await client.get("/api/v1/strategies", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["code"] == 0
    assert len(resp.json()["data"]) == 2


# 
# 7. 
# 

@pytest.mark.asyncio
async def test_update_strategy(client):
    """ stopped """
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(f"upd_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "upd_orig",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
        },
    )
    sid = create_resp.json()["data"]["id"]

    resp = await client.put(
        f"/api/v1/strategies/{sid}",
        headers=headers,
        json={"name": "upd_new", "base_amount": 20.0},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["name"] == "upd_new"
    assert data["base_amount"] == 20.0


@pytest.mark.asyncio
async def test_update_running_strategy_rejected(client):
    """"""
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(f"updrun_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "test",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
        },
    )
    sid = create_resp.json()["data"]["id"]

    # 
    await client.post(f"/api/v1/strategies/{sid}/start", headers=headers)

    #   
    resp = await client.put(
        f"/api/v1/strategies/{sid}",
        headers=headers,
        json={"name": "new_name"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == 4003


# 
# 8. 
# 

@pytest.mark.asyncio
async def test_delete_strategy(client):
    """ stopped """
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(f"del_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "del_test",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
        },
    )
    sid = create_resp.json()["data"]["id"]

    resp = await client.delete(f"/api/v1/strategies/{sid}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["code"] == 0

    # 
    list_resp = await client.get("/api/v1/strategies", headers=headers)
    assert len(list_resp.json()["data"]) == 0


@pytest.mark.asyncio
async def test_delete_running_strategy_rejected(client):
    """"""
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(f"delrun_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "test",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
        },
    )
    sid = create_resp.json()["data"]["id"]

    await client.post(f"/api/v1/strategies/{sid}/start", headers=headers)

    resp = await client.delete(f"/api/v1/strategies/{sid}", headers=headers)
    assert resp.status_code == 400
    assert resp.json()["code"] == 4003


# 
# 9.  API
# 

@pytest.mark.asyncio
async def test_state_transitions_full_cycle(client):
    """stoppedrunningpausedrunningstopped"""
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(f"cycle_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "cycle",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
        },
    )
    sid = create_resp.json()["data"]["id"]
    assert create_resp.json()["data"]["status"] == "stopped"

    # stopped  running
    resp = await client.post(f"/api/v1/strategies/{sid}/start", headers=headers)
    assert resp.json()["data"]["status"] == "running"

    # running  paused
    resp = await client.post(f"/api/v1/strategies/{sid}/pause", headers=headers)
    assert resp.json()["data"]["status"] == "paused"

    # paused  running
    resp = await client.post(f"/api/v1/strategies/{sid}/start", headers=headers)
    assert resp.json()["data"]["status"] == "running"

    # running  stopped
    resp = await client.post(f"/api/v1/strategies/{sid}/stop", headers=headers)
    assert resp.json()["data"]["status"] == "stopped"


@pytest.mark.asyncio
async def test_invalid_state_transition(client):
    """ 4003"""
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(f"invst_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "test",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
        },
    )
    sid = create_resp.json()["data"]["id"]

    # stopped  paused
    resp = await client.post(f"/api/v1/strategies/{sid}/pause", headers=headers)
    assert resp.status_code == 400
    assert resp.json()["code"] == 4003
    assert "stopped  paused" in resp.json()["message"]

    # stopped  stopped
    resp = await client.post(f"/api/v1/strategies/{sid}/stop", headers=headers)
    assert resp.status_code == 400
    assert resp.json()["code"] == 4003


@pytest.mark.asyncio
async def test_error_to_stopped(client):
    """error  stop """
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(f"errstop_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "test",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
        },
    )
    sid = create_resp.json()["data"]["id"]

    #  DB  error 
    db = await get_shared_db()
    await db.execute(
        "UPDATE strategies SET status='error' WHERE id=?", (sid,)
    )
    await db.commit()

    # error  stopped
    resp = await client.post(f"/api/v1/strategies/{sid}/stop", headers=headers)
    assert resp.json()["code"] == 0
    assert resp.json()["data"]["status"] == "stopped"


# 
# 10.  API 
# 

@pytest.mark.asyncio
async def test_amount_conversion_create(client):
    """"""
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(f"conv_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "conv_test",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 99.99,
            "stop_loss": 500.0,
            "take_profit": 200.0,
        },
    )
    data = resp.json()["data"]
    assert data["base_amount"] == 99.99
    assert data["stop_loss"] == 500.0
    assert data["take_profit"] == 200.0

    #  DB 
    db = await get_shared_db()
    row = await (await db.execute(
        "SELECT base_amount, stop_loss, take_profit FROM strategies WHERE id=?",
        (data["id"],)
    )).fetchone()
    assert row["base_amount"] == 9999  # 99.99  = 9999 
    assert row["stop_loss"] == 50000   # 500.0  = 50000 
    assert row["take_profit"] == 20000  # 200.0  = 20000 


# 
# 11. 
# 

@pytest.mark.asyncio
async def test_data_isolation_list(client):
    """ A  B """
    uid = _uid()
    token_a, _, acc_a = await _create_operator_with_account(f"isoa_{uid}")
    token_b, _, acc_b = await _create_operator_with_account(f"isob_{uid}")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # A 
    await client.post(
        "/api/v1/strategies",
        headers=headers_a,
        json={
            "account_id": acc_a,
            "name": "A",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
        },
    )

    # B 
    await client.post(
        "/api/v1/strategies",
        headers=headers_b,
        json={
            "account_id": acc_b,
            "name": "B",
            "type": "flat",
            "play_code": "DX2",
            "base_amount": 20.0,
        },
    )

    # A 
    resp_a = await client.get("/api/v1/strategies", headers=headers_a)
    assert len(resp_a.json()["data"]) == 1
    assert resp_a.json()["data"][0]["name"] == "A"

    # B 
    resp_b = await client.get("/api/v1/strategies", headers=headers_b)
    assert len(resp_b.json()["data"]) == 1
    assert resp_b.json()["data"][0]["name"] == "B"


@pytest.mark.asyncio
async def test_data_isolation_modify(client):
    """ A / B """
    uid = _uid()
    token_a, _, acc_a = await _create_operator_with_account(f"isomod_a_{uid}")
    token_b, _, acc_b = await _create_operator_with_account(f"isomod_b_{uid}")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # B 
    create_resp = await client.post(
        "/api/v1/strategies",
        headers=headers_b,
        json={
            "account_id": acc_b,
            "name": "B",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
        },
    )
    b_sid = create_resp.json()["data"]["id"]

    # A  B   404
    resp = await client.put(
        f"/api/v1/strategies/{b_sid}",
        headers=headers_a,
        json={"name": "hijack"},
    )
    assert resp.status_code == 404

    # A  B   404
    resp = await client.delete(f"/api/v1/strategies/{b_sid}", headers=headers_a)
    assert resp.status_code == 404

    # A  B   404
    resp = await client.post(f"/api/v1/strategies/{b_sid}/start", headers=headers_a)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_account_ownership_cross_operator(client):
    """"""
    uid = _uid()
    token_a, _, acc_a = await _create_operator_with_account(f"own_a_{uid}")
    token_b, _, acc_b = await _create_operator_with_account(f"own_b_{uid}")
    headers_a = {"Authorization": f"Bearer {token_a}"}

    # A  B   404
    resp = await client.post(
        "/api/v1/strategies",
        headers=headers_a,
        json={
            "account_id": acc_b,
            "name": "test",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
        },
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == 4001


# 
# 12. 
# 

@pytest.mark.asyncio
async def test_no_auth_returns_401(client):
    """ 401"""
    resp = await client.get("/api/v1/strategies")
    assert resp.status_code == 401
    assert resp.json()["code"] == 2002
