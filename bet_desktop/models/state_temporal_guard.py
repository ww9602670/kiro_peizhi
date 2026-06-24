"""Timestamped state snapshots and stale-data circuit breakers.

The guard is intentionally independent from the UI. It validates only
machine-captured state freshness and safety before any live workflow can even
reach the fail-closed physical-click boundary.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from bet_desktop.core.exceptions import AutomationErrorCode, AutomationException


def now_ms() -> int:
    """Return wall-clock milliseconds for cross-process freshness checks."""

    return time.time_ns() // 1_000_000


class StaleDataPanic(AutomationException):
    """Raised when a precheck tries to use an old state frame."""

    def __init__(self, *, instance_id: str, age_ms: int, threshold_ms: int) -> None:
        super().__init__(
            AutomationErrorCode.PRECHECK_FAILED,
            "temporal state is stale",
            {
                "instance_id": instance_id,
                "age_ms": age_ms,
                "threshold_ms": threshold_ms,
                "circuit_breaker": "stale_frame",
            },
        )


class TemporalStateMissing(AutomationException):
    """Raised when no trusted temporal state is available."""

    def __init__(self, instance_id: str) -> None:
        super().__init__(
            AutomationErrorCode.PRECHECK_FAILED,
            "temporal state is missing",
            {"instance_id": instance_id, "circuit_breaker": "missing_state"},
        )


@dataclass(frozen=True)
class TemporalStateSnapshot:
    """One trusted state sample captured by a worker process."""

    instance_id: str
    batch_id: str
    exact_countdown: int
    ocr_balance: str = ""
    timestamp_captured_ms: int = field(default_factory=now_ms)
    source: str = "unknown"
    ws_connected: bool = False
    page_alive: bool = True
    frame_id: int = 0
    confidence: float = 0.0
    safe_summary: dict[str, Any] = field(default_factory=dict)

    def age_ms(self, *, current_ms: int | None = None) -> int:
        return max(0, int((current_ms if current_ms is not None else now_ms()) - self.timestamp_captured_ms))

    def is_fresh(self, *, threshold_ms: int = 300, current_ms: int | None = None) -> bool:
        return self.age_ms(current_ms=current_ms) <= threshold_ms

    def to_safe_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["age_ms"] = self.age_ms()
        return data

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "TemporalStateSnapshot":
        return cls(
            instance_id=str(payload.get("instance_id", "")),
            batch_id=str(payload.get("batch_id", "")),
            exact_countdown=int(payload.get("exact_countdown", payload.get("countdown_seconds", -1))),
            ocr_balance=str(payload.get("ocr_balance", payload.get("balance_text", ""))),
            timestamp_captured_ms=int(payload.get("timestamp_captured_ms", now_ms())),
            source=str(payload.get("source", "mapping")),
            ws_connected=bool(payload.get("ws_connected", False)),
            page_alive=bool(payload.get("page_alive", True)),
            frame_id=int(payload.get("frame_id", 0)),
            confidence=float(payload.get("confidence", 0.0)),
            safe_summary=dict(payload.get("safe_summary", {}) or {}),
        )


@dataclass(frozen=True)
class TemporalGuardConfig:
    stale_threshold_ms: int = 300
    min_countdown_seconds: int = 3
    require_ws_connected: bool = True
    require_page_alive: bool = True


class TemporalStateGuard:
    """Fail-closed validator for temporal state snapshots."""

    def __init__(self, config: TemporalGuardConfig | None = None) -> None:
        self.config = config or TemporalGuardConfig()

    def validate_snapshot(
        self,
        snapshot: TemporalStateSnapshot | dict[str, Any] | None,
        *,
        expected_batch_id: str | None = None,
        current_ms: int | None = None,
    ) -> TemporalStateSnapshot:
        if snapshot is None:
            raise TemporalStateMissing(expected_batch_id or "unknown")
        if isinstance(snapshot, dict):
            snapshot = TemporalStateSnapshot.from_mapping(snapshot)

        age = snapshot.age_ms(current_ms=current_ms)
        if age > self.config.stale_threshold_ms:
            raise StaleDataPanic(
                instance_id=snapshot.instance_id,
                age_ms=age,
                threshold_ms=self.config.stale_threshold_ms,
            )
        if self.config.require_page_alive and not snapshot.page_alive:
            raise AutomationException(
                AutomationErrorCode.PAGE_FROZEN,
                "page is not alive",
                snapshot.to_safe_dict(),
            )
        if self.config.require_ws_connected and not snapshot.ws_connected:
            raise AutomationException(
                AutomationErrorCode.WEBSOCKET_INTERRUPTED,
                "websocket is not connected",
                snapshot.to_safe_dict(),
            )
        if expected_batch_id and snapshot.batch_id and snapshot.batch_id != expected_batch_id:
            raise AutomationException(
                AutomationErrorCode.BATCH_SWITCHED,
                "batch id changed in temporal guard",
                {
                    "instance_id": snapshot.instance_id,
                    "expected": expected_batch_id,
                    "actual": snapshot.batch_id,
                    "age_ms": age,
                },
            )
        if snapshot.exact_countdown < self.config.min_countdown_seconds:
            raise AutomationException(
                AutomationErrorCode.COUNTDOWN_UNSAFE,
                "countdown is below safety threshold",
                {
                    "instance_id": snapshot.instance_id,
                    "countdown_seconds": snapshot.exact_countdown,
                    "threshold_seconds": self.config.min_countdown_seconds,
                    "age_ms": age,
                },
            )
        return snapshot

    def validate_group(
        self,
        snapshots: dict[str, TemporalStateSnapshot | dict[str, Any]],
        *,
        expected_batch_id: str,
        required_instance_ids: tuple[str, ...] = ("a1", "a2", "a3", "a4"),
    ) -> dict[str, TemporalStateSnapshot]:
        validated: dict[str, TemporalStateSnapshot] = {}
        current = now_ms()
        for instance_id in required_instance_ids:
            item = snapshots.get(instance_id)
            if item is None:
                raise TemporalStateMissing(instance_id)
            validated[instance_id] = self.validate_snapshot(
                item,
                expected_batch_id=expected_batch_id,
                current_ms=current,
            )
        return validated

