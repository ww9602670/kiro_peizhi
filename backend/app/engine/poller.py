"""

IssuePoller 
1.  5s  GetCurrentInstall
2. 19:56-20:33, 06:00-07:00 1 30s 
3.  3  State1   
4. State=1   
5. issue  is_new_issue=True
6. /
"""
from __future__ import annotations

import logging
from datetime import datetime, time as dt_time
from typing import Callable, Optional

from app.engine.adapters.base import InstallInfo, PlatformAdapter
from app.engine.rate_limiter import RateLimiter
from app.engine.shared_market_runtime import (
    MARKET_STATE_SHARED_ERROR,
    PUBLIC_STATE_LOCAL_FALLBACK,
    PUBLIC_STATE_PROCESSING,
    SharedMarketRuntime,
    normalize_shared_market_state,
)

logger = logging.getLogger(__name__)

# 
NORMAL_INTERVAL = 5
SLOW_INTERVAL = 30

# 
RANDOM_DOWNTIME_THRESHOLD = 3


class IssuePoller:
    """

    Args:
        adapter: 
        rate_limiter: 
        downtime_ranges:  [("HH:MM", "HH:MM"), ...]
        time_func:  datetime
    """

    def __init__(
        self,
        adapter: PlatformAdapter,
        rate_limiter: RateLimiter,
        downtime_ranges: Optional[list[tuple[str, str]]] = None,
        time_func: Optional[Callable[[], datetime]] = None,
        shared_market_runtime: Optional[SharedMarketRuntime] = None,
        shared_market_owner_key: str = "",
        shared_market_platform_type: str = "JND28WEB",
        shared_market_platform_url: Optional[str] = None,
    ) -> None:
        self.adapter = adapter
        self.rate_limiter = rate_limiter
        if downtime_ranges is None:
            self.downtime_ranges = [
                ("19:56", "20:33"),
                ("06:00", "07:00"),
            ]
        else:
            self.downtime_ranges = downtime_ranges
        self._time_func = time_func or datetime.now

        # 
        self.last_issue: str = ""
        self.non_open_count: int = 0
        self._last_non_open_issue: str = ""
        self._in_slow_mode: bool = False
        self._slow_mode_reason: str = ""
        self.shared_market_runtime = shared_market_runtime
        self.shared_market_owner_key = shared_market_owner_key
        self.shared_market_platform_type = (
            (shared_market_platform_type or "JND28WEB").strip().upper()
        )
        self.shared_market_platform_url = (
            shared_market_platform_url
            or getattr(adapter, "base_url", None)
        )
        self.shared_market_state = PUBLIC_STATE_LOCAL_FALLBACK
        self.last_install: Optional[InstallInfo] = None
        self.last_success_at: Optional[datetime] = None

    @property
    def poll_interval(self) -> int:
        """"""
        return SLOW_INTERVAL if self._in_slow_mode else NORMAL_INTERVAL

    def is_known_downtime(self) -> bool:
        """ 1 

         1 
        - 19:56-20:33   19:55-20:33
        - 06:00-07:00   05:59-07:00
        """
        now = self._time_func()
        current = now.time()

        for start_str, end_str in self.downtime_ranges:
            start_h, start_m = map(int, start_str.split(":"))
            end_h, end_m = map(int, end_str.split(":"))

            #  1 
            early_m = start_m - 1
            early_h = start_h
            if early_m < 0:
                early_m = 59
                early_h = (start_h - 1) % 24

            early_start = dt_time(early_h, early_m, 0)
            end_time = dt_time(end_h, end_m, 0)

            if early_start <= end_time:
                # 
                if early_start <= current <= end_time:
                    return True
            else:
                #  23:55 - 01:00
                if current >= early_start or current <= end_time:
                    return True

        return False

    async def _fetch_install_local(self) -> InstallInfo:
        """ RateLimiter  adapter.get_current_install()"""
        return await self.rate_limiter.execute(
            "GetCurrentInstall",
            self.adapter.get_current_install,
        )

    async def _fetch_install(self) -> InstallInfo:
        if self.shared_market_runtime is None:
            install = await self._fetch_install_local()
            self.shared_market_state = PUBLIC_STATE_LOCAL_FALLBACK
            return install

        try:
            install = await self.shared_market_runtime.resolve_install(
                platform_type=self.shared_market_platform_type,
                platform_url=self.shared_market_platform_url,
                owner_key=self.shared_market_owner_key,
                fetch_local_install=self._fetch_install_local,
            )
            market_data_state = getattr(
                install,
                "market_data_state",
                getattr(install, "shared_market_state", PUBLIC_STATE_PROCESSING),
            )
            self.shared_market_state = normalize_shared_market_state(market_data_state)
            return install
        except Exception:
            logger.exception(
                "shared_snapshot_fallback account_key=%s platform=%s",
                self.shared_market_owner_key,
                self.shared_market_platform_type,
            )
            install = await self._fetch_install_local()
            self.shared_market_state = normalize_shared_market_state(
                MARKET_STATE_SHARED_ERROR,
                default=MARKET_STATE_SHARED_ERROR,
            )
            return install

    def _enter_slow_mode(self, reason: str) -> None:
        """"""
        if not self._in_slow_mode:
            self._in_slow_mode = True
            self._slow_mode_reason = reason
            logger.info("%s", reason)

    def _exit_slow_mode(self) -> None:
        """"""
        if self._in_slow_mode:
            logger.info(
                "%s ",
                self._slow_mode_reason,
            )
            self._in_slow_mode = False
            self._slow_mode_reason = ""

    async def poll(self) -> InstallInfo:
        """

        
        1.   /
        2.  GetCurrentInstall 
        3.  3  State1 
        4. State=1 
        5. 
        """
        # 1. 
        if self.is_known_downtime():
            self._enter_slow_mode("known_downtime")
        elif self._slow_mode_reason == "known_downtime":
            # 
            # 
            pass

        # 2. 
        install = await self._fetch_install()

        # 3.  / 
        if install.state != 1:
            # 
            if install.issue == self._last_non_open_issue or self._last_non_open_issue == "":
                self.non_open_count += 1
            else:
                # 
                self.non_open_count = 1
            self._last_non_open_issue = install.issue

            if self.non_open_count >= RANDOM_DOWNTIME_THRESHOLD:
                self._enter_slow_mode("random_downtime")
        else:
            # State=1 
            if self._in_slow_mode and install.issue != self.last_issue:
                # State=1 
                self._exit_slow_mode()
            elif self._in_slow_mode and self._slow_mode_reason == "known_downtime" and not self.is_known_downtime():
                #  State=1
                self._exit_slow_mode()

            # 
            self.non_open_count = 0
            self._last_non_open_issue = ""

        # 4. 
        if install.issue != self.last_issue and self.last_issue != "":
            install.is_new_issue = True
            logger.info(
                "%s  %s", self.last_issue, install.issue
            )

        #  last_issue is_new_issue
        if install.issue != self.last_issue:
            self.last_issue = install.issue

        self.last_install = install
        self.last_success_at = self._time_func()

        return install
