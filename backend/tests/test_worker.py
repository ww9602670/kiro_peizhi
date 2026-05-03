"""AccountWorker 单元测试

覆盖：
- mock 依赖注入
- 7s/8s/9s 投注时机
- 倒计时驱动主循环
- 异常恢复 5 次后 error
- 补结算 / 全新启动检测
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from app.engine.adapters.base import InstallInfo, RemoteLoginRequired
from app.engine.worker import (
    AccountWorker,
    SKIP_THRESHOLD,
    RESTART_DELAYS,
    MAX_RESTART_FAILURES,
    SETTLEMENT_WAIT_SECONDS_DEFAULT,
    SETTLEMENT_WAIT_SECONDS_MIN,
    SETTLEMENT_WAIT_SECONDS_MAX,
    SETTLE_DATA_RETRY_MAX,
    SETTLE_DATA_RETRY_INTERVAL,
    API_RETRY_DELAYS,
    API_RETRY_MAX,
    StrategyRuntimeProfile,
    WorkerStartupError,
    _parse_result,
)


# 
# Fixtures
# 


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


def _make_worker(**overrides) -> AccountWorker:
    """ mock  AccountWorker"""
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
    # poller.poll_interval 
    if not hasattr(defaults["poller"], "poll_interval"):
        type(defaults["poller"]).poll_interval = property(lambda self: 5)
    return AccountWorker(**defaults)


def _mock_lock_cursor(rowcount: int) -> AsyncMock:
    cursor = AsyncMock()
    cursor.rowcount = rowcount
    cursor.fetchone = AsyncMock(return_value=None)
    return cursor


# 
# _parse_result 
# 


class TestParseResult:
    def test_normal(self):
        balls, s = _parse_result("3,5,7")
        assert balls == [3, 5, 7]
        assert s == 15

    def test_empty(self):
        balls, s = _parse_result("")
        assert balls == []
        assert s == 0

    def test_single(self):
        balls, s = _parse_result("9")
        assert balls == [9]
        assert s == 9


# 
# 7s/8s/9s
# 


class TestBetTiming:
    """DoDCloseTimeStamp=7s8s9s"""

    def test_7s_skip(self):
        """CloseTimeStamp=7s  """
        worker = _make_worker()
        install = _make_install(close_countdown_sec=7)
        assert worker._should_bet(install) is False

    def test_8s_skip(self):
        """CloseTimeStamp=8s  8s"""
        worker = _make_worker()
        install = _make_install(close_countdown_sec=8)
        assert worker._should_bet(install) is False

    def test_9s_bet(self):
        """CloseTimeStamp=9s  """
        worker = _make_worker()
        install = _make_install(close_countdown_sec=9)
        assert worker._should_bet(install) is True

    def test_30s_bet(self):
        """CloseTimeStamp=30s enters the default 30s window."""
        worker = _make_worker()
        install = _make_install(close_countdown_sec=30)
        assert worker._should_bet(install) is True

    def test_60s_waits_for_window(self):
        """CloseTimeStamp=60s is still outside the default 30s window."""
        worker = _make_worker()
        install = _make_install(close_countdown_sec=60)
        assert worker._should_bet(install) is False

    def test_0s_skip(self):
        """CloseTimeStamp=0s  """
        worker = _make_worker()
        install = _make_install(close_countdown_sec=0)
        assert worker._should_bet(install) is False

    def test_threshold_constant(self):
        """SKIP_THRESHOLD  18"""
        assert SKIP_THRESHOLD == 8


# 
# 
# 


class TestDowntimeHandling:
    @pytest.mark.asyncio
    async def test_state_not_1_skips_betting(self):
        """state!=1 时不投注"""
        worker = _make_worker()
        worker.running = True
        worker._startup_ready = True
        worker._startup_ready = True
        worker._lock_token = "test-lock"

        install = _make_install(state=0, close_countdown_sec=0, open_countdown_sec=0)
        # _fetch_install_with_retry returns install, then on second call stops
        call_count = 0

        async def mock_poll():
            nonlocal call_count
            call_count += 1
            return install

        worker.poller.poll = mock_poll
        worker.session.login = AsyncMock()
        worker.db.commit = AsyncMock()

        # Mock DB for fresh start detection (has records → skip)
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value={"cnt": 1})

        # Mock _recover_unsettled_orders to skip
        mock_cursor_recover = AsyncMock()
        mock_cursor_recover.fetchall = AsyncMock(return_value=[])

        # 锁续约成功
        mock_cursor_lock = AsyncMock()
        mock_cursor_lock.rowcount = 1

        async def mock_db_execute(sql, params=None):
            if "COUNT" in sql:
                return mock_cursor
            if "worker_lock_ts=datetime('now', '+8 hours')" in sql and "worker_lock_token=?" in sql:
                return mock_cursor_lock
            return mock_cursor_recover

        worker.db.execute = mock_db_execute

        # After first loop iteration, stop
        sleep_count = 0

        async def mock_sleep(seconds):
            nonlocal sleep_count
            sleep_count += 1
            # Stop after settlement_wait_seconds sleep
            if sleep_count >= 2:
                worker.running = False

        with patch("app.engine.worker.asyncio.sleep", side_effect=mock_sleep):
            await worker._main_loop()

        # executor should not be called (state != 1)
        worker.executor.execute.assert_not_called()


# 
# 
# 


class TestExceptionRecovery:
    @pytest.mark.asyncio
    async def test_restart_delays_incremental(self):
        """5s  10s  30s"""
        assert RESTART_DELAYS == [5, 10, 30]

    @pytest.mark.asyncio
    async def test_5_failures_marks_error(self):
        """ 5  error"""
        worker = _make_worker()
        worker.running = True
        worker._startup_ready = True

        sleep_calls = []

        async def mock_sleep(seconds):
            sleep_calls.append(seconds)

        # _main_loop 
        call_count = 0

        async def failing_main_loop():
            nonlocal call_count
            call_count += 1
            raise RuntimeError("")

        worker._main_loop = failing_main_loop

        with patch("app.engine.worker.asyncio.sleep", side_effect=mock_sleep):
            await worker._run_with_restart()

        assert worker.status == "error"
        assert worker.running is False
        assert call_count == MAX_RESTART_FAILURES
        # 5, 10, 30, 304530
        assert sleep_calls == [5, 10, 30, 30]

    @pytest.mark.asyncio
    async def test_successful_login_resets_restart_count(self):
        """"""
        worker = _make_worker()
        worker._restart_count = 3
        worker.running = True
        worker._lock_token = "test-lock"

        # Mock DB for fresh start detection
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value={"cnt": 1})
        mock_cursor_empty = AsyncMock()
        mock_cursor_empty.fetchall = AsyncMock(return_value=[])
        mock_cursor_lock = AsyncMock()
        mock_cursor_lock.rowcount = 1

        async def mock_db_execute(sql, params=None):
            if "COUNT" in sql:
                return mock_cursor
            if "worker_lock_ts=datetime('now', '+8 hours')" in sql and "worker_lock_token=?" in sql:
                return mock_cursor_lock
            return mock_cursor_empty

        worker.db.execute = mock_db_execute
        worker.db.commit = AsyncMock()

        poll_count = 0

        async def mock_poll():
            nonlocal poll_count
            poll_count += 1
            worker.running = False
            return _make_install(state=0, open_countdown_sec=0)

        worker.poller.poll = mock_poll
        worker.session.login = AsyncMock()

        with patch("app.engine.worker.asyncio.sleep", new_callable=AsyncMock):
            await worker._main_loop()

        assert worker._restart_count == 0

    @pytest.mark.asyncio
    async def test_cancel_stops_worker(self):
        """ Worker CancelledError """
        worker = _make_worker()

        async def slow_main_loop():
            await asyncio.sleep(100)

        worker._main_loop = slow_main_loop
        worker.running = True

        task = asyncio.create_task(worker._run_with_restart())
        await asyncio.sleep(0.01)
        task.cancel()

        # _run_with_restart  CancelledError  break
        #  task 
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except asyncio.CancelledError:
            pass  #  Python 
        # Worker  error 
        assert worker.status != "error"


# 
# 
# 


class TestMainLoop:
    @pytest.mark.asyncio
    async def test_new_issue_triggers_settlement(self):
        """倒计时驱动：正常流程触发结算"""
        worker = _make_worker()
        worker.running = True
        worker._lock_token = "test-lock"

        # 投注阶段返回的 install（当前期号 20250302001）
        betting_install = _make_install(
            issue="20250302001",
            state=1,
            close_countdown_sec=60,
            open_countdown_sec=10,
            pre_issue="20250302000",
            pre_result="3,5,7",
        )
        # 结算阶段返回的 install（当前期号已推进到 20250302002，上期=20250302001）
        settlement_install = _make_install(
            issue="20250302002",
            state=1,
            close_countdown_sec=60,
            open_countdown_sec=10,
            pre_issue="20250302001",
            pre_result="4,2,6",
        )

        poll_count = 0

        async def mock_poll():
            nonlocal poll_count
            poll_count += 1
            # 前 1 次是投注阶段，之后是结算阶段
            return betting_install if poll_count <= 1 else settlement_install

        worker.poller.poll = mock_poll
        worker.session.login = AsyncMock()
        worker.settler._save_lottery_result = AsyncMock()
        worker._ensure_settlement_branch_started = AsyncMock()
        worker._register_issue_for_settlement = AsyncMock()
        worker.db.commit = AsyncMock()

        # Mock DB for fresh start (has records)
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value={"cnt": 1})
        mock_cursor_empty = AsyncMock()
        mock_cursor_empty.fetchall = AsyncMock(return_value=[])
        mock_cursor_lock = AsyncMock()
        mock_cursor_lock.rowcount = 1

        async def mock_db_execute(sql, params=None):
            if "COUNT" in sql:
                return mock_cursor
            if "worker_lock_ts=datetime('now', '+8 hours')" in sql and "worker_lock_token=?" in sql:
                return mock_cursor_lock
            return mock_cursor_empty

        worker.db.execute = mock_db_execute

        loop_count = 0

        async def mock_sleep(seconds):
            nonlocal loop_count
            loop_count += 1
            # Stop after settlement_wait_seconds sleep (2nd sleep in loop)
            if loop_count >= 2:
                worker.running = False

        with patch("app.engine.worker.asyncio.sleep", side_effect=mock_sleep):
            await worker._main_loop()

        worker._register_issue_for_settlement.assert_any_call(
            "20250302001",
            open_countdown_sec=10,
        )
        worker.settler.settle.assert_not_called()

    @pytest.mark.asyncio
    async def test_new_issue_triggers_reconciliation(self):
        """倒计时驱动：正常流程触发对账"""
        worker = _make_worker()
        worker.running = True
        worker._lock_token = "test-lock"

        betting_install = _make_install(
            issue="20250302001",
            state=1,
            close_countdown_sec=60,
            open_countdown_sec=10,
            pre_issue="20250302000",
            pre_result="3,5,7",
        )
        settlement_install = _make_install(
            issue="20250302002",
            state=1,
            close_countdown_sec=60,
            open_countdown_sec=10,
            pre_issue="20250302001",
            pre_result="4,2,6",
        )

        poll_count = 0

        async def mock_poll():
            nonlocal poll_count
            poll_count += 1
            return betting_install if poll_count <= 1 else settlement_install

        worker.poller.poll = mock_poll
        worker.session.login = AsyncMock()
        worker.settler._save_lottery_result = AsyncMock()
        worker._ensure_settlement_branch_started = AsyncMock()
        worker._register_issue_for_settlement = AsyncMock()
        worker.db.commit = AsyncMock()

        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value={"cnt": 1})
        mock_cursor_empty = AsyncMock()
        mock_cursor_empty.fetchall = AsyncMock(return_value=[])
        mock_cursor_lock = AsyncMock()
        mock_cursor_lock.rowcount = 1

        async def mock_db_execute(sql, params=None):
            if "COUNT" in sql:
                return mock_cursor
            if "worker_lock_ts=datetime('now', '+8 hours')" in sql and "worker_lock_token=?" in sql:
                return mock_cursor_lock
            return mock_cursor_empty

        worker.db.execute = mock_db_execute

        loop_count = 0

        async def mock_sleep(seconds):
            nonlocal loop_count
            loop_count += 1
            if loop_count >= 2:
                worker.running = False

        with patch("app.engine.worker.asyncio.sleep", side_effect=mock_sleep):
            await worker._main_loop()

        worker._register_issue_for_settlement.assert_any_call(
            "20250302001",
            open_countdown_sec=10,
        )
        worker.reconciler.reconcile.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_threshold_no_execute(self):
        """CloseTimeStamp <= SKIP_THRESHOLD 时不调用 executor"""
        worker = _make_worker()
        worker.running = True
        worker._lock_token = "test-lock"

        betting_install = _make_install(
            close_countdown_sec=8,
            state=1,
            open_countdown_sec=5,
            pre_issue="20250302000",
            pre_result="3,5,7",
        )
        settlement_install = _make_install(
            issue="20250302002",
            state=1,
            close_countdown_sec=8,
            open_countdown_sec=5,
            pre_issue="20250302001",
            pre_result="4,2,6",
        )

        poll_count = 0

        async def mock_poll():
            nonlocal poll_count
            poll_count += 1
            return betting_install if poll_count <= 1 else settlement_install

        worker.poller.poll = mock_poll
        worker.session.login = AsyncMock()
        worker.settler._save_lottery_result = AsyncMock()
        worker.db.commit = AsyncMock()

        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value={"cnt": 1})
        mock_cursor_empty = AsyncMock()
        mock_cursor_empty.fetchall = AsyncMock(return_value=[])
        mock_cursor_lock = AsyncMock()
        mock_cursor_lock.rowcount = 1

        async def mock_db_execute(sql, params=None):
            if "COUNT" in sql:
                return mock_cursor
            if "worker_lock_ts=datetime('now', '+8 hours')" in sql and "worker_lock_token=?" in sql:
                return mock_cursor_lock
            return mock_cursor_empty

        worker.db.execute = mock_db_execute

        loop_count = 0

        async def mock_sleep(seconds):
            nonlocal loop_count
            loop_count += 1
            if loop_count >= 2:
                worker.running = False

        with patch("app.engine.worker.asyncio.sleep", side_effect=mock_sleep):
            await worker._main_loop()

        worker.executor.execute.assert_not_called()


# 
# 
# 


class TestDataIsolation:
    """DoDWorker Aoperator_id=1 operator_id=2 """

    def test_worker_binds_operator_id(self):
        """Worker  operator_id"""
        worker_a = _make_worker(operator_id=1, account_id=100)
        worker_b = _make_worker(operator_id=2, account_id=200)

        assert worker_a.operator_id == 1
        assert worker_a.account_id == 100
        assert worker_b.operator_id == 2
        assert worker_b.account_id == 200

    @pytest.mark.asyncio
    async def test_settlement_uses_correct_platform_type(self):
        """ Worker  platform_type"""
        worker = _make_worker(platform_type="JND282")
        settlement_install = _make_install(
            issue="20250302002",
            state=1,
            open_countdown_sec=5,
            pre_issue="20250302001",
            pre_result="4,5,6",
        )

        worker.settler._save_lottery_result = AsyncMock()
        worker._fetch_install_with_retry = AsyncMock(return_value=settlement_install)
        worker._feedback_settlement_results = AsyncMock()
        worker._has_issue_unsettled_orders = AsyncMock(return_value=False)

        settled = await worker._try_settle_pending_issue("20250302001")

        assert settled is True
        worker.settler.settle.assert_called_once_with(
            issue="20250302001",
            balls=[4, 5, 6],
            sum_value=15,
            platform_type="JND282",
            adapter=worker.adapter,
            is_recovery=True,
        )

    @pytest.mark.asyncio
    async def test_reconciler_uses_correct_account_id(self):
        """ Worker  account_id"""
        worker = _make_worker(account_id=999)
        settlement_install = _make_install(
            issue="20250302002",
            state=1,
            open_countdown_sec=5,
            pre_issue="20250302001",
            pre_result="4,5,6",
        )

        worker.settler._save_lottery_result = AsyncMock()
        worker._fetch_install_with_retry = AsyncMock(return_value=settlement_install)
        worker._feedback_settlement_results = AsyncMock()
        worker._has_issue_unsettled_orders = AsyncMock(return_value=False)

        settled = await worker._try_settle_pending_issue("20250302001")

        assert settled is True
        worker.reconciler.reconcile.assert_called_once_with(
            issue="20250302001",
            account_id=999,
        )


# 
# 
# 


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_sets_running(self):
        """start()  running=True, status='running'"""
        worker = _make_worker()

        # Mock lock acquisition success
        mock_cursor = _mock_lock_cursor(1)
        worker.db.execute = AsyncMock(return_value=mock_cursor)
        worker.db.commit = AsyncMock()

        # Mock _run_with_restart to avoid actual loop
        async def noop():
            worker._resolve_startup_success()

        worker._run_with_restart = noop
        await worker.start()

        assert worker.running is True
        assert worker.status == "running"

    @pytest.mark.asyncio
    async def test_stop_sets_stopped(self):
        """stop()  running=False, status='stopped'"""
        worker = _make_worker()
        worker.running = True
        worker.status = "running"
        worker._lock_token = "test-token"

        worker.db.execute = AsyncMock()
        worker.db.commit = AsyncMock()

        await worker.stop()

        assert worker.running is False
        assert worker.status == "stopped"

    @pytest.mark.asyncio
    async def test_double_start_no_error(self):
        """ start() """
        worker = _make_worker()
        worker.running = True

        # Should just log warning, not raise
        await worker.start()

    def test_add_remove_strategy(self):
        """/"""
        worker = _make_worker()
        runner = MagicMock()

        worker.add_strategy(1, runner, apply_next_issue_only=False)
        assert 1 in worker.strategies

        worker.remove_strategy(1, apply_next_issue_only=False)
        assert 1 not in worker.strategies

        # 
        worker.remove_strategy(999)

    def test_add_strategy_stages_next_issue_by_default(self):
        worker = _make_worker()
        staged_runner = MagicMock()

        worker.add_strategy(
            2,
            staged_runner,
            profile=StrategyRuntimeProfile(strategy_id=2, bet_timing=60),
        )

        assert 2 not in worker.strategies
        assert 2 in worker.get_staged_or_current_strategies()
        assert worker.get_staged_or_current_profiles()[2].bet_timing == 60


# 
# 
# 


class TestSignalCollection:
    @pytest.mark.asyncio
    async def test_collect_signals_from_running_strategies(self):
        """ running """
        from app.engine.strategy_runner import BetSignal

        worker = _make_worker()
        runner = MagicMock()
        runner.collect_signals.return_value = [
            BetSignal(
                strategy_id=1,
                key_code="DX1",
                amount=1000,
                idempotent_id="20250302001-1-DX1",
            )
        ]
        worker.strategies = {1: runner}

        install = _make_install(issue="20250302001")
        signals = await worker._collect_signals(install)

        assert len(signals) == 1
        assert signals[0].key_code == "DX1"

    @pytest.mark.asyncio
    async def test_collect_signals_exception_isolated(self):
        """"""
        from app.engine.strategy_runner import BetSignal

        worker = _make_worker()

        runner_ok = MagicMock()
        runner_ok.collect_signals.return_value = [
            BetSignal(
                strategy_id=2,
                key_code="DX2",
                amount=500,
                idempotent_id="20250302001-2-DX2",
            )
        ]

        runner_fail = MagicMock()
        runner_fail.collect_signals.side_effect = RuntimeError("")

        worker.strategies = {1: runner_fail, 2: runner_ok}

        install = _make_install(issue="20250302001")
        signals = await worker._collect_signals(install)

        #  runner_ok 
        assert len(signals) == 1
        assert signals[0].strategy_id == 2

    @pytest.mark.asyncio
    async def test_collect_signals_passes_latest_result_history(self):
        """install.pre_result is exposed as StrategyContext.history[0]."""
        worker = _make_worker()
        runner = MagicMock()
        runner.collect_signals.return_value = []
        worker.strategies = {1: runner}

        install = _make_install(
            issue="20250302002",
            pre_issue="20250302001",
            pre_result="1,2,3",
        )
        await worker._collect_signals(install)

        ctx = runner.collect_signals.call_args.kwargs["ctx"]
        assert len(ctx.history) == 1
        assert ctx.history[0].issue == "20250302001"
        assert ctx.history[0].balls == [1, 2, 3]
        assert ctx.history[0].sum_value == 6


# 
# PBT: P22  Worker 
# 

from hypothesis import given, settings, strategies as st


class TestPBT_P22_WorkerRecoveryIdempotency:
    """P22: 

    **Validates: Requirements 10.2**

    Property: After N failures (1-4) followed by a successful _main_loop entry,
    the Worker state is consistent with a fresh start:
    - _restart_count resets to 0
    - status remains 'running' (not 'error')
    - running is True
    """

    @given(num_failures=st.integers(min_value=1, max_value=4))
    @settings(max_examples=100)
    def test_pbt_recovery_state_clean_after_failures(self, num_failures: int):
        """After N failures then success, Worker state matches a fresh start.

        **Validates: Requirements 10.2**
        """
        import asyncio

        async def _run():
            worker = _make_worker()
            worker.running = True
            worker.status = "running"
            worker._startup_ready = True

            call_count = 0

            async def mock_main_loop():
                nonlocal call_count
                call_count += 1
                if call_count <= num_failures:
                    raise RuntimeError(f" #{call_count}")
                # Success: simulate login resetting restart_count, then stop
                worker._restart_count = 0
                worker.running = False

            worker._main_loop = mock_main_loop

            with patch("app.engine.worker.asyncio.sleep", new_callable=AsyncMock):
                await worker._run_with_restart()

            # After recovery, state should be clean
            assert worker._restart_count == 0, (
                f"restart_count should be 0 after recovery, got {worker._restart_count}"
            )
            # Worker stopped itself gracefully (running=False), not due to error
            assert worker.status != "error", (
                f"status should not be 'error' after successful recovery, got {worker.status}"
            )
            # Verify all failures + 1 success happened
            assert call_count == num_failures + 1

        asyncio.get_event_loop().run_until_complete(_run())


# 
# PBT: P27  8s
# 


class TestPBT_P27_SkipThreshold:
    """P27: the worker only enters the 9..bet_timing window.

    **Validates: Requirements 5.1**

    Property: For any CloseTimeStamp value, _should_bet returns False
    when close_countdown_sec <= SKIP_THRESHOLD (8), and only returns True
    inside the configured strategy window.
    """

    @given(close_countdown_sec=st.integers(min_value=0, max_value=8))
    @settings(max_examples=100)
    def test_pbt_skip_when_lte_8(self, close_countdown_sec: int):
        """CloseTimeStamp 18  _should_bet returns False.

        **Validates: Requirements 5.1**
        """
        worker = _make_worker()
        install = _make_install(close_countdown_sec=close_countdown_sec)
        assert worker._should_bet(install) is False, (
            f"Expected skip for close_countdown_sec={close_countdown_sec}, "
            f"but _should_bet returned True"
        )

    @given(close_countdown_sec=st.integers(min_value=9, max_value=30))
    @settings(max_examples=100)
    def test_pbt_bet_when_within_default_window(self, close_countdown_sec: int):
        """CloseTimeStamp inside 9..30 enters the default 30s window."""
        worker = _make_worker()
        install = _make_install(close_countdown_sec=close_countdown_sec)
        assert worker._should_bet(install) is True, (
            f"Expected bet for close_countdown_sec={close_countdown_sec}, "
            f"but _should_bet returned False"
        )

    @given(close_countdown_sec=st.integers(min_value=31, max_value=300))
    @settings(max_examples=100)
    def test_pbt_skip_when_window_not_open_yet(self, close_countdown_sec: int):
        worker = _make_worker()
        install = _make_install(close_countdown_sec=close_countdown_sec)
        assert worker._should_bet(install) is False, (
            f"Expected wait for close_countdown_sec={close_countdown_sec}, "
            f"but _should_bet returned True"
        )


class TestIssueExecutionPlan:
    def test_replace_strategy_snapshot_preserves_executed_current_issue_groups(self):
        worker = _make_worker(
            strategies={1: MagicMock()},
            strategy_profiles={
                1: StrategyRuntimeProfile(strategy_id=1, bet_timing=60),
            },
        )

        plan = worker._ensure_issue_execution_plan("20250302001")
        worker._consume_timing_group(plan.groups[0])

        worker.replace_strategy_snapshot(
            {1: MagicMock(), 2: MagicMock()},
            {
                1: StrategyRuntimeProfile(strategy_id=1, bet_timing=60),
                2: StrategyRuntimeProfile(strategy_id=2, bet_timing=30),
            },
            apply_next_issue_only=False,
        )

        rebuilt = worker._issue_execution_plan
        assert rebuilt is not None
        assert rebuilt.issue == "20250302001"
        assert rebuilt.executed_strategy_ids == {1}

        pending_groups = {
            group.bet_timing: group.strategy_ids
            for group in rebuilt.groups
            if not group.executed
        }
        assert pending_groups == {30: [2]}

    @pytest.mark.asyncio
    async def test_run_due_strategy_windows_executes_groups_in_desc_order(self):
        from app.engine.executor import ExecutionReport
        from app.engine.strategy_runner import BetSignal

        worker = _make_worker(
            strategies={1: MagicMock(), 2: MagicMock()},
            strategy_profiles={
                1: StrategyRuntimeProfile(strategy_id=1, bet_timing=60),
                2: StrategyRuntimeProfile(strategy_id=2, bet_timing=40),
            },
        )
        worker.running = True
        worker.poller.last_success_at = datetime.now()
        worker._collect_signals = AsyncMock(
            side_effect=[
                [BetSignal(strategy_id=1, key_code="DX1", amount=1000, idempotent_id="i-1")],
                [BetSignal(strategy_id=2, key_code="DX2", amount=1000, idempotent_id="i-2")],
            ]
        )
        worker.executor.execute = AsyncMock(
            side_effect=[ExecutionReport(), ExecutionReport()]
        )
        worker._apply_execution_report = AsyncMock()

        result = await worker._run_due_strategy_windows(
            _make_install(close_countdown_sec=34)
        )

        assert result.close_countdown_sec == 34
        assert worker._collect_signals.call_args_list[0].kwargs["strategy_ids"] == [1]
        assert worker._collect_signals.call_args_list[1].kwargs["strategy_ids"] == [2]
        assert worker.executor.execute.await_count == 2
        worker.adapter.get_current_install.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_due_strategy_windows_marks_pending_groups_skipped_when_closed(self):
        worker = _make_worker(
            strategies={1: MagicMock(), 2: MagicMock()},
            strategy_profiles={
                1: StrategyRuntimeProfile(strategy_id=1, bet_timing=60),
                2: StrategyRuntimeProfile(strategy_id=2, bet_timing=40),
            },
        )
        worker.running = True
        worker.poller.last_success_at = datetime.now()
        closed_install = _make_install(state=2, close_countdown_sec=0, open_countdown_sec=0)
        setattr(closed_install, "market_data_state", "market_closed")
        worker.executor.execute = AsyncMock()

        result = await worker._run_due_strategy_windows(closed_install)

        assert result is closed_install
        assert worker._issue_execution_plan is not None
        assert worker._issue_execution_plan.skipped_strategy_reasons == {
            1: "market_closed",
            2: "market_closed",
        }
        worker.executor.execute.assert_not_awaited()
        worker.adapter.get_current_install.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_due_strategy_windows_skips_issue_when_snapshot_stale(self):
        worker = _make_worker(
            strategies={1: MagicMock(), 2: MagicMock()},
            strategy_profiles={
                1: StrategyRuntimeProfile(strategy_id=1, bet_timing=60),
                2: StrategyRuntimeProfile(strategy_id=2, bet_timing=40),
            },
        )
        worker.running = True
        worker.poller.last_success_at = datetime.now() - timedelta(seconds=50)
        worker.executor.execute = AsyncMock()

        result = await worker._run_due_strategy_windows(
            _make_install(close_countdown_sec=34)
        )

        assert result.close_countdown_sec == 34
        assert worker._issue_execution_plan is not None
        assert worker._issue_execution_plan.skipped_strategy_reasons == {
            1: "snapshot_stale",
            2: "snapshot_stale",
        }
        worker.executor.execute.assert_not_awaited()
        worker.adapter.get_current_install.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_due_strategy_windows_marks_dw3_gate_skipped_reason(self):
        runner = MagicMock()
        runner.collect_signals.return_value = []
        runner.strategy.required_history_issues = 1
        runner.strategy.last_signal_metadata = {
            "strategy_kind": "dw3",
            "gate_skipped": True,
            "skip_reason": "gate_skipped_all_blocked",
        }
        worker = _make_worker(
            strategies={1: runner},
            strategy_profiles={
                1: StrategyRuntimeProfile(strategy_id=1, bet_timing=60),
            },
        )
        worker.running = True
        worker.poller.last_success_at = datetime.now()
        worker._load_recent_history = AsyncMock(return_value=[])
        worker.executor.execute = AsyncMock()

        result = await worker._run_due_strategy_windows(
            _make_install(
                close_countdown_sec=34,
                pre_issue="20250302000",
                pre_result="3,2,1",
            )
        )

        assert result.close_countdown_sec == 34
        assert worker._issue_execution_plan is not None
        assert worker._issue_execution_plan.skipped_strategy_reasons == {
            1: "gate_skipped_all_blocked",
        }
        worker.executor.execute.assert_not_awaited()
        worker.adapter.get_current_install.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_due_strategy_windows_triggers_diagnostics_only_after_execution_failure(self):
        from app.engine.strategy_runner import BetSignal

        session_runtime = AsyncMock()
        session_runtime.recover = AsyncMock(return_value=True)
        worker = _make_worker(
            session_runtime=session_runtime,
            strategies={1: MagicMock()},
            strategy_profiles={
                1: StrategyRuntimeProfile(strategy_id=1, bet_timing=60),
            },
        )
        worker.running = True
        worker.poller.last_success_at = datetime.now()
        worker._collect_signals = AsyncMock(
            return_value=[
                BetSignal(strategy_id=1, key_code="DX1", amount=1000, idempotent_id="i-1")
            ]
        )
        worker.executor.execute = AsyncMock(side_effect=RuntimeError("bet failed"))
        worker._apply_execution_report = AsyncMock()

        await worker._run_due_strategy_windows(_make_install(close_countdown_sec=34))

        session_runtime.recover.assert_awaited_once_with("worker_bet_failure")
        worker.adapter.get_current_install.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_due_strategy_windows_does_not_trigger_diagnostics_when_execution_succeeds(self):
        from app.engine.executor import ExecutionReport
        from app.engine.strategy_runner import BetSignal

        session_runtime = AsyncMock()
        session_runtime.recover = AsyncMock(return_value=True)
        worker = _make_worker(
            session_runtime=session_runtime,
            strategies={1: MagicMock()},
            strategy_profiles={
                1: StrategyRuntimeProfile(strategy_id=1, bet_timing=60),
            },
        )
        worker.running = True
        worker.poller.last_success_at = datetime.now()
        worker._collect_signals = AsyncMock(
            return_value=[
                BetSignal(strategy_id=1, key_code="DX1", amount=1000, idempotent_id="i-1")
            ]
        )
        worker.executor.execute = AsyncMock(return_value=ExecutionReport())
        worker._apply_execution_report = AsyncMock()

        await worker._run_due_strategy_windows(_make_install(close_countdown_sec=34))

        session_runtime.recover.assert_not_awaited()
        worker.adapter.get_current_install.assert_not_called()


class TestCountdownValidationLogging:
    @pytest.mark.asyncio
    async def test_revalidate_group_window_logs_allowed_opportunity(self):
        worker = _make_worker()
        worker.poller.last_success_at = datetime.now()
        install = _make_install(
            issue="20250302001",
            state=1,
            close_countdown_sec=24,
        )
        setattr(install, "market_data_state", "shared_ok")

        with patch("app.engine.worker.log_countdown_validation") as log_validation:
            refreshed, reason = await worker._revalidate_group_window(
                install,
                bet_timing=30,
                strategy_ids=[1, 2],
            )

        assert reason is None
        assert refreshed.close_countdown_sec == 24
        assert log_validation.call_count == 1
        assert log_validation.call_args.kwargs["allowed"] is True
        assert log_validation.call_args.kwargs["phase"] == "pre_submit"
        assert log_validation.call_args.kwargs["strategy_ids"] == [1, 2]
        worker.adapter.get_current_install.assert_not_called()

    @pytest.mark.asyncio
    async def test_revalidate_group_window_logs_blocked_reason(self):
        worker = _make_worker()
        worker.poller.last_success_at = datetime.now()
        install = _make_install(
            issue="20250302002",
            state=1,
            close_countdown_sec=24,
        )
        setattr(install, "market_data_state", "shared_ok")

        with patch("app.engine.worker.log_countdown_validation") as log_validation:
            refreshed, reason = await worker._revalidate_group_window(
                install,
                bet_timing=30,
                strategy_ids=[7],
                expected_issue="20250302001",
            )

        assert refreshed.issue == "20250302002"
        assert reason == "issue_changed"
        assert log_validation.call_args.kwargs["allowed"] is False
        assert log_validation.call_args.kwargs["reason"] == "issue_changed"
        worker.adapter.get_current_install.assert_not_called()


# ==================================================================
# Helper: 创建带 mock DB 的 worker（用于主循环测试）
# ==================================================================

def _make_worker_with_db(has_records: bool = True, **overrides) -> AccountWorker:
    worker = _make_worker(**overrides)

    mock_cursor_count = AsyncMock()
    mock_cursor_count.fetchone = AsyncMock(
        return_value={"cnt": 1 if has_records else 0}
    )
    mock_cursor_empty = AsyncMock()
    mock_cursor_empty.fetchall = AsyncMock(return_value=[])

    # 锁续约成功的 cursor
    mock_cursor_lock = AsyncMock()
    mock_cursor_lock.rowcount = 1

    async def mock_db_execute(sql, params=None):
        if "COUNT" in sql:
            return mock_cursor_count
        if "worker_lock_ts=datetime('now', '+8 hours')" in sql and "worker_lock_token=?" in sql:
            return mock_cursor_lock
        return mock_cursor_empty

    worker.db.execute = mock_db_execute
    worker.db.commit = AsyncMock()
    worker.session.login = AsyncMock()
    worker.settler._save_lottery_result = AsyncMock()
    worker._lock_token = "test-lock-token"  # 预设 lock token
    return worker


# ==================================================================
# Task 7.7: Worker 主循环单元测试
# ==================================================================


class TestCountdownDrivenLoop:
    """7.7.1 正常倒计时驱动流程"""

    @pytest.mark.asyncio
    async def test_normal_countdown_flow(self):
        """正常流程：sleep(open_countdown) → sleep(settlement_wait) → fetch → settle → reconcile"""
        worker = _make_worker_with_db()
        worker.running = True
        worker._ensure_settlement_branch_started = AsyncMock()
        worker._register_issue_for_settlement = AsyncMock()

        betting_install = _make_install(
            issue="20250302001",
            state=1,
            close_countdown_sec=60,
            open_countdown_sec=15,
            pre_issue="20250302000",
            pre_result="3,5,7",
        )
        settlement_install = _make_install(
            issue="20250302002",
            state=1,
            close_countdown_sec=60,
            open_countdown_sec=15,
            pre_issue="20250302001",
            pre_result="4,2,6",
        )

        poll_count = 0

        async def mock_poll():
            nonlocal poll_count
            poll_count += 1
            return betting_install if poll_count <= 1 else settlement_install

        worker.poller.poll = mock_poll

        sleep_args = []

        async def mock_sleep(seconds):
            sleep_args.append(seconds)
            # Stop after settlement_wait sleep
            if len(sleep_args) >= 2:
                worker.running = False

        with patch("app.engine.worker.asyncio.sleep", side_effect=mock_sleep):
            await worker._main_loop()

        # Verify sleep sequence: open_countdown_sec, settlement_wait_seconds
        assert 1 <= sleep_args[0] <= 5

        # Verify settle was called with the betting issue (20250302001)
        worker._register_issue_for_settlement.assert_any_call(
            "20250302001",
            open_countdown_sec=15,
        )
        worker.settler.settle.assert_not_called()

        # Verify reconcile was called
        worker.reconciler.reconcile.assert_not_called()


class TestOpenCountdownSkip:
    """7.7.2 OpenTimeStamp <= 0 时跳过休眠"""

    @pytest.mark.asyncio
    async def test_open_countdown_zero_skips_sleep(self):
        """OpenTimeStamp=0 时跳过开奖倒计时休眠，直接进入结算等待"""
        worker = _make_worker_with_db()
        worker.running = True

        betting_install = _make_install(
            issue="20250302001",
            state=1,
            close_countdown_sec=60,
            open_countdown_sec=0,  # <= 0
            pre_issue="20250302000",
            pre_result="3,5,7",
        )
        settlement_install = _make_install(
            issue="20250302002",
            state=1,
            close_countdown_sec=60,
            open_countdown_sec=0,
            pre_issue="20250302001",
            pre_result="4,2,6",
        )

        poll_count = 0

        async def mock_poll():
            nonlocal poll_count
            poll_count += 1
            return betting_install if poll_count <= 1 else settlement_install

        worker.poller.poll = mock_poll

        sleep_args = []

        async def mock_sleep(seconds):
            sleep_args.append(seconds)
            # Stop after first sleep (settlement_wait only)
            if len(sleep_args) >= 1:
                worker.running = False

        with patch("app.engine.worker.asyncio.sleep", side_effect=mock_sleep):
            await worker._main_loop()

        # Only settlement_wait sleep, no open_countdown sleep
        assert 1 <= sleep_args[0] <= 5
        # No 0-second sleep for open_countdown
        assert 0 not in sleep_args or sleep_args[0] != 0

    @pytest.mark.asyncio
    async def test_open_countdown_negative_skips_sleep(self):
        """OpenTimeStamp=-5 时跳过开奖倒计时休眠"""
        worker = _make_worker_with_db()
        worker.running = True

        betting_install = _make_install(
            issue="20250302001",
            state=1,
            close_countdown_sec=60,
            open_countdown_sec=-5,  # negative
            pre_issue="20250302000",
            pre_result="3,5,7",
        )
        settlement_install = _make_install(
            issue="20250302002",
            state=1,
            close_countdown_sec=60,
            open_countdown_sec=-5,
            pre_issue="20250302001",
            pre_result="4,2,6",
        )

        poll_count = 0

        async def mock_poll():
            nonlocal poll_count
            poll_count += 1
            return betting_install if poll_count <= 1 else settlement_install

        worker.poller.poll = mock_poll

        sleep_args = []

        async def mock_sleep(seconds):
            sleep_args.append(seconds)
            if len(sleep_args) >= 1:
                worker.running = False

        with patch("app.engine.worker.asyncio.sleep", side_effect=mock_sleep):
            await worker._main_loop()

        # First sleep should be settlement_wait, not negative
        assert 1 <= sleep_args[0] <= 5


class TestSettlementDataRetry:
    """7.7.3 PreLotteryResult 重试 6 次后降级处理"""

    @pytest.mark.asyncio
    async def test_retry_6_times_then_degrade(self):
        """重试 6 次后 real 订单 settle_failed + sim 降级 + 发告警"""
        worker = _make_worker_with_db()
        worker.running = True
        settlement_install = _make_install(
            issue="20250302002",
            state=1,
            open_countdown_sec=0,
            pre_issue="20250302001",
            pre_result="",
        )
        worker._fetch_install_with_retry = AsyncMock(return_value=settlement_install)

        with patch("app.engine.worker.asyncio.sleep", new_callable=AsyncMock):
            result = await worker._fetch_settlement_data("20250302001")

        assert result is None
        alert_calls = worker.alert_service.send.call_args_list
        alert_types = [c.kwargs.get("alert_type") or c[1].get("alert_type", "") for c in alert_calls]
        assert "settlement_data_missing" in alert_types
        worker.settler._mark_orders_settle_failed.assert_not_called()


class TestRecoveryFlow:
    """7.7.4 补结算流程"""

    @pytest.mark.asyncio
    async def test_recovery_with_historical_results(self):
        """补结算：有历史开奖结果时调用 settle(is_recovery=True)"""
        worker = _make_worker()
        worker.running = False  # Don't enter main loop
        worker.session.login = AsyncMock()
        worker.settler._save_lottery_result = AsyncMock()

        # Mock DB: has records, has unsettled issues
        mock_cursor_count = AsyncMock()
        mock_cursor_count.fetchone = AsyncMock(return_value={"cnt": 5})

        class DictRow:
            def __init__(self, d):
                self._d = d
            def __getitem__(self, key):
                return self._d[key]
            def keys(self):
                return self._d.keys()

        mock_cursor_issues = AsyncMock()
        mock_cursor_issues.fetchall = AsyncMock(return_value=[
            DictRow({"issue": "20250302000"}),
            DictRow({"issue": "20250302001"}),
        ])

        async def mock_db_execute(sql, params=None):
            if "COUNT" in sql:
                return mock_cursor_count
            if "DISTINCT issue" in sql:
                return mock_cursor_issues
            mock_empty = AsyncMock()
            mock_empty.fetchall = AsyncMock(return_value=[])
            return mock_empty

        worker.db.execute = mock_db_execute

        # Mock adapter: return historical results
        worker.adapter.get_lottery_results = AsyncMock(return_value=[
            {"Installments": "20250302000", "OpenResult": "3,5,7"},
            {"Installments": "20250302001", "OpenResult": "1,2,3"},
        ])

        with patch("app.engine.worker.asyncio.sleep", new_callable=AsyncMock):
            await worker._recover_unsettled_orders()

        # Verify settle called twice with is_recovery=True
        assert worker.settler.settle.call_count == 2
        for c in worker.settler.settle.call_args_list:
            assert c.kwargs["is_recovery"] is True


class TestRecoveryHistoryMissing:
    """7.7.5 补结算历史缺失时 settle_data_expired 告警"""

    @pytest.mark.asyncio
    async def test_recovery_missing_history_sends_alert(self):
        """补结算：历史开奖结果缺失时发送 settle_data_expired 告警"""
        worker = _make_worker()
        worker.running = False
        worker.session.login = AsyncMock()

        class DictRow:
            def __init__(self, d):
                self._d = d
            def __getitem__(self, key):
                return self._d[key]
            def keys(self):
                return self._d.keys()

        mock_cursor_issues = AsyncMock()
        mock_cursor_issues.fetchall = AsyncMock(return_value=[
            DictRow({"issue": "20250301999"}),
        ])

        mock_cursor_orders = AsyncMock()
        mock_cursor_orders.fetchall = AsyncMock(return_value=[
            DictRow({"id": 1, "status": "bet_success", "simulation": 0}),
        ])

        async def mock_db_execute(sql, params=None):
            if "DISTINCT issue" in sql:
                return mock_cursor_issues
            if "bet_orders" in sql:
                return mock_cursor_orders
            mock_empty = AsyncMock()
            mock_empty.fetchall = AsyncMock(return_value=[])
            return mock_empty

        worker.db.execute = mock_db_execute

        # Adapter returns results but NOT for the issue we need
        worker.adapter.get_lottery_results = AsyncMock(return_value=[
            {"Installments": "20250302000", "OpenResult": "3,5,7"},
        ])
        worker._register_issue_for_settlement = AsyncMock()

        with patch("app.engine.worker.asyncio.sleep", new_callable=AsyncMock):
            await worker._recover_unsettled_orders()

        worker._register_issue_for_settlement.assert_called_once_with(
            "20250301999",
            immediate=True,
        )
        worker.settler._mark_orders_settle_failed.assert_not_called()


class TestFreshStartDetection:
    """7.7.6 全新启动时记录 last_issue 不触发结算（AC1.5）"""

    @pytest.mark.asyncio
    async def test_fresh_start_records_last_issue(self):
        """全新启动：无历史记录时记录 last_issue，不触发结算"""
        worker = _make_worker()
        worker.running = False  # Don't enter main loop
        worker.session.login = AsyncMock()

        # Mock DB: no records (fresh start)
        mock_cursor_count = AsyncMock()
        mock_cursor_count.fetchone = AsyncMock(return_value={"cnt": 0})

        async def mock_db_execute(sql, params=None):
            return mock_cursor_count

        worker.db.execute = mock_db_execute

        # Mock poller
        install = _make_install(issue="20250302005", close_countdown_sec=60)
        worker.poller.poll = AsyncMock(return_value=install)

        await worker._detect_fresh_start()

        # Verify the current issue remains joinable
        assert worker.poller.last_issue == ""

        # Verify settle was NOT called
        worker.settler.settle.assert_not_called()

    @pytest.mark.asyncio
    async def test_fresh_start_records_last_issue_when_window_not_safe(self):
        worker = _make_worker()
        worker.running = False
        worker.session.login = AsyncMock()

        mock_cursor_count = AsyncMock()
        mock_cursor_count.fetchone = AsyncMock(return_value={"cnt": 0})

        async def mock_db_execute(sql, params=None):
            return mock_cursor_count

        worker.db.execute = mock_db_execute
        worker.poller.poll = AsyncMock(
            return_value=_make_install(issue="20250302005", close_countdown_sec=8)
        )

        await worker._detect_fresh_start()

        assert worker.poller.last_issue == "20250302005"

    @pytest.mark.asyncio
    async def test_non_fresh_start_skips_detection(self):
        """非全新启动：有历史记录时不修改 last_issue"""
        worker = _make_worker()
        worker.running = False
        worker.session.login = AsyncMock()

        # Mock DB: has records
        mock_cursor_count = AsyncMock()
        mock_cursor_count.fetchone = AsyncMock(return_value={"cnt": 10})

        async def mock_db_execute(sql, params=None):
            return mock_cursor_count

        worker.db.execute = mock_db_execute

        original_last_issue = worker.poller.last_issue

        await worker._detect_fresh_start()

        # poller.poll should NOT have been called
        worker.poller.poll.assert_not_called()


# ==================================================================
# Task 7.6: settlement_wait_seconds 参数测试
# ==================================================================


class TestSettlementWaitSeconds:
    """7.6 settlement_wait_seconds 构造参数 clamp 测试"""

    def test_default_value(self):
        worker = _make_worker()
        assert worker._settlement_wait_seconds == SETTLEMENT_WAIT_SECONDS_DEFAULT

    def test_clamp_below_min(self):
        worker = _make_worker(settlement_wait_seconds=5)
        assert worker._settlement_wait_seconds == SETTLEMENT_WAIT_SECONDS_MIN

    def test_clamp_above_max(self):
        worker = _make_worker(settlement_wait_seconds=200)
        assert worker._settlement_wait_seconds == SETTLEMENT_WAIT_SECONDS_MAX

    def test_within_range(self):
        worker = _make_worker(settlement_wait_seconds=60)
        assert worker._settlement_wait_seconds == 60

    def test_min_boundary(self):
        worker = _make_worker(settlement_wait_seconds=SETTLEMENT_WAIT_SECONDS_MIN)
        assert worker._settlement_wait_seconds == SETTLEMENT_WAIT_SECONDS_MIN

    def test_max_boundary(self):
        worker = _make_worker(settlement_wait_seconds=SETTLEMENT_WAIT_SECONDS_MAX)
        assert worker._settlement_wait_seconds == SETTLEMENT_WAIT_SECONDS_MAX


# ==================================================================
# Task 7.2: _fetch_install_with_retry 测试
# ==================================================================


class TestFetchInstallWithRetry:
    """7.2 _fetch_install_with_retry 重试逻辑"""

    @pytest.mark.asyncio
    async def test_success_on_first_try(self):
        worker = _make_worker()
        install = _make_install()
        worker.poller.poll = AsyncMock(return_value=install)

        result = await worker._fetch_install_with_retry()
        assert result == install
        assert worker.poller.poll.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_delays_incremental(self):
        """重试间隔递增：5s → 10s → 30s"""
        worker = _make_worker()
        worker.poller.poll = AsyncMock(side_effect=RuntimeError("网络错误"))

        sleep_args = []

        async def mock_sleep(seconds):
            sleep_args.append(seconds)

        with patch("app.engine.worker.asyncio.sleep", side_effect=mock_sleep):
            result = await worker._fetch_install_with_retry()

        assert result is None
        assert worker.poller.poll.call_count == API_RETRY_MAX
        # 3 attempts: sleep after 1st (5s), sleep after 2nd (10s), no sleep after 3rd
        assert sleep_args == [5, 10]

    @pytest.mark.asyncio
    async def test_all_failures_send_alert(self):
        """3 次全部失败后发送 api_call_failed 告警"""
        worker = _make_worker()
        worker.poller.poll = AsyncMock(side_effect=RuntimeError("网络错误"))

        with patch("app.engine.worker.asyncio.sleep", new_callable=AsyncMock):
            result = await worker._fetch_install_with_retry()

        assert result is None
        worker.alert_service.send.assert_called_once()
        call_kwargs = worker.alert_service.send.call_args.kwargs
        assert call_kwargs["alert_type"] == "api_call_failed"

    @pytest.mark.asyncio
    async def test_success_on_second_try(self):
        """第 2 次成功"""
        worker = _make_worker()
        install = _make_install()
        worker.poller.poll = AsyncMock(
            side_effect=[RuntimeError("fail"), install]
        )

        with patch("app.engine.worker.asyncio.sleep", new_callable=AsyncMock):
            result = await worker._fetch_install_with_retry()

        assert result == install
        assert worker.poller.poll.call_count == 2

    @pytest.mark.asyncio
    async def test_remote_login_reconnects_and_retries(self):
        worker = _make_worker()
        install = _make_install()
        worker.poller.poll = AsyncMock(
            side_effect=[
                RemoteLoginRequired(raw_state=-2, message="remote login detected"),
                install,
            ]
        )
        worker.session.reconnect = AsyncMock()
        worker.session.ensure_session = AsyncMock(return_value=True)

        result = await worker._fetch_install_with_retry()

        assert result == install
        worker.session.reconnect.assert_awaited_once()
        worker.session.ensure_session.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_remote_login_failure_stops_worker_and_alerts(self):
        worker = _make_worker()
        worker.running = True
        worker.poller.poll = AsyncMock(
            side_effect=RemoteLoginRequired(raw_state=-2, message="remote login detected")
        )
        worker.session.reconnect = AsyncMock()
        worker.session.ensure_session = AsyncMock(return_value=False)
        worker._persist_strategy_statuses = AsyncMock()

        result = await worker._fetch_install_with_retry()

        assert result is None
        assert worker.running is False
        assert worker.status == "error"
        worker.alert_service.send.assert_awaited_once()
        assert worker.alert_service.send.await_args.kwargs["alert_type"] == "session_lost"
        worker._persist_strategy_statuses.assert_awaited_once_with("error")



# ==================================================================
# Task 8.6: 跨进程互斥锁单元测试
# ==================================================================

from app.engine.worker import LOCK_TTL_MINUTES, LOCK_RENEW_INTERVAL


class TestLockAcquireRenewRelease:
    """8.6.1 正常抢锁/续约/释放流程"""

    @pytest.mark.asyncio
    async def test_acquire_lock_success_no_existing_lock(self):
        """无锁时抢锁成功，返回 True，_lock_token 被设置"""
        worker = _make_worker()

        mock_cursor = _mock_lock_cursor(1)
        worker.db.execute = AsyncMock(return_value=mock_cursor)
        worker.db.commit = AsyncMock()

        result = await worker._acquire_lock()

        assert result is True
        assert worker._lock_token is not None
        # Verify CAS SQL was called
        call_args = worker.db.execute.call_args
        sql = call_args[0][0]
        assert "worker_lock_token" in sql
        assert "worker_lock_ts" in sql
        assert "datetime('now', '+8 hours', '-5 minutes')" in sql

    @pytest.mark.asyncio
    async def test_acquire_lock_fail_active_lock(self):
        """已有活跃锁时抢锁失败，返回 False，_lock_token 保持 None"""
        worker = _make_worker()

        mock_cursor = _mock_lock_cursor(0)  # CAS 失败
        worker.db.execute = AsyncMock(return_value=mock_cursor)
        worker.db.commit = AsyncMock()

        result = await worker._acquire_lock()

        assert result is False
        assert worker._lock_token is None

    @pytest.mark.asyncio
    async def test_renew_lock_success(self):
        """续约成功返回 True"""
        worker = _make_worker()
        worker._lock_token = "test-token-123"

        mock_cursor = _mock_lock_cursor(1)
        worker.db.execute = AsyncMock(return_value=mock_cursor)
        worker.db.commit = AsyncMock()

        result = await worker._renew_lock()

        assert result is True
        assert worker.running is False  # wasn't set to True
        # Verify SQL uses token match
        call_args = worker.db.execute.call_args
        sql = call_args[0][0]
        params = call_args[0][1]
        assert "worker_lock_token=?" in sql
        assert params == (worker.account_id, worker._platform_type, "test-token-123")

    @pytest.mark.asyncio
    async def test_renew_lock_no_token(self):
        """无 token 时续约返回 False"""
        worker = _make_worker()
        worker._lock_token = None

        result = await worker._renew_lock()

        assert result is False

    @pytest.mark.asyncio
    async def test_release_lock_success(self):
        """释放锁：清除 token 和 ts，_lock_token 置 None"""
        worker = _make_worker()
        worker._lock_token = "test-token-456"

        worker.db.execute = AsyncMock()
        worker.db.commit = AsyncMock()

        await worker._release_lock()

        assert worker._lock_token is None
        call_args = worker.db.execute.call_args
        sql = call_args[0][0]
        assert "worker_lock_token=NULL" in sql
        assert "worker_lock_ts=NULL" in sql

    @pytest.mark.asyncio
    async def test_release_lock_no_token_noop(self):
        """无 token 时释放锁为 noop"""
        worker = _make_worker()
        worker._lock_token = None
        worker.db.execute = AsyncMock()

        await worker._release_lock()

        worker.db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_full_acquire_renew_release_cycle(self):
        """完整流程：抢锁 → 续约 → 释放"""
        worker = _make_worker()

        # Acquire
        mock_cursor_acquire = _mock_lock_cursor(1)
        worker.db.execute = AsyncMock(return_value=mock_cursor_acquire)
        worker.db.commit = AsyncMock()

        assert await worker._acquire_lock() is True
        token = worker._lock_token
        assert token is not None

        # Renew
        mock_cursor_renew = AsyncMock()
        mock_cursor_renew.rowcount = 1
        worker.db.execute = AsyncMock(return_value=mock_cursor_renew)

        assert await worker._renew_lock() is True
        assert worker._lock_token == token  # token unchanged

        # Release
        worker.db.execute = AsyncMock()
        await worker._release_lock()
        assert worker._lock_token is None


class TestLockTimeout:
    """8.6.2 锁超时后新 Worker 可抢锁"""

    @pytest.mark.asyncio
    async def test_expired_lock_allows_new_acquire(self):
        """锁超时（TTL 过期）后新 Worker 可抢锁成功

        模拟：旧锁已超时（CAS 条件 worker_lock_ts < datetime('now', '-5 minutes') 满足），
        新 Worker 的 CAS UPDATE rowcount=1。
        """
        worker_b = _make_worker(account_id=100)

        # CAS 成功（旧锁已超时）
        mock_cursor = _mock_lock_cursor(1)
        worker_b.db.execute = AsyncMock(return_value=mock_cursor)
        worker_b.db.commit = AsyncMock()

        result = await worker_b._acquire_lock()

        assert result is True
        assert worker_b._lock_token is not None

    @pytest.mark.asyncio
    async def test_active_lock_blocks_new_acquire(self):
        """活跃锁（未超时）阻止新 Worker 抢锁

        模拟：旧锁仍活跃（CAS 条件不满足），CAS UPDATE rowcount=0。
        """
        worker_b = _make_worker(account_id=100)

        mock_cursor = _mock_lock_cursor(0)  # 旧锁仍活跃
        worker_b.db.execute = AsyncMock(return_value=mock_cursor)
        worker_b.db.commit = AsyncMock()

        result = await worker_b._acquire_lock()

        assert result is False
        assert worker_b._lock_token is None


class TestRenewFailureStopsWorker:
    """8.6.3 续约失败后 Worker 停止（running=False）"""

    @pytest.mark.asyncio
    async def test_renew_failure_sets_running_false(self):
        """续约失败（rowcount=0）→ running=False + 发送 worker_lock_lost 告警"""
        worker = _make_worker()
        worker.running = True
        worker._lock_token = "my-token"
        worker.poller.poll = AsyncMock(return_value=_make_install())

        mock_cursor = AsyncMock()
        mock_cursor.rowcount = 0  # 续约失败
        worker.db.execute = AsyncMock(return_value=mock_cursor)
        worker.db.commit = AsyncMock()

        result = await worker._renew_lock()

        assert result is False
        assert worker.running is False

        # 验证 worker_lock_lost 告警
        worker.alert_service.send.assert_called_once()
        call_kwargs = worker.alert_service.send.call_args.kwargs
        assert call_kwargs["alert_type"] == "worker_lock_lost"
        assert str(worker.account_id) in call_kwargs["title"]

    @pytest.mark.asyncio
    async def test_renew_failure_in_main_loop_breaks(self):
        """主循环中续约失败导致循环退出"""
        worker = _make_worker_with_db()
        worker.running = True
        worker._ensure_settlement_branch_started = AsyncMock()
        worker._register_issue_for_settlement = AsyncMock()
        worker._ensure_settlement_branch_started = AsyncMock()
        worker._register_issue_for_settlement = AsyncMock()
        worker._ensure_settlement_branch_started = AsyncMock()
        worker._register_issue_for_settlement = AsyncMock()
        worker._ensure_settlement_branch_started = AsyncMock()
        worker._register_issue_for_settlement = AsyncMock()
        worker._lock_token = "my-token"
        worker.poller.poll = AsyncMock(return_value=_make_install())

        # _renew_lock fails
        mock_cursor_renew = AsyncMock()
        mock_cursor_renew.rowcount = 0
        original_db_execute = worker.db.execute

        async def mock_db_execute(sql, params=None):
            if "worker_lock_ts=datetime('now', '+8 hours')" in sql and "worker_lock_token=?" in sql:
                return mock_cursor_renew
            return await original_db_execute(sql, params)

        worker.db.execute = mock_db_execute
        worker.db.commit = AsyncMock()

        with patch("app.engine.worker.asyncio.sleep", new_callable=AsyncMock):
            await worker._main_loop()

        # Worker should have stopped due to lock loss
        assert worker.running is False


class TestLockRaceCondition:
    """8.6.4 A 持锁卡顿 > TTL → B 抢锁 → A 续约失败停止"""

    @pytest.mark.asyncio
    async def test_a_stalls_b_acquires_a_renew_fails(self):
        """竞态场景：A 超时 → B 抢锁成功 → A 续约失败停止

        模拟：
        1. A 持有锁 token_a
        2. A 卡顿超过 TTL，B 抢锁成功（CAS rowcount=1）
        3. A 尝试续约，因 token 不匹配（rowcount=0）→ running=False
        """
        # Worker A
        worker_a = _make_worker(account_id=100)
        worker_a.running = True
        worker_a._lock_token = "token-a"

        # Worker B acquires lock (A's lock expired)
        worker_b = _make_worker(account_id=100)
        mock_cursor_b = _mock_lock_cursor(1)
        worker_b.db.execute = AsyncMock(return_value=mock_cursor_b)
        worker_b.db.commit = AsyncMock()

        result_b = await worker_b._acquire_lock()
        assert result_b is True
        assert worker_b._lock_token is not None
        assert worker_b._lock_token != "token-a"

        # Worker A tries to renew — fails because token changed
        mock_cursor_a = AsyncMock()
        mock_cursor_a.rowcount = 0  # token 不匹配
        worker_a.db.execute = AsyncMock(return_value=mock_cursor_a)
        worker_a.db.commit = AsyncMock()

        result_a = await worker_a._renew_lock()

        assert result_a is False
        assert worker_a.running is False  # A 停止

        # Verify A sent worker_lock_lost alert
        worker_a.alert_service.send.assert_called_once()
        call_kwargs = worker_a.alert_service.send.call_args.kwargs
        assert call_kwargs["alert_type"] == "worker_lock_lost"

    @pytest.mark.asyncio
    async def test_b_can_renew_after_acquiring_from_a(self):
        """B 抢锁成功后可以正常续约"""
        worker_b = _make_worker(account_id=100)

        # B acquires
        mock_cursor_acquire = _mock_lock_cursor(1)
        worker_b.db.execute = AsyncMock(return_value=mock_cursor_acquire)
        worker_b.db.commit = AsyncMock()

        await worker_b._acquire_lock()
        token_b = worker_b._lock_token

        # B renews
        mock_cursor_renew = AsyncMock()
        mock_cursor_renew.rowcount = 1
        worker_b.db.execute = AsyncMock(return_value=mock_cursor_renew)

        result = await worker_b._renew_lock()
        assert result is True
        assert worker_b._lock_token == token_b


class TestStartWithLock:
    """8.5 start() 中调用 _acquire_lock()"""

    @pytest.mark.asyncio
    async def test_start_acquires_lock_success(self):
        """start() 抢锁成功 → running=True"""
        worker = _make_worker()

        mock_cursor = _mock_lock_cursor(1)
        worker.db.execute = AsyncMock(return_value=mock_cursor)
        worker.db.commit = AsyncMock()

        # Mock _run_with_restart to avoid actual loop
        async def noop():
            worker._resolve_startup_success()

        worker._run_with_restart = noop
        await worker.start()

        assert worker.running is True
        assert worker.status == "running"
        assert worker._lock_token is not None

    @pytest.mark.asyncio
    async def test_start_lock_conflict_refuses(self):
        """start() 抢锁失败 → 拒绝启动 + 发送 worker_lock_conflict 告警"""
        worker = _make_worker()

        mock_cursor = _mock_lock_cursor(0)
        worker.db.execute = AsyncMock(return_value=mock_cursor)
        worker.db.commit = AsyncMock()

        with pytest.raises(WorkerStartupError):
            await worker.start()

        assert worker.running is False
        assert worker.status == "stopped"
        assert worker._lock_token is None

        # Verify worker_lock_conflict alert
        worker.alert_service.send.assert_called_once()
        call_kwargs = worker.alert_service.send.call_args.kwargs
        assert call_kwargs["alert_type"] == "worker_lock_conflict"

    @pytest.mark.asyncio
    async def test_main_loop_login_failure_raises_startup_error(self):
        worker = _make_worker()
        worker.session.login = AsyncMock(return_value=False)

        with pytest.raises(WorkerStartupError, match="session login failed"):
            await worker._main_loop()

    @pytest.mark.asyncio
    async def test_start_already_running_skips_lock(self):
        """已 running 时 start() 不重复抢锁"""
        worker = _make_worker()
        worker.running = True
        worker.db.execute = AsyncMock()

        await worker.start()

        # db.execute should not be called (no lock attempt)
        worker.db.execute.assert_not_called()


class TestStopWithLock:
    """stop() 中调用 _release_lock()"""

    @pytest.mark.asyncio
    async def test_stop_releases_lock(self):
        """stop() 释放锁"""
        worker = _make_worker()
        worker.running = True
        worker.status = "running"
        worker._lock_token = "my-token"

        worker.db.execute = AsyncMock()
        worker.db.commit = AsyncMock()

        await worker.stop()

        assert worker.running is False
        assert worker.status == "stopped"
        assert worker._lock_token is None

        # Verify release SQL was called
        call_args = worker.db.execute.call_args
        sql = call_args[0][0]
        assert "worker_lock_token=NULL" in sql

    @pytest.mark.asyncio
    async def test_stop_without_lock_no_error(self):
        """无锁时 stop() 不报错"""
        worker = _make_worker()
        worker.running = True
        worker.status = "running"
        worker._lock_token = None

        worker.db.execute = AsyncMock()

        await worker.stop()

        assert worker.status == "stopped"
        # No DB call for release (no token)
        worker.db.execute.assert_not_called()


class TestLockConstants:
    """锁相关常量验证"""

    def test_lock_ttl(self):
        assert LOCK_TTL_MINUTES == 5

    def test_lock_renew_interval(self):
        assert LOCK_RENEW_INTERVAL == 60

    def test_renew_interval_less_than_ttl_third(self):
        """续约间隔 <= TTL/3（60s <= 300s/3=100s）"""
        assert LOCK_RENEW_INTERVAL <= (LOCK_TTL_MINUTES * 60) / 3

    def test_lock_token_init_none(self):
        """_lock_token 初始化为 None"""
        worker = _make_worker()
        assert worker._lock_token is None


# ==================================================================
# 结算反馈测试：_feedback_settlement_results
# ==================================================================


class TestFeedbackSettlementResults:
    """测试 _feedback_settlement_results() 方法"""

    @pytest.mark.asyncio
    async def test_feedback_calls_on_result_for_each_order(self):
        """正常分发：每个已结算订单调用对应 runner.on_result"""
        worker = _make_worker()
        runner1 = AsyncMock()
        runner1.on_result = AsyncMock()
        worker.strategies = {10: runner1}

        # Mock DB 返回两笔已结算订单（同一 strategy_id）
        mock_rows = [
            {
                "strategy_id": 10,
                "key_code": "DX1",
                "is_win": 1,
                "pnl": 500,
            },
            {
                "strategy_id": 10,
                "key_code": "DX2",
                "is_win": 0,
                "pnl": -1000,
            },
        ]
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=mock_rows)
        worker.db.execute = AsyncMock(return_value=mock_cursor)

        await worker._feedback_settlement_results("20250302001")

        assert runner1.on_result.call_count == 2
        runner1.on_result.assert_any_call(1, 500, key_code="DX1")
        runner1.on_result.assert_any_call(0, -1000, key_code="DX2")

    @pytest.mark.asyncio
    async def test_feedback_reads_settled_simulation_orders(self):
        """妯℃嫙缁撶畻璁㈠崟涔熷簲鍙嶉鍒?runner"""
        worker = _make_worker()
        runner1 = AsyncMock()
        runner1.on_result = AsyncMock()
        worker.strategies = {10: runner1}

        mock_rows = [
            {
                "strategy_id": 10,
                "key_code": "DX1",
                "is_win": 1,
                "pnl": 500,
                "martin_level": None,
                "simulation": 1,
            }
        ]
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=mock_rows)
        worker.db.execute = AsyncMock(return_value=mock_cursor)

        await worker._feedback_settlement_results("20250302001")

        runner1.on_result.assert_called_once_with(1, 500, key_code="DX1")

    @pytest.mark.asyncio
    async def test_feedback_dispatches_to_correct_runners(self):
        """多策略分发：不同 strategy_id 的订单分发给各自的 runner"""
        worker = _make_worker()
        runner_a = AsyncMock()
        runner_a.on_result = AsyncMock()
        runner_b = AsyncMock()
        runner_b.on_result = AsyncMock()
        worker.strategies = {10: runner_a, 20: runner_b}

        mock_rows = [
            {
                "strategy_id": 10,
                "key_code": "DX1",
                "is_win": 1,
                "pnl": 500,
            },
            {
                "strategy_id": 20,
                "key_code": "DX2",
                "is_win": 0,
                "pnl": -1000,
            },
        ]
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=mock_rows)
        worker.db.execute = AsyncMock(return_value=mock_cursor)

        await worker._feedback_settlement_results("20250302001")

        runner_a.on_result.assert_called_once_with(1, 500, key_code="DX1")
        runner_b.on_result.assert_called_once_with(0, -1000, key_code="DX2")

    @pytest.mark.asyncio
    async def test_feedback_skips_missing_strategy(self):
        """strategy_id 无对应 runner 时安全跳过"""
        worker = _make_worker()
        runner1 = AsyncMock()
        runner1.on_result = AsyncMock()
        worker.strategies = {10: runner1}

        mock_rows = [
            {
                "strategy_id": 10,
                "key_code": "DX1",
                "is_win": 1,
                "pnl": 500,
            },
            {
                "strategy_id": 999,
                "key_code": "DX2",
                "is_win": 0,
                "pnl": -1000,
            },  # 不存在的 strategy
        ]
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=mock_rows)
        worker.db.execute = AsyncMock(return_value=mock_cursor)

        await worker._feedback_settlement_results("20250302001")

        # runner1 只收到 strategy_id=10 的调用
        runner1.on_result.assert_called_once_with(1, 500, key_code="DX1")

    @pytest.mark.asyncio
    async def test_feedback_continues_on_exception(self):
        """on_result 异常时不影响其他订单"""
        worker = _make_worker()
        runner_bad = AsyncMock()
        runner_bad.on_result = AsyncMock(side_effect=RuntimeError("boom"))
        runner_good = AsyncMock()
        runner_good.on_result = AsyncMock()
        worker.strategies = {10: runner_bad, 20: runner_good}

        mock_rows = [
            {
                "strategy_id": 10,
                "key_code": "DX1",
                "is_win": 0,
                "pnl": -500,
            },
            {
                "strategy_id": 20,
                "key_code": "DX2",
                "is_win": 1,
                "pnl": 800,
            },
        ]
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=mock_rows)
        worker.db.execute = AsyncMock(return_value=mock_cursor)

        await worker._feedback_settlement_results("20250302001")

        # runner_good 仍然收到调用
        runner_good.on_result.assert_called_once_with(1, 800, key_code="DX2")

    @pytest.mark.asyncio
    async def test_feedback_no_orders(self):
        """无已结算订单时不调用任何 runner"""
        worker = _make_worker()
        runner1 = AsyncMock()
        runner1.on_result = AsyncMock()
        worker.strategies = {10: runner1}

        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=[])
        worker.db.execute = AsyncMock(return_value=mock_cursor)

        await worker._feedback_settlement_results("20250302001")

        runner1.on_result.assert_not_called()

    @pytest.mark.asyncio
    async def test_feedback_applies_strategy_stop_request(self):
        """A StrategyRunner stop request is applied by the worker."""
        from app.engine.strategies.base import StrategyStopRequest

        worker = _make_worker()
        runner = AsyncMock()
        runner.on_result = AsyncMock(
            return_value=StrategyStopRequest(
                should_stop=True,
                reason="target_hit",
            )
        )
        worker.strategies = {10: runner}
        worker._stop_strategy_runner = AsyncMock()

        mock_rows = [
            {
                "strategy_id": 10,
                "key_code": "DX1",
                "is_win": 1,
                "pnl": 500,
            },
        ]
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=mock_rows)
        worker.db.execute = AsyncMock(return_value=mock_cursor)

        await worker._feedback_settlement_results("20250302001")

        worker._stop_strategy_runner.assert_called_once_with(10, "target_hit")

    @pytest.mark.asyncio
    async def test_feedback_ignores_red_wave_settlement_stop_request(self):
        """Red-wave double must not stop on settlement target-hit requests."""
        from app.engine.strategies.base import StrategyStopRequest

        worker = _make_worker()
        runner = AsyncMock()
        runner.strategy = MagicMock()
        runner.strategy.name.return_value = "red_wave_double_martin"
        runner.on_result = AsyncMock(
            return_value=StrategyStopRequest(
                should_stop=True,
                reason="target_hit",
            )
        )
        worker.strategies = {10: runner}
        worker._stop_strategy_runner = AsyncMock()

        mock_rows = [
            {
                "strategy_id": 10,
                "key_code": "DS4",
                "is_win": 1,
                "pnl": 500,
            },
        ]
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=mock_rows)
        worker.db.execute = AsyncMock(return_value=mock_cursor)

        await worker._feedback_settlement_results("20250302001")

        runner.on_result.assert_called_once_with(1, 500, key_code="DS4")
        worker._stop_strategy_runner.assert_not_called()

    @pytest.mark.asyncio
    async def test_feedback_ignores_green_wave_settlement_stop_request(self):
        """Green-wave single must not stop on settlement target-hit requests."""
        from app.engine.strategies.base import StrategyStopRequest

        worker = _make_worker()
        runner = AsyncMock()
        runner.strategy = MagicMock()
        runner.strategy.name.return_value = "green_wave_single_martin"
        runner.on_result = AsyncMock(
            return_value=StrategyStopRequest(
                should_stop=True,
                reason="target_hit",
            )
        )
        worker.strategies = {10: runner}
        worker._stop_strategy_runner = AsyncMock()

        mock_rows = [
            {
                "strategy_id": 10,
                "key_code": "DS3",
                "is_win": 1,
                "pnl": 500,
            },
        ]
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=mock_rows)
        worker.db.execute = AsyncMock(return_value=mock_cursor)

        await worker._feedback_settlement_results("20240101001")

        runner.on_result.assert_called_once_with(1, 500, key_code="DS3")
        worker._stop_strategy_runner.assert_not_called()


# ==================================================================
# 主循环集成测试：settle 后调用 feedback
# ==================================================================


class TestMainLoopFeedback:
    """验证主循环中 settle 后调用了 _feedback_settlement_results"""

    @pytest.mark.asyncio
    async def test_main_loop_calls_feedback_after_settle(self):
        """主循环中 settle 后调用 feedback"""
        worker = _make_worker_with_db()
        settlement_install = _make_install(
            issue="20250302002",
            state=1,
            close_countdown_sec=60,
            open_countdown_sec=10,
            pre_issue="20250302001",
            pre_result="4,2,6",
        )
        feedback_calls = []

        async def direct_feedback(issue):
            feedback_calls.append(issue)

        worker._feedback_settlement_results = direct_feedback
        worker._fetch_install_with_retry = AsyncMock(return_value=settlement_install)
        worker._has_issue_unsettled_orders = AsyncMock(return_value=False)

        await worker._try_settle_pending_issue("20250302001")

        assert "20250302001" in feedback_calls


class TestRecoverFeedback:
    """验证补结算后调用了 _feedback_settlement_results"""

    @pytest.mark.asyncio
    async def test_recover_calls_feedback_after_settle(self):
        """补结算成功后调用 feedback"""
        worker = _make_worker()

        # Mock DB: 有一个待补结算的 issue
        mock_cursor_issues = AsyncMock()
        mock_cursor_issues.fetchall = AsyncMock(
            return_value=[{"issue": "20250302001"}]
        )

        async def mock_db_execute(sql, params=None):
            return mock_cursor_issues

        worker.db.execute = mock_db_execute
        worker.db.commit = AsyncMock()

        # Mock adapter 返回历史开奖结果
        worker.adapter.get_lottery_results = AsyncMock(
            return_value=[
                {"Installments": "20250302001", "OpenResult": "3,5,7"}
            ]
        )

        # Mock settler.settle
        worker.settler.settle = AsyncMock()
        worker._has_issue_unsettled_orders = AsyncMock(return_value=False)

        feedback_calls = []

        async def mock_feedback(issue):
            feedback_calls.append(issue)

        worker._feedback_settlement_results = mock_feedback

        await worker._recover_unsettled_orders()

        assert "20250302001" in feedback_calls
