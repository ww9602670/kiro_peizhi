"""EngineManager 


- start_worker / stop_worker
- global_kill_switch / account_kill_switch
- restore_workers_on_startup
- graceful shutdown
- SessionStore / WorkerRegistry 
- 
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.engine.manager import (
    EngineManager,
    InMemorySessionStore,
    InMemoryWorkerRegistry,
    SessionStore,
    WorkerRegistry,
    make_runtime_key,
)
from app.engine.worker import AccountWorker, WorkerStartupError


# 
# Fixtures
# 


def _make_manager(**overrides) -> EngineManager:
    """ mock  EngineManager"""
    defaults = dict(
        db=AsyncMock(),
        session_store=InMemorySessionStore(),
        worker_registry=InMemoryWorkerRegistry(),
        alert_service=AsyncMock(),
    )
    defaults.update(overrides)
    return EngineManager(**defaults)


def _make_mock_worker(
    account_id: int = 100,
    operator_id: int = 1,
    running: bool = True,
    status: str = "running",
) -> AccountWorker:
    """ mock AccountWorker"""
    worker = MagicMock(spec=AccountWorker)
    worker.account_id = account_id
    worker.operator_id = operator_id
    worker.running = running
    worker.status = status
    worker.stop = AsyncMock()
    worker.start = AsyncMock()
    return worker


# 
# SessionStore 
# 


class TestInMemorySessionStore:
    @pytest.mark.asyncio
    async def test_set_and_get(self):
        store = InMemorySessionStore()
        await store.set(1, "token_abc")
        assert await store.get(1) == "token_abc"

    @pytest.mark.asyncio
    async def test_get_missing(self):
        store = InMemorySessionStore()
        assert await store.get(999) is None

    @pytest.mark.asyncio
    async def test_delete(self):
        store = InMemorySessionStore()
        await store.set(1, "token_abc")
        await store.delete(1)
        assert await store.get(1) is None

    @pytest.mark.asyncio
    async def test_delete_missing_no_error(self):
        store = InMemorySessionStore()
        await store.delete(999)  # 

    @pytest.mark.asyncio
    async def test_all_keys(self):
        store = InMemorySessionStore()
        await store.set(1, "a")
        await store.set(2, "b")
        keys = await store.all_keys()
        assert sorted(keys) == [1, 2]


# 
# WorkerRegistry 
# 


class TestInMemoryWorkerRegistry:
    @pytest.mark.asyncio
    async def test_register_and_get(self):
        registry = InMemoryWorkerRegistry()
        worker = _make_mock_worker(account_id=100)
        await registry.register(100, worker)
        assert await registry.get(100) is worker

    @pytest.mark.asyncio
    async def test_get_missing(self):
        registry = InMemoryWorkerRegistry()
        assert await registry.get(999) is None

    @pytest.mark.asyncio
    async def test_unregister(self):
        registry = InMemoryWorkerRegistry()
        worker = _make_mock_worker(account_id=100)
        await registry.register(100, worker)
        removed = await registry.unregister(100)
        assert removed is worker
        assert await registry.get(100) is None

    @pytest.mark.asyncio
    async def test_unregister_missing(self):
        registry = InMemoryWorkerRegistry()
        assert await registry.unregister(999) is None

    @pytest.mark.asyncio
    async def test_all_workers(self):
        registry = InMemoryWorkerRegistry()
        w1 = _make_mock_worker(account_id=100)
        w2 = _make_mock_worker(account_id=200)
        await registry.register(100, w1)
        await registry.register(200, w2)
        all_w = await registry.all_workers()
        assert len(all_w) == 2
        assert all_w[100] is w1
        assert all_w[200] is w2

    @pytest.mark.asyncio
    async def test_count(self):
        registry = InMemoryWorkerRegistry()
        assert await registry.count() == 0
        await registry.register(100, _make_mock_worker())
        assert await registry.count() == 1


# 
# stop_worker 
# 


class TestStopWorker:
    @pytest.mark.asyncio
    async def test_stop_existing_worker(self):
        """ Worker"""
        manager = _make_manager()
        worker = _make_mock_worker(account_id=100)
        worker._has_unsettled_orders = AsyncMock(return_value=False)
        await manager.registry.register(100, worker)

        result = await manager.stop_worker(100)

        assert result is True
        worker.stop.assert_called_once()
        assert await manager.registry.get(100) is None

    @pytest.mark.asyncio
    async def test_stop_nonexistent_worker(self):
        """ Worker  False"""
        manager = _make_manager()
        result = await manager.stop_worker(999)
        assert result is False


# 
# 
# 


class TestGlobalKillSwitch:
    @pytest.mark.asyncio
    async def test_global_kill_stops_all_workers(self):
        """ Worker"""
        manager = _make_manager()
        w1 = _make_mock_worker(account_id=100)
        w2 = _make_mock_worker(account_id=200)
        await manager.registry.register(100, w1)
        await manager.registry.register(200, w2)

        with patch("app.engine.manager.set_global_kill") as mock_set:
            await manager.global_kill_switch()
            mock_set.assert_called_once_with(True)

        w1.stop.assert_called_once()
        w2.stop.assert_called_once()
        #  Worker 
        assert await manager.registry.count() == 0

    @pytest.mark.asyncio
    async def test_global_kill_empty_workers(self):
        """ Worker """
        manager = _make_manager()
        with patch("app.engine.manager.set_global_kill"):
            await manager.global_kill_switch()
        assert await manager.registry.count() == 0


# 
# 
# 


class TestAccountKillSwitch:
    @pytest.mark.asyncio
    async def test_account_kill_stops_worker(self):
        """ Worker"""
        manager = _make_manager()
        worker = _make_mock_worker(account_id=100)
        worker._has_unsettled_orders = AsyncMock(return_value=False)
        await manager.registry.register(100, worker)

        result = await manager.account_kill_switch(100)

        assert result is True
        worker.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_account_kill_nonexistent(self):
        """ Worker  False"""
        manager = _make_manager()
        result = await manager.account_kill_switch(999)
        assert result is False

    @pytest.mark.asyncio
    async def test_account_kill_does_not_affect_others(self):
        """ Worker"""
        manager = _make_manager()
        w1 = _make_mock_worker(account_id=100)
        w1._has_unsettled_orders = AsyncMock(return_value=False)
        w2 = _make_mock_worker(account_id=200)
        await manager.registry.register(100, w1)
        await manager.registry.register(200, w2)

        await manager.account_kill_switch(100)

        w1.stop.assert_called_once()
        w2.stop.assert_not_called()
        assert await manager.registry.get(200) is w2


# 
# 
# 


class TestRestoreWorkersOnStartup:
    @pytest.mark.asyncio
    async def test_restore_online_accounts_with_running_strategies(self):
        """ online  + running """
        manager = _make_manager()

        # Mock db_ops
        with patch("app.engine.manager.db_ops") as mock_ops:
            mock_ops.operator_list_all = AsyncMock(return_value=[
                {"id": 1, "status": "active", "username": "op1"},
            ])
            mock_ops.account_list_by_operator = AsyncMock(return_value=[
                {"id": 100, "account_name": "acc1", "password": "pw1",
                 "status": "online", "platform_type": "JND28WEB", "operator_id": 1,
                 "effective_verification_run_id": 11,
                 "verification_stale": False,
                 "allowed_strategy_platform_types": ["JND28WEB"]},
            ])
            mock_ops.strategy_list_by_operator = AsyncMock(return_value=[
                {"id": 10, "account_id": 100, "status": "running",
                 "type": "flat", "play_code": "DX1", "base_amount": 100,
                 "bet_timing": 30, "simulation": 0},
            ])

            # Mock start_worker to avoid real component creation
            manager.start_worker = AsyncMock(return_value=_make_mock_worker())

            restored = await manager.restore_workers_on_startup()

        assert restored == 1
        manager.start_worker.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip_inactive_operators(self):
        """ active """
        manager = _make_manager()

        with patch("app.engine.manager.db_ops") as mock_ops:
            mock_ops.operator_list_all = AsyncMock(return_value=[
                {"id": 1, "status": "disabled", "username": "op1"},
            ])
            manager.start_worker = AsyncMock()

            restored = await manager.restore_workers_on_startup()

        assert restored == 0
        manager.start_worker.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_offline_accounts(self):
        """ online """
        manager = _make_manager()

        with patch("app.engine.manager.db_ops") as mock_ops:
            mock_ops.operator_list_all = AsyncMock(return_value=[
                {"id": 1, "status": "active", "username": "op1"},
            ])
            mock_ops.account_list_by_operator = AsyncMock(return_value=[
                {"id": 100, "account_name": "acc1", "password": "pw1",
                 "status": "offline", "platform_type": "JND28WEB", "operator_id": 1},
            ])
            mock_ops.strategy_list_by_operator = AsyncMock(return_value=[
                {"id": 10, "account_id": 100, "status": "running",
                 "type": "flat", "play_code": "DX1", "base_amount": 100},
            ])
            manager.start_worker = AsyncMock()

            restored = await manager.restore_workers_on_startup()

        assert restored == 0
        manager.start_worker.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_accounts_without_running_strategies(self):
        """ running """
        manager = _make_manager()

        with patch("app.engine.manager.db_ops") as mock_ops:
            mock_ops.operator_list_all = AsyncMock(return_value=[
                {"id": 1, "status": "active", "username": "op1"},
            ])
            mock_ops.account_list_by_operator = AsyncMock(return_value=[
                {"id": 100, "account_name": "acc1", "password": "pw1",
                 "status": "online", "platform_type": "JND28WEB", "operator_id": 1},
            ])
            mock_ops.strategy_list_by_operator = AsyncMock(return_value=[
                {"id": 10, "account_id": 100, "status": "stopped",
                 "type": "flat", "play_code": "DX1", "base_amount": 100},
            ])
            manager.start_worker = AsyncMock()

            restored = await manager.restore_workers_on_startup()

        assert restored == 0
        manager.start_worker.assert_not_called()

    @pytest.mark.asyncio
    async def test_restore_failure_does_not_block_others(self):
        """"""
        manager = _make_manager()

        call_count = 0

        async def mock_start_worker(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("")
            return _make_mock_worker()

        with patch("app.engine.manager.db_ops") as mock_ops:
            mock_ops.operator_list_all = AsyncMock(return_value=[
                {"id": 1, "status": "active", "username": "op1"},
            ])
            mock_ops.account_list_by_operator = AsyncMock(return_value=[
                {"id": 100, "account_name": "acc1", "password": "pw1",
                 "status": "online", "platform_type": "JND28WEB", "operator_id": 1,
                 "effective_verification_run_id": 11,
                 "verification_stale": False,
                 "allowed_strategy_platform_types": ["JND28WEB"]},
                {"id": 200, "account_name": "acc2", "password": "pw2",
                 "status": "online", "platform_type": "JND28WEB", "operator_id": 1,
                 "effective_verification_run_id": 22,
                 "verification_stale": False,
                 "allowed_strategy_platform_types": ["JND28WEB"]},
            ])
            mock_ops.strategy_list_by_operator = AsyncMock(return_value=[
                {"id": 10, "account_id": 100, "status": "running",
                 "type": "flat", "play_code": "DX1", "base_amount": 100},
                {"id": 20, "account_id": 200, "status": "running",
                 "type": "flat", "play_code": "DX2", "base_amount": 200},
            ])
            manager.start_worker = mock_start_worker

            restored = await manager.restore_workers_on_startup()

        # 
        assert restored == 1


class TestStartWorker:
    @pytest.mark.asyncio
    async def test_start_worker_builds_settler_without_platform_type_kwarg(self):
        manager = _make_manager()
        worker = _make_mock_worker(account_id=321, operator_id=9, running=False)

        with (
            patch("app.engine.manager.create_platform_adapter", return_value=MagicMock()) as mock_adapter_factory,
            patch("app.engine.manager.SessionManager", return_value=MagicMock()) as mock_session,
            patch("app.engine.manager.IssuePoller", return_value=MagicMock()) as mock_poller,
            patch("app.engine.manager.RiskController", return_value=MagicMock()) as mock_risk,
            patch("app.engine.manager.BetExecutor", return_value=MagicMock()) as mock_executor,
            patch("app.engine.manager.SettlementProcessor", return_value=MagicMock()) as mock_settler,
            patch("app.engine.manager.Reconciler", return_value=MagicMock()) as mock_reconciler,
            patch("app.engine.manager.AccountWorker", return_value=worker) as mock_worker_cls,
        ):
            result = await manager.start_worker(
                operator_id=9,
                account_id=321,
                account_name="acc321",
                password="pw321",
                platform_type="JND282",
                platform_url="https://example.test",
                strategies=[],
            )

        assert result is worker
        assert await manager.registry.get(make_runtime_key(321, "JND282")) is worker
        mock_adapter_factory.assert_called_once_with("JND282", "https://example.test")
        mock_session.assert_called_once()
        mock_poller.assert_called_once()
        mock_risk.assert_called_once()
        mock_executor.assert_called_once()
        mock_reconciler.assert_called_once()
        mock_settler.assert_called_once_with(
            db=manager.db,
            operator_id=9,
            account_id=321,
        )
        mock_reconciler.assert_called_once_with(
            db=manager.db,
            adapter=mock_adapter_factory.return_value,
            alert_service=manager.alert_service,
            operator_id=9,
        )
        mock_worker_cls.assert_called_once()
        worker.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_worker_unregisters_failed_worker_startup(self):
        manager = _make_manager()
        worker = _make_mock_worker(account_id=321, operator_id=9, running=False)
        worker.start = AsyncMock(side_effect=WorkerStartupError("startup failed"))

        with (
            patch("app.engine.manager.create_platform_adapter", return_value=MagicMock()),
            patch("app.engine.manager.SessionManager", return_value=MagicMock()),
            patch("app.engine.manager.IssuePoller", return_value=MagicMock()),
            patch("app.engine.manager.RiskController", return_value=MagicMock()),
            patch("app.engine.manager.BetExecutor", return_value=MagicMock()),
            patch("app.engine.manager.SettlementProcessor", return_value=MagicMock()),
            patch("app.engine.manager.Reconciler", return_value=MagicMock()),
            patch("app.engine.manager.AccountWorker", return_value=worker),
        ):
            with pytest.raises(WorkerStartupError):
                await manager.start_worker(
                    operator_id=9,
                    account_id=321,
                    account_name="acc321",
                    password="pw321",
                    platform_type="JND282",
                    platform_url="https://example.test",
                    strategies=[],
                )

        assert await manager.registry.get(make_runtime_key(321, "JND282")) is None

    @pytest.mark.asyncio
    async def test_hot_update_worker_replaces_current_issue_snapshot(self):
        manager = _make_manager()
        worker = _make_mock_worker(account_id=321, operator_id=9, running=True)
        worker.replace_strategy_snapshot = MagicMock()
        runners = {1: MagicMock()}
        profiles = {1: MagicMock()}

        with patch.object(
            manager,
            "_build_strategy_snapshot",
            return_value=(runners, profiles),
        ):
            result = await manager._hot_update_worker(
                worker,
                strategies=[{"id": 1, "status": "running"}],
            )

        assert result is worker
        worker.replace_strategy_snapshot.assert_called_once_with(
            runners,
            profiles,
            apply_next_issue_only=False,
        )


# 
# Graceful Shutdown 
# 


class TestGracefulShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_stops_all_workers(self):
        """shutdown  Worker"""
        manager = _make_manager()
        w1 = _make_mock_worker(account_id=100)
        w2 = _make_mock_worker(account_id=200)
        await manager.registry.register(100, w1)
        await manager.registry.register(200, w2)

        await manager.shutdown()

        w1.stop.assert_called_once()
        w2.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_cancels_health_check(self):
        """shutdown """
        manager = _make_manager()

        # 
        async def slow_loop():
            await asyncio.sleep(100)

        manager._health_check_task = asyncio.create_task(slow_loop())
        await asyncio.sleep(0.01)

        await manager.shutdown()

        assert manager._shutting_down is True
        assert manager._health_check_task.done()

    @pytest.mark.asyncio
    async def test_shutdown_empty_workers(self):
        """ Worker  shutdown """
        manager = _make_manager()
        await manager.shutdown()
        assert manager._shutting_down is True

    @pytest.mark.asyncio
    async def test_shutdown_worker_exception_isolated(self):
        """ Worker  Worker"""
        manager = _make_manager()
        w1 = _make_mock_worker(account_id=100)
        w1.stop = AsyncMock(side_effect=RuntimeError(""))
        w2 = _make_mock_worker(account_id=200)
        await manager.registry.register(100, w1)
        await manager.registry.register(200, w2)

        # 
        await manager.shutdown()

        w1.stop.assert_called_once()
        w2.stop.assert_called_once()


# 
# 
# 


# 
# _build_strategy_runner: registry 濂戠害淇娴嬭瘯
# 


class TestBuildStrategyRunner:
    def test_unknown_strategy_type_returns_none(self):
        """鏈煡绛栫暐绫诲瀷搴旇繑鍥?None 鑰屼笉鏄姏鍑?KeyError"""
        manager = _make_manager()
        strategy_data = {
            "id": 1,
            "type": "nonexistent_strategy",
            "play_code": "DX1",
            "base_amount": 100,
            "status": "running",
        }
        result = manager._build_strategy_runner(strategy_data)
        assert result is None

    def test_flat_strategy_builds_successfully(self):
        """flat strategy builds successfully."""
        manager = _make_manager()
        strategy_data = {
            "id": 1,
            "type": "flat",
            "play_code": "DX1",
            "base_amount": 100,
            "status": "running",
        }
        result = manager._build_strategy_runner(strategy_data)
        assert result is not None
        assert result.strategy_id == 1

    def test_martin_strategy_builds_successfully(self):
        """martin strategy builds successfully."""
        manager = _make_manager()
        strategy_data = {
            "id": 2,
            "type": "martin",
            "play_code": "DX1",
            "base_amount": 100,
            "martin_sequence": "[1, 2, 4, 8]",
            "status": "running",
        }
        result = manager._build_strategy_runner(strategy_data)
        assert result is not None
        assert result.strategy_id == 2


# 
# _create_adapter: Adapter Factory 娴嬭瘯
# 


class TestAdapterFactory:
    def test_jnd28web_creates_jnd_adapter(self):
        """JND28WEB 搴斿垱寤?JNDAdapter"""
        from app.engine.adapters.jnd import JNDAdapter

        manager = _make_manager()
        adapter = manager._create_adapter("JND28WEB")
        assert isinstance(adapter, JNDAdapter)

    def test_jnd282_creates_jnd_adapter(self):
        """JND282 搴斿垱寤?JNDAdapter"""
        from app.engine.adapters.jnd import JNDAdapter

        manager = _make_manager()
        adapter = manager._create_adapter("JND282")
        assert isinstance(adapter, JNDAdapter)

    def test_custom_url_passed_to_adapter(self):
        """鑷畾涔?URL 搴斾紶閫掔粰 adapter"""
        from app.engine.adapters.jnd import JNDAdapter

        manager = _make_manager()
        adapter = manager._create_adapter("JND28WEB", platform_url="https://custom.example.com")
        assert isinstance(adapter, JNDAdapter)

    def test_luckysb_creates_member_site_placeholder(self):
        """LUCKYSB must not be routed to JNDAdapter."""
        from app.engine.adapters.factory import MemberSiteAdapter
        from app.engine.adapters.jnd import JNDAdapter

        manager = _make_manager()
        adapter = manager._create_adapter("LUCKYSB", platform_url="https://member.example.com")

        assert isinstance(adapter, MemberSiteAdapter)
        assert not isinstance(adapter, JNDAdapter)
        assert adapter.platform_type == "LUCKYSB"
        assert adapter.base_url == "https://member.example.com"

    def test_unsupported_platform_raises_error(self):
        """涓嶆敮鎸佺殑骞冲彴绫诲瀷搴旀姏鍑?ValueError"""
        manager = _make_manager()
        with pytest.raises(ValueError, match="Unsupported platform type"):
            manager._create_adapter("UNKNOWN_PLATFORM")


# 
# 
# 


class TestHealthCheck:
    def test_record_account_fail(self):
        """"""
        manager = _make_manager()
        manager.record_account_fail(100)
        manager.record_account_fail(100)
        assert manager._account_fail_counts[100] == 2

    def test_record_bet_fail(self):
        """"""
        manager = _make_manager()
        manager.record_bet_fail(100)
        manager.record_bet_fail(100)
        assert manager._account_consecutive_bet_fails[100] == 2

    def test_reset_bet_fail(self):
        """"""
        manager = _make_manager()
        manager.record_bet_fail(100)
        manager.reset_bet_fail(100)
        assert 100 not in manager._account_consecutive_bet_fails

    def test_reset_account_fail(self):
        """"""
        manager = _make_manager()
        manager.record_account_fail(100)
        manager.reset_account_fail(100)
        assert 100 not in manager._account_fail_counts

    @pytest.mark.asyncio
    async def test_start_health_check(self):
        """"""
        manager = _make_manager()
        manager._shutting_down = True  # 

        await manager.start_health_check(admin_operator_id=1)

        assert manager._health_check_task is not None
        #  _shutting_down=True 
        await asyncio.sleep(0.05)

    @pytest.mark.asyncio
    async def test_health_check_calls_alert_service(self):
        """ AlertService.check_system_health"""
        manager = _make_manager()
        w1 = _make_mock_worker(account_id=100, status="running")
        await manager.registry.register(100, w1)
        manager.record_account_fail(100)
        manager.record_bet_fail(100)

        call_count = 0

        async def mock_sleep(seconds):
            nonlocal call_count
            call_count += 1
            #  sleep 
            manager._shutting_down = True

        with patch("app.engine.manager.asyncio.sleep", side_effect=mock_sleep):
            await manager._health_check_loop(admin_operator_id=1)

        manager.alert_service.check_system_health.assert_called_once()
        call_kwargs = manager.alert_service.check_system_health.call_args
        assert call_kwargs.kwargs["admin_operator_id"] == 1
        assert len(call_kwargs.kwargs["active_accounts"]) == 1
        assert call_kwargs.kwargs["account_fail_counts"] == {100: 1}
        assert call_kwargs.kwargs["account_consecutive_bet_fails"] == {100: 1}
