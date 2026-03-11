""" SchemaPydantic v2

BetOrderInfo   key_code_name 
"""
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.utils.key_code_map import get_key_code_name


class BetOrderInfo(BaseModel):
    """"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": 1,
                "idempotent_id": "202603021001-1-DX1",
                "strategy_id": 1,
                "account_id": 1,
                "issue": "202603021001",
                "key_code": "DX1",
                "key_code_name": "",
                "amount": 1.00,
                "odds": 1.98,
                "status": "settled",
                "open_result": "3,5,8",
                "sum_value": 16,
                "is_win": 1,
                "pnl": 0.98,
                "simulation": False,
                "martin_level": None,
                "bet_at": "2026-03-02 10:01:30",
                "settled_at": "2026-03-02 10:05:00",
                "fail_reason": None,
            }
        }
    )

    id: int
    idempotent_id: str
    strategy_id: int
    account_id: int
    issue: str
    key_code: str
    key_code_name: str
    amount: float
    odds: Optional[float] = None
    status: str
    open_result: Optional[str] = None
    sum_value: Optional[int] = None
    is_win: Optional[int] = None
    pnl: Optional[float] = None
    simulation: bool
    martin_level: Optional[int] = None
    bet_at: Optional[str] = None
    settled_at: Optional[str] = None
    fail_reason: Optional[str] = None


def row_to_bet_order_info(row: dict) -> BetOrderInfo:
    """ DB  BetOrderInfo + key_code_name """
    return BetOrderInfo(
        id=row["id"],
        idempotent_id=row["idempotent_id"],
        strategy_id=row["strategy_id"],
        account_id=row["account_id"],
        issue=row["issue"],
        key_code=row["key_code"],
        key_code_name=get_key_code_name(row["key_code"]),
        amount=row["amount"] / 100,  #   
        odds=(row["odds"] / 10000) if row.get("odds") is not None else None,  # 还原为浮点赔率
        status=row["status"],
        open_result=row.get("open_result"),
        sum_value=row.get("sum_value"),
        is_win=row.get("is_win"),
        pnl=(row["pnl"] / 100) if row.get("pnl") is not None else None,  #   
        simulation=bool(row.get("simulation", 0)),
        martin_level=row.get("martin_level"),
        bet_at=row.get("bet_at"),
        settled_at=row.get("settled_at"),
        fail_reason=row.get("fail_reason"),
    )
