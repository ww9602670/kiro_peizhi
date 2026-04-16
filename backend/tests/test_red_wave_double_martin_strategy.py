"""Tests for red_wave_double_martin directional behavior."""

import pytest

from app.engine.strategies.base import LotteryResult, StrategyContext
from app.engine.strategies.red_wave_double import RedWaveDoubleMartinStrategy


def _ctx(balls: list[int] | None = None) -> StrategyContext:
    history = []
    if balls is not None:
        history = [
            LotteryResult(
                issue="20260414001",
                balls=balls,
                sum_value=sum(balls),
            )
        ]
    return StrategyContext(
        current_issue="20260414002",
        history=history,
        balance=100_000,
        strategy_state={},
    )


def test_default_direction_is_ds4():
    strategy = RedWaveDoubleMartinStrategy(base_amount=100, sequence=[1, 2])
    assert strategy.direction_codes == ["DS4"]


def test_empty_directions_rejected():
    with pytest.raises(ValueError):
        RedWaveDoubleMartinStrategy(
            base_amount=100, sequence=[1, 2], direction_codes=[]
        )


def test_invalid_direction_rejected():
    with pytest.raises(ValueError):
        RedWaveDoubleMartinStrategy(
            base_amount=100, sequence=[1, 2], direction_codes=["DX1"]
        )


def test_non_red_wave_skips_when_inactive():
    strategy = RedWaveDoubleMartinStrategy(
        base_amount=100, sequence=[1, 2], direction_codes=["DS4"]
    )
    assert strategy.compute(_ctx([1, 1, 2])) == []
    assert strategy.states["DS4"].active is False


def test_sum_red_wave_starts_ds4_chase():
    strategy = RedWaveDoubleMartinStrategy(
        base_amount=100, sequence=[1, 2], direction_codes=["DS4"]
    )
    signals = strategy.compute(_ctx([1, 2, 3]))  # sum=6 red
    assert len(signals) == 1
    assert signals[0].key_code == "DS4"
    assert signals[0].amount == 100
    assert signals[0].martin_level == 0
    assert strategy.states["DS4"].active is True


def test_ball_direction_red_wave_starts_chase():
    strategy = RedWaveDoubleMartinStrategy(
        base_amount=100, sequence=[1, 2], direction_codes=["B1LM_S"]
    )
    signals = strategy.compute(_ctx([3, 1, 1]))  # ball1 red
    assert len(signals) == 1
    assert signals[0].key_code == "B1LM_S"
    assert signals[0].amount == 100
    assert strategy.states["B1LM_S"].active is True


def test_multi_directions_can_trigger_same_issue():
    strategy = RedWaveDoubleMartinStrategy(
        base_amount=100,
        sequence=[1, 2],
        direction_codes=["B1LM_S", "DS4"],
    )
    signals = strategy.compute(_ctx([3, 1, 2]))  # ball1 red, sum=6 red
    assert [s.key_code for s in signals] == ["B1LM_S", "DS4"]


def test_level_advances_only_for_matched_direction():
    strategy = RedWaveDoubleMartinStrategy(
        base_amount=100,
        sequence=[1, 2, 4],
        direction_codes=["B1LM_S", "DS4"],
    )
    strategy.compute(_ctx([3, 1, 2]))
    assert strategy.on_result(0, -100, key_code="B1LM_S") is None

    signals = strategy.compute(_ctx(None))
    by_code = {s.key_code: s for s in signals}
    assert by_code["B1LM_S"].amount == 200
    assert by_code["B1LM_S"].martin_level == 1
    assert by_code["DS4"].amount == 100
    assert by_code["DS4"].martin_level == 0


def test_win_resets_only_current_direction():
    strategy = RedWaveDoubleMartinStrategy(
        base_amount=100,
        sequence=[1, 2, 4],
        direction_codes=["B1LM_S", "DS4"],
    )
    strategy.compute(_ctx([3, 1, 2]))
    strategy.on_result(0, -100, key_code="B1LM_S")
    strategy.on_result(1, 100, key_code="B1LM_S")
    assert strategy.states["B1LM_S"].active is False
    assert strategy.states["B1LM_S"].level == 0
    assert strategy.states["DS4"].active is True
    assert strategy.states["DS4"].level == 0


def test_last_level_cycles_without_stop():
    strategy = RedWaveDoubleMartinStrategy(
        base_amount=100, sequence=[1, 2], direction_codes=["DS4"]
    )
    strategy.compute(_ctx([1, 2, 3]))  # sum=6 red
    strategy.on_result(0, -100, key_code="DS4")
    assert strategy.states["DS4"].level == 1
    strategy.on_result(0, -200, key_code="DS4")
    assert strategy.states["DS4"].active is True
    assert strategy.states["DS4"].level == 0
    signals = strategy.compute(_ctx(None))
    assert signals[0].amount == 100
    assert signals[0].martin_level == 0


def test_refund_keeps_level_and_active():
    strategy = RedWaveDoubleMartinStrategy(
        base_amount=100, sequence=[1, 2], direction_codes=["DS4"]
    )
    strategy.compute(_ctx([1, 2, 3]))
    strategy.on_result(0, -100, key_code="DS4")
    assert strategy.states["DS4"].level == 1
    strategy.on_result(-1, 0, key_code="DS4")
    assert strategy.states["DS4"].active is True
    assert strategy.states["DS4"].level == 1


def test_none_feedback_keeps_level():
    strategy = RedWaveDoubleMartinStrategy(
        base_amount=100, sequence=[1, 2], direction_codes=["DS4"]
    )
    strategy.compute(_ctx([1, 2, 3]))
    strategy.on_result(0, -100, key_code="DS4")
    strategy.on_result(None, 0, key_code="DS4")
    assert strategy.states["DS4"].level == 1


def test_unknown_or_missing_key_code_has_no_effect():
    strategy = RedWaveDoubleMartinStrategy(
        base_amount=100, sequence=[1, 2], direction_codes=["DS4"]
    )
    strategy.compute(_ctx([1, 2, 3]))
    strategy.on_result(0, -100, key_code="B1LM_S")
    assert strategy.states["DS4"].level == 0
    strategy.on_result(0, -100)
    assert strategy.states["DS4"].level == 0


def test_active_direction_can_continue_without_history():
    strategy = RedWaveDoubleMartinStrategy(
        base_amount=100, sequence=[1, 2], direction_codes=["DS4"]
    )
    strategy.compute(_ctx([1, 2, 3]))
    signals = strategy.compute(_ctx(None))
    assert len(signals) == 1
    assert signals[0].key_code == "DS4"


def test_loss_feedback_can_restore_inactive_direction_from_order_level():
    strategy = RedWaveDoubleMartinStrategy(
        base_amount=100, sequence=[1, 3, 9], direction_codes=["B2LM_S"]
    )

    strategy.on_result(0, -100, key_code="B2LM_S", martin_level=0)

    signals = strategy.compute(_ctx(None))
    assert strategy.states["B2LM_S"].active is True
    assert signals[0].key_code == "B2LM_S"
    assert signals[0].amount == 300
    assert signals[0].martin_level == 1


def test_win_feedback_resets_inactive_direction_from_order_level():
    strategy = RedWaveDoubleMartinStrategy(
        base_amount=100, sequence=[1, 3, 9], direction_codes=["B2LM_S"]
    )

    strategy.on_result(1, 99, key_code="B2LM_S", martin_level=1)

    assert strategy.states["B2LM_S"].active is False
    assert strategy.states["B2LM_S"].level == 0
