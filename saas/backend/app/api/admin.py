"""

GET    /admin/operators           
POST   /admin/operators           
PUT    /admin/operators/{id}      
PUT    /admin/operators/{id}/status  /
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import get_db_conn, require_admin
from app.models.db_ops import (
    audit_log_create,
    operator_create,
    operator_get_by_id,
    operator_get_by_username,
    operator_list_all,
    operator_list_paged,
    operator_update,
    operator_update_status,
    strategy_list_by_operator,
)
from app.schemas.common import ApiResponse, PagedData
from app.engine.kill_switch import get_global_kill, set_global_kill
from app.schemas.dashboard import AdminDashboard, OperatorSummary
from app.schemas.kill_switch import GlobalKillSwitchInfo, GlobalKillSwitchRequest
from app.schemas.operator import (
    OperatorCreate,
    OperatorInfo,
    OperatorUpdate,
    StatusUpdate,
)
from app.utils.response import BizError

router = APIRouter()


def _to_operator_info(row: dict) -> OperatorInfo:
    """ DB  OperatorInfo schema"""
    return OperatorInfo(
        id=row["id"],
        username=row["username"],
        role=row["role"],
        status=row["status"],
        max_accounts=row["max_accounts"],
        expire_date=row.get("expire_date"),
        created_at=row["created_at"],
    )


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.get("/admin/operators")
async def list_operators(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    admin: dict = Depends(require_admin),
    db=Depends(get_db_conn),
):
    """"""
    items, total = await operator_list_paged(db, page=page, page_size=page_size)
    paged = PagedData[OperatorInfo](
        items=[_to_operator_info(r) for r in items],
        total=total,
        page=page,
        page_size=page_size,
    )
    return ApiResponse[PagedData[OperatorInfo]](data=paged)


@router.post("/admin/operators")
async def create_operator(
    body: OperatorCreate,
    request: Request,
    admin: dict = Depends(require_admin),
    db=Depends(get_db_conn),
):
    """"""
    # 
    existing = await operator_get_by_username(db, username=body.username)
    if existing:
        raise BizError(4002, f" '{body.username}' ", status_code=409)

    row = await operator_create(
        db,
        username=body.username,
        password=body.password,
        max_accounts=body.max_accounts,
        expire_date=body.expire_date,
        created_by=admin["id"],
    )

    # 
    ip = _get_client_ip(request)
    await audit_log_create(
        db,
        operator_id=admin["id"],
        action="create_operator",
        target_type="operator",
        target_id=row["id"],
        detail=json.dumps({"username": body.username, "max_accounts": body.max_accounts}),
        ip_address=ip,
    )

    return ApiResponse[OperatorInfo](data=_to_operator_info(row))


@router.put("/admin/operators/{operator_id}")
async def update_operator(
    operator_id: int,
    body: OperatorUpdate,
    request: Request,
    admin: dict = Depends(require_admin),
    db=Depends(get_db_conn),
):
    """max_accounts / expire_date"""
    existing = await operator_get_by_id(db, operator_id=operator_id)
    if not existing:
        raise BizError(4001, "", status_code=404)

    # 
    update_fields: dict = {}
    if body.max_accounts is not None:
        update_fields["max_accounts"] = body.max_accounts
    if body.expire_date is not None:
        update_fields["expire_date"] = body.expire_date

    if not update_fields:
        return ApiResponse[OperatorInfo](data=_to_operator_info(existing))

    row = await operator_update(db, operator_id=operator_id, **update_fields)

    # 
    ip = _get_client_ip(request)
    await audit_log_create(
        db,
        operator_id=admin["id"],
        action="update_operator",
        target_type="operator",
        target_id=operator_id,
        detail=json.dumps(update_fields),
        ip_address=ip,
    )

    return ApiResponse[OperatorInfo](data=_to_operator_info(row))


@router.put("/admin/operators/{operator_id}/status")
async def update_operator_status(
    operator_id: int,
    body: StatusUpdate,
    request: Request,
    admin: dict = Depends(require_admin),
    db=Depends(get_db_conn),
):
    """/"""
    existing = await operator_get_by_id(db, operator_id=operator_id)
    if not existing:
        raise BizError(4001, "", status_code=404)

    # 
    if operator_id == admin["id"]:
        raise BizError(4002, "", status_code=409)

    row = await operator_update_status(db, operator_id=operator_id, status=body.status)

    # 
    ip = _get_client_ip(request)
    action = "disable_operator" if body.status == "disabled" else "enable_operator"
    await audit_log_create(
        db,
        operator_id=admin["id"],
        action=action,
        target_type="operator",
        target_id=operator_id,
        detail=json.dumps({"old_status": existing["status"], "new_status": body.status}),
        ip_address=ip,
    )

    return ApiResponse[OperatorInfo](data=_to_operator_info(row))


@router.post("/admin/kill-switch")
async def global_kill_switch(
    body: GlobalKillSwitchRequest,
    request: Request,
    admin: dict = Depends(require_admin),
    db=Depends(get_db_conn),
):
    """

    RiskController 
    EngineManager  Worker Phase 10.2 
    """
    set_global_kill(body.enabled)

    # 
    ip = _get_client_ip(request)
    action = "global_kill_switch_on" if body.enabled else "global_kill_switch_off"
    await audit_log_create(
        db,
        operator_id=admin["id"],
        action=action,
        target_type="system",
        target_id=None,
        detail=json.dumps({"enabled": body.enabled}),
        ip_address=ip,
    )

    return ApiResponse[GlobalKillSwitchInfo](
        data=GlobalKillSwitchInfo(enabled=get_global_kill())
    )


@router.get("/admin/dashboard")
async def admin_dashboard(
    admin: dict = Depends(require_admin),
    db=Depends(get_db_conn),
):
    """"""
    from datetime import datetime, timezone, timedelta

    operators = await operator_list_all(db)
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

    summaries: list[OperatorSummary] = []
    active_count = 0

    for op in operators:
        if op["status"] == "active":
            active_count += 1

        strategies = await strategy_list_by_operator(db, operator_id=op["id"])
        daily_pnl = sum(
            s["daily_pnl"] for s in strategies
            if s.get("daily_pnl_date") == today
        ) / 100
        total_pnl = sum(s["total_pnl"] for s in strategies) / 100
        running_count = sum(1 for s in strategies if s["status"] == "running")

        summaries.append(OperatorSummary(
            id=op["id"],
            username=op["username"],
            status=op["status"],
            daily_pnl=daily_pnl,
            total_pnl=total_pnl,
            running_strategies=running_count,
        ))

    dashboard = AdminDashboard(
        total_operators=len(operators),
        active_operators=active_count,
        operator_summaries=summaries,
    )
    return ApiResponse[AdminDashboard](data=dashboard)
