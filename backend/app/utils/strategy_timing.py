"""Shared bet timing rules and same-direction conflict helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

BET_TIMING_MIN = 19
BET_TIMING_MAX = 180
SAFE_CLOSE_THRESHOLD = 18
SAME_DIRECTION_MIN_GAP = 20

RED_WAVE_DOUBLE_ALLOWED_CODES = ("B1LM_S", "B2LM_S", "B3LM_S", "DS4")
GREEN_WAVE_SINGLE_ALLOWED_CODES = ("B1LM_D", "B2LM_D", "B3LM_D", "DS3")
WAVE_STRATEGY_ALLOWED_CODES: dict[str, tuple[str, ...]] = {
    "red_wave_double_martin": RED_WAVE_DOUBLE_ALLOWED_CODES,
    "green_wave_single_martin": GREEN_WAVE_SINGLE_ALLOWED_CODES,
}
WAVE_STRATEGY_TYPES = tuple(WAVE_STRATEGY_ALLOWED_CODES.keys())


@dataclass(frozen=True)
class StrategyTimingCandidate:
    """Minimal strategy shape used for bet_timing validation."""

    strategy_id: int | None
    account_id: int
    strategy_type: str
    play_code: str
    bet_timing: int
    normalized_direction_keys: tuple[str, ...]


@dataclass(frozen=True)
class TimingConflict:
    """A same-account same-direction bet_timing conflict."""

    strategy_id: int | None
    conflict_strategy_id: int | None
    account_id: int
    requested_bet_timing: int
    existing_bet_timing: int
    normalized_direction_overlap: tuple[str, ...]
    required_gap_seconds: int = SAME_DIRECTION_MIN_GAP
    suggested_bet_timings: tuple[int, ...] = ()


@dataclass(frozen=True)
class TimingResolution:
    """Resolved timing result for save-time same-direction validation."""

    requested_bet_timing: int
    resolved_bet_timing: int | None
    adjusted: bool
    conflicts: tuple[TimingConflict, ...] = ()
    reason: str | None = None


def is_wave_strategy_type(strategy_type: str) -> bool:
    return strategy_type in WAVE_STRATEGY_ALLOWED_CODES


def normalize_wave_strategy_play_code(strategy_type: str, play_code: str) -> str:
    """Normalize wave-strategy direction codes with the documented ordering."""
    allowed_codes = WAVE_STRATEGY_ALLOWED_CODES.get(strategy_type)
    if allowed_codes is None:
        raise ValueError(f"unsupported wave strategy type: {strategy_type}")

    codes: list[str] = []
    for raw in play_code.split(","):
        code = raw.strip().upper()
        if not code:
            continue
        if code not in allowed_codes:
            raise ValueError(f"invalid wave strategy direction key_code: {code}")
        if code not in codes:
            codes.append(code)

    if not codes:
        raise ValueError("wave strategy direction key_code is required")

    ordered = [code for code in allowed_codes if code in codes]
    return ",".join(ordered)


def normalize_red_wave_double_play_code(play_code: str) -> str:
    return normalize_wave_strategy_play_code("red_wave_double_martin", play_code)


def normalize_green_wave_single_play_code(play_code: str) -> str:
    return normalize_wave_strategy_play_code("green_wave_single_martin", play_code)


def normalize_direction_keys(strategy_type: str, play_code: str) -> tuple[str, ...]:
    """Normalize play_code into comparable direction keys."""
    if is_wave_strategy_type(strategy_type):
        return tuple(normalize_wave_strategy_play_code(strategy_type, play_code).split(","))

    codes: list[str] = []
    for raw in play_code.split(","):
        code = raw.strip().upper()
        if not code:
            continue
        if code not in codes:
            codes.append(code)

    if not codes:
        raise ValueError("play_code is required")

    return tuple(codes)


def build_timing_candidate(
    *,
    strategy_id: int | None,
    account_id: int,
    strategy_type: str,
    play_code: str,
    bet_timing: int,
) -> StrategyTimingCandidate:
    return StrategyTimingCandidate(
        strategy_id=strategy_id,
        account_id=account_id,
        strategy_type=strategy_type,
        play_code=play_code,
        bet_timing=int(bet_timing),
        normalized_direction_keys=normalize_direction_keys(strategy_type, play_code),
    )


def build_candidate_from_row(
    row: Mapping[str, Any],
    *,
    strategy_id: int | None = None,
    account_id: int | None = None,
    strategy_type: str | None = None,
    play_code: str | None = None,
    bet_timing: int | None = None,
) -> StrategyTimingCandidate:
    return build_timing_candidate(
        strategy_id=row.get("id") if strategy_id is None else strategy_id,
        account_id=int(row["account_id"] if account_id is None else account_id),
        strategy_type=str(row["type"] if strategy_type is None else strategy_type),
        play_code=str(row["play_code"] if play_code is None else play_code),
        bet_timing=int(row["bet_timing"] if bet_timing is None else bet_timing),
    )


def suggest_bet_timings(
    requested_bet_timing: int,
    conflicting_timings: Iterable[int],
    *,
    limit: int = 3,
) -> tuple[int, ...]:
    """Suggest a few human-friendly valid timings outside conflict ranges."""
    occupied = sorted({int(timing) for timing in conflicting_timings})
    if not occupied:
        return ()

    valid_candidates: list[int] = []
    range_start: int | None = None
    last_valid: int | None = None
    for timing in range(BET_TIMING_MIN, BET_TIMING_MAX + 1):
        is_valid = all(
            abs(timing - existing) >= SAME_DIRECTION_MIN_GAP for existing in occupied
        )
        if is_valid:
            if range_start is None:
                range_start = timing
            last_valid = timing
            continue
        if range_start is not None and last_valid is not None:
            valid_candidates.extend(
                _range_boundary_candidates(range_start, last_valid, requested_bet_timing)
            )
            range_start = None
            last_valid = None

    if range_start is not None and last_valid is not None:
        valid_candidates.extend(
            _range_boundary_candidates(range_start, last_valid, requested_bet_timing)
        )

    ordered = sorted(
        set(valid_candidates),
        key=lambda timing: (
            abs(timing - requested_bet_timing),
            0 if timing > requested_bet_timing else 1,
            timing,
        ),
    )
    return tuple(ordered[:limit])


def _range_boundary_candidates(
    start: int,
    end: int,
    requested_bet_timing: int,
) -> list[int]:
    candidates: list[int] = []
    if requested_bet_timing <= start:
        timing = start
        while timing <= end and len(candidates) < 3:
            candidates.append(timing)
            timing += SAME_DIRECTION_MIN_GAP
        return candidates

    if requested_bet_timing >= end:
        timing = end
        while timing >= start and len(candidates) < 3:
            candidates.append(timing)
            timing -= SAME_DIRECTION_MIN_GAP
        return candidates

    candidates.append(requested_bet_timing)
    down = requested_bet_timing - SAME_DIRECTION_MIN_GAP
    up = requested_bet_timing + SAME_DIRECTION_MIN_GAP
    while len(candidates) < 3:
        added = False
        if down >= start:
            candidates.append(down)
            down -= SAME_DIRECTION_MIN_GAP
            added = True
        if len(candidates) >= 3:
            break
        if up <= end:
            candidates.append(up)
            up += SAME_DIRECTION_MIN_GAP
            added = True
        if not added:
            break
    return candidates


def collect_timing_conflicts(
    candidate: StrategyTimingCandidate,
    others: Iterable[StrategyTimingCandidate],
) -> list[TimingConflict]:
    """Return same-direction conflicts against the provided candidates."""
    other_list = list(others)
    conflicts: list[TimingConflict] = []
    same_direction_timings: list[int] = []
    overlaps_by_strategy: dict[int | None, tuple[str, ...]] = {}

    candidate_keys = set(candidate.normalized_direction_keys)
    if not candidate_keys:
        return conflicts

    for other in other_list:
        if other.account_id != candidate.account_id:
            continue
        if candidate.strategy_id is not None and other.strategy_id == candidate.strategy_id:
            continue

        overlap = tuple(
            key
            for key in candidate.normalized_direction_keys
            if key in other.normalized_direction_keys
        )
        if not overlap:
            continue

        same_direction_timings.append(other.bet_timing)

        if abs(candidate.bet_timing - other.bet_timing) >= SAME_DIRECTION_MIN_GAP:
            continue

        overlaps_by_strategy[other.strategy_id] = overlap

    suggestions = suggest_bet_timings(candidate.bet_timing, same_direction_timings)
    for other in other_list:
        overlap = overlaps_by_strategy.get(other.strategy_id)
        if not overlap:
            continue
        conflicts.append(
            TimingConflict(
                strategy_id=candidate.strategy_id,
                conflict_strategy_id=other.strategy_id,
                account_id=candidate.account_id,
                requested_bet_timing=candidate.bet_timing,
                existing_bet_timing=other.bet_timing,
                normalized_direction_overlap=overlap,
                suggested_bet_timings=suggestions,
            )
        )
    return conflicts


def find_nearest_available_timing(
    requested_bet_timing: int,
    conflicting_timings: Iterable[int],
) -> int | None:
    """Find the nearest in-window timing that satisfies the minimum gap."""
    requested = int(requested_bet_timing)
    occupied = sorted({int(timing) for timing in conflicting_timings})
    if not occupied:
        return max(BET_TIMING_MIN, min(requested, BET_TIMING_MAX))

    valid_timings = [
        timing
        for timing in range(BET_TIMING_MIN, BET_TIMING_MAX + 1)
        if all(abs(timing - existing) >= SAME_DIRECTION_MIN_GAP for existing in occupied)
    ]
    if not valid_timings:
        return None

    return min(
        valid_timings,
        key=lambda timing: (
            abs(timing - requested),
            0 if timing > requested else 1,
            timing,
        ),
    )


def resolve_timing_conflicts(
    candidate: StrategyTimingCandidate,
    others: Iterable[StrategyTimingCandidate],
) -> TimingResolution:
    """Resolve candidate bet_timing against same-direction conflicts."""
    other_list = list(others)
    conflicts = tuple(collect_timing_conflicts(candidate, other_list))
    if not conflicts:
        return TimingResolution(
            requested_bet_timing=candidate.bet_timing,
            resolved_bet_timing=candidate.bet_timing,
            adjusted=False,
            conflicts=(),
        )

    occupied_timings = [
        other.bet_timing
        for other in other_list
        if other.account_id == candidate.account_id
        and (candidate.strategy_id is None or other.strategy_id != candidate.strategy_id)
        and any(
            key in other.normalized_direction_keys
            for key in candidate.normalized_direction_keys
        )
    ]
    resolved = find_nearest_available_timing(
        candidate.bet_timing,
        occupied_timings,
    )
    if resolved is None:
        return TimingResolution(
            requested_bet_timing=candidate.bet_timing,
            resolved_bet_timing=None,
            adjusted=False,
            conflicts=conflicts,
            reason="NO_AVAILABLE_BET_TIMING",
        )

    return TimingResolution(
        requested_bet_timing=candidate.bet_timing,
        resolved_bet_timing=resolved,
        adjusted=resolved != candidate.bet_timing,
        conflicts=conflicts,
    )


def summarize_timing_conflicts(conflicts: Iterable[TimingConflict]) -> dict[str, Any]:
    conflict_list = list(conflicts)
    if not conflict_list:
        return {}

    first = conflict_list[0]
    return {
        "kind": "BET_TIMING_CONFLICT",
        "account_id": first.account_id,
        "strategy_id": first.strategy_id,
        "conflict_strategy_ids": [
            conflict.conflict_strategy_id for conflict in conflict_list if conflict.conflict_strategy_id is not None
        ],
        "required_gap_seconds": first.required_gap_seconds,
        "requested_bet_timing": first.requested_bet_timing,
        "conflict_bet_timings": sorted(
            {conflict.existing_bet_timing for conflict in conflict_list}
        ),
        "overlap_direction_keys": sorted(
            {
                key
                for conflict in conflict_list
                for key in conflict.normalized_direction_overlap
            }
        ),
        "suggested_bet_timings": list(first.suggested_bet_timings),
    }
