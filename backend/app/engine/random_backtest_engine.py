"""PC28 随机马丁回测引擎 — NumPy 向量化实现

性能目标：3000 组 × 5000 期 < 10 秒
核心设计：
  - 方案预编码为 NumPy 数组（plan_balls, plan_masks）
  - 每期一次向量操作处理所有 G 组
  - bitmask 位运算实现 O(1) 命中判定
  - threading.Event 支持随时中断
  - progress_cb 每 100 期回调一次供进度更新
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from app.engine.random_plan_generator import Plan

_PROGRESS_INTERVAL = 100    # 每处理 N 期回调一次进度
_KLINE_DEFAULT_WINDOW = 50


# ---------------------------------------------------------------------------
# 结果数据类
# ---------------------------------------------------------------------------

@dataclass
class GroupSummary:
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


@dataclass
class KlineBar:
    index: int
    open: float
    high: float
    low: float
    close: float


@dataclass
class BacktestResult:
    total_equity_curve: list[float]
    abandoned_amount_curve: list[float]
    total_max_drawdown: float
    total_win_count: int
    total_lose_count: int
    total_win_rate: float
    group_summaries: list[GroupSummary]
    kline_data: list[KlineBar]
    issues: list[str]
    processed_issues: int


@dataclass
class BacktestConfig:
    fund_mode: str                  # "shared_total" | "per_group"
    initial_total_balance: float
    initial_balance_per_group: float
    base_unit: float
    martin_multiplier: float
    odds: float
    chase_limit: int
    kline_window: int = _KLINE_DEFAULT_WINDOW


# ---------------------------------------------------------------------------
# 回测引擎
# ---------------------------------------------------------------------------

class RandomBacktestEngine:
    def __init__(self, jnd28_db_path: str):
        self._db_path = jnd28_db_path
        self._stop_event = threading.Event()

    def request_stop(self) -> None:
        self._stop_event.set()

    def reset_stop(self) -> None:
        self._stop_event.clear()

    def run(
        self,
        config: BacktestConfig,
        plans: list[Plan],
        start_issue: str,
        end_issue: str,
        progress_cb: Callable[[int], None] | None = None,
    ) -> BacktestResult:
        """执行回测（同步，在线程池中调用）"""
        rows = self._load_history(start_issue, end_issue)
        if not rows:
            raise ValueError(f"日期范围内无历史数据：{start_issue} ~ {end_issue}")

        G = len(plans)
        N = len(plans[0].periods)
        K = len(plans[0].periods[0].numbers)

        # 预编码方案为 NumPy 数组
        # plan_balls[g, p] = 0-indexed ball（d_vals 列索引）
        # plan_masks[g, p] = 10-bit bitmask
        plan_balls = np.array(
            [[period.ball - 1 for period in plan.periods] for plan in plans],
            dtype=np.int8,
        )  # shape: [G, N]
        plan_masks = np.array(
            [[period.mask for period in plan.periods] for plan in plans],
            dtype=np.int16,
        )  # shape: [G, N]

        # 历史数据数组
        issues_list = [r[0] for r in rows]
        d_vals = np.array([[r[1], r[2], r[3]] for r in rows], dtype=np.int8)  # [T, 3]
        T = len(rows)

        # 初始化状态向量（长度 G）
        if config.fund_mode == "per_group":
            balances = np.full(G, config.initial_balance_per_group, dtype=np.float64)
        else:
            # 共享模式：每组逻辑余额初始化为相等份额（仅用于组统计）
            per_share = config.initial_total_balance / G if G > 0 else 0.0
            balances = np.full(G, per_share, dtype=np.float64)

        # 共享模式用单标量池，独立模式直接用 balances 之和
        if config.fund_mode == "shared_total":
            pool = config.initial_total_balance
        else:
            pool = None  # 不使用

        chase_step = np.zeros(G, dtype=np.int32)
        martin_level = np.zeros(G, dtype=np.int32)
        abandoned = np.zeros(G, dtype=bool)
        abandoned_issues = [""] * G

        # 用于各组统计
        peak_balances = balances.copy()
        max_drawdowns = np.zeros(G, dtype=np.float64)
        win_counts = np.zeros(G, dtype=np.int32)
        lose_counts = np.zeros(G, dtype=np.int32)
        max_consec_loss = np.zeros(G, dtype=np.int32)
        cur_consec_loss = np.zeros(G, dtype=np.int32)

        idx_g = np.arange(G, dtype=np.int32)

        # 输出曲线
        total_equity_curve: list[float] = []
        abandoned_amount_curve: list[float] = []

        processed = 0

        for t in range(T):
            if self._stop_event.is_set():
                break

            issue = issues_list[t]
            d_row = d_vals[t]  # shape: [3]

            active = ~abandoned  # shape: [G]

            if active.any():
                # 当前期使用的方案期索引
                period_idx = chase_step % N  # shape: [G]

                # 取各组对应的球号索引（0=d1, 1=d2, 2=d3）
                balls = plan_balls[idx_g, period_idx]  # shape: [G]
                masks = plan_masks[idx_g, period_idx]  # shape: [G]

                # 取各组对应球的开奖值
                ball_values = d_row[balls]  # shape: [G]

                # bitmask 命中判定
                hits = ((masks >> ball_values) & 1).astype(bool)  # shape: [G]
                hits &= active  # 已遗弃的组不命中

                # 计算投注额（对 active 组）
                bet_per_num = config.base_unit * (config.martin_multiplier ** martin_level)
                total_bet = (K * bet_per_num * active).astype(np.float64)  # 遗弃组 bet=0

                # 计算收益
                payout = bet_per_num * config.odds * hits.astype(np.float64)
                profit = payout - total_bet  # payout - total_bet（未命中时 = -total_bet）

                # 更新余额
                if config.fund_mode == "shared_total":
                    pool += profit.sum()
                    # 各组逻辑余额也更新（用于组统计）
                    balances += profit
                else:
                    balances += profit

                # 更新组统计
                active_hits = hits & active
                active_misses = (~hits) & active

                win_counts += active_hits.astype(np.int32)
                lose_counts += active_misses.astype(np.int32)

                cur_consec_loss = np.where(active_hits, 0, np.where(active, cur_consec_loss + 1, cur_consec_loss))
                max_consec_loss = np.maximum(max_consec_loss, cur_consec_loss)

                # 更新 peak 和 max_drawdown（仅 active 组）
                peak_balances = np.where(active, np.maximum(peak_balances, balances), peak_balances)
                drawdown = peak_balances - balances
                max_drawdowns = np.where(active, np.maximum(max_drawdowns, drawdown), max_drawdowns)

                # 更新 chase_step 和 martin_level
                chase_step = np.where(active_hits, 0, np.where(active, chase_step + 1, chase_step))
                martin_level = np.where(active_hits, 0, np.where(active, martin_level + 1, martin_level))

                # 遗弃判定（chase_step 达到 M，且当前为 active）
                new_abandoned = active & (chase_step >= config.chase_limit)
                if new_abandoned.any():
                    for g in np.where(new_abandoned)[0]:
                        abandoned_issues[g] = issue
                    abandoned |= new_abandoned

            # 记录曲线
            if config.fund_mode == "shared_total":
                total_balance = pool
            else:
                total_balance = float(balances.sum())

            abandoned_total = float(balances[abandoned].sum()) if abandoned.any() else 0.0

            total_equity_curve.append(total_balance)
            abandoned_amount_curve.append(abandoned_total)

            processed += 1
            if progress_cb and processed % _PROGRESS_INTERVAL == 0:
                progress_cb(processed)

        # 最终进度回调
        if progress_cb:
            progress_cb(processed)

        # 计算总资金最大回撤
        total_max_drawdown = _compute_curve_max_drawdown(total_equity_curve)

        # K 线
        kline_data = _compute_kline(total_equity_curve, config.kline_window)

        # 各组摘要
        if config.fund_mode == "shared_total":
            init_balance = config.initial_total_balance / G if G > 0 else 0.0
        else:
            init_balance = config.initial_balance_per_group

        group_summaries: list[GroupSummary] = []
        for g in range(G):
            total_bets_g = win_counts[g] + lose_counts[g]
            wr = float(win_counts[g] / total_bets_g) if total_bets_g > 0 else 0.0
            group_summaries.append(GroupSummary(
                group_id=g,
                abandoned=bool(abandoned[g]),
                abandoned_issue=abandoned_issues[g],
                final_balance=float(balances[g]),
                total_pnl=float(balances[g] - init_balance),
                win_count=int(win_counts[g]),
                lose_count=int(lose_counts[g]),
                win_rate=wr,
                max_consecutive_loss=int(max_consec_loss[g]),
                max_drawdown=float(max_drawdowns[g]),
            ))

        total_win = int(win_counts.sum())
        total_lose = int(lose_counts.sum())
        total_bets_all = total_win + total_lose

        return BacktestResult(
            total_equity_curve=total_equity_curve,
            abandoned_amount_curve=abandoned_amount_curve,
            total_max_drawdown=total_max_drawdown,
            total_win_count=total_win,
            total_lose_count=total_lose,
            total_win_rate=total_win / total_bets_all if total_bets_all > 0 else 0.0,
            group_summaries=group_summaries,
            kline_data=kline_data,
            issues=issues_list[:processed],
            processed_issues=processed,
        )

    def _load_history(self, start_issue: str, end_issue: str) -> list[tuple]:
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                "SELECT issue, d1, d2, d3 FROM jnd28_history "
                "WHERE issue >= ? AND issue <= ? ORDER BY issue ASC",
                (start_issue, end_issue),
            )
            return cursor.fetchall()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _compute_curve_max_drawdown(curve: list[float]) -> float:
    if not curve:
        return 0.0
    peak = curve[0]
    max_dd = 0.0
    for v in curve:
        if v > peak:
            peak = v
        dd = peak - v
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _compute_kline(curve: list[float], window: int) -> list[KlineBar]:
    if not curve or window <= 0:
        return []
    bars: list[KlineBar] = []
    for i in range(0, len(curve), window):
        segment = curve[i: i + window]
        if not segment:
            break
        bars.append(KlineBar(
            index=i // window,
            open=segment[0],
            high=max(segment),
            low=min(segment),
            close=segment[-1],
        ))
    return bars
