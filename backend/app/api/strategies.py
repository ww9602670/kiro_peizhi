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
    has_dw3_prefix,
    is_dw3_group_play_code,
    normalize_dw3_group_play_code,
    validate_state_transition,
)
from app.schemas.account import get_allowed_platform_types
from app.utils.strategy_timing import (
    BET_TIMING_MAX,
    BET_TIMING_MIN,
    build_candidate_from_row,
    build_timing_candidate,
    normalize_red_wave_double_play_code,
    resolve_timing_conflicts,
    summarize_timing_conflicts,
)
from app.utils.response import BizError
from app.utils.key_code_map import get_key_code_name
from app.utils.luckysb_play_codes import (
    LUCKYSB_PLATFORM_TYPE,
    get_luckysb_play_code_name,
    validate_luckysb_strategy,
)

router = APIRouter()

DW3_PLAY_CODE_LABELS: dict[str, str] = {
    "DW3_BS_BBB": "DW3 Big-Big-Big",
    "DW3_BS_BBS": "DW3 Big-Big-Small",
    "DW3_BS_BSB": "DW3 Big-Small-Big",
    "DW3_BS_BSS": "DW3 Big-Small-Small",
    "DW3_BS_SBB": "DW3 Small-Big-Big",
    "DW3_BS_SBS": "DW3 Small-Big-Small",
    "DW3_BS_SSB": "DW3 Small-Small-Big",
    "DW3_BS_SSS": "DW3 Small-Small-Small",
    "DW3_OE_OOO": "DW3 Odd-Odd-Odd",
    "DW3_OE_OOE": "DW3 Odd-Odd-Even",
    "DW3_OE_OEO": "DW3 Odd-Even-Odd",
    "DW3_OE_OEE": "DW3 Odd-Even-Even",
    "DW3_OE_EOO": "DW3 Even-Odd-Odd",
    "DW3_OE_EOE": "DW3 Even-Odd-Even",
    "DW3_OE_EEO": "DW3 Even-Even-Odd",
    "DW3_OE_EEE": "DW3 Even-Even-Even",
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
    if is_dw3_group_play_code(play_code):
        return ", ".join(DW3_PLAY_CODE_LABELS.get(c, c) for c in play_code.split(","))
    return ", ".join(get_key_code_name(c) for c in play_code.split(","))


def _validate_luckysb_or_raise(type_: str, play_code: str) -> None:
    try:
        validate_luckysb_strategy(type_, play_code)
    except ValueError as exc:
        raise BizError(1002, str(exc), status_code=400)


def _validate_strategy_platform_type_for_account(
    strategy_platform_type: str,
    account: dict,
) -> str:
    normalized = (strategy_platform_type or "").strip().upper()
    allowed = get_allowed_platform_types(account.get("game_type", ""))
    if normalized not in allowed:
        raise BizError(
            1002,
            f"platform_type is not allowed for game_type={account.get('game_type')}",
            status_code=400,
        )
    return normalized


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

    # daily_pnl 日期检查：如果 daily_pnl_date 不是今天，返回 0
    from datetime import datetime, timezone, timedelta
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    daily_pnl_raw = row["daily_pnl"] if row.get("daily_pnl_date") == today else 0

    platform_type = row.get("platform_type", "JND28WEB")

    return StrategyInfo(
        id=row["id"],
        account_id=row["account_id"],
        name=row["name"],
        type=row["type"],
        play_code=row["play_code"],
        play_code_name=_get_play_code_name(platform_type, row["play_code"]),
        base_amount=_fen_to_yuan(row["base_amount"]),
        martin_sequence=martin_sequence,
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
    )


#   

@router.get("/strategies")
async def list_strategies(
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """"""
    rows = await strategy_list_by_operator(db, operator_id=operator["id"])
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
    account = await account_get_by_id(
        db, account_id=body.account_id, operator_id=operator["id"]
    )
    if not account:
        raise BizError(4001, "", status_code=404)
    strategy_platform_type = _validate_strategy_platform_type_for_account(
        body.platform_type,
        account,
    )

    # 2. 
    play_code = body.play_code
    if strategy_platform_type == LUCKYSB_PLATFORM_TYPE:
        _validate_luckysb_or_raise(body.type, play_code)

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
    account = await account_get_by_id(
        db, account_id=existing["account_id"], operator_id=operator["id"]
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
    existing_platform_type = existing.get("platform_type") or get_allowed_platform_types(account["game_type"])[0]
    requested_platform_type = (
        _validate_strategy_platform_type_for_account(body.platform_type, account)
        if body.platform_type is not None
        else existing_platform_type
    )
    if body.play_code is not None:
        if requested_platform_type == LUCKYSB_PLATFORM_TYPE:
            update_fields["play_code"] = body.play_code
        elif existing["type"] == "red_wave_double_martin":
            try:
                update_fields["play_code"] = normalize_red_wave_double_play_code(
                    body.play_code
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
                "play_code update is only allowed for red_wave_double_martin or DW3 flat/martin",
                status_code=400,
            )
    if body.martin_sequence is not None:
        # 
        for v in body.martin_sequence:
            if v <= 0:
                raise BizError(1002, " 0", status_code=400)
        update_fields["martin_sequence"] = json.dumps(body.martin_sequence)
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
    """删除策略（仅 stopped 状态）

    force=true 时级联删除关联的投注记录。
    """
    existing = await strategy_get_by_id(
        db, strategy_id=strategy_id, operator_id=operator["id"]
    )
    if not existing:
        raise BizError(4001, "策略不存在", status_code=404)

    if existing["status"] != "stopped":
        raise BizError(4003, "只能删除已停止的策略", status_code=400)

    try:
        deleted = await strategy_delete(
            db, strategy_id=strategy_id, operator_id=operator["id"], force=force
        )
    except ValueError as e:
        raise BizError(4003, str(e), status_code=400)

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

    row = await strategy_update_status(
        db,
        strategy_id=strategy_id,
        operator_id=operator["id"],
        status=target_status,
    )
    
    logger.info(
        " strategy_id=%d new_status=%s",
        strategy_id,
        target_status,
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
        account = await account_get_by_id(
            db, account_id=existing["account_id"], operator_id=operator["id"]
        )
        if not account:
            logger.error(" account_id=%d", existing["account_id"])
            raise BizError(4001, "", status_code=404)
        
        logger.info(
            " account_id=%d account_name=%s status=%s",
            account["id"],
            account["account_name"],
            account["status"],
        )
        
        # 
        if account["status"] != "online":
            logger.error(" account_id=%d status=%s", account["id"], account["status"])
            raise BizError(4002, "", status_code=400)
        
        strategy_platform_type = existing.get("platform_type") or get_allowed_platform_types(account["game_type"])[0]
        all_strategies = await strategy_list_by_operator(db, operator_id=operator["id"])
        running_strategies = [
            s for s in all_strategies
            if s.get("account_id") == existing["account_id"]
            and s.get("status") == "running"
            and (s.get("platform_type") or strategy_platform_type) == strategy_platform_type
        ]
        
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
        strategy_platform_type = existing.get("platform_type") or get_allowed_platform_types(account["game_type"])[0]
        all_strategies = await strategy_list_by_operator(db, operator_id=operator["id"])
        running_strategies = [
            s for s in all_strategies
            if s.get("account_id") == existing["account_id"]
            and s.get("status") == "running"
            and s.get("id") != strategy_id
            and (s.get("platform_type") or strategy_platform_type) == strategy_platform_type
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
            strategy_platform_type = existing.get("platform_type") or get_allowed_platform_types(account["game_type"])[0]
            worker = await engine.registry.get((existing["account_id"], strategy_platform_type))
            if worker and worker.running:
                worker.remove_strategy(strategy_id)
                logger.info(
                    "暂停：从 Worker 移除策略 strategy_id=%d account_id=%d 剩余策略=%d",
                    strategy_id, existing["account_id"], len(worker.strategies),
                )
        except Exception as e:
            logger.exception(f"暂停移除策略异常: {e}")
    
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
