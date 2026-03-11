"""Reconciler


1. adapter.get_bet_history+ 
2.  vs 
3. |diff|  100  matched> 100  mismatch
4.  > 500  critical 
5. mismatch  + 
6.  3  mismatch   + 
7.  reconcile_records 

INTEGER1=100

DB  settled  reconcile_error 
 mismatch  bet_orders  reconcile_records + 
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import aiosqlite

from app.engine.adapters.base import PlatformAdapter
from app.engine.alert import AlertService
from app.engine.settlement import TERMINAL_STATES
from app.models.db_ops import (
    account_get_by_id,
    reconcile_record_create,
    strategy_list_by_operator,
    strategy_update_status,
)

logger = logging.getLogger(__name__)

# 
TOLERANCE_SINGLE = 100    #  1 
TOLERANCE_CUMULATIVE = 500  #  5 
CONSECUTIVE_MISMATCH_LIMIT = 3  #  mismatch 


class Reconciler:
    """"""

    def __init__(
        self,
        db: aiosqlite.Connection,
        adapter: PlatformAdapter,
        alert_service: AlertService,
        operator_id: int,
    ) -> None:
        self.db = db
        self.adapter = adapter
        self.alert_service = alert_service
        self.operator_id = operator_id
        # account_id   mismatch 
        self._consecutive_mismatch_count: dict[int, int] = {}
        # account_id  
        self._cumulative_diff: dict[int, int] = {}

    # ==================================================================
    # 
    # ==================================================================

    async def reconcile(self, account_id: int, issue: str) -> None:
        """按 simulation 分发到真实/模拟对账路径"""
        logger.info("对账 account_id=%d issue=%s", account_id, issue)

        # 查询该期号所有订单
        all_orders = await self._get_all_orders_for_issue(account_id, issue)

        real_orders = [o for o in all_orders if o.get("simulation", 0) == 0]
        sim_orders = [o for o in all_orders if o.get("simulation", 0) == 1]

        # 真实模式：验证所有订单在终态
        if real_orders:
            await self._reconcile_real(account_id, issue, real_orders)

        # 模拟模式：保持现有余额比较逻辑
        if sim_orders:
            await self._reconcile_simulated(account_id, issue, sim_orders)

    # ==================================================================
    # 真实模式对账：终态验证
    # ==================================================================

    async def _reconcile_real(
        self, account_id: int, issue: str, orders: list[dict]
    ) -> None:
        """真实模式对账：验证所有 simulation=0 订单在终态，否则发送告警。

        不写入 reconcile_error 状态。
        """
        non_terminal = [o for o in orders if o["status"] not in TERMINAL_STATES]
        if non_terminal:
            await self.alert_service.send(
                operator_id=self.operator_id,
                alert_type="unsettled_orders",
                title=f"未结算订单 期号 {issue}",
                detail=json.dumps({
                    "count": len(non_terminal),
                    "issue": issue,
                    "statuses": [o["status"] for o in non_terminal],
                }),
                account_id=account_id,
            )

    # ==================================================================
    # 模拟模式对账：余额比较（原有逻辑）
    # ==================================================================

    async def _reconcile_simulated(
        self, account_id: int, issue: str, orders: list[dict]
    ) -> None:
        """模拟模式对账：余额比较逻辑，容差 TOLERANCE_SINGLE=100"""
        # 1. 获取平台投注记录
        platform_bets = await self.adapter.get_bet_history(count=15)

        # 2. 统计数量
        local_count = len(orders)
        platform_count = self._count_platform_bets(platform_bets, issue)

        # 3. 获取平台余额
        balance_info = await self.adapter.query_balance()
        platform_balance = int(balance_info.balance * 100)

        # 4. 计算本地余额
        local_balance = await self._calc_local_balance(account_id)

        # 5. 比较差异
        diff = abs(platform_balance - local_balance)

        # 6. 判定
        status = "matched" if diff <= TOLERANCE_SINGLE else "mismatch"

        # 7. 保存对账记录
        detail = json.dumps({
            "local_count": local_count,
            "platform_count": platform_count,
            "local_balance": local_balance,
            "platform_balance": platform_balance,
            "diff": diff,
        })
        await self._save_reconcile_record(
            account_id, issue, local_count, platform_count,
            local_balance, platform_balance, diff, status, detail,
        )

        # 8. 处理结果
        if status == "mismatch":
            await self._handle_mismatch(account_id, issue, diff, local_balance, platform_balance)
        else:
            self._consecutive_mismatch_count[account_id] = 0
            await self._sync_balance(account_id, platform_balance)
            logger.info("对账匹配 account_id=%d issue=%s diff=%d", account_id, issue, diff)

        # 9. 累计差异检查
        self._cumulative_diff[account_id] = self._cumulative_diff.get(account_id, 0) + diff
        cumulative = self._cumulative_diff[account_id]
        if cumulative > TOLERANCE_CUMULATIVE:
            await self._handle_critical(account_id, issue, cumulative)

    # ==================================================================
    # 查询该期号所有订单
    # ==================================================================

    async def _get_all_orders_for_issue(
        self, account_id: int, issue: str
    ) -> list[dict]:
        """查询该期号下所有订单（不限状态）"""
        rows = await (
            await self.db.execute(
                "SELECT * FROM bet_orders WHERE account_id=? AND operator_id=? AND issue=?",
                (account_id, self.operator_id, issue),
            )
        ).fetchall()
        return [dict(r) for r in rows]

    # ==================================================================
    # 7.4.2  + 
    # ==================================================================

    async def _get_local_orders(self, account_id: int, issue: str) -> list[dict]:
        """status='settled'"""
        rows = await (
            await self.db.execute(
                "SELECT * FROM bet_orders WHERE account_id=? AND operator_id=? AND issue=? AND status='settled'",
                (account_id, self.operator_id, issue),
            )
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _count_platform_bets(platform_bets: list[dict], issue: str) -> int:
        """"""
        count = 0
        for bet in platform_bets:
            #  Installments / issue / Issue 
            bet_issue = bet.get("Installments") or bet.get("issue") or bet.get("Issue", "")
            if str(bet_issue) == str(issue):
                count += 1
        return count

    # ==================================================================
    # 7.4.3 
    # ==================================================================

    async def _calc_local_balance(self, account_id: int) -> int:
        """计算本地余额（用于对账比较）。

        直接返回 DB 中的 balance。
        executor 在下注成功时已扣减，settlement 在结算时已加回。
        平台扣款也是实时的，所以两边应该对齐。
        """
        account = await account_get_by_id(
            self.db, account_id=account_id, operator_id=self.operator_id,
        )
        if account is None:
            logger.warning("account_id=%d 不存在", account_id)
            return 0

        db_balance: int = account.get("balance", 0)
        logger.info(
            "本地余额 account_id=%d db_balance=%d",
            account_id, db_balance,
        )
        return db_balance

    # ==================================================================
    # 7.4.5 mismatch 
    # ==================================================================

    async def _handle_mismatch(
        self,
        account_id: int,
        issue: str,
        diff: int,
        local_balance: int,
        platform_balance: int,
    ) -> None:
        """mismatch  + 

        DB  settled  reconcile_error 
         bet_orders  reconcile_records + 
        """
        logger.warning(
            "account_id=%dissue=%sdiff=%d",
            account_id, issue, diff,
        )

        #  reconcile_error 
        await self.alert_service.send(
            operator_id=self.operator_id,
            alert_type="reconcile_error",
            title=f"对账差异 期号 {issue}",
            detail=json.dumps({
                "diff": diff,
                "local": local_balance,
                "platform": platform_balance,
                "account_id": account_id,
            }),
            account_id=account_id,
        )

        #  mismatch 
        prev = self._consecutive_mismatch_count.get(account_id, 0)
        self._consecutive_mismatch_count[account_id] = prev + 1

        # 7.4.6  3  mismatch  
        if self._consecutive_mismatch_count[account_id] >= CONSECUTIVE_MISMATCH_LIMIT:
            await self._pause_all_strategies(account_id, issue)

    # ==================================================================
    # 7.4.6  3  mismatch 
    # ==================================================================

    async def _pause_all_strategies(self, account_id: int, issue: str) -> None:
        """"""
        logger.critical(
            " %d account_id=%d",
            CONSECUTIVE_MISMATCH_LIMIT, account_id,
        )

        strategies = await strategy_list_by_operator(
            self.db, operator_id=self.operator_id,
        )
        paused_count = 0
        for strat in strategies:
            if strat["account_id"] == account_id and strat["status"] == "running":
                await strategy_update_status(
                    self.db,
                    strategy_id=strat["id"],
                    operator_id=self.operator_id,
                    status="paused",
                )
                paused_count += 1

        # 
        await self.alert_service.send(
            operator_id=self.operator_id,
            alert_type="reconcile_error",
            title=f"连续 {CONSECUTIVE_MISMATCH_LIMIT} 次不匹配 暂停策略",
            detail=json.dumps({
                "account_id": account_id,
                "issue": issue,
                "paused_strategies": paused_count,
                "consecutive_mismatches": self._consecutive_mismatch_count[account_id],
            }),
            account_id=account_id,
        )

    # ==================================================================
    #  critical 
    # ==================================================================

    async def _handle_critical(
        self, account_id: int, issue: str, cumulative: int
    ) -> None:
        """ critical """
        logger.critical(
            "account_id=%dcumulative=%dthreshold=%d",
            account_id, cumulative, TOLERANCE_CUMULATIVE,
        )
        await self.alert_service.send(
            operator_id=self.operator_id,
            alert_type="reconcile_error",
            title=f"累计差异{cumulative}超限{TOLERANCE_CUMULATIVE}",
            detail=json.dumps({
                "account_id": account_id,
                "issue": issue,
                "cumulative_diff": cumulative,
                "threshold": TOLERANCE_CUMULATIVE,
            }),
            account_id=account_id,
        )

    # ==================================================================
    # 7.4.7 
    # ==================================================================

    async def _save_reconcile_record(
        self,
        account_id: int,
        issue: str,
        local_count: int,
        platform_count: int,
        local_balance: int,
        platform_balance: int,
        diff: int,
        status: str,
        detail: Optional[str] = None,
    ) -> dict:
        """ reconcile_records """
        return await reconcile_record_create(
            self.db,
            operator_id=self.operator_id,
            account_id=account_id,
            issue=issue,
            local_bet_count=local_count,
            platform_bet_count=platform_count,
            local_balance=local_balance,
            platform_balance=platform_balance,
            diff_amount=diff,
            status=status,
            detail=detail,
        )

    async def _sync_balance(self, account_id: int, platform_balance: int) -> None:
        """对账匹配时，用平台余额校准本地 gambling_accounts.balance"""
        try:
            from app.models.db_ops import account_update
            await account_update(
                self.db,
                account_id=account_id,
                operator_id=self.operator_id,
                balance=platform_balance,
            )
            logger.info(
                "余额校准 account_id=%d platform_balance=%d",
                account_id, platform_balance,
            )
        except Exception:
            logger.exception(
                "余额校准异常 account_id=%d", account_id,
            )
