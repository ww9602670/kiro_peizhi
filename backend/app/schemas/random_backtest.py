"""PC28 随机马丁回测系统 — Pydantic 模型"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# 方案生成
# ---------------------------------------------------------------------------

class PlanGenerateRequest(BaseModel):
    num_groups: int = Field(..., ge=1, le=3000)
    periods_per_group: int = Field(..., ge=1, le=20, alias="N")
    numbers_per_period: int = Field(..., ge=1, le=10, alias="K")

    model_config = {"populate_by_name": True}


class PeriodBetSchema(BaseModel):
    period_index: int       # 1-based
    ball: int               # 1/2/3
    numbers: list[int]      # 升序，0-9
    mask: int               # 10-bit bitmask

    @model_validator(mode="before")
    @classmethod
    def compute_mask(cls, values: dict) -> dict:
        if "mask" not in values or values.get("mask") is None:
            nums = values.get("numbers", [])
            values["mask"] = sum(1 << n for n in nums)
        return values


class PlanSchema(BaseModel):
    group_id: int
    periods: list[PeriodBetSchema]


class PlanGenerateResponse(BaseModel):
    plan_set_id: int
    plans: list[PlanSchema]
    num_groups: int
    periods_per_group: int
    numbers_per_period: int


# ---------------------------------------------------------------------------
# 回测配置
# ---------------------------------------------------------------------------

class RandomBacktestConfig(BaseModel):
    fund_mode: Literal["shared_total", "per_group"]
    initial_total_balance: float = Field(default=0.0, ge=0)
    initial_balance_per_group: float = Field(default=0.0, ge=0)
    base_unit: float = Field(..., gt=0)
    martin_multiplier: float = Field(..., ge=1.0)
    odds: float = Field(default=9.927, gt=1.0)
    chase_limit: int = Field(..., ge=1)

    @model_validator(mode="after")
    def validate_balances(self) -> "RandomBacktestConfig":
        if self.fund_mode == "shared_total" and self.initial_total_balance <= 0:
            raise ValueError("共享模式需要 initial_total_balance > 0")
        if self.fund_mode == "per_group" and self.initial_balance_per_group <= 0:
            raise ValueError("每组独立模式需要 initial_balance_per_group > 0")
        return self


class BacktestTaskCreate(BaseModel):
    config: RandomBacktestConfig
    plans: list[PlanSchema]
    plan_set_id: int | None = None
    start_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    kline_window: int = Field(default=50, ge=10, le=500)

    @model_validator(mode="after")
    def validate_chase_limit(self) -> "BacktestTaskCreate":
        if self.plans:
            n = len(self.plans[0].periods)
            if self.config.chase_limit < n:
                raise ValueError(f"chase_limit({self.config.chase_limit}) 必须 >= periods_per_group({n})")
        return self


# ---------------------------------------------------------------------------
# 回测结果
# ---------------------------------------------------------------------------

class GroupResultSchema(BaseModel):
    group_id: int
    abandoned: bool
    abandoned_issue: str
    final_balance: float
    total_pnl: float
    win_count: int
    lose_count: int
    win_rate: float
    max_consecutive_loss: int
    max_drawdown: float


class KlineBarSchema(BaseModel):
    index: int      # 窗口序号（0-based）
    open: float
    high: float
    low: float
    close: float


class AggregateResultSchema(BaseModel):
    total_equity_curve: list[float]
    abandoned_amount_curve: list[float]
    total_max_drawdown: float
    total_win_count: int
    total_lose_count: int
    total_win_rate: float
    group_results: list[GroupResultSchema]
    kline_data: list[KlineBarSchema]
    issues: list[str]
    processed_issues: int       # 实际处理的期数（中断时 < total_issues）


class BacktestTaskInfo(BaseModel):
    id: int
    status: str
    total_issues: int
    processed_issues: int
    result: AggregateResultSchema | None = None
    error_message: str | None = None
    created_at: str
    completed_at: str | None = None
