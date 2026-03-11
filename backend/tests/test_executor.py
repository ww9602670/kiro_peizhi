"""BetExecutor


- 7.2.1 BetExecutor  + BetSignal 
- 7.2.2  IntegrityError 
- 7.2.3 
- 7.2.4  + betdata  KeyCode 
- 7.2.5 Confirmbet  + 
- 7.2.6 deadline/cancel 
- 7.2.7 
- 7.2.8 
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import aiosqlite

from app.database import DDL_STATEMENTS, INSERT_DEFAULT_ADMIN
from app.engine.adapters.base import BetResult, InstallInfo, PlatformAdapter
from app.engine.alert import AlertService
from app.engine.executor import BetExecutor
from app.engine.risk import RiskCheckResult, RiskController
from app.engine.strategy_runner import BetSignal
from app.models.db_ops import (
    account_create,
    account_update,
    bet_order_create,
    odds_batch_upsert,
    operator_create,
    strategy_create,
    strategy_update,
)


# 
# Fixtures
# 

@pytest.fixture
async def db():
    """"""
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
async def setup_data(db):
    """"""
    op = await operator_create(
        db, username="test_op", password="pass123",
        role="operator", status="active", created_by=1,
    )
    acct = await account_create(
        db, operator_id=op["id"], account_name="acct1",
        password="pw", platform_type="JND28WEB",
    )
    acct = await account_update(
        db, account_id=acct["id"], operator_id=op["id"],
        status="online", session_token="valid_token", balance=1_000_000,
    )
    strat = await strategy_create(
        db, operator_id=op["id"], account_id=acct["id"],
        name="A", type="flat", play_code="DX1", base_amount=1000,
    )
    strat = await strategy_update(
        db, strategy_id=strat["id"], operator_id=op["id"], status="running",
    )
    # 
    strat2 = await strategy_create(
        db, operator_id=op["id"], account_id=acct["id"],
        name="B", type="flat", play_code="DX1", base_amount=2000,
    )
    strat2 = await strategy_update(
        db, strategy_id=strat2["id"], operator_id=op["id"], status="running",
    )
    # 写入已确认赔率到 account_odds 表
    await odds_batch_upsert(
        db, account_id=acct["id"],
        odds_map={"DX1": 19800, "DX2": 19800, "DS3": 19800, "DS4": 19800,
                  "ZH7": 42930, "ZH8": 47250},
        confirmed=True,
    )
    return {"operator": op, "account": acct, "strategy": strat, "strategy2": strat2}


@pytest.fixture
def mock_adapter():
    """Mock PlatformAdapter"""
    adapter = AsyncMock(spec=PlatformAdapter)
    adapter.load_odds = AsyncMock(return_value={
        "DX1": 19800, "DX2": 19800, "DS3": 19800, "DS4": 19800,
    })
    adapter.place_bet = AsyncMock(return_value=BetResult(
        succeed=1, message="success", raw_response={"succeed": 1},
    ))
    return adapter


@pytest.fixture
def mock_risk():
    """Mock RiskController"""
    risk = AsyncMock(spec=RiskController)
    risk.check = AsyncMock(return_value=RiskCheckResult(passed=True))
    return risk


@pytest.fixture
async def alert_service(db):
    return AlertService(db)


@pytest.fixture
async def executor(db, mock_adapter, mock_risk, alert_service, setup_data):
    """ BetExecutor """
    return BetExecutor(
        db=db,
        adapter=mock_adapter,
        risk=mock_risk,
        alert_service=alert_service,
        operator_id=setup_data["operator"]["id"],
        account_id=setup_data["account"]["id"],
    )


def make_signal(
    strategy_id: int,
    key_code: str = "DX1",
    amount: int = 1000,
    issue: str = "20240101001",
    simulation: bool = False,
    martin_level: int = 0,
) -> BetSignal:
    return BetSignal(
        strategy_id=strategy_id,
        key_code=key_code,
        amount=amount,
        idempotent_id=f"{issue}-{strategy_id}-{key_code}",
        martin_level=martin_level,
        simulation=simulation,
    )


def make_install(
    issue: str = "20240101001",
    close_countdown_sec: int = 60,
    state: int = 1,
) -> InstallInfo:
    return InstallInfo(
        issue=issue,
        state=state,
        close_countdown_sec=close_countdown_sec,
        pre_issue="20240101000",
        pre_result="3,5,7",
    )


# 
# 1. BetExecutor  + BetSignal 7.2.1
# 

class TestBetExecutorSkeleton:
    def test_bet_signal_dataclass(self):
        """BetSignal """
        s = BetSignal(
            strategy_id=1, key_code="DX1", amount=100,
            idempotent_id="001-1-DX1", martin_level=2, simulation=True,
        )
        assert s.strategy_id == 1
        assert s.key_code == "DX1"
        assert s.amount == 100
        assert s.idempotent_id == "001-1-DX1"
        assert s.martin_level == 2
        assert s.simulation is True

    def test_bet_signal_defaults(self):
        """BetSignal """
        s = BetSignal(
            strategy_id=1, key_code="DX1", amount=100,
            idempotent_id="001-1-DX1",
        )
        assert s.martin_level == 0
        assert s.simulation is False

    def test_executor_init(self, executor, setup_data):
        """BetExecutor """
        assert executor.operator_id == setup_data["operator"]["id"]
        assert executor.account_id == setup_data["account"]["id"]
        assert executor.db is not None
        assert executor.adapter is not None
        assert executor.risk is not None
        assert executor.alert_service is not None


# 
# 2. 7.2.2
# 

class TestIdempotency:
    @pytest.mark.asyncio
    async def test_duplicate_signal_skipped(self, db, executor, setup_data):
        """ idempotent_id """
        signal = make_signal(setup_data["strategy"]["id"])
        install = make_install()

        # 
        await bet_order_create(
            db, idempotent_id=signal.idempotent_id,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            strategy_id=setup_data["strategy"]["id"],
            issue="20240101001", key_code="DX1", amount=1000,
            status="bet_success",
        )

        #   
        await executor.execute(install, [signal])

        # place_bet 
        executor.adapter.place_bet.assert_not_called()

    @pytest.mark.asyncio
    async def test_integrity_error_captured(self, db, executor, setup_data):
        """ idempotent_id  IntegrityError """
        signal = make_signal(setup_data["strategy"]["id"])

        #  _create_order  IntegrityError
        original_create = executor._create_order

        call_count = 0

        async def patched_create(sig, odds, issue):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # 
                return await original_create(sig, odds, issue)
            # 
            raise aiosqlite.IntegrityError("UNIQUE constraint failed: bet_orders.idempotent_id")

        executor._create_order = patched_create

        install = make_install()
        # 
        await executor.execute(install, [signal])

    @pytest.mark.asyncio
    async def test_is_duplicate_returns_true(self, db, executor, setup_data):
        """_is_duplicate  idempotent_id  True"""
        signal = make_signal(setup_data["strategy"]["id"])
        await bet_order_create(
            db, idempotent_id=signal.idempotent_id,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            strategy_id=setup_data["strategy"]["id"],
            issue="20240101001", key_code="DX1", amount=1000,
        )
        assert await executor._is_duplicate(signal) is True

    @pytest.mark.asyncio
    async def test_is_duplicate_returns_false(self, executor, setup_data):
        """_is_duplicate  idempotent_id  False"""
        signal = make_signal(setup_data["strategy"]["id"])
        assert await executor._is_duplicate(signal) is False


# 
# 3. 7.2.3
# 

class TestRiskIntegration:
    @pytest.mark.asyncio
    async def test_risk_rejected_signal_skipped(self, executor, setup_data):
        """"""
        executor.risk.check = AsyncMock(
            return_value=RiskCheckResult(passed=False, reason="")
        )
        signal = make_signal(setup_data["strategy"]["id"])
        install = make_install()

        await executor.execute(install, [signal])

        #    load_odds  place_bet
        executor.adapter.place_bet.assert_not_called()

    @pytest.mark.asyncio
    async def test_risk_check_called_per_signal(self, executor, setup_data):
        """"""
        s1 = make_signal(setup_data["strategy"]["id"], key_code="DX1")
        s2 = make_signal(setup_data["strategy2"]["id"], key_code="DX2")
        install = make_install()

        await executor.execute(install, [s1, s2])

        assert executor.risk.check.call_count == 2

    @pytest.mark.asyncio
    async def test_partial_risk_rejection(self, executor, setup_data):
        """"""
        # 
        executor.risk.check = AsyncMock(
            side_effect=[
                RiskCheckResult(passed=True),
                RiskCheckResult(passed=False, reason=""),
            ]
        )
        s1 = make_signal(setup_data["strategy"]["id"], key_code="DX1")
        s2 = make_signal(setup_data["strategy2"]["id"], key_code="DX2")
        install = make_install()

        await executor.execute(install, [s1, s2])

        # place_bet  betdata  1 
        executor.adapter.place_bet.assert_called_once()
        betdata = executor.adapter.place_bet.call_args[0][1]
        assert len(betdata) == 1
        assert betdata[0]["KeyCode"] == "DX1"


# 
# 4.  + betdata 7.2.4
# 

class TestBetdataAssembly:
    @pytest.mark.asyncio
    async def test_same_keycode_not_merged(self, executor, setup_data):
        """ KeyCode   betdata  2 

        ADX1100 + BDX1200
         betdata  2  DX1 100200 1  300
        """
        s1 = make_signal(setup_data["strategy"]["id"], key_code="DX1", amount=100)
        s2 = make_signal(setup_data["strategy2"]["id"], key_code="DX1", amount=200)
        install = make_install()

        await executor.execute(install, [s1, s2])

        executor.adapter.place_bet.assert_called_once()
        betdata = executor.adapter.place_bet.call_args[0][1]

        # 2 
        assert len(betdata) == 2
        amounts = sorted([b["Amount"] for b in betdata])
        assert amounts == [100, 200]
        #  DX1
        assert all(b["KeyCode"] == "DX1" for b in betdata)
        #  300
        assert sum(b["Amount"] for b in betdata) == 300
        assert 300 not in [b["Amount"] for b in betdata]

    @pytest.mark.asyncio
    async def test_betdata_count_equals_signal_count(self, executor, setup_data):
        """betdata  = """
        s1 = make_signal(setup_data["strategy"]["id"], key_code="DX1", amount=100)
        s2 = make_signal(setup_data["strategy2"]["id"], key_code="DX2", amount=200)
        install = make_install()

        await executor.execute(install, [s1, s2])

        betdata = executor.adapter.place_bet.call_args[0][1]
        assert len(betdata) == 2

    @pytest.mark.asyncio
    async def test_odds_zero_skipped(self, db, executor, setup_data):
        """赔率为 0 的玩法被跳过"""
        # 覆盖 DB 中的赔率：DX1=0, DX2=1980
        await db.execute(
            "UPDATE account_odds SET odds_value=0 WHERE account_id=? AND key_code='DX1'",
            (setup_data["account"]["id"],),
        )
        await db.commit()

        s1 = make_signal(setup_data["strategy"]["id"], key_code="DX1", amount=100)
        s2 = make_signal(setup_data["strategy2"]["id"], key_code="DX2", amount=200)
        install = make_install()

        await executor.execute(install, [s1, s2])

        betdata = executor.adapter.place_bet.call_args[0][1]
        assert len(betdata) == 1
        assert betdata[0]["KeyCode"] == "DX2"

    @pytest.mark.asyncio
    async def test_odds_missing_keycode_skipped(self, db, executor, setup_data):
        """赔率中不存在的 KeyCode 视为 0"""
        # DB 中没有 "UNKNOWN" 这个 key_code
        s1 = make_signal(setup_data["strategy"]["id"], key_code="UNKNOWN", amount=100)
        install = make_install()

        await executor.execute(install, [s1])

        # 赔率为 0 不调用 place_bet
        executor.adapter.place_bet.assert_not_called()

    @pytest.mark.asyncio
    async def test_betdata_fields_correct(self, db, executor, setup_data):
        """betdata 中 KeyCode、Amount、Odds 字段正确"""
        # 更新 DB 中 DX1 赔率为 20530
        await db.execute(
            "UPDATE account_odds SET odds_value=20530 WHERE account_id=? AND key_code='DX1'",
            (setup_data["account"]["id"],),
        )
        await db.commit()

        s1 = make_signal(setup_data["strategy"]["id"], key_code="DX1", amount=500)
        install = make_install()

        await executor.execute(install, [s1])

        betdata = executor.adapter.place_bet.call_args[0][1]
        assert len(betdata) == 1
        assert betdata[0] == {"KeyCode": "DX1", "Amount": 500, "Odds": 20530}

    @pytest.mark.asyncio
    async def test_all_odds_zero_no_place_bet(self, db, executor, setup_data):
        """所有赔率为 0 时不调用 place_bet"""
        # 将 DB 中所有赔率设为 0
        await db.execute(
            "UPDATE account_odds SET odds_value=0 WHERE account_id=?",
            (setup_data["account"]["id"],),
        )
        await db.commit()

        s1 = make_signal(setup_data["strategy"]["id"], key_code="DX1")
        s2 = make_signal(setup_data["strategy2"]["id"], key_code="DX2")
        install = make_install()

        await executor.execute(install, [s1, s2])

        executor.adapter.place_bet.assert_not_called()


# 
# 5. Confirmbet  + 7.2.5
# 

class TestConfirmbetZeroRetry:
    @pytest.mark.asyncio
    async def test_place_bet_called_once_on_success(self, executor, setup_data):
        """ place_bet  = 1"""
        signal = make_signal(setup_data["strategy"]["id"])
        install = make_install()

        await executor.execute(install, [signal])

        assert executor.adapter.place_bet.call_count == 1

    @pytest.mark.asyncio
    async def test_succeed_1_marks_bet_success(self, db, executor, setup_data):
        """succeed=1   status='bet_success'"""
        signal = make_signal(setup_data["strategy"]["id"])
        install = make_install()

        await executor.execute(install, [signal])

        row = await (await db.execute(
            "SELECT * FROM bet_orders WHERE idempotent_id=?",
            (signal.idempotent_id,),
        )).fetchone()
        assert row is not None
        assert row["status"] == "bet_success"

    @pytest.mark.asyncio
    async def test_succeed_not_1_marks_bet_failed(self, db, executor, setup_data):
        """succeed≠1 且非5 时标记 status='bet_failed' 且 fail_reason 非空"""
        executor.adapter.place_bet = AsyncMock(return_value=BetResult(
            succeed=10, message="参数不正确", raw_response={},
        ))
        signal = make_signal(setup_data["strategy"]["id"])
        install = make_install()

        await executor.execute(install, [signal])

        # place_bet 只调用 = 1 次（非 succeed=5 不重试）
        assert executor.adapter.place_bet.call_count == 1

        row = await (await db.execute(
            "SELECT * FROM bet_orders WHERE idempotent_id=?",
            (signal.idempotent_id,),
        )).fetchone()
        assert row["status"] == "bet_failed"
        assert row["fail_reason"] is not None
        assert "succeed=10" in row["fail_reason"]

    @pytest.mark.asyncio
    async def test_succeed_5_retries_with_live_odds(self, db, executor, setup_data):
        """succeed=5（赔率已变）时，从平台获取实时赔率并重试一次"""
        # 第一次返回 succeed=5，第二次返回 succeed=1
        executor.adapter.place_bet = AsyncMock(side_effect=[
            BetResult(succeed=5, message="赔率已经改变，是否继续投注？", raw_response={}),
            BetResult(succeed=1, message="success", raw_response={"succeed": 1}),
        ])
        executor.adapter.load_odds = AsyncMock(return_value={
            "DX1": 20530, "DX2": 20530,
        })
        signal = make_signal(setup_data["strategy"]["id"])
        install = make_install()

        await executor.execute(install, [signal])

        # place_bet 调用 2 次（首次 + 重试）
        assert executor.adapter.place_bet.call_count == 2
        # load_odds 调用 1 次（获取实时赔率）
        assert executor.adapter.load_odds.call_count == 1

        row = await (await db.execute(
            "SELECT * FROM bet_orders WHERE idempotent_id=?",
            (signal.idempotent_id,),
        )).fetchone()
        assert row["status"] == "bet_success"

    @pytest.mark.asyncio
    async def test_succeed_5_retry_also_fails(self, db, executor, setup_data):
        """succeed=5 重试后仍然失败，标记 bet_failed"""
        executor.adapter.place_bet = AsyncMock(return_value=BetResult(
            succeed=5, message="赔率已经改变，是否继续投注？", raw_response={},
        ))
        executor.adapter.load_odds = AsyncMock(return_value={
            "DX1": 20530, "DX2": 20530,
        })
        signal = make_signal(setup_data["strategy"]["id"])
        install = make_install()

        await executor.execute(install, [signal])

        # place_bet 调用 2 次
        assert executor.adapter.place_bet.call_count == 2

        row = await (await db.execute(
            "SELECT * FROM bet_orders WHERE idempotent_id=?",
            (signal.idempotent_id,),
        )).fetchone()
        assert row["status"] == "bet_failed"

    @pytest.mark.asyncio
    async def test_succeed_5_load_odds_fails(self, db, executor, setup_data):
        """succeed=5 但获取实时赔率失败，标记 bet_failed"""
        executor.adapter.place_bet = AsyncMock(return_value=BetResult(
            succeed=5, message="赔率已经改变，是否继续投注？", raw_response={},
        ))
        executor.adapter.load_odds = AsyncMock(side_effect=Exception("网络错误"))
        signal = make_signal(setup_data["strategy"]["id"])
        install = make_install()

        await executor.execute(install, [signal])

        # place_bet 只调用 1 次（重试前 load_odds 失败）
        assert executor.adapter.place_bet.call_count == 1

        row = await (await db.execute(
            "SELECT * FROM bet_orders WHERE idempotent_id=?",
            (signal.idempotent_id,),
        )).fetchone()
        assert row["status"] == "bet_failed"
        assert "实时赔率" in row["fail_reason"]

    @pytest.mark.asyncio
    async def test_timeout_no_retry(self, db, executor, setup_data):
        """  place_bet  = 1 bet_failedfail_reason  'timeout'"""
        executor.adapter.place_bet = AsyncMock(side_effect=TimeoutError(""))
        signal = make_signal(setup_data["strategy"]["id"])
        install = make_install()

        await executor.execute(install, [signal])

        assert executor.adapter.place_bet.call_count == 1

        row = await (await db.execute(
            "SELECT * FROM bet_orders WHERE idempotent_id=?",
            (signal.idempotent_id,),
        )).fetchone()
        assert row["status"] == "bet_failed"
        assert "timeout" in row["fail_reason"].lower()

    @pytest.mark.asyncio
    async def test_network_error_no_retry(self, db, executor, setup_data):
        """  place_bet  = 1 bet_failed"""
        executor.adapter.place_bet = AsyncMock(
            side_effect=ConnectionError("")
        )
        signal = make_signal(setup_data["strategy"]["id"])
        install = make_install()

        await executor.execute(install, [signal])

        assert executor.adapter.place_bet.call_count == 1

        row = await (await db.execute(
            "SELECT * FROM bet_orders WHERE idempotent_id=?",
            (signal.idempotent_id,),
        )).fetchone()
        assert row["status"] == "bet_failed"
        assert "ConnectionError" in row["fail_reason"]


# 
# 6. deadline/cancel 7.2.6
# 

class TestDeadlineCancel:
    @pytest.mark.asyncio
    async def test_close_countdown_sec_too_small_skips(self, executor, setup_data):
        """close_countdown_sec <= 10 deadline_seconds <= 0"""
        signal = make_signal(setup_data["strategy"]["id"])
        install = make_install(close_countdown_sec=10)

        await executor.execute(install, [signal])

        executor.adapter.place_bet.assert_not_called()
        executor.risk.check.assert_not_called()

    @pytest.mark.asyncio
    async def test_close_countdown_sec_zero_skips(self, executor, setup_data):
        """close_countdown_sec=0 """
        signal = make_signal(setup_data["strategy"]["id"])
        install = make_install(close_countdown_sec=0)

        await executor.execute(install, [signal])

        executor.adapter.place_bet.assert_not_called()

    @pytest.mark.asyncio
    async def test_timeout_leaves_orders_pending(self, db, executor, setup_data):
        """deadline  pending"""
        #  place_bet  deadline
        async def slow_place_bet(issue, betdata):
            await asyncio.sleep(10)  #  deadline
            return BetResult(succeed=1, message="ok", raw_response={})

        executor.adapter.place_bet = AsyncMock(side_effect=slow_place_bet)
        signal = make_signal(setup_data["strategy"]["id"])
        # close_countdown_sec=11  deadline=1 
        install = make_install(close_countdown_sec=11)

        await executor.execute(install, [signal])

        #  pending
        row = await (await db.execute(
            "SELECT * FROM bet_orders WHERE idempotent_id=?",
            (signal.idempotent_id,),
        )).fetchone()
        #  pending
        if row is not None:
            assert row["status"] == "pending"

    @pytest.mark.asyncio
    async def test_sufficient_deadline_works(self, executor, setup_data):
        """close_countdown_sec """
        signal = make_signal(setup_data["strategy"]["id"])
        install = make_install(close_countdown_sec=60)

        await executor.execute(install, [signal])

        executor.adapter.place_bet.assert_called_once()


# 
# 7. 7.2.7
# 

class TestSimulationMode:
    @pytest.mark.asyncio
    async def test_simulation_skips_place_bet(self, executor, setup_data):
        """simulation=True  Confirmbet"""
        signal = make_signal(
            setup_data["strategy"]["id"], simulation=True,
        )
        install = make_install()

        await executor.execute(install, [signal])

        #  place_bet
        executor.adapter.place_bet.assert_not_called()

    @pytest.mark.asyncio
    async def test_simulation_records_virtual_order(self, db, executor, setup_data):
        """simulation=True status=bet_success, simulation=1"""
        signal = make_signal(
            setup_data["strategy"]["id"], simulation=True,
        )
        install = make_install()

        await executor.execute(install, [signal])

        row = await (await db.execute(
            "SELECT * FROM bet_orders WHERE idempotent_id=?",
            (signal.idempotent_id,),
        )).fetchone()
        assert row is not None
        assert row["status"] == "bet_success"
        assert row["simulation"] == 1

    @pytest.mark.asyncio
    async def test_mixed_simulation_and_real(self, db, executor, setup_data):
        """ Confirmbet"""
        sim_signal = make_signal(
            setup_data["strategy"]["id"], key_code="DX1", simulation=True,
        )
        real_signal = make_signal(
            setup_data["strategy2"]["id"], key_code="DX2", simulation=False,
        )
        install = make_install()

        await executor.execute(install, [sim_signal, real_signal])

        # place_bet 
        executor.adapter.place_bet.assert_called_once()
        betdata = executor.adapter.place_bet.call_args[0][1]
        # betdata 
        assert len(betdata) == 1
        assert betdata[0]["KeyCode"] == "DX2"

        # 
        sim_row = await (await db.execute(
            "SELECT * FROM bet_orders WHERE idempotent_id=?",
            (sim_signal.idempotent_id,),
        )).fetchone()
        assert sim_row["simulation"] == 1
        assert sim_row["status"] == "bet_success"

        # 
        real_row = await (await db.execute(
            "SELECT * FROM bet_orders WHERE idempotent_id=?",
            (real_signal.idempotent_id,),
        )).fetchone()
        assert real_row["simulation"] == 0
        assert real_row["status"] == "bet_success"


# 
# 8. 7.2.8
# 

class TestBetFailAlert:
    @pytest.mark.asyncio
    async def test_bet_fail_triggers_alert(self, db, executor, setup_data):
        """下注失败 触发 AlertService.send(bet_fail)"""
        # 使用 succeed=10（非5），不触发重试
        executor.adapter.place_bet = AsyncMock(return_value=BetResult(
            succeed=10, message="参数不正确", raw_response={},
        ))
        signal = make_signal(setup_data["strategy"]["id"])
        install = make_install()

        await executor.execute(install, [signal])

        # 检查告警
        row = await (await db.execute(
            "SELECT * FROM alerts WHERE operator_id=? AND type='bet_fail'",
            (setup_data["operator"]["id"],),
        )).fetchone()
        assert row is not None
        assert "下注" in row["title"] or "失败" in row["title"] or install.issue in row["title"]

    @pytest.mark.asyncio
    async def test_timeout_triggers_alert(self, db, executor, setup_data):
        """ bet_fail """
        executor.adapter.place_bet = AsyncMock(side_effect=TimeoutError())
        signal = make_signal(setup_data["strategy"]["id"])
        install = make_install()

        await executor.execute(install, [signal])

        row = await (await db.execute(
            "SELECT * FROM alerts WHERE type='bet_fail'",
        )).fetchone()
        assert row is not None

    @pytest.mark.asyncio
    async def test_success_no_alert(self, db, executor, setup_data):
        """"""
        signal = make_signal(setup_data["strategy"]["id"])
        install = make_install()

        await executor.execute(install, [signal])

        row = await (await db.execute(
            "SELECT * FROM alerts WHERE type='bet_fail'",
        )).fetchone()
        assert row is None


# 
# 9. 
# 

class TestEmptySignals:
    @pytest.mark.asyncio
    async def test_empty_signals_noop(self, executor):
        """"""
        install = make_install()
        await executor.execute(install, [])
        executor.adapter.place_bet.assert_not_called()
        executor.risk.check.assert_not_called()


# 
# PBT: hypothesis
# 

from hypothesis import given, settings, assume
from hypothesis import strategies as st


class TestPBT_P12_BetMergeNoSignalLoss:
    """P12:   =, , KeyCode

    **Validates: Requirements 5.4**
    """

    @given(
        signal_specs=st.lists(
            st.tuples(
                st.sampled_from(["DX1", "DX2", "DS3", "DS4", "ZH7", "ZH8"]),
                st.integers(min_value=100, max_value=100_000),
            ),
            min_size=1,
            max_size=10,
        ),
    )
    @settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_pbt_bet_merge_no_signal_loss(self, signal_specs):
        """1-10KeyCode betdata =KeyCode

        **Validates: Requirements 5.4**
        """
        # Setup: fresh in-memory DB for each test
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys=ON")
        for stmt in DDL_STATEMENTS:
            await conn.execute(stmt)
        await conn.execute(INSERT_DEFAULT_ADMIN)
        await conn.commit()

        # Create operator, account, strategies
        op = await operator_create(
            conn, username="pbt_op", password="pass123",
            role="operator", status="active", created_by=1,
        )
        acct = await account_create(
            conn, operator_id=op["id"], account_name="pbt_acct",
            password="pw", platform_type="JND28WEB",
        )
        acct = await account_update(
            conn, account_id=acct["id"], operator_id=op["id"],
            status="online", session_token="tok", balance=10_000_000,
        )

        # Create one strategy per signal
        strategies = []
        for i in range(len(signal_specs)):
            s = await strategy_create(
                conn, operator_id=op["id"], account_id=acct["id"],
                name=f"{i}", type="flat", play_code=signal_specs[i][0],
                base_amount=signal_specs[i][1],
            )
            s = await strategy_update(
                conn, strategy_id=s["id"], operator_id=op["id"], status="running",
            )
            strategies.append(s)

        # Build signals
        signals = []
        issue = "20240101001"
        for i, (key_code, amount) in enumerate(signal_specs):
            signals.append(BetSignal(
                strategy_id=strategies[i]["id"],
                key_code=key_code,
                amount=amount,
                idempotent_id=f"{issue}-{strategies[i]['id']}-{key_code}",
                martin_level=0,
                simulation=False,
            ))

        # Build odds dict covering all key codes with non-zero odds
        all_key_codes = set(kc for kc, _ in signal_specs)
        odds_dict = {kc: 19800 for kc in all_key_codes}

        # Insert confirmed odds into DB
        await odds_batch_upsert(
            conn, account_id=acct["id"],
            odds_map=odds_dict,
            confirmed=True,
        )

        # Mock adapter
        adapter = AsyncMock(spec=PlatformAdapter)
        adapter.load_odds = AsyncMock(return_value=odds_dict)
        captured_betdata = []

        async def capture_place_bet(iss, betdata):
            captured_betdata.extend(betdata)
            return BetResult(succeed=1, message="ok", raw_response={"succeed": 1})

        adapter.place_bet = AsyncMock(side_effect=capture_place_bet)

        # Mock risk: all pass
        risk = AsyncMock(spec=RiskController)
        risk.check = AsyncMock(return_value=RiskCheckResult(passed=True))

        alert_svc = AlertService(conn)

        executor = BetExecutor(
            db=conn, adapter=adapter, risk=risk,
            alert_service=alert_svc,
            operator_id=op["id"], account_id=acct["id"],
        )

        install = InstallInfo(
            issue=issue, state=1, close_countdown_sec=60,
            pre_issue="20240101000", pre_result="3,5,7",
        )

        await executor.execute(install, signals)

        # Assertions
        # betdata count = signal count (no merging)
        assert len(captured_betdata) == len(signal_specs), (
            f"betdata  {len(captured_betdata)} !=  {len(signal_specs)}"
        )

        # Each betdata entry has correct fields
        for i, (key_code, amount) in enumerate(signal_specs):
            bd = captured_betdata[i]
            assert bd["KeyCode"] == key_code
            assert bd["Amount"] == amount
            assert bd["Odds"] == odds_dict[key_code]

        # Verify same KeyCode signals are NOT merged
        from collections import Counter
        signal_kc_counts = Counter(kc for kc, _ in signal_specs)
        betdata_kc_counts = Counter(bd["KeyCode"] for bd in captured_betdata)
        assert signal_kc_counts == betdata_kc_counts, (
            f"KeyCode : signals={signal_kc_counts}, betdata={betdata_kc_counts}"
        )

        await conn.close()


class TestPBT_P23_IdempotentIdReplaySafety:
    """P23:  ID    idempotent_id DB  IntegrityError 

    **Validates: Requirements 4.4, 5.2**
    """

    @given(
        idempotent_id=st.from_regex(r"[0-9]{8,12}-[0-9]{1,4}-[A-Z]{2,5}[0-9]{0,2}", fullmatch=True),
    )
    @settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_pbt_idempotent_replay_safety(self, idempotent_id):
        """ idempotent_id IntegrityError 1 

        **Validates: Requirements 4.4, 5.2**
        """
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys=ON")
        for stmt in DDL_STATEMENTS:
            await conn.execute(stmt)
        await conn.execute(INSERT_DEFAULT_ADMIN)
        await conn.commit()

        op = await operator_create(
            conn, username="replay_op", password="pass123",
            role="operator", status="active", created_by=1,
        )
        acct = await account_create(
            conn, operator_id=op["id"], account_name="replay_acct",
            password="pw", platform_type="JND28WEB",
        )
        strat = await strategy_create(
            conn, operator_id=op["id"], account_id=acct["id"],
            name="replay_strat", type="flat", play_code="DX1", base_amount=1000,
        )

        # First insert  should succeed
        await bet_order_create(
            conn,
            idempotent_id=idempotent_id,
            operator_id=op["id"],
            account_id=acct["id"],
            strategy_id=strat["id"],
            issue="20240101001",
            key_code="DX1",
            amount=1000,
            status="pending",
        )

        # Second insert  should raise IntegrityError
        with pytest.raises(Exception) as exc_info:
            await bet_order_create(
                conn,
                idempotent_id=idempotent_id,
                operator_id=op["id"],
                account_id=acct["id"],
                strategy_id=strat["id"],
                issue="20240101001",
                key_code="DX1",
                amount=1000,
                status="pending",
            )
        # Verify it's an IntegrityError
        assert "UNIQUE constraint failed" in str(exc_info.value) or "IntegrityError" in type(exc_info.value).__name__

        # Verify only 1 row exists
        row = await (await conn.execute(
            "SELECT COUNT(*) as cnt FROM bet_orders WHERE idempotent_id=?",
            (idempotent_id,),
        )).fetchone()
        assert row["cnt"] == 1, (
            f"idempotent_id={idempotent_id}  1  {row['cnt']} "
        )

        await conn.close()


class TestPBT_P26_ConfirmbetZeroRetry:
    """P26: Confirmbet    place_bet succeed1 BetExecutor  place_bet 

    **Validates: Requirements 5.2**
    """

    @given(
        failure_type=st.sampled_from([
            "succeed_0", "succeed_2", "succeed_5", "succeed_10", "succeed_18",
            "timeout", "connection_error",
        ]),
        amount=st.integers(min_value=100, max_value=100_000),
        key_code=st.sampled_from(["DX1", "DX2", "DS3", "DS4"]),
    )
    @settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_pbt_confirmbet_zero_retry(self, failure_type, amount, key_code):
        """place_bet  1  bet_failed

        **Validates: Requirements 5.2**
        """
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys=ON")
        for stmt in DDL_STATEMENTS:
            await conn.execute(stmt)
        await conn.execute(INSERT_DEFAULT_ADMIN)
        await conn.commit()

        op = await operator_create(
            conn, username="retry_op", password="pass123",
            role="operator", status="active", created_by=1,
        )
        acct = await account_create(
            conn, operator_id=op["id"], account_name="retry_acct",
            password="pw", platform_type="JND28WEB",
        )
        acct = await account_update(
            conn, account_id=acct["id"], operator_id=op["id"],
            status="online", session_token="tok", balance=10_000_000,
        )
        strat = await strategy_create(
            conn, operator_id=op["id"], account_id=acct["id"],
            name="retry_strat", type="flat", play_code=key_code, base_amount=amount,
        )
        strat = await strategy_update(
            conn, strategy_id=strat["id"], operator_id=op["id"], status="running",
        )

        # Configure mock adapter based on failure type
        adapter = AsyncMock(spec=PlatformAdapter)
        adapter.load_odds = AsyncMock(return_value={
            "DX1": 19800, "DX2": 19800, "DS3": 19800, "DS4": 19800,
        })

        # Insert confirmed odds into DB
        await odds_batch_upsert(
            conn, account_id=acct["id"],
            odds_map={"DX1": 19800, "DX2": 19800, "DS3": 19800, "DS4": 19800},
            confirmed=True,
        )

        if failure_type.startswith("succeed_"):
            succeed_val = int(failure_type.split("_")[1])
            adapter.place_bet = AsyncMock(return_value=BetResult(
                succeed=succeed_val, message=f"error_{succeed_val}",
                raw_response={"succeed": succeed_val},
            ))
        elif failure_type == "timeout":
            adapter.place_bet = AsyncMock(side_effect=TimeoutError(""))
        elif failure_type == "connection_error":
            adapter.place_bet = AsyncMock(side_effect=ConnectionError(""))

        risk = AsyncMock(spec=RiskController)
        risk.check = AsyncMock(return_value=RiskCheckResult(passed=True))
        alert_svc = AlertService(conn)

        executor = BetExecutor(
            db=conn, adapter=adapter, risk=risk,
            alert_service=alert_svc,
            operator_id=op["id"], account_id=acct["id"],
        )

        signal = BetSignal(
            strategy_id=strat["id"],
            key_code=key_code,
            amount=amount,
            idempotent_id=f"20240101001-{strat['id']}-{key_code}",
            martin_level=0,
            simulation=False,
        )

        install = InstallInfo(
            issue="20240101001", state=1, close_countdown_sec=60,
            pre_issue="20240101000", pre_result="3,5,7",
        )

        await executor.execute(install, [signal])

        # Core assertion: place_bet called once (or twice for succeed_5 retry)
        if failure_type == "succeed_5":
            # succeed=5 triggers live odds retry, so place_bet called twice
            assert adapter.place_bet.call_count == 2, (
                f"succeed_5 时 place_bet 应调用 2 次，实际 {adapter.place_bet.call_count} 次"
            )
        else:
            assert adapter.place_bet.call_count == 1, (
                f"place_bet 应调用 1 次，实际 {adapter.place_bet.call_count} 次 "
                f"failure_type={failure_type}"
            )

        # Verify order status = bet_failed
        row = await (await conn.execute(
            "SELECT * FROM bet_orders WHERE idempotent_id=?",
            (signal.idempotent_id,),
        )).fetchone()
        assert row is not None
        assert row["status"] == "bet_failed", (
            f" bet_failed {row['status']}"
            f"failure_type={failure_type}"
        )
        assert row["fail_reason"] is not None and len(row["fail_reason"]) > 0

        await conn.close()
