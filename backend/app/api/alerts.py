""" API 

GET    /alerts             is_read 
PUT    /alerts/{id}/read  
PUT    /alerts/read-all   
GET    /alerts/unread-count  
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_current_operator, get_db_conn
from app.models.db_ops import (
    alert_get_unread_count,
    alert_list_by_operator,
    alert_mark_all_read,
    alert_mark_read,
)
from app.utils.response import BizError

router = APIRouter()


@router.get("/alerts")
async def list_alerts(
    is_read: Optional[int] = Query(None, ge=0, le=1, description="0=, 1=, ="),
    page: int = Query(1, ge=1, description=""),
    page_size: int = Query(20, ge=1, le=100, description=""),
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """"""
    items, total = await alert_list_by_operator(
        db,
        operator_id=operator["id"],
        is_read=is_read,
        page=page,
        page_size=page_size,
    )
    return {
        "code": 0,
        "message": "success",
        "data": {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    }


@router.put("/alerts/{alert_id}/read")
async def mark_alert_read(
    alert_id: int,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """"""
    success = await alert_mark_read(db, alert_id=alert_id, operator_id=operator["id"])
    if not success:
        raise BizError(4001, "", status_code=404)
    return {"code": 0, "message": "success", "data": None}


@router.put("/alerts/read-all")
async def mark_all_alerts_read(
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """"""
    count = await alert_mark_all_read(db, operator_id=operator["id"])
    return {"code": 0, "message": "success", "data": {"marked_count": count}}


@router.get("/alerts/unread-count")
async def get_unread_count(
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """"""
    count = await alert_get_unread_count(db, operator_id=operator["id"])
    return {"code": 0, "message": "success", "data": {"count": count}}
