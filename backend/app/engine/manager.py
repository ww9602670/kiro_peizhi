"""Engine manager for account-platform workers."""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

import aiosqlite

from app.config import (
    BOCAI_SHARED_DETECTOR_ACCOUNT,
    BOCAI_SHARED_DETECTOR_PASSWORD,
)
from app.engine.adapters.base import PlatformAdapter
from app.engine.adapters.factory import create_platform_adapter
from app.engine.alert import AlertService
from app.engine.executor import BetExecutor
from app.engine.kill_switch import set_global_kill
from app.engine.poller import IssuePoller
from app.engine.rate_limiter import RateLimiter
from app.engine.reconciler import Reconciler
from app.engine.risk import RiskController
from app.engine.session_runtime import AccountSessionRuntime, SessionRuntimeRegistry
from app.engine.settlement import SettlementProcessor
from app.engine.shared_market_runtime import SharedMarketRuntime
from app.engine.strategy_runner import StrategyRunner
from app.engine.worker import AccountWorker, StrategyRuntimeProfile
from app.models import db_ops
from app.utils.strategy_timing import (
    BET_TIMING_MAX,
    BET_TIMING_MIN,
    DEFAULT_BET_TIMING,
    WAVE_STRATEGY_TYPES,
    normalize_direction_keys,
)

logger = logging.getLogger(__name__)

RuntimeKey = tuple[int, str]
RuntimeKeyLike = RuntimeKey | int
HEALTH_CHECK_INTERVAL = 60


def _is_dw3_group_token(token: str) -> bool:
    return token.startswith("DW3_BS_") or token.startswith("DW3_OE_")


def make_runtime_key(account_id: int, platform_type: str) -> RuntimeKey:
    return account_id, (platform_type or "JND28WEB").upper()


def make_shared_market_owner_key(account_id: int, platform_type: str) -> str:
    return f"{account_id}:{(platform_type or 'JND28WEB').upper()}"


def _is_truthy_flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


def _parse_effective_verification_run_id(account: dict[str, Any]) -> int | None:
    raw = account.get("effective_verification_run_id")
    if raw is None:
        return None
    try:
        run_id = int(raw)
    except (TypeError, ValueError):
        return None
    return run_id if run_id > 0 else None


def _parse_allowed_platform_types(value: object) -> set[str]:
    if value is None:
        return set()
    raw_items: list[object]
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return set()
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError:
            raw_items = [item.strip() for item in stripped.split(",")]
        else:
            raw_items = decoded if isinstance(decoded, list) else [decoded]
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = [value]

    return {
        str(raw or "").strip().upper()
        for raw in raw_items
        if str(raw or "").strip().upper() in {"JND28WEB", "JND282", "LUCKYSB"}
    }


def _is_platform_verified_for_restore(account: dict[str, Any], platform_type: str) -> bool:
    allowed_platforms = _parse_allowed_platform_types(account.get("allowed_strategy_platform_types"))
    normalized_platform = (platform_type or "").upper()
    if allowed_platforms:
        return normalized_platform in allowed_platforms
    if _is_truthy_flag(account.get("verification_stale")):
        return True
    return _parse_effective_verification_run_id(account) is not None


def _is_detector_betting_account(account: dict[str, Any]) -> bool:
    detector_account = (BOCAI_SHARED_DETECTOR_ACCOUNT or "").strip()
    detector_password = (BOCAI_SHARED_DETECTOR_PASSWORD or "").strip()
    if not detector_account:
        return False
    if str(account.get("account_name") or "").strip() != detector_account:
        return False
    if not detector_password:
        return True
    return str(account.get("password") or "").strip() == detector_password


def _runtime_key_matches_account(runtime_key: RuntimeKeyLike, account_id: int) -> bool:
    if isinstance(runtime_key, tuple):
        return runtime_key[0] == account_id
    return runtime_key == account_id


def _runtime_key_parts(
    runtime_key: RuntimeKeyLike,
    worker: AccountWorker | None = None,
) -> RuntimeKey:
    if isinstance(runtime_key, tuple):
        return make_runtime_key(runtime_key[0], runtime_key[1])

    platform_type = (
        getattr(worker, "_platform_type", None)
        or getattr(worker, "platform_type", None)
        or "JND28WEB"
    )
    return make_runtime_key(runtime_key, str(platform_type))


class SessionStore(ABC):
    @abstractmethod
    async def get(self, runtime_key: RuntimeKey) -> Optional[str]:
        ...

    @abstractmethod
    async def set(self, runtime_key: RuntimeKey, token: str) -> None:
        ...

    @abstractmethod
    async def delete(self, runtime_key: RuntimeKey) -> None:
        ...

    @abstractmethod
    async def all_keys(self) -> list[RuntimeKey]:
        ...


class InMemorySessionStore(SessionStore):
    def __init__(self) -> None:
        self._store: dict[RuntimeKey, str] = {}

    async def get(self, runtime_key: RuntimeKey) -> Optional[str]:
        return self._store.get(runtime_key)

    async def set(self, runtime_key: RuntimeKey, token: str) -> None:
        self._store[runtime_key] = token

    async def delete(self, runtime_key: RuntimeKey) -> None:
        self._store.pop(runtime_key, None)

    async def all_keys(self) -> list[RuntimeKey]:
        return list(self._store.keys())


class WorkerRegistry(ABC):
    @abstractmethod
    async def register(self, runtime_key: RuntimeKey, worker: AccountWorker) -> None:
        ...

    @abstractmethod
    async def unregister(self, runtime_key: RuntimeKey) -> Optional[AccountWorker]:
        ...

    @abstractmethod
    async def get(self, runtime_key: RuntimeKey) -> Optional[AccountWorker]:
        ...

    @abstractmethod
    async def all_workers(self) -> dict[RuntimeKey, AccountWorker]:
        ...

    @abstractmethod
    async def count(self) -> int:
        ...


class InMemoryWorkerRegistry(WorkerRegistry):
    def __init__(self) -> None:
        self._workers: dict[RuntimeKey, AccountWorker] = {}

    async def register(self, runtime_key: RuntimeKey, worker: AccountWorker) -> None:
        self._workers[runtime_key] = worker

    async def unregister(self, runtime_key: RuntimeKey) -> Optional[AccountWorker]:
        return self._workers.pop(runtime_key, None)

    async def get(self, runtime_key: RuntimeKey) -> Optional[AccountWorker]:
        return self._workers.get(runtime_key)

    async def all_workers(self) -> dict[RuntimeKey, AccountWorker]:
        return dict(self._workers)

    async def count(self) -> int:
        return len(self._workers)


class EngineManager:
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
        self.shared_market_runtime = SharedMarketRuntime(db=self.db)
        self._account_fail_counts: dict[int, int] = {}
        self._account_consecutive_bet_fails: dict[int, int] = {}
        self._health_check_task: Optional[asyncio.Task] = None
        self._shutting_down = False
        self.session_runtime_registry = SessionRuntimeRegistry()

    def _create_adapter(self, platform_type: str, platform_url: Optional[str] = None) -> PlatformAdapter:
        return create_platform_adapter(platform_type, platform_url)

    async def start_worker(
        self,
        *,
        operator_id: int,
        account_id: int,
        account_name: str,
        password: str,
        platform_type: str = "JND28WEB",
        platform_url: Optional[str] = None,
        strategies: Optional[list[dict[str, Any]]] = None,
    ) -> AccountWorker:
        runtime_key = make_runtime_key(account_id, platform_type)
        if _is_detector_betting_account(
            {
                "account_name": account_name,
                "password": password,
            }
        ):
            logger.warning(
                "skip start worker operator_id=%d account_id=%d reason=shared_detector",
                operator_id,
                account_id,
            )
            raise RuntimeError("shared detector account cannot be used for betting worker")

        logger.info(
            "start_worker operator_id=%d account_id=%d platform=%s strategies_count=%d",
            operator_id,
            account_id,
            runtime_key[1],
            len(strategies) if strategies else 0,
        )

        existing = await self.registry.get(runtime_key)
        if existing and existing.running:
            if getattr(existing, "settling_only", False):
                existing.exit_settling_mode()
            return await self._hot_update_worker(existing, strategies)

        runtime = await self.get_or_create_session_runtime(
            operator_id=operator_id,
            account_id=account_id,
            account_name=account_name,
            password=password,
            platform_type=runtime_key[1],
            platform_url=platform_url,
        )
        adapter = runtime.adapter
        session = runtime.session
        rate_limiter = RateLimiter()
        shared_owner_key = make_shared_market_owner_key(account_id, runtime_key[1])
        poller = IssuePoller(
            adapter=adapter,
            rate_limiter=rate_limiter,
            shared_market_runtime=self.shared_market_runtime,
            shared_market_owner_key=shared_owner_key,
            shared_market_platform_type=runtime_key[1],
            shared_market_platform_url=platform_url or getattr(adapter, "base_url", None),
            db=self.db,
            operator_id=operator_id,
            account_id=account_id,
        )
        risk = RiskController(
            db=self.db,
            alert_service=self.alert_service,
            operator_id=operator_id,
            account_id=account_id,
            platform_type=runtime_key[1],
        )
        executor = BetExecutor(
            db=self.db,
            adapter=adapter,
            risk=risk,
            alert_service=self.alert_service,
            operator_id=operator_id,
            account_id=account_id,
            platform_type=runtime_key[1],
            session_runtime=runtime,
        )
        settler = SettlementProcessor(
            db=self.db,
            operator_id=operator_id,
            alert_service=self.alert_service,
            account_id=account_id,
            session_recover=lambda: runtime.recover("settlement"),
            session_runtime=runtime,
        )
        reconciler = Reconciler(
            db=self.db,
            adapter=adapter,
            alert_service=self.alert_service,
            operator_id=operator_id,
        )

        strategy_runners, strategy_profiles = self._build_strategy_snapshot(strategies)
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
            strategy_profiles=strategy_profiles,
            platform_type=runtime_key[1],
            session_runtime=runtime,
        )

        await self.registry.register(runtime_key, worker)
        try:
            await worker.start()
        except Exception:
            await self.registry.unregister(runtime_key)
            raise
        return worker

    async def _hot_update_worker(
        self,
        worker: AccountWorker,
        strategies: Optional[list[dict[str, Any]]] = None,
    ) -> AccountWorker:
        if not strategies:
            return worker
        next_runners, next_profiles = self._build_strategy_snapshot(strategies)
        worker.replace_strategy_snapshot(
            next_runners,
            next_profiles,
            apply_next_issue_only=False,
        )
        return worker

    async def stop_worker(self, account_id: int, platform_type: str | None = None) -> bool:
        if platform_type:
            return await self._stop_worker_by_key(make_runtime_key(account_id, platform_type))

        workers = await self.registry.all_workers()
        matched_keys = [key for key in workers if _runtime_key_matches_account(key, account_id)]
        if not matched_keys:
            return False
        results = await asyncio.gather(*(self._stop_worker_by_key(key) for key in matched_keys))
        return any(results)

    async def _stop_worker_by_key(self, runtime_key: RuntimeKeyLike) -> bool:
        registry_key: RuntimeKeyLike = runtime_key
        worker = await self.registry.get(registry_key)
        if worker is None and isinstance(runtime_key, tuple):
            registry_key = runtime_key[0]
            worker = await self.registry.get(registry_key)
        if worker is None:
            return False

        account_id, platform_type = _runtime_key_parts(runtime_key, worker)
        try:
            has_unsettled = await worker._has_unsettled_orders()
        except Exception:
            logger.exception(
                "failed to inspect unsettled orders account_id=%d platform=%s",
                account_id,
                platform_type,
            )
            has_unsettled = False

        if has_unsettled:
            async def _on_complete(_: int, __: str = platform_type) -> None:
                await self._release_shared_market_owner(worker)
                await self.registry.unregister(registry_key)
                await self.session_store.delete(registry_key)
                await self._remove_session_runtime(account_id, platform_type)

            worker._on_settle_complete = _on_complete
            await worker.enter_settling_mode()
        else:
            await worker.stop()
            await self._release_shared_market_owner(worker)
            await self.registry.unregister(registry_key)
            await self.session_store.delete(registry_key)
            await self._remove_session_runtime(account_id, platform_type)
        return True

    async def global_kill_switch(self) -> None:
        set_global_kill(True)
        workers = await self.registry.all_workers()
        for runtime_key, worker in workers.items():
            try:
                await worker.stop()
                await self._release_shared_market_owner(worker)
                await self.registry.unregister(runtime_key)
            except Exception:
                logger.exception("failed to stop worker key=%s", runtime_key)
        logger.warning("stopped_workers=%d", len(workers))

    async def account_kill_switch(self, account_id: int) -> bool:
        stopped = await self.stop_worker(account_id)
        if stopped:
            logger.warning("account_kill_switch account_id=%d", account_id)
        return stopped

    async def restore_workers_on_startup(self) -> int:
        restored = 0
        operators = await db_ops.operator_list_all(self.db)
        for operator in operators:
            if operator.get("status") != "active":
                continue
            operator_id = operator["id"]
            accounts = await db_ops.account_list_by_operator(self.db, operator_id=operator_id)
            strategies = await db_ops.strategy_list_by_operator(self.db, operator_id=operator_id)
            for account in accounts:
                if _is_detector_betting_account(account):
                    logger.info(
                        "skip restore worker operator_id=%d account_id=%d reason=shared_detector",
                        operator_id,
                        account["id"],
                    )
                    continue
                grouped: dict[str, list[dict[str, Any]]] = {}
                for strategy in strategies:
                    if strategy.get("account_id") != account["id"] or strategy.get("status") != "running":
                        continue
                    grouped.setdefault(strategy.get("platform_type") or "JND28WEB", []).append(strategy)

                for platform_type, running_strategies in grouped.items():
                    normalized_platform_type = (platform_type or "JND28WEB").upper()
                    if not _is_platform_verified_for_restore(account, normalized_platform_type):
                        logger.info(
                            "skip restore worker operator_id=%d account_id=%d platform=%s gate=verification",
                            operator_id,
                            account["id"],
                            normalized_platform_type,
                        )
                        continue
                    try:
                        await self.start_worker(
                            operator_id=operator_id,
                            account_id=account["id"],
                            account_name=account["account_name"],
                            password=account["password"],
                            platform_type=normalized_platform_type,
                            platform_url=account.get("platform_url"),
                            strategies=running_strategies,
                        )
                        restored += 1
                    except Exception:
                        logger.exception(
                            "restore worker failed operator_id=%d account_id=%d platform=%s",
                            operator_id,
                            account["id"],
                            platform_type,
                        )
        logger.info("restored_workers=%d", restored)
        return restored

    async def start_health_check(self, admin_operator_id: int = 1) -> None:
        if self._health_check_task and not self._health_check_task.done():
            return
        self._health_check_task = asyncio.create_task(
            self._health_check_loop(admin_operator_id)
        )

    async def _health_check_loop(self, admin_operator_id: int) -> None:
        while not self._shutting_down:
            try:
                workers = await self.registry.all_workers()
                active_accounts = []
                for key, worker in workers.items():
                    account_id, platform_type = _runtime_key_parts(key, worker)
                    active_accounts.append(
                        {"id": account_id, "platform_type": platform_type, "status": worker.status}
                    )
                await self.alert_service.check_system_health(
                    admin_operator_id=admin_operator_id,
                    active_accounts=active_accounts,
                    account_fail_counts=self._account_fail_counts,
                    account_consecutive_bet_fails=self._account_consecutive_bet_fails,
                )
            except Exception:
                logger.exception("health check failed")
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)

    def record_account_fail(self, account_id: int) -> None:
        self._account_fail_counts[account_id] = self._account_fail_counts.get(account_id, 0) + 1

    def record_bet_fail(self, account_id: int) -> None:
        self._account_consecutive_bet_fails[account_id] = (
            self._account_consecutive_bet_fails.get(account_id, 0) + 1
        )

    def reset_bet_fail(self, account_id: int) -> None:
        self._account_consecutive_bet_fails.pop(account_id, None)

    def reset_account_fail(self, account_id: int) -> None:
        self._account_fail_counts.pop(account_id, None)

    async def shutdown(self) -> None:
        self._shutting_down = True
        if self._health_check_task and not self._health_check_task.done():
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass

        workers = await self.registry.all_workers()
        if workers:
            await asyncio.gather(
                *(self._stop_worker_safe(runtime_key, worker) for runtime_key, worker in workers.items())
            )
        await self.session_runtime_registry.shutdown()
        await self.shared_market_runtime.shutdown()
        logger.info("EngineManager stopped_workers=%d", len(workers))

    async def _stop_worker_safe(self, runtime_key: RuntimeKey, worker: AccountWorker) -> None:
        account_id, platform_type = _runtime_key_parts(runtime_key, worker)
        try:
            await worker.stop()
            await self._release_shared_market_owner(worker)
            await self.registry.unregister(runtime_key)
            await self.session_store.delete(runtime_key)
            await self._remove_session_runtime(account_id, platform_type)
        except Exception:
            logger.exception("failed to stop worker key=%s", runtime_key)

    async def _remove_session_runtime(self, account_id: int, platform_type: str) -> None:
        try:
            await self.session_runtime_registry.remove(account_id, platform_type)
        except Exception:
            logger.exception(
                "failed to remove session runtime account_id=%d platform=%s",
                account_id,
                platform_type,
            )

    async def _release_shared_market_owner(self, worker: AccountWorker | None) -> None:
        poller = getattr(worker, "poller", None)
        owner_key = str(getattr(poller, "shared_market_owner_key", "") or "")
        if not owner_key:
            return
        if owner_key.startswith("shared-group-"):
            logger.info("skip shared collector release owner=%s", owner_key)
            return
        try:
            await self.shared_market_runtime.release_owner(owner_key)
        except Exception:
            logger.exception("shared collector release failed owner=%s", owner_key)

    async def get_or_create_session_runtime(
        self,
        *,
        operator_id: int,
        account_id: int,
        account_name: str,
        password: str,
        platform_type: str,
        platform_url: str | None = None,
    ) -> AccountSessionRuntime:
        return await self.session_runtime_registry.get_or_create(
            db=self.db,
            alert_service=self.alert_service,
            operator_id=operator_id,
            account_id=account_id,
            account_name=account_name,
            password=password,
            platform_type=platform_type,
            platform_url=platform_url,
        )

    async def get_runtime_for_account(
        self,
        *,
        account_id: int,
        platform_type: str | None = None,
    ) -> AccountSessionRuntime | None:
        if platform_type:
            runtime = self.session_runtime_registry.get(account_id, platform_type)
            if runtime is not None:
                return runtime
        workers = await self.registry.all_workers()
        for runtime_key in workers:
            rid, rplatform = _runtime_key_parts(runtime_key)
            if rid != account_id:
                continue
            runtime = self.session_runtime_registry.get(rid, rplatform)
            if runtime is not None:
                return runtime
        return self.session_runtime_registry.find_by_account(account_id)

    async def get_worker(
        self,
        *,
        account_id: int,
        platform_type: str | None = None,
    ) -> AccountWorker | None:
        if platform_type:
            return await self.registry.get(make_runtime_key(account_id, platform_type))
        workers = await self.registry.all_workers()
        for key, worker in workers.items():
            if _runtime_key_matches_account(key, account_id):
                return worker
        return None

    def _build_runtime_profile(
        self,
        strategy_data: dict[str, Any],
    ) -> StrategyRuntimeProfile:
        return StrategyRuntimeProfile(
            strategy_id=int(strategy_data["id"]),
            bet_timing=max(
                BET_TIMING_MIN,
                min(int(strategy_data.get("bet_timing", DEFAULT_BET_TIMING)), BET_TIMING_MAX),
            ),
            normalized_direction_keys=normalize_direction_keys(
                str(strategy_data.get("type", "flat")),
                str(strategy_data.get("play_code", "")),
            ),
        )

    def _build_strategy_snapshot(
        self,
        strategies: Optional[list[dict[str, Any]]],
    ) -> tuple[dict[int, StrategyRunner], dict[int, StrategyRuntimeProfile]]:
        runners: dict[int, StrategyRunner] = {}
        profiles: dict[int, StrategyRuntimeProfile] = {}
        if not strategies:
            return runners, profiles
        for strategy in strategies:
            if strategy.get("status") not in (None, "running"):
                continue
            runner = self._build_strategy_runner(strategy)
            if runner is None:
                continue
            strategy_id = int(strategy["id"])
            runners[strategy_id] = runner
            profiles[strategy_id] = self._build_runtime_profile(strategy)
        return runners, profiles

    def _build_strategy_runner(self, strategy_data: dict[str, Any]) -> Optional[StrategyRunner]:
        import app.engine.strategies  # noqa: F401
        from app.engine.strategies.dw3 import DW3FlatStrategy, DW3MartinStrategy
        from app.engine.strategies.omission_random import (
            AiRandomFlatStrategy,
            AiRandomMartinStrategy,
            AiSameRandomFlatStrategy,
            AiSameRandomMartinStrategy,
            OmissionRandomFlatStrategy,
            OmissionRandomMartinStrategy,
        )
        from app.engine.strategies.registry import get_strategy_class
        from app.utils.omission_random import (
            AI_RANDOM_FLAT_TYPE,
            AI_RANDOM_MARTIN_TYPE,
            AI_SAME_RANDOM_FLAT_TYPE,
            AI_SAME_RANDOM_MARTIN_TYPE,
            OMISSION_RANDOM_FLAT_TYPE,
            OMISSION_RANDOM_MARTIN_TYPE,
        )

        strategy_type = strategy_data.get("type", "flat")
        play_code = strategy_data.get("play_code", "DX1")
        base_amount = strategy_data.get("base_amount", 100)
        key_codes = [c.strip() for c in play_code.split(",") if c.strip()]
        is_dw3_group_strategy = bool(key_codes) and all(_is_dw3_group_token(code) for code in key_codes)
        gate_window_issues = int(strategy_data.get("gate_window_issues") or 0)

        seq_values: list[float] | None = None
        if strategy_data.get("martin_sequence"):
            seq_str = strategy_data["martin_sequence"]
            if isinstance(seq_str, str):
                import json as _json

                try:
                    parsed = _json.loads(seq_str)
                    seq_values = [float(x) for x in parsed]
                except (ValueError, TypeError):
                    seq_values = [float(x.strip()) for x in seq_str.split(",")]
            elif isinstance(seq_str, list):
                seq_values = [float(x) for x in seq_str]

        strategy_config: dict[str, Any] = {}
        raw_strategy_config = strategy_data.get("strategy_config")
        if raw_strategy_config:
            if isinstance(raw_strategy_config, str):
                import json as _json

                try:
                    parsed_config = _json.loads(raw_strategy_config)
                    if isinstance(parsed_config, dict):
                        strategy_config = parsed_config
                except (ValueError, TypeError):
                    logger.warning("invalid strategy_config strategy_id=%s", strategy_data.get("id"))
                    return None
            elif isinstance(raw_strategy_config, dict):
                strategy_config = raw_strategy_config

        if is_dw3_group_strategy:
            if gate_window_issues <= 0:
                logger.warning("invalid DW3 gate_window_issues strategy_id=%s", strategy_data.get("id"))
                return None
            if strategy_type == "flat":
                strategy_instance = DW3FlatStrategy(
                    group_tokens=key_codes,
                    base_amount=base_amount,
                    gate_window_issues=gate_window_issues,
                )
            elif strategy_type == "martin":
                if not seq_values:
                    logger.warning("missing martin_sequence strategy_id=%s", strategy_data.get("id"))
                    return None
                strategy_instance = DW3MartinStrategy(
                    group_tokens=key_codes,
                    base_amount=base_amount,
                    sequence=seq_values,
                    gate_window_issues=gate_window_issues,
                    strategy_name=str(strategy_data.get("name") or "dw3_martin"),
                )
            else:
                logger.warning("unsupported DW3 strategy type=%s", strategy_type)
                return None
        else:
            try:
                strategy_cls = get_strategy_class(strategy_type)
            except KeyError:
                logger.warning("unknown strategy type=%s", strategy_type)
                return None

            if strategy_type in (
                OMISSION_RANDOM_FLAT_TYPE,
                AI_RANDOM_FLAT_TYPE,
                AI_SAME_RANDOM_FLAT_TYPE,
            ):
                kwargs = {
                    "base_amount": base_amount,
                    "config": strategy_config,
                    "strategy_name": str(strategy_data.get("name") or strategy_type),
                }
                if strategy_type == AI_RANDOM_FLAT_TYPE:
                    strategy_cls = AiRandomFlatStrategy
                elif strategy_type == AI_SAME_RANDOM_FLAT_TYPE:
                    strategy_cls = AiSameRandomFlatStrategy
                else:
                    strategy_cls = OmissionRandomFlatStrategy
            elif strategy_type in (
                OMISSION_RANDOM_MARTIN_TYPE,
                AI_RANDOM_MARTIN_TYPE,
                AI_SAME_RANDOM_MARTIN_TYPE,
            ):
                if not seq_values:
                    logger.warning("missing martin_sequence strategy_id=%s", strategy_data.get("id"))
                    return None
                kwargs = {
                    "base_amount": base_amount,
                    "config": strategy_config,
                    "sequence": seq_values,
                    "strategy_name": str(strategy_data.get("name") or strategy_type),
                    "alert_service": self.alert_service,
                    "operator_id": int(strategy_data.get("operator_id") or 0),
                }
                if strategy_type == AI_RANDOM_MARTIN_TYPE:
                    strategy_cls = AiRandomMartinStrategy
                elif strategy_type == AI_SAME_RANDOM_MARTIN_TYPE:
                    strategy_cls = AiSameRandomMartinStrategy
                else:
                    strategy_cls = OmissionRandomMartinStrategy
            elif strategy_type == "flat":
                kwargs: dict[str, Any] = {
                    "key_codes": key_codes,
                    "base_amount": base_amount,
                }
            elif strategy_type == "martin" or strategy_type in WAVE_STRATEGY_TYPES:
                if not seq_values:
                    logger.warning("missing martin_sequence strategy_id=%s", strategy_data.get("id"))
                    return None
                if strategy_type == "martin":
                    kwargs = {
                        "key_codes": key_codes,
                        "base_amount": base_amount,
                        "sequence": seq_values,
                    }
                else:
                    kwargs = {
                        "base_amount": base_amount,
                        "sequence": seq_values,
                        "direction_codes": key_codes,
                    }
            elif strategy_type == "random_martin":
                from app.engine.random_plan_generator import Plan, PeriodBet
                from app.config import BOCAI_DB_PATH as _RM_DB_PATH
                import sqlite3 as _sqlite3

                cfg = strategy_config
                plan_set_id = cfg.get("plan_set_id")
                group_ids = cfg.get("group_ids", [])
                if not plan_set_id or not group_ids:
                    logger.warning(
                        "random_martin missing plan_set_id or group_ids strategy_id=%s",
                        strategy_data.get("id"),
                    )
                    return None
                try:
                    with _sqlite3.connect(_RM_DB_PATH) as _conn:
                        row = _conn.execute(
                            "SELECT plans_json FROM random_plan_sets WHERE id=?",
                            (plan_set_id,),
                        ).fetchone()
                    if not row:
                        logger.warning(
                            "plan_set_id=%d not found strategy_id=%s",
                            plan_set_id, strategy_data.get("id"),
                        )
                        return None
                    all_plans_data = json.loads(row[0])
                    all_plans: dict[int, Plan] = {
                        pd["group_id"]: Plan(
                            group_id=pd["group_id"],
                            periods=[
                                PeriodBet(
                                    period_index=pb["period_index"],
                                    ball=pb["ball"],
                                    numbers=tuple(pb["numbers"]),
                                    mask=pb["mask"],
                                )
                                for pb in pd["periods"]
                            ],
                        )
                        for pd in all_plans_data
                    }
                    groups = [all_plans[gid] for gid in group_ids if gid in all_plans]
                except Exception:
                    logger.exception(
                        "failed to load plan_set_id=%d strategy_id=%s",
                        plan_set_id, strategy_data.get("id"),
                    )
                    return None
                if len(groups) != len(group_ids):
                    logger.warning(
                        "some group_ids missing in plan_set plan_set_id=%d strategy_id=%s",
                        plan_set_id, strategy_data.get("id"),
                    )
                    return None
                kwargs = {
                    "groups": groups,
                    "N": int(cfg.get("N", len(groups[0].periods))),
                    "M": int(cfg["M"]),
                    "base_unit_fen": int(base_amount),
                    "martin_multiplier": float(cfg.get("martin_multiplier", 2.0)),
                    "runtime_state": cfg.get("runtime_state", {}),
                    "alert_service": self.alert_service,
                    "operator_id": int(strategy_data.get("operator_id") or 0),
                    "strategy_name": str(strategy_data.get("name") or "random_martin"),
                }
            else:
                logger.warning("unsupported strategy type=%s", strategy_type)
                return None
            strategy_instance = strategy_cls(**kwargs)

        runner = StrategyRunner(
            strategy_id=strategy_data["id"],
            strategy=strategy_instance,
            simulation=bool(strategy_data.get("simulation", 0)),
        )
        if strategy_data.get("status") == "running":
            runner.start()
        return runner
