"""Fixed visual regions for the observed live embedded baccarat layout.

The module only identifies and describes screen regions. It does not click,
submit, or send any betting action.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
import re
from typing import Any, Protocol

import numpy as np
from PIL import Image


BASE_SIZE = (960, 620)

LIVE_GAME_REGIONS: dict[str, dict[str, Any]] = {
    "table_info": {"bbox": (70, 14, 405, 48), "label": "牌局/房间/限红"},
    "game_status": {"bbox": (356, 118, 522, 146), "label": "局数/阶段"},
    "countdown": {"bbox": (565, 106, 602, 146), "label": "倒计时"},
    "balance": {"bbox": (102, 547, 186, 600), "label": "余额"},
    "bet_player": {"bbox": (114, 140, 334, 297), "label": "闲"},
    "bet_banker": {"bbox": (622, 140, 842, 297), "label": "庄"},
    "bet_player_pair": {"bbox": (114, 306, 334, 413), "label": "闲对"},
    "bet_tie": {"bbox": (340, 306, 618, 413), "label": "和"},
    "bet_banker_pair": {"bbox": (623, 306, 842, 413), "label": "庄对"},
    "chip_4": {"bbox": (194, 451, 272, 506), "center": (233, 489), "label": "4"},
    "chip_10": {"bbox": (294, 455, 371, 506), "center": (333, 489), "label": "10"},
    "chip_20": {"bbox": (393, 455, 470, 506), "center": (432, 489), "label": "20"},
    "chip_50": {"bbox": (492, 455, 569, 506), "center": (531, 489), "label": "50"},
    "chip_100": {"bbox": (591, 455, 668, 506), "center": (630, 489), "label": "100"},
    "chip_200": {"bbox": (690, 455, 767, 506), "center": (729, 489), "label": "200"},
}


@dataclass(frozen=True)
class LiveGameVisualSnapshot:
    game_visible: bool
    confidence: float
    regions: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class LiveGameTextSnapshot:
    batch_id: str = ""
    countdown_seconds: int = -1
    balance_text: str = ""
    phase_text: str = ""
    raw_text: dict[str, list[str]] | None = None
    ocr_available: bool = False


class OcrCallable(Protocol):
    def __call__(self, image: Any) -> Any:
        ...


_RAPID_OCR: Any | None = None


def recognize_live_game_layout_from_png(image_bytes: bytes) -> LiveGameVisualSnapshot:
    """Detect whether the current screenshot resembles the calibrated game table."""

    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception:
        return LiveGameVisualSnapshot(game_visible=False, confidence=0.0, regions={})

    width, height = _active_content_dimensions(image)
    regions = _scale_regions(width, height)
    region_names = ("bet_player", "bet_banker", "bet_tie")
    scores: list[float] = []
    hits = 0
    for region_name in region_names:
        crop = np.asarray(image.crop(regions[region_name]["bbox"]), dtype=np.float32)
        if crop.size == 0:
            scores.append(0.0)
            continue
        mean_rgb = crop.reshape(-1, 3).mean(axis=0)
        red, green, blue = mean_rgb
        green_delta = float(green - max(red, blue))
        brightness = float(mean_rgb.mean()) / 255.0
        score = max(0.0, min(1.0, green_delta / 70.0 * 0.8 + brightness * 0.2))
        scores.append(score)
        if green_delta >= 8 and brightness >= 0.16:
            hits += 1

    confidence = max(scores) if scores else 0.0
    game_visible = hits >= 2 or confidence >= 0.32
    return LiveGameVisualSnapshot(game_visible=game_visible, confidence=confidence, regions=regions)


def extract_live_game_text_from_png(
    image_bytes: bytes,
    *,
    ocr_engine: OcrCallable | None = None,
) -> LiveGameTextSnapshot:
    """Read calibrated text regions from a real game screenshot.

    The function is read-only. It never clicks or sends page actions.
    """

    engine = ocr_engine or _get_rapid_ocr()
    if engine is None:
        return LiveGameTextSnapshot(ocr_available=False)

    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception:
        return LiveGameTextSnapshot(ocr_available=True)

    width, height = _active_content_dimensions(image)
    regions = _scale_regions(width, height)
    raw_text = {
        "batch": _ocr_region(engine, image, regions["table_info"]["bbox"], upscale=3),
        "status": _ocr_region(engine, image, regions["game_status"]["bbox"], upscale=3),
        "countdown": _ocr_region(engine, image, regions["countdown"]["bbox"], upscale=4),
        "balance": _ocr_region(engine, image, regions["balance"]["bbox"], upscale=3),
    }
    batch_id = _parse_batch_id(raw_text["batch"])
    balance_text = _parse_balance_text(raw_text["balance"])
    phase_text = " ".join(raw_text["status"]).strip()
    countdown = _parse_countdown(raw_text["countdown"], phase_text=phase_text)
    return LiveGameTextSnapshot(
        batch_id=batch_id,
        countdown_seconds=countdown,
        balance_text=balance_text,
        phase_text=phase_text,
        raw_text=raw_text,
        ocr_available=True,
    )


def _scale_regions(width: int, height: int) -> dict[str, dict[str, Any]]:
    sx = width / BASE_SIZE[0]
    sy = height / BASE_SIZE[1]
    scaled: dict[str, dict[str, Any]] = {}
    for name, data in LIVE_GAME_REGIONS.items():
        x1, y1, x2, y2 = data["bbox"]
        item = {
            "bbox": (
                round(x1 * sx),
                round(y1 * sy),
                round(x2 * sx),
                round(y2 * sy),
            ),
            "label": data["label"],
        }
        if "center" in data:
            cx, cy = data["center"]
            item["center"] = (round(cx * sx), round(cy * sy))
        scaled[name] = item
    return scaled


def _active_content_dimensions(image: Image.Image) -> tuple[int, int]:
    """Return the visible game content size, ignoring bottom letterbox bars."""

    width, height = image.size
    arr = np.asarray(image.convert("RGB"))
    non_black = (arr[:, :, 0] > 12) | (arr[:, :, 1] > 12) | (arr[:, :, 2] > 12)
    row_hits = non_black.sum(axis=1)
    active_rows = np.where(row_hits > max(30, width * 0.04))[0]
    if active_rows.size == 0:
        return width, height
    active_height = int(active_rows.max()) + 1
    if active_height < height * 0.5:
        return width, height
    return width, active_height


def _get_rapid_ocr() -> Any | None:
    global _RAPID_OCR
    if _RAPID_OCR is not None:
        return _RAPID_OCR
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception:
        return None
    _RAPID_OCR = RapidOCR()
    return _RAPID_OCR


def _ocr_region(engine: OcrCallable, image: Image.Image, bbox: tuple[int, int, int, int], *, upscale: int) -> list[str]:
    crop = image.crop(bbox)
    if upscale > 1:
        crop = crop.resize((crop.width * upscale, crop.height * upscale))
    result = engine(np.asarray(crop))
    rows = result[0] if isinstance(result, tuple) else result
    texts: list[str] = []
    if not rows:
        return texts
    for row in rows:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            texts.append(str(row[1]).strip())
    return [text for text in texts if text]


def _parse_batch_id(texts: list[str]) -> str:
    joined = " ".join(texts)
    for pattern in (
        r"牌局编号[：:\s]*([0-9A-Za-z\-]{10,})",
        r"([0-9]{2,}-[0-9]{6,}-[0-9]{6,}-[0-9]+)",
    ):
        match = re.search(pattern, joined)
        if match:
            return match.group(1)
    return ""


def _parse_balance_text(texts: list[str]) -> str:
    candidates: list[str] = []
    for text in texts:
        candidates.extend(re.findall(r"\d+\.\d{1,2}", text))
    return candidates[-1] if candidates else ""


def _parse_countdown(texts: list[str], *, phase_text: str) -> int:
    if any(keyword in phase_text for keyword in ("派彩", "派奖", "发牌", "开牌")):
        return -1
    joined = " ".join(texts)
    if not joined:
        return -1
    if re.search(r"[0-9]", joined) and not re.search(r"(倒计时|剩余|下注|秒)", joined):
        return -1
    match = re.search(r"(\d{1,2})", joined)
    return int(match.group(1)) if match else -1
