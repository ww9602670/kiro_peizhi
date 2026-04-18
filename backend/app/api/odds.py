"""Odds endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query

from app.api.accounts import _login_platform_account, _sync_odds
from app.api.dependencies import get_current_operator, get_db_conn
from app.engine.adapters.factory import create_platform_adapter
from app.models.db_ops import (
    account_get_by_id,
    odds_confirm_all,
    odds_list_by_account,
)
from app.schemas.account import get_allowed_platform_types
from app.schemas.common import ApiResponse
from app.schemas.odds import (
    OddsConfirmResponse,
    OddsItem,
    OddsListResponse,
    OddsRefreshResponse,
    PeriodInfo,
)
from app.utils.response import BizError

router = APIRouter()
logger = logging.getLogger(__name__)


async def _get_verified_account(account_id: int, operator: dict, db):
    account = await account_get_by_id(db, account_id=account_id, operator_id=operator["id"])
    if not account:
        raise BizError(4001, "account not found", status_code=404)
    return account


def _resolve_platform_type(account: dict, platform_type: str | None) -> str:
    normalized = (platform_type or "").strip().upper()
    if not normalized:
        raise BizError(1002, "platform_type is required", status_code=400)
    allowed = get_allowed_platform_types(account["game_type"])
    if normalized not in allowed:
        raise BizError(
            1002,
            f"platform_type is not allowed for game_type={account['game_type']}",
            status_code=400,
        )
    return normalized


def _state_label(state: int) -> str:
    return {1: "open", 2: "closed", 3: "waiting"}.get(state, "unknown")


def _build_refresh_response(
    *,
    account_id: int,
    platform_type: str,
    period: PeriodInfo | None = None,
    odds_synced: bool = False,
    odds_count: int = 0,
    odds_message: str = "",
) -> OddsRefreshResponse:
    return OddsRefreshResponse(
        account_id=account_id,
        platform_type=platform_type,
        period=period,
        odds_synced=odds_synced,
        odds_count=odds_count,
        odds_message=odds_message,
    )


@router.get("/accounts/{account_id}/odds")
async def get_account_odds(
    account_id: int,
    platform_type: str | None = Query(default=None),
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    account = await _get_verified_account(account_id, operator, db)
    resolved_platform_type = _resolve_platform_type(account, platform_type)
    rows = await odds_list_by_account(
        db,
        account_id=account_id,
        platform_type=resolved_platform_type,
    )
    items = [
        OddsItem(
            key_code=row["key_code"],
            odds_value=row["odds_value"],
            confirmed=bool(row["confirmed"]),
            fetched_at=row["fetched_at"],
            confirmed_at=row["confirmed_at"],
        )
        for row in rows
    ]
    return ApiResponse[OddsListResponse](
        data=OddsListResponse(
            account_id=account_id,
            platform_type=resolved_platform_type,
            items=items,
            has_unconfirmed=any(not item.confirmed for item in items),
        )
    )


@router.post("/accounts/{account_id}/odds/confirm")
async def confirm_account_odds(
    account_id: int,
    platform_type: str | None = Query(default=None),
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    account = await _get_verified_account(account_id, operator, db)
    resolved_platform_type = _resolve_platform_type(account, platform_type)
    count = await odds_confirm_all(
        db,
        account_id=account_id,
        platform_type=resolved_platform_type,
    )
    return ApiResponse[OddsConfirmResponse](data=OddsConfirmResponse(confirmed_count=count))


@router.post("/accounts/{account_id}/odds/refresh")
async def refresh_account_odds(
    account_id: int,
    platform_type: str | None = Query(default=None),
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    account = await _get_verified_account(account_id, operator, db)
    resolved_platform_type = _resolve_platform_type(account, platform_type)
    adapter = create_platform_adapter(
        resolved_platform_type,
        account.get("platform_url"),
    )

    try:
        login_result = await _login_platform_account(
            adapter,
            account["account_name"],
            account["password"],
        )
        if not login_result.success:
            return ApiResponse[OddsRefreshResponse](
                data=_build_refresh_response(
                    account_id=account_id,
                    platform_type=resolved_platform_type,
                    odds_message=f"login failed: {login_result.message}",
                )
            )

        try:
            install = await adapter.get_current_install()
        except Exception as exc:
            logger.warning("get_current_install failed account_id=%d: %s", account_id, exc)
            return ApiResponse[OddsRefreshResponse](
                data=_build_refresh_response(
                    account_id=account_id,
                    platform_type=resolved_platform_type,
                    odds_message=f"issue fetch failed: {exc}",
                )
            )

        period = PeriodInfo(
            issue=install.issue,
            state=install.state,
            state_label=_state_label(install.state),
            close_countdown_sec=install.close_countdown_sec,
            open_countdown_sec=install.open_countdown_sec,
            pre_issue=install.pre_issue,
            pre_result=install.pre_result,
        )

        if install.state != 1:
            return ApiResponse[OddsRefreshResponse](
                data=_build_refresh_response(
                    account_id=account_id,
                    platform_type=resolved_platform_type,
                    period=period,
                    odds_message=f"state={_state_label(install.state)}, odds not refreshed",
                )
            )

        try:
            raw_odds = await adapter.load_odds(install.issue)
        except Exception as exc:
            logger.warning("load_odds failed account_id=%d: %s", account_id, exc)
            return ApiResponse[OddsRefreshResponse](
                data=_build_refresh_response(
                    account_id=account_id,
                    platform_type=resolved_platform_type,
                    period=period,
                    odds_message=f"odds fetch failed: {exc}",
                )
            )

        non_zero = {key: value for key, value in raw_odds.items() if value > 0}
        if not non_zero:
            return ApiResponse[OddsRefreshResponse](
                data=_build_refresh_response(
                    account_id=account_id,
                    platform_type=resolved_platform_type,
                    period=period,
                    odds_message="platform returned empty odds",
                )
            )

        try:
            await _sync_odds(
                db,
                account_id,
                operator["id"],
                resolved_platform_type,
                non_zero,
            )
            synced = True
            message = f"odds synced: {len(non_zero)} items"
        except Exception as exc:
            logger.error("save odds failed account_id=%d: %s", account_id, exc)
            synced = False
            message = f"odds save failed: {exc}"

        return ApiResponse[OddsRefreshResponse](
            data=_build_refresh_response(
                account_id=account_id,
                platform_type=resolved_platform_type,
                period=period,
                odds_synced=synced,
                odds_count=len(non_zero),
                odds_message=message,
            )
        )
    finally:
        await adapter.close()
