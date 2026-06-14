from types import SimpleNamespace

from bet_desktop.coordinator.hedge_execution_orchestrator import (
    HedgeExecutionOrchestrator,
    HedgeExecutionState,
)
from bet_desktop.core.execution_events import ExecutionEventType
from bet_desktop.core.hedge_execution_config import HedgeExecutionConfig
from bet_desktop.models.state_temporal_guard import TemporalStateSnapshot, now_ms

from bet_desktop.core.addon_four import AddonFourFrequency, AddonFourMode


def _snapshot(
    instance_id: str,
    round_id: str,
    *,
    include_limit: bool = True,
    include_coordinates: bool = True,
    room_id: str = "room-1",
    countdown: int = 12,
    timestamp_ms: int | None = None,
    ws_connected: bool = True,
    page_alive: bool = True,
    phase_key: str = "betting_open",
) -> TemporalStateSnapshot:
    return TemporalStateSnapshot(
        instance_id=instance_id,
        batch_id="batch-1",
        exact_countdown=countdown,
        ocr_balance="2000",
        timestamp_captured_ms=now_ms() if timestamp_ms is None else timestamp_ms,
        ws_connected=ws_connected,
        page_alive=page_alive,
        safe_summary={
            "room_id": room_id,
            "table_id": "table-1",
            "round_id": round_id,
            "phase_key": phase_key,
            "proxy_latency_ms": 30,
            **({"table_min": 10, "table_max": 1000} if include_limit else {}),
            **(
                {"runtime_coordinates": {"chips": {"5": [0, 1]}, "bet_regions": {"庄": [0], "闲": [0]}}}
                if include_coordinates
                else {}
            ),
        },
    )


def test_start_and_redundant_start_emits_blocked():
    events: list = []
    orch = HedgeExecutionOrchestrator(event_listener=lambda event: events.append(event))
    config = HedgeExecutionConfig(main_amount_min=100, main_amount_max=100)

    orch.start(config)
    orch.start(config)

    assert orch.state == HedgeExecutionState.RUNNING
    assert events[0].event_type == ExecutionEventType.EXECUTION_STARTED
    assert events[-1].event_type == ExecutionEventType.EXECUTION_BLOCKED


def test_pause_and_stop_transitions():
    events: list = []
    orch = HedgeExecutionOrchestrator(event_listener=lambda event: events.append(event))
    orch.start(HedgeExecutionConfig(main_amount_min=100, main_amount_max=100))

    orch.pause()
    assert orch.state == HedgeExecutionState.PAUSED

    orch.stop()
    assert orch.state == HedgeExecutionState.STOPPED
    assert any(item.event_type == ExecutionEventType.EXECUTION_STOPPED for item in events)


def test_temporal_state_without_full_group_waits_for_snapshots():
    orch = HedgeExecutionOrchestrator()
    orch.start(HedgeExecutionConfig(main_amount_min=100, main_amount_max=100))
    orch.on_temporal_state(_snapshot("a1", round_id="R1"))

    assert orch.state == HedgeExecutionState.WAITING_NEXT_ROUND
    last_event = orch.events[-1]
    assert last_event.event_type == ExecutionEventType.EXECUTION_BLOCKED
    assert last_event.safe_summary.get("reason") == "waiting_temporal_state"
    missing = last_event.safe_summary.get("missing_instances")
    assert tuple(missing) == ("a2", "a3", "a4")


def test_excluded_account_missing_snapshot_is_not_required():
    orch = HedgeExecutionOrchestrator()
    orch.start(
        HedgeExecutionConfig(
            main_amount_min=100,
            main_amount_max=100,
            excluded_account_ids=("a3",),
        )
    )
    orch._latest_temporal_states = {
        "a1": _snapshot("a1", "R3"),
        "a2": _snapshot("a2", "R3"),
        "a4": _snapshot("a4", "R3"),
    }

    plan = orch.evaluate_current_round()

    assert plan is not None
    assert {leg.account_id for leg in plan.plan.legs} == {"a1", "a2", "a4"}


def test_evaluate_current_round_outputs_shadow_plan():
    orch = HedgeExecutionOrchestrator()
    orch.start(HedgeExecutionConfig(main_amount_min=100, main_amount_max=100))

    orch._latest_temporal_states = {
        "a1": _snapshot("a1", "R100"),
        "a2": _snapshot("a2", "R100"),
        "a3": _snapshot("a3", "R100"),
        "a4": _snapshot("a4", "R100"),
    }
    plan = orch.evaluate_current_round()

    assert plan is not None
    assert plan.round_id == "R100"
    assert plan.plan.main_amount == 100
    assert len(plan.plan.legs) == 4
    assert orch.state == HedgeExecutionState.WAITING_NEXT_ROUND
    event_types = {item.event_type for item in orch.events}
    assert ExecutionEventType.PLAN_GENERATED in event_types
    assert ExecutionEventType.BET_EXECUTION_STARTED in event_types
    execution_events = [
        item for item in orch.events if item.event_type == ExecutionEventType.BET_EXECUTION_STARTED
    ]
    assert execution_events[-1].state == HedgeExecutionState.SHADOW_EXECUTING.value
    assert execution_events[-1].safe_summary["live_clicks"] == 0


def test_round_suffixes_are_normalized_for_sync_gate():
    orch = HedgeExecutionOrchestrator()
    orch.start(HedgeExecutionConfig(main_amount_min=100, main_amount_max=100))

    orch._latest_temporal_states = {
        "a1": _snapshot("a1", "50-1781371346-8503490059-1108"),
        "a2": _snapshot("a2", "50-1781371346-8503490059-165"),
        "a3": _snapshot("a3", "50-1781371346-8503490059-244"),
        "a4": _snapshot("a4", "50-1781371346-8503490059-999"),
    }

    plan = orch.evaluate_current_round()

    assert plan is not None
    assert plan.round_id == "50-1781371346-8503490059-1108"


def test_countdown_skew_waits_for_synchronized_snapshots():
    orch = HedgeExecutionOrchestrator()
    orch.start(HedgeExecutionConfig(main_amount_min=100, main_amount_max=100))

    orch._latest_temporal_states = {
        "a1": _snapshot("a1", "R200", countdown=2, phase_key="pre_bet"),
        "a2": _snapshot("a2", "R200", countdown=12, phase_key="betting_open"),
        "a3": _snapshot("a3", "R200", countdown=12, phase_key="betting_open"),
        "a4": _snapshot("a4", "R200", countdown=12, phase_key="betting_open"),
    }

    plan = orch.evaluate_current_round()

    assert plan is None
    assert orch.state == HedgeExecutionState.WAITING_NEXT_ROUND
    last_event = orch.events[-1]
    assert last_event.event_type == ExecutionEventType.EXECUTION_BLOCKED
    assert last_event.safe_summary["reason"] == "waiting_synchronized_snapshots"
    assert "countdown_skew" in last_event.safe_summary["reasons"]


def test_repeated_sync_block_is_throttled_to_keep_ui_responsive():
    events: list = []
    orch = HedgeExecutionOrchestrator(event_listener=lambda event: events.append(event))
    orch.start(HedgeExecutionConfig(main_amount_min=100, main_amount_max=100))
    orch._latest_temporal_states = {
        "a1": _snapshot("a1", "R200", countdown=2, phase_key="pre_bet"),
        "a2": _snapshot("a2", "R200", countdown=12, phase_key="betting_open"),
        "a3": _snapshot("a3", "R200", countdown=12, phase_key="betting_open"),
        "a4": _snapshot("a4", "R200", countdown=12, phase_key="betting_open"),
    }

    assert orch.evaluate_current_round() is None
    event_count = len(events)
    assert orch.evaluate_current_round() is None

    assert len(events) == event_count


def test_volatile_sync_block_details_are_throttled_to_keep_ui_responsive():
    events: list = []
    orch = HedgeExecutionOrchestrator(event_listener=lambda event: events.append(event))
    orch.start(HedgeExecutionConfig(main_amount_min=100, main_amount_max=100))

    for index in range(20):
        orch._latest_temporal_states = {
            "a1": _snapshot("a1", f"R{index}", countdown=2, phase_key="pre_bet"),
            "a2": _snapshot("a2", f"R{index + 1}", countdown=12, phase_key="betting_open"),
            "a3": _snapshot("a3", f"R{index + 2}", countdown=12, phase_key="betting_open"),
            "a4": _snapshot("a4", f"R{index + 3}", countdown=12, phase_key="betting_open"),
        }
        assert orch.evaluate_current_round() is None

    blocked_events = [
        item for item in events if item.event_type == ExecutionEventType.EXECUTION_BLOCKED
    ]
    assert len(blocked_events) == 1


def test_stale_snapshot_waits_for_refresh_before_execution():
    orch = HedgeExecutionOrchestrator()
    orch.start(HedgeExecutionConfig(main_amount_min=100, main_amount_max=100))
    stale_ms = now_ms() - 3000

    orch._latest_temporal_states = {
        "a1": _snapshot("a1", "R201"),
        "a2": _snapshot("a2", "R201"),
        "a3": _snapshot("a3", "R201"),
        "a4": _snapshot("a4", "R201", timestamp_ms=stale_ms),
    }

    plan = orch.evaluate_current_round()

    assert plan is None
    last_event = orch.events[-1]
    assert last_event.safe_summary["reason"] == "waiting_synchronized_snapshots"
    assert "stale_snapshot" in last_event.safe_summary["reasons"]
    assert last_event.safe_summary["stale_instances"] == ("a4",)


def test_mixed_short_and_full_round_ids_are_synchronized():
    orch = HedgeExecutionOrchestrator()
    orch.start(HedgeExecutionConfig(main_amount_min=100, main_amount_max=100))

    orch._latest_temporal_states = {
        "a1": _snapshot("a1", "50-1781428894-8505428140-1152"),
        "a2": _snapshot("a2", "8505428140"),
        "a3": _snapshot("a3", "50-1781428894-8505428140-198"),
        "a4": _snapshot("a4", "50-1781428894-8505428140-1241"),
    }

    plan = orch.evaluate_current_round()

    assert plan is not None
    blocked = [item for item in orch.events if item.event_type == ExecutionEventType.EXECUTION_BLOCKED]
    assert not any("round_mismatch" in item.safe_summary.get("reasons", ()) for item in blocked)


def test_missing_real_round_id_does_not_fallback_to_timestamp():
    orch = HedgeExecutionOrchestrator()
    orch.start(HedgeExecutionConfig(main_amount_min=100, main_amount_max=100))

    orch._latest_temporal_states = {
        instance_id: TemporalStateSnapshot(
            instance_id=instance_id,
            batch_id="",
            exact_countdown=12,
            ocr_balance="2000",
            ws_connected=True,
            page_alive=True,
            safe_summary={
                "room_id": "room-1",
                "phase_key": "betting_open",
                "table_min": 10,
                "table_max": 1000,
                "runtime_coordinates": {"chips": {"5": [0, 1]}, "bet_regions": {"banker": [0], "player": [0]}},
            },
        )
        for instance_id in ("a1", "a2", "a3", "a4")
    }

    plan = orch.evaluate_current_round()

    assert plan is None
    last_event = orch.events[-1]
    assert "missing_round" in last_event.safe_summary["reasons"]
    assert set(last_event.safe_summary["round_keys"].values()) == {""}


def test_shadow_stable_snapshots_are_execution_online_even_if_legacy_page_flags_are_false():
    orch = HedgeExecutionOrchestrator()
    orch.start(HedgeExecutionConfig(main_amount_min=100, main_amount_max=100))

    orch._latest_temporal_states = {
        instance_id: TemporalStateSnapshot(
            instance_id=instance_id,
            batch_id="50-1781428894-8505428140-1152",
            exact_countdown=12,
            ocr_balance="2000",
            source="runtime_v2_shadow_stable",
            ws_connected=False,
            page_alive=False,
            safe_summary={
                "room_id": "room-1",
                "round_id": "50-1781428894-8505428140-1152",
                "phase_key": "betting_open",
                "runtime_v2_shadow_bridge": True,
                "runtime_v2_shadow_online": True,
                "table_min": 10,
                "table_max": 1000,
                "runtime_coordinates": {"chips": {"5": [0, 1]}, "bet_regions": {"banker": [0], "player": [0]}},
            },
        )
        for instance_id in ("a1", "a2", "a3", "a4")
    }

    plan = orch.evaluate_current_round()

    assert plan is not None
    blocked = [item for item in orch.events if item.event_type == ExecutionEventType.EXECUTION_BLOCKED]
    assert not any("offline_or_page_dead" in item.safe_summary.get("reasons", ()) for item in blocked)


def test_orchestrator_calls_bound_shadow_executor():
    class FakeShadowExecutor:
        def __init__(self) -> None:
            self.requests = []

        def execute_shadow_run(self, request):
            self.requests.append(request)
            return SimpleNamespace(
                phase="shadow_execute",
                safe_summary={"mode": "shadow_dry_run", "live_clicks": 0},
            )

    executor = FakeShadowExecutor()
    orch = HedgeExecutionOrchestrator(shadow_executor=executor)
    orch.start(HedgeExecutionConfig(main_amount_min=100, main_amount_max=100))
    orch._latest_temporal_states = {
        "a1": _snapshot("a1", "R101"),
        "a2": _snapshot("a2", "R101"),
        "a3": _snapshot("a3", "R101"),
        "a4": _snapshot("a4", "R101"),
    }

    plan = orch.evaluate_current_round()

    assert plan is not None
    assert len(executor.requests) == 1
    assert executor.requests[0].batch_id == "R101"
    leg_events = [
        item for item in orch.events if item.event_type == ExecutionEventType.BET_LEG_SUCCEEDED
    ]
    assert leg_events[-1].safe_summary["shadow_executor"]["status"] == "completed"


def test_waiting_next_round_recover_and_recompute():
    orch = HedgeExecutionOrchestrator()
    orch.start(HedgeExecutionConfig(main_amount_min=100, main_amount_max=100))

    states = {
        "a1": _snapshot("a1", "R1"),
        "a2": _snapshot("a2", "R1"),
        "a3": _snapshot("a3", "R1"),
        "a4": _snapshot("a4", "R1"),
    }
    orch._latest_temporal_states = states
    first_plan = orch.evaluate_current_round()

    assert first_plan is not None
    assert first_plan.round_id == "R1"
    assert orch.state == HedgeExecutionState.WAITING_NEXT_ROUND
    assert orch._active_round_id == "R1"

    states_next = {
        "a1": _snapshot("a1", "R2"),
        "a2": _snapshot("a2", "R2"),
        "a3": _snapshot("a3", "R2"),
        "a4": _snapshot("a4", "R2"),
    }
    orch._latest_temporal_states = states_next
    second_plan = orch.evaluate_current_round()

    assert second_plan is not None
    assert second_plan.round_id == "R2"
    assert orch._active_round_id == "R2"


def test_excluded_account_missing_snapshot_not_blocked():
    orch = HedgeExecutionOrchestrator()
    config = HedgeExecutionConfig(
        main_account_id="a1",
        excluded_account_ids=("a4",),
        main_amount_min=100,
        main_amount_max=100,
    )
    orch.start(config)

    orch._latest_temporal_states = {
        "a1": _snapshot("a1", "R10"),
        "a2": _snapshot("a2", "R10"),
        "a3": _snapshot("a3", "R10"),
    }
    plan = orch.evaluate_current_round()

    assert plan is not None
    assert plan.plan.main_amount == 100
    assert len(plan.plan.legs) == 3
    blocked = [item for item in orch.events if item.event_type == ExecutionEventType.ACCOUNT_EXCLUDED]
    assert not blocked


def test_coordinate_or_limit_missing_is_fail_closed():
    orch = HedgeExecutionOrchestrator()
    orch.start(HedgeExecutionConfig(main_amount_min=100, main_amount_max=100))

    orch._latest_temporal_states = {
        "a1": _snapshot("a1", "R20", include_coordinates=True, include_limit=False),
        "a2": _snapshot("a2", "R20", include_coordinates=False, include_limit=True),
        "a3": _snapshot("a3", "R20"),
        "a4": _snapshot("a4", "R20"),
    }
    plan = orch.evaluate_current_round()

    assert plan is None
    assert orch.state == HedgeExecutionState.WAITING_NEXT_ROUND
    assert any(
        item.event_type == ExecutionEventType.EXECUTION_BLOCKED
        for item in orch.events
        if item.message and "坐标或限额缺失" in item.message
    )
    assert all(item.event_type != ExecutionEventType.PLAN_GENERATED for item in orch.events)


def test_addon_four_is_recorded_in_shadow_plan():
    orch = HedgeExecutionOrchestrator()
    config = HedgeExecutionConfig(
        main_amount_min=100,
        main_amount_max=100,
        addon_four_mode=AddonFourMode.BANKER,
        addon_four_frequency=AddonFourFrequency.EVERY_ROUND,
    )
    orch.start(config)

    orch._latest_temporal_states = {
        "a1": _snapshot("a1", "R8"),
        "a2": _snapshot("a2", "R8"),
        "a3": _snapshot("a3", "R8"),
        "a4": _snapshot("a4", "R8"),
    }
    plan = orch.evaluate_current_round()

    assert plan is not None
    assert plan.addon_four is not None
    assert plan.addon_four.get("enabled") is True
    addon_events = [
        item
        for item in orch.events
        if item.event_type == ExecutionEventType.ADDON_FOUR_DECIDED
    ]
    assert addon_events
    assert "额外风险动作" in addon_events[-1].message
