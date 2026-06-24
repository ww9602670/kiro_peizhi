"""Runtime bet confirmation and balance ledger helpers.

This module consumes already-captured runtime signals. It does not read or
change the protected page-state extraction path.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from uuid import uuid4

from bet_desktop.core.real_bet_detection import (
    RealBetDetectionSettlementStatus,
    RealBetDetectionSnapshot,
    RealBetDetectionSource,
    RealBetDetectionStatus,
    compute_status_from_amounts,
    parse_round_prefix,
)


ROUND_RE = re.compile(r"\b\d+-\d+-\d+-(\d{1,4})\b")


def parse_display_amount_cents(value: object) -> int | None:
    """Parse UI balance text such as ``336.38`` into cents."""

    if value is None:
        return None
    if isinstance(value, Decimal):
        amount = value
    elif isinstance(value, (int, float)):
        amount = Decimal(str(value))
    else:
        text = str(value).strip()
        if not text:
            return None
        match = re.search(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?|[-+]?\d+(?:\.\d+)?", text)
        if not match:
            return None
        amount = Decimal(match.group(0).replace(",", ""))
    try:
        return int((amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return None


def cents_to_display(cents: int | None) -> str:
    if cents is None:
        return "-"
    return f"{Decimal(int(cents)) / Decimal('100'):.2f}"


def _safe_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(Decimal(text))
    except (InvalidOperation, ValueError):
        return None


def _normalize_event(raw_event: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw_event, dict):
        return {}
    if isinstance(raw_event.get("data"), dict):
        data = dict(raw_event["data"])
        for key in ("timestamp_ms", "ts", "source"):
            data.setdefault(key, raw_event.get(key))
        return data
    return raw_event


def _hit_name(hit: dict[str, Any]) -> str:
    key = str(hit.get("key") or "").strip()
    if key:
        return key
    path = str(hit.get("path") or "").strip()
    if "." in path:
        return path.rsplit(".", 1)[-1]
    return path


def _field_map(hits: list[dict[str, Any]]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        name = _hit_name(hit).lower()
        if not name:
            continue
        fields.setdefault(name, hit.get("value"))
        path = str(hit.get("path") or "").strip().lower()
        if path:
            fields.setdefault(path, hit.get("value"))
    return fields


def _first_field(fields: dict[str, Any], *names: str) -> Any:
    for name in names:
        lowered = name.lower()
        if lowered in fields:
            return fields[lowered]
    return None


def _short_round_id(game_no: object) -> str:
    text = str(game_no or "").strip()
    match = ROUND_RE.search(text)
    if match:
        return match.group(1)
    parts = text.split("-")
    if len(parts) >= 4 and parts[-1].isdigit():
        return parts[-1]
    return ""


def extract_bet_confirmation_from_frontend_event(
    raw_event: dict[str, Any],
    *,
    instance_id: str = "",
    captured_ms: int | None = None,
) -> dict[str, Any] | None:
    """Extract a server bet confirmation from one frontend probe event."""

    event = _normalize_event(raw_event)
    event_type = str(event.get("event") or "").strip()
    if event_type not in {"json_parse_state", "object_summary"}:
        return None

    hits = event.get("hits")
    if not isinstance(hits, list):
        return None
    fields = _field_map([hit for hit in hits if isinstance(hit, dict)])
    bet_cents = _safe_int(_first_field(fields, "bet", "json.parse.properties.bet"))
    amount_before = _safe_int(
        _first_field(fields, "amountbeforebet", "amount_before_bet", "json.parse.properties.amountbeforebet")
    )
    if bet_cents is None or bet_cents <= 0 or amount_before is None:
        return None

    game_no = str(_first_field(fields, "gameno", "game_no", "json.parse.properties.gameno") or "").strip()
    room_id = str(_first_field(fields, "roomid", "room_id", "json.parse.properties.roomid") or "").strip()
    sn = str(_first_field(fields, "sn", "gamesn", "json.parse.properties.sn") or "").strip()
    amount_after_reward = _safe_int(
        _first_field(
            fields,
            "amountafterreward",
            "amount_after_reward",
            "json.parse.properties.amountafterreward",
        )
    )
    timestamp_ms = _safe_int(event.get("ts")) or _safe_int(event.get("timestamp_ms")) or captured_ms or 0
    short_game_no = _short_round_id(game_no)
    source = str(event.get("source") or event_type)
    dedupe_key = "|".join(
        str(part)
        for part in (
            instance_id,
            timestamp_ms,
            game_no,
            room_id,
            sn,
            bet_cents,
            amount_before,
            amount_after_reward,
        )
    )
    return {
        "audit_type": "bet_confirmation",
        "instance_id": instance_id,
        "timestamp_ms": int(timestamp_ms or 0),
        "game_no": game_no,
        "short_game_no": short_game_no,
        "room_id": room_id,
        "sn": sn,
        "bet_cents": int(bet_cents),
        "amount_before_bet_cents": int(amount_before),
        "amount_after_reward_cents": amount_after_reward,
        "expected_after_bet_cents": int(amount_before - bet_cents),
        "source": source,
        "dedupe_key": dedupe_key,
    }


@dataclass
class RuntimeBetRecord:
    record_id: str
    instance_id: str
    game_no: str
    short_game_no: str
    room_id: str
    sn: str
    bet_cents: int
    amount_before_bet_cents: int | None
    expected_after_bet_cents: int | None
    post_bet_balance_cents: int | None = None
    settlement_balance_cents: int | None = None
    profit_loss_cents: int | None = None
    status: str = "confirmed"
    balance_check_status: str = "pending"
    confirmation_timestamp_ms: int = 0
    settled_at_ms: int = 0
    source: str = ""
    dedupe_key: str = ""

    def to_safe_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["bet_amount"] = cents_to_display(self.bet_cents)
        data["amount_before_bet"] = cents_to_display(self.amount_before_bet_cents)
        data["expected_after_bet"] = cents_to_display(self.expected_after_bet_cents)
        data["post_bet_balance"] = cents_to_display(self.post_bet_balance_cents)
        data["settlement_balance"] = cents_to_display(self.settlement_balance_cents)
        data["profit_loss"] = cents_to_display(self.profit_loss_cents)
        return data


class RuntimeBetLedger:
    """Small in-memory ledger for runtime confirmation, balance, and PnL."""

    def __init__(self) -> None:
        self.records: list[RuntimeBetRecord] = []
        self.latest_balance_cents: dict[str, int] = {}
        self.latest_batch_id: dict[str, str] = {}
        self._seen_confirmation_keys: set[str] = set()
        self._round_profit_loss_cents: dict[tuple[str, str], int] = {}

    def record_confirmation(self, instance_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        key = str(payload.get("dedupe_key") or "")
        if key and key in self._seen_confirmation_keys:
            return {"event_type": "duplicate_confirmation", "instance_id": instance_id, "record": None}
        if key:
            self._seen_confirmation_keys.add(key)

        bet_cents = _safe_int(payload.get("bet_cents")) or 0
        amount_before = _safe_int(payload.get("amount_before_bet_cents"))
        expected_after = _safe_int(payload.get("expected_after_bet_cents"))
        if expected_after is None and amount_before is not None:
            expected_after = amount_before - bet_cents
        record = RuntimeBetRecord(
            record_id=str(uuid4()),
            instance_id=instance_id,
            game_no=str(payload.get("game_no") or ""),
            short_game_no=str(payload.get("short_game_no") or _short_round_id(payload.get("game_no"))),
            room_id=str(payload.get("room_id") or ""),
            sn=str(payload.get("sn") or ""),
            bet_cents=bet_cents,
            amount_before_bet_cents=amount_before,
            expected_after_bet_cents=expected_after,
            confirmation_timestamp_ms=_safe_int(payload.get("timestamp_ms")) or 0,
            source=str(payload.get("source") or ""),
            dedupe_key=key,
        )
        latest_balance = self.latest_balance_cents.get(instance_id)
        if latest_balance is not None and expected_after is not None and latest_balance == expected_after:
            record.post_bet_balance_cents = latest_balance
            record.balance_check_status = "verified"
        self.records.append(record)
        return {
            "event_type": "bet_confirmed",
            "instance_id": instance_id,
            "record": record.to_safe_dict(),
            "message": self._format_confirmation_message(record),
        }

    def update_balance_from_snapshot(self, snapshot: Any) -> list[dict[str, Any]]:
        instance_id = str(getattr(snapshot, "instance_id", "") or "")
        batch_id = str(getattr(snapshot, "batch_id", "") or "")
        balance_text = getattr(snapshot, "ocr_balance", "")
        timestamp_ms = _safe_int(getattr(snapshot, "timestamp_captured_ms", 0)) or 0
        summary = getattr(snapshot, "safe_summary", {}) or {}
        phase_key = str(summary.get("phase_key") or summary.get("runtime_phase") or summary.get("phase_text") or "")
        return self.update_balance(
            instance_id,
            balance_text,
            batch_id=batch_id,
            timestamp_ms=timestamp_ms,
            phase_key=phase_key,
        )

    def update_balance(
        self,
        instance_id: str,
        balance_text: object,
        *,
        batch_id: str = "",
        timestamp_ms: int = 0,
        phase_key: str = "",
    ) -> list[dict[str, Any]]:
        if not instance_id:
            return []
        balance_cents = parse_display_amount_cents(balance_text)
        if balance_cents is None:
            return []

        events: list[dict[str, Any]] = []
        old_batch_id = self.latest_batch_id.get(instance_id, "")
        if (
            batch_id
            and old_batch_id
            and batch_id != old_batch_id
            and self._is_next_round_balance_phase(phase_key)
        ):
            events.extend(self._settle_round(instance_id, old_batch_id, balance_cents, timestamp_ms))
        if batch_id:
            self.latest_batch_id[instance_id] = batch_id
        self.latest_balance_cents[instance_id] = balance_cents

        for record in self.records:
            if record.instance_id != instance_id or record.status != "confirmed":
                continue
            if record.balance_check_status == "verified" or record.expected_after_bet_cents is None:
                continue
            if balance_cents == record.expected_after_bet_cents:
                record.post_bet_balance_cents = balance_cents
                record.balance_check_status = "verified"
                events.append(
                    {
                        "event_type": "post_bet_balance_verified",
                        "instance_id": instance_id,
                        "record": record.to_safe_dict(),
                        "message": self._format_post_bet_message(record),
                    }
                )
        return events

    def account_profit_loss_yuan(self) -> dict[str, float]:
        totals: dict[str, int] = {}
        for (instance_id, _round_key), cents in self._round_profit_loss_cents.items():
            totals[instance_id] = totals.get(instance_id, 0) + cents
        return {instance_id: cents / 100.0 for instance_id, cents in totals.items()}

    def account_confirmed_flow_cents(
        self,
        *,
        instance_ids: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, int]:
        allowed = {str(item) for item in instance_ids} if instance_ids is not None else None
        totals: dict[str, int] = {}
        for record in self.records:
            if allowed is not None and record.instance_id not in allowed:
                continue
            totals[record.instance_id] = totals.get(record.instance_id, 0) + int(record.bet_cents or 0)
        return totals

    def account_confirmed_flow_yuan(
        self,
        *,
        instance_ids: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, float]:
        return {
            instance_id: cents / 100.0
            for instance_id, cents in self.account_confirmed_flow_cents(instance_ids=instance_ids).items()
        }

    def account_unsettled_bet_cents(
        self,
        *,
        instance_ids: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, int]:
        allowed = {str(item) for item in instance_ids} if instance_ids is not None else None
        totals: dict[str, int] = {}
        for record in self.records:
            if record.status != "confirmed":
                continue
            if allowed is not None and record.instance_id not in allowed:
                continue
            totals[record.instance_id] = totals.get(record.instance_id, 0) + int(record.bet_cents or 0)
        return totals

    def account_unsettled_bet_yuan(
        self,
        *,
        instance_ids: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, float]:
        return {
            instance_id: cents / 100.0
            for instance_id, cents in self.account_unsettled_bet_cents(instance_ids=instance_ids).items()
        }

    def real_bet_detection_snapshots(
        self,
        *,
        instance_ids: list[str] | tuple[str, ...] | None = None,
        run_id: str = "",
        session_id: str = "",
        timestamp_ms: int = 0,
    ) -> list[RealBetDetectionSnapshot]:
        allowed = {str(item) for item in instance_ids} if instance_ids is not None else None
        scoped_records = [
            record
            for record in self.records
            if allowed is None or record.instance_id in allowed
        ]
        run_confirmed_cents = sum(int(record.bet_cents or 0) for record in scoped_records)
        grouped: dict[tuple[str, str], list[RuntimeBetRecord]] = {}
        for record in scoped_records:
            round_id = self._detection_round_id(record)
            grouped.setdefault((record.instance_id, round_id), []).append(record)

        snapshots: list[RealBetDetectionSnapshot] = []
        for (instance_id, round_id), records in sorted(grouped.items()):
            records.sort(key=lambda item: (item.confirmation_timestamp_ms or 0, item.record_id))
            confirmed_cents = sum(int(record.bet_cents or 0) for record in records)
            created_at_ms = min((int(record.confirmation_timestamp_ms or 0) for record in records), default=0)
            settled_at_ms = max((int(record.settled_at_ms or 0) for record in records), default=0)
            updated_at_ms = max(
                int(timestamp_ms or 0),
                settled_at_ms,
                max((int(record.confirmation_timestamp_ms or 0) for record in records), default=0),
            )
            settlement_profit_loss_cents = self._detection_profit_loss_cents(instance_id, round_id, records)
            manual_unresolved = any(record.status == "manual_unresolved" for record in records)
            settled = all(record.status == "settled" for record in records)
            status = compute_status_from_amounts(
                confirmed_cents,
                confirmed_cents,
                settled=settled,
                manual_unresolved=manual_unresolved,
            )
            source = RealBetDetectionSource.BALANCE_DELTA.value
            settlement_status = RealBetDetectionSettlementStatus.UNSETTLED.value
            if status == RealBetDetectionStatus.SETTLED.value:
                source = RealBetDetectionSource.LEDGER_SETTLEMENT.value
                settlement_status = RealBetDetectionSettlementStatus.SETTLED.value
            elif status == RealBetDetectionStatus.MANUAL_UNRESOLVED.value:
                source = RealBetDetectionSource.MANUAL_CLEAR.value
                settlement_status = RealBetDetectionSettlementStatus.MANUAL_CLEAR.value

            snapshots.append(
                RealBetDetectionSnapshot(
                    run_id=run_id,
                    session_id=session_id,
                    account_id=instance_id,
                    round_id=round_id,
                    round_id_prefix=parse_round_prefix(round_id),
                    side="unknown",
                    planned_cents=confirmed_cents,
                    confirmed_cents=confirmed_cents,
                    status=status,
                    source=source,
                    created_at_ms=created_at_ms,
                    updated_at_ms=updated_at_ms,
                    settlement_status=settlement_status,
                    settled_at_ms=settled_at_ms,
                    settlement_profit_loss_cents=settlement_profit_loss_cents,
                    run_planned_cents=run_confirmed_cents,
                    run_confirmed_cents=run_confirmed_cents,
                    session_planned_cents=run_confirmed_cents,
                    session_confirmed_cents=run_confirmed_cents,
                )
            )
        return snapshots

    def real_bet_detection_summary(
        self,
        *,
        instance_ids: list[str] | tuple[str, ...] | None = None,
        run_id: str = "",
        session_id: str = "",
        timestamp_ms: int = 0,
    ) -> dict[str, Any]:
        return {
            "snapshots": [
                snapshot.as_dict()
                for snapshot in self.real_bet_detection_snapshots(
                    instance_ids=instance_ids,
                    run_id=run_id,
                    session_id=session_id,
                    timestamp_ms=timestamp_ms,
                )
            ],
            "confirmed_flow_cents_by_account": self.account_confirmed_flow_cents(instance_ids=instance_ids),
            "unsettled_bet_cents_by_account": self.account_unsettled_bet_cents(instance_ids=instance_ids),
            "settled_profit_loss_yuan_by_account": self.account_profit_loss_yuan(),
        }

    def recent_records(self, limit: int = 50) -> list[dict[str, Any]]:
        return [record.to_safe_dict() for record in self.records[-max(1, int(limit)):]]

    def pending_confirmed_summary(
        self,
        *,
        instance_ids: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, dict[str, Any]]:
        allowed = {str(item) for item in instance_ids} if instance_ids is not None else None
        summary: dict[str, dict[str, Any]] = {}
        for record in self.records:
            if record.status != "confirmed":
                continue
            if allowed is not None and record.instance_id not in allowed:
                continue
            item = summary.setdefault(
                record.instance_id,
                {
                    "instance_id": record.instance_id,
                    "record_count": 0,
                    "bet_cents": 0,
                    "oldest_confirmation_ms": record.confirmation_timestamp_ms,
                    "latest_confirmation_ms": record.confirmation_timestamp_ms,
                    "rounds": set(),
                    "latest_batch_id": self.latest_batch_id.get(record.instance_id, ""),
                    "latest_balance_cents": self.latest_balance_cents.get(record.instance_id),
                },
            )
            item["record_count"] = int(item["record_count"]) + 1
            item["bet_cents"] = int(item["bet_cents"]) + int(record.bet_cents or 0)
            timestamp_ms = int(record.confirmation_timestamp_ms or 0)
            if timestamp_ms:
                oldest = int(item.get("oldest_confirmation_ms") or timestamp_ms)
                latest = int(item.get("latest_confirmation_ms") or timestamp_ms)
                item["oldest_confirmation_ms"] = min(oldest, timestamp_ms)
                item["latest_confirmation_ms"] = max(latest, timestamp_ms)
            round_key = record.game_no or record.short_game_no
            if round_key:
                item["rounds"].add(round_key)
        for item in summary.values():
            item["rounds"] = sorted(item["rounds"])
        return summary

    def mark_pending_records_manual_unresolved(
        self,
        *,
        instance_ids: list[str] | tuple[str, ...],
        reason: str,
        timestamp_ms: int = 0,
    ) -> list[dict[str, Any]]:
        allowed = {str(item) for item in instance_ids}
        events: list[dict[str, Any]] = []
        for instance_id in sorted(allowed):
            records = [
                record
                for record in self.records
                if record.instance_id == instance_id and record.status == "confirmed"
            ]
            if not records:
                continue
            records.sort(key=lambda item: item.confirmation_timestamp_ms)
            total_bet_cents = sum(int(record.bet_cents or 0) for record in records)
            for record in records:
                record.status = "manual_unresolved"
                record.balance_check_status = "manual_unresolved"
                record.settled_at_ms = int(timestamp_ms or 0)
                record.profit_loss_cents = None
                record.settlement_balance_cents = None
            last_record = records[-1]
            events.append(
                {
                    "event_type": "manual_settlement_cleared",
                    "instance_id": instance_id,
                    "record": last_record.to_safe_dict(),
                    "message": self._format_manual_clear_message(
                        last_record,
                        total_bet_cents=total_bet_cents,
                        reason=reason,
                        record_count=len(records),
                    ),
                }
            )
        return events

    def reconcile_stale_confirmed_records(
        self,
        *,
        instance_ids: list[str] | tuple[str, ...] | None = None,
        timestamp_ms: int = 0,
    ) -> list[dict[str, Any]]:
        """Settle one stale pending round from the latest visible balance.

        This is a stop/pause recovery path. It intentionally only reconciles
        when an account has exactly one pending full game number and the page
        has already moved to a different batch, so it cannot silently merge
        multiple unresolved rounds.
        """

        allowed = {str(item) for item in instance_ids} if instance_ids is not None else None
        pending_by_account: dict[str, dict[str, list[RuntimeBetRecord]]] = {}
        for record in self.records:
            if record.status != "confirmed" or not record.game_no:
                continue
            if allowed is not None and record.instance_id not in allowed:
                continue
            pending_by_account.setdefault(record.instance_id, {}).setdefault(record.game_no, []).append(record)

        events: list[dict[str, Any]] = []
        for instance_id, records_by_game in pending_by_account.items():
            latest_batch = self.latest_batch_id.get(instance_id, "")
            latest_balance = self.latest_balance_cents.get(instance_id)
            if not latest_batch or latest_balance is None:
                continue
            if latest_batch in records_by_game:
                continue
            stale_items = [
                (game_no, list(candidates))
                for game_no, candidates in records_by_game.items()
                if game_no and game_no != latest_batch
            ]
            if not stale_items:
                continue
            stale_records = [record for _game_no, candidates in stale_items for record in candidates]
            stale_records.sort(key=lambda item: item.confirmation_timestamp_ms)
            first_record = stale_records[0]
            last_record = stale_records[-1]
            before_bet = first_record.amount_before_bet_cents
            if before_bet is None:
                continue
            stale_games = sorted({game_no for game_no, _candidates in stale_items})
            round_key_text = (
                self._round_key_for_batch(stale_games[0], last_record)
                if len(stale_games) == 1
                else f"{self._round_key_for_batch(stale_games[0], last_record)}..{self._round_key_for_batch(stale_games[-1], last_record)}"
            )
            round_key = (instance_id, round_key_text)
            if round_key in self._round_profit_loss_cents:
                continue
            profit_loss = int(latest_balance) - int(before_bet)
            self._round_profit_loss_cents[round_key] = profit_loss
            for record in stale_records:
                record.status = "settled"
                record.settled_at_ms = int(timestamp_ms or 0)
                record.profit_loss_cents = 0
                record.settlement_balance_cents = None
            last_record.settlement_balance_cents = int(latest_balance)
            last_record.profit_loss_cents = profit_loss
            events.append(
                {
                    "event_type": (
                        "round_reconciled_after_stop"
                        if len(stale_games) == 1
                        else "stale_rounds_reconciled_after_stop"
                    ),
                    "instance_id": instance_id,
                    "record": last_record.to_safe_dict(),
                    "message": self._format_reconciliation_message(
                        last_record,
                        latest_batch,
                        round_count=len(stale_games),
                        first_round=stale_games[0],
                        last_round=stale_games[-1],
                    ),
                }
            )
        return events

    def _detection_round_id(self, record: RuntimeBetRecord) -> str:
        return str(record.game_no or record.short_game_no or record.record_id)

    def _detection_profit_loss_cents(
        self,
        instance_id: str,
        round_id: str,
        records: list[RuntimeBetRecord],
    ) -> int | None:
        if not records or not all(record.status == "settled" for record in records):
            return None
        candidates = [
            record.profit_loss_cents
            for record in records
            if record.profit_loss_cents is not None
        ]
        if candidates:
            return sum(int(value) for value in candidates)
        round_key = (instance_id, round_id)
        if round_key in self._round_profit_loss_cents:
            return int(self._round_profit_loss_cents[round_key])
        return None

    def _settle_round(
        self,
        instance_id: str,
        old_batch_id: str,
        settlement_balance_cents: int,
        timestamp_ms: int,
    ) -> list[dict[str, Any]]:
        candidates = [
            record
            for record in self.records
            if record.instance_id == instance_id
            and record.status == "confirmed"
            and self._record_matches_batch(record, old_batch_id)
        ]
        if not candidates:
            return []
        candidates.sort(key=lambda item: item.confirmation_timestamp_ms)
        first_record = candidates[0]
        last_record = candidates[-1]
        before_bet = first_record.amount_before_bet_cents
        if before_bet is None:
            return []

        round_key = (instance_id, self._round_key_for_batch(old_batch_id, last_record))
        if round_key in self._round_profit_loss_cents:
            return []
        profit_loss = settlement_balance_cents - before_bet
        self._round_profit_loss_cents[round_key] = profit_loss
        for record in candidates:
            record.status = "settled"
            record.settlement_balance_cents = settlement_balance_cents
            record.settled_at_ms = timestamp_ms
            record.profit_loss_cents = 0
        last_record.profit_loss_cents = profit_loss
        return [
            {
                "event_type": "round_settled",
                "instance_id": instance_id,
                "record": last_record.to_safe_dict(),
                "message": self._format_settlement_message(last_record),
            }
        ]

    @staticmethod
    def _record_matches_batch(record: RuntimeBetRecord, batch_id: str) -> bool:
        if record.game_no and record.game_no == batch_id:
            return True
        short_batch = _short_round_id(batch_id)
        return bool(short_batch and record.short_game_no and record.short_game_no == short_batch)

    @staticmethod
    def _round_key_for_batch(batch_id: str, record: RuntimeBetRecord) -> str:
        return batch_id or record.game_no or record.short_game_no or record.record_id

    @staticmethod
    def _is_settlement_phase(phase_key: str) -> bool:
        return str(phase_key or "").strip().lower() in {"settling", "payout", "settled"}

    @staticmethod
    def _is_next_round_balance_phase(phase_key: str) -> bool:
        return str(phase_key or "").strip().lower() in {
            "pre_bet",
            "waiting_before_betting",
            "betting_open",
            "betting",
        }

    @staticmethod
    def _format_confirmation_message(record: RuntimeBetRecord) -> str:
        return (
            f"{record.instance_id} 下注确认: 局={record.short_game_no or record.game_no or '-'} "
            f"batch={record.game_no or '-'} "
            f"金额={cents_to_display(record.bet_cents)} "
            f"下注前={cents_to_display(record.amount_before_bet_cents)} "
            f"期望下注后={cents_to_display(record.expected_after_bet_cents)}"
        )

    @staticmethod
    def _format_post_bet_message(record: RuntimeBetRecord) -> str:
        return (
            f"{record.instance_id} 下注后余额已核对: 局={record.short_game_no or record.game_no or '-'} "
            f"batch={record.game_no or '-'} "
            f"余额={cents_to_display(record.post_bet_balance_cents)}"
        )

    @staticmethod
    def _format_reconciliation_message(
        record: RuntimeBetRecord,
        latest_batch: str,
        *,
        round_count: int = 1,
        first_round: str = "",
        last_round: str = "",
    ) -> str:
        range_text = ""
        if round_count > 1:
            range_text = f" stale_rounds={round_count} first_round={first_round or '-'} last_round={last_round or '-'}"
        return (
            f"{record.instance_id} stop-after-settlement-reconciled: "
            f"round={record.short_game_no or record.game_no or '-'} batch={record.game_no or '-'} "
            f"current_round={latest_batch or '-'} "
            f"settlement={cents_to_display(record.settlement_balance_cents)} "
            f"profit_loss={cents_to_display(record.profit_loss_cents)}"
            f"{range_text}"
        )

    @staticmethod
    def _format_manual_clear_message(
        record: RuntimeBetRecord,
        *,
        total_bet_cents: int,
        reason: str,
        record_count: int,
    ) -> str:
        return (
            f"{record.instance_id} 人工确认清账: "
            f"round={record.short_game_no or record.game_no or '-'} batch={record.game_no or '-'} "
            f"records={record_count} pending_bet={cents_to_display(total_bet_cents)} "
            f"reason={reason or '-'}"
        )

    @staticmethod
    def _format_settlement_message(record: RuntimeBetRecord) -> str:
        return (
            f"{record.instance_id} 本局结算: 局={record.short_game_no or record.game_no or '-'} "
            f"batch={record.game_no or '-'} "
            f"结算后={cents_to_display(record.settlement_balance_cents)} "
            f"盈亏={cents_to_display(record.profit_loss_cents)}"
        )

