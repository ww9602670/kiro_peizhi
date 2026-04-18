from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.engine.strategies.base import LotteryResult, StrategyContext
from app.engine.strategies.dw3 import DW3FlatStrategy
from app.engine.strategy_runner import BetSignal, StrategyRunner
from app.utils.dw3_groups import DW3_GROUP_TOKENS, number_to_dw3_key_code


MAX_BET_REFERENCE = 126


@dataclass(frozen=True)
class DW3OneShotGatePayload:
    issue: str
    pre_issue: str
    pre_result: str
    betdata: list[dict[str, int | str]]
    summary: dict[str, Any]
    missing_odds: list[str] = field(default_factory=list)


def synthetic_dw3_odds_map(odds_scaled: int = 9_917_000) -> dict[str, int]:
    return {
        number_to_dw3_key_code(number): int(odds_scaled)
        for number in range(1000)
    }


def build_dw3_one_shot_gate_payload(
    *,
    issue: str,
    pre_issue: str,
    pre_result: str,
    odds_map: Mapping[str, int],
    amount_fen: int = 1,
    gate_window_issues: int = 1,
    strategy_id: int = 1,
) -> DW3OneShotGatePayload:
    if amount_fen <= 0:
        raise ValueError("amount_fen must be > 0")
    if gate_window_issues <= 0:
        raise ValueError("gate_window_issues must be > 0")

    balls = parse_dw3_pre_result(pre_result)
    strategy = DW3FlatStrategy(
        group_tokens=list(DW3_GROUP_TOKENS),
        base_amount=int(amount_fen),
        gate_window_issues=int(gate_window_issues),
    )
    runner = StrategyRunner(
        strategy_id=int(strategy_id),
        strategy=strategy,
        simulation=False,
    )
    runner.start()

    signals = runner.collect_signals(
        StrategyContext(
            current_issue=issue,
            history=[
                LotteryResult(
                    issue=pre_issue,
                    balls=balls,
                    sum_value=sum(balls),
                )
            ],
            balance=0,
        ),
        issue,
    )

    betdata, missing_odds = _signals_to_betdata(signals, odds_map)
    metadata = (
        signals[0].metadata
        if signals and isinstance(signals[0].metadata, dict)
        else strategy.last_signal_metadata
    )
    total_amount = sum(int(item["Amount"]) for item in betdata)
    key_codes = [str(item["KeyCode"]) for item in betdata]
    unique_key_count = len(set(key_codes))
    summary: dict[str, Any] = {
        "probe_mode": "strategy_gate",
        "issue": issue,
        "pre_issue": pre_issue,
        "pre_result": pre_result,
        "amount_fen": int(amount_fen),
        "gate_window_issues": int(gate_window_issues),
        "gate_bypassed": False,
        "selected_group_count": len(DW3_GROUP_TOKENS),
        "effective_groups": list(metadata.get("effective_groups", [])),
        "blocked_groups": list(metadata.get("blocked_groups", [])),
        "gate_skipped": bool(metadata.get("gate_skipped", False)),
        "skip_reason": metadata.get("skip_reason"),
        "signal_count": len(signals),
        "request_item_count": len(betdata),
        "unique_key_count": unique_key_count,
        "total_amount_fen": total_amount,
        "plan_total_amount_fen": int(metadata.get("total_amount", 0) or 0),
        "missing_odds_count": len(missing_odds),
        "max_bet_reference": MAX_BET_REFERENCE,
        "exceeds_max_bet_reference": len(betdata) > MAX_BET_REFERENCE,
        "submit_mode": metadata.get("submit_mode", "single_request_required"),
    }
    return DW3OneShotGatePayload(
        issue=issue,
        pre_issue=pre_issue,
        pre_result=pre_result,
        betdata=betdata,
        summary=summary,
        missing_odds=missing_odds,
    )


def build_dw3_full_coverage_capacity_payload(
    *,
    issue: str,
    odds_map: Mapping[str, int],
    amount_fen: int = 1,
) -> DW3OneShotGatePayload:
    if amount_fen <= 0:
        raise ValueError("amount_fen must be > 0")

    betdata, missing_odds = _number_range_to_betdata(
        range(1000),
        odds_map,
        amount_fen=int(amount_fen),
    )
    total_amount = sum(int(item["Amount"]) for item in betdata)
    key_codes = [str(item["KeyCode"]) for item in betdata]
    unique_key_count = len(set(key_codes))
    summary: dict[str, Any] = {
        "probe_mode": "full_1000_capacity",
        "issue": issue,
        "pre_issue": "",
        "pre_result": "",
        "amount_fen": int(amount_fen),
        "gate_window_issues": 0,
        "gate_bypassed": True,
        "selected_group_count": len(DW3_GROUP_TOKENS),
        "effective_groups": list(DW3_GROUP_TOKENS),
        "blocked_groups": [],
        "gate_skipped": False,
        "skip_reason": None,
        "signal_count": 0,
        "request_item_count": len(betdata),
        "unique_key_count": unique_key_count,
        "total_amount_fen": total_amount,
        "plan_total_amount_fen": total_amount,
        "missing_odds_count": len(missing_odds),
        "max_bet_reference": MAX_BET_REFERENCE,
        "exceeds_max_bet_reference": len(betdata) > MAX_BET_REFERENCE,
        "submit_mode": "single_request_required",
    }
    return DW3OneShotGatePayload(
        issue=issue,
        pre_issue="",
        pre_result="",
        betdata=betdata,
        summary=summary,
        missing_odds=missing_odds,
    )


def validate_gate_payload_ready(payload: DW3OneShotGatePayload) -> None:
    if payload.summary.get("gate_skipped"):
        raise ValueError(f"DW3 gate plan skipped: {payload.summary.get('skip_reason')}")
    if payload.missing_odds:
        raise ValueError(f"DW3 odds missing for {len(payload.missing_odds)} key codes")
    if len(payload.betdata) <= MAX_BET_REFERENCE:
        raise ValueError(
            "DW3 one-shot request is not larger than "
            f"{MAX_BET_REFERENCE}: {len(payload.betdata)}"
        )
    if payload.summary.get("unique_key_count") != len(payload.betdata):
        raise ValueError("DW3 one-shot request contains duplicate KeyCode values")


def parse_dw3_pre_result(pre_result: str) -> list[int]:
    text = str(pre_result or "").strip()
    if not text:
        raise ValueError("pre_result is required")
    if "," in text:
        parts = [part.strip() for part in text.split(",") if part.strip()]
    else:
        parts = [ch for ch in text if ch.isdigit()]
    if len(parts) != 3:
        raise ValueError(f"pre_result must contain exactly 3 digits: {pre_result!r}")
    balls = [int(part) for part in parts]
    if any(ball < 0 or ball > 9 for ball in balls):
        raise ValueError(f"pre_result digits must be in [0, 9]: {pre_result!r}")
    return balls


def _signals_to_betdata(
    signals: list[BetSignal],
    odds_map: Mapping[str, int],
) -> tuple[list[dict[str, int | str]], list[str]]:
    betdata: list[dict[str, int | str]] = []
    missing_odds: list[str] = []
    seen: set[str] = set()

    for signal in signals:
        key_code = signal.key_code.upper()
        if key_code in seen:
            raise ValueError(f"duplicate DW3 key code in signal list: {key_code}")
        seen.add(key_code)

        odds = int(odds_map.get(key_code, 0) or 0)
        if odds <= 0:
            missing_odds.append(key_code)
            continue
        betdata.append(
            {
                "KeyCode": key_code,
                "Amount": int(signal.amount),
                "Odds": odds,
            }
        )

    return betdata, missing_odds


def _number_range_to_betdata(
    numbers: range,
    odds_map: Mapping[str, int],
    *,
    amount_fen: int,
) -> tuple[list[dict[str, int | str]], list[str]]:
    betdata: list[dict[str, int | str]] = []
    missing_odds: list[str] = []
    seen: set[str] = set()

    for number in numbers:
        key_code = number_to_dw3_key_code(number).upper()
        if key_code in seen:
            raise ValueError(f"duplicate DW3 key code in capacity range: {key_code}")
        seen.add(key_code)

        odds = int(odds_map.get(key_code, 0) or 0)
        if odds <= 0:
            missing_odds.append(key_code)
            continue
        betdata.append(
            {
                "KeyCode": key_code,
                "Amount": int(amount_fen),
                "Odds": odds,
            }
        )

    return betdata, missing_odds


__all__ = [
    "DW3OneShotGatePayload",
    "MAX_BET_REFERENCE",
    "build_dw3_full_coverage_capacity_payload",
    "build_dw3_one_shot_gate_payload",
    "parse_dw3_pre_result",
    "synthetic_dw3_odds_map",
    "validate_gate_payload_ready",
]
