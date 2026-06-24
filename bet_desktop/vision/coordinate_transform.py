"""Runtime coordinate conversion for the observed live Canvas game.

The project keeps visual regions in a normalized game-canvas coordinate system.
At runtime, these coordinates must be converted to Playwright page coordinates
using the actual Canvas or iframe rectangle. This module is read-only; it never
clicks or submits any page action.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CanvasBaseSize:
    width: float
    height: float


@dataclass(frozen=True)
class CanvasRuntimeRect:
    """Canvas position and size in top-level Playwright page CSS pixels."""

    x: float
    y: float
    width: float
    height: float

    def validate(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("runtime rect width/height must be positive")
        if self.x < 0 or self.y < 0:
            raise ValueError("runtime rect x/y must not be negative")


@dataclass(frozen=True)
class RuntimePoint:
    x: float
    y: float


@dataclass(frozen=True)
class CanvasCoordinateTransform:
    """Convert normalized game coordinates into top-level page coordinates."""

    base_size: CanvasBaseSize
    runtime_rect: CanvasRuntimeRect

    def __post_init__(self) -> None:
        if self.base_size.width <= 0 or self.base_size.height <= 0:
            raise ValueError("base size width/height must be positive")
        self.runtime_rect.validate()

    @property
    def scale_x(self) -> float:
        return self.runtime_rect.width / self.base_size.width

    @property
    def scale_y(self) -> float:
        return self.runtime_rect.height / self.base_size.height

    def to_page_point(self, base_x: float, base_y: float) -> RuntimePoint:
        return RuntimePoint(
            x=self.runtime_rect.x + base_x * self.scale_x,
            y=self.runtime_rect.y + base_y * self.scale_y,
        )

    def to_page_bbox(self, bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        x1, y1, x2, y2 = bbox
        p1 = self.to_page_point(x1, y1)
        p2 = self.to_page_point(x2, y2)
        return (p1.x, p1.y, p2.x, p2.y)


def transform_from_viewport(
    *,
    viewport_width: int,
    viewport_height: int,
    base_width: int = 960,
    base_height: int = 620,
) -> CanvasCoordinateTransform:
    """Build a transform when the game Canvas fills the captured viewport."""

    return CanvasCoordinateTransform(
        base_size=CanvasBaseSize(float(base_width), float(base_height)),
        runtime_rect=CanvasRuntimeRect(0.0, 0.0, float(viewport_width), float(viewport_height)),
    )
