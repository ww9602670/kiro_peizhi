"""Abstract WebSocket protocol parser slot.

The default parser is conservative and payload-safe. It only extracts public
state hints such as batch id, countdown and balance from obvious JSON/text
fields. Site-specific decryption or binary decoding belongs in a separate
subclass injected into the worker.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ParsedWSState:
    batch_id: str = ""
    room_id: str = ""
    countdown: int | None = None
    balance: str = ""
    action: int | None = None
    timed: int | None = None
    selected_bet: int | None = None
    pending_chip_cents: int | None = None
    current_load_type: int | None = None
    is_can_betting: bool | None = None
    confidence: float = 0.0
    source: str = "ws_parser"
    safe_summary: dict[str, Any] = field(default_factory=dict)

    @property
    def has_state(self) -> bool:
        return bool(
            self.batch_id
            or self.room_id
            or self.countdown is not None
            or self.balance
            or self.action is not None
            or self.timed is not None
            or self.current_load_type is not None
            or self.is_can_betting is not None
        )

    def to_worker_update(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.batch_id:
            result["batch_id"] = self.batch_id
        if self.room_id:
            result["room_id"] = self.room_id
        if self.countdown is not None:
            result["countdown"] = self.countdown
        if self.balance:
            result["balance"] = self.balance
        runtime_summary = {
            "ws_runtime_action": self.action,
            "ws_runtime_timed": self.timed,
            "ws_runtime_selected_bet": self.selected_bet,
            "ws_runtime_pending_chip_cents": self.pending_chip_cents,
            "ws_runtime_current_load_type": self.current_load_type,
            "ws_runtime_is_can_betting": self.is_can_betting,
            "ws_room_id": self.room_id,
        }
        runtime_summary = {key: value for key, value in runtime_summary.items() if value is not None}
        if runtime_summary:
            result.setdefault("safe_summary", {}).update(runtime_summary)
        if self.safe_summary:
            result.setdefault("safe_summary", {}).update(dict(self.safe_summary))
        result["confidence"] = self.confidence
        result["source"] = self.source
        return result


class AbstractWSParser(ABC):
    """Parser interface used by WebSocket listeners."""

    @abstractmethod
    def parse_frame(self, payload: Any, *, direction: str, url: str = "") -> ParsedWSState | None:
        """Return parsed state from one WebSocket frame."""


class HeuristicWSParser(AbstractWSParser):
    """Payload-safe JSON/text parser for unknown WebSocket protocols."""

    BATCH_PATTERNS = (
        re.compile(r"([0-9]{2,}-[0-9]{6,}-[0-9]{6,}-[0-9]+)"),
        re.compile(r"(?:batchId|batch_id|issueNo|issue|roundId|gameNo)[\"':=\s]+([0-9A-Za-z\-]{8,80})", re.I),
    )
    COUNTDOWN_PATTERN = re.compile(
        r"(?:countdown|countdownSeconds|leftSeconds|timeLeft|remainSeconds|betSeconds)[\"':=\s]+(\d{1,2})",
        re.I,
    )
    BALANCE_PATTERN = re.compile(
        r"(?:balance|wallet|credit|money|availableBalance)[\"':=\s]+(\d+(?:\.\d{1,2})?)",
        re.I,
    )

    def parse_frame(self, payload: Any, *, direction: str, url: str = "") -> ParsedWSState:
        parsed = self._parse_json(payload)
        if parsed is not None:
            state = self._parse_json_state(parsed)
            if state.has_state:
                return state
        text = self._payload_text(payload)
        if not text:
            return ParsedWSState(source="ws_parser_empty")
        return self._parse_text_state(text, direction=direction)

    def _parse_json_state(self, payload: Any) -> ParsedWSState:
        result: dict[str, Any] = {}

        def visit(value: Any, depth: int = 0) -> None:
            if depth > 6:
                return
            if isinstance(value, dict):
                lowered = {str(key).lower(): item for key, item in value.items()}
                for key in ("batch_id", "batchid", "issue", "issueno", "roundid", "gameno", "gamesn"):
                    if key in lowered and lowered[key] not in (None, ""):
                        result.setdefault("batch_id", str(lowered[key]))
                
                # Support room/table identification
                for key in ("roomid", "roomno", "room", "tableid", "tableno", "table", "desk", "cid"):
                    if key in lowered and lowered[key] not in (None, ""):
                        result.setdefault("room_id", str(lowered[key]))

                for key in ("countdown", "countdownseconds", "leftseconds", "timeleft", "remainseconds", "betseconds", "timed", "time"):
                    if key in lowered:
                        try:
                            result.setdefault("countdown", int(float(lowered[key])))
                        except Exception:
                            pass
                for key in ("action", "actionflag"):
                    if key in lowered:
                        result.setdefault("action", lowered[key])
                for key in ("timed", "time", "countdown", "leftseconds", "timeleft", "remainseconds", "betseconds"):
                    if key in lowered:
                        result.setdefault("timed", lowered[key])
                for key in ("selectedbet",):
                    if key in lowered:
                        result.setdefault("selected_bet", lowered[key])
                for key in ("choumaallcount", "pendingchip", "pendingbet"):
                    if key in lowered:
                        result.setdefault("pending_chip_cents", lowered[key])
                for key in ("currentloadtype", "loadtype"):
                    if key in lowered:
                        result.setdefault("current_load_type", lowered[key])
                for key in ("iscanbetting", "canbet", "canbetting"):
                    if key in lowered:
                        result.setdefault("is_can_betting", lowered[key])
                for key in ("balance", "wallet", "credit", "money", "availablebalance"):
                    if key in lowered and lowered[key] not in (None, ""):
                        result.setdefault("balance", str(lowered[key]))
                for child in value.values():
                    visit(child, depth + 1)
            elif isinstance(value, list):
                for child in value[:80]:
                    visit(child, depth + 1)

        visit(payload)
        return self._normalize(result, source="ws_parser_json", confidence=0.95)

    def _parse_text_state(self, text: str, *, direction: str) -> ParsedWSState:
        result: dict[str, Any] = {}
        for pattern in self.BATCH_PATTERNS:
            match = pattern.search(text)
            if match:
                result["batch_id"] = match.group(1)
                break
        countdown = self.COUNTDOWN_PATTERN.search(text)
        if countdown:
            result["countdown"] = int(countdown.group(1))
        balance = self.BALANCE_PATTERN.search(text)
        if balance:
            result["balance"] = balance.group(1)
        return self._normalize(
            result,
            source=f"ws_parser_text_{direction}",
            confidence=0.65,
        )

    @staticmethod
    def _normalize(result: dict[str, Any], *, source: str, confidence: float) -> ParsedWSState:
        batch_id = str(result.get("batch_id", "")).strip()
        if batch_id and len(batch_id) < 8:
            batch_id = ""
        countdown_value: int | None = None
        if "countdown" in result:
            try:
                value = int(result["countdown"])
                if 0 <= value <= 90:
                    countdown_value = value
            except Exception:
                countdown_value = None
        balance = str(result.get("balance", "")).strip()
        if balance and not re.fullmatch(r"\d+(?:\.\d{1,2})?", balance):
            balance = ""
        action = HeuristicWSParser._safe_int(result.get("action"))
        timed = HeuristicWSParser._safe_int(result.get("timed"))
        selected_bet = HeuristicWSParser._safe_int(result.get("selected_bet"))
        pending_chip_cents = HeuristicWSParser._safe_int(result.get("pending_chip_cents"))
        current_load_type = HeuristicWSParser._safe_int(result.get("current_load_type"))
        is_can_betting = HeuristicWSParser._safe_bool(result.get("is_can_betting"))
        has_any = bool(
            batch_id
            or countdown_value is not None
            or balance
            or action is not None
            or timed is not None
            or current_load_type is not None
            or is_can_betting is not None
        )
        return ParsedWSState(
            batch_id=batch_id[:80],
            countdown=countdown_value,
            balance=balance,
            action=action,
            timed=timed,
            selected_bet=selected_bet,
            pending_chip_cents=pending_chip_cents,
            current_load_type=current_load_type,
            is_can_betting=is_can_betting,
            confidence=confidence if has_any else 0.0,
            source=source,
        )

    @staticmethod
    def _parse_json(payload: Any) -> Any | None:
        if isinstance(payload, (dict, list)):
            return payload
        text = HeuristicWSParser._payload_text(payload).strip()
        if not text.startswith(("{", "[")):
            return None
        try:
            return json.loads(text)
        except Exception:
            return None

    @staticmethod
    def _payload_text(payload: Any) -> str:
        if isinstance(payload, bytes):
            return payload.decode("utf-8", errors="ignore")
        if isinstance(payload, (dict, list)):
            return json.dumps(payload, ensure_ascii=False)
        return str(payload or "")

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        try:
            if value is None or value == "":
                return None
            return int(float(value))
        except Exception:
            return None

    @staticmethod
    def _safe_bool(value: Any) -> bool | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes"}:
                return True
            if lowered in {"false", "0", "no"}:
                return False
        return None


DEFAULT_WS_PARSER = HeuristicWSParser()

