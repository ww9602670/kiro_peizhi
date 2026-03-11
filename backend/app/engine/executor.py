"""Docstring placeholder."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import aiosqlite

from app.engine.adapters.base import BetResult, InstallInfo, PlatformAdapter
from app.engine.alert import AlertService
from app.engine.risk import RiskCheckResult, RiskController
from app.engine.strategy_runner import BetSignal
from app.models.db_ops import (
    account_update,
    bet_order_create,
    bet_order_update_status,
    odds_get_confirmed_map,
    odds_has_records,
)

logger = logging.getLogger(__name__)


class BetExecutor:
    """BetExecutor: execute bet signals for a given install."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        adapter: PlatformAdapter,
        risk: RiskController,
        alert_service: AlertService,
        operator_id: int,
        account_id: int,
    ) -> None:
        self.db = db
        self.adapter = adapter
        self.risk = risk
        self.alert_service = alert_service
        self.operator_id = operator_id
        self.account_id = account_id

    async def execute(
        self, install: InstallInfo, signals: list[BetSignal]
    ) -> None:
        """Execute signals with deadline protection."""
        if not signals:
            return

        deadline_seconds = install.close_countdown_sec - 10
        if deadline_seconds <= 0:
            logger.info(
                "issue=%sclose_timestamp=%d",
                install.issue, install.close_countdown_sec,
            )
            return

        try:
            await asyncio.wait_for(
                self._execute_inner(install, signals),
                timeout=deadline_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "deadline ?pendingissue=%s",
                install.issue,
            )
            # ?pending?

    async def _execute_inner(
        self, install: InstallInfo, signals: list[BetSignal]
    ) -> None:
        """Inner execution logic."""
        # 1. 
        new_signals = []
        for s in signals:
            if not await self._is_duplicate(s):
                new_signals.append(s)

        if not new_signals:
            return

        # 2. ?
        approved: list[BetSignal] = []
        for signal in new_signals:
            check = await self.risk.check(signal)
            if check.passed:
                approved.append(signal)
            else:
                logger.info(
                    "idempotent_id=%sreason=%s",
                    signal.idempotent_id, check.reason,
                )

        if not approved:
            return

        # 3. 从本地数据库读取已确认赔率（替代原 adapter.load_odds）
        odds = await odds_get_confirmed_map(self.db, account_id=self.account_id)

        if odds is None:
            has_records = await odds_has_records(self.db, account_id=self.account_id)
            if not has_records:
                await self.alert_service.send(
                    operator_id=self.operator_id,
                    alert_type="odds_missing",
                    title="请先登录获取赔率",
                    detail="该账号尚未获取赔率数据，请先手动登录",
                    account_id=self.account_id,
                )
            else:
                await self.alert_service.send(
                    operator_id=self.operator_id,
                    alert_type="odds_unconfirmed",
                    title="请先确认赔率更新",
                    detail="该账号存在未确认的赔率变动，请先确认后再下注",
                    account_id=self.account_id,
                )
            return

        # 4.  betdata KeyCode ? 
        betdata: list[dict] = []
        orders_created: list[dict] = []
        simulation_signals: list[BetSignal] = []

        for signal in approved:
            signal_odds = odds.get(signal.key_code, 0)
            if signal_odds == 0:
                logger.info(
                    "?key_code=%sidempotent_id=%s",
                    signal.key_code, signal.idempotent_id,
                )
                continue

            # 
            order = await self._create_order(signal, signal_odds, install.issue)
            if order is None:
                # IntegrityError 
                continue
            orders_created.append(order)

            if signal.simulation:
                simulation_signals.append(signal)
            else:
                betdata.append({
                    "KeyCode": signal.key_code,
                    "Amount": signal.amount,
                    "Odds": signal_odds,
                })

        # 5. ?
        if simulation_signals:
            for signal in simulation_signals:
                order = self._find_order(orders_created, signal.idempotent_id)
                if order:
                    await bet_order_update_status(
                        self.db,
                        order_id=order["id"],
                        operator_id=self.operator_id,
                        status="bet_success",
                        bet_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                    )
                    logger.info(
                        "idempotent_id=%s",
                        signal.idempotent_id,
                    )

        # 6.  Confirmbet?
        if betdata:
            await self._place_and_process(
                install, betdata, orders_created,
            )

    # ------------------------------------------------------------------
    # ?
    # ------------------------------------------------------------------

    async def _is_duplicate(self, signal: BetSignal) -> bool:
        """Check duplicate by idempotent_id in bet_orders."""
        row = await (
            await self.db.execute(
                "SELECT id FROM bet_orders WHERE idempotent_id=?",
                (signal.idempotent_id,),
            )
        ).fetchone()
        if row is not None:
            logger.info(
                "idempotent_id=%s", signal.idempotent_id
            )
            return True
        return False

    # ------------------------------------------------------------------
    #  IntegrityError ?
    # ------------------------------------------------------------------

    async def _create_order(
        self, signal: BetSignal, odds: int, issue: str
    ) -> dict | None:
        """Create order, return None on IntegrityError (duplicate)."""
        try:
            order = await bet_order_create(
                self.db,
                idempotent_id=signal.idempotent_id,
                operator_id=self.operator_id,
                account_id=self.account_id,
                strategy_id=signal.strategy_id,
                issue=issue,
                key_code=signal.key_code,
                amount=signal.amount,
                odds=odds,
                status="pending",
                simulation=1 if signal.simulation else 0,
                martin_level=signal.martin_level,
            )
            return order
        except Exception as e:
            # IntegrityErrorUNIQUE ?
            if "UNIQUE constraint failed" in str(e) or "IntegrityError" in type(e).__name__:
                logger.info(
                    "IntegrityError idempotent_id=%s",
                    signal.idempotent_id,
                )
                return None
            raise

    # ------------------------------------------------------------------
    # Confirmbet  + 处理
    # ------------------------------------------------------------------

    async def _place_and_process(
        self,
        install: InstallInfo,
        betdata: list[dict],
        orders_created: list[dict],
    ) -> None:
        """Call adapter.place_bet() and process result.

        当平台返回 succeed=5（赔率已变）时，自动从平台获取实时赔率并重试一次。
        """
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        try:
            result: BetResult = await self.adapter.place_bet(
                install.issue, betdata
            )
        except (TimeoutError, asyncio.TimeoutError):
            await self._mark_all_failed(
                orders_created, "timeout", now_str,
            )
            await self._send_bet_fail_alert(
                install.issue, "Confirmbet 超时"
            )
            return
        except Exception as e:
            fail_reason = f"异常: {type(e).__name__}: {e}"
            await self._mark_all_failed(
                orders_created, fail_reason, now_str,
            )
            await self._send_bet_fail_alert(
                install.issue, fail_reason,
            )
            return

        # succeed=5: 赔率已变，用实时赔率重试一次
        if result.succeed == 5:
            logger.info(
                "赔率已变(succeed=5), 从平台获取实时赔率重试 issue=%s account_id=%d",
                install.issue, self.account_id,
            )
            result = await self._retry_with_live_odds(install, betdata)
            if result is None:
                # 获取实时赔率失败
                fail_reason = "赔率已变, 重新获取实时赔率失败"
                await self._mark_all_failed(
                    orders_created, fail_reason, now_str,
                )
                await self._send_bet_fail_alert(install.issue, fail_reason)
                return

        if result.succeed == 1:
            total_bet_amount = 0
            for order in orders_created:
                if order.get("simulation", 0) == 1:
                    continue
                try:
                    await bet_order_update_status(
                        self.db,
                        order_id=order["id"],
                        operator_id=self.operator_id,
                        status="bet_success",
                        bet_at=now_str,
                        bet_response=str(result.raw_response),
                    )
                    total_bet_amount += order.get("amount", 0)
                except Exception:
                    logger.error(
                        "更新订单状态失败 order_id=%d", order["id"],
                        exc_info=True,
                    )
            # 下注成功后扣减本地余额
            if total_bet_amount > 0:
                await self._deduct_balance(total_bet_amount)
        else:
            fail_reason = f"succeed={result.succeed}, message={result.message}"
            await self._mark_all_failed(
                orders_created, fail_reason, now_str,
            )
            await self._send_bet_fail_alert(
                install.issue, fail_reason,
            )

    async def _retry_with_live_odds(
        self, install: InstallInfo, betdata: list[dict]
    ) -> BetResult | None:
        """从平台实时获取赔率，用新赔率重建 betdata 并重试下注。"""
        try:
            live_odds = await self.adapter.load_odds(install.issue)
        except Exception:
            logger.exception(
                "获取实时赔率失败 issue=%s account_id=%d",
                install.issue, self.account_id,
            )
            return None

        if not live_odds:
            logger.warning(
                "实时赔率为空 issue=%s account_id=%d",
                install.issue, self.account_id,
            )
            return None

        # 用实时赔率替换 betdata 中的 Odds
        new_betdata: list[dict] = []
        for bet in betdata:
            key_code = bet["KeyCode"]
            new_odds = live_odds.get(key_code, 0)
            if new_odds == 0:
                logger.info(
                    "实时赔率中无此玩法 key_code=%s, 跳过", key_code,
                )
                continue
            new_betdata.append({
                "KeyCode": key_code,
                "Amount": bet["Amount"],
                "Odds": new_odds,
            })

        if not new_betdata:
            return None

        logger.info(
            "使用实时赔率重试下注 issue=%s items=%d account_id=%d",
            install.issue, len(new_betdata), self.account_id,
        )

        try:
            return await self.adapter.place_bet(install.issue, new_betdata)
        except Exception:
            logger.exception(
                "实时赔率重试下注异常 issue=%s account_id=%d",
                install.issue, self.account_id,
            )
            return None

    async def _mark_all_failed(
        self,
        orders: list[dict],
        fail_reason: str,
        bet_at: str,
    ) -> None:
        """Mark all non-simulation orders as bet_failed."""
        for order in orders:
            if order.get("simulation", 0) == 1:
                continue  # ?
            try:
                await bet_order_update_status(
                    self.db,
                    order_id=order["id"],
                    operator_id=self.operator_id,
                    status="bet_failed",
                    fail_reason=fail_reason,
                    bet_at=bet_at,
                )
            except Exception:
                logger.error(
                    "order_id=%d", order["id"],
                    exc_info=True,
                )

    # ------------------------------------------------------------------
    # 
    # ------------------------------------------------------------------

    async def _send_bet_fail_alert(self, issue: str, reason: str) -> None:
        """Send bet failure alert."""
        await self.alert_service.send(
            operator_id=self.operator_id,
            alert_type="bet_fail",
            title=f"?{issue}",
            detail=reason,
            account_id=self.account_id,
        )

    # ------------------------------------------------------------------
    # 
    # ------------------------------------------------------------------

    @staticmethod
    def _find_order(
        orders: list[dict], idempotent_id: str
    ) -> dict | None:
        """Find order by idempotent_id."""
        for o in orders:
            if o.get("idempotent_id") == idempotent_id:
                return o
        return None

    async def _deduct_balance(self, amount: int) -> None:
        """下注成功后扣减本地 gambling_accounts.balance"""
        try:
            row = await (
                await self.db.execute(
                    "SELECT balance FROM gambling_accounts WHERE id=?",
                    (self.account_id,),
                )
            ).fetchone()
            if row is None:
                return
            new_balance = row["balance"] - amount
            await self.db.execute(
                "UPDATE gambling_accounts SET balance=? WHERE id=?",
                (new_balance, self.account_id),
            )
            await self.db.commit()
            logger.info(
                "本地余额扣减 account_id=%d amount=%d new_balance=%d",
                self.account_id, amount, new_balance,
            )
        except Exception:
            logger.exception(
                "扣减余额异常 account_id=%d amount=%d", self.account_id, amount,
            )

