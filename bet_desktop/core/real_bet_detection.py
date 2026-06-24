"""Structured contracts for real-bet detection snapshots.

This module intentionally contains pure data contracts only; it does not read
runtime logs or execute real clicking logic.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping

from bet_desktop.models.state_temporal_guard import now_ms


def _coerce_str(value: object, field_name: str) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value).strip()


def _coerce_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be int")
    return int(value)


def _coerce_non_negative_cents(value: object, field_name: str) -> int:
    amount = _coerce_int(value, field_name)
    if amount < 0:
        raise ValueError(f"{field_name} must be >= 0")
    return amount


def _enum_values(enum_type: type[Enum]) -> set[str]:
    return {item.value for item in enum_type}  # type: ignore[attr-defined]


def _coerce_status(value: object, enum_type: type[Enum], field_name: str) -> str:
    if isinstance(value, enum_type):
        return value.value
    candidate = _coerce_str(value, field_name)
    if candidate not in _enum_values(enum_type):
        raise ValueError(f"{field_name} must be one of: {', '.join(sorted(_enum_values(enum_type)))}")
    return candidate


def _extract_round_prefix(round_id: str, *, segments: int = 3) -> str:
    parts = [part for part in str(round_id).strip().split("-") if part]
    if not parts:
        return ""
    return "-".join(parts[:segments])


class RealBetDetectionStatus(str, Enum):
    PLANNED = "planned"
    DISPATCHED = "dispatched"
    CONFIRMED_COMPLETE = "confirmed_complete"
    CONFIRMED_PARTIAL = "confirmed_partial"
    MISSING = "missing"
    SETTLED = "settled"
    MANUAL_UNRESOLVED = "manual_unresolved"


class RealBetDetectionSource(str, Enum):
    WORKER_RESULT = "worker_result"
    BALANCE_DELTA = "balance_delta"
    LEDGER_SETTLEMENT = "ledger_settlement"
    MANUAL_CLEAR = "manual_clear"


class RealBetDetectionSettlementStatus(str, Enum):
    UNSETTLED = "unsettled"
    SETTLED = "settled"
    MANUAL_CLEAR = "manual_clear"


def resolve_missing_cents(planned_cents: int, confirmed_cents: int) -> int:
    """Return amount still missing, never negative."""

    planned = _coerce_non_negative_cents(planned_cents, "planned_cents")
    confirmed = _coerce_non_negative_cents(confirmed_cents, "confirmed_cents")
    return max(0, planned - confirmed)


def parse_round_prefix(round_id: str) -> str:
    """Return 前三段局号 for stable grouping."""

    return _extract_round_prefix(round_id)


def compute_status_from_amounts(
    planned_cents: int,
    confirmed_cents: int,
    *,
    settled: bool = False,
    manual_unresolved: bool = False,
) -> str:
    """Derive a status from amounts for callers assembling new snapshots."""

    planned = _coerce_non_negative_cents(planned_cents, "planned_cents")
    confirmed = _coerce_non_negative_cents(confirmed_cents, "confirmed_cents")

    if manual_unresolved:
        return RealBetDetectionStatus.MANUAL_UNRESOLVED.value
    if settled:
        return RealBetDetectionStatus.SETTLED.value
    if planned == 0 and confirmed == 0:
        return RealBetDetectionStatus.MISSING.value
    if confirmed == 0:
        return RealBetDetectionStatus.DISPATCHED.value
    if confirmed < planned:
        return RealBetDetectionStatus.CONFIRMED_PARTIAL.value
    return RealBetDetectionStatus.CONFIRMED_COMPLETE.value


def safe_copy_payload(payload: Mapping[str, Any], **updates: Any) -> dict[str, Any]:
    """Return a shallow, isolated copy with requested updates applied."""

    copied = dict(payload)
    copied.update(updates)
    return copied


@dataclass(frozen=True)
class RealBetDetectionSnapshot:
    """One immutable row representing detection result for one account-round."""

    run_id: str
    session_id: str
    account_id: str
    round_id: str
    round_id_prefix: str
    side: str
    planned_cents: int
    confirmed_cents: int
    status: str
    source: str
    created_at_ms: int
    updated_at_ms: int
    settlement_status: str = RealBetDetectionSettlementStatus.UNSETTLED.value
    settled_at_ms: int = 0
    settlement_profit_loss_cents: int | None = None
    run_planned_cents: int = 0
    run_confirmed_cents: int = 0
    run_missing_cents: int = field(init=False)
    session_planned_cents: int = 0
    session_confirmed_cents: int = 0
    session_missing_cents: int = field(init=False)
    missing_cents: int = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _coerce_str(self.run_id, "run_id"))
        object.__setattr__(self, "session_id", _coerce_str(self.session_id, "session_id"))
        object.__setattr__(self, "account_id", _coerce_str(self.account_id, "account_id"))
        if not self.account_id:
            raise ValueError("account_id is required")

        object.__setattr__(self, "round_id", _coerce_str(self.round_id, "round_id"))
        if not self.round_id:
            raise ValueError("round_id is required")

        object.__setattr__(self, "round_id_prefix", _coerce_str(self.round_id_prefix, "round_id_prefix") or parse_round_prefix(self.round_id))
        if not self.round_id_prefix:
            raise ValueError("round_id_prefix is required")

        object.__setattr__(self, "side", _coerce_str(self.side, "side"))
        if not self.side:
            raise ValueError("side is required")

        object.__setattr__(self, "planned_cents", _coerce_non_negative_cents(self.planned_cents, "planned_cents"))
        object.__setattr__(self, "confirmed_cents", _coerce_non_negative_cents(self.confirmed_cents, "confirmed_cents"))
        object.__setattr__(self, "created_at_ms", _coerce_non_negative_cents(self.created_at_ms, "created_at_ms"))
        object.__setattr__(self, "updated_at_ms", _coerce_non_negative_cents(self.updated_at_ms, "updated_at_ms"))
        object.__setattr__(self, "settled_at_ms", _coerce_non_negative_cents(self.settled_at_ms, "settled_at_ms"))

        object.__setattr__(self, "run_planned_cents", _coerce_non_negative_cents(self.run_planned_cents, "run_planned_cents"))
        object.__setattr__(
            self,
            "run_confirmed_cents",
            _coerce_non_negative_cents(self.run_confirmed_cents, "run_confirmed_cents"),
        )
        object.__setattr__(
            self,
            "session_planned_cents",
            _coerce_non_negative_cents(self.session_planned_cents, "session_planned_cents"),
        )
        object.__setattr__(
            self,
            "session_confirmed_cents",
            _coerce_non_negative_cents(self.session_confirmed_cents, "session_confirmed_cents"),
        )

        object.__setattr__(self, "status", _coerce_status(self.status, RealBetDetectionStatus, "status"))
        object.__setattr__(self, "source", _coerce_status(self.source, RealBetDetectionSource, "source"))
        object.__setattr__(
            self,
            "settlement_status",
            _coerce_status(self.settlement_status, RealBetDetectionSettlementStatus, "settlement_status"),
        )

        if self.settlement_profit_loss_cents is not None:
            object.__setattr__(self, "settlement_profit_loss_cents", _coerce_int(self.settlement_profit_loss_cents, "settlement_profit_loss_cents"))

        if self.status == RealBetDetectionStatus.SETTLED.value:
            object.__setattr__(self, "settlement_status", RealBetDetectionSettlementStatus.SETTLED.value)
        elif self.status == RealBetDetectionStatus.MANUAL_UNRESOLVED.value:
            object.__setattr__(self, "source", RealBetDetectionSource.MANUAL_CLEAR.value)
            object.__setattr__(self, "settlement_status", RealBetDetectionSettlementStatus.MANUAL_CLEAR.value)

        object.__setattr__(self, "missing_cents", resolve_missing_cents(self.planned_cents, self.confirmed_cents))
        object.__setattr__(
            self,
            "run_missing_cents",
            resolve_missing_cents(self.run_planned_cents, self.run_confirmed_cents),
        )
        object.__setattr__(
            self,
            "session_missing_cents",
            resolve_missing_cents(self.session_planned_cents, self.session_confirmed_cents),
        )

    @property
    def is_complete(self) -> bool:
        return self.confirmed_cents >= self.planned_cents and self.status != RealBetDetectionStatus.MISSING.value

    @property
    def is_partial(self) -> bool:
        return 0 < self.confirmed_cents < self.planned_cents

    @property
    def is_settled(self) -> bool:
        return self.settlement_status == RealBetDetectionSettlementStatus.SETTLED.value

    @property
    def is_manual_unresolved(self) -> bool:
        return self.status == RealBetDetectionStatus.MANUAL_UNRESOLVED.value

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = str(self.status)
        data["source"] = str(self.source)
        data["settlement_status"] = str(self.settlement_status)
        return data

    def to_jsonl_record(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RealBetDetectionSnapshot":
        value = dict(payload)
        round_id = _coerce_str(value.get("round_id") or value.get("batch_id"), "round_id")
        round_prefix = _coerce_str(value.get("round_id_prefix") or value.get("round_prefix"), "round_id_prefix")
        if not round_prefix:
            round_prefix = parse_round_prefix(round_id)
        if not round_prefix:
            raise ValueError("round_id_prefix is required")
        return cls(
            run_id=_coerce_str(value.get("run_id", ""), "run_id"),
            session_id=_coerce_str(value.get("session_id", ""), "session_id"),
            account_id=_coerce_str(value.get("account_id", ""), "account_id"),
            round_id=round_id,
            round_id_prefix=round_prefix,
            side=_coerce_str(value.get("side"), "side"),
            planned_cents=_coerce_non_negative_cents(
                value.get("planned_cents", value.get("planned_amount", 0)),
                "planned_cents",
            ),
            confirmed_cents=_coerce_non_negative_cents(
                value.get("confirmed_cents", value.get("confirmed_amount", 0)),
                "confirmed_cents",
            ),
            status=_coerce_status(value.get("status", RealBetDetectionStatus.PLANNED.value), RealBetDetectionStatus, "status"),
            source=_coerce_status(
                value.get("source", RealBetDetectionSource.WORKER_RESULT.value),
                RealBetDetectionSource,
                "source",
            ),
            created_at_ms=_coerce_non_negative_cents(value.get("created_at_ms", now_ms()), "created_at_ms"),
            updated_at_ms=_coerce_non_negative_cents(value.get("updated_at_ms", now_ms()), "updated_at_ms"),
            settlement_status=_coerce_status(
                value.get("settlement_status", RealBetDetectionSettlementStatus.UNSETTLED.value),
                RealBetDetectionSettlementStatus,
                "settlement_status",
            ),
            settled_at_ms=_coerce_non_negative_cents(value.get("settled_at_ms", 0), "settled_at_ms"),
            settlement_profit_loss_cents=(
                None
                if value.get("settlement_profit_loss_cents") is None
                else _coerce_int(value["settlement_profit_loss_cents"], "settlement_profit_loss_cents")
            ),
            run_planned_cents=_coerce_non_negative_cents(
                value.get("run_planned_cents", value.get("run_total_planned_cents", 0)),
                "run_planned_cents",
            ),
            run_confirmed_cents=_coerce_non_negative_cents(
                value.get("run_confirmed_cents", value.get("run_total_confirmed_cents", 0)),
                "run_confirmed_cents",
            ),
            session_planned_cents=_coerce_non_negative_cents(
                value.get("session_planned_cents", value.get("session_total_planned_cents", 0)),
                "session_planned_cents",
            ),
            session_confirmed_cents=_coerce_non_negative_cents(
                value.get("session_confirmed_cents", value.get("session_total_confirmed_cents", 0)),
                "session_confirmed_cents",
            ),
        )

    @classmethod
    def from_jsonl_record(cls, text: str) -> "RealBetDetectionSnapshot":
        payload = json.loads(text)
        if not isinstance(payload, Mapping):
            raise TypeError("JSONL record must decode to object")
        return cls.from_mapping(payload)

    def safe_copy(self, **updates: Any) -> "RealBetDetectionSnapshot":
        return RealBetDetectionSnapshot.from_mapping(safe_copy_payload(self.as_dict(), **updates))


__all__ = [
    "RealBetDetectionSnapshot",
    "RealBetDetectionStatus",
    "RealBetDetectionSource",
    "RealBetDetectionSettlementStatus",
    "compute_status_from_amounts",
    "parse_round_prefix",
    "resolve_missing_cents",
    "safe_copy_payload",
]

