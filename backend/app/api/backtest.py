"""回测 API

POST   /backtest/tasks          创建回测任务
GET    /backtest/tasks          回测任务列表（分页）
GET    /backtest/tasks/{id}     查询回测任务详情
GET    /backtest/history-status 查询历史数据范围
POST   /backtest/fill-history   补充历史数据（8828 数据源，v2）
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends

from app.api.dependencies import get_current_operator, get_db_conn
from app.config import BOCAI_HISTORY_DB_PATH
from app.engine.backtest import BacktestConfig, BacktestEngine
from app.schemas.backtest import (
    BacktestCreate,
    BacktestInfo,
    BacktestRecordSchema,
    BacktestResultSchema,
    HistoryDataStatus,
)
from app.schemas.common import ApiResponse, PagedData
from app.utils.key_code_map import KEY_CODE_MAP
from app.utils.response import BizError

router = APIRouter()
logger = logging.getLogger(__name__)

# jnd28.sqlite3 路径（项目根目录）
_JND28_DB = BOCAI_HISTORY_DB_PATH

# 已知不可补全的缺失期号（平台未开奖 + 8828 无历史数据）
_KNOWN_MISSING_ISSUES = 181

# 回测任务最大保留数量（每个操作者）
_MAX_BACKTEST_TASKS = 5


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _yuan_to_fen(yuan: float) -> int:
    return int(yuan * 100)


def _fen_to_yuan(fen: int) -> float:
    return fen / 100


def _now() -> str:
    from datetime import datetime, timezone, timedelta
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def _date_to_issue_range(db_path: str, start_date: str, end_date: str) -> tuple[str, str]:
    """根据日期范围查询对应的期号范围"""
    conn = sqlite3.connect(db_path)
    try:
        # start_date 当天最早的期号
        row = conn.execute(
            "SELECT MIN(issue) as mi FROM jnd28_history WHERE open_time >= ?",
            (start_date + " 00:00:00",),
        ).fetchone()
        start_issue = row[0] if row and row[0] else None

        # end_date 当天最晚的期号
        row = conn.execute(
            "SELECT MAX(issue) as mi FROM jnd28_history WHERE open_time <= ?",
            (end_date + " 23:59:59",),
        ).fetchone()
        end_issue = row[0] if row and row[0] else None

        if not start_issue or not end_issue:
            raise ValueError(f"日期范围 {start_date} ~ {end_date} 内无历史数据")
        return start_issue, end_issue
    finally:
        conn.close()


def _row_to_backtest_info(row: dict[str, Any]) -> BacktestInfo:
    """DB 行 → BacktestInfo schema"""
    key_codes = json.loads(row["key_codes"]) if isinstance(row["key_codes"], str) else row["key_codes"]
    ms_raw = row.get("martin_sequence")
    martin_sequence = json.loads(ms_raw) if ms_raw else None

    result_schema = None
    if row.get("result_json"):
        try:
            rd = json.loads(row["result_json"]) if isinstance(row["result_json"], str) else row["result_json"]
            result_schema = BacktestResultSchema(
                total_pnl=_fen_to_yuan(rd["total_pnl"]),
                total_bet_amount=_fen_to_yuan(rd["total_bet_amount"]),
                total_issues=rd["total_issues"],
                win_count=rd["win_count"],
                lose_count=rd["lose_count"],
                win_rate=rd["win_rate"],
                max_consecutive_loss=rd["max_consecutive_loss"],
                max_drawdown=_fen_to_yuan(rd["max_drawdown"]),
                skipped_issues=rd.get("skipped_issues", 0),
                martin_level_dist=rd.get("martin_level_dist"),
                max_martin_level=rd.get("max_martin_level"),
                records=[
                    BacktestRecordSchema(
                        issue=r["issue"],
                        amount=_fen_to_yuan(r["amount"]),
                        pnl=_fen_to_yuan(r["pnl"]),
                        cumulative_pnl=_fen_to_yuan(r["cumulative_pnl"]),
                        martin_level=r.get("martin_level"),
                    )
                    for r in rd.get("records", [])
                ],
            )
        except Exception:
            logger.exception("解析 result_json 失败 task_id=%s", row.get("id"))

    return BacktestInfo(
        id=row["id"],
        strategy_type=row["strategy_type"],
        key_codes=key_codes,
        base_amount=_fen_to_yuan(row["base_amount"]),
        martin_sequence=martin_sequence,
        start_issue=row["start_issue"],
        end_issue=row["end_issue"],
        status=row["status"],
        error_message=row.get("error_message"),
        total_issues=row.get("total_issues", 0),
        processed_issues=row.get("processed_issues", 0),
        created_at=row.get("created_at", ""),
        completed_at=row.get("completed_at"),
        result=result_schema,
    )


# ---------------------------------------------------------------------------
# 回测执行（后台任务）
# ---------------------------------------------------------------------------

async def _cleanup_old_tasks(db, operator_id: int):
    """保留最近 _MAX_BACKTEST_TASKS 条任务，删除更早的"""
    try:
        rows = await (await db.execute(
            """SELECT id FROM backtest_tasks WHERE operator_id=?
               ORDER BY created_at DESC LIMIT -1 OFFSET ?""",
            (operator_id, _MAX_BACKTEST_TASKS),
        )).fetchall()
        if rows:
            ids = [r["id"] for r in rows]
            placeholders = ",".join("?" * len(ids))
            await db.execute(
                f"DELETE FROM backtest_tasks WHERE id IN ({placeholders})", ids
            )
            await db.commit()
            logger.info("清理旧回测任务 operator_id=%d, 删除 %d 条", operator_id, len(ids))
    except Exception:
        logger.exception("清理旧回测任务失败")


async def _run_backtest_task(task_id: int, config: BacktestConfig, db):
    """在后台线程中执行回测，完成后更新数据库"""
    try:
        await db.execute(
            "UPDATE backtest_tasks SET status='running' WHERE id=?", (task_id,)
        )
        await db.commit()

        engine = BacktestEngine(os.path.abspath(_JND28_DB))
        result = await asyncio.to_thread(engine.run, config)

        result_json = json.dumps(result.to_dict(), ensure_ascii=False)
        now = _now()
        await db.execute(
            """UPDATE backtest_tasks
               SET status='completed', result_json=?, total_issues=?,
                   processed_issues=?, completed_at=?
               WHERE id=?""",
            (result_json, result.total_issues, result.total_issues, now, task_id),
        )
        await db.commit()
        logger.info("回测任务完成 task_id=%d issues=%d", task_id, result.total_issues)
    except Exception as e:
        logger.exception("回测任务失败 task_id=%d", task_id)
        try:
            await db.execute(
                "UPDATE backtest_tasks SET status='failed', error_message=? WHERE id=?",
                (str(e)[:500], task_id),
            )
            await db.commit()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# API 端点
# ---------------------------------------------------------------------------

@router.post("/backtest/tasks")
async def create_backtest_task(
    body: BacktestCreate,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """创建回测任务"""
    # 验证 key_codes
    for kc in body.key_codes:
        if kc not in KEY_CODE_MAP:
            raise BizError(1002, f"无效的玩法编码: {kc}", status_code=400)

    # 验证 odds_map 包含所有 key_codes
    for kc in body.key_codes:
        if kc not in body.odds_map:
            raise BizError(1002, f"odds_map 缺少 {kc} 的赔率", status_code=400)

    # 马丁策略验证
    if body.strategy_type == "martin":
        if not body.martin_sequence:
            raise BizError(1002, "马丁策略需要 martin_sequence", status_code=400)
        for v in body.martin_sequence:
            if v <= 0:
                raise BizError(1002, "martin_sequence 中的值必须大于 0", status_code=400)

    # 单位转换：元 → 分
    base_amount_fen = _yuan_to_fen(body.base_amount)
    odds_map_int = {k: int(v * 10000) for k, v in body.odds_map.items()}
    martin_seq_int = [int(v) for v in body.martin_sequence] if body.martin_sequence else None

    # 日期转期号
    db_path = os.path.abspath(_JND28_DB)
    if body.start_date and body.end_date:
        try:
            start_issue, end_issue = _date_to_issue_range(db_path, body.start_date, body.end_date)
        except ValueError as e:
            raise BizError(1002, str(e), status_code=400)
    else:
        start_issue = body.start_issue or ""
        end_issue = body.end_issue or ""
    if not start_issue or not end_issue:
        raise BizError(1002, "缺少日期范围或期号范围", status_code=400)

    # 写入数据库
    now = _now()
    cursor = await db.execute(
        """INSERT INTO backtest_tasks
           (operator_id, strategy_type, key_codes, base_amount,
            martin_sequence, odds_map, start_issue, end_issue, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
        (
            operator["id"],
            body.strategy_type,
            json.dumps(body.key_codes),
            base_amount_fen,
            json.dumps(martin_seq_int) if martin_seq_int else None,
            json.dumps(odds_map_int),
            start_issue,
            end_issue,
            now,
        ),
    )
    await db.commit()
    task_id = cursor.lastrowid

    # 构造引擎配置
    config = BacktestConfig(
        strategy_type=body.strategy_type,
        key_codes=body.key_codes,
        base_amount=base_amount_fen,
        martin_sequence=martin_seq_int,
        odds_map=odds_map_int,
        start_issue=start_issue,
        end_issue=end_issue,
    )

    # 清理旧任务：只保留最近 _MAX_BACKTEST_TASKS 条
    await _cleanup_old_tasks(db, operator["id"])

    # 后台执行回测
    asyncio.create_task(_run_backtest_task(task_id, config, db))

    # 返回任务信息
    row = await (await db.execute(
        "SELECT * FROM backtest_tasks WHERE id=?", (task_id,)
    )).fetchone()
    info = _row_to_backtest_info(dict(row))
    return ApiResponse[BacktestInfo](data=info)


@router.get("/backtest/tasks")
async def list_backtest_tasks(
    page: int = 1,
    page_size: int = 20,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """回测任务列表（分页）"""
    count_row = await (await db.execute(
        "SELECT COUNT(*) as cnt FROM backtest_tasks WHERE operator_id=?",
        (operator["id"],),
    )).fetchone()
    total = count_row["cnt"] if count_row else 0

    offset = (page - 1) * page_size
    rows = await (await db.execute(
        """SELECT * FROM backtest_tasks WHERE operator_id=?
           ORDER BY created_at DESC LIMIT ? OFFSET ?""",
        (operator["id"], page_size, offset),
    )).fetchall()

    items = [_row_to_backtest_info(dict(r)) for r in rows]
    data = PagedData[BacktestInfo](items=items, total=total, page=page, page_size=page_size)
    return ApiResponse[PagedData[BacktestInfo]](data=data)


@router.get("/backtest/tasks/{task_id}")
async def get_backtest_task(
    task_id: int,
    operator: dict = Depends(get_current_operator),
    db=Depends(get_db_conn),
):
    """查询回测任务详情"""
    row = await (await db.execute(
        "SELECT * FROM backtest_tasks WHERE id=? AND operator_id=?",
        (task_id, operator["id"]),
    )).fetchone()
    if not row:
        raise BizError(4001, "回测任务不存在", status_code=404)

    info = _row_to_backtest_info(dict(row))
    return ApiResponse[BacktestInfo](data=info)


@router.get("/backtest/history-status")
async def get_history_status(
    operator: dict = Depends(get_current_operator),
):
    """查询历史数据范围 + 自动同步状态"""
    from app.engine.history_sync import get_sync_service

    db_path = os.path.abspath(_JND28_DB)

    def _query():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """SELECT COUNT(*) as total_count,
                          MIN(issue) as min_issue, MAX(issue) as max_issue,
                          MIN(open_time) as min_time, MAX(open_time) as max_time
                   FROM jnd28_history"""
            ).fetchone()
            total = row["total_count"] if row else 0

            # 计算缺失期数（简单估算）
            gap_count = 0
            if total > 0 and row["min_issue"] and row["max_issue"]:
                try:
                    expected = int(row["max_issue"]) - int(row["min_issue"]) + 1
                    gap_count = max(0, expected - total)
                except ValueError:
                    pass

            # 扣除已知不可补全的缺失期号（平台未开奖 + 数据源无数据）
            effective_gap = max(0, gap_count - _KNOWN_MISSING_ISSUES)

            # 读取 sync_state
            last_sync_time = None
            last_sync_inserted = None
            try:
                r = conn.execute("SELECT v FROM sync_state WHERE k='last_sync_time'").fetchone()
                if r:
                    last_sync_time = r[0]
                r = conn.execute("SELECT v FROM sync_state WHERE k='last_sync_inserted'").fetchone()
                if r:
                    last_sync_inserted = int(r[0])
            except Exception:
                pass

            return {
                "total_count": total,
                "min_issue": row["min_issue"] if row else None,
                "max_issue": row["max_issue"] if row else None,
                "min_time": row["min_time"] if row else None,
                "max_time": row["max_time"] if row else None,
                "gap_count": effective_gap,
                "last_sync_time": last_sync_time,
                "last_sync_inserted": last_sync_inserted,
            }
        finally:
            conn.close()

    data = await asyncio.to_thread(_query)

    # 从 HistorySyncService 获取同步状态并触发自动检测
    try:
        sync_svc = get_sync_service()
        await sync_svc.check_and_sync()
        sync_status = sync_svc.get_status()
        data["syncing"] = sync_status["syncing"]
        # 优先使用内存中的最新值
        if sync_status["last_sync_time"]:
            data["last_sync_time"] = sync_status["last_sync_time"]
        if sync_status["last_sync_inserted"] is not None:
            data["last_sync_inserted"] = sync_status["last_sync_inserted"]
    except Exception:
        data["syncing"] = False

    return ApiResponse[HistoryDataStatus](data=HistoryDataStatus(**data))


@router.post("/backtest/fill-history")
async def fill_history(
    operator: dict = Depends(get_current_operator),
):
    """补充历史数据（使用 HistorySyncService 统一同步）"""
    from app.engine.history_sync import get_sync_service

    try:
        sync_svc = get_sync_service()
        result = await sync_svc.manual_sync()
        from app.schemas.backtest import FillHistoryResult
        return ApiResponse[FillHistoryResult](data=FillHistoryResult(
            total_inserted=result.get("total_inserted", 0),
            days_processed=0,
            days_failed=[],
        ))
    except RuntimeError:
        raise BizError(4002, "同步正在执行中，请稍候", status_code=409)
    except Exception as e:
        logger.exception("补充历史数据失败")
        raise BizError(5001, f"补充历史数据失败: {e}", status_code=500)
