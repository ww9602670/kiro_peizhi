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
from typing import Any, Callable, Optional

from app.engine.adapters.base import InstallInfo, PlatformAdapter
from app.engine.rate_limiter import RateLimiter
from app.engine.shared_market_runtime import (
    DRAW_STATE_NORMAL,
    MARKET_STATE_MARKET_CLOSED,
    MARKET_STATE_SHARED_ERROR,
    MARKET_STATE_SHARED_OK,
    MARKET_STATE_SHARED_STALE,
    PUBLIC_STATE_LOCAL_FALLBACK,
    SharedMarketRuntime,
    normalize_draw_state,
    normalize_market_url,
    normalize_shared_market_state,
)
from app.models import db_ops

logger = logging.getLogger(__name__)

# 
NORMAL_INTERVAL = 5
SLOW_INTERVAL = 30

# 
RANDOM_DOWNTIME_THRESHOLD = 3

ROUTE_STATE_LOCAL = "local"
ROUTE_STATE_SHARED_PENDING = "shared_pending"
ROUTE_STATE_SHARED = "shared"
ROUTE_STATE_SHARED_ERROR_LOCAL_FALLBACK = "shared_error_local_fallback"
_ROUTE_STATES = {
    ROUTE_STATE_LOCAL,
    ROUTE_STATE_SHARED_PENDING,
    ROUTE_STATE_SHARED,
    ROUTE_STATE_SHARED_ERROR_LOCAL_FALLBACK,
}


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
        db: Any | None = None,
        operator_id: int | None = None,
        account_id: int | None = None,
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
        self.shared_data_source_state = ROUTE_STATE_LOCAL
        self.shared_group_id: int | None = None
        self.pending_shared_group_id: int | None = None
        self.handoff_after_issue: str | None = None
        self.db = db
        self.operator_id = operator_id
        self.account_id = account_id
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

    def _route_enabled(self) -> bool:
        return self.db is not None and self.account_id is not None

    @staticmethod
    def _safe_int(value: object) -> int | None:
        if value is None:
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @staticmethod
    def _safe_text(value: object) -> str:
        return str(value or "").strip()

    @staticmethod
    def _route_state(route: dict[str, Any] | None) -> str:
        state = str((route or {}).get("data_source_state") or ROUTE_STATE_LOCAL)
        state = state.strip().lower()
        return state if state in _ROUTE_STATES else ROUTE_STATE_LOCAL

    def _cache_route(self, route: dict[str, Any] | None) -> None:
        self.shared_data_source_state = self._route_state(route)
        self.shared_group_id = self._safe_int((route or {}).get("shared_group_id"))
        self.pending_shared_group_id = self._safe_int(
            (route or {}).get("pending_shared_group_id")
        )
        self.handoff_after_issue = self._safe_text(
            (route or {}).get("handoff_after_issue")
        ) or None

    def _normalized_platform_url(self) -> str:
        return normalize_market_url(self.shared_market_platform_url)

    async def _load_or_create_route(self) -> dict[str, Any] | None:
        if not self._route_enabled():
            return None
        assert self.db is not None
        assert self.account_id is not None

        route = await db_ops.account_shared_route_get(
            self.db,
            account_id=self.account_id,
            platform_type=self.shared_market_platform_type,
        )
        if route is not None:
            self._cache_route(route)
            return route

        route = await db_ops.account_shared_route_upsert(
            self.db,
            account_id=self.account_id,
            operator_id=self.operator_id,
            platform_type=self.shared_market_platform_type,
            normalized_url=self._normalized_platform_url() or None,
            data_source_state=ROUTE_STATE_LOCAL,
        )
        logger.info(
            "shared_route_init account_id=%s platform=%s state=%s",
            self.account_id,
            self.shared_market_platform_type,
            ROUTE_STATE_LOCAL,
        )
        self._cache_route(route)
        return route

    async def _resolve_shared_group_id_for_url(self) -> int | None:
        normalized_url = self._normalized_platform_url()
        if not normalized_url:
            return None

        contracts = getattr(self.shared_market_runtime, "_contracts", None)
        resolver = getattr(contracts, "group_resolve_by_url", None)
        if callable(resolver):
            return self._safe_int(
                await resolver(
                    platform_type=self.shared_market_platform_type,
                    normalized_url=normalized_url,
                )
            )
        return None

    async def _mark_pending_if_url_is_shared(
        self,
        route: dict[str, Any],
        install: InstallInfo,
    ) -> dict[str, Any] | None:
        if not self._route_enabled():
            return route
        assert self.db is not None
        assert self.account_id is not None

        shared_group_id = await self._resolve_shared_group_id_for_url()
        if shared_group_id is None:
            return route

        route = await db_ops.account_shared_route_mark_pending(
            self.db,
            account_id=self.account_id,
            operator_id=self.operator_id,
            platform_type=self.shared_market_platform_type,
            pending_shared_group_id=shared_group_id,
            handoff_after_issue=self._safe_text(install.issue) or None,
            normalized_url=self._normalized_platform_url() or None,
        )
        logger.info(
            "shared_route_pending account_id=%s platform=%s group_id=%s after_issue=%s",
            self.account_id,
            self.shared_market_platform_type,
            shared_group_id,
            self._safe_text(install.issue),
        )
        self._cache_route(route)
        return route

    async def _get_snapshot(self, shared_group_id: int) -> object | None:
        contracts = getattr(self.shared_market_runtime, "_contracts", None)
        getter = getattr(contracts, "snapshot_get_latest", None)
        if callable(getter):
            return await getter(shared_group_id=shared_group_id)
        return None

    def _snapshot_value(self, snapshot: object, *keys: str) -> object:
        if isinstance(snapshot, dict):
            for key in keys:
                if key in snapshot and snapshot[key] is not None:
                    return snapshot[key]
            return None
        for key in keys:
            value = getattr(snapshot, key, None)
            if value is not None:
                return value
        return None

    def _snapshot_market_state(self, snapshot: object) -> str:
        effective_state = getattr(
            self.shared_market_runtime,
            "_effective_snapshot_market_data_state",
            None,
        )
        if callable(effective_state) and not isinstance(snapshot, dict):
            try:
                return normalize_shared_market_state(effective_state(snapshot))
            except Exception:
                logger.exception(
                    "shared_snapshot_state_check_failed account_id=%s platform=%s",
                    self.account_id,
                    self.shared_market_platform_type,
                )

        state = normalize_shared_market_state(
            self._snapshot_value(snapshot, "market_data_state", "shared_market_state"),
            default=MARKET_STATE_SHARED_OK,
        )
        source_status = self._safe_text(
            self._snapshot_value(snapshot, "source_status")
        ).lower()
        if state == MARKET_STATE_SHARED_OK and source_status in {
            "error",
            "failed",
            "offline",
        }:
            return MARKET_STATE_SHARED_ERROR
        if state == MARKET_STATE_SHARED_OK and source_status in {"stale", "expired"}:
            return MARKET_STATE_SHARED_STALE
        if state == MARKET_STATE_SHARED_OK and source_status in {
            "closed",
            "market_closed",
        }:
            return MARKET_STATE_MARKET_CLOSED
        return state

    def _snapshot_draw_state(self, snapshot: object) -> str:
        return normalize_draw_state(
            self._snapshot_value(snapshot, "draw_state"),
            default=DRAW_STATE_NORMAL,
        )

    def _snapshot_issue(self, snapshot: object) -> str:
        return self._safe_text(
            self._snapshot_value(snapshot, "issue", "current_issue", "installments")
        )

    def _snapshot_is_usable_source(self, snapshot: object) -> bool:
        return self._snapshot_market_state(snapshot) in {
            MARKET_STATE_SHARED_OK,
            MARKET_STATE_MARKET_CLOSED,
        }

    def _snapshot_is_safe_boundary(
        self,
        snapshot: object,
        local_install: InstallInfo,
    ) -> bool:
        if not self._snapshot_is_usable_source(snapshot):
            return False
        if self._snapshot_draw_state(snapshot) != DRAW_STATE_NORMAL:
            return False
        snapshot_issue = self._snapshot_issue(snapshot)
        local_issue = self._safe_text(local_install.issue)
        return bool(snapshot_issue and local_issue and snapshot_issue == local_issue)

    def _local_confirmed_after_issue(
        self,
        install: InstallInfo,
        handoff_after_issue: str | None,
    ) -> bool:
        after_issue = self._safe_text(handoff_after_issue)
        current_issue = self._safe_text(install.issue)
        if not after_issue or not current_issue or current_issue == after_issue:
            return False

        pre_result = self._safe_text(install.pre_result)
        if not pre_result:
            return False

        pre_issue = self._safe_text(install.pre_issue)
        return pre_issue == after_issue or bool(pre_issue)

    def _snapshot_to_install(self, snapshot: object) -> InstallInfo:
        to_install = getattr(snapshot, "to_install", None)
        if callable(to_install):
            return to_install()

        issue = self._safe_text(
            self._snapshot_value(snapshot, "issue", "current_issue", "installments")
        )
        state = self._snapshot_value(snapshot, "state", "current_state")
        close_countdown = self._snapshot_value(
            snapshot,
            "close_countdown_sec",
            "close_timestamp",
            "close_countdown",
        )
        open_countdown = self._snapshot_value(
            snapshot,
            "open_countdown_sec",
            "open_timestamp",
            "open_countdown",
        )
        pre_issue = self._safe_text(
            self._snapshot_value(snapshot, "pre_issue", "pre_installments")
        )
        pre_result = self._safe_text(
            self._snapshot_value(snapshot, "pre_result", "open_result", "pre_lottery_result")
        )
        install = InstallInfo(
            issue=issue,
            state=int(state or 0),
            close_countdown_sec=max(0, int(close_countdown or 0)),
            open_countdown_sec=max(0, int(open_countdown or 0)),
            pre_issue=pre_issue,
            pre_result=pre_result,
            is_new_issue=False,
        )
        market_state = self._snapshot_market_state(snapshot)
        setattr(install, "market_data_state", market_state)
        setattr(install, "shared_market_state", market_state)
        setattr(install, "draw_state", self._snapshot_draw_state(snapshot))
        setattr(
            install,
            "next_normal_refresh_at",
            self._snapshot_value(snapshot, "next_normal_refresh_at"),
        )
        setattr(
            install,
            "next_draw_retry_at",
            self._snapshot_value(snapshot, "next_draw_retry_at"),
        )
        setattr(
            install,
            "snapshot_version",
            int(self._snapshot_value(snapshot, "snapshot_version") or 0),
        )
        setattr(
            install,
            "message_code",
            self._snapshot_value(snapshot, "message_code"),
        )
        setattr(
            install,
            "message_text",
            self._snapshot_value(snapshot, "message_text"),
        )
        return install

    async def _set_route_local(self, reason: str) -> None:
        if not self._route_enabled():
            return
        assert self.db is not None
        assert self.account_id is not None
        route = await db_ops.account_shared_route_set_state(
            self.db,
            account_id=self.account_id,
            platform_type=self.shared_market_platform_type,
            data_source_state=ROUTE_STATE_LOCAL,
            fallback_reason=reason,
        )
        logger.info(
            "shared_route_local account_id=%s platform=%s reason=%s",
            self.account_id,
            self.shared_market_platform_type,
            reason,
        )
        self._cache_route(route)

    async def _mark_route_fallback(
        self,
        shared_group_id: int | None,
        reason: str,
    ) -> None:
        if not self._route_enabled():
            return
        assert self.db is not None
        assert self.account_id is not None
        route = await db_ops.account_shared_route_mark_fallback(
            self.db,
            account_id=self.account_id,
            platform_type=self.shared_market_platform_type,
            shared_group_id=shared_group_id,
            fallback_reason=reason,
        )
        logger.warning(
            "shared_route_fallback account_id=%s platform=%s group_id=%s reason=%s",
            self.account_id,
            self.shared_market_platform_type,
            shared_group_id,
            reason,
        )
        self._cache_route(route)

    async def _complete_handoff(
        self,
        shared_group_id: int,
        confirmed_issue: str | None,
        reason: str,
    ) -> None:
        if not self._route_enabled():
            return
        assert self.db is not None
        assert self.account_id is not None
        route = await db_ops.account_shared_route_complete_handoff(
            self.db,
            account_id=self.account_id,
            platform_type=self.shared_market_platform_type,
            shared_group_id=shared_group_id,
            confirmed_issue=confirmed_issue,
        )
        logger.info(
            "shared_route_handoff_complete account_id=%s platform=%s group_id=%s issue=%s reason=%s",
            self.account_id,
            self.shared_market_platform_type,
            shared_group_id,
            confirmed_issue,
            reason,
        )
        self._cache_route(route)

    async def _fetch_install_legacy_runtime(self) -> InstallInfo:
        install = await self._fetch_install_local()
        self.shared_market_state = PUBLIC_STATE_LOCAL_FALLBACK
        self.shared_data_source_state = ROUTE_STATE_LOCAL
        return install

    async def _fetch_install_pending(
        self,
        route: dict[str, Any],
    ) -> InstallInfo:
        install = await self._fetch_install_local()
        self.shared_market_state = PUBLIC_STATE_LOCAL_FALLBACK
        self._cache_route(route)

        pending_group_id = self._safe_int(route.get("pending_shared_group_id"))
        if pending_group_id is None:
            await self._set_route_local("pending_without_group")
            return install

        handoff_after_issue = self._safe_text(route.get("handoff_after_issue"))
        if not handoff_after_issue:
            route = await db_ops.account_shared_route_mark_pending(
                self.db,
                account_id=self.account_id,
                operator_id=self.operator_id,
                platform_type=self.shared_market_platform_type,
                pending_shared_group_id=pending_group_id,
                handoff_after_issue=self._safe_text(install.issue) or None,
                normalized_url=self._normalized_platform_url() or None,
            )
            self._cache_route(route)
            return install

        if not self._local_confirmed_after_issue(install, handoff_after_issue):
            return install

        try:
            snapshot = await self._get_snapshot(pending_group_id)
        except Exception:
            logger.exception(
                "shared_pending_snapshot_check_failed account_id=%s platform=%s group_id=%s",
                self.account_id,
                self.shared_market_platform_type,
                pending_group_id,
            )
            return install

        if snapshot is not None and self._snapshot_is_safe_boundary(snapshot, install):
            await self._complete_handoff(
                pending_group_id,
                self._safe_text(install.issue) or None,
                "pending_next_draw_confirmed",
            )
        return install

    async def _fetch_install_shared(
        self,
        route: dict[str, Any],
    ) -> InstallInfo:
        shared_group_id = self._safe_int(route.get("shared_group_id"))
        if shared_group_id is None:
            install = await self._fetch_install_local()
            self.shared_market_state = PUBLIC_STATE_LOCAL_FALLBACK
            await self._set_route_local("shared_without_group")
            return install

        try:
            snapshot = await self._get_snapshot(shared_group_id)
        except Exception:
            install = await self._fetch_install_local()
            self.shared_market_state = PUBLIC_STATE_LOCAL_FALLBACK
            logger.exception(
                "shared_snapshot_read_failed account_id=%s platform=%s group_id=%s",
                self.account_id,
                self.shared_market_platform_type,
                shared_group_id,
            )
            await self._mark_route_fallback(shared_group_id, "snapshot_read_error")
            return install

        if snapshot is None:
            install = await self._fetch_install_local()
            self.shared_market_state = PUBLIC_STATE_LOCAL_FALLBACK
            await self._mark_route_fallback(shared_group_id, "snapshot_missing")
            return install

        market_state = self._snapshot_market_state(snapshot)
        if market_state in {MARKET_STATE_SHARED_ERROR, MARKET_STATE_SHARED_STALE}:
            install = await self._fetch_install_local()
            self.shared_market_state = PUBLIC_STATE_LOCAL_FALLBACK
            await self._mark_route_fallback(shared_group_id, f"snapshot_{market_state}")
            return install

        install = self._snapshot_to_install(snapshot)
        self.shared_market_state = market_state
        self._cache_route(route)
        return install

    async def _fetch_install_fallback(
        self,
        route: dict[str, Any],
    ) -> InstallInfo:
        install = await self._fetch_install_local()
        self.shared_market_state = PUBLIC_STATE_LOCAL_FALLBACK
        self._cache_route(route)

        shared_group_id = self._safe_int(route.get("shared_group_id"))
        if shared_group_id is None:
            return install

        try:
            snapshot = await self._get_snapshot(shared_group_id)
        except Exception:
            logger.exception(
                "shared_fallback_snapshot_check_failed account_id=%s platform=%s group_id=%s",
                self.account_id,
                self.shared_market_platform_type,
                shared_group_id,
            )
            return install

        if snapshot is not None and self._snapshot_is_safe_boundary(snapshot, install):
            await self._complete_handoff(
                shared_group_id,
                self._safe_text(install.issue) or None,
                "fallback_snapshot_recovered",
            )
        return install

    async def _fetch_install(self) -> InstallInfo:
        if not self._route_enabled():
            return await self._fetch_install_legacy_runtime()

        try:
            route = await self._load_or_create_route()
        except Exception:
            logger.exception(
                "shared_route_load_failed account_id=%s platform=%s",
                self.account_id,
                self.shared_market_platform_type,
            )
            install = await self._fetch_install_local()
            self.shared_market_state = PUBLIC_STATE_LOCAL_FALLBACK
            return install

        state = self._route_state(route)
        if state == ROUTE_STATE_SHARED:
            return await self._fetch_install_shared(route or {})
        if state == ROUTE_STATE_SHARED_PENDING:
            return await self._fetch_install_pending(route or {})
        if state == ROUTE_STATE_SHARED_ERROR_LOCAL_FALLBACK:
            return await self._fetch_install_fallback(route or {})

        install = await self._fetch_install_local()
        self.shared_market_state = PUBLIC_STATE_LOCAL_FALLBACK
        if route is not None:
            try:
                await self._mark_pending_if_url_is_shared(route, install)
            except Exception:
                logger.exception(
                    "shared_route_pending_check_failed account_id=%s platform=%s",
                    self.account_id,
                    self.shared_market_platform_type,
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
