"""DW3 group and bucket helpers.

This module keeps DW3 domain logic pure and deterministic:
- 16 group tokens (8 BS + 8 OE) and labels
- draw/key-code decoding into (bs_token, oe_token)
- 64 bucket helpers and membership checks
- theoretical units/stake math for effective token sets
"""

from __future__ import annotations

from decimal import Decimal
from numbers import Real
from typing import Iterable

DW3_GROUP_SIZE = 125
DW3_TOTAL_NUMBERS = 1000
DW3_KEY_CODE_PREFIX = "DW3_"

# BS tokens
DW3_BS_BBB = "DW3_BS_BBB"
DW3_BS_BBS = "DW3_BS_BBS"
DW3_BS_BSB = "DW3_BS_BSB"
DW3_BS_BSS = "DW3_BS_BSS"
DW3_BS_SBB = "DW3_BS_SBB"
DW3_BS_SBS = "DW3_BS_SBS"
DW3_BS_SSB = "DW3_BS_SSB"
DW3_BS_SSS = "DW3_BS_SSS"

# OE tokens
DW3_OE_OOO = "DW3_OE_OOO"
DW3_OE_OOE = "DW3_OE_OOE"
DW3_OE_OEO = "DW3_OE_OEO"
DW3_OE_OEE = "DW3_OE_OEE"
DW3_OE_EOO = "DW3_OE_EOO"
DW3_OE_EOE = "DW3_OE_EOE"
DW3_OE_EEO = "DW3_OE_EEO"
DW3_OE_EEE = "DW3_OE_EEE"

DW3_BS_TOKENS: tuple[str, ...] = (
    DW3_BS_BBB,
    DW3_BS_BBS,
    DW3_BS_BSB,
    DW3_BS_BSS,
    DW3_BS_SBB,
    DW3_BS_SBS,
    DW3_BS_SSB,
    DW3_BS_SSS,
)
DW3_OE_TOKENS: tuple[str, ...] = (
    DW3_OE_OOO,
    DW3_OE_OOE,
    DW3_OE_OEO,
    DW3_OE_OEE,
    DW3_OE_EOO,
    DW3_OE_EOE,
    DW3_OE_EEO,
    DW3_OE_EEE,
)
DW3_GROUP_TOKENS: tuple[str, ...] = DW3_BS_TOKENS + DW3_OE_TOKENS

DW3_BS_LABELS: dict[str, str] = {
    DW3_BS_BBB: "Big Big Big",
    DW3_BS_BBS: "Big Big Small",
    DW3_BS_BSB: "Big Small Big",
    DW3_BS_BSS: "Big Small Small",
    DW3_BS_SBB: "Small Big Big",
    DW3_BS_SBS: "Small Big Small",
    DW3_BS_SSB: "Small Small Big",
    DW3_BS_SSS: "Small Small Small",
}
DW3_OE_LABELS: dict[str, str] = {
    DW3_OE_OOO: "Odd Odd Odd",
    DW3_OE_OOE: "Odd Odd Even",
    DW3_OE_OEO: "Odd Even Odd",
    DW3_OE_OEE: "Odd Even Even",
    DW3_OE_EOO: "Even Odd Odd",
    DW3_OE_EOE: "Even Odd Even",
    DW3_OE_EEO: "Even Even Odd",
    DW3_OE_EEE: "Even Even Even",
}
DW3_GROUP_LABELS: dict[str, str] = {**DW3_BS_LABELS, **DW3_OE_LABELS}

DW3_BUCKETS: tuple[tuple[str, str], ...] = tuple(
    (bs_token, oe_token) for bs_token in DW3_BS_TOKENS for oe_token in DW3_OE_TOKENS
)

_DW3_BS_TOKEN_SET = set(DW3_BS_TOKENS)
_DW3_OE_TOKEN_SET = set(DW3_OE_TOKENS)
_DW3_BUCKET_SET = set(DW3_BUCKETS)

_BS_PATTERN_TO_TOKEN = {
    "BBB": DW3_BS_BBB,
    "BBS": DW3_BS_BBS,
    "BSB": DW3_BS_BSB,
    "BSS": DW3_BS_BSS,
    "SBB": DW3_BS_SBB,
    "SBS": DW3_BS_SBS,
    "SSB": DW3_BS_SSB,
    "SSS": DW3_BS_SSS,
}
_OE_PATTERN_TO_TOKEN = {
    "OOO": DW3_OE_OOO,
    "OOE": DW3_OE_OOE,
    "OEO": DW3_OE_OEO,
    "OEE": DW3_OE_OEE,
    "EOO": DW3_OE_EOO,
    "EOE": DW3_OE_EOE,
    "EEO": DW3_OE_EEO,
    "EEE": DW3_OE_EEE,
}
_BS_TOKEN_TO_PATTERN = {token: pattern for pattern, token in _BS_PATTERN_TO_TOKEN.items()}
_OE_TOKEN_TO_PATTERN = {token: pattern for pattern, token in _OE_PATTERN_TO_TOKEN.items()}


def normalize_dw3_number(value: int | str) -> int:
    """Normalize a DW3 number into int in [0, 999]."""
    if isinstance(value, bool):
        raise TypeError("DW3 number must be int or str, got bool")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw or not raw.isdigit():
            raise ValueError(f"Invalid DW3 number: {value!r}")
        if len(raw) > 3:
            raise ValueError(f"DW3 number must be 1..3 digits: {value!r}")
        number = int(raw)
    else:
        raise TypeError(f"DW3 number must be int or str, got {type(value).__name__}")

    if number < 0 or number >= DW3_TOTAL_NUMBERS:
        raise ValueError(f"DW3 number must be in [0, 999], got {number}")
    return number


def format_dw3_number(value: int | str) -> str:
    """Return zero-padded 3-digit DW3 number text."""
    return f"{normalize_dw3_number(value):03d}"


def number_to_dw3_key_code(value: int | str) -> str:
    """Convert number to key code, for example 7 -> DW3_007."""
    return f"{DW3_KEY_CODE_PREFIX}{format_dw3_number(value)}"


def dw3_key_code_to_number(key_code: str) -> int:
    """Parse DW3 key code text into number, for example DW3_007 -> 7."""
    if not isinstance(key_code, str):
        raise TypeError(f"key_code must be str, got {type(key_code).__name__}")
    if not key_code.startswith(DW3_KEY_CODE_PREFIX):
        raise ValueError(f"Invalid DW3 key code: {key_code!r}")
    suffix = key_code[len(DW3_KEY_CODE_PREFIX) :]
    if len(suffix) != 3 or not suffix.isdigit():
        raise ValueError(f"Invalid DW3 key code: {key_code!r}")
    return normalize_dw3_number(suffix)


def decode_draw_to_signals(draw: int | str) -> tuple[str, str]:
    """Decode draw number into (bs_token, oe_token)."""
    number = normalize_dw3_number(draw)
    digits = _digits_of(number)
    bs_pattern = "".join("B" if digit >= 5 else "S" for digit in digits)
    oe_pattern = "".join("O" if digit % 2 else "E" for digit in digits)
    return _BS_PATTERN_TO_TOKEN[bs_pattern], _OE_PATTERN_TO_TOKEN[oe_pattern]


def get_dw3_bucket_index(key_code: str) -> tuple[str, str]:
    """Map DW3 key code to its 64-bucket index: (bs_token, oe_token)."""
    return decode_draw_to_signals(dw3_key_code_to_number(key_code))


def build_dw3_bucket_map() -> dict[tuple[str, str], list[str]]:
    """Build full 64-bucket map: (bs_token, oe_token) -> [DW3_xxx, ...]."""
    bucket_map: dict[tuple[str, str], list[str]] = {
        (bs_token, oe_token): [] for bs_token, oe_token in DW3_BUCKETS
    }
    for number in range(DW3_TOTAL_NUMBERS):
        key_code = number_to_dw3_key_code(number)
        bucket_map[decode_draw_to_signals(number)].append(key_code)
    return bucket_map


def build_dw3_token_map() -> dict[str, list[str]]:
    """Build token map for all 16 tokens: token -> [DW3_xxx, ...]."""
    token_map: dict[str, list[str]] = {token: [] for token in DW3_GROUP_TOKENS}
    for number in range(DW3_TOTAL_NUMBERS):
        key_code = number_to_dw3_key_code(number)
        bs_token, oe_token = decode_draw_to_signals(number)
        token_map[bs_token].append(key_code)
        token_map[oe_token].append(key_code)
    return token_map


def number_matches_bs_token(number: int | str, bs_token: str) -> bool:
    """Return True when number belongs to a BS group token."""
    if bs_token not in _DW3_BS_TOKEN_SET:
        raise ValueError(f"Unknown BS token: {bs_token}")
    digits = _digits_of(normalize_dw3_number(number))
    actual_pattern = "".join("B" if digit >= 5 else "S" for digit in digits)
    return actual_pattern == _BS_TOKEN_TO_PATTERN[bs_token]


def number_matches_oe_token(number: int | str, oe_token: str) -> bool:
    """Return True when number belongs to an OE group token."""
    if oe_token not in _DW3_OE_TOKEN_SET:
        raise ValueError(f"Unknown OE token: {oe_token}")
    digits = _digits_of(normalize_dw3_number(number))
    actual_pattern = "".join("O" if digit % 2 else "E" for digit in digits)
    return actual_pattern == _OE_TOKEN_TO_PATTERN[oe_token]


def number_matches_bucket(number: int | str, bs_token: str, oe_token: str) -> bool:
    """Return True when number belongs to the (bs_token, oe_token) bucket."""
    bucket = (bs_token, oe_token)
    if bucket not in _DW3_BUCKET_SET:
        raise ValueError(f"Unknown DW3 bucket: {bucket}")
    return number_matches_bs_token(number, bs_token) and number_matches_oe_token(
        number, oe_token
    )


def key_code_matches_bucket(key_code: str, bs_token: str, oe_token: str) -> bool:
    """Return True when key code belongs to the (bs_token, oe_token) bucket."""
    return number_matches_bucket(dw3_key_code_to_number(key_code), bs_token, oe_token)


def calculate_dw3_theoretical_totals(
    *,
    stage_amount: Decimal | int | float,
    selected_bs_tokens: Iterable[str],
    selected_oe_tokens: Iterable[str],
    blocked_bs_tokens: Iterable[str] = (),
    blocked_oe_tokens: Iterable[str] = (),
) -> tuple[int, Decimal | int | float]:
    """Return (total_units, total_stake) from effective BS/OE token sets.

    total_units = 125 * (len(effective_bs) + len(effective_oe))
    total_stake = total_units * stage_amount
    """
    if isinstance(stage_amount, bool) or not isinstance(stage_amount, (Real, Decimal)):
        raise TypeError("stage_amount must be int, float, or Decimal")

    selected_bs = _normalize_token_set(
        selected_bs_tokens, _DW3_BS_TOKEN_SET, "selected_bs_tokens"
    )
    selected_oe = _normalize_token_set(
        selected_oe_tokens, _DW3_OE_TOKEN_SET, "selected_oe_tokens"
    )
    blocked_bs = _normalize_token_set(
        blocked_bs_tokens, _DW3_BS_TOKEN_SET, "blocked_bs_tokens"
    )
    blocked_oe = _normalize_token_set(
        blocked_oe_tokens, _DW3_OE_TOKEN_SET, "blocked_oe_tokens"
    )

    effective_bs_count = len(selected_bs - blocked_bs)
    effective_oe_count = len(selected_oe - blocked_oe)
    total_units = DW3_GROUP_SIZE * (effective_bs_count + effective_oe_count)
    return total_units, stage_amount * total_units


def _digits_of(number: int) -> tuple[int, int, int]:
    return number // 100, (number // 10) % 10, number % 10


def _normalize_token_set(
    tokens: Iterable[str], allowed_tokens: set[str], field_name: str
) -> set[str]:
    if isinstance(tokens, str):
        raise TypeError(f"{field_name} must be an iterable of tokens, not str")
    token_set = set(tokens)
    unknown_tokens = token_set - allowed_tokens
    if unknown_tokens:
        bad_tokens = ", ".join(sorted(unknown_tokens))
        raise ValueError(f"Unknown tokens in {field_name}: {bad_tokens}")
    return token_set


__all__ = [
    "DW3_GROUP_SIZE",
    "DW3_TOTAL_NUMBERS",
    "DW3_KEY_CODE_PREFIX",
    "DW3_BS_BBB",
    "DW3_BS_BBS",
    "DW3_BS_BSB",
    "DW3_BS_BSS",
    "DW3_BS_SBB",
    "DW3_BS_SBS",
    "DW3_BS_SSB",
    "DW3_BS_SSS",
    "DW3_OE_OOO",
    "DW3_OE_OOE",
    "DW3_OE_OEO",
    "DW3_OE_OEE",
    "DW3_OE_EOO",
    "DW3_OE_EOE",
    "DW3_OE_EEO",
    "DW3_OE_EEE",
    "DW3_BS_TOKENS",
    "DW3_OE_TOKENS",
    "DW3_GROUP_TOKENS",
    "DW3_BS_LABELS",
    "DW3_OE_LABELS",
    "DW3_GROUP_LABELS",
    "DW3_BUCKETS",
    "normalize_dw3_number",
    "format_dw3_number",
    "number_to_dw3_key_code",
    "dw3_key_code_to_number",
    "decode_draw_to_signals",
    "get_dw3_bucket_index",
    "build_dw3_bucket_map",
    "build_dw3_token_map",
    "number_matches_bs_token",
    "number_matches_oe_token",
    "number_matches_bucket",
    "key_code_matches_bucket",
    "calculate_dw3_theoretical_totals",
]
