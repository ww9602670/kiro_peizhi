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
import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.database import get_shared_db
from app.models.db_ops import (
    account_create,
    account_verification_run_complete,
    account_verification_run_create,
    operator_create,
)
from app.schemas.strategy import validate_state_transition
from app.utils.auth import create_token, register_session, persist_jti


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _create_operator_with_account(
    username: str,
    max_accounts: int = 3,
    game_type: str = "JND28",
    platform_url: str | None = None,
    create_effective_verification: bool = True,
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
        game_type=game_type,
        platform_url=platform_url,
    )
    if create_effective_verification:
        run = await account_verification_run_create(
            db,
            account_id=acc["id"],
            snapshot_game_type=game_type,
            snapshot_platform_url=platform_url,
            snapshot_password_hash=hashlib.sha256("accpass".encode("utf-8")).hexdigest(),
        )
        supported_platforms = ["LUCKYSB"] if game_type == "LUCKYSB" else ["JND28WEB", "JND282"]
        await account_verification_run_complete(
            db,
            verification_run_id=run["id"],
            capabilities=[
                {
                    "platform_type": platform_type,
                    "verify_status": "supported",
                    "market_state": "open",
                    "detected_issue": None,
                    "last_verified_at": "2026-04-19 00:00:00",
                    "odds_synced": True,
                    "odds_count": 1,
                    "odds_message": "ok",
                    "last_error": None,
                }
                for platform_type in supported_platforms
            ],
        )
    # Set account to online so strategy start works
    await db.execute(
        "UPDATE gambling_accounts SET status='online' WHERE id=?", (acc["id"],)
    )
    await db.commit()
    return token, op["id"], acc["id"]


@pytest.fixture(autouse=True)
def mock_engine():
    """Mock app.state.engine for strategy state transition tests."""
    engine = MagicMock()
    engine.start_worker = AsyncMock()
    engine.stop_worker = AsyncMock()
    app.state.engine = engine
    yield engine
    app.state.engine = None


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

    def test_red_wave_double_requires_sequence(self):
        from app.schemas.strategy import StrategyCreate
        with pytest.raises(Exception):
            StrategyCreate(
                account_id=1, name="red", type="red_wave_double_martin",
                play_code="DS4", base_amount=10.0,
                martin_sequence=None,
            )

    def test_red_wave_double_valid(self):
        from app.schemas.strategy import StrategyCreate
        s = StrategyCreate(
            account_id=1, name="red", type="red_wave_double_martin",
            play_code="DS4,B1LM_S,DS4", base_amount=10.0,
            martin_sequence=[1, 2, 4],
        )
        assert s.martin_sequence == [1, 2, 4]
        assert s.play_code == "B1LM_S,DS4"

    def test_red_wave_double_invalid_play_code(self):
        from app.schemas.strategy import StrategyCreate
        with pytest.raises(Exception):
            StrategyCreate(
                account_id=1, name="red", type="red_wave_double_martin",
                play_code="DX1", base_amount=10.0,
                martin_sequence=[1, 2, 4],
            )

    def test_red_wave_double_empty_play_code(self):
        from app.schemas.strategy import StrategyCreate
        with pytest.raises(Exception):
            StrategyCreate(
                account_id=1, name="red", type="red_wave_double_martin",
                play_code=" , ", base_amount=10.0,
                martin_sequence=[1, 2, 4],
            )

    def test_green_wave_single_valid(self):
        from app.schemas.strategy import StrategyCreate
        s = StrategyCreate(
            account_id=1, name="green", type="green_wave_single_martin",
            play_code="DS3,B1LM_D,DS3", base_amount=10.0,
            martin_sequence=[1, 2, 4],
        )
        assert s.martin_sequence == [1, 2, 4]
        assert s.play_code == "B1LM_D,DS3"


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


@pytest.mark.asyncio
async def test_create_red_wave_double_multi_directions(client):
    uid = _uid()
    token, op_id, acc_id = await _create_operator_with_account(f"red_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "red_double",
            "type": "red_wave_double_martin",
            "play_code": "DS4,B1LM_S,DS4",
            "base_amount": 5.0,
            "martin_sequence": [1, 2, 4],
            "simulation": True,
            "stop_loss": 100.0,
            "take_profit": 50.0,
        },
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["type"] == "red_wave_double_martin"
    assert data["play_code"] == "B1LM_S,DS4"
    assert data["martin_sequence"] == [1, 2, 4]
    assert data["simulation"] is True
    assert data["stop_loss"] == 100.0
    assert data["take_profit"] == 50.0


@pytest.mark.asyncio
async def test_create_red_wave_double_rejects_invalid_direction(client):
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(f"red_bad_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "red_double_bad",
            "type": "red_wave_double_martin",
            "play_code": "DX1",
            "base_amount": 5.0,
            "martin_sequence": [1, 2, 4],
        },
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == 1001


@pytest.mark.asyncio
async def test_create_green_wave_single_multi_directions(client):
    uid = _uid()
    token, op_id, acc_id = await _create_operator_with_account(f"green_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "green_single",
            "type": "green_wave_single_martin",
            "play_code": "DS3,B1LM_D,DS3",
            "base_amount": 5.0,
            "martin_sequence": [1, 2, 4],
            "simulation": True,
            "stop_loss": 100.0,
            "take_profit": 50.0,
        },
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["type"] == "green_wave_single_martin"
    assert data["play_code"] == "B1LM_D,DS3"
    assert data["martin_sequence"] == [1, 2, 4]
    assert data["simulation"] is True
    assert data["stop_loss"] == 100.0
    assert data["take_profit"] == 50.0


@pytest.mark.asyncio
async def test_create_luckysb_flat_strategy(client):
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(
        f"luckysb_flat_{uid}",
        game_type="LUCKYSB",
        platform_url="https://member.example.com",
    )
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "lucky_flat",
            "type": "flat",
            "play_code": "LUCKYSB_B1_01",
            "base_amount": 5.0,
            "platform_type": "LUCKYSB",
        },
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["platform_type"] == "LUCKYSB"
    assert data["play_code"] == "LUCKYSB_B1_01"
    assert data["play_code_name"] == "冠军 01"


@pytest.mark.asyncio
async def test_luckysb_rejects_red_wave_strategy(client):
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(
        f"luckysb_red_{uid}",
        game_type="LUCKYSB",
        platform_url="https://member.example.com",
    )
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "lucky_red",
            "type": "red_wave_double_martin",
            "play_code": "DS4",
            "base_amount": 5.0,
            "martin_sequence": [1, 2, 4],
            "platform_type": "LUCKYSB",
        },
    )

    assert resp.status_code == 400
    assert resp.json()["code"] == 1002


@pytest.mark.asyncio
async def test_luckysb_account_rejects_platform_type_mismatch(client):
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(
        f"luckysb_mismatch_{uid}",
        game_type="LUCKYSB",
        platform_url="https://member.example.com",
    )
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "lucky_wrong_platform",
            "type": "red_wave_double_martin",
            "play_code": "DS4",
            "base_amount": 5.0,
            "martin_sequence": [1, 2, 4],
            "platform_type": "JND28WEB",
        },
    )

    assert resp.status_code == 400
    assert resp.json()["code"] == 1002
    assert "platform_type" in resp.json()["message"]


@pytest.mark.asyncio
async def test_luckysb_rejects_multi_play_code(client):
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(
        f"luckysb_multi_{uid}",
        game_type="LUCKYSB",
        platform_url="https://member.example.com",
    )
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "lucky_multi",
            "type": "flat",
            "play_code": "LUCKYSB_B1_01,LUCKYSB_B2_02",
            "base_amount": 5.0,
            "platform_type": "LUCKYSB",
        },
    )

    assert resp.status_code == 400
    assert resp.json()["code"] == 1002


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
async def test_create_strategy_rejected_without_effective_verification_run(client):
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(
        f"noverify_{uid}",
        create_effective_verification=False,
    )
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "no_verify",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
        },
    )

    assert resp.status_code == 400
    assert resp.json()["code"] == 1002
    assert "effective_verification_run_id" in resp.json()["message"]


@pytest.mark.asyncio
async def test_create_strategy_rejected_when_verification_stale(client):
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(f"create_stale_{uid}")
    headers = {"Authorization": f"Bearer {token}"}
    db = await get_shared_db()
    await db.execute(
        "UPDATE account_verification_runs SET stale=1, stale_reason='test_stale' WHERE account_id=?",
        (acc_id,),
    )
    await db.commit()

    resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "stale_create",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
        },
    )

    assert resp.status_code == 400
    assert resp.json()["code"] == 1002
    assert "verification_stale" in resp.json()["message"]


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
                "play_code": "DX1" if i == 0 else "DX2",
                "base_amount": 10.0,
            },
        )

    resp = await client.get("/api/v1/strategies", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["code"] == 0
    assert len(resp.json()["data"]) == 2


@pytest.mark.asyncio
async def test_create_strategy_auto_adjusts_same_direction_timing_for_non_dw3(client):
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(f"conflict_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    first_resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "first",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
            "bet_timing": 30,
        },
    )
    assert first_resp.status_code == 200
    first_id = first_resp.json()["data"]["id"]

    db = await get_shared_db()
    await db.execute("UPDATE strategies SET status='running' WHERE id=?", (first_id,))
    await db.commit()

    resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "second",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
            "bet_timing": 45,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["bet_timing"] == 50


@pytest.mark.asyncio
async def test_create_strategy_rejects_when_no_valid_non_dw3_timing_slot(client):
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(f"no_slot_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    db = await get_shared_db()
    occupied_timings = [19, 39, 59, 79, 99, 119, 139, 159, 179]
    for idx, timing in enumerate(occupied_timings):
        create_resp = await client.post(
            "/api/v1/strategies",
            headers=headers,
            json={
                "account_id": acc_id,
                "name": f"occupied_{idx}",
                "type": "flat",
                "play_code": "DX1",
                "base_amount": 10.0,
                "bet_timing": timing,
            },
        )
        assert create_resp.status_code == 200
        strategy_id = create_resp.json()["data"]["id"]
        await db.execute("UPDATE strategies SET status='running' WHERE id=?", (strategy_id,))
    await db.commit()

    resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "candidate",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
            "bet_timing": 60,
        },
    )

    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == 1003
    assert body["data"]["reason"] == "NO_AVAILABLE_BET_TIMING"
    assert body["data"]["bet_timing_window"] == {"min": 19, "max": 180}


@pytest.mark.asyncio
async def test_dw3_save_keeps_requested_timing_under_policy_a(client):
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(f"dw3_policy_a_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    first_resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "dw3_first",
            "type": "flat",
            "play_code": "DW3_BS_BBB,DW3_OE_OOO",
            "gate_window_issues": 3,
            "base_amount": 10.0,
            "bet_timing": 30,
        },
    )
    assert first_resp.status_code == 200
    first_id = first_resp.json()["data"]["id"]

    db = await get_shared_db()
    await db.execute("UPDATE strategies SET status='running' WHERE id=?", (first_id,))
    await db.commit()

    second_resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "dw3_second",
            "type": "flat",
            "play_code": "DW3_BS_BBB,DW3_OE_OOO",
            "gate_window_issues": 3,
            "base_amount": 10.0,
            "bet_timing": 35,
        },
    )

    assert second_resp.status_code == 200
    assert second_resp.json()["data"]["bet_timing"] == 35


@pytest.mark.asyncio
async def test_start_strategy_allows_minimum_20s_gap(client, mock_engine):
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(f"start_conflict_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    first_resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "first",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
            "bet_timing": 30,
        },
    )
    second_resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "second",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
            "bet_timing": 50,
        },
    )
    first_id = first_resp.json()["data"]["id"]
    second_id = second_resp.json()["data"]["id"]

    start_first = await client.post(f"/api/v1/strategies/{first_id}/start", headers=headers)
    assert start_first.status_code == 200

    mock_engine.start_worker.reset_mock()
    start_second = await client.post(f"/api/v1/strategies/{second_id}/start", headers=headers)

    assert start_second.status_code == 200
    assert start_second.json()["code"] == 0
    mock_engine.start_worker.assert_called_once()


@pytest.mark.asyncio
async def test_start_strategy_auto_adjusts_same_direction_timing(client, mock_engine):
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(f"start_adjust_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    first_resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "first",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
            "bet_timing": 30,
        },
    )
    second_resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "second",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
            "bet_timing": 45,
        },
    )
    first_id = first_resp.json()["data"]["id"]
    second_id = second_resp.json()["data"]["id"]

    start_first = await client.post(f"/api/v1/strategies/{first_id}/start", headers=headers)
    assert start_first.status_code == 200

    mock_engine.start_worker.reset_mock()
    start_second = await client.post(f"/api/v1/strategies/{second_id}/start", headers=headers)

    assert start_second.status_code == 200
    assert start_second.json()["data"]["bet_timing"] == 50
    mock_engine.start_worker.assert_called_once()


@pytest.mark.asyncio
async def test_start_strategy_rejected_without_effective_verification_run(client, mock_engine):
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(f"start_noverify_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "start_no_verify",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
        },
    )
    sid = create_resp.json()["data"]["id"]

    db = await get_shared_db()
    await db.execute("DELETE FROM account_verification_runs WHERE account_id=?", (acc_id,))
    await db.commit()

    resp = await client.post(f"/api/v1/strategies/{sid}/start", headers=headers)
    assert resp.status_code == 400
    assert resp.json()["code"] == 4002
    assert "effective_verification_run_id" in resp.json()["message"]
    mock_engine.start_worker.assert_not_called()
    list_resp = await client.get("/api/v1/strategies", headers=headers)
    strategy = next(item for item in list_resp.json()["data"] if item["id"] == sid)
    assert strategy["status"] == "stopped"


@pytest.mark.asyncio
async def test_start_strategy_rejected_when_verification_stale(client, mock_engine):
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(f"start_stale_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "start_stale",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
        },
    )
    sid = create_resp.json()["data"]["id"]

    db = await get_shared_db()
    await db.execute(
        "UPDATE account_verification_runs SET stale=1, stale_reason='test_stale' WHERE account_id=?",
        (acc_id,),
    )
    await db.commit()

    resp = await client.post(f"/api/v1/strategies/{sid}/start", headers=headers)
    assert resp.status_code == 400
    assert resp.json()["code"] == 4002
    assert "verification_stale" in resp.json()["message"]
    mock_engine.start_worker.assert_not_called()
    list_resp = await client.get("/api/v1/strategies", headers=headers)
    strategy = next(item for item in list_resp.json()["data"] if item["id"] == sid)
    assert strategy["status"] == "stopped"


@pytest.mark.asyncio
async def test_start_strategy_keeps_stopped_when_worker_start_fails(client, mock_engine):
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(f"start_fail_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "start_fail",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
            "bet_timing": 30,
        },
    )
    sid = create_resp.json()["data"]["id"]
    mock_engine.start_worker.side_effect = RuntimeError("startup failed")

    resp = await client.post(f"/api/v1/strategies/{sid}/start", headers=headers)
    assert resp.status_code == 500

    list_resp = await client.get("/api/v1/strategies", headers=headers)
    strategy = next(item for item in list_resp.json()["data"] if item["id"] == sid)
    assert strategy["status"] == "stopped"


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
async def test_update_red_wave_strategy_play_code(client):
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(f"updred_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "upd_red_orig",
            "type": "red_wave_double_martin",
            "play_code": "DS4",
            "base_amount": 10.0,
            "martin_sequence": [1, 2, 4],
        },
    )
    sid = create_resp.json()["data"]["id"]

    resp = await client.put(
        f"/api/v1/strategies/{sid}",
        headers=headers,
        json={"play_code": "B2LM_S,DS4"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["play_code"] == "B2LM_S,DS4"


@pytest.mark.asyncio
async def test_update_green_wave_strategy_play_code(client):
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(f"updgreen_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "upd_green_orig",
            "type": "green_wave_single_martin",
            "play_code": "DS3",
            "base_amount": 10.0,
            "martin_sequence": [1, 2, 4],
        },
    )
    sid = create_resp.json()["data"]["id"]

    resp = await client.put(
        f"/api/v1/strategies/{sid}",
        headers=headers,
        json={"play_code": "B2LM_D,DS3"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["play_code"] == "B2LM_D,DS3"


@pytest.mark.asyncio
async def test_update_luckysb_strategy_play_code(client):
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(
        f"updlucky_{uid}",
        game_type="LUCKYSB",
        platform_url="https://member.example.com",
    )
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "upd_lucky_orig",
            "type": "flat",
            "play_code": "LUCKYSB_B1_01",
            "base_amount": 10.0,
            "platform_type": "LUCKYSB",
        },
    )
    sid = create_resp.json()["data"]["id"]

    resp = await client.put(
        f"/api/v1/strategies/{sid}",
        headers=headers,
        json={"play_code": "LUCKYSB_B2_10"},
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["play_code"] == "LUCKYSB_B2_10"
    assert resp.json()["data"]["play_code_name"] == "亚军 10"


@pytest.mark.asyncio
async def test_update_luckysb_strategy_rejects_platform_type_mismatch(client):
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(
        f"updlucky_platform_{uid}",
        game_type="LUCKYSB",
        platform_url="https://member.example.com",
    )
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "upd_lucky_platform",
            "type": "flat",
            "play_code": "LUCKYSB_B1_01",
            "base_amount": 10.0,
            "platform_type": "LUCKYSB",
        },
    )
    sid = create_resp.json()["data"]["id"]

    resp = await client.put(
        f"/api/v1/strategies/{sid}",
        headers=headers,
        json={"platform_type": "JND28WEB"},
    )

    assert resp.status_code == 400
    assert resp.json()["code"] == 1002
    assert "platform_type" in resp.json()["message"]


@pytest.mark.asyncio
async def test_update_non_red_wave_play_code_rejected(client):
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(f"updflatpc_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "flat_orig",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
        },
    )
    sid = create_resp.json()["data"]["id"]

    resp = await client.put(
        f"/api/v1/strategies/{sid}",
        headers=headers,
        json={"play_code": "DX2"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == 1002


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


def test_schema_rejects_dw3_without_gate_window_issues():
    from app.schemas.strategy import StrategyCreate

    with pytest.raises(Exception):
        StrategyCreate(
            account_id=1,
            name="dw3_without_gate",
            type="flat",
            play_code="DW3_BS_BBB,DW3_OE_OOO",
            base_amount=10.0,
        )


@pytest.mark.asyncio
async def test_create_dw3_strategy_persists_gate_window_issues(client):
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(f"dw3create_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "dw3_flat",
            "type": "flat",
            "play_code": "DW3_BS_BBB,DW3_OE_OOO",
            "gate_window_issues": 3,
            "base_amount": 10.0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["play_code"] == "DW3_BS_BBB,DW3_OE_OOO"
    assert body["data"]["gate_window_issues"] == 3

    db = await get_shared_db()
    row = await (
        await db.execute(
            "SELECT gate_window_issues FROM strategies WHERE id=?",
            (body["data"]["id"],),
        )
    ).fetchone()
    assert row["gate_window_issues"] == 3


@pytest.mark.asyncio
async def test_update_dw3_flat_strategy_play_code_allowed(client):
    uid = _uid()
    token, _, acc_id = await _create_operator_with_account(f"dw3update_{uid}")
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": acc_id,
            "name": "dw3_flat_update",
            "type": "flat",
            "play_code": "DW3_BS_BBB,DW3_OE_OOO",
            "gate_window_issues": 2,
            "base_amount": 10.0,
        },
    )
    assert create_resp.status_code == 200
    strategy_id = create_resp.json()["data"]["id"]

    update_resp = await client.put(
        f"/api/v1/strategies/{strategy_id}",
        headers=headers,
        json={
            "play_code": "DW3_BS_SSS,DW3_OE_EEE",
            "gate_window_issues": 5,
        },
    )
    assert update_resp.status_code == 200
    data = update_resp.json()["data"]
    assert data["play_code"] == "DW3_BS_SSS,DW3_OE_EEE"
    assert data["gate_window_issues"] == 5


@pytest.mark.asyncio
async def test_strategies_table_has_gate_window_issues_column():
    db = await get_shared_db()
    rows = await (await db.execute("PRAGMA table_info(strategies)")).fetchall()
    column_names = {row["name"] for row in rows}
    assert "gate_window_issues" in column_names


@pytest.mark.asyncio
async def test_list_strategies_reads_simulation_pnl_from_stats(client):
    from datetime import datetime, timedelta, timezone

    uid = _uid()
    token, operator_id, account_id = await _create_operator_with_account(f"sim_pnl_{uid}")
    headers = {"Authorization": f"Bearer {token}"}
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

    real_resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": account_id,
            "name": "real",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
            "simulation": False,
        },
    )
    sim_resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": account_id,
            "name": "sim",
            "type": "flat",
            "play_code": "DX2",
            "base_amount": 10.0,
            "simulation": True,
        },
    )
    real_id = real_resp.json()["data"]["id"]
    sim_id = sim_resp.json()["data"]["id"]

    db = await get_shared_db()
    await db.execute(
        "UPDATE strategies SET daily_pnl=?, total_pnl=?, daily_pnl_date=? WHERE id=?",
        (1200, 3000, today, real_id),
    )
    await db.execute(
        "UPDATE strategies SET daily_pnl=?, total_pnl=?, daily_pnl_date=? WHERE id=?",
        (9999, 8888, today, sim_id),
    )
    await db.execute(
        """INSERT INTO simulation_strategy_stats
           (strategy_id, operator_id, account_id, daily_pnl, total_pnl, daily_pnl_date, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, datetime('now', '+8 hours'), datetime('now', '+8 hours'))""",
        (sim_id, operator_id, account_id, 2500, 4500, today),
    )
    await db.commit()

    list_resp = await client.get("/api/v1/strategies", headers=headers)
    assert list_resp.status_code == 200
    data = list_resp.json()["data"]
    by_id = {item["id"]: item for item in data}

    assert by_id[real_id]["daily_pnl"] == 12.0
    assert by_id[real_id]["total_pnl"] == 30.0
    assert by_id[sim_id]["daily_pnl"] == 25.0
    assert by_id[sim_id]["total_pnl"] == 45.0


@pytest.mark.asyncio
async def test_list_strategies_simulation_pnl_falls_back_to_zero_without_stats(client):
    from datetime import datetime, timedelta, timezone

    uid = _uid()
    token, _, account_id = await _create_operator_with_account(f"sim_zero_{uid}")
    headers = {"Authorization": f"Bearer {token}"}
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

    create_resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "account_id": account_id,
            "name": "sim_no_stats",
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 10.0,
            "simulation": True,
        },
    )
    strategy_id = create_resp.json()["data"]["id"]

    db = await get_shared_db()
    await db.execute(
        "UPDATE strategies SET daily_pnl=?, total_pnl=?, daily_pnl_date=? WHERE id=?",
        (5000, 7000, today, strategy_id),
    )
    await db.commit()

    list_resp = await client.get("/api/v1/strategies", headers=headers)
    assert list_resp.status_code == 200
    item = next(x for x in list_resp.json()["data"] if x["id"] == strategy_id)
    assert item["daily_pnl"] == 0.0
    assert item["total_pnl"] == 0.0
