"""

GET /bet-orders         +  + operator_id 
GET /bet-orders/{id}    operator_id 
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_current_operator, get_db_conn
from app.models.db_ops import bet_order_get_by_id, bet_order_list_by_operator, bet_order_summary_by_operator
from app.schemas.bet_order import BetOrderInfo, row_to_bet_order_info
from app.schemas.common import ApiResponse, PagedData
from app.utils.response import BizError

router = APIRouter()

_BJT = timezone(timedelta(hours=8))


def _parse_order_date(value: str, *, end_of_day: bool) -> datetime:
    raw = (value or "").strip()
    if not raw:
        raise ValueError("empty date")
    try:
        if len(raw) == 10:
            parsed_date = datetime.strptime(raw, "%Y-%m-%d").date()
            boundary = time.max if end_of_day else time.min
            return datetime.combine(parsed_date, boundary).replace(microsecond=0)
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise ValueError("date must be YYYY-MM-DD or YYYY-MM-DD HH:MM:SS") from exc


def _effective_order_range(
    date_from: str | None,
    date_to: str | None,
) -> tuple[str, str]:
    now = datetime.now(_BJT).replace(tzinfo=None, microsecond=0)
    if not date_from and not date_to:
        return (
            (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S"),
            now.strftime("%Y-%m-%d %H:%M:%S"),
        )

    floor = now - timedelta(days=3)
    start = _parse_order_date(date_from, end_of_day=False) if date_from else floor
    end = _parse_order_date(date_to, end_of_day=True) if date_to else now

    if start < floor:
        start = floor
    if end > now:
        end = now

    return start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")


def _order_query_floor() -> str:
    now = datetime.now(_BJT).replace(tzinfo=None, microsecond=0)
    return (now - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")


@router.get("/bet-orders")
async def list_bet_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    date_from: Optional[str] = Query(None, description=" YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description=" YYYY-MM-DD"),
    strategy_id: Optional[int] = Query(None, description=" ID "),
    status: Optional[str] = Query(None, description="筛选状态: settled/pending"),
    account_id: Optional[int] = Query(None, description="筛选账户ID"),
    ledger: Literal["real", "simulation"] = Query("real", description="ledger: real/simulation"),
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """ + """
    try:
        effective_date_from, effective_date_to = _effective_order_range(date_from, date_to)
    except ValueError as exc:
        raise BizError(1002, str(exc), status_code=400) from exc

    items, total = await bet_order_list_by_operator(
        db,
        operator_id=operator["id"],
        page=page,
        page_size=page_size,
        date_from=effective_date_from,
        date_to=effective_date_to,
        strategy_id=strategy_id,
        status=status,
        account_id=account_id,
        ledger=ledger,
    )
    # 汇总统计（基于完整筛选条件，不分页）
    summary = await bet_order_summary_by_operator(
        db,
        operator_id=operator["id"],
        date_from=effective_date_from,
        date_to=effective_date_to,
        strategy_id=strategy_id,
        status=status,
        account_id=account_id,
        ledger=ledger,
    )
    bet_orders = [row_to_bet_order_info(r) for r in items]
    paged = PagedData[BetOrderInfo](
        items=bet_orders,
        total=total,
        page=page,
        page_size=page_size,
    )
    return ApiResponse(data={
        "paged": paged.model_dump(),
        "summary": summary,
    })


@router.get("/bet-orders/{order_id}")
async def get_bet_order(
    order_id: int,
    ledger: Literal["real", "simulation"] = Query("real", description="ledger: real/simulation"),
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """ operator_id """
    row = await bet_order_get_by_id(
        db,
        order_id=order_id,
        operator_id=operator["id"],
        ledger=ledger,
    )
    if row is None:
        raise BizError(4001, "", status_code=404)
    if row.get("created_at") and row["created_at"] < _order_query_floor():
        raise BizError(4001, "", status_code=404)
    return ApiResponse[BetOrderInfo](data=row_to_bet_order_info(row))
