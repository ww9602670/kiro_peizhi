"""Manual-anchor coordinate calibration for the live game layout.

Coordinates are layout geometry, not packet-stream data. A single operator
anchor click fixes the page-space offset; all chip and betting regions are then
computed from the calibrated base layout.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from bet_desktop.vision.coordinate_transform import (
    CanvasBaseSize,
    CanvasCoordinateTransform,
    CanvasRuntimeRect,
    RuntimePoint,
    transform_from_viewport,
)
from bet_desktop.vision.live_game_regions import BASE_SIZE, LIVE_GAME_REGIONS


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CALIBRATION_DIR = PROJECT_ROOT / "bet_desktop" / "artifacts"
DEFAULT_ANCHOR_NAME = "home_button"

BASE_ANCHORS: dict[str, tuple[float, float]] = {
    "home_button": (35.0, 37.0),
    "table_info": (237.5, 31.0),
    "status_label": (439.0, 132.0),
}


@dataclass(frozen=True)
class CoordinateCalibration:
    instance_id: str
    anchor_name: str
    anchor_x: float
    anchor_y: float
    viewport_width: int
    viewport_height: int
    source_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def calibration_path(instance_id: str, directory: Path | None = None) -> Path:
    safe_id = "".join(ch for ch in str(instance_id or "default") if ch.isalnum() or ch in ("-", "_")) or "default"
    return (directory or DEFAULT_CALIBRATION_DIR) / f"coordinate_calibration_{safe_id}.json"


def load_calibration(instance_id: str, directory: Path | None = None) -> CoordinateCalibration | None:
    path = calibration_path(instance_id, directory)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    try:
        return CoordinateCalibration(
            instance_id=str(data.get("instance_id") or instance_id),
            anchor_name=str(data.get("anchor_name") or DEFAULT_ANCHOR_NAME),
            anchor_x=float(data["anchor_x"]),
            anchor_y=float(data["anchor_y"]),
            viewport_width=int(data.get("viewport_width") or BASE_SIZE[0]),
            viewport_height=int(data.get("viewport_height") or BASE_SIZE[1]),
            source_url=str(data.get("source_url") or ""),
        )
    except Exception:
        return None


def save_calibration(calibration: CoordinateCalibration, directory: Path | None = None) -> Path:
    path = calibration_path(calibration.instance_id, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(calibration.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def coordinates_from_runtime_geometry(
    *,
    viewport_width: int,
    viewport_height: int,
    instance_id: str = "",
    directory: Path | None = None,
    canvas_rect: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    transform = _transform_from_canvas_rect(canvas_rect)
    if transform is not None:
        payload = coordinates_from_transform(transform)
        rect = transform.runtime_rect
        payload["calibration"] = {
            "source": "canvas_runtime_rect",
            "canvas_rect": [
                round(rect.x, 2),
                round(rect.y, 2),
                round(rect.width, 2),
                round(rect.height, 2),
            ],
        }
        return payload

    calibration = load_calibration(instance_id, directory) if instance_id else None
    if calibration is not None:
        transform = transform_from_anchor(
            anchor_name=calibration.anchor_name,
            anchor_x=calibration.anchor_x,
            anchor_y=calibration.anchor_y,
            viewport_width=viewport_width or calibration.viewport_width,
            viewport_height=viewport_height or calibration.viewport_height,
        )
        payload = coordinates_from_transform(transform)
        payload["calibration"] = {
            "source": "manual_anchor",
            "anchor_name": calibration.anchor_name,
            "anchor": [round(calibration.anchor_x, 2), round(calibration.anchor_y, 2)],
            "viewport": [calibration.viewport_width, calibration.viewport_height],
        }
        return payload

    transform = transform_from_viewport(
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        base_width=BASE_SIZE[0],
        base_height=BASE_SIZE[1],
    )
    payload = coordinates_from_transform(transform)
    payload["calibration"] = {"source": "viewport_fallback"}
    return payload


def _transform_from_canvas_rect(canvas_rect: Mapping[str, Any] | None) -> CanvasCoordinateTransform | None:
    if not canvas_rect:
        return None
    try:
        x = max(0.0, float(canvas_rect.get("x", 0.0) or 0.0))
        y = max(0.0, float(canvas_rect.get("y", 0.0) or 0.0))
        width = float(canvas_rect.get("width", canvas_rect.get("w", 0.0)) or 0.0)
        height = float(canvas_rect.get("height", canvas_rect.get("h", 0.0)) or 0.0)
        return CanvasCoordinateTransform(
            base_size=CanvasBaseSize(float(BASE_SIZE[0]), float(BASE_SIZE[1])),
            runtime_rect=CanvasRuntimeRect(x=x, y=y, width=width, height=height),
        )
    except Exception:
        return None


def transform_from_anchor(
    *,
    anchor_name: str,
    anchor_x: float,
    anchor_y: float,
    viewport_width: int,
    viewport_height: int,
) -> CanvasCoordinateTransform:
    base_anchor_x, base_anchor_y = base_anchor_point(anchor_name)
    scale_x = float(viewport_width) / float(BASE_SIZE[0])
    scale_y = float(viewport_height) / float(BASE_SIZE[1])
    return CanvasCoordinateTransform(
        base_size=CanvasBaseSize(float(BASE_SIZE[0]), float(BASE_SIZE[1])),
        runtime_rect=CanvasRuntimeRect(
            x=float(anchor_x) - base_anchor_x * scale_x,
            y=float(anchor_y) - base_anchor_y * scale_y,
            width=float(BASE_SIZE[0]) * scale_x,
            height=float(BASE_SIZE[1]) * scale_y,
        ),
    )


def base_anchor_point(anchor_name: str) -> tuple[float, float]:
    if anchor_name in BASE_ANCHORS:
        return BASE_ANCHORS[anchor_name]
    region = LIVE_GAME_REGIONS.get(anchor_name)
    if not region:
        raise ValueError(f"unknown anchor: {anchor_name}")
    if "center" in region:
        center = region["center"]
        return float(center[0]), float(center[1])
    x1, y1, x2, y2 = region["bbox"]
    return float((x1 + x2) / 2), float((y1 + y2) / 2)


def coordinates_from_transform(transform: CanvasCoordinateTransform) -> dict[str, Any]:
    chips: dict[str, Any] = {}
    bet_regions: dict[str, Any] = {}
    for name, region in LIVE_GAME_REGIONS.items():
        if not (name.startswith("chip_") or name.startswith("bet_")):
            continue
        payload = _region_payload(transform, region)
        if name.startswith("chip_"):
            chips[name] = payload
        else:
            bet_regions[name] = payload
    return {"chips": chips, "bet_regions": bet_regions}


def _region_payload(transform: CanvasCoordinateTransform, region: dict[str, Any]) -> dict[str, Any]:
    bbox = transform.to_page_bbox(region["bbox"])
    if "center" in region:
        center = region["center"]
        point = transform.to_page_point(float(center[0]), float(center[1]))
    else:
        x1, y1, x2, y2 = region["bbox"]
        point = transform.to_page_point(float((x1 + x2) / 2), float((y1 + y2) / 2))
    return {
        "bbox": [round(value, 2) for value in bbox],
        "center": _point_payload(point),
        "label": str(region.get("label", "")),
    }


def _point_payload(point: RuntimePoint) -> list[float]:
    return [round(point.x, 2), round(point.y, 2)]
