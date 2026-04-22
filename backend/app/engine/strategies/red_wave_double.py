"""Wave-trigger + directional Martin strategies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Optional

from app.engine.strategies.base import (
    BaseStrategy,
    BetInstruction,
    StrategyContext,
    StrategyStopRequest,
)
from app.engine.strategies.registry import register_strategy
from app.utils.strategy_timing import (
    GREEN_WAVE_SINGLE_ALLOWED_CODES,
    RED_WAVE_DOUBLE_ALLOWED_CODES,
)

_RED_BALL_VALUES = frozenset({3, 6, 9})
_RED_SUM_VALUES = frozenset({3, 6, 9, 12, 15, 18, 21, 24})
_GREEN_BALL_VALUES = frozenset({1, 4, 7})
_GREEN_SUM_VALUES = frozenset({1, 4, 7, 10, 16, 19, 22, 25})
_BALL_INDEX = {
    "B1LM_D": 0,
    "B1LM_S": 0,
    "B2LM_D": 1,
    "B2LM_S": 1,
    "B3LM_D": 2,
    "B3LM_S": 2,
}


@dataclass
class DirectionState:
    active: bool = False
    level: int = 0


def normalize_direction_codes(
    codes: list[str] | None,
    *,
    allowed_codes: tuple[str, ...],
    default_codes: tuple[str, ...],
) -> list[str]:
    if codes is None:
        return list(default_codes)
    if len(codes) == 0:
        raise ValueError("direction_codes is required")

    normalized = []
    for code in codes:
        c = code.strip().upper()
        if not c:
            continue
        if c not in allowed_codes:
            raise ValueError(f"invalid direction code: {code}")
        if c not in normalized:
            normalized.append(c)

    if not normalized:
        raise ValueError("direction_codes is required")

    ordered = [code for code in allowed_codes if code in normalized]
    return ordered


class _WaveDirectionMartinStrategy(BaseStrategy):
    """Directional wave trigger + directional chase."""

    STRATEGY_NAME: ClassVar[str]
    ALLOWED_DIRECTION_CODES: ClassVar[tuple[str, ...]]
    DEFAULT_DIRECTION_CODES: ClassVar[tuple[str, ...]]
    BALL_TRIGGER_VALUES: ClassVar[frozenset[int]]
    SUM_TRIGGER_VALUES: ClassVar[frozenset[int]]

    def __init__(
        self,
        base_amount: int,
        sequence: list[int | float],
        *,
        direction_codes: list[str] | None = None,
    ) -> None:
        if base_amount <= 0:
            raise ValueError("base_amount must be > 0")
        if not sequence:
            raise ValueError("sequence is required")
        if any(v <= 0 for v in sequence):
            raise ValueError("sequence values must be > 0")

        self._base_amount = base_amount
        self._sequence = [float(v) for v in sequence]
        self._direction_codes = normalize_direction_codes(
            direction_codes,
            allowed_codes=self.ALLOWED_DIRECTION_CODES,
            default_codes=self.DEFAULT_DIRECTION_CODES,
        )
        self._states = {
            code: DirectionState(active=False, level=0)
            for code in self._direction_codes
        }

    def name(self) -> str:
        return self.STRATEGY_NAME

    @property
    def level(self) -> int:
        primary = self._direction_codes[0]
        return self._states[primary].level

    @property
    def active(self) -> bool:
        return any(state.active for state in self._states.values())

    @property
    def states(self) -> dict[str, DirectionState]:
        return self._states

    @property
    def direction_codes(self) -> list[str]:
        return list(self._direction_codes)

    def level_for(self, key_code: str) -> int:
        code = key_code.strip().upper()
        state = self._states.get(code)
        if state is None:
            raise ValueError(f"unknown direction code: {key_code}")
        return state.level

    def compute(self, ctx: StrategyContext) -> list[BetInstruction]:
        latest = ctx.history[0] if ctx.history else None
        instructions: list[BetInstruction] = []

        for code in self._direction_codes:
            state = self._states[code]
            if not state.active:
                if latest is None:
                    continue
                if not self._is_target_wave(code, latest.balls, latest.sum_value):
                    continue
                state.active = True
                state.level = 0

            multiplier = self._sequence[state.level]
            amount = int(self._base_amount * multiplier)
            instructions.append(
                BetInstruction(
                    key_code=code,
                    amount=amount,
                    martin_level=state.level,
                )
            )

        return instructions

    def on_result(
        self,
        is_win: Optional[int],
        pnl: int,
        key_code: str | None = None,
        martin_level: int | None = None,
    ) -> Optional[StrategyStopRequest]:
        if key_code is None:
            return None

        code = key_code.strip().upper()
        state = self._states.get(code)
        if state is None:
            return None
        if not state.active and martin_level is None:
            return None

        if is_win == 1:
            state.active = False
            state.level = 0
            return None

        if is_win == 0:
            state.active = True
            current_level = state.level
            if martin_level is not None:
                current_level = max(0, min(int(martin_level), len(self._sequence) - 1))
            next_level = current_level + 1
            if next_level >= len(self._sequence):
                state.level = 0
            else:
                state.level = next_level
            return None

        # refund / no feedback: keep chasing state and level unchanged
        if martin_level is not None:
            state.active = True
            state.level = max(0, min(int(martin_level), len(self._sequence) - 1))
        return None

    def _is_target_wave(self, code: str, balls: list[int], sum_value: int) -> bool:
        if code.startswith("DS"):
            return sum_value in self.SUM_TRIGGER_VALUES

        ball_index = _BALL_INDEX[code]
        if ball_index >= len(balls):
            return False
        return balls[ball_index] in self.BALL_TRIGGER_VALUES


@register_strategy("red_wave_double_martin")
class RedWaveDoubleMartinStrategy(_WaveDirectionMartinStrategy):
    """Red-wave trigger + directional double chase."""

    STRATEGY_NAME = "red_wave_double_martin"
    ALLOWED_DIRECTION_CODES = RED_WAVE_DOUBLE_ALLOWED_CODES
    DEFAULT_DIRECTION_CODES = ("DS4",)
    BALL_TRIGGER_VALUES = _RED_BALL_VALUES
    SUM_TRIGGER_VALUES = _RED_SUM_VALUES


@register_strategy("green_wave_single_martin")
class GreenWaveSingleMartinStrategy(_WaveDirectionMartinStrategy):
    """Green-wave trigger + directional single chase."""

    STRATEGY_NAME = "green_wave_single_martin"
    ALLOWED_DIRECTION_CODES = GREEN_WAVE_SINGLE_ALLOWED_CODES
    DEFAULT_DIRECTION_CODES = ("DS3",)
    BALL_TRIGGER_VALUES = _GREEN_BALL_VALUES
    SUM_TRIGGER_VALUES = _GREEN_SUM_VALUES
