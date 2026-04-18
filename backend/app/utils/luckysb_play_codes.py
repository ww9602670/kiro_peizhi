"""LUCKYSB single-ball play code catalog."""

from __future__ import annotations

import re

LUCKYSB_PLATFORM_TYPE = "LUCKYSB"
LUCKYSB_ALLOWED_STRATEGY_TYPES = {"flat", "martin"}
LUCKYSB_PLAY_CODE_PATTERN = re.compile(r"^LUCKYSB_B(10|[1-9])_(10|0[1-9])$")

_RANK_NAMES = {
    1: "冠军",
    2: "亚军",
    3: "第三名",
    4: "第四名",
    5: "第五名",
    6: "第六名",
    7: "第七名",
    8: "第八名",
    9: "第九名",
    10: "第十名",
}


def make_luckysb_play_code(rank: int, car: int) -> str:
    """Build a LUCKYSB internal play code."""
    return f"LUCKYSB_B{rank}_{car:02d}"


def make_luckysb_play_name(rank: int, car: int) -> str:
    """Build the display name for a LUCKYSB single-ball play code."""
    return f"{_RANK_NAMES[rank]} {car:02d}"


LUCKYSB_PLAY_CODE_NAME_MAP: dict[str, str] = {
    make_luckysb_play_code(rank, car): make_luckysb_play_name(rank, car)
    for rank in range(1, 11)
    for car in range(1, 11)
}


LUCKYSB_PLAY_CODE_GROUPS: list[dict] = [
    {
        "group_name": _RANK_NAMES[rank],
        "items": [
            {
                "key_code": make_luckysb_play_code(rank, car),
                "name": make_luckysb_play_name(rank, car),
            }
            for car in range(1, 11)
        ],
    }
    for rank in range(1, 11)
]


def is_luckysb_play_code(play_code: str) -> bool:
    """Return whether play_code is one legal LUCKYSB single-ball code."""
    return play_code in LUCKYSB_PLAY_CODE_NAME_MAP


def get_luckysb_play_code_name(play_code: str) -> str:
    """Return the LUCKYSB display name, or the original code if unknown."""
    return LUCKYSB_PLAY_CODE_NAME_MAP.get(play_code, play_code)


def validate_luckysb_strategy(type_: str, play_code: str) -> None:
    """Validate LUCKYSB strategy constraints."""
    if type_ not in LUCKYSB_ALLOWED_STRATEGY_TYPES:
        raise ValueError("LUCKYSB only supports flat or martin strategies")
    if "," in play_code:
        raise ValueError("LUCKYSB play_code must be a single play code")
    if not LUCKYSB_PLAY_CODE_PATTERN.fullmatch(play_code) or not is_luckysb_play_code(
        play_code
    ):
        raise ValueError("invalid LUCKYSB play_code")
