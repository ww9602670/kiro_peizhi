"""PC28 随机马丁回测 API

POST   /random-backtest/generate-plan       即时生成方案
POST   /random-backtest/tasks               创建回测任务（后台执行）
GET    /random-backtest/tasks               任务列表（分页）
GET    /random-backtest/tasks/{id}          任务详情及进度
POST   /random-backtest/tasks/{id}/cancel   中断任务
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_current_operator, get_db_conn
from app.config import BOCAI_DB_PATH, BOCAI_HISTORY_DB_PATH
from app.engine.random_backtest_engine import (
    BacktestConfig,
    BacktestResult,
    RandomBacktestEngine,
)
from app.engine.random_plan_generator import Plan, PeriodBet, generate_plans
from app.schemas.common import ApiResponse, PagedData
from app.schemas.random_backtest import (
    AggregateResultSchema,
    BacktestTaskCreate,
    BacktestTaskInfo,
    GroupResultSchema,
    KlineBarSchema,
    PeriodBetSchema,
    PlanGenerateRequest,
    PlanGenerateResponse,
    PlanSchema,
)
from app.utils.response import BizError

router = APIRouter()
logger = logging.getLogger(__name__)

_JND28_DB = BOCAI_HISTORY_DB_PATH
_MAX_TASKS_PER_OPERATOR = 10


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _now_str() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def _date_to_issue_range(start_date: str, end_date: str) -> tuple[str, str]:
    conn = sqlite3.connect(_JND28_DB)
    try:
        row = conn.execute(
            "SELECT MIN(issue) FROM jnd28_history WHERE open_time >= ?",
            (start_date + " 00:00:00",),
        ).fetchone()
        start_issue = row[0] if row and row[0] else None

        row = conn.execute(
            "SELECT MAX(issue) FROM jnd28_history WHERE open_time <= ?",
            (end_date + " 23:59:59",),
        ).fetchone()
        end_issue = row[0] if row and row[0] else None

        if not start_issue or not end_issue:
            raise ValueError(f"日期范围 {start_date} ~ {end_date} 内无历史数据")
        return start_issue, end_issue
    finally:
        conn.close()


def _count_issues(start_issue: str, end_issue: str) -> int:
    conn = sqlite3.connect(_JND28_DB)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM jnd28_history WHERE issue >= ? AND issue <= ?",
            (start_issue, end_issue),
        ).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def _plans_to_json(plans: list[Plan]) -> str:
    data = [
        {
            "group_id": p.group_id,
            "periods": [
                {"period_index": pb.period_index, "ball": pb.ball,
                 "numbers": list(pb.numbers), "mask": pb.mask}
                for pb in p.periods
            ],
        }
        for p in plans
    ]
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _plans_from_json(raw: str) -> list[Plan]:
    data = json.loads(raw)
    plans: list[Plan] = []
    for pd in data:
        periods = [
            PeriodBet(
                period_index=pb["period_index"],
                ball=pb["ball"],
                numbers=tuple(pb["numbers"]),
                mask=pb["mask"],
            )
            for pb in pd["periods"]
        ]
        plans.append(Plan(group_id=pd["group_id"], periods=periods))
    return plans


def _result_to_schema(result: BacktestResult) -> AggregateResultSchema:
    return AggregateResultSchema(
        total_equity_curve=result.total_equity_curve,
        abandoned_amount_curve=result.abandoned_amount_curve,
        total_max_drawdown=result.total_max_drawdown,
        total_win_count=result.total_win_count,
        total_lose_count=result.total_lose_count,
        total_win_rate=result.total_win_rate,
        group_results=[
            GroupResultSchema(
                group_id=g.group_id,
                abandoned=g.abandoned,
                abandoned_issue=g.abandoned_issue,
                final_balance=g.final_balance,
                total_pnl=g.total_pnl,
                win_count=g.win_count,
                lose_count=g.lose_count,
                win_rate=g.win_rate,
                max_consecutive_loss=g.max_consecutive_loss,
                max_drawdown=g.max_drawdown,
            )
            for g in result.group_summaries
        ],
        kline_data=[
            KlineBarSchema(index=k.index, open=k.open, high=k.high, low=k.low, close=k.close)
            for k in result.kline_data
        ],
        issues=result.issues,
        processed_issues=result.processed_issues,
    )


def _row_to_task_info(row: dict[str, Any]) -> BacktestTaskInfo:
    result_schema = None
    if row.get("result_json"):
        try:
            rd = json.loads(row["result_json"])
            result_schema = AggregateResultSchema(**rd)
        except Exception:
            logger.exception("failed to parse result_json for task %s", row["id"])

    return BacktestTaskInfo(
        id=row["id"],
        status=row["status"],
        total_issues=row["total_issues"] or 0,
        processed_issues=row["processed_issues"] or 0,
        result=result_schema,
        error_message=row.get("error_message"),
        created_at=row["created_at"],
        completed_at=row.get("completed_at"),
    )


# ---------------------------------------------------------------------------
# 后台任务执行
# ---------------------------------------------------------------------------

async def _run_backtest_task(
    task_id: int,
    config: BacktestConfig,
    plans: list[Plan],
    start_issue: str,
    end_issue: str,
    request: Request,
    db,
) -> None:
    engine = RandomBacktestEngine(_JND28_DB)
    request.app.state.random_backtest_engines[task_id] = engine

    await db.execute(
        "UPDATE random_backtest_tasks SET status='running' WHERE id=?",
        (task_id,),
    )
    await db.commit()

    _main_db_path = BOCAI_DB_PATH

    def _progress_cb(processed: int) -> None:
        try:
            conn = sqlite3.connect(_main_db_path)
            conn.execute(
                "UPDATE random_backtest_tasks SET processed_issues=? WHERE id=?",
                (processed, task_id),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    try:
        result: BacktestResult = await asyncio.to_thread(
            engine.run, config, plans, start_issue, end_issue, _progress_cb
        )

        # 判断是否被中断
        is_cancelled = engine._stop_event.is_set()
        final_status = "cancelled" if is_cancelled else "completed"
        result_json = json.dumps(_result_to_schema(result).model_dump(), ensure_ascii=False)

        await db.execute(
            """UPDATE random_backtest_tasks
               SET status=?, result_json=?, processed_issues=?, completed_at=?
               WHERE id=?""",
            (final_status, result_json, result.processed_issues, _now_str(), task_id),
        )
        await db.commit()

    except Exception as exc:
        logger.exception("random backtest task %d failed", task_id)
        await db.execute(
            "UPDATE random_backtest_tasks SET status='failed', error_message=?, completed_at=? WHERE id=?",
            (str(exc)[:500], _now_str(), task_id),
        )
        await db.commit()
    finally:
        request.app.state.random_backtest_engines.pop(task_id, None)


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------

@router.post("/random-backtest/generate-plan", response_model=ApiResponse[PlanGenerateResponse])
async def generate_plan(
    body: PlanGenerateRequest,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """生成随机投注方案并自动入库 random_plan_sets，返回 plan_set_id"""
    try:
        plans = generate_plans(
            num_groups=body.num_groups,
            periods_per_group=body.periods_per_group,
            numbers_per_period=body.numbers_per_period,
        )
    except ValueError as e:
        raise BizError(4001, str(e))

    plan_schemas = [
        PlanSchema(
            group_id=p.group_id,
            periods=[
                PeriodBetSchema(
                    period_index=pb.period_index,
                    ball=pb.ball,
                    numbers=list(pb.numbers),
                    mask=pb.mask,
                )
                for pb in p.periods
            ],
        )
        for p in plans
    ]

    cursor = await db.execute(
        """INSERT INTO random_plan_sets
           (operator_id, num_groups, periods_per_group, numbers_per_period, plans_json)
           VALUES (?, ?, ?, ?, ?)""",
        (
            operator["id"],
            body.num_groups,
            body.periods_per_group,
            body.numbers_per_period,
            _plans_to_json(plans),
        ),
    )
    await db.commit()
    plan_set_id = cursor.lastrowid

    return ApiResponse(data=PlanGenerateResponse(
        plan_set_id=plan_set_id,
        plans=plan_schemas,
        num_groups=body.num_groups,
        periods_per_group=body.periods_per_group,
        numbers_per_period=body.numbers_per_period,
    ))


@router.post("/random-backtest/tasks", response_model=ApiResponse[BacktestTaskInfo])
async def create_random_backtest_task(
    body: BacktestTaskCreate,
    request: Request,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """创建回测任务，异步后台执行"""
    operator_id = operator["id"]

    # 日期 → 期号
    try:
        start_issue, end_issue = await asyncio.to_thread(
            _date_to_issue_range, body.start_date, body.end_date
        )
    except ValueError as e:
        raise BizError(4001, str(e))

    total_issues = await asyncio.to_thread(_count_issues, start_issue, end_issue)

    # 清理超出上限的旧任务
    cursor = await db.execute(
        "SELECT id FROM random_backtest_tasks WHERE operator_id=? ORDER BY created_at DESC LIMIT -1 OFFSET ?",
        (operator_id, _MAX_TASKS_PER_OPERATOR),
    )
    old_rows = await cursor.fetchall()
    for old in old_rows:
        await db.execute("DELETE FROM random_backtest_tasks WHERE id=?", (old["id"],))

    # 转换方案
    plans_internal = [
        Plan(
            group_id=ps.group_id,
            periods=[
                PeriodBet(pb.period_index, pb.ball, tuple(pb.numbers), pb.mask)
                for pb in ps.periods
            ],
        )
        for ps in body.plans
    ]

    # 入库
    config_dict = body.config.model_dump()
    config_dict["kline_window"] = body.kline_window
    cursor = await db.execute(
        """INSERT INTO random_backtest_tasks
           (operator_id, config_json, plans_json, plan_set_id, status, total_issues, created_at)
           VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
        (
            operator_id,
            json.dumps(config_dict, ensure_ascii=False),
            _plans_to_json(plans_internal),
            body.plan_set_id,
            total_issues,
            _now_str(),
        ),
    )
    await db.commit()
    task_id = cursor.lastrowid

    # 构建引擎配置
    engine_config = BacktestConfig(
        fund_mode=body.config.fund_mode,
        initial_total_balance=body.config.initial_total_balance,
        initial_balance_per_group=body.config.initial_balance_per_group,
        base_unit=body.config.base_unit,
        martin_multiplier=body.config.martin_multiplier,
        odds=body.config.odds,
        chase_limit=body.config.chase_limit,
        kline_window=body.kline_window,
    )

    # 启动后台任务
    asyncio.create_task(
        _run_backtest_task(task_id, engine_config, plans_internal, start_issue, end_issue, request, db)
    )

    return ApiResponse(data=BacktestTaskInfo(
        id=task_id,
        status="pending",
        total_issues=total_issues,
        processed_issues=0,
        created_at=_now_str(),
    ))


@router.get("/random-backtest/tasks", response_model=ApiResponse[PagedData[BacktestTaskInfo]])
async def list_random_backtest_tasks(
    page: int = 1,
    page_size: int = 20,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """分页查询当前操作者的回测任务"""
    operator_id = operator["id"]
    offset = (page - 1) * page_size

    cursor = await db.execute(
        "SELECT COUNT(*) FROM random_backtest_tasks WHERE operator_id=?",
        (operator_id,),
    )
    row = await cursor.fetchone()
    total = row[0]

    cursor = await db.execute(
        """SELECT id, status, total_issues, processed_issues, error_message,
                  result_json, created_at, completed_at
           FROM random_backtest_tasks
           WHERE operator_id=?
           ORDER BY created_at DESC
           LIMIT ? OFFSET ?""",
        (operator_id, page_size, offset),
    )
    rows = await cursor.fetchall()

    items = [_row_to_task_info(dict(r)) for r in rows]
    return ApiResponse(data=PagedData(items=items, total=total, page=page, page_size=page_size))


@router.get("/random-backtest/tasks/{task_id}", response_model=ApiResponse[BacktestTaskInfo])
async def get_random_backtest_task(
    task_id: int,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """查询单个任务详情及实时进度"""
    operator_id = operator["id"]
    cursor = await db.execute(
        """SELECT id, status, total_issues, processed_issues, error_message,
                  result_json, created_at, completed_at
           FROM random_backtest_tasks WHERE id=? AND operator_id=?""",
        (task_id, operator_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise BizError(4004, "任务不存在", status_code=404)

    return ApiResponse(data=_row_to_task_info(dict(row)))


@router.post("/random-backtest/tasks/{task_id}/cancel", response_model=ApiResponse[dict])
async def cancel_random_backtest_task(
    task_id: int,
    request: Request,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """中断正在运行的回测任务"""
    operator_id = operator["id"]
    cursor = await db.execute(
        "SELECT status FROM random_backtest_tasks WHERE id=? AND operator_id=?",
        (task_id, operator_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise BizError(4004, "任务不存在", status_code=404)

    if row["status"] not in ("pending", "running"):
        raise BizError(4001, f"任务状态 '{row['status']}' 不可中断")

    engine: RandomBacktestEngine | None = request.app.state.random_backtest_engines.get(task_id)
    if engine:
        engine.request_stop()

    return ApiResponse(data={"task_id": task_id, "message": "已发送中断信号"})
