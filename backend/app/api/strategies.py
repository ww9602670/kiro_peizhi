"""

GET    /strategies               
POST   /strategies               
PUT    /strategies/{id}           stopped 
DELETE /strategies/{id}           stopped 
POST   /strategies/{id}/start    
POST   /strategies/{id}/pause    
POST   /strategies/{id}/stop     
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_current_operator, get_db_conn
from app.models.db_ops import (
    account_get_by_id,
    account_platform_capability_list_by_run,
    account_verification_run_get_effective,
    account_verification_run_get_latest,
    account_verification_run_get_latest_completed,
    operator_strategy_permission_list,
    strategy_create,
    strategy_delete,
    strategy_get_by_id,
    strategy_list_by_operator,
    strategy_update,
    strategy_update_status,
)
from app.schemas.common import ApiResponse
from app.schemas.strategy import (
    StrategyCreate,
    StrategyInfo,
    StrategyUpdate,
    derive_strategy_permission_type,
    has_dw3_prefix,
    is_dw3_group_play_code,
    normalize_dw3_group_play_code,
    validate_state_transition,
)
from app.utils.strategy_timing import (
    BET_TIMING_MAX,
    BET_TIMING_MIN,
    build_candidate_from_row,
    build_timing_candidate,
    is_wave_strategy_type,
    normalize_wave_strategy_play_code,
    resolve_timing_conflicts,
    summarize_timing_conflicts,
)
from app.utils.response import BizError
from app.utils.key_code_map import get_key_code_name
from app.utils.omission_random import (
    AI_RANDOM_WEIGHT_MODE,
    OMISSION_WEIGHT_MODE,
    build_omission_play_code,
    is_ai_same_random_type,
    is_ai_random_type,
    is_random_pick_type,
    normalize_strategy_config,
    omission_play_code_name,
    parse_omission_play_code,
)
from app.utils.luckysb_play_codes import (
    LUCKYSB_PLATFORM_TYPE,
    get_luckysb_play_code_name,
    validate_luckysb_strategy,
)

router = APIRouter()

DW3_PLAY_CODE_LABELS: dict[str, str] = {
    "DW3_BS_BBB": "大大大",
    "DW3_BS_BBS": "大大小",
    "DW3_BS_BSB": "大小大",
    "DW3_BS_BSS": "大小小",
    "DW3_BS_SBB": "小大大",
    "DW3_BS_SBS": "小大小",
    "DW3_BS_SSB": "小小大",
    "DW3_BS_SSS": "小小小",
    "DW3_OE_OOO": "单单单",
    "DW3_OE_OOE": "单单双",
    "DW3_OE_OEO": "单双单",
    "DW3_OE_OEE": "单双双",
    "DW3_OE_EOO": "双单单",
    "DW3_OE_EOE": "双单双",
    "DW3_OE_EEO": "双双单",
    "DW3_OE_EEE": "双双双",
}


#   

def _yuan_to_fen(yuan: float) -> int:
    """  """
    return int(yuan * 100)


def _fen_to_yuan(fen: int) -> float:
    """  """
    return fen / 100


def _fen_to_yuan_optional(fen: Optional[int]) -> Optional[float]:
    """  """
    return fen / 100 if fen is not None else None


def _get_play_code_name(platform_type: str, play_code: str) -> str:
    if platform_type == LUCKYSB_PLATFORM_TYPE:
        return get_luckysb_play_code_name(play_code)
    if any(code.strip().upper().startswith("OMR_") for code in play_code.split(",")):
        return omission_play_code_name(play_code)
    if is_dw3_group_play_code(play_code):
        return ", ".join(DW3_PLAY_CODE_LABELS.get(c, c) for c in play_code.split(","))
    return ", ".join(get_key_code_name(c) for c in play_code.split(","))


def _validate_luckysb_or_raise(type_: str, play_code: str) -> None:
    try:
        validate_luckysb_strategy(type_, play_code)
    except ValueError as exc:
        raise BizError(1002, str(exc), status_code=400)


def _normalize_strategy_platform_type(platform_type: str | None) -> str:
    normalized = (platform_type or "").strip().upper()
    return normalized or "JND28WEB"


def _is_truthy_flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


def _parse_allowed_platform_types(value: object) -> list[str]:
    if value is None:
        return []
    raw_items: list[object]
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError:
            raw_items = [item.strip() for item in stripped.split(",")]
        else:
            raw_items = decoded if isinstance(decoded, list) else [decoded]
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = [value]

    allowed: list[str] = []
    for raw in raw_items:
        normalized = str(raw or "").strip().upper()
        if normalized not in {"JND28WEB", "JND282", "LUCKYSB"}:
            continue
        if normalized not in allowed:
            allowed.append(normalized)
    return allowed


def _build_summary_status_reason_from_capabilities(capabilities: list[dict]) -> str | None:
    if not capabilities:
        return "not_verified"
    if any(item.get("verify_status") == "supported" for item in capabilities):
        return None
    if any(item.get("verify_status") == "unsupported" for item in capabilities):
        return "unsupported_platform"
    if any(item.get("verify_status") == "error" for item in capabilities):
        return "verification_error"
    return "not_verified"


async def _load_account_with_strategy_gate_context(
    db,
    *,
    account_id: int,
    operator_id: int,
) -> dict | None:
    account = await account_get_by_id(db, account_id=account_id, operator_id=operator_id)
    if not account:
        return None

    latest_run = await account_verification_run_get_latest(db, account_id=account_id)
    latest_completed_run = await account_verification_run_get_latest_completed(
        db, account_id=account_id
    )
    effective_run = await account_verification_run_get_effective(db, account_id=account_id)
    capability_run = effective_run or latest_completed_run

    capabilities: list[dict] = []
    allowed_strategy_platform_types: list[str] = []
    if capability_run:
        raw_capabilities = await account_platform_capability_list_by_run(
            db,
            verification_run_id=capability_run["id"],
        )
        for item in raw_capabilities:
            normalized_platform_type = _normalize_strategy_platform_type(
                item.get("platform_type")
            )
            verify_status = str(item.get("verify_status") or "")
            capability = {
                "platform_type": normalized_platform_type,
                "verify_status": verify_status,
                "market_state": item.get("market_state"),
                "detected_issue": item.get("detected_issue"),
                "odds_synced": bool(item.get("odds_synced")),
                "odds_message": item.get("odds_message"),
                "last_verified_at": item.get("last_verified_at"),
            }
            capabilities.append(capability)
            if (
                verify_status == "supported"
                and normalized_platform_type not in allowed_strategy_platform_types
            ):
                allowed_strategy_platform_types.append(normalized_platform_type)

    verification_stale = bool(
        latest_completed_run and int(latest_completed_run.get("stale") or 0) == 1
    )

    enriched = dict(account)
    enriched["latest_verification_run_id"] = latest_run["id"] if latest_run else None
    enriched["effective_verification_run_id"] = capability_run["id"] if capability_run else None
    enriched["verification_stale"] = verification_stale
    enriched["allowed_strategy_platform_types"] = allowed_strategy_platform_types
    enriched["platform_capabilities"] = capabilities
    enriched["summary_status_reason"] = _build_summary_status_reason_from_capabilities(
        capabilities
    )
    return enriched


def _get_effective_verification_run_id(account: dict) -> int | None:
    raw = account.get("effective_verification_run_id")
    if raw is None:
        return None
    try:
        run_id = int(raw)
    except (TypeError, ValueError):
        return None
    return run_id if run_id > 0 else None


def _validate_account_platform_gate_or_raise(
    *,
    account: dict,
    strategy_platform_type: str,
    error_code: int,
) -> str:
    normalized = _normalize_strategy_platform_type(strategy_platform_type)
    effective_run_id = _get_effective_verification_run_id(account)
    if effective_run_id is None:
        raise BizError(
            error_code,
            "effective_verification_run_id is missing; verify account before strategy operations",
            status_code=400,
        )
    allowed = _parse_allowed_platform_types(account.get("allowed_strategy_platform_types"))
    if normalized not in allowed:
        raise BizError(
            error_code,
            (
                f"platform_type={normalized} is not allowed by "
                f"effective_verification_run_id={effective_run_id}"
            ),
            status_code=400,
        )
    return normalized


def _validate_strategy_platform_type_for_account(
    strategy_platform_type: str,
    account: dict,
) -> str:
    return _validate_account_platform_gate_or_raise(
        account=account,
        strategy_platform_type=strategy_platform_type,
        error_code=1002,
    )


async def _ensure_strategy_permission_or_raise(
    db,
    *,
    operator_id: int,
    strategy_type: str,
    play_code: str,
) -> str:
    try:
        permission_type = derive_strategy_permission_type(strategy_type, play_code)
    except ValueError as exc:
        raise BizError(1002, str(exc), status_code=400)

    allowed = await operator_strategy_permission_list(
        db,
        operator_id=operator_id,
    )
    if permission_type not in allowed:
        raise BizError(
            4003,
            "当前操作者账号暂未开通该策略，请联系管理员",
            status_code=403,
        )
    return permission_type


async def _filter_rows_by_strategy_permissions(
    db,
    *,
    operator_id: int,
    rows: list[dict],
) -> list[dict]:
    allowed = set(await operator_strategy_permission_list(
        db,
        operator_id=operator_id,
    ))
    visible: list[dict] = []
    for row in rows:
        try:
            permission_type = derive_strategy_permission_type(
                str(row.get("type") or ""),
                str(row.get("play_code") or ""),
            )
        except ValueError:
            continue
        if permission_type in allowed:
            visible.append(row)
    return visible


def _is_dw3_timing_conflict_exempt(play_code: str) -> bool:
    """Policy A: DW3 does not join generic save-time timing auto-allocation."""
    return is_dw3_group_play_code(play_code)


def _raise_timing_conflict(conflicts, *, reason: str | None = None) -> None:
    detail = summarize_timing_conflicts(conflicts)
    detail["reason"] = reason or "BET_TIMING_CONFLICT"
    detail["bet_timing_window"] = {
        "min": BET_TIMING_MIN,
        "max": BET_TIMING_MAX,
    }
    raise BizError(
        1003,
        "bet_timing conflicts with same-direction strategies; keep at least 20s gap",
        status_code=400,
        data=detail,
    )


async def _resolve_bet_timing_for_save(
    db,
    *,
    operator_id: int,
    candidate,
    statuses: set[str] | None = None,
) -> int:
    if _is_dw3_timing_conflict_exempt(candidate.play_code):
        return candidate.bet_timing

    if statuses is None:
        statuses = {"running"}

    rows = await strategy_list_by_operator(db, operator_id=operator_id)
    others = []
    for row in rows:
        if row.get("account_id") != candidate.account_id:
            continue
        if candidate.strategy_id is not None and row.get("id") == candidate.strategy_id:
            continue
        if statuses is not None and row.get("status") not in statuses:
            continue
        if _is_dw3_timing_conflict_exempt(str(row.get("play_code") or "")):
            continue
        others.append(build_candidate_from_row(row))

    resolution = resolve_timing_conflicts(candidate, others)
    if resolution.resolved_bet_timing is None:
        _raise_timing_conflict(resolution.conflicts, reason=resolution.reason)
    return int(resolution.resolved_bet_timing)


#  DB   StrategyInfo  

def _to_strategy_info(row: dict) -> StrategyInfo:
    """ DB  StrategyInfo schema"""
    # martin_sequence: JSON   list[float]
    ms_raw = row.get("martin_sequence")
    martin_sequence = json.loads(ms_raw) if ms_raw else None
    config_raw = row.get("strategy_config")
    strategy_config = None
    if config_raw:
        try:
            strategy_config = json.loads(config_raw) if isinstance(config_raw, str) else config_raw
        except (TypeError, json.JSONDecodeError):
            strategy_config = None

    # daily_pnl 日期检查：如果 daily_pnl_date 不是今天，返回 0
    from datetime import datetime, timezone, timedelta
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    daily_pnl_raw = row["daily_pnl"] if row.get("daily_pnl_date") == today else 0

    platform_type = row.get("platform_type", "JND28WEB")

    bust_events = None
    if row.get("type") == "random_martin" and strategy_config:
        rt = strategy_config.get("runtime_state", {})
        groups = rt.get("groups", [])
        busted = [
            {"group_id": i, "bust_count": g["bust_count"]}
            for i, g in enumerate(groups)
            if isinstance(g, dict) and g.get("bust_count", 0) > 0
        ]
        if busted:
            bust_events = busted

    return StrategyInfo(
        id=row["id"],
        account_id=row["account_id"],
        name=row["name"],
        type=row["type"],
        play_code=row["play_code"],
        play_code_name=_get_play_code_name(platform_type, row["play_code"]),
        base_amount=_fen_to_yuan(row["base_amount"]),
        martin_sequence=martin_sequence,
        strategy_config=strategy_config,
        bet_timing=row["bet_timing"],
        simulation=bool(row["simulation"]),
        status=row["status"],
        martin_level=row["martin_level"],
        stop_loss=_fen_to_yuan_optional(row.get("stop_loss")),
        take_profit=_fen_to_yuan_optional(row.get("take_profit")),
        daily_pnl=_fen_to_yuan(daily_pnl_raw),
        total_pnl=_fen_to_yuan(row["total_pnl"]),
        gate_window_issues=row.get("gate_window_issues"),
        platform_type=platform_type,
        bust_events=bust_events,
    )


@router.get("/strategies")
async def list_strategies(
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """"""
    rows = await strategy_list_by_operator(db, operator_id=operator["id"])
    rows = await _filter_rows_by_strategy_permissions(
        db,
        operator_id=operator["id"],
        rows=rows,
    )
    items = [_to_strategy_info(r) for r in rows]
    return ApiResponse[list[StrategyInfo]](data=items)


@router.post("/strategies")
async def create_strategy(
    body: StrategyCreate,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """


    1. account_id
    2. schema
    3.
    """
    # 1.  account
    account = await _load_account_with_strategy_gate_context(
        db,
        account_id=body.account_id,
        operator_id=operator["id"],
    )
    if not account:
        raise BizError(4001, "", status_code=404)
    strategy_platform_type = _validate_strategy_platform_type_for_account(
        body.platform_type,
        account,
    )
    play_code = body.play_code

    # random_martin: 验证 plan_set_id 归属
    if body.type == "random_martin" and body.strategy_config:
        plan_set_id = body.strategy_config.get("plan_set_id")
        if plan_set_id:
            ps_row = await (
                await db.execute(
                    "SELECT id FROM random_plan_sets WHERE id=? AND operator_id=?",
                    (plan_set_id, operator["id"]),
                )
            ).fetchone()
            if not ps_row:
                raise BizError(4001, f"plan_set_id={plan_set_id} 不存在或不属于当前操作者")

    strategy_config_json = (
        json.dumps(body.strategy_config, ensure_ascii=False)
        if body.strategy_config is not None
        else None
    )
    if strategy_platform_type == LUCKYSB_PLATFORM_TYPE:
        _validate_luckysb_or_raise(body.type, play_code)

    await _ensure_strategy_permission_or_raise(
        db,
        operator_id=operator["id"],
        strategy_type=body.type,
        play_code=play_code,
    )

    resolved_bet_timing = await _resolve_bet_timing_for_save(
        db,
        operator_id=operator["id"],
        candidate=build_timing_candidate(
            strategy_id=None,
            account_id=body.account_id,
            strategy_type=body.type,
            play_code=play_code,
            bet_timing=body.bet_timing,
        ),
    )

    martin_seq_json = (
        json.dumps(body.martin_sequence) if body.martin_sequence else None
    )

    row = await strategy_create(
        db,
        operator_id=operator["id"],
        account_id=body.account_id,
        name=body.name,
        type=body.type,
        play_code=play_code,
        base_amount=_yuan_to_fen(body.base_amount),
        martin_sequence=martin_seq_json,
        strategy_config=strategy_config_json,
        bet_timing=resolved_bet_timing,
        simulation=1 if body.simulation else 0,
        stop_loss=_yuan_to_fen(body.stop_loss) if body.stop_loss is not None else None,
        take_profit=_yuan_to_fen(body.take_profit) if body.take_profit is not None else None,
        gate_window_issues=body.gate_window_issues,
        platform_type=strategy_platform_type,
    )

    return ApiResponse[StrategyInfo](data=_to_strategy_info(row))


@router.put("/strategies/{strategy_id}")
async def update_strategy(
    strategy_id: int,
    body: StrategyUpdate,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """ stopped """
    existing = await strategy_get_by_id(
        db, strategy_id=strategy_id, operator_id=operator["id"]
    )
    if not existing:
        raise BizError(4001, "", status_code=404)
    account = await _load_account_with_strategy_gate_context(
        db,
        account_id=existing["account_id"],
        operator_id=operator["id"],
    )
    if not account:
        raise BizError(4001, "", status_code=404)

    if existing["status"] != "stopped":
        raise BizError(4003, "", status_code=400)

    # 
    update_fields: dict = {}
    if body.name is not None:
        update_fields["name"] = body.name
    if body.base_amount is not None:
        update_fields["base_amount"] = _yuan_to_fen(body.base_amount)
    existing_platform_type = _normalize_strategy_platform_type(existing.get("platform_type"))
    requested_platform_type = (
        _validate_strategy_platform_type_for_account(body.platform_type, account)
        if body.platform_type is not None
        else existing_platform_type
    )
    if body.play_code is not None:
        if requested_platform_type == LUCKYSB_PLATFORM_TYPE:
            update_fields["play_code"] = body.play_code
        elif is_random_pick_type(existing["type"]):
            try:
                categories = parse_omission_play_code(body.play_code)
                if is_ai_same_random_type(existing["type"]) and "sum" in categories:
                    raise ValueError("sum category is not supported")
                update_fields["play_code"] = build_omission_play_code(categories)
            except ValueError as exc:
                raise BizError(1002, str(exc), status_code=400)
        elif is_wave_strategy_type(existing["type"]):
            try:
                update_fields["play_code"] = normalize_wave_strategy_play_code(
                    existing["type"], body.play_code
                )
            except ValueError as exc:
                raise BizError(1002, str(exc), status_code=400)
        elif existing["type"] in ("flat", "martin") and (
            has_dw3_prefix(body.play_code) or is_dw3_group_play_code(existing["play_code"])
        ):
            try:
                update_fields["play_code"] = normalize_dw3_group_play_code(body.play_code)
            except ValueError as exc:
                raise BizError(1002, str(exc), status_code=400)
        else:
                raise BizError(
                    1002,
                    "play_code update is only allowed for wave strategies or DW3 flat/martin",
                    status_code=400,
                )
    if body.martin_sequence is not None:
        # 
        for v in body.martin_sequence:
            if v <= 0:
                raise BizError(1002, " 0", status_code=400)
        update_fields["martin_sequence"] = json.dumps(body.martin_sequence)
    if body.strategy_config is not None:
        if not is_random_pick_type(existing["type"]):
            raise BizError(1002, "strategy_config is only supported for random pick strategies", status_code=400)
        try:
            weight_mode = (
                AI_RANDOM_WEIGHT_MODE
                if is_ai_random_type(existing["type"])
                else OMISSION_WEIGHT_MODE
            )
            normalized_config = normalize_strategy_config(
                body.strategy_config,
                weight_mode=weight_mode,
                allow_sum=not is_ai_same_random_type(existing["type"]),
            )
        except ValueError as exc:
            raise BizError(1002, str(exc), status_code=400)
        update_fields["strategy_config"] = json.dumps(normalized_config, ensure_ascii=False)
        update_fields["play_code"] = build_omission_play_code(normalized_config["categories"])
    if body.bet_timing is not None:
        update_fields["bet_timing"] = body.bet_timing
    if body.simulation is not None:
        update_fields["simulation"] = 1 if body.simulation else 0
    if body.stop_loss is not None:
        update_fields["stop_loss"] = _yuan_to_fen(body.stop_loss)
    if body.take_profit is not None:
        update_fields["take_profit"] = _yuan_to_fen(body.take_profit)
    if body.gate_window_issues is not None:
        update_fields["gate_window_issues"] = body.gate_window_issues
    if body.platform_type is not None:
        update_fields["platform_type"] = requested_platform_type

    if not update_fields:
        return ApiResponse[StrategyInfo](data=_to_strategy_info(existing))

    final_platform_type = update_fields.get("platform_type", existing_platform_type)
    final_play_code = update_fields.get("play_code", existing["play_code"])
    final_gate_window_issues = update_fields.get(
        "gate_window_issues", existing.get("gate_window_issues")
    )
    if final_platform_type == LUCKYSB_PLATFORM_TYPE:
        _validate_luckysb_or_raise(existing["type"], final_play_code)
    if is_dw3_group_play_code(final_play_code) and final_gate_window_issues is None:
        raise BizError(1002, "gate_window_issues is required for DW3 play_code", status_code=400)

    await _ensure_strategy_permission_or_raise(
        db,
        operator_id=operator["id"],
        strategy_type=existing["type"],
        play_code=final_play_code,
    )

    candidate = build_timing_candidate(
        strategy_id=existing["id"],
        account_id=existing["account_id"],
        strategy_type=existing["type"],
        play_code=update_fields.get("play_code", existing["play_code"]),
        bet_timing=update_fields.get("bet_timing", existing["bet_timing"]),
    )
    resolved_bet_timing = await _resolve_bet_timing_for_save(
        db,
        operator_id=operator["id"],
        candidate=candidate,
    )
    if resolved_bet_timing != candidate.bet_timing:
        update_fields["bet_timing"] = resolved_bet_timing

    row = await strategy_update(
        db, strategy_id=strategy_id, operator_id=operator["id"], **update_fields
    )
    return ApiResponse[StrategyInfo](data=_to_strategy_info(row))


@router.delete("/strategies/{strategy_id}")
async def delete_strategy(
    strategy_id: int,
    force: bool = False,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """Soft-delete a stopped strategy while preserving order history."""
    del force  # accepted for backward-compatible clients; no hard deletion is performed.
    existing = await strategy_get_by_id(
        db, strategy_id=strategy_id, operator_id=operator["id"]
    )
    if not existing:
        raise BizError(4001, "策略不存在", status_code=404)

    if existing["status"] != "stopped":
        raise BizError(4003, "只能删除已停止的策略", status_code=400)

    deleted = await strategy_delete(
        db, strategy_id=strategy_id, operator_id=operator["id"]
    )

    if not deleted:
        raise BizError(4001, "策略不存在", status_code=404)

    return ApiResponse(data=None)


#   

async def _transition_strategy(
    strategy_id: int,
    target_status: str,
    operator: dict,
    db,
    request: Request,
) -> StrategyInfo:
    """"""
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info(
        " strategy_id=%d current_status=? target_status=%s",
        strategy_id,
        target_status,
    )
    
    existing = await strategy_get_by_id(
        db, strategy_id=strategy_id, operator_id=operator["id"]
    )
    if not existing:
        raise BizError(4001, "", status_code=404)

    current = existing["status"]
    logger.info(
        " strategy_id=%d current_status=%s target_status=%s",
        strategy_id,
        current,
        target_status,
    )
    
    if not validate_state_transition(current, target_status):
        raise BizError(
            4003,
            f": {current}  {target_status}",
            status_code=400,
        )

    if target_status == "running":
        resolved_bet_timing = await _resolve_bet_timing_for_save(
            db,
            operator_id=operator["id"],
            candidate=build_candidate_from_row(existing),
            statuses={"running"},
        )
        if resolved_bet_timing != existing["bet_timing"]:
            existing = await strategy_update(
                db,
                strategy_id=strategy_id,
                operator_id=operator["id"],
                bet_timing=resolved_bet_timing,
            )

    #  EngineManager
    try:
        engine = getattr(request.app.state, "engine", None)
        if engine is None:
            logger.error(" Engine is None!")
            raise BizError(5001, "", status_code=500)
        logger.info(" Engine ")
    except Exception as e:
        logger.exception(f"  Engine : {e}")
        raise BizError(5001, f": {type(e).__name__}", status_code=500)
    
    #  Worker
    if target_status == "running":
        logger.info("  Worker...")
        
        # 
        account = await _load_account_with_strategy_gate_context(
            db,
            account_id=existing["account_id"],
            operator_id=operator["id"],
        )
        if not account:
            logger.error(" account_id=%d", existing["account_id"])
            raise BizError(4001, "", status_code=404)
        
        logger.info(
            " account_id=%d account_name=%s",
            account["id"],
            account["account_name"],
        )
        
        strategy_platform_type = _normalize_strategy_platform_type(existing.get("platform_type"))
        _validate_account_platform_gate_or_raise(
            account=account,
            strategy_platform_type=strategy_platform_type,
            error_code=4002,
        )
        await _ensure_strategy_permission_or_raise(
            db,
            operator_id=operator["id"],
            strategy_type=existing["type"],
            play_code=existing["play_code"],
        )
        all_strategies = await strategy_list_by_operator(db, operator_id=operator["id"])
        running_strategies = [
            s for s in all_strategies
            if s.get("account_id") == existing["account_id"]
            and s.get("status") == "running"
            and _normalize_strategy_platform_type(s.get("platform_type")) == strategy_platform_type
        ]
        if not any(int(s.get("id")) == int(strategy_id) for s in running_strategies):
            running_strategies.append({**existing, "status": "running"})
        
        logger.info(
            "  running account_id=%d count=%d",
            account["id"],
            len(running_strategies),
        )
        
        #  Worker
        try:
            logger.info("  engine.start_worker...")
            # 优先使用策略的 platform_type，回退到账号的 platform_type
            await engine.start_worker(
                operator_id=operator["id"],
                account_id=account["id"],
                account_name=account["account_name"],
                password=account["password"],
                platform_type=strategy_platform_type,
                platform_url=account.get("platform_url"),
                strategies=running_strategies,
            )
            logger.info(" engine.start_worker ")
        except Exception as e:
            logger.exception(f"  Worker : {e}")
            raise BizError(5001, f" Worker : {str(e)}", status_code=500)
    
    #  Worker
    elif target_status == "stopped":
        logger.info("  Worker...")
        
        account = await account_get_by_id(
            db, account_id=existing["account_id"], operator_id=operator["id"]
        )
        if not account:
            raise BizError(4001, "", status_code=404)
        strategy_platform_type = _normalize_strategy_platform_type(existing.get("platform_type"))
        all_strategies = await strategy_list_by_operator(db, operator_id=operator["id"])
        running_strategies = [
            s for s in all_strategies
            if s.get("account_id") == existing["account_id"]
            and s.get("status") == "running"
            and s.get("id") != strategy_id
            and _normalize_strategy_platform_type(s.get("platform_type")) == strategy_platform_type
        ]
        
        logger.info(
            "  running account_id=%d count=%d",
            existing["account_id"],
            len(running_strategies),
        )
        
        #  running  Worker
        if not running_strategies:
            try:
                logger.info("  engine.stop_worker...")
                await engine.stop_worker(
                    account_id=existing["account_id"],
                    platform_type=strategy_platform_type,
                )
                logger.info(" engine.stop_worker ")
            except Exception as e:
                logger.exception(f"  Worker : {e}")
                # 
        else:
            # 还有其他 running 策略：从 Worker 中移除该策略，不停止 Worker
            try:
                worker = await engine.registry.get((existing["account_id"], strategy_platform_type))
                if worker and worker.running:
                    worker.remove_strategy(strategy_id)
                    logger.info(
                        "从 Worker 移除策略 strategy_id=%d account_id=%d 剩余策略=%d",
                        strategy_id, existing["account_id"], len(worker.strategies),
                    )
            except Exception as e:
                logger.exception(f"移除策略异常: {e}")
    
    # 暂停策略：从 Worker 中移除该策略（不停止 Worker）
    elif target_status == "paused":
        logger.info("暂停策略，从 Worker 移除 strategy_id=%d...", strategy_id)
        try:
            account = await account_get_by_id(
                db, account_id=existing["account_id"], operator_id=operator["id"]
            )
            if not account:
                raise BizError(4001, "", status_code=404)
            strategy_platform_type = _normalize_strategy_platform_type(existing.get("platform_type"))
            worker = await engine.registry.get((existing["account_id"], strategy_platform_type))
            if worker and worker.running:
                worker.remove_strategy(strategy_id)
                logger.info(
                    "暂停：从 Worker 移除策略 strategy_id=%d account_id=%d 剩余策略=%d",
                    strategy_id, existing["account_id"], len(worker.strategies),
                )
        except Exception as e:
            logger.exception(f"暂停移除策略异常: {e}")
    
    row = await strategy_update_status(
        db,
        strategy_id=strategy_id,
        operator_id=operator["id"],
        status=target_status,
    )
    logger.info(" strategy_id=%d new_status=%s", strategy_id, target_status)
    return _to_strategy_info(row)


@router.post("/strategies/{strategy_id}/start")
async def start_strategy(
    strategy_id: int,
    request: Request,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """stopped/paused  running"""
    info = await _transition_strategy(strategy_id, "running", operator, db, request)
    return ApiResponse[StrategyInfo](data=info)


@router.post("/strategies/{strategy_id}/pause")
async def pause_strategy(
    strategy_id: int,
    request: Request,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """running  paused"""
    info = await _transition_strategy(strategy_id, "paused", operator, db, request)
    return ApiResponse[StrategyInfo](data=info)


@router.post("/strategies/{strategy_id}/stop")
async def stop_strategy(
    strategy_id: int,
    request: Request,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """running/paused/error  stopped"""
    info = await _transition_strategy(strategy_id, "stopped", operator, db, request)
    return ApiResponse[StrategyInfo](data=info)
