"""
Account binding and account-level validation endpoints.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import get_current_operator, get_db_conn
from app.engine.adapters.base import LoginResult, PlatformAdapter
from app.engine.adapters.factory import create_platform_adapter
from app.engine.session_runtime import AccountSessionRuntime
from app.models.db_ops import (
    account_create,
    account_delete,
    account_get_by_id,
    account_list_by_operator,
    account_platform_session_delete,
    account_platform_session_list,
    account_platform_session_upsert,
    account_platform_capability_list_by_run,
    account_verification_run_complete,
    account_verification_run_create,
    account_verification_run_fail,
    account_verification_run_get_effective,
    account_verification_run_get_latest,
    account_verification_run_get_latest_completed,
    account_verification_run_get_running,
    account_verification_runs_mark_stale_by_account,
    account_verification_runs_refresh_stale,
    account_shared_route_get,
    account_shared_route_mark_pending,
    account_shared_route_upsert,
    account_update,
    alert_create,
    odds_batch_upsert,
    odds_list_by_account,
    operator_list_all,
    operator_strategy_permission_list,
    shared_market_group_resolve_by_url,
)
from app.schemas.account import (
    AccountCreate,
    AccountInfo,
    KillSwitchUpdate,
    get_allowed_platform_types,
    mask_password,
)
from app.schemas.common import ApiResponse
from app.utils.captcha import CaptchaError, CaptchaService, get_shared_captcha_service
from app.utils.omission_random import (
    is_random_pick_type,
    key_codes_for_category,
    normalize_categories,
    parse_omission_play_code,
)
from app.utils.platform_url import normalize_platform_url
from app.utils.response import BizError

router = APIRouter()
logger = logging.getLogger(__name__)
_BJT = timezone(timedelta(hours=8))
CAPTCHA_LOGIN_ATTEMPTS = 3
VERIFICATION_TTL_MINUTES = 30
VERIFICATION_TIMEOUT_SECONDS = 90

FRONTEND_SIGNAL_NORMAL = "normal"
FRONTEND_SIGNAL_PROCESSING = "processing"
FRONTEND_SIGNAL_NEED_RELOGIN = "need_relogin"
FRONTEND_SIGNAL_NEED_CONFIRM_ODDS = "need_confirm_odds"
_PROCESSING_SESSION_STATUSES = {"login_failed", "reconnecting"}
_RELOGIN_SESSION_STATUSES = {"login_error"}
_RELOGIN_REASON_MARKERS = (
    "bad credential",
    "invalid credential",
    "wrong password",
    "password error",
    "password incorrect",
    "password mismatch",
    "密码错误",
    "账号异常",
    "重新登录",
    "重新登入",
    "remote login",
)

# Keep an ASCII-only marker set for stable matching across codepage environments.
_RELOGIN_REASON_MARKERS = (
    "bad credential",
    "invalid credential",
    "wrong password",
    "password error",
    "password incorrect",
    "password mismatch",
    "invalid account",
    "account abnormal",
    "relogin",
    "re-login",
    "remote login",
)


@dataclass(frozen=True)
class PlatformCapabilityProbe:
    platform_type: str
    verify_status: str
    market_state: str
    detected_issue: str | None
    odds_synced: bool
    odds_count: int
    odds_message: str
    last_error: str | None
    last_verified_at: str


def _password_hash(raw: str) -> str:
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()


def _safe_text(value: object) -> str:
    return "" if value is None else str(value)


def _discovered_shared_group_id(result: object) -> int | None:
    candidate = result[0] if isinstance(result, (tuple, list)) and result else result
    if candidate in (None, ""):
        return None
    try:
        return int(candidate)
    except (TypeError, ValueError):
        return None


def _now_bjt() -> str:
    return datetime.now(_BJT).strftime("%Y-%m-%d %H:%M:%S")


_DETECTION_STATUS_ALIASES = {
    "pending": "untested",
    "matched": "success",
    "review_required": "failed",
}


def _normalize_platform_type(value: object) -> str:
    return _safe_text(value or "JND28WEB").strip().upper() or "JND28WEB"


def _normalize_probe_platform_type(value: object) -> str:
    if isinstance(value, (list, tuple, set)):
        value = next(iter(value), "JND28WEB")
    return _normalize_platform_type(value)


def _normalize_detection_status(value: object) -> str:
    raw = _safe_text(value).strip().lower()
    return _DETECTION_STATUS_ALIASES.get(raw, raw)


def _candidate_shared_platform_types(account: dict[str, Any]) -> list[str]:
    platform_types = get_allowed_platform_types(_safe_text(account.get("game_type")))
    return platform_types or ["JND28WEB"]


def _set_adapter_platform_type(adapter: PlatformAdapter, platform_type: str) -> None:
    if hasattr(adapter, "lottery_type"):
        setattr(adapter, "lottery_type", platform_type)
    elif hasattr(adapter, "platform_type"):
        setattr(adapter, "platform_type", platform_type)


def _state_to_market_state(state: int) -> str:
    return {1: "open", 2: "closed", 3: "waiting"}.get(state, "unknown")


def _classify_probe_failure_message(raw_message: str) -> str:
    normalized = (raw_message or "").lower()
    if "unsupported" in normalized or "not implemented" in normalized:
        return "unsupported"
    return "probe_failed"


def _build_summary_status_reason(
    capabilities: list[dict[str, Any]],
) -> str | None:
    if not capabilities:
        return "not_verified"

    supported_count = sum(1 for item in capabilities if item.get("verify_status") == "supported")
    unsupported_count = sum(1 for item in capabilities if item.get("verify_status") == "unsupported")
    probe_failed_count = sum(1 for item in capabilities if item.get("verify_status") == "probe_failed")

    if supported_count >= 1 and probe_failed_count == 0:
        return None
    if supported_count >= 1 and probe_failed_count >= 1:
        return "probe_partial_failure"
    if supported_count == 0 and unsupported_count >= 1 and probe_failed_count == 0:
        return "unsupported_only"
    if supported_count == 0 and probe_failed_count >= 1 and unsupported_count == 0:
        return "probe_failed_only"
    if supported_count == 0 and unsupported_count >= 1 and probe_failed_count >= 1:
        return "unsupported_with_probe_failed"
    return "not_verified"


def _requires_manual_relogin_by_reason(reason: str | None) -> bool:
    normalized = (reason or "").strip().lower()
    if not normalized:
        return False
    return any(marker in normalized for marker in _RELOGIN_REASON_MARKERS)


def _resolve_frontend_signal(
    *,
    verification_view: dict[str, Any],
    has_unconfirmed_odds: bool,
) -> tuple[str, str | None]:
    session_statuses = {
        str(status or "").strip().lower()
        for status in verification_view.get("platform_session_statuses") or []
        if str(status or "").strip()
    }

    latest_run_status = str(verification_view.get("latest_run_status") or "").strip().lower()
    latest_run_stale_reason = str(verification_view.get("latest_run_stale_reason") or "").strip()
    latest_completed_stale_reason = str(
        verification_view.get("latest_completed_stale_reason") or ""
    ).strip().lower()

    if session_statuses & _RELOGIN_SESSION_STATUSES:
        return FRONTEND_SIGNAL_NEED_RELOGIN, "session_login_error"
    if latest_completed_stale_reason == "manual_logout":
        return FRONTEND_SIGNAL_NEED_RELOGIN, "manual_logout"
    if latest_run_status == "failed" and _requires_manual_relogin_by_reason(latest_run_stale_reason):
        return FRONTEND_SIGNAL_NEED_RELOGIN, "verification_login_failed"
    if has_unconfirmed_odds:
        return FRONTEND_SIGNAL_NEED_CONFIRM_ODDS, "odds_unconfirmed"
    if verification_view.get("verification_in_progress"):
        return FRONTEND_SIGNAL_PROCESSING, "verification_in_progress"
    if session_statuses & _PROCESSING_SESSION_STATUSES:
        return FRONTEND_SIGNAL_PROCESSING, "session_reconnecting"
    return FRONTEND_SIGNAL_NORMAL, None


async def _has_unconfirmed_odds(db, *, account_id: int) -> bool:
    row = await (
        await db.execute(
            "SELECT 1 FROM account_odds "
            "WHERE account_id=? AND confirmed=0 AND COALESCE(odds_value, 0) <= 0 LIMIT 1",
            (account_id,),
        )
    ).fetchone()
    return row is not None


def _normal_positive_odds(raw_odds: dict[str, Any]) -> dict[str, int]:
    normal: dict[str, int] = {}
    for key, value in (raw_odds or {}).items():
        try:
            odds_value = int(value)
        except (TypeError, ValueError):
            continue
        if odds_value > 0:
            normal[str(key)] = odds_value
    return normal


def _split_play_code_tokens(play_code: str | None) -> set[str]:
    return {
        token.strip().upper()
        for token in str(play_code or "").split(",")
        if token.strip()
    }


def _random_pick_categories_from_strategy(strategy: dict[str, Any]) -> list[str]:
    raw_config = strategy.get("strategy_config")
    if isinstance(raw_config, str) and raw_config.strip():
        try:
            parsed = json.loads(raw_config)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict) and parsed.get("categories") is not None:
            try:
                return normalize_categories(parsed.get("categories"))
            except ValueError:
                pass

    try:
        return parse_omission_play_code(str(strategy.get("play_code") or ""))
    except ValueError:
        return []


def _strategy_odds_key_scope(strategy: dict[str, Any]) -> tuple[set[str], bool]:
    if is_random_pick_type(strategy.get("type")):
        watched: set[str] = set()
        for category in _random_pick_categories_from_strategy(strategy):
            watched.update(key_codes_for_category(category))
        return watched, False

    play_tokens = _split_play_code_tokens(strategy.get("play_code"))
    if play_tokens:
        return play_tokens, False
    return set(), True


async def _running_strategy_odds_scope(
    db,
    *,
    operator_id: int,
    account_id: int,
    platform_type: str,
) -> tuple[set[str], bool, bool]:
    rows = await (
        await db.execute(
            """SELECT id, type, play_code, strategy_config
                 FROM strategies
                WHERE operator_id=?
                  AND account_id=?
                  AND UPPER(platform_type)=?
                  AND status='running'
                  AND deleted_at IS NULL""",
            (operator_id, account_id, platform_type.strip().upper()),
        )
    ).fetchall()
    if not rows:
        return set(), False, False

    watched: set[str] = set()
    has_unbounded_strategy = False
    for row in rows:
        scoped_keys, unbounded = _strategy_odds_key_scope(dict(row))
        watched.update(scoped_keys)
        has_unbounded_strategy = has_unbounded_strategy or unbounded
    return watched, has_unbounded_strategy, True


async def _collect_account_verification_view(
    db,
    row: dict,
) -> dict[str, Any]:
    await account_verification_runs_refresh_stale(
        db,
        account_id=row["id"],
        current_game_type=row["game_type"],
        current_platform_url=row.get("platform_url"),
        current_password=row["password"],
        verification_ttl_minutes=VERIFICATION_TTL_MINUTES,
    )

    latest_run = await account_verification_run_get_latest(db, account_id=row["id"])
    effective_run = await account_verification_run_get_effective(db, account_id=row["id"])
    running_run = await account_verification_run_get_running(db, account_id=row["id"])
    latest_completed_run = await account_verification_run_get_latest_completed(
        db, account_id=row["id"]
    )
    platform_sessions = await account_platform_session_list(db, account_id=row["id"])

    capabilities: list[dict[str, Any]] = []
    if effective_run:
        raw_capabilities = await account_platform_capability_list_by_run(
            db,
            verification_run_id=effective_run["id"],
        )
        capabilities = [
            {
                "platform_type": item.get("platform_type"),
                "verify_status": item.get("verify_status"),
                "market_state": item.get("market_state"),
                "detected_issue": item.get("detected_issue"),
                "odds_synced": bool(item.get("odds_synced")),
                "odds_message": item.get("odds_message"),
                "last_verified_at": item.get("last_verified_at"),
            }
            for item in raw_capabilities
        ]

    allowed_strategy_platform_types = [
        item["platform_type"]
        for item in capabilities
        if item.get("verify_status") == "supported"
    ]
    verification_stale = bool(
        latest_completed_run and int(latest_completed_run.get("stale") or 0) == 1
    )

    summary_status_reason = _build_summary_status_reason(capabilities)

    return {
        "latest_verification_run_id": latest_run["id"] if latest_run else None,
        "effective_verification_run_id": effective_run["id"] if effective_run else None,
        "verification_in_progress": running_run is not None,
        "verification_stale": verification_stale,
        "allowed_strategy_platform_types": allowed_strategy_platform_types,
        "platform_capabilities": capabilities,
        "summary_status_reason": summary_status_reason,
        "latest_run_status": latest_run.get("run_status") if latest_run else None,
        "latest_run_stale_reason": latest_run.get("stale_reason") if latest_run else None,
        "latest_completed_stale_reason": (
            latest_completed_run.get("stale_reason") if latest_completed_run else None
        ),
        "platform_session_statuses": [
            str(session.get("status") or "").strip().lower()
            for session in platform_sessions
            if str(session.get("status") or "").strip()
        ],
    }


def _to_account_info(
    row: dict,
    *,
    allowed_strategy_platform_types: list[str] | None = None,
    allowed_strategy_types: list[str] | None = None,
    platform_capabilities: list[dict[str, Any]] | None = None,
    latest_verification_run_id: int | None = None,
    effective_verification_run_id: int | None = None,
    verification_in_progress: bool = False,
    verification_stale: bool = False,
    summary_status_reason: str | None = None,
    odds_synced: bool | None = None,
    odds_count: int | None = None,
    odds_message: str | None = None,
    frontend_signal: str = FRONTEND_SIGNAL_NORMAL,
    frontend_signal_reason: str | None = None,
) -> AccountInfo:
    return AccountInfo(
        id=row["id"],
        account_name=row["account_name"],
        password_masked=mask_password(row["password"]),
        game_type=row["game_type"],
        allowed_strategy_platform_types=allowed_strategy_platform_types or [],
        allowed_strategy_types=allowed_strategy_types or [],
        platform_capabilities=platform_capabilities or [],
        latest_verification_run_id=latest_verification_run_id,
        effective_verification_run_id=effective_verification_run_id,
        verification_in_progress=verification_in_progress,
        verification_stale=verification_stale,
        summary_status_reason=summary_status_reason,
        frontend_signal=frontend_signal,
        frontend_signal_reason=frontend_signal_reason,
        platform_url=row.get("platform_url"),
        status=row["status"],
        balance=row["balance"] / 100,
        kill_switch=bool(row["kill_switch"]),
        last_login_at=row.get("last_login_at"),
        odds_synced=odds_synced,
        odds_count=odds_count,
        odds_message=odds_message,
    )


def _should_retry_captcha_login(result: LoginResult) -> bool:
    if result.success:
        return False
    message = (result.message or "").lower()
    retry_markers = ("captcha", "verify", "vcode", "token")
    return result.captcha_required or any(marker in message for marker in retry_markers) or not message


async def _login_platform_account(
    adapter: PlatformAdapter,
    account_name: str,
    password: str,
    *,
    captcha_service: CaptchaService | None = None,
    max_captcha_attempts: int = CAPTCHA_LOGIN_ATTEMPTS,
) -> LoginResult:
    result = await adapter.login(account_name, password)
    if result.success:
        return result

    get_captcha = getattr(adapter, "get_captcha", None)
    if not callable(get_captcha):
        return result

    captcha_service = captcha_service or get_shared_captcha_service()
    last_result = result
    for attempt in range(max_captcha_attempts):
        captcha_image = await get_captcha()
        captcha_code = (await captcha_service.recognize(captcha_image)).strip()
        if not captcha_code:
            raise CaptchaError("OCR returned empty captcha result")

        logger.info(
            "platform login using captcha attempt=%d account=%s",
            attempt + 1,
            account_name,
        )
        last_result = await adapter.login(
            account_name,
            password,
            captcha_code=captcha_code,
        )
        if last_result.success:
            return last_result
        if not _should_retry_captcha_login(last_result):
            break

    return last_result


async def _sync_odds(
    db,
    account_id: int,
    operator_id: int,
    platform_type: str,
    new_odds: dict[str, int],
) -> None:
    normal_odds = _normal_positive_odds(new_odds)
    if not normal_odds:
        return

    existing = await odds_list_by_account(
        db,
        account_id=account_id,
        platform_type=platform_type,
    )
    if not existing:
        await odds_batch_upsert(
            db,
            account_id=account_id,
            platform_type=platform_type,
            odds_map=normal_odds,
            confirmed=True,
        )
        return

    old_map = {row["key_code"]: row["odds_value"] for row in existing}
    if old_map == normal_odds:
        await odds_batch_upsert(
            db,
            account_id=account_id,
            platform_type=platform_type,
            odds_map=normal_odds,
            confirmed=True,
        )
        return

    await odds_batch_upsert(
        db,
        account_id=account_id,
        platform_type=platform_type,
        odds_map=normal_odds,
        confirmed=True,
    )

    changes: list[str] = []
    changed_by_key: dict[str, str] = {}
    for key in sorted(set(old_map) | set(normal_odds)):
        old_val = old_map.get(key)
        new_val = normal_odds.get(key)
        if old_val != new_val:
            line = f"{key}: {old_val} -> {new_val}"
            changes.append(line)
            changed_by_key[str(key).upper()] = line

    watched_keys, has_unbounded_strategy, has_running_strategy = await _running_strategy_odds_scope(
        db,
        operator_id=operator_id,
        account_id=account_id,
        platform_type=platform_type,
    )
    if not has_running_strategy:
        return

    if not has_unbounded_strategy:
        watched_upper = {key.upper() for key in watched_keys}
        changes = [
            line
            for key, line in changed_by_key.items()
            if key in watched_upper
        ]
        if not changes:
            return

    await alert_create(
        db,
        operator_id=operator_id,
        type="odds_changed",
        level="warning",
        title=f"赔率变动：账号 {account_id}，平台 {platform_type}",
        detail="检测到运行中策略使用的平台赔率发生变化：\n" + "\n".join(changes),
    )


async def _probe_platform_capability(
    *,
    adapter: PlatformAdapter,
    db,
    account: dict,
    operator_id: int,
    platform_type: str,
) -> PlatformCapabilityProbe:
    last_verified_at = _now_bjt()
    _set_adapter_platform_type(adapter, platform_type)

    try:
        install = await adapter.get_current_install()
    except Exception as exc:
        reason = str(exc) or "probe failed"
        verify_status = _classify_probe_failure_message(reason)
        return PlatformCapabilityProbe(
            platform_type=platform_type,
            verify_status=verify_status,
            market_state="unknown",
            detected_issue=reason,
            odds_synced=False,
            odds_count=0,
            odds_message=f"probe failed: {reason}",
            last_error=reason,
            last_verified_at=last_verified_at,
        )

    market_state = _state_to_market_state(install.state)
    if market_state != "open":
        return PlatformCapabilityProbe(
            platform_type=platform_type,
            verify_status="supported",
            market_state=market_state,
            detected_issue=None,
            odds_synced=False,
            odds_count=0,
            odds_message=f"market state is {market_state}, odds not synced",
            last_error=None,
            last_verified_at=last_verified_at,
        )

    try:
        raw_odds = await adapter.load_odds(install.issue)
    except Exception as exc:
        reason = str(exc) or "odds fetch failed"
        return PlatformCapabilityProbe(
            platform_type=platform_type,
            verify_status="supported",
            market_state=market_state,
            detected_issue=None,
            odds_synced=False,
            odds_count=0,
            odds_message=f"market open, but odds fetch failed: {reason}",
            last_error=reason,
            last_verified_at=last_verified_at,
        )

    non_zero_odds = _normal_positive_odds(raw_odds)
    if not non_zero_odds:
        return PlatformCapabilityProbe(
            platform_type=platform_type,
            verify_status="supported",
            market_state=market_state,
            detected_issue=None,
            odds_synced=False,
            odds_count=0,
            odds_message="market open, but platform returned empty odds",
            last_error=None,
            last_verified_at=last_verified_at,
        )

    try:
        await _sync_odds(
            db,
            account_id=account["id"],
            operator_id=operator_id,
            platform_type=platform_type,
            new_odds=non_zero_odds,
        )
    except Exception as exc:
        reason = str(exc) or "odds save failed"
        logger.error(
            "persist odds failed account_id=%d platform_type=%s: %s",
            account["id"],
            platform_type,
            reason,
        )
        return PlatformCapabilityProbe(
            platform_type=platform_type,
            verify_status="supported",
            market_state=market_state,
            detected_issue=None,
            odds_synced=False,
            odds_count=len(non_zero_odds),
            odds_message=f"market open, but odds save failed: {reason}",
            last_error=reason,
            last_verified_at=last_verified_at,
        )

    return PlatformCapabilityProbe(
        platform_type=platform_type,
        verify_status="supported",
        market_state=market_state,
        detected_issue=None,
        odds_synced=True,
        odds_count=len(non_zero_odds),
        odds_message=f"odds synced: {len(non_zero_odds)} items",
        last_error=None,
        last_verified_at=last_verified_at,
    )


async def _build_account_info(
    *,
    db,
    row: dict,
    odds_synced: bool | None = None,
    odds_count: int | None = None,
    odds_message: str | None = None,
) -> AccountInfo:
    verification_view = await _collect_account_verification_view(db, row)
    allowed_strategy_types = await operator_strategy_permission_list(
        db,
        operator_id=row["operator_id"],
    )
    has_unconfirmed_odds = await _has_unconfirmed_odds(db, account_id=row["id"])
    frontend_signal, frontend_signal_reason = _resolve_frontend_signal(
        verification_view=verification_view,
        has_unconfirmed_odds=has_unconfirmed_odds,
    )
    return _to_account_info(
        row,
        allowed_strategy_platform_types=verification_view["allowed_strategy_platform_types"],
        allowed_strategy_types=allowed_strategy_types,
        platform_capabilities=verification_view["platform_capabilities"],
        latest_verification_run_id=verification_view["latest_verification_run_id"],
        effective_verification_run_id=verification_view["effective_verification_run_id"],
        verification_in_progress=verification_view["verification_in_progress"],
        verification_stale=verification_view["verification_stale"],
        summary_status_reason=verification_view["summary_status_reason"],
        odds_synced=odds_synced,
        odds_count=odds_count,
        odds_message=odds_message,
        frontend_signal=frontend_signal,
        frontend_signal_reason=frontend_signal_reason,
    )


async def _uncovered_url_context(db, *, normalized_url: str) -> dict[str, Any] | None:
    record = await (
        await db.execute(
            """SELECT detection_status, failure_reason, detection_error,
                      matched_shared_group_id, shared_group_id
                 FROM shared_market_uncovered_urls
                WHERE normalized_url=?""",
            (normalized_url,),
        )
    ).fetchone()
    return dict(record) if record else None


def _shared_market_route_payload(
    route: dict[str, Any] | None,
    *,
    platform_type: str,
    normalized_url: str | None,
    uncovered_url: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = (route or {}).get("data_source_state") or "local"
    detection_status = _normalize_detection_status(
        (uncovered_url or {}).get("detection_status")
    )
    if not detection_status:
        detection_status = "success" if state in {"shared", "shared_pending"} else "untested"

    return {
        "platform_type": platform_type,
        "normalized_url": normalized_url,
        "data_source_state": state,
        "shared_group_id": (route or {}).get("shared_group_id"),
        "shared_group_key": (route or {}).get("shared_group_key"),
        "pending_shared_group_id": (route or {}).get("pending_shared_group_id"),
        "pending_shared_group_key": (route or {}).get("pending_shared_group_key"),
        "handoff_after_issue": (route or {}).get("handoff_after_issue"),
        "handoff_confirmed_issue": (route or {}).get("handoff_confirmed_issue"),
        "fallback_reason": (route or {}).get("fallback_reason"),
        "last_switch_at": (route or {}).get("last_switch_at"),
        "last_checked_at": (route or {}).get("last_checked_at"),
        "detection_status": detection_status,
        "failure_reason": (uncovered_url or {}).get("failure_reason")
        or (uncovered_url or {}).get("detection_error")
        or (route or {}).get("fallback_reason"),
    }


async def _build_shared_market_route_payload(
    db,
    *,
    row: dict,
    platform_type: str | None = None,
) -> dict[str, Any]:
    resolved_platform_type = _normalize_platform_type(
        platform_type or _candidate_shared_platform_types(row)[0]
    )
    normalized_url = normalize_platform_url(row.get("platform_url"))
    route = await account_shared_route_get(
        db,
        account_id=int(row["id"]),
        platform_type=resolved_platform_type,
    )
    uncovered_url = (
        await _uncovered_url_context(db, normalized_url=normalized_url)
        if normalized_url
        else None
    )
    return _shared_market_route_payload(
        route,
        platform_type=resolved_platform_type,
        normalized_url=normalized_url or None,
        uncovered_url=uncovered_url,
    )


async def _build_account_payload(
    *,
    db,
    row: dict,
    odds_synced: bool | None = None,
    odds_count: int | None = None,
    odds_message: str | None = None,
) -> dict[str, Any]:
    info = await _build_account_info(
        db=db,
        row=row,
        odds_synced=odds_synced,
        odds_count=odds_count,
        odds_message=odds_message,
    )
    payload = info.model_dump()
    payload["shared_market_route"] = await _build_shared_market_route_payload(
        db,
        row=row,
    )
    return payload


async def _resolve_shared_group_for_route(
    db,
    *,
    normalized_url: str,
    platform_type: str,
) -> dict[str, Any] | None:
    group = await shared_market_group_resolve_by_url(
        db,
        normalized_url=normalized_url,
        only_enabled=True,
    )
    if group is None:
        return None
    collector_platform_type = _normalize_platform_type(
        group.get("collector_platform_type") or platform_type
    )
    if collector_platform_type != _normalize_platform_type(platform_type):
        return None
    return group


async def _notify_admins_shared_detection_failed(
    db,
    *,
    account: dict,
    normalized_url: str,
    platform_type: str,
    failure_reason: str,
) -> None:
    detail = json.dumps(
        {
            "shared_group_id": None,
            "collector_account_name": None,
            "error_class": "url_detection_failed",
            "error_text": failure_reason,
            "consecutive_error_count": 1,
            "last_success_at": None,
            "dedupe_key": f"shared_url_detection:{platform_type}:{normalized_url}",
            "normalized_url": normalized_url,
            "platform_type": platform_type,
            "account_id": account.get("id"),
            "account_name": account.get("account_name"),
        },
        sort_keys=True,
    )
    admins = await operator_list_all(db)
    for admin in admins:
        if admin.get("role") != "admin" or admin.get("status") != "active":
            continue
        await alert_create(
            db,
            operator_id=int(admin["id"]),
            type="shared_market_error",
            level="warning",
            title="shared url detection failed",
            detail=detail,
        )


async def _sync_shared_market_routes_on_bind(db, *, account: dict) -> None:
    normalized_url = normalize_platform_url(account.get("platform_url"))
    if not normalized_url:
        return

    for platform_type in _candidate_shared_platform_types(account):
        normalized_platform_type = _normalize_platform_type(platform_type)
        group = await _resolve_shared_group_for_route(
            db,
            normalized_url=normalized_url,
            platform_type=normalized_platform_type,
        )
        if group is None:
            await account_shared_route_upsert(
                db,
                account_id=int(account["id"]),
                operator_id=int(account["operator_id"]),
                platform_type=normalized_platform_type,
                normalized_url=normalized_url,
                data_source_state="local",
            )
            continue

        await account_shared_route_upsert(
            db,
            account_id=int(account["id"]),
            operator_id=int(account["operator_id"]),
            platform_type=normalized_platform_type,
            normalized_url=normalized_url,
            data_source_state="shared",
            shared_group_id=int(group["shared_group_id"]),
        )


@router.get("/accounts")
async def list_accounts(
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    rows = await account_list_by_operator(db, operator_id=operator["id"])
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append(await _build_account_payload(db=db, row=row))
    return ApiResponse(data=result)


@router.post("/accounts")
async def bind_account(
    body: AccountCreate,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    existing = await account_list_by_operator(db, operator_id=operator["id"])
    if len(existing) >= operator["max_accounts"]:
        raise BizError(4002, "max account limit reached", status_code=409)

    try:
        row = await account_create(
            db,
            operator_id=operator["id"],
            account_name=body.account_name,
            password=body.password,
            game_type=body.game_type,
            platform_url=body.platform_url,
        )
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc):
            raise BizError(4002, "account already bound", status_code=409)
        raise

    await _sync_shared_market_routes_on_bind(db, account=row)

    return ApiResponse(
        data=await _build_account_payload(
            db=db,
            row=row,
            odds_synced=False,
            odds_count=0,
            odds_message="account bound, please verify account",
        )
    )


@router.delete("/accounts/{account_id}")
async def unbind_account(
    account_id: int,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    try:
        deleted = await account_delete(db, account_id=account_id, operator_id=operator["id"])
    except ValueError as exc:
        if str(exc) == "account has running strategies":
            raise BizError(4003, "请先停止该账号下的策略，再解绑账号", status_code=400)
        raise
    if not deleted:
        raise BizError(4001, "account not found", status_code=404)
    return ApiResponse(data=None)


async def _run_account_verification_flow(
    *,
    db,
    operator_id: int,
    account: dict,
    session_runtime: AccountSessionRuntime | None,
) -> tuple[dict, list[PlatformCapabilityProbe]]:
    candidate_platform_types = []
    seen_platform_types: set[str] = set()
    for raw_platform_type in get_allowed_platform_types(account["game_type"]):
        platform_type = _normalize_platform_type(raw_platform_type)
        if platform_type in seen_platform_types:
            continue
        candidate_platform_types.append(platform_type)
        seen_platform_types.add(platform_type)
    if not candidate_platform_types:
        raise BizError(
            1002,
            f"platform_type is not allowed for game_type={account['game_type']}",
            status_code=400,
        )

    adapter = session_runtime.adapter if session_runtime is not None else create_platform_adapter(
        candidate_platform_types[0],
        account.get("platform_url"),
    )
    try:
        if session_runtime is not None:
            session_ok = await session_runtime.ensure_logged_in("account_verify")
            if not session_ok:
                raise BizError(
                    4003,
                    "account verification failed: session unavailable",
                    status_code=400,
                )
            login_result = LoginResult(success=True, token=session_runtime.session.session_token)
        else:
            login_result = await _login_platform_account(
                adapter,
                account["account_name"],
                account["password"],
            )
            if not login_result.success:
                raise BizError(
                    4003,
                    f"account verification failed: {login_result.message}",
                    status_code=400,
                )

        now = _now_bjt()
        if session_runtime is not None:
            balance_info = await session_runtime.run_platform_call(
                "account_verify_query_balance",
                adapter.query_balance,
            )
        else:
            balance_info = await adapter.query_balance()
        balance_cents = int(balance_info.balance * 100)
        row = await account_update(
            db,
            account_id=account["id"],
            operator_id=operator_id,
            status="online",
            balance=balance_cents,
            last_login_at=now,
            login_fail_count=0,
        )
        if not row:
            raise BizError(4001, "account not found", status_code=404)

        capabilities: list[PlatformCapabilityProbe] = []
        for platform_type in candidate_platform_types:
            if session_runtime is not None:
                capability = await session_runtime.run_platform_call(
                    f"account_verify_probe_{platform_type}",
                    lambda _platform_type=platform_type: _probe_platform_capability(
                        adapter=adapter,
                        db=db,
                        account=account,
                        operator_id=operator_id,
                        platform_type=_platform_type,
                    ),
                )
            else:
                capability = await _probe_platform_capability(
                    adapter=adapter,
                    db=db,
                    account=account,
                    operator_id=operator_id,
                    platform_type=platform_type,
                )
            capabilities.append(capability)
            if capability.verify_status == "supported":
                await account_platform_session_upsert(
                    db,
                    account_id=account["id"],
                    platform_type=platform_type,
                    status="online",
                    session_token=login_result.token,
                    last_login_at=now,
                    login_fail_count=0,
                )
        return row, capabilities
    finally:
        if session_runtime is None:
            await adapter.close()


async def _record_shared_market_url(
    request: Request,
    *,
    db,
    account: dict,
) -> None:
    normalized_url = normalize_platform_url(account.get("platform_url"))
    if not normalized_url:
        return

    engine = getattr(request.app.state, "engine", None)
    runtime = getattr(engine, "shared_market_runtime", None)
    detection_failure: tuple[str, str] | None = None

    for candidate_platform_type in _candidate_shared_platform_types(account):
        platform_type = _normalize_platform_type(candidate_platform_type)
        group = await _resolve_shared_group_for_route(
            db,
            normalized_url=normalized_url,
            platform_type=platform_type,
        )
        if group is not None:
            await account_shared_route_upsert(
                db,
                account_id=int(account["id"]),
                operator_id=int(account["operator_id"]),
                platform_type=platform_type,
                normalized_url=normalized_url,
                data_source_state="shared",
                shared_group_id=int(group["shared_group_id"]),
            )
            return

        await account_shared_route_upsert(
            db,
            account_id=int(account["id"]),
            operator_id=int(account["operator_id"]),
            platform_type=platform_type,
            normalized_url=normalized_url,
            data_source_state="local",
        )

        if runtime is None:
            detection_failure = (platform_type, "shared_market_runtime_unavailable")
            break

        try:
            discovery_result = await runtime.discover_and_bind_uncovered_url(
                platform_type=platform_type,
                platform_url=normalized_url,
                account_id=int(account.get("id") or 0),
                sample_raw_url=account.get("platform_url"),
            )
            discovered_group_id = _discovered_shared_group_id(discovery_result)
            if discovered_group_id is not None:
                try:
                    await account_shared_route_mark_pending(
                        db,
                        account_id=int(account["id"]),
                        operator_id=int(account["operator_id"]),
                        platform_type=platform_type,
                        normalized_url=normalized_url,
                        pending_shared_group_id=int(discovered_group_id),
                    )
                except Exception as exc:
                    logger.exception(
                        "shared_market_route_pending_failed account_id=%s platform=%s group_id=%s",
                        account.get("id"),
                        platform_type,
                        discovered_group_id,
                    )
                    await _notify_admins_shared_detection_failed(
                        db,
                        account=account,
                        normalized_url=normalized_url,
                        platform_type=platform_type,
                        failure_reason=str(exc)[:200],
                    )
                return

            detection_failure = (platform_type, "no_shared_group_matched")
        except Exception as exc:
            logger.exception(
                "shared_market_discovery_hook_failed account_id=%s platform=%s",
                account.get("id"),
                platform_type,
            )
            detection_failure = (platform_type, str(exc)[:200])

    if detection_failure is not None:
        platform_type, failure_reason = detection_failure
        await _notify_admins_shared_detection_failed(
            db,
            account=account,
            normalized_url=normalized_url,
            platform_type=platform_type,
            failure_reason=failure_reason,
        )


async def _verify_account(
    *,
    account_id: int,
    operator: dict,
    db,
    request: Request,
) -> ApiResponse[dict[str, Any]]:
    account = await account_get_by_id(db, account_id=account_id, operator_id=operator["id"])
    if not account:
        raise BizError(4001, "account not found", status_code=404)

    try:
        run = await account_verification_run_create(
            db,
            account_id=account["id"],
            snapshot_game_type=account["game_type"],
            snapshot_platform_url=account.get("platform_url"),
            snapshot_password_hash=_password_hash(account["password"]),
        )
    except sqlite3.IntegrityError as exc:
        message = str(exc).lower()
        if "uq_verification_runs_running" in message or "account_verification_runs.account_id" in message:
            raise BizError(4002, "verification_in_progress", status_code=409)
        raise
    except Exception as exc:
        message = str(exc).lower()
        if "uq_verification_runs_running" in message or "account_verification_runs.account_id" in message:
            raise BizError(4002, "verification_in_progress", status_code=409)
        raise

    try:
        engine = getattr(request.app.state, "engine", None)
        runtime = None
        if engine is not None and hasattr(engine, "get_runtime_for_account"):
            runtime = await engine.get_runtime_for_account(
                account_id=account["id"],
            )

        row, capabilities = await asyncio.wait_for(
            _run_account_verification_flow(
                db=db,
                operator_id=operator["id"],
                account=account,
                session_runtime=runtime,
            ),
            timeout=VERIFICATION_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        await account_verification_run_fail(
            db,
            verification_run_id=run["id"],
            run_status="timed_out",
            stale_reason="verification_timeout",
        )
        raise BizError(5004, "verification_timeout", status_code=504)
    except CaptchaError as exc:
        await account_verification_run_fail(
            db,
            verification_run_id=run["id"],
            run_status="failed",
            stale_reason=f"captcha_error:{exc}",
        )
        raise BizError(4003, f"captcha recognition failed: {exc}", status_code=400)
    except BizError as exc:
        await account_verification_run_fail(
            db=db,
            verification_run_id=run["id"],
            run_status="failed",
            stale_reason=(exc.message or "")[:200],
        )
        raise
    except Exception as exc:
        logger.exception("account verification failed account_id=%s", account_id)
        await account_verification_run_fail(
            db,
            verification_run_id=int(run["id"]),
            run_status="failed",
            stale_reason=(str(exc) or "")[:200],
        )
        raise BizError(4003, f"account verification failed: {exc}", status_code=400)

    payload_by_platform: dict[str, dict[str, Any]] = {}
    for item in capabilities:
        platform_type = _normalize_probe_platform_type(item.platform_type)
        payload_by_platform[platform_type] = {
            "platform_type": platform_type,
            "verify_status": _safe_text(item.verify_status),
            "market_state": _safe_text(item.market_state),
            "detected_issue": None if item.detected_issue is None else _safe_text(item.detected_issue),
            "last_verified_at": _safe_text(item.last_verified_at),
            "odds_synced": bool(item.odds_synced),
            "odds_count": int(item.odds_count or 0),
            "odds_message": _safe_text(item.odds_message),
            "last_error": None if item.last_error is None else _safe_text(item.last_error),
        }
    payload_capabilities = list(payload_by_platform.values())
    await account_verification_run_complete(
        db,
        verification_run_id=int(run["id"]),
        capabilities=payload_capabilities,
    )

    refreshed_row = await account_get_by_id(db, account_id=account_id, operator_id=operator["id"])
    if not refreshed_row:
        raise BizError(4001, "account not found", status_code=404)

    total_odds_count = sum(item.odds_count for item in capabilities)
    odds_synced = any(item.odds_synced for item in capabilities)
    odds_message = "verification completed"
    synced_item = next((item for item in capabilities if item.odds_synced), None)
    if synced_item:
        odds_message = synced_item.odds_message
    elif capabilities:
        odds_message = capabilities[0].odds_message

    await _record_shared_market_url(
        request=request,
        db=db,
        account=refreshed_row,
    )
    return ApiResponse(
        data=await _build_account_payload(
            db=db,
            row=refreshed_row,
            odds_synced=odds_synced,
            odds_count=total_odds_count,
            odds_message=odds_message,
        )
    )


@router.post("/accounts/{account_id}/verify")
async def verify_account(
    account_id: int,
    request: Request,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    return await _verify_account(
        account_id=account_id,
        operator=operator,
        db=db,
        request=request,
    )


@router.post("/accounts/{account_id}/login", deprecated=True)
async def manual_login_alias(
    account_id: int,
    request: Request,
    platform_type: str | None = Query(default=None),
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    _ = platform_type
    return await _verify_account(
        account_id=account_id,
        operator=operator,
        db=db,
        request=request,
    )


@router.post("/accounts/{account_id}/logout")
async def manual_logout(
    account_id: int,
    request: Request,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    account = await account_get_by_id(db, account_id=account_id, operator_id=operator["id"])
    if not account:
        raise BizError(4001, "account not found", status_code=404)

    engine = getattr(request.app.state, "engine", None)
    if engine is not None:
        await engine.stop_worker(account_id=account_id)

    sessions = await account_platform_session_list(db, account_id=account_id)
    for session in sessions:
        await account_platform_session_delete(
            db,
            account_id=account_id,
            platform_type=session["platform_type"],
        )
    await account_verification_runs_mark_stale_by_account(
        db,
        account_id=account_id,
        stale_reason="manual_logout",
    )

    row = await account_update(
        db,
        account_id=account_id,
        operator_id=operator["id"],
        status="inactive",
        session_token=None,
    )
    if not row:
        raise BizError(4001, "account not found", status_code=404)
    return ApiResponse(data=await _build_account_payload(db=db, row=row))


@router.post("/accounts/{account_id}/kill-switch")
async def toggle_kill_switch(
    account_id: int,
    body: KillSwitchUpdate,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    account = await account_get_by_id(db, account_id=account_id, operator_id=operator["id"])
    if not account:
        raise BizError(4001, "account not found", status_code=404)

    row = await account_update(
        db,
        account_id=account_id,
        operator_id=operator["id"],
        kill_switch=1 if body.enabled else 0,
    )
    if not row:
        raise BizError(4001, "account not found", status_code=404)
    return ApiResponse(data=await _build_account_payload(db=db, row=row))
