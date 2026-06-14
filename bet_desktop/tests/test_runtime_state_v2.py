from __future__ import annotations

from bet_desktop.browser.frontend_state_probe import FrontendStateEvent
from bet_desktop.browser.live_runtime_state import LiveRuntimeSnapshot, RuntimeFrameState
from bet_desktop.models.state_temporal_guard import now_ms
from bet_desktop.runtime_state_v2.candidates import candidate_from_frontend_event, candidate_from_runtime_snapshot
from bet_desktop.runtime_state_v2 import decision as decision_module
from bet_desktop.runtime_state_v2.decision import decide_runtime_state
from bet_desktop.runtime_state_v2.schemas import RoomSession, RuntimeCandidate
from bet_desktop.runtime_state_v2.shadow import build_runtime_v2_shadow_decision


def _runtime_snapshot(
    *,
    room_id: str,
    table_label: str = "",
    game_no: str = "50-1781157444-8495921944-1513",
    timed: int | None = 11,
    action: int | None = 3,
    balance_cents: int | None = 16520,
) -> LiveRuntimeSnapshot:
    return LiveRuntimeSnapshot(
        timestamp_ms=now_ms(),
        instance_id="a1",
        frame=RuntimeFrameState(
            page_index=0,
            frame_index=0,
            game_no=game_no,
            action=action,
            timed=timed,
            balance_cents=balance_cents,
            room_id=room_id,
            table_label=table_label,
            user_min_bet_cents=400,
            user_max_bet_cents=25000,
        ),
        game_visible=True,
        layout_confidence=0.8,
        viewport={"width": 960, "height": 620},
        phase_key="betting_open" if action == 3 else "unknown",
        phase_label="betting open" if action == 3 else "unknown",
        betting_open=action == 3,
        coordinates={},
    )


def test_runtime_v2_accepts_t002_runtime_without_table_label() -> None:
    session = RoomSession(instance_id="a1", expected_room_id="182020002", expected_room_label="T002")
    candidate = candidate_from_runtime_snapshot(_runtime_snapshot(room_id="182020002"))

    decision = decide_runtime_state(
        instance_id="a1",
        session=session,
        candidates=[candidate],
        legacy_payload={"instance_id": "a1", "batch_id": "", "safe_summary": {}},
    )

    assert decision.accepted_state is candidate
    assert decision.accepted_state.room_label == "T002"
    assert decision.accepted_state.batch_id == "50-1781157444-8495921944-1513"
    assert decision.accepted_state.countdown == 11
    assert decision.accepted_balance is candidate


def test_runtime_v2_rejects_cross_room_state_for_t003_session() -> None:
    session = RoomSession(instance_id="a1", expected_room_id="182020003", expected_room_label="T003")
    candidate = candidate_from_runtime_snapshot(_runtime_snapshot(room_id="182020001", table_label="T001"))

    decision = decide_runtime_state(
        instance_id="a1",
        session=session,
        candidates=[candidate],
        legacy_payload={"instance_id": "a1", "batch_id": "", "safe_summary": {}},
    )

    assert decision.accepted_state is None
    assert decision.rejected[0]["reason"] == "room_id_conflict"


def test_runtime_v2_reports_balance_only_without_state() -> None:
    session = RoomSession(instance_id="a1", expected_room_id="182020003", expected_room_label="T003")
    candidate = candidate_from_runtime_snapshot(
        _runtime_snapshot(room_id="182020003", game_no="", timed=None, action=None, balance_cents=9288)
    )

    decision = decide_runtime_state(
        instance_id="a1",
        session=session,
        candidates=[candidate],
        legacy_payload={"instance_id": "a1", "batch_id": "", "safe_summary": {}},
    )

    assert decision.accepted_state is None
    assert decision.accepted_balance is candidate
    assert any(item["reason"] == "balance_only_no_state_signal" for item in decision.rejected)


def test_runtime_v2_merges_room_metadata_into_state_candidate() -> None:
    session = RoomSession(instance_id="a1")
    state_candidate = candidate_from_runtime_snapshot(_runtime_snapshot(room_id="", table_label=""))
    room_candidate = RuntimeCandidate(
        source="label_runtime",
        instance_id="a1",
        room_label="T001",
        limit_label="4-250",
        confidence=0.9,
    )

    decision = decide_runtime_state(
        instance_id="a1",
        session=session,
        candidates=[state_candidate, room_candidate],
        legacy_payload={"instance_id": "a1", "batch_id": "", "safe_summary": {}},
    )

    assert decision.accepted_state is not None
    assert decision.accepted_state.batch_id == state_candidate.batch_id
    assert decision.accepted_state.room_label == "T001"
    assert decision.accepted_state.limit_label == "4-250"
    assert decision.accepted_state.evidence["merged_room_metadata_source"] == "label_runtime"


def test_runtime_v2_shadow_uses_legacy_room_session() -> None:
    legacy = {
        "instance_id": "a1",
        "batch_id": "",
        "exact_countdown": -1,
        "ocr_balance": "",
        "safe_summary": {
            "room_entry_expected_room_id": "182020003",
            "room_entry_expected_room_label": "T003",
        },
    }

    decision = build_runtime_v2_shadow_decision(
        instance_id="a1",
        legacy_payload=legacy,
        runtime_snapshot=_runtime_snapshot(room_id="182020003"),
    )

    payload = decision.to_safe_dict()
    assert payload["session"]["expected_room_label"] == "T003"
    assert payload["accepted_state"]["room_label"] == "T003"
    assert payload["accepted_state"]["countdown"] == 11


def test_runtime_v2_shadow_stable_countdown_keeps_drifting_on_repeated_raw_value(monkeypatch) -> None:
    batch_id = "50-1781400332-8504342624-1103"
    legacy = {
        "instance_id": "a1",
        "batch_id": batch_id,
        "exact_countdown": 12,
        "safe_summary": {
            "room_entry_expected_room_id": "182020001",
            "room_entry_expected_room_label": "T001",
        },
    }

    monkeypatch.setattr(decision_module, "now_ms", lambda: 100_000)
    first = build_runtime_v2_shadow_decision(
        instance_id="a1",
        legacy_payload=legacy,
        runtime_snapshot=_runtime_snapshot(room_id="182020001", table_label="T001", game_no=batch_id, timed=12),
    )

    monkeypatch.setattr(decision_module, "now_ms", lambda: 103_100)
    second = build_runtime_v2_shadow_decision(
        instance_id="a1",
        legacy_payload=legacy,
        runtime_snapshot=_runtime_snapshot(room_id="182020001", table_label="T001", game_no=batch_id, timed=12),
        previous_decision=first,
    )

    assert first.stable_state is not None
    assert first.stable_state.countdown == 12
    assert second.accepted_state is not None
    assert second.accepted_state.countdown == 12
    assert second.stable_state is not None
    assert second.stable_state.countdown == 9
    assert second.stable_state.evidence["stable_shadow"]["countdown_smoothed"] is True


def test_runtime_v2_merges_state_fields_from_distinct_sources() -> None:
    batch_id = "50-1781288535-8500595039-1419"
    session = RoomSession(instance_id="a3", expected_room_id="182020003", expected_room_label="T003")
    label = RuntimeCandidate(
        source="label_runtime",
        timestamp_ms=now_ms(),
        batch_id=batch_id,
        countdown=1,
        confidence=0.58,
    )
    runtime = RuntimeCandidate(
        source="page_runtime",
        timestamp_ms=now_ms(),
        room_id="182020003",
        room_label="T003",
        limit_label="4-250",
        batch_id=batch_id,
        phase_key="dealing",
        phase_label="dealing cards",
        current_load_type=10,
        confidence=0.0,
    )

    decision = decide_runtime_state(
        instance_id="a3",
        session=session,
        candidates=[runtime, label],
        legacy_payload={"instance_id": "a3", "safe_summary": {}},
    )

    assert decision.accepted_state is not None
    assert decision.accepted_state.room_label == "T003"
    assert decision.accepted_state.limit_label == "4-250"
    assert decision.accepted_state.batch_id == batch_id
    assert decision.accepted_state.countdown == 1
    assert decision.accepted_state.phase_key == "dealing"


def test_runtime_v2_merges_roomless_frontend_when_short_round_matches() -> None:
    batch_id = "50-1781281370-8500291836-1101"
    session = RoomSession(instance_id="a3", expected_room_id="182020003", expected_room_label="T003")
    label = RuntimeCandidate(
        source="label_runtime",
        timestamp_ms=now_ms(),
        batch_id=batch_id,
        countdown=1,
        confidence=0.58,
    )
    frontend = RuntimeCandidate(
        source="frontend_json_parse_state",
        timestamp_ms=now_ms() + 1,
        batch_id="8500291836",
        countdown=12,
        phase_key="betting_open",
        phase_label="betting window open",
        action=3,
        confidence=0.75,
    )

    decision = decide_runtime_state(
        instance_id="a3",
        session=session,
        candidates=[label, frontend],
        legacy_payload={"instance_id": "a3", "safe_summary": {}},
    )

    assert decision.accepted_state is not None
    assert decision.accepted_state.room_label == "T003"
    assert decision.accepted_state.batch_id == batch_id
    assert decision.accepted_state.countdown == 12
    assert decision.accepted_state.phase_key == "betting_open"
    assert decision.accepted_state.action == 3


def test_runtime_v2_frontend_event_derives_phase_from_action() -> None:
    candidate = candidate_from_frontend_event(
        FrontendStateEvent(
            timestamp_ms=now_ms(),
            event_type="json_parse_state",
            batch_id="8500291836",
            countdown=12,
            action=3,
            confidence=0.75,
        )
    )

    assert candidate is not None
    assert candidate.phase_key == "betting_open"
    assert candidate.phase_label == "betting window open"


def test_runtime_v2_anchors_to_legacy_active_batch_over_stale_candidates() -> None:
    stale_batch = "50-1781289517-8500631653-1410"
    active_batch = "50-1781289641-8500636051-132"
    legacy = {
        "instance_id": "a3",
        "batch_id": active_batch,
        "exact_countdown": 3,
        "ocr_balance": "50.71",
        "source": "label_runtime_resolved",
        "safe_summary": {
            "locked_room_id": "182020003",
            "locked_room_label": "T003",
            "limit_label": "4-250",
            "phase_text": "betting_open",
        },
    }
    stale_runtime = _runtime_snapshot(
        room_id="182020003",
        table_label="T003",
        game_no=stale_batch,
        timed=None,
        action=12,
        balance_cents=5071,
    )

    decision = build_runtime_v2_shadow_decision(
        instance_id="a3",
        legacy_payload=legacy,
        runtime_snapshot=stale_runtime,
    )

    assert decision.accepted_state is not None
    assert decision.accepted_state.source == "legacy_state"
    assert decision.accepted_state.batch_id == active_batch
    assert decision.accepted_state.countdown == 3
    assert decision.accepted_state.room_label == "T003"


def test_runtime_v2_uses_legacy_anchor_even_when_countdown_missing() -> None:
    stale_batch = "50-1781290215-8500656996-1409"
    active_batch = "50-1781290289-8500659714-180"
    legacy = {
        "instance_id": "a3",
        "batch_id": active_batch,
        "exact_countdown": -1,
        "ocr_balance": "50.71",
        "source": "label_runtime_resolved",
        "safe_summary": {
            "locked_room_id": "182020003",
            "locked_room_label": "T003",
            "limit_label": "4-250",
        },
    }
    stale_runtime = _runtime_snapshot(
        room_id="182020003",
        table_label="T003",
        game_no=stale_batch,
        timed=None,
        action=12,
        balance_cents=5071,
    )
    frontend = FrontendStateEvent(
        timestamp_ms=now_ms() + 1,
        event_type="json_parse_state",
        batch_id="8500659714",
        countdown=4,
        action=5,
        confidence=0.75,
    )

    decision = build_runtime_v2_shadow_decision(
        instance_id="a3",
        legacy_payload=legacy,
        runtime_snapshot=stale_runtime,
        frontend_events=[frontend],
    )

    assert decision.accepted_state is not None
    assert decision.accepted_state.source == "legacy_state"
    assert decision.accepted_state.batch_id == active_batch
    assert decision.accepted_state.countdown == 4
    assert decision.accepted_state.phase_key == "settling"


def test_runtime_v2_rejects_roomless_frontend_phase_without_round_anchor() -> None:
    batch_id = "50-1781289641-8500636051-132"
    session = RoomSession(
        instance_id="a3",
        expected_room_id="182020003",
        expected_room_label="T003",
        active_room_id="182020003",
        active_room_label="T003",
        active_batch_id=batch_id,
    )
    label = RuntimeCandidate(
        source="label_runtime",
        timestamp_ms=now_ms(),
        batch_id=batch_id,
        countdown=1,
        confidence=0.58,
    )
    unanchored_frontend = RuntimeCandidate(
        source="frontend_json_parse_state",
        timestamp_ms=now_ms() + 1,
        phase_key="settling",
        phase_label="payout or settling",
        action=5,
        confidence=0.25,
    )

    decision = decide_runtime_state(
        instance_id="a3",
        session=session,
        candidates=[label, unanchored_frontend],
        legacy_payload={"instance_id": "a3", "safe_summary": {}},
    )

    assert decision.accepted_state is not None
    assert decision.accepted_state.batch_id == batch_id
    assert decision.accepted_state.phase_key == ""
    assert decision.accepted_state.action is None


def test_runtime_v2_does_not_merge_future_roomless_frontend_into_current_round() -> None:
    batch_id = "50-1781290541-8500668935-186"
    session = RoomSession(
        instance_id="a3",
        expected_room_id="182020003",
        expected_room_label="T003",
        active_room_id="182020003",
        active_room_label="T003",
        active_batch_id=batch_id,
    )
    legacy = RuntimeCandidate(
        source="legacy_state",
        timestamp_ms=now_ms(),
        room_id="182020003",
        room_label="T003",
        limit_label="4-250",
        batch_id=batch_id,
        balance_text="50.71",
        confidence=0.58,
    )
    future_frontend = RuntimeCandidate(
        source="frontend_json_parse_state",
        timestamp_ms=now_ms() + 1,
        batch_id="8500669000",
        countdown=12,
        phase_key="betting_open",
        phase_label="betting window open",
        action=3,
        confidence=0.75,
    )

    decision = decide_runtime_state(
        instance_id="a3",
        session=session,
        candidates=[legacy, future_frontend],
        legacy_payload={"instance_id": "a3", "safe_summary": {}},
    )

    assert decision.accepted_state is not None
    assert decision.accepted_state.batch_id == batch_id
    assert decision.accepted_state.countdown is None
    assert decision.accepted_state.phase_key == ""
    assert decision.accepted_state.action is None


def test_runtime_v2_shadow_holds_recent_same_round_state_when_legacy_has_gap() -> None:
    batch_id = "50-1781311139-8501205346-150"
    previous = build_runtime_v2_shadow_decision(
        instance_id="a3",
        legacy_payload={
            "instance_id": "a3",
            "batch_id": batch_id,
            "exact_countdown": 4,
            "ocr_balance": "50.71",
            "source": "label_runtime_resolved",
            "safe_summary": {
                "locked_room_id": "182020003",
                "locked_room_label": "T003",
                "limit_label": "4-250",
                "phase_text": "settling",
            },
        },
    )

    current = build_runtime_v2_shadow_decision(
        instance_id="a3",
        legacy_payload={
            "instance_id": "a3",
            "batch_id": batch_id,
            "exact_countdown": -1,
            "ocr_balance": "50.71",
            "source": "label_runtime_resolved",
            "safe_summary": {
                "locked_room_id": "182020003",
                "locked_room_label": "T003",
                "limit_label": "4-250",
            },
        },
        previous_decision=previous,
    )

    assert current.accepted_state is not None
    assert current.accepted_state.room_label == "T003"
    assert current.accepted_state.batch_id == batch_id
    assert current.accepted_state.countdown == 4
    assert current.accepted_state.phase_key == "settling"
    assert "shadow_continuity" in current.accepted_state.evidence


def test_runtime_v2_shadow_marks_stable_state_after_repeated_same_runtime_key() -> None:
    batch_id = "50-1781358684-8502912453-1538"
    first = build_runtime_v2_shadow_decision(
        instance_id="a3",
        legacy_payload={
            "instance_id": "a3",
            "batch_id": batch_id,
            "exact_countdown": 12,
            "ocr_balance": "50.71",
            "source": "label_runtime_resolved",
            "safe_summary": {
                "locked_room_id": "182020002",
                "locked_room_label": "T002",
                "limit_label": "4-250",
                "phase_text": "betting_open",
            },
        },
    )

    second = build_runtime_v2_shadow_decision(
        instance_id="a3",
        legacy_payload={
            "instance_id": "a3",
            "batch_id": batch_id,
            "exact_countdown": 11,
            "ocr_balance": "50.71",
            "source": "label_runtime_resolved",
            "safe_summary": {
                "locked_room_id": "182020002",
                "locked_room_label": "T002",
                "limit_label": "4-250",
                "phase_text": "betting_open",
            },
        },
        previous_decision=first,
    )

    assert first.stable_state is not None
    assert first.stable_state.evidence["stable_shadow"]["frame_count"] == 1
    assert second.stable_state is not None
    assert second.stable_state.batch_id == batch_id
    assert second.stable_state.evidence["stable_shadow"]["frame_count"] == 2
    assert second.to_safe_dict()["stable_state"]["evidence"]["stable_shadow"]["ready_frames"] == 2


def test_runtime_v2_shadow_holds_stable_state_briefly_when_current_frame_is_incomplete() -> None:
    batch_id = "50-1781358684-8502912453-1538"
    first = build_runtime_v2_shadow_decision(
        instance_id="a3",
        legacy_payload={
            "instance_id": "a3",
            "batch_id": batch_id,
            "exact_countdown": 12,
            "ocr_balance": "50.71",
            "source": "label_runtime_resolved",
            "safe_summary": {
                "locked_room_label": "T002",
                "phase_text": "betting_open",
            },
        },
    )
    second = build_runtime_v2_shadow_decision(
        instance_id="a3",
        legacy_payload={
            "instance_id": "a3",
            "batch_id": batch_id,
            "exact_countdown": 11,
            "ocr_balance": "50.71",
            "source": "label_runtime_resolved",
            "safe_summary": {
                "locked_room_label": "T002",
                "phase_text": "betting_open",
            },
        },
        previous_decision=first,
    )
    held = build_runtime_v2_shadow_decision(
        instance_id="a3",
        legacy_payload={
            "instance_id": "a3",
            "batch_id": batch_id,
            "exact_countdown": -1,
            "ocr_balance": "50.71",
            "source": "label_runtime_resolved",
            "safe_summary": {
                "locked_room_label": "T002",
            },
        },
        previous_decision=second,
    )

    assert held.stable_state is not None
    assert held.stable_state.batch_id == batch_id
    assert held.stable_state.evidence["stable_shadow"]["held"] is True
