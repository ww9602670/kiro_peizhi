from __future__ import annotations

import re
from typing import Any


ROOM_ID_TO_LABEL = {
    "9101": "T001",
    "182020001": "T001",
    "182020002": "T002",
    "182020003": "T003",
    "182020004": "T004",
}


def normalize_room_label(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    match = re.search(r"T\s*0*(\d{1,3})", text, re.IGNORECASE)
    if match:
        return f"T{int(match.group(1)):03d}"
    if re.fullmatch(r"\d{1,3}", text):
        return f"T{int(text):03d}"
    return ""


def room_label_from_room_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    mapped = ROOM_ID_TO_LABEL.get(text)
    if mapped:
        return mapped
    match = re.search(r"(\d{3})$", text)
    if match:
        index = int(match.group(1))
        if 1 <= index <= 4:
            return f"T{index:03d}"
    return ""


def normalize_limit_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"(\d{1,5}(?:\.\d+)?)\s*[-~]\s*(\d{1,5}(?:\.\d+)?)", text)
    if not match:
        return ""
    return f"{match.group(1)}-{match.group(2)}".replace(" ", "")


def cents_limit_label(low: Any, high: Any) -> str:
    low_int = as_int(low)
    high_int = as_int(high)
    if low_int is None or high_int is None or low_int <= 0 or high_int <= 0:
        return ""
    return f"{low_int // 100}-{high_int // 100}"


def as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(str(value).strip()))
    except Exception:
        return None


def as_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes"}:
        return True
    if text in {"0", "false", "no"}:
        return False
    return None

