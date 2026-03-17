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
import time
from typing import Any

import aiosqlite

from app.models.db_ops import alert_create

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

        #  warning
        level = ALERT_LEVEL_MAP.get(alert_type, "warning")

        #  DB
        await alert_create(
            self.db,
            operator_id=operator_id,
            type=alert_type,
            level=level,
            title=title,
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
