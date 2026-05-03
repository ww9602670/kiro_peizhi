"""Shared market runtime for cross-account current-install snapshots."""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Optional

from app.config import (
    BOCAI_SHARED_DETECTOR_ACCOUNT,
    BOCAI_SHARED_DETECTOR_ENABLED,
    BOCAI_SHARED_DETECTOR_PASSWORD,
)
from app.engine.adapters.base import InstallInfo, RemoteLoginRequired
from app.models import db_ops
from app.utils.captcha import get_shared_captcha_service
from app.utils.platform_url import normalize_platform_url

logger = logging.getLogger(__name__)

DEFAULT_SHARED_MARKET_FRESHNESS_SECONDS = 35
DEFAULT_COLLECTOR_INTERVAL_SECONDS = 25.0
DEFAULT_COLLECTOR_ERROR_BACKOFF_SECONDS = 5.0
DEFAULT_LOCAL_FALLBACK_INTERVAL_SECONDS = 80
DEFAULT_SHARED_CAPTCHA_LOGIN_ATTEMPTS = 3
DRAW_FIRST_REFRESH_DELAY_SECONDS = 10.0
DRAW_PENDING_RETRY_INTERVALS_SECONDS = (10.0, 10.0, 10.0, 5.0, 3.0, 1.0)
DRAW_WAIT_RETRY_INTERVAL_SECONDS = 10.0
SHARED_ERROR_MESSAGE_CODE = "SHARED-002"
SHARED_ERROR_MESSAGE_TEXT = "数据更新变慢，可能影响投注，请联系管理员处理。日志编号：SHARED-002。"

MARKET_STATE_SHARED_OK = "shared_ok"
MARKET_STATE_SHARED_ERROR = "shared_error"
MARKET_STATE_SHARED_STALE = "shared_stale"
MARKET_STATE_MARKET_CLOSED = "market_closed"

DRAW_STATE_NORMAL = "normal"
DRAW_STATE_PENDING = "draw_pending"
DRAW_STATE_WAIT_RETRY = "draw_wait_retry"

# Legacy aliases kept for compatibility during rollout.
PUBLIC_STATE_SHARED_HIT = "shared_hit"
PUBLIC_STATE_LOCAL_FALLBACK = "local_fallback"
PUBLIC_STATE_PROCESSING = "processing"
_MARKET_STATE_ALIASES = {
    MARKET_STATE_SHARED_OK: MARKET_STATE_SHARED_OK,
    MARKET_STATE_SHARED_ERROR: MARKET_STATE_SHARED_ERROR,
    MARKET_STATE_SHARED_STALE: MARKET_STATE_SHARED_STALE,
    MARKET_STATE_MARKET_CLOSED: MARKET_STATE_MARKET_CLOSED,
    PUBLIC_STATE_SHARED_HIT: MARKET_STATE_SHARED_OK,
    PUBLIC_STATE_LOCAL_FALLBACK: MARKET_STATE_SHARED_ERROR,
    PUBLIC_STATE_PROCESSING: MARKET_STATE_SHARED_OK,
}
_DRAW_STATE_ALIASES = {
    DRAW_STATE_NORMAL: DRAW_STATE_NORMAL,
    DRAW_STATE_PENDING: DRAW_STATE_PENDING,
    DRAW_STATE_WAIT_RETRY: DRAW_STATE_WAIT_RETRY,
    PUBLIC_STATE_PROCESSING: DRAW_STATE_PENDING,
}
_SHARED_RELOGIN_ERROR_MARKERS = (
    "login",
    "re-login",
    "relogin",
    "unauthorized",
    "401",
    "403",
    "session",
    "token",
    "captcha",
)


def normalize_market_url(raw_url: str | None) -> str:
    """Normalize platform url into a stable pool key."""
    return normalize_platform_url(raw_url)


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _safe_text(value: object) -> str:
    return "" if value is None else str(value)


def _safe_stripped_text(value: object) -> str:
    return _safe_text(value).strip()


def _should_retry_captcha_login(result: object) -> bool:
    if getattr(result, "success", True):
        return False
    message = _safe_text(getattr(result, "message", "")).lower()
    retry_markers = ("captcha", "verify", "vcode", "token")
    return (
        bool(getattr(result, "captcha_required", False))
        or any(marker in message for marker in retry_markers)
        or not message
    )


def _requires_shared_relogin(exc: BaseException) -> bool:
    if isinstance(exc, RemoteLoginRequired):
        return True
    message = _safe_text(exc).lower()
    return any(marker in message for marker in _SHARED_RELOGIN_ERROR_MARKERS)


def _login_method_accepts_captcha(login_method: Any) -> bool:
    try:
        signature = inspect.signature(login_method)
    except (TypeError, ValueError):
        return True

    params = list(signature.parameters.values())
    if any(
        p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        for p in params
    ):
        return True
    if any(
        p.kind == inspect.Parameter.KEYWORD_ONLY and p.name == "captcha_code"
        for p in params
    ):
        return True

    positional_count = sum(
        1
        for p in params
        if p.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    )
    return positional_count >= 3


async def _login_without_captcha(
    login_method: Any,
    account_name: str,
    account_password: str,
) -> Any:
    return await login_method(account_name, account_password)


async def _login_with_captcha(
    login_method: Any,
    account_name: str,
    account_password: str,
    captcha_code: str,
) -> Any:
    if _login_method_accepts_captcha(login_method):
        try:
            return await login_method(account_name, account_password, captcha_code)
        except TypeError:
            return await login_method(account_name, account_password, captcha_code=captcha_code)
    return await login_method(account_name, account_password)


def _is_shared_detector_account(account_name: object, account_password: object) -> bool:
    detector_account = _safe_stripped_text(BOCAI_SHARED_DETECTOR_ACCOUNT)
    if not detector_account:
        return False
    if _safe_stripped_text(account_name) != detector_account:
        return False

    detector_password = _safe_stripped_text(BOCAI_SHARED_DETECTOR_PASSWORD)
    if not detector_password:
        return True
    return _safe_stripped_text(account_password) == detector_password


def normalize_shared_market_state(
    value: object,
    *,
    default: str = MARKET_STATE_SHARED_OK,
) -> str:
    text = _safe_text(value).strip().lower()
    normalized = _MARKET_STATE_ALIASES.get(text)
    if normalized:
        return normalized
    fallback = _MARKET_STATE_ALIASES.get(_safe_text(default).strip().lower())
    return fallback or MARKET_STATE_SHARED_OK


def normalize_draw_state(
    value: object,
    *,
    default: str = DRAW_STATE_NORMAL,
) -> str:
    text = _safe_text(value).strip().lower()
    normalized = _DRAW_STATE_ALIASES.get(text)
    if normalized:
        return normalized
    fallback = _DRAW_STATE_ALIASES.get(_safe_text(default).strip().lower())
    return fallback or DRAW_STATE_NORMAL


def _format_refresh_at(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def _attach_market_data_state(
    install: InstallInfo,
    state: str,
    *,
    draw_state: str = DRAW_STATE_NORMAL,
    next_normal_refresh_at: str | None = None,
    next_draw_retry_at: str | None = None,
    snapshot_version: int = 0,
    message_code: str | None = None,
    message_text: str | None = None,
) -> InstallInfo:
    normalized = normalize_shared_market_state(state)
    normalized_draw_state = normalize_draw_state(draw_state)
    # Keep both names for compatibility during rollout.
    setattr(install, "market_data_state", normalized)
    setattr(install, "shared_market_state", normalized)
    setattr(install, "draw_state", normalized_draw_state)
    setattr(install, "next_normal_refresh_at", next_normal_refresh_at)
    setattr(install, "next_draw_retry_at", next_draw_retry_at)
    setattr(install, "snapshot_version", max(0, _safe_int(snapshot_version, 0)))
    setattr(install, "message_code", _safe_text(message_code) or None)
    setattr(install, "message_text", _safe_text(message_text) or None)
    return install


def _parse_fetched_at(value: object) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _snapshot_age_seconds(
    fetched_at: Optional[datetime],
    *,
    now: Optional[datetime] = None,
) -> int:
    if fetched_at is None:
        return 0
    current = now
    if current is None:
        current = datetime.now(fetched_at.tzinfo) if fetched_at.tzinfo else datetime.now()
    elif fetched_at.tzinfo is not None and current.tzinfo is None:
        current = current.replace(tzinfo=fetched_at.tzinfo)
    elif fetched_at.tzinfo is None and current.tzinfo is not None:
        current = current.replace(tzinfo=None)
    return max(0, int((current - fetched_at).total_seconds()))


def _next_normal_collector_sleep_seconds(
    *,
    open_countdown_sec: int,
    collector_interval_seconds: float,
) -> float:
    """Schedule normal collection so the first post-draw refresh happens on time."""
    normal_interval = max(0.5, float(collector_interval_seconds))
    countdown = max(0, int(open_countdown_sec))
    if countdown <= 0:
        return normal_interval
    if countdown <= normal_interval:
        return float(countdown) + DRAW_FIRST_REFRESH_DELAY_SECONDS
    return normal_interval


@dataclass(slots=True)
class SharedMarketSnapshot:
    shared_group_id: int
    issue: str
    state: int
    close_countdown_sec: int
    open_countdown_sec: int
    pre_issue: str
    pre_result: str
    fetched_at: Optional[datetime]
    source_status: str = "ok"
    market_data_state: str = MARKET_STATE_SHARED_OK
    draw_state: str = DRAW_STATE_NORMAL
    next_normal_refresh_at: str | None = None
    next_draw_retry_at: str | None = None
    snapshot_version: int = 0
    message_code: str | None = None
    message_text: str | None = None

    def to_install(self, *, now: Optional[datetime] = None) -> InstallInfo:
        age_seconds = _snapshot_age_seconds(self.fetched_at, now=now)
        install = InstallInfo(
            issue=self.issue,
            state=self.state,
            close_countdown_sec=max(0, int(self.close_countdown_sec) - age_seconds),
            open_countdown_sec=max(0, int(self.open_countdown_sec) - age_seconds),
            pre_issue=self.pre_issue,
            pre_result=self.pre_result,
            is_new_issue=False,
        )
        return _attach_market_data_state(
            install,
            self.market_data_state,
            draw_state=self.draw_state,
            next_normal_refresh_at=self.next_normal_refresh_at,
            next_draw_retry_at=self.next_draw_retry_at,
            snapshot_version=self.snapshot_version,
            message_code=self.message_code,
            message_text=self.message_text,
        )


class SharedMarketContracts:
    """Frozen db-op contract bridge for shared market runtime."""

    def __init__(self, db: Any) -> None:
        self._db = db
        self._missing_contracts_reported: set[str] = set()

    async def group_resolve_by_url(
        self,
        *,
        platform_type: str,
        normalized_url: str,
    ) -> Optional[int]:
        result = await self._invoke_with_variants(
            "shared_market_group_resolve_by_url",
            [
                {"platform_type": platform_type, "normalized_url": normalized_url},
                {"line_code": platform_type, "normalized_url": normalized_url},
                {"platform_type": platform_type, "url": normalized_url},
                {"line_code": platform_type, "url": normalized_url},
                {"normalized_url": normalized_url},
                {"url": normalized_url},
            ],
        )
        if result is None:
            return None
        if isinstance(result, dict):
            group_id = (
                result.get("shared_group_id")
                or result.get("group_id")
                or result.get("id")
            )
            return _safe_int(group_id, 0) or None
        group_id = _safe_int(result, 0)
        return group_id or None

    async def snapshot_upsert(
        self,
        *,
        shared_group_id: int,
        install: InstallInfo,
        source_status: str,
        last_error: str | None = None,
        market_data_state: str | None = None,
        draw_state: str | None = None,
        next_normal_refresh_at: str | None = None,
        next_draw_retry_at: str | None = None,
        snapshot_version: int | None = None,
        message_code: str | None = None,
        message_text: str | None = None,
    ) -> None:
        fetched_at_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self._invoke_with_variants(
            "shared_market_snapshot_upsert",
            [
                {
                    "shared_group_id": shared_group_id,
                    "issue": install.issue,
                    "state": install.state,
                    "close_countdown_sec": install.close_countdown_sec,
                    "open_countdown_sec": install.open_countdown_sec,
                    "pre_issue": install.pre_issue,
                    "open_result": install.pre_result,
                    "source_status": source_status,
                    "last_error": last_error,
                    "fetched_at": fetched_at_text,
                    "market_data_state": normalize_shared_market_state(market_data_state),
                    "draw_state": normalize_draw_state(draw_state),
                    "next_normal_refresh_at": next_normal_refresh_at,
                    "next_draw_retry_at": next_draw_retry_at,
                    "snapshot_version": snapshot_version,
                    "message_code": message_code,
                    "message_text": message_text,
                },
                {
                    "shared_group_id": shared_group_id,
                    "installments": install.issue,
                    "state": install.state,
                    "close_timestamp": install.close_countdown_sec,
                    "open_timestamp": install.open_countdown_sec,
                    "pre_installments": install.pre_issue,
                    "pre_lottery_result": install.pre_result,
                    "status": source_status,
                    "last_error": last_error,
                    "fetched_at": fetched_at_text,
                },
            ],
            allow_failure=True,
        )

    async def snapshot_mark_error(
        self,
        *,
        shared_group_id: int,
        last_error: str,
        install: InstallInfo | None = None,
    ) -> None:
        if install is not None:
            await self.snapshot_upsert(
                shared_group_id=shared_group_id,
                install=install,
                source_status="error",
                last_error=last_error,
                market_data_state=MARKET_STATE_SHARED_ERROR,
                message_code=SHARED_ERROR_MESSAGE_CODE,
                message_text=SHARED_ERROR_MESSAGE_TEXT,
            )
            return

        fetched_at_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self._invoke_with_variants(
            "shared_market_snapshot_upsert",
            [
                {
                    "shared_group_id": shared_group_id,
                    "issue": None,
                    "state": None,
                    "close_countdown_sec": None,
                    "open_countdown_sec": None,
                    "pre_issue": None,
                    "open_result": None,
                    "source_status": "error",
                    "last_error": last_error,
                    "fetched_at": fetched_at_text,
                    "market_data_state": MARKET_STATE_SHARED_ERROR,
                    "message_code": SHARED_ERROR_MESSAGE_CODE,
                    "message_text": SHARED_ERROR_MESSAGE_TEXT,
                },
                {
                    "shared_group_id": shared_group_id,
                    "installments": None,
                    "state": None,
                    "close_timestamp": None,
                    "open_timestamp": None,
                    "pre_installments": None,
                    "pre_lottery_result": None,
                    "status": "error",
                    "last_error": last_error,
                    "fetched_at": fetched_at_text,
                },
            ],
            allow_failure=True,
        )

    async def snapshot_get_latest(self, *, shared_group_id: int) -> Optional[SharedMarketSnapshot]:
        row = await self._invoke_with_variants(
            "shared_market_snapshot_get_latest",
            [
                {"shared_group_id": shared_group_id},
                {"group_id": shared_group_id},
            ],
        )
        if not isinstance(row, dict):
            return None
        issue = _safe_text(row.get("issue") or row.get("installments"))
        if not issue:
            return None
        return SharedMarketSnapshot(
            shared_group_id=_safe_int(
                row.get("shared_group_id") or row.get("group_id") or shared_group_id,
                shared_group_id,
            ),
            issue=issue,
            state=_safe_int(row.get("state"), 0),
            close_countdown_sec=_safe_int(
                row.get("close_countdown_sec") or row.get("close_timestamp"),
                0,
            ),
            open_countdown_sec=_safe_int(
                row.get("open_countdown_sec") or row.get("open_timestamp"),
                0,
            ),
            pre_issue=_safe_text(row.get("pre_issue") or row.get("pre_installments")),
            pre_result=_safe_text(
                row.get("pre_result")
                or row.get("pre_lottery_result")
                or row.get("open_result")
            ),
            fetched_at=_parse_fetched_at(
                row.get("fetched_at")
                or row.get("updated_at")
                or row.get("snapshot_fetched_at")
            ),
            source_status=_safe_text(row.get("source_status") or row.get("status") or "ok").lower(),
            market_data_state=normalize_shared_market_state(
                row.get("market_data_state")
                or row.get("shared_market_state"),
            ),
            draw_state=normalize_draw_state(row.get("draw_state")),
            next_normal_refresh_at=_safe_text(row.get("next_normal_refresh_at")) or None,
            next_draw_retry_at=_safe_text(row.get("next_draw_retry_at")) or None,
            snapshot_version=_safe_int(row.get("snapshot_version"), 0),
            message_code=_safe_text(row.get("message_code")) or None,
            message_text=_safe_text(row.get("message_text")) or None,
        )

    async def uncovered_url_touch(
        self,
        *,
        platform_type: str,
        normalized_url: str,
        sample_raw_url: str | None = None,
        last_account_id: int | None = None,
        last_platform_type: str | None = None,
    ) -> dict[str, Any] | None:
        result = await self._invoke_with_variants(
            "shared_market_uncovered_url_touch",
            [
                {
                    "platform_type": platform_type,
                    "normalized_url": normalized_url,
                    "sample_raw_url": sample_raw_url,
                    "last_account_id": last_account_id,
                    "last_platform_type": last_platform_type or platform_type,
                },
                {
                    "platform_type": platform_type,
                    "normalized_url": normalized_url,
                    "sample_url": sample_raw_url,
                    "account_id": last_account_id,
                },
                {
                    "line_code": platform_type,
                    "normalized_url": normalized_url,
                    "sample_raw_url": sample_raw_url,
                    "last_account_id": last_account_id,
                    "last_platform_type": last_platform_type or platform_type,
                },
                {
                    "platform_type": platform_type,
                    "url": normalized_url,
                    "sample_raw_url": sample_raw_url,
                    "last_account_id": last_account_id,
                    "last_platform_type": last_platform_type or platform_type,
                },
                {
                    "line_code": platform_type,
                    "url": normalized_url,
                },
            ],
            allow_failure=True,
        )
        return result if isinstance(result, dict) else None

    async def shared_market_group_list(self, *, include_disabled: bool = False) -> list[dict[str, Any]]:
        result = await self._invoke_with_variants(
            "shared_market_group_list",
            [
                {"include_disabled": include_disabled},
                {"only_disabled": False if include_disabled else True},
            ],
            allow_failure=True,
        )
        if not isinstance(result, list):
            return []
        return [row for row in result if isinstance(row, dict)]

    async def shared_market_group_get(self, *, shared_group_id: int) -> dict[str, Any] | None:
        result = await self._invoke_with_variants(
            "shared_market_group_get",
            [
                {"shared_group_id": shared_group_id},
                {"group_id": shared_group_id},
            ],
            allow_failure=True,
        )
        return result if isinstance(result, dict) else None

    async def uncovered_url_mark_detecting(self, *, row_id: int) -> None:
        await self._invoke_with_variants(
            "shared_market_uncovered_url_mark_detecting",
            [{"row_id": row_id}],
            allow_failure=True,
        )

    async def uncovered_url_mark_matched(self, *, row_id: int, shared_group_id: int) -> None:
        await self._invoke_with_variants(
            "shared_market_uncovered_url_mark_matched",
            [{"row_id": row_id, "shared_group_id": shared_group_id}],
            allow_failure=True,
        )

    async def uncovered_url_mark_review_required(
        self,
        *,
        row_id: int,
        review_status: str = "review_required",
        failure_reason: str | None = None,
    ) -> None:
        await self._invoke_with_variants(
            "shared_market_uncovered_url_mark_review_required",
            [
                {
                    "row_id": row_id,
                    "review_status": review_status,
                    "failure_reason": failure_reason,
                },
                {
                    "row_id": row_id,
                    "status": review_status,
                    "failure_reason": failure_reason,
                },
            ],
            allow_failure=True,
        )

    async def shared_market_group_url_add(
        self,
        *,
        shared_group_id: int,
        normalized_url: str,
    ) -> None:
        await self._invoke_with_variants(
            "shared_market_group_url_add",
            [
                {
                    "shared_group_id": shared_group_id,
                    "normalized_url": normalized_url,
                },
            ],
            allow_failure=True,
        )

    async def active_admin_operator_ids(self) -> list[int]:
        result = await self._invoke_with_variants(
            "operator_list_all",
            [{}],
            allow_failure=True,
        )
        if not isinstance(result, list):
            return []
        ids: list[int] = []
        for row in result:
            if not isinstance(row, dict):
                continue
            if _safe_stripped_text(row.get("role")).lower() != "admin":
                continue
            status = _safe_stripped_text(row.get("status")).lower()
            if status and status != "active":
                continue
            operator_id = _safe_int(row.get("id"), 0)
            if operator_id > 0:
                ids.append(operator_id)
        return ids

    async def admin_alert_create(
        self,
        *,
        operator_id: int,
        title: str,
        detail: str,
    ) -> None:
        await self._invoke_with_variants(
            "alert_create",
            [
                {
                    "operator_id": operator_id,
                    "type": "shared_market_error",
                    "level": "critical",
                    "title": title,
                    "detail": detail,
                },
            ],
            allow_failure=True,
        )

    async def _invoke_with_variants(
        self,
        contract_name: str,
        variants: list[dict[str, Any]],
        *,
        allow_failure: bool = False,
    ) -> Any:
        fn = getattr(db_ops, contract_name, None)
        if fn is None:
            if contract_name not in self._missing_contracts_reported:
                self._missing_contracts_reported.add(contract_name)
                logger.warning("shared_market_contract_missing contract=%s", contract_name)
            return None

        last_type_error: Optional[TypeError] = None
        for kwargs in variants:
            try:
                result = fn(self._db, **kwargs)
            except TypeError as exc:
                last_type_error = exc
                continue
            if inspect.isawaitable(result):
                return await result
            return result

        if allow_failure:
            return None
        if last_type_error is not None:
            logger.warning(
                "shared_market_contract_signature_mismatch contract=%s detail=%s",
                contract_name,
                last_type_error,
            )
        return None


@dataclass(slots=True)
class _CollectorState:
    shared_group_id: int
    owner_key: str
    stop_event: asyncio.Event
    task: asyncio.Task[None]
    cleanup: Callable[[], Awaitable[None]] | None = None


@dataclass(slots=True)
class _DrawRefreshState:
    active: bool = False
    retry_index: int = 0
    trigger_issue: str = ""
    trigger_pre_issue: str = ""
    trigger_pre_result: str = ""

    def start(self, install: InstallInfo) -> None:
        self.active = True
        self.retry_index = 0
        self.trigger_issue = _safe_text(install.issue)
        self.trigger_pre_issue = _safe_text(install.pre_issue)
        self.trigger_pre_result = _safe_text(install.pre_result)

    def reset(self) -> None:
        self.active = False
        self.retry_index = 0
        self.trigger_issue = ""
        self.trigger_pre_issue = ""
        self.trigger_pre_result = ""

    def has_new_draw_data(self, install: InstallInfo) -> bool:
        current_issue = _safe_text(install.issue)
        current_pre_issue = _safe_text(install.pre_issue)
        current_pre_result = _safe_text(install.pre_result)
        return (
            (self.trigger_issue and current_issue and current_issue != self.trigger_issue)
            or (self.trigger_pre_issue and current_pre_issue and current_pre_issue != self.trigger_pre_issue)
            or (self.trigger_pre_result != current_pre_result)
        )


class SharedMarketRuntime:
    """Shared current-install pool with collector lifecycle and fallback."""

    def __init__(
        self,
        *,
        db: Any,
        contracts: Optional[SharedMarketContracts] = None,
        freshness_seconds: int = DEFAULT_SHARED_MARKET_FRESHNESS_SECONDS,
        collector_interval_seconds: float = DEFAULT_COLLECTOR_INTERVAL_SECONDS,
        collector_error_backoff_seconds: float = DEFAULT_COLLECTOR_ERROR_BACKOFF_SECONDS,
    ) -> None:
        self._contracts = contracts or SharedMarketContracts(db)
        self._freshness_seconds = max(1, int(freshness_seconds))
        self._collector_interval_seconds = max(0.5, float(collector_interval_seconds))
        self._collector_error_backoff_seconds = max(0.5, float(collector_error_backoff_seconds))
        self._local_fallback_interval_seconds = DEFAULT_LOCAL_FALLBACK_INTERVAL_SECONDS
        self._collectors: dict[int, _CollectorState] = {}
        self._shared_error_alerted_group_ids: set[int] = set()
        self._last_local_fallback_at: dict[str, datetime] = {}
        self._last_local_fallback_install: dict[str, InstallInfo] = {}
        self._lock = asyncio.Lock()

    async def ensure_enabled_collectors(self) -> int:
        """Start collectors for enabled shared groups with best-effort config."""
        started = 0
        groups = await self._contracts.shared_market_group_list(include_disabled=False)
        for group_row in groups:
            shared_group_id = self._safe_dict_id(group_row, keys=("id", "shared_group_id", "group_id"))
            if shared_group_id <= 0:
                continue

            full_group = await self._contracts.shared_market_group_get(
                shared_group_id=shared_group_id,
            )
            if not isinstance(full_group, dict):
                full_group = group_row

            if _is_shared_detector_account(
                account_name=full_group.get("collector_account_name"),
                account_password=full_group.get("collector_password_enc"),
            ):
                logger.info(
                    "shared_collector_skip group_id=%d reason=shared_detector_account",
                    shared_group_id,
                )
                continue

            fetcher, owner_key, cleanup = self._build_group_fetcher(
                shared_group_id=shared_group_id,
                shared_group=full_group,
            )
            if fetcher is None:
                logger.info(
                    "shared_collector_skip group_id=%d reason=no_fetcher",
                    shared_group_id,
                )
                continue

            await self._ensure_collector(
                shared_group_id=shared_group_id,
                owner_key=owner_key,
                fetch_local_install=fetcher,
                cleanup=cleanup,
            )
            started += 1
        logger.info("shared_collectors_started count=%d", started)
        return started

    async def discover_and_bind_uncovered_url(
        self,
        *,
        platform_type: str,
        platform_url: str | None,
        account_id: int | None = None,
        sample_raw_url: str | None = None,
    ) -> tuple[int | None, InstallInfo | None]:
        normalized_url = normalize_market_url(platform_url)
        if not normalized_url:
            return None, None

        touch_row = await self._contracts.uncovered_url_touch(
            platform_type=platform_type,
            normalized_url=normalized_url,
            sample_raw_url=sample_raw_url or platform_url,
            last_account_id=account_id,
            last_platform_type=platform_type,
        )
        row_id = self._safe_dict_id(touch_row, keys=("id",))
        if row_id > 0:
            await self._contracts.uncovered_url_mark_detecting(row_id=row_id)

        try:
            discovered_group_id, install = await self._discover_url_group_match(
                platform_type=platform_type,
                normalized_url=normalized_url,
            )
            if discovered_group_id is None:
                if row_id > 0:
                    await self._contracts.uncovered_url_mark_review_required(
                        row_id=row_id,
                        failure_reason="no_shared_group_matched",
                    )
                logger.info(
                    "shared_url_discovery_review_required platform_type=%s normalized_url=%s",
                    platform_type,
                    normalized_url,
                )
                return None, None

            if row_id > 0:
                await self._contracts.uncovered_url_mark_matched(
                    row_id=row_id,
                    shared_group_id=discovered_group_id,
                )
            await self._contracts.shared_market_group_url_add(
                shared_group_id=discovered_group_id,
                normalized_url=normalized_url,
            )
            if install is not None:
                await self._contracts.snapshot_upsert(
                    shared_group_id=discovered_group_id,
                    install=install,
                    source_status="ok",
                )
            logger.info(
                "shared_url_matched platform_type=%s group_id=%d normalized_url=%s",
                platform_type,
                discovered_group_id,
                normalized_url,
            )
            return discovered_group_id, install
        except Exception as exc:
            if row_id > 0:
                await self._contracts.uncovered_url_mark_review_required(
                    row_id=row_id,
                    failure_reason=_safe_text(exc)[:200],
                )
            logger.exception(
                "shared_url_discovery_failed platform_type=%s normalized_url=%s",
                platform_type,
                normalized_url,
            )
            return None, None

    def _is_snapshot_stale(self, snapshot: SharedMarketSnapshot) -> bool:
        if snapshot.fetched_at is None:
            return True
        now = datetime.now(snapshot.fetched_at.tzinfo) if snapshot.fetched_at.tzinfo else datetime.now()
        age_seconds = (now - snapshot.fetched_at).total_seconds()
        return age_seconds > self._freshness_seconds

    def _effective_snapshot_market_data_state(self, snapshot: SharedMarketSnapshot) -> str:
        state = normalize_shared_market_state(snapshot.market_data_state)
        source_status = _safe_text(snapshot.source_status).strip().lower()
        if state == MARKET_STATE_SHARED_OK and source_status in {"error", "failed", "offline"}:
            return MARKET_STATE_SHARED_ERROR
        if state == MARKET_STATE_SHARED_OK and source_status in {"stale", "expired"}:
            return MARKET_STATE_SHARED_STALE
        if state == MARKET_STATE_SHARED_OK and source_status in {"closed", "market_closed"}:
            return MARKET_STATE_MARKET_CLOSED
        if state == MARKET_STATE_SHARED_OK and self._is_snapshot_stale(snapshot):
            return MARKET_STATE_SHARED_STALE
        return state

    def _snapshot_install_with_state(
        self,
        snapshot: SharedMarketSnapshot,
        *,
        market_data_state: str | None = None,
        draw_state: str | None = None,
    ) -> InstallInfo:
        install = snapshot.to_install()
        return _attach_market_data_state(
            install,
            market_data_state or snapshot.market_data_state,
            draw_state=draw_state or snapshot.draw_state,
            next_normal_refresh_at=snapshot.next_normal_refresh_at,
            next_draw_retry_at=snapshot.next_draw_retry_at,
            snapshot_version=snapshot.snapshot_version,
            message_code=snapshot.message_code,
            message_text=snapshot.message_text,
        )

    def _local_fallback_key(self, *, owner_key: str, shared_group_id: int) -> str:
        account_id = self._extract_account_id(owner_key)
        if account_id and account_id > 0:
            return f"account:{account_id}"
        return f"group:{shared_group_id}"

    def _can_use_local_fallback(
        self,
        *,
        market_data_state: str,
        draw_state: str,
    ) -> bool:
        if normalize_draw_state(draw_state) in {DRAW_STATE_PENDING, DRAW_STATE_WAIT_RETRY}:
            return False
        normalized_market_state = normalize_shared_market_state(market_data_state)
        return normalized_market_state in {MARKET_STATE_SHARED_ERROR, MARKET_STATE_SHARED_STALE}

    def _is_market_closed_install(self, install: InstallInfo) -> bool:
        state = _safe_int(getattr(install, "state", 0), 0)
        if state in (1, 2, 3):
            return False
        return (
            state == 0
            and _safe_int(getattr(install, "close_countdown_sec", 0), 0) <= 0
            and _safe_int(getattr(install, "open_countdown_sec", 0), 0) <= 0
        )

    async def resolve_install(
        self,
        *,
        platform_type: str,
        platform_url: str | None,
        owner_key: str,
        fetch_local_install: Callable[[], Awaitable[InstallInfo]],
    ) -> InstallInfo:
        normalized_url = normalize_market_url(platform_url)
        if not normalized_url:
            install = await fetch_local_install()
            return _attach_market_data_state(install, PUBLIC_STATE_LOCAL_FALLBACK)

        shared_group_id = await self._contracts.group_resolve_by_url(
            platform_type=platform_type,
            normalized_url=normalized_url,
        )
        if shared_group_id is None:
            logger.info(
                "shared_group_miss platform_type=%s normalized_url=%s",
                platform_type,
                normalized_url,
            )
            discovered_group_id, discovered_install = await self.discover_and_bind_uncovered_url(
                platform_type=platform_type,
                platform_url=platform_url,
                sample_raw_url=platform_url,
                account_id=self._extract_account_id(owner_key),
            )
            if discovered_group_id is not None:
                await self._ensure_collector(
                    shared_group_id=discovered_group_id,
                    owner_key=owner_key,
                    fetch_local_install=fetch_local_install,
                )
                if discovered_install is not None:
                    return _attach_market_data_state(discovered_install, MARKET_STATE_SHARED_OK)
                snapshot = await self._contracts.snapshot_get_latest(
                    shared_group_id=discovered_group_id,
                )
                if snapshot is None:
                    return await self._fallback_local(
                        shared_group_id=discovered_group_id,
                        owner_key=owner_key,
                        reason="discovered_no_snapshot",
                        fetch_local_install=fetch_local_install,
                        market_data_state=MARKET_STATE_SHARED_STALE,
                    )

                draw_state = normalize_draw_state(snapshot.draw_state)
                market_data_state = self._effective_snapshot_market_data_state(snapshot)
                if draw_state in {DRAW_STATE_PENDING, DRAW_STATE_WAIT_RETRY}:
                    return self._snapshot_install_with_state(
                        snapshot,
                        market_data_state=market_data_state,
                        draw_state=draw_state,
                    )
                if self._can_use_local_fallback(
                    market_data_state=market_data_state,
                    draw_state=draw_state,
                ):
                    return await self._fallback_local(
                        shared_group_id=discovered_group_id,
                        owner_key=owner_key,
                        reason=f"discovered_{market_data_state}",
                        fetch_local_install=fetch_local_install,
                        market_data_state=market_data_state,
                        draw_state=draw_state,
                        snapshot_fallback=snapshot,
                    )
                return self._snapshot_install_with_state(
                    snapshot,
                    market_data_state=market_data_state,
                    draw_state=draw_state,
                )

            install = await fetch_local_install()
            return _attach_market_data_state(install, PUBLIC_STATE_LOCAL_FALLBACK)

        logger.info(
            "shared_group_hit group_id=%d platform_type=%s",
            shared_group_id,
            platform_type,
        )
        await self._ensure_collector(
            shared_group_id=shared_group_id,
            owner_key=owner_key,
            fetch_local_install=fetch_local_install,
        )

        snapshot = await self._contracts.snapshot_get_latest(shared_group_id=shared_group_id)
        if snapshot is None:
            return await self._fallback_local(
                shared_group_id=shared_group_id,
                owner_key=owner_key,
                reason="missing",
                fetch_local_install=fetch_local_install,
                market_data_state=MARKET_STATE_SHARED_STALE,
            )

        draw_state = normalize_draw_state(snapshot.draw_state)
        market_data_state = self._effective_snapshot_market_data_state(snapshot)
        if draw_state in {DRAW_STATE_PENDING, DRAW_STATE_WAIT_RETRY}:
            return self._snapshot_install_with_state(
                snapshot,
                market_data_state=market_data_state,
                draw_state=draw_state,
            )
        if self._can_use_local_fallback(
            market_data_state=market_data_state,
            draw_state=draw_state,
        ):
            return await self._fallback_local(
                shared_group_id=shared_group_id,
                owner_key=owner_key,
                reason=f"source_{market_data_state}",
                fetch_local_install=fetch_local_install,
                market_data_state=market_data_state,
                draw_state=draw_state,
                snapshot_fallback=snapshot,
            )
        return self._snapshot_install_with_state(
            snapshot,
            market_data_state=market_data_state,
            draw_state=draw_state,
        )

    async def release_owner(self, owner_key: str) -> None:
        if not owner_key:
            return
        await self._stop_collectors(lambda state: state.owner_key == owner_key)

    async def shutdown(self) -> None:
        await self._stop_collectors(lambda _state: True)

    async def _fallback_local(
        self,
        *,
        shared_group_id: int,
        owner_key: str,
        reason: str,
        fetch_local_install: Callable[[], Awaitable[InstallInfo]],
        market_data_state: str = MARKET_STATE_SHARED_ERROR,
        draw_state: str = DRAW_STATE_NORMAL,
        snapshot_fallback: SharedMarketSnapshot | None = None,
    ) -> InstallInfo:
        fallback_key = self._local_fallback_key(
            owner_key=owner_key,
            shared_group_id=shared_group_id,
        )
        now = datetime.now()
        last_fallback_at = self._last_local_fallback_at.get(fallback_key)
        if last_fallback_at is not None:
            elapsed_seconds = (now - last_fallback_at).total_seconds()
            if elapsed_seconds < self._local_fallback_interval_seconds:
                cached_install = self._last_local_fallback_install.get(fallback_key)
                if cached_install is not None:
                    return _attach_market_data_state(
                        cached_install,
                        market_data_state,
                        draw_state=draw_state,
                    )
                if snapshot_fallback is not None:
                    return self._snapshot_install_with_state(
                        snapshot_fallback,
                        market_data_state=market_data_state,
                        draw_state=draw_state,
                    )

        logger.info(
            "shared_snapshot_fallback group_id=%d reason=%s key=%s",
            shared_group_id,
            reason,
            fallback_key,
        )
        install = await fetch_local_install()
        self._last_local_fallback_at[fallback_key] = now
        self._last_local_fallback_install[fallback_key] = install
        return _attach_market_data_state(
            install,
            market_data_state,
            draw_state=draw_state,
        )

    async def _discover_url_group_match(
        self,
        *,
        platform_type: str,
        normalized_url: str,
    ) -> tuple[int | None, InstallInfo | None]:
        if not BOCAI_SHARED_DETECTOR_ENABLED:
            logger.debug("shared_discovery_detector_disabled")
            return None, None

        normalized_platform_type = (platform_type or "JND28WEB").strip().upper()
        groups = await self._contracts.shared_market_group_list(include_disabled=False)
        if not groups:
            return None, None

        detector_account = _safe_stripped_text(BOCAI_SHARED_DETECTOR_ACCOUNT)
        detector_password = _safe_stripped_text(BOCAI_SHARED_DETECTOR_PASSWORD)
        if not detector_account or not detector_password:
            logger.warning("shared_discovery_detector_missing_credentials")
            return None, None

        for row in groups:
            if not isinstance(row, dict):
                continue
            group_id = self._safe_dict_id(
                row,
                keys=("id", "shared_group_id", "group_id"),
            )
            if group_id <= 0:
                continue
            detail = await self._contracts.shared_market_group_get(shared_group_id=group_id)
            if isinstance(detail, dict):
                row = {**row, **detail}

            collector_platform_type = (
                _safe_stripped_text(row.get("collector_platform_type"))
                or normalized_platform_type
            ).upper()
            if collector_platform_type != normalized_platform_type:
                continue

            try:
                install = await self._probe_with_shared_account(
                    platform_type=collector_platform_type,
                    platform_url=normalized_url,
                    account_name=detector_account,
                    account_password=detector_password,
                )
            except Exception as exc:
                logger.debug(
                    "shared_discovery_probe_failed group_id=%d platform=%s err=%s",
                    group_id,
                    collector_platform_type,
                    exc,
                )
                continue
            return group_id, install

        return None, None

    async def _probe_with_shared_account(
        self,
        *,
        platform_type: str,
        platform_url: str,
        account_name: str,
        account_password: str,
    ) -> InstallInfo:
        from app.engine.adapters.factory import create_platform_adapter

        adapter = create_platform_adapter(platform_type, platform_url)
        try:
            await self._ensure_shared_login(
                adapter=adapter,
                account_name=account_name,
                account_password=account_password,
            )
            install = await adapter.get_current_install()
        finally:
            closer = getattr(adapter, "close", None)
            if callable(closer):
                await closer()
        if not self._is_valid_install(install):
            raise RuntimeError("invalid shared install payload")
        return install

    def _is_valid_install(self, install: InstallInfo) -> bool:
        issue = _safe_text(install.issue).strip()
        if not issue:
            return False
        if install.close_countdown_sec is None or install.open_countdown_sec is None:
            return False
        if not _safe_text(install.pre_result).strip():
            return False
        return True

    async def _ensure_shared_login(
        self,
        *,
        adapter: Any,
        account_name: str,
        account_password: str,
    ) -> None:
        login_method = getattr(adapter, "login", None)
        if not callable(login_method):
            return

        login_result = await _login_without_captcha(login_method, account_name, account_password)
        if not getattr(login_result, "success", True):
            if not _should_retry_captcha_login(login_result):
                msg = _safe_text(getattr(login_result, "message", ""))
                raise RuntimeError(f"shared login failed: {msg}")

            get_captcha = getattr(adapter, "get_captcha", None)
            if not callable(get_captcha):
                msg = _safe_text(getattr(login_result, "message", ""))
                raise RuntimeError(f"shared login failed: {msg}")

            captcha_service = get_shared_captcha_service()
            last_result = login_result
            for attempt in range(DEFAULT_SHARED_CAPTCHA_LOGIN_ATTEMPTS):
                try:
                    captcha_image = await get_captcha()
                    captcha_code = (await captcha_service.recognize(captcha_image)).strip()
                except Exception as exc:
                    raise RuntimeError(
                        f"shared login failed: {exc}"
                    ) from exc
                if not captcha_code:
                    raise RuntimeError("shared login failed: OCR returned empty captcha result")

                logger.info(
                    "shared platform login using captcha attempt=%d account=%s",
                    attempt + 1,
                    account_name,
                )
                last_result = await _login_with_captcha(
                    login_method=login_method,
                    account_name=account_name,
                    account_password=account_password,
                    captcha_code=captcha_code,
                )
                if getattr(last_result, "success", True):
                    return
                if not _should_retry_captcha_login(last_result):
                    break

            msg = _safe_text(getattr(last_result, "message", ""))
            raise RuntimeError(f"shared login failed: {msg}")


    async def _ensure_collector(
        self,
        *,
        shared_group_id: int,
        owner_key: str,
        fetch_local_install: Callable[[], Awaitable[InstallInfo]],
        cleanup: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        async with self._lock:
            existing = self._collectors.get(shared_group_id)
            if existing and not existing.task.done():
                return

            stop_event = asyncio.Event()
            task = asyncio.create_task(
                self._collector_loop(
                    shared_group_id=shared_group_id,
                    owner_key=owner_key,
                    stop_event=stop_event,
                    fetch_local_install=fetch_local_install,
                ),
                name=f"shared-market-collector-{shared_group_id}",
            )
            self._collectors[shared_group_id] = _CollectorState(
                shared_group_id=shared_group_id,
                owner_key=owner_key,
                stop_event=stop_event,
                task=task,
                cleanup=cleanup,
            )

    async def _stop_collectors(
        self,
        predicate: Callable[[_CollectorState], bool],
    ) -> None:
        targets: list[_CollectorState] = []
        async with self._lock:
            for group_id, state in list(self._collectors.items()):
                if not predicate(state):
                    continue
                state.stop_event.set()
                self._collectors.pop(group_id, None)
                targets.append(state)

        for state in targets:
            state.task.cancel()
            try:
                await state.task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception(
                    "shared_collector_stop_error group_id=%d owner=%s",
                    state.shared_group_id,
                    state.owner_key,
                )
            cleanup = state.cleanup
            if cleanup is not None:
                try:
                    await cleanup()
                except Exception:
                    logger.exception(
                        "shared_collector_cleanup_error group_id=%d owner=%s",
                        state.shared_group_id,
                        state.owner_key,
                    )

    async def _collector_loop(
        self,
        *,
        shared_group_id: int,
        owner_key: str,
        stop_event: asyncio.Event,
        fetch_local_install: Callable[[], Awaitable[InstallInfo]],
    ) -> None:
        last_install: Optional[InstallInfo] = None
        draw_refresh = _DrawRefreshState()
        while not stop_event.is_set():
            sleep_seconds = self._collector_interval_seconds
            try:
                install = await fetch_local_install()
                last_install = install
                now = datetime.now()
                market_data_state = MARKET_STATE_SHARED_OK
                draw_state = DRAW_STATE_NORMAL
                next_normal_refresh_at: datetime | None = None
                next_draw_retry_at: datetime | None = None

                if self._is_market_closed_install(install):
                    draw_refresh.reset()
                    market_data_state = MARKET_STATE_MARKET_CLOSED
                    sleep_seconds = self._collector_interval_seconds
                    next_normal_refresh_at = now + timedelta(seconds=sleep_seconds)
                else:
                    open_countdown_sec = _safe_int(getattr(install, "open_countdown_sec", 0), 0)
                    if not draw_refresh.active and open_countdown_sec <= 0:
                        draw_refresh.start(install)

                    if draw_refresh.active:
                        if draw_refresh.has_new_draw_data(install):
                            draw_refresh.reset()
                            sleep_seconds = self._collector_interval_seconds
                            next_normal_refresh_at = now + timedelta(seconds=sleep_seconds)
                        elif draw_refresh.retry_index >= len(DRAW_PENDING_RETRY_INTERVALS_SECONDS):
                            draw_state = DRAW_STATE_WAIT_RETRY
                            sleep_seconds = DRAW_WAIT_RETRY_INTERVAL_SECONDS
                            next_draw_retry_at = now + timedelta(seconds=sleep_seconds)
                        else:
                            draw_state = DRAW_STATE_PENDING
                            sleep_seconds = float(
                                DRAW_PENDING_RETRY_INTERVALS_SECONDS[draw_refresh.retry_index]
                            )
                            draw_refresh.retry_index += 1
                            next_draw_retry_at = now + timedelta(seconds=sleep_seconds)
                    else:
                        sleep_seconds = _next_normal_collector_sleep_seconds(
                            open_countdown_sec=open_countdown_sec,
                            collector_interval_seconds=self._collector_interval_seconds,
                        )
                        next_normal_refresh_at = now + timedelta(seconds=sleep_seconds)

                await self._contracts.snapshot_upsert(
                    shared_group_id=shared_group_id,
                    install=install,
                    source_status="ok",
                    market_data_state=market_data_state,
                    draw_state=draw_state,
                    next_normal_refresh_at=_format_refresh_at(next_normal_refresh_at),
                    next_draw_retry_at=_format_refresh_at(next_draw_retry_at),
                )
                self._shared_error_alerted_group_ids.discard(shared_group_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error_text = _safe_text(exc)[:500] or type(exc).__name__
                logger.exception(
                    "shared_collector_error group_id=%d owner=%s",
                    shared_group_id,
                    owner_key,
                )
                draw_refresh.reset()
                await self._contracts.snapshot_mark_error(
                    shared_group_id=shared_group_id,
                    install=last_install,
                    last_error=error_text,
                )
                await self._notify_admin_shared_error(
                    shared_group_id=shared_group_id,
                    owner_key=owner_key,
                    error_text=error_text,
                )
                sleep_seconds = self._collector_error_backoff_seconds

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=sleep_seconds)
            except asyncio.TimeoutError:
                continue

    async def _notify_admin_shared_error(
        self,
        *,
        shared_group_id: int,
        owner_key: str,
        error_text: str,
    ) -> None:
        if shared_group_id in self._shared_error_alerted_group_ids:
            return
        self._shared_error_alerted_group_ids.add(shared_group_id)

        logger.warning(
            "shared_collector_admin_alert_triggered group_id=%d owner=%s error=%s",
            shared_group_id,
            owner_key,
            error_text,
        )
        try:
            admin_ids = await self._contracts.active_admin_operator_ids()
            for operator_id in admin_ids:
                await self._contracts.admin_alert_create(
                    operator_id=operator_id,
                    title=SHARED_ERROR_MESSAGE_TEXT,
                    detail="",
                )
        except Exception:
            logger.exception(
                "shared_collector_admin_alert_failed group_id=%d owner=%s",
                shared_group_id,
                owner_key,
            )

    def _build_group_fetcher(
        self,
        *,
        shared_group_id: int,
        shared_group: dict[str, Any],
    ) -> tuple[
        Callable[[], Awaitable[InstallInfo]] | None,
        str,
        Callable[[], Awaitable[None]] | None,
    ]:
        platform_type = _safe_stripped_text(shared_group.get("collector_platform_type")) or "JND28WEB"
        platform_url = _safe_stripped_text(
            shared_group.get("primary_url") or shared_group.get("normalized_url")
        )
        account_name = _safe_stripped_text(shared_group.get("collector_account_name"))
        account_password = _safe_stripped_text(shared_group.get("collector_password_enc"))
        owner_key = _safe_stripped_text(shared_group.get("owner_key")) or f"shared-group-{shared_group_id}"

        from app.engine.adapters.factory import create_platform_adapter

        adapter = create_platform_adapter(platform_type, platform_url or None)
        logged_in = False
        if not account_name or not account_password:
            logged_in = True

        async def fetcher() -> InstallInfo:
            nonlocal logged_in
            if not logged_in:
                await self._ensure_shared_login(
                    adapter=adapter,
                    account_name=account_name,
                    account_password=account_password,
                )
                logged_in = True
            try:
                return await adapter.get_current_install()
            except Exception as exc:
                if not account_name or not account_password or not _requires_shared_relogin(exc):
                    raise
                logger.warning(
                    "shared_collector_relogin group_id=%d owner=%s reason=%s",
                    shared_group_id,
                    owner_key,
                    type(exc).__name__,
                )
                logged_in = False
                await self._ensure_shared_login(
                    adapter=adapter,
                    account_name=account_name,
                    account_password=account_password,
                )
                logged_in = True
                return await adapter.get_current_install()

        async def cleanup() -> None:
            closer = getattr(adapter, "close", None)
            if callable(closer):
                await closer()

        return fetcher, owner_key, cleanup

    def _safe_dict_id(self, value: Any, keys: tuple[str, ...]) -> int:
        if isinstance(value, dict):
            for key in keys:
                parsed = _safe_int(value.get(key), 0)
                if parsed > 0:
                    return parsed
        elif isinstance(value, int):
            return value
        return 0

    def _extract_account_id(self, owner_key: str) -> int | None:
        prefix = _safe_stripped_text(owner_key).split(":", 1)[0]
        if not prefix or not prefix.isdigit():
            return None
        return _safe_int(prefix)
