"""AccountWorker 单元测试

覆盖：
- mock 依赖注入
- 17s/18s/19s 投注时机
- 倒计时驱动主循环
- 异常恢复 5 次后 error
- 补结算 / 全新启动检测
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from app.engine.adapters.base import InstallInfo
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
# 17s/18s/19s
# 


class TestBetTiming:
    """DoDCloseTimeStamp=17s18s19s"""

    def test_17s_skip(self):
        """CloseTimeStamp=17s  """
        worker = _make_worker()
        install = _make_install(close_countdown_sec=17)
        assert worker._should_bet(install) is False

    def test_18s_skip(self):
        """CloseTimeStamp=18s  18s"""
        worker = _make_worker()
        install = _make_install(close_countdown_sec=18)
        assert worker._should_bet(install) is False

    def test_19s_bet(self):
        """CloseTimeStamp=19s  """
        worker = _make_worker()
        install = _make_install(close_countdown_sec=19)
        assert worker._should_bet(install) is True

    def test_60s_bet(self):
        """CloseTimeStamp=60s  """
        worker = _make_worker()
        install = _make_install(close_countdown_sec=60)
        assert worker._should_bet(install) is True

    def test_0s_skip(self):
        """CloseTimeStamp=0s  """
        worker = _make_worker()
        install = _make_install(close_countdown_sec=0)
        assert worker._should_bet(install) is False

    def test_threshold_constant(self):
        """SKIP_THRESHOLD  18"""
        assert SKIP_THRESHOLD == 18


# 
# 
# 


class TestDowntimeHandling:
    @pytest.mark.asyncio
    async def test_state_not_1_skips_betting(self):
        """state!=1 时不投注"""
        worker = _make_worker()
        worker.running = True
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
            if "worker_lock_ts=datetime('now')" in sql and "worker_lock_token=?" in sql:
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
            if "worker_lock_ts=datetime('now')" in sql and "worker_lock_token=?" in sql:
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
            if "worker_lock_ts=datetime('now')" in sql and "worker_lock_token=?" in sql:
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

        # 结算的是当前投注期号 20250302001（从 settlement_install.pre_issue 获取）
        worker.settler.settle.assert_called_once_with(
            issue="20250302001",
            balls=[4, 2, 6],
            sum_value=12,
            platform_type="JND28WEB",
            adapter=worker.adapter,
        )

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
            if "worker_lock_ts=datetime('now')" in sql and "worker_lock_token=?" in sql:
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

        worker.reconciler.reconcile.assert_called_once_with(
            issue="20250302001",
            account_id=100,
        )

    @pytest.mark.asyncio
    async def test_skip_threshold_no_execute(self):
        """CloseTimeStamp <= 18s 时不调用 executor"""
        worker = _make_worker()
        worker.running = True
        worker._lock_token = "test-lock"

        betting_install = _make_install(
            close_countdown_sec=18,
            state=1,
            open_countdown_sec=5,
            pre_issue="20250302000",
            pre_result="3,5,7",
        )
        settlement_install = _make_install(
            issue="20250302002",
            state=1,
            close_countdown_sec=18,
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
            if "worker_lock_ts=datetime('now')" in sql and "worker_lock_token=?" in sql:
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
        worker.running = True
        worker._lock_token = "test-lock"

        betting_install = _make_install(
            issue="20250302001",
            state=1,
            open_countdown_sec=5,
            pre_issue="20250302000",
            pre_result="1,2,3",
        )
        settlement_install = _make_install(
            issue="20250302002",
            state=1,
            open_countdown_sec=5,
            pre_issue="20250302001",
            pre_result="4,5,6",
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
            if "worker_lock_ts=datetime('now')" in sql and "worker_lock_token=?" in sql:
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

        worker.settler.settle.assert_called_once_with(
            issue="20250302001",
            balls=[4, 5, 6],
            sum_value=15,
            platform_type="JND282",
            adapter=worker.adapter,
        )

    @pytest.mark.asyncio
    async def test_reconciler_uses_correct_account_id(self):
        """ Worker  account_id"""
        worker = _make_worker(account_id=999)
        worker.running = True
        worker._lock_token = "test-lock"

        betting_install = _make_install(
            issue="20250302001",
            state=1,
            open_countdown_sec=5,
            pre_issue="20250302000",
            pre_result="1,2,3",
        )
        settlement_install = _make_install(
            issue="20250302002",
            state=1,
            open_countdown_sec=5,
            pre_issue="20250302001",
            pre_result="4,5,6",
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
            if "worker_lock_ts=datetime('now')" in sql and "worker_lock_token=?" in sql:
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
        mock_cursor = AsyncMock()
        mock_cursor.rowcount = 1
        worker.db.execute = AsyncMock(return_value=mock_cursor)
        worker.db.commit = AsyncMock()

        # Mock _run_with_restart to avoid actual loop
        async def noop():
            pass

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

        worker.add_strategy(1, runner)
        assert 1 in worker.strategies

        worker.remove_strategy(1)
        assert 1 not in worker.strategies

        # 
        worker.remove_strategy(999)


# 
# 
# 


class TestSignalCollection:
    def test_collect_signals_from_running_strategies(self):
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
        signals = worker._collect_signals(install)

        assert len(signals) == 1
        assert signals[0].key_code == "DX1"

    def test_collect_signals_exception_isolated(self):
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
        signals = worker._collect_signals(install)

        #  runner_ok 
        assert len(signals) == 1
        assert signals[0].strategy_id == 2


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
# PBT: P27  18s 
# 


class TestPBT_P27_SkipThreshold:
    """P27: CloseTimeStamp 18  skip; > 18  bet.

    **Validates: Requirements 5.1**

    Property: For any CloseTimeStamp value, _should_bet returns False
    when close_countdown_sec <= SKIP_THRESHOLD (18), and True otherwise.
    """

    @given(close_countdown_sec=st.integers(min_value=0, max_value=18))
    @settings(max_examples=100)
    def test_pbt_skip_when_lte_18(self, close_countdown_sec: int):
        """CloseTimeStamp 18  _should_bet returns False.

        **Validates: Requirements 5.1**
        """
        worker = _make_worker()
        install = _make_install(close_countdown_sec=close_countdown_sec)
        assert worker._should_bet(install) is False, (
            f"Expected skip for close_countdown_sec={close_countdown_sec}, "
            f"but _should_bet returned True"
        )

    @given(close_countdown_sec=st.integers(min_value=19, max_value=300))
    @settings(max_examples=100)
    def test_pbt_bet_when_gt_18(self, close_countdown_sec: int):
        """CloseTimeStamp > 18  _should_bet returns True.

        **Validates: Requirements 5.1**
        """
        worker = _make_worker()
        install = _make_install(close_countdown_sec=close_countdown_sec)
        assert worker._should_bet(install) is True, (
            f"Expected bet for close_countdown_sec={close_countdown_sec}, "
            f"but _should_bet returned False"
        )


# ==================================================================
# Helper: 创建带 mock DB 的 worker（用于主循环测试）
# ==================================================================

def _make_worker_with_db(has_records: bool = True, **overrides) -> AccountWorker:
    """创建带 mock DB 的 worker，简化主循环测试"""
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
        if "worker_lock_ts=datetime('now')" in sql and "worker_lock_token=?" in sql:
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
        assert sleep_args[0] == 15  # open_countdown_sec
        assert sleep_args[1] == SETTLEMENT_WAIT_SECONDS_DEFAULT  # settlement_wait

        # Verify settle was called with the betting issue (20250302001)
        worker.settler.settle.assert_called_once_with(
            issue="20250302001",
            balls=[4, 2, 6],
            sum_value=12,
            platform_type="JND28WEB",
            adapter=worker.adapter,
        )

        # Verify reconcile was called
        worker.reconciler.reconcile.assert_called_once_with(
            issue="20250302001",
            account_id=100,
        )

        # Verify lottery result saved
        worker.settler._save_lottery_result.assert_called_once_with(
            "20250302001", "4,2,6", 12,
        )


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
        assert sleep_args[0] == SETTLEMENT_WAIT_SECONDS_DEFAULT
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
        assert sleep_args[0] == SETTLEMENT_WAIT_SECONDS_DEFAULT


class TestSettlementDataRetry:
    """7.7.3 PreLotteryResult 重试 6 次后降级处理"""

    @pytest.mark.asyncio
    async def test_retry_6_times_then_degrade(self):
        """重试 6 次后 real 订单 settle_failed + sim 降级 + 发告警"""
        worker = _make_worker_with_db()
        worker.running = True

        # 投注阶段返回正常 install
        betting_install = _make_install(
            issue="20250302001",
            state=1,
            open_countdown_sec=0,
            pre_issue="20250302000",
            pre_result="3,5,7",
        )
        # 结算阶段返回的 install：pre_result 为空（模拟开奖数据缺失）
        # pre_issue 不匹配 expected（20250302001），或 pre_result 为空
        settlement_install = _make_install(
            issue="20250302002",
            state=1,
            open_countdown_sec=0,
            pre_issue="20250302001",
            pre_result="",  # empty — 开奖结果缺失
        )

        poll_count = 0

        async def mock_poll():
            nonlocal poll_count
            poll_count += 1
            return betting_install if poll_count <= 1 else settlement_install

        worker.poller.poll = mock_poll

        # Mock DB for _handle_settlement_data_missing
        mock_orders = [
            {"id": 1, "simulation": 0, "status": "bet_success"},
            {"id": 2, "simulation": 1, "status": "bet_success"},
        ]
        mock_cursor_orders = AsyncMock()
        mock_cursor_orders.fetchall = AsyncMock(return_value=[
            MagicMock(**{"__getitem__": lambda s, k: o[k], "keys": lambda s: o.keys(), **{f"__{k}__": None for k in []}})
            for o in mock_orders
        ])

        # We need to handle multiple DB calls
        call_idx = 0
        mock_cursor_count = AsyncMock()
        mock_cursor_count.fetchone = AsyncMock(return_value={"cnt": 1})
        mock_cursor_empty = AsyncMock()
        mock_cursor_empty.fetchall = AsyncMock(return_value=[])

        # Create proper dict-like rows
        class DictRow:
            def __init__(self, d):
                self._d = d
            def __getitem__(self, key):
                return self._d[key]
            def keys(self):
                return self._d.keys()

        mock_cursor_with_orders = AsyncMock()
        mock_cursor_with_orders.fetchall = AsyncMock(return_value=[
            DictRow({"id": 1, "simulation": 0, "status": "bet_success", "account_id": 100,
                     "key_code": "DX1", "amount": 1000, "odds": 19800, "strategy_id": 1}),
            DictRow({"id": 2, "simulation": 1, "status": "bet_success", "account_id": 100,
                     "key_code": "DX2", "amount": 500, "odds": 19800, "strategy_id": 1}),
        ])

        mock_cursor_lock = AsyncMock()
        mock_cursor_lock.rowcount = 1

        async def mock_db_execute(sql, params=None):
            if "COUNT" in sql:
                return mock_cursor_count
            if "worker_lock_ts=datetime('now')" in sql and "worker_lock_token=?" in sql:
                return mock_cursor_lock
            if "DISTINCT issue" in sql:
                return mock_cursor_empty
            if "bet_orders" in sql and "status='bet_success'" in sql:
                return mock_cursor_with_orders
            return mock_cursor_empty

        worker.db.execute = mock_db_execute

        sleep_count = 0

        async def mock_sleep(seconds):
            nonlocal sleep_count
            sleep_count += 1
            # After all retries + settlement_wait, stop
            if sleep_count > SETTLE_DATA_RETRY_MAX + 2:
                worker.running = False

        with patch("app.engine.worker.asyncio.sleep", side_effect=mock_sleep):
            await worker._main_loop()

        # Verify settlement_data_missing alert was sent
        alert_calls = worker.alert_service.send.call_args_list
        alert_types = [c.kwargs.get("alert_type") or c[1].get("alert_type", "") for c in alert_calls]
        assert "settlement_data_missing" in alert_types

        # Verify _mark_orders_settle_failed was called
        worker.settler._mark_orders_settle_failed.assert_called()


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

        with patch("app.engine.worker.asyncio.sleep", new_callable=AsyncMock):
            await worker._recover_unsettled_orders()

        # Verify settle_data_expired alert
        alert_calls = worker.alert_service.send.call_args_list
        alert_types = [c.kwargs.get("alert_type") for c in alert_calls]
        assert "settle_data_expired" in alert_types

        # Verify _mark_orders_settle_failed was called
        worker.settler._mark_orders_settle_failed.assert_called()


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
        install = _make_install(issue="20250302005")
        worker.poller.poll = AsyncMock(return_value=install)

        await worker._detect_fresh_start()

        # Verify last_issue was set
        assert worker.poller.last_issue == "20250302005"

        # Verify settle was NOT called
        worker.settler.settle.assert_not_called()

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

        mock_cursor = AsyncMock()
        mock_cursor.rowcount = 1
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
        assert "datetime('now', '-5 minutes')" in sql

    @pytest.mark.asyncio
    async def test_acquire_lock_fail_active_lock(self):
        """已有活跃锁时抢锁失败，返回 False，_lock_token 保持 None"""
        worker = _make_worker()

        mock_cursor = AsyncMock()
        mock_cursor.rowcount = 0  # CAS 失败
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

        mock_cursor = AsyncMock()
        mock_cursor.rowcount = 1
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
        assert params == (worker.account_id, "test-token-123")

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
        mock_cursor_acquire = AsyncMock()
        mock_cursor_acquire.rowcount = 1
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
        mock_cursor = AsyncMock()
        mock_cursor.rowcount = 1
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

        mock_cursor = AsyncMock()
        mock_cursor.rowcount = 0  # 旧锁仍活跃
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
        worker._lock_token = "my-token"

        # _renew_lock fails
        mock_cursor_renew = AsyncMock()
        mock_cursor_renew.rowcount = 0
        original_db_execute = worker.db.execute

        async def mock_db_execute(sql, params=None):
            if "worker_lock_ts=datetime('now')" in sql and "worker_lock_token=?" in sql:
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
        mock_cursor_b = AsyncMock()
        mock_cursor_b.rowcount = 1
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
        mock_cursor_acquire = AsyncMock()
        mock_cursor_acquire.rowcount = 1
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

        mock_cursor = AsyncMock()
        mock_cursor.rowcount = 1
        worker.db.execute = AsyncMock(return_value=mock_cursor)
        worker.db.commit = AsyncMock()

        # Mock _run_with_restart to avoid actual loop
        async def noop():
            pass

        worker._run_with_restart = noop

        await worker.start()

        assert worker.running is True
        assert worker.status == "running"
        assert worker._lock_token is not None

    @pytest.mark.asyncio
    async def test_start_lock_conflict_refuses(self):
        """start() 抢锁失败 → 拒绝启动 + 发送 worker_lock_conflict 告警"""
        worker = _make_worker()

        mock_cursor = AsyncMock()
        mock_cursor.rowcount = 0  # 抢锁失败
        worker.db.execute = AsyncMock(return_value=mock_cursor)
        worker.db.commit = AsyncMock()

        await worker.start()

        assert worker.running is False
        assert worker.status == "stopped"
        assert worker._lock_token is None

        # Verify worker_lock_conflict alert
        worker.alert_service.send.assert_called_once()
        call_kwargs = worker.alert_service.send.call_args.kwargs
        assert call_kwargs["alert_type"] == "worker_lock_conflict"

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
