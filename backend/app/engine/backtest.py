"""回测引擎 - 使用历史数据模拟策略运行

核心类：
- BacktestConfig: 回测配置
- BacktestRecord: 单期回测记录
- BacktestResult: 回测结果（含汇总指标和逐期记录）
- BacktestEngine: 回测引擎

INTEGER1=100分
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field

from app.engine.strategies.base import BaseStrategy, BetInstruction, LotteryResult, StrategyContext
from app.engine.strategies.flat import FlatStrategyImpl
from app.engine.strategies.martin import MartinStrategyImpl
from app.utils.key_code_map import KEY_CODE_MAP, check_win

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class BacktestConfig:
    """回测配置"""
    strategy_type: str              # 'flat' | 'martin'
    key_codes: list[str]            # 玩法编码列表
    base_amount: int                # 分
    martin_sequence: list[int] | None  # 马丁倍率序列（整数）
    odds_map: dict[str, int]        # {key_code: odds}，10000倍整数
    start_issue: str
    end_issue: str

    def to_dict(self) -> dict:
        return {
            "strategy_type": self.strategy_type,
            "key_codes": self.key_codes,
            "base_amount": self.base_amount,
            "martin_sequence": self.martin_sequence,
            "odds_map": self.odds_map,
            "start_issue": self.start_issue,
            "end_issue": self.end_issue,
        }

    @classmethod
    def from_dict(cls, d: dict) -> BacktestConfig:
        return cls(
            strategy_type=d["strategy_type"],
            key_codes=d["key_codes"],
            base_amount=d["base_amount"],
            martin_sequence=d.get("martin_sequence"),
            odds_map={k: int(v) for k, v in d["odds_map"].items()},
            start_issue=d["start_issue"],
            end_issue=d["end_issue"],
        )


@dataclass
class BacktestRecord:
    """单期回测记录"""
    issue: str
    amount: int               # 分（所有 key_code 投注总额）
    pnl: int                  # 分（所有 key_code 盈亏总和）
    cumulative_pnl: int       # 分（累计盈亏）
    martin_level: int | None  # 马丁层级（flat 策略为 None）

    def to_dict(self) -> dict:
        return {
            "issue": self.issue,
            "amount": self.amount,
            "pnl": self.pnl,
            "cumulative_pnl": self.cumulative_pnl,
            "martin_level": self.martin_level,
        }

    @classmethod
    def from_dict(cls, d: dict) -> BacktestRecord:
        return cls(
            issue=d["issue"],
            amount=d["amount"],
            pnl=d["pnl"],
            cumulative_pnl=d["cumulative_pnl"],
            martin_level=d.get("martin_level"),
        )


@dataclass
class BacktestResult:
    """回测结果"""
    # 汇总指标
    total_pnl: int                # 总盈亏（分）
    total_bet_amount: int         # 总投注额（分）
    total_issues: int             # 总期数
    win_count: int                # 胜场数
    lose_count: int               # 负场数
    win_rate: float               # 胜率 (0.0 ~ 1.0)
    max_consecutive_loss: int     # 最大连续亏损次数
    max_drawdown: int             # 最大回撤金额（分）
    skipped_issues: int           # 跳过的缺失期数

    # 马丁专属
    martin_level_dist: dict[int, int] | None  # {level: count}
    max_martin_level: int | None

    # 逐期记录
    records: list[BacktestRecord] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_pnl": self.total_pnl,
            "total_bet_amount": self.total_bet_amount,
            "total_issues": self.total_issues,
            "win_count": self.win_count,
            "lose_count": self.lose_count,
            "win_rate": self.win_rate,
            "max_consecutive_loss": self.max_consecutive_loss,
            "max_drawdown": self.max_drawdown,
            "skipped_issues": self.skipped_issues,
            "martin_level_dist": self.martin_level_dist,
            "max_martin_level": self.max_martin_level,
            "records": [r.to_dict() for r in self.records],
        }

    @classmethod
    def from_dict(cls, d: dict) -> BacktestResult:
        martin_dist = d.get("martin_level_dist")
        if martin_dist is not None:
            martin_dist = {int(k): v for k, v in martin_dist.items()}
        return cls(
            total_pnl=d["total_pnl"],
            total_bet_amount=d["total_bet_amount"],
            total_issues=d["total_issues"],
            win_count=d["win_count"],
            lose_count=d["lose_count"],
            win_rate=d["win_rate"],
            max_consecutive_loss=d["max_consecutive_loss"],
            max_drawdown=d["max_drawdown"],
            skipped_issues=d["skipped_issues"],
            martin_level_dist=martin_dist,
            max_martin_level=d.get("max_martin_level"),
            records=[BacktestRecord.from_dict(r) for r in d.get("records", [])],
        )



# ---------------------------------------------------------------------------
# 回测引擎
# ---------------------------------------------------------------------------

# DX1/DX2 和值 13/14 退款的 key_code 集合
_REFUND_KEY_CODES = {"DX1", "DX2"}
_REFUND_SUM_VALUES = {13, 14}


class BacktestEngine:
    """回测引擎 - 使用历史数据模拟策略运行"""

    def __init__(self, jnd28_db_path: str):
        self._db_path = jnd28_db_path

    def run(self, config: BacktestConfig) -> BacktestResult:
        """执行回测（同步方法，在线程池中调用）"""
        strategy = self._create_strategy(config)
        rows = self._load_history(config.start_issue, config.end_issue)

        # 计算理论期号范围内的总期数（用于 skipped_issues）
        try:
            expected_count = int(config.end_issue) - int(config.start_issue) + 1
        except ValueError:
            expected_count = len(rows)

        records: list[BacktestRecord] = []
        cumulative_pnl = 0

        for row in rows:
            issue, d1, d2, d3, sum_value = row
            balls = [d1, d2, d3]

            # 获取马丁层级（在 compute 之前记录）
            martin_level: int | None = None
            if config.strategy_type == "martin" and isinstance(strategy, MartinStrategyImpl):
                martin_level = strategy.level

            # 构造 StrategyContext 并获取投注指令
            ctx = StrategyContext(
                current_issue=issue,
                history=[LotteryResult(issue=issue, balls=balls, sum_value=sum_value)],
                balance=10_000_000,  # 回测不限余额
            )
            instructions = strategy.compute(ctx)

            # 计算本期盈亏
            total_amount = 0
            total_pnl = 0
            any_win = False
            any_refund = False

            for inst in instructions:
                amount = inst.amount
                total_amount += amount
                odds = config.odds_map.get(inst.key_code, 0)

                # 和值 13/14 退款判定
                if inst.key_code in _REFUND_KEY_CODES and sum_value in _REFUND_SUM_VALUES:
                    # 退款：pnl = 0
                    any_refund = True
                    continue

                win = check_win(inst.key_code, balls, sum_value)
                if win:
                    pnl = amount * odds // 10000 - amount
                    any_win = True
                else:
                    pnl = -amount
                total_pnl += pnl

            cumulative_pnl += total_pnl

            records.append(BacktestRecord(
                issue=issue,
                amount=total_amount,
                pnl=total_pnl,
                cumulative_pnl=cumulative_pnl,
                martin_level=martin_level,
            ))

            # 通知策略结果
            if any_refund and not any_win and total_pnl == 0:
                # 全部退款
                strategy.on_result(-1, 0)
            elif any_win or total_pnl > 0:
                strategy.on_result(1, total_pnl)
            else:
                strategy.on_result(0, total_pnl)

        skipped = max(0, expected_count - len(rows))
        summary = self._compute_summary(records, config.strategy_type)

        return BacktestResult(
            total_pnl=summary["total_pnl"],
            total_bet_amount=summary["total_bet_amount"],
            total_issues=summary["total_issues"],
            win_count=summary["win_count"],
            lose_count=summary["lose_count"],
            win_rate=summary["win_rate"],
            max_consecutive_loss=summary["max_consecutive_loss"],
            max_drawdown=summary["max_drawdown"],
            skipped_issues=skipped,
            martin_level_dist=summary["martin_level_dist"],
            max_martin_level=summary["max_martin_level"],
            records=records,
        )

    def _create_strategy(self, config: BacktestConfig) -> BaseStrategy:
        """根据配置创建策略实例（不注入 alert_service）"""
        if config.strategy_type == "flat":
            return FlatStrategyImpl(
                key_codes=config.key_codes,
                base_amount=config.base_amount,
            )
        elif config.strategy_type == "martin":
            if not config.martin_sequence:
                raise ValueError("martin 策略需要 martin_sequence")
            return MartinStrategyImpl(
                key_codes=config.key_codes,
                base_amount=config.base_amount,
                sequence=config.martin_sequence,
            )
        else:
            raise ValueError(f"不支持的策略类型: {config.strategy_type}")

    def _load_history(self, start_issue: str, end_issue: str) -> list[tuple]:
        """从 jnd28.sqlite3 加载历史数据（同步）"""
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                "SELECT issue, d1, d2, d3, sum FROM jnd28_history "
                "WHERE issue >= ? AND issue <= ? ORDER BY issue ASC",
                (start_issue, end_issue),
            )
            return cursor.fetchall()
        finally:
            conn.close()

    @staticmethod
    def _compute_summary(records: list[BacktestRecord], strategy_type: str) -> dict:
        """计算汇总指标"""
        if not records:
            return {
                "total_pnl": 0,
                "total_bet_amount": 0,
                "total_issues": 0,
                "win_count": 0,
                "lose_count": 0,
                "win_rate": 0.0,
                "max_consecutive_loss": 0,
                "max_drawdown": 0,
                "martin_level_dist": None if strategy_type == "flat" else {},
                "max_martin_level": None if strategy_type == "flat" else None,
            }

        total_pnl = sum(r.pnl for r in records)
        total_bet_amount = sum(r.amount for r in records)
        total_issues = len(records)

        win_count = sum(1 for r in records if r.pnl > 0)
        lose_count = sum(1 for r in records if r.pnl <= 0)
        win_rate = win_count / total_issues if total_issues > 0 else 0.0

        # 最大连续亏损
        max_consecutive_loss = 0
        current_loss_streak = 0
        for r in records:
            if r.pnl <= 0:
                current_loss_streak += 1
                max_consecutive_loss = max(max_consecutive_loss, current_loss_streak)
            else:
                current_loss_streak = 0

        # 最大回撤
        max_drawdown = 0
        peak = 0
        for r in records:
            peak = max(peak, r.cumulative_pnl)
            drawdown = peak - r.cumulative_pnl
            max_drawdown = max(max_drawdown, drawdown)

        # 马丁层级分布
        martin_level_dist: dict[int, int] | None = None
        max_martin_level: int | None = None
        if strategy_type == "martin":
            martin_level_dist = {}
            for r in records:
                level = r.martin_level if r.martin_level is not None else 0
                martin_level_dist[level] = martin_level_dist.get(level, 0) + 1
            max_martin_level = max(martin_level_dist.keys()) if martin_level_dist else 0

        return {
            "total_pnl": total_pnl,
            "total_bet_amount": total_bet_amount,
            "total_issues": total_issues,
            "win_count": win_count,
            "lose_count": lose_count,
            "win_rate": win_rate,
            "max_consecutive_loss": max_consecutive_loss,
            "max_drawdown": max_drawdown,
            "martin_level_dist": martin_level_dist,
            "max_martin_level": max_martin_level,
        }
