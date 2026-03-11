"""EngineManager  

Phase 10.2:  AccountWorker 


  - start_worker / stop_worker/ AccountWorker 
  - global_kill_switch Worker + 
  - account_kill_switch Worker
  - restore_workers_on_startup online  Worker
  -  30%+  /  5 


  - SessionStore  + InMemorySessionStore
  - WorkerRegistry  + InMemoryWorkerRegistry
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

import aiosqlite

from app.engine.adapters.base import PlatformAdapter
from app.engine.adapters.jnd import JNDAdapter
from app.engine.alert import AlertService
from app.engine.executor import BetExecutor
from app.engine.kill_switch import get_global_kill, set_global_kill
from app.engine.poller import IssuePoller
from app.engine.rate_limiter import RateLimiter
from app.engine.reconciler import Reconciler
from app.engine.risk import RiskController
from app.engine.session import SessionManager
from app.engine.settlement import SettlementProcessor
from app.engine.strategy_runner import StrategyRunner
from app.engine.worker import AccountWorker
from app.models import db_ops
from app.utils.captcha import CaptchaService

logger = logging.getLogger(__name__)


# 
#  Redis 
# 


class SessionStore(ABC):
    """ InMemory Redis"""

    @abstractmethod
    async def get(self, account_id: int) -> Optional[str]:
        """ session token"""
        ...

    @abstractmethod
    async def set(self, account_id: int, token: str) -> None:
        """ session token"""
        ...

    @abstractmethod
    async def delete(self, account_id: int) -> None:
        """ session token"""
        ...

    @abstractmethod
    async def all_keys(self) -> list[int]:
        """ session  account_id"""
        ...


class InMemorySessionStore(SessionStore):
    """"""

    def __init__(self) -> None:
        self._store: dict[int, str] = {}

    async def get(self, account_id: int) -> Optional[str]:
        return self._store.get(account_id)

    async def set(self, account_id: int, token: str) -> None:
        self._store[account_id] = token

    async def delete(self, account_id: int) -> None:
        self._store.pop(account_id, None)

    async def all_keys(self) -> list[int]:
        return list(self._store.keys())


class WorkerRegistry(ABC):
    """Worker  InMemory Redis"""

    @abstractmethod
    async def register(self, account_id: int, worker: AccountWorker) -> None:
        """ Worker"""
        ...

    @abstractmethod
    async def unregister(self, account_id: int) -> Optional[AccountWorker]:
        """ Worker Worker"""
        ...

    @abstractmethod
    async def get(self, account_id: int) -> Optional[AccountWorker]:
        """ Worker"""
        ...

    @abstractmethod
    async def all_workers(self) -> dict[int, AccountWorker]:
        """ Worker"""
        ...

    @abstractmethod
    async def count(self) -> int:
        """ Worker """
        ...


class InMemoryWorkerRegistry(WorkerRegistry):
    """ Worker """

    def __init__(self) -> None:
        self._workers: dict[int, AccountWorker] = {}

    async def register(self, account_id: int, worker: AccountWorker) -> None:
        self._workers[account_id] = worker

    async def unregister(self, account_id: int) -> Optional[AccountWorker]:
        return self._workers.pop(account_id, None)

    async def get(self, account_id: int) -> Optional[AccountWorker]:
        return self._workers.get(account_id)

    async def all_workers(self) -> dict[int, AccountWorker]:
        return dict(self._workers)

    async def count(self) -> int:
        return len(self._workers)


# 
# EngineManager
# 

# 
HEALTH_CHECK_INTERVAL = 60


class EngineManager:
    """

     AccountWorker /
     online  Worker

    Args:
        db: 
        session_store:  InMemory
        worker_registry: Worker  InMemory
        alert_service: 
    """

    def __init__(
        self,
        *,
        db: aiosqlite.Connection,
        session_store: Optional[SessionStore] = None,
        worker_registry: Optional[WorkerRegistry] = None,
        alert_service: Optional[AlertService] = None,
    ) -> None:
        self.db = db
        self.session_store = session_store or InMemorySessionStore()
        self.registry = worker_registry or InMemoryWorkerRegistry()
        self.alert_service = alert_service or AlertService(db)

        # 
        self._account_fail_counts: dict[int, int] = {}
        self._account_consecutive_bet_fails: dict[int, int] = {}
        self._health_check_task: Optional[asyncio.Task] = None
        self._shutting_down: bool = False

    # ------------------------------------------------------------------
    # Worker 
    # ------------------------------------------------------------------

    async def start_worker(
        self,
        *,
        operator_id: int,
        account_id: int,
        account_name: str,
        password: str,
        platform_type: str = "JND28WEB",
        strategies: Optional[list[dict[str, Any]]] = None,
    ) -> AccountWorker:
        """ AccountWorker

         account_id  Worker
        """
        logger.info(
            "  Workeroperator_id=%d account_id=%d strategies_count=%d",
            operator_id,
            account_id,
            len(strategies) if strategies else 0,
        )
        
        #  Worker
        existing = await self.registry.get(account_id)
        if existing and existing.running:
            logger.info(" Workeraccount_id=%d", account_id)
            await existing.stop()
            await self.registry.unregister(account_id)

        # 
        adapter = JNDAdapter(platform_type=platform_type)
        rate_limiter = RateLimiter()
        captcha_service = CaptchaService()
        session = SessionManager(
            adapter=adapter,
            alert_service=self.alert_service,
            captcha_service=captcha_service,
            operator_id=operator_id,
            account_id=account_id,
            account_name=account_name,
            password=password,
            db=self.db,  #   db 
        )
        poller = IssuePoller(adapter=adapter, rate_limiter=rate_limiter)
        risk = RiskController(
            db=self.db,
            alert_service=self.alert_service,
            operator_id=operator_id,
            account_id=account_id,
        )
        executor = BetExecutor(
            db=self.db,
            adapter=adapter,
            risk=risk,
            alert_service=self.alert_service,
            operator_id=operator_id,
            account_id=account_id,
        )
        settler = SettlementProcessor(
            db=self.db,
            operator_id=operator_id,
        )
        reconciler = Reconciler(
            db=self.db,
            adapter=adapter,
            alert_service=self.alert_service,
            operator_id=operator_id,
        )

        # 
        strategy_runners: dict[int, StrategyRunner] = {}
        if strategies:
            for s in strategies:
                runner = self._build_strategy_runner(s)
                if runner:
                    strategy_runners[s["id"]] = runner

        worker = AccountWorker(
            operator_id=operator_id,
            account_id=account_id,
            db=self.db,
            adapter=adapter,
            session=session,
            poller=poller,
            executor=executor,
            settler=settler,
            reconciler=reconciler,
            risk=risk,
            alert_service=self.alert_service,
            strategies=strategy_runners,
            bet_timing=strategies[0].get("bet_timing", 30) if strategies else 30,
            platform_type=platform_type,
        )

        await self.registry.register(account_id, worker)
        await worker.start()

        logger.info(
            " Worker operator_id=%d account_id=%d strategies=%d",
            operator_id,
            account_id,
            len(strategy_runners),
        )
        return worker

    async def stop_worker(self, account_id: int) -> bool:
        """ AccountWorker"""
        worker = await self.registry.unregister(account_id)
        if worker is None:
            return False
        await worker.stop()
        await self.session_store.delete(account_id)
        logger.info("Worker account_id=%d", account_id)
        return True

    # ------------------------------------------------------------------
    # 
    # ------------------------------------------------------------------

    async def global_kill_switch(self) -> None:
        """ +  Worker"""
        set_global_kill(True)
        workers = await self.registry.all_workers()
        for account_id, worker in workers.items():
            try:
                await worker.stop()
                await self.registry.unregister(account_id)
            except Exception:
                logger.exception(" Worker account_id=%d", account_id)
        logger.warning("stopped_workers=%d", len(workers))

    async def account_kill_switch(self, account_id: int) -> bool:
        """ Worker"""
        stopped = await self.stop_worker(account_id)
        if stopped:
            logger.warning("account_id=%d", account_id)
        return stopped

    # ------------------------------------------------------------------
    # 
    # ------------------------------------------------------------------

    async def restore_workers_on_startup(self) -> int:
        """ online  Worker

         status='online'  + running  Worker

        Returns:
             Worker 
        """
        restored = 0
        operators = await db_ops.operator_list_all(self.db)

        for op in operators:
            if op.get("status") != "active":
                continue
            operator_id = op["id"]
            accounts = await db_ops.account_list_by_operator(
                self.db, operator_id=operator_id
            )
            for acc in accounts:
                if acc.get("status") != "online":
                    continue
                #  running 
                all_strategies = await db_ops.strategy_list_by_operator(
                    self.db, operator_id=operator_id
                )
                running_strategies = [
                    s for s in all_strategies
                    if s.get("account_id") == acc["id"]
                    and s.get("status") == "running"
                ]
                if not running_strategies:
                    continue

                try:
                    await self.start_worker(
                        operator_id=operator_id,
                        account_id=acc["id"],
                        account_name=acc["account_name"],
                        password=acc["password"],
                        platform_type=acc.get("platform_type", "JND28WEB"),
                        strategies=running_strategies,
                    )
                    restored += 1
                except Exception:
                    logger.exception(
                        " Worker operator_id=%d account_id=%d",
                        operator_id,
                        acc["id"],
                    )

        logger.info("restored_workers=%d", restored)
        return restored

    # ------------------------------------------------------------------
    # 
    # ------------------------------------------------------------------

    async def start_health_check(self, admin_operator_id: int = 1) -> None:
        """"""
        if self._health_check_task and not self._health_check_task.done():
            return
        self._health_check_task = asyncio.create_task(
            self._health_check_loop(admin_operator_id)
        )

    async def _health_check_loop(self, admin_operator_id: int) -> None:
        """"""
        while not self._shutting_down:
            try:
                workers = await self.registry.all_workers()
                active_accounts = []
                for account_id, worker in workers.items():
                    active_accounts.append({"id": account_id, "status": worker.status})

                await self.alert_service.check_system_health(
                    admin_operator_id=admin_operator_id,
                    active_accounts=active_accounts,
                    account_fail_counts=self._account_fail_counts,
                    account_consecutive_bet_fails=self._account_consecutive_bet_fails,
                )
            except Exception:
                logger.exception("")

            await asyncio.sleep(HEALTH_CHECK_INTERVAL)

    def record_account_fail(self, account_id: int) -> None:
        """ Worker """
        self._account_fail_counts[account_id] = (
            self._account_fail_counts.get(account_id, 0) + 1
        )

    def record_bet_fail(self, account_id: int) -> None:
        """ Worker """
        self._account_consecutive_bet_fails[account_id] = (
            self._account_consecutive_bet_fails.get(account_id, 0) + 1
        )

    def reset_bet_fail(self, account_id: int) -> None:
        """"""
        self._account_consecutive_bet_fails.pop(account_id, None)

    def reset_account_fail(self, account_id: int) -> None:
        """"""
        self._account_fail_counts.pop(account_id, None)

    # ------------------------------------------------------------------
    # Graceful Shutdown
    # ------------------------------------------------------------------

    async def shutdown(self) -> None:
        """ Worker + """
        self._shutting_down = True

        # 
        if self._health_check_task and not self._health_check_task.done():
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass

        #  Worker
        workers = await self.registry.all_workers()
        stop_tasks = []
        for account_id, worker in workers.items():
            stop_tasks.append(self._stop_worker_safe(account_id, worker))

        if stop_tasks:
            await asyncio.gather(*stop_tasks)

        logger.info("EngineManager stopped_workers=%d", len(workers))

    async def _stop_worker_safe(self, account_id: int, worker: AccountWorker) -> None:
        """ Worker"""
        try:
            await worker.stop()
            await self.registry.unregister(account_id)
        except Exception:
            logger.exception(" Worker account_id=%d", account_id)

    # ------------------------------------------------------------------
    # 
    # ------------------------------------------------------------------

    def _build_strategy_runner(self, strategy_data: dict[str, Any]) -> Optional[StrategyRunner]:
        """ StrategyRunner"""
        from app.engine.strategies.registry import get_strategy_class

        strategy_type = strategy_data.get("type", "flat")
        strategy_cls = get_strategy_class(strategy_type)
        if strategy_cls is None:
            logger.warning("type=%s", strategy_type)
            return None

        # 
        play_code = strategy_data.get("play_code", "DX1")
        base_amount = strategy_data.get("base_amount", 100)
        
        if strategy_type == "flat":
            # key_codes 
            kwargs: dict[str, Any] = {
                "key_codes": [play_code],
                "base_amount": base_amount,
            }
        elif strategy_type == "martin":
            # key_codes  sequence
            kwargs: dict[str, Any] = {
                "key_codes": [play_code],
                "base_amount": base_amount,
            }
            if strategy_data.get("martin_sequence"):
                seq_str = strategy_data["martin_sequence"]
                if isinstance(seq_str, str):
                    import json as _json
                    try:
                        parsed = _json.loads(seq_str)
                        kwargs["sequence"] = [float(x) for x in parsed]
                    except (ValueError, TypeError):
                        kwargs["sequence"] = [float(x.strip()) for x in seq_str.split(",")]
                elif isinstance(seq_str, list):
                    kwargs["sequence"] = [float(x) for x in seq_str]
            else:
                # 
                logger.warning(" martin_sequencestrategy_id=%s", strategy_data.get("id"))
                return None
        else:
            logger.warning("type=%s", strategy_type)
            return None

        strategy_instance = strategy_cls(**kwargs)

        runner = StrategyRunner(
            strategy_id=strategy_data["id"],
            strategy=strategy_instance,
            simulation=bool(strategy_data.get("simulation", 0)),
        )
        #  running runner
        if strategy_data.get("status") == "running":
            runner.start()

        return runner
