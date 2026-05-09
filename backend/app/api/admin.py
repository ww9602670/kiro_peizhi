"""

GET    /admin/operators           
POST   /admin/operators           
PUT    /admin/operators/{id}      
PUT    /admin/operators/{id}/status  /
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import get_db_conn, require_admin
from app.models.db_ops import (
    account_shared_route_list_for_admin,
    account_shared_route_mark_pending,
    audit_log_create,
    alert_create,
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
    shared_market_group_url_exists,
    shared_market_uncovered_url_bind_group,
    shared_market_uncovered_url_get,
    shared_market_uncovered_url_list,
    shared_market_uncovered_url_mark_detecting,
    shared_market_uncovered_url_mark_failed,
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
    SharedMarketRouteInfo,
    SharedMarketUncoveredJoinGroupRequest,
    SharedMarketUncoveredUrlInfo,
    StrategyPermissionUpdate,
)
from app.utils.platform_url import normalize_platform_url
from app.utils.response import BizError

router = APIRouter()
logger = logging.getLogger(__name__)

_ROUTE_STATES = {
    "local",
    "shared_pending",
    "shared",
    "shared_error_local_fallback",
}
_DETECTION_STATUSES = {"untested", "detecting", "success", "failed", "ignored"}
_LEGACY_DETECTION_STATUS_ALIASES = {
    "pending": "untested",
    "matched": "success",
    "review_required": "failed",
}
_RECHECKABLE_DETECTION_STATUSES = {"untested", "failed", "ignored"}


def _normalize_platform_type(value: object) -> str:
    return str(value or "JND28WEB").strip().upper() or "JND28WEB"


def _normalize_detection_status(value: object) -> str:
    raw = str(value or "").strip().lower()
    return _LEGACY_DETECTION_STATUS_ALIASES.get(raw, raw)


def _discovered_shared_group_id(result: object) -> int | None:
    candidate = result[0] if isinstance(result, (tuple, list)) and result else result
    if candidate in (None, ""):
        return None
    try:
        return int(candidate)
    except (TypeError, ValueError):
        return None


def _validate_uncovered_status_param(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    raw = value.strip().lower()
    normalized = _normalize_detection_status(value)
    allowed = _DETECTION_STATUSES | set(_LEGACY_DETECTION_STATUS_ALIASES)
    if normalized not in _DETECTION_STATUSES and raw not in allowed:
        raise BizError(4002, "invalid detection status", status_code=400)
    return raw


def _shared_runtime_from_request(request: Request):
    engine = getattr(request.app.state, "engine", None)
    return getattr(engine, "shared_market_runtime", None)


def _active_admin_ids(rows: list[dict[str, Any]]) -> list[int]:
    return [
        int(row["id"])
        for row in rows
        if row.get("role") == "admin" and row.get("status") == "active"
    ]


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


def _to_shared_market_route_info(row: dict[str, Any]) -> SharedMarketRouteInfo:
    return SharedMarketRouteInfo(
        account_id=row["account_id"],
        operator_id=row.get("operator_id"),
        operator_name=row.get("operator_name"),
        account_name=row.get("account_name"),
        platform_type=_normalize_platform_type(row.get("platform_type")),
        normalized_url=row.get("normalized_url"),
        data_source_state=row.get("data_source_state") or "local",
        shared_group_id=row.get("shared_group_id"),
        shared_group_key=row.get("shared_group_key"),
        pending_shared_group_id=row.get("pending_shared_group_id"),
        pending_shared_group_key=row.get("pending_shared_group_key"),
        handoff_after_issue=row.get("handoff_after_issue"),
        handoff_confirmed_issue=row.get("handoff_confirmed_issue"),
        fallback_reason=row.get("fallback_reason"),
        last_switch_at=row.get("last_switch_at"),
        last_checked_at=row.get("last_checked_at"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


async def _notify_admins_shared_detection_failed(
    db,
    *,
    normalized_url: str,
    platform_type: str,
    account_id: int | None = None,
    failure_reason: str | None = None,
) -> None:
    detail = json.dumps(
        {
            "shared_group_id": None,
            "collector_account_name": None,
            "error_class": "url_detection_failed",
            "error_text": failure_reason or "shared url detection failed",
            "consecutive_error_count": 1,
            "last_success_at": None,
            "dedupe_key": f"shared_url_detection:{platform_type}:{normalized_url}",
            "normalized_url": normalized_url,
            "platform_type": platform_type,
            "account_id": account_id,
        },
        sort_keys=True,
    )
    for operator_id in _active_admin_ids(await operator_list_all(db)):
        await alert_create(
            db,
            operator_id=operator_id,
            type="shared_market_error",
            level="warning",
            title="shared url detection failed",
            detail=detail,
        )


async def _mark_routes_pending_for_url(
    db,
    *,
    normalized_url: str,
    shared_group_id: int,
    platform_type: str | None = None,
) -> int:
    where = [
        "normalized_url=?",
        "data_source_state IN ('local', 'shared_pending', 'shared_error_local_fallback')",
    ]
    params: list[Any] = [normalized_url]
    if platform_type:
        where.append("platform_type=?")
        params.append(_normalize_platform_type(platform_type))

    rows = await (
        await db.execute(
            f"""SELECT account_id, operator_id, platform_type
                  FROM account_shared_market_routes
                 WHERE {" AND ".join(where)}""",
            tuple(params),
        )
    ).fetchall()

    updated = 0
    for row in rows:
        await account_shared_route_mark_pending(
            db,
            account_id=int(row["account_id"]),
            operator_id=row.get("operator_id"),
            platform_type=_normalize_platform_type(row.get("platform_type")),
            normalized_url=normalized_url,
            pending_shared_group_id=shared_group_id,
        )
        updated += 1
    return updated


async def _run_shared_url_detection_task(
    *,
    runtime,
    db,
    record_id: int,
    normalized_url: str,
    platform_type: str,
    account_id: int | None,
    sample_raw_url: str | None,
) -> None:
    try:
        discovery_result = await runtime.discover_and_bind_uncovered_url(
            platform_type=platform_type,
            platform_url=normalized_url,
            account_id=account_id,
            sample_raw_url=sample_raw_url or normalized_url,
        )
        discovered_group_id = _discovered_shared_group_id(discovery_result)
        if discovered_group_id is not None:
            await _mark_routes_pending_for_url(
                db,
                normalized_url=normalized_url,
                platform_type=platform_type,
                shared_group_id=int(discovered_group_id),
            )
            return

        await shared_market_uncovered_url_mark_failed(
            db,
            row_id=record_id,
            failure_reason="no_shared_group_matched",
        )
        await _notify_admins_shared_detection_failed(
            db,
            normalized_url=normalized_url,
            platform_type=platform_type,
            account_id=account_id,
            failure_reason="no_shared_group_matched",
        )
    except Exception as exc:
        logger.exception(
            "shared_url_detection_task_failed record_id=%s normalized_url=%s",
            record_id,
            normalized_url,
        )
        await shared_market_uncovered_url_mark_failed(
            db,
            row_id=record_id,
            failure_reason=str(exc)[:200],
        )
        await _notify_admins_shared_detection_failed(
            db,
            normalized_url=normalized_url,
            platform_type=platform_type,
            account_id=account_id,
            failure_reason=str(exc)[:200],
        )


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
        detection_status=_normalize_detection_status(row.get("detection_status"))
        or "untested",
        detection_attempts=int(row.get("detection_attempts") or 0),
        next_detect_at=row.get("next_detect_at"),
        last_checked_at=row.get("last_checked_at"),
        last_account_id=row.get("last_account_id"),
        last_platform_type=row.get("last_platform_type"),
        sample_raw_url=row.get("sample_raw_url"),
        status=row.get("status") or "pending",
        failure_reason=row.get("failure_reason"),
        detection_error=row.get("detection_error"),
        shared_group_id=row.get("shared_group_id"),
        shared_group_key=row.get("group_key"),
        matched_shared_group_id=row.get("matched_shared_group_id"),
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
                collector_owner_key=row.get("collector_owner_key"),
                collector_health_state=row.get("collector_health_state"),
                collector_last_success_at=row.get("collector_last_success_at"),
                collector_last_error_at=row.get("collector_last_error_at"),
                collector_last_error_class=row.get("collector_last_error_class"),
                collector_last_error=row.get("collector_last_error"),
                collector_consecutive_error_count=row.get(
                    "collector_consecutive_error_count"
                ),
                collector_preheated_at=row.get("collector_preheated_at"),
                collector_alerted_at=row.get("collector_alerted_at"),
                source_status=row.get("source_status"),
                last_error=row.get("last_error"),
                snapshot_issue=row.get("snapshot_issue"),
                snapshot_pre_issue=row.get("snapshot_pre_issue"),
                snapshot_open_result=row.get("snapshot_open_result"),
                snapshot_fetched_at=row.get("snapshot_fetched_at"),
                snapshot_updated_at=row.get("snapshot_updated_at"),
                provider_owner_key=row.get("provider_owner_key"),
                provider_kind=row.get("provider_kind"),
                provider_account_name=row.get("provider_account_name"),
            )
            for row in groups
        ]
    )


@router.get("/admin/shared-market-routes")
async def list_shared_market_routes(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    state: str | None = Query(default=None),
    operator_id: int | None = Query(default=None),
    account_id: int | None = Query(default=None),
    shared_group_id: int | None = Query(default=None),
    admin: dict = Depends(require_admin),
    db=Depends(get_db_conn),
):
    if state is not None and state not in _ROUTE_STATES:
        raise BizError(4002, "invalid route state", status_code=400)

    items, total = await account_shared_route_list_for_admin(
        db,
        page=page,
        page_size=page_size,
        state=state,
        operator_id=operator_id,
        account_id=account_id,
        shared_group_id=shared_group_id,
    )
    paged = PagedData[SharedMarketRouteInfo](
        items=[_to_shared_market_route_info(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )
    return ApiResponse[PagedData[SharedMarketRouteInfo]](data=paged)


@router.get("/admin/shared-market-uncovered-urls")
async def list_shared_market_uncovered_urls(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str | None = Query(default="pending"),
    detection_status: str | None = Query(default=None),
    admin: dict = Depends(require_admin),
    db=Depends(get_db_conn),
):
    filter_status = _validate_uncovered_status_param(detection_status)
    if filter_status is None:
        filter_status = _validate_uncovered_status_param(status)
    items, total = await shared_market_uncovered_url_list(
        db,
        status=filter_status or "",
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
    current_status = _normalize_detection_status(row.get("detection_status")) or "untested"
    if current_status in {"detecting", "success"}:
        raise BizError(4002, "status does not allow ignore", status_code=400)

    review = await shared_market_uncovered_url_set_status(
        db,
        row_id=record_id,
        status="ignored",
        reviewed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        failure_reason="ignored_by_admin",
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

    current_status = _normalize_detection_status(row.get("detection_status")) or "untested"
    if current_status == "detecting":
        raise BizError(4091, "detection already running", status_code=409)
    if current_status not in _RECHECKABLE_DETECTION_STATUSES:
        raise BizError(4002, "status does not allow recheck", status_code=400)

    runtime = _shared_runtime_from_request(request)
    review = await shared_market_uncovered_url_mark_detecting(
        db,
        row_id=record_id,
        checked_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        detecting_owner=f"admin:{admin['id']}",
    )
    if review is None:
        raise BizError(4001, "record not found", status_code=404)

    normalized_url = row.get("normalized_url") or ""
    platform_type = _normalize_platform_type(row.get("last_platform_type"))
    account_id = row.get("last_account_id")
    if runtime is None:
        logger.warning(
            "shared_url_detection_runtime_unavailable record_id=%s normalized_url=%s",
            record_id,
            normalized_url,
        )
    else:
        # P3: 同步等待 detection 完成。原本 fire-and-forget 会让用户看到"已标记"
        # 但其实结果还没出，必须刷新才能看到 matched/failed。
        # 改为 await — 接口可能阻塞数秒（取决于平台探测耗时），但用户拿到结果前不会返回。
        await _run_shared_url_detection_task(
            runtime=runtime,
            db=db,
            record_id=record_id,
            normalized_url=normalized_url,
            platform_type=platform_type,
            account_id=account_id,
            sample_raw_url=row.get("sample_raw_url"),
        )

    # 重新读最新记录返回，让前端立即看到 matched/failed 结果
    refreshed = await shared_market_uncovered_url_get(db, row_id=record_id)
    if refreshed is not None:
        review = refreshed

    await audit_log_create(
        db,
        operator_id=admin["id"],
        action="recheck_shared_market_uncovered_url",
        target_type="shared_market_uncovered_url",
        target_id=record_id,
        detail=json.dumps(
            {"detection_status": (review or {}).get("detection_status") or "detecting"}
        ),
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
        raise BizError(4002, "invalid shared url", status_code=400)

    existing_url = await shared_market_group_url_exists(
        db,
        normalized_url=normalized_url,
    )
    if (
        existing_url is not None
        and existing_url.get("shared_group_id") is not None
        and int(existing_url["shared_group_id"]) != body.shared_group_id
    ):
        raise BizError(4092, "url already belongs to another shared group", status_code=409)

    review = await shared_market_uncovered_url_bind_group(
        db,
        row_id=record_id,
        shared_group_id=body.shared_group_id,
        failure_reason="joined_shared_group_by_admin",
    )
    if review is None:
        raise BizError(4001, "record not found", status_code=404)
    await _mark_routes_pending_for_url(
        db,
        normalized_url=normalized_url,
        platform_type=row.get("last_platform_type"),
        shared_group_id=body.shared_group_id,
    )

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
