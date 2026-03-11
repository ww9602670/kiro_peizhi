"""SettlementProcessor


1.  key_code_map.check_win
2. JND282  / JND28WEB 
3. 
4.  + DB 
5. daily_pnl / total_pnl
6. lottery_results 

INTEGER1=100
1000 
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, date
from typing import TYPE_CHECKING

import aiosqlite

from app.engine.adapters.config import PLATFORM_CONFIGS
from app.models.db_ops import (
    account_update,
    bet_order_update_status,
    lottery_result_save,
    strategy_get_by_id,
    strategy_update_pnl,
)
from app.utils.key_code_map import check_win

if TYPE_CHECKING:
    from app.engine.adapters.base import PlatformAdapter

logger = logging.getLogger(__name__)


# 
# 
# 

class IllegalStateTransition(Exception):
    """"""

    def __init__(self, current_status: str, new_status: str) -> None:
        self.current_status = current_status
        self.new_status = new_status
        super().__init__(
            f": {current_status}  {new_status}"
        )


# 
# 
# 

VALID_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"betting"},
    "betting": {"bet_success", "bet_failed"},
    "bet_success": {"settling", "pending_match", "settle_timeout", "settle_failed"},
    "settling": {"settled", "settle_failed", "settle_timeout"},
    "pending_match": {"settling", "settle_timeout"},
    "settled": {"reconcile_error"},
    # 终态
    "bet_failed": set(),
    "settle_timeout": {"settling"},   # 高优先级来源可覆盖（补结算获取到平台数据）
    "settle_failed": {"settling"},    # 高优先级来源可覆盖（API 恢复后重新结算）
    "reconcile_error": set(),
}

TERMINAL_STATES = {"bet_failed", "settled", "settle_timeout", "settle_failed", "reconcile_error"}

# pending_match wall-clock 超时：30 分钟
PENDING_MATCH_WALL_CLOCK_TIMEOUT = 30 * 60  # 秒

# 来源优先级：数值越小优先级越高
SOURCE_PRIORITY_PLATFORM = 1   # 平台数据匹配 → settled (match_source=platform)
SOURCE_PRIORITY_LOCAL = 2      # 本地自计算 → settled (match_source=local)
SOURCE_PRIORITY_TIMEOUT = 3    # 超时/故障 → settle_timeout / settle_failed

# 不可覆盖的终态（settled/bet_failed/reconcile_error 不允许被任何来源覆盖）
NON_OVERRIDABLE_TERMINAL_STATES = {"settled", "bet_failed", "reconcile_error"}

# 可覆盖的终态（settle_timeout/settle_failed 可被高优先级来源覆盖）
OVERRIDABLE_TERMINAL_STATES = {"settle_timeout", "settle_failed"}

# 终态 → 来源优先级映射
_TERMINAL_STATE_PRIORITY: dict[str, int] = {
    "settled": SOURCE_PRIORITY_PLATFORM,       # settled 最高优先级（不可覆盖）
    "settle_timeout": SOURCE_PRIORITY_TIMEOUT,
    "settle_failed": SOURCE_PRIORITY_TIMEOUT,
}


# 
# 
# 

class SettleResult:
    """"""

    __slots__ = ("is_win", "pnl")

    def __init__(self, is_win: int, pnl: int) -> None:
        self.is_win = is_win  # 1=, 0=, -1=
        self.pnl = pnl        # 


# 
# SettlementProcessor
# 

class SettlementProcessor:
    """"""

    def __init__(self, db: aiosqlite.Connection, operator_id: int, alert_service=None) -> None:
        self.db = db
        self.operator_id = operator_id
        self.alert_service = alert_service

    # ==================================================================
    # 
    # ==================================================================

    async def settle(
        self,
        issue: str,
        balls: list[int],
        sum_value: int,
        platform_type: str,
        adapter: PlatformAdapter | None = None,
        is_recovery: bool = False,
    ) -> None:
        """结算入口：按 simulation 分发到真实/模拟路径

        adapter: 平台适配器，真实投注结算时需要（Task 4 实现 _settle_real）
        is_recovery: True 时为补结算模式，额外包含 settle_timeout/settle_failed 订单
        """
        # 1. 保存开奖结果
        open_result = ",".join(str(b) for b in balls)
        await self._save_lottery_result(issue, open_result, sum_value)

        # 2. 查询待结算订单（补结算模式包含可恢复终态）
        orders = await self._get_settleable_orders(issue, include_recoverable=is_recovery)
        if not orders:
            logger.info("issue=%s 无待结算订单", issue)
            return

        # 3. 按 simulation 分组
        real_orders = [o for o in orders if o.get("simulation", 0) == 0]
        sim_orders = [o for o in orders if o.get("simulation", 0) == 1]

        # 4. 先模拟后真实，两组在独立事务中完成
        if sim_orders:
            await self._settle_simulated(sim_orders, balls, sum_value, platform_type, open_result)

        if real_orders:
            if adapter is not None:
                await self._settle_real(real_orders, issue, adapter, open_result, sum_value)
            else:
                # 无 adapter 时使用本地计算（向后兼容 + 降级）
                await self._settle_simulated(real_orders, balls, sum_value, platform_type, open_result)

    # ==================================================================
    # 7.3.2  key_code_map
    # ==================================================================

    @staticmethod
    def _check_win(key_code: str, balls: list[int], sum_value: int) -> bool:
        """ key_code_map.check_win"""
        return check_win(key_code, balls, sum_value)

    # ==================================================================
    # 7.3.3 
    # ==================================================================

    @staticmethod
    def _calculate_result(
        order: dict,
        balls: list[int],
        sum_value: int,
        platform_type: str,
    ) -> SettleResult:
        """

        JND282 
          14  DX1/DS4/ZH8 
          13  DX2/DS3/ZH9 
        JND28WEB
        """
        key_code = order["key_code"]

        #  JND282
        config = PLATFORM_CONFIGS.get(platform_type, {})
        refund_rules: dict[int, set[str]] = config.get("refund_rules", {})

        if refund_rules:
            refund_codes = refund_rules.get(sum_value)
            if refund_codes and key_code in refund_codes:
                return SettleResult(is_win=-1, pnl=0)

        # 
        is_win = check_win(key_code, balls, sum_value)

        # 7.3.4 
        amount: int = order["amount"]
        odds: int = order["odds"]

        if is_win:
            pnl = amount * odds // 10000 - amount
        else:
            pnl = -amount

        return SettleResult(is_win=1 if is_win else 0, pnl=pnl)

    # ==================================================================
    # 7.3.5 
    # ==================================================================

    @staticmethod
    def _transition_status(order: dict, new_status: str) -> None:
        """ IllegalStateTransition

         + DB BEFORE UPDATE 
        bet_failed / reconcile_error
        settled   reconcile_error
        """
        current = order["status"]

        #  allowed 
        allowed = VALID_TRANSITIONS.get(current, set())
        if new_status not in allowed:
            raise IllegalStateTransition(current, new_status)

    # ==================================================================
    # 
    # ==================================================================

    async def _get_settleable_orders(self, issue: str, include_recoverable: bool = False) -> list[dict]:
        """查询可结算订单

        默认模式（正常结算）：status IN ('bet_success', 'pending_match')
        恢复模式（补结算）：额外包含 settle_timeout, settle_failed（可被高优先级覆盖）

        已在不可覆盖终态（settled, bet_failed, reconcile_error）的订单不会被查出。
        """
        statuses = ['bet_success', 'pending_match']
        if include_recoverable:
            statuses.extend(['settle_timeout', 'settle_failed'])

        placeholders = ','.join('?' * len(statuses))
        rows = await (
            await self.db.execute(
                f"SELECT * FROM bet_orders WHERE issue=? AND operator_id=? "
                f"AND status IN ({placeholders})",
                (issue, self.operator_id, *statuses),
            )
        ).fetchall()
        return [dict(r) for r in rows]

    async def _settle_order(
        self,
        order: dict,
        result: SettleResult,
        open_result: str,
        sum_value: int,
    ) -> None:
        """"""
        # 两步状态转换: current → settling → settled
        current_status = order["status"]
        if current_status != "settling":
            self._transition_status(order, "settling")
            order = dict(order, status="settling")
        self._transition_status(order, "settled")

        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        await bet_order_update_status(
            self.db,
            order_id=order["id"],
            operator_id=self.operator_id,
            status="settled",
            is_win=result.is_win,
            pnl=result.pnl,
            open_result=open_result,
            sum_value=sum_value,
            settled_at=now_str,
            match_source="local",
        )
        logger.info(
            "order_id=%dis_win=%dpnl=%d",
            order["id"], result.is_win, result.pnl,
        )

    # ==================================================================
    # 模拟订单组结算（保留现有逻辑，独立事务）
    # ==================================================================

    async def _settle_simulated(
        self,
        orders: list[dict],
        balls: list[int],
        sum_value: int,
        platform_type: str,
        open_result: str,
    ) -> None:
        """模拟投注结算：使用本地 check_win + odds 自计算

        结算完成后所有订单 match_source="local"。
        """
        strategy_pnl_map: dict[int, int] = {}
        account_balance_delta: dict[int, int] = {}

        for order in orders:
            result = self._calculate_result(order, balls, sum_value, platform_type)
            await self._settle_order(order, result, open_result, sum_value)

            aid = order["account_id"]
            balance_change = order["amount"] + result.pnl
            account_balance_delta[aid] = account_balance_delta.get(aid, 0) + balance_change

            sid = order["strategy_id"]
            if result.is_win != -1:
                strategy_pnl_map[sid] = strategy_pnl_map.get(sid, 0) + result.pnl

        for sid, pnl_delta in strategy_pnl_map.items():
            await self._update_strategy_pnl(sid, pnl_delta)

        for aid, delta in account_balance_delta.items():
            await self._update_account_balance(aid, delta)

    # ==================================================================
    # 原子状态转换（CAS 语义）
    # ==================================================================

    async def _atomic_transition(
        self, order_id: int, from_status: str, to_status: str, **extra
    ) -> bool:
        """原子状态转换，返回是否成功（CAS 语义）

        使用 WHERE status=current_status 确保并发安全。
        单个订单的状态更新、pnl 写入、余额变更在同一事务中完成。
        """
        # 验证状态转换合法性
        self._transition_status({"status": from_status}, to_status)

        set_parts = ["status=?"]
        values: list = [to_status]
        for k, v in extra.items():
            set_parts.append(f"{k}=?")
            values.append(v)

        values.extend([order_id, self.operator_id, from_status])
        cursor = await self.db.execute(
            f"UPDATE bet_orders SET {', '.join(set_parts)} "
            "WHERE id=? AND operator_id=? AND status=?",
            tuple(values),
        )
        await self.db.commit()
        return cursor.rowcount > 0

    # ==================================================================
    # 带优先级的原子状态转换
    # ==================================================================

    async def _atomic_transition_with_priority(
        self,
        order_id: int,
        from_status: str,
        to_status: str,
        source_priority: int,
        **extra,
    ) -> bool:
        """带优先级的原子状态转换

        source_priority: 1=platform, 2=local, 3=timeout/failed
        非终态→终态直接执行；终态覆盖时检查 source_priority < current_priority。
        """
        # 非终态 → 任意合法目标：直接执行
        if from_status not in TERMINAL_STATES:
            return await self._atomic_transition(order_id, from_status, to_status, **extra)

        # 不可覆盖终态：直接拒绝
        if from_status in NON_OVERRIDABLE_TERMINAL_STATES:
            return False

        # 可覆盖终态（settle_timeout / settle_failed）：检查优先级
        current_priority = _TERMINAL_STATE_PRIORITY.get(from_status, SOURCE_PRIORITY_TIMEOUT)
        if source_priority >= current_priority:
            return False  # 同级或低优先级，不覆盖

        return await self._atomic_transition(order_id, from_status, to_status, **extra)

    # 7.3.6 
    async def _update_strategy_pnl(self, strategy_id: int, pnl_delta: int) -> None:
        """ daily_pnl / total_pnl

         0:00  daily_pnl daily_pnl_date  0
        """
        strategy = await strategy_get_by_id(
            self.db, strategy_id=strategy_id, operator_id=self.operator_id,
        )
        if strategy is None:
            logger.warning("strategy_id=%d", strategy_id)
            return

        today_str = date.today().strftime("%Y-%m-%d")
        current_daily = strategy["daily_pnl"]
        current_total = strategy["total_pnl"]
        current_date = strategy.get("daily_pnl_date")

        #    daily_pnl
        if current_date != today_str:
            current_daily = 0

        new_daily = current_daily + pnl_delta
        new_total = current_total + pnl_delta

        await strategy_update_pnl(
            self.db,
            strategy_id=strategy_id,
            operator_id=self.operator_id,
            daily_pnl=new_daily,
            total_pnl=new_total,
            daily_pnl_date=today_str,
        )
        logger.info(
            "strategy_id=%ddaily_pnl=%dtotal_pnl=%d",
            strategy_id, new_daily, new_total,
        )

    # 7.3.7 
    async def _save_lottery_result(
        self, issue: str, open_result: str, sum_value: int
    ) -> None:
        """ lottery_results INSERT OR IGNORE"""
        await lottery_result_save(
            self.db,
            issue=issue,
            open_result=open_result,
            sum_value=sum_value,
        )

    # 7.3.8 更新账号余额
    async def _update_account_balance(self, account_id: int, delta: int) -> None:
        """结算后更新 gambling_accounts.balance

        delta = amount + pnl（即结算返还金额）
        赢：delta = amount + (amount*odds//10000 - amount) = amount*odds//10000
        输：delta = amount + (-amount) = 0
        退款：delta = amount + 0 = amount
        """
        if delta == 0:
            return

        try:
            row = await (
                await self.db.execute(
                    "SELECT balance FROM gambling_accounts WHERE id=?",
                    (account_id,),
                )
            ).fetchone()
            if row is None:
                logger.warning("更新余额失败: account_id=%d 不存在", account_id)
                return

            old_balance = row["balance"]
            new_balance = old_balance + delta
            await self.db.execute(
                "UPDATE gambling_accounts SET balance=?, updated_at=? WHERE id=?",
                (new_balance, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), account_id),
            )
            await self.db.commit()
            logger.info(
                "账号余额更新 account_id=%d old=%d new=%d delta=%d",
                account_id, old_balance, new_balance, delta,
            )
        except Exception:
            logger.exception("更新账号余额异常 account_id=%d delta=%d", account_id, delta)

    # ==================================================================
    # Task 4: 真实投注结算路径
    # ==================================================================

    async def _retry_api(self, func, max_retries: int = 3, interval: int = 5):
        """通用 API 重试，固定间隔，最多 max_retries 次

        全部失败后抛出最后一次异常。
        """
        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                return await func()
            except Exception as e:
                last_error = e
                logger.warning(
                    "API 调用失败 attempt=%d/%d: %s", attempt + 1, max_retries, e,
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(interval)
        raise last_error  # type: ignore[misc]

    async def _update_account_balance_absolute(
        self, account_id: int, platform_balance: int
    ) -> None:
        """直接写入平台余额（绝对值），用于真实投注模式

        platform_balance 应为 int(accountLimit * 100)。
        若值为负数则不更新并记录日志。
        """
        if platform_balance < 0:
            logger.warning(
                "平台余额为负值，不更新 account_id=%d balance=%d",
                account_id, platform_balance,
            )
            return

        try:
            row = await (
                await self.db.execute(
                    "SELECT balance FROM gambling_accounts WHERE id=?",
                    (account_id,),
                )
            ).fetchone()
            if row is None:
                logger.warning("绝对余额更新失败: account_id=%d 不存在", account_id)
                return

            old_balance = row["balance"]
            await self.db.execute(
                "UPDATE gambling_accounts SET balance=?, updated_at=? WHERE id=?",
                (platform_balance, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), account_id),
            )
            await self.db.commit()
            logger.info(
                "平台余额写入 account_id=%d old=%d new=%d",
                account_id, old_balance, platform_balance,
            )
        except Exception:
            logger.exception(
                "绝对余额更新异常 account_id=%d balance=%d", account_id, platform_balance,
            )

    async def _mark_orders_settle_failed(self, orders: list[dict]) -> None:
        """将订单列表标记为 settle_failed"""
        for order in orders:
            current_status = order["status"]
            if current_status in NON_OVERRIDABLE_TERMINAL_STATES:
                continue
            try:
                await self._atomic_transition(
                    order["id"], current_status, "settle_failed",
                )
            except IllegalStateTransition:
                logger.warning(
                    "标记 settle_failed 跳过 order_id=%d: 非法转换 %s→settle_failed",
                    order["id"], current_status,
                )

    async def _settle_real(
        self,
        orders: list[dict],
        issue: str,
        adapter: "PlatformAdapter",
        open_result: str,
        sum_value: int,
    ) -> None:
        """真实投注结算：从平台获取数据

        1. 调用 QueryResult 更新余额
        2. 调用 Topbetlist 获取平台投注记录
        3. _match_and_settle() 匹配
        """
        # 1. 更新余额（QueryResult）
        try:
            balance_info = await self._retry_api(adapter.query_balance)
            account_limit = balance_info.balance
            # accountLimit 为负值或非数值时不更新
            if not isinstance(account_limit, (int, float)):
                logger.warning("accountLimit 非数值: %r", account_limit)
            else:
                platform_balance = int(account_limit * 100)
                await self._update_account_balance_absolute(
                    orders[0]["account_id"], platform_balance,
                )
        except Exception:
            logger.exception("QueryResult 失败，余额未更新")

        # 2. 获取平台投注记录（Topbetlist）
        try:
            platform_bets = await self._retry_api(
                lambda: adapter.get_bet_history(count=15)
            )
        except Exception:
            # 3 次重试全部失败 → settle_failed + 告警
            logger.exception("Topbetlist 3 次重试全部失败 期号 %s", issue)
            await self._mark_orders_settle_failed(orders)
            if self.alert_service:
                await self.alert_service.send(
                    operator_id=self.operator_id,
                    alert_type="settle_api_failed",
                    title=f"Topbetlist 调用失败 期号 {issue}",
                    detail=f"3 次重试全部失败，{len(orders)} 笔订单标记 settle_failed",
                )
            return

        # 3. 匹配平台记录与本地订单
        await self._match_and_settle(orders, platform_bets, issue, open_result, sum_value)

    # ==================================================================
    # Task 3: 平台数据匹配算法与歧义检测
    # ==================================================================

    async def _match_and_settle(
        self,
        orders: list[dict],
        platform_bets: list[dict],
        issue: str,
        open_result: str,
        sum_value: int,
        is_normal_cycle: bool = True,
    ) -> None:
        """使用 (Installments, KeyCode, Amount) 匹配，含歧义检测

        Args:
            orders: 本地待结算订单列表
            platform_bets: 平台 Topbetlist 返回的记录列表
            issue: 当前结算期号
            open_result: 开奖结果字符串
            sum_value: 开奖结果和值
            is_normal_cycle: 是否为正常结算周期（非 API 失败/补结算）
        """
        # 3.7 平台记录落库
        await self._persist_platform_records(platform_bets)

        # 构建平台记录索引：(issue, key_code, amount_fen) -> [records]
        platform_index: dict[tuple, list[dict]] = {}
        for bet in platform_bets:
            bet_issue = str(bet.get("Installments", ""))
            key_code = str(bet.get("KeyCode", ""))
            amount_fen = int(float(bet.get("Amount", 0)) * 100)
            key = (bet_issue, key_code, amount_fen)
            platform_index.setdefault(key, []).append(bet)

        # 按 bet_at 升序排列本地订单（bet_at 相同时按 id 升序）
        sorted_orders = sorted(
            orders, key=lambda o: (o.get("bet_at") or "", o.get("id", 0))
        )

        # 3.3 Topbetlist 覆盖检测
        platform_issue_count = sum(
            len(recs)
            for (bi, _, _), recs in platform_index.items()
            if bi == issue
        )
        local_issue_count = len([o for o in sorted_orders if o["status"] == "bet_success"])
        if local_issue_count > platform_issue_count:
            if self.alert_service:
                await self.alert_service.send(
                    operator_id=self.operator_id,
                    alert_type="topbetlist_coverage_warning",
                    title=f"Topbetlist 覆盖不足 期号 {issue}",
                    detail=json.dumps({
                        "issue": issue,
                        "local_count": local_issue_count,
                        "platform_count": platform_issue_count,
                    }),
                )

        # 按 Match_Key 分组本地订单
        local_groups: dict[tuple, list[dict]] = {}
        for order in sorted_orders:
            match_key = (issue, order["key_code"], order["amount"])
            local_groups.setdefault(match_key, []).append(order)

        strategy_pnl_map: dict[int, int] = {}

        for match_key, group_orders in local_groups.items():
            candidates = platform_index.get(match_key, [])

            # 3.2 歧义检测：同一 Match_Key 下本地订单数 >= 2 且平台记录 >= 2 且 WinAmount 不全相同
            if len(group_orders) >= 2 and len(candidates) >= 2:
                win_amounts = [float(c.get("WinAmount", 0)) for c in candidates]
                if len(set(win_amounts)) > 1:
                    # 歧义：WinAmount 不全相同，降级为 pending_match
                    for order in group_orders:
                        await self._mark_pending_match(order, is_normal_cycle=is_normal_cycle)
                    if self.alert_service:
                        await self.alert_service.send(
                            operator_id=self.operator_id,
                            alert_type="match_ambiguity",
                            title=f"匹配歧义 期号 {issue} KeyCode={match_key[1]}",
                            detail=json.dumps({
                                "issue": issue,
                                "key_code": match_key[1],
                                "amount": match_key[2],
                                "local_count": len(group_orders),
                                "platform_count": len(candidates),
                                "win_amounts": win_amounts,
                            }),
                        )
                    continue  # 跳过该组

            # 无歧义或单条订单：正常逐一配对
            for order in group_orders:
                if candidates:
                    platform_record = candidates.pop(0)  # 消耗一条
                    pnl = await self._settle_order_from_platform(
                        order, platform_record, open_result, sum_value,
                    )
                    sid = order["strategy_id"]
                    strategy_pnl_map[sid] = strategy_pnl_map.get(sid, 0) + pnl
                else:
                    # 3.4 未匹配，标记 pending_match
                    await self._mark_pending_match(order, is_normal_cycle=is_normal_cycle)

        # 更新策略 PnL
        for sid, pnl_delta in strategy_pnl_map.items():
            await self._update_strategy_pnl(sid, pnl_delta)

    # ==================================================================
    # 3.4 标记 pending_match + 3.5 pending_match_count 累加与超时
    # ==================================================================

    async def _mark_pending_match(
        self, order: dict, *, is_normal_cycle: bool = True
    ) -> None:
        """将未匹配订单标记为 pending_match，或处理已在 pending_match 的订单

        is_normal_cycle: 仅在正常结算周期中递增 pending_match_count
        """
        current_status = order["status"]
        order_id = order["id"]

        # 3.5 wall-clock 30 分钟超时检测
        if current_status == "pending_match":
            bet_at_str = order.get("bet_at") or order.get("created_at") or ""
            if bet_at_str:
                try:
                    bet_at_time = datetime.strptime(bet_at_str, "%Y-%m-%d %H:%M:%S")
                    elapsed = (datetime.utcnow() - bet_at_time).total_seconds()
                    if elapsed > PENDING_MATCH_WALL_CLOCK_TIMEOUT:
                        await self._transition_to_settle_timeout(order)
                        return
                except ValueError:
                    pass

            # 3.5 正常周期递增 pending_match_count
            if is_normal_cycle:
                new_count = order.get("pending_match_count", 0) + 1
                if new_count >= 2:  # Settle_Timeout_Cycles = 2
                    await self._transition_to_settle_timeout(order)
                    return
                # 递增计数
                await self.db.execute(
                    "UPDATE bet_orders SET pending_match_count=? "
                    "WHERE id=? AND operator_id=? AND status='pending_match'",
                    (new_count, order_id, self.operator_id),
                )
                await self.db.commit()
            return

        # bet_success → pending_match
        if current_status == "bet_success":
            await self._atomic_transition(
                order_id, "bet_success", "pending_match",
                pending_match_count=0,
            )

    async def _transition_to_settle_timeout(self, order: dict) -> None:
        """将订单转为 settle_timeout 并发送告警"""
        order_id = order["id"]
        current_status = order["status"]
        success = await self._atomic_transition(
            order_id, current_status, "settle_timeout",
        )
        if success and self.alert_service:
            await self.alert_service.send(
                operator_id=self.operator_id,
                alert_type="settle_timeout",
                title=f"结算超时 order_id={order_id}",
                detail=json.dumps({
                    "order_id": order_id,
                    "issue": order.get("issue", ""),
                    "key_code": order.get("key_code", ""),
                    "amount": order.get("amount", 0),
                }),
            )

    # ==================================================================
    # 3.6 _settle_order_from_platform
    # ==================================================================

    async def _settle_order_from_platform(
        self,
        order: dict,
        platform_record: dict,
        open_result: str,
        sum_value: int,
    ) -> int:
        """从平台数据结算单个订单，返回 pnl

        pnl = int(float(WinAmount) * 100) - amount
        is_win = 1 if WinAmount > 0 else 0
        match_source = "platform"
        """
        win_amount_yuan = float(platform_record.get("WinAmount", 0))
        win_amount_fen = int(win_amount_yuan * 100)
        amount = order["amount"]

        is_win = 1 if win_amount_yuan > 0 else 0
        pnl = win_amount_fen - amount

        order_id = order["id"]
        current_status = order["status"]
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        # 两步状态转换: current → settling → settled
        ok = await self._atomic_transition(
            order_id, current_status, "settling",
        )
        if not ok:
            logger.warning(
                "平台结算跳过 order_id=%d: CAS 失败 from_status=%s",
                order_id, current_status,
            )
            return 0

        ok = await self._atomic_transition(
            order_id, "settling", "settled",
            is_win=is_win,
            pnl=pnl,
            open_result=open_result,
            sum_value=sum_value,
            settled_at=now_str,
            match_source="platform",
        )
        if not ok:
            logger.warning(
                "平台结算跳过 order_id=%d: settling→settled CAS 失败", order_id,
            )
            return 0

        logger.info(
            "平台结算完成 order_id=%d is_win=%d pnl=%d match_source=platform",
            order_id, is_win, pnl,
        )
        return pnl

    # ==================================================================
    # 3.7 平台记录落库
    # ==================================================================

    async def _persist_platform_records(self, platform_bets: list[dict]) -> None:
        """将 Topbetlist 返回的原始记录写入 bet_order_platform_records 表"""
        for bet in platform_bets:
            issue = str(bet.get("Installments", ""))
            key_code = str(bet.get("KeyCode", ""))
            amount_fen = int(float(bet.get("Amount", 0)) * 100)
            win_amount_fen = int(float(bet.get("WinAmount", 0)) * 100)
            raw = json.dumps(bet, ensure_ascii=False)
            await self.db.execute(
                "INSERT INTO bet_order_platform_records "
                "(issue, key_code, amount, win_amount, raw_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (issue, key_code, amount_fen, win_amount_fen, raw),
            )
        await self.db.commit()


