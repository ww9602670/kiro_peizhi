"""回测 API Schema

元/分转换规则：
- 前端传入元（float），后端存储分（int）
- base_amount: 元 → 分 (×100)
- odds: 浮点赔率 → 10000倍整数 (×10000)
- 响应中分 → 元 (÷100)
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, field_validator


class BacktestCreate(BaseModel):
    """创建回测任务请求"""
    strategy_type: Literal["flat", "martin"]
    key_codes: list[str]
    base_amount: float                        # 元
    martin_sequence: list[float] | None = None  # 马丁倍率序列
    odds_map: dict[str, float]                # {key_code: 浮点赔率}
    start_date: str                           # YYYY-MM-DD
    end_date: str                             # YYYY-MM-DD
    start_issue: str | None = None            # 由后端根据日期自动填充
    end_issue: str | None = None

    @field_validator("key_codes")
    @classmethod
    def key_codes_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("key_codes 不能为空")
        return v

    @field_validator("base_amount")
    @classmethod
    def base_amount_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("base_amount 必须大于 0")
        return v

    def to_fen(self) -> dict:
        """转换为分/10000倍整数，供引擎使用"""
        return {
            "strategy_type": self.strategy_type,
            "key_codes": self.key_codes,
            "base_amount": int(self.base_amount * 100),
            "martin_sequence": (
                [int(x) for x in self.martin_sequence]
                if self.martin_sequence else None
            ),
            "odds_map": {k: int(v * 10000) for k, v in self.odds_map.items()},
            "start_issue": self.start_issue,
            "end_issue": self.end_issue,
        }


class BacktestRecordSchema(BaseModel):
    """单期回测记录"""
    issue: str
    amount: float             # 元
    pnl: float                # 元
    cumulative_pnl: float     # 元
    martin_level: int | None = None


class BacktestResultSchema(BaseModel):
    """回测结果"""
    total_pnl: float              # 元
    total_bet_amount: float       # 元
    total_issues: int
    win_count: int
    lose_count: int
    win_rate: float
    max_consecutive_loss: int
    max_drawdown: float           # 元
    skipped_issues: int
    martin_level_dist: dict[int, int] | None = None
    max_martin_level: int | None = None
    records: list[BacktestRecordSchema] = []


class BacktestInfo(BaseModel):
    """回测任务信息"""
    id: int
    strategy_type: str
    key_codes: list[str]
    base_amount: float            # 元
    martin_sequence: list[float] | None = None
    start_issue: str
    end_issue: str
    status: str
    error_message: str | None = None
    total_issues: int = 0
    processed_issues: int = 0
    created_at: str
    completed_at: str | None = None
    result: BacktestResultSchema | None = None


class HistoryDataStatus(BaseModel):
    """历史数据状态"""
    total_count: int
    min_issue: str | None = None
    max_issue: str | None = None
    min_time: str | None = None
    max_time: str | None = None
    gap_count: int = 0
    syncing: bool = False
    last_sync_time: str | None = None
    last_sync_inserted: int | None = None


class FillHistoryResult(BaseModel):
    """补充历史数据结果"""
    total_inserted: int
    days_processed: int
    days_failed: list[str] = []


def backtest_task_row_to_info(row: dict) -> BacktestInfo:
    """将数据库行转换为 BacktestInfo（分→元）"""
    import json

    key_codes = json.loads(row["key_codes"]) if isinstance(row["key_codes"], str) else row["key_codes"]
    martin_seq_raw = row.get("martin_sequence")
    martin_sequence = json.loads(martin_seq_raw) if martin_seq_raw else None

    result_schema: Optional[BacktestResultSchema] = None
    result_json = row.get("result_json")
    if result_json:
        rd = json.loads(result_json) if isinstance(result_json, str) else result_json
        records = [
            BacktestRecordSchema(
                issue=r["issue"],
                amount=r["amount"] / 100,
                pnl=r["pnl"] / 100,
                cumulative_pnl=r["cumulative_pnl"] / 100,
                martin_level=r.get("martin_level"),
            )
            for r in rd.get("records", [])
        ]
        ml_dist = rd.get("martin_level_dist")
        if ml_dist is not None:
            ml_dist = {int(k): v for k, v in ml_dist.items()}
        result_schema = BacktestResultSchema(
            total_pnl=rd["total_pnl"] / 100,
            total_bet_amount=rd["total_bet_amount"] / 100,
            total_issues=rd["total_issues"],
            win_count=rd["win_count"],
            lose_count=rd["lose_count"],
            win_rate=rd["win_rate"],
            max_consecutive_loss=rd["max_consecutive_loss"],
            max_drawdown=rd["max_drawdown"] / 100,
            skipped_issues=rd["skipped_issues"],
            martin_level_dist=ml_dist,
            max_martin_level=rd.get("max_martin_level"),
            records=records,
        )

    return BacktestInfo(
        id=row["id"],
        strategy_type=row["strategy_type"],
        key_codes=key_codes,
        base_amount=row["base_amount"] / 100,
        martin_sequence=martin_sequence,
        start_issue=row["start_issue"],
        end_issue=row["end_issue"],
        status=row["status"],
        error_message=row.get("error_message"),
        total_issues=row.get("total_issues", 0),
        processed_issues=row.get("processed_issues", 0),
        created_at=row["created_at"],
        completed_at=row.get("completed_at"),
        result=result_schema,
    )
