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
    validate_state_transition,
)
from app.utils.response import BizError

router = APIRouter()


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


#  DB   StrategyInfo  

def _to_strategy_info(row: dict) -> StrategyInfo:
    """ DB  StrategyInfo schema"""
    # martin_sequence: JSON   list[float]
    ms_raw = row.get("martin_sequence")
    martin_sequence = json.loads(ms_raw) if ms_raw else None

    return StrategyInfo(
        id=row["id"],
        account_id=row["account_id"],
        name=row["name"],
        type=row["type"],
        play_code=row["play_code"],
        base_amount=_fen_to_yuan(row["base_amount"]),
        martin_sequence=martin_sequence,
        bet_timing=row["bet_timing"],
        simulation=bool(row["simulation"]),
        status=row["status"],
        martin_level=row["martin_level"],
        stop_loss=_fen_to_yuan_optional(row.get("stop_loss")),
        take_profit=_fen_to_yuan_optional(row.get("take_profit")),
        daily_pnl=_fen_to_yuan(row["daily_pnl"]),
        total_pnl=_fen_to_yuan(row["total_pnl"]),
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

    # 2. 
    martin_seq_json = (
        json.dumps(body.martin_sequence) if body.martin_sequence else None
    )

    row = await strategy_create(
        db,
        operator_id=operator["id"],
        account_id=body.account_id,
        name=body.name,
        type=body.type,
        play_code=body.play_code,
        base_amount=_yuan_to_fen(body.base_amount),
        martin_sequence=martin_seq_json,
        bet_timing=body.bet_timing,
        simulation=1 if body.simulation else 0,
        stop_loss=_yuan_to_fen(body.stop_loss) if body.stop_loss is not None else None,
        take_profit=_yuan_to_fen(body.take_profit) if body.take_profit is not None else None,
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

    if existing["status"] != "stopped":
        raise BizError(4003, "", status_code=400)

    # 
    update_fields: dict = {}
    if body.name is not None:
        update_fields["name"] = body.name
    if body.base_amount is not None:
        update_fields["base_amount"] = _yuan_to_fen(body.base_amount)
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

    if not update_fields:
        return ApiResponse[StrategyInfo](data=_to_strategy_info(existing))

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
        
        #  running 
        all_strategies = await strategy_list_by_operator(db, operator_id=operator["id"])
        running_strategies = [
            s for s in all_strategies
            if s.get("account_id") == existing["account_id"]
            and s.get("status") == "running"
        ]
        
        logger.info(
            "  running account_id=%d count=%d",
            account["id"],
            len(running_strategies),
        )
        
        #  Worker
        try:
            logger.info("  engine.start_worker...")
            await engine.start_worker(
                operator_id=operator["id"],
                account_id=account["id"],
                account_name=account["account_name"],
                password=account["password"],
                platform_type=account.get("platform_type", "JND28WEB"),
                strategies=running_strategies,
            )
            logger.info(" engine.start_worker ")
        except Exception as e:
            logger.exception(f"  Worker : {e}")
            raise BizError(5001, f" Worker : {str(e)}", status_code=500)
    
    #  Worker
    elif target_status == "stopped":
        logger.info("  Worker...")
        
        #  running 
        all_strategies = await strategy_list_by_operator(db, operator_id=operator["id"])
        running_strategies = [
            s for s in all_strategies
            if s.get("account_id") == existing["account_id"]
            and s.get("status") == "running"
            and s.get("id") != strategy_id  # 
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
                await engine.stop_worker(account_id=existing["account_id"])
                logger.info(" engine.stop_worker ")
            except Exception as e:
                logger.exception(f"  Worker : {e}")
                # 
    
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
