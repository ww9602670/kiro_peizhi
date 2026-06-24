from __future__ import annotations

from dataclasses import replace
from typing import Any

from .candidates import (
    candidate_from_legacy_payload,
    candidate_from_canvas_snapshot,
    candidate_from_frontend_event,
    candidate_from_label_snapshot,
    candidate_from_runtime_snapshot,
    candidate_from_ws_state,
)
from .decision import decide_runtime_state
from .schemas import RoomSession, RuntimeCandidate, RuntimeV2Decision


_SHADOW_STATE_HOLD_MS = 15_000
_SHADOW_PHASE_ONLY_HOLD_MS = 3_000
_STABLE_SHADOW_HOLD_MS = 2_500
_STABLE_SHADOW_READY_FRAMES = 2
_STABLE_COUNTDOWN_REANCHOR_TOLERANCE_SECONDS = 1


def build_runtime_v2_shadow_decision(
    *,
    instance_id: str,
    legacy_payload: dict[str, Any],
    runtime_snapshot: Any = None,
    label_snapshot: Any = None,
    canvas_snapshot: Any = None,
    frontend_events: list[Any] | None = None,
    ws_states: list[Any] | None = None,
    previous_decision: RuntimeV2Decision | None = None,
) -> RuntimeV2Decision:
    candidates = [
        candidate
        for candidate in (
            candidate_from_runtime_snapshot(runtime_snapshot),
            candidate_from_label_snapshot(label_snapshot),
            candidate_from_canvas_snapshot(canvas_snapshot),
            candidate_from_legacy_payload(legacy_payload),
        )
        if candidate is not None
    ]
    candidates.extend(
        candidate
        for candidate in (candidate_from_frontend_event(event) for event in frontend_events or [])
        if candidate is not None
    )
    candidates.extend(
        candidate
        for candidate in (candidate_from_ws_state(state) for state in ws_states or [])
        if candidate is not None
    )
    decision = decide_runtime_state(
        instance_id=instance_id,
        session=RoomSession.from_legacy_payload(legacy_payload),
        candidates=candidates,
        legacy_payload=legacy_payload,
    )
    if previous_decision is None:
        return _with_stable_shadow_state(decision, None)
    decision = _with_shadow_continuity(decision, previous_decision)
    return _with_stable_shadow_state(decision, previous_decision)


def _with_shadow_continuity(
    decision: RuntimeV2Decision,
    previous_decision: RuntimeV2Decision,
) -> RuntimeV2Decision:
    state = decision.accepted_state
    previous = previous_decision.accepted_state
    if state is None or previous is None:
        return decision
    if not state.batch_id or state.batch_id != previous.batch_id:
        return decision
    if not _same_room(decision.session, state, previous):
        return decision

    missing_countdown = state.countdown is None and previous.countdown is not None
    missing_phase = not state.has_phase_signal and previous.has_phase_signal
    if not missing_countdown and not missing_phase:
        return decision

    age_ms = max(0, decision.timestamp_ms - previous_decision.timestamp_ms)
    if age_ms > _SHADOW_STATE_HOLD_MS:
        return decision

    countdown = state.countdown
    if missing_countdown and previous.countdown is not None:
        countdown = max(0, previous.countdown - int(age_ms / 1000))

    phase_key = state.phase_key
    phase_label = state.phase_label
    betting_open = state.betting_open
    action = state.action
    timed = state.timed
    current_load_type = state.current_load_type
    is_can_betting = state.is_can_betting
    if missing_phase and (
        previous.countdown is not None or age_ms <= _SHADOW_PHASE_ONLY_HOLD_MS
    ):
        phase_key = previous.phase_key
        phase_label = previous.phase_label
        betting_open = previous.betting_open
        action = previous.action
        timed = previous.timed
        current_load_type = previous.current_load_type
        is_can_betting = previous.is_can_betting

    evidence = dict(state.evidence)
    evidence["shadow_continuity"] = {
        "source": previous.source,
        "age_ms": age_ms,
        "batch_id": previous.batch_id,
    }
    return replace(
        decision,
        accepted_state=replace(
            state,
            countdown=countdown,
            phase_key=phase_key,
            phase_label=phase_label,
            betting_open=betting_open,
            action=action,
            timed=timed,
            current_load_type=current_load_type,
            is_can_betting=is_can_betting,
            evidence=evidence,
        ),
    )


def _with_stable_shadow_state(
    decision: RuntimeV2Decision,
    previous_decision: RuntimeV2Decision | None,
) -> RuntimeV2Decision:
    state = decision.accepted_state
    previous = previous_decision.stable_state if previous_decision is not None else None
    if state is None:
        return _hold_previous_stable_state(decision, previous, stable_gate_reason="no_accepted_state")
    if isinstance(state.evidence, dict) and "shadow_continuity" in state.evidence:
        return _hold_previous_stable_state(decision, previous, stable_gate_reason="continuity_fill")

    if not _state_has_complete_runtime_fields(state):
        return _hold_previous_stable_state(decision, previous, stable_gate_reason="fields_incomplete")

    current_key = _stable_state_key(decision.session, state)
    if not current_key:
        return _hold_previous_stable_state(decision, previous, stable_gate_reason="fields_incomplete")

    previous_key = _stable_state_key(previous_decision.session, previous) if previous_decision and previous else ""
    same_stable_key = bool(previous is not None and current_key == previous_key)
    frame_count = _previous_stable_frame_count(previous) + 1 if same_stable_key else 1

    countdown = state.countdown
    countdown_smoothed = False
    countdown_anchor = _countdown_anchor_from_previous(previous) if same_stable_key else None
    if same_stable_key and previous is not None and previous.countdown is not None and countdown is not None:
        if countdown_anchor is None:
            countdown_anchor = (int(previous.countdown), int(previous.timestamp_ms))
        anchor_value, anchor_ms = countdown_anchor
        expected = max(0, int(anchor_value) - int(max(0, decision.timestamp_ms - anchor_ms) / 1000))
        if int(countdown) >= expected - _STABLE_COUNTDOWN_REANCHOR_TOLERANCE_SECONDS:
            countdown = expected
            countdown_smoothed = True
        else:
            countdown_anchor = (int(countdown), int(decision.timestamp_ms))
    elif countdown is not None:
        countdown_anchor = (int(countdown), int(decision.timestamp_ms))

    evidence = dict(state.evidence)
    evidence["stable_shadow"] = {
        "frame_count": frame_count,
        "ready_frames": _STABLE_SHADOW_READY_FRAMES,
        "key": current_key,
        "held": False,
        "raw_countdown": state.countdown,
        "countdown_smoothed": countdown_smoothed,
    }
    if countdown_anchor is not None:
        evidence["stable_shadow"]["countdown_anchor"] = countdown_anchor[0]
        evidence["stable_shadow"]["countdown_anchor_ms"] = countdown_anchor[1]
    stable_state = replace(
        state,
        timestamp_ms=decision.timestamp_ms,
        countdown=countdown,
        evidence=evidence,
    )
    decision = replace(decision, stable_state=stable_state)
    if frame_count < _STABLE_SHADOW_READY_FRAMES:
        return _set_stable_state_diagnostic(
            decision,
            stable_gate_reason="insufficient_frames",
            frame_count=frame_count,
            ready_frames=_STABLE_SHADOW_READY_FRAMES,
        )
    return _set_stable_state_diagnostic(decision, stable_gate_reason=None)


def _hold_previous_stable_state(
    decision: RuntimeV2Decision,
    previous: RuntimeCandidate | None,
    *,
    stable_gate_reason: str,
) -> RuntimeV2Decision:
    if previous is None:
        return _set_stable_state_diagnostic(
            replace(decision, stable_state=None),
            stable_gate_reason=stable_gate_reason,
        )
    if not _same_room(decision.session, previous, previous):
        return _set_stable_state_diagnostic(
            replace(decision, stable_state=None),
            stable_gate_reason="key_changed",
            previous_room=previous.room_id,
        )
    current = decision.accepted_state
    if current is not None:
        current_key = _stable_state_key(decision.session, current)
        previous_key = _stable_state_key(decision.session, previous)
        if current_key and previous_key and current_key != previous_key:
            return _set_stable_state_diagnostic(
                replace(decision, stable_state=None),
                stable_gate_reason="key_changed",
                current_key=current_key,
                previous_key=previous_key,
            )

    age_ms = max(0, decision.timestamp_ms - previous.timestamp_ms)
    if age_ms > _STABLE_SHADOW_HOLD_MS:
        return _set_stable_state_diagnostic(
            replace(decision, stable_state=None),
            stable_gate_reason="held_timeout",
            held_age_ms=age_ms,
        )

    countdown = previous.countdown
    if countdown is not None:
        countdown = max(0, int(countdown) - int(age_ms / 1000))
    evidence = dict(previous.evidence)
    stable_meta = dict(evidence.get("stable_shadow") or {})
    stable_meta.update(
        {
            "held": True,
            "held_age_ms": age_ms,
            "ready_frames": _STABLE_SHADOW_READY_FRAMES,
        }
    )
    evidence["stable_shadow"] = stable_meta
    decision = replace(
        decision,
        stable_state=replace(
            previous,
            timestamp_ms=decision.timestamp_ms,
            countdown=countdown,
            evidence=evidence,
        ),
    )
    return _set_stable_state_diagnostic(
        decision,
        stable_gate_reason="held",
        held_age_ms=age_ms,
        frame_count=stable_meta.get("frame_count") if isinstance(stable_meta, dict) else None,
    )


def _set_stable_state_diagnostic(
    decision: RuntimeV2Decision,
    stable_gate_reason: str | None,
    **meta: object,
) -> RuntimeV2Decision:
    if stable_gate_reason is None and not meta:
        return replace(decision, stable_state_diagnostic={})
    payload: dict[str, object] = {
        "stable_gate_reason": stable_gate_reason,
    }
    if meta:
        payload.update(meta)
    return replace(decision, stable_state_diagnostic=payload)


def _countdown_anchor_from_previous(previous: RuntimeCandidate | None) -> tuple[int, int] | None:
    if previous is None:
        return None
    meta = previous.evidence.get("stable_shadow") if isinstance(previous.evidence, dict) else {}
    if not isinstance(meta, dict):
        return None
    try:
        anchor = int(meta.get("countdown_anchor"))
        anchor_ms = int(meta.get("countdown_anchor_ms"))
    except (TypeError, ValueError):
        return None
    if anchor < 0 or anchor_ms <= 0:
        return None
    return anchor, anchor_ms


def _state_has_complete_runtime_fields(state: RuntimeCandidate) -> bool:
    return bool(
        state.batch_id
        and state.countdown is not None
        and int(state.countdown) >= 0
        and state.has_phase_signal
    )


def _previous_stable_frame_count(previous: RuntimeCandidate | None) -> int:
    if previous is None:
        return 0
    meta = previous.evidence.get("stable_shadow") if isinstance(previous.evidence, dict) else {}
    if not isinstance(meta, dict):
        return 0
    try:
        return max(0, int(meta.get("frame_count") or 0))
    except (TypeError, ValueError):
        return 0


def _stable_state_key(session: RoomSession, state: RuntimeCandidate | None) -> str:
    if state is None:
        return ""
    room = state.normalized_room_label or session.room_label or state.room_id or session.room_id
    batch = _stable_batch_id(state.batch_id)
    phase = str(state.phase_key or "").strip()
    if not room or not batch or not phase or phase == "unknown":
        return ""
    return f"{room}:{batch}:{phase}"


def _stable_batch_id(value: object) -> str:
    text = str(value or "").strip()
    if text in {"", "-"}:
        return ""
    parts = text.split("-")
    if (
        len(parts) >= 4
        and len(parts[0]) >= 2
        and len(parts[1]) >= 6
        and len(parts[2]) >= 6
        and all(part.isdigit() for part in parts[:4])
    ):
        return "-".join(parts[:3])
    return text


def _same_room(
    session: RoomSession,
    state: RuntimeCandidate,
    previous: RuntimeCandidate,
) -> bool:
    state_room_id = state.room_id or session.room_id
    previous_room_id = previous.room_id
    if state_room_id and previous_room_id and state_room_id != previous_room_id:
        return False

    state_room_label = state.normalized_room_label or session.room_label
    previous_room_label = previous.normalized_room_label
    if state_room_label and previous_room_label and state_room_label != previous_room_label:
        return False
    return True

