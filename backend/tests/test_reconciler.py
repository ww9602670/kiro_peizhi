""" Reconciler 


- 7.4.1 Reconciler 
- 7.4.2  + 
- 7.4.3 
- 7.4.4 diff=99/100/101 
- 7.4.5 mismatch  reconcile_error 
- 7.4.6  3  mismatch 
- 7.4.7  reconcile_records 
- 7.4.8  > 500 critical 
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
import aiosqlite

from app.database import init_db, get_shared_db, close_shared_db
from app.engine.adapters.base import BalanceInfo, PlatformAdapter
from app.engine.alert import AlertService
from app.engine.reconciler import (
    CONSECUTIVE_MISMATCH_LIMIT,
    TOLERANCE_CUMULATIVE,
    TOLERANCE_SINGLE,
    Reconciler,
)
from app.models.db_ops import (
    account_create,
    account_get_by_id,
    account_update,
    bet_order_create,
    bet_order_update_status,
    operator_create,
    reconcile_record_list_by_account,
    strategy_create,
    strategy_get_by_id,
    strategy_list_by_operator,
    strategy_update_status,
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
    """ 100000  = 1000 """
    op = await operator_create(db, username="test_recon", password="pass123")
    acc = await account_create(
        db, operator_id=op["id"], account_name="recon_acc",
        password="pwd", platform_type="JND282",
    )
    #  100000 1000 
    await account_update(
        db, account_id=acc["id"], operator_id=op["id"],
        balance=100000,
    )
    acc = await account_get_by_id(db, account_id=acc["id"], operator_id=op["id"])

    strat = await strategy_create(
        db, operator_id=op["id"], account_id=acc["id"],
        name="", type="flat", play_code="DX1",
        base_amount=1000,
    )
    # strategy_create  status='stopped' running
    await strategy_update_status(
        db, strategy_id=strat["id"], operator_id=op["id"], status="running",
    )
    strat = await strategy_get_by_id(db, strategy_id=strat["id"], operator_id=op["id"])
    return {"operator": op, "account": acc, "strategy": strat}


def _make_mock_adapter(
    platform_balance_yuan: float = 1000.0,
    platform_bets: list[dict] | None = None,
) -> AsyncMock:
    """ mock PlatformAdapter"""
    adapter = AsyncMock(spec=PlatformAdapter)
    adapter.query_balance.return_value = BalanceInfo(balance=platform_balance_yuan)
    adapter.get_bet_history.return_value = platform_bets or []
    return adapter


async def _create_settled_order(
    db, setup, *, issue="20240101001", key_code="DX1",
    amount=1000, odds=19800, idempotent_id=None, simulation=1,
):
    """"""
    idem = idempotent_id or f"{issue}-{setup['strategy']['id']}-{key_code}"
    order = await bet_order_create(
        db,
        idempotent_id=idem,
        operator_id=setup["operator"]["id"],
        account_id=setup["account"]["id"],
        strategy_id=setup["strategy"]["id"],
        issue=issue, key_code=key_code,
        amount=amount, odds=odds, status="pending",
        simulation=simulation,
    )
    # pending  bet_success  settled
    await bet_order_update_status(
        db, order_id=order["id"],
        operator_id=setup["operator"]["id"],
        status="bet_success",
    )
    await bet_order_update_status(
        db, order_id=order["id"],
        operator_id=setup["operator"]["id"],
        status="settled", is_win=1, pnl=980,
    )
    return order


async def _create_in_transit_order(
    db, setup, *, issue="20240101002", key_code="DX1",
    amount=500, idempotent_id=None, simulation=1,
):
    """bet_success """
    idem = idempotent_id or f"{issue}-{setup['strategy']['id']}-{key_code}-transit"
    order = await bet_order_create(
        db,
        idempotent_id=idem,
        operator_id=setup["operator"]["id"],
        account_id=setup["account"]["id"],
        strategy_id=setup["strategy"]["id"],
        issue=issue, key_code=key_code,
        amount=amount, odds=19800, status="pending",
        simulation=simulation,
    )
    await bet_order_update_status(
        db, order_id=order["id"],
        operator_id=setup["operator"]["id"],
        status="bet_success",
    )
    return order


# 
# 7.4.1 Reconciler 
# 

class TestReconcilerInit:
    """Reconciler """

    @pytest.mark.asyncio
    async def test_init(self, db, setup_data):
        adapter = _make_mock_adapter()
        alert_svc = AlertService(db)
        recon = Reconciler(db, adapter, alert_svc, setup_data["operator"]["id"])

        assert recon.db is db
        assert recon.adapter is adapter
        assert recon.alert_service is alert_svc
        assert recon.operator_id == setup_data["operator"]["id"]
        assert recon._consecutive_mismatch_count == {}
        assert recon._cumulative_diff == {}


# 
# 7.4.2 
# 

class TestCountPlatformBets:
    """"""

    def test_count_matching_issue(self):
        bets = [
            {"Installments": "20240101001", "Amount": 100},
            {"Installments": "20240101001", "Amount": 200},
            {"Installments": "20240101002", "Amount": 300},
        ]
        assert Reconciler._count_platform_bets(bets, "20240101001") == 2

    def test_count_no_match(self):
        bets = [{"Installments": "20240101002", "Amount": 100}]
        assert Reconciler._count_platform_bets(bets, "20240101001") == 0

    def test_count_empty_bets(self):
        assert Reconciler._count_platform_bets([], "20240101001") == 0

    def test_count_with_issue_key(self):
        """ issue """
        bets = [{"issue": "20240101001", "Amount": 100}]
        assert Reconciler._count_platform_bets(bets, "20240101001") == 1


# 
# 7.4.3 
# 

class TestCalcLocalBalance:
    """本地余额计算：直接返回 db_balance"""

    @pytest.mark.asyncio
    async def test_balance_returns_db_balance(self, db, setup_data):
        """直接返回 DB 中的 balance"""
        adapter = _make_mock_adapter()
        alert_svc = AlertService(db)
        recon = Reconciler(db, adapter, alert_svc, setup_data["operator"]["id"])

        balance = await recon._calc_local_balance(setup_data["account"]["id"])
        assert balance == 100000

    @pytest.mark.asyncio
    async def test_balance_with_in_transit_unchanged(self, db, setup_data):
        """in-transit 订单不影响返回值（直接用 db_balance）"""
        await _create_in_transit_order(db, setup_data, amount=500)

        adapter = _make_mock_adapter()
        alert_svc = AlertService(db)
        recon = Reconciler(db, adapter, alert_svc, setup_data["operator"]["id"])

        balance = await recon._calc_local_balance(setup_data["account"]["id"])
        assert balance == 100000  # db_balance 不变

    @pytest.mark.asyncio
    async def test_balance_nonexistent_account(self, db, setup_data):
        """不存在的账号返回 0"""
        adapter = _make_mock_adapter()
        alert_svc = AlertService(db)
        recon = Reconciler(db, adapter, alert_svc, setup_data["operator"]["id"])

        balance = await recon._calc_local_balance(99999)
        assert balance == 0


# 
# 7.4.4  diff=99/100/101
# 

class TestToleranceJudgment:
    """"""

    @pytest.mark.asyncio
    async def test_diff_99_matched(self, db, setup_data):
        """diff=99  matched"""
        await _create_settled_order(db, setup_data, issue="20240101001", simulation=1)
        #  =  + 0.99  = 1000.99 
        adapter = _make_mock_adapter(platform_balance_yuan=1000.99)
        alert_svc = AlertService(db)
        recon = Reconciler(db, adapter, alert_svc, setup_data["operator"]["id"])

        await recon.reconcile(setup_data["account"]["id"], "20240101001")

        records, total = await reconcile_record_list_by_account(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"],
        )
        assert total == 1
        assert records[0]["status"] == "matched"
        assert records[0]["diff_amount"] == 99

    @pytest.mark.asyncio
    async def test_diff_100_matched(self, db, setup_data):
        """diff=100  matched"""
        await _create_settled_order(db, setup_data, issue="20240101001", simulation=1)
        #  =  + 1.00  = 1001.00 
        adapter = _make_mock_adapter(platform_balance_yuan=1001.00)
        alert_svc = AlertService(db)
        recon = Reconciler(db, adapter, alert_svc, setup_data["operator"]["id"])

        await recon.reconcile(setup_data["account"]["id"], "20240101001")

        records, _ = await reconcile_record_list_by_account(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"],
        )
        assert records[0]["status"] == "matched"
        assert records[0]["diff_amount"] == 100

    @pytest.mark.asyncio
    async def test_diff_101_mismatch(self, db, setup_data):
        """diff=101  mismatch"""
        await _create_settled_order(db, setup_data, issue="20240101001", simulation=1)
        #  =  + 1.01  = 1001.01 
        adapter = _make_mock_adapter(platform_balance_yuan=1001.01)
        alert_svc = AlertService(db)
        recon = Reconciler(db, adapter, alert_svc, setup_data["operator"]["id"])

        await recon.reconcile(setup_data["account"]["id"], "20240101001")

        records, _ = await reconcile_record_list_by_account(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"],
        )
        assert records[0]["status"] == "mismatch"
        assert records[0]["diff_amount"] == 101

    @pytest.mark.asyncio
    async def test_diff_0_matched(self, db, setup_data):
        """diff=0  matched"""
        await _create_settled_order(db, setup_data, issue="20240101001", simulation=1)
        adapter = _make_mock_adapter(platform_balance_yuan=1000.00)
        alert_svc = AlertService(db)
        recon = Reconciler(db, adapter, alert_svc, setup_data["operator"]["id"])

        await recon.reconcile(setup_data["account"]["id"], "20240101001")

        records, _ = await reconcile_record_list_by_account(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"],
        )
        assert records[0]["status"] == "matched"
        assert records[0]["diff_amount"] == 0

    @pytest.mark.asyncio
    async def test_platform_lower_than_local(self, db, setup_data):
        """"""
        await _create_settled_order(db, setup_data, issue="20240101001", simulation=1)
        #  = 998.99  = 99899  = 100000 diff = 101
        adapter = _make_mock_adapter(platform_balance_yuan=998.99)
        alert_svc = AlertService(db)
        recon = Reconciler(db, adapter, alert_svc, setup_data["operator"]["id"])

        await recon.reconcile(setup_data["account"]["id"], "20240101001")

        records, _ = await reconcile_record_list_by_account(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"],
        )
        assert records[0]["status"] == "mismatch"
        assert records[0]["diff_amount"] == 101


# 
# 7.4.5 mismatch  reconcile_error 
# 

class TestMismatchHandling:
    """mismatch """

    @pytest.mark.asyncio
    async def test_mismatch_sends_alert(self, db, setup_data):
        """mismatch  reconcile_error """
        await _create_settled_order(db, setup_data, issue="20240101001", simulation=1)
        #  = 1002  = 100200  = 100000 diff = 200 > 100
        adapter = _make_mock_adapter(platform_balance_yuan=1002.00)
        alert_svc = AlertService(db)
        recon = Reconciler(db, adapter, alert_svc, setup_data["operator"]["id"])

        await recon.reconcile(setup_data["account"]["id"], "20240101001")

        # 
        alerts_rows = await (
            await db.execute(
                "SELECT * FROM alerts WHERE operator_id=? AND type='reconcile_error'",
                (setup_data["operator"]["id"],),
            )
        ).fetchall()
        assert len(alerts_rows) >= 1
        alert = dict(alerts_rows[0])
        assert "" in alert["title"]
        assert alert["level"] == "critical"

    @pytest.mark.asyncio
    async def test_matched_no_alert(self, db, setup_data):
        """matched """
        await _create_settled_order(db, setup_data, issue="20240101001", simulation=1)
        adapter = _make_mock_adapter(platform_balance_yuan=1000.00)
        alert_svc = AlertService(db)
        recon = Reconciler(db, adapter, alert_svc, setup_data["operator"]["id"])

        await recon.reconcile(setup_data["account"]["id"], "20240101001")

        alerts_rows = await (
            await db.execute(
                "SELECT * FROM alerts WHERE operator_id=? AND type='reconcile_error'",
                (setup_data["operator"]["id"],),
            )
        ).fetchall()
        assert len(alerts_rows) == 0

    @pytest.mark.asyncio
    async def test_mismatch_does_not_modify_bet_orders(self, db, setup_data):
        """mismatch  bet_orders DB """
        # 
        order = await _create_settled_order(db, setup_data)

        #  mismatch
        adapter = _make_mock_adapter(platform_balance_yuan=1005.00)
        alert_svc = AlertService(db)
        recon = Reconciler(db, adapter, alert_svc, setup_data["operator"]["id"])

        await recon.reconcile(setup_data["account"]["id"], "20240101001")

        #  settled
        row = await (
            await db.execute("SELECT status FROM bet_orders WHERE id=?", (order["id"],))
        ).fetchone()
        assert dict(row)["status"] == "settled"


# 
# 7.4.6  3  mismatch 
# 

class TestConsecutiveMismatchPause:
    """ mismatch """

    @pytest.mark.asyncio
    async def test_3_consecutive_mismatches_pause_strategies(self, db, setup_data):
        """ 3  mismatch   running """
        #  running 
        strat2 = await strategy_create(
            db, operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            name="2", type="flat", play_code="DX2",
            base_amount=500,
        )
        await strategy_update_status(
            db, strategy_id=strat2["id"],
            operator_id=setup_data["operator"]["id"],
            status="running",
        )
        strat2 = await strategy_get_by_id(
            db, strategy_id=strat2["id"],
            operator_id=setup_data["operator"]["id"],
        )

        # 为每个期号创建模拟订单
        for i in range(3):
            await _create_settled_order(db, setup_data, issue=f"2024010100{i+1}", simulation=1, idempotent_id=f"sim-pause-{i}")

        adapter = _make_mock_adapter(platform_balance_yuan=1005.00)  # diff=500 > 100
        alert_svc = AlertService(db)
        recon = Reconciler(db, adapter, alert_svc, setup_data["operator"]["id"])

        #  3  mismatch
        for i in range(3):
            #  alert_svc 
            alert_svc = AlertService(db)
            recon.alert_service = alert_svc
            await recon.reconcile(setup_data["account"]["id"], f"2024010100{i+1}")

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

    @pytest.mark.asyncio
    async def test_2_mismatches_no_pause(self, db, setup_data):
        """ 2  mismatch """
        # 为每个期号创建模拟订单
        for i in range(2):
            await _create_settled_order(db, setup_data, issue=f"2024010100{i+1}", simulation=1, idempotent_id=f"sim-nopause-{i}")

        adapter = _make_mock_adapter(platform_balance_yuan=1005.00)
        alert_svc = AlertService(db)
        recon = Reconciler(db, adapter, alert_svc, setup_data["operator"]["id"])

        for i in range(2):
            alert_svc = AlertService(db)
            recon.alert_service = alert_svc
            await recon.reconcile(setup_data["account"]["id"], f"2024010100{i+1}")

        strat = await strategy_get_by_id(
            db, strategy_id=setup_data["strategy"]["id"],
            operator_id=setup_data["operator"]["id"],
        )
        assert strat["status"] == "running"

    @pytest.mark.asyncio
    async def test_matched_resets_consecutive_count(self, db, setup_data):
        """matched  mismatch """
        account_id = setup_data["account"]["id"]

        # 为所有期号创建模拟订单
        for i in range(6):
            await _create_settled_order(db, setup_data, issue=f"2024010100{i+1}", simulation=1, idempotent_id=f"sim-reset-{i}")

        # 2  mismatch
        adapter_bad = _make_mock_adapter(platform_balance_yuan=1005.00)
        alert_svc = AlertService(db)
        recon = Reconciler(db, adapter_bad, alert_svc, setup_data["operator"]["id"])

        for i in range(2):
            alert_svc = AlertService(db)
            recon.alert_service = alert_svc
            await recon.reconcile(account_id, f"2024010100{i+1}")

        assert recon._consecutive_mismatch_count[account_id] == 2

        # 1  matched  
        recon.adapter = _make_mock_adapter(platform_balance_yuan=1000.00)
        await recon.reconcile(account_id, "20240101003")
        assert recon._consecutive_mismatch_count[account_id] == 0

        #  2  mismatch  
        recon.adapter = _make_mock_adapter(platform_balance_yuan=1005.00)
        for i in range(2):
            alert_svc = AlertService(db)
            recon.alert_service = alert_svc
            await recon.reconcile(account_id, f"2024010100{i+4}")

        strat = await strategy_get_by_id(
            db, strategy_id=setup_data["strategy"]["id"],
            operator_id=setup_data["operator"]["id"],
        )
        assert strat["status"] == "running"

    @pytest.mark.asyncio
    async def test_only_running_strategies_paused(self, db, setup_data):
        """ running stopped """
        #  stopped 
        strat_stopped = await strategy_create(
            db, operator_id=setup_data["operator"]["id"],
            account_id=setup_data["account"]["id"],
            name="", type="flat", play_code="DX2",
            base_amount=500,
        )
        # strategy_create  status='stopped'

        # 为每个期号创建模拟订单
        for i in range(3):
            await _create_settled_order(db, setup_data, issue=f"2024010100{i+1}", simulation=1, idempotent_id=f"sim-only-{i}")

        adapter = _make_mock_adapter(platform_balance_yuan=1005.00)
        alert_svc = AlertService(db)
        recon = Reconciler(db, adapter, alert_svc, setup_data["operator"]["id"])

        for i in range(3):
            alert_svc = AlertService(db)
            recon.alert_service = alert_svc
            await recon.reconcile(setup_data["account"]["id"], f"2024010100{i+1}")

        # running 
        s_running = await strategy_get_by_id(
            db, strategy_id=setup_data["strategy"]["id"],
            operator_id=setup_data["operator"]["id"],
        )
        assert s_running["status"] == "paused"

        # stopped 
        s_stopped = await strategy_get_by_id(
            db, strategy_id=strat_stopped["id"],
            operator_id=setup_data["operator"]["id"],
        )
        assert s_stopped["status"] == "stopped"


# 
# 7.4.7 
# 

class TestReconcileRecordPersistence:
    """"""

    @pytest.mark.asyncio
    async def test_record_saved_on_matched(self, db, setup_data):
        """matched """
        await _create_settled_order(db, setup_data, issue="20240101001", simulation=1)
        adapter = _make_mock_adapter(platform_balance_yuan=1000.00)
        alert_svc = AlertService(db)
        recon = Reconciler(db, adapter, alert_svc, setup_data["operator"]["id"])

        await recon.reconcile(setup_data["account"]["id"], "20240101001")

        records, total = await reconcile_record_list_by_account(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"],
        )
        assert total == 1
        rec = records[0]
        assert rec["account_id"] == setup_data["account"]["id"]
        assert rec["issue"] == "20240101001"
        assert rec["status"] == "matched"
        assert rec["local_balance"] == 100000
        assert rec["platform_balance"] == 100000
        assert rec["diff_amount"] == 0

    @pytest.mark.asyncio
    async def test_record_saved_on_mismatch(self, db, setup_data):
        """mismatch """
        await _create_settled_order(db, setup_data, issue="20240101001", simulation=1)
        adapter = _make_mock_adapter(platform_balance_yuan=1002.00)
        alert_svc = AlertService(db)
        recon = Reconciler(db, adapter, alert_svc, setup_data["operator"]["id"])

        await recon.reconcile(setup_data["account"]["id"], "20240101001")

        records, _ = await reconcile_record_list_by_account(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"],
        )
        rec = records[0]
        assert rec["status"] == "mismatch"
        assert rec["diff_amount"] == 200
        assert rec["local_balance"] == 100000
        assert rec["platform_balance"] == 100200

    @pytest.mark.asyncio
    async def test_record_contains_detail_json(self, db, setup_data):
        """ detail JSON"""
        await _create_settled_order(db, setup_data, issue="20240101001", simulation=1)
        adapter = _make_mock_adapter(platform_balance_yuan=1000.50)
        alert_svc = AlertService(db)
        recon = Reconciler(db, adapter, alert_svc, setup_data["operator"]["id"])

        await recon.reconcile(setup_data["account"]["id"], "20240101001")

        records, _ = await reconcile_record_list_by_account(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"],
        )
        detail = json.loads(records[0]["detail"])
        assert "local_count" in detail
        assert "platform_count" in detail
        assert "local_balance" in detail
        assert "platform_balance" in detail
        assert "diff" in detail

    @pytest.mark.asyncio
    async def test_multiple_reconcile_records(self, db, setup_data):
        """"""
        await _create_settled_order(db, setup_data, issue="20240101001", simulation=1, idempotent_id="sim-multi-1")
        await _create_settled_order(db, setup_data, issue="20240101002", simulation=1, idempotent_id="sim-multi-2")
        adapter = _make_mock_adapter(platform_balance_yuan=1000.00)
        alert_svc = AlertService(db)
        recon = Reconciler(db, adapter, alert_svc, setup_data["operator"]["id"])

        await recon.reconcile(setup_data["account"]["id"], "20240101001")
        await recon.reconcile(setup_data["account"]["id"], "20240101002")

        records, total = await reconcile_record_list_by_account(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"],
        )
        assert total == 2

    @pytest.mark.asyncio
    async def test_record_bet_counts(self, db, setup_data):
        """"""
        #  2 
        await _create_settled_order(
            db, setup_data, issue="20240101001",
            key_code="DX1", idempotent_id="settle-1",
        )
        await _create_settled_order(
            db, setup_data, issue="20240101001",
            key_code="DX2", idempotent_id="settle-2",
        )

        #  3 
        platform_bets = [
            {"Installments": "20240101001", "Amount": 100},
            {"Installments": "20240101001", "Amount": 200},
            {"Installments": "20240101001", "Amount": 300},
        ]
        adapter = _make_mock_adapter(
            platform_balance_yuan=1000.00,
            platform_bets=platform_bets,
        )
        alert_svc = AlertService(db)
        recon = Reconciler(db, adapter, alert_svc, setup_data["operator"]["id"])

        await recon.reconcile(setup_data["account"]["id"], "20240101001")

        records, _ = await reconcile_record_list_by_account(
            db, account_id=setup_data["account"]["id"],
            operator_id=setup_data["operator"]["id"],
        )
        rec = records[0]
        assert rec["local_bet_count"] == 2
        assert rec["platform_bet_count"] == 3


# 
# 7.4.8  > 500 critical 
# 

class TestCumulativeDiffCritical:
    """ critical """

    @pytest.mark.asyncio
    async def test_cumulative_over_500_triggers_critical(self, db, setup_data):
        """ 500  _handle_critical """
        account_id = setup_data["account"]["id"]
        op_id = setup_data["operator"]["id"]

        # 为每个期号创建模拟订单
        for i in range(3):
            await _create_settled_order(db, setup_data, issue=f"2024010100{i+1}", simulation=1, idempotent_id=f"sim-cum500-{i}")

        #  diff=200mismatch3  = 600 > 500
        adapter = _make_mock_adapter(platform_balance_yuan=1002.00)  # diff=200
        alert_svc = AsyncMock(spec=AlertService)
        alert_svc.send = AsyncMock(return_value=True)
        recon = Reconciler(db, adapter, alert_svc, op_id)

        for i in range(3):
            await recon.reconcile(account_id, f"2024010100{i+1}")

        assert recon._cumulative_diff[account_id] == 600

        #  alert_service.send 
        send_calls = alert_svc.send.call_args_list
        cumulative_calls = [
            c for c in send_calls
            if "累计" in (c.kwargs.get("title", "") or "")
        ]
        assert len(cumulative_calls) >= 1

    @pytest.mark.asyncio
    async def test_cumulative_under_500_no_critical(self, db, setup_data):
        """ 500  critical """
        account_id = setup_data["account"]["id"]
        op_id = setup_data["operator"]["id"]

        # 为每个期号创建模拟订单
        for i in range(2):
            await _create_settled_order(db, setup_data, issue=f"2024010100{i+1}", simulation=1, idempotent_id=f"sim-cum-no-{i}")

        #  diff=2002  = 400 < 500
        adapter = _make_mock_adapter(platform_balance_yuan=1002.00)
        alert_svc = AsyncMock(spec=AlertService)
        alert_svc.send = AsyncMock(return_value=True)
        recon = Reconciler(db, adapter, alert_svc, op_id)

        for i in range(2):
            await recon.reconcile(account_id, f"2024010100{i+1}")

        assert recon._cumulative_diff[account_id] == 400

        # 
        send_calls = alert_svc.send.call_args_list
        cumulative_calls = [
            c for c in send_calls
            if "累计" in (c.kwargs.get("title", "") or "")
        ]
        assert len(cumulative_calls) == 0

    @pytest.mark.asyncio
    async def test_cumulative_includes_matched_diffs(self, db, setup_data):
        """ matched  matched
        
        注意：对账 matched 时会校准本地余额到平台余额，
        所以后续对账 diff 会变为 0。累计 diff 只计第一次。
        """
        account_id = setup_data["account"]["id"]
        op_id = setup_data["operator"]["id"]

        # 为每个期号创建模拟订单
        for i in range(6):
            await _create_settled_order(db, setup_data, issue=f"2024010100{i+1}", simulation=1, idempotent_id=f"sim-cum-match-{i}")

        #  diff=99matched校准后后续 diff=0
        adapter = _make_mock_adapter(platform_balance_yuan=1000.99)  # diff=99
        alert_svc = AsyncMock(spec=AlertService)
        alert_svc.send = AsyncMock(return_value=True)
        recon = Reconciler(db, adapter, alert_svc, op_id)

        for i in range(6):
            await recon.reconcile(account_id, f"2024010100{i+1}")

        # 第一次 diff=99，校准后后续 diff=0，累计=99
        assert recon._cumulative_diff[account_id] == 99

        # 累计 < 500，不触发 critical
        send_calls = alert_svc.send.call_args_list
        cumulative_calls = [
            c for c in send_calls
            if "累计" in (c.kwargs.get("title", "") or "")
        ]
        assert len(cumulative_calls) == 0


# 
# 
# 

class TestInTransitDeduction:
    """in-transit 不影响对账余额（平台扣款实时，db_balance 已反映）"""

    @pytest.mark.asyncio
    async def test_in_transit_does_not_affect_balance(self, db, setup_data):
        """in-transit 订单不影响对账余额"""
        account_id = setup_data["account"]["id"]
        op_id = setup_data["operator"]["id"]

        # 创建一个模拟已结算订单触发 _reconcile_simulated 路径
        await _create_settled_order(db, setup_data, issue="20240101001", simulation=1)
        await _create_in_transit_order(db, setup_data, issue="20240101002", amount=500, simulation=1)

        # db_balance=100000, 平台=1000元=100000分 → diff=0 matched
        adapter = _make_mock_adapter(platform_balance_yuan=1000.00)
        alert_svc = AlertService(db)
        recon = Reconciler(db, adapter, alert_svc, op_id)

        await recon.reconcile(account_id, "20240101001")

        records, _ = await reconcile_record_list_by_account(
            db, account_id=account_id, operator_id=op_id,
        )
        assert records[0]["status"] == "matched"
        assert records[0]["local_balance"] == 100000
        assert records[0]["platform_balance"] == 100000
        assert records[0]["diff_amount"] == 0

    @pytest.mark.asyncio
    async def test_real_diff_detected(self, db, setup_data):
        """真实差异能被正确检测"""
        account_id = setup_data["account"]["id"]
        op_id = setup_data["operator"]["id"]

        # 创建模拟订单触发 _reconcile_simulated 路径
        await _create_settled_order(db, setup_data, issue="20240101001", simulation=1)

        # db_balance=100000, 平台=980元=98000分 → diff=2000 → mismatch
        adapter = _make_mock_adapter(platform_balance_yuan=980.00)
        alert_svc = AlertService(db)
        recon = Reconciler(db, adapter, alert_svc, op_id)

        await recon.reconcile(account_id, "20240101001")

        records, _ = await reconcile_record_list_by_account(
            db, account_id=account_id, operator_id=op_id,
        )
        assert records[0]["status"] == "mismatch"
        assert records[0]["diff_amount"] == 2000


# 
# PBT: P18 
# 

from hypothesis import given, settings, strategies as st


class TestPBT_P18_ToleranceJudgment:
    """P18:   |diff|100matched, |diff|>100mismatch

    **Validates: Requirements 7.1**

    Property: For any diff value:
      - |diff|  100  matched
      - |diff| > 100  mismatch

    The Reconciler compares platform_balance (in yuan, converted to fen)
    vs local_balance (in fen). TOLERANCE_SINGLE = 100 fen.
    """

    @given(diff_fen=st.integers(min_value=-5000, max_value=5000))
    @settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_pbt_tolerance_judgment(self, diff_fen: int):
        """For any diff in fen, tolerance judgment is correct.

        **Validates: Requirements 7.1**
        """
        # Setup: fresh in-memory DB for each example
        await close_shared_db()
        await init_db(":memory:")
        conn = await get_shared_db()

        try:
            # Create operator + account with known balance
            base_balance_fen = 100000  # 1000 yuan
            op = await operator_create(conn, username="pbt_op", password="pass123")
            acc = await account_create(
                conn, operator_id=op["id"], account_name="pbt_acc",
                password="pwd", platform_type="JND282",
            )
            await account_update(
                conn, account_id=acc["id"], operator_id=op["id"],
                balance=base_balance_fen,
            )

            # Platform balance = local balance + diff_fen
            # platform_balance_yuan = (base_balance_fen + diff_fen) / 100
            platform_balance_yuan = (base_balance_fen + diff_fen) / 100.0

            adapter = _make_mock_adapter(platform_balance_yuan=platform_balance_yuan)
            alert_svc = AlertService(conn)
            recon = Reconciler(conn, adapter, alert_svc, op["id"])

            # 创建模拟订单触发 _reconcile_simulated 路径
            strat = await strategy_create(
                conn, operator_id=op["id"], account_id=acc["id"],
                name="pbt_strat", type="flat", play_code="DX1",
                base_amount=1000,
            )
            pbt_order = await bet_order_create(
                conn,
                idempotent_id=f"pbt-{diff_fen}",
                operator_id=op["id"],
                account_id=acc["id"],
                strategy_id=strat["id"],
                issue="20240101001", key_code="DX1",
                amount=1000, odds=19800, status="pending",
                simulation=1,
            )
            await bet_order_update_status(
                conn, order_id=pbt_order["id"],
                operator_id=op["id"], status="bet_success",
            )
            await bet_order_update_status(
                conn, order_id=pbt_order["id"],
                operator_id=op["id"], status="settled", is_win=1, pnl=980,
            )

            await recon.reconcile(acc["id"], "20240101001")

            records, total = await reconcile_record_list_by_account(
                conn, account_id=acc["id"], operator_id=op["id"],
            )
            assert total == 1
            rec = records[0]

            abs_diff = abs(diff_fen)
            if abs_diff <= TOLERANCE_SINGLE:
                assert rec["status"] == "matched", (
                    f"|diff|={abs_diff}  {TOLERANCE_SINGLE} should be matched, got {rec['status']}"
                )
            else:
                assert rec["status"] == "mismatch", (
                    f"|diff|={abs_diff} > {TOLERANCE_SINGLE} should be mismatch, got {rec['status']}"
                )
        finally:
            await close_shared_db()


# ==================================================================
# PBT: Property 6 — 终态验证（真实模式对账）
# ==================================================================

from app.engine.settlement import TERMINAL_STATES

# 所有可能的订单状态
ALL_STATUSES = [
    "pending", "betting", "bet_success", "bet_failed",
    "settling", "pending_match", "settled",
    "settle_timeout", "settle_failed", "reconcile_error",
]


class TestPBT_P6_TerminalStateVerification:
    """Property 6: 终态验证（真实模式对账）

    **Validates: Requirements 6.1, 6.2**

    For any order status set S:
    - If S ⊆ TERMINAL_STATES → no unsettled_orders alert
    - If ∃s ∈ S, s ∉ TERMINAL_STATES → triggers unsettled_orders alert
    """

    @given(
        statuses=st.lists(
            st.sampled_from(ALL_STATUSES),
            min_size=1,
            max_size=8,
        )
    )
    @settings(max_examples=200)
    @pytest.mark.asyncio
    async def test_pbt_terminal_state_verification(self, statuses: list[str]):
        """For any set of real order statuses, _reconcile_real correctly
        detects non-terminal orders and triggers unsettled_orders alert.

        **Validates: Requirements 6.1, 6.2**
        """
        await close_shared_db()
        await init_db(":memory:")
        conn = await get_shared_db()

        try:
            op = await operator_create(conn, username="pbt_p6_op", password="pass123")
            acc = await account_create(
                conn, operator_id=op["id"], account_name="pbt_p6_acc",
                password="pwd", platform_type="JND282",
            )

            # 构造 mock 订单列表（不需要真实 DB 订单，直接传给 _reconcile_real）
            orders = [
                {"id": i + 1, "status": s, "simulation": 0}
                for i, s in enumerate(statuses)
            ]

            adapter = _make_mock_adapter()
            alert_svc = AsyncMock(spec=AlertService)
            alert_svc.send = AsyncMock(return_value=True)
            recon = Reconciler(conn, adapter, alert_svc, op["id"])

            await recon._reconcile_real(acc["id"], "20240101001", orders)

            # 判定：是否所有状态都在终态
            all_terminal = all(s in TERMINAL_STATES for s in statuses)
            non_terminal_statuses = [s for s in statuses if s not in TERMINAL_STATES]

            unsettled_calls = [
                c for c in alert_svc.send.call_args_list
                if c.kwargs.get("alert_type") == "unsettled_orders"
            ]

            if all_terminal:
                assert len(unsettled_calls) == 0, (
                    f"All statuses {statuses} are terminal, "
                    f"but unsettled_orders alert was sent"
                )
            else:
                assert len(unsettled_calls) == 1, (
                    f"Non-terminal statuses {non_terminal_statuses} exist, "
                    f"but unsettled_orders alert was not sent (calls={len(unsettled_calls)})"
                )
                # 验证 detail 内容
                call_kwargs = unsettled_calls[0].kwargs
                detail = json.loads(call_kwargs["detail"])
                assert detail["count"] == len(non_terminal_statuses)
                assert detail["issue"] == "20240101001"
                assert set(detail["statuses"]) == set(non_terminal_statuses)
        finally:
            await close_shared_db()
