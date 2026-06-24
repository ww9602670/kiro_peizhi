from __future__ import annotations

from collections import Counter
import re
from dataclasses import replace
from typing import Iterable

from bet_desktop.models.state_temporal_guard import now_ms

from .schemas import RoomSession, RuntimeCandidate, RuntimeV2Decision


SOURCE_PRIORITY = {
    "legacy_state": 99,
    "frontend_bound_room_object": 98,
    "frontend_object_scan": 96,
    "canvas_text": 95,
    "label_runtime": 90,
    "page_runtime": 68,
}


def decide_runtime_state(
    *,
    instance_id: str,
    session: RoomSession,
    candidates: Iterable[RuntimeCandidate],
    legacy_payload: dict,
) -> RuntimeV2Decision:
    candidate_list = list(candidates)
    accepted: list[RuntimeCandidate] = []
    rejected: list[dict] = []
    for candidate in candidate_list:
        if candidate is None:
            continue
        reason = _reject_reason(session, candidate)
        if reason:
            rejected.append(_rejection(candidate, reason))
            continue
        accepted.append(candidate)
        if candidate.is_balance_only:
            rejected.append(_rejection(candidate, "balance_only_no_state_signal"))

    accepted_state = _best_state_candidate(accepted, session)
    if accepted_state is not None:
        accepted_state = _with_merged_runtime_fields(accepted_state, candidate_list, accepted, session)
        accepted_state = _with_merged_room_metadata(accepted_state, accepted, session)
    accepted_balance = _best_balance_candidate(accepted)
    if accepted_state is None and accepted:
        rejected.append(
            {
                "source": "runtime_state_v2",
                "reason": "no_candidate_had_batch_and_runtime_signal",
            }
        )

    return RuntimeV2Decision(
        instance_id=instance_id,
        timestamp_ms=now_ms(),
        session=session,
        accepted_state=accepted_state,
        accepted_balance=accepted_balance,
        rejected=rejected,
        acceptance_diagnostic=_build_acceptance_diagnostic(
            candidate_list,
            accepted=accepted,
            rejected=rejected,
            accepted_state=accepted_state,
        ),
        candidates=candidate_list,
        legacy_payload=legacy_payload,
    )


def _build_acceptance_diagnostic(
    candidate_list: list[RuntimeCandidate],
    *,
    accepted: list[RuntimeCandidate],
    rejected: list[dict],
    accepted_state: RuntimeCandidate | None,
) -> dict[str, object]:
    reasons = _rejection_reason_counts(rejected)
    diagnostics: dict[str, object] = {
        "candidate_count": len(candidate_list),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "reject_reasons": [{"reason": reason, "count": count} for reason, count in reasons],
        "no_accepted_state": accepted_state is None,
    }
    if accepted_state is not None:
        diagnostics["missing_fields"] = []
        return diagnostics

    missing_fields = _missing_state_fields(candidate_list)
    diagnostics["missing_fields"] = missing_fields
    missing_reasons = [field for field in ("round_id", "countdown", "phase") if field in missing_fields]
    if not candidate_list:
        diagnostics["reasons"] = ["no_candidates"]
    elif _all_room_mismatch_rejected(rejected):
        diagnostics["reasons"] = ["candidate_room_mismatch"]
    elif missing_reasons:
        diagnostics["reasons"] = ["candidate_filtered"] + missing_reasons
    else:
        diagnostics["reasons"] = ["candidate_filtered"]
    return diagnostics


def _rejection_reason_counts(rejected: list[dict]) -> list[tuple[str, int]]:
    reasons = Counter(item.get("reason") for item in rejected if isinstance(item, dict) and item.get("reason"))
    return reasons.most_common(3)


def _missing_state_fields(candidates: list[RuntimeCandidate]) -> list[str]:
    has_round = any(bool(candidate.batch_id) for candidate in candidates)
    if not has_round:
        return ["round_id"]

    has_countdown = any(bool(candidate.batch_id) and candidate.countdown is not None for candidate in candidates)
    has_phase = any(bool(candidate.batch_id) and candidate.has_phase_signal for candidate in candidates)

    missing_fields: list[str] = []
    if not has_countdown:
        missing_fields.append("countdown")
    if not has_phase:
        missing_fields.append("phase")
    return missing_fields


def _all_room_mismatch_rejected(rejected: list[dict]) -> bool:
    room_rejections = {"room_id_conflict", "room_label_conflict", "state_signal_without_room_identity"}
    if not rejected:
        return False
    return all(item.get("reason") in room_rejections for item in rejected if isinstance(item, dict) and item.get("reason"))


def _reject_reason(session: RoomSession, candidate: RuntimeCandidate) -> str:
    expected_id = session.room_id
    expected_label = session.room_label
    candidate_label = candidate.normalized_room_label
    if expected_id and candidate.room_id and candidate.room_id != expected_id:
        return "room_id_conflict"
    if expected_label and candidate_label and candidate_label != expected_label:
        return "room_label_conflict"
    if expected_label and candidate.has_state_signal and not candidate.has_room_identity:
        if candidate.source not in {"label_runtime", "canvas_text"}:
            return "state_signal_without_room_identity"
    return ""


def _best_state_candidate(candidates: list[RuntimeCandidate], session: RoomSession) -> RuntimeCandidate | None:
    state_candidates = [candidate for candidate in candidates if candidate.has_state_signal]
    anchor_batch_id = session.active_batch_id
    if anchor_batch_id:
        anchored = [candidate for candidate in state_candidates if _round_score(anchor_batch_id, candidate.batch_id) > 0]
        if anchored:
            state_candidates = anchored
        else:
            state_candidates = [
                candidate
                for candidate in candidates
                if candidate.source == "legacy_state"
                and candidate.batch_id
                and candidate.has_room_identity
                and _round_score(anchor_batch_id, candidate.batch_id) > 0
            ]
    if not state_candidates:
        return None
    return max(state_candidates, key=lambda candidate: _state_score(candidate, session))


def _best_balance_candidate(candidates: list[RuntimeCandidate]) -> RuntimeCandidate | None:
    balance_candidates = [candidate for candidate in candidates if candidate.balance_text]
    if not balance_candidates:
        return None
    return max(balance_candidates, key=lambda item: (item.has_room_identity, item.confidence, item.timestamp_ms))


def _with_merged_room_metadata(
    state: RuntimeCandidate,
    candidates: list[RuntimeCandidate],
    session: RoomSession,
) -> RuntimeCandidate:
    room_id = state.room_id or session.room_id
    room_label = state.normalized_room_label or session.room_label
    limit_label = state.limit_label
    metadata = _best_room_metadata_candidate(state, candidates)
    if metadata is not None:
        room_id = room_id or metadata.room_id
        room_label = room_label or metadata.normalized_room_label
        limit_label = limit_label or metadata.limit_label
    if room_id == state.room_id and room_label == state.room_label and limit_label == state.limit_label:
        return state
    evidence = dict(state.evidence)
    if metadata is not None:
        evidence["merged_room_metadata_source"] = metadata.source
    if session.room_label or session.room_id:
        evidence["merged_room_session"] = session.to_safe_dict()
    return replace(
        state,
        room_id=room_id,
        room_label=room_label,
        limit_label=limit_label,
        evidence=evidence,
    )


def _with_merged_runtime_fields(
    state: RuntimeCandidate,
    candidates: list[RuntimeCandidate],
    accepted: list[RuntimeCandidate],
    session: RoomSession,
) -> RuntimeCandidate:
    compatible = _runtime_merge_candidates(state, candidates, accepted, session)
    if not compatible:
        return state

    batch_source = _best_field_candidate(compatible, "batch_id", state=state, session=session)
    countdown_source = _best_field_candidate(compatible, "countdown", state=state, session=session)
    phase_source = _best_phase_candidate(compatible, state=state, session=session)

    batch_id = state.batch_id or (batch_source.batch_id if batch_source else "")
    countdown = state.countdown if state.countdown is not None else None
    phase_key = state.phase_key
    phase_label = state.phase_label
    betting_open = state.betting_open
    action = state.action
    timed = state.timed
    current_load_type = state.current_load_type
    is_can_betting = state.is_can_betting

    if countdown_source is not None and (
        countdown is None
        or _field_score(countdown_source, "countdown", state=state, session=session)
        > _field_score(state, "countdown", state=state, session=session)
    ):
        countdown = countdown_source.countdown
    if phase_source is not None and (
        not state.has_phase_signal
        or _field_score(phase_source, "phase", state=state, session=session)
        > _field_score(state, "phase", state=state, session=session)
    ):
        phase_key = phase_source.phase_key
        phase_label = phase_source.phase_label
        betting_open = phase_source.betting_open
        action = phase_source.action
        timed = phase_source.timed
        current_load_type = phase_source.current_load_type
        is_can_betting = phase_source.is_can_betting

    if (
        batch_id == state.batch_id
        and countdown == state.countdown
        and phase_key == state.phase_key
        and phase_label == state.phase_label
        and betting_open == state.betting_open
        and action == state.action
        and timed == state.timed
        and current_load_type == state.current_load_type
        and is_can_betting == state.is_can_betting
    ):
        return state

    evidence = dict(state.evidence)
    merged_sources = {}
    if batch_source is not None and batch_id != state.batch_id:
        merged_sources["batch_id"] = batch_source.source
    if countdown_source is not None and countdown != state.countdown:
        merged_sources["countdown"] = countdown_source.source
    if phase_source is not None and (
        phase_key != state.phase_key
        or phase_label != state.phase_label
        or action != state.action
        or timed != state.timed
        or current_load_type != state.current_load_type
        or is_can_betting != state.is_can_betting
    ):
        merged_sources["phase"] = phase_source.source
    if merged_sources:
        evidence["merged_runtime_sources"] = merged_sources

    return replace(
        state,
        batch_id=batch_id,
        countdown=countdown,
        phase_key=phase_key,
        phase_label=phase_label,
        betting_open=betting_open,
        action=action,
        timed=timed,
        current_load_type=current_load_type,
        is_can_betting=is_can_betting,
        evidence=evidence,
    )


def _runtime_candidate_can_merge(
    state: RuntimeCandidate,
    candidate: RuntimeCandidate,
    accepted: list[RuntimeCandidate],
    session: RoomSession,
) -> bool:
    if _room_metadata_conflicts(state, candidate):
        return False
    session_label = session.room_label
    candidate_label = candidate.normalized_room_label
    if session.room_id and candidate.room_id and candidate.room_id != session.room_id:
        return False
    if session_label and candidate_label and candidate_label != session_label:
        return False
    if candidate in accepted:
        return _round_compatible(state.batch_id, candidate.batch_id)
    return _roomless_frontend_can_merge(state, candidate, session)


def _runtime_merge_candidates(
    state: RuntimeCandidate,
    candidates: list[RuntimeCandidate],
    accepted: list[RuntimeCandidate],
    session: RoomSession,
) -> list[RuntimeCandidate]:
    compatible: list[RuntimeCandidate] = []
    for candidate in candidates:
        if candidate is state:
            continue
        if _runtime_candidate_can_merge(state, candidate, accepted, session):
            compatible.append(candidate)
    return compatible


def _roomless_frontend_can_merge(
    state: RuntimeCandidate,
    candidate: RuntimeCandidate,
    session: RoomSession,
) -> bool:
    if not candidate.source.startswith("frontend_"):
        return False
    if candidate.room_id or candidate.normalized_room_label:
        return False
    if not candidate.batch_id or not candidate.has_state_signal:
        return False
    if candidate.source not in {"frontend_json_parse_state", "frontend_timed_context"}:
        return False
    anchor_batch_id = state.batch_id or session.active_batch_id
    anchor_short = _round_short_id(anchor_batch_id)
    candidate_short = _round_short_id(candidate.batch_id)
    if not anchor_short or not candidate_short:
        return False
    return candidate_short == anchor_short


def _best_field_candidate(
    candidates: list[RuntimeCandidate],
    field: str,
    *,
    state: RuntimeCandidate,
    session: RoomSession,
) -> RuntimeCandidate | None:
    field_candidates = [candidate for candidate in candidates if getattr(candidate, field) not in ("", None)]
    if not field_candidates:
        return None
    return max(field_candidates, key=lambda candidate: _field_score(candidate, field, state=state, session=session))


def _best_phase_candidate(
    candidates: list[RuntimeCandidate],
    *,
    state: RuntimeCandidate,
    session: RoomSession,
) -> RuntimeCandidate | None:
    phase_candidates = [candidate for candidate in candidates if candidate.has_phase_signal]
    if not phase_candidates:
        return None
    return max(phase_candidates, key=lambda candidate: _field_score(candidate, "phase", state=state, session=session))


def _field_score(
    candidate: RuntimeCandidate,
    field: str,
    *,
    state: RuntimeCandidate,
    session: RoomSession,
) -> tuple:
    return (
        _field_source_priority(candidate.source, field),
        _round_score(state.batch_id, candidate.batch_id),
        candidate.has_room_identity or bool(session.room_label or session.room_id),
        candidate.confidence,
        candidate.timestamp_ms,
    )


def _field_source_priority(source: str, field: str) -> int:
    if source.startswith("frontend_"):
        return 97
    if field == "phase" and source == "page_runtime":
        return 96
    if field == "countdown" and source == "label_runtime":
        return 96
    return SOURCE_PRIORITY.get(source, 40)


def _round_compatible(state_batch_id: str, candidate_batch_id: str) -> bool:
    if not state_batch_id:
        return True
    if not candidate_batch_id:
        return False
    return _round_score(state_batch_id, candidate_batch_id) > 0


def _round_score(state_batch_id: str, candidate_batch_id: str) -> int:
    if not candidate_batch_id:
        return 1
    if not state_batch_id:
        return 2
    if candidate_batch_id == state_batch_id:
        return 4
    state_short = _round_short_id(state_batch_id)
    candidate_short = _round_short_id(candidate_batch_id) or candidate_batch_id
    if state_short and candidate_short and state_short == candidate_short:
        return 3
    return 0


def _round_short_id(value: str) -> str:
    text = str(value or "")
    match = re.match(r"^\d+-\d+-(\d+)-\d+$", text)
    if match:
        return match.group(1)
    if re.fullmatch(r"\d{8,}", text):
        return text
    return ""


def _best_room_metadata_candidate(
    state: RuntimeCandidate,
    candidates: list[RuntimeCandidate],
) -> RuntimeCandidate | None:
    metadata_candidates = [
        candidate
        for candidate in candidates
        if candidate is not state
        and (candidate.has_room_identity or candidate.limit_label)
        and not _room_metadata_conflicts(state, candidate)
    ]
    if not metadata_candidates:
        return None
    return max(metadata_candidates, key=_room_metadata_score)


def _room_metadata_conflicts(state: RuntimeCandidate, candidate: RuntimeCandidate) -> bool:
    state_label = state.normalized_room_label
    candidate_label = candidate.normalized_room_label
    if state.room_id and candidate.room_id and state.room_id != candidate.room_id:
        return True
    if state_label and candidate_label and state_label != candidate_label:
        return True
    return False


def _room_metadata_score(candidate: RuntimeCandidate) -> tuple:
    return (
        bool(candidate.has_room_identity),
        bool(candidate.limit_label),
        SOURCE_PRIORITY.get(candidate.source, 40),
        candidate.confidence,
        candidate.timestamp_ms,
    )


def _state_score(candidate: RuntimeCandidate, session: RoomSession) -> tuple:
    return (
        _round_score(session.active_batch_id, candidate.batch_id),
        SOURCE_PRIORITY.get(candidate.source, 40),
        candidate.has_room_identity,
        bool(candidate.batch_id),
        candidate.countdown is not None,
        candidate.has_phase_signal,
        candidate.confidence,
        candidate.timestamp_ms,
    )


def _rejection(candidate: RuntimeCandidate, reason: str) -> dict:
    return {
        "source": candidate.source,
        "reason": reason,
        "room_id": candidate.room_id,
        "room_label": candidate.normalized_room_label,
        "batch_id": candidate.batch_id,
        "has_state_signal": candidate.has_state_signal,
    }

