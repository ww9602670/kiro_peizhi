"""
Account binding and account-level validation endpoints.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import get_current_operator, get_db_conn
from app.engine.adapters.base import LoginResult, PlatformAdapter
from app.engine.adapters.factory import create_platform_adapter
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
    account_verification_runs_refresh_stale,
    account_update,
    alert_create,
    odds_batch_upsert,
    odds_list_by_account,
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
from app.utils.response import BizError

router = APIRouter()
logger = logging.getLogger(__name__)
_BJT = timezone(timedelta(hours=8))
CAPTCHA_LOGIN_ATTEMPTS = 3
VERIFICATION_TTL_MINUTES = 30
VERIFICATION_TIMEOUT_SECONDS = 90


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


def _now_bjt() -> str:
    return datetime.now(_BJT).strftime("%Y-%m-%d %H:%M:%S")


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
    }


def _to_account_info(
    row: dict,
    *,
    allowed_strategy_platform_types: list[str] | None = None,
    platform_capabilities: list[dict[str, Any]] | None = None,
    latest_verification_run_id: int | None = None,
    effective_verification_run_id: int | None = None,
    verification_in_progress: bool = False,
    verification_stale: bool = False,
    summary_status_reason: str | None = None,
    odds_synced: bool | None = None,
    odds_count: int | None = None,
    odds_message: str | None = None,
) -> AccountInfo:
    return AccountInfo(
        id=row["id"],
        account_name=row["account_name"],
        password_masked=mask_password(row["password"]),
        game_type=row["game_type"],
        allowed_strategy_platform_types=allowed_strategy_platform_types or [],
        platform_capabilities=platform_capabilities or [],
        latest_verification_run_id=latest_verification_run_id,
        effective_verification_run_id=effective_verification_run_id,
        verification_in_progress=verification_in_progress,
        verification_stale=verification_stale,
        summary_status_reason=summary_status_reason,
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
            odds_map=new_odds,
            confirmed=True,
        )
        return

    old_map = {row["key_code"]: row["odds_value"] for row in existing}
    if old_map == new_odds:
        return

    await odds_batch_upsert(
        db,
        account_id=account_id,
        platform_type=platform_type,
        odds_map=new_odds,
        confirmed=False,
    )

    changes: list[str] = []
    for key in sorted(set(old_map) | set(new_odds)):
        old_val = old_map.get(key)
        new_val = new_odds.get(key)
        if old_val != new_val:
            changes.append(f"{key}: {old_val} -> {new_val}")

    await alert_create(
        db,
        operator_id=operator_id,
        type="odds_changed",
        level="warning",
        title=f"odds changed account_id={account_id} platform={platform_type}",
        detail="\n".join(changes),
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

    non_zero_odds = {key: value for key, value in raw_odds.items() if value > 0}
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
    return _to_account_info(
        row,
        allowed_strategy_platform_types=verification_view["allowed_strategy_platform_types"],
        platform_capabilities=verification_view["platform_capabilities"],
        latest_verification_run_id=verification_view["latest_verification_run_id"],
        effective_verification_run_id=verification_view["effective_verification_run_id"],
        verification_in_progress=verification_view["verification_in_progress"],
        verification_stale=verification_view["verification_stale"],
        summary_status_reason=verification_view["summary_status_reason"],
        odds_synced=odds_synced,
        odds_count=odds_count,
        odds_message=odds_message,
    )


@router.get("/accounts")
async def list_accounts(
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    rows = await account_list_by_operator(db, operator_id=operator["id"])
    result: list[AccountInfo] = []
    for row in rows:
        result.append(await _build_account_info(db=db, row=row))
    return ApiResponse[list[AccountInfo]](data=result)


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

    return ApiResponse[AccountInfo](
        data=await _build_account_info(
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
    deleted = await account_delete(db, account_id=account_id, operator_id=operator["id"])
    if not deleted:
        raise BizError(4001, "account not found", status_code=404)
    return ApiResponse(data=None)


async def _run_account_verification_flow(
    *,
    db,
    operator_id: int,
    account: dict,
) -> tuple[dict, list[PlatformCapabilityProbe]]:
    candidate_platform_types = get_allowed_platform_types(account["game_type"])
    if not candidate_platform_types:
        raise BizError(
            1002,
            f"platform_type is not allowed for game_type={account['game_type']}",
            status_code=400,
        )

    adapter = create_platform_adapter(
        candidate_platform_types[0],
        account.get("platform_url"),
    )
    try:
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
        await adapter.close()


async def _verify_account(
    *,
    account_id: int,
    operator: dict,
    db,
) -> ApiResponse[AccountInfo]:
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
        row, capabilities = await asyncio.wait_for(
            _run_account_verification_flow(
                db=db,
                operator_id=operator["id"],
                account=account,
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
            verification_run_id=run["id"],
            run_status="failed",
            stale_reason=(str(exc) or "")[:200],
        )
        raise BizError(4003, f"account verification failed: {exc}", status_code=400)

    payload_capabilities = [
        {
            "platform_type": item.platform_type,
            "verify_status": item.verify_status,
            "market_state": item.market_state,
            "detected_issue": item.detected_issue,
            "last_verified_at": item.last_verified_at,
            "odds_synced": item.odds_synced,
            "odds_count": item.odds_count,
            "odds_message": item.odds_message,
            "last_error": item.last_error,
        }
        for item in capabilities
    ]
    await account_verification_run_complete(
        db,
        verification_run_id=run["id"],
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

    return ApiResponse[AccountInfo](
        data=await _build_account_info(
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
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    return await _verify_account(account_id=account_id, operator=operator, db=db)


@router.post("/accounts/{account_id}/login", deprecated=True)
async def manual_login_alias(
    account_id: int,
    platform_type: str | None = Query(default=None),
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    _ = platform_type
    return await _verify_account(account_id=account_id, operator=operator, db=db)


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

    row = await account_update(
        db,
        account_id=account_id,
        operator_id=operator["id"],
        status="inactive",
        session_token=None,
    )
    if not row:
        raise BizError(4001, "account not found", status_code=404)
    return ApiResponse[AccountInfo](data=await _build_account_info(db=db, row=row))


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
    return ApiResponse[AccountInfo](data=await _build_account_info(db=db, row=row))
