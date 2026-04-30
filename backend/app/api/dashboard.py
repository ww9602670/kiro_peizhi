"""

GET /dashboard              
GET /dashboard/recent-bets  
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

_BJT = timezone(timedelta(hours=8))

from fastapi import APIRouter, Depends

from app.api.dependencies import get_current_operator, get_db_conn
from app.schemas.account import get_allowed_platform_types
from app.models.db_ops import (
    account_list_by_operator,
    alert_get_unread_count,
    alert_list_by_operator,
    bet_order_list_by_operator,
    bet_order_list_pending_by_operator,
    lottery_result_list_recent,
    strategy_list_by_operator,
)
from app.schemas.alert import AlertInfo
from app.schemas.bet_order import BetOrderInfo, row_to_bet_order_info
from app.schemas.common import ApiResponse
from app.schemas.dashboard import OperatorDashboard, RecentLotteryResult
from app.schemas.strategy import StrategyInfo

#  strategies 
from app.api.strategies import _filter_rows_by_strategy_permissions, _to_strategy_info

router = APIRouter()

DEFAULT_COUNTDOWN_PLATFORM_TYPE = "JND28WEB"


def _to_recent_lottery_result(row: dict) -> RecentLotteryResult:
    return RecentLotteryResult(
        id=row["id"],
        issue=row["issue"],
        open_result=row["open_result"],
        sum_value=row["sum_value"],
        open_time=row.get("open_time"),
        created_at=row["created_at"],
    )


def _resolve_countdown_platform_type(
    accounts: list[dict],
    strategies: list[dict],
    running_strategies: list[StrategyInfo],
) -> str:
    for info in running_strategies:
        platform_type = (getattr(info, "platform_type", None) or "").strip().upper()
        if platform_type:
            return platform_type

    for strategy in reversed(strategies):
        platform_type = str(strategy.get("platform_type") or "").strip().upper()
        if platform_type:
            return platform_type

    for account in accounts:
        if bool(account.get("verification_stale")):
            continue
        for platform_type in account.get("allowed_strategy_platform_types") or []:
            normalized = str(platform_type or "").strip().upper()
            if normalized:
                return normalized

    for account in accounts:
        for platform_type in get_allowed_platform_types(str(account.get("game_type") or "")):
            normalized = str(platform_type or "").strip().upper()
            if normalized:
                return normalized

    return DEFAULT_COUNTDOWN_PLATFORM_TYPE


@router.get("/dashboard")
async def get_dashboard(
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """"""
    operator_id = operator["id"]

    # 1. 
    accounts = await account_list_by_operator(db, operator_id=operator_id)
    total_balance = sum(a.get("balance", 0) for a in accounts) / 100

    # 2.   running  + 
    strategies = await strategy_list_by_operator(db, operator_id=operator_id)
    visible_strategies = await _filter_rows_by_strategy_permissions(
        db,
        operator_id=operator_id,
        rows=strategies,
    )
    # 构建 account_id -> {name, platform_type} 映射
    acct_map = {a["id"]: a for a in accounts}
    running_strategies = []
    for s in visible_strategies:
        if s["status"] == "running":
            info = _to_strategy_info(s)
            acct = acct_map.get(s["account_id"])
            if acct:
                info.account_name = acct.get("account_name") or acct.get("name", "")
                info.platform_type = s.get("platform_type") or ""
            running_strategies.append(info)

    today = datetime.now(_BJT).strftime("%Y-%m-%d")
    daily_pnl = sum(
        s["daily_pnl"] for s in strategies
        if s.get("daily_pnl_date") == today
    ) / 100
    total_pnl = sum(s["total_pnl"] for s in strategies) / 100

    # 3. 待结算投注（最多5条，JOIN 策略名+账户名）
    pending_rows = await bet_order_list_pending_by_operator(
        db, operator_id=operator_id, limit=5
    )
    pending_bets = [row_to_bet_order_info(r) for r in pending_rows]

    # 4. 
    unread_alerts = await alert_get_unread_count(db, operator_id=operator_id)
    recent_result_rows = await lottery_result_list_recent(db, limit=10)
    recent_results = [_to_recent_lottery_result(row) for row in recent_result_rows]
    recent_alert_rows, _ = await alert_list_by_operator(
        db,
        operator_id=operator_id,
        page=1,
        page_size=10,
    )
    recent_alerts = [AlertInfo(**row) for row in recent_alert_rows]

    dashboard = OperatorDashboard(
        balance=total_balance,
        daily_pnl=daily_pnl,
        total_pnl=total_pnl,
        countdown_platform_type=_resolve_countdown_platform_type(
            accounts=accounts,
            strategies=strategies,
            running_strategies=running_strategies,
        ),
        running_strategies=running_strategies,
        pending_bets=pending_bets,
        unread_alerts=unread_alerts,
        recent_results=recent_results,
        recent_alerts=recent_alerts,
    )
    return ApiResponse[OperatorDashboard](data=dashboard)


@router.get("/dashboard/recent-bets")
async def get_recent_bets(
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """20 """
    rows, _ = await bet_order_list_by_operator(
        db, operator_id=operator["id"], page=1, page_size=20
    )
    bets = [row_to_bet_order_info(r) for r in rows]
    return ApiResponse[list[BetOrderInfo]](data=bets)
