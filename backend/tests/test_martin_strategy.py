"""MartinStrategyImpl


- 
- 
- compute() base  sequence[level]
- on_result() win0 / losenext / refund
-  +  + martin_reset 
- level  [0, len(sequence)-1]
- round_loss 
- flush_alerts 
-  win/lose 
-  1
"""

import logging
from unittest.mock import AsyncMock, MagicMock

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
def martin_cls():
    """ MartinStrategyImpl"""
    from app.engine.strategies.martin import MartinStrategyImpl
    register_strategy("martin")(MartinStrategyImpl)
    return MartinStrategyImpl


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


@pytest.fixture
def mock_alert_service():
    """Mock AlertServicesend  AsyncMock"""
    svc = MagicMock()
    svc.send = AsyncMock(return_value=True)
    return svc


# ---------------------------------------------------------------------------
# 
# ---------------------------------------------------------------------------

class TestMartinRegistration:

    def test_registered_as_martin(self, martin_cls):
        assert "martin" in list_strategies()
        assert get_strategy_class("martin") is martin_cls

    def test_is_base_strategy_subclass(self, martin_cls):
        assert issubclass(martin_cls, BaseStrategy)


# ---------------------------------------------------------------------------
# 
# ---------------------------------------------------------------------------

class TestMartinInit:

    def test_valid_construction(self, martin_cls):
        s = martin_cls(key_codes=["DX1"], base_amount=1000, sequence=[1, 2, 4])
        assert s._key_codes == ["DX1"]
        assert s._base_amount == 1000
        assert s._sequence == [1, 2, 4]
        assert s.level == 0
        assert s.round_loss == 0

    def test_empty_key_codes_raises(self, martin_cls):
        with pytest.raises(ValueError, match=""):
            martin_cls(key_codes=[], base_amount=1000, sequence=[1, 2])

    def test_zero_amount_raises(self, martin_cls):
        with pytest.raises(ValueError, match=" 0"):
            martin_cls(key_codes=["DX1"], base_amount=0, sequence=[1, 2])

    def test_negative_amount_raises(self, martin_cls):
        with pytest.raises(ValueError, match=" 0"):
            martin_cls(key_codes=["DX1"], base_amount=-100, sequence=[1, 2])

    def test_empty_sequence_raises(self, martin_cls):
        with pytest.raises(ValueError, match=""):
            martin_cls(key_codes=["DX1"], base_amount=1000, sequence=[])

    def test_zero_in_sequence_raises(self, martin_cls):
        with pytest.raises(ValueError, match=" 0"):
            martin_cls(key_codes=["DX1"], base_amount=1000, sequence=[1, 0, 4])

    def test_negative_in_sequence_raises(self, martin_cls):
        with pytest.raises(ValueError, match=" 0"):
            martin_cls(key_codes=["DX1"], base_amount=1000, sequence=[1, -2, 4])


# ---------------------------------------------------------------------------
# name()
# ---------------------------------------------------------------------------

class TestMartinName:

    def test_name_returns_martin(self, martin_cls):
        s = martin_cls(key_codes=["DX1"], base_amount=1000, sequence=[1, 2, 4])
        assert s.name() == "martin"


# ---------------------------------------------------------------------------
# compute()  
# ---------------------------------------------------------------------------

class TestMartinCompute:

    def test_level_0_amount(self, martin_cls, default_ctx):
        """level=0  amount = base  sequence[0]"""
        s = martin_cls(key_codes=["DX1"], base_amount=1000, sequence=[1, 2, 4, 8, 16])
        result = s.compute(default_ctx)
        assert len(result) == 1
        assert result[0].key_code == "DX1"
        assert result[0].amount == 1000  # 1000  1

    def test_level_advances_amount(self, martin_cls, default_ctx):
        """lose  level """
        s = martin_cls(key_codes=["DX1"], base_amount=1000, sequence=[1, 2, 4, 8, 16])
        # level=0  amount=1000
        assert s.compute(default_ctx)[0].amount == 1000
        s.on_result(is_win=0, pnl=-1000)
        # level=1  amount=2000
        assert s.compute(default_ctx)[0].amount == 2000
        s.on_result(is_win=0, pnl=-2000)
        # level=2  amount=4000
        assert s.compute(default_ctx)[0].amount == 4000

    def test_multiple_key_codes(self, martin_cls, default_ctx):
        """ key_codes """
        s = martin_cls(key_codes=["DX1", "DS3"], base_amount=500, sequence=[1, 3])
        result = s.compute(default_ctx)
        assert len(result) == 2
        assert all(bi.amount == 500 for bi in result)  # 500  1

    def test_amount_is_integer(self, martin_cls, default_ctx):
        """"""
        s = martin_cls(key_codes=["DX1"], base_amount=1000, sequence=[1.5, 2.5])
        result = s.compute(default_ctx)
        assert isinstance(result[0].amount, int)
        assert result[0].amount == 1500  # int(1000  1.5)

    def test_amount_with_float_sequence(self, martin_cls, default_ctx):
        """"""
        s = martin_cls(key_codes=["DX1"], base_amount=100, sequence=[1, 2.5, 6.25])
        assert s.compute(default_ctx)[0].amount == 100
        s.on_result(is_win=0, pnl=-100)
        assert s.compute(default_ctx)[0].amount == 250
        s.on_result(is_win=0, pnl=-250)
        assert s.compute(default_ctx)[0].amount == 625


# ---------------------------------------------------------------------------
# on_result()  
# ---------------------------------------------------------------------------

class TestMartinOnResult:

    def test_win_resets_to_0(self, martin_cls):
        """win  level = 0"""
        s = martin_cls(key_codes=["DX1"], base_amount=1000, sequence=[1, 2, 4])
        s.on_result(is_win=0, pnl=-1000)  # level  1
        s.on_result(is_win=0, pnl=-2000)  # level  2
        assert s.level == 2
        s.on_result(is_win=1, pnl=3000)   # win  level = 0
        assert s.level == 0

    def test_lose_advances_level(self, martin_cls):
        """lose  level + 1"""
        s = martin_cls(key_codes=["DX1"], base_amount=1000, sequence=[1, 2, 4, 8])
        assert s.level == 0
        s.on_result(is_win=0, pnl=-1000)
        assert s.level == 1
        s.on_result(is_win=0, pnl=-2000)
        assert s.level == 2
        s.on_result(is_win=0, pnl=-4000)
        assert s.level == 3

    def test_refund_unchanged(self, martin_cls):
        """refund  level """
        s = martin_cls(key_codes=["DX1"], base_amount=1000, sequence=[1, 2, 4])
        s.on_result(is_win=0, pnl=-1000)  # level  1
        assert s.level == 1
        s.on_result(is_win=-1, pnl=0)     # refund  
        assert s.level == 1

    def test_none_unchanged(self, martin_cls):
        """is_win=None  level """
        s = martin_cls(key_codes=["DX1"], base_amount=1000, sequence=[1, 2, 4])
        s.on_result(is_win=0, pnl=-1000)  # level  1
        s.on_result(is_win=None, pnl=0)   # None  
        assert s.level == 1

    def test_win_at_level_0_stays_0(self, martin_cls):
        """level=0  win  0"""
        s = martin_cls(key_codes=["DX1"], base_amount=1000, sequence=[1, 2, 4])
        assert s.level == 0
        s.on_result(is_win=1, pnl=950)
        assert s.level == 0


# ---------------------------------------------------------------------------
#  +  + 
# ---------------------------------------------------------------------------

class TestMartinSequenceExhaustion:

    def test_sequence_exhaustion_resets_level(self, martin_cls):
        """ level  0"""
        s = martin_cls(key_codes=["DX1"], base_amount=1000, sequence=[1, 2, 4])
        s.on_result(is_win=0, pnl=-1000)  # level  1
        s.on_result(is_win=0, pnl=-2000)  # level  2
        s.on_result(is_win=0, pnl=-4000)  # level    0
        assert s.level == 0

    def test_sequence_exhaustion_logs_warning(self, martin_cls, caplog):
        """ WARNING """
        s = martin_cls(
            key_codes=["DX1"], base_amount=1000, sequence=[1, 2],
            strategy_name="",
        )
        with caplog.at_level(logging.WARNING):
            s.on_result(is_win=0, pnl=-1000)  # level  1
            s.on_result(is_win=0, pnl=-2000)  #   
        assert "" in caplog.text
        assert "" in caplog.text

    def test_sequence_exhaustion_creates_pending_alert(self, martin_cls):
        """ pending alert"""
        s = martin_cls(
            key_codes=["DX1"], base_amount=1000, sequence=[1, 2],
            strategy_name="",
        )
        s.on_result(is_win=0, pnl=-1000)  # level  1
        assert len(s.pending_alerts) == 0
        s.on_result(is_win=0, pnl=-2000)  #   
        assert len(s.pending_alerts) == 1
        alert = s.pending_alerts[0]
        assert alert.alert_type == "martin_reset"
        assert "" in alert.title
        assert "3000 " in alert.detail  # 1000 + 2000

    def test_sequence_exhaustion_resets_round_loss(self, martin_cls):
        """ round_loss  0"""
        s = martin_cls(key_codes=["DX1"], base_amount=1000, sequence=[1, 2])
        s.on_result(is_win=0, pnl=-1000)
        assert s.round_loss == 1000
        s.on_result(is_win=0, pnl=-2000)  # 
        assert s.round_loss == 0

    def test_multiple_exhaustions(self, martin_cls):
        """"""
        s = martin_cls(key_codes=["DX1"], base_amount=100, sequence=[1, 2])
        # 
        s.on_result(is_win=0, pnl=-100)
        s.on_result(is_win=0, pnl=-200)  # 
        assert s.level == 0
        assert len(s.pending_alerts) == 1
        # 
        s.on_result(is_win=0, pnl=-100)
        s.on_result(is_win=0, pnl=-200)  # 
        assert s.level == 0
        assert len(s.pending_alerts) == 2


# ---------------------------------------------------------------------------
# level  [0, len(sequence)-1]
# ---------------------------------------------------------------------------

class TestMartinLevelBounds:

    def test_level_never_exceeds_max(self, martin_cls):
        """ lose  level  len(sequence)-1"""
        seq = [1, 2, 4, 8, 16]
        s = martin_cls(key_codes=["DX1"], base_amount=100, sequence=seq)
        for i in range(20):  # 
            assert 0 <= s.level < len(seq)
            s.on_result(is_win=0, pnl=-100)
        assert 0 <= s.level < len(seq)

    def test_level_always_valid_mixed_results(self, martin_cls):
        """ win/lose/refund  level """
        seq = [1, 2, 4]
        s = martin_cls(key_codes=["DX1"], base_amount=100, sequence=seq)
        results = [0, 0, 1, 0, -1, 0, 0, 0, 1, 0, 0, 0, 0, 1]
        for r in results:
            s.on_result(is_win=r, pnl=-100 if r == 0 else (100 if r == 1 else 0))
            assert 0 <= s.level < len(seq), f"level={s.level} out of bounds"


# ---------------------------------------------------------------------------
# round_loss 
# ---------------------------------------------------------------------------

class TestMartinRoundLoss:

    def test_round_loss_accumulates(self, martin_cls):
        """ lose  round_loss """
        s = martin_cls(key_codes=["DX1"], base_amount=1000, sequence=[1, 2, 4, 8])
        s.on_result(is_win=0, pnl=-1000)
        assert s.round_loss == 1000
        s.on_result(is_win=0, pnl=-2000)
        assert s.round_loss == 3000
        s.on_result(is_win=0, pnl=-4000)
        assert s.round_loss == 7000

    def test_win_resets_round_loss(self, martin_cls):
        """win  round_loss  0"""
        s = martin_cls(key_codes=["DX1"], base_amount=1000, sequence=[1, 2, 4])
        s.on_result(is_win=0, pnl=-1000)
        s.on_result(is_win=0, pnl=-2000)
        assert s.round_loss == 3000
        s.on_result(is_win=1, pnl=5000)
        assert s.round_loss == 0

    def test_refund_does_not_affect_round_loss(self, martin_cls):
        """refund  round_loss"""
        s = martin_cls(key_codes=["DX1"], base_amount=1000, sequence=[1, 2, 4])
        s.on_result(is_win=0, pnl=-1000)
        assert s.round_loss == 1000
        s.on_result(is_win=-1, pnl=0)
        assert s.round_loss == 1000


# ---------------------------------------------------------------------------
# flush_alerts  
# ---------------------------------------------------------------------------

class TestMartinFlushAlerts:

    @pytest.mark.asyncio
    async def test_flush_sends_alerts(self, martin_cls, mock_alert_service):
        """flush_alerts  AlertService.send"""
        s = martin_cls(
            key_codes=["DX1"], base_amount=1000, sequence=[1, 2],
            alert_service=mock_alert_service,
            operator_id=42,
            strategy_name="",
        )
        s.on_result(is_win=0, pnl=-1000)
        s.on_result(is_win=0, pnl=-2000)  #   pending alert
        assert len(s.pending_alerts) == 1

        await s.flush_alerts()

        mock_alert_service.send.assert_called_once()
        call_kwargs = mock_alert_service.send.call_args
        assert call_kwargs.kwargs["operator_id"] == 42
        assert call_kwargs.kwargs["alert_type"] == "martin_reset"
        assert "" in call_kwargs.kwargs["title"]
        # 
        assert len(s.pending_alerts) == 0

    @pytest.mark.asyncio
    async def test_flush_without_service_clears_queue(self, martin_cls):
        """ alert_service  flush """
        s = martin_cls(key_codes=["DX1"], base_amount=1000, sequence=[1])
        s.on_result(is_win=0, pnl=-1000)  # 
        assert len(s.pending_alerts) == 1
        await s.flush_alerts()
        assert len(s.pending_alerts) == 0

    @pytest.mark.asyncio
    async def test_flush_multiple_alerts(self, martin_cls, mock_alert_service):
        """flush """
        s = martin_cls(
            key_codes=["DX1"], base_amount=100, sequence=[1],
            alert_service=mock_alert_service,
            operator_id=1,
        )
        #  1 lose 
        s.on_result(is_win=0, pnl=-100)
        s.on_result(is_win=0, pnl=-100)
        s.on_result(is_win=0, pnl=-100)
        assert len(s.pending_alerts) == 3

        await s.flush_alerts()
        assert mock_alert_service.send.call_count == 3
        assert len(s.pending_alerts) == 0


# ---------------------------------------------------------------------------
#  win/lose 
# ---------------------------------------------------------------------------

class TestMartinMultiRound:

    def test_win_lose_cycle(self, martin_cls, default_ctx):
        """ win/lose """
        seq = [1, 2, 4, 8, 16]
        s = martin_cls(key_codes=["DX1"], base_amount=1000, sequence=seq)

        # level=0, amount=1000, lose
        assert s.compute(default_ctx)[0].amount == 1000
        s.on_result(is_win=0, pnl=-1000)
        assert s.level == 1

        # level=1, amount=2000, lose
        assert s.compute(default_ctx)[0].amount == 2000
        s.on_result(is_win=0, pnl=-2000)
        assert s.level == 2

        # level=2, amount=4000, win  
        assert s.compute(default_ctx)[0].amount == 4000
        s.on_result(is_win=1, pnl=4000)
        assert s.level == 0

        # level=0, amount=1000
        assert s.compute(default_ctx)[0].amount == 1000

    def test_refund_in_middle_of_sequence(self, martin_cls, default_ctx):
        """level """
        s = martin_cls(key_codes=["DX1"], base_amount=1000, sequence=[1, 2, 4])
        s.on_result(is_win=0, pnl=-1000)  # level  1
        assert s.level == 1
        s.on_result(is_win=-1, pnl=0)     # refund  
        assert s.level == 1
        assert s.compute(default_ctx)[0].amount == 2000  #  level=1
        s.on_result(is_win=0, pnl=-2000)  # level  2
        assert s.level == 2
        assert s.compute(default_ctx)[0].amount == 4000


# ---------------------------------------------------------------------------
# 
# ---------------------------------------------------------------------------

class TestMartinEdgeCases:

    def test_sequence_length_1(self, martin_cls, default_ctx):
        """ 1 lose """
        s = martin_cls(key_codes=["DX1"], base_amount=1000, sequence=[1])
        assert s.compute(default_ctx)[0].amount == 1000
        s.on_result(is_win=0, pnl=-1000)  #   
        assert s.level == 0
        assert len(s.pending_alerts) == 1
        assert s.compute(default_ctx)[0].amount == 1000

    def test_very_long_sequence(self, martin_cls):
        """"""
        seq = list(range(1, 101))  # [1, 2, 3, ..., 100]
        s = martin_cls(key_codes=["DX1"], base_amount=100, sequence=seq)
        for i in range(99):
            assert s.level == i
            s.on_result(is_win=0, pnl=-100 * seq[i])
        assert s.level == 99
        #  lose  
        s.on_result(is_win=0, pnl=-100 * 100)
        assert s.level == 0
        assert len(s.pending_alerts) == 1

    def test_win_immediately_after_start(self, martin_cls, default_ctx):
        """ winlevel  0"""
        s = martin_cls(key_codes=["DX1"], base_amount=1000, sequence=[1, 2, 4])
        s.on_result(is_win=1, pnl=950)
        assert s.level == 0
        assert s.round_loss == 0
        assert s.compute(default_ctx)[0].amount == 1000


# ---------------------------------------------------------------------------
# PBT: hypothesis
# ---------------------------------------------------------------------------

from hypothesis import given, settings, assume
from hypothesis import strategies as st


class TestPBT_P7_MartinStateTransition:
    """P7:   win0, lose(level+1)%len, refund, level[0,len-1]

    **Validates: Requirements 4.2**
    """

    @given(
        seq_len=st.integers(min_value=1, max_value=10),
        base_amount=st.integers(min_value=1, max_value=100_000),
        initial_loses=st.integers(min_value=0, max_value=9),
        results=st.lists(
            st.sampled_from(["win", "lose", "refund"]),
            min_size=1,
            max_size=50,
        ),
    )
    @settings(max_examples=100)
    def test_pbt_martin_state_transitions(
        self, seq_len, base_amount, initial_loses, results
    ):
        """

        **Validates: Requirements 4.2**
        """
        from app.engine.strategies.martin import MartinStrategyImpl

        sequence = list(range(1, seq_len + 1))  # [1, 2, ..., seq_len]
        s = MartinStrategyImpl(key_codes=["DX1"], base_amount=base_amount, sequence=sequence)

        #  initial_loses  seq_len-1
        for _ in range(initial_loses % seq_len):
            s.on_result(is_win=0, pnl=-base_amount)

        #  level 
        assert 0 <= s.level < seq_len

        for result in results:
            old_level = s.level

            if result == "win":
                s.on_result(is_win=1, pnl=base_amount)
                # win  level  0
                assert s.level == 0, f"win  level  0 {s.level}"
            elif result == "lose":
                s.on_result(is_win=0, pnl=-base_amount)
                if old_level + 1 >= seq_len:
                    #    0
                    assert s.level == 0, (
                        f" level  0 {s.level}"
                    )
                else:
                    # level + 1
                    assert s.level == old_level + 1, (
                        f"lose  level  {old_level + 1} {s.level}"
                    )
            elif result == "refund":
                s.on_result(is_win=-1, pnl=0)
                # refund  level 
                assert s.level == old_level, (
                    f"refund  level {old_level} {s.level}"
                )

            # level  [0, seq_len-1]
            assert 0 <= s.level < seq_len, (
                f"level={s.level}  [0, {seq_len - 1}]"
            )


class TestPBT_P8_MartinBetAmount:
    """P8:   amount=basesequence[level], amount>0

    **Validates: Requirements 4.2**
    """

    @given(
        base_amount=st.integers(min_value=1, max_value=100_000),
        sequence=st.lists(
            st.integers(min_value=1, max_value=100),
            min_size=1,
            max_size=10,
        ),
    )
    @settings(max_examples=100)
    def test_pbt_martin_bet_amount(self, base_amount, sequence):
        """ base_amount>0  compute  amount = base * sequence[level]  amount > 0

        **Validates: Requirements 4.2**
        """
        from app.engine.strategies.martin import MartinStrategyImpl

        ctx = StrategyContext(
            current_issue="20250101001",
            history=[LotteryResult(issue="20250101000", balls=[3, 5, 7], sum_value=15)],
            balance=1_000_00,
        )
        s = MartinStrategyImpl(key_codes=["DX1"], base_amount=base_amount, sequence=sequence)

        for level_idx in range(len(sequence)):
            #  level 
            instructions = s.compute(ctx)
            assert len(instructions) == 1
            expected_amount = int(base_amount * sequence[level_idx])
            assert instructions[0].amount == expected_amount, (
                f"level={level_idx}: expected {expected_amount}, got {instructions[0].amount}"
            )
            assert instructions[0].amount > 0, (
                f"level={level_idx}: amount  > 0 {instructions[0].amount}"
            )

            # lose
            if level_idx < len(sequence) - 1:
                s.on_result(is_win=0, pnl=-instructions[0].amount)
            #  lose
