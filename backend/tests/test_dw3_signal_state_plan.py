from __future__ import annotations

from app.engine.dw3_plan import build_dw3_execution_plan
from app.engine.dw3_signal_state import (
    DW3SignalState,
    bootstrap_dw3_signal_state,
    compute_blocked_groups,
    update_dw3_signal_state_with_draw,
)
from app.engine.strategies.base import LotteryResult, StrategyContext
from app.engine.strategies.dw3 import DW3FlatStrategy


def test_compute_blocked_groups_n1_uses_latest_seen_signal() -> None:
    state = bootstrap_dw3_signal_state(
        platform_type="WEB",
        lottery_history=[{"issue": "20260418001", "draw": "111"}],
    )
    update_dw3_signal_state_with_draw(
        state=state,
        issue="20260418002",
        draw="321",
    )

    blocked_bs, blocked_oe = compute_blocked_groups(state=state, gate_window_issues=1)

    assert state.latest_closed_seq == 2
    assert blocked_bs == {"DW3_BS_SSS"}
    assert blocked_oe == {"DW3_OE_OEO"}


def test_bootstrap_accepts_ball_sequence_draws() -> None:
    state = bootstrap_dw3_signal_state(
        platform_type="WEB",
        lottery_history=[
            {"issue": "20260418001", "draw": [3, 2, 1]},
        ],
    )

    assert state.latest_closed_seq == 1
    assert state.last_seen_bs == {"DW3_BS_SSS": 1}
    assert state.last_seen_oe == {"DW3_OE_OEO": 1}


def test_dw3_strategy_uses_lottery_result_balls_for_gate_plan() -> None:
    strategy = DW3FlatStrategy(
        group_tokens=["DW3_BS_SSS", "DW3_OE_OEO"],
        base_amount=100,
        gate_window_issues=1,
    )
    ctx = StrategyContext(
        current_issue="20260418002",
        history=[
            LotteryResult(issue="20260418001", balls=[3, 2, 1], sum_value=6),
        ],
        balance=0,
    )

    instructions = strategy.compute(ctx)

    assert instructions == []
    assert strategy.last_signal_metadata["gate_skipped"] is True
    assert strategy.last_signal_metadata["skip_reason"] == "gate_skipped_all_blocked"


def test_compute_blocked_groups_n3_blocks_only_within_three_issues() -> None:
    state = DW3SignalState(
        platform_type="WEB",
        latest_closed_issue="20260418010",
        latest_closed_seq=10,
        last_seen_bs={
            "DW3_BS_BBB": 8,   # gap=2 -> blocked for N=3
            "DW3_BS_SSS": 7,   # gap=3 -> not blocked for N=3
        },
        last_seen_oe={
            "DW3_OE_OOO": 9,   # gap=1 -> blocked for N=3
            "DW3_OE_OEO": 7,   # gap=3 -> not blocked for N=3
        },
    )

    blocked_bs, blocked_oe = compute_blocked_groups(state=state, gate_window_issues=3)

    assert blocked_bs == {"DW3_BS_BBB"}
    assert blocked_oe == {"DW3_OE_OOO"}


def test_gate_skip_produces_no_bet_instructions_and_zero_stake() -> None:
    state = DW3SignalState(
        platform_type="WEB",
        latest_closed_issue="20260418010",
        latest_closed_seq=10,
        last_seen_bs={"DW3_BS_SSS": 10},
        last_seen_oe={"DW3_OE_OEO": 10},
    )

    plan = build_dw3_execution_plan(
        state=state,
        selected_bs={"DW3_BS_SSS"},
        selected_oe={"DW3_OE_OEO"},
        gate_window_issues=1,
        stage_amount=10,
        bucket_map={
            ("DW3_BS_SSS", "DW3_OE_OEO"): ["DW3_321"],
        },
    )

    assert plan.gate_skipped is True
    assert plan.merged_amounts == {}
    assert plan.total_stake == 0
    assert plan.effective_key_count == 0


def test_merged_amount_multiplier_math_and_total_stake_formula() -> None:
    state = DW3SignalState(
        platform_type="WEB",
        latest_closed_issue="20260418001",
        latest_closed_seq=1,
    )
    stage_amount = 10

    plan = build_dw3_execution_plan(
        state=state,
        selected_bs={"DW3_BS_BBB"},
        selected_oe={"DW3_OE_OOO"},
        gate_window_issues=0,
        stage_amount=stage_amount,
        bucket_map={
            ("DW3_BS_BBB", "DW3_OE_OOO"): ["DW3_000"],  # multiplier 2
            ("DW3_BS_BBB", "DW3_OE_EEE"): ["DW3_001"],  # multiplier 1
            ("DW3_BS_SSS", "DW3_OE_OOO"): ["DW3_002"],  # multiplier 1
            ("DW3_BS_SSS", "DW3_OE_EEE"): ["DW3_003"],  # multiplier 0
        },
    )

    assert plan.gate_skipped is False
    assert plan.merged_amounts == {
        "DW3_000": 20,
        "DW3_001": 10,
        "DW3_002": 10,
    }
    assert "DW3_003" not in plan.merged_amounts
    assert plan.effective_key_count == 3
    assert plan.total_stake == 125 * stage_amount * (1 + 1)
