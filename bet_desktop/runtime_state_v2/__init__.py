"""Shadow runtime state pipeline for room-scoped baccarat state evidence."""

from .candidates import (
    candidate_from_canvas_snapshot,
    candidate_from_frontend_event,
    candidate_from_label_snapshot,
    candidate_from_runtime_snapshot,
)
from .decision import decide_runtime_state
from .schemas import RoomSession, RuntimeCandidate, RuntimeV2Decision

__all__ = [
    "RoomSession",
    "RuntimeCandidate",
    "RuntimeV2Decision",
    "candidate_from_canvas_snapshot",
    "candidate_from_frontend_event",
    "candidate_from_label_snapshot",
    "candidate_from_runtime_snapshot",
    "decide_runtime_state",
]

