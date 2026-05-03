"""AlertService  

Phase 4.5: send + /
Phase 9.1:  12  + 

12 

9 
  - login_failcritical
  - captcha_failwarning 5 
  - session_lostwarning
  - bet_failwarning
  - reconcile_errorcritical
  - balance_lowwarning 3 
  - stop_lossinfo
  - take_profitinfo
  - martin_resetinfo

 1 
  - platform_limitwarning

2 
  - system_api_failcritical30%+ 
  - consecutive_failcritical 5 
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import aiosqlite

from app.models.db_ops import alert_create

logger = logging.getLogger(__name__)

# 
#   12 
# 

ALERT_LEVEL_MAP: dict[str, str] = {
    # 9 
    "login_fail": "critical",
    "captcha_fail": "warning",
    "session_lost": "warning",
    "bet_fail": "warning",
    "reconcile_error": "critical",
    "balance_low": "warning",
    "stop_loss": "info",
    "take_profit": "info",
    "martin_reset": "info",
    #  1 
    "platform_limit": "warning",
    # 
    "system_api_fail": "critical",
    "consecutive_fail": "critical",
    # 结算相关告警
    "settlement_data_missing": "warning",
    "settle_api_failed": "critical",
    "settle_timeout": "warning",
    "unsettled_orders": "warning",
    "api_call_failed": "critical",
    "settle_data_expired": "critical",
    "topbetlist_coverage_warning": "warning",
    "match_ambiguity": "warning",
    "worker_lock_conflict": "critical",
    "worker_lock_lost": "critical",
    "session_reconnecting": "warning",
    "session_reconnect_failed": "critical",
    "shared_market_error": "critical",
}

ALERT_OPERATOR_COPY: dict[str, tuple[str, str]] = {
    "login_fail": ("SESSION-003", "账号登录失败，请联系管理员处理。"),
    "captcha_fail": ("SESSION-004", "账号验证失败，请联系管理员处理。"),
    "session_lost": ("SESSION-002", "账号重连失败，请联系管理员处理。"),
    "session_reconnecting": ("SESSION-001", "账号会话异常，系统正在重连。"),
    "session_reconnect_failed": ("SESSION-002", "账号重连失败，请联系管理员处理。"),
    "bet_fail": ("BET-003", "本期下注失败，请检查账号和平台状态。"),
    "platform_limit": ("BET-002", "当前策略过多，可能引发风控导致下注失败。"),
    "shared_market_error": ("SHARED-002", "数据更新变慢，可能影响投注，请联系管理员处理。"),
    "system_api_fail": ("SYSTEM-001", "系统接口异常率升高，请联系管理员处理。"),
    "consecutive_fail": ("SYSTEM-002", "账号连续下注失败，请联系管理员处理。"),
    "settlement_data_missing": ("SETTLE-002", "结算数据暂未返回，系统将继续补偿。"),
    "settle_timeout": ("SETTLE-002", "结算数据暂未返回，系统将继续补偿。"),
    "unsettled_orders": ("SETTLE-001", "订单结算中，请稍后查看。"),
    "settle_api_failed": ("SETTLE-003", "结算失败，请联系管理员处理。"),
    "api_call_failed": ("SETTLE-003", "结算失败，请联系管理员处理。"),
    "settle_data_expired": ("SETTLE-003", "结算失败，请联系管理员处理。"),
    "worker_lock_conflict": ("WORKER-001", "任务执行冲突，系统已自动保护。"),
    "worker_lock_lost": ("WORKER-002", "任务执行锁异常，请联系管理员处理。"),
}

# 
SYSTEM_ALERT_TYPES = {"system_api_fail", "consecutive_fail"}

# 
_DEDUP_WINDOW = 300  # 5 

# 
SYSTEM_API_FAIL_THRESHOLD = 0.30  # 30% 
CONSECUTIVE_FAIL_THRESHOLD = 5    #  5 


class AlertService:
    """

    
    - send(): /
    - send_system_alert(): 
    - check_system_health(): 
    """

    def __init__(self, db: aiosqlite.Connection) -> None:
        self.db = db
        # (operator_id, alert_type, account_id)  last_sent_at timestamp
        self._dedup_cache: dict[tuple[int, str, int | None], float] = {}

    @staticmethod
    def format_operator_title(alert_type: str, fallback_title: str) -> str:
        template = ALERT_OPERATOR_COPY.get(alert_type)
        if template is None:
            return fallback_title
        code, message = template
        return f"{message}日志编号：{code}。"

    async def send(
        self,
        operator_id: int,
        alert_type: str,
        title: str,
        detail: str | None = None,
        account_id: int | None = None,
    ) -> bool:
        """

         (operator_id, alert_type, account_id)  5 
         True False 
        """
        now = time.time()
        dedup_key = (operator_id, alert_type, account_id)

        # 
        last_sent = self._dedup_cache.get(dedup_key)
        if last_sent is not None and (now - last_sent) < _DEDUP_WINDOW:
            return False

        operator_title = self.format_operator_title(alert_type, title)

        if detail:
            logger.info(
                "operator_alert_detail type=%s operator_id=%d account_id=%s detail=%s",
                alert_type,
                operator_id,
                account_id,
                detail[:1000],
            )

        #  warning
        level = ALERT_LEVEL_MAP.get(alert_type, "warning")

        #  DB
        await alert_create(
            self.db,
            operator_id=operator_id,
            type=alert_type,
            level=level,
            title=operator_title,
            detail=detail,
        )

        # 
        self._dedup_cache[dedup_key] = now

        return True

    async def send_system_alert(
        self,
        admin_operator_id: int,
        alert_type: str,
        title: str,
        detail: str | None = None,
    ) -> bool:
        """

        system_api_fail, consecutive_fail
         send() 
        """
        return await self.send(
            operator_id=admin_operator_id,
            alert_type=alert_type,
            title=title,
            detail=detail,
            account_id=None,
        )

    async def check_system_health(
        self,
        admin_operator_id: int,
        active_accounts: list[dict[str, Any]],
        account_fail_counts: dict[int, int],
        account_consecutive_bet_fails: dict[int, int],
    ) -> list[str]:
        """

        
        - admin_operator_id:  operator_id
        - active_accounts: 
        - account_fail_counts: {account_id: }
        - account_consecutive_bet_fails: {account_id: }

        
        """
        triggered: list[str] = []

        # 1. system_api_fail: 30%+ 
        if active_accounts:
            total = len(active_accounts)
            failed = sum(1 for acc in active_accounts if account_fail_counts.get(acc["id"], 0) > 0)
            fail_rate = failed / total
            if fail_rate >= SYSTEM_API_FAIL_THRESHOLD:
                detail = json.dumps(
                    {"fail_rate": round(fail_rate * 100, 1), "failed_accounts": failed, "total_accounts": total},
                    ensure_ascii=False,
                )
                sent = await self.send_system_alert(
                    admin_operator_id=admin_operator_id,
                    alert_type="system_api_fail",
                    title=f" API {failed}/{total} ",
                    detail=detail,
                )
                if sent:
                    triggered.append("system_api_fail")

        # 2. consecutive_fail:  5 
        for account_id, consecutive_fails in account_consecutive_bet_fails.items():
            if consecutive_fails >= CONSECUTIVE_FAIL_THRESHOLD:
                detail = json.dumps(
                    {"account_id": account_id, "consecutive_fails": consecutive_fails},
                    ensure_ascii=False,
                )
                sent = await self.send_system_alert(
                    admin_operator_id=admin_operator_id,
                    alert_type="consecutive_fail",
                    title=f" {account_id}  {consecutive_fails} ",
                    detail=detail,
                )
                if sent:
                    triggered.append("consecutive_fail")

        return triggered
