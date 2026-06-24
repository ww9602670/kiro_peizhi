from __future__ import annotations

from typing import Any

from bet_desktop.browser.live_runtime_state import ACTION_PHASES

from .normalizers import as_bool, as_int, cents_limit_label, normalize_limit_label, normalize_room_label, room_label_from_room_id
from .schemas import RuntimeCandidate


def candidate_from_runtime_snapshot(snapshot: Any) -> RuntimeCandidate | None:
    if snapshot is None:
        return None
    frame = getattr(snapshot, "frame", None)
    if frame is None:
        return None
    room_id = str(getattr(frame, "room_id", "") or "")
    table_label = normalize_room_label(getattr(frame, "table_label", "")) or room_label_from_room_id(room_id)
    return RuntimeCandidate(
        source="page_runtime",
        timestamp_ms=int(getattr(snapshot, "timestamp_ms", 0) or 0),
        instance_id=str(getattr(snapshot, "instance_id", "") or ""),
        room_id=room_id,
        room_label=table_label,
        limit_label=cents_limit_label(
            getattr(frame, "user_min_bet_cents", None),
            getattr(frame, "user_max_bet_cents", None),
        ),
        batch_id=str(getattr(snapshot, "game_no", "") or ""),
        countdown=as_int(getattr(snapshot, "countdown_seconds", None)),
        balance_text=str(getattr(snapshot, "balance_text", "") or ""),
        phase_key=str(getattr(snapshot, "phase_key", "") or ""),
        phase_label=str(getattr(snapshot, "phase_label", "") or ""),
        betting_open=as_bool(getattr(snapshot, "betting_open", None)),
        action=as_int(getattr(frame, "action", None)),
        timed=as_int(getattr(frame, "timed", None)),
        current_load_type=as_int(getattr(frame, "current_load_type", None)),
        is_can_betting=as_bool(getattr(frame, "is_can_betting", None)),
        confidence=float(getattr(snapshot, "layout_confidence", 0.0) or 0.0),
        evidence={
            "game_visible": bool(getattr(snapshot, "game_visible", False)),
            "frame_index": getattr(frame, "frame_index", None),
            "page_index": getattr(frame, "page_index", None),
        },
    )


def candidate_from_legacy_payload(payload: dict[str, Any]) -> RuntimeCandidate | None:
    if not isinstance(payload, dict):
        return None
    summary = dict(payload.get("safe_summary", {}) or {})
    countdown = as_int(payload.get("exact_countdown"))
    if countdown is not None and countdown < 0:
        countdown = None
    action = as_int(summary.get("runtime_action") or summary.get("frontend_runtime_action"))
    current_load_type = as_int(summary.get("runtime_current_load_type") or summary.get("frontend_runtime_current_load_type"))
    is_can_betting = as_bool(summary.get("runtime_is_can_betting") if "runtime_is_can_betting" in summary else summary.get("frontend_runtime_is_can_betting"))
    phase_key = str(summary.get("phase_text") or "")
    phase_label = ""
    betting_open = None
    if not phase_key:
        if action is not None:
            phase_key, phase_label, betting_open = ACTION_PHASES.get(int(action), ("", "", None))
        elif current_load_type is not None:
            phase_key, phase_label, betting_open = ACTION_PHASES.get(int(current_load_type), ("", "", None))
        elif is_can_betting is not None:
            betting_open = bool(is_can_betting)
            phase_key = "betting_open" if betting_open else "betting_closed"
            phase_label = "betting window open" if betting_open else "betting window closed"
    candidate = RuntimeCandidate(
        source="legacy_state",
        timestamp_ms=as_int(payload.get("timestamp_captured_ms")) or 0,
        instance_id=str(payload.get("instance_id", "") or ""),
        room_id=str(summary.get("locked_room_id") or summary.get("room_id") or ""),
        room_label=normalize_room_label(summary.get("locked_room_label") or summary.get("room_label")),
        limit_label=normalize_limit_label(summary.get("limit_label")),
        batch_id=str(payload.get("batch_id") or ""),
        countdown=countdown,
        balance_text=str(payload.get("ocr_balance") or ""),
        phase_key=phase_key,
        phase_label=phase_label,
        betting_open=betting_open,
        action=action,
        timed=as_int(summary.get("runtime_timed") or summary.get("frontend_runtime_timed")),
        current_load_type=current_load_type,
        is_can_betting=is_can_betting,
        confidence=float(payload.get("confidence", 0.0) or 0.0),
        evidence={"snapshot_source": str(payload.get("source") or "")},
    )
    return candidate if candidate.has_state_signal or candidate.balance_text else None


def candidate_from_label_snapshot(snapshot: Any) -> RuntimeCandidate | None:
    if snapshot is None:
        return None
    frame = getattr(snapshot, "frame", None)
    if frame is None:
        return None
    return RuntimeCandidate(
        source="label_runtime",
        timestamp_ms=int(getattr(snapshot, "timestamp_ms", 0) or 0),
        instance_id=str(getattr(snapshot, "instance_id", "") or ""),
        room_label=normalize_room_label(getattr(frame, "room_label", "")),
        limit_label=normalize_limit_label(getattr(frame, "limit_label", "")),
        batch_id=str(getattr(frame, "game_no", "") or ""),
        countdown=as_int(getattr(frame, "countdown_seconds", None)),
        phase_key=str(getattr(frame, "phase_text", "") or ""),
        confidence=float(getattr(frame, "confidence", 0.0) or 0.0),
        evidence={
            "render_signal": bool(getattr(frame, "render_signal", False)),
            "frame_index": getattr(frame, "frame_index", None),
            "page_index": getattr(frame, "page_index", None),
        },
    )


def candidate_from_canvas_snapshot(snapshot: Any) -> RuntimeCandidate | None:
    if snapshot is None:
        return None
    return RuntimeCandidate(
        source="canvas_text",
        timestamp_ms=int(getattr(snapshot, "timestamp_ms", 0) or 0),
        instance_id=str(getattr(snapshot, "instance_id", "") or ""),
        room_label=normalize_room_label(getattr(snapshot, "room_label", "")),
        limit_label=normalize_limit_label(getattr(snapshot, "limit_label", "")),
        batch_id=str(getattr(snapshot, "game_no", "") or ""),
        countdown=as_int(getattr(snapshot, "countdown_seconds", None)),
        phase_key=str(getattr(snapshot, "phase_text", "") or ""),
        confidence=float(getattr(snapshot, "confidence", 0.0) or 0.0),
        evidence={"record_count": len(getattr(snapshot, "records", []) or [])},
    )


def candidate_from_frontend_event(event: Any) -> RuntimeCandidate | None:
    if event is None:
        return None
    action = as_int(getattr(event, "action", None))
    current_load_type = as_int(getattr(event, "current_load_type", None))
    is_can_betting = as_bool(getattr(event, "is_can_betting", None))
    phase_key = str(getattr(event, "phase_text", "") or "")
    phase_label = ""
    betting_open = None
    if not phase_key:
        if action is not None:
            phase_key, phase_label, betting_open = ACTION_PHASES.get(
                int(action),
                ("", "", None),
            )
        elif current_load_type is not None:
            phase_key, phase_label, betting_open = ACTION_PHASES.get(
                int(current_load_type),
                ("", "", None),
            )
        elif is_can_betting is not None:
            betting_open = bool(is_can_betting)
            phase_key = "betting_open" if betting_open else "betting_closed"
            phase_label = "betting window open" if betting_open else "betting window closed"
    return RuntimeCandidate(
        source=f"frontend_{str(getattr(event, 'event_type', '') or 'event')}",
        timestamp_ms=int(getattr(event, "timestamp_ms", 0) or 0),
        instance_id="",
        room_id=str(getattr(event, "room_id", "") or ""),
        room_label=normalize_room_label(getattr(event, "room_label", "")),
        limit_label=normalize_limit_label(getattr(event, "limit_label", "")),
        batch_id=str(getattr(event, "batch_id", "") or ""),
        countdown=as_int(getattr(event, "countdown", None)),
        balance_text=str(getattr(event, "balance", "") or ""),
        phase_key=phase_key,
        phase_label=phase_label,
        betting_open=betting_open,
        action=action,
        timed=as_int(getattr(event, "timed", None)),
        current_load_type=current_load_type,
        is_can_betting=is_can_betting,
        confidence=float(getattr(event, "confidence", 0.0) or 0.0),
        context_path=str(getattr(event, "context_path", "") or ""),
        evidence={
            "short_batch_id": str(getattr(event, "short_batch_id", "") or ""),
            "batch_id_is_full": bool(getattr(event, "batch_id_is_full", False)),
        },
    )


def candidate_from_ws_state(state: Any) -> RuntimeCandidate | None:
    if state is None:
        return None
    return RuntimeCandidate(
        source=f"ws_{str(getattr(state, 'source', '') or 'parser')}",
        room_id=str(getattr(state, "room_id", "") or ""),
        batch_id=str(getattr(state, "batch_id", "") or ""),
        countdown=as_int(getattr(state, "countdown", None)),
        balance_text=str(getattr(state, "balance", "") or ""),
        action=as_int(getattr(state, "action", None)),
        timed=as_int(getattr(state, "timed", None)),
        current_load_type=as_int(getattr(state, "current_load_type", None)),
        is_can_betting=as_bool(getattr(state, "is_can_betting", None)),
        confidence=float(getattr(state, "confidence", 0.0) or 0.0),
        evidence=dict(getattr(state, "safe_summary", {}) or {}),
    )

