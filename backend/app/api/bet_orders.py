"""

GET /bet-orders         +  + operator_id 
GET /bet-orders/{id}    operator_id 
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_current_operator, get_db_conn
from app.models.db_ops import bet_order_get_by_id, bet_order_list_by_operator
from app.schemas.bet_order import BetOrderInfo, row_to_bet_order_info
from app.schemas.common import ApiResponse, PagedData
from app.utils.response import BizError

router = APIRouter()


@router.get("/bet-orders")
async def list_bet_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    date_from: Optional[str] = Query(None, description=" YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description=" YYYY-MM-DD"),
    strategy_id: Optional[int] = Query(None, description=" ID "),
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """ + """
    items, total = await bet_order_list_by_operator(
        db,
        operator_id=operator["id"],
        page=page,
        page_size=page_size,
        date_from=date_from,
        date_to=date_to,
        strategy_id=strategy_id,
    )
    bet_orders = [row_to_bet_order_info(r) for r in items]
    paged = PagedData[BetOrderInfo](
        items=bet_orders,
        total=total,
        page=page,
        page_size=page_size,
    )
    return ApiResponse[PagedData[BetOrderInfo]](data=paged)


@router.get("/bet-orders/{order_id}")
async def get_bet_order(
    order_id: int,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """ operator_id """
    row = await bet_order_get_by_id(db, order_id=order_id, operator_id=operator["id"])
    if row is None:
        raise BizError(4001, "", status_code=404)
    return ApiResponse[BetOrderInfo](data=row_to_bet_order_info(row))
