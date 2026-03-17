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
import logging
import time
import uuid
from typing import Optional

import aiosqlite

from app.engine.adapters.base import InstallInfo, PlatformAdapter
from app.engine.alert import AlertService
from app.engine.executor import BetExecutor
from app.engine.poller import IssuePoller
from app.engine.reconciler import Reconciler
from app.engine.risk import RiskController
from app.engine.session import SessionManager
from app.engine.settlement import SettlementProcessor
from app.engine.strategy_runner import BetSignal, StrategyRunner

logger = logging.getLogger(__name__)

# 
MIN_BET_TIMING = 5       #  5s
DEFAULT_BET_TIMING = 30   #  30s
DEADLINE_MARGIN = 10      #  10s 
SKIP_THRESHOLD = 18       # CloseTimeStamp  18s 

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
        self._platform_type = platform_type
        self._settlement_wait_seconds = max(
            SETTLEMENT_WAIT_SECONDS_MIN,
            min(settlement_wait_seconds, SETTLEMENT_WAIT_SECONDS_MAX),
        )

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

    def add_strategy(self, strategy_id: int, runner: StrategyRunner) -> None:
        """"""
        self.strategies[strategy_id] = runner

    def remove_strategy(self, strategy_id: int) -> None:
        """"""
        self.strategies.pop(strategy_id, None)

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
            if not self.settling_only:
                if install.state == 1 and self._should_bet(install):
                    signals = self._collect_signals(install)
                    if signals:
                        try:
                            await self.executor.execute(install, signals)
                        except Exception:
                            logger.exception(
                                "投注异常 issue=%s account_id=%d",
                                install.issue,
                                self.account_id,
                            )

            # 4. 等待开奖倒计时归零
            if install.open_countdown_sec > 0:
                await asyncio.sleep(install.open_countdown_sec)

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
                "SELECT strategy_id, is_win, pnl FROM bet_orders "
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
                await runner.on_result(row["is_win"], row["pnl"])
            except Exception:
                logger.exception(
                    "结算反馈异常 strategy_id=%d issue=%s account_id=%d",
                    sid, issue, self.account_id,
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
        """CAS 抢锁：生成 UUID4 token，写入 gambling_accounts

        条件：无锁（worker_lock_token IS NULL）或锁超时（worker_lock_ts < now - 5min）。
        使用 DB 时间 datetime('now') 消除应用时钟漂移。
        返回 True 表示抢锁成功，False 表示已有活跃锁。
        """
        token = str(uuid.uuid4())
        cursor = await self.db.execute(
            "UPDATE gambling_accounts "
            "SET worker_lock_token=?, worker_lock_ts=datetime('now', '+8 hours') "
            "WHERE id=? AND (worker_lock_token IS NULL "
            "OR worker_lock_ts < datetime('now', '+8 hours', '-5 minutes'))",
            (token, self.account_id),
        )
        await self.db.commit()
        if cursor.rowcount > 0:
            self._lock_token = token
            logger.info(
                "抢锁成功 account_id=%d token=%s",
                self.account_id,
                token,
            )
            return True
        logger.warning(
            "抢锁失败（已有活跃锁） account_id=%d",
            self.account_id,
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
            "UPDATE gambling_accounts "
            "SET worker_lock_ts=datetime('now', '+8 hours') "
            "WHERE id=? AND worker_lock_token=?",
            (self.account_id, self._lock_token),
        )
        await self.db.commit()
        if cursor.rowcount > 0:
            return True
        # 失锁：立即停止
        logger.error(
            "续约失败（已失锁） account_id=%d token=%s",
            self.account_id,
            self._lock_token,
        )
        self.running = False
        await self.alert_service.send(
            operator_id=self.operator_id,
            alert_type="worker_lock_lost",
            title=f"Worker 失锁 account_id={self.account_id}",
            detail=f"续约失败，token={self._lock_token}",
            account_id=self.account_id,
        )
        return False

    async def _release_lock(self) -> None:
        """释放锁：仅当前持锁者可释放（WHERE worker_lock_token=self._lock_token）"""
        if self._lock_token is None:
            return
        try:
            await self.db.execute(
                "UPDATE gambling_accounts "
                "SET worker_lock_token=NULL, worker_lock_ts=NULL "
                "WHERE id=? AND worker_lock_token=?",
                (self.account_id, self._lock_token),
            )
            await self.db.commit()
            logger.info(
                "释放锁 account_id=%d token=%s",
                self.account_id,
                self._lock_token,
            )
        except Exception:
            logger.exception(
                "释放锁异常 account_id=%d token=%s",
                self.account_id,
                self._lock_token,
            )
        finally:
            self._lock_token = None

    # ------------------------------------------------------------------
    # 
    # ------------------------------------------------------------------

    def _should_bet(self, install: InstallInfo) -> bool:
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

        # 
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

        return True

    # ------------------------------------------------------------------
    # 
    # ------------------------------------------------------------------

    def _collect_signals(self, install: InstallInfo) -> list[BetSignal]:
        """ running """
        from app.engine.strategies.base import StrategyContext, LotteryResult

        signals: list[BetSignal] = []
        context = StrategyContext(
            current_issue=install.issue,
            history=[],
            balance=0,
            strategy_state={},
        )

        for _sid, runner in self.strategies.items():
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
