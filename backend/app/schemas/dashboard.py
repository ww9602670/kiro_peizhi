""" SchemaPydantic v2

OperatorDashboard  
AdminDashboard     
OperatorSummary    
"""
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.schemas.bet_order import BetOrderInfo
from app.schemas.strategy import StrategyInfo


class OperatorDashboard(BaseModel):
    """"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "balance": 100.00,
                "daily_pnl": 5.50,
                "total_pnl": 120.00,
                "running_strategies": [],
                "pending_bets": [],
                "unread_alerts": 3,
            }
        }
    )

    balance: float
    daily_pnl: float
    total_pnl: float
    running_strategies: list[StrategyInfo]
    pending_bets: list[BetOrderInfo]
    unread_alerts: int


class OperatorSummary(BaseModel):
    """"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": 1,
                "username": "operator1",
                "status": "active",
                "daily_pnl": 5.50,
                "total_pnl": 120.00,
                "running_strategies": 2,
            }
        }
    )

    id: int
    username: str
    status: str
    daily_pnl: float
    total_pnl: float
    running_strategies: int


class AdminDashboard(BaseModel):
    """"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "total_operators": 5,
                "active_operators": 3,
                "operator_summaries": [],
            }
        }
    )

    total_operators: int
    active_operators: int
    operator_summaries: list[OperatorSummary]
