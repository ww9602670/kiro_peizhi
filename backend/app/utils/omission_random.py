"""Shared helpers for omission-random strategies."""

from __future__ import annotations

from typing import Any

OMISSION_RANDOM_FLAT_TYPE = "omission_random_flat"
OMISSION_RANDOM_MARTIN_TYPE = "omission_random_martin"
OMISSION_RANDOM_TYPES = (OMISSION_RANDOM_FLAT_TYPE, OMISSION_RANDOM_MARTIN_TYPE)
AI_RANDOM_FLAT_TYPE = "ai_random_flat"
AI_RANDOM_MARTIN_TYPE = "ai_random_martin"
AI_RANDOM_TYPES = (AI_RANDOM_FLAT_TYPE, AI_RANDOM_MARTIN_TYPE)
AI_SAME_RANDOM_FLAT_TYPE = "ai_same_random_flat"
AI_SAME_RANDOM_MARTIN_TYPE = "ai_same_random_martin"
AI_SAME_RANDOM_TYPES = (AI_SAME_RANDOM_FLAT_TYPE, AI_SAME_RANDOM_MARTIN_TYPE)
RANDOM_PICK_TYPES = OMISSION_RANDOM_TYPES + AI_RANDOM_TYPES + AI_SAME_RANDOM_TYPES

OMISSION_WEIGHT_MODE = "omission_plus_random"
AI_RANDOM_WEIGHT_MODE = "pure_random"
DEFAULT_HISTORY_WINDOW = 100
ADAPTIVE_HISTORY_WINDOWS = (100, 50, 10)
WEIGHTED_PICK_COUNT = 3
VALID_PICK_COUNTS = (3, 4, 5, 6)

CATEGORY_ORDER = ("ball1", "ball2", "ball3", "sum")
CATEGORY_LABELS = {
    "ball1": "球1",
    "ball2": "球2",
    "ball3": "球3",
    "sum": "和值",
}
CATEGORY_PLAY_CODES = {
    "ball1": "OMR_BALL1",
    "ball2": "OMR_BALL2",
    "ball3": "OMR_BALL3",
    "sum": "OMR_SUM",
}
PLAY_CODE_CATEGORIES = {value: key for key, value in CATEGORY_PLAY_CODES.items()}


def is_omission_random_type(strategy_type: str | None) -> bool:
    return str(strategy_type or "") in OMISSION_RANDOM_TYPES


def is_ai_random_type(strategy_type: str | None) -> bool:
    return str(strategy_type or "") in AI_RANDOM_TYPES + AI_SAME_RANDOM_TYPES


def is_ai_same_random_type(strategy_type: str | None) -> bool:
    return str(strategy_type or "") in AI_SAME_RANDOM_TYPES


def is_random_pick_type(strategy_type: str | None) -> bool:
    return str(strategy_type or "") in RANDOM_PICK_TYPES


def is_random_pick_martin_type(strategy_type: str | None) -> bool:
    return str(strategy_type or "") in (
        OMISSION_RANDOM_MARTIN_TYPE,
        AI_RANDOM_MARTIN_TYPE,
        AI_SAME_RANDOM_MARTIN_TYPE,
    )


def normalize_categories(raw_categories: Any) -> list[str]:
    if raw_categories is None:
        raise ValueError("categories is required")
    if isinstance(raw_categories, str):
        items = [item.strip() for item in raw_categories.split(",")]
    elif isinstance(raw_categories, (list, tuple, set)):
        items = [str(item).strip() for item in raw_categories]
    else:
        raise ValueError("categories must be a list")

    selected = {item for item in items if item}
    invalid = selected - set(CATEGORY_ORDER)
    if invalid:
        raise ValueError(f"invalid categories: {','.join(sorted(invalid))}")
    ordered = [category for category in CATEGORY_ORDER if category in selected]
    if not ordered:
        raise ValueError("categories is required")
    return ordered


def normalize_pick_count(value: Any) -> int:
    try:
        pick_count = int(value)
    except (TypeError, ValueError):
        raise ValueError("pick_count must be an integer") from None
    if pick_count not in VALID_PICK_COUNTS:
        raise ValueError("pick_count must be 3, 4, 5 or 6")
    return pick_count


def normalize_strategy_config(
    raw_config: Any,
    *,
    weight_mode: str = OMISSION_WEIGHT_MODE,
    allow_sum: bool = True,
) -> dict[str, Any]:
    if not isinstance(raw_config, dict):
        raise ValueError("strategy_config is required")

    if weight_mode not in {OMISSION_WEIGHT_MODE, AI_RANDOM_WEIGHT_MODE}:
        raise ValueError("invalid weight_mode")

    categories = normalize_categories(raw_config.get("categories"))
    if not allow_sum and "sum" in categories:
        raise ValueError("sum category is not supported")

    config = {
        "pick_count": normalize_pick_count(raw_config.get("pick_count")),
        "categories": categories,
        "weight_mode": weight_mode,
    }
    runtime_state = raw_config.get("runtime_state")
    if isinstance(runtime_state, dict):
        config["runtime_state"] = runtime_state
    return config


def build_omission_play_code(categories: list[str]) -> str:
    normalized = normalize_categories(categories)
    return ",".join(CATEGORY_PLAY_CODES[category] for category in normalized)


def parse_omission_play_code(play_code: str) -> list[str]:
    categories: list[str] = []
    for raw in str(play_code or "").split(","):
        token = raw.strip().upper()
        if not token:
            continue
        category = PLAY_CODE_CATEGORIES.get(token)
        if category is None:
            raise ValueError(f"invalid omission random play_code: {token}")
        if category not in categories:
            categories.append(category)
    if not categories:
        raise ValueError("omission random play_code is required")
    return [category for category in CATEGORY_ORDER if category in categories]


def omission_play_code_name(play_code: str) -> str:
    try:
        categories = parse_omission_play_code(play_code)
    except ValueError:
        return play_code
    return ", ".join(CATEGORY_LABELS[category] for category in categories)


def category_for_key_code(key_code: str | None) -> str | None:
    raw = str(key_code or "").strip()
    if raw in CATEGORY_ORDER:
        return raw
    code = raw.upper()
    if code.startswith("B1QH"):
        return "ball1"
    if code.startswith("B2QH"):
        return "ball2"
    if code.startswith("B3QH"):
        return "ball3"
    if code.startswith("HZ"):
        return "sum"
    return None


def key_codes_for_category(category: str) -> list[str]:
    if category == "ball1":
        return [f"B1QH{digit}" for digit in range(10)]
    if category == "ball2":
        return [f"B2QH{digit}" for digit in range(10)]
    if category == "ball3":
        return [f"B3QH{digit}" for digit in range(10)]
    if category == "sum":
        return [f"HZ{value + 1}" for value in range(28)]
    raise ValueError(f"invalid category: {category}")


def value_for_result(category: str, balls: list[int], sum_value: int) -> int | None:
    if category == "ball1":
        return int(balls[0]) if len(balls) > 0 else None
    if category == "ball2":
        return int(balls[1]) if len(balls) > 1 else None
    if category == "ball3":
        return int(balls[2]) if len(balls) > 2 else None
    if category == "sum":
        return int(sum_value)
    return None


def key_code_value(category: str, key_code: str) -> int:
    code = key_code.strip().upper()
    if category in {"ball1", "ball2", "ball3"}:
        return int(code.split("QH", 1)[1])
    if category == "sum":
        return int(code[2:]) - 1
    raise ValueError(f"invalid category: {category}")
