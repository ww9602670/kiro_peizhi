"""

GET /dashboard              
GET /dashboard/recent-bets  
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends

from app.api.dependencies import get_current_operator, get_db_conn
from app.models.db_ops import (
    account_list_by_operator,
    alert_get_unread_count,
    bet_order_list_by_operator,
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
    running_strategies = [
        _to_strategy_info(s) for s in strategies if s["status"] == "running"
    ]

    today = datetime.utcnow().strftime("%Y-%m-%d")
    daily_pnl = sum(
        s["daily_pnl"] for s in strategies
        if s.get("daily_pnl_date") == today
    ) / 100
    total_pnl = sum(s["total_pnl"] for s in strategies) / 100

    # 3.  20 
    recent_rows, _ = await bet_order_list_by_operator(
        db, operator_id=operator_id, page=1, page_size=20
    )
    recent_bets = [row_to_bet_order_info(r) for r in recent_rows]

    # 4. 
    unread_alerts = await alert_get_unread_count(db, operator_id=operator_id)

    dashboard = OperatorDashboard(
        balance=total_balance,
        daily_pnl=daily_pnl,
        total_pnl=total_pnl,
        running_strategies=running_strategies,
        recent_bets=recent_bets,
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
