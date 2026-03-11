"""FlatStrategyImpl"""

import pytest

from app.engine.strategies.base import (
    BaseStrategy,
    BetInstruction,
    StrategyContext,
    LotteryResult,
)
from app.engine.strategies.registry import (
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


@pytest.fixture
def flat_cls():
    """ FlatStrategyImpl"""
    from app.engine.strategies.flat import FlatStrategyImpl
    # clean_registry clears the registry, so re-register after import
    register_strategy("flat")(FlatStrategyImpl)
    return FlatStrategyImpl


@pytest.fixture
def default_ctx() -> StrategyContext:
    """"""
    return StrategyContext(
        current_issue="20250101001",
        history=[
            LotteryResult(issue="20250101000", balls=[3, 5, 7], sum_value=15),
        ],
        balance=1_000_00,  # 1000  = 100000 
    )


# ---------------------------------------------------------------------------
# 
# ---------------------------------------------------------------------------

class TestFlatRegistration:
    """FlatStrategyImpl """

    def test_registered_as_flat(self, flat_cls):
        """ 'flat'"""
        assert "flat" in list_strategies()
        assert get_strategy_class("flat") is flat_cls

    def test_is_base_strategy_subclass(self, flat_cls):
        """FlatStrategyImpl  BaseStrategy """
        assert issubclass(flat_cls, BaseStrategy)


# ---------------------------------------------------------------------------
# 
# ---------------------------------------------------------------------------

class TestFlatInit:
    """"""

    def test_valid_single_key_code(self, flat_cls):
        """ key_code """
        s = flat_cls(key_codes=["DX1"], base_amount=1000)
        assert s._key_codes == ["DX1"]
        assert s._base_amount == 1000

    def test_valid_multiple_key_codes(self, flat_cls):
        """ key_codes """
        s = flat_cls(key_codes=["DX1", "DS3", "ZH7"], base_amount=500)
        assert len(s._key_codes) == 3

    def test_empty_key_codes_raises(self, flat_cls):
        """ key_codes  ValueError"""
        with pytest.raises(ValueError, match=""):
            flat_cls(key_codes=[], base_amount=1000)

    def test_zero_amount_raises(self, flat_cls):
        """base_amount=0  ValueError"""
        with pytest.raises(ValueError, match=" 0"):
            flat_cls(key_codes=["DX1"], base_amount=0)

    def test_negative_amount_raises(self, flat_cls):
        """ base_amount  ValueError"""
        with pytest.raises(ValueError, match=" 0"):
            flat_cls(key_codes=["DX1"], base_amount=-100)


# ---------------------------------------------------------------------------
# name() 
# ---------------------------------------------------------------------------

class TestFlatName:
    """name() """

    def test_name_returns_flat(self, flat_cls):
        s = flat_cls(key_codes=["DX1"], base_amount=1000)
        assert s.name() == "flat"


# ---------------------------------------------------------------------------
# compute() 
# ---------------------------------------------------------------------------

class TestFlatCompute:
    """compute()  BetInstruction"""

    def test_single_key_code(self, flat_cls, default_ctx):
        """ key_code  1 """
        s = flat_cls(key_codes=["DX1"], base_amount=1000)
        result = s.compute(default_ctx)
        assert len(result) == 1
        assert isinstance(result[0], BetInstruction)
        assert result[0].key_code == "DX1"
        assert result[0].amount == 1000

    def test_multiple_key_codes(self, flat_cls, default_ctx):
        """ key_codes """
        codes = ["DX1", "DS3", "ZH7"]
        s = flat_cls(key_codes=codes, base_amount=500)
        result = s.compute(default_ctx)
        assert len(result) == 3
        for i, bi in enumerate(result):
            assert bi.key_code == codes[i]
            assert bi.amount == 500

    def test_amount_is_integer(self, flat_cls, default_ctx):
        """"""
        s = flat_cls(key_codes=["DX1"], base_amount=1)
        result = s.compute(default_ctx)
        assert isinstance(result[0].amount, int)

    def test_compute_idempotent(self, flat_cls, default_ctx):
        """ compute """
        s = flat_cls(key_codes=["DX1", "DS4"], base_amount=2000)
        r1 = s.compute(default_ctx)
        r2 = s.compute(default_ctx)
        assert len(r1) == len(r2)
        for a, b in zip(r1, r2):
            assert a.key_code == b.key_code
            assert a.amount == b.amount

    def test_compute_ignores_context_balance(self, flat_cls):
        """"""
        s = flat_cls(key_codes=["DX1"], base_amount=1000)
        ctx_rich = StrategyContext(current_issue="1", history=[], balance=999_999_99)
        ctx_poor = StrategyContext(current_issue="1", history=[], balance=1)
        r_rich = s.compute(ctx_rich)
        r_poor = s.compute(ctx_poor)
        assert r_rich[0].amount == r_poor[0].amount == 1000

    def test_compute_ignores_history(self, flat_cls):
        """"""
        s = flat_cls(key_codes=["DS3"], base_amount=500)
        ctx_empty = StrategyContext(current_issue="1", history=[], balance=10000)
        ctx_with_history = StrategyContext(
            current_issue="1",
            history=[LotteryResult(issue="0", balls=[0, 0, 0], sum_value=0)],
            balance=10000,
        )
        assert s.compute(ctx_empty)[0].amount == s.compute(ctx_with_history)[0].amount


# ---------------------------------------------------------------------------
# on_result()   
# ---------------------------------------------------------------------------

class TestFlatOnResult:
    """on_result() """

    def test_on_result_win_no_side_effect(self, flat_cls, default_ctx):
        """ compute """
        s = flat_cls(key_codes=["DX1"], base_amount=1000)
        before = s.compute(default_ctx)
        s.on_result(is_win=1, pnl=950)
        after = s.compute(default_ctx)
        assert before[0].amount == after[0].amount
        assert before[0].key_code == after[0].key_code

    def test_on_result_lose_no_side_effect(self, flat_cls, default_ctx):
        """ compute """
        s = flat_cls(key_codes=["DX1"], base_amount=1000)
        before = s.compute(default_ctx)
        s.on_result(is_win=0, pnl=-1000)
        after = s.compute(default_ctx)
        assert before[0].amount == after[0].amount

    def test_on_result_refund_no_side_effect(self, flat_cls, default_ctx):
        """ compute """
        s = flat_cls(key_codes=["DX1"], base_amount=1000)
        before = s.compute(default_ctx)
        s.on_result(is_win=-1, pnl=0)
        after = s.compute(default_ctx)
        assert before[0].amount == after[0].amount

    def test_on_result_none_no_side_effect(self, flat_cls, default_ctx):
        """is_win=None """
        s = flat_cls(key_codes=["DX1"], base_amount=1000)
        s.on_result(is_win=None, pnl=0)  # 
        result = s.compute(default_ctx)
        assert len(result) == 1

    def test_multiple_on_result_calls(self, flat_cls, default_ctx):
        """ on_result  compute"""
        s = flat_cls(key_codes=["DX1", "DS3"], base_amount=2000)
        for _ in range(10):
            s.on_result(is_win=0, pnl=-2000)
        for _ in range(5):
            s.on_result(is_win=1, pnl=1000)
        result = s.compute(default_ctx)
        assert len(result) == 2
        assert all(bi.amount == 2000 for bi in result)
