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
)
from app.engine.worker import AccountWorker


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
                 "status": "online", "platform_type": "JND28WEB", "operator_id": 1},
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
                 "status": "online", "platform_type": "JND28WEB", "operator_id": 1},
                {"id": 200, "account_name": "acc2", "password": "pw2",
                 "status": "online", "platform_type": "JND28WEB", "operator_id": 1},
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
