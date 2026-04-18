"""
Account binding and account-level validation endpoints.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

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
    get_default_platform_type,
    mask_password,
)
from app.schemas.common import ApiResponse
from app.utils.captcha import CaptchaError, CaptchaService, get_shared_captcha_service
from app.utils.response import BizError

router = APIRouter()
logger = logging.getLogger(__name__)
_BJT = timezone(timedelta(hours=8))
CAPTCHA_LOGIN_ATTEMPTS = 3


@dataclass(frozen=True)
class OddsSyncStatus:
    odds_synced: bool
    odds_count: int
    odds_message: str


def _resolve_account_platform_type(account: dict, requested_platform_type: str | None = None) -> str:
    if requested_platform_type:
        return requested_platform_type
    return get_default_platform_type(account["game_type"])


def _validate_account_platform_type(account: dict, platform_type: str) -> str:
    normalized = (platform_type or "").strip().upper()
    allowed = get_allowed_platform_types(account["game_type"])
    if normalized not in allowed:
        raise BizError(
            1002,
            f"platform_type is not allowed for game_type={account['game_type']}",
            status_code=400,
        )
    return normalized


def _to_account_info(
    row: dict,
    *,
    odds_synced: bool | None = None,
    odds_count: int | None = None,
    odds_message: str | None = None,
) -> AccountInfo:
    game_type = row["game_type"]
    return AccountInfo(
        id=row["id"],
        account_name=row["account_name"],
        password_masked=mask_password(row["password"]),
        game_type=game_type,
        allowed_strategy_platform_types=get_allowed_platform_types(game_type),
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


def _state_label(state: int) -> str:
    return {1: "open", 2: "closed", 3: "waiting"}.get(state, f"unknown({state})")


async def _sync_odds_after_login(
    *,
    adapter: PlatformAdapter,
    db,
    account_id: int,
    operator_id: int,
    platform_type: str,
) -> OddsSyncStatus:
    try:
        install = await adapter.get_current_install()
    except Exception as exc:
        logger.warning("get_current_install failed account_id=%d: %s", account_id, exc)
        return OddsSyncStatus(False, 0, f"login ok, but issue fetch failed: {exc}")

    if install.state != 1:
        return OddsSyncStatus(
            False,
            0,
            f"login ok, current state is {_state_label(install.state)}, odds not synced",
        )

    try:
        raw_odds = await adapter.load_odds(install.issue)
    except Exception as exc:
        logger.warning("load_odds failed account_id=%d issue=%s: %s", account_id, install.issue, exc)
        return OddsSyncStatus(False, 0, f"login ok, but odds fetch failed: {exc}")

    non_zero_odds = {key: value for key, value in raw_odds.items() if value > 0}
    if not non_zero_odds:
        return OddsSyncStatus(False, 0, "login ok, but platform returned empty odds")

    try:
        await _sync_odds(
            db,
            account_id=account_id,
            operator_id=operator_id,
            platform_type=platform_type,
            new_odds=non_zero_odds,
        )
    except Exception as exc:
        logger.error("persist odds failed account_id=%d: %s", account_id, exc)
        return OddsSyncStatus(False, len(non_zero_odds), f"login ok, but odds save failed: {exc}")

    return OddsSyncStatus(True, len(non_zero_odds), f"odds synced: {len(non_zero_odds)} items")


@router.get("/accounts")
async def list_accounts(
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    rows = await account_list_by_operator(db, operator_id=operator["id"])
    return ApiResponse[list[AccountInfo]](data=[_to_account_info(row) for row in rows])


@router.post("/accounts")
async def bind_account(
    body: AccountCreate,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    existing = await account_list_by_operator(db, operator_id=operator["id"])
    if len(existing) >= operator["max_accounts"]:
        raise BizError(4002, "max account limit reached", status_code=409)

    validation_platform_type = get_default_platform_type(body.game_type)
    adapter = create_platform_adapter(validation_platform_type, body.platform_url)
    try:
        login_result = await _login_platform_account(
            adapter,
            body.account_name,
            body.password,
        )
    except CaptchaError as exc:
        raise BizError(4003, f"account login failed: {exc}", status_code=400)
    finally:
        await adapter.close()

    if not login_result.success:
        raise BizError(4003, f"account login failed: {login_result.message}", status_code=400)

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
        data=_to_account_info(
            row,
            odds_synced=False,
            odds_count=0,
            odds_message="account bound, validate a platform session before using odds",
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


@router.post("/accounts/{account_id}/login")
async def manual_login(
    account_id: int,
    platform_type: str | None = Query(default=None),
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    account = await account_get_by_id(db, account_id=account_id, operator_id=operator["id"])
    if not account:
        raise BizError(4001, "account not found", status_code=404)

    resolved_platform_type = _validate_account_platform_type(
        account,
        _resolve_account_platform_type(account, platform_type),
    )
    adapter = create_platform_adapter(resolved_platform_type, account.get("platform_url"))

    try:
        login_result = await _login_platform_account(
            adapter,
            account["account_name"],
            account["password"],
        )
        if not login_result.success:
            raise BizError(4003, f"account login failed: {login_result.message}", status_code=400)

        balance_info = await adapter.query_balance()
        balance_cents = int(balance_info.balance * 100)
        now = datetime.now(_BJT).strftime("%Y-%m-%d %H:%M:%S")

        row = await account_update(
            db,
            account_id=account_id,
            operator_id=operator["id"],
            status="online",
            balance=balance_cents,
            last_login_at=now,
            login_fail_count=0,
        )
        await account_platform_session_upsert(
            db,
            account_id=account_id,
            platform_type=resolved_platform_type,
            status="online",
            session_token=login_result.token,
            last_login_at=now,
            login_fail_count=0,
        )

        odds_status = await _sync_odds_after_login(
            adapter=adapter,
            db=db,
            account_id=account_id,
            operator_id=operator["id"],
            platform_type=resolved_platform_type,
        )
        return ApiResponse[AccountInfo](
            data=_to_account_info(
                row,
                odds_synced=odds_status.odds_synced,
                odds_count=odds_status.odds_count,
                odds_message=odds_status.odds_message,
            )
        )
    except CaptchaError as exc:
        raise BizError(4003, f"captcha recognition failed: {exc}", status_code=400)
    finally:
        await adapter.close()


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
    return ApiResponse[AccountInfo](data=_to_account_info(row))


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
    return ApiResponse[AccountInfo](data=_to_account_info(row))

