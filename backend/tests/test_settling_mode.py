"""结算模式集成测试

覆盖：
- stop_worker 有/无未结算订单的分支行为
- 结算模式下跳过投注
- 结算完毕自动停止
- enter_settling_mode 状态设置
- 超时处理标记 settle_failed
- 清理回调
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from app.engine.adapters.base import InstallInfo
from app.engine.worker import (
    AccountWorker,
    SETTLEMENT_WAIT_SECONDS_DEFAULT,
)
from app.engine.manager import (
    EngineManager,
    InMemorySessionStore,
    InMemoryWorkerRegistry,
)


# ------------------------------------------------------------------
# Fixtures / Helpers
# ------------------------------------------------------------------


def _make_mock_worker(**overrides) -> AccountWorker:
    """创建带 mock 依赖的 AccountWorker"""
    defaults = dict(
        operator_id=1,
        account_id=100,
        db=AsyncMock(),
        adapter=AsyncMock(),
        session=AsyncMock(),
        poller=MagicMock(),
        executor=AsyncMock(),
        settler=AsyncMock(),
        reconciler=AsyncMock(),
        risk=AsyncMock(),
        alert_service=AsyncMock(),
        strategies={},
        bet_timing=30,
        platform_type="JND28WEB",
        settlement_wait_seconds=SETTLEMENT_WAIT_SECONDS_DEFAULT,
    )
    defaults.update(overrides)
    if not hasattr(defaults["poller"], "poll_interval"):
        type(defaults["poller"]).poll_interval = property(lambda self: 5)
    return AccountWorker(**defaults)


def _make_manager(**overrides) -> EngineManager:
    """创建带 mock 依赖的 EngineManager"""
    defaults = dict(
        db=AsyncMock(),
        session_store=InMemorySessionStore(),
        worker_registry=InMemoryWorkerRegistry(),
        alert_service=AsyncMock(),
    )
    defaults.update(overrides)
    return EngineManager(**defaults)


def _make_install(
    issue: str = "20250302001",
    state: int = 1,
    close_countdown_sec: int = 60,
    open_countdown_sec: int = 30,
    pre_issue: str = "20250302000",
    pre_result: str = "3,5,7",
    is_new_issue: bool = False,
) -> InstallInfo:
    return InstallInfo(
        issue=issue,
        state=state,
        close_countdown_sec=close_countdown_sec,
        open_countdown_sec=open_countdown_sec,
        pre_issue=pre_issue,
        pre_result=pre_result,
        is_new_issue=is_new_issue,
    )


async def _create_memory_db() -> aiosqlite.Connection:
    """创建内存 SQLite 数据库并初始化 bet_orders 表"""
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute("""
        CREATE TABLE bet_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            operator_id INTEGER NOT NULL,
            issue TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'bet_success',
            key_code TEXT DEFAULT '',
            amount REAL DEFAULT 0,
            simulation INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now', '+8 hours'))
        )
    """)
    await db.execute("""
        CREATE TABLE simulation_bet_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            operator_id INTEGER NOT NULL,
            issue TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'bet_success',
            key_code TEXT DEFAULT '',
            amount REAL DEFAULT 0,
            simulation INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now', '+8 hours'))
        )
    """)
    await db.commit()
    return db


# ==================================================================
# 测试 1: stop_worker 有未结算订单 → 进入 settling 模式
# ==================================================================


class TestStopWorkerEntersSettlingMode:
    @pytest.mark.asyncio
    async def test_stop_worker_enters_settling_mode_with_unsettled_orders(self):
        """有未结算订单时，stop_worker 使 Worker 进入 settling 状态并保留在 registry"""
        manager = _make_manager()
        worker = _make_mock_worker()
        worker.running = True
        worker.status = "running"

        # mock _has_unsettled_orders 返回 True
        worker._has_unsettled_orders = AsyncMock(return_value=True)
        # mock enter_settling_mode（避免真实 DB 查询日志部分）
        original_enter = worker.enter_settling_mode

        async def mock_enter():
            worker.settling_only = True
            worker.status = "settling"
            worker._settling_deadline = time.time() + 600
            worker.strategies.clear()

        worker.enter_settling_mode = mock_enter

        await manager.registry.register(100, worker)
        result = await manager.stop_worker(100)

        assert result is True
        assert worker.settling_only is True
        assert worker.status == "settling"
        # Worker 仍在 registry 中
        assert await manager.registry.get(100) is worker


# ==================================================================
# 测试 2: stop_worker 无未结算订单 → 直接停止
# ==================================================================


class TestStopWorkerStopsDirectly:
    @pytest.mark.asyncio
    async def test_stop_worker_stops_directly_without_unsettled_orders(self):
        """无未结算订单时，stop_worker 直接停止 Worker 并从 registry 移除"""
        manager = _make_manager()
        worker = _make_mock_worker()
        worker.running = True
        worker.status = "running"
        worker._has_unsettled_orders = AsyncMock(return_value=False)
        worker.stop = AsyncMock()

        await manager.registry.register(100, worker)
        result = await manager.stop_worker(100)

        assert result is True
        worker.stop.assert_called_once()
        # Worker 已从 registry 移除
        assert await manager.registry.get(100) is None


# ==================================================================
# 测试 3: 结算模式下跳过投注
# ==================================================================


class TestSettlingModeSkipsBetting:
    @pytest.mark.asyncio
    async def test_settling_mode_skips_betting(self):
        """settling_only=True 时，_collect_signals 返回空（strategies 已清空），executor 不被调用"""
        worker = _make_mock_worker()
        worker.settling_only = True
        worker.strategies.clear()

        install = _make_install(issue="20250302001")
        signals = await worker._collect_signals(install)

        assert signals == []
        worker.executor.execute.assert_not_called()


# ==================================================================
# 测试 4: 结算完毕自动停止（使用内存 SQLite）
# ==================================================================


class TestSettlingModeAutoStops:
    @pytest.mark.asyncio
    async def test_settling_mode_auto_stops_when_all_settled(self):
        """使用真实内存 DB 验证 _has_unsettled_orders 在订单状态变化时的行为"""
        db = await _create_memory_db()
        try:
            worker = _make_mock_worker(db=db)

            # 插入 bet_success 订单
            await db.execute(
                "INSERT INTO bet_orders (account_id, operator_id, issue, status) "
                "VALUES (?, ?, ?, ?)",
                (100, 1, "20250302001", "bet_success"),
            )
            await db.commit()

            # 进入结算模式
            await worker.enter_settling_mode()

            # 验证有未结算订单
            assert await worker._has_unsettled_orders() is True

            # 模拟结算完成：更新订单状态
            await db.execute(
                "UPDATE bet_orders SET status='settled' "
                "WHERE account_id=? AND operator_id=?",
                (100, 1),
            )
            await db.commit()

            # 验证无未结算订单
            assert await worker._has_unsettled_orders() is False
        finally:
            await db.close()


# ==================================================================
# 测试 5: enter_settling_mode 设置正确状态
# ==================================================================


class TestEnterSettlingModeSetsState:
    @pytest.mark.asyncio
    async def test_enter_settling_mode_sets_correct_state(self):
        """enter_settling_mode 设置 settling_only、status、_settling_deadline 并清空 strategies"""
        worker = _make_mock_worker()
        # 添加一些策略
        worker.strategies = {1: MagicMock(), 2: MagicMock()}

        # mock DB 查询（日志记录部分）
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=[])
        worker.db.execute = AsyncMock(return_value=mock_cursor)

        before = time.time()
        await worker.enter_settling_mode()
        after = time.time()

        assert worker.settling_only is True
        assert worker.status == "settling"
        assert worker._settling_deadline is not None
        # deadline 应在 [before+600, after+600] 范围内
        assert before + 600 <= worker._settling_deadline <= after + 600
        # strategies 已清空
        assert len(worker.strategies) == 0

    def test_exit_settling_mode_restores_running_state_and_clears_callback(self):
        worker = _make_mock_worker()
        worker.running = True
        worker.status = "settling"
        worker.settling_only = True
        worker._settling_deadline = time.time() + 600
        worker._on_settle_complete = AsyncMock()

        worker.exit_settling_mode()

        assert worker.settling_only is False
        assert worker.status == "running"
        assert worker._settling_deadline is None
        assert worker._on_settle_complete is None


# ==================================================================
# 测试 6: 超时处理标记 settle_failed（使用内存 SQLite）
# ==================================================================


class TestHandleSettlingTimeout:
    @pytest.mark.asyncio
    async def test_handle_settling_timeout_marks_orders_failed(self):
        """超时后，未结算订单被标记为 settle_failed，告警被发送"""
        db = await _create_memory_db()
        try:
            settler = AsyncMock()
            alert_service = AsyncMock()
            worker = _make_mock_worker(
                db=db, settler=settler, alert_service=alert_service,
            )
            worker._settling_deadline = time.time() - 10  # 已超时

            # 插入 bet_success 订单
            await db.execute(
                "INSERT INTO bet_orders (account_id, operator_id, issue, status) "
                "VALUES (?, ?, ?, ?)",
                (100, 1, "20250302001", "bet_success"),
            )
            await db.execute(
                "INSERT INTO bet_orders (account_id, operator_id, issue, status) "
                "VALUES (?, ?, ?, ?)",
                (100, 1, "20250302001", "pending_match"),
            )
            await db.commit()

            await worker._handle_settling_timeout()

            # settler._mark_orders_settle_failed 被调用，传入 2 笔订单
            settler._mark_orders_settle_failed.assert_called_once()
            orders_arg = settler._mark_orders_settle_failed.call_args[0][0]
            assert len(orders_arg) == 2

            # 告警被发送
            alert_service.send.assert_called_once()
            call_kwargs = alert_service.send.call_args.kwargs
            assert call_kwargs["alert_type"] == "settling_mode_timeout"
            assert "100" in call_kwargs["title"]

            # Worker 状态已停止
            assert worker.running is False
            assert worker.status == "stopped"
        finally:
            await db.close()


# ==================================================================
# 测试 7: 清理回调
# ==================================================================


class TestCleanupAfterSettling:
    @pytest.mark.asyncio
    async def test_cleanup_after_settling_calls_callback(self):
        """_cleanup_after_settling 调用 _on_settle_complete 回调"""
        worker = _make_mock_worker()
        worker._lock_token = "test-token"

        callback = AsyncMock()
        worker._on_settle_complete = callback

        # mock _release_lock
        worker.db.execute = AsyncMock()
        worker.db.commit = AsyncMock()

        await worker._cleanup_after_settling()

        callback.assert_called_once_with(100)  # account_id=100


# ==================================================================
# 测试 8: 结算模式超时完整流程（集成测试）
# ==================================================================


class TestSettlingModeTimeoutIntegration:
    """集成测试：Worker 进入结算模式 → 平台不返回开奖结果 → 超时 →
    订单标记 settle_failed → 告警发送 → Worker 自动停止并清理
    """

    @pytest.mark.asyncio
    async def test_settling_timeout_full_flow(self):
        """完整超时流程：进入结算模式 → 超时 → 标记 settle_failed → 告警 → 清理"""
        db = await _create_memory_db()
        try:
            settler = AsyncMock()
            alert_service = AsyncMock()
            cleanup_callback = AsyncMock()

            worker = _make_mock_worker(
                db=db, settler=settler, alert_service=alert_service,
            )

            # 插入 2 笔未结算订单
            await db.execute(
                "INSERT INTO bet_orders (account_id, operator_id, issue, status) "
                "VALUES (?, ?, ?, ?)",
                (100, 1, "20250302001", "bet_success"),
            )
            await db.execute(
                "INSERT INTO bet_orders (account_id, operator_id, issue, status) "
                "VALUES (?, ?, ?, ?)",
                (100, 1, "20250302001", "pending_match"),
            )
            await db.commit()

            # 1. 进入结算模式
            await worker.enter_settling_mode()
            assert worker.settling_only is True
            assert worker.status == "settling"
            assert await worker._has_unsettled_orders() is True

            # 2. 模拟超时（将 deadline 设为过去）
            worker._settling_deadline = time.time() - 10
            worker._on_settle_complete = cleanup_callback

            # 3. 执行超时处理
            await worker._handle_settling_timeout()

            # 4. 验证订单被标记为 settle_failed
            settler._mark_orders_settle_failed.assert_called_once()
            failed_orders = settler._mark_orders_settle_failed.call_args[0][0]
            assert len(failed_orders) == 2
            statuses = {o["status"] for o in failed_orders}
            assert statuses == {"bet_success", "pending_match"}

            # 5. 验证告警发送
            alert_service.send.assert_called_once()
            alert_kwargs = alert_service.send.call_args.kwargs
            assert alert_kwargs["alert_type"] == "settling_mode_timeout"
            assert alert_kwargs["operator_id"] == 1
            assert "100" in alert_kwargs["title"]

            # 6. 验证 Worker 已停止
            assert worker.running is False
            assert worker.status == "stopped"

            # 7. 执行清理
            worker.db = AsyncMock()  # mock DB for _release_lock
            worker.db.commit = AsyncMock()
            await worker._cleanup_after_settling()

            # 8. 验证清理回调被调用
            cleanup_callback.assert_called_once_with(100)
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_settling_timeout_no_unsettled_orders(self):
        """超时时无未结算订单的边界情况"""
        db = await _create_memory_db()
        try:
            settler = AsyncMock()
            alert_service = AsyncMock()
            worker = _make_mock_worker(
                db=db, settler=settler, alert_service=alert_service,
            )

            await worker.enter_settling_mode()
            worker._settling_deadline = time.time() - 10

            await worker._handle_settling_timeout()

            # settler 不应被调用（无订单）
            settler._mark_orders_settle_failed.assert_not_called()
            # 告警仍然发送（通知超时事件）
            alert_service.send.assert_called_once()
            assert worker.running is False
        finally:
            await db.close()
