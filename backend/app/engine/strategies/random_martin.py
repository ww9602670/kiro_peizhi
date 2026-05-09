"""PC28 随机马丁并发策略

G 组独立马丁序列并发下注，聚合后统一提交。

- 每组按 chase_step % N 循环使用方案中对应的 PeriodBet
- 按球聚合各组金额；全覆盖 10 号码时执行 min-subtraction
- on_result 通过 key_code 解析获胜组；下期 compute 首先批量处理上期 LOSE 组
- bust（chase_step 达到 M 未中）后立即 reset，继续运行（bust_count+1）
- 余额不足由 executor/risk 层处理，无需策略内部检查
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from app.engine.random_plan_generator import Plan
from app.engine.strategies.base import (
    BaseStrategy,
    BetInstruction,
    StrategyContext,
)
from app.engine.strategies.registry import register_strategy

logger = logging.getLogger(__name__)


@dataclass
class GroupLiveState:
    chase_step: int = 0    # 当前追投进度 (0-based，到 M 时 bust)
    martin_level: int = 0  # 马丁倍率档位 (0-based)
    bust_count: int = 0    # 累计爆掉次数


@register_strategy("random_martin")
class RandomMartinStrategy(BaseStrategy):
    """PC28 随机马丁并发策略。

    Args:
        groups: 选定的 Plan 列表（已按 group_ids 筛选，保持原始顺序）
        N: 每组期数（方案循环周期）
        M: 追投上限（chase_step 到达 M 触发 bust 并 reset）
        base_unit_fen: 基础单位金额（分，INTEGER1=100）
        martin_multiplier: 马丁倍数（每次连亏 martin_level+1）
        runtime_state: 恢复点 {"groups": [{"chase_step", "martin_level", "bust_count"}, ...]}
        alert_service: AlertService 实例（可选，供后续扩展）
        operator_id: 操作者 ID
        strategy_name: 策略名称
    """

    def __init__(
        self,
        groups: list[Plan],
        N: int,
        M: int,
        base_unit_fen: int,
        martin_multiplier: float,
        runtime_state: dict | None = None,
        alert_service=None,
        operator_id: int = 0,
        strategy_name: str = "random_martin",
    ) -> None:
        if not groups:
            raise ValueError("groups 不能为空")
        if N < 1:
            raise ValueError("N 必须 >= 1")
        if M < N:
            raise ValueError("M 必须 >= N")
        if base_unit_fen <= 0:
            raise ValueError("base_unit_fen 必须 > 0")
        if martin_multiplier < 1.0:
            raise ValueError("martin_multiplier 必须 >= 1.0")

        self._groups = groups
        self._N = N
        self._M = M
        self._base_unit_fen = base_unit_fen
        self._martin_multiplier = martin_multiplier
        self._alert_service = alert_service
        self._operator_id = operator_id
        self._strategy_name = strategy_name

        # 恢复或初始化运行时状态
        saved = (runtime_state or {}).get("groups", [])
        self._group_states: list[GroupLiveState] = []
        for i in range(len(groups)):
            if i < len(saved):
                s = saved[i]
                self._group_states.append(GroupLiveState(
                    chase_step=int(s.get("chase_step", 0)),
                    martin_level=int(s.get("martin_level", 0)),
                    bust_count=int(s.get("bust_count", 0)),
                ))
            else:
                self._group_states.append(GroupLiveState())

        # 期间状态（每次 compute 重置）
        # key=(ball, num)，value=贡献该 key_code 的组 ID 集合（仅记录已下注的 key_code）
        self._contribution_map: dict[tuple[int, int], set[int]] = defaultdict(set)
        self._won_groups: set[int] = set()
        self._period_initialized: bool = False  # 防止首期错误触发 _finalize

    def name(self) -> str:
        return "random_martin"

    # ------------------------------------------------------------------
    # 主逻辑
    # ------------------------------------------------------------------

    def compute(self, ctx: StrategyContext) -> list[BetInstruction]:
        """计算本期下注指令。

        流程：
        1. 处理上期未 WIN 的组为 LOSE（首期跳过）
        2. 各组贡献原始金额到 (ball, num) 桶
        3. 按球聚合；全覆盖 10 号码时做 min-subtraction
        4. 仅对有正值金额的 key_code 生成 BetInstruction，并记录 _contribution_map
        """
        if self._period_initialized:
            self._finalize_previous_period()

        self._period_initialized = True
        self._contribution_map = defaultdict(set)
        self._won_groups = set()

        # 步骤 2：收集各组贡献
        raw_totals: dict[tuple[int, int], float] = defaultdict(float)
        raw_contrib: dict[tuple[int, int], set[int]] = defaultdict(set)

        for g, state in enumerate(self._group_states):
            period_idx = state.chase_step % self._N
            period_bet = self._groups[g].periods[period_idx]
            amount_per_num = self._base_unit_fen * (self._martin_multiplier ** state.martin_level)
            ball = period_bet.ball  # 1/2/3
            for num in period_bet.numbers:
                raw_totals[(ball, num)] += amount_per_num
                raw_contrib[(ball, num)].add(g)

        # 步骤 3+4：按球聚合 + min-subtraction + 生成指令
        instructions: list[BetInstruction] = []
        for ball in (1, 2, 3):
            sub: dict[int, float] = {
                num: amt for (b, num), amt in raw_totals.items() if b == ball
            }
            if not sub:
                continue
            if len(sub) == 10:
                min_v = min(sub.values())
                sub = {num: amt - min_v for num, amt in sub.items() if amt - min_v > 0}
            for num, amount in sub.items():
                if amount <= 0:
                    continue
                instructions.append(
                    BetInstruction(key_code=f"B{ball}QH{num}", amount=int(amount))
                )
                # 记录贡献此 key_code 的组（min-subtraction 后仍有金额才记录）
                for g in raw_contrib.get((ball, num), set()):
                    self._contribution_map[(ball, num)].add(g)

        return instructions

    def on_result(
        self,
        is_win: Optional[int],
        pnl: int,
        key_code: str | None = None,
        martin_level: int | None = None,
    ) -> None:
        """处理单个结算订单。

        仅处理 is_win=1 的情况：解析 key_code 获取 (ball, num)，
        将贡献此 key_code 的组标记为 WIN 并 reset。
        """
        if is_win != 1 or not key_code or "QH" not in key_code:
            return

        try:
            # "B1QH5" → ball=1, num=5
            ball_str, num_str = key_code[1:].split("QH")
            ball = int(ball_str)
            num = int(num_str)
        except (ValueError, IndexError):
            logger.warning("random_martin: 无法解析 key_code=%s", key_code)
            return

        for g in self._contribution_map.get((ball, num), set()):
            if g not in self._won_groups:
                self._won_groups.add(g)
                self._apply_win(g)

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _finalize_previous_period(self) -> None:
        """将上期未 WIN 的所有组记为 LOSE，更新追投进度。"""
        for g in range(len(self._group_states)):
            if g not in self._won_groups:
                self._apply_lose(g)

    def _apply_win(self, g: int) -> None:
        state = self._group_states[g]
        state.chase_step = 0
        state.martin_level = 0

    def _apply_lose(self, g: int) -> None:
        state = self._group_states[g]
        state.chase_step += 1
        state.martin_level += 1
        if state.chase_step >= self._M:
            state.bust_count += 1
            state.chase_step = 0
            state.martin_level = 0
            logger.debug(
                "random_martin: bust strategy=%s group=%d bust_count=%d",
                self._strategy_name, g, state.bust_count,
            )

    # ------------------------------------------------------------------
    # 外部查询接口
    # ------------------------------------------------------------------

    def get_bust_events(self) -> list[dict]:
        """返回 bust_count > 0 的组信息，用于前端展示。"""
        return [
            {"group_id": g, "bust_count": s.bust_count}
            for g, s in enumerate(self._group_states)
            if s.bust_count > 0
        ]

    def get_config_snapshot(self) -> dict:
        """返回当前运行时状态，供 manager.py 持久化 strategy_config。"""
        return {
            "groups": [
                {
                    "chase_step": s.chase_step,
                    "martin_level": s.martin_level,
                    "bust_count": s.bust_count,
                }
                for s in self._group_states
            ]
        }
