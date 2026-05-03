import random

import pytest

from app.engine.strategies.base import LotteryResult, StrategyContext
from app.engine.strategies.omission_random import (
    AiRandomFlatStrategy,
    AiRandomMartinStrategy,
    AiSameRandomFlatStrategy,
    AiSameRandomMartinStrategy,
    OmissionRandomFlatStrategy,
    OmissionRandomMartinStrategy,
)


def _history(count: int = 100) -> list[LotteryResult]:
    return [
        LotteryResult(
            issue=str(1000 - index),
            balls=[index % 10, (index + 1) % 10, (index + 2) % 10],
            sum_value=(index % 10) + ((index + 1) % 10) + ((index + 2) % 10),
        )
        for index in range(count)
    ]


def _ctx() -> StrategyContext:
    return StrategyContext(
        current_issue="1001",
        history=_history(),
        balance=100000,
    )


def test_flat_generates_pick_count_per_selected_category():
    strategy = OmissionRandomFlatStrategy(
        base_amount=100,
        config={"pick_count": 5, "categories": ["ball1", "ball2"]},
        rng=random.Random(1),
    )

    signals = strategy.compute(_ctx())

    assert len(signals) == 10
    assert sum(1 for signal in signals if signal.key_code.startswith("B1QH")) == 5
    assert sum(1 for signal in signals if signal.key_code.startswith("B2QH")) == 5
    assert all(signal.amount == 100 for signal in signals)
    assert len({signal.key_code for signal in signals if signal.key_code.startswith("B1QH")}) == 5


def test_martin_levels_advance_independently_by_category():
    strategy = OmissionRandomMartinStrategy(
        base_amount=100,
        sequence=[1, 2, 4],
        config={"pick_count": 3, "categories": ["ball1", "ball2"]},
        rng=random.Random(2),
    )

    strategy.on_result(1, 100, key_code="ball1", martin_level=0)
    strategy.on_result(0, -300, key_code="ball2", martin_level=0)
    signals = strategy.compute(_ctx())

    ball1_amounts = {signal.amount for signal in signals if signal.key_code.startswith("B1QH")}
    ball2_amounts = {signal.amount for signal in signals if signal.key_code.startswith("B2QH")}
    assert ball1_amounts == {100}
    assert ball2_amounts == {200}


def test_adaptive_window_and_reverse_weight_are_category_scoped():
    strategy = OmissionRandomMartinStrategy(
        base_amount=100,
        sequence=[1, 2],
        config={"pick_count": 3, "categories": ["ball1", "ball2"]},
        rng=random.Random(3),
    )

    strategy.on_result(1, 100, key_code="ball1", martin_level=0)
    strategy.on_result(1, 100, key_code="ball1", martin_level=1)
    config = strategy.export_config()
    assert config["runtime_state"]["categories"]["ball1"]["history_window"] == 50
    assert config["runtime_state"]["categories"]["ball2"]["history_window"] == 100

    strategy.on_result(1, 100, key_code="ball1", martin_level=0)
    config = strategy.export_config()
    assert config["runtime_state"]["categories"]["ball1"]["history_window"] == 10

    strategy.on_result(0, -100, key_code="ball1", martin_level=0)
    strategy.on_result(0, -200, key_code="ball1", martin_level=1)
    config = strategy.export_config()
    assert config["runtime_state"]["categories"]["ball1"]["history_window"] == 100
    assert config["runtime_state"]["categories"]["ball1"]["reverse_active"] is False

    strategy.on_result(0, -100, key_code="ball1", martin_level=0)
    strategy.on_result(0, -200, key_code="ball1", martin_level=1)
    config = strategy.export_config()
    assert config["runtime_state"]["categories"]["ball1"]["reverse_active"] is True


def test_martin_exhaustion_alert_uses_chinese_copy():
    strategy = OmissionRandomMartinStrategy(
        base_amount=100,
        sequence=[1, 2],
        config={"pick_count": 3, "categories": ["ball1"]},
        strategy_name="333",
        rng=random.Random(4),
    )

    strategy.on_result(0, -300, key_code="ball1", martin_level=0)
    strategy.on_result(0, -600, key_code="ball1", martin_level=1)

    assert len(strategy.pending_alerts) == 1
    alert = strategy.pending_alerts[0]
    assert alert.title == "333 球1"
    assert "马丁已连续走完 2 档" in alert.detail
    assert "累计亏损 9.00" in alert.detail
    assert "已重置为第一档" in alert.detail
    assert "ran through" not in alert.detail


def test_ai_random_flat_uses_pure_uniform_pick_mode():
    strategy = AiRandomFlatStrategy(
        base_amount=100,
        config={"pick_count": 4, "categories": ["ball1", "sum"]},
        rng=random.Random(5),
    )

    signals = strategy.compute(_ctx())

    assert len(signals) == 8
    assert strategy.export_config()["weight_mode"] == "pure_random"
    assert {signal.metadata["strategy_kind"] for signal in signals} == {"ai_random"}
    assert sum(1 for signal in signals if signal.key_code.startswith("B1QH")) == 4
    assert sum(1 for signal in signals if signal.key_code.startswith("HZ")) == 4


def test_ai_same_random_flat_uses_same_digits_for_selected_balls():
    strategy = AiSameRandomFlatStrategy(
        base_amount=100,
        config={"pick_count": 4, "categories": ["ball1", "ball2", "ball3"]},
        rng=random.Random(5),
    )

    signals = strategy.compute(_ctx())

    assert len(signals) == 12
    digits_by_prefix = {
        prefix: {
            int(signal.key_code.rsplit("QH", 1)[1])
            for signal in signals
            if signal.key_code.startswith(prefix)
        }
        for prefix in ("B1QH", "B2QH", "B3QH")
    }
    assert digits_by_prefix["B1QH"] == digits_by_prefix["B2QH"]
    assert digits_by_prefix["B1QH"] == digits_by_prefix["B3QH"]
    assert all(not signal.key_code.startswith("HZ") for signal in signals)
    assert {signal.metadata["strategy_kind"] for signal in signals} == {"ai_same_random"}


def test_ai_same_random_rejects_sum_category():
    with pytest.raises(ValueError, match="sum category"):
        AiSameRandomFlatStrategy(
            base_amount=100,
            config={"pick_count": 3, "categories": ["ball1", "sum"]},
            rng=random.Random(1),
        )


def test_ai_same_random_martin_keeps_category_levels_and_shared_digits():
    strategy = AiSameRandomMartinStrategy(
        base_amount=100,
        sequence=[1, 2, 4],
        config={"pick_count": 3, "categories": ["ball1", "ball2"]},
        rng=random.Random(6),
    )

    strategy.on_result(0, -300, key_code="ball2", martin_level=0)
    signals = strategy.compute(_ctx())

    ball1_amounts = {signal.amount for signal in signals if signal.key_code.startswith("B1QH")}
    ball2_amounts = {signal.amount for signal in signals if signal.key_code.startswith("B2QH")}
    ball1_digits = {
        int(signal.key_code.rsplit("QH", 1)[1])
        for signal in signals
        if signal.key_code.startswith("B1QH")
    }
    ball2_digits = {
        int(signal.key_code.rsplit("QH", 1)[1])
        for signal in signals
        if signal.key_code.startswith("B2QH")
    }
    assert ball1_amounts == {100}
    assert ball2_amounts == {200}
    assert ball1_digits == ball2_digits


def test_ai_random_martin_does_not_adapt_history_window_or_reverse_weight():
    strategy = AiRandomMartinStrategy(
        base_amount=100,
        sequence=[1, 2],
        config={"pick_count": 3, "categories": ["ball1"]},
        rng=random.Random(6),
    )

    strategy.on_result(1, 100, key_code="ball1", martin_level=0)
    strategy.on_result(1, 100, key_code="ball1", martin_level=0)
    config = strategy.export_config()
    state = config["runtime_state"]["categories"]["ball1"]
    assert state["history_window"] == 100
    assert state["reverse_active"] is False

    strategy.on_result(0, -100, key_code="ball1", martin_level=0)
    strategy.on_result(0, -200, key_code="ball1", martin_level=1)
    config = strategy.export_config()
    state = config["runtime_state"]["categories"]["ball1"]
    assert state["history_window"] == 100
    assert state["reverse_active"] is False
