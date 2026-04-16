"""

GET /dashboard              
GET /dashboard/recent-bets  
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

_BJT = timezone(timedelta(hours=8))

from fastapi import APIRouter, Depends

from app.api.dependencies import get_current_operator, get_db_conn
from app.models.db_ops import (
    account_list_by_operator,
    alert_get_unread_count,
    bet_order_list_by_operator,
    bet_order_list_pending_by_operator,
    strategy_list_by_operator,
)
from app.schemas.bet_order import BetOrderInfo, row_to_bet_order_info
from app.schemas.common import ApiResponse
from app.schemas.dashboard import OperatorDashboard
from app.schemas.strategy import StrategyInfo

#  strategies 
from app.api.strategies import _to_strategy_info

router = APIRouter()


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
    # 构建 account_id -> {name, platform_type} 映射
    acct_map = {a["id"]: a for a in accounts}
    running_strategies = []
    for s in strategies:
        if s["status"] == "running":
            info = _to_strategy_info(s)
            acct = acct_map.get(s["account_id"])
            if acct:
                info.account_name = acct.get("account_name") or acct.get("name", "")
                info.platform_type = acct.get("platform_type", "")
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

    dashboard = OperatorDashboard(
        balance=total_balance,
        daily_pnl=daily_pnl,
        total_pnl=total_pnl,
        running_strategies=running_strategies,
        pending_bets=pending_bets,
        unread_alerts=unread_alerts,
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
