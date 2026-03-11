"""RiskController

 10 

   kill_switch   session   strategy_status   operator_status
    balance   single_bet_limit   daily_limit   period_limit
    stop_loss   take_profit

INTEGER1=100
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import aiosqlite

from app.engine.alert import AlertService
from app.engine.strategy_runner import BetSignal
from app.models.db_ops import (
    account_get_by_id,
    operator_get_by_id,
    strategy_get_by_id,
    strategy_list_by_operator,
    strategy_update_status,
)

logger = logging.getLogger(__name__)

#  
PLATFORM_DEFAULT_SINGLE_BET_LIMIT = 100_000_00  # 10 = 10000000


@dataclass
class RiskCheckResult:
    """"""
    passed: bool
    reason: str = ""


class RiskController:
    """

    :
        db: 
        alert_service: 
        operator_id:  ID
        account_id:  ID
        global_kill: 
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        alert_service: AlertService,
        operator_id: int,
        account_id: int,
        global_kill: bool = False,
    ) -> None:
        self.db = db
        self.alert_service = alert_service
        self.operator_id = operator_id
        self.account_id = account_id
        self.global_kill = global_kill

        # account_id  
        self._balance_fail_count: dict[int, int] = {}
        # issue  
        self._period_bets: dict[str, int] = {}
        # 
        self._check_log: list[str] = []

    async def check(self, signal: BetSignal) -> RiskCheckResult:
        """ 10 """
        self._check_log.clear()

        #  0-9  10 
        checks = [
            ("kill_switch", self._check_kill_switch),
            ("session", self._check_session),
            ("strategy_status", self._check_strategy_status),
            ("operator_status", self._check_operator_status),
            ("balance", self._check_balance),
            ("single_bet_limit", self._check_single_bet_limit),
            ("daily_limit", self._check_daily_limit),
            ("period_limit", self._check_period_limit),
            ("stop_loss", self._check_stop_loss),
            ("take_profit", self._check_take_profit),
        ]

        for name, check_fn in checks:
            self._check_log.append(name)
            result = await check_fn(signal)
            if not result.passed:
                logger.info(
                    "check=%sstrategy_id=%dreason=%s",
                    name, signal.strategy_id, result.reason,
                )
                return result

        return RiskCheckResult(passed=True)

    # ------------------------------------------------------------------
    #  
    # ------------------------------------------------------------------
    async def _check_kill_switch(self, signal: BetSignal) -> RiskCheckResult:
        if self.global_kill:
            return RiskCheckResult(passed=False, reason="")
        account = await account_get_by_id(
            self.db, account_id=self.account_id, operator_id=self.operator_id
        )
        if account and account.get("kill_switch"):
            return RiskCheckResult(passed=False, reason="")
        return RiskCheckResult(passed=True)

    # ------------------------------------------------------------------
    #  
    # ------------------------------------------------------------------
    async def _check_session(self, signal: BetSignal) -> RiskCheckResult:
        account = await account_get_by_id(
            self.db, account_id=self.account_id, operator_id=self.operator_id
        )
        if not account:
            return RiskCheckResult(passed=False, reason="")
        token = account.get("session_token")
        if not token:
            return RiskCheckResult(passed=False, reason=" session_token")
        return RiskCheckResult(passed=True)

    # ------------------------------------------------------------------
    #  
    # ------------------------------------------------------------------
    async def _check_strategy_status(self, signal: BetSignal) -> RiskCheckResult:
        strategy = await strategy_get_by_id(
            self.db, strategy_id=signal.strategy_id, operator_id=self.operator_id
        )
        if not strategy:
            return RiskCheckResult(passed=False, reason="")
        if strategy["status"] != "running":
            return RiskCheckResult(
                passed=False,
                reason=f" running{strategy['status']}",
            )
        return RiskCheckResult(passed=True)

    # ------------------------------------------------------------------
    #  
    # ------------------------------------------------------------------
    async def _check_operator_status(self, signal: BetSignal) -> RiskCheckResult:
        operator = await operator_get_by_id(
            self.db, operator_id=self.operator_id
        )
        if not operator:
            return RiskCheckResult(passed=False, reason="")
        if operator["status"] != "active":
            return RiskCheckResult(
                passed=False,
                reason=f" active{operator['status']}",
            )
        return RiskCheckResult(passed=True)

    # ------------------------------------------------------------------
    #  
    # ------------------------------------------------------------------
    async def _check_balance(self, signal: BetSignal) -> RiskCheckResult:
        account = await account_get_by_id(
            self.db, account_id=self.account_id, operator_id=self.operator_id
        )
        if not account:
            return RiskCheckResult(passed=False, reason="")

        balance = account.get("balance", 0)
        if balance < signal.amount:
            # 
            count = self._balance_fail_count.get(self.account_id, 0) + 1
            self._balance_fail_count[self.account_id] = count

            if count >= 3:
                #  3    + 
                await self._pause_all_strategies()
                await self.alert_service.send(
                    operator_id=self.operator_id,
                    alert_type="balance_low",
                    title="3",
                    detail=f"={balance}={signal.amount}",
                    account_id=self.account_id,
                )
                # 
                self._balance_fail_count[self.account_id] = 0

            return RiskCheckResult(
                passed=False,
                reason=f"={balance}={signal.amount}",
            )

        # 
        self._balance_fail_count[self.account_id] = 0
        return RiskCheckResult(passed=True)

    # ------------------------------------------------------------------
    #  
    # ------------------------------------------------------------------
    async def _check_single_bet_limit(self, signal: BetSignal) -> RiskCheckResult:
        account = await account_get_by_id(
            self.db, account_id=self.account_id, operator_id=self.operator_id
        )
        if not account:
            return RiskCheckResult(passed=False, reason="")

        # 
        platform_limit = PLATFORM_DEFAULT_SINGLE_BET_LIMIT
        #  None 
        self_limit = account.get("single_bet_limit")

        # 
        if signal.amount > platform_limit:
            await self.alert_service.send(
                operator_id=self.operator_id,
                alert_type="platform_limit",
                title=f"{signal.amount} > {platform_limit}",
                detail=f"={signal.key_code}={platform_limit}",
                account_id=self.account_id,
            )
            return RiskCheckResult(
                passed=False,
                reason=f"={signal.amount}={platform_limit}",
            )

        # 
        if self_limit is not None and signal.amount > self_limit:
            return RiskCheckResult(
                passed=False,
                reason=f"={signal.amount}={self_limit}",
            )

        return RiskCheckResult(passed=True)

    # ------------------------------------------------------------------
    #  
    # ------------------------------------------------------------------
    async def _check_daily_limit(self, signal: BetSignal) -> RiskCheckResult:
        account = await account_get_by_id(
            self.db, account_id=self.account_id, operator_id=self.operator_id
        )
        if not account:
            return RiskCheckResult(passed=False, reason="")

        daily_limit = account.get("daily_limit")
        if daily_limit is None:
            # 
            return RiskCheckResult(passed=True)

        # 
        today = datetime.utcnow().strftime("%Y-%m-%d")
        row = await (await self.db.execute(
            """SELECT COALESCE(SUM(amount), 0) as total
               FROM bet_orders
               WHERE account_id=? AND operator_id=?
                 AND status NOT IN ('bet_failed')
                 AND created_at >= ?""",
            (self.account_id, self.operator_id, today),
        )).fetchone()
        daily_total = row["total"] if row else 0

        if daily_total + signal.amount > daily_limit:
            return RiskCheckResult(
                passed=False,
                reason=f"={daily_total}+={signal.amount}={daily_limit}",
            )

        return RiskCheckResult(passed=True)

    # ------------------------------------------------------------------
    #  
    # ------------------------------------------------------------------
    async def _check_period_limit(self, signal: BetSignal) -> RiskCheckResult:
        account = await account_get_by_id(
            self.db, account_id=self.account_id, operator_id=self.operator_id
        )
        if not account:
            return RiskCheckResult(passed=False, reason="")

        period_limit = account.get("period_limit")
        if period_limit is None:
            # 
            return RiskCheckResult(passed=True)

        #  idempotent_id  {issue}-{strategy_id}-{key_code}
        issue = signal.idempotent_id.rsplit("-", 2)[0] if "-" in signal.idempotent_id else ""

        # DB  + 
        row = await (await self.db.execute(
            """SELECT COALESCE(SUM(amount), 0) as total
               FROM bet_orders
               WHERE account_id=? AND operator_id=? AND issue=?
                 AND status NOT IN ('bet_failed')""",
            (self.account_id, self.operator_id, issue),
        )).fetchone()
        period_total_db = row["total"] if row else 0

        # 
        period_total_mem = self._period_bets.get(issue, 0)
        period_total = period_total_db + period_total_mem

        if period_total + signal.amount > period_limit:
            return RiskCheckResult(
                passed=False,
                reason=f"={period_total}+={signal.amount}={period_limit}",
            )

        # 
        self._period_bets[issue] = period_total_mem + signal.amount
        return RiskCheckResult(passed=True)

    # ------------------------------------------------------------------
    #  
    # ------------------------------------------------------------------
    async def _check_stop_loss(self, signal: BetSignal) -> RiskCheckResult:
        strategy = await strategy_get_by_id(
            self.db, strategy_id=signal.strategy_id, operator_id=self.operator_id
        )
        if not strategy:
            return RiskCheckResult(passed=False, reason="")

        stop_loss = strategy.get("stop_loss")
        if stop_loss is None:
            return RiskCheckResult(passed=True)

        # is_win=-1  pnl=0 
        daily_pnl = await self._calc_daily_pnl(signal.strategy_id)

        if daily_pnl <= -stop_loss:
            await self.alert_service.send(
                operator_id=self.operator_id,
                alert_type="stop_loss",
                title=f" {strategy['name']} ={-daily_pnl}={stop_loss}",
                detail=f"strategy_id={signal.strategy_id}daily_pnl={daily_pnl}",
                account_id=self.account_id,
            )
            return RiskCheckResult(
                passed=False,
                reason=f"daily_pnl={daily_pnl}=-{stop_loss}",
            )

        return RiskCheckResult(passed=True)

    # ------------------------------------------------------------------
    #  
    # ------------------------------------------------------------------
    async def _check_take_profit(self, signal: BetSignal) -> RiskCheckResult:
        strategy = await strategy_get_by_id(
            self.db, strategy_id=signal.strategy_id, operator_id=self.operator_id
        )
        if not strategy:
            return RiskCheckResult(passed=False, reason="")

        take_profit = strategy.get("take_profit")
        if take_profit is None:
            return RiskCheckResult(passed=True)

        # 
        daily_pnl = await self._calc_daily_pnl(signal.strategy_id)

        if daily_pnl >= take_profit:
            await self.alert_service.send(
                operator_id=self.operator_id,
                alert_type="take_profit",
                title=f" {strategy['name']} ={daily_pnl}={take_profit}",
                detail=f"strategy_id={signal.strategy_id}daily_pnl={daily_pnl}",
                account_id=self.account_id,
            )
            return RiskCheckResult(
                passed=False,
                reason=f"daily_pnl={daily_pnl}={take_profit}",
            )

        return RiskCheckResult(passed=True)

    # ------------------------------------------------------------------
    # 
    # ------------------------------------------------------------------

    async def _calc_daily_pnl(self, strategy_id: int) -> int:
        """is_win=-1 """
        today = datetime.utcnow().strftime("%Y-%m-%d")
        row = await (await self.db.execute(
            """SELECT COALESCE(SUM(pnl), 0) as total_pnl
               FROM bet_orders
               WHERE strategy_id=? AND operator_id=?
                 AND status = 'settled'
                 AND is_win != -1
                 AND created_at >= ?""",
            (strategy_id, self.operator_id, today),
        )).fetchone()
        return row["total_pnl"] if row else 0

    async def _pause_all_strategies(self) -> None:
        """"""
        strategies = await strategy_list_by_operator(
            self.db, operator_id=self.operator_id
        )
        for s in strategies:
            if s["account_id"] == self.account_id and s["status"] == "running":
                await strategy_update_status(
                    self.db,
                    strategy_id=s["id"],
                    operator_id=self.operator_id,
                    status="paused",
                )
                logger.info(
                    "strategy_id=%daccount_id=%d",
                    s["id"], self.account_id,
                )

    def reset_period_bets(self, issue: str | None = None) -> None:
        """"""
        if issue:
            self._period_bets.pop(issue, None)
        else:
            self._period_bets.clear()
