"""集成测试：倒计时 → 结算 → 对账 完整流程

Task 11: 测试多组件协作（SettlementProcessor, Reconciler, Worker 锁机制）

11.1 端到端集成测试：倒计时 → 结算 → 对账（mock adapter）
11.2 混合模式测试：同一期号下真实 + 模拟订单
11.3 补结算测试：Worker 重启后恢复 settle_timeout/settle_failed 订单
11.4 锁竞态故障注入测试：A 持锁卡顿 > TTL → B 抢锁 → A 恢复
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.database import init_db, get_shared_db, close_shared_db
from app.engine.adapters.base import BalanceInfo, InstallInfo, PlatformAdapter
from app.engine.alert import AlertService
from app.engine.reconciler import Reconciler
from app.engine.settlement import (
    TERMINAL_STATES,
    SettlementProcessor,
)
from app.engine.worker import AccountWorker
from app.models.db_ops import (
    account_create,
    account_get_by_id,
    account_update,
    bet_order_create,
    bet_order_get_by_id,
    bet_order_update_status,
    operator_create,
    strategy_create,
    strategy_get_by_id,
)


# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────

@pytest.fixture()
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture()
async def db():
    """内存 SQLite"""
    await close_shared_db()
    await init_db(":memory:")
    conn = await get_shared_db()
    yield conn
    await close_shared_db()


@pytest.fixture()
async def setup_data(db):
    """创建 operator + account(balance=100000) + strategy"""
    op = await operator_create(db, username="integ_op", password="pass123")
    acc = await account_create(
        db, operator_id=op["id"], account_name="integ_acc",
        password="pwd", platform_type="JND28WEB",
    )
    await account_update(
        db, account_id=acc["id"], operator_id=op["id"], balance=100000,
    )
    acc = await account_get_by_id(db, account_id=acc["id"], operator_id=op["id"])
    strat = await strategy_create(
        db, operator_id=op["id"], account_id=acc["id"],
        name="集成测试策略", type="flat", play_code="DX1",
        base_amount=1000,
    )
    return {"operator": op, "account": acc, "strategy": strat}


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

async def _create_order(db, setup, *, issue="20240101001", key_code="DX1",
                        amount=1000, odds=19800, status="bet_success",
                        simulation=0, idempotent_id=None):
    """创建订单并推进到指定状态"""
    idem = idempotent_id or f"{issue}-{setup['strategy']['id']}-{key_code}-sim{simulation}-{amount}"
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
    if status == "bet_success":
        await bet_order_update_status(
            db, order_id=order["id"],
            operator_id=setup["operator"]["id"],
            status="bet_success",
        )
    elif status == "settle_timeout":
        await bet_order_update_status(
            db, order_id=order["id"],
            operator_id=setup["operator"]["id"],
            status="bet_success",
        )
        await bet_order_update_status(
            db, order_id=order["id"],
            operator_id=setup["operator"]["id"],
            status="settle_timeout",
        )
    elif status == "settle_failed":
        await bet_order_update_status(
            db, order_id=order["id"],
            operator_id=setup["operator"]["id"],
            status="bet_success",
        )
        await bet_order_update_status(
            db, order_id=order["id"],
            operator_id=setup["operator"]["id"],
            status="settle_failed",
        )
    return await bet_order_get_by_id(
        db, order_id=order["id"], operator_id=setup["operator"]["id"],
    )


def _make_platform_bet(issue: str, key_code: str, amount_yuan: float,
                       win_amount_yuan: float) -> dict:
    """构造 Topbetlist 返回的单条平台记录"""
    return {
        "Installments": issue,
        "KeyCode": key_code,
        "Amount": str(amount_yuan),
        "WinAmount": str(win_amount_yuan),
    }


def _make_mock_adapter(
    platform_balance_yuan: float = 1000.0,
    platform_bets: list[dict] | None = None,
    lottery_results: list[dict] | None = None,
) -> AsyncMock:
    """构造 mock PlatformAdapter"""
    adapter = AsyncMock(spec=PlatformAdapter)
    adapter.query_balance.return_value = BalanceInfo(balance=platform_balance_yuan)
    adapter.get_bet_history.return_value = platform_bets or []
    adapter.get_lottery_results.return_value = lottery_results or []
    return adapter



# ══════════════════════════════════════════════
# 11.1 端到端集成测试
# ══════════════════════════════════════════════

class TestEndToEndSettlement:
    """模拟完整的 结算 → 对账 流程（mock adapter）

    DoD：真实订单 match_source=platform，模拟订单 match_source=local，对账无告警
    """

    @pytest.mark.asyncio
    async def test_full_settle_and_reconcile(self, db, setup_data):
        """端到端：真实订单走平台匹配，模拟订单走本地计算，对账无告警"""
        op_id = setup_data["operator"]["id"]
        acc_id = setup_data["account"]["id"]
        issue = "20240201001"
        balls = [3, 5, 7]
        sum_value = 15  # 大 → DX1 中奖

        # 1. 创建真实订单（simulation=0）
        real_order = await _create_order(
            db, setup_data, issue=issue, key_code="DX1",
            amount=1000, odds=19800, simulation=0,
            idempotent_id=f"{issue}-real-DX1",
        )

        # 2. 创建模拟订单（simulation=1）
        sim_order = await _create_order(
            db, setup_data, issue=issue, key_code="DX1",
            amount=500, odds=19800, simulation=1,
            idempotent_id=f"{issue}-sim-DX1",
        )

        # 3. 构造 mock adapter
        # 真实订单：平台返回 WinAmount=19.80（1000分 * 19800/10000 = 1980分 = 19.80元）
        platform_bets = [
            _make_platform_bet(issue, "DX1", 10.0, 19.80),  # amount=1000分=10元
        ]
        adapter = _make_mock_adapter(
            platform_balance_yuan=1019.80,  # 原始1000 + 赢19.80
            platform_bets=platform_bets,
        )

        # 4. 执行结算
        alert_svc = AlertService(db)
        settler = SettlementProcessor(db, op_id, alert_service=alert_svc)
        await settler.settle(
            issue=issue, balls=balls, sum_value=sum_value,
            platform_type="JND28WEB", adapter=adapter,
        )

        # 5. 验证真实订单
        real = await bet_order_get_by_id(db, order_id=real_order["id"], operator_id=op_id)
        assert real["status"] == "settled"
        assert real["match_source"] == "platform"
        assert real["is_win"] == 1
        # pnl = int(19.80 * 100) - 1000 = 1980 - 1000 = 980
        assert real["pnl"] == 980

        # 6. 验证模拟订单
        sim = await bet_order_get_by_id(db, order_id=sim_order["id"], operator_id=op_id)
        assert sim["status"] == "settled"
        assert sim["match_source"] == "local"
        assert sim["is_win"] == 1
        # pnl = 500 * 19800 // 10000 - 500 = 990 - 500 = 490
        assert sim["pnl"] == 490

        # 7. 执行对账
        reconciler = Reconciler(db, adapter, alert_svc, op_id)
        await reconciler.reconcile(account_id=acc_id, issue=issue)

        # 8. 验证无 unsettled_orders 告警（所有订单都在终态）
        alerts_rows = await (
            await db.execute(
                "SELECT * FROM alerts WHERE operator_id=? AND type='unsettled_orders'",
                (op_id,),
            )
        ).fetchall()
        assert len(alerts_rows) == 0

    @pytest.mark.asyncio
    async def test_real_order_loss_platform(self, db, setup_data):
        """端到端：真实订单输了（WinAmount=0），match_source=platform"""
        op_id = setup_data["operator"]["id"]
        issue = "20240201002"
        balls = [1, 2, 3]
        sum_value = 6  # 小 → DX1 不中奖

        real_order = await _create_order(
            db, setup_data, issue=issue, key_code="DX1",
            amount=2000, odds=19800, simulation=0,
            idempotent_id=f"{issue}-real-loss",
        )

        platform_bets = [
            _make_platform_bet(issue, "DX1", 20.0, 0.0),  # 输了
        ]
        adapter = _make_mock_adapter(
            platform_balance_yuan=980.0,
            platform_bets=platform_bets,
        )

        alert_svc = AlertService(db)
        settler = SettlementProcessor(db, op_id, alert_service=alert_svc)
        await settler.settle(
            issue=issue, balls=balls, sum_value=sum_value,
            platform_type="JND28WEB", adapter=adapter,
        )

        real = await bet_order_get_by_id(db, order_id=real_order["id"], operator_id=op_id)
        assert real["status"] == "settled"
        assert real["match_source"] == "platform"
        assert real["is_win"] == 0
        assert real["pnl"] == -2000  # 0 - 2000



# ══════════════════════════════════════════════
# 11.2 混合模式测试
# ══════════════════════════════════════════════

class TestMixedModeSettlement:
    """同一期号下同时存在真实和模拟订单的结算

    DoD：模拟先结算，真实后结算，各自路径正确
    """

    @pytest.mark.asyncio
    async def test_sim_before_real_execution_order(self, db, setup_data):
        """验证模拟订单先结算，真实订单后结算"""
        op_id = setup_data["operator"]["id"]
        issue = "20240301001"
        balls = [5, 5, 5]
        sum_value = 15  # 大 → DX1 中奖

        # 创建真实订单
        real_order = await _create_order(
            db, setup_data, issue=issue, key_code="DX1",
            amount=1000, odds=19800, simulation=0,
            idempotent_id=f"{issue}-mixed-real",
        )

        # 创建模拟订单
        sim_order = await _create_order(
            db, setup_data, issue=issue, key_code="DX1",
            amount=500, odds=19800, simulation=1,
            idempotent_id=f"{issue}-mixed-sim",
        )

        # 平台数据（仅匹配真实订单）
        platform_bets = [
            _make_platform_bet(issue, "DX1", 10.0, 19.80),
        ]
        adapter = _make_mock_adapter(
            platform_balance_yuan=1019.80,
            platform_bets=platform_bets,
        )

        # 用 side_effect 追踪调用顺序
        settle_call_order = []
        alert_svc = AlertService(db)
        settler = SettlementProcessor(db, op_id, alert_service=alert_svc)

        original_settle_sim = settler._settle_simulated
        original_settle_real = settler._settle_real

        async def tracked_settle_sim(*args, **kwargs):
            settle_call_order.append("simulated")
            return await original_settle_sim(*args, **kwargs)

        async def tracked_settle_real(*args, **kwargs):
            settle_call_order.append("real")
            return await original_settle_real(*args, **kwargs)

        settler._settle_simulated = tracked_settle_sim
        settler._settle_real = tracked_settle_real

        await settler.settle(
            issue=issue, balls=balls, sum_value=sum_value,
            platform_type="JND28WEB", adapter=adapter,
        )

        # 验证执行顺序：先模拟后真实
        assert settle_call_order == ["simulated", "real"]

        # 验证模拟订单结果
        sim = await bet_order_get_by_id(db, order_id=sim_order["id"], operator_id=op_id)
        assert sim["status"] == "settled"
        assert sim["match_source"] == "local"

        # 验证真实订单结果
        real = await bet_order_get_by_id(db, order_id=real_order["id"], operator_id=op_id)
        assert real["status"] == "settled"
        assert real["match_source"] == "platform"

    @pytest.mark.asyncio
    async def test_mixed_mode_different_key_codes(self, db, setup_data):
        """混合模式：不同 key_code 的真实和模拟订单各自正确结算"""
        op_id = setup_data["operator"]["id"]
        issue = "20240301002"
        balls = [5, 5, 5]
        sum_value = 15  # 大+奇

        # 真实订单 DX1（大，中奖）
        real_big = await _create_order(
            db, setup_data, issue=issue, key_code="DX1",
            amount=1000, odds=19800, simulation=0,
            idempotent_id=f"{issue}-real-big",
        )

        # 模拟订单 DX2（小，不中奖）
        sim_small = await _create_order(
            db, setup_data, issue=issue, key_code="DX2",
            amount=500, odds=19800, simulation=1,
            idempotent_id=f"{issue}-sim-small",
        )

        platform_bets = [
            _make_platform_bet(issue, "DX1", 10.0, 19.80),
        ]
        adapter = _make_mock_adapter(
            platform_balance_yuan=1019.80,
            platform_bets=platform_bets,
        )

        alert_svc = AlertService(db)
        settler = SettlementProcessor(db, op_id, alert_service=alert_svc)
        await settler.settle(
            issue=issue, balls=balls, sum_value=sum_value,
            platform_type="JND28WEB", adapter=adapter,
        )

        # 真实订单中奖
        real = await bet_order_get_by_id(db, order_id=real_big["id"], operator_id=op_id)
        assert real["status"] == "settled"
        assert real["match_source"] == "platform"
        assert real["is_win"] == 1

        # 模拟订单不中奖（DX2=小，sum=15 是大）
        sim = await bet_order_get_by_id(db, order_id=sim_small["id"], operator_id=op_id)
        assert sim["status"] == "settled"
        assert sim["match_source"] == "local"
        assert sim["is_win"] == 0
        assert sim["pnl"] == -500

    @pytest.mark.asyncio
    async def test_only_sim_orders_no_adapter_needed(self, db, setup_data):
        """仅模拟订单时，不调用 adapter 的 Topbetlist/QueryResult"""
        op_id = setup_data["operator"]["id"]
        issue = "20240301003"
        balls = [5, 5, 5]
        sum_value = 15

        await _create_order(
            db, setup_data, issue=issue, key_code="DX1",
            amount=1000, odds=19800, simulation=1,
            idempotent_id=f"{issue}-sim-only",
        )

        adapter = _make_mock_adapter()
        alert_svc = AlertService(db)
        settler = SettlementProcessor(db, op_id, alert_service=alert_svc)
        await settler.settle(
            issue=issue, balls=balls, sum_value=sum_value,
            platform_type="JND28WEB", adapter=adapter,
        )

        # adapter 的 query_balance 和 get_bet_history 不应被调用
        adapter.query_balance.assert_not_called()
        adapter.get_bet_history.assert_not_called()



# ══════════════════════════════════════════════
# 11.3 补结算测试
# ══════════════════════════════════════════════

class TestRecoverySettlement:
    """Worker 重启后正确处理未结算订单（含 settle_timeout/settle_failed 恢复）

    DoD：settle_timeout 订单被恢复为 settled
    """

    @pytest.mark.asyncio
    async def test_settle_timeout_recovered_to_settled(self, db, setup_data):
        """settle_timeout 订单通过补结算（is_recovery=True）恢复为 settled"""
        op_id = setup_data["operator"]["id"]
        issue = "20240401001"
        balls = [5, 5, 5]
        sum_value = 15  # DX1 中奖

        # 创建一个 settle_timeout 的真实订单
        timeout_order = await _create_order(
            db, setup_data, issue=issue, key_code="DX1",
            amount=1000, odds=19800, simulation=0,
            status="settle_timeout",
            idempotent_id=f"{issue}-timeout-real",
        )

        # 平台数据现在可用了
        platform_bets = [
            _make_platform_bet(issue, "DX1", 10.0, 19.80),
        ]
        adapter = _make_mock_adapter(
            platform_balance_yuan=1019.80,
            platform_bets=platform_bets,
        )

        alert_svc = AlertService(db)
        settler = SettlementProcessor(db, op_id, alert_service=alert_svc)

        # 补结算模式：is_recovery=True
        await settler.settle(
            issue=issue, balls=balls, sum_value=sum_value,
            platform_type="JND28WEB", adapter=adapter,
            is_recovery=True,
        )

        # 验证：settle_timeout → settling → settled
        order = await bet_order_get_by_id(db, order_id=timeout_order["id"], operator_id=op_id)
        assert order["status"] == "settled"
        assert order["match_source"] == "platform"
        assert order["is_win"] == 1
        assert order["pnl"] == 980

    @pytest.mark.asyncio
    async def test_settle_failed_recovered_to_settled(self, db, setup_data):
        """settle_failed 订单通过补结算恢复为 settled"""
        op_id = setup_data["operator"]["id"]
        issue = "20240401002"
        balls = [1, 2, 3]
        sum_value = 6  # DX1 不中奖

        failed_order = await _create_order(
            db, setup_data, issue=issue, key_code="DX1",
            amount=2000, odds=19800, simulation=0,
            status="settle_failed",
            idempotent_id=f"{issue}-failed-real",
        )

        platform_bets = [
            _make_platform_bet(issue, "DX1", 20.0, 0.0),  # 输了
        ]
        adapter = _make_mock_adapter(
            platform_balance_yuan=980.0,
            platform_bets=platform_bets,
        )

        alert_svc = AlertService(db)
        settler = SettlementProcessor(db, op_id, alert_service=alert_svc)

        await settler.settle(
            issue=issue, balls=balls, sum_value=sum_value,
            platform_type="JND28WEB", adapter=adapter,
            is_recovery=True,
        )

        order = await bet_order_get_by_id(db, order_id=failed_order["id"], operator_id=op_id)
        assert order["status"] == "settled"
        assert order["match_source"] == "platform"
        assert order["is_win"] == 0
        assert order["pnl"] == -2000

    @pytest.mark.asyncio
    async def test_normal_settle_does_not_recover_timeout(self, db, setup_data):
        """正常结算（is_recovery=False）不处理 settle_timeout 订单"""
        op_id = setup_data["operator"]["id"]
        issue = "20240401003"
        balls = [5, 5, 5]
        sum_value = 15

        timeout_order = await _create_order(
            db, setup_data, issue=issue, key_code="DX1",
            amount=1000, odds=19800, simulation=0,
            status="settle_timeout",
            idempotent_id=f"{issue}-timeout-no-recover",
        )

        platform_bets = [
            _make_platform_bet(issue, "DX1", 10.0, 19.80),
        ]
        adapter = _make_mock_adapter(
            platform_balance_yuan=1019.80,
            platform_bets=platform_bets,
        )

        alert_svc = AlertService(db)
        settler = SettlementProcessor(db, op_id, alert_service=alert_svc)

        # 正常模式：不包含 settle_timeout
        await settler.settle(
            issue=issue, balls=balls, sum_value=sum_value,
            platform_type="JND28WEB", adapter=adapter,
            is_recovery=False,
        )

        # settle_timeout 订单不应被处理
        order = await bet_order_get_by_id(db, order_id=timeout_order["id"], operator_id=op_id)
        assert order["status"] == "settle_timeout"

    @pytest.mark.asyncio
    async def test_recovery_mixed_statuses(self, db, setup_data):
        """补结算同时处理 bet_success + settle_timeout + settle_failed 订单"""
        op_id = setup_data["operator"]["id"]
        issue = "20240401004"
        balls = [5, 5, 5]
        sum_value = 15

        # 三种状态的订单
        order_bs = await _create_order(
            db, setup_data, issue=issue, key_code="DX1",
            amount=1000, odds=19800, simulation=0,
            status="bet_success",
            idempotent_id=f"{issue}-bs",
        )
        order_to = await _create_order(
            db, setup_data, issue=issue, key_code="DX1",
            amount=1000, odds=19800, simulation=0,
            status="settle_timeout",
            idempotent_id=f"{issue}-to",
        )
        order_sf = await _create_order(
            db, setup_data, issue=issue, key_code="DX1",
            amount=1000, odds=19800, simulation=0,
            status="settle_failed",
            idempotent_id=f"{issue}-sf",
        )

        # 平台返回 3 条匹配记录（WinAmount 全同，无歧义）
        platform_bets = [
            _make_platform_bet(issue, "DX1", 10.0, 19.80),
            _make_platform_bet(issue, "DX1", 10.0, 19.80),
            _make_platform_bet(issue, "DX1", 10.0, 19.80),
        ]
        adapter = _make_mock_adapter(
            platform_balance_yuan=1059.40,
            platform_bets=platform_bets,
        )

        alert_svc = AlertService(db)
        settler = SettlementProcessor(db, op_id, alert_service=alert_svc)

        await settler.settle(
            issue=issue, balls=balls, sum_value=sum_value,
            platform_type="JND28WEB", adapter=adapter,
            is_recovery=True,
        )

        # 所有订单都应被恢复为 settled
        for oid in [order_bs["id"], order_to["id"], order_sf["id"]]:
            order = await bet_order_get_by_id(db, order_id=oid, operator_id=op_id)
            assert order["status"] == "settled", f"order {oid} status={order['status']}"
            assert order["match_source"] == "platform"

    @pytest.mark.asyncio
    async def test_worker_recover_unsettled_orders(self, db, setup_data):
        """Worker._recover_unsettled_orders 完整流程：
        扫描未结算订单 → 获取历史开奖 → settle(is_recovery=True)
        """
        op_id = setup_data["operator"]["id"]
        acc_id = setup_data["account"]["id"]
        issue = "20240401005"

        # 创建 settle_timeout 订单
        timeout_order = await _create_order(
            db, setup_data, issue=issue, key_code="DX1",
            amount=1000, odds=19800, simulation=0,
            status="settle_timeout",
            idempotent_id=f"{issue}-worker-recover",
        )

        # 平台历史开奖结果
        lottery_results = [
            {"Installments": issue, "OpenResult": "5,5,5"},
        ]
        # 平台投注记录
        platform_bets = [
            _make_platform_bet(issue, "DX1", 10.0, 19.80),
        ]
        adapter = _make_mock_adapter(
            platform_balance_yuan=1019.80,
            platform_bets=platform_bets,
            lottery_results=lottery_results,
        )

        alert_svc = AlertService(db)
        settler = SettlementProcessor(db, op_id, alert_service=alert_svc)
        reconciler = Reconciler(db, adapter, alert_svc, op_id)

        worker = AccountWorker(
            operator_id=op_id, account_id=acc_id, db=db,
            adapter=adapter, session=AsyncMock(),
            poller=MagicMock(), executor=AsyncMock(),
            settler=settler, reconciler=reconciler,
            risk=AsyncMock(), alert_service=alert_svc,
            strategies={}, platform_type="JND28WEB",
        )

        await worker._recover_unsettled_orders()

        # 验证 settle_timeout 订单被恢复为 settled
        order = await bet_order_get_by_id(db, order_id=timeout_order["id"], operator_id=op_id)
        assert order["status"] == "settled"
        assert order["match_source"] == "platform"



# ══════════════════════════════════════════════
# 11.4 锁竞态故障注入测试
# ══════════════════════════════════════════════

class TestLockRaceCondition:
    """A 持锁卡顿 > TTL → B 抢锁 → A 恢复后不产生副作用

    DoD：A 的 running=False，B 正常运行
    """

    @pytest.mark.asyncio
    async def test_lock_race_a_stalls_b_acquires(self, db, setup_data):
        """A 持锁 → A 卡顿超过 TTL → B 抢锁成功 → A 续约失败停止"""
        op_id = setup_data["operator"]["id"]
        acc_id = setup_data["account"]["id"]

        alert_svc = AlertService(db)

        # Worker A 抢锁
        worker_a = AccountWorker(
            operator_id=op_id, account_id=acc_id, db=db,
            adapter=AsyncMock(), session=AsyncMock(),
            poller=MagicMock(), executor=AsyncMock(),
            settler=AsyncMock(), reconciler=AsyncMock(),
            risk=AsyncMock(), alert_service=alert_svc,
            strategies={}, platform_type="JND28WEB",
        )

        acquired_a = await worker_a._acquire_lock()
        assert acquired_a is True
        assert worker_a._lock_token is not None
        token_a = worker_a._lock_token

        # 模拟 A 卡顿超过 TTL（5 分钟）：直接修改 DB 中的 lock_ts 为 6 分钟前
        await db.execute(
            "UPDATE gambling_accounts "
            "SET worker_lock_ts=datetime('now', '-6 minutes') "
            "WHERE id=?",
            (acc_id,),
        )
        await db.commit()

        # Worker B 抢锁（A 的锁已超时）
        worker_b = AccountWorker(
            operator_id=op_id, account_id=acc_id, db=db,
            adapter=AsyncMock(), session=AsyncMock(),
            poller=MagicMock(), executor=AsyncMock(),
            settler=AsyncMock(), reconciler=AsyncMock(),
            risk=AsyncMock(), alert_service=alert_svc,
            strategies={}, platform_type="JND28WEB",
        )

        acquired_b = await worker_b._acquire_lock()
        assert acquired_b is True
        assert worker_b._lock_token is not None
        assert worker_b._lock_token != token_a

        # A 尝试续约 → 失败（token 已被 B 覆盖）
        worker_a.running = True
        renew_result = await worker_a._renew_lock()
        assert renew_result is False
        assert worker_a.running is False  # A 检测到失锁，自动停止

        # B 续约 → 成功
        worker_b.running = True
        renew_b = await worker_b._renew_lock()
        assert renew_b is True
        assert worker_b.running is True

        # 验证 worker_lock_lost 告警已发送
        alerts = await (
            await db.execute(
                "SELECT * FROM alerts WHERE operator_id=? AND type='worker_lock_lost'",
                (op_id,),
            )
        ).fetchall()
        assert len(alerts) >= 1

    @pytest.mark.asyncio
    async def test_a_cannot_release_b_lock(self, db, setup_data):
        """A 失锁后尝试释放锁，不影响 B 的锁"""
        op_id = setup_data["operator"]["id"]
        acc_id = setup_data["account"]["id"]

        alert_svc = AlertService(db)

        # A 抢锁
        worker_a = AccountWorker(
            operator_id=op_id, account_id=acc_id, db=db,
            adapter=AsyncMock(), session=AsyncMock(),
            poller=MagicMock(), executor=AsyncMock(),
            settler=AsyncMock(), reconciler=AsyncMock(),
            risk=AsyncMock(), alert_service=alert_svc,
            strategies={}, platform_type="JND28WEB",
        )
        await worker_a._acquire_lock()
        token_a = worker_a._lock_token

        # 模拟超时
        await db.execute(
            "UPDATE gambling_accounts "
            "SET worker_lock_ts=datetime('now', '-6 minutes') "
            "WHERE id=?",
            (acc_id,),
        )
        await db.commit()

        # B 抢锁
        worker_b = AccountWorker(
            operator_id=op_id, account_id=acc_id, db=db,
            adapter=AsyncMock(), session=AsyncMock(),
            poller=MagicMock(), executor=AsyncMock(),
            settler=AsyncMock(), reconciler=AsyncMock(),
            risk=AsyncMock(), alert_service=alert_svc,
            strategies={}, platform_type="JND28WEB",
        )
        await worker_b._acquire_lock()
        token_b = worker_b._lock_token

        # A 尝试释放锁（WHERE worker_lock_token=token_a，不匹配 B 的 token）
        await worker_a._release_lock()

        # B 的锁不受影响
        row = await (
            await db.execute(
                "SELECT worker_lock_token FROM gambling_accounts WHERE id=?",
                (acc_id,),
            )
        ).fetchone()
        assert row["worker_lock_token"] == token_b

    @pytest.mark.asyncio
    async def test_b_normal_operation_after_a_stops(self, db, setup_data):
        """B 抢锁后正常运行，A 停止后不产生任何副作用"""
        op_id = setup_data["operator"]["id"]
        acc_id = setup_data["account"]["id"]

        alert_svc = AlertService(db)

        # A 抢锁
        worker_a = AccountWorker(
            operator_id=op_id, account_id=acc_id, db=db,
            adapter=AsyncMock(), session=AsyncMock(),
            poller=MagicMock(), executor=AsyncMock(),
            settler=AsyncMock(), reconciler=AsyncMock(),
            risk=AsyncMock(), alert_service=alert_svc,
            strategies={}, platform_type="JND28WEB",
        )
        await worker_a._acquire_lock()

        # 模拟超时
        await db.execute(
            "UPDATE gambling_accounts "
            "SET worker_lock_ts=datetime('now', '-6 minutes') "
            "WHERE id=?",
            (acc_id,),
        )
        await db.commit()

        # B 抢锁
        worker_b = AccountWorker(
            operator_id=op_id, account_id=acc_id, db=db,
            adapter=AsyncMock(), session=AsyncMock(),
            poller=MagicMock(), executor=AsyncMock(),
            settler=AsyncMock(), reconciler=AsyncMock(),
            risk=AsyncMock(), alert_service=alert_svc,
            strategies={}, platform_type="JND28WEB",
        )
        await worker_b._acquire_lock()
        worker_b.running = True

        # A 续约失败 → running=False
        worker_a.running = True
        await worker_a._renew_lock()
        assert worker_a.running is False

        # B 多次续约都成功
        for _ in range(3):
            assert await worker_b._renew_lock() is True
        assert worker_b.running is True

        # B 正常释放锁
        await worker_b._release_lock()
        row = await (
            await db.execute(
                "SELECT worker_lock_token FROM gambling_accounts WHERE id=?",
                (acc_id,),
            )
        ).fetchone()
        assert row["worker_lock_token"] is None

    @pytest.mark.asyncio
    async def test_active_lock_blocks_new_worker(self, db, setup_data):
        """活跃锁阻止新 Worker 启动"""
        op_id = setup_data["operator"]["id"]
        acc_id = setup_data["account"]["id"]

        alert_svc = AlertService(db)

        # A 抢锁
        worker_a = AccountWorker(
            operator_id=op_id, account_id=acc_id, db=db,
            adapter=AsyncMock(), session=AsyncMock(),
            poller=MagicMock(), executor=AsyncMock(),
            settler=AsyncMock(), reconciler=AsyncMock(),
            risk=AsyncMock(), alert_service=alert_svc,
            strategies={}, platform_type="JND28WEB",
        )
        await worker_a._acquire_lock()

        # B 尝试抢锁（A 的锁仍活跃）→ 失败
        worker_b = AccountWorker(
            operator_id=op_id, account_id=acc_id, db=db,
            adapter=AsyncMock(), session=AsyncMock(),
            poller=MagicMock(), executor=AsyncMock(),
            settler=AsyncMock(), reconciler=AsyncMock(),
            risk=AsyncMock(), alert_service=alert_svc,
            strategies={}, platform_type="JND28WEB",
        )
        acquired_b = await worker_b._acquire_lock()
        assert acquired_b is False
        assert worker_b._lock_token is None

        # 释放 A 的锁
        await worker_a._release_lock()
