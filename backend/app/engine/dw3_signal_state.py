from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

try:
    from app.utils import dw3_groups as _dw3_groups
except Exception:  # pragma: no cover - optional during package-split work
    _dw3_groups = None


def _build_token_space(prefix: str, alphabet: str) -> tuple[str, ...]:
    return tuple(
        f"{prefix}{a}{b}{c}"
        for a in alphabet
        for b in alphabet
        for c in alphabet
    )


_DEFAULT_BS_TOKENS = _build_token_space("DW3_BS_", "BS")
_DEFAULT_OE_TOKENS = _build_token_space("DW3_OE_", "OE")


def _load_token_space(name: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
    if _dw3_groups is None:
        return fallback
    tokens = getattr(_dw3_groups, name, None)
    if not tokens:
        return fallback
    return tuple(str(token) for token in tokens)


DW3_BS_TOKENS: tuple[str, ...] = _load_token_space("DW3_BS_TOKENS", _DEFAULT_BS_TOKENS)
DW3_OE_TOKENS: tuple[str, ...] = _load_token_space("DW3_OE_TOKENS", _DEFAULT_OE_TOKENS)

_ISSUE_KEYS = (
    "issue",
    "issue_no",
    "issueNo",
    "expect",
    "period",
    "draw_issue",
)
_DRAW_KEYS = (
    "draw",
    "draw_code",
    "open_code",
    "openCode",
    "result",
    "number",
    "winning_number",
    "win_number",
    "nums",
    "balls",
)
_SEQ_KEYS = ("closed_seq", "closedSeq", "seq", "sequence")


@dataclass
class DW3SignalState:
    platform_type: str
    latest_closed_issue: str | None = None
    latest_closed_seq: int = 0
    last_seen_bs: dict[str, int] = field(default_factory=dict)
    last_seen_oe: dict[str, int] = field(default_factory=dict)


def create_dw3_signal_state(platform_type: str) -> DW3SignalState:
    return DW3SignalState(platform_type=platform_type)


def bootstrap_dw3_signal_state(
    platform_type: str,
    lottery_history: Iterable[Any],
) -> DW3SignalState:
    state = create_dw3_signal_state(platform_type)
    return update_dw3_signal_state_from_history(state, lottery_history)


def update_dw3_signal_state_from_history(
    state: DW3SignalState,
    lottery_history: Iterable[Any],
) -> DW3SignalState:
    for entry in lottery_history:
        parsed = _extract_history_entry(entry)
        if parsed is None:
            continue
        issue, draw, closed_seq = parsed
        update_dw3_signal_state_with_draw(
            state=state,
            issue=issue,
            draw=draw,
            closed_seq=closed_seq,
        )
    return state


def update_dw3_signal_state_with_draw(
    *,
    state: DW3SignalState,
    issue: str | int | None,
    draw: Any,
    closed_seq: int | None = None,
) -> DW3SignalState:
    next_seq = int(closed_seq) if closed_seq is not None else state.latest_closed_seq + 1
    if next_seq <= state.latest_closed_seq:
        return state

    bs_token, oe_token = _decode_draw_to_signals(draw)
    state.latest_closed_seq = next_seq
    state.latest_closed_issue = str(issue) if issue is not None else state.latest_closed_issue
    state.last_seen_bs[bs_token] = next_seq
    state.last_seen_oe[oe_token] = next_seq
    return state


def compute_blocked_groups(
    state: DW3SignalState,
    gate_window_issues: int,
    *,
    bs_tokens: Iterable[str] | None = None,
    oe_tokens: Iterable[str] | None = None,
) -> tuple[set[str], set[str]]:
    if gate_window_issues <= 0 or state.latest_closed_seq <= 0:
        return set(), set()

    blocked_bs: set[str] = set()
    blocked_oe: set[str] = set()

    bs_candidates = tuple(bs_tokens) if bs_tokens is not None else DW3_BS_TOKENS
    oe_candidates = tuple(oe_tokens) if oe_tokens is not None else DW3_OE_TOKENS

    for token in bs_candidates:
        last_seen = state.last_seen_bs.get(token)
        if _is_recent_hit(state.latest_closed_seq, last_seen, gate_window_issues):
            blocked_bs.add(token)

    for token in oe_candidates:
        last_seen = state.last_seen_oe.get(token)
        if _is_recent_hit(state.latest_closed_seq, last_seen, gate_window_issues):
            blocked_oe.add(token)

    return blocked_bs, blocked_oe


def compute_effective_groups(
    selected_bs: Iterable[str],
    selected_oe: Iterable[str],
    blocked_bs: Iterable[str],
    blocked_oe: Iterable[str],
) -> tuple[set[str], set[str]]:
    selected_bs_set = set(selected_bs)
    selected_oe_set = set(selected_oe)
    return selected_bs_set - set(blocked_bs), selected_oe_set - set(blocked_oe)


def _is_recent_hit(
    latest_closed_seq: int,
    last_seen_seq: int | None,
    gate_window_issues: int,
) -> bool:
    return last_seen_seq is not None and (latest_closed_seq - last_seen_seq) < gate_window_issues


def _extract_history_entry(
    entry: Any,
) -> tuple[str | int | None, Any, int | None] | None:
    if entry is None:
        return None

    if isinstance(entry, Mapping):
        issue = _first_present(entry, _ISSUE_KEYS)
        draw = _first_present(entry, _DRAW_KEYS)
        if draw is None:
            return None
        seq = _coerce_int(_first_present(entry, _SEQ_KEYS))
        return issue, draw, seq

    if isinstance(entry, (tuple, list)):
        if len(entry) == 2:
            return entry[0], entry[1], None
        if len(entry) >= 3:
            return entry[0], entry[1], _coerce_int(entry[2])
        return None

    issue = _first_attr(entry, _ISSUE_KEYS)
    draw = _first_attr(entry, _DRAW_KEYS)
    if draw is None:
        return None
    seq = _coerce_int(_first_attr(entry, _SEQ_KEYS))
    return issue, draw, seq


def _first_present(mapping: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _first_attr(obj: Any, keys: Sequence[str]) -> Any:
    for key in keys:
        if hasattr(obj, key):
            return getattr(obj, key)
    return None


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _decode_draw_to_signals(draw: Any) -> tuple[str, str]:
    normalized_draw = _normalize_draw(draw)
    decode_fn = getattr(_dw3_groups, "decode_draw_to_signals", None) if _dw3_groups else None
    if callable(decode_fn):
        bs_token, oe_token = decode_fn(normalized_draw)
        return str(bs_token), str(oe_token)

    digits = [int(ch) for ch in normalized_draw]
    bs_suffix = "".join("B" if value >= 5 else "S" for value in digits)
    oe_suffix = "".join("O" if value % 2 else "E" for value in digits)
    return f"DW3_BS_{bs_suffix}", f"DW3_OE_{oe_suffix}"


def _normalize_draw(draw: Any) -> str:
    if isinstance(draw, str):
        stripped = draw.strip()
        if len(stripped) == 3 and stripped.isdigit():
            return stripped
        digits = [ch for ch in stripped if ch.isdigit()]
        if len(digits) == 3:
            return "".join(digits)
        raise ValueError(f"Invalid DW3 draw value: {draw!r}")

    if isinstance(draw, int):
        if 0 <= draw <= 999:
            return f"{draw:03d}"
        raise ValueError(f"Invalid DW3 draw value: {draw!r}")

    if isinstance(draw, Sequence):
        if len(draw) != 3:
            raise ValueError(f"Invalid DW3 draw value: {draw!r}")
        normalized_digits: list[str] = []
        for part in draw:
            value = _coerce_int(part)
            if value is None or value < 0 or value > 9:
                raise ValueError(f"Invalid DW3 draw value: {draw!r}")
            normalized_digits.append(str(value))
        return "".join(normalized_digits)

    raise ValueError(f"Invalid DW3 draw value: {draw!r}")


__all__ = [
    "DW3SignalState",
    "DW3_BS_TOKENS",
    "DW3_OE_TOKENS",
    "bootstrap_dw3_signal_state",
    "compute_blocked_groups",
    "compute_effective_groups",
    "create_dw3_signal_state",
    "update_dw3_signal_state_from_history",
    "update_dw3_signal_state_with_draw",
]
