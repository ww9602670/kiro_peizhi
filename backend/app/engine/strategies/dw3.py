from __future__ import annotations

from typing import Iterable

from app.engine.dw3_plan import build_dw3_execution_plan
from app.engine.dw3_signal_state import bootstrap_dw3_signal_state
from app.engine.strategies.base import BetInstruction, StrategyContext
from app.engine.strategies.flat import FlatStrategyImpl
from app.engine.strategies.martin import MartinStrategyImpl
from app.utils.dw3_groups import (
    DW3_BS_TOKENS,
    DW3_OE_TOKENS,
)
_DW3_BS_TOKEN_SET = set(DW3_BS_TOKENS)
_DW3_OE_TOKEN_SET = set(DW3_OE_TOKENS)


def _split_dw3_group_tokens(tokens: Iterable[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    bs_tokens: list[str] = []
    oe_tokens: list[str] = []
    for token in tokens:
        if token in _DW3_BS_TOKEN_SET:
            if token not in bs_tokens:
                bs_tokens.append(token)
            continue
        if token in _DW3_OE_TOKEN_SET:
            if token not in oe_tokens:
                oe_tokens.append(token)
            continue
        raise ValueError(f"Invalid DW3 group token: {token}")
    if not bs_tokens and not oe_tokens:
        raise ValueError("DW3 strategy requires at least one group token")
    return tuple(bs_tokens), tuple(oe_tokens)


class _DW3StrategyMixin:
    settlement_scope = "issue"

    def __init__(self, *, group_tokens: list[str], gate_window_issues: int) -> None:
        if gate_window_issues <= 0:
            raise ValueError("gate_window_issues must be > 0")
        self._selected_bs, self._selected_oe = _split_dw3_group_tokens(group_tokens)
        self.required_history_issues = gate_window_issues
        self._gate_window_issues = gate_window_issues
        self.last_signal_metadata: dict[str, object] = {}

    def _build_dw3_instructions(
        self,
        ctx: StrategyContext,
        *,
        stage_amount: int,
        martin_level: int | None = None,
    ) -> list[BetInstruction]:
        if stage_amount <= 0:
            self.last_signal_metadata = {}
            return []
        state = self._build_signal_state(ctx)
        plan = build_dw3_execution_plan(
            state=state,
            selected_bs=self._selected_bs,
            selected_oe=self._selected_oe,
            gate_window_issues=self._gate_window_issues,
            stage_amount=stage_amount,
        )
        metadata = _build_dw3_signal_metadata(plan)
        self.last_signal_metadata = metadata
        if plan.gate_skipped:
            return []

        return [
            BetInstruction(
                key_code=key_code,
                amount=amount,
                martin_level=martin_level,
                metadata=metadata,
            )
            for key_code, amount in plan.merged_amounts.items()
        ]

    def _build_signal_state(self, ctx: StrategyContext):
        if not ctx.history:
            return None
        history_entries = []
        for index, result in enumerate(reversed(ctx.history), start=1):
            history_entries.append(
                {
                    "issue": result.issue,
                    "draw": [int(ball) for ball in result.balls],
                    "closed_seq": index,
                }
            )
        return bootstrap_dw3_signal_state(
            platform_type="DW3",
            lottery_history=history_entries,
        )


def _build_dw3_signal_metadata(plan) -> dict[str, object]:
    return {
        "strategy_kind": "dw3",
        "gate_skipped": bool(plan.gate_skipped),
        "skip_reason": plan.skip_reason,
        "effective_groups": sorted(plan.effective_bs | plan.effective_oe),
        "blocked_groups": sorted(plan.blocked_bs | plan.blocked_oe),
        "unique_key_count": int(plan.effective_key_count),
        "total_amount": int(plan.total_stake),
        "submit_mode": plan.submit_mode,
    }


class DW3FlatStrategy(_DW3StrategyMixin, FlatStrategyImpl):
    def __init__(
        self,
        *,
        group_tokens: list[str],
        base_amount: int,
        gate_window_issues: int,
    ) -> None:
        FlatStrategyImpl.__init__(self, key_codes=["DW3_SENTINEL"], base_amount=base_amount)
        _DW3StrategyMixin.__init__(
            self,
            group_tokens=group_tokens,
            gate_window_issues=gate_window_issues,
        )

    def compute(self, ctx: StrategyContext) -> list[BetInstruction]:
        return self._build_dw3_instructions(ctx, stage_amount=self._base_amount)


class DW3MartinStrategy(_DW3StrategyMixin, MartinStrategyImpl):
    def __init__(
        self,
        *,
        group_tokens: list[str],
        base_amount: int,
        sequence: list[int | float],
        gate_window_issues: int,
        alert_service=None,
        operator_id: int = 0,
        strategy_name: str = "",
    ) -> None:
        MartinStrategyImpl.__init__(
            self,
            key_codes=["DW3_SENTINEL"],
            base_amount=base_amount,
            sequence=sequence,
            alert_service=alert_service,
            operator_id=operator_id,
            strategy_name=strategy_name or "dw3_martin",
        )
        _DW3StrategyMixin.__init__(
            self,
            group_tokens=group_tokens,
            gate_window_issues=gate_window_issues,
        )

    def compute(self, ctx: StrategyContext) -> list[BetInstruction]:
        multiplier = self._sequence[self._level]
        stage_amount = int(self._base_amount * multiplier)
        return self._build_dw3_instructions(
            ctx,
            stage_amount=stage_amount,
            martin_level=self.level,
        )
