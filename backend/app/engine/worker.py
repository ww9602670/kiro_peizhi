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
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

import aiosqlite

from app.engine.adapters.base import InstallInfo, PlatformAdapter
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
)

logger = logging.getLogger(__name__)

# 
MIN_BET_TIMING = BET_TIMING_MIN
DEFAULT_BET_TIMING = 30   #  30s
DEADLINE_MARGIN = 10      #  10s 
SKIP_THRESHOLD = SAFE_CLOSE_THRESHOLD

# 
RESTART_DELAYS = [5, 10, 30]
MAX_RESTART_FAILURES = 5

# 倒计时驱动结算配置
SETTLEMENT_WAIT_SECONDS_DEFAULT = 30
SETTLEMENT_WAIT_SECONDS_MIN = 10
SETTLEMENT_WAIT_SECONDS_MAX = 120

# 结算数据拉取重试
SETTLE_DATA_RETRY_MAX = 6
SETTLE_DATA_RETRY_INTERVAL = 5

# GetCurrentInstall 网络重试
API_RETRY_DELAYS = [5, 10, 30]
API_RETRY_MAX = 3

# 跨进程互斥锁
LOCK_TTL_MINUTES = 5
LOCK_RENEW_INTERVAL = 60  # 秒


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

        # 结算模式
        self.settling_only: bool = False
        self._settling_deadline: float | None = None
        self._on_settle_complete: Optional[asyncio.coroutines] = None  # 结算完成回调

    # ------------------------------------------------------------------
    # 
    # ------------------------------------------------------------------

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
            return install, "precheck_failed"

        if refreshed.issue != install.issue:
            return refreshed, "issue_changed"
        if refreshed.state != 1:
            return refreshed, "state_not_open"
        if refreshed.close_countdown_sec <= SKIP_THRESHOLD:
            return refreshed, "remaining_too_small"
        if refreshed.close_countdown_sec > bet_timing:
            return refreshed, "window_not_open"
        return refreshed, None

    async def _has_unsettled_orders(self) -> bool:
        """检查是否有未结算订单（bet_success 或 pending_match）"""
        row = await (
            await self.db.execute(
                "SELECT COUNT(*) as cnt FROM bet_orders "
                "WHERE account_id=? AND operator_id=? "
                "AND status IN ('bet_success', 'pending_match')",
                (self.account_id, self.operator_id),
            )
        ).fetchone()
        return (row["cnt"] if row else 0) > 0

    async def enter_settling_mode(self) -> None:
        """进入结算模式：停止投注，继续结算

        设置 settling_only 标志，清空策略列表，设置 10 分钟超时。
        主循环将跳过投注阶段，仅执行结算和对账。
        """
        self.settling_only = True
        self.status = "settling"
        self._settling_deadline = time.time() + 600  # 10 分钟超时

        # 清空策略，防止产生投注信号
        self.strategies.clear()
        self.strategy_profiles.clear()
        self._issue_execution_plan = None
        self._next_issue_profiles = None
        self._next_issue_strategies = None

        # 记录日志：待结算期号和订单数量
        try:
            rows = await (
                await self.db.execute(
                    "SELECT issue, COUNT(*) as cnt FROM bet_orders "
                    "WHERE account_id=? AND operator_id=? "
                    "AND status IN ('bet_success', 'pending_match') "
                    "GROUP BY issue",
                    (self.account_id, self.operator_id),
                )
            ).fetchall()
            issues_info = {r["issue"]: r["cnt"] for r in rows}
            total = sum(issues_info.values())
            logger.info(
                "Worker 进入结算模式 account_id=%d 待结算期号=%s 总订单数=%d 超时=%ds",
                self.account_id,
                issues_info,
                total,
                600,
            )
        except Exception:
            logger.exception(
                "结算模式日志记录异常 account_id=%d", self.account_id,
            )

    # ------------------------------------------------------------------
    # 
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """ Worker （含抢锁）"""
        if self.running:
            logger.warning("Worker account_id=%d", self.account_id)
            return

        # 抢锁
        acquired = await self._acquire_lock()
        if not acquired:
            await self.alert_service.send(
                operator_id=self.operator_id,
                alert_type="worker_lock_conflict",
                title=f"Worker 抢锁冲突 account_id={self.account_id}",
                detail="已有活跃锁，拒绝启动",
                account_id=self.account_id,
            )
            logger.error(
                "Worker 启动失败（锁冲突） account_id=%d",
                self.account_id,
            )
            return

        self.running = True
        self.status = "running"
        self._restart_count = 0
        self._task = asyncio.create_task(self._run_with_restart())
        logger.info(
            "Worker operator_id=%d account_id=%d",
            self.operator_id,
            self.account_id,
        )

    async def stop(self) -> None:
        """强制停止 Worker（cancel task + 释放锁）

        结算逻辑由结算模式的主循环处理，stop() 不再调用 _settle_before_stop()。
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
            "Worker 已停止 operator_id=%d account_id=%d",
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
            except asyncio.CancelledError:
                logger.info(
                    "Worker account_id=%d", self.account_id
                )
                break
            except Exception:
                self._restart_count += 1
                logger.exception(
                    "Worker account_id=%d restart_count=%d",
                    self.account_id,
                    self._restart_count,
                )
                if self._restart_count >= MAX_RESTART_FAILURES:
                    self.status = "error"
                    self.running = False
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

    # ------------------------------------------------------------------
    # 主循环（倒计时驱动模式）
    # ------------------------------------------------------------------

    async def _main_loop(self) -> None:
        """倒计时驱动主循环

        流程：login → 全新启动检测 → 补结算 → 循环(fetch → bet → sleep → settle → reconcile)
        """
        logger.info(
            "启动 Worker operator_id=%d account_id=%d",
            self.operator_id,
            self.account_id,
        )
        await self.session.login()
        self._restart_count = 0

        # 全新启动检测（AC1.5）
        await self._detect_fresh_start()

        # 补结算
        await self._recover_unsettled_orders()

        logger.info(
            "进入倒计时循环 Worker operator_id=%d account_id=%d",
            self.operator_id,
            self.account_id,
        )

        while self.running:
            # 0. 锁续约（每次循环迭代开始时）
            if not await self._renew_lock():
                break  # 失锁，退出循环

            # 1. 获取当前期号信息
            install = await self._fetch_install_with_retry()
            if install is None:
                await asyncio.sleep(60)
                continue

            # 2. 记录当前期号（投注的是 install.issue，结算时需要验证该期号的开奖结果）
            pre_issue = install.issue

            # 3. 投注阶段（结算模式下跳过）
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
                                "投注异常 issue=%s account_id=%d",
                                install.issue,
                                self.account_id,
                            )

            # 4. 等待开奖倒计时归零
            if (
                settlement_anchor.issue == pre_issue
                and settlement_anchor.open_countdown_sec > 0
            ):
                await asyncio.sleep(settlement_anchor.open_countdown_sec)

            # 5. 额外等待 settlement_wait_seconds
            await asyncio.sleep(self._settlement_wait_seconds)

            # 6. 拉取新期号 + 上期开奖结果
            new_install = await self._fetch_settlement_data(pre_issue)
            if new_install is None:
                continue  # 已发告警，跳过本期

            # 7. 持久化开奖结果
            balls, sum_value = _parse_result(new_install.pre_result)
            await self.settler._save_lottery_result(
                new_install.pre_issue,
                new_install.pre_result,
                sum_value,
            )

            # 8. 执行结算
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
                    "结算异常 issue=%s account_id=%d",
                    new_install.pre_issue,
                    self.account_id,
                )

            # 8.5 结算结果反馈给策略（驱动马丁倍增）
            await self._feedback_settlement_results(new_install.pre_issue)

            # 9. 对账
            try:
                await self.reconciler.reconcile(
                    issue=new_install.pre_issue,
                    account_id=self.account_id,
                )
            except Exception:
                logger.exception(
                    "对账异常 issue=%s account_id=%d",
                    new_install.pre_issue,
                    self.account_id,
                )

            # 10. 结算模式检查
            if self.settling_only:
                # 超时检查
                if self._settling_deadline and time.time() > self._settling_deadline:
                    logger.warning(
                        "结算模式超时 account_id=%d", self.account_id,
                    )
                    await self._handle_settling_timeout()
                    await self._cleanup_after_settling()
                    break

                # 检查是否还有未结算订单
                if not await self._has_unsettled_orders():
                    logger.info(
                        "结算模式完成：所有订单已结算 account_id=%d",
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
                    )
                    if revalidate_reason == "window_not_open":
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
        """获取当前期号信息，网络异常时按 5s → 10s → 30s 重试

        最多 3 次，全部失败发 api_call_failed 告警并返回 None。
        """
        for attempt in range(API_RETRY_MAX):
            try:
                return await self.poller.poll()
            except Exception:
                logger.exception(
                    "GetCurrentInstall 失败 attempt=%d/%d account_id=%d",
                    attempt + 1,
                    API_RETRY_MAX,
                    self.account_id,
                )
                if attempt < API_RETRY_MAX - 1:
                    delay = API_RETRY_DELAYS[attempt]
                    await asyncio.sleep(delay)

        # 全部失败
        await self.alert_service.send(
            operator_id=self.operator_id,
            alert_type="api_call_failed",
            title=f"GetCurrentInstall 调用失败 account_id={self.account_id}",
            detail=f"{API_RETRY_MAX} 次重试全部失败",
            account_id=self.account_id,
        )
        return None

    # ------------------------------------------------------------------
    # 7.3 _fetch_settlement_data
    # ------------------------------------------------------------------

    async def _fetch_settlement_data(self, expected_pre_issue: str) -> Optional[InstallInfo]:
        """拉取新期号，验证 PreLotteryResult 有效性，最多重试 6 次

        全部失败时：real 订单标记 settle_failed + sim 订单用 check_win 降级结算 + 发告警。
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

        # 6 次重试失败 → 降级处理
        logger.warning(
            "结算数据缺失 期号=%s account_id=%d，执行降级处理",
            expected_pre_issue,
            self.account_id,
        )
        await self._handle_settlement_data_missing(expected_pre_issue)

        await self.alert_service.send(
            operator_id=self.operator_id,
            alert_type="settlement_data_missing",
            title=f"结算数据缺失 期号 {expected_pre_issue}",
            detail=f"重试 {SETTLE_DATA_RETRY_MAX} 次后仍无有效开奖结果",
            account_id=self.account_id,
        )
        return None

    async def _handle_settlement_data_missing(self, issue: str) -> None:
        """结算数据缺失时的降级处理

        real 订单标记 settle_failed，sim 订单用 check_win 降级结算。
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

        # real 订单标记 settle_failed
        if real_orders:
            await self.settler._mark_orders_settle_failed(real_orders)

        # sim 订单用 check_win 降级结算（无开奖结果，无法计算，也标记 settle_failed）
        # 注意：AC1.4 说 sim 订单使用本地 check_win 降级结算，但无开奖结果时无法计算
        # 设计文档说 sim 订单用 check_win 降级结算，但这需要开奖结果
        # 这里 sim 订单也标记 settle_failed（因为没有开奖数据无法计算）
        if sim_orders:
            await self.settler._mark_orders_settle_failed(sim_orders)

    # ------------------------------------------------------------------
    # 7.4 全新启动检测（AC1.5）
    # ------------------------------------------------------------------

    async def _detect_fresh_start(self) -> None:
        """全新启动检测

        若数据库中无该账号的 bet_orders 记录，调用 GetCurrentInstall
        获取当前期号记录为 last_issue，从下一期开始正常循环。
        """
        row = await (
            await self.db.execute(
                "SELECT COUNT(*) as cnt FROM bet_orders "
                "WHERE account_id=? AND operator_id=?",
                (self.account_id, self.operator_id),
            )
        ).fetchone()

        count = row["cnt"] if row else 0
        if count == 0:
            # 全新启动：记录当前期号为 last_issue
            try:
                install = await self.poller.poll()
                self.poller.last_issue = install.issue
                logger.info(
                    "全新启动检测：无历史记录，记录 last_issue=%s account_id=%d",
                    install.issue,
                    self.account_id,
                )
            except Exception:
                logger.exception(
                    "全新启动检测：获取当前期号失败 account_id=%d",
                    self.account_id,
                )

    # ------------------------------------------------------------------
    # 7.5 _recover_unsettled_orders
    # ------------------------------------------------------------------

    async def _recover_unsettled_orders(self) -> None:
        """重启时补结算：扫描未结算订单，按 issue 分组，获取历史开奖结果

        有结果则 settle(is_recovery=True)；无结果且订单距今超过3分钟则标记 settle_failed；
        距今不超过3分钟的新订单跳过，留给正常结算周期处理。
        """
        rows = await (
            await self.db.execute(
                "SELECT DISTINCT issue FROM bet_orders "
                "WHERE account_id=? AND operator_id=? "
                "AND status IN ('bet_success', 'pending_match', 'settle_timeout')",
                (self.account_id, self.operator_id),
            )
        ).fetchall()
        issues = [r["issue"] for r in rows]
        if not issues:
            return

        logger.info(
            "补结算开始：%d 个期号待处理 account_id=%d",
            len(issues),
            self.account_id,
        )

        # 获取历史开奖结果
        try:
            results = await self.adapter.get_lottery_results(count=50)
        except Exception:
            logger.exception(
                "补结算：获取历史开奖结果失败 account_id=%d", self.account_id,
            )
            results = []

        result_map = {str(r.get("Installments", "")): r.get("OpenResult", "") for r in results}

        for issue in issues:
            open_result = result_map.get(issue)
            if open_result and open_result.strip():
                # 有开奖结果 → 执行补结算
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
                    logger.info("补结算完成 issue=%s account_id=%d", issue, self.account_id)
                except Exception:
                    logger.exception(
                        "补结算异常 issue=%s account_id=%d", issue, self.account_id,
                    )
            else:
                # 无开奖结果 → 检查订单年龄，新订单跳过
                if await self._has_recent_orders(issue, max_age_seconds=180):
                    logger.info(
                        "补结算跳过：期号 %s 有近3分钟内的新订单，留给正常周期 account_id=%d",
                        issue, self.account_id,
                    )
                    continue

                # 老订单 → 标记 settle_failed + 发告警
                await self._mark_issue_orders_settle_failed(issue)
                await self.alert_service.send(
                    operator_id=self.operator_id,
                    alert_type="settle_data_expired",
                    title=f"补结算数据过期 期号 {issue}",
                    detail=f"历史开奖结果中无 issue={issue} 的记录，且订单已超过3分钟",
                    account_id=self.account_id,
                )

    async def _has_recent_orders(self, issue: str, max_age_seconds: int = 180) -> bool:
        """检查指定期号是否有距今不超过 max_age_seconds 的订单

        用于补结算时区分"刚下注还没开奖"和"真正过期"的订单。
        """
        from datetime import datetime, timezone, timedelta
        _bjt = timezone(timedelta(hours=8))

        rows = await (
            await self.db.execute(
                "SELECT bet_at, created_at FROM bet_orders "
                "WHERE issue=? AND account_id=? AND operator_id=? "
                "AND status IN ('bet_success', 'pending_match', 'settle_timeout') "
                "ORDER BY bet_at DESC LIMIT 1",
                (issue, self.account_id, self.operator_id),
            )
        ).fetchall()

        if not rows:
            return False

        row = rows[0]
        # 优先用 bet_at，其次 created_at
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
        """结算模式超时处理

        将所有未结算订单标记为 settle_failed，发送告警，停止 Worker。
        """
        rows = await (
            await self.db.execute(
                "SELECT * FROM bet_orders WHERE account_id=? AND operator_id=? "
                "AND status IN ('bet_success', 'pending_match')",
                (self.account_id, self.operator_id),
            )
        ).fetchall()
        orders = [dict(r) for r in rows]

        if orders:
            await self.settler._mark_orders_settle_failed(orders)
            logger.warning(
                "结算模式超时：%d 笔订单标记为 settle_failed account_id=%d",
                len(orders),
                self.account_id,
            )

        # 发送超时告警
        elapsed = int(time.time() - (self._settling_deadline - 600)) if self._settling_deadline else 0
        await self.alert_service.send(
            operator_id=self.operator_id,
            alert_type="settling_mode_timeout",
            title=f"结算模式超时 account_id={self.account_id}",
            detail=f"等待 {elapsed}s 后超时，{len(orders)} 笔订单标记为 settle_failed",
            account_id=self.account_id,
        )

        self.running = False
        self.status = "stopped"

    async def _cleanup_after_settling(self) -> None:
        """结算模式退出后的清理：释放锁、从 registry 注销"""
        await self._release_lock()
        if self._on_settle_complete:
            try:
                await self._on_settle_complete(self.account_id)
            except Exception:
                logger.exception(
                    "结算完成回调异常 account_id=%d", self.account_id,
                )

    async def _settle_before_stop(self) -> None:
        """停止前补结算：扫描所有已下注但未结算的订单，尝试结算

        与 _recover_unsettled_orders 类似，但在停止时调用。
        先尝试获取开奖结果进行正常结算，如果获取不到（还没开奖），
        则标记为 pending_match 等待下次启动时补结算。
        """
        rows = await (
            await self.db.execute(
                "SELECT DISTINCT issue FROM bet_orders "
                "WHERE account_id=? AND operator_id=? "
                "AND status IN ('bet_success', 'pending_match')",
                (self.account_id, self.operator_id),
            )
        ).fetchall()
        issues = [r["issue"] for r in rows]
        if not issues:
            logger.info(
                "停止前补结算：无待结算订单 account_id=%d",
                self.account_id,
            )
            return

        logger.info(
            "停止前补结算：%d 个期号待处理 account_id=%d",
            len(issues),
            self.account_id,
        )

        # 获取历史开奖结果
        try:
            results = await self.adapter.get_lottery_results(count=50)
        except Exception:
            logger.exception(
                "停止前补结算：获取历史开奖结果失败 account_id=%d",
                self.account_id,
            )
            results = []

        result_map = {
            str(r.get("Installments", "")): r.get("OpenResult", "")
            for r in results
        }

        # 也尝试从 GetCurrentInstall 获取上期结果
        try:
            install = await self.poller.poll()
            if install and install.pre_issue and install.pre_result:
                result_map[str(install.pre_issue)] = install.pre_result
        except Exception:
            logger.warning(
                "停止前补结算：获取当前期号失败 account_id=%d",
                self.account_id,
            )

        settled_count = 0
        pending_count = 0
        for issue in issues:
            open_result = result_map.get(issue)
            if open_result and open_result.strip():
                # 有开奖结果 → 执行结算
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
                        "停止前补结算完成 issue=%s account_id=%d",
                        issue,
                        self.account_id,
                    )
                except Exception:
                    logger.exception(
                        "停止前补结算异常 issue=%s account_id=%d",
                        issue,
                        self.account_id,
                    )
            else:
                # 还没开奖 → 标记为 pending_match，下次启动时补结算
                pending_count += 1
                logger.info(
                    "停止前补结算：期号 %s 尚未开奖，保持待结算状态 account_id=%d",
                    issue,
                    self.account_id,
                )

        logger.info(
            "停止前补结算完成：已结算=%d 待下次补结算=%d account_id=%d",
            settled_count,
            pending_count,
            self.account_id,
        )

    async def _feedback_settlement_results(self, issue: str) -> None:
        """结算后将结果反馈给对应的 StrategyRunner

        查询该期号已结算订单，按 strategy_id 分发 on_result。
        用于驱动马丁策略的 level 推进。
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
                    "结算反馈跳过：strategy_id=%d 无对应 runner account_id=%d",
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
                    "结算反馈异常 strategy_id=%d issue=%s account_id=%d",
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
        if strategy_name == "red_wave_double_martin":
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
        """将指定期号下所有未结算订单标记为 settle_failed"""
        rows = await (
            await self.db.execute(
                "SELECT * FROM bet_orders WHERE issue=? AND account_id=? "
                "AND operator_id=? "
                "AND status IN ('bet_success', 'pending_match', 'settle_timeout')",
                (issue, self.account_id, self.operator_id),
            )
        ).fetchall()
        orders = [dict(r) for r in rows]
        if orders:
            await self.settler._mark_orders_settle_failed(orders)

    # ------------------------------------------------------------------
    # 跨进程互斥锁
    # ------------------------------------------------------------------

    async def _acquire_lock(self) -> bool:
        """CAS 抢锁：生成 UUID4 token，写入 account_platform_sessions

        条件：无锁（worker_lock_token IS NULL）或锁超时（worker_lock_ts < now - 5min）。
        使用 DB 时间 datetime('now') 消除应用时钟漂移。
        返回 True 表示抢锁成功，False 表示已有活跃锁。
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
                "抢锁成功 account_id=%d platform=%s token=%s",
                self.account_id,
                self._platform_type,
                token,
            )
            return True
        logger.warning(
            "抢锁失败（已有活跃锁） account_id=%d platform=%s",
            self.account_id,
            self._platform_type,
        )
        return False

    async def _renew_lock(self) -> bool:
        """续约锁：更新 worker_lock_ts，仅当前持锁者可续约

        rowcount=0 表示已失锁（token 不匹配），设置 running=False 并发告警。
        返回 True 表示续约成功，False 表示失锁。
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
        # 失锁：立即停止
        logger.error(
            "续约失败（已失锁） account_id=%d platform=%s token=%s",
            self.account_id,
            self._platform_type,
            self._lock_token,
        )
        self.running = False
        await self.alert_service.send(
            operator_id=self.operator_id,
            alert_type="worker_lock_lost",
            title=f"Worker 失锁 account_id={self.account_id} platform={self._platform_type}",
            detail=f"续约失败，platform={self._platform_type}, token={self._lock_token}",
            account_id=self.account_id,
        )
        return False

    async def _release_lock(self) -> None:
        """释放锁：仅当前持锁者可释放（WHERE worker_lock_token=self._lock_token）"""
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
                "释放锁 account_id=%d platform=%s token=%s",
                self.account_id,
                self._platform_type,
                self._lock_token,
            )
        except Exception:
            logger.exception(
                "释放锁异常 account_id=%d platform=%s token=%s",
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
                "SELECT strategy_id, key_code, is_win, pnl, martin_level FROM bet_orders "
                "WHERE issue=? AND account_id=? AND operator_id=? "
                "AND status='settled'",
                (issue, self.account_id, self.operator_id),
            )
        ).fetchall()

        issue_scope_rows: dict[int, list] = defaultdict(list)
        for row in rows:
            strategy_id = int(row["strategy_id"])
            runner = self.strategies.get(strategy_id)
            if runner is None:
                continue
            strategy = getattr(runner, "strategy", None)
            if getattr(strategy, "settlement_scope", "order") == "issue":
                issue_scope_rows[strategy_id].append(row)
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
                "ç¼æ’¶ç•»é™å¶‰î›­å¯®å‚šçˆ¶ strategy_id=%d issue=%s account_id=%d",
                strategy_id,
                issue,
                self.account_id,
            )
