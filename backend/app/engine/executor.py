"""Docstring placeholder."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

_BJT = timezone(timedelta(hours=8))


def _now_bj() -> str:
    """返回北京时间字符串"""
    return datetime.now(_BJT).strftime("%Y-%m-%d %H:%M:%S")

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
    odds_get_latest_map,
    odds_has_records,
    simulation_bet_order_create,
    simulation_bet_order_update,
)
from app.utils.logger import log_bet, log_countdown_validation
from app.utils.strategy_timing import SAFE_CLOSE_THRESHOLD, WAVE_STRATEGY_TYPES

logger = logging.getLogger(__name__)


@dataclass
class ExecutionReport:
    """Execution side effects that should be handled by worker."""

    stop_strategy_ids: dict[int, str] = field(default_factory=dict)


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
        platform_type: str,
    ) -> None:
        self.db = db
        self.adapter = adapter
        self.risk = risk
        self.alert_service = alert_service
        self.operator_id = operator_id
        self.account_id = account_id
        self.platform_type = platform_type

    def _log_confirmbet_terminal(
        self,
        *,
        issue: str,
        betdata: list[dict],
        result: str,
        error_code: str,
        attempt: str,
        succeed: int | None = None,
        message: str | None = None,
        terminal: bool = True,
        excluded_from_countdown_quant: bool = False,
    ) -> None:
        total_amount = sum(int(item.get("Amount", 0) or 0) for item in betdata)
        log_bet(
            operator_id=self.operator_id,
            account_id=self.account_id,
            issue=issue,
            key_code="CONFIRMBET_BATCH",
            amount=total_amount,
            result=result,
            request_item_count=len(betdata),
            attempt=attempt,
            succeed=succeed,
            error_code=error_code,
            terminal=terminal,
            excluded_from_countdown_quant=excluded_from_countdown_quant,
            platform_type=self.platform_type,
            message_text=message,
        )

    async def execute(
        self, install: InstallInfo, signals: list[BetSignal]
    ) -> ExecutionReport:
        """Execute signals with deadline protection."""
        report = ExecutionReport()
        if not signals:
            return report

        deadline_seconds = install.close_countdown_sec - 10
        if deadline_seconds <= 0:
            logger.info(
                "issue=%sclose_timestamp=%d",
                install.issue, install.close_countdown_sec,
            )
            return report

        try:
            return await asyncio.wait_for(
                self._execute_inner(install, signals),
                timeout=deadline_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "deadline ?pendingissue=%s",
                install.issue,
            )
            # ?pending?
            return report

    async def _execute_inner(
        self, install: InstallInfo, signals: list[BetSignal]
    ) -> ExecutionReport:
        """Inner execution logic."""
        report = ExecutionReport()
        # 1. 
        new_signals = []
        for s in signals:
            if not await self._is_duplicate(s):
                new_signals.append(s)

        if not new_signals:
            return report

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
                if check.stop_strategy:
                    stop_reason = check.stop_reason or check.reason
                    report.stop_strategy_ids[signal.strategy_id] = stop_reason

        if not approved:
            return report

        approved = await self._apply_red_wave_atomic_balance_guard(
            approved, report
        )
        if not approved:
            return report

        # 3. Resolve odds for this submit.
        # Runtime resolution can use live/latest odds when confirmation lags.
        odds = await self._resolve_execution_odds(install)

        if odds is None:
            has_records = await odds_has_records(
                self.db,
                account_id=self.account_id,
                platform_type=self.platform_type,
            )
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
            return report

        # 4.  betdata KeyCode ? 
        betdata: list[dict] = []
        orders_created: list[dict] = []
        simulation_signals: list[BetSignal] = []
        request_signals: list[BetSignal] = []

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
                request_signals.append(signal)

        # 5. ?
        if simulation_signals:
            for signal in simulation_signals:
                order = self._find_order(orders_created, signal.idempotent_id)
                if order:
                    await simulation_bet_order_update(
                        self.db,
                        order_id=order["id"],
                        status="bet_success",
                        operator_id=self.operator_id,
                        bet_at=_now_bj(),
                    )
                    logger.info(
                        "idempotent_id=%s",
                        signal.idempotent_id,
                    )

        # 6.  Confirmbet?
        if betdata:
            self._log_request_summary(install.issue, request_signals, betdata)
            await self._place_and_process(
                install, betdata, orders_created,
            )
        return report

    async def _resolve_execution_odds(
        self, install: InstallInfo
    ) -> dict[str, int] | None:
        confirmed_odds = await odds_get_confirmed_map(
            self.db,
            account_id=self.account_id,
            platform_type=self.platform_type,
        )
        if confirmed_odds is not None:
            return confirmed_odds

        live_odds = await self._load_live_odds_for_execution(install)
        if live_odds:
            return live_odds

        latest_odds = await odds_get_latest_map(
            self.db,
            account_id=self.account_id,
            platform_type=self.platform_type,
        )
        if latest_odds is not None:
            logger.warning(
                "using unconfirmed latest odds for execution account_id=%d platform=%s issue=%s",
                self.account_id,
                self.platform_type,
                install.issue,
            )
            return latest_odds

        return None

    async def _load_live_odds_for_execution(
        self, install: InstallInfo
    ) -> dict[str, int] | None:
        if install.state != 1 or install.close_countdown_sec <= SAFE_CLOSE_THRESHOLD:
            return None

        try:
            live_odds = await self.adapter.load_odds(install.issue)
        except Exception:
            logger.exception(
                "runtime odds refresh failed issue=%s account_id=%d",
                install.issue,
                self.account_id,
            )
            return None

        live_non_zero = {
            key: value for key, value in live_odds.items() if value > 0
        }
        if not live_non_zero:
            logger.warning(
                "runtime odds refresh returned empty issue=%s account_id=%d",
                install.issue,
                self.account_id,
            )
            return None
        return live_non_zero

    def _log_request_summary(
        self,
        issue: str,
        signals: list[BetSignal],
        betdata: list[dict],
    ) -> None:
        dw3_metadata_by_strategy: dict[int, dict] = {}
        for signal in signals:
            metadata = signal.metadata if isinstance(signal.metadata, dict) else {}
            if metadata.get("strategy_kind") != "dw3":
                continue
            dw3_metadata_by_strategy.setdefault(signal.strategy_id, metadata)

        if not dw3_metadata_by_strategy:
            return

        dw3_betdata = [
            bet
            for bet in betdata
            if str(bet.get("KeyCode", "")).upper().startswith("DW3_")
        ]
        if not dw3_betdata:
            return

        effective_groups: set[str] = set()
        blocked_groups: set[str] = set()
        for metadata in dw3_metadata_by_strategy.values():
            effective_groups.update(str(item) for item in metadata.get("effective_groups", ()))
            blocked_groups.update(str(item) for item in metadata.get("blocked_groups", ()))

        unique_key_count = len(
            {
                str(bet.get("KeyCode", "")).upper()
                for bet in dw3_betdata
                if str(bet.get("KeyCode", "")).strip()
            }
        )
        total_amount = sum(int(bet.get("Amount", 0) or 0) for bet in dw3_betdata)

        log_bet(
            operator_id=self.operator_id,
            account_id=self.account_id,
            issue=issue,
            key_code="DW3_BATCH",
            amount=total_amount,
            result="request_submit",
            strategy_ids=sorted(dw3_metadata_by_strategy),
            effective_groups=sorted(effective_groups),
            blocked_groups=sorted(blocked_groups),
            unique_key_count=unique_key_count,
            dw3_request_item_count=len(dw3_betdata),
            request_item_count=len(betdata),
            total_amount=total_amount,
        )

    async def _apply_red_wave_atomic_balance_guard(
        self,
        approved: list[BetSignal],
        report: ExecutionReport,
    ) -> list[BetSignal]:
        """Prevent partial placement for one wave strategy in one issue."""
        if not approved:
            return approved

        strategy_ids = {s.strategy_id for s in approved}
        strategy_types = await self._get_strategy_types(strategy_ids)
        red_strategy_ids = {
            sid
            for sid, stype in strategy_types.items()
            if stype in WAVE_STRATEGY_TYPES
        }
        if not red_strategy_ids:
            return approved

        balance = await self._get_account_balance()
        blocked: set[int] = set()

        for sid in red_strategy_ids:
            total_amount = sum(
                s.amount
                for s in approved
                if s.strategy_id == sid and not s.simulation
            )
            if total_amount > balance:
                blocked.add(sid)
                report.stop_strategy_ids[sid] = "balance_insufficient"

        if not blocked:
            return approved

        return [s for s in approved if s.strategy_id not in blocked]

    async def _get_strategy_types(self, strategy_ids: set[int]) -> dict[int, str]:
        if not strategy_ids:
            return {}
        placeholders = ",".join("?" for _ in strategy_ids)
        rows = await (
            await self.db.execute(
                f"SELECT id, type FROM strategies WHERE id IN ({placeholders})",
                tuple(strategy_ids),
            )
        ).fetchall()
        return {int(row["id"]): str(row["type"]) for row in rows}

    async def _get_account_balance(self) -> int:
        row = await (
            await self.db.execute(
                "SELECT balance FROM gambling_accounts WHERE id=?",
                (self.account_id,),
            )
        ).fetchone()
        if row is None:
            return 0
        return int(row["balance"])

    # ------------------------------------------------------------------
    # ?
    # ------------------------------------------------------------------

    async def _is_duplicate(self, signal: BetSignal) -> bool:
        """Check duplicate by idempotent_id in both real/simulation order tables."""
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

        sim_row = await (
            await self.db.execute(
                "SELECT id FROM simulation_bet_orders WHERE idempotent_id=?",
                (signal.idempotent_id,),
            )
        ).fetchone()
        if sim_row is not None:
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
            if signal.simulation:
                order = await simulation_bet_order_create(
                    self.db,
                    idempotent_id=signal.idempotent_id,
                    operator_id=self.operator_id,
                    account_id=self.account_id,
                    strategy_id=signal.strategy_id,
                    issue=issue,
                    platform_type=self.platform_type,
                    key_code=signal.key_code,
                    amount=signal.amount,
                    odds=odds,
                    status="pending",
                )
                order["simulation"] = 1
            else:
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
                    simulation=0,
                    martin_level=signal.martin_level,
                    actual_platform_type=self.platform_type,
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
        now_str = _now_bj()
        effective_betdata = betdata

        try:
            result: BetResult = await self.adapter.place_bet(
                install.issue, betdata
            )
        except (TimeoutError, asyncio.TimeoutError):
            self._log_confirmbet_terminal(
                issue=install.issue,
                betdata=betdata,
                result="confirmbet_failed",
                error_code="TIMEOUT",
                attempt="initial",
                message="timeout",
                excluded_from_countdown_quant=True,
            )
            await self._mark_all_failed(
                orders_created, "timeout", now_str,
            )
            await self._send_bet_fail_alert(
                install.issue, "Confirmbet 超时"
            )
            return
        except Exception as e:
            fail_reason = f"异常: {type(e).__name__}: {e}"
            self._log_confirmbet_terminal(
                issue=install.issue,
                betdata=betdata,
                result="confirmbet_failed",
                error_code=type(e).__name__.upper(),
                attempt="initial",
                message=str(e),
                excluded_from_countdown_quant=isinstance(e, ConnectionError),
            )
            await self._mark_all_failed(
                orders_created, fail_reason, now_str,
            )
            await self._send_bet_fail_alert(
                install.issue, fail_reason,
            )
            return

        # succeed=5: 赔率已变，用实时赔率重试一次
        if result.succeed == 5:
            self._log_confirmbet_terminal(
                issue=install.issue,
                betdata=betdata,
                result="confirmbet_retry",
                error_code="ODDS_CHANGED",
                attempt="initial",
                succeed=result.succeed,
                message=result.message,
                terminal=False,
            )
            logger.info(
                "赔率已变(succeed=5), 从平台获取实时赔率重试 issue=%s account_id=%d",
                install.issue, self.account_id,
            )
            retry_result = await self._retry_with_live_odds(install, betdata)
            if retry_result is None:
                # 获取实时赔率失败
                fail_reason = "赔率已变, 重新获取实时赔率失败"
                self._log_confirmbet_terminal(
                    issue=install.issue,
                    betdata=betdata,
                    result="confirmbet_failed",
                    error_code="ODDS_CHANGED",
                    attempt="retry",
                    message="retry_window_closed_or_live_odds_unavailable",
                )
                await self._mark_all_failed(
                    orders_created, fail_reason, now_str,
                )
                await self._send_bet_fail_alert(install.issue, fail_reason)
                return
            result, effective_betdata = retry_result

        if result.succeed == 1:
            retry_attempt = "retry" if bool((result.raw_response or {}).get("_retry_attempt")) else "initial"
            self._log_confirmbet_terminal(
                issue=install.issue,
                betdata=effective_betdata,
                result="confirmbet_success",
                error_code="SUCCESS",
                attempt=retry_attempt,
                succeed=result.succeed,
                message=result.message,
            )
            total_bet_amount = 0
            effective_odds_by_key = {
                str(item.get("KeyCode")): int(item.get("Odds", 0) or 0)
                for item in effective_betdata
            }
            for order in orders_created:
                if order.get("simulation", 0) == 1:
                    continue
                try:
                    extra_fields = {
                        "bet_at": now_str,
                        "bet_response": str(result.raw_response),
                    }
                    effective_odds = effective_odds_by_key.get(str(order.get("key_code")))
                    if effective_odds:
                        extra_fields["odds"] = effective_odds
                    await bet_order_update_status(
                        self.db,
                        order_id=order["id"],
                        operator_id=self.operator_id,
                        status="bet_success",
                        **extra_fields,
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
            retry_attempt = "retry" if bool((result.raw_response or {}).get("_retry_attempt")) else "initial"
            self._log_confirmbet_terminal(
                issue=install.issue,
                betdata=effective_betdata,
                result="confirmbet_failed",
                error_code=result.error_code,
                attempt=retry_attempt,
                succeed=result.succeed,
                message=result.message,
                excluded_from_countdown_quant=result.error_code in {"UNKNOWN", "CLOSED"},
            )
            fail_reason = f"succeed={result.succeed}, message={result.message}"
            await self._mark_all_failed(
                orders_created, fail_reason, now_str,
            )
            await self._send_bet_fail_alert(
                install.issue, fail_reason,
            )

    async def _retry_with_live_odds(
        self, install: InstallInfo, betdata: list[dict]
    ) -> tuple[BetResult, list[dict]] | None:
        """从平台实时获取赔率，用新赔率重建 betdata 并重试下注。"""
        if not await self._retry_window_is_open(install):
            return None

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
                return None
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
            result = await self.adapter.place_bet(install.issue, new_betdata)
            if not isinstance(result.raw_response, dict):
                result.raw_response = {}
            result.raw_response["_retry_attempt"] = True
            result.raw_response["_retry_item_count"] = len(new_betdata)
            return result, new_betdata
        except Exception:
            logger.exception(
                "实时赔率重试下注异常 issue=%s account_id=%d",
                install.issue, self.account_id,
            )
            return None

    async def _retry_window_is_open(self, install: InstallInfo) -> bool:
        """Revalidate issue/state/remaining before retrying odds-changed orders."""
        fallback_open = (
            install.state == 1 and install.close_countdown_sec > SAFE_CLOSE_THRESHOLD
        )
        try:
            detail = await self.adapter.get_current_install_detail()
        except Exception:
            logger.exception(
                "retry revalidation failed issue=%s account_id=%d",
                install.issue,
                self.account_id,
            )
            return fallback_open

        if not isinstance(detail, dict):
            return fallback_open

        issue = self._detail_text(detail, "installments", "Installments")
        state = self._detail_int(detail, "state", "State")
        remaining = self._detail_int(
            detail,
            "close_countdown_sec",
            "CloseCountdownSec",
            "CloseTimeStamp",
        )
        if not issue and state == 0 and remaining == 0:
            return fallback_open

        is_open = (
            issue == install.issue
            and state == 1
            and remaining > SAFE_CLOSE_THRESHOLD
        )
        log_countdown_validation(
            operator_id=self.operator_id,
            account_id=self.account_id,
            issue=install.issue,
            phase="retry_submit",
            allowed=is_open,
            state=state,
            close_countdown_sec=remaining,
            platform_type=self.platform_type,
            expected_issue=install.issue,
            current_issue=issue or None,
            reason=None if is_open else "retry_window_closed",
        )
        if not is_open:
            logger.info(
                "retry window closed issue=%s current_issue=%s state=%s remaining=%s account_id=%d",
                install.issue,
                issue,
                state,
                remaining,
                self.account_id,
            )
        return is_open

    @staticmethod
    def _detail_text(detail: dict, *keys: str) -> str:
        for key in keys:
            value = detail.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    @staticmethod
    def _detail_int(detail: dict, *keys: str) -> int:
        text = BetExecutor._detail_text(detail, *keys)
        if not text:
            return 0
        try:
            return int(float(text))
        except ValueError:
            return 0

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

