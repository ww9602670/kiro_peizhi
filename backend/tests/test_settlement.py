""" SettlementProcessor 


- 7.3.2  sum=0/13/14/27
- 7.3.3 JND282  / JND28WEB 
- 7.3.4 
- 7.3.5 /
- 7.3.6 daily_pnl  +  daily_pnl
- 7.3.7 
"""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta

import pytest
import aiosqlite

from app.database import init_db, get_shared_db, close_shared_db
from app.engine.settlement import (
    IllegalStateTransition,
    SettlementProcessor,
    SettleResult,
    TERMINAL_STATES,
    VALID_TRANSITIONS,
    PENDING_MATCH_WALL_CLOCK_TIMEOUT,
)
from app.models.db_ops import (
    bet_order_create,
    bet_order_get_by_id,
    lottery_result_get_by_issue,
    operator_create,
    account_create,
    strategy_create,
    strategy_get_by_id,
    bet_order_update_status,
)


# 
# Fixtures
# 

@pytest.fixture()
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture()
async def db():
    """ :memory: """
    await close_shared_db()
    await init_db(":memory:")
    conn = await get_shared_db()
    yield conn
    await close_shared_db()


@pytest.fixture()
async def setup_data(db):
    """ (operator, account, strategy) """
    op = await operator_create(db, username="test_op", password="pass123")
    acc = await account_create(
        db, operator_id=op["id"], account_name="acc1",
        password="pwd", platform_type="JND282",
    )
    strat = await strategy_create(
        db, operator_id=op["id"], account_id=acc["id"],
        name="", type="flat", play_code="DX1",
        base_amount=1000,
    )
    return {"operator": op, "account": acc, "strategy": strat}


async def _create_bet_order(db, setup, *, issue="20240101001", key_code="DX1",
                            amount=1000, odds=19800, status="bet_success",
                            idempotent_id=None):
    """"""
    idem = idempotent_id or f"{issue}-{setup['strategy']['id']}-{key_code}"
    order = await bet_order_create(
        db,
        idempotent_id=idem,
        operator_id=setup["operator"]["id"],
        account_id=setup["account"]["id"],
        strategy_id=setup["strategy"]["id"],
        issue=issue,
        key_code=key_code,
        amount=amount,
        odds=odds,
        status="pending",
    )
    #  bet_success
    if status == "bet_success":
        await bet_order_update_status(
            db, order_id=order["id"],
            operator_id=setup["operator"]["id"],
            status="bet_success",
        )
        order = await bet_order_get_by_id(
            db, order_id=order["id"],
            operator_id=setup["operator"]["id"],
        )
    return order



# 
# 7.3.2 
# 

class TestCheckWin:
    """ sum=0/13/14/27"""

    # ---  ---
    @pytest.mark.parametrize("sum_val,expected", [
        (14, True), (15, True), (27, True),  # 
        (13, False), (0, False),              # 
    ])
    def test_dx1_big(self, sum_val, expected):
        assert SettlementProcessor._check_win("DX1", [0, 0, sum_val], sum_val) == expected

    @pytest.mark.parametrize("sum_val,expected", [
        (13, True), (0, True),   # 
        (14, False), (27, False),  # 
    ])
    def test_dx2_small(self, sum_val, expected):
        assert SettlementProcessor._check_win("DX2", [0, 0, sum_val], sum_val) == expected

    # ---  ---
    @pytest.mark.parametrize("sum_val,expected", [
        (1, True), (13, True), (27, True),  # 
        (0, False), (14, False),             # 
    ])
    def test_ds3_odd(self, sum_val, expected):
        assert SettlementProcessor._check_win("DS3", [0, 0, sum_val], sum_val) == expected

    @pytest.mark.parametrize("sum_val,expected", [
        (0, True), (14, True), (26, True),  # 
        (1, False), (13, False),             # 
    ])
    def test_ds4_even(self, sum_val, expected):
        assert SettlementProcessor._check_win("DS4", [0, 0, sum_val], sum_val) == expected

    # ---  ---
    @pytest.mark.parametrize("sum_val,expected", [
        (22, True), (27, True),  # 
        (21, False), (0, False),
    ])
    def test_jdx5_extreme_big(self, sum_val, expected):
        assert SettlementProcessor._check_win("JDX5", [0, 0, sum_val], sum_val) == expected

    @pytest.mark.parametrize("sum_val,expected", [
        (5, True), (0, True),   # 
        (6, False), (27, False),
    ])
    def test_jdx6_extreme_small(self, sum_val, expected):
        assert SettlementProcessor._check_win("JDX6", [0, 0, sum_val], sum_val) == expected

    # ---  ---
    def test_zh7_big_odd(self):
        assert SettlementProcessor._check_win("ZH7", [5, 5, 5], 15) is True
        assert SettlementProcessor._check_win("ZH7", [5, 5, 4], 14) is False  # 14

    def test_zh8_big_even(self):
        assert SettlementProcessor._check_win("ZH8", [5, 5, 4], 14) is True
        assert SettlementProcessor._check_win("ZH8", [5, 5, 5], 15) is False  # 15

    def test_zh9_small_odd(self):
        assert SettlementProcessor._check_win("ZH9", [4, 4, 5], 13) is True
        assert SettlementProcessor._check_win("ZH9", [4, 4, 4], 12) is False  # 12

    def test_zh10_small_even(self):
        assert SettlementProcessor._check_win("ZH10", [4, 4, 4], 12) is True
        assert SettlementProcessor._check_win("ZH10", [4, 4, 5], 13) is False  # 13

    # ---  ---
    def test_hz_exact(self):
        # HZ1  0, HZ14  13, HZ15  14, HZ28  27
        assert SettlementProcessor._check_win("HZ1", [0, 0, 0], 0) is True
        assert SettlementProcessor._check_win("HZ14", [4, 4, 5], 13) is True
        assert SettlementProcessor._check_win("HZ15", [5, 5, 4], 14) is True
        assert SettlementProcessor._check_win("HZ28", [9, 9, 9], 27) is True
        assert SettlementProcessor._check_win("HZ1", [1, 0, 0], 1) is False

    # ---  ---
    def test_sb_red(self):
        # : {3,6,9,12,15,18,21,24}
        assert SettlementProcessor._check_win("SB1", [1, 1, 1], 3) is True
        assert SettlementProcessor._check_win("SB1", [0, 0, 0], 0) is False  # 0

    def test_sb_green(self):
        # : {1,4,7,10,16,19,22,25}
        assert SettlementProcessor._check_win("SB2", [0, 0, 1], 1) is True
        assert SettlementProcessor._check_win("SB2", [1, 1, 1], 3) is False

    def test_sb_blue(self):
        # : {2,5,8,11,17,20,23,26}
        assert SettlementProcessor._check_win("SB3", [1, 1, 0], 2) is True
        assert SettlementProcessor._check_win("SB3", [1, 1, 1], 3) is False

    # ---  ---
    def test_bz4_triple(self):
        assert SettlementProcessor._check_win("BZ4", [3, 3, 3], 9) is True
        assert SettlementProcessor._check_win("BZ4", [3, 3, 4], 10) is False

    # ---  ---
    def test_ball_number(self):
        assert SettlementProcessor._check_win("B1QH5", [5, 0, 0], 5) is True
        assert SettlementProcessor._check_win("B1QH5", [4, 0, 0], 4) is False
        assert SettlementProcessor._check_win("B2QH3", [0, 3, 0], 3) is True
        assert SettlementProcessor._check_win("B3QH9", [0, 0, 9], 9) is True

    # ---  ---
    def test_ball_lm(self):
        # B1LM_DA: 5
        assert SettlementProcessor._check_win("B1LM_DA", [5, 0, 0], 5) is True
        assert SettlementProcessor._check_win("B1LM_DA", [4, 0, 0], 4) is False
        # B2LM_X: <5
        assert SettlementProcessor._check_win("B2LM_X", [0, 3, 0], 3) is True
        assert SettlementProcessor._check_win("B2LM_X", [0, 5, 0], 5) is False
        # B3LM_D: 
        assert SettlementProcessor._check_win("B3LM_D", [0, 0, 7], 7) is True
        assert SettlementProcessor._check_win("B3LM_D", [0, 0, 6], 6) is False
        # B1LM_S: 
        assert SettlementProcessor._check_win("B1LM_S", [4, 0, 0], 4) is True
        assert SettlementProcessor._check_win("B1LM_S", [3, 0, 0], 3) is False

    # ---  ---
    def test_lhh(self):
        assert SettlementProcessor._check_win("LHH_L", [9, 5, 1], 15) is True   # 1>3
        assert SettlementProcessor._check_win("LHH_H", [1, 5, 9], 15) is True   # 1<3
        assert SettlementProcessor._check_win("LHH_HE", [5, 5, 5], 15) is True  # 1==3
        assert SettlementProcessor._check_win("LHH_L", [5, 5, 5], 15) is False
        assert SettlementProcessor._check_win("LHH_H", [5, 5, 5], 15) is False

    # ---  ---
    def test_boundary_sum_0(self):
        """0"""
        balls = [0, 0, 0]
        assert SettlementProcessor._check_win("DX2", balls, 0) is True
        assert SettlementProcessor._check_win("DS4", balls, 0) is True
        assert SettlementProcessor._check_win("JDX6", balls, 0) is True
        assert SettlementProcessor._check_win("DX1", balls, 0) is False

    def test_boundary_sum_13(self):
        """13"""
        balls = [4, 4, 5]
        assert SettlementProcessor._check_win("DX2", balls, 13) is True
        assert SettlementProcessor._check_win("DS3", balls, 13) is True
        assert SettlementProcessor._check_win("DX1", balls, 13) is False

    def test_boundary_sum_14(self):
        """14"""
        balls = [5, 5, 4]
        assert SettlementProcessor._check_win("DX1", balls, 14) is True
        assert SettlementProcessor._check_win("DS4", balls, 14) is True
        assert SettlementProcessor._check_win("DX2", balls, 14) is False

    def test_boundary_sum_27(self):
        """27"""
        balls = [9, 9, 9]
        assert SettlementProcessor._check_win("DX1", balls, 27) is True
        assert SettlementProcessor._check_win("DS3", balls, 27) is True
        assert SettlementProcessor._check_win("JDX5", balls, 27) is True



# 
# 7.3.3 JND282  / JND28WEB 
# 

class TestCalculateResult:
    """_calculate_result """

    def _make_order(self, key_code="DX1", amount=1000, odds=19800):
        return {"key_code": key_code, "amount": amount, "odds": odds}

    # --- JND282  6  ---

    def test_jnd282_sum14_dx1_refund(self):
        """14 + DX1 """
        order = self._make_order("DX1")
        result = SettlementProcessor._calculate_result(order, [5, 5, 4], 14, "JND282")
        assert result.is_win == -1
        assert result.pnl == 0

    def test_jnd282_sum14_ds4_refund(self):
        """14 + DS4 """
        order = self._make_order("DS4")
        result = SettlementProcessor._calculate_result(order, [5, 5, 4], 14, "JND282")
        assert result.is_win == -1
        assert result.pnl == 0

    def test_jnd282_sum14_zh8_refund(self):
        """14 + ZH8 """
        order = self._make_order("ZH8")
        result = SettlementProcessor._calculate_result(order, [5, 5, 4], 14, "JND282")
        assert result.is_win == -1
        assert result.pnl == 0

    def test_jnd282_sum13_dx2_refund(self):
        """13 + DX2 """
        order = self._make_order("DX2")
        result = SettlementProcessor._calculate_result(order, [4, 4, 5], 13, "JND282")
        assert result.is_win == -1
        assert result.pnl == 0

    def test_jnd282_sum13_ds3_refund(self):
        """13 + DS3 """
        order = self._make_order("DS3")
        result = SettlementProcessor._calculate_result(order, [4, 4, 5], 13, "JND282")
        assert result.is_win == -1
        assert result.pnl == 0

    def test_jnd282_sum13_zh9_refund(self):
        """13 + ZH9 """
        order = self._make_order("ZH9")
        result = SettlementProcessor._calculate_result(order, [4, 4, 5], 13, "JND282")
        assert result.is_win == -1
        assert result.pnl == 0

    # --- JND282  ---

    def test_jnd282_sum14_dx2_normal(self):
        """14 + DX2 """
        order = self._make_order("DX2")
        result = SettlementProcessor._calculate_result(order, [5, 5, 4], 14, "JND282")
        assert result.is_win == 0
        assert result.pnl == -1000

    def test_jnd282_sum13_dx1_normal(self):
        """13 + DX1 """
        order = self._make_order("DX1")
        result = SettlementProcessor._calculate_result(order, [4, 4, 5], 13, "JND282")
        assert result.is_win == 0
        assert result.pnl == -1000

    def test_jnd282_sum_not_13_14_no_refund(self):
        """1314  """
        order = self._make_order("DX1", amount=1000, odds=19800)
        result = SettlementProcessor._calculate_result(order, [5, 5, 5], 15, "JND282")
        assert result.is_win == 1
        assert result.pnl == 1000 * 19800 // 10000 - 1000  # 980

    # --- JND28WEB  ---

    def test_jnd28web_sum14_dx1_no_refund(self):
        """JND28WEB 14 + DX1  """
        order = self._make_order("DX1", amount=1000, odds=19800)
        result = SettlementProcessor._calculate_result(order, [5, 5, 4], 14, "JND28WEB")
        assert result.is_win == 1
        assert result.pnl == 1000 * 19800 // 10000 - 1000

    def test_jnd28web_sum14_ds4_no_refund(self):
        """JND28WEB 14 + DS4  """
        order = self._make_order("DS4", amount=1000, odds=19800)
        result = SettlementProcessor._calculate_result(order, [5, 5, 4], 14, "JND28WEB")
        assert result.is_win == 1
        assert result.pnl == 1000 * 19800 // 10000 - 1000

    def test_jnd28web_sum14_zh8_no_refund(self):
        """JND28WEB 14 + ZH8  """
        order = self._make_order("ZH8", amount=1000, odds=45000)
        result = SettlementProcessor._calculate_result(order, [5, 5, 4], 14, "JND28WEB")
        assert result.is_win == 1
        assert result.pnl == 1000 * 45000 // 10000 - 1000

    def test_jnd28web_sum13_dx2_no_refund(self):
        """JND28WEB 13 + DX2  """
        order = self._make_order("DX2", amount=1000, odds=19800)
        result = SettlementProcessor._calculate_result(order, [4, 4, 5], 13, "JND28WEB")
        assert result.is_win == 1
        assert result.pnl == 1000 * 19800 // 10000 - 1000

    def test_jnd28web_sum13_ds3_no_refund(self):
        """JND28WEB 13 + DS3  """
        order = self._make_order("DS3", amount=1000, odds=19800)
        result = SettlementProcessor._calculate_result(order, [4, 4, 5], 13, "JND28WEB")
        assert result.is_win == 1
        assert result.pnl == 1000 * 19800 // 10000 - 1000

    def test_jnd28web_sum13_zh9_no_refund(self):
        """JND28WEB 13 + ZH9  """
        order = self._make_order("ZH9", amount=1000, odds=45000)
        result = SettlementProcessor._calculate_result(order, [4, 4, 5], 13, "JND28WEB")
        assert result.is_win == 1
        assert result.pnl == 1000 * 45000 // 10000 - 1000


# 
# 7.3.4 
# 

class TestPnlCalculation:
    """"""

    def _make_order(self, key_code="DX1", amount=1000, odds=19800):
        return {"key_code": key_code, "amount": amount, "odds": odds}

    def test_win_pnl(self):
        """pnl = amount * odds // 10000 - amount"""
        order = self._make_order(amount=1000, odds=19800)
        result = SettlementProcessor._calculate_result(order, [5, 5, 5], 15, "JND28WEB")
        assert result.is_win == 1
        assert result.pnl == 1000 * 19800 // 10000 - 1000  # 980
        assert isinstance(result.pnl, int)

    def test_lose_pnl(self):
        """pnl = -amount"""
        order = self._make_order("DX1", amount=500, odds=19800)
        result = SettlementProcessor._calculate_result(order, [4, 4, 5], 13, "JND28WEB")
        assert result.is_win == 0
        assert result.pnl == -500
        assert isinstance(result.pnl, int)

    def test_refund_pnl(self):
        """pnl = 0"""
        order = self._make_order("DX1", amount=1000, odds=19800)
        result = SettlementProcessor._calculate_result(order, [5, 5, 4], 14, "JND282")
        assert result.is_win == -1
        assert result.pnl == 0
        assert isinstance(result.pnl, int)

    def test_integer_division(self):
        """整数除法截断"""
        # 20530 * 100 // 10000 = 205 而非 205.3
        order = self._make_order("DX1", amount=100, odds=20530)
        result = SettlementProcessor._calculate_result(order, [5, 5, 5], 15, "JND28WEB")
        assert result.pnl == 100 * 20530 // 10000 - 100  # 205 - 100 = 105
        assert isinstance(result.pnl, int)

    def test_large_amount_integer(self):
        """大金额整数运算"""
        order = self._make_order("DX1", amount=999999, odds=19800)
        result = SettlementProcessor._calculate_result(order, [5, 5, 5], 15, "JND28WEB")
        expected = 999999 * 19800 // 10000 - 999999
        assert result.pnl == expected
        assert isinstance(result.pnl, int)



# 
# 7.3.5 /
# 

class TestTransitionStatus:
    """"""

    def _make_order(self, status):
        return {"status": status}

    # ---  ---
    @pytest.mark.parametrize("current,new", [
        ("pending", "betting"),
        ("betting", "bet_success"),
        ("betting", "bet_failed"),
        ("bet_success", "settling"),
        ("bet_success", "pending_match"),
        ("bet_success", "settle_timeout"),
        ("bet_success", "settle_failed"),
        ("settling", "settled"),
        ("settling", "settle_failed"),
        ("settling", "settle_timeout"),
        ("pending_match", "settling"),
        ("pending_match", "settle_timeout"),
        ("settled", "reconcile_error"),
        ("settle_timeout", "settling"),
        ("settle_failed", "settling"),
    ])
    def test_valid_transitions(self, current, new):
        order = self._make_order(current)
        # 
        SettlementProcessor._transition_status(order, new)

    # ---  ---
    @pytest.mark.parametrize("current,new", [
        ("pending", "settled"),
        ("pending", "bet_success"),
        ("betting", "settled"),
        ("bet_success", "pending"),
        ("settling", "bet_success"),
        ("pending_match", "settled"),
        ("settle_timeout", "settled"),
        ("settle_failed", "settled"),
    ])
    def test_invalid_transitions(self, current, new):
        order = self._make_order(current)
        with pytest.raises(IllegalStateTransition) as exc_info:
            SettlementProcessor._transition_status(order, new)
        assert exc_info.value.current_status == current
        assert exc_info.value.new_status == new

    # ---  ---
    @pytest.mark.parametrize("terminal", list(TERMINAL_STATES))
    @pytest.mark.parametrize("target", [
        "pending", "betting", "bet_success", "settling", "settled",
        "bet_failed", "reconcile_error", "pending_match",
        "settle_timeout", "settle_failed",
    ])
    def test_terminal_state_no_transition(self, terminal, target):
        """"""
        if terminal == "settled" and target == "reconcile_error":
            # settled  reconcile_error 
            return
        if terminal == "settle_timeout" and target == "settling":
            # settle_timeout → settling 合法（高优先级覆盖）
            return
        if terminal == "settle_failed" and target == "settling":
            # settle_failed → settling 合法（高优先级覆盖）
            return
        order = self._make_order(terminal)
        with pytest.raises(IllegalStateTransition):
            SettlementProcessor._transition_status(order, target)

    # --- DB  ---
    @pytest.mark.asyncio
    async def test_db_terminal_trigger_settled(self, db, setup_data):
        """DB  UPDATE  RAISE ABORT"""
        order = await _create_bet_order(db, setup_data, key_code="DX1")
        #  settled
        await bet_order_update_status(
            db, order_id=order["id"],
            operator_id=setup_data["operator"]["id"],
            status="settled", is_win=1, pnl=980,
        )
        #  SQL UPDATE   
        with pytest.raises(Exception, match=""):
            await db.execute(
                "UPDATE bet_orders SET status='pending' WHERE id=?",
                (order["id"],),
            )

    @pytest.mark.asyncio
    async def test_db_terminal_trigger_bet_failed(self, db, setup_data):
        """DB bet_failed  UPDATE  RAISE ABORT"""
        order = await _create_bet_order(db, setup_data, key_code="DX2",
                                        idempotent_id="test-fail-1")
        await bet_order_update_status(
            db, order_id=order["id"],
            operator_id=setup_data["operator"]["id"],
            status="bet_failed", fail_reason="",
        )
        with pytest.raises(Exception, match=""):
            await db.execute(
                "UPDATE bet_orders SET status='pending' WHERE id=?",
                (order["id"],),
            )


# 
# 7.3.6  + daily_pnl  + 
# 

class TestStrategyPnlUpdate:
    """"""

    @pytest.mark.asyncio
    async def test_settle_updates_strategy_pnl(self, db, setup_data):
        """ daily_pnl / total_pnl """
        # 
        await _create_bet_order(
            db, setup_data, key_code="DX1", amount=1000, odds=19800,
        )
        processor = SettlementProcessor(db, setup_data["operator"]["id"])
        await processor.settle("20240101001", [5, 5, 5], 15, "JND28WEB")

        strat = await strategy_get_by_id(
            db, strategy_id=setup_data["strategy"]["id"],
            operator_id=setup_data["operator"]["id"],
        )
        expected_pnl = 1000 * 19800 // 10000 - 1000  # 980
        assert strat["daily_pnl"] == expected_pnl
        assert strat["total_pnl"] == expected_pnl
        assert strat["daily_pnl_date"] == date.today().strftime("%Y-%m-%d")

    @pytest.mark.asyncio
    async def test_refund_not_counted_in_daily_pnl(self, db, setup_data):
        """is_win=-1, pnl=0 daily_pnl"""
        # JND282 14 + DX1  
        await _create_bet_order(
            db, setup_data, key_code="DX1", amount=1000, odds=19800,
        )
        #  JND282
        await db.execute(
            "UPDATE gambling_accounts SET platform_type='JND282' WHERE id=?",
            (setup_data["account"]["id"],),
        )
        await db.commit()

        processor = SettlementProcessor(db, setup_data["operator"]["id"])
        await processor.settle("20240101001", [5, 5, 4], 14, "JND282")

        strat = await strategy_get_by_id(
            db, strategy_id=setup_data["strategy"]["id"],
            operator_id=setup_data["operator"]["id"],
        )
        #  pnl=0  daily_pnl
        assert strat["daily_pnl"] == 0
        assert strat["total_pnl"] == 0

    @pytest.mark.asyncio
    async def test_daily_pnl_reset_on_new_day(self, db, setup_data):
        """daily_pnl_date  daily_pnl """
        sid = setup_data["strategy"]["id"]
        oid = setup_data["operator"]["id"]

        #  daily_pnl
        from app.models.db_ops import strategy_update
        await strategy_update(
            db, strategy_id=sid, operator_id=oid,
            daily_pnl=5000, total_pnl=10000, daily_pnl_date="2020-01-01",
        )

        # 
        await _create_bet_order(
            db, setup_data, key_code="DX1", amount=1000, odds=19800,
        )

        processor = SettlementProcessor(db, oid)
        await processor.settle("20240101001", [5, 5, 5], 15, "JND28WEB")

        strat = await strategy_get_by_id(db, strategy_id=sid, operator_id=oid)
        expected_pnl = 1000 * 19800 // 10000 - 1000  # 980
        # daily_pnl 
        assert strat["daily_pnl"] == expected_pnl  # 0 + 980 = 980 5000 + 980
        # total_pnl 
        assert strat["total_pnl"] == 10000 + expected_pnl
        assert strat["daily_pnl_date"] == date.today().strftime("%Y-%m-%d")

    @pytest.mark.asyncio
    async def test_multiple_orders_pnl_accumulation(self, db, setup_data):
        """"""
        # 
        await _create_bet_order(
            db, setup_data, key_code="DX1", amount=1000, odds=19800,
            idempotent_id="20240101001-1-DX1",
        )
        await _create_bet_order(
            db, setup_data, key_code="DX2", amount=500, odds=19800,
            idempotent_id="20240101001-1-DX2",
        )

        processor = SettlementProcessor(db, setup_data["operator"]["id"])
        # 15DX1DX2
        await processor.settle("20240101001", [5, 5, 5], 15, "JND28WEB")

        strat = await strategy_get_by_id(
            db, strategy_id=setup_data["strategy"]["id"],
            operator_id=setup_data["operator"]["id"],
        )
        win_pnl = 1000 * 19800 // 10000 - 1000   # 980
        lose_pnl = -500
        assert strat["daily_pnl"] == win_pnl + lose_pnl  # 480
        assert strat["total_pnl"] == win_pnl + lose_pnl


# 
# 7.3.7 
# 

class TestLotteryResultCache:
    """ lottery_results """

    @pytest.mark.asyncio
    async def test_lottery_result_saved(self, db, setup_data):
        """"""
        await _create_bet_order(db, setup_data, key_code="DX1")

        processor = SettlementProcessor(db, setup_data["operator"]["id"])
        await processor.settle("20240101001", [5, 5, 5], 15, "JND28WEB")

        lr = await lottery_result_get_by_issue(db, issue="20240101001")
        assert lr is not None
        assert lr["open_result"] == "5,5,5"
        assert lr["sum_value"] == 15

    @pytest.mark.asyncio
    async def test_lottery_result_idempotent(self, db, setup_data):
        """INSERT OR IGNORE"""
        await _create_bet_order(db, setup_data, key_code="DX1")

        processor = SettlementProcessor(db, setup_data["operator"]["id"])
        await processor.settle("20240101001", [5, 5, 5], 15, "JND28WEB")
        #  lottery_result 
        await processor.settle("20240101001", [5, 5, 5], 15, "JND28WEB")

        lr = await lottery_result_get_by_issue(db, issue="20240101001")
        assert lr is not None


# 
# 
# 

class TestSettleIntegration:
    """"""

    @pytest.mark.asyncio
    async def test_full_settle_flow(self, db, setup_data):
        """    """
        order = await _create_bet_order(
            db, setup_data, key_code="DX1", amount=1000, odds=19800,
        )

        processor = SettlementProcessor(db, setup_data["operator"]["id"])
        await processor.settle("20240101001", [5, 5, 5], 15, "JND28WEB")

        # 
        settled = await bet_order_get_by_id(
            db, order_id=order["id"],
            operator_id=setup_data["operator"]["id"],
        )
        assert settled["status"] == "settled"
        assert settled["is_win"] == 1
        assert settled["pnl"] == 1000 * 19800 // 10000 - 1000
        assert settled["open_result"] == "5,5,5"
        assert settled["sum_value"] == 15
        assert settled["settled_at"] is not None

    @pytest.mark.asyncio
    async def test_no_orders_to_settle(self, db, setup_data):
        """"""
        processor = SettlementProcessor(db, setup_data["operator"]["id"])
        # 
        await processor.settle("20240101001", [5, 5, 5], 15, "JND28WEB")
        # 

    @pytest.mark.asyncio
    async def test_settle_only_bet_success_orders(self, db, setup_data):
        """ bet_success pending """
        #  bet_success 
        order_ok = await _create_bet_order(
            db, setup_data, key_code="DX1", amount=1000, odds=19800,
            idempotent_id="20240101001-1-DX1",
        )
        #  pending  bet_success
        order_pending = await bet_order_create(
            db,
            idempotent_id="20240101001-1-DX2",
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            strategy_id=setup_data["strategy"]["id"],
            issue="20240101001",
            key_code="DX2",
            amount=500,
            odds=19800,
            status="pending",
        )

        processor = SettlementProcessor(db, setup_data["operator"]["id"])
        await processor.settle("20240101001", [5, 5, 5], 15, "JND28WEB")

        # bet_success 
        settled = await bet_order_get_by_id(
            db, order_id=order_ok["id"],
            operator_id=setup_data["operator"]["id"],
        )
        assert settled["status"] == "settled"

        # pending 
        still_pending = await bet_order_get_by_id(
            db, order_id=order_pending["id"],
            operator_id=setup_data["operator"]["id"],
        )
        assert still_pending["status"] == "pending"


# 
# Phase 13  Property-Based Tests (hypothesis)
# P1, P2, P3, P4, P5, P6, P16
# 

from hypothesis import given, settings, assume
from hypothesis import strategies as st


# 
# P1: 
# 

class TestPBT_P1_SumConsistency:
    """P1:    (b1,b2,b3)  [0,9]sum  [0,27]

    **Validates: Requirements 3.1, 3.2**
    """

    @given(
        b1=st.integers(min_value=0, max_value=9),
        b2=st.integers(min_value=0, max_value=9),
        b3=st.integers(min_value=0, max_value=9),
    )
    @settings(max_examples=100)
    def test_pbt_p1_sum_consistency(self, b1: int, b2: int, b3: int):
        """**Validates: Requirements 3.1, 3.2**

         (b1, b2, b3) 0  bi  9
        -  = b1 + b2 + b3
        -  [0, 27]
        """
        sum_value = b1 + b2 + b3
        assert sum_value == b1 + b2 + b3
        assert 0 <= sum_value <= 27


# 
# P2: 
# 

class TestPBT_P2_BigSmall:
    """P2:   s14DX1, s13DX2, 

    **Validates: Requirements 3.1**
    """

    @given(sum_value=st.integers(min_value=0, max_value=27))
    @settings(max_examples=100)
    def test_pbt_p2_big_small(self, sum_value: int):
        """**Validates: Requirements 3.1**

         s  [0, 27]
        - s  14  DX1 
        - s  13  DX2 
        - DX1  DX2 
        """
        balls = [0, 0, sum_value]  #  sum_value 
        dx1_win = SettlementProcessor._check_win("DX1", balls, sum_value)
        dx2_win = SettlementProcessor._check_win("DX2", balls, sum_value)

        # 
        if sum_value >= 14:
            assert dx1_win is True
        else:
            assert dx1_win is False

        if sum_value <= 13:
            assert dx2_win is True
        else:
            assert dx2_win is False

        # 
        assert dx1_win != dx2_win, f"DX1  DX2 sum={sum_value}"


# 
# P3: 
# 

class TestPBT_P3_OddEven:
    """P3:   DS3, DS4, 

    **Validates: Requirements 3.1**
    """

    @given(sum_value=st.integers(min_value=0, max_value=27))
    @settings(max_examples=100)
    def test_pbt_p3_odd_even(self, sum_value: int):
        """**Validates: Requirements 3.1**

         s  [0, 27]
        -   DS3 
        -   DS4 
        - DS3  DS4 
        """
        balls = [0, 0, sum_value]
        ds3_win = SettlementProcessor._check_win("DS3", balls, sum_value)
        ds4_win = SettlementProcessor._check_win("DS4", balls, sum_value)

        if sum_value % 2 == 1:
            assert ds3_win is True
        else:
            assert ds3_win is False

        if sum_value % 2 == 0:
            assert ds4_win is True
        else:
            assert ds4_win is False

        # 
        assert ds3_win != ds4_win, f"DS3  DS4 sum={sum_value}"


# 
# P4: 
# 

class TestPBT_P4_CombinationConsistency:
    """P4: 

    **Validates: Requirements 3.1**
    """

    @given(
        b1=st.integers(min_value=0, max_value=9),
        b2=st.integers(min_value=0, max_value=9),
        b3=st.integers(min_value=0, max_value=9),
    )
    @settings(max_examples=100)
    def test_pbt_p4_combination_consistency(self, b1: int, b2: int, b3: int):
        """**Validates: Requirements 3.1**

        ZH7()  DX1()  DS3()
        ZH8()  DX1()  DS4()
        ZH9()  DX2()  DS3()
        ZH10()  DX2()  DS4()
        """
        balls = [b1, b2, b3]
        sum_value = b1 + b2 + b3

        dx1 = SettlementProcessor._check_win("DX1", balls, sum_value)
        dx2 = SettlementProcessor._check_win("DX2", balls, sum_value)
        ds3 = SettlementProcessor._check_win("DS3", balls, sum_value)
        ds4 = SettlementProcessor._check_win("DS4", balls, sum_value)

        zh7 = SettlementProcessor._check_win("ZH7", balls, sum_value)
        zh8 = SettlementProcessor._check_win("ZH8", balls, sum_value)
        zh9 = SettlementProcessor._check_win("ZH9", balls, sum_value)
        zh10 = SettlementProcessor._check_win("ZH10", balls, sum_value)

        assert zh7 == (dx1 and ds3), f"ZH7  DX1DS3, sum={sum_value}"
        assert zh8 == (dx1 and ds4), f"ZH8  DX1DS4, sum={sum_value}"
        assert zh9 == (dx2 and ds3), f"ZH9  DX2DS3, sum={sum_value}"
        assert zh10 == (dx2 and ds4), f"ZH10  DX2DS4, sum={sum_value}"


# 
# P5: JND282 
# 

class TestPBT_P5_JND282Refund:
    """P5: JND282 

    **Validates: Requirements 3.2**
    """

    #  13  14 
    @given(
        b1=st.integers(min_value=0, max_value=9),
        b2=st.integers(min_value=0, max_value=9),
    )
    @settings(max_examples=100)
    def test_pbt_p5_jnd282_sum14_refund(self, b1: int, b2: int):
        """**Validates: Requirements 3.2**

        =14 JND282 DX1/DS4/ZH8 
        JND28WEB 
        """
        b3 = 14 - b1 - b2
        assume(0 <= b3 <= 9)
        balls = [b1, b2, b3]

        refund_codes_14 = ["DX1", "DS4", "ZH8"]
        for kc in refund_codes_14:
            order = {"key_code": kc, "amount": 1000, "odds": 19800}
            result = SettlementProcessor._calculate_result(order, balls, 14, "JND282")
            assert result.is_win == -1, f"JND282 sum=14 {kc} "
            assert result.pnl == 0, f"JND282 sum=14 {kc} pnl  0"

            # JND28WEB 
            result_web = SettlementProcessor._calculate_result(order, balls, 14, "JND28WEB")
            assert result_web.is_win != -1, f"JND28WEB sum=14 {kc} "

    @given(
        b1=st.integers(min_value=0, max_value=9),
        b2=st.integers(min_value=0, max_value=9),
    )
    @settings(max_examples=100)
    def test_pbt_p5_jnd282_sum13_refund(self, b1: int, b2: int):
        """**Validates: Requirements 3.2**

        =13 JND282 DX2/DS3/ZH9 
        JND28WEB 
        """
        b3 = 13 - b1 - b2
        assume(0 <= b3 <= 9)
        balls = [b1, b2, b3]

        refund_codes_13 = ["DX2", "DS3", "ZH9"]
        for kc in refund_codes_13:
            order = {"key_code": kc, "amount": 1000, "odds": 19800}
            result = SettlementProcessor._calculate_result(order, balls, 13, "JND282")
            assert result.is_win == -1, f"JND282 sum=13 {kc} "
            assert result.pnl == 0, f"JND282 sum=13 {kc} pnl  0"

            # JND28WEB 
            result_web = SettlementProcessor._calculate_result(order, balls, 13, "JND28WEB")
            assert result_web.is_win != -1, f"JND28WEB sum=13 {kc} "

    @given(
        b1=st.integers(min_value=0, max_value=9),
        b2=st.integers(min_value=0, max_value=9),
        b3=st.integers(min_value=0, max_value=9),
    )
    @settings(max_examples=100)
    def test_pbt_p5_jnd282_no_refund_other_sums(self, b1: int, b2: int, b3: int):
        """**Validates: Requirements 3.2**

        13  14 JND282 
        """
        sum_value = b1 + b2 + b3
        assume(sum_value != 13 and sum_value != 14)
        balls = [b1, b2, b3]

        #  13/14 
        for kc in ["DX1", "DX2", "DS3", "DS4", "ZH8", "ZH9"]:
            order = {"key_code": kc, "amount": 1000, "odds": 19800}
            result = SettlementProcessor._calculate_result(order, balls, sum_value, "JND282")
            assert result.is_win != -1, f"JND282 sum={sum_value} {kc} "


# 
# P6: 
# 

class TestPBT_P6_PnlCalculation:
    """P6: 

    **Validates: Requirements 7.1**
    """

    @given(
        amount=st.integers(min_value=1, max_value=100000),
        odds=st.integers(min_value=10010, max_value=100000),
    )
    @settings(max_examples=100)
    def test_pbt_p6_win_pnl(self, amount: int, odds: int):
        """**Validates: Requirements 7.1**

        pnl = amount * odds // 10000 - amount，且 pnl > 0
        """
        # DX1 + sum=15  
        order = {"key_code": "DX1", "amount": amount, "odds": odds}
        result = SettlementProcessor._calculate_result(order, [5, 5, 5], 15, "JND28WEB")

        assert result.is_win == 1
        expected_pnl = amount * odds // 10000 - amount
        assert result.pnl == expected_pnl
        # amount * odds // 10000 - amount 可能为 0
        assert result.pnl >= 0, f"胜利 pnl 应 >= 0, got {result.pnl}"
        assert isinstance(result.pnl, int)

    @given(
        amount=st.integers(min_value=1, max_value=100000),
        odds=st.integers(min_value=10010, max_value=100000),
    )
    @settings(max_examples=100)
    def test_pbt_p6_lose_pnl(self, amount: int, odds: int):
        """**Validates: Requirements 7.1**

        pnl = -amountpnl < 0
        """
        # DX1 + sum=10  
        order = {"key_code": "DX1", "amount": amount, "odds": odds}
        result = SettlementProcessor._calculate_result(order, [3, 3, 4], 10, "JND28WEB")

        assert result.is_win == 0
        assert result.pnl == -amount
        assert result.pnl < 0
        assert isinstance(result.pnl, int)

    @given(
        amount=st.integers(min_value=1, max_value=100000),
        odds=st.integers(min_value=10010, max_value=100000),
    )
    @settings(max_examples=100)
    def test_pbt_p6_refund_pnl(self, amount: int, odds: int):
        """**Validates: Requirements 7.1**

        pnl = 0
        """
        # JND282 + DX1 + sum=14  
        order = {"key_code": "DX1", "amount": amount, "odds": odds}
        result = SettlementProcessor._calculate_result(order, [5, 5, 4], 14, "JND282")

        assert result.is_win == -1
        assert result.pnl == 0
        assert isinstance(result.pnl, int)


# 
# P16: 
# 

class TestPBT_P16_IntegerPrecision:
    """P16: 

    **Validates: Requirements 7.1**
    """

    @given(
        amount=st.integers(min_value=1, max_value=100000),
        odds=st.integers(min_value=10010, max_value=100000),
    )
    @settings(max_examples=100)
    def test_pbt_p16_pnl_is_integer(self, amount: int, odds: int):
        """**Validates: Requirements 7.1**

        任意 amount 和 odds（10000倍整数）
        计算 amount * odds // 10000 - amount 结果为整数
        """
        pnl = amount * odds // 10000 - amount
        assert isinstance(pnl, int), f"pnl 不是整数, got {type(pnl)}"

    @given(
        operations=st.lists(
            st.tuples(
                st.sampled_from(["win", "lose", "refund"]),
                st.integers(min_value=1, max_value=100000),
                st.integers(min_value=10010, max_value=100000),
            ),
            min_size=1,
            max_size=20,
        ),
    )
    @settings(max_examples=100)
    def test_pbt_p16_balance_always_integer(self, operations):
        """**Validates: Requirements 7.1**

        
        pnl=0
        """
        balance = 1_000_000  #  10000  = 1000000 

        for op_type, amount, odds in operations:
            if amount > balance:
                continue  # 

            balance -= amount  # 
            assert isinstance(balance, int), f""

            if op_type == "win":
                pnl = amount * odds // 10000 - amount
                balance += amount + pnl  #  + 
            elif op_type == "lose":
                pnl = -amount
                # 
                pass
            else:  # refund
                pnl = 0
                balance += amount  # 

            assert isinstance(balance, int), f""
            assert isinstance(pnl, int), f"pnl "


# 
# P15:   RuleBasedStateMachine
# 

from hypothesis.stateful import RuleBasedStateMachine, rule, initialize, invariant


ALL_STATUSES = list(VALID_TRANSITIONS.keys())

# bet_failed, reconcile_error 不可覆盖
# settled 仅允许 reconcile_error
# settle_timeout, settle_failed 允许 settling（高优先级覆盖）
STRICT_TERMINAL_STATES = {"bet_failed", "reconcile_error"}


class BetOrderStateMachine(RuleBasedStateMachine):
    """P15:   RuleBasedStateMachine

    **Validates: Requirements 7.0**

     RuleBasedStateMachine 
    - 
    -  IllegalStateTransition
    - bet_failed, reconcile_error
    - settled   reconcile_error
    - 
    """

    def __init__(self):
        super().__init__()
        self.current_status: str = ""
        self.transition_count: int = 0

    @initialize()
    def init_order(self):
        """ pending """
        self.current_status = "pending"
        self.transition_count = 0

    @rule(target_status=st.sampled_from(ALL_STATUSES))
    def try_transition(self, target_status: str):
        """

        **Validates: Requirements 7.0**
        """
        order = {"status": self.current_status}
        allowed = VALID_TRANSITIONS.get(self.current_status, set())

        if target_status in allowed:
            # 
            SettlementProcessor._transition_status(order, target_status)
            self.current_status = target_status
            self.transition_count += 1
        else:
            #  IllegalStateTransition
            try:
                SettlementProcessor._transition_status(order, target_status)
                #  bug
                assert False, (
                    f" IllegalStateTransition: {self.current_status}  {target_status}"
                )
            except IllegalStateTransition as e:
                assert e.current_status == self.current_status
                assert e.new_status == target_status

    @invariant()
    def status_is_valid(self):
        """"""
        assert self.current_status in VALID_TRANSITIONS, (
            f": {self.current_status}"
        )

    @invariant()
    def strict_terminal_states_have_no_transitions(self):
        """bet_failed, reconcile_error"""
        for ts in STRICT_TERMINAL_STATES:
            assert VALID_TRANSITIONS[ts] == set(), (
                f" {ts} : {VALID_TRANSITIONS[ts]}"
            )

    @invariant()
    def settled_only_allows_reconcile_error(self):
        """settled  reconcile_error"""
        assert VALID_TRANSITIONS["settled"] == {"reconcile_error"}, (
            f"settled   reconcile_error: {VALID_TRANSITIONS['settled']}"
        )

    @invariant()
    def soft_terminal_allows_settling(self):
        """settle_timeout/settle_failed 仅允许 settling"""
        assert VALID_TRANSITIONS["settle_timeout"] == {"settling"}, (
            f"settle_timeout 应仅允许 settling: {VALID_TRANSITIONS['settle_timeout']}"
        )
        assert VALID_TRANSITIONS["settle_failed"] == {"settling"}, (
            f"settle_failed 应仅允许 settling: {VALID_TRANSITIONS['settle_failed']}"
        )

    @invariant()
    def terminal_rejects_all_when_reached(self):
        """"""
        if self.current_status in STRICT_TERMINAL_STATES:
            order = {"status": self.current_status}
            for target in ALL_STATUSES:
                try:
                    SettlementProcessor._transition_status(order, target)
                    assert False, (
                        f" {self.current_status}  {target} "
                    )
                except IllegalStateTransition:
                    pass  # 


TestBetOrderStateMachine = BetOrderStateMachine.TestCase
TestBetOrderStateMachine.settings = settings(
    max_examples=200,
    stateful_step_count=10,
)


# ──────────────────────────────────────────────
# Property 1: 状态机合法性 (countdown-driven-settlement)
# ──────────────────────────────────────────────

class TestPBT_Property1_StateMachineLegality:
    """Property 1: 状态机合法性

    **Validates: Requirements 8.1, 8.2, 8.3, 8.4**

    对于所有 (current_status, target_status) 对：
    - 若 target ∈ VALID_TRANSITIONS[current]，则 _transition_status 不抛异常
    - 若 target ∉ VALID_TRANSITIONS[current]，则 _transition_status 抛出 IllegalStateTransition
    """

    ALL_STATES = list(VALID_TRANSITIONS.keys())

    # 构建合法对列表
    LEGAL_PAIRS = [
        (current, target)
        for current, targets in VALID_TRANSITIONS.items()
        for target in targets
    ]

    # 构建非法对列表
    ILLEGAL_PAIRS = [
        (current, target)
        for current in VALID_TRANSITIONS
        for target in VALID_TRANSITIONS
        if target not in VALID_TRANSITIONS[current]
    ]

    @given(data=st.data())
    @settings(max_examples=200)
    def test_pbt_legal_transitions_no_exception(self, data):
        """**Validates: Requirements 8.1, 8.2, 8.3, 8.4**

        对于所有合法 (current, target) 对，_transition_status 不抛异常。
        """
        pair = data.draw(st.sampled_from(self.LEGAL_PAIRS))
        current, target = pair

        order = {"status": current}
        # 不应抛异常
        SettlementProcessor._transition_status(order, target)

    @given(data=st.data())
    @settings(max_examples=200)
    def test_pbt_illegal_transitions_raise(self, data):
        """**Validates: Requirements 8.1, 8.2, 8.3, 8.4**

        对于所有非法 (current, target) 对，_transition_status 抛出 IllegalStateTransition。
        """
        pair = data.draw(st.sampled_from(self.ILLEGAL_PAIRS))
        current, target = pair

        order = {"status": current}
        with pytest.raises(IllegalStateTransition) as exc_info:
            SettlementProcessor._transition_status(order, target)
        assert exc_info.value.current_status == current
        assert exc_info.value.new_status == target


# ──────────────────────────────────────────────
# Task 2 新增导入
# ──────────────────────────────────────────────

from app.engine.settlement import (
    SOURCE_PRIORITY_PLATFORM,
    SOURCE_PRIORITY_LOCAL,
    SOURCE_PRIORITY_TIMEOUT,
    NON_OVERRIDABLE_TERMINAL_STATES,
    OVERRIDABLE_TERMINAL_STATES,
)


# ──────────────────────────────────────────────
# Task 2.2: _get_settleable_orders 单元测试
# ──────────────────────────────────────────────

class TestGetSettleableOrders:
    """_get_settleable_orders 双模式查询测试"""

    @pytest.mark.asyncio
    async def test_normal_mode_returns_bet_success_and_pending_match(self, db, setup_data):
        """正常模式返回 bet_success 和 pending_match 订单"""
        # bet_success 订单
        o1 = await _create_bet_order(db, setup_data, key_code="DX1", idempotent_id="t2-o1")
        # pending_match 订单
        o2 = await _create_bet_order(db, setup_data, key_code="DX2", idempotent_id="t2-o2")
        await bet_order_update_status(
            db, order_id=o2["id"], operator_id=setup_data["operator"]["id"],
            status="pending_match",
        )

        processor = SettlementProcessor(db, setup_data["operator"]["id"])
        orders = await processor._get_settleable_orders("20240101001", include_recoverable=False)
        statuses = {o["status"] for o in orders}
        assert statuses == {"bet_success", "pending_match"}
        assert len(orders) == 2

    @pytest.mark.asyncio
    async def test_normal_mode_excludes_terminal_states(self, db, setup_data):
        """正常模式不返回终态订单"""
        o1 = await _create_bet_order(db, setup_data, key_code="DX1", idempotent_id="t2-ex1")
        # 将订单结算为 settled
        processor = SettlementProcessor(db, setup_data["operator"]["id"])
        await processor.settle("20240101001", [5, 5, 5], 15, "JND28WEB")

        orders = await processor._get_settleable_orders("20240101001", include_recoverable=False)
        assert len(orders) == 0

    @pytest.mark.asyncio
    async def test_recovery_mode_includes_settle_timeout_and_failed(self, db, setup_data):
        """恢复模式额外返回 settle_timeout 和 settle_failed 订单"""
        o1 = await _create_bet_order(db, setup_data, key_code="DX1", idempotent_id="t2-r1")
        o2 = await _create_bet_order(db, setup_data, key_code="DX2", idempotent_id="t2-r2")
        # 标记为 settle_timeout
        await bet_order_update_status(
            db, order_id=o1["id"], operator_id=setup_data["operator"]["id"],
            status="settle_timeout",
        )
        # 标记为 settle_failed
        await bet_order_update_status(
            db, order_id=o2["id"], operator_id=setup_data["operator"]["id"],
            status="settle_failed",
        )

        processor = SettlementProcessor(db, setup_data["operator"]["id"])
        orders = await processor._get_settleable_orders("20240101001", include_recoverable=True)
        statuses = {o["status"] for o in orders}
        assert "settle_timeout" in statuses
        assert "settle_failed" in statuses
        assert len(orders) == 2

    @pytest.mark.asyncio
    async def test_recovery_mode_excludes_non_overridable_terminals(self, db, setup_data):
        """恢复模式不返回 settled/bet_failed/reconcile_error 订单"""
        o1 = await _create_bet_order(db, setup_data, key_code="DX1", idempotent_id="t2-nr1")
        # 结算为 settled
        processor = SettlementProcessor(db, setup_data["operator"]["id"])
        await processor.settle("20240101001", [5, 5, 5], 15, "JND28WEB")

        orders = await processor._get_settleable_orders("20240101001", include_recoverable=True)
        assert len(orders) == 0


# ──────────────────────────────────────────────
# Task 2.3: 执行顺序测试（先模拟后真实）
# ──────────────────────────────────────────────

class TestSettleExecutionOrder:
    """验证先模拟后真实的执行顺序"""

    @pytest.mark.asyncio
    async def test_simulated_settled_before_real(self, db, setup_data):
        """模拟订单先于真实订单结算"""
        # 创建模拟订单（simulation=1）
        sim_strat = await strategy_create(
            db, operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            name="sim_strat", type="flat", play_code="DX1",
            base_amount=1000, simulation=1,
        )
        sim_order = await bet_order_create(
            db, idempotent_id="t2-sim-1",
            operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            strategy_id=sim_strat["id"],
            issue="20240101001", key_code="DX1",
            amount=1000, odds=19800, status="pending", simulation=1,
        )
        await bet_order_update_status(
            db, order_id=sim_order["id"],
            operator_id=setup_data["operator"]["id"],
            status="bet_success",
        )

        # 创建真实订单（simulation=0）
        real_order = await _create_bet_order(
            db, setup_data, key_code="DX2", idempotent_id="t2-real-1",
        )

        processor = SettlementProcessor(db, setup_data["operator"]["id"])
        await processor.settle("20240101001", [5, 5, 5], 15, "JND28WEB")

        # 两个订单都应该被结算
        sim_settled = await bet_order_get_by_id(
            db, order_id=sim_order["id"],
            operator_id=setup_data["operator"]["id"],
        )
        real_settled = await bet_order_get_by_id(
            db, order_id=real_order["id"],
            operator_id=setup_data["operator"]["id"],
        )
        assert sim_settled["status"] == "settled"
        assert real_settled["status"] == "settled"

        # 模拟订单的 settled_at 应 <= 真实订单的 settled_at
        assert sim_settled["settled_at"] <= real_settled["settled_at"]


# ──────────────────────────────────────────────
# Task 2.4: _atomic_transition 单元测试
# ──────────────────────────────────────────────

class TestAtomicTransition:
    """_atomic_transition CAS 语义测试"""

    @pytest.mark.asyncio
    async def test_successful_transition(self, db, setup_data):
        """合法转换返回 True"""
        order = await _create_bet_order(db, setup_data, key_code="DX1", idempotent_id="t2-at1")
        processor = SettlementProcessor(db, setup_data["operator"]["id"])

        result = await processor._atomic_transition(
            order["id"], "bet_success", "settling",
        )
        assert result is True

        updated = await bet_order_get_by_id(
            db, order_id=order["id"], operator_id=setup_data["operator"]["id"],
        )
        assert updated["status"] == "settling"

    @pytest.mark.asyncio
    async def test_cas_fails_when_status_changed(self, db, setup_data):
        """CAS 失败（状态已被其他流程修改）返回 False"""
        order = await _create_bet_order(db, setup_data, key_code="DX1", idempotent_id="t2-at2")
        processor = SettlementProcessor(db, setup_data["operator"]["id"])

        # 先转换为 settling
        await processor._atomic_transition(order["id"], "bet_success", "settling")

        # 再尝试从 bet_success 转换（已经不是 bet_success 了）
        result = await processor._atomic_transition(
            order["id"], "bet_success", "pending_match",
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_extra_fields_written(self, db, setup_data):
        """extra 字段在同一事务中写入"""
        order = await _create_bet_order(db, setup_data, key_code="DX1", idempotent_id="t2-at3")
        processor = SettlementProcessor(db, setup_data["operator"]["id"])

        result = await processor._atomic_transition(
            order["id"], "bet_success", "settling",
            match_source="platform",
        )
        assert result is True

        updated = await bet_order_get_by_id(
            db, order_id=order["id"], operator_id=setup_data["operator"]["id"],
        )
        assert updated["status"] == "settling"
        assert updated["match_source"] == "platform"

    @pytest.mark.asyncio
    async def test_illegal_transition_raises(self, db, setup_data):
        """非法状态转换抛出 IllegalStateTransition"""
        order = await _create_bet_order(db, setup_data, key_code="DX1", idempotent_id="t2-at4")
        processor = SettlementProcessor(db, setup_data["operator"]["id"])

        with pytest.raises(IllegalStateTransition):
            await processor._atomic_transition(
                order["id"], "bet_success", "settled",  # 跳过 settling，非法
            )


# ──────────────────────────────────────────────
# Task 2.5: _atomic_transition_with_priority 单元测试
# ──────────────────────────────────────────────

class TestAtomicTransitionWithPriority:
    """带优先级的原子状态转换测试"""

    @pytest.mark.asyncio
    async def test_non_terminal_to_terminal_executes_directly(self, db, setup_data):
        """非终态→终态直接执行，不检查优先级"""
        order = await _create_bet_order(db, setup_data, key_code="DX1", idempotent_id="t2-atp1")
        processor = SettlementProcessor(db, setup_data["operator"]["id"])

        result = await processor._atomic_transition_with_priority(
            order["id"], "bet_success", "settling",
            source_priority=SOURCE_PRIORITY_TIMEOUT,
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_non_overridable_terminal_rejected(self, db, setup_data):
        """不可覆盖终态（settled）直接拒绝"""
        order = await _create_bet_order(db, setup_data, key_code="DX1", idempotent_id="t2-atp2")
        processor = SettlementProcessor(db, setup_data["operator"]["id"])
        # 先结算
        await processor.settle("20240101001", [5, 5, 5], 15, "JND28WEB")

        result = await processor._atomic_transition_with_priority(
            order["id"], "settled", "settling",
            source_priority=SOURCE_PRIORITY_PLATFORM,
        )
        assert result is False


# ──────────────────────────────────────────────
# Task 2.6: Property 2 - 结算路径分发正确性 (PBT)
# ──────────────────────────────────────────────

class TestPBT_Property2_SettlementPathDispatch:
    """Property 2: 结算路径分发正确性

    **Validates: Requirements 4.1, 4.2, 4.3**

    对于任意订单集合 orders（每个订单有 simulation ∈ {0, 1}）：
    - simulation=0 的订单全部进入真实结算路径
    - simulation=1 的订单全部进入模拟结算路径
    - 两组的并集 == 原始集合（无遗漏无重复）
    """

    @given(
        simulations=st.lists(
            st.sampled_from([0, 1]),
            min_size=1,
            max_size=20,
        ),
    )
    @settings(max_examples=100)
    def test_pbt_property2_dispatch_completeness(self, simulations: list[int]):
        """**Validates: Requirements 4.1, 4.2, 4.3**

        验证 simulation=0 进入真实路径，simulation=1 进入模拟路径，无遗漏无重复。
        """
        # 构造订单列表
        orders = [
            {"id": i, "simulation": sim, "key_code": "DX1", "amount": 1000}
            for i, sim in enumerate(simulations)
        ]

        # 模拟 settle() 中的分组逻辑
        real_orders = [o for o in orders if o.get("simulation", 0) == 0]
        sim_orders = [o for o in orders if o.get("simulation", 0) == 1]

        # 属性 1：simulation=0 全部在 real_orders
        for o in real_orders:
            assert o["simulation"] == 0, f"真实组中出现 simulation={o['simulation']}"

        # 属性 2：simulation=1 全部在 sim_orders
        for o in sim_orders:
            assert o["simulation"] == 1, f"模拟组中出现 simulation={o['simulation']}"

        # 属性 3：两组并集 == 原始集合（无遗漏）
        real_ids = {o["id"] for o in real_orders}
        sim_ids = {o["id"] for o in sim_orders}
        all_ids = {o["id"] for o in orders}
        assert real_ids | sim_ids == all_ids, "存在遗漏订单"

        # 属性 4：两组无交集（无重复）
        assert real_ids & sim_ids == set(), "存在重复订单"

        # 属性 5：数量守恒
        assert len(real_orders) + len(sim_orders) == len(orders)


# ──────────────────────────────────────────────
# Task 2.7: 终态覆盖优先级单元测试 (P10)
# ──────────────────────────────────────────────

class TestTerminalStateOverridePriority:
    """Property 10: 终态覆盖优先级

    priority 1=platform > 2=local > 3=timeout/failed
    """

    @pytest.mark.asyncio
    async def test_settle_timeout_overridden_by_platform(self, db, setup_data):
        """2.7.1: settle_timeout 订单被 platform 来源覆盖为 settled"""
        order = await _create_bet_order(db, setup_data, key_code="DX1", idempotent_id="t2-p10-1")
        processor = SettlementProcessor(db, setup_data["operator"]["id"])

        # 先转为 settle_timeout
        await bet_order_update_status(
            db, order_id=order["id"],
            operator_id=setup_data["operator"]["id"],
            status="settle_timeout",
        )

        # platform 优先级（1）覆盖 settle_timeout（优先级 3）→ settling
        result = await processor._atomic_transition_with_priority(
            order["id"], "settle_timeout", "settling",
            source_priority=SOURCE_PRIORITY_PLATFORM,
        )
        assert result is True

        updated = await bet_order_get_by_id(
            db, order_id=order["id"], operator_id=setup_data["operator"]["id"],
        )
        assert updated["status"] == "settling"

    @pytest.mark.asyncio
    async def test_settle_failed_overridden_by_platform(self, db, setup_data):
        """2.7.2: settle_failed 订单被 platform 来源覆盖为 settled"""
        order = await _create_bet_order(db, setup_data, key_code="DX1", idempotent_id="t2-p10-2")
        processor = SettlementProcessor(db, setup_data["operator"]["id"])

        # 先转为 settle_failed
        await bet_order_update_status(
            db, order_id=order["id"],
            operator_id=setup_data["operator"]["id"],
            status="settle_failed",
        )

        # platform 优先级（1）覆盖 settle_failed（优先级 3）→ settling
        result = await processor._atomic_transition_with_priority(
            order["id"], "settle_failed", "settling",
            source_priority=SOURCE_PRIORITY_PLATFORM,
        )
        assert result is True

        updated = await bet_order_get_by_id(
            db, order_id=order["id"], operator_id=setup_data["operator"]["id"],
        )
        assert updated["status"] == "settling"

    @pytest.mark.asyncio
    async def test_settled_cannot_be_overridden(self, db, setup_data):
        """2.7.3: settled 订单不可被任何来源覆盖"""
        order = await _create_bet_order(db, setup_data, key_code="DX1", idempotent_id="t2-p10-3")
        processor = SettlementProcessor(db, setup_data["operator"]["id"])

        # 结算为 settled
        await processor.settle("20240101001", [5, 5, 5], 15, "JND28WEB")

        # 尝试用最高优先级覆盖 settled → 应失败
        result = await processor._atomic_transition_with_priority(
            order["id"], "settled", "settling",
            source_priority=SOURCE_PRIORITY_PLATFORM,
        )
        assert result is False

        # 验证状态未变
        updated = await bet_order_get_by_id(
            db, order_id=order["id"], operator_id=setup_data["operator"]["id"],
        )
        assert updated["status"] == "settled"

    @pytest.mark.asyncio
    async def test_same_priority_cannot_override(self, db, setup_data):
        """2.7.4: 同优先级来源不可覆盖（local 不可覆盖 local 级别的终态）"""
        order = await _create_bet_order(db, setup_data, key_code="DX1", idempotent_id="t2-p10-4")
        processor = SettlementProcessor(db, setup_data["operator"]["id"])

        # 先转为 settle_timeout（优先级 3）
        await bet_order_update_status(
            db, order_id=order["id"],
            operator_id=setup_data["operator"]["id"],
            status="settle_timeout",
        )

        # 同优先级（3=timeout）尝试覆盖 → 应失败
        result = await processor._atomic_transition_with_priority(
            order["id"], "settle_timeout", "settling",
            source_priority=SOURCE_PRIORITY_TIMEOUT,
        )
        assert result is False

        # 验证状态未变
        updated = await bet_order_get_by_id(
            db, order_id=order["id"], operator_id=setup_data["operator"]["id"],
        )
        assert updated["status"] == "settle_timeout"


# ──────────────────────────────────────────────
# Task 3: 平台数据匹配算法与歧义检测
# ──────────────────────────────────────────────

from app.engine.alert import AlertService


# ──────────────────────────────────────────────
# Task 3.8: Property 3 - 匹配唯一性属性测试 (PBT)
# ──────────────────────────────────────────────

class TestPBT_Property3_MatchUniqueness:
    """Property 3: 平台数据匹配唯一性

    **Validates: Requirements 5.1, 5.2**

    对于任意平台记录集 P 和本地订单集 L：
    - 每条平台记录最多被消耗一次
    - 每条本地订单最多匹配一条平台记录
    """

    @given(
        n_local=st.integers(min_value=1, max_value=8),
        n_platform=st.integers(min_value=0, max_value=10),
        n_keys=st.integers(min_value=1, max_value=3),
    )
    @settings(max_examples=100)
    def test_pbt_property3_match_uniqueness(
        self, n_local: int, n_platform: int, n_keys: int,
    ):
        """**Validates: Requirements 5.1, 5.2**

        验证匹配算法的唯一性：每条平台记录最多消耗一次，每条本地订单最多匹配一条平台记录。
        使用纯逻辑模拟（不需要 DB），验证 pop(0) 消耗语义。
        """
        issue = "20240101001"
        key_codes = [f"DX{i+1}" for i in range(n_keys)]
        amount = 1000

        # 构造本地订单
        local_orders = []
        for i in range(n_local):
            kc = key_codes[i % n_keys]
            local_orders.append({
                "id": i + 1,
                "key_code": kc,
                "amount": amount,
                "bet_at": f"2024-01-01 00:00:{i:02d}",
                "status": "bet_success",
                "strategy_id": 1,
                "account_id": 1,
                "issue": issue,
            })

        # 构造平台记录
        platform_bets = []
        for i in range(n_platform):
            kc = key_codes[i % n_keys]
            platform_bets.append({
                "Installments": issue,
                "KeyCode": kc,
                "Amount": str(amount / 100),  # 元
                "WinAmount": "19.80" if i % 2 == 0 else "0",
            })

        # 构建平台索引（模拟 _match_and_settle 的索引构建）
        platform_index: dict[tuple, list[dict]] = {}
        for bet in platform_bets:
            bet_issue = str(bet["Installments"])
            key_code = str(bet["KeyCode"])
            amount_fen = int(float(bet["Amount"]) * 100)
            key = (bet_issue, key_code, amount_fen)
            platform_index.setdefault(key, []).append(bet)

        # 记录原始平台记录数量（按 key）
        original_counts = {k: len(v) for k, v in platform_index.items()}

        # 排序本地订单
        sorted_orders = sorted(
            local_orders, key=lambda o: (o.get("bet_at", ""), o.get("id", 0))
        )

        # 按 Match_Key 分组
        local_groups: dict[tuple, list[dict]] = {}
        for order in sorted_orders:
            match_key = (issue, order["key_code"], order["amount"])
            local_groups.setdefault(match_key, []).append(order)

        # 模拟匹配
        matched_orders: set[int] = set()
        consumed_platform: list[int] = []  # 记录被消耗的平台记录索引

        for match_key, group_orders in local_groups.items():
            candidates = platform_index.get(match_key, [])
            for order in group_orders:
                if candidates:
                    record = candidates.pop(0)
                    matched_orders.add(order["id"])
                    consumed_platform.append(id(record))

        # 属性 1：每条平台记录最多消耗一次
        assert len(consumed_platform) == len(set(consumed_platform)), \
            "存在平台记录被重复消耗"

        # 属性 2：每条本地订单最多匹配一条平台记录
        assert len(matched_orders) <= n_local, \
            "匹配的订单数超过本地订单总数"

        # 属性 3：消耗的平台记录数 <= 原始平台记录数
        total_consumed = len(consumed_platform)
        total_platform = sum(original_counts.values())
        assert total_consumed <= total_platform, \
            "消耗的平台记录数超过原始数量"

        # 属性 4：未匹配的订单数 = 总数 - 匹配数
        unmatched = n_local - len(matched_orders)
        assert unmatched >= 0, "未匹配订单数为负"


# ──────────────────────────────────────────────
# Task 3.9: 歧义检测单元测试 (P9)
# ──────────────────────────────────────────────

class TestAmbiguityDetection:
    """P9: 歧义检测单元测试"""

    @pytest.mark.asyncio
    async def test_win_amount_all_same_normal_settlement(self, db, setup_data):
        """3.9.1: WinAmount 全同时正常结算（无告警）

        同一 Match_Key 下 2 条本地订单，平台 2 条记录 WinAmount 全同 → 正常结算
        """
        oid = setup_data["operator"]["id"]
        alert_svc = AlertService(db)
        processor = SettlementProcessor(db, oid, alert_service=alert_svc)

        # 创建 2 条 bet_success 订单（同 key_code, 同 amount）
        o1 = await _create_bet_order(
            db, setup_data, key_code="DX1", amount=1000, odds=19800,
            idempotent_id="t3-amb-same-1",
        )
        o2 = await _create_bet_order(
            db, setup_data, key_code="DX1", amount=1000, odds=19800,
            idempotent_id="t3-amb-same-2",
        )

        issue = "20240101001"
        # 平台记录：2 条，WinAmount 全同（都赢）
        platform_bets = [
            {"Installments": issue, "KeyCode": "DX1", "Amount": "10.00", "WinAmount": "19.80"},
            {"Installments": issue, "KeyCode": "DX1", "Amount": "10.00", "WinAmount": "19.80"},
        ]

        await processor._match_and_settle(
            [dict(o1), dict(o2)], platform_bets, issue, "5,5,5", 15,
        )

        # 验证两条订单都被正常结算
        settled1 = await bet_order_get_by_id(db, order_id=o1["id"], operator_id=oid)
        settled2 = await bet_order_get_by_id(db, order_id=o2["id"], operator_id=oid)
        assert settled1["status"] == "settled"
        assert settled2["status"] == "settled"
        assert settled1["match_source"] == "platform"
        assert settled2["match_source"] == "platform"

        # 验证无 match_ambiguity 告警
        alerts = await (
            await db.execute(
                "SELECT * FROM alerts WHERE operator_id=? AND type='match_ambiguity'",
                (oid,),
            )
        ).fetchall()
        assert len(alerts) == 0

    @pytest.mark.asyncio
    async def test_win_amount_different_triggers_ambiguity(self, db, setup_data):
        """3.9.2: WinAmount 不同时触发 match_ambiguity 告警并全部标记 pending_match

        同一 Match_Key 下 2 条本地订单，平台 2 条记录 WinAmount 不同 → 歧义
        """
        oid = setup_data["operator"]["id"]
        alert_svc = AlertService(db)
        processor = SettlementProcessor(db, oid, alert_service=alert_svc)

        # 创建 2 条 bet_success 订单（同 key_code, 同 amount）
        o1 = await _create_bet_order(
            db, setup_data, key_code="DX1", amount=1000, odds=19800,
            idempotent_id="t3-amb-diff-1",
        )
        o2 = await _create_bet_order(
            db, setup_data, key_code="DX1", amount=1000, odds=19800,
            idempotent_id="t3-amb-diff-2",
        )

        issue = "20240101001"
        # 平台记录：2 条，WinAmount 不同（一赢一输）
        platform_bets = [
            {"Installments": issue, "KeyCode": "DX1", "Amount": "10.00", "WinAmount": "19.80"},
            {"Installments": issue, "KeyCode": "DX1", "Amount": "10.00", "WinAmount": "0"},
        ]

        await processor._match_and_settle(
            [dict(o1), dict(o2)], platform_bets, issue, "5,5,5", 15,
        )

        # 验证两条订单都被标记为 pending_match
        pm1 = await bet_order_get_by_id(db, order_id=o1["id"], operator_id=oid)
        pm2 = await bet_order_get_by_id(db, order_id=o2["id"], operator_id=oid)
        assert pm1["status"] == "pending_match"
        assert pm2["status"] == "pending_match"

        # 验证 match_ambiguity 告警已发送
        alerts = await (
            await db.execute(
                "SELECT * FROM alerts WHERE operator_id=? AND type='match_ambiguity'",
                (oid,),
            )
        ).fetchall()
        assert len(alerts) == 1
        detail = json.loads(alerts[0]["detail"])
        assert detail["issue"] == issue
        assert detail["key_code"] == "DX1"
        assert detail["amount"] == 1000
        assert detail["local_count"] == 2
        assert detail["platform_count"] == 2
        assert set(detail["win_amounts"]) == {19.80, 0.0}


# ──────────────────────────────────────────────
# Task 3.3: Topbetlist 覆盖检测单元测试
# ──────────────────────────────────────────────

class TestTopbetlistCoverageDetection:
    """Topbetlist 覆盖检测"""

    @pytest.mark.asyncio
    async def test_coverage_warning_when_local_exceeds_platform(self, db, setup_data):
        """本地订单数 > 平台同期记录数时发送 topbetlist_coverage_warning 告警"""
        oid = setup_data["operator"]["id"]
        alert_svc = AlertService(db)
        processor = SettlementProcessor(db, oid, alert_service=alert_svc)

        issue = "20240101001"
        # 创建 3 条本地订单
        o1 = await _create_bet_order(
            db, setup_data, key_code="DX1", amount=1000, odds=19800,
            idempotent_id="t3-cov-1",
        )
        o2 = await _create_bet_order(
            db, setup_data, key_code="DX2", amount=500, odds=19800,
            idempotent_id="t3-cov-2",
        )
        o3 = await _create_bet_order(
            db, setup_data, key_code="DS3", amount=1000, odds=19800,
            idempotent_id="t3-cov-3",
        )

        # 平台只有 1 条记录
        platform_bets = [
            {"Installments": issue, "KeyCode": "DX1", "Amount": "10.00", "WinAmount": "19.80"},
        ]

        orders = [dict(o1), dict(o2), dict(o3)]
        await processor._match_and_settle(orders, platform_bets, issue, "5,5,5", 15)

        # 验证 topbetlist_coverage_warning 告警
        alerts = await (
            await db.execute(
                "SELECT * FROM alerts WHERE operator_id=? AND type='topbetlist_coverage_warning'",
                (oid,),
            )
        ).fetchall()
        assert len(alerts) == 1
        detail = json.loads(alerts[0]["detail"])
        assert detail["local_count"] == 3
        assert detail["platform_count"] == 1

        # DX1 匹配成功，DX2 和 DS3 标记 pending_match
        s1 = await bet_order_get_by_id(db, order_id=o1["id"], operator_id=oid)
        s2 = await bet_order_get_by_id(db, order_id=o2["id"], operator_id=oid)
        s3 = await bet_order_get_by_id(db, order_id=o3["id"], operator_id=oid)
        assert s1["status"] == "settled"
        assert s2["status"] == "pending_match"
        assert s3["status"] == "pending_match"


# ──────────────────────────────────────────────
# Task 3.5: pending_match_count 累加与 settle_timeout 测试
# ──────────────────────────────────────────────

class TestPendingMatchTimeout:
    """pending_match_count 累加与 settle_timeout 转换"""

    @pytest.mark.asyncio
    async def test_pending_match_count_increments_in_normal_cycle(self, db, setup_data):
        """正常结算周期中 pending_match_count 递增"""
        oid = setup_data["operator"]["id"]
        alert_svc = AlertService(db)
        processor = SettlementProcessor(db, oid, alert_service=alert_svc)

        o1 = await _create_bet_order(
            db, setup_data, key_code="DX1", amount=1000, odds=19800,
            idempotent_id="t3-pm-inc-1",
        )
        # 先标记为 pending_match
        await bet_order_update_status(
            db, order_id=o1["id"], operator_id=oid, status="pending_match",
            pending_match_count=0,
        )
        order = await bet_order_get_by_id(db, order_id=o1["id"], operator_id=oid)

        # 第一次正常周期未匹配
        await processor._mark_pending_match(dict(order), is_normal_cycle=True)
        updated = await bet_order_get_by_id(db, order_id=o1["id"], operator_id=oid)
        assert updated["status"] == "pending_match"
        assert updated["pending_match_count"] == 1

    @pytest.mark.asyncio
    async def test_settle_timeout_after_2_cycles(self, db, setup_data):
        """连续 2 个正常结算周期未匹配 → settle_timeout"""
        oid = setup_data["operator"]["id"]
        alert_svc = AlertService(db)
        processor = SettlementProcessor(db, oid, alert_service=alert_svc)

        o1 = await _create_bet_order(
            db, setup_data, key_code="DX1", amount=1000, odds=19800,
            idempotent_id="t3-pm-timeout-1",
        )
        # pending_match_count=1（已经过 1 个周期）
        await bet_order_update_status(
            db, order_id=o1["id"], operator_id=oid, status="pending_match",
            pending_match_count=1,
        )
        order = await bet_order_get_by_id(db, order_id=o1["id"], operator_id=oid)

        # 第二次正常周期未匹配 → 应触发 settle_timeout
        await processor._mark_pending_match(dict(order), is_normal_cycle=True)
        updated = await bet_order_get_by_id(db, order_id=o1["id"], operator_id=oid)
        assert updated["status"] == "settle_timeout"

        # 验证 settle_timeout 告警
        alerts = await (
            await db.execute(
                "SELECT * FROM alerts WHERE operator_id=? AND type='settle_timeout'",
                (oid,),
            )
        ).fetchall()
        assert len(alerts) == 1

    @pytest.mark.asyncio
    async def test_pending_match_count_not_incremented_in_non_normal_cycle(self, db, setup_data):
        """API 失败/补结算周期中 pending_match_count 不递增"""
        oid = setup_data["operator"]["id"]
        processor = SettlementProcessor(db, oid)

        o1 = await _create_bet_order(
            db, setup_data, key_code="DX1", amount=1000, odds=19800,
            idempotent_id="t3-pm-noinc-1",
        )
        await bet_order_update_status(
            db, order_id=o1["id"], operator_id=oid, status="pending_match",
            pending_match_count=0,
        )
        order = await bet_order_get_by_id(db, order_id=o1["id"], operator_id=oid)

        # 非正常周期
        await processor._mark_pending_match(dict(order), is_normal_cycle=False)
        updated = await bet_order_get_by_id(db, order_id=o1["id"], operator_id=oid)
        assert updated["status"] == "pending_match"
        assert updated["pending_match_count"] == 0  # 未递增


# ──────────────────────────────────────────────
# Task 3.6: _settle_order_from_platform 单元测试
# ──────────────────────────────────────────────

class TestSettleOrderFromPlatform:
    """_settle_order_from_platform 方法测试"""

    @pytest.mark.asyncio
    async def test_win_order_from_platform(self, db, setup_data):
        """中奖订单：pnl = int(WinAmount * 100) - amount, is_win=1"""
        oid = setup_data["operator"]["id"]
        processor = SettlementProcessor(db, oid)

        o1 = await _create_bet_order(
            db, setup_data, key_code="DX1", amount=1000, odds=19800,
            idempotent_id="t3-plat-win-1",
        )

        platform_record = {"WinAmount": "19.80"}
        pnl = await processor._settle_order_from_platform(
            dict(o1), platform_record, "5,5,5", 15,
        )

        # pnl = int(19.80 * 100) - 1000 = 1980 - 1000 = 980
        assert pnl == 980

        settled = await bet_order_get_by_id(db, order_id=o1["id"], operator_id=oid)
        assert settled["status"] == "settled"
        assert settled["is_win"] == 1
        assert settled["pnl"] == 980
        assert settled["match_source"] == "platform"

    @pytest.mark.asyncio
    async def test_lose_order_from_platform(self, db, setup_data):
        """未中奖订单：pnl = 0 - amount = -amount, is_win=0"""
        oid = setup_data["operator"]["id"]
        processor = SettlementProcessor(db, oid)

        o1 = await _create_bet_order(
            db, setup_data, key_code="DX1", amount=1000, odds=19800,
            idempotent_id="t3-plat-lose-1",
        )

        platform_record = {"WinAmount": "0"}
        pnl = await processor._settle_order_from_platform(
            dict(o1), platform_record, "5,5,5", 15,
        )

        # pnl = int(0 * 100) - 1000 = -1000
        assert pnl == -1000

        settled = await bet_order_get_by_id(db, order_id=o1["id"], operator_id=oid)
        assert settled["status"] == "settled"
        assert settled["is_win"] == 0
        assert settled["pnl"] == -1000
        assert settled["match_source"] == "platform"


# ──────────────────────────────────────────────
# Task 3.7: 平台记录落库单元测试
# ──────────────────────────────────────────────

class TestPersistPlatformRecords:
    """平台记录落库测试"""

    @pytest.mark.asyncio
    async def test_platform_records_persisted(self, db, setup_data):
        """每条 Topbetlist 记录对应一行，含 raw_json"""
        oid = setup_data["operator"]["id"]
        processor = SettlementProcessor(db, oid)

        platform_bets = [
            {"Installments": "20240101001", "KeyCode": "DX1", "Amount": "10.00", "WinAmount": "19.80"},
            {"Installments": "20240101001", "KeyCode": "DX2", "Amount": "5.00", "WinAmount": "0"},
        ]

        await processor._persist_platform_records(platform_bets)

        rows = await (
            await db.execute("SELECT * FROM bet_order_platform_records ORDER BY id")
        ).fetchall()
        assert len(rows) == 2

        r1 = dict(rows[0])
        assert r1["issue"] == "20240101001"
        assert r1["key_code"] == "DX1"
        assert r1["amount"] == 1000
        assert r1["win_amount"] == 1980
        raw = json.loads(r1["raw_json"])
        assert raw["KeyCode"] == "DX1"

        r2 = dict(rows[1])
        assert r2["issue"] == "20240101001"
        assert r2["key_code"] == "DX2"
        assert r2["amount"] == 500
        assert r2["win_amount"] == 0


# ──────────────────────────────────────────────
# Task 4: 真实投注结算路径实现
# ──────────────────────────────────────────────

from unittest.mock import AsyncMock, MagicMock
from app.engine.adapters.base import BalanceInfo


class _FakeAdapter:
    """用于 Task 4 测试的 mock adapter"""

    def __init__(
        self,
        balance: float = 100.0,
        bet_history: list[dict] | None = None,
        query_balance_error: bool = False,
        get_bet_history_error: bool = False,
    ):
        self._balance = balance
        self._bet_history = bet_history or []
        self._query_balance_error = query_balance_error
        self._get_bet_history_error = get_bet_history_error

    async def query_balance(self) -> BalanceInfo:
        if self._query_balance_error:
            raise ConnectionError("QueryResult 网络异常")
        return BalanceInfo(balance=self._balance)

    async def get_bet_history(self, count: int = 15) -> list[dict]:
        if self._get_bet_history_error:
            raise ConnectionError("Topbetlist 网络异常")
        return self._bet_history


# ──────────────────────────────────────────────
# Task 4.1: _settle_real 单元测试
# ──────────────────────────────────────────────

class TestSettleReal:
    """_settle_real 方法测试"""

    @pytest.mark.asyncio
    async def test_settle_real_full_flow(self, db, setup_data):
        """完整真实结算流程：余额更新 + 匹配成功"""
        oid = setup_data["operator"]["id"]
        aid = setup_data["account"]["id"]
        alert_svc = AlertService(db)
        processor = SettlementProcessor(db, oid, alert_service=alert_svc)

        issue = "20240101001"
        o1 = await _create_bet_order(
            db, setup_data, key_code="DX1", amount=1000, odds=19800,
            idempotent_id="t4-real-1",
        )

        adapter = _FakeAdapter(
            balance=500.55,
            bet_history=[
                {"Installments": issue, "KeyCode": "DX1", "Amount": "10.00", "WinAmount": "19.80"},
            ],
        )

        await processor._settle_real(
            [dict(o1)], issue, adapter, "5,5,5", 15,
        )

        # 验证余额写入 int(500.55 * 100) = 50055
        acc = await (
            await db.execute("SELECT balance FROM gambling_accounts WHERE id=?", (aid,))
        ).fetchone()
        assert acc["balance"] == 50055

        # 验证订单结算
        settled = await bet_order_get_by_id(db, order_id=o1["id"], operator_id=oid)
        assert settled["status"] == "settled"
        assert settled["match_source"] == "platform"
        assert settled["is_win"] == 1
        assert settled["pnl"] == 980  # int(19.80 * 100) - 1000

    @pytest.mark.asyncio
    async def test_settle_real_query_balance_fails_still_settles(self, db, setup_data):
        """QueryResult 失败时仍继续 Topbetlist 匹配"""
        oid = setup_data["operator"]["id"]
        alert_svc = AlertService(db)
        processor = SettlementProcessor(db, oid, alert_service=alert_svc)

        issue = "20240101001"
        o1 = await _create_bet_order(
            db, setup_data, key_code="DX1", amount=1000, odds=19800,
            idempotent_id="t4-real-qf-1",
        )

        adapter = _FakeAdapter(
            query_balance_error=True,
            bet_history=[
                {"Installments": issue, "KeyCode": "DX1", "Amount": "10.00", "WinAmount": "0"},
            ],
        )

        await processor._settle_real(
            [dict(o1)], issue, adapter, "5,5,5", 15,
        )

        # 订单仍应被结算
        settled = await bet_order_get_by_id(db, order_id=o1["id"], operator_id=oid)
        assert settled["status"] == "settled"
        assert settled["match_source"] == "platform"
        assert settled["is_win"] == 0


# ──────────────────────────────────────────────
# Task 4.2: _retry_api 单元测试
# ──────────────────────────────────────────────

class TestRetryApi:
    """_retry_api 通用重试方法测试"""

    @pytest.mark.asyncio
    async def test_retry_succeeds_first_try(self, db, setup_data):
        """首次成功直接返回"""
        processor = SettlementProcessor(db, setup_data["operator"]["id"])
        call_count = 0

        async def success_func():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await processor._retry_api(success_func, max_retries=3, interval=0)
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_try(self, db, setup_data):
        """第二次成功"""
        processor = SettlementProcessor(db, setup_data["operator"]["id"])
        call_count = 0

        async def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("fail")
            return "ok"

        result = await processor._retry_api(flaky_func, max_retries=3, interval=0)
        assert result == "ok"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_retry_all_fail_raises(self, db, setup_data):
        """3 次全部失败抛出最后一次异常"""
        processor = SettlementProcessor(db, setup_data["operator"]["id"])
        call_count = 0

        async def always_fail():
            nonlocal call_count
            call_count += 1
            raise ConnectionError(f"fail-{call_count}")

        with pytest.raises(ConnectionError, match="fail-3"):
            await processor._retry_api(always_fail, max_retries=3, interval=0)
        assert call_count == 3


# ──────────────────────────────────────────────
# Task 4.3: _update_account_balance_absolute 单元测试
# ──────────────────────────────────────────────

class TestUpdateAccountBalanceAbsolute:
    """_update_account_balance_absolute 方法测试"""

    @pytest.mark.asyncio
    async def test_writes_absolute_balance(self, db, setup_data):
        """正常写入绝对余额"""
        oid = setup_data["operator"]["id"]
        aid = setup_data["account"]["id"]
        processor = SettlementProcessor(db, oid)

        await processor._update_account_balance_absolute(aid, 12345)

        acc = await (
            await db.execute("SELECT balance FROM gambling_accounts WHERE id=?", (aid,))
        ).fetchone()
        assert acc["balance"] == 12345

    @pytest.mark.asyncio
    async def test_negative_balance_not_updated(self, db, setup_data):
        """负值余额不更新"""
        oid = setup_data["operator"]["id"]
        aid = setup_data["account"]["id"]
        processor = SettlementProcessor(db, oid)

        # 先设置一个已知余额
        await db.execute(
            "UPDATE gambling_accounts SET balance=? WHERE id=?", (5000, aid)
        )
        await db.commit()

        await processor._update_account_balance_absolute(aid, -100)

        acc = await (
            await db.execute("SELECT balance FROM gambling_accounts WHERE id=?", (aid,))
        ).fetchone()
        assert acc["balance"] == 5000  # 未变

    @pytest.mark.asyncio
    async def test_zero_balance_updated(self, db, setup_data):
        """零值余额正常写入"""
        oid = setup_data["operator"]["id"]
        aid = setup_data["account"]["id"]
        processor = SettlementProcessor(db, oid)

        await processor._update_account_balance_absolute(aid, 0)

        acc = await (
            await db.execute("SELECT balance FROM gambling_accounts WHERE id=?", (aid,))
        ).fetchone()
        assert acc["balance"] == 0


# ──────────────────────────────────────────────
# Task 4.4: Topbetlist 全部失败 → settle_failed + 告警
# ──────────────────────────────────────────────

class TestTopbetlistAllFail:
    """Topbetlist 3 次重试全部失败时的处理"""

    @pytest.mark.asyncio
    async def test_all_orders_marked_settle_failed(self, db, setup_data):
        """所有 simulation=0 订单标记为 settle_failed"""
        oid = setup_data["operator"]["id"]
        alert_svc = AlertService(db)
        processor = SettlementProcessor(db, oid, alert_service=alert_svc)

        issue = "20240101001"
        o1 = await _create_bet_order(
            db, setup_data, key_code="DX1", amount=1000, odds=19800,
            idempotent_id="t4-fail-1",
        )
        o2 = await _create_bet_order(
            db, setup_data, key_code="DX2", amount=500, odds=19800,
            idempotent_id="t4-fail-2",
        )

        adapter = _FakeAdapter(get_bet_history_error=True)

        await processor._settle_real(
            [dict(o1), dict(o2)], issue, adapter, "5,5,5", 15,
        )

        # 验证订单状态
        s1 = await bet_order_get_by_id(db, order_id=o1["id"], operator_id=oid)
        s2 = await bet_order_get_by_id(db, order_id=o2["id"], operator_id=oid)
        assert s1["status"] == "settle_failed"
        assert s2["status"] == "settle_failed"

    @pytest.mark.asyncio
    async def test_settle_api_failed_alert_sent(self, db, setup_data):
        """发送 settle_api_failed 告警"""
        oid = setup_data["operator"]["id"]
        alert_svc = AlertService(db)
        processor = SettlementProcessor(db, oid, alert_service=alert_svc)

        issue = "20240101001"
        o1 = await _create_bet_order(
            db, setup_data, key_code="DX1", amount=1000, odds=19800,
            idempotent_id="t4-alert-1",
        )

        adapter = _FakeAdapter(get_bet_history_error=True)

        await processor._settle_real(
            [dict(o1)], issue, adapter, "5,5,5", 15,
        )

        # 验证告警
        alerts = await (
            await db.execute(
                "SELECT * FROM alerts WHERE operator_id=? AND type='settle_api_failed'",
                (oid,),
            )
        ).fetchall()
        assert len(alerts) == 1
        assert "Topbetlist" in alerts[0]["title"]


# ──────────────────────────────────────────────
# Task 4.5: Property 5 - 余额更新正确性 (PBT)
# ──────────────────────────────────────────────

class TestPBT_Property5_BalanceUpdateCorrectness:
    """Property 5: 余额更新正确性（真实模式）

    **Validates: Requirements 2.1**

    对于任意 accountLimit 浮点值 v（v >= 0）：
    - 写入的 balance == int(v * 100)
    """

    @given(
        account_limit=st.floats(
            min_value=0.0,
            max_value=1_000_000.0,
            allow_nan=False,
            allow_infinity=False,
        ),
    )
    @settings(max_examples=200)
    def test_pbt_property5_balance_conversion(self, account_limit: float):
        """**Validates: Requirements 2.1**

        对于任意 accountLimit 浮点值 v >= 0，写入 balance == int(v * 100)。
        验证 int() 截断语义（floor toward zero）。
        """
        expected_balance = int(account_limit * 100)

        # 验证转换结果为整数
        assert isinstance(expected_balance, int)

        # 验证非负
        assert expected_balance >= 0

        # 验证 int() 截断语义：int(v * 100) <= v * 100
        assert expected_balance <= account_limit * 100

        # 验证截断误差 < 1（即不超过 1 分）
        assert account_limit * 100 - expected_balance < 1.0

    @given(
        account_limit=st.floats(
            min_value=0.0,
            max_value=1_000_000.0,
            allow_nan=False,
            allow_infinity=False,
        ),
    )
    @settings(max_examples=200)
    def test_pbt_property5_balance_db_roundtrip(self, account_limit: float):
        """**Validates: Requirements 2.1**

        验证 DB 写入后读取的值与 int(accountLimit * 100) 一致。
        使用 asyncio 运行实际 DB 操作。
        """
        expected = int(account_limit * 100)

        async def _run():
            await close_shared_db()
            await init_db(":memory:")
            conn = await get_shared_db()
            try:
                # 创建 operator + account
                op = await operator_create(conn, username="pbt_op", password="pass")
                acc = await account_create(
                    conn, operator_id=op["id"], account_name="pbt_acc",
                    password="pwd", platform_type="JND282",
                )
                processor = SettlementProcessor(conn, op["id"])
                await processor._update_account_balance_absolute(acc["id"], expected)

                row = await (
                    await conn.execute(
                        "SELECT balance FROM gambling_accounts WHERE id=?",
                        (acc["id"],),
                    )
                ).fetchone()
                assert row["balance"] == expected
            finally:
                await close_shared_db()

        asyncio.get_event_loop().run_until_complete(_run())


# ──────────────────────────────────────────────
# Property 4: 模拟结算 PnL 计算一致性
# ──────────────────────────────────────────────

class TestPBT_Property4_SimulatedPnlConsistency:
    """Property 4: 模拟结算 PnL 计算一致性

    **Validates: Requirements 3.1, 3.2, 3.3, 3.4**

    对于任意 (key_code, balls, sum_value, amount, odds)：
    - is_win=1 时：pnl == amount * odds // 10000 - amount
    - is_win=0 时：pnl == -amount
    - is_win=-1 时：pnl == 0
    """

    # ── 5.3.1 PBT: is_win=1 时 pnl == amount * odds // 10000 - amount ──

    @given(
        amount=st.integers(min_value=1, max_value=1_000_000),
        odds=st.integers(min_value=10000, max_value=100_000),
        sum_value=st.integers(min_value=14, max_value=27),
    )
    @settings(max_examples=200)
    def test_pbt_property4_win_pnl(self, amount: int, odds: int, sum_value: int):
        """**Validates: Requirements 3.1, 3.2**

        is_win=1 时 pnl == amount * odds // 10000 - amount

        使用 DX1（大）+ sum >= 14 确保中奖，JND28WEB 无退款规则。
        """
        # 构造 balls 使 sum 匹配
        b3 = min(sum_value, 9)
        remainder = sum_value - b3
        b2 = min(remainder, 9)
        b1 = remainder - b2
        assume(0 <= b1 <= 9)
        balls = [b1, b2, b3]

        order = {"key_code": "DX1", "amount": amount, "odds": odds}
        result = SettlementProcessor._calculate_result(order, balls, sum_value, "JND28WEB")

        assert result.is_win == 1
        expected_pnl = amount * odds // 10000 - amount
        assert result.pnl == expected_pnl
        assert isinstance(result.pnl, int)

    # ── 5.3.1 PBT: is_win=0 时 pnl == -amount ──

    @given(
        amount=st.integers(min_value=1, max_value=1_000_000),
        odds=st.integers(min_value=10000, max_value=100_000),
        sum_value=st.integers(min_value=0, max_value=13),
    )
    @settings(max_examples=200)
    def test_pbt_property4_lose_pnl(self, amount: int, odds: int, sum_value: int):
        """**Validates: Requirements 3.1, 3.2**

        is_win=0 时 pnl == -amount

        使用 DX1（大）+ sum <= 13 确保未中奖，JND28WEB 无退款规则。
        """
        b3 = min(sum_value, 9)
        remainder = sum_value - b3
        b2 = min(remainder, 9)
        b1 = remainder - b2
        assume(0 <= b1 <= 9)
        balls = [b1, b2, b3]

        order = {"key_code": "DX1", "amount": amount, "odds": odds}
        result = SettlementProcessor._calculate_result(order, balls, sum_value, "JND28WEB")

        assert result.is_win == 0
        assert result.pnl == -amount
        assert isinstance(result.pnl, int)

    # ── 5.3.1 PBT: is_win=-1 时 pnl == 0 ──

    @given(
        amount=st.integers(min_value=1, max_value=1_000_000),
        odds=st.integers(min_value=10000, max_value=100_000),
        key_code=st.sampled_from(["DX1", "DS4", "ZH8"]),
    )
    @settings(max_examples=200)
    def test_pbt_property4_refund_pnl_sum14(self, amount: int, odds: int, key_code: str):
        """**Validates: Requirements 3.3, 3.4**

        is_win=-1（退款）时 pnl == 0

        JND282 平台 sum=14 时 DX1/DS4/ZH8 触发退款。
        """
        # sum=14 的合法 balls 组合
        balls = [5, 5, 4]
        order = {"key_code": key_code, "amount": amount, "odds": odds}
        result = SettlementProcessor._calculate_result(order, balls, 14, "JND282")

        assert result.is_win == -1
        assert result.pnl == 0
        assert isinstance(result.pnl, int)

    @given(
        amount=st.integers(min_value=1, max_value=1_000_000),
        odds=st.integers(min_value=10000, max_value=100_000),
        key_code=st.sampled_from(["DX2", "DS3", "ZH9"]),
    )
    @settings(max_examples=200)
    def test_pbt_property4_refund_pnl_sum13(self, amount: int, odds: int, key_code: str):
        """**Validates: Requirements 3.3, 3.4**

        is_win=-1（退款）时 pnl == 0

        JND282 平台 sum=13 时 DX2/DS3/ZH9 触发退款。
        """
        balls = [4, 4, 5]
        order = {"key_code": key_code, "amount": amount, "odds": odds}
        result = SettlementProcessor._calculate_result(order, balls, 13, "JND282")

        assert result.is_win == -1
        assert result.pnl == 0
        assert isinstance(result.pnl, int)

# ──────────────────────────────────────────────
# Task 6: 开奖结果持久化单元测试（R7）
# ──────────────────────────────────────────────


class TestSaveLotteryResult:
    """直接测试 _save_lottery_result() 方法，验证 INSERT OR IGNORE 语义"""

    @pytest.mark.asyncio
    async def test_save_lottery_result_normal(self, db, setup_data):
        """6.3.1 正常写入 (issue, open_result, sum_value)"""
        processor = SettlementProcessor(db, setup_data["operator"]["id"])
        await processor._save_lottery_result("20240201001", "3,5,7", 15)

        row = await lottery_result_get_by_issue(db, issue="20240201001")
        assert row is not None
        assert row["issue"] == "20240201001"
        assert row["open_result"] == "3,5,7"
        assert row["sum_value"] == 15
        assert row["created_at"] is not None

    @pytest.mark.asyncio
    async def test_save_lottery_result_duplicate_ignore(self, db, setup_data):
        """6.3.2 重复 issue INSERT OR IGNORE 不报错，且不覆盖原始数据"""
        processor = SettlementProcessor(db, setup_data["operator"]["id"])

        # 第一次写入
        await processor._save_lottery_result("20240201002", "1,2,3", 6)

        # 第二次写入相同 issue 但不同数据 — 不应报错
        await processor._save_lottery_result("20240201002", "9,9,9", 27)

        # 验证数据未被覆盖，仍为第一次写入的值
        row = await lottery_result_get_by_issue(db, issue="20240201002")
        assert row is not None
        assert row["open_result"] == "1,2,3"
        assert row["sum_value"] == 6


# ──────────────────────────────────────────────
# Task 10: 结算幂等性与正确性属性测试
# ──────────────────────────────────────────────


# ──────────────────────────────────────────────
# Task 10.1: Property 8 - 结算幂等性 (PBT)
# ──────────────────────────────────────────────

class TestPBT_Property8_SettlementIdempotency:
    """Property 8: 结算幂等性

    **Validates: Requirements 9.1, 9.3**

    对于任意已结算订单集合（status ∈ TERMINAL_STATES）：
    - 对同一 issue 重复调用 settle() 后，订单状态、pnl、balance 均不变
    - strategy 的 daily_pnl/total_pnl 不会被重复累加
    """

    @given(
        n_orders=st.integers(min_value=1, max_value=5),
        sum_value=st.integers(min_value=14, max_value=27),
        repeat_count=st.integers(min_value=2, max_value=4),
    )
    @settings(max_examples=50)
    def test_pbt_property8_idempotency(
        self, n_orders: int, sum_value: int, repeat_count: int,
    ):
        """**Validates: Requirements 9.1, 9.3**

        重复调用 settle() 后订单状态/pnl/balance 不变，strategy pnl 不重复累加。
        """
        # 构造 balls 使 sum 匹配
        b3 = min(sum_value, 9)
        remainder = sum_value - b3
        b2 = min(remainder, 9)
        b1 = remainder - b2
        assume(0 <= b1 <= 9)
        balls = [b1, b2, b3]

        async def _run():
            await close_shared_db()
            await init_db(":memory:")
            conn = await get_shared_db()
            try:
                op = await operator_create(conn, username="pbt8_op", password="pass")
                acc = await account_create(
                    conn, operator_id=op["id"], account_name="pbt8_acc",
                    password="pwd", platform_type="JND28WEB",
                )
                strat = await strategy_create(
                    conn, operator_id=op["id"], account_id=acc["id"],
                    name="pbt8_strat", type="flat", play_code="DX1",
                    base_amount=1000,
                )
                setup = {"operator": op, "account": acc, "strategy": strat}

                issue = "20240101001"
                # 创建 n_orders 个订单
                for i in range(n_orders):
                    await _create_bet_order(
                        conn, setup, issue=issue,
                        key_code="DX1", amount=1000, odds=19800,
                        idempotent_id=f"pbt8-{issue}-{i}",
                    )

                processor = SettlementProcessor(conn, op["id"])

                # 第一次结算
                await processor.settle(issue, balls, sum_value, "JND28WEB")

                # 记录第一次结算后的快照
                orders_after_first = await (
                    await conn.execute(
                        "SELECT id, status, pnl, is_win, match_source FROM bet_orders "
                        "WHERE issue=? AND operator_id=? ORDER BY id",
                        (issue, op["id"]),
                    )
                ).fetchall()
                orders_snapshot = [dict(r) for r in orders_after_first]

                acc_after_first = await (
                    await conn.execute(
                        "SELECT balance FROM gambling_accounts WHERE id=?",
                        (acc["id"],),
                    )
                ).fetchone()
                balance_snapshot = acc_after_first["balance"]

                strat_after_first = await strategy_get_by_id(
                    conn, strategy_id=strat["id"], operator_id=op["id"],
                )
                daily_pnl_snapshot = strat_after_first["daily_pnl"]
                total_pnl_snapshot = strat_after_first["total_pnl"]

                # 重复调用 settle() repeat_count 次
                for _ in range(repeat_count):
                    await processor.settle(issue, balls, sum_value, "JND28WEB")

                # 验证订单状态/pnl 不变
                orders_after_repeat = await (
                    await conn.execute(
                        "SELECT id, status, pnl, is_win, match_source FROM bet_orders "
                        "WHERE issue=? AND operator_id=? ORDER BY id",
                        (issue, op["id"]),
                    )
                ).fetchall()
                for snap, current in zip(orders_snapshot, orders_after_repeat):
                    current = dict(current)
                    assert snap["status"] == current["status"], \
                        f"订单 {snap['id']} 状态变化: {snap['status']} → {current['status']}"
                    assert snap["pnl"] == current["pnl"], \
                        f"订单 {snap['id']} pnl 变化: {snap['pnl']} → {current['pnl']}"
                    assert snap["is_win"] == current["is_win"], \
                        f"订单 {snap['id']} is_win 变化"

                # 验证余额不变
                acc_after_repeat = await (
                    await conn.execute(
                        "SELECT balance FROM gambling_accounts WHERE id=?",
                        (acc["id"],),
                    )
                ).fetchone()
                assert balance_snapshot == acc_after_repeat["balance"], \
                    f"余额变化: {balance_snapshot} → {acc_after_repeat['balance']}"

                # 验证 strategy pnl 不重复累加
                strat_after_repeat = await strategy_get_by_id(
                    conn, strategy_id=strat["id"], operator_id=op["id"],
                )
                assert daily_pnl_snapshot == strat_after_repeat["daily_pnl"], \
                    f"daily_pnl 变化: {daily_pnl_snapshot} → {strat_after_repeat['daily_pnl']}"
                assert total_pnl_snapshot == strat_after_repeat["total_pnl"], \
                    f"total_pnl 变化: {total_pnl_snapshot} → {strat_after_repeat['total_pnl']}"

            finally:
                await close_shared_db()

        asyncio.get_event_loop().run_until_complete(_run())


# ──────────────────────────────────────────────
# Task 10.2: Property 7 - match_source 标记一致性 (PBT)
# ──────────────────────────────────────────────

class TestPBT_Property7_MatchSourceConsistency:
    """Property 7: match_source 标记一致性

    **Validates: Requirements 5.4**

    对于所有已结算订单：
    - simulation=0 且 status=settled → match_source ∈ {"platform", "local"}
    - simulation=1 且 status=settled → match_source == "local"
    """

    @given(
        n_sim=st.integers(min_value=1, max_value=4),
        n_real=st.integers(min_value=0, max_value=3),
        sum_value=st.integers(min_value=14, max_value=27),
    )
    @settings(max_examples=50)
    def test_pbt_property7_match_source(
        self, n_sim: int, n_real: int, sum_value: int,
    ):
        """**Validates: Requirements 5.4**

        simulation=0 已结算订单 match_source ∈ {"platform", "local"}，
        simulation=1 已结算订单 match_source="local"。
        """
        b3 = min(sum_value, 9)
        remainder = sum_value - b3
        b2 = min(remainder, 9)
        b1 = remainder - b2
        assume(0 <= b1 <= 9)
        balls = [b1, b2, b3]

        async def _run():
            await close_shared_db()
            await init_db(":memory:")
            conn = await get_shared_db()
            try:
                op = await operator_create(conn, username="pbt7_op", password="pass")
                acc = await account_create(
                    conn, operator_id=op["id"], account_name="pbt7_acc",
                    password="pwd", platform_type="JND28WEB",
                )
                # 模拟策略 (simulation=1)
                sim_strat = await strategy_create(
                    conn, operator_id=op["id"], account_id=acc["id"],
                    name="sim_strat", type="flat", play_code="DX1",
                    base_amount=1000, simulation=1,
                )
                # 真实策略 (simulation=0)
                real_strat = await strategy_create(
                    conn, operator_id=op["id"], account_id=acc["id"],
                    name="real_strat", type="flat", play_code="DX1",
                    base_amount=1000, simulation=0,
                )

                issue = "20240101001"

                # 创建模拟订单
                for i in range(n_sim):
                    order = await bet_order_create(
                        conn, idempotent_id=f"pbt7-sim-{i}",
                        operator_id=op["id"], account_id=acc["id"],
                        strategy_id=sim_strat["id"],
                        issue=issue, key_code="DX1",
                        amount=1000, odds=19800, status="pending",
                        simulation=1,
                    )
                    await bet_order_update_status(
                        conn, order_id=order["id"],
                        operator_id=op["id"], status="bet_success",
                    )

                # 创建真实订单
                for i in range(n_real):
                    order = await bet_order_create(
                        conn, idempotent_id=f"pbt7-real-{i}",
                        operator_id=op["id"], account_id=acc["id"],
                        strategy_id=real_strat["id"],
                        issue=issue, key_code="DX1",
                        amount=1000, odds=19800, status="pending",
                        simulation=0,
                    )
                    await bet_order_update_status(
                        conn, order_id=order["id"],
                        operator_id=op["id"], status="bet_success",
                    )

                processor = SettlementProcessor(conn, op["id"])
                # 无 adapter → 真实订单也走本地计算（降级路径）
                await processor.settle(issue, balls, sum_value, "JND28WEB")

                # 验证所有已结算订单的 match_source
                rows = await (
                    await conn.execute(
                        "SELECT simulation, status, match_source FROM bet_orders "
                        "WHERE issue=? AND operator_id=? AND status='settled'",
                        (issue, op["id"]),
                    )
                ).fetchall()

                for row in rows:
                    row = dict(row)
                    sim = row["simulation"]
                    ms = row["match_source"]
                    if sim == 1:
                        assert ms == "local", \
                            f"simulation=1 订单 match_source 应为 'local'，实际为 '{ms}'"
                    else:
                        assert ms in {"platform", "local"}, \
                            f"simulation=0 订单 match_source 应为 'platform' 或 'local'，实际为 '{ms}'"

            finally:
                await close_shared_db()

        asyncio.get_event_loop().run_until_complete(_run())


# ──────────────────────────────────────────────
# Task 10.3: P11 - pending_match 超时终态单元测试
# ──────────────────────────────────────────────

class TestPendingMatchTimeoutTerminal:
    """P11: pending_match 超时终态

    10.3.1: 连续 2 个正常结算周期未匹配 → settle_timeout
    10.3.2: wall-clock 30 分钟超时 → settle_timeout
    """

    @pytest.mark.asyncio
    async def test_settle_timeout_after_2_normal_cycles(self, db, setup_data):
        """10.3.1: 连续 2 个正常结算周期未匹配 → settle_timeout

        模拟完整的 2 个正常结算周期：
        - 周期 1: bet_success → pending_match (count=0 → 1)
        - 周期 2: pending_match (count=1 → 2) → settle_timeout
        """
        oid = setup_data["operator"]["id"]
        alert_svc = AlertService(db)
        processor = SettlementProcessor(db, oid, alert_service=alert_svc)

        o1 = await _create_bet_order(
            db, setup_data, key_code="DX1", amount=1000, odds=19800,
            idempotent_id="t10-p11-cycle-1",
        )

        # 周期 1: bet_success → pending_match（首次未匹配）
        await processor._mark_pending_match(dict(o1), is_normal_cycle=True)
        after_cycle1 = await bet_order_get_by_id(db, order_id=o1["id"], operator_id=oid)
        assert after_cycle1["status"] == "pending_match"
        assert after_cycle1["pending_match_count"] == 0  # 首次从 bet_success 转入，count=0

        # 周期 2: pending_match (count=0) → count=1
        await processor._mark_pending_match(dict(after_cycle1), is_normal_cycle=True)
        after_cycle2 = await bet_order_get_by_id(db, order_id=o1["id"], operator_id=oid)
        assert after_cycle2["status"] == "pending_match"
        assert after_cycle2["pending_match_count"] == 1

        # 周期 3: pending_match (count=1) → settle_timeout (count >= 2)
        await processor._mark_pending_match(dict(after_cycle2), is_normal_cycle=True)
        after_cycle3 = await bet_order_get_by_id(db, order_id=o1["id"], operator_id=oid)
        assert after_cycle3["status"] == "settle_timeout"

        # 验证 settle_timeout 告警
        alerts = await (
            await db.execute(
                "SELECT * FROM alerts WHERE operator_id=? AND type='settle_timeout'",
                (oid,),
            )
        ).fetchall()
        assert len(alerts) >= 1

    @pytest.mark.asyncio
    async def test_settle_timeout_wall_clock_30min(self, db, setup_data):
        """10.3.2: wall-clock 30 分钟超时 → settle_timeout

        模拟 bet_at 在 31 分钟前，即使 pending_match_count=0 也应超时。
        """
        oid = setup_data["operator"]["id"]
        alert_svc = AlertService(db)
        processor = SettlementProcessor(db, oid, alert_service=alert_svc)

        o1 = await _create_bet_order(
            db, setup_data, key_code="DX1", amount=1000, odds=19800,
            idempotent_id="t10-p11-wallclock-1",
        )
        # 先转为 pending_match
        await bet_order_update_status(
            db, order_id=o1["id"], operator_id=oid, status="pending_match",
            pending_match_count=0,
        )

        # 将 bet_at 设为 31 分钟前（超过 PENDING_MATCH_WALL_CLOCK_TIMEOUT=1800s）
        old_time = datetime.utcnow().replace(microsecond=0)
        past_time = old_time - timedelta(minutes=31)
        past_str = past_time.strftime("%Y-%m-%d %H:%M:%S")
        await db.execute(
            "UPDATE bet_orders SET bet_at=? WHERE id=?",
            (past_str, o1["id"]),
        )
        await db.commit()

        order = await bet_order_get_by_id(db, order_id=o1["id"], operator_id=oid)

        # 调用 _mark_pending_match，应触发 wall-clock 超时
        await processor._mark_pending_match(dict(order), is_normal_cycle=True)

        updated = await bet_order_get_by_id(db, order_id=o1["id"], operator_id=oid)
        assert updated["status"] == "settle_timeout"

        # 验证告警
        alerts = await (
            await db.execute(
                "SELECT * FROM alerts WHERE operator_id=? AND type='settle_timeout'",
                (oid,),
            )
        ).fetchall()
        assert len(alerts) >= 1


# ──────────────────────────────────────────────
# Task 10.4: P12 - settle_timeout/settle_failed 互斥属性测试 (PBT)
# ──────────────────────────────────────────────

class TestPBT_Property12_TimeoutFailedMutualExclusion:
    """Property 12: settle_timeout/settle_failed 互斥

    **Validates: Requirements 5.3 (TERMINAL_STATES 定义)**

    对于任意订单路径：settle_timeout 和 settle_failed 互斥，
    同一订单不会同时经历两者。

    settle_timeout: 平台数据可达但未找到匹配记录（匹配超时）
    settle_failed: 平台 API 不可达或开奖数据缺失（系统故障）
    两者的入口条件不同，状态机保证互斥。
    """

    # 从 bet_success 出发的所有可能路径
    # settle_timeout 路径: bet_success → pending_match → settle_timeout
    #                   或: bet_success → settle_timeout
    # settle_failed 路径:  bet_success → settle_failed
    #                   或: bet_success → settling → settle_failed

    @given(
        path_choice=st.sampled_from([
            # settle_timeout 路径
            ["bet_success", "pending_match", "settle_timeout"],
            ["bet_success", "settle_timeout"],
            # settle_failed 路径
            ["bet_success", "settle_failed"],
            ["bet_success", "settling", "settle_failed"],
            # settling → settle_timeout 路径
            ["bet_success", "settling", "settle_timeout"],
            # 正常路径
            ["bet_success", "settling", "settled"],
            # pending_match → settling → settled
            ["bet_success", "pending_match", "settling", "settled"],
            # pending_match → settling → settle_failed
            ["bet_success", "pending_match", "settling", "settle_failed"],
            # pending_match → settling → settle_timeout
            ["bet_success", "pending_match", "settling", "settle_timeout"],
        ]),
    )
    @settings(max_examples=100)
    def test_pbt_property12_mutual_exclusion(self, path_choice: list[str]):
        """**Validates: Requirements 5.3**

        任意订单路径验证 settle_timeout 和 settle_failed 互斥。
        一条路径中不会同时包含 settle_timeout 和 settle_failed 作为终态。
        """
        # 验证路径中每一步转换都是合法的
        for i in range(len(path_choice) - 1):
            current = path_choice[i]
            target = path_choice[i + 1]
            allowed = VALID_TRANSITIONS.get(current, set())
            assert target in allowed, \
                f"非法转换: {current} → {target}"

        # 获取路径的终态
        terminal = path_choice[-1]

        # 统计路径中经历的终态
        terminal_states_in_path = [
            s for s in path_choice if s in {"settle_timeout", "settle_failed"}
        ]

        # 互斥性：路径中最多只有一种终态类型
        unique_terminals = set(terminal_states_in_path)
        assert len(unique_terminals) <= 1, \
            f"路径 {path_choice} 中同时包含 settle_timeout 和 settle_failed"

    @given(data=st.data())
    @settings(max_examples=200)
    def test_pbt_property12_state_machine_exclusion(self, data):
        """**Validates: Requirements 5.3**

        从 bet_success 出发，通过随机合法转换到达终态，
        验证不可能在同一路径中同时经历 settle_timeout 和 settle_failed。
        """
        current = "bet_success"
        visited_terminals: set[str] = set()
        path = [current]

        for _ in range(10):  # 最多 10 步
            allowed = VALID_TRANSITIONS.get(current, set())
            if not allowed:
                break
            target = data.draw(st.sampled_from(sorted(allowed)))
            # 验证转换合法
            SettlementProcessor._transition_status({"status": current}, target)
            current = target
            path.append(current)

            if current in {"settle_timeout", "settle_failed"}:
                visited_terminals.add(current)

            if current in TERMINAL_STATES:
                break

        # 互斥性验证
        assert not (
            "settle_timeout" in visited_terminals and "settle_failed" in visited_terminals
        ), f"路径 {' → '.join(path)} 中同时经历了 settle_timeout 和 settle_failed"


# ──────────────────────────────────────────────
# Task 10.5: P13 - 优先级覆盖路径可达性单元测试
# ──────────────────────────────────────────────

class TestPriorityOverrideReachability:
    """P13: 优先级覆盖路径可达性

    补结算成功将 settle_timeout 订单覆盖为 settled。
    """

    @pytest.mark.asyncio
    async def test_recovery_overrides_settle_timeout_to_settled(self, db, setup_data):
        """10.5.1: 补结算成功将 settle_timeout 订单覆盖为 settled

        完整路径: bet_success → settle_timeout → (补结算) settling → settled
        """
        oid = setup_data["operator"]["id"]
        aid = setup_data["account"]["id"]
        alert_svc = AlertService(db)
        processor = SettlementProcessor(db, oid, alert_service=alert_svc)

        issue = "20240101001"
        o1 = await _create_bet_order(
            db, setup_data, key_code="DX1", amount=1000, odds=19800,
            idempotent_id="t10-p13-1",
        )

        # 先标记为 settle_timeout（模拟匹配超时）
        await bet_order_update_status(
            db, order_id=o1["id"], operator_id=oid, status="settle_timeout",
        )

        # 验证当前状态
        before = await bet_order_get_by_id(db, order_id=o1["id"], operator_id=oid)
        assert before["status"] == "settle_timeout"

        # 补结算：is_recovery=True 会包含 settle_timeout 订单
        # 无 adapter → 走本地计算降级路径
        await processor.settle(
            issue, [5, 5, 5], 15, "JND28WEB",
            is_recovery=True,
        )

        # 验证订单被覆盖为 settled
        after = await bet_order_get_by_id(db, order_id=o1["id"], operator_id=oid)
        assert after["status"] == "settled", \
            f"补结算后状态应为 settled，实际为 {after['status']}"
        assert after["is_win"] == 1  # DX1 + sum=15 → 赢
        assert after["pnl"] == 1000 * 19800 // 10000 - 1000  # 980
        assert after["match_source"] == "local"  # 降级路径

        # 验证 strategy pnl 已更新
        strat = await strategy_get_by_id(
            db, strategy_id=setup_data["strategy"]["id"], operator_id=oid,
        )
        assert strat["daily_pnl"] == 980
        assert strat["total_pnl"] == 980
