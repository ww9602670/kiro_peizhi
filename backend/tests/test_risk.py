"""RiskController


- 
- 
- 10  + 
-  3 
- 
- amount=limit amount=limit+1 
- 
"""
from __future__ import annotations

import pytest
import aiosqlite

from app.database import DDL_STATEMENTS, INSERT_DEFAULT_ADMIN
from app.engine.alert import AlertService
from app.engine.risk import (
    PLATFORM_DEFAULT_SINGLE_BET_LIMIT,
    RiskCheckResult,
    RiskController,
)
from app.engine.strategy_runner import BetSignal
from app.models.db_ops import (
    account_create,
    account_update,
    bet_order_create,
    operator_create,
    strategy_create,
    strategy_get_by_id,
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
    """ (operator, account, strategy) """
    op = await operator_create(
        db, username="test_op", password="pass123",
        role="operator", status="active", created_by=1,
    )
    acct = await account_create(
        db, operator_id=op["id"], account_name="acct1",
        password="pw", platform_type="JND28WEB",
    )
    #  +  session_token + 
    acct = await account_update(
        db, account_id=acct["id"], operator_id=op["id"],
        status="online", session_token="valid_token", balance=100_000,
    )
    strat = await strategy_create(
        db, operator_id=op["id"], account_id=acct["id"],
        name="", type="flat", play_code="DX1",
        base_amount=1000, stop_loss=None, take_profit=None,
    )
    #  running
    strat = await strategy_update(
        db, strategy_id=strat["id"], operator_id=op["id"], status="running",
    )
    return {"operator": op, "account": acct, "strategy": strat}


@pytest.fixture
async def alert_service(db):
    return AlertService(db)


@pytest.fixture
async def risk(db, setup_data, alert_service):
    """ RiskController """
    return RiskController(
        db=db,
        alert_service=alert_service,
        operator_id=setup_data["operator"]["id"],
        account_id=setup_data["account"]["id"],
        global_kill=False,
    )


def make_signal(strategy_id: int, amount: int = 1000, key_code: str = "DX1",
                issue: str = "20240101001") -> BetSignal:
    """ BetSignal"""
    return BetSignal(
        strategy_id=strategy_id,
        key_code=key_code,
        amount=amount,
        idempotent_id=f"{issue}-{strategy_id}-{key_code}",
    )


async def _insert_settled_order(
    db, *, idempotent_id: str, operator_id: int, account_id: int,
    strategy_id: int, issue: str, key_code: str, amount: int,
    is_win: int, pnl: int,
) -> None:
    """"""
    from app.models.db_ops import _now
    now = _now()
    await db.execute(
        """INSERT INTO bet_orders
           (idempotent_id, operator_id, account_id, strategy_id, issue,
            key_code, amount, status, is_win, pnl, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'settled', ?, ?, ?)""",
        (idempotent_id, operator_id, account_id, strategy_id, issue,
         key_code, amount, is_win, pnl, now),
    )
    await db.commit()


# 
# 1. RiskCheckResult 
# 

class TestRiskCheckResult:
    def test_passed(self):
        r = RiskCheckResult(passed=True)
        assert r.passed is True
        assert r.reason == ""

    def test_failed_with_reason(self):
        r = RiskCheckResult(passed=False, reason="")
        assert r.passed is False
        assert r.reason == ""


# 
# 2. 
# 

class TestCheckOrder:
    """ 10 """

    @pytest.mark.asyncio
    async def test_all_pass_order(self, risk, setup_data):
        """ =  10 """
        signal = make_signal(setup_data["strategy"]["id"])
        result = await risk.check(signal)
        assert result.passed is True
        assert risk._check_log == [
            "kill_switch", "session", "strategy_status", "operator_status",
            "balance", "single_bet_limit", "daily_limit", "period_limit",
            "stop_loss", "take_profit",
        ]

    @pytest.mark.asyncio
    async def test_short_circuit_at_third(self, db, setup_data, alert_service):
        """ 3 strategy_status 3 """
        #  stopped
        await strategy_update(
            db, strategy_id=setup_data["strategy"]["id"],
            operator_id=setup_data["operator"]["id"], status="stopped",
        )
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
        )
        signal = make_signal(setup_data["strategy"]["id"])
        result = await risk.check(signal)
        assert result.passed is False
        assert "strategy_status" in risk._check_log[-1]
        assert len(risk._check_log) == 3

    @pytest.mark.asyncio
    async def test_short_circuit_at_first(self, db, setup_data, alert_service):
        """ 1 kill_switch 1 """
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            global_kill=True,
        )
        signal = make_signal(setup_data["strategy"]["id"])
        result = await risk.check(signal)
        assert result.passed is False
        assert len(risk._check_log) == 1
        assert risk._check_log[0] == "kill_switch"


# 
# 3. 
# 

class TestKillSwitch:
    @pytest.mark.asyncio
    async def test_global_kill(self, db, setup_data, alert_service):
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            global_kill=True,
        )
        signal = make_signal(setup_data["strategy"]["id"])
        result = await risk._check_kill_switch(signal)
        assert result.passed is False
        assert "" in result.reason

    @pytest.mark.asyncio
    async def test_account_kill(self, db, setup_data, alert_service):
        await account_update(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"], kill_switch=1,
        )
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
        )
        signal = make_signal(setup_data["strategy"]["id"])
        result = await risk._check_kill_switch(signal)
        assert result.passed is False
        assert "" in result.reason

    @pytest.mark.asyncio
    async def test_no_kill(self, risk, setup_data):
        signal = make_signal(setup_data["strategy"]["id"])
        result = await risk._check_kill_switch(signal)
        assert result.passed is True


class TestSession:
    @pytest.mark.asyncio
    async def test_no_session_token(self, db, setup_data, alert_service):
        await account_update(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"], session_token=None,
        )
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
        )
        signal = make_signal(setup_data["strategy"]["id"])
        result = await risk._check_session(signal)
        assert result.passed is False
        assert "session_token" in result.reason

    @pytest.mark.asyncio
    async def test_empty_session_token(self, db, setup_data, alert_service):
        await account_update(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"], session_token="",
        )
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
        )
        signal = make_signal(setup_data["strategy"]["id"])
        result = await risk._check_session(signal)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_valid_session(self, risk, setup_data):
        signal = make_signal(setup_data["strategy"]["id"])
        result = await risk._check_session(signal)
        assert result.passed is True


class TestStrategyStatus:
    @pytest.mark.asyncio
    async def test_not_running(self, db, setup_data, alert_service):
        await strategy_update(
            db, strategy_id=setup_data["strategy"]["id"],
            operator_id=setup_data["operator"]["id"], status="paused",
        )
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
        )
        signal = make_signal(setup_data["strategy"]["id"])
        result = await risk._check_strategy_status(signal)
        assert result.passed is False
        assert "paused" in result.reason

    @pytest.mark.asyncio
    async def test_running(self, risk, setup_data):
        signal = make_signal(setup_data["strategy"]["id"])
        result = await risk._check_strategy_status(signal)
        assert result.passed is True


class TestOperatorStatus:
    @pytest.mark.asyncio
    async def test_disabled(self, db, setup_data, alert_service):
        from app.models.db_ops import operator_update
        await operator_update(
            db, operator_id=setup_data["operator"]["id"], status="disabled",
        )
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
        )
        signal = make_signal(setup_data["strategy"]["id"])
        result = await risk._check_operator_status(signal)
        assert result.passed is False
        assert "disabled" in result.reason

    @pytest.mark.asyncio
    async def test_active(self, risk, setup_data):
        signal = make_signal(setup_data["strategy"]["id"])
        result = await risk._check_operator_status(signal)
        assert result.passed is True


class TestBalance:
    @pytest.mark.asyncio
    async def test_sufficient(self, risk, setup_data):
        """100000 >= 1000"""
        signal = make_signal(setup_data["strategy"]["id"], amount=1000)
        result = await risk._check_balance(signal)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_insufficient(self, db, setup_data, alert_service):
        """"""
        await account_update(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"], balance=500,
        )
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
        )
        signal = make_signal(setup_data["strategy"]["id"], amount=1000)
        result = await risk._check_balance(signal)
        assert result.passed is False
        assert "" in result.reason

    @pytest.mark.asyncio
    async def test_exact_balance(self, db, setup_data, alert_service):
        """  """
        await account_update(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"], balance=1000,
        )
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
        )
        signal = make_signal(setup_data["strategy"]["id"], amount=1000)
        result = await risk._check_balance(signal)
        assert result.passed is True


# 
# 4. 
# 

class TestSingleBetLimit:
    @pytest.mark.asyncio
    async def test_within_platform_limit(self, risk, setup_data):
        """  """
        signal = make_signal(setup_data["strategy"]["id"], amount=1000)
        result = await risk._check_single_bet_limit(signal)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_exceed_platform_limit(self, risk, setup_data):
        """  """
        signal = make_signal(
            setup_data["strategy"]["id"],
            amount=PLATFORM_DEFAULT_SINGLE_BET_LIMIT + 1,
        )
        result = await risk._check_single_bet_limit(signal)
        assert result.passed is False
        assert "" in result.reason

    @pytest.mark.asyncio
    async def test_exact_platform_limit(self, risk, setup_data):
        """  """
        signal = make_signal(
            setup_data["strategy"]["id"],
            amount=PLATFORM_DEFAULT_SINGLE_BET_LIMIT,
        )
        result = await risk._check_single_bet_limit(signal)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_self_limit_pass(self, db, setup_data, alert_service):
        """  """
        await account_update(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"],
            single_bet_limit=5000,
        )
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
        )
        signal = make_signal(setup_data["strategy"]["id"], amount=5000)
        result = await risk._check_single_bet_limit(signal)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_self_limit_reject(self, db, setup_data, alert_service):
        """ 1   """
        await account_update(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"],
            single_bet_limit=5000,
        )
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
        )
        signal = make_signal(setup_data["strategy"]["id"], amount=5001)
        result = await risk._check_single_bet_limit(signal)
        assert result.passed is False
        assert "" in result.reason

    @pytest.mark.asyncio
    async def test_platform_limit_triggers_alert(self, db, setup_data, alert_service):
        """ platform_limit """
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
        )
        signal = make_signal(
            setup_data["strategy"]["id"],
            amount=PLATFORM_DEFAULT_SINGLE_BET_LIMIT + 1,
        )
        await risk._check_single_bet_limit(signal)
        # 
        row = await (await db.execute(
            "SELECT * FROM alerts WHERE operator_id=? AND type='platform_limit'",
            (setup_data["operator"]["id"],),
        )).fetchone()
        assert row is not None


class TestDailyLimit:
    @pytest.mark.asyncio
    async def test_no_limit_set(self, risk, setup_data):
        """  """
        signal = make_signal(setup_data["strategy"]["id"])
        result = await risk._check_daily_limit(signal)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_within_limit(self, db, setup_data, alert_service):
        """  """
        await account_update(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"],
            daily_limit=10000,
        )
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
        )
        signal = make_signal(setup_data["strategy"]["id"], amount=5000)
        result = await risk._check_daily_limit(signal)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_exceed_limit(self, db, setup_data, alert_service):
        """  """
        await account_update(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"],
            daily_limit=10000,
        )
        # 
        await bet_order_create(
            db, idempotent_id="20240101001-1-DX1",
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            strategy_id=setup_data["strategy"]["id"],
            issue="20240101001", key_code="DX1", amount=8000,
            status="bet_success",
        )
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
        )
        signal = make_signal(setup_data["strategy"]["id"], amount=3000)
        result = await risk._check_daily_limit(signal)
        assert result.passed is False
        assert "" in result.reason


class TestPeriodLimit:
    @pytest.mark.asyncio
    async def test_no_limit_set(self, risk, setup_data):
        """  """
        signal = make_signal(setup_data["strategy"]["id"])
        result = await risk._check_period_limit(signal)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_within_limit(self, db, setup_data, alert_service):
        """  """
        await account_update(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"],
            period_limit=10000,
        )
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
        )
        signal = make_signal(setup_data["strategy"]["id"], amount=5000)
        result = await risk._check_period_limit(signal)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_exceed_limit(self, db, setup_data, alert_service):
        """  """
        await account_update(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"],
            period_limit=10000,
        )
        # 
        await bet_order_create(
            db, idempotent_id="20240101001-99-DX2",
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            strategy_id=setup_data["strategy"]["id"],
            issue="20240101001", key_code="DX2", amount=8000,
            status="bet_success",
        )
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
        )
        signal = make_signal(setup_data["strategy"]["id"], amount=3000, issue="20240101001")
        result = await risk._check_period_limit(signal)
        assert result.passed is False
        assert "" in result.reason


# 
# 5. 
# 

class TestStopLoss:
    @pytest.mark.asyncio
    async def test_no_stop_loss_set(self, risk, setup_data):
        """  """
        signal = make_signal(setup_data["strategy"]["id"])
        result = await risk._check_stop_loss(signal)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_stop_loss_not_triggered(self, db, setup_data, alert_service):
        """  """
        await strategy_update(
            db, strategy_id=setup_data["strategy"]["id"],
            operator_id=setup_data["operator"]["id"],
            stop_loss=10000,
        )
        # -5000 10000
        await _insert_settled_order(
            db, idempotent_id="loss-1-DX1",
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            strategy_id=setup_data["strategy"]["id"],
            issue="20240101001", key_code="DX1", amount=5000,
            is_win=0, pnl=-5000,
        )
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
        )
        signal = make_signal(setup_data["strategy"]["id"])
        result = await risk._check_stop_loss(signal)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_stop_loss_triggered(self, db, setup_data, alert_service):
        """   + """
        await strategy_update(
            db, strategy_id=setup_data["strategy"]["id"],
            operator_id=setup_data["operator"]["id"],
            stop_loss=10000,
        )
        # 
        await _insert_settled_order(
            db, idempotent_id="loss-big-DX1",
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            strategy_id=setup_data["strategy"]["id"],
            issue="20240101001", key_code="DX1", amount=10000,
            is_win=0, pnl=-10000,
        )
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
        )
        signal = make_signal(setup_data["strategy"]["id"])
        result = await risk._check_stop_loss(signal)
        assert result.passed is False
        assert "" in result.reason
        # 
        row = await (await db.execute(
            "SELECT * FROM alerts WHERE type='stop_loss'"
        )).fetchone()
        assert row is not None

    @pytest.mark.asyncio
    async def test_refund_not_counted_in_stop_loss(self, db, setup_data, alert_service):
        """is_win=-1"""
        await strategy_update(
            db, strategy_id=setup_data["strategy"]["id"],
            operator_id=setup_data["operator"]["id"],
            stop_loss=10000,
        )
        #  -5000
        await _insert_settled_order(
            db, idempotent_id="loss-a-DX1",
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            strategy_id=setup_data["strategy"]["id"],
            issue="20240101001", key_code="DX1", amount=5000,
            is_win=0, pnl=-5000,
        )
        # is_win=-1, pnl=0 
        await _insert_settled_order(
            db, idempotent_id="refund-b-DX1",
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            strategy_id=setup_data["strategy"]["id"],
            issue="20240101002", key_code="DX1", amount=8000,
            is_win=-1, pnl=0,
        )
        #  -4000 = -5000 + -4000 = -9000 10000
        await _insert_settled_order(
            db, idempotent_id="loss-c-DX1",
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            strategy_id=setup_data["strategy"]["id"],
            issue="20240101003", key_code="DX1", amount=4000,
            is_win=0, pnl=-4000,
        )

        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
        )
        signal = make_signal(setup_data["strategy"]["id"])
        result = await risk._check_stop_loss(signal)
        #  = -9000 = 10000
        assert result.passed is True


class TestTakeProfit:
    @pytest.mark.asyncio
    async def test_no_take_profit_set(self, risk, setup_data):
        """  """
        signal = make_signal(setup_data["strategy"]["id"])
        result = await risk._check_take_profit(signal)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_take_profit_triggered(self, db, setup_data, alert_service):
        """   + """
        await strategy_update(
            db, strategy_id=setup_data["strategy"]["id"],
            operator_id=setup_data["operator"]["id"],
            take_profit=5000,
        )
        await _insert_settled_order(
            db, idempotent_id="win-1-DX1",
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            strategy_id=setup_data["strategy"]["id"],
            issue="20240101001", key_code="DX1", amount=5000,
            is_win=1, pnl=5000,
        )
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
        )
        signal = make_signal(setup_data["strategy"]["id"])
        result = await risk._check_take_profit(signal)
        assert result.passed is False
        assert "" in result.reason
        # 
        row = await (await db.execute(
            "SELECT * FROM alerts WHERE type='take_profit'"
        )).fetchone()
        assert row is not None

    @pytest.mark.asyncio
    async def test_take_profit_not_triggered(self, db, setup_data, alert_service):
        """  """
        await strategy_update(
            db, strategy_id=setup_data["strategy"]["id"],
            operator_id=setup_data["operator"]["id"],
            take_profit=10000,
        )
        await _insert_settled_order(
            db, idempotent_id="win-small-DX1",
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            strategy_id=setup_data["strategy"]["id"],
            issue="20240101001", key_code="DX1", amount=3000,
            is_win=1, pnl=3000,
        )
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
        )
        signal = make_signal(setup_data["strategy"]["id"])
        result = await risk._check_take_profit(signal)
        assert result.passed is True


# 
# 6.  3 
# 

class TestBalanceConsecutivePause:
    @pytest.mark.asyncio
    async def test_consecutive_3_failures_pauses_all(self, db, setup_data, alert_service):
        """ 3    + balance_low """
        #  0
        await account_update(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"], balance=0,
        )
        #  running
        strat2 = await strategy_create(
            db, operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            name="2", type="flat", play_code="DX2", base_amount=500,
        )
        await strategy_update(
            db, strategy_id=strat2["id"],
            operator_id=setup_data["operator"]["id"], status="running",
        )

        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
        )

        signal = make_signal(setup_data["strategy"]["id"], amount=1000)

        #  1 
        result = await risk._check_balance(signal)
        assert result.passed is False
        assert risk._balance_fail_count[setup_data["account"]["id"]] == 1

        #  2 
        result = await risk._check_balance(signal)
        assert result.passed is False
        assert risk._balance_fail_count[setup_data["account"]["id"]] == 2

        #  3    + 
        result = await risk._check_balance(signal)
        assert result.passed is False
        # 
        assert risk._balance_fail_count[setup_data["account"]["id"]] == 0

        # 
        s1 = await strategy_get_by_id(
            db, strategy_id=setup_data["strategy"]["id"],
            operator_id=setup_data["operator"]["id"],
        )
        s2 = await strategy_get_by_id(
            db, strategy_id=strat2["id"],
            operator_id=setup_data["operator"]["id"],
        )
        assert s1["status"] == "paused"
        assert s2["status"] == "paused"

        #  balance_low 
        row = await (await db.execute(
            "SELECT * FROM alerts WHERE type='balance_low'"
        )).fetchone()
        assert row is not None

    @pytest.mark.asyncio
    async def test_balance_ok_resets_counter(self, db, setup_data, alert_service):
        """"""
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
        )
        #  2 
        await account_update(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"], balance=0,
        )
        signal = make_signal(setup_data["strategy"]["id"], amount=1000)
        await risk._check_balance(signal)
        await risk._check_balance(signal)
        assert risk._balance_fail_count[setup_data["account"]["id"]] == 2

        # 
        await account_update(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"], balance=100_000,
        )
        result = await risk._check_balance(signal)
        assert result.passed is True
        assert risk._balance_fail_count[setup_data["account"]["id"]] == 0


# 
# 7.  check 
# 

class TestCombination:
    @pytest.mark.asyncio
    async def test_all_checks_pass(self, risk, setup_data):
        """  passed=True"""
        signal = make_signal(setup_data["strategy"]["id"], amount=1000)
        result = await risk.check(signal)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_kill_switch_blocks_everything(self, db, setup_data, alert_service):
        """  """
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            global_kill=True,
        )
        signal = make_signal(setup_data["strategy"]["id"])
        result = await risk.check(signal)
        assert result.passed is False
        assert "" in result.reason
        assert len(risk._check_log) == 1

    @pytest.mark.asyncio
    async def test_balance_fail_before_limits(self, db, setup_data, alert_service):
        """  """
        await account_update(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"], balance=0,
        )
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
        )
        signal = make_signal(setup_data["strategy"]["id"], amount=1000)
        result = await risk.check(signal)
        assert result.passed is False
        assert "" in result.reason
        #  balance  5 
        assert len(risk._check_log) == 5
        assert risk._check_log[-1] == "balance"

    @pytest.mark.asyncio
    async def test_stop_loss_with_mixed_results(self, db, setup_data, alert_service):
        """"""
        await strategy_update(
            db, strategy_id=setup_data["strategy"]["id"],
            operator_id=setup_data["operator"]["id"],
            stop_loss=8000,
        )
        #  -5000
        await _insert_settled_order(
            db, idempotent_id="mix-loss-DX1",
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            strategy_id=setup_data["strategy"]["id"],
            issue="20240101001", key_code="DX1", amount=5000,
            is_win=0, pnl=-5000,
        )
        #  +2000
        await _insert_settled_order(
            db, idempotent_id="mix-win-DX2",
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            strategy_id=setup_data["strategy"]["id"],
            issue="20240101002", key_code="DX2", amount=2000,
            is_win=1, pnl=2000,
        )
        #  pnl=0
        await _insert_settled_order(
            db, idempotent_id="mix-refund-DX1",
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            strategy_id=setup_data["strategy"]["id"],
            issue="20240101003", key_code="DX1", amount=10000,
            is_win=-1, pnl=0,
        )
        #  -6000
        await _insert_settled_order(
            db, idempotent_id="mix-loss2-DX1",
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            strategy_id=setup_data["strategy"]["id"],
            issue="20240101004", key_code="DX1", amount=6000,
            is_win=0, pnl=-6000,
        )

        #  = -5000 + 2000 + (-6000) = -9000 8000  
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
        )
        signal = make_signal(setup_data["strategy"]["id"])
        result = await risk.check(signal)
        assert result.passed is False
        assert "" in result.reason


# 
# 8. 
# 

class TestAlertTriggers:
    @pytest.mark.asyncio
    async def test_stop_loss_alert(self, db, setup_data, alert_service):
        """ AlertService.send(stop_loss)"""
        await strategy_update(
            db, strategy_id=setup_data["strategy"]["id"],
            operator_id=setup_data["operator"]["id"],
            stop_loss=1000,
        )
        await _insert_settled_order(
            db, idempotent_id="alert-loss-DX1",
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            strategy_id=setup_data["strategy"]["id"],
            issue="20240101001", key_code="DX1", amount=2000,
            is_win=0, pnl=-2000,
        )

        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
        )
        signal = make_signal(setup_data["strategy"]["id"])
        await risk._check_stop_loss(signal)

        row = await (await db.execute(
            "SELECT * FROM alerts WHERE type='stop_loss' AND operator_id=?",
            (setup_data["operator"]["id"],),
        )).fetchone()
        assert row is not None
        assert "" in row["title"]

    @pytest.mark.asyncio
    async def test_take_profit_alert(self, db, setup_data, alert_service):
        """ AlertService.send(take_profit)"""
        await strategy_update(
            db, strategy_id=setup_data["strategy"]["id"],
            operator_id=setup_data["operator"]["id"],
            take_profit=1000,
        )
        await _insert_settled_order(
            db, idempotent_id="alert-win-DX1",
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            strategy_id=setup_data["strategy"]["id"],
            issue="20240101001", key_code="DX1", amount=1000,
            is_win=1, pnl=2000,
        )

        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
        )
        signal = make_signal(setup_data["strategy"]["id"])
        await risk._check_take_profit(signal)

        row = await (await db.execute(
            "SELECT * FROM alerts WHERE type='take_profit' AND operator_id=?",
            (setup_data["operator"]["id"],),
        )).fetchone()
        assert row is not None
        assert "" in row["title"]

    @pytest.mark.asyncio
    async def test_platform_limit_alert(self, db, setup_data, alert_service):
        """ platform_limit """
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
        )
        signal = make_signal(
            setup_data["strategy"]["id"],
            amount=PLATFORM_DEFAULT_SINGLE_BET_LIMIT + 100,
        )
        await risk._check_single_bet_limit(signal)

        row = await (await db.execute(
            "SELECT * FROM alerts WHERE type='platform_limit' AND operator_id=?",
            (setup_data["operator"]["id"],),
        )).fetchone()
        assert row is not None
        assert "" in row["title"]


# 
# 9. period_bets 
# 

class TestPeriodBetsTracking:
    @pytest.mark.asyncio
    async def test_period_bets_accumulate(self, db, setup_data, alert_service):
        """ period_limit """
        await account_update(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"],
            period_limit=10000,
        )
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
        )
        issue = "20240101001"
        #  3000  
        s1 = make_signal(setup_data["strategy"]["id"], amount=3000, issue=issue)
        r1 = await risk._check_period_limit(s1)
        assert r1.passed is True

        #  3000   6000
        s2 = make_signal(setup_data["strategy"]["id"], amount=3000, issue=issue, key_code="DX2")
        r2 = await risk._check_period_limit(s2)
        assert r2.passed is True

        #  5000   6000 + 5000 = 11000 > 10000
        s3 = make_signal(setup_data["strategy"]["id"], amount=5000, issue=issue, key_code="DS3")
        r3 = await risk._check_period_limit(s3)
        assert r3.passed is False

    @pytest.mark.asyncio
    async def test_reset_period_bets(self, db, setup_data, alert_service):
        """reset_period_bets """
        risk = RiskController(
            db=db, alert_service=alert_service,
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
        )
        risk._period_bets["20240101001"] = 5000
        risk.reset_period_bets("20240101001")
        assert "20240101001" not in risk._period_bets


# 
# PBT: hypothesis
# 

from hypothesis import given, settings, assume
from hypothesis import strategies as st


# 
# PBT  PBT  DB + 
#  hypothesis  pytest async fixtures
# 

async def _pbt_setup():
    """ DB +  (db, operator, account, strategy, alert_service)"""
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys=ON")
    for stmt in DDL_STATEMENTS:
        await conn.execute(stmt)
    await conn.execute(INSERT_DEFAULT_ADMIN)
    await conn.commit()

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
        status="online", session_token="valid_token", balance=999_999_999,
    )
    strat = await strategy_create(
        conn, operator_id=op["id"], account_id=acct["id"],
        name="PBT", type="flat", play_code="DX1",
        base_amount=1000, stop_loss=None, take_profit=None,
    )
    strat = await strategy_update(
        conn, strategy_id=strat["id"], operator_id=op["id"], status="running",
    )
    alert_svc = AlertService(conn)
    return conn, op, acct, strat, alert_svc


async def _pbt_teardown(conn):
    await conn.close()


class TestPBT_P10_StopLossTakeProfit:
    """P10: 

    **Validates: Requirements 6.2**

    Properties:
    - daily_pnl  -stop_loss  check fails
    - daily_pnl  take_profit  check fails
    - -stop_loss < daily_pnl < take_profit  
    - stop_loss/take_profit  None  
    """

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(
        daily_pnl=st.integers(min_value=-10_000_000, max_value=10_000_000),
        stop_loss=st.one_of(st.none(), st.integers(min_value=1, max_value=10_000_000)),
        take_profit=st.one_of(st.none(), st.integers(min_value=1, max_value=10_000_000)),
    )
    async def test_pbt_stop_loss_take_profit(self, daily_pnl, stop_loss, take_profit):
        """**Validates: Requirements 6.2**

        For any daily_pnl and stop_loss/take_profit thresholds:
        - daily_pnl  -stop_loss  stop_loss check fails (triggered)
        - daily_pnl  take_profit  take_profit check fails (triggered)
        - None thresholds  always passes
        """
        conn, op, acct, strat, alert_svc = await _pbt_setup()
        try:
            # Set stop_loss / take_profit on strategy
            await strategy_update(
                conn, strategy_id=strat["id"], operator_id=op["id"],
                stop_loss=stop_loss, take_profit=take_profit,
            )

            # Insert a single settled order to produce the desired daily_pnl
            if daily_pnl != 0:
                is_win = 1 if daily_pnl > 0 else 0
                await _insert_settled_order(
                    conn, idempotent_id=f"pbt-pnl-{daily_pnl}",
                    operator_id=op["id"], account_id=acct["id"],
                    strategy_id=strat["id"], issue="20240101001",
                    key_code="DX1", amount=abs(daily_pnl),
                    is_win=is_win, pnl=daily_pnl,
                )

            risk = RiskController(
                db=conn, alert_service=alert_svc,
                operator_id=op["id"], account_id=acct["id"],
            )
            signal = make_signal(strat["id"], amount=100)

            # Test stop_loss check
            sl_result = await risk._check_stop_loss(signal)
            if stop_loss is None:
                assert sl_result.passed is True, "stop_loss=None should always pass"
            elif daily_pnl <= -stop_loss:
                assert sl_result.passed is False, (
                    f"daily_pnl={daily_pnl}  -{stop_loss} should trigger stop_loss"
                )
            else:
                assert sl_result.passed is True, (
                    f"daily_pnl={daily_pnl} > -{stop_loss} should not trigger stop_loss"
                )

            # Test take_profit check
            tp_result = await risk._check_take_profit(signal)
            if take_profit is None:
                assert tp_result.passed is True, "take_profit=None should always pass"
            elif daily_pnl >= take_profit:
                assert tp_result.passed is False, (
                    f"daily_pnl={daily_pnl}  {take_profit} should trigger take_profit"
                )
            else:
                assert tp_result.passed is True, (
                    f"daily_pnl={daily_pnl} < {take_profit} should not trigger take_profit"
                )
        finally:
            await _pbt_teardown(conn)


class TestPBT_P11_LimitChecks:
    """P11: 

    **Validates: Requirements 6.1**

    Properties:
    - amount  limit  passes
    - amount > limit  fails
    - Single bet limit checks both platform default and self-set limits
    """

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(
        amount=st.integers(min_value=1, max_value=20_000_000),
        self_limit=st.one_of(st.none(), st.integers(min_value=1, max_value=20_000_000)),
    )
    async def test_pbt_single_bet_limit(self, amount, self_limit):
        """**Validates: Requirements 6.1**

        Single bet limit: amount must be  platform limit AND  self-set limit (if set).
        """
        conn, op, acct, strat, alert_svc = await _pbt_setup()
        try:
            if self_limit is not None:
                await account_update(
                    conn, account_id=acct["id"], operator_id=op["id"],
                    single_bet_limit=self_limit,
                )

            risk = RiskController(
                db=conn, alert_service=alert_svc,
                operator_id=op["id"], account_id=acct["id"],
            )
            signal = make_signal(strat["id"], amount=amount)
            result = await risk._check_single_bet_limit(signal)

            platform_limit = PLATFORM_DEFAULT_SINGLE_BET_LIMIT

            if amount > platform_limit:
                assert result.passed is False, (
                    f"amount={amount} > platform_limit={platform_limit} should fail"
                )
            elif self_limit is not None and amount > self_limit:
                assert result.passed is False, (
                    f"amount={amount} > self_limit={self_limit} should fail"
                )
            else:
                assert result.passed is True, (
                    f"amount={amount} within limits should pass"
                )
        finally:
            await _pbt_teardown(conn)

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(
        amount=st.integers(min_value=1, max_value=500_000),
        daily_limit=st.one_of(st.none(), st.integers(min_value=1, max_value=1_000_000)),
        existing_total=st.integers(min_value=0, max_value=500_000),
    )
    async def test_pbt_daily_limit(self, amount, daily_limit, existing_total):
        """**Validates: Requirements 6.1**

        Daily limit: daily_total + amount must be  daily_limit (if set).
        None daily_limit  always passes.
        """
        conn, op, acct, strat, alert_svc = await _pbt_setup()
        try:
            if daily_limit is not None:
                await account_update(
                    conn, account_id=acct["id"], operator_id=op["id"],
                    daily_limit=daily_limit,
                )

            # Insert existing bet orders to simulate daily total
            if existing_total > 0:
                await bet_order_create(
                    conn, idempotent_id=f"pbt-daily-existing-{existing_total}",
                    operator_id=op["id"], account_id=acct["id"],
                    strategy_id=strat["id"], issue="20240101001",
                    key_code="DX1", amount=existing_total,
                    status="bet_success",
                )

            risk = RiskController(
                db=conn, alert_service=alert_svc,
                operator_id=op["id"], account_id=acct["id"],
            )
            signal = make_signal(strat["id"], amount=amount)
            result = await risk._check_daily_limit(signal)

            if daily_limit is None:
                assert result.passed is True, "daily_limit=None should always pass"
            elif existing_total + amount > daily_limit:
                assert result.passed is False, (
                    f"existing={existing_total}+amount={amount} > daily_limit={daily_limit} should fail"
                )
            else:
                assert result.passed is True, (
                    f"existing={existing_total}+amount={amount}  daily_limit={daily_limit} should pass"
                )
        finally:
            await _pbt_teardown(conn)

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(
        amount=st.integers(min_value=1, max_value=500_000),
        period_limit=st.one_of(st.none(), st.integers(min_value=1, max_value=1_000_000)),
        existing_total=st.integers(min_value=0, max_value=500_000),
    )
    async def test_pbt_period_limit(self, amount, period_limit, existing_total):
        """**Validates: Requirements 6.1**

        Period limit: period_total + amount must be  period_limit (if set).
        None period_limit  always passes.
        """
        conn, op, acct, strat, alert_svc = await _pbt_setup()
        try:
            if period_limit is not None:
                await account_update(
                    conn, account_id=acct["id"], operator_id=op["id"],
                    period_limit=period_limit,
                )

            issue = "20240101001"
            # Insert existing bet orders for this period
            if existing_total > 0:
                await bet_order_create(
                    conn, idempotent_id=f"pbt-period-existing-{existing_total}",
                    operator_id=op["id"], account_id=acct["id"],
                    strategy_id=strat["id"], issue=issue,
                    key_code="DX1", amount=existing_total,
                    status="bet_success",
                )

            risk = RiskController(
                db=conn, alert_service=alert_svc,
                operator_id=op["id"], account_id=acct["id"],
            )
            signal = make_signal(strat["id"], amount=amount, issue=issue)
            result = await risk._check_period_limit(signal)

            if period_limit is None:
                assert result.passed is True, "period_limit=None should always pass"
            elif existing_total + amount > period_limit:
                assert result.passed is False, (
                    f"existing={existing_total}+amount={amount} > period_limit={period_limit} should fail"
                )
            else:
                assert result.passed is True, (
                    f"existing={existing_total}+amount={amount}  period_limit={period_limit} should pass"
                )
        finally:
            await _pbt_teardown(conn)


class TestPBT_P19_CheckOrderInvariance:
    """P19: 

    **Validates: Requirements 6.1, 6.2**

    Property: The checks list order is always:
    [kill_switch, session, strategy_status, operator_status, balance,
     single_bet_limit, daily_limit, period_limit, stop_loss, take_profit]

    The order is fixed regardless of which check fails.
    """

    EXPECTED_ORDER = [
        "kill_switch", "session", "strategy_status", "operator_status",
        "balance", "single_bet_limit", "daily_limit", "period_limit",
        "stop_loss", "take_profit",
    ]

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(
        global_kill=st.booleans(),
        has_session=st.booleans(),
        strategy_running=st.booleans(),
        operator_active=st.booleans(),
        balance=st.integers(min_value=0, max_value=10_000_000),
        amount=st.integers(min_value=1, max_value=1_000_000),
    )
    async def test_pbt_check_order_invariance(
        self, global_kill, has_session, strategy_running,
        operator_active, balance, amount,
    ):
        """**Validates: Requirements 6.1, 6.2**

        For any input, the checks list order is always
        [kill_switch, session, strategy_status, operator_status, balance,
         single_bet_limit, daily_limit, period_limit, stop_loss, take_profit].
        Short-circuit doesn't change the defined order.
        """
        conn, op, acct, strat, alert_svc = await _pbt_setup()
        try:
            # Configure conditions based on generated inputs
            if not has_session:
                await account_update(
                    conn, account_id=acct["id"], operator_id=op["id"],
                    session_token=None,
                )

            if not strategy_running:
                await strategy_update(
                    conn, strategy_id=strat["id"], operator_id=op["id"],
                    status="stopped",
                )

            if not operator_active:
                from app.models.db_ops import operator_update
                await operator_update(
                    conn, operator_id=op["id"], status="disabled",
                )

            await account_update(
                conn, account_id=acct["id"], operator_id=op["id"],
                balance=balance,
            )

            risk = RiskController(
                db=conn, alert_service=alert_svc,
                operator_id=op["id"], account_id=acct["id"],
                global_kill=global_kill,
            )
            signal = make_signal(strat["id"], amount=amount)
            await risk.check(signal)

            # The _check_log records which checks were executed (in order).
            # It must be a prefix of the expected full order.
            log = risk._check_log
            assert len(log) >= 1, "At least one check must execute"
            assert len(log) <= len(self.EXPECTED_ORDER), "Cannot exceed 10 checks"

            # The executed checks must be the first N items of the expected order
            expected_prefix = self.EXPECTED_ORDER[:len(log)]
            assert log == expected_prefix, (
                f"Check order mismatch: got {log}, expected prefix {expected_prefix}"
            )
        finally:
            await _pbt_teardown(conn)
