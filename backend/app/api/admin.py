"""

GET    /admin/operators           
POST   /admin/operators           
PUT    /admin/operators/{id}      
PUT    /admin/operators/{id}/status  /
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import get_db_conn, require_admin
from app.models.db_ops import (
    audit_log_create,
    operator_strategy_permission_list,
    operator_strategy_permission_set,
    operator_create,
    operator_get_by_id,
    operator_get_by_username,
    operator_list_all,
    operator_list_paged,
    operator_update,
    operator_update_status,
    shared_market_group_get,
    shared_market_group_list,
    shared_market_uncovered_url_bind_group,
    shared_market_uncovered_url_get,
    shared_market_uncovered_url_list,
    shared_market_uncovered_url_set_status,
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
from app.schemas.strategy import (
    OperatorStrategyPermissionInfo,
    SharedMarketGroupInfo,
    SharedMarketUncoveredJoinGroupRequest,
    SharedMarketUncoveredUrlInfo,
    StrategyPermissionUpdate,
)
from app.utils.platform_url import normalize_platform_url
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


def _to_operator_strategy_permission_info(
    *,
    operator: dict,
    allowed_strategy_types: list[str],
) -> OperatorStrategyPermissionInfo:
    return OperatorStrategyPermissionInfo(
        operator_id=operator["id"],
        username=operator["username"],
        allowed_strategy_types=allowed_strategy_types,
    )


@router.get("/admin/operators/{operator_id}/strategy-permissions")
async def list_operator_strategy_permissions(
    operator_id: int,
    admin: dict = Depends(require_admin),
    db=Depends(get_db_conn),
):
    existing = await operator_get_by_id(db, operator_id=operator_id)
    if not existing or existing["role"] != "operator":
        raise BizError(4001, "operator not found", status_code=404)

    allowed_strategy_types = await operator_strategy_permission_list(
        db,
        operator_id=operator_id,
    )
    return ApiResponse[OperatorStrategyPermissionInfo](
        data=_to_operator_strategy_permission_info(
            operator=existing,
            allowed_strategy_types=allowed_strategy_types,
        )
    )


@router.put("/admin/operators/{operator_id}/strategy-permissions")
async def update_operator_strategy_permissions(
    operator_id: int,
    body: StrategyPermissionUpdate,
    request: Request,
    admin: dict = Depends(require_admin),
    db=Depends(get_db_conn),
):
    existing = await operator_get_by_id(db, operator_id=operator_id)
    if not existing or existing["role"] != "operator":
        raise BizError(4001, "operator not found", status_code=404)

    allowed_strategy_types = await operator_strategy_permission_set(
        db,
        operator_id=operator_id,
        strategy_types=list(body.strategy_types),
        created_by=admin["id"],
    )
    if allowed_strategy_types is None:
        raise BizError(4001, "operator not found", status_code=404)

    await audit_log_create(
        db,
        operator_id=admin["id"],
        action="update_operator_strategy_permissions",
        target_type="operator",
        target_id=operator_id,
        detail=json.dumps(
            {
                "operator_id": operator_id,
                "strategy_types": allowed_strategy_types,
            }
        ),
        ip_address=_get_client_ip(request),
    )
    return ApiResponse[OperatorStrategyPermissionInfo](
        data=_to_operator_strategy_permission_info(
            operator=existing,
            allowed_strategy_types=allowed_strategy_types,
        )
    )


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


def _to_shared_market_uncovered_info(row: dict[str, Any]) -> SharedMarketUncoveredUrlInfo:
    return SharedMarketUncoveredUrlInfo(
        id=row["id"],
        normalized_url=row["normalized_url"],
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        hit_count=row["hit_count"],
        detection_status=row.get("detection_status") or "pending",
        last_account_id=row.get("last_account_id"),
        last_platform_type=row.get("last_platform_type"),
        sample_raw_url=row.get("sample_raw_url"),
        status=row.get("status") or "pending",
        failure_reason=row.get("failure_reason"),
        shared_group_id=row.get("shared_group_id"),
        shared_group_key=row.get("group_key"),
    )


@router.get("/admin/shared-market-groups")
async def list_shared_market_groups(
    include_disabled: bool = False,
    admin: dict = Depends(require_admin),
    db=Depends(get_db_conn),
):
    groups = await shared_market_group_list(
        db,
        include_disabled=include_disabled,
    )
    return ApiResponse[list[SharedMarketGroupInfo]](
        data=[
            SharedMarketGroupInfo(
                id=row["id"],
                group_key=row["group_key"],
                enabled=row["enabled"],
                collector_platform_type=row.get("collector_platform_type"),
                collector_account_name=row.get("collector_account_name"),
                primary_url=row.get("primary_url"),
                source_status=row.get("source_status"),
                last_error=row.get("last_error"),
                snapshot_issue=row.get("snapshot_issue"),
                snapshot_pre_issue=row.get("snapshot_pre_issue"),
                snapshot_open_result=row.get("snapshot_open_result"),
                snapshot_fetched_at=row.get("snapshot_fetched_at"),
                snapshot_updated_at=row.get("snapshot_updated_at"),
            )
            for row in groups
        ]
    )


@router.get("/admin/shared-market-uncovered-urls")
async def list_shared_market_uncovered_urls(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str = Query(default="pending"),
    admin: dict = Depends(require_admin),
    db=Depends(get_db_conn),
):
    items, total = await shared_market_uncovered_url_list(
        db,
        status=status,
        page=page,
        page_size=page_size,
    )
    paged = PagedData[SharedMarketUncoveredUrlInfo](
        items=[_to_shared_market_uncovered_info(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )
    return ApiResponse[PagedData[SharedMarketUncoveredUrlInfo]](data=paged)


@router.post("/admin/shared-market-uncovered-urls/{record_id}/ignore")
async def ignore_shared_market_uncovered_url(
    record_id: int,
    request: Request,
    admin: dict = Depends(require_admin),
    db=Depends(get_db_conn),
):
    row = await shared_market_uncovered_url_get(db, row_id=record_id)
    if row is None:
        raise BizError(4001, "record not found", status_code=404)
    review = await shared_market_uncovered_url_set_status(
        db,
        row_id=record_id,
        status="ignored",
        reviewed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        failure_reason="管理员手动忽略",
    )
    if review is None:
        raise BizError(4001, "record not found", status_code=404)

    await audit_log_create(
        db,
        operator_id=admin["id"],
        action="ignore_shared_market_uncovered_url",
        target_type="shared_market_uncovered_url",
        target_id=record_id,
        detail='{"status":"ignored"}',
        ip_address=_get_client_ip(request),
    )
    return ApiResponse[SharedMarketUncoveredUrlInfo](
        data=_to_shared_market_uncovered_info(review)
    )


@router.post("/admin/shared-market-uncovered-urls/{record_id}/recheck")
async def recheck_shared_market_uncovered_url(
    record_id: int,
    request: Request,
    admin: dict = Depends(require_admin),
    db=Depends(get_db_conn),
):
    row = await shared_market_uncovered_url_get(db, row_id=record_id)
    if row is None:
        raise BizError(4001, "record not found", status_code=404)

    review = await shared_market_uncovered_url_set_status(
        db,
        row_id=record_id,
        status="pending",
        reviewed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        failure_reason="管理员已重新检测",
    )
    if review is None:
        raise BizError(4001, "record not found", status_code=404)

    await audit_log_create(
        db,
        operator_id=admin["id"],
        action="recheck_shared_market_uncovered_url",
        target_type="shared_market_uncovered_url",
        target_id=record_id,
        detail='{"status":"pending"}',
        ip_address=_get_client_ip(request),
    )
    return ApiResponse[SharedMarketUncoveredUrlInfo](
        data=_to_shared_market_uncovered_info(review)
    )


@router.post("/admin/shared-market-uncovered-urls/{record_id}/join-shared-group")
async def join_shared_market_uncovered_url(
    record_id: int,
    request: Request,
    body: SharedMarketUncoveredJoinGroupRequest,
    admin: dict = Depends(require_admin),
    db=Depends(get_db_conn),
):
    row = await shared_market_uncovered_url_get(db, row_id=record_id)
    if row is None:
        raise BizError(4001, "record not found", status_code=404)

    group = await shared_market_group_get(db, shared_group_id=body.shared_group_id)
    if group is None:
        raise BizError(4002, "shared group not found", status_code=404)

    if group.get("enabled", 1) == 0:
        raise BizError(4002, "shared group is disabled", status_code=400)

    normalized_url = normalize_platform_url(row.get("normalized_url"))
    if not normalized_url or normalized_url != row.get("normalized_url"):
        raise BizError(4002, "共享网址格式错误，不能加入共享组", status_code=400)

    review = await shared_market_uncovered_url_bind_group(
        db,
        row_id=record_id,
        shared_group_id=body.shared_group_id,
        failure_reason="管理员手动加入共享组",
    )
    if review is None:
        raise BizError(4001, "record not found", status_code=404)

    await audit_log_create(
        db,
        operator_id=admin["id"],
        action="join_shared_market_uncovered_url_group",
        target_type="shared_market_uncovered_url",
        target_id=record_id,
        detail=json.dumps(
            {"shared_group_id": body.shared_group_id, "normalized_url": row["normalized_url"]}
        ),
        ip_address=_get_client_ip(request),
    )
    return ApiResponse[SharedMarketUncoveredUrlInfo](
        data=_to_shared_market_uncovered_info(review)
    )
