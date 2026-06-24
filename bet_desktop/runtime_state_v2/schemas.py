from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from bet_desktop.models.state_temporal_guard import now_ms

from .normalizers import normalize_room_label, room_label_from_room_id


@dataclass(frozen=True)
class RoomSession:
    instance_id: str
    expected_room_id: str = ""
    expected_room_label: str = ""
    active_room_id: str = ""
    active_room_label: str = ""
    active_batch_id: str = ""
    source: str = "unknown"

    @classmethod
    def from_legacy_payload(cls, payload: dict[str, Any]) -> "RoomSession":
        summary = dict(payload.get("safe_summary", {}) or {})
        expected_id = str(summary.get("room_entry_expected_room_id") or "").strip()
        expected_label = normalize_room_label(summary.get("room_entry_expected_room_label"))
        active_id = str(summary.get("locked_room_id") or summary.get("room_id") or "").strip()
        active_label = (
            normalize_room_label(summary.get("locked_room_label"))
            or normalize_room_label(summary.get("room_label"))
            or room_label_from_room_id(active_id)
        )
        return cls(
            instance_id=str(payload.get("instance_id", "")),
            expected_room_id=expected_id,
            expected_room_label=expected_label,
            active_room_id=active_id,
            active_room_label=active_label,
            active_batch_id=str(payload.get("batch_id") or ""),
            source=str(payload.get("source") or "legacy"),
        )

    @property
    def room_id(self) -> str:
        return self.expected_room_id or self.active_room_id

    @property
    def room_label(self) -> str:
        return self.expected_room_label or self.active_room_label or room_label_from_room_id(self.room_id)

    def to_safe_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value not in ("", None)}


@dataclass(frozen=True)
class RuntimeCandidate:
    source: str
    timestamp_ms: int = field(default_factory=now_ms)
    instance_id: str = ""
    room_id: str = ""
    room_label: str = ""
    limit_label: str = ""
    batch_id: str = ""
    countdown: int | None = None
    balance_text: str = ""
    phase_key: str = ""
    phase_label: str = ""
    betting_open: bool | None = None
    action: int | None = None
    timed: int | None = None
    current_load_type: int | None = None
    is_can_betting: bool | None = None
    confidence: float = 0.0
    context_path: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def normalized_room_label(self) -> str:
        return normalize_room_label(self.room_label) or room_label_from_room_id(self.room_id)

    @property
    def has_room_identity(self) -> bool:
        return bool(self.room_id or self.normalized_room_label)

    @property
    def has_phase_signal(self) -> bool:
        return any(
            (
                self.phase_key and self.phase_key != "unknown",
                self.action is not None,
                self.timed is not None,
                self.current_load_type is not None,
                self.is_can_betting is not None,
            )
        )

    @property
    def has_state_signal(self) -> bool:
        return bool(self.batch_id and (self.countdown is not None or self.has_phase_signal))

    @property
    def is_balance_only(self) -> bool:
        return bool(self.balance_text and not self.has_state_signal)

    def to_safe_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["room_label_normalized"] = self.normalized_room_label
        data["has_state_signal"] = self.has_state_signal
        data["is_balance_only"] = self.is_balance_only
        return {key: value for key, value in data.items() if value not in ("", None, {}, [])}


@dataclass(frozen=True)
class RuntimeV2Decision:
    instance_id: str
    timestamp_ms: int
    session: RoomSession
    accepted_state: RuntimeCandidate | None
    accepted_balance: RuntimeCandidate | None
    rejected: list[dict[str, Any]]
    candidates: list[RuntimeCandidate]
    acceptance_diagnostic: dict[str, Any] = field(default_factory=dict)
    stable_state_diagnostic: dict[str, Any] = field(default_factory=dict)
    stable_state: RuntimeCandidate | None = None
    legacy_payload: dict[str, Any] = field(default_factory=dict)

    @property
    def has_state(self) -> bool:
        return self.accepted_state is not None

    def to_safe_dict(self) -> dict[str, Any]:
        accepted_state = self.accepted_state.to_safe_dict() if self.accepted_state else {}
        accepted_balance = self.accepted_balance.to_safe_dict() if self.accepted_balance else {}
        stable_state = self.stable_state.to_safe_dict() if self.stable_state else {}
        return {
            "instance_id": self.instance_id,
            "timestamp_ms": self.timestamp_ms,
            "session": self.session.to_safe_dict(),
            "has_state": self.has_state,
            "accepted_state": accepted_state,
            "accepted_balance": accepted_balance,
            "stable_state": stable_state,
            "rejected": self.rejected,
            "acceptance_diagnostic": self.acceptance_diagnostic,
            "stable_state_diagnostic": self.stable_state_diagnostic,
            "candidate_count": len(self.candidates),
            "candidates": [candidate.to_safe_dict() for candidate in self.candidates],
            "legacy": {
                "batch_id": self.legacy_payload.get("batch_id", ""),
                "exact_countdown": self.legacy_payload.get("exact_countdown", -1),
                "ocr_balance": self.legacy_payload.get("ocr_balance", ""),
                "source": self.legacy_payload.get("source", ""),
            },
        }

