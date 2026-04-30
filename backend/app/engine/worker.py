"""AccountWorker  

Phase 10.1:  AccountWorker EngineManager 


          
         


  -  30s 5s 10s
  - CloseTimeStamp  18s  8.1s  + 10s 


  -  Worker try/except + 
  - 5s/10s/30s 5  error


  -  operator_id operator_id
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

import aiosqlite

from app.engine.adapters.base import InstallInfo, PlatformAdapter, RemoteLoginRequired
from app.engine.alert import AlertService
from app.engine.executor import BetExecutor, ExecutionReport
from app.engine.poller import IssuePoller
from app.engine.reconciler import Reconciler
from app.engine.risk import RiskController
from app.engine.session import SessionManager
from app.engine.settlement import SettlementProcessor
from app.engine.strategies.base import StrategyStopRequest
from app.engine.strategy_runner import BetSignal, StrategyRunner
from app.models.db_ops import account_platform_session_upsert, strategy_update_status
from app.utils.strategy_timing import (
    BET_TIMING_MAX,
    BET_TIMING_MIN,
    SAFE_CLOSE_THRESHOLD,
    WAVE_STRATEGY_TYPES,
)
from app.utils.logger import log_countdown_validation

logger = logging.getLogger(__name__)

# 
MIN_BET_TIMING = BET_TIMING_MIN
DEFAULT_BET_TIMING = 30   #  30s
DEADLINE_MARGIN = 10      #  10s 
SKIP_THRESHOLD = SAFE_CLOSE_THRESHOLD

# 
RESTART_DELAYS = [5, 10, 30]
MAX_RESTART_FAILURES = 5

# 鍊掕鏃堕┍鍔ㄧ粨绠楅厤缃?
SETTLEMENT_WAIT_SECONDS_DEFAULT = 30
SETTLEMENT_WAIT_SECONDS_MIN = 10
SETTLEMENT_WAIT_SECONDS_MAX = 120

# 缁撶畻鏁版嵁鎷夊彇閲嶈瘯
SETTLE_DATA_RETRY_MAX = 6
SETTLE_DATA_RETRY_INTERVAL = 5

# GetCurrentInstall 缃戠粶閲嶈瘯
API_RETRY_DELAYS = [5, 10, 30]
API_RETRY_MAX = 3

# 璺ㄨ繘绋嬩簰鏂ラ攣
LOCK_TTL_MINUTES = 5
LOCK_RENEW_INTERVAL = 60  # 绉?


@dataclass(frozen=True)
class StrategyRuntimeProfile:
    """Runtime metadata for one strategy in one worker snapshot."""

    strategy_id: int
    bet_timing: int
    normalized_direction_keys: tuple[str, ...] = ()
    version: int = 0


@dataclass
class TimingGroup:
    """Strategies that share the same effective bet_timing."""

    bet_timing: int
    strategy_ids: list[int]
    executed: bool = False


@dataclass
class IssueExecutionPlan:
    """Stable execution snapshot for one issue."""

    issue: str
    plan_version: int
    created_from_profiles_version: int
    groups: list[TimingGroup]
    executed_strategy_ids: set[int] = field(default_factory=set)
    skipped_strategy_reasons: dict[int, str] = field(default_factory=dict)


class WorkerStartupError(RuntimeError):
    """Raised when a worker cannot become ready during startup."""


def _parse_result(result_str: str) -> tuple[list[int], int]:
    """ "b1,b2,b3"  (balls, sum_value)"""
    if not result_str or not result_str.strip():
        return [], 0
    parts = result_str.strip().split(",")
    balls = [int(p.strip()) for p in parts if p.strip()]
    return balls, sum(balls)


class AccountWorker:
    """

    Args:
        operator_id:  ID
        account_id:  ID
        db: 
        adapter: 
        session: 
        poller: 
        executor: 
        settler: 
        reconciler: 
        risk: 
        alert_service: 
        strategies:  {strategy_id: StrategyRunner}
        bet_timing:  30s
    """

    def __init__(
        self,
        *,
        operator_id: int,
        account_id: int,
        db: aiosqlite.Connection,
        adapter: PlatformAdapter,
        session: SessionManager,
        poller: IssuePoller,
        executor: BetExecutor,
        settler: SettlementProcessor,
        reconciler: Reconciler,
        risk: RiskController,
        alert_service: AlertService,
        strategies: Optional[dict[int, StrategyRunner]] = None,
        strategy_profiles: Optional[dict[int, StrategyRuntimeProfile]] = None,
        bet_timing: int = DEFAULT_BET_TIMING,
        platform_type: str = "JND28WEB",
        settlement_wait_seconds: int = SETTLEMENT_WAIT_SECONDS_DEFAULT,
    ) -> None:
        self.operator_id = operator_id
        self.account_id = account_id
        self.db = db
        self.adapter = adapter
        self.session = session
        self.poller = poller
        self.executor = executor
        self.settler = settler
        self.reconciler = reconciler
        self.risk = risk
        self.alert_service = alert_service
        self.strategies: dict[int, StrategyRunner] = strategies or {}
        self.bet_timing = max(MIN_BET_TIMING, min(bet_timing, 300))
        self.strategy_profiles: dict[int, StrategyRuntimeProfile] = (
            dict(strategy_profiles)
            if strategy_profiles is not None
            else {
                strategy_id: StrategyRuntimeProfile(
                    strategy_id=strategy_id,
                    bet_timing=self.bet_timing,
                )
                for strategy_id in self.strategies
            }
        )
        self._profiles_version: int = 1 if self.strategy_profiles else 0
        self._issue_execution_plan: Optional[IssueExecutionPlan] = None
        self._next_issue_strategies: Optional[dict[int, StrategyRunner]] = None
        self._next_issue_profiles: Optional[dict[int, StrategyRuntimeProfile]] = None
        self._platform_type = platform_type
        self._settlement_wait_seconds = max(
            SETTLEMENT_WAIT_SECONDS_MIN,
            min(settlement_wait_seconds, SETTLEMENT_WAIT_SECONDS_MAX),
        )
        self._last_signal_collection_reasons: dict[int, str] = {}

        # 
        self.running: bool = False
        self.status: str = "stopped"  # stopped / running / error
        self._task: Optional[asyncio.Task] = None
        self._restart_count: int = 0
        self._lock_token: Optional[str] = None
        self._startup_future: Optional[asyncio.Future[None]] = None
        self._startup_ready: bool = False

        # 缁撶畻妯″紡
        self.settling_only: bool = False
        self._settling_deadline: float | None = None
        self._on_settle_complete: Optional[asyncio.coroutines] = None  # 缁撶畻瀹屾垚鍥炶皟

    # ------------------------------------------------------------------
    # 
    # ------------------------------------------------------------------

    def _resolve_startup_success(self) -> None:
        if self._startup_future and not self._startup_future.done():
            self._startup_future.set_result(None)
        self._startup_ready = True

    def _resolve_startup_failure(self, exc: Exception) -> None:
        if self._startup_future and not self._startup_future.done():
            self._startup_future.set_exception(exc)

    def _iter_known_strategy_ids(self) -> set[int]:
        staged_ids = set((self._next_issue_strategies or {}).keys())
        return set(self.strategies.keys()) | staged_ids

    async def _persist_strategy_statuses(self, status: str) -> None:
        for strategy_id in sorted(self._iter_known_strategy_ids()):
            try:
                await strategy_update_status(
                    self.db,
                    strategy_id=strategy_id,
                    operator_id=self.operator_id,
                    status=status,
                )
            except Exception:
                logger.exception(
                    "strategy status persist failed strategy_id=%d account_id=%d status=%s",
                    strategy_id,
                    self.account_id,
                    status,
                )

    async def _handle_startup_failure(self, exc: Exception) -> None:
        self.status = "stopped"
        self.running = False
        await self._persist_strategy_statuses("stopped")
        self._resolve_startup_failure(exc)

    async def _handle_terminal_worker_error(self) -> None:
        self.status = "error"
        self.running = False
        await self._persist_strategy_statuses("error")

    def add_strategy(
        self,
        strategy_id: int,
        runner: StrategyRunner,
        *,
        profile: Optional[StrategyRuntimeProfile] = None,
        apply_next_issue_only: bool = True,
    ) -> None:
        """Hot-add a strategy, defaulting to next-issue activation."""
        if apply_next_issue_only:
            next_runners = self.get_staged_or_current_strategies()
            next_profiles = self.get_staged_or_current_profiles()
            next_runners[strategy_id] = runner
            next_profiles[strategy_id] = profile or StrategyRuntimeProfile(
                strategy_id=strategy_id,
                bet_timing=self.bet_timing,
            )
            self.stage_strategy_snapshot(next_runners, next_profiles)
            return

        self.strategies[strategy_id] = runner
        self.strategy_profiles[strategy_id] = profile or StrategyRuntimeProfile(
            strategy_id=strategy_id,
            bet_timing=self.bet_timing,
        )
        self._profiles_version += 1
        self._remove_strategy_from_issue_plan(strategy_id)

    def remove_strategy(self, strategy_id: int, *, apply_next_issue_only: bool = True) -> None:
        """Hot-remove a strategy, defaulting to next-issue deactivation."""
        if apply_next_issue_only:
            next_runners = self.get_staged_or_current_strategies()
            next_profiles = self.get_staged_or_current_profiles()
            next_runners.pop(strategy_id, None)
            next_profiles.pop(strategy_id, None)
            self.stage_strategy_snapshot(next_runners, next_profiles)
            return

        self.strategies.pop(strategy_id, None)
        self.strategy_profiles.pop(strategy_id, None)
        self._profiles_version += 1
        self._remove_strategy_from_issue_plan(strategy_id)

    def get_staged_or_current_strategies(self) -> dict[int, StrategyRunner]:
        """Return the snapshot that will feed the next issue plan."""
        return dict(self._next_issue_strategies or self.strategies)

    def get_staged_or_current_profiles(self) -> dict[int, StrategyRuntimeProfile]:
        """Return the profiles snapshot that will feed the next issue plan."""
        return dict(self._next_issue_profiles or self.strategy_profiles)

    def stage_strategy_snapshot(
        self,
        strategies: dict[int, StrategyRunner],
        profiles: dict[int, StrategyRuntimeProfile],
    ) -> None:
        """Replace the next-issue strategy snapshot without touching current issue."""
        self._next_issue_strategies = dict(strategies)
        self._next_issue_profiles = dict(profiles)
        self._profiles_version += 1

    def replace_strategy_snapshot(
        self,
        strategies: dict[int, StrategyRunner],
        profiles: dict[int, StrategyRuntimeProfile],
        *,
        apply_next_issue_only: bool = True,
    ) -> None:
        """Replace the worker strategy snapshot, optionally taking effect in the current issue."""
        if apply_next_issue_only:
            self.stage_strategy_snapshot(strategies, profiles)
            return

        previous_plan = self._issue_execution_plan
        executed_ids = (
            set(previous_plan.executed_strategy_ids)
            if previous_plan is not None
            else set()
        )
        skipped_reasons = (
            dict(previous_plan.skipped_strategy_reasons)
            if previous_plan is not None
            else {}
        )

        self.strategies = dict(strategies)
        self.strategy_profiles = dict(profiles)
        self._next_issue_strategies = None
        self._next_issue_profiles = None
        self._profiles_version += 1

        if previous_plan is None:
            return

        rebuilt_plan = self._build_issue_execution_plan(previous_plan.issue)
        active_strategy_ids = {
            strategy_id
            for group in rebuilt_plan.groups
            for strategy_id in group.strategy_ids
        }
        rebuilt_plan.executed_strategy_ids = {
            strategy_id
            for strategy_id in executed_ids
            if strategy_id in active_strategy_ids
        }
        rebuilt_plan.skipped_strategy_reasons = {
            strategy_id: reason
            for strategy_id, reason in skipped_reasons.items()
            if strategy_id in active_strategy_ids
        }

        for group in rebuilt_plan.groups:
            group.strategy_ids = [
                strategy_id
                for strategy_id in group.strategy_ids
                if strategy_id not in rebuilt_plan.executed_strategy_ids
                and strategy_id not in rebuilt_plan.skipped_strategy_reasons
            ]
            if not group.strategy_ids:
                group.executed = True

        self._issue_execution_plan = rebuilt_plan

    def _apply_staged_snapshot(self) -> None:
        if self._next_issue_strategies is None and self._next_issue_profiles is None:
            return
        self.strategies = dict(self._next_issue_strategies or {})
        self.strategy_profiles = dict(self._next_issue_profiles or {})
        self._next_issue_strategies = None
        self._next_issue_profiles = None

    def _build_issue_execution_plan(self, issue: str) -> IssueExecutionPlan:
        grouped: dict[int, list[int]] = {}
        for strategy_id, profile in self.strategy_profiles.items():
            if strategy_id not in self.strategies:
                continue
            grouped.setdefault(profile.bet_timing, []).append(strategy_id)

        groups = [
            TimingGroup(
                bet_timing=bet_timing,
                strategy_ids=sorted(strategy_ids),
            )
            for bet_timing, strategy_ids in sorted(grouped.items(), reverse=True)
        ]
        return IssueExecutionPlan(
            issue=issue,
            plan_version=self._profiles_version,
            created_from_profiles_version=self._profiles_version,
            groups=groups,
        )

    def _ensure_issue_execution_plan(self, issue: str) -> IssueExecutionPlan:
        if self._issue_execution_plan and self._issue_execution_plan.issue == issue:
            return self._issue_execution_plan

        self._apply_staged_snapshot()
        self._issue_execution_plan = self._build_issue_execution_plan(issue)
        return self._issue_execution_plan

    def _can_join_current_issue(self, install: InstallInfo) -> bool:
        """Return whether a newly started strategy may still join the current issue."""
        return install.state == 1 and install.close_countdown_sec > SKIP_THRESHOLD

    def _remove_strategy_from_issue_plan(self, strategy_id: int) -> None:
        if self._issue_execution_plan is None:
            return
        self._issue_execution_plan.executed_strategy_ids.discard(strategy_id)
        self._issue_execution_plan.skipped_strategy_reasons.pop(strategy_id, None)
        for group in self._issue_execution_plan.groups:
            if strategy_id in group.strategy_ids:
                group.strategy_ids = [
                    existing_id
                    for existing_id in group.strategy_ids
                    if existing_id != strategy_id
                ]
                if not group.strategy_ids:
                    group.executed = True

    def _mark_pending_groups_skipped(self, reason: str) -> None:
        if self._issue_execution_plan is None:
            return
        for group in self._issue_execution_plan.groups:
            if group.executed:
                continue
            self._mark_strategy_reasons(group.strategy_ids, reason)

    def _mark_strategy_reasons(self, strategy_ids: list[int], reason: str) -> None:
        if self._issue_execution_plan is None:
            return
        for strategy_id in strategy_ids:
            self._issue_execution_plan.skipped_strategy_reasons.setdefault(
                strategy_id,
                reason,
            )

    def _consume_timing_group(self, group: TimingGroup) -> None:
        if self._issue_execution_plan is None:
            return
        group.executed = True
        self._issue_execution_plan.executed_strategy_ids.update(group.strategy_ids)

    def _get_due_timing_groups(self, remaining: int) -> list[TimingGroup]:
        if self._issue_execution_plan is None:
            return []
        return [
            group
            for group in self._issue_execution_plan.groups
            if not group.executed and remaining <= group.bet_timing
        ]

    async def _revalidate_group_window(
        self,
        install: InstallInfo,
        *,
        bet_timing: int,
        strategy_ids: list[int] | None = None,
    ) -> tuple[InstallInfo, str | None]:
        """Refresh the current issue snapshot before submitting one timing group."""
        try:
            refreshed = await self.adapter.get_current_install()
        except Exception:
            logger.exception(
                "group revalidation failed issue=%s account_id=%d bet_timing=%ds",
                install.issue,
                self.account_id,
                bet_timing,
            )
            log_countdown_validation(
                operator_id=self.operator_id,
                account_id=self.account_id,
                issue=install.issue,
                phase="pre_submit",
                allowed=False,
                state=install.state,
                close_countdown_sec=install.close_countdown_sec,
                platform_type=self._platform_type,
                expected_issue=install.issue,
                current_issue=install.issue,
                bet_timing=bet_timing,
                reason="precheck_failed",
                strategy_ids=sorted(strategy_ids or []),
            )
            return install, "precheck_failed"

        reason: str | None = None
        if refreshed.issue != install.issue:
            reason = "issue_changed"
        elif refreshed.state != 1:
            reason = "state_not_open"
        elif refreshed.close_countdown_sec <= SKIP_THRESHOLD:
            reason = "remaining_too_small"
        elif refreshed.close_countdown_sec > bet_timing:
            reason = "window_not_open"

        log_countdown_validation(
            operator_id=self.operator_id,
            account_id=self.account_id,
            issue=install.issue,
            phase="pre_submit",
            allowed=reason is None,
            state=refreshed.state,
            close_countdown_sec=refreshed.close_countdown_sec,
            platform_type=self._platform_type,
            expected_issue=install.issue,
            current_issue=refreshed.issue,
            bet_timing=bet_timing,
            reason=reason,
            strategy_ids=sorted(strategy_ids or []),
        )
        return refreshed, reason

    async def _has_unsettled_orders(self) -> bool:
        """Return whether this worker still has unsettled orders in either ledger."""
        row = await (
            await self.db.execute(
                "SELECT ("
                "  SELECT COUNT(*) FROM bet_orders "
                "  WHERE account_id=? AND operator_id=? "
                "  AND status IN ('bet_success', 'pending_match')"
                ") + ("
                "  SELECT COUNT(*) FROM simulation_bet_orders "
                "  WHERE account_id=? AND operator_id=? "
                "  AND status IN ('bet_success', 'pending_match')"
                ") AS cnt",
                (self.account_id, self.operator_id, self.account_id, self.operator_id),
            )
        ).fetchone()
        return (row["cnt"] if row else 0) > 0

    async def enter_settling_mode(self) -> None:
        """Enter settling mode and keep the worker alive for settlement catch-up."""
        self.settling_only = True
        self.status = "settling"
        self._settling_deadline = time.time() + 600  # 10 鍒嗛挓瓒呮椂

        # 娓呯┖绛栫暐锛岄槻姝骇鐢熸姇娉ㄤ俊鍙?
        self.strategies.clear()
        self.strategy_profiles.clear()
        self._issue_execution_plan = None
        self._next_issue_profiles = None
        self._next_issue_strategies = None

        # 璁板綍鏃ュ織锛氬緟缁撶畻鏈熷彿鍜岃鍗曟暟閲?
        try:
            rows = await (
                await self.db.execute(
                    "SELECT issue, SUM(cnt) as cnt FROM ("
                    "  SELECT issue, COUNT(*) as cnt FROM bet_orders "
                    "  WHERE account_id=? AND operator_id=? "
                    "  AND status IN ('bet_success', 'pending_match') "
                    "  GROUP BY issue "
                    "  UNION ALL "
                    "  SELECT issue, COUNT(*) as cnt FROM simulation_bet_orders "
                    "  WHERE account_id=? AND operator_id=? "
                    "  AND status IN ('bet_success', 'pending_match') "
                    "  GROUP BY issue"
                    ") GROUP BY issue",
                    (self.account_id, self.operator_id, self.account_id, self.operator_id),
                )
            ).fetchall()
            issues_info = {r["issue"]: r["cnt"] for r in rows}
            total = sum(issues_info.values())
            logger.info(
                "Worker 杩涘叆缁撶畻妯″紡 account_id=%d 寰呯粨绠楁湡鍙?%s 鎬昏鍗曟暟=%d 瓒呮椂=%ds",
                self.account_id,
                issues_info,
                total,
                600,
            )
        except Exception:
            logger.exception(
                "缁撶畻妯″紡鏃ュ織璁板綍寮傚父 account_id=%d", self.account_id,
            )

    def exit_settling_mode(self) -> None:
        """Resume normal betting when a settling-only worker is started again."""
        if not self.settling_only:
            return

        self.settling_only = False
        self._settling_deadline = None
        self._on_settle_complete = None
        if self.running:
            self.status = "running"
        logger.info(
            "Worker exited settling mode operator_id=%d account_id=%d",
            self.operator_id,
            self.account_id,
        )

    # ------------------------------------------------------------------
    # 
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the worker after acquiring the runtime lock."""
        if self.running:
            logger.warning("Worker account_id=%d", self.account_id)
            return

        # 鎶㈤攣
        acquired = await self._acquire_lock()
        if not acquired:
            await self.alert_service.send(
                operator_id=self.operator_id,
                alert_type="worker_lock_conflict",
                title=f"Worker 鎶㈤攣鍐茬獊 account_id={self.account_id}",
                detail="宸叉湁娲昏穬閿侊紝鎷掔粷鍚姩",
                account_id=self.account_id,
            )
            logger.error(
                "Worker 鍚姩澶辫触锛堥攣鍐茬獊锛?account_id=%d",
                self.account_id,
            )
            raise WorkerStartupError(
                f"worker lock conflict account_id={self.account_id} platform={self._platform_type}"
            )

        self.running = True
        self.status = "running"
        self._restart_count = 0
        self._startup_ready = False
        self._startup_future = asyncio.get_running_loop().create_future()
        self._task = asyncio.create_task(self._run_with_restart())
        await self._startup_future
        logger.info(
            "Worker operator_id=%d account_id=%d",
            self.operator_id,
            self.account_id,
        )

    async def stop(self) -> None:
        """寮哄埗鍋滄 Worker锛坈ancel task + 閲婃斁閿侊級

        缁撶畻閫昏緫鐢辩粨绠楁ā寮忕殑涓诲惊鐜鐞嗭紝stop() 涓嶅啀璋冪敤 _settle_before_stop()銆?
        """
        self.running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

        await self._release_lock()
        self.status = "stopped"
        logger.info(
            "Worker 宸插仠姝?operator_id=%d account_id=%d",
            self.operator_id,
            self.account_id,
        )

    # ------------------------------------------------------------------
    #  + 
    # ------------------------------------------------------------------

    async def _run_with_restart(self) -> None:
        """

         Worker 
        5s  10s  30s
         5  error
        """
        while self.running:
            try:
                await self._main_loop()
                break
            except asyncio.CancelledError:
                logger.info(
                    "Worker account_id=%d", self.account_id
                )
                break
            except WorkerStartupError as exc:
                logger.error(
                    "Worker startup failed account_id=%d platform=%s reason=%s",
                    self.account_id,
                    self._platform_type,
                    exc,
                )
                await self._handle_startup_failure(exc)
                break
            except Exception:
                if not self._startup_ready:
                    exc = WorkerStartupError(
                        f"worker startup crashed account_id={self.account_id} platform={self._platform_type}"
                    )
                    logger.exception(
                        "Worker startup crashed account_id=%d platform=%s",
                        self.account_id,
                        self._platform_type,
                    )
                    await self._handle_startup_failure(exc)
                    break
                self._restart_count += 1
                logger.exception(
                    "Worker account_id=%d restart_count=%d",
                    self.account_id,
                    self._restart_count,
                )
                if self._restart_count >= MAX_RESTART_FAILURES:
                    await self._handle_terminal_worker_error()
                    logger.error(
                        "Worker  %d  erroraccount_id=%d",
                        MAX_RESTART_FAILURES,
                        self.account_id,
                    )
                    break
                delay_idx = min(
                    self._restart_count - 1, len(RESTART_DELAYS) - 1
                )
                delay = RESTART_DELAYS[delay_idx]
                logger.info(
                    "Worker  %ds account_id=%d",
                    delay,
                    self.account_id,
                )
                await asyncio.sleep(delay)
        await self._release_lock()
        self._task = None

    # ------------------------------------------------------------------
    # 涓诲惊鐜紙鍊掕鏃堕┍鍔ㄦā寮忥級
    # ------------------------------------------------------------------

    async def _main_loop(self) -> None:
        """鍊掕鏃堕┍鍔ㄤ富寰幆

        娴佺▼锛歭ogin 鈫?鍏ㄦ柊鍚姩妫€娴?鈫?琛ョ粨绠?鈫?寰幆(fetch 鈫?bet 鈫?sleep 鈫?settle 鈫?reconcile)
        """
        logger.info(
            "鍚姩 Worker operator_id=%d account_id=%d",
            self.operator_id,
            self.account_id,
        )
        login_ok = await self.session.login()
        if not login_ok:
            raise WorkerStartupError(
                f"session login failed account_id={self.account_id} platform={self._platform_type}"
            )

        startup_install = await self._fetch_install_with_retry()
        if startup_install is None:
            raise WorkerStartupError(
                f"initial runtime snapshot unavailable account_id={self.account_id} platform={self._platform_type}"
            )

        self._restart_count = 0

        # 鍏ㄦ柊鍚姩妫€娴嬶紙AC1.5锛?
        await self._detect_fresh_start(startup_install)
        self._resolve_startup_success()

        # 琛ョ粨绠?
        await self._recover_unsettled_orders()

        logger.info(
            "杩涘叆鍊掕鏃跺惊鐜?Worker operator_id=%d account_id=%d",
            self.operator_id,
            self.account_id,
        )

        while self.running:
            # 0. 閿佺画绾︼紙姣忔寰幆杩唬寮€濮嬫椂锛?
            if not await self._renew_lock():
                break  # 澶遍攣锛岄€€鍑哄惊鐜?

            # 1. 鑾峰彇褰撳墠鏈熷彿淇℃伅
            install = startup_install
            startup_install = None
            if install is None:
                install = await self._fetch_install_with_retry()
            if install is None:
                await asyncio.sleep(60)
                continue

            # 2. 璁板綍褰撳墠鏈熷彿锛堟姇娉ㄧ殑鏄?install.issue锛岀粨绠楁椂闇€瑕侀獙璇佽鏈熷彿鐨勫紑濂栫粨鏋滐級
            pre_issue = install.issue

            # 3. 鎶曟敞闃舵锛堢粨绠楁ā寮忎笅璺宠繃锛?
            settlement_anchor = install
            if not self.settling_only:
                settlement_anchor = await self._run_due_strategy_windows(install)
            if False and not self.settling_only:
                if install.state == 1 and self._should_bet(install):
                    signals = await self._collect_signals(install)
                    if signals:
                        try:
                            report = await self.executor.execute(install, signals)
                            await self._apply_execution_report(report)
                        except Exception:
                            logger.exception(
                                "鎶曟敞寮傚父 issue=%s account_id=%d",
                                install.issue,
                                self.account_id,
                            )

            # 4. 绛夊緟寮€濂栧€掕鏃跺綊闆?
            if (
                settlement_anchor.issue == pre_issue
                and settlement_anchor.open_countdown_sec > 0
            ):
                await asyncio.sleep(settlement_anchor.open_countdown_sec)

            # 5. 棰濆绛夊緟 settlement_wait_seconds
            await asyncio.sleep(self._settlement_wait_seconds)

            # 6. 鎷夊彇鏂版湡鍙?+ 涓婃湡寮€濂栫粨鏋?
            new_install = await self._fetch_settlement_data(pre_issue)
            if new_install is None:
                continue  # 宸插彂鍛婅锛岃烦杩囨湰鏈?

            # 7. 鎸佷箙鍖栧紑濂栫粨鏋?
            balls, sum_value = _parse_result(new_install.pre_result)
            await self.settler._save_lottery_result(
                new_install.pre_issue,
                new_install.pre_result,
                sum_value,
            )

            # 8. 鎵ц缁撶畻
            try:
                await self.settler.settle(
                    issue=new_install.pre_issue,
                    balls=balls,
                    sum_value=sum_value,
                    platform_type=self._platform_type,
                    adapter=self.adapter,
                )
            except Exception:
                logger.exception(
                    "缁撶畻寮傚父 issue=%s account_id=%d",
                    new_install.pre_issue,
                    self.account_id,
                )

            # 8.5 缁撶畻缁撴灉鍙嶉缁欑瓥鐣ワ紙椹卞姩椹竵鍊嶅锛?
            await self._feedback_settlement_results(new_install.pre_issue)

            # 9. 瀵硅处
            try:
                await self.reconciler.reconcile(
                    issue=new_install.pre_issue,
                    account_id=self.account_id,
                )
            except Exception:
                logger.exception(
                    "瀵硅处寮傚父 issue=%s account_id=%d",
                    new_install.pre_issue,
                    self.account_id,
                )

            # 10. 缁撶畻妯″紡妫€鏌?
            if self.settling_only:
                # 瓒呮椂妫€鏌?
                if self._settling_deadline and time.time() > self._settling_deadline:
                    logger.warning(
                        "缁撶畻妯″紡瓒呮椂 account_id=%d", self.account_id,
                    )
                    await self._handle_settling_timeout()
                    await self._cleanup_after_settling()
                    break

                # 妫€鏌ユ槸鍚﹁繕鏈夋湭缁撶畻璁㈠崟
                if not await self._has_unsettled_orders():
                    logger.info(
                        "缁撶畻妯″紡瀹屾垚锛氭墍鏈夎鍗曞凡缁撶畻 account_id=%d",
                        self.account_id,
                    )
                    self.running = False
                    self.status = "stopped"
                    await self._cleanup_after_settling()
                    break

    # ------------------------------------------------------------------
    # 7.2 _fetch_install_with_retry
    # ------------------------------------------------------------------

    async def _run_due_strategy_windows(self, install: InstallInfo) -> InstallInfo:
        """Execute timing groups for one issue until the window closes or rolls."""
        current_install = install
        plan = self._ensure_issue_execution_plan(current_install.issue)

        while self.running and not self.settling_only:
            if current_install.issue != plan.issue:
                self._mark_pending_groups_skipped("issue_changed")
                return current_install

            if current_install.state != 1:
                self._mark_pending_groups_skipped("state_not_open")
                return current_install

            remaining = current_install.close_countdown_sec
            if remaining <= SKIP_THRESHOLD:
                self._mark_pending_groups_skipped("remaining_too_small")
                return current_install

            due_groups = self._get_due_timing_groups(remaining)
            if due_groups:
                for group in due_groups:
                    current_install, revalidate_reason = await self._revalidate_group_window(
                        current_install,
                        bet_timing=group.bet_timing,
                        strategy_ids=group.strategy_ids,
                    )
                    if revalidate_reason == "window_not_open":
                        break
                    if revalidate_reason == "precheck_failed":
                        # Transient revalidation failures should not kill the
                        # rest of the current issue. Keep pending groups alive,
                        # wait for the next poll tick, and retry with a fresh
                        # snapshot after session recovery.
                        break
                    if revalidate_reason is not None:
                        self._mark_pending_groups_skipped(revalidate_reason)
                        return current_install

                    self._consume_timing_group(group)
                    signals = await self._collect_signals(
                        current_install,
                        strategy_ids=group.strategy_ids,
                    )
                    signal_strategy_ids = {
                        int(signal.strategy_id) for signal in signals
                    }
                    skipped_ids = [
                        strategy_id
                        for strategy_id in group.strategy_ids
                        if strategy_id not in signal_strategy_ids
                    ]
                    if skipped_ids:
                        self._mark_signal_collection_reasons(skipped_ids)
                    if not signals:
                        self._mark_signal_collection_reasons(group.strategy_ids)
                        continue
                    try:
                        report = await self.executor.execute(current_install, signals)
                        await self._apply_execution_report(report)
                    except Exception:
                        self._mark_strategy_reasons(
                            sorted(signal_strategy_ids),
                            "execution_failed",
                        )
                        logger.exception(
                            "bet execution failed issue=%s account_id=%d group=%ds",
                            current_install.issue,
                            self.account_id,
                            group.bet_timing,
                        )

            pending_groups = [group for group in plan.groups if not group.executed]
            if not pending_groups:
                return current_install

            next_group = max(pending_groups, key=lambda group: group.bet_timing)
            if remaining <= next_group.bet_timing:
                wait_seconds = max(1, int(getattr(self.poller, "poll_interval", 5)))
            else:
                wait_seconds = max(
                    1,
                    min(
                        int(getattr(self.poller, "poll_interval", 5)),
                        remaining - next_group.bet_timing,
                    ),
                )

            await asyncio.sleep(wait_seconds)
            next_install = await self._fetch_install_with_retry()
            if next_install is None:
                return current_install
            current_install = next_install

        return current_install

    async def _fetch_install_with_retry(self) -> Optional[InstallInfo]:
        """鑾峰彇褰撳墠鏈熷彿淇℃伅锛岀綉缁滃紓甯告椂鎸?5s 鈫?10s 鈫?30s 閲嶈瘯

        鏈€澶?3 娆★紝鍏ㄩ儴澶辫触鍙?api_call_failed 鍛婅骞惰繑鍥?None銆?
        """
        for attempt in range(API_RETRY_MAX):
            try:
                return await self.poller.poll()
            except RemoteLoginRequired as exc:
                recovered = await self._recover_remote_login(exc)
                if not recovered:
                    return None
                continue
            except Exception:
                logger.exception(
                    "GetCurrentInstall 澶辫触 attempt=%d/%d account_id=%d",
                    attempt + 1,
                    API_RETRY_MAX,
                    self.account_id,
                )
                if attempt < API_RETRY_MAX - 1:
                    delay = API_RETRY_DELAYS[attempt]
                    await asyncio.sleep(delay)

        # 鍏ㄩ儴澶辫触
        await self.alert_service.send(
            operator_id=self.operator_id,
            alert_type="api_call_failed",
            title=f"GetCurrentInstall 璋冪敤澶辫触 account_id={self.account_id}",
            detail=f"{API_RETRY_MAX} 次重试全部失败",
            account_id=self.account_id,
        )
        return None

    async def _recover_remote_login(self, exc: RemoteLoginRequired) -> bool:
        logger.warning(
            "remote login detected account_id=%d state=%s message=%s",
            self.account_id,
            exc.raw_state,
            str(exc),
        )
        await self.session._reconnect()

        try:
            session_ready = await self.session.ensure_session()
        except Exception:
            logger.exception(
                "remote login recovery check failed account_id=%d state=%s",
                self.account_id,
                exc.raw_state,
            )
            session_ready = False

        if session_ready:
            return True

        self.status = "error"
        self.running = False
        await self.alert_service.send(
            operator_id=self.operator_id,
            alert_type="session_lost",
            title=f"Remote login requires manual action account_id={self.account_id}",
            detail=(
                f"GetCurrentInstall returned State={exc.raw_state}; "
                "controlled reconnect failed and worker stopped"
            ),
            account_id=self.account_id,
        )
        return False

    # ------------------------------------------------------------------
    # 7.3 _fetch_settlement_data
    # ------------------------------------------------------------------

    async def _fetch_settlement_data(self, expected_pre_issue: str) -> Optional[InstallInfo]:
        """鎷夊彇鏂版湡鍙凤紝楠岃瘉 PreLotteryResult 鏈夋晥鎬э紝鏈€澶氶噸璇?6 娆?

        鍏ㄩ儴澶辫触鏃讹細real 璁㈠崟鏍囪 settle_failed + sim 璁㈠崟鐢?check_win 闄嶇骇缁撶畻 + 鍙戝憡璀︺€?
        """
        for attempt in range(SETTLE_DATA_RETRY_MAX):
            install = await self._fetch_install_with_retry()
            if install is None:
                return None

            pre_result = install.pre_result
            if (
                pre_result
                and pre_result.strip()
                and install.pre_issue == expected_pre_issue
            ):
                return install

            if attempt < SETTLE_DATA_RETRY_MAX - 1:
                await asyncio.sleep(SETTLE_DATA_RETRY_INTERVAL)

        # 6 娆￠噸璇曞け璐?鈫?闄嶇骇澶勭悊
        logger.warning(
            "Settlement data missing issue=%s account_id=%d, downgrade path triggered",
            expected_pre_issue,
            self.account_id,
        )
        await self._handle_settlement_data_missing(expected_pre_issue)

        await self.alert_service.send(
            operator_id=self.operator_id,
            alert_type="settlement_data_missing",
            title=f"缁撶畻鏁版嵁缂哄け 鏈熷彿 {expected_pre_issue}",
            detail=f"重试 {SETTLE_DATA_RETRY_MAX} 次后仍无有效开奖数据",
            account_id=self.account_id,
        )
        return None

    async def _handle_settlement_data_missing(self, issue: str) -> None:
        """缁撶畻鏁版嵁缂哄け鏃剁殑闄嶇骇澶勭悊

        real 璁㈠崟鏍囪 settle_failed锛宻im 璁㈠崟鐢?check_win 闄嶇骇缁撶畻銆?
        """
        rows = await (
            await self.db.execute(
                "SELECT * FROM bet_orders WHERE issue=? AND account_id=? "
                "AND operator_id=? AND status='bet_success'",
                (issue, self.account_id, self.operator_id),
            )
        ).fetchall()
        orders = [dict(r) for r in rows]
        if not orders:
            return

        real_orders = [o for o in orders if o.get("simulation", 0) == 0]
        sim_orders = [o for o in orders if o.get("simulation", 0) == 1]

        # real 璁㈠崟鏍囪 settle_failed
        if real_orders:
            await self.settler._mark_orders_settle_failed(real_orders)

        # sim 璁㈠崟鐢?check_win 闄嶇骇缁撶畻锛堟棤寮€濂栫粨鏋滐紝鏃犳硶璁＄畻锛屼篃鏍囪 settle_failed锛?
        # 娉ㄦ剰锛欰C1.4 璇?sim 璁㈠崟浣跨敤鏈湴 check_win 闄嶇骇缁撶畻锛屼絾鏃犲紑濂栫粨鏋滄椂鏃犳硶璁＄畻
        # 璁捐鏂囨。璇?sim 璁㈠崟鐢?check_win 闄嶇骇缁撶畻锛屼絾杩欓渶瑕佸紑濂栫粨鏋?
        # 杩欓噷 sim 璁㈠崟涔熸爣璁?settle_failed锛堝洜涓烘病鏈夊紑濂栨暟鎹棤娉曡绠楋級
        if sim_orders:
            await self.settler._mark_orders_settle_failed(sim_orders)

    # ------------------------------------------------------------------
    # 7.4 鍏ㄦ柊鍚姩妫€娴嬶紙AC1.5锛?
    # ------------------------------------------------------------------

    async def _detect_fresh_start(self, install: InstallInfo | None = None) -> None:
        """Handle first-start behavior without unnecessarily skipping a legal current issue."""
        row = await (
            await self.db.execute(
                "SELECT COUNT(*) as cnt FROM bet_orders "
                "WHERE account_id=? AND operator_id=?",
                (self.account_id, self.operator_id),
            )
        ).fetchone()

        count = row["cnt"] if row else 0
        if count != 0:
            return

        try:
            current_install = install or await self.poller.poll()
            if self._can_join_current_issue(current_install):
                self.poller.last_issue = ""
                logger.info(
                    "鍏ㄦ柊鍚姩妫€娴嬶細褰撳墠鏈熶粛鍦ㄥ悎娉曠獥鍙ｏ紝鍏佽鍙備笌褰撴湡 issue=%s account_id=%d remaining=%ds",
                    current_install.issue,
                    self.account_id,
                    current_install.close_countdown_sec,
                )
            else:
                self.poller.last_issue = current_install.issue
                logger.info(
                        "鍏ㄦ柊鍚姩妫€娴嬶細褰撳墠鏈熷凡涓嶅湪鍚堟硶绐楀彛锛岃褰?last_issue=%s account_id=%d",
                        current_install.issue,
                        self.account_id,
                    )
        except Exception:
            logger.exception(
                    "鍏ㄦ柊鍚姩妫€娴嬶細鑾峰彇褰撳墠鏈熷け璐?account_id=%d",
                    self.account_id,
                )
            # 鍏ㄦ柊鍚姩锛氳褰曞綋鍓嶆湡鍙蜂负 last_issue
            try:
                current_install = install or await self.poller.poll()
                self.poller.last_issue = current_install.issue
                logger.info(
                    "鍏ㄦ柊鍚姩妫€娴嬶細鏃犲巻鍙茶褰曪紝璁板綍 last_issue=%s account_id=%d",
                    current_install.issue,
                    self.account_id,
                )
            except Exception:
                logger.exception(
                    "鍏ㄦ柊鍚姩妫€娴嬶細鑾峰彇褰撳墠鏈熷彿澶辫触 account_id=%d",
                    self.account_id,
                )

    # ------------------------------------------------------------------
    # 7.5 _recover_unsettled_orders
    # ------------------------------------------------------------------

    async def _recover_unsettled_orders(self) -> None:
        """閲嶅惎鏃惰ˉ缁撶畻锛氭壂鎻忔湭缁撶畻璁㈠崟锛屾寜 issue 鍒嗙粍锛岃幏鍙栧巻鍙插紑濂栫粨鏋?

        鏈夌粨鏋滃垯 settle(is_recovery=True)锛涙棤缁撴灉涓旇鍗曡窛浠婅秴杩?鍒嗛挓鍒欐爣璁?settle_failed锛?
        璺濅粖涓嶈秴杩?鍒嗛挓鐨勬柊璁㈠崟璺宠繃锛岀暀缁欐甯哥粨绠楀懆鏈熷鐞嗐€?
        """
        rows = await (
            await self.db.execute(
                "SELECT DISTINCT issue FROM ("
                "  SELECT issue FROM bet_orders "
                "  WHERE account_id=? AND operator_id=? "
                "  AND status IN ('bet_success', 'pending_match', 'settle_timeout') "
                "  UNION "
                "  SELECT issue FROM simulation_bet_orders "
                "  WHERE account_id=? AND operator_id=? "
                "  AND status IN ('bet_success', 'pending_match', 'settle_timeout')"
                ")",
                (self.account_id, self.operator_id, self.account_id, self.operator_id),
            )
        ).fetchall()
        issues = [r["issue"] for r in rows]
        if not issues:
            return

        logger.info(
            "琛ョ粨绠楀紑濮嬶細%d 涓湡鍙峰緟澶勭悊 account_id=%d",
            len(issues),
            self.account_id,
        )

        # 鑾峰彇鍘嗗彶寮€濂栫粨鏋?
        try:
            results = await self.adapter.get_lottery_results(count=50)
        except Exception:
            logger.exception(
                "琛ョ粨绠楋細鑾峰彇鍘嗗彶寮€濂栫粨鏋滃け璐?account_id=%d", self.account_id,
            )
            results = []

        result_map = {str(r.get("Installments", "")): r.get("OpenResult", "") for r in results}

        for issue in issues:
            open_result = result_map.get(issue)
            if open_result and open_result.strip():
                # 鏈夊紑濂栫粨鏋?鈫?鎵ц琛ョ粨绠?
                balls, sum_value = _parse_result(open_result)
                try:
                    await self.settler.settle(
                        issue=issue,
                        balls=balls,
                        sum_value=sum_value,
                        platform_type=self._platform_type,
                        adapter=self.adapter,
                        is_recovery=True,
                    )
                    await self._feedback_settlement_results(issue)
                    logger.info("琛ョ粨绠楀畬鎴?issue=%s account_id=%d", issue, self.account_id)
                except Exception:
                    logger.exception(
                        "琛ョ粨绠楀紓甯?issue=%s account_id=%d", issue, self.account_id,
                    )
            else:
                # 鏃犲紑濂栫粨鏋?鈫?妫€鏌ヨ鍗曞勾榫勶紝鏂拌鍗曡烦杩?
                if await self._has_recent_orders(issue, max_age_seconds=180):
                    logger.info(
                        "琛ョ粨绠楄烦杩囷細鏈熷彿 %s 鏈夎繎3鍒嗛挓鍐呯殑鏂拌鍗曪紝鐣欑粰姝ｅ父鍛ㄦ湡 account_id=%d",
                        issue, self.account_id,
                    )
                    continue

                # 鑰佽鍗?鈫?鏍囪 settle_failed + 鍙戝憡璀?
                await self._mark_issue_orders_settle_failed(issue)
                await self.alert_service.send(
                    operator_id=self.operator_id,
                    alert_type="settle_data_expired",
                    title=f"琛ョ粨绠楁暟鎹繃鏈?鏈熷彿 {issue}",
                    detail=f"鍘嗗彶寮€濂栫粨鏋滀腑鏃?issue={issue} 鐨勮褰曪紝涓旇鍗曞凡瓒呰繃3鍒嗛挓",
                    account_id=self.account_id,
                )

    async def _has_recent_orders(self, issue: str, max_age_seconds: int = 180) -> bool:
        """妫€鏌ユ寚瀹氭湡鍙锋槸鍚︽湁璺濅粖涓嶈秴杩?max_age_seconds 鐨勮鍗?

        鐢ㄤ簬琛ョ粨绠楁椂鍖哄垎"鍒氫笅娉ㄨ繕娌″紑濂?鍜?鐪熸杩囨湡"鐨勮鍗曘€?
        """
        from datetime import datetime, timezone, timedelta
        _bjt = timezone(timedelta(hours=8))

        rows = await (
            await self.db.execute(
                "SELECT bet_at, created_at FROM ("
                "  SELECT bet_at, created_at FROM bet_orders "
                "  WHERE issue=? AND account_id=? AND operator_id=? "
                "  AND status IN ('bet_success', 'pending_match', 'settle_timeout') "
                "  UNION ALL "
                "  SELECT bet_at, created_at FROM simulation_bet_orders "
                "  WHERE issue=? AND account_id=? AND operator_id=? "
                "  AND status IN ('bet_success', 'pending_match', 'settle_timeout')"
                ") ORDER BY bet_at DESC LIMIT 1",
                (
                    issue,
                    self.account_id,
                    self.operator_id,
                    issue,
                    self.account_id,
                    self.operator_id,
                ),
            )
        ).fetchall()

        if not rows:
            return False

        row = rows[0]
        # 浼樺厛鐢?bet_at锛屽叾娆?created_at
        bet_at_str = None
        try:
            bet_at_str = row["bet_at"]
        except (KeyError, TypeError):
            pass
        if not bet_at_str:
            try:
                bet_at_str = row["created_at"]
            except (KeyError, TypeError):
                pass
        if not bet_at_str:
            return False

        try:
            bet_at = datetime.strptime(bet_at_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_bjt)
            now = datetime.now(_bjt)
            elapsed = (now - bet_at).total_seconds()
            return elapsed < max_age_seconds
        except (ValueError, TypeError):
            return False

    async def _handle_settling_timeout(self) -> None:
        """Mark lingering unsettled orders as failed when settling mode times out."""
        rows = await (
            await self.db.execute(
                "SELECT id, status, 0 as simulation FROM bet_orders WHERE account_id=? AND operator_id=? "
                "AND status IN ('bet_success', 'pending_match') "
                "UNION ALL "
                "SELECT id, status, 1 as simulation FROM simulation_bet_orders WHERE account_id=? AND operator_id=? "
                "AND status IN ('bet_success', 'pending_match')",
                (self.account_id, self.operator_id, self.account_id, self.operator_id),
            )
        ).fetchall()
        orders = [dict(r) for r in rows]

        if orders:
            await self.settler._mark_orders_settle_failed(orders)
            logger.warning(
                "settling mode timeout: marked %d orders settle_failed account_id=%d",
                len(orders),
                self.account_id,
            )

        # 鍙戦€佽秴鏃跺憡璀?
        elapsed = int(time.time() - (self._settling_deadline - 600)) if self._settling_deadline else 0
        await self.alert_service.send(
            operator_id=self.operator_id,
            alert_type="settling_mode_timeout",
            title=f"缁撶畻妯″紡瓒呮椂 account_id={self.account_id}",
            detail=f"绛夊緟 {elapsed}s 鍚庤秴鏃讹紝{len(orders)} 绗旇鍗曟爣璁颁负 settle_failed",
            account_id=self.account_id,
        )

        self.running = False
        self.status = "stopped"

    async def _cleanup_after_settling(self) -> None:
        """缁撶畻妯″紡閫€鍑哄悗鐨勬竻鐞嗭細閲婃斁閿併€佷粠 registry 娉ㄩ攢"""
        await self._release_lock()
        if self._on_settle_complete:
            try:
                await self._on_settle_complete(self.account_id)
            except Exception:
                logger.exception(
                    "缁撶畻瀹屾垚鍥炶皟寮傚父 account_id=%d", self.account_id,
                )

    async def _settle_before_stop(self) -> None:
        """Try to settle outstanding issues before a worker fully stops."""
        rows = await (
            await self.db.execute(
                "SELECT DISTINCT issue FROM ("
                "  SELECT issue FROM bet_orders "
                "  WHERE account_id=? AND operator_id=? "
                "  AND status IN ('bet_success', 'pending_match') "
                "  UNION "
                "  SELECT issue FROM simulation_bet_orders "
                "  WHERE account_id=? AND operator_id=? "
                "  AND status IN ('bet_success', 'pending_match')"
                ")",
                (self.account_id, self.operator_id, self.account_id, self.operator_id),
            )
        ).fetchall()
        issues = [r["issue"] for r in rows]
        if not issues:
            logger.info(
                "鍋滄鍓嶈ˉ缁撶畻锛氭棤寰呯粨绠楄鍗?account_id=%d",
                self.account_id,
            )
            return

        logger.info(
            "鍋滄鍓嶈ˉ缁撶畻锛?d 涓湡鍙峰緟澶勭悊 account_id=%d",
            len(issues),
            self.account_id,
        )

        # 鑾峰彇鍘嗗彶寮€濂栫粨鏋?
        try:
            results = await self.adapter.get_lottery_results(count=50)
        except Exception:
            logger.exception(
                "鍋滄鍓嶈ˉ缁撶畻锛氳幏鍙栧巻鍙插紑濂栫粨鏋滃け璐?account_id=%d",
                self.account_id,
            )
            results = []

        result_map = {
            str(r.get("Installments", "")): r.get("OpenResult", "")
            for r in results
        }

        # 涔熷皾璇曚粠 GetCurrentInstall 鑾峰彇涓婃湡缁撴灉
        try:
            install = await self.poller.poll()
            if install and install.pre_issue and install.pre_result:
                result_map[str(install.pre_issue)] = install.pre_result
        except Exception:
            logger.warning(
                "鍋滄鍓嶈ˉ缁撶畻锛氳幏鍙栧綋鍓嶆湡鍙峰け璐?account_id=%d",
                self.account_id,
            )

        settled_count = 0
        pending_count = 0
        for issue in issues:
            open_result = result_map.get(issue)
            if open_result and open_result.strip():
                # 鏈夊紑濂栫粨鏋?鈫?鎵ц缁撶畻
                balls, sum_value = _parse_result(open_result)
                try:
                    await self.settler.settle(
                        issue=issue,
                        balls=balls,
                        sum_value=sum_value,
                        platform_type=self._platform_type,
                        adapter=self.adapter,
                        is_recovery=True,
                    )
                    settled_count += 1
                    logger.info(
                        "鍋滄鍓嶈ˉ缁撶畻瀹屾垚 issue=%s account_id=%d",
                        issue,
                        self.account_id,
                    )
                except Exception:
                    logger.exception(
                        "鍋滄鍓嶈ˉ缁撶畻寮傚父 issue=%s account_id=%d",
                        issue,
                        self.account_id,
                    )
            else:
                # 杩樻病寮€濂?鈫?鏍囪涓?pending_match锛屼笅娆″惎鍔ㄦ椂琛ョ粨绠?
                pending_count += 1
                logger.info(
                    "鍋滄鍓嶈ˉ缁撶畻锛氭湡鍙?%s 灏氭湭寮€濂栵紝淇濇寔寰呯粨绠楃姸鎬?account_id=%d",
                    issue,
                    self.account_id,
                )

        logger.info(
            "鍋滄鍓嶈ˉ缁撶畻瀹屾垚锛氬凡缁撶畻=%d 寰呬笅娆¤ˉ缁撶畻=%d account_id=%d",
            settled_count,
            pending_count,
            self.account_id,
        )

    async def _feedback_settlement_results(self, issue: str) -> None:
        """缁撶畻鍚庡皢缁撴灉鍙嶉缁欏搴旂殑 StrategyRunner

        鏌ヨ璇ユ湡鍙峰凡缁撶畻璁㈠崟锛屾寜 strategy_id 鍒嗗彂 on_result銆?
        鐢ㄤ簬椹卞姩椹竵绛栫暐鐨?level 鎺ㄨ繘銆?
        """
        rows = await (
            await self.db.execute(
                "SELECT strategy_id, key_code, is_win, pnl, martin_level FROM bet_orders "
                "WHERE issue=? AND account_id=? AND operator_id=? "
                "AND status='settled'",
                (issue, self.account_id, self.operator_id),
            )
        ).fetchall()

        for row in rows:
            sid = row["strategy_id"]
            runner = self.strategies.get(sid)
            if runner is None:
                logger.debug(
                    "缁撶畻鍙嶉璺宠繃锛歴trategy_id=%d 鏃犲搴?runner account_id=%d",
                    sid, self.account_id,
                )
                continue
            try:
                try:
                    order_martin_level = row["martin_level"]
                except (KeyError, IndexError):
                    order_martin_level = None
                feedback_kwargs = {"key_code": row["key_code"]}
                if order_martin_level is not None:
                    feedback_kwargs["martin_level"] = order_martin_level
                stop_request = await runner.on_result(
                    row["is_win"],
                    row["pnl"],
                    **feedback_kwargs,
                )
                if (
                    isinstance(stop_request, StrategyStopRequest)
                    and stop_request.should_stop
                ):
                    if not self._should_apply_settlement_stop_request(
                        runner, stop_request
                    ):
                        logger.info(
                            "settlement stop request ignored strategy_id=%d "
                            "account_id=%d reason=%s",
                            sid,
                            self.account_id,
                            stop_request.reason,
                        )
                        continue
                    await self._stop_strategy_runner(
                        sid, stop_request.reason or "strategy_requested_stop"
                    )
            except Exception:
                logger.exception(
                    "缁撶畻鍙嶉寮傚父 strategy_id=%d issue=%s account_id=%d",
                    sid, issue, self.account_id,
                )

    def _should_apply_settlement_stop_request(
        self, runner: StrategyRunner, stop_request: StrategyStopRequest
    ) -> bool:
        """Return whether a strategy-origin settlement stop should be applied."""
        runner_attrs = getattr(runner, "__dict__", {})
        strategy = (
            runner_attrs.get("strategy")
            if isinstance(runner_attrs, dict)
            else None
        )
        try:
            strategy_name = strategy.name() if strategy is not None else ""
        except Exception:
            strategy_name = ""

        # Red-wave double is a long-running monitor. Settlement outcomes such as
        # target hit, refund, no result, or Martin sequence cycling must never
        # stop it; risk stops are applied through ExecutionReport instead.
        if strategy_name in WAVE_STRATEGY_TYPES:
            return False

        return True

    async def _apply_execution_report(self, report: ExecutionReport | None) -> None:
        """Apply executor risk-stop report for this account worker."""
        if not report or not report.stop_strategy_ids:
            return
        for sid, reason in report.stop_strategy_ids.items():
            await self._stop_strategy_runner(sid, reason or "risk_stop")

    async def _stop_strategy_runner(self, strategy_id: int, reason: str) -> None:
        """Stop one strategy runner and persist strategy status to stopped."""
        runner = self.strategies.get(strategy_id)
        if runner is not None:
            runner.stop()
        self.remove_strategy(strategy_id, apply_next_issue_only=False)
        if self._next_issue_strategies is not None:
            self._next_issue_strategies.pop(strategy_id, None)
        if self._next_issue_profiles is not None:
            self._next_issue_profiles.pop(strategy_id, None)

        try:
            await strategy_update_status(
                self.db,
                strategy_id=strategy_id,
                operator_id=self.operator_id,
                status="stopped",
            )
        except Exception:
            logger.exception(
                "strategy stop persist failed strategy_id=%d account_id=%d reason=%s",
                strategy_id,
                self.account_id,
                reason,
            )
            return

        logger.info(
            "strategy stopped strategy_id=%d account_id=%d reason=%s",
            strategy_id,
            self.account_id,
            reason,
        )

    async def _mark_issue_orders_settle_failed(self, issue: str) -> None:
        """Mark lingering orders for a specific issue as settle_failed."""
        rows = await (
            await self.db.execute(
                "SELECT id, status, 0 as simulation FROM bet_orders WHERE issue=? AND account_id=? "
                "AND operator_id=? "
                "AND status IN ('bet_success', 'pending_match', 'settle_timeout') "
                "UNION ALL "
                "SELECT id, status, 1 as simulation FROM simulation_bet_orders WHERE issue=? AND account_id=? "
                "AND operator_id=? "
                "AND status IN ('bet_success', 'pending_match', 'settle_timeout')",
                (
                    issue,
                    self.account_id,
                    self.operator_id,
                    issue,
                    self.account_id,
                    self.operator_id,
                ),
            )
        ).fetchall()
        orders = [dict(r) for r in rows]
        if orders:
            await self.settler._mark_orders_settle_failed(orders)

    # ------------------------------------------------------------------
    # 璺ㄨ繘绋嬩簰鏂ラ攣
    # ------------------------------------------------------------------

    async def _acquire_lock(self) -> bool:
        """CAS 鎶㈤攣锛氱敓鎴?UUID4 token锛屽啓鍏?account_platform_sessions

        鏉′欢锛氭棤閿侊紙worker_lock_token IS NULL锛夋垨閿佽秴鏃讹紙worker_lock_ts < now - 5min锛夈€?
        浣跨敤 DB 鏃堕棿 datetime('now') 娑堥櫎搴旂敤鏃堕挓婕傜Щ銆?
        杩斿洖 True 琛ㄧず鎶㈤攣鎴愬姛锛孎alse 琛ㄧず宸叉湁娲昏穬閿併€?
        """
        token = str(uuid.uuid4())
        await account_platform_session_upsert(
            self.db,
            account_id=self.account_id,
            platform_type=self._platform_type,
            status=self.status,
        )
        cursor = await self.db.execute(
            "UPDATE account_platform_sessions "
            "SET worker_lock_token=?, worker_lock_ts=datetime('now', '+8 hours') "
            "WHERE account_id=? AND platform_type=? AND (worker_lock_token IS NULL "
            "OR worker_lock_ts < datetime('now', '+8 hours', '-5 minutes'))",
            (token, self.account_id, self._platform_type),
        )
        await self.db.commit()
        if cursor.rowcount > 0:
            self._lock_token = token
            logger.info(
                "鎶㈤攣鎴愬姛 account_id=%d platform=%s token=%s",
                self.account_id,
                self._platform_type,
                token,
            )
            return True
        logger.warning(
            "鎶㈤攣澶辫触锛堝凡鏈夋椿璺冮攣锛?account_id=%d platform=%s",
            self.account_id,
            self._platform_type,
        )
        return False

    async def _renew_lock(self) -> bool:
        """缁害閿侊細鏇存柊 worker_lock_ts锛屼粎褰撳墠鎸侀攣鑰呭彲缁害

        rowcount=0 琛ㄧず宸插け閿侊紙token 涓嶅尮閰嶏級锛岃缃?running=False 骞跺彂鍛婅銆?
        杩斿洖 True 琛ㄧず缁害鎴愬姛锛孎alse 琛ㄧず澶遍攣銆?
        """
        if self._lock_token is None:
            return False
        cursor = await self.db.execute(
            "UPDATE account_platform_sessions "
            "SET worker_lock_ts=datetime('now', '+8 hours') "
            "WHERE account_id=? AND platform_type=? AND worker_lock_token=?",
            (self.account_id, self._platform_type, self._lock_token),
        )
        await self.db.commit()
        if cursor.rowcount > 0:
            return True
        # 澶遍攣锛氱珛鍗冲仠姝?
        logger.error(
            "缁害澶辫触锛堝凡澶遍攣锛?account_id=%d platform=%s token=%s",
            self.account_id,
            self._platform_type,
            self._lock_token,
        )
        self.running = False
        await self.alert_service.send(
            operator_id=self.operator_id,
            alert_type="worker_lock_lost",
            title=f"Worker 澶遍攣 account_id={self.account_id} platform={self._platform_type}",
            detail=f"缁害澶辫触锛宲latform={self._platform_type}, token={self._lock_token}",
            account_id=self.account_id,
        )
        return False

    async def _release_lock(self) -> None:
        """Release the worker lock token from account_platform_sessions."""
        if self._lock_token is None:
            return
        try:
            await self.db.execute(
                "UPDATE account_platform_sessions "
                "SET worker_lock_token=NULL, worker_lock_ts=NULL "
                "WHERE account_id=? AND platform_type=? AND worker_lock_token=?",
                (self.account_id, self._platform_type, self._lock_token),
            )
            await self.db.commit()
            logger.info(
                "閲婃斁閿?account_id=%d platform=%s token=%s",
                self.account_id,
                self._platform_type,
                self._lock_token,
            )
        except Exception:
            logger.exception(
                "閲婃斁閿佸紓甯?account_id=%d platform=%s token=%s",
                self.account_id,
                self._platform_type,
                self._lock_token,
            )
        finally:
            self._lock_token = None

    # ------------------------------------------------------------------
    # 
    # ------------------------------------------------------------------

    def _should_bet(
        self,
        install: InstallInfo,
        *,
        bet_timing: Optional[int] = None,
    ) -> bool:
        """

        
        - State != 1  
        - close_countdown_sec <= 18s  
        - State == 1  close_countdown_sec > 18s  

        Args:
            install: 

        Returns:
            True 
        """
        #  State
        if install.state != 1:
            logger.info(
                "issue=%s state=%d account_id=%d",
                install.issue,
                install.state,
                self.account_id,
            )
            return False

        remaining = install.close_countdown_sec
        if remaining <= SKIP_THRESHOLD:
            logger.info(
                "issue=%s remaining=%ds threshold=%ds account_id=%d",
                install.issue,
                remaining,
                SKIP_THRESHOLD,
                self.account_id,
            )
            return False

        effective_bet_timing = max(
            MIN_BET_TIMING,
            min(bet_timing or self.bet_timing, BET_TIMING_MAX),
        )
        if remaining > effective_bet_timing:
            logger.info(
                "issue=%s remaining=%ds window=%ds account_id=%d",
                install.issue,
                remaining,
                effective_bet_timing,
                self.account_id,
            )
            return False

        return True

    # ------------------------------------------------------------------
    # 
    # ------------------------------------------------------------------

    def _collect_signals(
        self,
        install: InstallInfo,
        *,
        strategy_ids: Optional[list[int]] = None,
    ) -> list[BetSignal]:
        """ running """
        from app.engine.strategies.base import StrategyContext, LotteryResult

        signals: list[BetSignal] = []
        history: list[LotteryResult] = []
        if install.pre_issue and install.pre_result:
            try:
                balls, sum_value = _parse_result(install.pre_result)
                if balls:
                    history = [
                        LotteryResult(
                            issue=str(install.pre_issue),
                            balls=balls,
                            sum_value=sum_value,
                        )
                    ]
            except Exception:
                logger.warning(
                    "invalid pre_result ignored account_id=%d pre_issue=%s pre_result=%r",
                    self.account_id,
                    install.pre_issue,
                    install.pre_result,
                )

        context = StrategyContext(
            current_issue=install.issue,
            history=history,
            balance=0,
            strategy_state={},
        )

        selected_ids = strategy_ids or list(self.strategies.keys())

        for _sid in selected_ids:
            runner = self.strategies.get(_sid)
            if runner is None:
                continue
            try:
                runner_signals = runner.collect_signals(
                    ctx=context, issue=install.issue
                )
                signals.extend(runner_signals)
            except Exception:
                logger.exception(
                    "strategy_id=%d account_id=%d",
                    _sid,
                    self.account_id,
                )

        return signals

    async def _collect_signals(
        self,
        install: InstallInfo,
        *,
        strategy_ids: Optional[list[int]] = None,
    ) -> list[BetSignal]:
        """Collect signals with enough recent closed history for DW3 gates."""
        from app.engine.strategies.base import StrategyContext, LotteryResult

        selected_ids = strategy_ids or list(self.strategies.keys())
        history_limit = self._get_required_history_issues(selected_ids)
        history: list[LotteryResult] = []
        if history_limit > 0:
            history = await self._load_recent_history(install, history_limit)

        context = StrategyContext(
            current_issue=install.issue,
            history=history,
            balance=0,
            strategy_state={},
        )

        signals: list[BetSignal] = []
        self._last_signal_collection_reasons = {}
        for strategy_id in selected_ids:
            runner = self.strategies.get(strategy_id)
            if runner is None:
                continue
            try:
                runner_signals = runner.collect_signals(
                    ctx=context,
                    issue=install.issue,
                )
                signals.extend(runner_signals)
                if runner_signals:
                    continue
                metadata = getattr(runner.strategy, "last_signal_metadata", {})
                if (
                    isinstance(metadata, dict)
                    and metadata.get("strategy_kind") == "dw3"
                    and metadata.get("gate_skipped")
                ):
                    skip_reason = str(metadata.get("skip_reason") or "gate_skipped")
                    self._last_signal_collection_reasons[strategy_id] = skip_reason
            except Exception:
                logger.exception(
                    "strategy_id=%d account_id=%d",
                    strategy_id,
                    self.account_id,
                )
        return signals

    def _mark_signal_collection_reasons(self, strategy_ids: list[int]) -> None:
        for strategy_id in strategy_ids:
            reason = self._last_signal_collection_reasons.get(
                strategy_id,
                "no_signal",
            )
            self._mark_strategy_reasons([strategy_id], reason)

    def _get_required_history_issues(self, strategy_ids: list[int]) -> int:
        required = 1
        for strategy_id in strategy_ids:
            runner = self.strategies.get(strategy_id)
            if runner is None:
                continue
            strategy = getattr(runner, "strategy", None)
            history_issues = getattr(strategy, "required_history_issues", 1)
            try:
                required = max(required, int(history_issues))
            except (TypeError, ValueError):
                continue
        return required

    async def _load_recent_history(
        self,
        install: InstallInfo,
        limit: int,
    ) -> list["LotteryResult"]:
        from app.engine.strategies.base import LotteryResult

        if limit <= 0:
            return []

        history: list[LotteryResult] = []
        seen_issues: set[str] = set()

        def append_history(issue: str | None, open_result: str | None, sum_value: int | None = None) -> None:
            issue_text = str(issue or "").strip()
            result_text = str(open_result or "").strip()
            if not issue_text or not result_text or issue_text in seen_issues:
                return
            try:
                balls, parsed_sum = _parse_result(result_text)
            except Exception:
                logger.warning(
                    "invalid history ignored account_id=%d issue=%s open_result=%r",
                    self.account_id,
                    issue_text,
                    result_text,
                )
                return
            if not balls:
                return
            history.append(
                LotteryResult(
                    issue=issue_text,
                    balls=balls,
                    sum_value=int(sum_value) if sum_value is not None else parsed_sum,
                )
            )
            seen_issues.add(issue_text)

        append_history(install.pre_issue, install.pre_result)
        if len(history) >= limit:
            return history[:limit]

        query_limit = max(limit * 4, limit + 8)
        rows = await (
            await self.db.execute(
                "SELECT issue, open_result, sum_value FROM lottery_results "
                "ORDER BY CAST(issue AS INTEGER) DESC LIMIT ?",
                (query_limit,),
            )
        ).fetchall()
        for row in rows:
            append_history(row["issue"], row["open_result"], row["sum_value"])
            if len(history) >= limit:
                break
        return history[:limit]

    async def _feedback_settlement_results(self, issue: str) -> None:
        rows = await (
            await self.db.execute(
                "SELECT strategy_id, key_code, is_win, pnl, martin_level, 0 as simulation FROM bet_orders "
                "WHERE issue=? AND account_id=? AND operator_id=? "
                "AND status='settled' "
                "UNION ALL "
                "SELECT strategy_id, key_code, is_win, pnl, NULL as martin_level, 1 as simulation FROM simulation_bet_orders "
                "WHERE issue=? AND account_id=? AND operator_id=? "
                "AND status='settled'",
                (
                    issue,
                    self.account_id,
                    self.operator_id,
                    issue,
                    self.account_id,
                    self.operator_id,
                ),
            )
        ).fetchall()
        rows = [dict(row) for row in rows]

        issue_scope_rows: dict[int, list] = defaultdict(list)
        category_scope_rows: dict[tuple[int, str], list] = defaultdict(list)
        for row in rows:
            strategy_id = int(row["strategy_id"])
            runner = self.strategies.get(strategy_id)
            if runner is None:
                continue
            strategy = getattr(runner, "strategy", None)
            settlement_scope = getattr(strategy, "settlement_scope", "order")
            if settlement_scope == "issue":
                issue_scope_rows[strategy_id].append(row)
                continue
            if settlement_scope == "category":
                category_for_key_code = getattr(strategy, "category_for_key_code", None)
                category = (
                    category_for_key_code(row["key_code"])
                    if callable(category_for_key_code)
                    else None
                )
                if category:
                    category_scope_rows[(strategy_id, str(category))].append(row)
                    continue
            await self._apply_settlement_feedback(
                strategy_id,
                runner,
                row["is_win"],
                row["pnl"],
                issue,
                key_code=row["key_code"],
                martin_level=row.get("martin_level"),
            )

        for strategy_id, grouped_rows in issue_scope_rows.items():
            runner = self.strategies.get(strategy_id)
            if runner is None:
                continue
            total_pnl = sum(int(row["pnl"] or 0) for row in grouped_rows)
            if total_pnl > 0:
                issue_result = 1
            elif total_pnl < 0:
                issue_result = 0
            else:
                issue_result = -1
            martin_level = next(
                (
                    row.get("martin_level")
                    for row in grouped_rows
                    if row.get("martin_level") is not None
                ),
                None,
            )
            await self._apply_settlement_feedback(
                strategy_id,
                runner,
                issue_result,
                total_pnl,
                issue,
                martin_level=martin_level,
            )

        for (strategy_id, category), grouped_rows in category_scope_rows.items():
            runner = self.strategies.get(strategy_id)
            if runner is None:
                continue
            total_pnl = sum(int(row["pnl"] or 0) for row in grouped_rows)
            if any(int(row["is_win"] or 0) == 1 for row in grouped_rows):
                category_result = 1
            elif any(int(row["is_win"] or 0) == 0 for row in grouped_rows):
                category_result = 0
            else:
                category_result = -1
            martin_level = next(
                (
                    row.get("martin_level")
                    for row in grouped_rows
                    if row.get("martin_level") is not None
                ),
                None,
            )
            await self._apply_settlement_feedback(
                strategy_id,
                runner,
                category_result,
                total_pnl,
                issue,
                key_code=category,
                martin_level=martin_level,
            )

    async def _apply_settlement_feedback(
        self,
        strategy_id: int,
        runner: StrategyRunner,
        is_win: int | None,
        pnl: int,
        issue: str,
        *,
        key_code: str | None = None,
        martin_level: int | None = None,
    ) -> None:
        try:
            feedback_kwargs: dict[str, object] = {}
            if key_code is not None:
                feedback_kwargs["key_code"] = key_code
            if martin_level is not None:
                feedback_kwargs["martin_level"] = martin_level
            stop_request = await runner.on_result(
                is_win,
                pnl,
                **feedback_kwargs,
            )
            await self._persist_strategy_runtime_config(strategy_id, runner)
            if (
                isinstance(stop_request, StrategyStopRequest)
                and stop_request.should_stop
            ):
                if not self._should_apply_settlement_stop_request(
                    runner, stop_request
                ):
                    logger.info(
                        "settlement stop request ignored strategy_id=%d "
                        "account_id=%d reason=%s",
                        strategy_id,
                        self.account_id,
                        stop_request.reason,
                    )
                    return
                await self._stop_strategy_runner(
                    strategy_id,
                    stop_request.reason or "strategy_requested_stop",
                )
        except Exception:
            logger.exception(
                "莽录聛忙鈥櫬睹р€⒙幻┞嶁劉氓露鈥懊€郝ヂモ€毰∶喡?strategy_id=%d issue=%s account_id=%d",
                strategy_id,
                issue,
                self.account_id,
            )

    async def _persist_strategy_runtime_config(
        self,
        strategy_id: int,
        runner: StrategyRunner,
    ) -> None:
        strategy = getattr(runner, "strategy", None)
        export_config = getattr(strategy, "export_config", None)
        if not callable(export_config):
            return
        try:
            strategy_config = export_config()
            await self.db.execute(
                "UPDATE strategies SET strategy_config=?, updated_at=datetime('now', '+8 hours') "
                "WHERE id=? AND operator_id=?",
                (
                    json.dumps(strategy_config, ensure_ascii=False),
                    strategy_id,
                    self.operator_id,
                ),
            )
            await self.db.commit()
        except Exception:
            logger.exception(
                "persist strategy runtime config failed strategy_id=%d account_id=%d",
                strategy_id,
                self.account_id,
            )


