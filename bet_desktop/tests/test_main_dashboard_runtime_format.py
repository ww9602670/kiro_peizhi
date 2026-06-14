from __future__ import annotations

import pytest

from bet_desktop.models.state_temporal_guard import TemporalStateSnapshot, now_ms
from bet_desktop.ui.main_dashboard import InstanceCard, MainDashboard


class _TextLabel:
    def __init__(self) -> None:
        self.text = ""
        self.tooltip = ""

    def setText(self, value: str) -> None:
        self.text = str(value)

    def setToolTip(self, value: str) -> None:
        self.tooltip = str(value)


class _StateDot:
    def set_state(self, value: str) -> None:
        self.value = value


class _ProgressPanel:
    def __init__(self, status: str) -> None:
        self._headless_status = {"a3": status}
        self.updates: list[dict[str, str]] = []

    def update_headless_progress(self, updates: dict[str, str]) -> None:
        self.updates.append(dict(updates))
        self._headless_status.update(updates)


def _make_card() -> InstanceCard:
    card = InstanceCard.__new__(InstanceCard)
    card._last_temporal_update_ms = 0
    card.batch_id = _TextLabel()
    card.countdown = _TextLabel()
    card.balance = _TextLabel()
    card.proxy = _TextLabel()
    card.fingerprint = _TextLabel()
    card.runtime = _TextLabel()
    card.game_no = _TextLabel()
    card.runtime_phase = _TextLabel()
    card.runtime_coordinates = _TextLabel()
    card.dot = _StateDot()
    card.status_label = _TextLabel()
    card.title_label = _TextLabel()
    card.fingerprint_state = ""
    card._set_ws_heartbeat = lambda *_args, **_kwargs: None
    return card


def _make_dashboard_with_progress(status: str) -> tuple[MainDashboard, _ProgressPanel]:
    panel = _ProgressPanel(status)
    dashboard = MainDashboard.__new__(MainDashboard)
    dashboard.controller_panel = panel
    dashboard._room_entry_started_ms = {"a3": now_ms()}
    dashboard._room_entry_targets = {}
    dashboard._room_entry_progress_log_keys = {}
    return dashboard, panel


def test_runtime_room_label_prefers_table_code_and_maps_room_id() -> None:
    assert InstanceCard._valid_room_label("9101", "T001") == "T001"
    assert InstanceCard._room_label_from_room_id("9101") == "T001"
    assert InstanceCard._room_label_from_room_id("182020001") == "T001"


def test_runtime_limit_label_rejects_game_no_shape() -> None:
    assert InstanceCard._valid_limit_label("50-1780805809-8482745008-242", "4-250") == "4-250"


def test_runtime_limit_label_formats_user_limit_cents() -> None:
    assert InstanceCard._limit_label_from_cents(400, 25000) == "4-250"


def test_runtime_phase_action_takes_priority_over_false_canbet() -> None:
    assert InstanceCard._phase_key_from_values(3, False, None, "unknown") == "betting_open"
    assert InstanceCard._phase_key_from_values(10, False, None, "unknown") == "dealing"
    assert InstanceCard._phase_key_from_values(None, False, None, "unknown") == "betting_closed"


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (3, "betting_open"),
        (2, "pre_bet"),
        (9, "pre_bet"),
        (10, "dealing"),
        (11, "opening"),
        (12, "opening"),
        (5, "settling"),
        (13, "settling"),
    ],
)
def test_runtime_action_mapping_in_phase_key(action: int, expected: str) -> None:
    assert InstanceCard._phase_key_from_values(action, None, None, "") == expected


def test_instance_card_update_from_temporal_snapshot_uses_snapshot_balance_and_tail_batch() -> None:
    card = _make_card()
    snapshot = TemporalStateSnapshot(
        instance_id="a1",
        batch_id="50-1780905135-8486868732-1302",
        exact_countdown=12,
        ocr_balance="88.80",
        safe_summary={
            "frontend_room_label": "T001",
            "frontend_limit_label": "4-250",
            "frontend_runtime_action": 3,
        },
        timestamp_captured_ms=now_ms(),
        source="test",
    )

    card.update_temporal_state(snapshot)

    assert card.batch_id.text == "50-1780905135-8486868732"
    assert "50-1780905135-8486868732-1302" in card.batch_id.tooltip
    assert card.countdown.text == "12 秒"
    assert card.balance.text == "88.80"
    assert card.game_no.text == "T001 限额 4-250"


def test_instance_card_normalizes_same_room_batch_tail_for_ui_display() -> None:
    first = _make_card()
    second = _make_card()

    first.update_temporal_state(
        TemporalStateSnapshot(
            instance_id="a1",
            batch_id="50-1781319961-8501464448-1266",
            exact_countdown=4,
            source="test",
        )
    )
    second.update_temporal_state(
        TemporalStateSnapshot(
            instance_id="a2",
            batch_id="50-1781319961-8501464448-1255",
            exact_countdown=4,
            source="test",
        )
    )

    assert first.batch_id.text == "50-1781319961-8501464448"
    assert second.batch_id.text == first.batch_id.text
    assert "1266" in first.batch_id.tooltip
    assert "1255" in second.batch_id.tooltip


def test_frontend_action_overrides_false_canbet_in_runtime_display() -> None:
    card = _make_card()
    snapshot = TemporalStateSnapshot(
        instance_id="a1",
        batch_id="50-1780905135-8486868732-1302",
        exact_countdown=12,
        safe_summary={
            "runtime_action": 5,
            "frontend_runtime_action": 3,
            "frontend_runtime_is_can_betting": False,
        },
        source="test",
    )

    card.update_temporal_state(snapshot)

    assert "下注中" in card.runtime_phase.text


def test_runtime_phase_hides_internal_debug_fields_in_main_display() -> None:
    card = _make_card()
    snapshot = TemporalStateSnapshot(
        instance_id="a1",
        batch_id="50-1780905135-8486868732-1302",
        exact_countdown=4,
        safe_summary={
            "runtime_room_label": "T001",
            "runtime_limit_label": "4-250",
            "runtime_action": 5,
            "runtime_current_load_type": 10,
            "runtime_is_can_betting": False,
            "runtime_selected_bet": 400,
            "runtime_pending_chip_cents": 0,
        },
        source="test",
    )

    card.update_temporal_state(snapshot)

    assert card.runtime_phase.text == "不可下注 · 派彩中"
    assert "action=" not in card.runtime_phase.text
    assert "canBet=" not in card.runtime_phase.text
    assert "selectedBet=" not in card.runtime_phase.text
    assert "pending=" not in card.runtime_phase.text


def test_instance_card_displays_top_level_runtime_coordinates() -> None:
    card = _make_card()
    snapshot = TemporalStateSnapshot(
        instance_id="a1",
        batch_id="50-1780905135-8486868732-1302",
        exact_countdown=12,
        safe_summary={
            "runtime_coordinates": {
                "chips": {
                    "chip_4": {"center": [233, 489]},
                    "chip_10": {"center": [333, 489]},
                },
                "bet_regions": {
                    "bet_player": {"center": [224, 218]},
                    "bet_banker": {"center": [732, 218]},
                    "bet_tie": {"center": [479, 359]},
                },
            },
            "runtime_viewport": {"width": 960, "height": 620},
            "runtime_layout_confidence": 0.91,
            "runtime_room_label": "T001",
            "runtime_limit_label": "4-250",
        },
        source="test",
    )

    card.update_temporal_state(snapshot)

    assert "2" in card.runtime_coordinates.text
    assert "3" in card.runtime_coordinates.text
    assert "chip_4" in card.runtime_coordinates.tooltip
    assert "bet_player" in card.runtime_coordinates.tooltip
    assert "960" in card.runtime_coordinates.tooltip


def test_room_entry_progress_finishes_when_target_room_runtime_state_is_valid() -> None:
    dashboard, panel = _make_dashboard_with_progress("已点击房间 3，加载中")
    snapshot = TemporalStateSnapshot(
        instance_id="a3",
        batch_id="50-1781287735-8500564344-1406",
        exact_countdown=12,
        ocr_balance="49.39",
        safe_summary={
            "runtime_room_label": "T003",
            "runtime_action": 3,
            "last_ws_age_ms": 120,
        },
        ws_connected=True,
        source="test",
    )

    dashboard._update_room_entry_progress_from_temporal_state(snapshot)

    assert "房间 3 状态完整 6/6" in panel._headless_status["a3"]


def test_room_entry_progress_uses_heartbeat_age_for_ws_readiness() -> None:
    dashboard, panel = _make_dashboard_with_progress("已点击房间 3，加载中")
    snapshot = TemporalStateSnapshot(
        instance_id="a3",
        batch_id="50-1781324030-8501604838-1212",
        exact_countdown=11,
        ocr_balance="50.71",
        safe_summary={
            "runtime_room_label": "T003",
            "runtime_action": 10,
            "last_ws_age_ms": 225,
        },
        ws_connected=False,
        source="test",
    )

    dashboard._update_room_entry_progress_from_temporal_state(snapshot)

    assert "等待WS" not in panel._headless_status["a3"]
    assert "状态完整 6/6" in panel._headless_status["a3"]


def test_room_entry_progress_can_use_shadow_state_without_updating_card_fields() -> None:
    dashboard, panel = _make_dashboard_with_progress("进入房间 2 3/6 游戏页已打开")
    dashboard._room_entry_targets = {"a4": 2}
    dashboard._room_entry_started_ms = {"a4": now_ms()}
    dashboard.latest_temporal_states = {
        "a4": TemporalStateSnapshot(
            instance_id="a4",
            batch_id="",
            exact_countdown=-1,
            ocr_balance="115.22",
            safe_summary={"last_ws_age_ms": 206},
            source="test",
        )
    }

    dashboard.on_runtime_v2_shadow(
        "a4",
        {
            "accepted_state": {
                "room_label": "T002",
                "batch_id": "50-1781324015-8501604313-1163",
                "countdown": 3,
                "phase_key": "settling",
            },
            "accepted_balance": {"balance_text": "115.22"},
        },
    )

    assert "房间 2 状态完整 6/6" in panel._headless_status["a4"]


def test_room_entry_progress_waits_for_stable_shadow_frames() -> None:
    dashboard, panel = _make_dashboard_with_progress("杩涘叆鎴块棿 2 3/6 娓告垙椤靛凡鎵撳紑")
    dashboard._room_entry_targets = {"a4": 2}
    dashboard._room_entry_started_ms = {"a4": now_ms()}
    dashboard.latest_temporal_states = {
        "a4": TemporalStateSnapshot(
            instance_id="a4",
            batch_id="",
            exact_countdown=-1,
            ocr_balance="115.22",
            safe_summary={"last_ws_age_ms": 206},
            source="test",
        )
    }

    dashboard.on_runtime_v2_shadow(
        "a4",
        {
            "accepted_state": {
                "room_label": "T002",
                "batch_id": "50-1781324015-8501604313-1163",
                "countdown": 3,
                "phase_key": "settling",
            },
            "stable_state": {
                "room_label": "T002",
                "batch_id": "50-1781324015-8501604313-1163",
                "countdown": 3,
                "phase_key": "settling",
                "evidence": {
                    "stable_shadow": {
                        "frame_count": 1,
                        "ready_frames": 2,
                        "held": False,
                    }
                },
            },
            "accepted_balance": {"balance_text": "115.22"},
        },
    )

    assert "6/6" not in panel._headless_status["a4"]


def test_runtime_v2_shadow_stable_state_feeds_execution_orchestrator() -> None:
    class _Orchestrator:
        def __init__(self) -> None:
            self.snapshots: list[TemporalStateSnapshot] = []

        def on_temporal_state(self, snapshot: TemporalStateSnapshot) -> None:
            self.snapshots.append(snapshot)

    dashboard, _panel = _make_dashboard_with_progress("杩涘叆鎴块棿 2 3/6")
    dashboard._room_entry_targets = {"a4": 2}
    dashboard.latest_temporal_states = {
        "a4": TemporalStateSnapshot(
            instance_id="a4",
            batch_id="",
            exact_countdown=-1,
            ocr_balance="115.22",
            safe_summary={
                "last_ws_age_ms": 206,
                "runtime_coordinates": {"chips": {"4": [1, 2]}, "bet_regions": {"庄": [3, 4]}},
            },
            ws_connected=False,
            page_alive=False,
            source="test",
        )
    }
    orchestrator = _Orchestrator()
    dashboard._hedge_orchestrator = orchestrator

    dashboard.on_runtime_v2_shadow(
        "a4",
        {
            "timestamp_ms": now_ms(),
            "session": {"expected_room_id": "182020002", "expected_room_label": "T002"},
            "stable_state": {
                "source": "page_runtime",
                "room_label": "T002",
                "room_id": "182020002",
                "limit_label": "4-250",
                "batch_id": "50-1781324015-8501604313-1163",
                "countdown": 8,
                "phase_key": "betting_open",
                "confidence": 0.9,
                "evidence": {
                    "stable_shadow": {
                        "frame_count": 2,
                        "ready_frames": 2,
                        "held": False,
                    }
                },
            },
            "accepted_balance": {"balance_text": "115.22"},
        },
    )

    assert orchestrator.snapshots
    snapshot = orchestrator.snapshots[-1]
    assert snapshot.source == "runtime_v2_shadow_stable"
    assert snapshot.batch_id == "50-1781324015-8501604313-1163"
    assert snapshot.exact_countdown == 8
    assert snapshot.ws_connected is True
    assert snapshot.page_alive is True
    assert snapshot.safe_summary["runtime_v2_shadow_bridge"] is True
    assert snapshot.safe_summary["runtime_v2_shadow_online"] is True
    assert snapshot.safe_summary["runtime_coordinates"]["chips"]


def test_runtime_v2_shadow_inherits_ui_visible_limit_label() -> None:
    class _Orchestrator:
        def __init__(self) -> None:
            self.snapshots: list[TemporalStateSnapshot] = []

        def on_temporal_state(self, snapshot: TemporalStateSnapshot) -> None:
            self.snapshots.append(snapshot)

    dashboard, _panel = _make_dashboard_with_progress("room entry")
    dashboard._room_entry_targets = {"a2": 1}
    dashboard.latest_temporal_states = {
        "a2": TemporalStateSnapshot(
            instance_id="a2",
            batch_id="",
            exact_countdown=-1,
            ocr_balance="83.64",
            safe_summary={
                "last_ws_age_ms": 120,
                "frontend_limit_label": "4-250",
            },
            source="test",
        )
    }
    orchestrator = _Orchestrator()
    dashboard._hedge_orchestrator = orchestrator

    dashboard.on_runtime_v2_shadow(
        "a2",
        {
            "timestamp_ms": now_ms(),
            "session": {"expected_room_id": "182020001", "expected_room_label": "T001"},
            "stable_state": {
                "source": "page_runtime",
                "room_label": "T001",
                "room_id": "182020001",
                "batch_id": "50-1781324015-8501604313-1163",
                "countdown": 8,
                "phase_key": "betting_open",
                "confidence": 0.9,
            },
            "accepted_balance": {"balance_text": "83.64"},
        },
    )

    snapshot = orchestrator.snapshots[-1]
    assert snapshot.safe_summary["limit_label"] == "4-250"
    assert snapshot.safe_summary["runtime_limit_label"] == "4-250"


def test_room_entry_progress_does_not_finish_from_balance_only() -> None:
    dashboard, panel = _make_dashboard_with_progress("已点击房间 3，加载中")
    snapshot = TemporalStateSnapshot(
        instance_id="a3",
        batch_id="",
        exact_countdown=-1,
        ocr_balance="49.39",
        safe_summary={"runtime_room_label": "T003"},
        source="test",
    )

    dashboard._update_room_entry_progress_from_temporal_state(snapshot)

    assert "房间 3 已确认" in panel._headless_status["a3"]
    assert "等待局号" in panel._headless_status["a3"]
    assert "状态完整" not in panel._headless_status["a3"]
