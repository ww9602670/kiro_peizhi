"""PC28 随机方案生成器

规则：
- 组内去重：同一组内，同一个 (ball, numbers) 组合不能在两期中重复出现；
            但相同号码配不同球号 OK，相同球号配不同号码 OK。
- 跨组去重：不同组间，只有完整的 N 期序列完全相同才算重复，
            单个 (ball, numbers) 可在不同组的不同期中重复使用。
- 可行性：N > 3×C(10,K) 时，组内 N 期无法全部不重复，无法满足。
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from itertools import combinations as _combinations

_MAX_ATTEMPTS = 1_000


@dataclass
class PeriodBet:
    period_index: int       # 1-based
    ball: int               # 1/2/3
    numbers: tuple[int, ...]  # 升序，0-9
    mask: int               # 10-bit bitmask


@dataclass
class Plan:
    group_id: int
    periods: list[PeriodBet] = field(default_factory=list)


def generate_plans(
    num_groups: int,
    periods_per_group: int,
    numbers_per_period: int,
    rng: random.Random | None = None,
) -> list[Plan]:
    """生成 num_groups 个方案，每组含 periods_per_group 期。

    Raises:
        ValueError: 参数不可行或超过最大尝试次数
    """
    _check_feasibility(num_groups, periods_per_group, numbers_per_period)

    rng = rng or random.Random()

    # 所有可用的 (ball, numbers) 组合
    all_combos: list[tuple[int, tuple[int, ...]]] = [
        (ball, nums)
        for ball in range(1, 4)
        for nums in map(tuple, _combinations(range(10), numbers_per_period))
    ]

    # 跨组去重：记录已生成的完整序列
    global_seen_sequences: set[tuple] = set()
    plans: list[Plan] = []

    for g in range(num_groups):
        for _ in range(_MAX_ATTEMPTS):
            # 从全部组合中随机选 N 个不重复的作为本组 N 期
            chosen = tuple(rng.sample(all_combos, periods_per_group))
            if chosen not in global_seen_sequences:
                global_seen_sequences.add(chosen)
                periods = [
                    PeriodBet(i + 1, ball, nums, sum(1 << n for n in nums))
                    for i, (ball, nums) in enumerate(chosen)
                ]
                plans.append(Plan(group_id=g, periods=periods))
                break
        else:
            raise ValueError(
                f"无法生成组 {g} 的不重复方案，已重试 {_MAX_ATTEMPTS} 次。"
            )

    return plans


def _check_feasibility(num_groups: int, periods_per_group: int, numbers_per_period: int) -> None:
    # 可用的 (ball, numbers) 组合总数 = 3 × C(10, K)
    total_combos = 3 * math.comb(10, numbers_per_period)

    # 组内去重要求：N 期各取不同的 (ball, numbers)，需要 N ≤ total_combos
    if periods_per_group > total_combos:
        raise ValueError(
            f"每组期数 N={periods_per_group} 超过可用的唯一(球号+号码)组合数 "
            f"3×C(10,{numbers_per_period})={total_combos}，组内去重不可能满足。"
            f"请减少 N 或增大 K。"
        )
    # 跨组去重上限远大于实际需求（P(total_combos, N) 数量级），无需额外检查。
