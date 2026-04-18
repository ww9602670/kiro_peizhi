from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import re
from typing import Iterable, Mapping, Sequence

try:
    from app.utils import dw3_groups as _dw3_groups
except Exception:  # pragma: no cover - optional during package-split work
    _dw3_groups = None

from app.engine.dw3_signal_state import (
    DW3SignalState,
    compute_blocked_groups,
    compute_effective_groups,
)


@dataclass
class DW3ExecutionPlan:
    selected_bs: set[str] = field(default_factory=set)
    selected_oe: set[str] = field(default_factory=set)
    blocked_bs: set[str] = field(default_factory=set)
    blocked_oe: set[str] = field(default_factory=set)
    effective_bs: set[str] = field(default_factory=set)
    effective_oe: set[str] = field(default_factory=set)
    stage_amount: int = 0
    total_stake: int = 0
    merged_amounts: dict[str, int] = field(default_factory=dict)
    effective_key_count: int = 0
    submit_mode: str = "single_request_required"
    gate_skipped: bool = False
    skip_reason: str | None = None


def build_dw3_execution_plan(
    *,
    state: DW3SignalState | None,
    selected_bs: Iterable[str],
    selected_oe: Iterable[str],
    gate_window_issues: int,
    stage_amount: int,
    submit_mode: str = "single_request_required",
    bucket_map: Mapping[tuple[str, str], Sequence[str]] | None = None,
) -> DW3ExecutionPlan:
    if stage_amount < 0:
        raise ValueError("stage_amount must be >= 0")

    selected_bs_set = set(selected_bs)
    selected_oe_set = set(selected_oe)

    if state is None:
        return _build_skipped_plan(
            selected_bs=selected_bs_set,
            selected_oe=selected_oe_set,
            stage_amount=stage_amount,
            submit_mode=submit_mode,
            skip_reason="dw3_signal_state_unavailable",
        )

    blocked_bs, blocked_oe = compute_blocked_groups(
        state=state,
        gate_window_issues=gate_window_issues,
        bs_tokens=selected_bs_set,
        oe_tokens=selected_oe_set,
    )
    effective_bs, effective_oe = compute_effective_groups(
        selected_bs=selected_bs_set,
        selected_oe=selected_oe_set,
        blocked_bs=blocked_bs,
        blocked_oe=blocked_oe,
    )

    if not effective_bs and not effective_oe:
        return _build_skipped_plan(
            selected_bs=selected_bs_set,
            selected_oe=selected_oe_set,
            blocked_bs=blocked_bs,
            blocked_oe=blocked_oe,
            effective_bs=effective_bs,
            effective_oe=effective_oe,
            stage_amount=stage_amount,
            submit_mode=submit_mode,
            skip_reason="gate_skipped_all_blocked",
        )

    merged_amounts = _build_merged_amounts(
        effective_bs=effective_bs,
        effective_oe=effective_oe,
        stage_amount=stage_amount,
        bucket_map=bucket_map,
    )

    if not merged_amounts:
        return _build_skipped_plan(
            selected_bs=selected_bs_set,
            selected_oe=selected_oe_set,
            blocked_bs=blocked_bs,
            blocked_oe=blocked_oe,
            effective_bs=effective_bs,
            effective_oe=effective_oe,
            stage_amount=stage_amount,
            submit_mode=submit_mode,
            skip_reason="gate_skipped_no_effective_keys",
        )

    total_stake = 125 * stage_amount * (len(effective_bs) + len(effective_oe))
    return DW3ExecutionPlan(
        selected_bs=selected_bs_set,
        selected_oe=selected_oe_set,
        blocked_bs=blocked_bs,
        blocked_oe=blocked_oe,
        effective_bs=effective_bs,
        effective_oe=effective_oe,
        stage_amount=stage_amount,
        total_stake=total_stake,
        merged_amounts=merged_amounts,
        effective_key_count=len(merged_amounts),
        submit_mode=submit_mode,
        gate_skipped=False,
    )


def _build_skipped_plan(
    *,
    selected_bs: set[str],
    selected_oe: set[str],
    stage_amount: int,
    submit_mode: str,
    skip_reason: str,
    blocked_bs: set[str] | None = None,
    blocked_oe: set[str] | None = None,
    effective_bs: set[str] | None = None,
    effective_oe: set[str] | None = None,
) -> DW3ExecutionPlan:
    return DW3ExecutionPlan(
        selected_bs=selected_bs,
        selected_oe=selected_oe,
        blocked_bs=blocked_bs or set(),
        blocked_oe=blocked_oe or set(),
        effective_bs=effective_bs or set(),
        effective_oe=effective_oe or set(),
        stage_amount=stage_amount,
        total_stake=0,
        merged_amounts={},
        effective_key_count=0,
        submit_mode=submit_mode,
        gate_skipped=True,
        skip_reason=skip_reason,
    )


def _build_merged_amounts(
    *,
    effective_bs: set[str],
    effective_oe: set[str],
    stage_amount: int,
    bucket_map: Mapping[tuple[str, str], Sequence[str]] | None,
) -> dict[str, int]:
    merged_amounts: dict[str, int] = {}
    resolved_buckets = bucket_map if bucket_map is not None else _build_dw3_bucket_map()
    for (bs_token, oe_token), key_codes in resolved_buckets.items():
        multiplier = int(bs_token in effective_bs) + int(oe_token in effective_oe)
        if multiplier == 0:
            continue
        amount = stage_amount * multiplier
        for key_code in key_codes:
            merged_amounts[key_code] = merged_amounts.get(key_code, 0) + amount
    return merged_amounts


def _build_dw3_bucket_map() -> Mapping[tuple[str, str], Sequence[str]]:
    build_fn = getattr(_dw3_groups, "build_dw3_bucket_map", None) if _dw3_groups else None
    if callable(build_fn):
        return build_fn()
    return _fallback_bucket_map()


@lru_cache(maxsize=1)
def _fallback_bucket_map() -> dict[tuple[str, str], list[str]]:
    buckets: dict[tuple[str, str], list[str]] = {}
    for number in range(1000):
        key_code = f"DW3_{number:03d}"
        bucket_index = _fallback_bucket_index(key_code)
        buckets.setdefault(bucket_index, []).append(key_code)
    return buckets


def _fallback_bucket_index(key_code: str) -> tuple[str, str]:
    digits = _extract_key_code_digits(key_code)
    bs_suffix = "".join("B" if value >= 5 else "S" for value in digits)
    oe_suffix = "".join("O" if value % 2 else "E" for value in digits)
    return f"DW3_BS_{bs_suffix}", f"DW3_OE_{oe_suffix}"


def _extract_key_code_digits(key_code: str) -> tuple[int, int, int]:
    matched = re.search(r"(\d{3})$", key_code)
    if not matched:
        raise ValueError(f"Invalid DW3 key code: {key_code!r}")
    text = matched.group(1)
    return int(text[0]), int(text[1]), int(text[2])


__all__ = [
    "DW3ExecutionPlan",
    "build_dw3_execution_plan",
]

