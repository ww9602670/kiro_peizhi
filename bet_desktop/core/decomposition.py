"""Chip denomination decomposition helpers."""

from __future__ import annotations

from functools import lru_cache
from typing import Iterable


def decompose_value(amount: int, denominations: Iterable[int], *, max_steps: int = 5) -> list[int]:
    """Return an exact chip sequence using the fewest chips within max_steps."""
    target = int(amount)
    if target <= 0:
        return []
    denoms = tuple(sorted({int(item) for item in denominations if int(item) > 0}, reverse=True))
    if not denoms:
        raise ValueError("denominations must not be empty")

    @lru_cache(maxsize=None)
    def best(remaining: int, steps_left: int) -> tuple[int, ...] | None:
        if remaining == 0:
            return ()
        if remaining < 0 or steps_left <= 0:
            return None
        selected: tuple[int, ...] | None = None
        for denom in denoms:
            child = best(remaining - denom, steps_left - 1)
            if child is None:
                continue
            candidate = tuple(sorted((denom, *child), reverse=True))
            if selected is None:
                selected = candidate
                continue
            if (len(candidate), tuple(-item for item in candidate)) < (
                len(selected),
                tuple(-item for item in selected),
            ):
                selected = candidate
        return selected

    result = best(target, int(max_steps))
    if result is None:
        raise ValueError(f"cannot decompose {target} within {int(max_steps)} steps")
    return list(result)
