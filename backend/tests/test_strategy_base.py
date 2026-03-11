""""""

import pytest
from typing import Optional

from app.engine.strategies.base import (
    BaseStrategy,
    BetInstruction,
    LotteryResult,
    StrategyContext,
)
from app.engine.strategies.registry import (
    _STRATEGY_REGISTRY,
    _clear_registry,
    get_strategy_class,
    list_strategies,
    register_strategy,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_registry():
    """"""
    _clear_registry()
    yield
    _clear_registry()


# ---------------------------------------------------------------------------
# Helper: 
# ---------------------------------------------------------------------------

class DummyStrategy(BaseStrategy):
    """"""

    def name(self) -> str:
        return "dummy"

    def compute(self, ctx: StrategyContext) -> list[BetInstruction]:
        return [BetInstruction(key_code="DX1", amount=1000)]


class AnotherStrategy(BaseStrategy):
    """"""

    def name(self) -> str:
        return "another"

    def compute(self, ctx: StrategyContext) -> list[BetInstruction]:
        return []


# ---------------------------------------------------------------------------
# BaseStrategy ABC 
# ---------------------------------------------------------------------------

class TestBaseStrategy:
    """BaseStrategy ABC """

    def test_cannot_instantiate_abc(self):
        """ BaseStrategy"""
        with pytest.raises(TypeError):
            BaseStrategy()  # type: ignore

    def test_concrete_strategy_instantiates(self):
        """"""
        s = DummyStrategy()
        assert s.name() == "dummy"

    def test_compute_returns_bet_instructions(self):
        """compute  BetInstruction """
        s = DummyStrategy()
        ctx = StrategyContext(
            current_issue="12345",
            history=[],
            balance=100000,
        )
        result = s.compute(ctx)
        assert len(result) == 1
        assert result[0].key_code == "DX1"
        assert result[0].amount == 1000

    def test_on_result_default_is_noop(self):
        """ on_result """
        s = DummyStrategy()
        #  is_win 
        s.on_result(1, 500)
        s.on_result(0, -1000)
        s.on_result(-1, 0)
        s.on_result(None, 0)


# ---------------------------------------------------------------------------
# 
# ---------------------------------------------------------------------------

class TestDataClasses:
    """StrategyContext / BetInstruction / LotteryResult """

    def test_strategy_context_defaults(self):
        """StrategyContext  strategy_state  dict"""
        ctx = StrategyContext(current_issue="100", history=[], balance=5000)
        assert ctx.strategy_state == {}

    def test_strategy_context_with_history(self):
        """StrategyContext """
        lr = LotteryResult(issue="99", balls=[3, 5, 7], sum_value=15)
        ctx = StrategyContext(
            current_issue="100",
            history=[lr],
            balance=5000,
            strategy_state={"martin_level": 2},
        )
        assert len(ctx.history) == 1
        assert ctx.history[0].sum_value == 15
        assert ctx.strategy_state["martin_level"] == 2

    def test_bet_instruction_fields(self):
        """BetInstruction """
        bi = BetInstruction(key_code="DS3", amount=2000)
        assert bi.key_code == "DS3"
        assert bi.amount == 2000

    def test_lottery_result_fields(self):
        """LotteryResult """
        lr = LotteryResult(issue="12345", balls=[1, 2, 3], sum_value=6)
        assert lr.issue == "12345"
        assert lr.balls == [1, 2, 3]
        assert lr.sum_value == 6

    def test_balance_and_amount_are_int(self):
        """"""
        ctx = StrategyContext(current_issue="1", history=[], balance=10000)
        bi = BetInstruction(key_code="DX1", amount=500)
        assert isinstance(ctx.balance, int)
        assert isinstance(bi.amount, int)


# ---------------------------------------------------------------------------
# 
# ---------------------------------------------------------------------------

class TestRegistry:
    """"""

    def test_register_and_get(self):
        """"""
        register_strategy("dummy")(DummyStrategy)
        cls = get_strategy_class("dummy")
        assert cls is DummyStrategy

    def test_register_multiple(self):
        """"""
        register_strategy("dummy")(DummyStrategy)
        register_strategy("another")(AnotherStrategy)
        assert get_strategy_class("dummy") is DummyStrategy
        assert get_strategy_class("another") is AnotherStrategy

    def test_list_strategies_empty(self):
        """"""
        assert list_strategies() == []

    def test_list_strategies(self):
        """list_strategies """
        register_strategy("flat")(DummyStrategy)
        register_strategy("martin")(AnotherStrategy)
        names = list_strategies()
        assert set(names) == {"flat", "martin"}

    def test_get_unregistered_raises_key_error(self):
        """ KeyError"""
        with pytest.raises(KeyError, match=""):
            get_strategy_class("nonexistent")

    def test_duplicate_registration_raises_value_error(self):
        """ ValueError"""
        register_strategy("dup")(DummyStrategy)
        with pytest.raises(ValueError, match=""):
            register_strategy("dup")(AnotherStrategy)

    def test_duplicate_registration_preserves_original(self):
        """"""
        register_strategy("dup")(DummyStrategy)
        with pytest.raises(ValueError):
            register_strategy("dup")(AnotherStrategy)
        # 
        assert get_strategy_class("dup") is DummyStrategy

    def test_decorator_returns_class_unchanged(self):
        """"""
        result = register_strategy("test")(DummyStrategy)
        assert result is DummyStrategy

    def test_registered_class_is_instantiable(self):
        """"""
        register_strategy("dummy")(DummyStrategy)
        cls = get_strategy_class("dummy")
        instance = cls()
        assert instance.name() == "dummy"
        ctx = StrategyContext(current_issue="1", history=[], balance=10000)
        instructions = instance.compute(ctx)
        assert len(instructions) > 0

    def test_clear_registry(self):
        """_clear_registry """
        register_strategy("dummy")(DummyStrategy)
        assert len(list_strategies()) == 1
        _clear_registry()
        assert len(list_strategies()) == 0
