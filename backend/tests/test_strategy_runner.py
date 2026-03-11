"""StrategyRunner


- BetSignal 
- StrategyRunner start/pause/stop 
- collect_signalsrunning paused/stopped/error  []
- idempotent_id {issue}-{strategy_id}-{key_code}
- key_code 
- martin_level 
- simulation 
- compute   status=error
- on_result  + flush_alerts 
-  idempotent_id
"""

import logging
import re
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.engine.strategies.base import (
    BaseStrategy,
    BetInstruction,
    StrategyContext,
    LotteryResult,
)
from app.engine.strategies.registry import _clear_registry
from app.engine.strategy_runner import BetSignal, StrategyRunner

# idempotent_id 
IDEMPOTENT_RE = re.compile(r"^\d+-\d+-[A-Z0-9_]+$")


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
def default_ctx() -> StrategyContext:
    return StrategyContext(
        current_issue="20250101001",
        history=[LotteryResult(issue="20250101000", balls=[3, 5, 7], sum_value=15)],
        balance=100_000,
    )


class StubStrategy(BaseStrategy):
    """"""

    def __init__(self, key_code: str = "DX1", amount: int = 1000):
        self._key_code = key_code
        self._amount = amount

    def name(self) -> str:
        return "stub"

    def compute(self, ctx: StrategyContext) -> list[BetInstruction]:
        return [BetInstruction(key_code=self._key_code, amount=self._amount)]

    def on_result(self, is_win: Optional[int], pnl: int) -> None:
        pass


class ErrorStrategy(BaseStrategy):
    """compute """

    def name(self) -> str:
        return "error"

    def compute(self, ctx: StrategyContext) -> list[BetInstruction]:
        raise RuntimeError("")

    def on_result(self, is_win: Optional[int], pnl: int) -> None:
        pass


class MultiKeyStrategy(BaseStrategy):
    """ key_code """

    def __init__(self, key_codes: list[str], amount: int = 500):
        self._key_codes = key_codes
        self._amount = amount

    def name(self) -> str:
        return "multi"

    def compute(self, ctx: StrategyContext) -> list[BetInstruction]:
        return [BetInstruction(key_code=kc, amount=self._amount) for kc in self._key_codes]

    def on_result(self, is_win: Optional[int], pnl: int) -> None:
        pass


# ---------------------------------------------------------------------------
# BetSignal 
# ---------------------------------------------------------------------------

class TestBetSignal:

    def test_fields(self):
        sig = BetSignal(
            strategy_id=1,
            key_code="DX1",
            amount=1000,
            idempotent_id="12345-1-DX1",
            martin_level=2,
            simulation=True,
        )
        assert sig.strategy_id == 1
        assert sig.key_code == "DX1"
        assert sig.amount == 1000
        assert sig.idempotent_id == "12345-1-DX1"
        assert sig.martin_level == 2
        assert sig.simulation is True

    def test_defaults(self):
        sig = BetSignal(
            strategy_id=1, key_code="DX1", amount=1000, idempotent_id="1-1-DX1"
        )
        assert sig.martin_level == 0
        assert sig.simulation is False


# ---------------------------------------------------------------------------
# 
# ---------------------------------------------------------------------------

class TestStrategyRunnerStatus:

    def test_initial_status_is_stopped(self):
        runner = StrategyRunner(strategy_id=1, strategy=StubStrategy())
        assert runner.status == "stopped"

    def test_start_from_stopped(self):
        runner = StrategyRunner(strategy_id=1, strategy=StubStrategy())
        runner.start()
        assert runner.status == "running"

    def test_pause_from_running(self):
        runner = StrategyRunner(strategy_id=1, strategy=StubStrategy())
        runner.start()
        runner.pause()
        assert runner.status == "paused"

    def test_stop_from_running(self):
        runner = StrategyRunner(strategy_id=1, strategy=StubStrategy())
        runner.start()
        runner.stop()
        assert runner.status == "stopped"

    def test_stop_from_paused(self):
        runner = StrategyRunner(strategy_id=1, strategy=StubStrategy())
        runner.start()
        runner.pause()
        runner.stop()
        assert runner.status == "stopped"

    def test_stop_from_error(self):
        runner = StrategyRunner(strategy_id=1, strategy=StubStrategy())
        runner.status = "error"
        runner.stop()
        assert runner.status == "stopped"

    def test_start_from_paused(self):
        runner = StrategyRunner(strategy_id=1, strategy=StubStrategy())
        runner.start()
        runner.pause()
        runner.start()
        assert runner.status == "running"

    def test_start_from_error(self):
        runner = StrategyRunner(strategy_id=1, strategy=StubStrategy())
        runner.status = "error"
        runner.start()
        assert runner.status == "running"

    def test_start_from_running_raises(self):
        runner = StrategyRunner(strategy_id=1, strategy=StubStrategy())
        runner.start()
        with pytest.raises(ValueError, match=" running "):
            runner.start()

    def test_pause_from_stopped_raises(self):
        runner = StrategyRunner(strategy_id=1, strategy=StubStrategy())
        with pytest.raises(ValueError, match=" stopped "):
            runner.pause()

    def test_pause_from_paused_raises(self):
        runner = StrategyRunner(strategy_id=1, strategy=StubStrategy())
        runner.start()
        runner.pause()
        with pytest.raises(ValueError, match=" paused "):
            runner.pause()

    def test_stop_is_always_allowed(self):
        """stop """
        for initial in ("stopped", "running", "paused", "error"):
            runner = StrategyRunner(strategy_id=1, strategy=StubStrategy())
            runner.status = initial
            runner.stop()
            assert runner.status == "stopped"


# ---------------------------------------------------------------------------
# collect_signals 
# ---------------------------------------------------------------------------

class TestCollectSignals:

    def test_running_produces_signals(self, default_ctx):
        runner = StrategyRunner(strategy_id=1, strategy=StubStrategy())
        runner.start()
        signals = runner.collect_signals(default_ctx, "12345")
        assert len(signals) == 1
        assert isinstance(signals[0], BetSignal)

    def test_stopped_returns_empty(self, default_ctx):
        runner = StrategyRunner(strategy_id=1, strategy=StubStrategy())
        assert runner.collect_signals(default_ctx, "12345") == []

    def test_paused_returns_empty(self, default_ctx):
        runner = StrategyRunner(strategy_id=1, strategy=StubStrategy())
        runner.start()
        runner.pause()
        assert runner.collect_signals(default_ctx, "12345") == []

    def test_error_returns_empty(self, default_ctx):
        runner = StrategyRunner(strategy_id=1, strategy=StubStrategy())
        runner.status = "error"
        assert runner.collect_signals(default_ctx, "12345") == []

    def test_multiple_instructions(self, default_ctx):
        strategy = MultiKeyStrategy(key_codes=["DX1", "DS3", "ZH7"], amount=500)
        runner = StrategyRunner(strategy_id=2, strategy=strategy)
        runner.start()
        signals = runner.collect_signals(default_ctx, "99999")
        assert len(signals) == 3
        assert [s.key_code for s in signals] == ["DX1", "DS3", "ZH7"]
        assert all(s.amount == 500 for s in signals)

    def test_compute_exception_sets_error(self, default_ctx, caplog):
        runner = StrategyRunner(strategy_id=1, strategy=ErrorStrategy())
        runner.start()
        assert runner.status == "running"

        with caplog.at_level(logging.ERROR):
            signals = runner.collect_signals(default_ctx, "12345")

        assert signals == []
        assert runner.status == "error"
        assert "" in caplog.text


# ---------------------------------------------------------------------------
# idempotent_id 
# ---------------------------------------------------------------------------

class TestIdempotentId:

    def test_format_matches_regex(self, default_ctx):
        runner = StrategyRunner(strategy_id=42, strategy=StubStrategy(key_code="DX1"))
        runner.start()
        signals = runner.collect_signals(default_ctx, "12345")
        assert len(signals) == 1
        assert IDEMPOTENT_RE.match(signals[0].idempotent_id)

    def test_format_components(self, default_ctx):
        runner = StrategyRunner(strategy_id=7, strategy=StubStrategy(key_code="DS3"))
        runner.start()
        signals = runner.collect_signals(default_ctx, "99001")
        assert signals[0].idempotent_id == "99001-7-DS3"

    def test_key_code_uppercased_in_id(self, default_ctx):
        """ key_code  idempotent_id """
        runner = StrategyRunner(strategy_id=1, strategy=StubStrategy(key_code="dx1"))
        runner.start()
        signals = runner.collect_signals(default_ctx, "10000")
        assert signals[0].idempotent_id == "10000-1-DX1"
        assert signals[0].key_code == "DX1"

    def test_mixed_case_key_code(self, default_ctx):
        runner = StrategyRunner(strategy_id=3, strategy=StubStrategy(key_code="Zh7"))
        runner.start()
        signals = runner.collect_signals(default_ctx, "55555")
        assert signals[0].idempotent_id == "55555-3-ZH7"
        assert IDEMPOTENT_RE.match(signals[0].idempotent_id)

    def test_key_code_with_underscore(self, default_ctx):
        """ key_code LHH_L"""
        runner = StrategyRunner(strategy_id=5, strategy=StubStrategy(key_code="LHH_L"))
        runner.start()
        signals = runner.collect_signals(default_ctx, "88888")
        assert signals[0].idempotent_id == "88888-5-LHH_L"
        assert IDEMPOTENT_RE.match(signals[0].idempotent_id)

    @pytest.mark.parametrize(
        "issue,strategy_id,key_code",
        [
            ("10001", 1, "DX1"),
            ("10001", 1, "DX2"),
            ("10001", 2, "DX1"),
            ("10002", 1, "DX1"),
            ("99999", 100, "ZH10"),
            ("12345", 7, "B1QH5"),
        ],
    )
    def test_format_always_matches_regex(self, default_ctx, issue, strategy_id, key_code):
        runner = StrategyRunner(
            strategy_id=strategy_id, strategy=StubStrategy(key_code=key_code)
        )
        runner.start()
        signals = runner.collect_signals(default_ctx, issue)
        assert len(signals) == 1
        assert IDEMPOTENT_RE.match(signals[0].idempotent_id)

    @pytest.mark.parametrize(
        "triple_a,triple_b",
        [
            (("10001", 1, "DX1"), ("10001", 1, "DX2")),
            (("10001", 1, "DX1"), ("10001", 2, "DX1")),
            (("10001", 1, "DX1"), ("10002", 1, "DX1")),
            (("10001", 1, "DX1"), ("10002", 2, "DS3")),
            (("99999", 50, "ZH7"), ("99999", 50, "ZH8")),
        ],
    )
    def test_different_triples_produce_different_ids(self, default_ctx, triple_a, triple_b):
        """ (issue, strategy_id, key_code)  idempotent_id"""
        issue_a, sid_a, kc_a = triple_a
        issue_b, sid_b, kc_b = triple_b

        runner_a = StrategyRunner(strategy_id=sid_a, strategy=StubStrategy(key_code=kc_a))
        runner_a.start()
        sig_a = runner_a.collect_signals(default_ctx, issue_a)

        runner_b = StrategyRunner(strategy_id=sid_b, strategy=StubStrategy(key_code=kc_b))
        runner_b.start()
        sig_b = runner_b.collect_signals(default_ctx, issue_b)

        assert sig_a[0].idempotent_id != sig_b[0].idempotent_id


# ---------------------------------------------------------------------------
# martin_level 
# ---------------------------------------------------------------------------

class TestMartinLevel:

    def test_reads_level_from_martin_strategy(self, default_ctx):
        """ MartinStrategyImpl  level """
        from app.engine.strategies.martin import MartinStrategyImpl

        martin = MartinStrategyImpl(
            key_codes=["DX1"], base_amount=1000, sequence=[1, 2, 4, 8]
        )
        runner = StrategyRunner(strategy_id=1, strategy=martin)
        runner.start()

        # level=0
        signals = runner.collect_signals(default_ctx, "10001")
        assert signals[0].martin_level == 0

        # lose  level=1
        martin.on_result(is_win=0, pnl=-1000)
        signals = runner.collect_signals(default_ctx, "10002")
        assert signals[0].martin_level == 1

        # lose  level=2
        martin.on_result(is_win=0, pnl=-2000)
        signals = runner.collect_signals(default_ctx, "10003")
        assert signals[0].martin_level == 2

    def test_default_level_0_for_flat(self, default_ctx):
        """FlatStrategy  level  martin_level=0"""
        from app.engine.strategies.flat import FlatStrategyImpl

        flat = FlatStrategyImpl(key_codes=["DX1"], base_amount=1000)
        runner = StrategyRunner(strategy_id=1, strategy=flat)
        runner.start()
        signals = runner.collect_signals(default_ctx, "10001")
        assert signals[0].martin_level == 0


# ---------------------------------------------------------------------------
# simulation 
# ---------------------------------------------------------------------------

class TestSimulation:

    def test_simulation_false_by_default(self, default_ctx):
        runner = StrategyRunner(strategy_id=1, strategy=StubStrategy())
        runner.start()
        signals = runner.collect_signals(default_ctx, "10001")
        assert signals[0].simulation is False

    def test_simulation_true_propagated(self, default_ctx):
        runner = StrategyRunner(strategy_id=1, strategy=StubStrategy(), simulation=True)
        runner.start()
        signals = runner.collect_signals(default_ctx, "10001")
        assert signals[0].simulation is True

    def test_simulation_consistent_across_signals(self, default_ctx):
        strategy = MultiKeyStrategy(key_codes=["DX1", "DS3"], amount=500)
        runner = StrategyRunner(strategy_id=1, strategy=strategy, simulation=True)
        runner.start()
        signals = runner.collect_signals(default_ctx, "10001")
        assert all(s.simulation is True for s in signals)


# ---------------------------------------------------------------------------
# on_result 
# ---------------------------------------------------------------------------

class TestOnResult:

    @pytest.mark.asyncio
    async def test_delegates_to_strategy(self):
        """on_result """
        strategy = MagicMock(spec=BaseStrategy)
        strategy.on_result = MagicMock()
        runner = StrategyRunner(strategy_id=1, strategy=strategy)
        await runner.on_result(is_win=1, pnl=500)
        strategy.on_result.assert_called_once_with(1, 500)

    @pytest.mark.asyncio
    async def test_calls_flush_alerts_if_available(self):
        """ flush_alertson_result """
        strategy = MagicMock(spec=BaseStrategy)
        strategy.on_result = MagicMock()
        strategy.flush_alerts = AsyncMock()
        runner = StrategyRunner(strategy_id=1, strategy=strategy)
        await runner.on_result(is_win=0, pnl=-1000)
        strategy.flush_alerts.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_flush_alerts_for_flat(self):
        """FlatStrategy  flush_alerts"""
        strategy = StubStrategy()
        runner = StrategyRunner(strategy_id=1, strategy=strategy)
        # 
        await runner.on_result(is_win=1, pnl=500)

    @pytest.mark.asyncio
    async def test_martin_on_result_integration(self, default_ctx):
        """martin  on_result  level """
        from app.engine.strategies.martin import MartinStrategyImpl

        martin = MartinStrategyImpl(
            key_codes=["DX1"], base_amount=1000, sequence=[1, 2, 4]
        )
        runner = StrategyRunner(strategy_id=1, strategy=martin)
        runner.start()

        # level=0
        signals = runner.collect_signals(default_ctx, "10001")
        assert signals[0].amount == 1000
        assert signals[0].martin_level == 0

        # lose  level=1
        await runner.on_result(is_win=0, pnl=-1000)
        signals = runner.collect_signals(default_ctx, "10002")
        assert signals[0].amount == 2000
        assert signals[0].martin_level == 1


# ---------------------------------------------------------------------------
# PBT: hypothesis
# ---------------------------------------------------------------------------

from hypothesis import given, settings, assume
from hypothesis import strategies as st


class TestPBT_P9_IdempotentIdUniqueness:
    """P9:  ID   ID,  {issue}-{strategy_id}-{key_code}

    **Validates: Requirements 4.4, 5.2**
    """

    @given(
        issue_a=st.from_regex(r"[0-9]{5,15}", fullmatch=True),
        strategy_id_a=st.integers(min_value=1, max_value=10000),
        key_code_a=st.sampled_from([
            "DX1", "DX2", "DS3", "DS4", "ZH7", "ZH8", "ZH9", "ZH10",
            "JDX5", "JDX6", "HZ1", "HZ14", "HZ28", "BZ4",
            "B1QH0", "B1QH9", "LHH_L", "LHH_H", "LHH_HE",
        ]),
        issue_b=st.from_regex(r"[0-9]{5,15}", fullmatch=True),
        strategy_id_b=st.integers(min_value=1, max_value=10000),
        key_code_b=st.sampled_from([
            "DX1", "DX2", "DS3", "DS4", "ZH7", "ZH8", "ZH9", "ZH10",
            "JDX5", "JDX6", "HZ1", "HZ14", "HZ28", "BZ4",
            "B1QH0", "B1QH9", "LHH_L", "LHH_H", "LHH_HE",
        ]),
    )
    @settings(max_examples=100)
    def test_pbt_different_triples_different_ids(
        self,
        issue_a, strategy_id_a, key_code_a,
        issue_b, strategy_id_b, key_code_b,
    ):
        """ (issue, strategy_id, key_code)  idempotent_id

        **Validates: Requirements 4.4, 5.2**
        """
        # 
        assume(
            (issue_a, strategy_id_a, key_code_a)
            != (issue_b, strategy_id_b, key_code_b)
        )

        ctx = StrategyContext(
            current_issue="20250101001",
            history=[LotteryResult(issue="20250101000", balls=[3, 5, 7], sum_value=15)],
            balance=100_000,
        )

        runner_a = StrategyRunner(
            strategy_id=strategy_id_a,
            strategy=StubStrategy(key_code=key_code_a),
        )
        runner_a.start()
        signals_a = runner_a.collect_signals(ctx, issue_a)

        runner_b = StrategyRunner(
            strategy_id=strategy_id_b,
            strategy=StubStrategy(key_code=key_code_b),
        )
        runner_b.start()
        signals_b = runner_b.collect_signals(ctx, issue_b)

        assert len(signals_a) == 1
        assert len(signals_b) == 1

        id_a = signals_a[0].idempotent_id
        id_b = signals_b[0].idempotent_id

        #    ID
        assert id_a != id_b, (
            f" ID: "
            f"({issue_a},{strategy_id_a},{key_code_a})  {id_a}, "
            f"({issue_b},{strategy_id_b},{key_code_b})  {id_b}"
        )

        # 
        assert IDEMPOTENT_RE.match(id_a), f"ID : {id_a}"
        assert IDEMPOTENT_RE.match(id_b), f"ID : {id_b}"

        # {issue}-{strategy_id}-{key_code}
        expected_a = f"{issue_a}-{strategy_id_a}-{key_code_a.upper()}"
        expected_b = f"{issue_b}-{strategy_id_b}-{key_code_b.upper()}"
        assert id_a == expected_a, f"Expected {expected_a}, got {id_a}"
        assert id_b == expected_b, f"Expected {expected_b}, got {id_b}"
