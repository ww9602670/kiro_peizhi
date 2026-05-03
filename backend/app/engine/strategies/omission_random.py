"""Omission-weighted random pick strategies."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Any, Optional

from app.engine.strategies.base import BaseStrategy, BetInstruction, StrategyContext
from app.engine.strategies.martin import PendingAlert
from app.engine.strategies.registry import register_strategy
from app.utils.omission_random import (
    ADAPTIVE_HISTORY_WINDOWS,
    AI_RANDOM_FLAT_TYPE,
    AI_RANDOM_MARTIN_TYPE,
    AI_SAME_RANDOM_FLAT_TYPE,
    AI_SAME_RANDOM_MARTIN_TYPE,
    AI_RANDOM_WEIGHT_MODE,
    CATEGORY_LABELS,
    DEFAULT_HISTORY_WINDOW,
    OMISSION_RANDOM_FLAT_TYPE,
    OMISSION_RANDOM_MARTIN_TYPE,
    OMISSION_WEIGHT_MODE,
    WEIGHTED_PICK_COUNT,
    category_for_key_code,
    key_code_value,
    key_codes_for_category,
    normalize_strategy_config,
    value_for_result,
)

logger = logging.getLogger(__name__)


@dataclass
class CategoryState:
    level: int = 0
    history_window: int = DEFAULT_HISTORY_WINDOW
    short_win_streak: int = 0
    reverse_active: bool = False
    round_loss: int = 0

    @classmethod
    def from_raw(cls, raw: Any) -> "CategoryState":
        if not isinstance(raw, dict):
            return cls()
        try:
            level = max(0, int(raw.get("level", 0)))
        except (TypeError, ValueError):
            level = 0
        try:
            history_window = int(raw.get("history_window", DEFAULT_HISTORY_WINDOW))
        except (TypeError, ValueError):
            history_window = DEFAULT_HISTORY_WINDOW
        if history_window not in ADAPTIVE_HISTORY_WINDOWS:
            history_window = DEFAULT_HISTORY_WINDOW
        try:
            short_win_streak = max(0, int(raw.get("short_win_streak", 0)))
        except (TypeError, ValueError):
            short_win_streak = 0
        try:
            round_loss = max(0, int(raw.get("round_loss", 0)))
        except (TypeError, ValueError):
            round_loss = 0
        return cls(
            level=level,
            history_window=history_window,
            short_win_streak=short_win_streak,
            reverse_active=bool(raw.get("reverse_active", False)),
            round_loss=round_loss,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "history_window": self.history_window,
            "short_win_streak": self.short_win_streak,
            "reverse_active": self.reverse_active,
            "round_loss": self.round_loss,
        }


class _OmissionRandomBaseStrategy(BaseStrategy):
    required_history_issues = DEFAULT_HISTORY_WINDOW
    settlement_scope = "category"
    strategy_kind = "omission_random"
    weight_mode = OMISSION_WEIGHT_MODE
    allow_sum_category = True

    def __init__(
        self,
        *,
        base_amount: int,
        config: dict[str, Any],
        sequence: list[int | float] | None = None,
        strategy_name: str = "",
        alert_service=None,
        operator_id: int = 0,
        rng: random.Random | None = None,
    ) -> None:
        normalized = normalize_strategy_config(
            config,
            weight_mode=self.weight_mode,
            allow_sum=self.allow_sum_category,
        )
        if base_amount <= 0:
            raise ValueError("base_amount must be > 0")
        self._base_amount = int(base_amount)
        self._pick_count = int(normalized["pick_count"])
        self._categories = list(normalized["categories"])
        self._sequence = [float(v) for v in (sequence or [])]
        if self.is_martin and (not self._sequence or any(v <= 0 for v in self._sequence)):
            raise ValueError("sequence is required")
        self._strategy_name = strategy_name or self.name()
        self._alert_service = alert_service
        self._operator_id = operator_id
        self._rng = rng or random.SystemRandom()
        raw_runtime_state = normalized.get("runtime_state")
        raw_category_states = (
            raw_runtime_state.get("categories")
            if isinstance(raw_runtime_state, dict)
            else None
        )
        self._states: dict[str, CategoryState] = {}
        for category in self._categories:
            state = CategoryState.from_raw(
                raw_category_states.get(category)
                if isinstance(raw_category_states, dict)
                else None
            )
            if self.is_martin and self._sequence:
                state.level = min(state.level, len(self._sequence) - 1)
            else:
                state.level = 0
            self._states[category] = state
        self._pending_alerts: list[PendingAlert] = []

    @property
    def is_martin(self) -> bool:
        return False

    @property
    def level(self) -> int:
        return max((state.level for state in self._states.values()), default=0)

    @property
    def pending_alerts(self) -> list[PendingAlert]:
        return self._pending_alerts

    def category_for_key_code(self, key_code: str | None) -> str | None:
        return category_for_key_code(key_code)

    def export_config(self) -> dict[str, Any]:
        return {
            "pick_count": self._pick_count,
            "categories": list(self._categories),
            "weight_mode": self.weight_mode,
            "runtime_state": {
                "categories": {
                    category: self._states[category].to_dict()
                    for category in self._categories
                }
            },
        }

    def compute(self, ctx: StrategyContext) -> list[BetInstruction]:
        instructions: list[BetInstruction] = []
        for category in self._categories:
            state = self._states[category]
            amount = self._amount_for_state(state)
            picks = self._pick_category_numbers(category, state, ctx)
            for key_code in picks:
                instructions.append(
                    BetInstruction(
                        key_code=key_code,
                        amount=amount,
                        martin_level=state.level if self.is_martin else None,
                        metadata={
                            "strategy_kind": self.strategy_kind,
                            "category": category,
                            "history_window": state.history_window,
                            "reverse_active": state.reverse_active,
                        },
                    )
                )
        return instructions

    def on_result(
        self,
        is_win: Optional[int],
        pnl: int,
        key_code: str | None = None,
        martin_level: int | None = None,
    ) -> None:
        category = category_for_key_code(key_code)
        if category is None or category not in self._states:
            return None
        if not self.is_martin:
            return None
        self._apply_martin_result(
            category=category,
            state=self._states[category],
            is_win=is_win,
            pnl=int(pnl or 0),
            martin_level=martin_level,
        )
        return None

    async def flush_alerts(self) -> None:
        if not self._alert_service or not self._pending_alerts:
            self._pending_alerts.clear()
            return
        for alert in self._pending_alerts:
            await self._alert_service.send(
                operator_id=self._operator_id,
                alert_type=alert.alert_type,
                title=alert.title,
                detail=alert.detail,
            )
        self._pending_alerts.clear()

    def _amount_for_state(self, state: CategoryState) -> int:
        return self._base_amount

    def _pick_category_numbers(
        self,
        category: str,
        state: CategoryState,
        ctx: StrategyContext,
    ) -> list[str]:
        candidates = key_codes_for_category(category)
        weighted_count = min(WEIGHTED_PICK_COUNT, self._pick_count, len(candidates))
        window = ctx.history[: state.history_window]
        weighted = self._weighted_sample(
            category,
            candidates,
            window,
            weighted_count,
            reverse=state.reverse_active,
        )
        remaining = [key_code for key_code in candidates if key_code not in weighted]
        random_count = min(self._pick_count - len(weighted), len(remaining))
        random_picks = self._uniform_sample(remaining, random_count)
        return weighted + random_picks

    def _weighted_sample(
        self,
        category: str,
        candidates: list[str],
        history,
        count: int,
        *,
        reverse: bool,
    ) -> list[str]:
        if count <= 0:
            return []
        distances = self._omission_distances(category, candidates, history)
        max_distance = max(distances.values(), default=0)
        remaining = list(candidates)
        selected: list[str] = []
        for _ in range(min(count, len(remaining))):
            weights = []
            for key_code in remaining:
                distance = distances[key_code]
                if reverse:
                    weights.append(max(1, max_distance - distance + 1))
                else:
                    weights.append(max(1, distance + 1))
            chosen_index = self._weighted_index(weights)
            selected.append(remaining.pop(chosen_index))
        return selected

    def _uniform_sample(self, candidates: list[str], count: int) -> list[str]:
        if count <= 0:
            return []
        remaining = list(candidates)
        selected: list[str] = []
        for _ in range(min(count, len(remaining))):
            index = self._rng.randrange(len(remaining))
            selected.append(remaining.pop(index))
        return selected

    def _weighted_index(self, weights: list[int]) -> int:
        total = sum(weights)
        if total <= 0:
            return self._rng.randrange(len(weights))
        marker = self._rng.uniform(0, total)
        cumulative = 0.0
        for index, weight in enumerate(weights):
            cumulative += weight
            if marker <= cumulative:
                return index
        return len(weights) - 1

    def _omission_distances(self, category: str, candidates: list[str], history) -> dict[str, int]:
        values_to_codes = {
            key_code_value(category, key_code): key_code
            for key_code in candidates
        }
        distances = {
            key_code: len(history) + 1
            for key_code in candidates
        }
        for index, result in enumerate(history):
            value = value_for_result(category, result.balls, result.sum_value)
            key_code = values_to_codes.get(value)
            if key_code is not None and distances[key_code] == len(history) + 1:
                distances[key_code] = index
        return distances

    def _apply_martin_result(
        self,
        *,
        category: str,
        state: CategoryState,
        is_win: int | None,
        pnl: int,
        martin_level: int | None,
    ) -> None:
        current_level = state.level
        if martin_level is not None:
            current_level = max(0, min(int(martin_level), len(self._sequence) - 1))
            state.level = current_level

        if is_win == 1:
            if current_level <= 1:
                state.short_win_streak += 1
            else:
                state.short_win_streak = 0

            if state.short_win_streak >= 3:
                state.history_window = 10
            elif state.short_win_streak >= 2:
                state.history_window = 50
            else:
                state.history_window = DEFAULT_HISTORY_WINDOW
            state.level = 0
            state.round_loss = 0
            state.reverse_active = False
            return

        if is_win == 0:
            state.round_loss += abs(pnl)
            next_level = current_level + 1
            if next_level >= len(self._sequence):
                self._handle_round_bust(category, state)
            else:
                state.level = next_level
            return

        if martin_level is not None:
            state.level = current_level

    def _handle_round_bust(self, category: str, state: CategoryState) -> None:
        logger.warning(
            "omission_random_martin_bust strategy=%s category=%s level_count=%d round_loss=%d",
            self._strategy_name,
            category,
            len(self._sequence),
            state.round_loss,
        )
        self._pending_alerts.append(
            PendingAlert(
                alert_type="martin_reset",
                title=f"{self._strategy_name} {CATEGORY_LABELS.get(category, category)}",
                detail=(
                    f"{self._strategy_name} {CATEGORY_LABELS.get(category, category)} "
                    f"马丁已连续走完 {len(self._sequence)} 档，"
                    f"累计亏损 {state.round_loss / 100:.2f}，已重置为第一档。"
                ),
            )
        )
        if state.history_window != DEFAULT_HISTORY_WINDOW:
            state.history_window = DEFAULT_HISTORY_WINDOW
            state.reverse_active = False
        else:
            state.reverse_active = True
        state.level = 0
        state.short_win_streak = 0
        state.round_loss = 0


class _AiRandomBaseStrategy(_OmissionRandomBaseStrategy):
    required_history_issues = 0
    strategy_kind = "ai_random"
    weight_mode = AI_RANDOM_WEIGHT_MODE

    def _pick_category_numbers(
        self,
        category: str,
        state: CategoryState,
        ctx: StrategyContext,
    ) -> list[str]:
        del state, ctx
        return self._uniform_sample(key_codes_for_category(category), self._pick_count)

    def _apply_martin_result(
        self,
        *,
        category: str,
        state: CategoryState,
        is_win: int | None,
        pnl: int,
        martin_level: int | None,
    ) -> None:
        current_level = state.level
        if martin_level is not None:
            current_level = max(0, min(int(martin_level), len(self._sequence) - 1))
            state.level = current_level

        if is_win == 1:
            state.level = 0
            state.history_window = DEFAULT_HISTORY_WINDOW
            state.short_win_streak = 0
            state.reverse_active = False
            state.round_loss = 0
            return

        if is_win == 0:
            state.round_loss += abs(pnl)
            next_level = current_level + 1
            if next_level >= len(self._sequence):
                self._handle_round_bust(category, state)
            else:
                state.level = next_level
            return

        if martin_level is not None:
            state.level = current_level

    def _handle_round_bust(self, category: str, state: CategoryState) -> None:
        logger.warning(
            "ai_random_martin_bust strategy=%s category=%s level_count=%d round_loss=%d",
            self._strategy_name,
            category,
            len(self._sequence),
            state.round_loss,
        )
        self._pending_alerts.append(
            PendingAlert(
                alert_type="martin_reset",
                title=f"{self._strategy_name} {CATEGORY_LABELS.get(category, category)}",
                detail=(
                    f"{self._strategy_name} {CATEGORY_LABELS.get(category, category)} "
                    f"马丁已连续跑完 {len(self._sequence)} 档，"
                    f"累计亏损 {state.round_loss / 100:.2f}，已重置为第一档。"
                ),
            )
        )
        state.level = 0
        state.history_window = DEFAULT_HISTORY_WINDOW
        state.short_win_streak = 0
        state.reverse_active = False
        state.round_loss = 0


class _AiSameRandomBaseStrategy(_AiRandomBaseStrategy):
    strategy_kind = "ai_same_random"
    allow_sum_category = False

    def compute(self, ctx: StrategyContext) -> list[BetInstruction]:
        shared_picks = self._uniform_sample(key_codes_for_category("ball1"), self._pick_count)
        shared_values = [key_code_value("ball1", key_code) for key_code in shared_picks]

        instructions: list[BetInstruction] = []
        for category in self._categories:
            state = self._states[category]
            amount = self._amount_for_state(state)
            category_codes = key_codes_for_category(category)
            for value in shared_values:
                instructions.append(
                    BetInstruction(
                        key_code=category_codes[value],
                        amount=amount,
                        martin_level=state.level if self.is_martin else None,
                        metadata={
                            "strategy_kind": self.strategy_kind,
                            "category": category,
                            "shared_value": value,
                            "history_window": state.history_window,
                            "reverse_active": state.reverse_active,
                        },
                    )
                )
        return instructions


@register_strategy(OMISSION_RANDOM_FLAT_TYPE)
class OmissionRandomFlatStrategy(_OmissionRandomBaseStrategy):
    def name(self) -> str:
        return OMISSION_RANDOM_FLAT_TYPE


@register_strategy(OMISSION_RANDOM_MARTIN_TYPE)
class OmissionRandomMartinStrategy(_OmissionRandomBaseStrategy):
    @property
    def is_martin(self) -> bool:
        return True

    def name(self) -> str:
        return OMISSION_RANDOM_MARTIN_TYPE

    def _amount_for_state(self, state: CategoryState) -> int:
        multiplier = self._sequence[state.level]
        return int(self._base_amount * multiplier)


@register_strategy(AI_RANDOM_FLAT_TYPE)
class AiRandomFlatStrategy(_AiRandomBaseStrategy):
    def name(self) -> str:
        return AI_RANDOM_FLAT_TYPE


@register_strategy(AI_RANDOM_MARTIN_TYPE)
class AiRandomMartinStrategy(_AiRandomBaseStrategy):
    @property
    def is_martin(self) -> bool:
        return True

    def name(self) -> str:
        return AI_RANDOM_MARTIN_TYPE

    def _amount_for_state(self, state: CategoryState) -> int:
        multiplier = self._sequence[state.level]
        return int(self._base_amount * multiplier)


@register_strategy(AI_SAME_RANDOM_FLAT_TYPE)
class AiSameRandomFlatStrategy(_AiSameRandomBaseStrategy):
    def name(self) -> str:
        return AI_SAME_RANDOM_FLAT_TYPE


@register_strategy(AI_SAME_RANDOM_MARTIN_TYPE)
class AiSameRandomMartinStrategy(_AiSameRandomBaseStrategy):
    @property
    def is_martin(self) -> bool:
        return True

    def name(self) -> str:
        return AI_SAME_RANDOM_MARTIN_TYPE

    def _amount_for_state(self, state: CategoryState) -> int:
        multiplier = self._sequence[state.level]
        return int(self._base_amount * multiplier)
