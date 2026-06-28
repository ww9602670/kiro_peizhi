from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import logging
import multiprocessing as mp
import queue
import re
import time
import traceback
from dataclasses import asdict, dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence
from urllib.parse import urlsplit

from bet_desktop.browser.frontend_state_probe import (
    FRONTEND_BOUND_ROOM_SCAN_JS,
    FRONTEND_OBJECT_SCAN_JS,
    FRONTEND_STATE_PROBE_JS,
    parse_frontend_state_events,
    FrontendStateEvent,
)
from bet_desktop.browser.login_flow import fill_login_form_when_visible
from bet_desktop.browser.game_launch_url import (
    capture_local_storage_for_candidate,
    enter_baccarat_hall_room,
    extract_game_launch_url_from_page,
    install_local_storage_init_script,
    read_baccarat_load_state,
    resolve_baccarat_hall_entry_buttons,
    resolve_baccarat_hall_room_entry_candidates,
)
from bet_desktop.models.state_temporal_guard import TemporalStateSnapshot, now_ms
from bet_desktop.page.real_detector import RealPageStateDetector
from bet_desktop.parsers.ws_protocol_parser import AbstractWSParser, DEFAULT_WS_PARSER, ParsedWSState
from bet_desktop.browser.live_runtime_state import (
    install_canvas_text_probe,
    read_live_runtime_snapshot,
    LiveRuntimeSnapshot,
    read_live_label_runtime_snapshot,
    LiveLabelRuntimeSnapshot,
    read_canvas_text_snapshot,
    CanvasTextSnapshot,
)
from bet_desktop.core.runtime_bet_ledger import extract_bet_confirmation_from_frontend_event
from bet_desktop.runtime_state_v2.shadow import build_runtime_v2_shadow_decision

try:
    from PIL import Image, ImageChops, ImageDraw
except Exception:  # pragma: no cover - diagnostic screenshots are optional.
    Image = None
    ImageChops = None
    ImageDraw = None

# Constants for worker operations
LOG_MAX_MESSAGE_LENGTH = 1000
DEFAULT_POLL_INTERVAL_MS = 120
MAX_QUEUE_RETRIES = 3
ROOM_CONFLICT_SWITCH_SAMPLES = 2
ROOM_ABSENT_CLEAR_SAMPLES = 3
BaccaratCoordinateSize = tuple[int, int]
BACCARAT_COORDINATE_BASE_SIZE: BaccaratCoordinateSize = (960, 620)
BACCARAT_COORDINATE_TOLERANCE_PX = 2
DEFAULT_PROFILE_ROOT = Path(__file__).resolve().parents[1] / "artifacts" / "profiles"

WorkerEventType = Literal["state", "health", "error", "log", "ws_raw", "audit", "state_v2_shadow"]

@dataclass(frozen=True)
class ClusterWorkerConfig:
    """Configuration for a cluster process worker instance."""
    instance_id: str
    login_url: str = ""
    target_url: str = ""
    username: str = ""
    password: str = ""
    auto_fill_login: bool = False
    headless: bool = False
    proxy: dict[str, str] = field(default_factory=dict)
    viewport_width: int = 960
    viewport_height: int = 620
    user_data_dir: str = ""
    browser_channel: str = "chrome"
    state_poll_interval_ms: int = 120
    queue_maxsize: int = 16
    ws_parser_path: str = ""
    login_timeout_seconds: int = 120
    enable_frontend_probe: bool = True
    enable_runtime_scan: bool = True
    enable_canvas_probe: bool = True
    runtime_pipeline: str = "legacy"
    runtime_shadow_interval_ms: int = 1000
    debug_port: int = 0

@dataclass(frozen=True)
class ClusterWorkerEvent:
    """Event emitted by a worker process to the main controller."""
    event_type: WorkerEventType
    instance_id: str
    timestamp_ms: int
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _RuntimeState:
    """Internal state tracker for the worker process.
    
    This class maintains the current 'truth' about the game state, reconciling
    updates from WebSocket frames, frontend JS probes, and visual runtime snapshots.
    """
    def __init__(self, instance_id: str) -> None:
        self.instance_id = instance_id
        self.batch_id = ""
        self.room_id = ""
        self.room_label = ""
        self.limit_label = ""
        self.phase_text = ""
        self.countdown = -1
        self.countdown_anchor_ms = 0
        self.balance = ""
        self.source = "initial"
        self.ws_connected = False
        self.page_alive = True
        self.frame_id = 0
        
        # Timing metrics
        self.last_ws_ms = 0
        self.last_frontend_ms = 0
        self.last_runtime_ms = 0
        self.last_page_ms = now_ms()
        
        # Confidence and diagnostics
        self.confidence = 0.0
        self.safe_summary: dict[str, Any] = {}
        self.runtime_state: dict[str, Any] = {}
        self.runtime_action: int | None = None
        self.runtime_timed: int | None = None
        self.runtime_selected_bet: int | None = None
        self.runtime_pending_chip_cents: int | None = None
        self.runtime_current_load_type: int | None = None
        self.runtime_is_can_betting: bool | None = None
        self.runtime_authority = 0
        self.runtime_authority_ms = 0
        
        # Frontend lock state (to prevent context switching between rooms)
        self.locked_frontend_batch_id = ""
        self.locked_frontend_batch_rank = (0, 0, 0, 0)
        self.frontend_locked_context_path = ""
        self.locked_room_id = ""
        self.locked_room_label = ""
        self.room_conflict_identity = ""
        self.room_conflict_seen_count = 0
        self.room_absent_seen_count = 0

    def update(
        self,
        *,
        batch_id: str = "",
        room_id: str = "",
        room_label: str = "",
        limit_label: str = "",
        phase_text: str = "",
        countdown: int | None = None,
        balance: str = "",
        source: str,
        confidence: float | None = None,
        safe_summary: dict[str, Any] | None = None,
        runtime_state: dict[str, Any] | None = None,
    ) -> TemporalStateSnapshot:
        """Update the internal state with new information.
        
        Returns a snapshot of the updated state.
        """
        original_batch_id = batch_id
        incoming_batch_rank = _batch_rank_from_id(batch_id)
        same_round_as_locked = _is_same_round_batch(batch_id, self.locked_frontend_batch_id)
        same_round_as_current = _is_same_round_batch(batch_id, self.batch_id)
        stale_batch_update = bool(
            incoming_batch_rank != (0, 0, 0, 0)
            and self.locked_frontend_batch_rank
            and not same_round_as_locked
            and incoming_batch_rank < self.locked_frontend_batch_rank
        )
        runtime_signal_summary = dict(safe_summary) if safe_summary is not None else None
        incoming_has_runtime_signal = bool(
            countdown is not None
            or phase_text
            or runtime_state
            or _safe_summary_has_live_display_signal(runtime_signal_summary)
            or _safe_summary_has_structured_phase_signal(runtime_signal_summary)
        )
        incoming_authority = _runtime_update_authority(source, runtime_signal_summary, runtime_state)
        lower_authority_phase_supplement = _lower_authority_phase_supplement_allowed(
            self,
            runtime_signal_summary,
            runtime_state,
            room_id=room_id,
            room_label=room_label,
            batch_id=batch_id,
        )
        current_batch_rank = _batch_rank_from_id(self.batch_id)
        is_newer_display_batch = bool(
            incoming_batch_rank != (0, 0, 0, 0)
            and (
                current_batch_rank == (0, 0, 0, 0)
                or (incoming_batch_rank > current_batch_rank and not same_round_as_current)
            )
        )
        is_same_runtime_batch = not batch_id or not self.batch_id or batch_id == self.batch_id or same_round_as_current

        if stale_batch_update:
            batch_id = ""
            phase_text = ""
            countdown = None
            runtime_state = None
            incoming_has_runtime_signal = False
            source = f"{source}_stale_ignored"
            if safe_summary is not None:
                safe_summary = _drop_volatile_safe_summary(dict(safe_summary))
                safe_summary["ignored_stale_game_no"] = original_batch_id
        elif batch_id and not _is_trusted_display_batch_id(
            batch_id,
            room_id=room_id,
            room_label=room_label,
            limit_label=limit_label,
            phase_text=phase_text,
            safe_summary=safe_summary,
        ):
            phase_text = ""
            countdown = None
            runtime_state = None
            incoming_has_runtime_signal = False
            source = f"{source}_internal_ignored"
            if safe_summary is not None:
                safe_summary = _drop_volatile_safe_summary(dict(safe_summary))
                safe_summary["internal_game_no"] = batch_id
            batch_id = ""
        elif (
            incoming_has_runtime_signal
            and is_same_runtime_batch
            and not is_newer_display_batch
            and self.runtime_authority
            and incoming_authority < self.runtime_authority
            and not lower_authority_phase_supplement
            and now_ms() - self.runtime_authority_ms <= _RUNTIME_AUTHORITY_HOLD_MS
            and not (countdown is not None and countdown >= 0 and self.countdown < 0)
        ):
            phase_text = ""
            countdown = None
            runtime_state = None
            incoming_has_runtime_signal = False
            source = f"{source}_low_authority_ignored"
            if safe_summary is not None:
                safe_summary = _drop_volatile_safe_summary(dict(safe_summary))
                safe_summary["ignored_low_authority_source"] = source
                safe_summary["ignored_runtime_authority"] = incoming_authority
                safe_summary["active_runtime_authority"] = self.runtime_authority

        if batch_id and same_round_as_current:
            batch_id = self.batch_id

        if batch_id and batch_id != self.batch_id:
            self.batch_id = batch_id
            self.phase_text = ""
            self.runtime_state = {}
            self.runtime_action = None
            self.runtime_timed = None
            self.runtime_current_load_type = None
            self.runtime_is_can_betting = None
            if countdown is None:
                self.countdown = -1
                self.countdown_anchor_ms = 0
        
        if room_id and room_id != self.room_id:
            self.room_id = room_id
            
        if room_label and room_label != self.room_label:
            self.room_label = room_label
            
        normalized_limit = _normalized_runtime_limit_label(limit_label)
        if normalized_limit and normalized_limit != self.limit_label:
            if not _should_ignore_limit_update(self.limit_label, normalized_limit):
                self.limit_label = normalized_limit
            
        cleaned_summary = _clean_safe_summary(dict(safe_summary), self.limit_label) if safe_summary is not None else None
        incoming_runtime_action = _as_int_or_none(
            _first_present(
                cleaned_summary or {},
                ("frontend_runtime_action", "ws_runtime_action", "runtime_action"),
            )
        )
        incoming_short_batch_id = str((cleaned_summary or {}).get("frontend_short_batch_id") or "")
        current_short_batch_id = str(self.safe_summary.get("frontend_short_batch_id") or "")
        has_structured_phase_signal = _safe_summary_has_structured_phase_signal(cleaned_summary) or _runtime_state_has_structured_phase_signal(runtime_state)
        if phase_text and phase_text != self.phase_text:
            self.phase_text = phase_text
        elif not phase_text and has_structured_phase_signal:
            self.phase_text = ""

        if countdown is not None and countdown >= 0:
            same_phase_refresh = bool(
                self.countdown_anchor_ms
                and self.countdown == countdown
                and incoming_runtime_action is not None
                and self.runtime_action == incoming_runtime_action
                and (not incoming_short_batch_id or incoming_short_batch_id == current_short_batch_id)
            )
            if not same_phase_refresh:
                self.countdown = countdown
                self.countdown_anchor_ms = now_ms()
        elif countdown is not None and countdown < 0:
            self.countdown = -1
            self.countdown_anchor_ms = 0
            
        if balance:
            self.balance = balance
        
        self.source = source
        if confidence is not None:
            self.confidence = float(confidence)
            
        if cleaned_summary is not None:
            source_text = source.lower()
            if (
                _safe_summary_has_live_display_signal(cleaned_summary)
                or runtime_state
                or "frontend_event_type" in cleaned_summary
                or source_text.startswith(("canvas_text_runtime", "label_runtime", "page_runtime", "ws"))
            ):
                self.safe_summary = _drop_volatile_safe_summary(self.safe_summary)
            self.safe_summary.update(cleaned_summary)
            _apply_runtime_summary_to_state(self, cleaned_summary)
            
        if runtime_state:
            self.runtime_state = dict(runtime_state)
            _apply_runtime_snapshot_to_state(self, self.runtime_state)

        if incoming_has_runtime_signal and not (
            lower_authority_phase_supplement
            and self.runtime_authority
            and incoming_authority < self.runtime_authority
        ):
            self.runtime_authority = incoming_authority
            self.runtime_authority_ms = now_ms()
            
        self.frame_id += 1
        return self.snapshot()

    def snapshot(self) -> TemporalStateSnapshot:
        """Create a point-in-time snapshot of the current state."""
        # Compute exact countdown based on drift
        exact_countdown = self.countdown
        if self.countdown >= 0 and self.countdown_anchor_ms > 0:
            elapsed = int((now_ms() - self.countdown_anchor_ms) / 1000)
            exact_countdown = max(0, self.countdown - elapsed)

        summary = {
            **self.safe_summary,
            "room_id": self.room_id,
            "room_label": self.room_label,
            "limit_label": self.limit_label,
            "phase_text": self.phase_text,
            "last_ws_age_ms": max(0, now_ms() - self.last_ws_ms) if self.last_ws_ms else -1,
            "last_frontend_age_ms": max(0, now_ms() - self.last_frontend_ms) if self.last_frontend_ms else -1,
            "frontend_locked_context_path": self.frontend_locked_context_path,
            "locked_room_id": self.locked_room_id,
            "locked_room_label": self.locked_room_label,
            "runtime_authority": self.runtime_authority,
        }
        runtime_fields = {
            "runtime_action": self.runtime_action,
            "runtime_timed": self.runtime_timed,
            "runtime_selected_bet": self.runtime_selected_bet,
            "runtime_pending_chip_cents": self.runtime_pending_chip_cents,
            "runtime_current_load_type": self.runtime_current_load_type,
            "runtime_is_can_betting": self.runtime_is_can_betting,
        }
        summary.update({key: value for key, value in runtime_fields.items() if value is not None})
        if self.runtime_state:
            summary["runtime_state"] = self.runtime_state

        return TemporalStateSnapshot(
            instance_id=self.instance_id,
            batch_id=self.batch_id,
            exact_countdown=exact_countdown,
            ocr_balance=self.balance,
            timestamp_captured_ms=now_ms(),
            source=self.source,
            ws_connected=self.ws_connected,
            page_alive=self.page_alive,
            frame_id=self.frame_id,
            confidence=self.confidence,
            safe_summary=summary,
        )


def _extract_state_from_payload(payload: Any) -> dict[str, Any]:
    """Helper to extract common game state fields from varying JSON payloads."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return {}
    
    if not isinstance(payload, dict):
        return {}
    
    # Heuristic 1: "data" wrapper common in many platforms
    data = payload.get("data")
    if isinstance(data, dict):
        return {
            "batch_id": str(data.get("issueNo", "")),
            "countdown": data.get("leftSeconds"),
            "balance": str(data.get("wallet", "")),
        }
    
    # Heuristic 2: Direct keys
    return {
        "batch_id": str(payload.get("batchId", payload.get("batch_id", ""))),
        "countdown": payload.get("countdown"),
        "balance": str(payload.get("balance", "")),
    }


def _is_bindable_frontend_source(context_path: str) -> bool:
    """Check if a JS object path is a valid source for room binding."""
    if not context_path:
        return False
    path = context_path.lower()
    # application._prevScene.$Component.8.gameList0.$dataProvider._source.0
    # application._currentScene.gameList.0.0
    if "gamelist" in path or "_source" in path or "dataprovider" in path or "currentscene" in path:
        return True
    return False


def _is_room_identity_frontend_source(context_path: str) -> bool:
    path = str(context_path or "").lower()
    if "_prevscene" in path:
        return False
    if _is_bindable_frontend_source(path):
        return True
    return False


def _is_room_list_cache_context(context_path: str) -> bool:
    path = str(context_path or "").lower()
    return bool(
        "_prevscene" in path
        and ("gamelist" in path or "_source" in path or "dataprovider" in path)
    )


def _is_current_room_list_runtime_context(context_path: str) -> bool:
    path = str(context_path or "").lower()
    if "$dataprovider" in path or "._source" in path:
        return False
    return bool(
        ("_prevscene" in path or "_currentscene" in path)
        and re.search(r"(?:^|\.)gamelist\.\d+\.\d+(?:\.|$)", path)
    )


def _short_batch_from_display_batch(value: str) -> str:
    parts = str(value or "").strip().split("-")
    return parts[2] if len(parts) >= 4 else ""


def _normalized_runtime_limit_label(value: object) -> str:
    text = str(value or "").strip().replace(" ", "")
    if not text:
        return ""
    match = re.fullmatch(r"(\d{1,6}(?:\.\d+)?)\s*[-~]\s*(\d{1,6}(?:\.\d+)?)", text)
    if not match:
        return ""
    low = float(match.group(1))
    high = float(match.group(2))
    if low <= 0 or high < low:
        return ""
    low_text = f"{low:g}"
    high_text = f"{high:g}"
    return f"{low_text}-{high_text}"


def _is_broad_default_limit(value: str) -> bool:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)", str(value or ""))
    if not match:
        return False
    low = float(match.group(1))
    high = float(match.group(2))
    return low <= 1 and high >= 100000


def _should_ignore_limit_update(current: str, candidate: str) -> bool:
    return bool(current and candidate and _is_broad_default_limit(candidate) and current != candidate)


def _clean_safe_summary(summary: dict[str, Any], current_limit: str) -> dict[str, Any]:
    cleaned = dict(summary)
    for key, value in list(cleaned.items()):
        if key == "limit_label" or key.endswith("_limit_label"):
            normalized = _normalized_runtime_limit_label(value)
            if not normalized or _should_ignore_limit_update(current_limit, normalized):
                cleaned[key] = ""
            else:
                cleaned[key] = normalized
    return cleaned


_VOLATILE_SAFE_SUMMARY_KEYS = {
    "frontend_runtime_action",
    "frontend_runtime_timed",
    "frontend_runtime_selected_bet",
    "frontend_runtime_pending_chip_cents",
    "frontend_runtime_current_load_type",
    "frontend_runtime_is_can_betting",
    "frontend_countdown",
    "frontend_runtime_ignored_reason",
    "frontend_ignored_countdown",
    "frontend_ignored_runtime_action",
    "frontend_ignored_runtime_timed",
    "frontend_ignored_runtime_current_load_type",
    "frontend_ignored_runtime_is_can_betting",
    "ws_runtime_action",
    "ws_runtime_timed",
    "ws_runtime_selected_bet",
    "ws_runtime_pending_chip_cents",
    "ws_runtime_current_load_type",
    "ws_runtime_is_can_betting",
    "runtime_action",
    "runtime_timed",
    "runtime_selected_bet",
    "runtime_pending_chip_cents",
    "runtime_current_load_type",
    "runtime_is_can_betting",
    "runtime_countdown",
    "canvas_phase_text",
    "label_phase_text",
    "frontend_phase_text",
    "runtime_phase",
    "runtime_phase_label",
    "runtime_betting_open",
    "runtime_memory_game_no",
    "frontend_context_path",
    "frontend_matched_paths",
    "frontend_event_type",
}

_IGNORED_RUNTIME_DIAGNOSTIC_KEYS = {
    "frontend_runtime_ignored_reason",
    "frontend_ignored_countdown",
    "frontend_ignored_runtime_action",
    "frontend_ignored_runtime_timed",
    "frontend_ignored_runtime_current_load_type",
    "frontend_ignored_runtime_is_can_betting",
}

_RUNTIME_SIGNAL_SAFE_SUMMARY_KEYS = _VOLATILE_SAFE_SUMMARY_KEYS - _IGNORED_RUNTIME_DIAGNOSTIC_KEYS - {
    "frontend_context_path",
    "frontend_matched_paths",
    "frontend_event_type",
}

_STRUCTURED_PHASE_KEYS = {
    "frontend_runtime_action",
    "frontend_runtime_current_load_type",
    "frontend_runtime_is_can_betting",
    "ws_runtime_action",
    "ws_runtime_current_load_type",
    "ws_runtime_is_can_betting",
    "runtime_action",
    "runtime_current_load_type",
    "runtime_is_can_betting",
}


def _drop_volatile_safe_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in summary.items() if key not in _VOLATILE_SAFE_SUMMARY_KEYS}


def _safe_summary_has_live_display_signal(summary: dict[str, Any] | None) -> bool:
    if not summary:
        return False
    for key in _RUNTIME_SIGNAL_SAFE_SUMMARY_KEYS:
        if key in summary and summary.get(key) not in (None, ""):
            return True
    return False


def _safe_summary_has_structured_phase_signal(summary: dict[str, Any] | None) -> bool:
    if not summary:
        return False
    return any(key in summary and summary.get(key) is not None for key in _STRUCTURED_PHASE_KEYS)


_RUNTIME_AUTHORITY_HOLD_MS = 1600


def _summary_text(summary: Mapping[str, Any] | None) -> str:
    if not summary:
        return ""
    matched = summary.get("frontend_matched_paths")
    if isinstance(matched, Sequence) and not isinstance(matched, (str, bytes)):
        matched_text = " ".join(str(item) for item in matched)
    else:
        matched_text = str(matched or "")
    parts = [
        str(summary.get("frontend_context_path") or ""),
        str(summary.get("frontend_event_type") or ""),
        str(summary.get("runtime_context_path") or ""),
        matched_text,
    ]
    return " ".join(parts)


def _summary_is_generic_action_only(summary: Mapping[str, Any] | None) -> bool:
    if not summary:
        return False
    if summary.get("frontend_event_type") != "json_parse_state":
        return False
    has_action = summary.get("frontend_runtime_action") is not None
    has_timing = any(
        summary.get(key) is not None
        for key in (
            "frontend_countdown",
            "frontend_runtime_timed",
            "frontend_runtime_current_load_type",
            "frontend_runtime_is_can_betting",
        )
    )
    text = _summary_text(summary).lower()
    has_countdown_path = "countdown" in text or "timed" in text or "actionflag" in text
    return bool(has_action and not has_timing and not has_countdown_path)


def _runtime_update_authority(
    source: str,
    summary: Mapping[str, Any] | None,
    runtime_state: Mapping[str, Any] | None,
) -> int:
    source_text = str(source or "").lower()
    combined = f"{source_text} {_summary_text(summary)}".lower()
    if source_text.startswith("canvas_text_runtime"):
        score = 95
    elif source_text.startswith("label_runtime_resolved"):
        score = 90
    elif source_text.startswith("page_runtime"):
        score = 68
    elif source_text.startswith("frontend_room_list_runtime"):
        score = 98
    elif source_text.startswith("frontend_bound_room"):
        score = 55
    elif source_text.startswith("frontend_object_scan") or source_text.startswith("frontend_timed_context"):
        score = 50
    elif source_text.startswith("frontend_json_parse"):
        score = 45
    elif source_text.startswith("ws"):
        score = 42
    else:
        score = 35

    if "currentscene" in combined or "runningscene" in combined:
        score += 15
    if "countdown" in combined or "timed" in combined or (summary and summary.get("frontend_countdown") is not None):
        score += 8
    if "actionflag" in combined:
        score += 15
    if summary and summary.get("frontend_limit_label") and not _is_broad_default_limit(str(summary.get("frontend_limit_label"))):
        score += 6
    if runtime_state:
        score += 5
    if "_prevscene" in combined and not source_text.startswith("frontend_room_list_runtime"):
        score = min(score, 55)
    if _summary_is_generic_action_only(summary):
        score = min(score, 32)
    return max(0, min(100, score))


def _runtime_state_has_structured_phase_signal(runtime_state: dict[str, Any] | None) -> bool:
    if not isinstance(runtime_state, dict):
        return False
    frame = runtime_state.get("frame")
    if not isinstance(frame, dict):
        return False
    return any(frame.get(key) is not None for key in ("action", "current_load_type", "is_can_betting"))


def _summary_first(summary: Mapping[str, Any] | None, *keys: str) -> Any:
    if not summary:
        return None
    for key in keys:
        value = summary.get(key)
        if value not in (None, ""):
            return value
    return None


def _summary_room_id(summary: Mapping[str, Any] | None) -> str:
    return str(
        _summary_first(
            summary,
            "frontend_room_id",
            "runtime_room_id",
            "ws_room_id",
            "room_id",
            "locked_room_id",
        )
        or ""
    ).strip()


def _summary_room_label(summary: Mapping[str, Any] | None) -> str:
    return _normalized_room_label(
        _summary_first(
            summary,
            "frontend_room_label",
            "runtime_room_label",
            "ws_room_label",
            "room_label",
            "locked_room_label",
        )
    )


def _phase_signal_room_matches_current(
    state: _RuntimeState,
    summary: Mapping[str, Any] | None,
    *,
    room_id: object = "",
    room_label: object = "",
) -> bool:
    incoming_id = str(room_id or _summary_room_id(summary) or "").strip()
    incoming_label = _normalized_room_label(room_label) or _summary_room_label(summary) or _room_label_hint_from_id(incoming_id)
    current_id = str(state.locked_room_id or state.room_id or "").strip()
    current_label = (
        _normalized_room_label(state.locked_room_label)
        or _normalized_room_label(state.room_label)
        or _room_label_hint_from_id(current_id)
    )
    if current_label and incoming_label and incoming_label != current_label:
        return False
    if current_id and incoming_id and incoming_id != current_id:
        if not (current_label and incoming_label and current_label == incoming_label):
            return False
    return bool(incoming_label or incoming_id or current_label or current_id)


def _summary_short_batch_ids(summary: Mapping[str, Any] | None) -> set[str]:
    values = {
        str(
            _summary_first(
                summary,
                "frontend_short_batch_id",
                "runtime_short_batch_id",
                "ws_short_batch_id",
            )
            or ""
        ).strip()
    }
    for key in (
        "frontend_batch_id",
        "runtime_memory_game_no",
        "display_game_no",
        "round_id",
        "canvas_game_no",
        "label_game_no",
    ):
        text = str((summary.get(key) if summary else "") or "").strip()
        if text:
            values.add(_short_batch_from_display_batch(text) or text)
    return {value for value in values if value}


def _phase_signal_batch_matches_current(
    state: _RuntimeState,
    summary: Mapping[str, Any] | None,
    *,
    batch_id: object = "",
) -> bool:
    incoming_batch = str(batch_id or "").strip()
    if incoming_batch and state.batch_id and incoming_batch == state.batch_id:
        return True
    incoming_shorts = _summary_short_batch_ids(summary)
    if incoming_batch:
        incoming_shorts.add(_short_batch_from_display_batch(incoming_batch) or incoming_batch)
    current_shorts = {_short_batch_from_display_batch(state.batch_id)}
    current_shorts.update(_summary_short_batch_ids(state.safe_summary))
    current_shorts = {value for value in current_shorts if value}
    if incoming_shorts and current_shorts and incoming_shorts.intersection(current_shorts):
        return True
    return bool(not state.batch_id and incoming_shorts)


def _lower_authority_phase_supplement_allowed(
    state: _RuntimeState,
    summary: Mapping[str, Any] | None,
    runtime_state: Mapping[str, Any] | None,
    *,
    room_id: object = "",
    room_label: object = "",
    batch_id: object = "",
) -> bool:
    if not (
        _safe_summary_has_structured_phase_signal(dict(summary or {}))
        or _runtime_state_has_structured_phase_signal(dict(runtime_state or {}))
    ):
        return False
    if not _phase_signal_room_matches_current(state, summary, room_id=room_id, room_label=room_label):
        return False
    return _phase_signal_batch_matches_current(state, summary, batch_id=batch_id)


def _first_present(mapping: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


def _as_int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except Exception:
        return None


def _as_bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return None


def _apply_runtime_summary_to_state(state: _RuntimeState, summary: Mapping[str, Any]) -> None:
    action = _as_int_or_none(_first_present(summary, ("frontend_runtime_action", "ws_runtime_action", "runtime_action")))
    timed = _as_int_or_none(_first_present(summary, ("frontend_runtime_timed", "ws_runtime_timed", "runtime_timed")))
    selected_bet = _as_int_or_none(
        _first_present(summary, ("frontend_runtime_selected_bet", "ws_runtime_selected_bet", "runtime_selected_bet"))
    )
    pending_chip = _as_int_or_none(
        _first_present(
            summary,
            ("frontend_runtime_pending_chip_cents", "ws_runtime_pending_chip_cents", "runtime_pending_chip_cents"),
        )
    )
    current_load_type = _as_int_or_none(
        _first_present(
            summary,
            ("frontend_runtime_current_load_type", "ws_runtime_current_load_type", "runtime_current_load_type"),
        )
    )
    is_can_betting = _as_bool_or_none(
        _first_present(
            summary,
            ("frontend_runtime_is_can_betting", "ws_runtime_is_can_betting", "runtime_is_can_betting"),
        )
    )
    if action is not None:
        state.runtime_action = action
    if timed is not None:
        state.runtime_timed = timed
    if selected_bet is not None:
        state.runtime_selected_bet = selected_bet
    if pending_chip is not None:
        state.runtime_pending_chip_cents = pending_chip
    if current_load_type is not None:
        state.runtime_current_load_type = current_load_type
    if is_can_betting is not None:
        state.runtime_is_can_betting = is_can_betting


def _apply_runtime_snapshot_to_state(state: _RuntimeState, runtime_state: Mapping[str, Any]) -> None:
    frame = runtime_state.get("frame")
    if not isinstance(frame, Mapping):
        return
    _apply_runtime_summary_to_state(
        state,
        {
            "runtime_action": frame.get("action"),
            "runtime_timed": frame.get("timed"),
            "runtime_selected_bet": frame.get("selected_bet"),
            "runtime_pending_chip_cents": frame.get("pending_chip_cents"),
            "runtime_current_load_type": frame.get("current_load_type"),
            "runtime_is_can_betting": frame.get("is_can_betting"),
        },
    )


_FULL_BATCH_ID_RE = re.compile(r"(?<![\d-])\d{2,}-\d{6,}-\d{6,}-\d+(?!\d)")
_DISPLAY_BATCH_TAIL_MAX_WIDTH = 5
_DISPLAY_BATCH_ID_RE = re.compile(
    rf"(?<![\d-])\d{{2,}}-\d{{6,}}-\d{{6,}}-\d{{1,{_DISPLAY_BATCH_TAIL_MAX_WIDTH}}}(?!\d)"
)


def _batch_rank_from_id(value: str) -> tuple[int, int, int, int]:
    text = str(value or "").strip()
    if not _FULL_BATCH_ID_RE.fullmatch(text):
        return (0, 0, 0, 0)
    parsed: list[int] = []
    for part in text.split("-")[:4]:
        try:
            parsed.append(int(part))
        except Exception:
            parsed.append(0)
    # The first segment is a game/table family code on some platforms, not a
    # monotonic round counter. Rank by the changing round segments first so a
    # platform-specific family code cannot lock out the visible baccarat round.
    return (parsed[1], parsed[2], parsed[3], parsed[0])


def _batch_round_key_from_id(value: str) -> tuple[int, int, int]:
    text = str(value or "").strip()
    if not _FULL_BATCH_ID_RE.fullmatch(text):
        return (0, 0, 0)
    parsed: list[int] = []
    for part in text.split("-")[:3]:
        try:
            parsed.append(int(part))
        except Exception:
            parsed.append(0)
    return (parsed[0], parsed[1], parsed[2])


def _is_same_round_batch(left: str, right: str) -> bool:
    left_key = _batch_round_key_from_id(left)
    return left_key != (0, 0, 0) and left_key == _batch_round_key_from_id(right)


def _is_display_batch_id(value: str) -> bool:
    return bool(_DISPLAY_BATCH_ID_RE.fullmatch(str(value or "").strip()))


def _display_batch_id_or_empty(value: str) -> str:
    text = str(value or "").strip()
    return text if _is_display_batch_id(text) else ""


def _batch_tail_width(value: str) -> int:
    parts = str(value or "").strip().split("-")
    return len(parts[3]) if len(parts) >= 4 else 0


def _has_batch_corrobating_context(
    *,
    room_id: str = "",
    room_label: str = "",
    limit_label: str = "",
    phase_text: str = "",
    safe_summary: Mapping[str, Any] | None = None,
) -> bool:
    if room_id or room_label or _normalized_runtime_limit_label(limit_label) or phase_text:
        return True
    summary = safe_summary or {}
    for key in (
        "frontend_room_id",
        "frontend_room_label",
        "frontend_limit_label",
        "frontend_phase_text",
        "runtime_room_id",
        "runtime_room_label",
        "runtime_limit_label",
        "runtime_phase",
        "runtime_phase_label",
        "label_room_label",
        "label_limit_label",
        "label_phase_text",
        "label_game_no",
        "canvas_room_label",
        "canvas_limit_label",
        "canvas_phase_text",
        "canvas_game_no",
    ):
        if summary.get(key) not in (None, ""):
            return True
    return False


def _is_trusted_display_batch_id(
    value: str,
    *,
    room_id: str = "",
    room_label: str = "",
    limit_label: str = "",
    phase_text: str = "",
    safe_summary: Mapping[str, Any] | None = None,
) -> bool:
    if not _is_display_batch_id(value):
        return False
    if _batch_tail_width(value) <= _DISPLAY_BATCH_TAIL_MAX_WIDTH:
        return True
    return _has_batch_corrobating_context(
        room_id=room_id,
        room_label=room_label,
        limit_label=limit_label,
        phase_text=phase_text,
        safe_summary=safe_summary,
    )


def _is_untrusted_zero_countdown(
    state: _RuntimeState,
    *,
    countdown: int | None,
    phase_text: str,
    display_game_no: str,
    raw_game_no: str,
    room_label: str = "",
    limit_label: str = "",
) -> bool:
    if countdown != 0:
        return False
    trusted_display_game_no = bool(
        display_game_no
        and (
            _batch_tail_width(display_game_no) <= _DISPLAY_BATCH_TAIL_MAX_WIDTH
            or _has_batch_corrobating_context(
                room_label=room_label,
                limit_label=limit_label,
                phase_text=phase_text,
            )
        )
    )
    if phase_text or trusted_display_game_no:
        return False
    if state.countdown <= 2:
        return False
    return bool(raw_game_no)


def _is_stale_locked_batch(state: _RuntimeState, batch_id: str) -> bool:
    if _is_same_round_batch(batch_id, state.locked_frontend_batch_id):
        return False
    rank = _batch_rank_from_id(batch_id)
    return bool(rank != (0, 0, 0, 0) and state.locked_frontend_batch_rank and rank < state.locked_frontend_batch_rank)


def _is_stale_current_batch(state: _RuntimeState, batch_id: str) -> bool:
    if _is_same_round_batch(batch_id, state.batch_id):
        return False
    rank = _batch_rank_from_id(batch_id)
    current_rank = _batch_rank_from_id(state.batch_id)
    return bool(rank != (0, 0, 0, 0) and current_rank != (0, 0, 0, 0) and rank < current_rank)


def _promote_visible_batch_lock(state: _RuntimeState, batch_id: str) -> None:
    rank = _batch_rank_from_id(batch_id)
    if rank != (0, 0, 0, 0) and (not state.locked_frontend_batch_id or rank > state.locked_frontend_batch_rank):
        state.locked_frontend_batch_id = batch_id
        state.locked_frontend_batch_rank = rank


def _normalized_room_label(value: object) -> str:
    text = str(value or "").strip().upper()
    if re.fullmatch(r"T\d{3,}", text):
        return text
    return ""


def _room_label_hint_from_id(value: object) -> str:
    text = str(value or "").strip()
    if text == "9101":
        return "T001"
    if re.fullmatch(r"\d{6,12}", text):
        return f"T{int(text[-3:]):03d}"
    return ""


def _room_identity_conflicts(state: _RuntimeState, *, room_id: object = "", room_label: object = "") -> bool:
    if not state.locked_room_id and not state.locked_room_label:
        return False
    incoming_id = str(room_id or "").strip()
    incoming_label = _normalized_room_label(room_label) or _room_label_hint_from_id(incoming_id)
    if incoming_label and state.locked_room_label and incoming_label != state.locked_room_label:
        return True
    if incoming_label and state.locked_room_label:
        return False
    if incoming_id and state.locked_room_id and incoming_id != state.locked_room_id:
        return True
    return False


def _promote_room_lock(state: _RuntimeState, *, room_id: object = "", room_label: object = "") -> None:
    incoming_id = str(room_id or "").strip()
    incoming_label = _normalized_room_label(room_label) or _room_label_hint_from_id(incoming_id)
    if not incoming_id and not incoming_label:
        return
    if _room_identity_conflicts(state, room_id=incoming_id, room_label=incoming_label):
        return
    if incoming_id and not state.locked_room_id:
        state.locked_room_id = incoming_id
    if incoming_label and not state.locked_room_label:
        state.locked_room_label = incoming_label


def _reset_room_observation_counters(state: _RuntimeState) -> None:
    state.room_conflict_identity = ""
    state.room_conflict_seen_count = 0
    state.room_absent_seen_count = 0


def _reset_runtime_room_fields(state: _RuntimeState) -> None:
    state.batch_id = ""
    state.phase_text = ""
    state.countdown = -1
    state.countdown_anchor_ms = 0
    state.locked_frontend_batch_id = ""
    state.locked_frontend_batch_rank = (0, 0, 0, 0)
    state.frontend_locked_context_path = ""
    state.runtime_state = {}
    state.runtime_action = None
    state.runtime_timed = None
    state.runtime_selected_bet = None
    state.runtime_pending_chip_cents = None
    state.runtime_current_load_type = None
    state.runtime_is_can_betting = None
    state.runtime_authority = 0
    state.runtime_authority_ms = 0


def _room_identity_key(*, room_id: object = "", room_label: object = "") -> str:
    incoming_id = str(room_id or "").strip()
    incoming_label = _normalized_room_label(room_label) or _room_label_hint_from_id(incoming_id)
    return incoming_label or incoming_id


def _room_conflict_confirmed(state: _RuntimeState, *, room_id: object = "", room_label: object = "") -> bool:
    key = _room_identity_key(room_id=room_id, room_label=room_label)
    if not key.strip("|"):
        state.room_conflict_identity = ""
        state.room_conflict_seen_count = 0
        return False
    if key == state.room_conflict_identity:
        state.room_conflict_seen_count += 1
    else:
        state.room_conflict_identity = key
        state.room_conflict_seen_count = 1
    return state.room_conflict_seen_count >= ROOM_CONFLICT_SWITCH_SAMPLES


def _switch_room_lock_for_observed_room(
    state: _RuntimeState,
    *,
    room_id: object = "",
    room_label: object = "",
    source: str,
) -> bool:
    incoming_id = str(room_id or "").strip()
    incoming_label = _normalized_room_label(room_label) or _room_label_hint_from_id(incoming_id)
    if not incoming_id and not incoming_label:
        return False
    previous_room_id = state.locked_room_id or state.room_id
    previous_room_label = state.locked_room_label or state.room_label
    state.locked_room_id = incoming_id
    state.locked_room_label = incoming_label
    state.room_id = incoming_id
    state.room_label = incoming_label
    _reset_runtime_room_fields(state)
    state.source = source
    state.safe_summary = {
        key: value
        for key, value in _drop_volatile_safe_summary(state.safe_summary).items()
        if key not in _ROOM_SWITCH_SAFE_SUMMARY_KEYS
    }
    state.safe_summary.update(
        {
            "observed_room_switch": True,
            "previous_room_id": previous_room_id,
            "previous_room_label": previous_room_label,
            "room_id": incoming_id,
            "room_label": incoming_label,
            "locked_room_id": incoming_id,
            "locked_room_label": incoming_label,
        }
    )
    _reset_room_observation_counters(state)
    state.frame_id += 1
    return True


_ROOM_SWITCH_SAFE_SUMMARY_KEYS = {
    "frontend_room_label",
    "frontend_room_id",
    "frontend_limit_label",
    "frontend_short_batch_id",
    "runtime_room_id",
    "runtime_room_label",
    "runtime_limit_label",
    "runtime_memory_game_no",
    "runtime_memory_game_no_ignored",
    "runtime_countdown",
    "runtime_coordinates",
    "runtime_viewport",
    "runtime_layout_confidence",
    "runtime_game_visible",
    "label_game_no",
    "label_internal_game_no",
    "label_countdown",
    "label_room_label",
    "label_limit_label",
    "label_matches",
    "canvas_game_no",
    "canvas_internal_game_no",
    "canvas_countdown",
    "canvas_room_label",
    "canvas_limit_label",
    "canvas_records",
    "room_id",
    "room_label",
    "phase_text",
    "runtime_state",
    "locked_room_id",
    "locked_room_label",
}


def _switch_room_lock_for_entry(
    state: _RuntimeState,
    *,
    room_index: int,
    room_id: object = "",
    room_label: object = "",
) -> bool:
    incoming_id = str(room_id or "").strip()
    incoming_label = _normalized_room_label(room_label) or _room_label_hint_from_id(incoming_id)
    if not incoming_label and 1 <= room_index <= 4:
        incoming_label = f"T{room_index:03d}"
    if not incoming_id and not incoming_label:
        return False

    state.locked_room_id = incoming_id
    state.locked_room_label = incoming_label
    state.room_id = incoming_id
    state.room_label = incoming_label
    _reset_runtime_room_fields(state)
    state.balance = ""
    state.source = "room_entry_lock_switch"
    _reset_room_observation_counters(state)
    state.safe_summary = {
        key: value
        for key, value in _drop_volatile_safe_summary(state.safe_summary).items()
        if key not in _ROOM_SWITCH_SAFE_SUMMARY_KEYS
    }
    state.safe_summary.update(
        {
            "room_entry_expected_room_index": room_index,
            "room_entry_expected_room_id": incoming_id,
            "room_entry_expected_room_label": incoming_label,
        }
    )
    state.frame_id += 1
    return True


def _clear_room_state_for_hall(
    state: _RuntimeState,
    *,
    balance: object = "",
    ready_summary: Mapping[str, Any] | None = None,
) -> TemporalStateSnapshot:
    state.batch_id = ""
    state.room_id = ""
    state.room_label = ""
    state.limit_label = ""
    _reset_runtime_room_fields(state)
    balance_text = str(balance or "").strip()
    if balance_text:
        state.balance = balance_text
    state.source = "hall_idle"
    state.locked_room_id = ""
    state.locked_room_label = ""
    _reset_room_observation_counters(state)
    summary = dict(ready_summary or {})
    summary.update(
        {
            "hall_idle": True,
            "room_id": "",
            "room_label": "",
            "limit_label": "",
            "phase_text": "",
        }
    )
    state.safe_summary = summary
    state.frame_id += 1
    return state.snapshot()


def _clear_room_state_if_room_absent(
    state: _RuntimeState,
    *,
    balance: object = "",
    ready_summary: Mapping[str, Any] | None = None,
) -> TemporalStateSnapshot | None:
    has_room_state = bool(
        state.batch_id
        or state.room_id
        or state.room_label
        or state.locked_room_id
        or state.locked_room_label
    )
    if not has_room_state:
        state.room_absent_seen_count = 0
        return None
    state.room_absent_seen_count += 1
    if state.room_absent_seen_count < ROOM_ABSENT_CLEAR_SAMPLES:
        return None
    summary = dict(ready_summary or {})
    summary["room_absent_clear"] = True
    return _clear_room_state_for_hall(state, balance=balance, ready_summary=summary)


def _runtime_probe_has_live_game_state(
    *,
    runtime_snapshot: LiveRuntimeSnapshot | None = None,
    label_snapshot: LiveLabelRuntimeSnapshot | None = None,
    canvas_snapshot: CanvasTextSnapshot | None = None,
) -> bool:
    for snapshot in (runtime_snapshot, label_snapshot, canvas_snapshot):
        if snapshot is None:
            continue
        if str(getattr(snapshot, "game_no", "") or "").strip():
            return True
        if getattr(snapshot, "countdown_seconds", None) is not None:
            return True
        if str(getattr(snapshot, "phase_text", "") or "").strip():
            return True
    return False


def _limit_label_from_cents(low: object, high: object) -> str:
    low_value = _as_int_or_none(low)
    high_value = _as_int_or_none(high)
    if low_value is None or high_value is None or low_value <= 0 or high_value < low_value:
        return ""
    if low_value >= 100 and high_value >= 100:
        low_display = low_value // 100 if low_value % 100 == 0 else low_value / 100
        high_display = high_value // 100 if high_value % 100 == 0 else high_value / 100
        return f"{low_display:g}-{high_display:g}"
    return f"{low_value}-{high_value}"


def _frontend_event_conflicts_room_lock(state: _RuntimeState, event: FrontendStateEvent) -> bool:
    return _room_identity_conflicts(state, room_id=event.room_id, room_label=event.room_label)


def _frontend_event_lacks_expected_room_identity(state: _RuntimeState, event: FrontendStateEvent) -> bool:
    if not (state.locked_room_id or state.locked_room_label):
        return False
    if not state.safe_summary.get("room_entry_expected_room_label"):
        return False
    if event.room_id or event.room_label:
        return False
    if not (_frontend_event_updates_runtime(event) or event.balance):
        return False
    return True


def _frontend_event_updates_runtime(event: FrontendStateEvent) -> bool:
    return bool(
        event.batch_id
        or event.countdown is not None
        or event.action is not None
        or event.timed is not None
        or event.selected_bet is not None
        or event.pending_chip_cents is not None
        or event.current_load_type is not None
        or event.is_can_betting is not None
        or event.real_time is not None
        or event.phase_text
    )


def _frontend_event_updates_runtime_values(event: FrontendStateEvent) -> bool:
    if _is_room_list_cache_context(event.context_path):
        return False
    return bool(
        event.countdown is not None
        or event.action is not None
        or event.timed is not None
        or event.selected_bet is not None
        or event.pending_chip_cents is not None
        or event.current_load_type is not None
        or event.is_can_betting is not None
        or event.real_time is not None
        or event.phase_text
    )


def _frontend_event_text(event: FrontendStateEvent) -> str:
    return " ".join([event.context_path, event.event_type, *[str(path) for path in event.matched_paths]]).lower()


def _frontend_event_is_generic_action_only(event: FrontendStateEvent) -> bool:
    if event.event_type != "json_parse_state" or event.action is None:
        return False
    if event.countdown is not None or event.timed is not None or event.current_load_type is not None:
        return False
    if event.is_can_betting is not None:
        return False
    text = _frontend_event_text(event)
    return not ("countdown" in text or "timed" in text or "actionflag" in text)


def _frontend_event_is_unbound_root_runtime_noise(event: FrontendStateEvent) -> bool:
    """Root JSON.parse runtime hints are cross-room queues unless they carry binding context."""
    if str(event.context_path or "").strip().lower() != "json.parse":
        return False
    if event.batch_id_is_full:
        return False
    if event.room_label or event.room_id or event.limit_label or event.phase_text:
        return False
    return bool(event.batch_id or event.countdown is not None or event.action is not None)


def _frontend_event_is_passive_analytics_profile(event: FrontendStateEvent) -> bool:
    path = str(event.context_path or "").strip().lower()
    if path != "application.currentscene.analyticsdata":
        return False
    if _frontend_event_updates_runtime_values(event):
        return False
    return bool(event.batch_id or event.room_id or event.room_label or event.limit_label or event.balance)


def _frontend_event_runtime_authority(event: FrontendStateEvent) -> int:
    if not _frontend_event_updates_runtime_values(event):
        return 0
    if _is_room_list_cache_context(event.context_path):
        return 0
    text = _frontend_event_text(event)
    if event.event_type == "bound_room_object":
        score = 55
    elif event.event_type in {"object_scan", "timed_context"}:
        score = 50
    elif event.event_type == "json_parse_state":
        score = 45
    else:
        score = 35
    if "currentscene" in text or "runningscene" in text:
        score += 15
    if event.countdown is not None:
        score += 8
    if event.action is not None:
        score += 5
    if "actionflag" in text:
        score += 15
    if event.limit_label and not _is_broad_default_limit(event.limit_label):
        score += 6
    if "_prevscene" in text:
        score = min(score, 55)
    if _frontend_event_is_generic_action_only(event):
        score = min(score, 32)
    return max(0, min(100, score))


def _room_list_runtime_matches_current_state(state: _RuntimeState, event: FrontendStateEvent) -> bool:
    if event.event_type not in {"object_scan", "bound_room_object"}:
        return False
    if not _is_current_room_list_runtime_context(event.context_path):
        return False
    if not _frontend_event_updates_runtime(event):
        return False
    if not (event.room_label or event.room_id):
        return False
    if _frontend_event_conflicts_room_lock(state, event):
        return False
    incoming_label = _normalized_room_label(event.room_label) or _room_label_hint_from_id(event.room_id)
    state_label = (
        state.locked_room_label
        or _normalized_room_label(state.room_label)
        or _room_label_hint_from_id(state.room_id)
        or _room_label_hint_from_id(state.locked_room_id)
    )
    if state_label and incoming_label and incoming_label != state_label:
        return False
    current_short = _short_batch_from_display_batch(state.batch_id)
    event_short = event.short_batch_id or _short_batch_from_display_batch(event.batch_id)
    if current_short:
        return bool(event_short and event_short == current_short)
    if "_currentscene" in str(event.context_path or "").lower():
        return bool(incoming_label)
    return bool(event.batch_id_is_full and incoming_label)


def _lock_frontend_source_if_needed(state: _RuntimeState, event: FrontendStateEvent) -> None:
    """Bind the runtime state to a specific frontend context if it provides a full batch ID."""
    rank = _batch_rank_from_id(event.batch_id)
    if _is_room_identity_frontend_source(event.context_path):
        _promote_room_lock(state, room_id=event.room_id, room_label=event.room_label)
    if (
        event.batch_id_is_full
        and event.batch_id
        and rank != (0, 0, 0, 0)
        and not _is_room_list_cache_context(event.context_path)
        and not _frontend_event_conflicts_room_lock(state, event)
    ):
        if not state.locked_frontend_batch_id or rank >= state.locked_frontend_batch_rank:
            state.locked_frontend_batch_id = event.batch_id
            state.locked_frontend_batch_rank = rank
            if _is_bindable_frontend_source(event.context_path) and _frontend_event_updates_runtime_values(event):
                state.frontend_locked_context_path = event.context_path


def _select_frontend_state_event(state: _RuntimeState, events: list[FrontendStateEvent]) -> FrontendStateEvent | None:
    """Pick the most authoritative event from a list of candidates."""
    if not events:
        return None
    
    # Filter by room and batch locks.
    candidates = [
        e
        for e in events
        if not _frontend_event_conflicts_room_lock(state, e)
        and not _frontend_event_lacks_expected_room_identity(state, e)
        and not _frontend_event_is_unbound_root_runtime_noise(e)
    ]
    if state.locked_frontend_batch_id:
        # If we have a lock, only allow full batches that are >= current lock
        candidates = [
            e
            for e in candidates
            if (
                not e.batch_id_is_full
                or e.batch_rank >= state.locked_frontend_batch_rank
                or _is_same_round_batch(e.batch_id, state.locked_frontend_batch_id)
            )
        ]
        if not candidates:
            return None

    # Prefer full batch events
    full_events = [e for e in candidates if e.batch_id_is_full]
    if full_events:
        # Prefer the one with highest rank (newest game)
        return max(full_events, key=lambda e: (e.batch_rank, e.timestamp_ms))
    
    # Fallback to newest short batch or generic state
    state_events = [e for e in candidates if e.has_state]
    if state_events:
        return max(state_events, key=lambda e: (e.confidence, e.timestamp_ms))
        
    return None


def _select_frontend_state_events(state: _RuntimeState, events: list[FrontendStateEvent]) -> list[FrontendStateEvent]:
    """Filter and order a batch of events for sequential processing."""
    if not events:
        return []
        
    # 1. Stricter filtering based on lock
    valid = []
    for e in events:
        if _frontend_event_is_unbound_root_runtime_noise(e):
            continue
        if _frontend_event_conflicts_room_lock(state, e):
            continue
        if _frontend_event_lacks_expected_room_identity(state, e):
            continue
        # If we have a bindable lock, we are very picky
        if state.frontend_locked_context_path:
            is_newer_full_batch = bool(
                e.batch_id_is_full
                and e.batch_rank != (0, 0, 0, 0)
                and not _is_same_round_batch(e.batch_id, state.locked_frontend_batch_id)
                and e.batch_rank > state.locked_frontend_batch_rank
            )
            if not _is_bindable_frontend_source(e.context_path) and not is_newer_full_batch:
                # Generic runtime hints without room identity are only accepted when they
                # move the full game number forward.
                continue
            if (
                e.batch_id_is_full
                and e.batch_rank < state.locked_frontend_batch_rank
                and not _is_same_round_batch(e.batch_id, state.locked_frontend_batch_id)
            ):
                # Reject stale batches
                continue
        elif state.locked_frontend_batch_id:
            if (
                e.batch_id_is_full
                and e.batch_rank < state.locked_frontend_batch_rank
                and not _is_same_round_batch(e.batch_id, state.locked_frontend_batch_id)
            ):
                continue
        valid.append(e)
    
    if not valid:
        return []

    bindable_runtime = [
        e for e in valid if _is_bindable_frontend_source(e.context_path) and _frontend_event_updates_runtime_values(e)
    ]
    if bindable_runtime:
        best_bindable_rank = max((e.batch_rank for e in bindable_runtime), default=(0, 0, 0, 0))
        valid = [
            e
            for e in valid
            if _is_bindable_frontend_source(e.context_path)
            or not _frontend_event_updates_runtime_values(e)
            or (e.batch_id_is_full and e.batch_rank > best_bindable_rank)
        ]

    # 2. Group and deduplicate
    best_by_group = {}
    for e in valid:
        key = (e.batch_id, e.context_path)
        if key not in best_by_group or e.timestamp_ms > best_by_group[key].timestamp_ms:
            best_by_group[key] = e
            
    final_candidates = list(best_by_group.values())
    passthrough: list[FrontendStateEvent] = []
    runtime_by_batch: dict[str, FrontendStateEvent] = {}
    for e in final_candidates:
        if e.batch_id_is_full and _frontend_event_updates_runtime_values(e):
            current = runtime_by_batch.get(e.batch_id)
            if current is None or (
                _frontend_event_runtime_authority(e),
                e.confidence,
                e.timestamp_ms,
            ) > (
                _frontend_event_runtime_authority(current),
                current.confidence,
                current.timestamp_ms,
            ):
                runtime_by_batch[e.batch_id] = e
        else:
            passthrough.append(e)
    final_candidates = passthrough + list(runtime_by_batch.values())

    # 3. Sort logic:
    # We want bindable sources to come FIRST (at index 0)
    # But among sources of the same "bindability", we want CHRONOLOGICAL order.
    # So: (not bindable, timestamp) sorted ASCENDING.
    def sort_key(e: FrontendStateEvent):
        return (not _is_bindable_frontend_source(e.context_path), e.timestamp_ms)

    return sorted(final_candidates, key=sort_key)


def _update_from_frontend_state(state: _RuntimeState, event: FrontendStateEvent) -> TemporalStateSnapshot:
    """Apply a frontend event to the runtime state."""
    state.last_frontend_ms = event.timestamp_ms
    if _frontend_event_is_unbound_root_runtime_noise(event):
        return state.snapshot()
    if _frontend_event_conflicts_room_lock(state, event):
        trusted_room_runtime = bool(
            event.batch_id_is_full
            and _frontend_event_updates_runtime_values(event)
            and (
                _is_current_room_list_runtime_context(event.context_path)
                or (
                    _is_bindable_frontend_source(event.context_path)
                    and not _is_room_list_cache_context(event.context_path)
                )
            )
        )
        if not trusted_room_runtime or not _room_conflict_confirmed(
            state,
            room_id=event.room_id,
            room_label=event.room_label,
        ):
            return state.snapshot()
        _switch_room_lock_for_observed_room(
            state,
            room_id=event.room_id,
            room_label=event.room_label,
            source="frontend_room_switch",
        )
    if _frontend_event_lacks_expected_room_identity(state, event):
        return state.snapshot()
    if _frontend_event_is_passive_analytics_profile(event):
        summary = {
            "frontend_room_label": event.room_label,
            "frontend_room_id": event.room_id,
            "frontend_limit_label": event.limit_label,
            "frontend_context_path": event.context_path,
            "frontend_matched_paths": event.matched_paths,
            "frontend_event_type": event.event_type,
            "frontend_runtime_ignored_reason": "passive_analytics_profile",
        }
        if event.batch_id:
            summary["frontend_ignored_game_no"] = event.batch_id
        summary = {k: v for k, v in summary.items() if v is not None}
        return state.update(
            room_id=event.room_id,
            room_label=event.room_label,
            limit_label=event.limit_label,
            balance=event.balance,
            source="frontend_analytics_profile",
            confidence=event.confidence,
            safe_summary=summary,
        )
    was_newer_full_batch = bool(
        event.batch_id_is_full
        and event.batch_rank != (0, 0, 0, 0)
        and not _is_same_round_batch(event.batch_id, state.locked_frontend_batch_id)
        and event.batch_rank > state.locked_frontend_batch_rank
    )
    _lock_frontend_source_if_needed(state, event)
    
    # Logic for batch locking and rejection
    if event.batch_id_is_full:
        if _is_stale_locked_batch(state, event.batch_id):
            # Reject stale full batch
            return state.snapshot()

    # Authority check: once bound to a table object, generic JSON must not overwrite game runtime fields.
    if state.frontend_locked_context_path and not _is_bindable_frontend_source(event.context_path):
        if _frontend_event_updates_runtime(event) and not was_newer_full_batch:
            return state.snapshot()

    source_tag = "frontend_probe_resolved"
    if event.event_type == "json_parse_state":
        source_tag = "frontend_json_parse_resolved"
    elif event.event_type == "timed_context":
        source_tag = "frontend_timed_context_resolved"
    elif event.event_type == "object_scan":
        source_tag = "frontend_object_scan_resolved"
    elif event.event_type == "bound_room_object":
        source_tag = "frontend_bound_room"
    elif "gameList" in event.context_path or "room" in event.context_path:
        # Heuristic for room profile
        if not event.countdown and not event.action:
             source_tag = "frontend_room_profile"

    ignore_json_runtime = bool(event.event_type == "json_parse_state" and _frontend_event_updates_runtime_values(event))
    event_countdown = event.countdown
    event_phase_text = event.phase_text
    event_action = event.action
    event_timed = event.timed
    event_selected_bet = event.selected_bet
    event_pending_chip_cents = event.pending_chip_cents
    event_current_load_type = event.current_load_type
    event_is_can_betting = event.is_can_betting
    if ignore_json_runtime:
        source_tag = "frontend_json_parse_runtime_ignored"
        event_countdown = (
            -1
            if (
                event.batch_id_is_full
                and event.batch_id
                and event.batch_id != state.batch_id
                and not _is_same_round_batch(event.batch_id, state.batch_id)
            )
            else None
        )
        event_phase_text = ""
        event_action = None
        event_timed = None
        event_selected_bet = None
        event_pending_chip_cents = None
        event_current_load_type = None
        event_is_can_betting = None

    room_list_runtime_allowed = _room_list_runtime_matches_current_state(state, event)
    if _is_room_list_cache_context(event.context_path) and not room_list_runtime_allowed:
        if re.search(r"(?:^|\.)gamelist\.\d+$", str(event.context_path or "").lower()):
            return state.snapshot()
        summary = {
            "frontend_room_label": event.room_label,
            "frontend_room_id": event.room_id,
            "frontend_limit_label": event.limit_label,
            "frontend_context_path": event.context_path,
            "frontend_matched_paths": event.matched_paths,
            "frontend_event_type": event.event_type,
            "frontend_runtime_ignored_reason": "room_list_cache",
        }
        if event.countdown is not None:
            summary["frontend_ignored_countdown"] = event.countdown
        if event.action is not None:
            summary["frontend_ignored_runtime_action"] = event.action
        if event.timed is not None:
            summary["frontend_ignored_runtime_timed"] = event.timed
        if event.current_load_type is not None:
            summary["frontend_ignored_runtime_current_load_type"] = event.current_load_type
        if event.is_can_betting is not None:
            summary["frontend_ignored_runtime_is_can_betting"] = event.is_can_betting
        summary = {k: v for k, v in summary.items() if v is not None}
        return state.update(
            room_id=event.room_id,
            room_label=event.room_label,
            limit_label=event.limit_label,
            balance=event.balance,
            source="frontend_room_cache_profile",
            confidence=event.confidence,
            safe_summary=summary,
        )
    if room_list_runtime_allowed:
        source_tag = "frontend_room_list_runtime"

    # Special case: room profile should not update batch/countdown if it's just metadata
    if source_tag == "frontend_room_profile":
        return state.update(
            room_id=event.room_id,
            room_label=event.room_label,
            limit_label=event.limit_label,
            source=source_tag,
            confidence=event.confidence,
            safe_summary={
                "frontend_room_label": event.room_label,
                "frontend_room_id": event.room_id,
                "frontend_limit_label": event.limit_label,
                "frontend_context_path": event.context_path,
                "frontend_matched_paths": event.matched_paths,
                "frontend_event_type": event.event_type,
            }
        )

    # Prepare summary from frontend metadata
    summary = {
        "frontend_runtime_action": event_action,
        "frontend_runtime_timed": event_timed,
        "frontend_runtime_selected_bet": event_selected_bet,
        "frontend_runtime_pending_chip_cents": event_pending_chip_cents,
        "frontend_runtime_current_load_type": event_current_load_type,
        "frontend_runtime_is_can_betting": event_is_can_betting,
        "frontend_countdown": event_countdown,
        "frontend_short_batch_id": event.short_batch_id,
        "frontend_room_label": event.room_label,
        "frontend_room_id": event.room_id,
        "frontend_limit_label": event.limit_label,
        "frontend_phase_text": event_phase_text,
        "frontend_locked_context_path": state.frontend_locked_context_path,
        "frontend_context_path": event.context_path,
        "frontend_matched_paths": event.matched_paths,
        "frontend_event_type": event.event_type,
    }
    if ignore_json_runtime:
        summary.update(
            {
                "frontend_runtime_ignored_reason": "json_parse_queue",
                "frontend_ignored_countdown": event.countdown,
                "frontend_ignored_runtime_action": event.action,
                "frontend_ignored_runtime_timed": event.timed,
                "frontend_ignored_runtime_current_load_type": event.current_load_type,
                "frontend_ignored_runtime_is_can_betting": event.is_can_betting,
            }
        )
    # Filter None values
    summary = {k: v for k, v in summary.items() if v is not None}

    # Authority rule for limits: if we have a room profile limit, prefer it
    if state.source == "frontend_room_profile" and event.limit_label:
        if "1-500000" in event.limit_label or "1-100000" in event.limit_label:
            # Likely a default limit, ignore it if we already have a profile limit
            summary.pop("frontend_limit_label", None)
            event_limit = ""
        else:
            event_limit = event.limit_label
    else:
        event_limit = event.limit_label

    return state.update(
        batch_id=event.batch_id if event.batch_id_is_full else "",
        room_id=event.room_id,
        room_label=event.room_label,
        limit_label=event_limit,
        phase_text=event_phase_text,
        countdown=event_countdown,
        balance=event.balance,
        source=source_tag,
        confidence=event.confidence,
        safe_summary=summary
    )


def _update_from_label_runtime_state(state: _RuntimeState, snapshot: LiveLabelRuntimeSnapshot) -> TemporalStateSnapshot:
    """Apply a label-based runtime snapshot to the state."""
    state.last_runtime_ms = snapshot.timestamp_ms
    if snapshot.confidence < 0.35:
        return state.snapshot()
    
    # Check if this update should override existing page lock
    label_game_no = str(snapshot.game_no or "")
    display_game_no = _display_batch_id_or_empty(label_game_no)
    label_visible_context = bool(
        snapshot.frame.render_signal
        or snapshot.countdown_seconds is not None
        or snapshot.room_label
        or snapshot.limit_label
        or snapshot.phase_text
    )
    if (
        display_game_no
        and _batch_tail_width(display_game_no) > _DISPLAY_BATCH_TAIL_MAX_WIDTH
        and not label_visible_context
        and not _has_batch_corrobating_context(
            room_label=snapshot.room_label,
            limit_label=snapshot.limit_label,
            phase_text=snapshot.phase_text,
        )
    ):
        display_game_no = ""
    stale_label_game_no = ""
    if display_game_no:
        if _is_stale_locked_batch(state, display_game_no) or _is_stale_current_batch(state, display_game_no):
            stale_label_game_no = display_game_no
            display_game_no = ""
        else:
            _promote_visible_batch_lock(state, display_game_no)
    if snapshot.room_label and _room_identity_conflicts(state, room_label=snapshot.room_label):
        if not display_game_no or not _room_conflict_confirmed(state, room_label=snapshot.room_label):
            return state.snapshot()
        _switch_room_lock_for_observed_room(
            state,
            room_label=snapshot.room_label,
            source="label_runtime_room_switch",
        )
    else:
        if snapshot.room_label:
            state.room_conflict_identity = ""
            state.room_conflict_seen_count = 0
        _promote_room_lock(state, room_label=snapshot.room_label)
            
    summary = {
        "label_game_no": display_game_no,
        "label_countdown": snapshot.countdown_seconds,
        "label_room_label": snapshot.room_label,
        "label_limit_label": snapshot.limit_label,
        "label_phase_text": snapshot.phase_text,
        "label_confidence": snapshot.confidence,
        "label_matches": snapshot.frame.matches[:10],
    }
    if label_game_no and not display_game_no:
        summary["label_internal_game_no"] = label_game_no
    countdown = snapshot.countdown_seconds
    phase_text = snapshot.phase_text
    if stale_label_game_no:
        summary["label_ignored_countdown"] = countdown
        countdown = None
        phase_text = ""
    if _is_untrusted_zero_countdown(
        state,
        countdown=countdown,
        phase_text=phase_text,
        display_game_no=display_game_no,
        raw_game_no=label_game_no,
        room_label=snapshot.room_label,
        limit_label=snapshot.limit_label,
    ):
        summary["label_ignored_countdown"] = countdown
        countdown = None
    
    return state.update(
        batch_id=display_game_no,
        room_label=snapshot.room_label,
        limit_label=snapshot.limit_label,
        phase_text=phase_text,
        countdown=countdown,
        source="label_runtime_resolved",
        confidence=snapshot.confidence,
        safe_summary=summary
    )


def _update_from_canvas_text_state(state: _RuntimeState, snapshot: CanvasTextSnapshot) -> TemporalStateSnapshot:
    """Apply a canvas text snapshot to the state."""
    state.last_runtime_ms = snapshot.timestamp_ms
    if snapshot.confidence < 0.35:
        return state.snapshot()
    canvas_game_no = str(snapshot.game_no or "")
    display_game_no = _display_batch_id_or_empty(canvas_game_no)
    canvas_visible_context = bool(
        snapshot.records
        or snapshot.countdown_seconds is not None
        or snapshot.room_label
        or snapshot.limit_label
        or snapshot.phase_text
    )
    if (
        display_game_no
        and _batch_tail_width(display_game_no) > _DISPLAY_BATCH_TAIL_MAX_WIDTH
        and not canvas_visible_context
        and not _has_batch_corrobating_context(
            room_label=snapshot.room_label,
            limit_label=snapshot.limit_label,
            phase_text=snapshot.phase_text,
        )
    ):
        display_game_no = ""
    stale_canvas_game_no = ""
    if display_game_no:
        if _is_stale_locked_batch(state, display_game_no) or _is_stale_current_batch(state, display_game_no):
            stale_canvas_game_no = display_game_no
            display_game_no = ""
        else:
            _promote_visible_batch_lock(state, display_game_no)
    if snapshot.room_label and _room_identity_conflicts(state, room_label=snapshot.room_label):
        if not display_game_no or not _room_conflict_confirmed(state, room_label=snapshot.room_label):
            return state.snapshot()
        _switch_room_lock_for_observed_room(
            state,
            room_label=snapshot.room_label,
            source="canvas_text_room_switch",
        )
    else:
        if snapshot.room_label:
            state.room_conflict_identity = ""
            state.room_conflict_seen_count = 0
        _promote_room_lock(state, room_label=snapshot.room_label)
    summary = {
        "canvas_game_no": display_game_no,
        "canvas_countdown": snapshot.countdown_seconds,
        "canvas_room_label": snapshot.room_label,
        "canvas_limit_label": snapshot.limit_label,
        "canvas_phase_text": snapshot.phase_text,
        "canvas_confidence": snapshot.confidence,
        "canvas_records": snapshot.records[:10],
    }
    if canvas_game_no and not display_game_no:
        summary["canvas_internal_game_no"] = canvas_game_no
    countdown = snapshot.countdown_seconds
    phase_text = snapshot.phase_text
    if stale_canvas_game_no:
        summary["canvas_ignored_countdown"] = countdown
        countdown = None
        phase_text = ""
    if _is_untrusted_zero_countdown(
        state,
        countdown=countdown,
        phase_text=phase_text,
        display_game_no=display_game_no,
        raw_game_no=canvas_game_no,
        room_label=snapshot.room_label,
        limit_label=snapshot.limit_label,
    ):
        summary["canvas_ignored_countdown"] = countdown
        countdown = None
    return state.update(
        batch_id=display_game_no,
        room_label=snapshot.room_label,
        limit_label=snapshot.limit_label,
        phase_text=phase_text,
        countdown=countdown,
        source="canvas_text_runtime",
        confidence=snapshot.confidence,
        safe_summary=summary,
    )


def _update_from_page_runtime_state(state: _RuntimeState, runtime: LiveRuntimeSnapshot) -> TemporalStateSnapshot:
    """Apply the active page runtime object when it exposes a current game state."""
    state.last_runtime_ms = runtime.timestamp_ms
    runtime_summary = runtime.to_safe_dict()
    runtime_frame = runtime_summary.get("frame") if isinstance(runtime_summary.get("frame"), dict) else {}
    runtime_room_id = str(runtime_frame.get("room_id") or "")
    runtime_room_label = str(runtime_frame.get("table_label") or "") or _room_label_hint_from_id(runtime_room_id)
    if not runtime_room_label:
        runtime_room_label = str(state.safe_summary.get("room_entry_expected_room_label") or "")
    runtime_limit_label = _limit_label_from_cents(
        runtime_frame.get("user_min_bet_cents"),
        runtime_frame.get("user_max_bet_cents"),
    )
    runtime_game_no = str(runtime.game_no or "")
    runtime_countdown = runtime.countdown_seconds
    runtime_action = _as_int_or_none(runtime_frame.get("action"))
    runtime_timed = _as_int_or_none(runtime_frame.get("timed"))
    runtime_current_load_type = _as_int_or_none(runtime_frame.get("current_load_type"))
    runtime_is_can_betting = _as_bool_or_none(runtime_frame.get("is_can_betting"))
    runtime_has_state = bool(
        _is_display_batch_id(runtime_game_no)
        and (
            runtime_countdown is not None
            or runtime_action is not None
            or runtime_timed is not None
            or runtime_current_load_type is not None
            or runtime_is_can_betting is not None
            or runtime.phase_key not in ("", "unknown")
        )
    )
    if runtime_room_id or runtime_room_label:
        if _room_identity_conflicts(state, room_id=runtime_room_id, room_label=runtime_room_label):
            if not runtime_has_state or not _room_conflict_confirmed(
                state,
                room_id=runtime_room_id,
                room_label=runtime_room_label,
            ):
                return state.snapshot()
            _switch_room_lock_for_observed_room(
                state,
                room_id=runtime_room_id,
                room_label=runtime_room_label,
                source="page_runtime_room_switch",
            )
        else:
            state.room_conflict_identity = ""
            state.room_conflict_seen_count = 0
            _promote_room_lock(state, room_id=runtime_room_id, room_label=runtime_room_label)

    summary = {
        "runtime_memory_game_no": runtime_game_no if runtime_has_state else "",
        "runtime_memory_game_no_ignored": "" if runtime_has_state else runtime_game_no,
        "runtime_room_id": runtime_room_id,
        "runtime_room_label": runtime_room_label,
        "runtime_limit_label": runtime_limit_label,
    }
    if runtime.coordinates:
        summary["runtime_coordinates"] = runtime.coordinates
    if runtime.viewport:
        summary["runtime_viewport"] = runtime.viewport
    if runtime.layout_confidence:
        summary["runtime_layout_confidence"] = runtime.layout_confidence
    summary["runtime_game_visible"] = runtime.game_visible
    if runtime_has_state:
        summary.update(
            {
                "runtime_countdown": runtime_countdown,
                "runtime_phase": runtime.phase_key,
                "runtime_phase_label": runtime.phase_label,
                "runtime_betting_open": runtime.betting_open,
                "runtime_action": runtime_action,
                "runtime_timed": runtime_timed,
                "runtime_current_load_type": runtime_current_load_type,
                "runtime_is_can_betting": runtime_is_can_betting,
            }
        )
    summary = {key: value for key, value in summary.items() if value not in (None, "")}

    if runtime_has_state:
        return state.update(
            batch_id=runtime_game_no,
            room_id=runtime_room_id,
            room_label=runtime_room_label,
            limit_label=runtime_limit_label,
            phase_text=runtime.phase_key,
            countdown=runtime_countdown,
            balance=runtime.balance_text,
            source="page_runtime_state",
            confidence=max(0.70, float(runtime.layout_confidence or 0.0)),
            safe_summary=summary,
            runtime_state=runtime_summary,
        )

    return state.update(
        room_id=runtime_room_id,
        room_label=runtime_room_label,
        limit_label=runtime_limit_label,
        balance=runtime.balance_text,
        source="page_runtime_balance",
        safe_summary=summary,
    )


def _emit(event_queue: mp.Queue, instance_id: str, event_type: WorkerEventType, payload: dict[str, Any]) -> None:
    """Send an event to the main process via the multiprocess queue."""
    try:
        event = ClusterWorkerEvent(
            event_type=event_type,
            instance_id=instance_id,
            timestamp_ms=now_ms(),
            payload=payload,
        )
        for _ in range(MAX_QUEUE_RETRIES):
            try:
                event_queue.put_nowait(event)
                break
            except queue.Full:
                time.sleep(0.01)
    except Exception:
        pass


def _emit_state(event_queue: mp.Queue, snapshot: TemporalStateSnapshot) -> None:
    """Emit a game state update event."""
    _emit(event_queue, snapshot.instance_id, "state", snapshot.to_safe_dict())


def _diagnostic_page_url(page: Any) -> str:
    try:
        parts = urlsplit(str(getattr(page, "url", "") or ""))
    except Exception:
        return ""
    if not parts.scheme or not parts.netloc:
        return str(getattr(page, "url", "") or "")[:160]
    path = parts.path or "/"
    return f"{parts.scheme}://{parts.netloc}{path}"[:160]


def _emit_hall_clear_probe_audit(
    config: ClusterWorkerConfig,
    runtime: dict[str, Any],
    state: "_RuntimeState",
    event_queue: mp.Queue,
    *,
    location: str,
    page: Any,
    ready: Any,
    room_entry_loading: bool,
    would_clear: bool,
    page_index: int = -1,
    page_count: int = 0,
) -> None:
    payload = {
        "audit_type": "hall_clear_probe",
        "location": location,
        "page_index": page_index,
        "page_count": page_count,
        "page_url": _diagnostic_page_url(page),
        "ready": ready.safe_summary() if hasattr(ready, "safe_summary") else {},
        "room_entry_loading": bool(room_entry_loading),
        "would_clear": bool(would_clear),
        "state": {
            "batch_id": str(getattr(state, "batch_id", "") or ""),
            "room_id": str(getattr(state, "room_id", "") or ""),
            "room_label": str(getattr(state, "room_label", "") or ""),
        },
    }
    signature = json.dumps(
        {
            "location": payload["location"],
            "page_index": payload["page_index"],
            "page_url": payload["page_url"],
            "ready": payload["ready"],
            "room_entry_loading": payload["room_entry_loading"],
            "would_clear": payload["would_clear"],
        },
        sort_keys=True,
        ensure_ascii=True,
        default=str,
    )
    current_ms = now_ms()
    last_by_location = runtime.setdefault("hall_clear_probe_last", {})
    throttle_key = f"{location}:{page_index}"
    last = last_by_location.get(throttle_key) if isinstance(last_by_location, dict) else None
    if (
        isinstance(last, dict)
        and last.get("signature") == signature
        and current_ms - int(last.get("timestamp_ms") or 0) < 1000
    ):
        return
    if isinstance(last_by_location, dict):
        last_by_location[throttle_key] = {"signature": signature, "timestamp_ms": current_ms}
    _emit(event_queue, config.instance_id, "audit", payload)


def _runtime_v2_shadow_enabled(config: ClusterWorkerConfig) -> bool:
    return str(getattr(config, "runtime_pipeline", "legacy") or "legacy").lower() in {"v2_shadow", "v2_live"}


def _runtime_v2_shadow_ready(config: ClusterWorkerConfig, runtime: dict[str, Any]) -> bool:
    if not _runtime_v2_shadow_enabled(config):
        return False
    current_ms = now_ms()
    min_interval = max(0, int(getattr(config, "runtime_shadow_interval_ms", 1000) or 0))
    last_ms = int(runtime.get("runtime_v2_shadow_last_ms") or 0)
    return not last_ms or current_ms - last_ms >= min_interval


def _emit_runtime_v2_shadow(
    *,
    config: ClusterWorkerConfig,
    runtime: dict[str, Any],
    state: _RuntimeState,
    event_queue: mp.Queue,
    runtime_snapshot: LiveRuntimeSnapshot | None = None,
    label_snapshot: LiveLabelRuntimeSnapshot | None = None,
    canvas_snapshot: CanvasTextSnapshot | None = None,
    frontend_events: list[FrontendStateEvent] | None = None,
) -> None:
    if not _runtime_v2_shadow_enabled(config):
        return
    try:
        current_ms = now_ms()
        min_interval = max(0, int(getattr(config, "runtime_shadow_interval_ms", 1000) or 0))
        last_ms = int(runtime.get("runtime_v2_shadow_last_ms") or 0)
        if last_ms and current_ms - last_ms < min_interval:
            return
        runtime["runtime_v2_shadow_last_ms"] = current_ms
        legacy_payload = state.snapshot().to_safe_dict()
        decision = build_runtime_v2_shadow_decision(
            instance_id=config.instance_id,
            legacy_payload=legacy_payload,
            runtime_snapshot=runtime_snapshot,
            label_snapshot=label_snapshot,
            canvas_snapshot=canvas_snapshot,
            frontend_events=frontend_events,
            previous_decision=runtime.get("runtime_v2_shadow_previous_decision"),
        )
        runtime["runtime_v2_shadow_previous_decision"] = decision
        payload = decision.to_safe_dict()
        payload["pipeline_mode"] = str(getattr(config, "runtime_pipeline", "legacy") or "legacy")
        _emit(event_queue, config.instance_id, "state_v2_shadow", payload)
    except Exception:
        pass


def _cache_runtime_v2_frontend_events(runtime: dict[str, Any], events: list[FrontendStateEvent]) -> None:
    if not events:
        return
    current_ms = now_ms()
    recent = [
        event
        for event in runtime.get("runtime_v2_frontend_events", [])
        if getattr(event, "timestamp_ms", 0) and current_ms - int(getattr(event, "timestamp_ms", 0)) <= 5000
    ]
    recent.extend(event for event in events if getattr(event, "timestamp_ms", 0))
    runtime["runtime_v2_frontend_events"] = recent[-60:]


def _runtime_v2_recent_frontend_events(runtime: dict[str, Any]) -> list[FrontendStateEvent]:
    current_ms = now_ms()
    return [
        event
        for event in runtime.get("runtime_v2_frontend_events", [])
        if getattr(event, "timestamp_ms", 0) and current_ms - int(getattr(event, "timestamp_ms", 0)) <= 5000
    ]


def _emit_bet_confirmation_audits(
    config: ClusterWorkerConfig,
    runtime: dict[str, Any],
    state: "_RuntimeState",
    event_queue: mp.Queue,
    raw_events: list[dict[str, Any]],
    captured_ms: int,
) -> None:
    """Emit bet-confirmation audit events without changing state extraction."""

    seen_keys: set[str] = runtime.setdefault("seen_bet_confirmation_keys", set())
    seen_order: list[str] = runtime.setdefault("seen_bet_confirmation_key_order", [])
    for raw_event in raw_events:
        payload = extract_bet_confirmation_from_frontend_event(
            raw_event,
            instance_id=config.instance_id,
            captured_ms=captured_ms,
        )
        if not payload:
            continue
        key = str(payload.get("dedupe_key") or "")
        if key and key in seen_keys:
            continue
        if key:
            seen_keys.add(key)
            seen_order.append(key)
            if len(seen_order) > 500:
                old_key = seen_order.pop(0)
                seen_keys.discard(old_key)
        payload["context"] = {
            "current_batch_id": state.batch_id,
            "current_room_id": state.room_id,
            "current_room_label": state.room_label,
            "current_balance": getattr(state, "balance", ""),
            "runtime_selected_bet": getattr(state, "runtime_selected_bet", None),
            "runtime_pending_chip_cents": getattr(state, "runtime_pending_chip_cents", None),
        }
        _emit(event_queue, config.instance_id, "audit", payload)


async def _run_worker(config: ClusterWorkerConfig, event_queue: mp.Queue, command_queue: mp.Queue, stop_event: Any) -> None:
    """Main worker entry point (async)."""
    state = _RuntimeState(config.instance_id)
    _emit(event_queue, config.instance_id, "log", {"message": f"Worker process {config.instance_id} starting"})
    pw = None
    browser = None
    context = None
    runtime: dict[str, Any] = {}
    
    try:
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        context, browser = await _open_browser_context(pw, config, event_queue)
        await _install_context_probes(config, context)
        page = await _active_page(context)
        ws_parser = _load_ws_parser(config.ws_parser_path)
        _attach_network_collectors(config, context, page, state, event_queue, ws_parser)
        runtime.update(
            {
                "context": context,
                "browser": browser,
                "page": page,
                "mode": "headed",
                "launch_bundle": None,
            }
        )
        tasks = [
            asyncio.create_task(_navigate_initial_page(config, runtime, event_queue)),
            asyncio.create_task(
                _poll_worker_commands(config, runtime, pw, state, event_queue, command_queue, stop_event, ws_parser)
            ),
            asyncio.create_task(_poll_frontend_state(config, runtime, state, event_queue, stop_event)),
            asyncio.create_task(_poll_runtime_state(config, runtime, state, event_queue, stop_event)),
            asyncio.create_task(_viewport_guard(config, runtime, event_queue, stop_event)),
            asyncio.create_task(_health_heartbeat(config, runtime, state, event_queue, stop_event)),
        ]
        while not stop_event.is_set():
            await asyncio.sleep(0.5)
            if all(t.done() for t in tasks): break
        for t in tasks:
            if not t.done(): t.cancel()
    except Exception as e:
        _emit(event_queue, config.instance_id, "error", {"message": str(e), "traceback": traceback.format_exc()})
    finally:
        try:
            context = runtime.get("context") or context
            browser = runtime.get("browser") or browser
            if context is not None:
                await context.close()
            elif browser is not None:
                await browser.close()
        except Exception:
            pass
        try:
            if pw is not None:
                await pw.stop()
        except Exception:
            pass


def _profile_dir(config: ClusterWorkerConfig) -> Path:
    if config.user_data_dir:
        return Path(config.user_data_dir)
    return DEFAULT_PROFILE_ROOT / config.instance_id


async def _open_browser_context(pw, config: ClusterWorkerConfig, event_queue: mp.Queue):
    viewport = {"width": config.viewport_width, "height": config.viewport_height}
    proxy = _playwright_proxy(config.proxy)
    profile_dir = _profile_dir(config)
    profile_dir.mkdir(parents=True, exist_ok=True)

    if config.debug_port:
        cdp_endpoint = f"http://127.0.0.1:{int(config.debug_port)}"
        try:
            browser = await pw.chromium.connect_over_cdp(cdp_endpoint, timeout=3000)
            context = browser.contexts[0] if browser.contexts else await browser.new_context(
                viewport=viewport,
                device_scale_factor=1,
                ignore_https_errors=True,
            )
            restored = await _restore_context_viewport(config, context)
            _emit(
                event_queue,
                config.instance_id,
                "log",
                {
                    "message": (
                        f"Connected to existing controlled Chrome via CDP: {cdp_endpoint}"
                        + (f" / viewport restored {restored}" if restored else "")
                    )
                },
            )
            return context, browser
        except Exception:
            pass

    persistent_options: dict[str, Any] = {
        "headless": config.headless,
        "viewport": viewport,
        "device_scale_factor": 1,
        "ignore_https_errors": True,
    }
    if proxy:
        persistent_options["proxy"] = proxy
    if config.browser_channel:
        persistent_options["channel"] = config.browser_channel
    launch_args = _browser_launch_args(config)
    if launch_args:
        persistent_options["args"] = launch_args

    try:
        context = await pw.chromium.launch_persistent_context(str(profile_dir), **persistent_options)
        await _restore_context_viewport(config, context)
        _emit(
            event_queue,
            config.instance_id,
            "log",
            {
                "message": (
                    f"Using persistent browser profile: {profile_dir}"
                    + (f" (CDP http://127.0.0.1:{config.debug_port})" if config.debug_port else "")
                )
            },
        )
        return context, None
    except Exception as exc:
        if not config.browser_channel:
            raise
        _emit(
            event_queue,
            config.instance_id,
            "log",
            {"message": f"Chrome channel unavailable, falling back to bundled Chromium: {type(exc).__name__}"},
        )
        persistent_options.pop("channel", None)
        context = await pw.chromium.launch_persistent_context(str(profile_dir), **persistent_options)
        await _restore_context_viewport(config, context)
        return context, None


def _browser_launch_args(config: ClusterWorkerConfig) -> list[str]:
    args = [
        f"--window-size={int(config.viewport_width)},{int(config.viewport_height)}",
        "--force-device-scale-factor=1",
    ]
    if config.debug_port:
        args.append(f"--remote-debugging-port={int(config.debug_port)}")
    return args


def _baccarat_standard_viewport() -> dict[str, int]:
    return {"width": BACCARAT_COORDINATE_BASE_SIZE[0], "height": BACCARAT_COORDINATE_BASE_SIZE[1]}


def _near_int(value: Any, expected: int, *, tolerance: int = BACCARAT_COORDINATE_TOLERANCE_PX) -> bool:
    try:
        return abs(float(value) - float(expected)) <= tolerance
    except (TypeError, ValueError):
        return False


def _standard_hall_candidate_issue(candidate: Mapping[str, Any]) -> str:
    canvas_rect = candidate.get("canvas_rect") if isinstance(candidate.get("canvas_rect"), Mapping) else {}
    if not canvas_rect:
        return "missing_canvas_rect"
    expected_width, expected_height = BACCARAT_COORDINATE_BASE_SIZE
    if not _near_int(canvas_rect.get("left"), 0) or not _near_int(canvas_rect.get("top"), 0):
        return (
            "canvas_origin_not_zero:"
            f"left={canvas_rect.get('left')},top={canvas_rect.get('top')},"
            f"expected=0,0"
        )
    if not _near_int(canvas_rect.get("width"), expected_width) or not _near_int(
        canvas_rect.get("height"), expected_height
    ):
        return (
            "canvas_size_not_standard:"
            f"{canvas_rect.get('width')}x{canvas_rect.get('height')},"
            f"expected={expected_width}x{expected_height}"
        )
    base_size = candidate.get("base_size") if isinstance(candidate.get("base_size"), Mapping) else {}
    if base_size and (
        not _near_int(base_size.get("width"), expected_width)
        or not _near_int(base_size.get("height"), expected_height)
    ):
        return (
            "base_size_not_standard:"
            f"{base_size.get('width')}x{base_size.get('height')},"
            f"expected={expected_width}x{expected_height}"
        )
    return ""


def _standard_hall_candidates(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(candidate) for candidate in candidates if not _standard_hall_candidate_issue(candidate)]


def _standard_hall_buttons_issue(buttons: Sequence[Mapping[str, Any]]) -> str:
    if len(buttons) != 4:
        return f"button_count_not_four:{len(buttons)}"
    for button in buttons:
        if not isinstance(button, Mapping):
            return "button_not_mapping"
        issue = _standard_hall_candidate_issue(button)
        if issue:
            room_index = button.get("room_index", "?")
            return f"room_{room_index}:{issue}"
    return ""


async def _force_baccarat_standard_page_viewport(page: Any) -> None:
    try:
        await page.set_viewport_size(_baccarat_standard_viewport())
    except Exception:
        pass


async def _restore_context_viewport(config: ClusterWorkerConfig, context) -> int:
    target = {"width": int(config.viewport_width), "height": int(config.viewport_height)}
    restored = 0
    try:
        pages = list(context.pages)
    except Exception:
        pages = []
    for page in pages:
        try:
            if page.is_closed():
                continue
            current = page.viewport_size or {}
            if int(current.get("width") or 0) == target["width"] and int(current.get("height") or 0) == target["height"]:
                continue
            await page.set_viewport_size(target)
            restored += 1
        except Exception:
            continue
    return restored


async def _viewport_guard(config: ClusterWorkerConfig, runtime: dict[str, Any], event_queue: mp.Queue, stop_event: Any) -> None:
    last_emit_ms = 0
    while not stop_event.is_set():
        context = runtime.get("context")
        if context is None:
            await asyncio.sleep(1.0)
            continue
        restored = await _restore_context_viewport(config, context)
        if restored:
            current_ms = now_ms()
            if current_ms - last_emit_ms >= 5000:
                last_emit_ms = current_ms
                _emit(
                    event_queue,
                    config.instance_id,
                    "log",
                    {"message": f"Viewport guard restored {restored} page(s) to {config.viewport_width}x{config.viewport_height}"},
                )
        await asyncio.sleep(1.0)


async def _install_context_probes(config: ClusterWorkerConfig, context) -> None:
    if config.enable_frontend_probe:
        try:
            await context.add_init_script(FRONTEND_STATE_PROBE_JS)
        except Exception:
            pass
    if config.enable_canvas_probe:
        await install_canvas_text_probe(context)


async def _active_page(context):
    pages = []
    try:
        pages = [page for page in list(context.pages) if not page.is_closed()]
    except Exception:
        pages = []
    for page in pages:
        try:
            if page.url and page.url != "about:blank":
                return page
        except Exception:
            continue
    if pages:
        return pages[0]
    return await context.new_page()


async def _goto_url(page, url: str) -> None:
    if not url:
        return
    await page.goto(url, wait_until="domcontentloaded", timeout=45000)


def _attach_network_collectors(config, context, page, state, event_queue, ws_parser):
    attached_pages: set[int] = set()

    def on_ws(ws):
        state.ws_connected = True
        ws.on("framereceived", lambda f: _handle_frame(f, "inbound", ws.url))
        ws.on("framesent", lambda f: _handle_frame(f, "outbound", ws.url))
        ws.on("close", lambda: setattr(state, "ws_connected", False))

    def _handle_frame(frame, direction, url):
        state.last_ws_ms = now_ms()
        try:
            parsed = ws_parser.parse_frame(frame, direction=direction, url=str(url))
            if parsed and parsed.has_state:
                update_data = parsed.to_worker_update()
                batch_id = update_data.get("batch_id", "")
                room_id = update_data.get("room_id", "")
                if room_id and _room_identity_conflicts(state, room_id=room_id):
                    return
                if batch_id and _is_stale_locked_batch(state, str(batch_id)):
                    snapshot = state.update(
                        balance=update_data.get("balance", ""),
                        source=f"ws_{direction}_balance",
                        safe_summary={"ws_stale_game_no": batch_id},
                    )
                else:
                    snapshot = state.update(
                        batch_id=batch_id,
                        room_id=room_id,
                        countdown=update_data.get("countdown"),
                        balance=update_data.get("balance", ""),
                        source=f"ws_{direction}",
                        safe_summary=update_data.get("safe_summary")
                    )
                _emit_state(event_queue, snapshot)
        except Exception: pass

    def attach_page(candidate):
        try:
            key = id(candidate)
            if key in attached_pages:
                return
            attached_pages.add(key)
            candidate.on("websocket", on_ws)
            candidate.on("response", lambda r: asyncio.create_task(_handle_response(r, state, event_queue)))
        except Exception:
            pass

    for candidate in list(getattr(context, "pages", []) or []):
        attach_page(candidate)
    attach_page(page)
    try:
        context.on("page", attach_page)
    except Exception:
        pass


async def _handle_response(response, state, event_queue):
    try:
        if "json" in response.headers.get("content-type", "").lower():
            text = await response.text()
            if len(text) < 50000:
                data = json.loads(text)
                extracted = _extract_state_from_payload(data)
                if extracted.get("batch_id") or extracted.get("countdown") is not None:
                    batch_id = extracted.get("batch_id", "")
                    if batch_id and _is_stale_locked_batch(state, str(batch_id)):
                        snapshot = state.update(
                            balance=extracted.get("balance", ""),
                            source="http_response_balance",
                            safe_summary={"http_stale_game_no": batch_id},
                        )
                    else:
                        snapshot = state.update(
                            batch_id=batch_id,
                            countdown=extracted.get("countdown"),
                            balance=extracted.get("balance", ""),
                            source="http_response"
                        )
                    _emit_state(event_queue, snapshot)
    except Exception: pass


async def _poll_frontend_state(config, runtime, state, event_queue, stop_event):
    last_object_scan_ms = 0
    while not stop_event.is_set():
        try:
            context = runtime.get("context")
            if context is None:
                await asyncio.sleep(config.state_poll_interval_ms / 1000.0)
                continue
            captured_ms = now_ms()
            include_object_scan = captured_ms - last_object_scan_ms >= 500
            if include_object_scan:
                last_object_scan_ms = captured_ms
            pages = list(context.pages)
            page_count = len(pages)
            for page_index, page in enumerate(pages):
                if page.is_closed(): continue
                try:
                    ready = await read_baccarat_load_state(page)
                    room_entry_loading = bool(
                        runtime.get("room_entry_pending")
                        or now_ms() < int(runtime.get("room_entry_loading_until_ms") or 0)
                    )
                    would_clear = bool(ready.hall_ready and not ready.game_ready and not room_entry_loading)
                    _emit_hall_clear_probe_audit(
                        config,
                        runtime,
                        state,
                        event_queue,
                        location="frontend_poll",
                        page=page,
                        ready=ready,
                        room_entry_loading=room_entry_loading,
                        would_clear=would_clear,
                        page_index=page_index,
                        page_count=page_count,
                    )
                    if ready.hall_ready and not ready.game_ready and not room_entry_loading:
                        continue
                except Exception:
                    pass
                raw_events = await _collect_frontend_events_from_page(config, page, include_object_scan=include_object_scan)
                if raw_events:
                    _emit_bet_confirmation_audits(config, runtime, state, event_queue, raw_events, captured_ms)
                    parsed_events: list[FrontendStateEvent] = []
                    for raw_event in raw_events:
                        parsed_events.extend(parse_frontend_state_events(raw_event))
                    parsed_events = [e for e in parsed_events if e and e.has_state]
                    _cache_runtime_v2_frontend_events(runtime, parsed_events)
                    selected = _select_frontend_state_events(state, parsed_events)
                    for event in selected:
                        snapshot = _update_from_frontend_state(state, event)
                        _emit_state(event_queue, snapshot)
        except Exception: pass
        await asyncio.sleep(config.state_poll_interval_ms / 1000.0)


async def _collect_frontend_events_from_page(
    config: ClusterWorkerConfig,
    page,
    *,
    include_object_scan: bool,
) -> list[dict[str, Any]]:
    raw_events: list[dict[str, Any]] = []
    try:
        frames = list(page.frames)
    except Exception:
        frames = []
    for frame_index, frame in enumerate(frames):
        if config.enable_frontend_probe:
            try:
                await frame.evaluate(FRONTEND_STATE_PROBE_JS)
            except Exception:
                pass
            try:
                drained = await frame.evaluate(
                    "() => window.__betDesktopDrainFrontendStateEvents ? window.__betDesktopDrainFrontendStateEvents() : []"
                )
            except Exception:
                drained = []
            for raw in drained if isinstance(drained, list) else []:
                if isinstance(raw, dict):
                    raw.setdefault("frame_index", frame_index)
                    raw.setdefault("frame_url", str(getattr(frame, "url", "") or "")[:240])
                    raw_events.append(raw)
        if include_object_scan:
            for script in (FRONTEND_BOUND_ROOM_SCAN_JS, FRONTEND_OBJECT_SCAN_JS):
                try:
                    raw = await frame.evaluate(script)
                except Exception:
                    raw = None
                if isinstance(raw, dict):
                    raw.setdefault("frame_index", frame_index)
                    raw.setdefault("frame_url", str(getattr(frame, "url", "") or "")[:240])
                    raw_events.append(raw)
    return raw_events


async def _poll_runtime_state(config, runtime, state, event_queue, stop_event):
    while not stop_event.is_set():
        try:
            context = runtime.get("context")
            if context is None:
                await asyncio.sleep(0.5)
                continue
            active_page = await _active_page(context)
            runtime["page"] = active_page
            ready = await read_baccarat_load_state(active_page)
            try:
                pages = [page for page in list(context.pages) if not page.is_closed()]
            except Exception:
                pages = []
            try:
                page_index = pages.index(active_page)
            except ValueError:
                page_index = -1
            room_entry_loading = bool(
                runtime.get("room_entry_pending")
                or now_ms() < int(runtime.get("room_entry_loading_until_ms") or 0)
            )
            would_clear = bool(ready.hall_ready and not ready.game_ready and not room_entry_loading)
            _emit_hall_clear_probe_audit(
                config,
                runtime,
                state,
                event_queue,
                location="runtime_poll",
                page=active_page,
                ready=ready,
                room_entry_loading=room_entry_loading,
                would_clear=would_clear,
                page_index=page_index,
                page_count=len(pages),
            )
            if ready.game_ready:
                state.room_absent_seen_count = 0
                runtime["room_entry_loading_until_ms"] = 0
            if ready.hall_ready and not ready.game_ready and not room_entry_loading:
                runtime_snapshot = (
                    await read_live_runtime_snapshot(active_page, instance_id=config.instance_id)
                    if config.enable_runtime_scan
                    else None
                )
                snapshot = _clear_room_state_for_hall(
                    state,
                    balance=runtime_snapshot.balance_text if runtime_snapshot else "",
                    ready_summary=ready.safe_summary(),
                )
                _emit_state(event_queue, snapshot)
                await asyncio.sleep(0.5)
                continue
            if config.enable_runtime_scan:
                runtime_snapshot = await read_live_runtime_snapshot(active_page, instance_id=config.instance_id)
            else:
                runtime_snapshot = None
            if runtime_snapshot:
                snapshot = _update_from_page_runtime_state(state, runtime_snapshot)
                _emit_state(event_queue, snapshot)
            labels = await read_live_label_runtime_snapshot(active_page, instance_id=config.instance_id)
            if labels:
                snapshot = _update_from_label_runtime_state(state, labels)
                _emit_state(event_queue, snapshot)
            canvas = None
            if config.enable_canvas_probe:
                canvas = await read_canvas_text_snapshot(active_page, instance_id=config.instance_id)
                if canvas:
                    snapshot = _update_from_canvas_text_state(state, canvas)
                    _emit_state(event_queue, snapshot)
            if not ready.game_ready and not room_entry_loading:
                if _runtime_probe_has_live_game_state(
                    runtime_snapshot=runtime_snapshot,
                    label_snapshot=labels,
                    canvas_snapshot=canvas,
                ):
                    state.room_absent_seen_count = 0
                else:
                    snapshot = _clear_room_state_if_room_absent(
                        state,
                        balance=runtime_snapshot.balance_text if runtime_snapshot else "",
                        ready_summary=ready.safe_summary(),
                    )
                    if snapshot:
                        _emit_state(event_queue, snapshot)
                        await asyncio.sleep(0.5)
                        continue
            _emit_runtime_v2_shadow(
                config=config,
                runtime=runtime,
                state=state,
                event_queue=event_queue,
                runtime_snapshot=runtime_snapshot,
                label_snapshot=labels,
                canvas_snapshot=canvas,
                frontend_events=_runtime_v2_recent_frontend_events(runtime),
            )
        except Exception: pass
        await asyncio.sleep(0.5)


async def _health_heartbeat(config, runtime, state, event_queue, stop_event):
    while not stop_event.is_set():
        try:
            _emit(
                event_queue,
                config.instance_id,
                "health",
                {"status": "running", "ws": state.ws_connected, "mode": runtime.get("mode") or "headed"},
            )
        except Exception: pass
        await asyncio.sleep(5.0)


async def _navigate_initial_page(config, runtime, event_queue):
    try:
        context = runtime.get("context")
        if context is None:
            return
        active_page = await _active_page(context)
        runtime["page"] = active_page
        if config.target_url:
            await _goto_url(active_page, config.target_url)
            _emit(event_queue, config.instance_id, "log", {"message": "Target page opened"})
        elif config.login_url:
            await _goto_url(active_page, config.login_url)
            _emit(event_queue, config.instance_id, "log", {"message": "Login page opened"})
            if config.auto_fill_login and config.username and config.password:
                result = await fill_login_form_when_visible(
                    context,
                    active_page,
                    username=config.username,
                    password=config.password,
                    timeout_seconds=config.login_timeout_seconds,
                )
                _emit(event_queue, config.instance_id, "health", {"login_auto_fill": "submitted", **result})
    except Exception as exc:
        _emit(event_queue, config.instance_id, "error", {"message": f"navigation failed: {type(exc).__name__}"})


async def _poll_worker_commands(config, runtime, pw, state, event_queue, command_queue, stop_event, ws_parser):
    while not stop_event.is_set():
        try:
            cmd = command_queue.get_nowait()
            if cmd.get("command") == "fill_login":
                context = runtime.get("context")
                if context is None:
                    continue
                active_page = await _active_page(context)
                runtime["page"] = active_page
                result = await fill_login_form_when_visible(
                    context,
                    active_page,
                    username=str(cmd.get("username") or ""),
                    password=str(cmd.get("password") or ""),
                )
                _emit(event_queue, config.instance_id, "health", {"login_auto_fill": "submitted", **result})
            elif cmd.get("command") == "navigate":
                context = runtime.get("context")
                if context is None:
                    continue
                active_page = await _active_page(context)
                runtime["page"] = active_page
                await _goto_url(active_page, str(cmd.get("url") or ""))
                _emit(event_queue, config.instance_id, "log", {"message": "Target page opened"})
            elif cmd.get("command") == "capture_game_launch_context":
                await _capture_game_launch_context(config, runtime, event_queue)
            elif cmd.get("command") == "handoff_to_headless":
                await _handoff_to_headless(config, runtime, pw, state, event_queue, ws_parser)
            elif cmd.get("command") == "enter_room":
                await _enter_headless_room(config, runtime, event_queue, int(cmd.get("room_index") or 0), state)
            elif cmd.get("command") == "report_headless_status":
                await _report_headless_status(config, runtime, event_queue)
            elif cmd.get("command") == "release_headless":
                await _release_headless_session(config, runtime, event_queue)
        except queue.Empty: pass
        await asyncio.sleep(0.2)


async def _capture_game_launch_context(config, runtime, event_queue):
    context = runtime.get("context")
    if context is None:
        _emit(event_queue, config.instance_id, "error", {"message": "capture failed: no browser context"})
        return None
    active_page = await _active_page(context)
    runtime["page"] = active_page
    candidate = await extract_game_launch_url_from_page(active_page)
    if candidate is None:
        _emit(
            event_queue,
            config.instance_id,
            "error",
            {"message": "capture failed: no baccarat launch URL found in current browser"},
        )
        return None
    storage_state = await context.storage_state()
    local_storage_items = await capture_local_storage_for_candidate(active_page, candidate)
    try:
        user_agent = await active_page.evaluate("() => navigator.userAgent")
    except Exception:
        user_agent = ""
    bundle = {
        "url": candidate.url,
        "safe_summary": candidate.safe_summary(),
        "storage_state": storage_state,
        "local_storage_items": local_storage_items,
        "user_agent": str(user_agent or ""),
        "captured_ms": now_ms(),
    }
    runtime["launch_bundle"] = bundle
    _emit(
        event_queue,
        config.instance_id,
        "log",
        {
            "message": (
                "Captured baccarat launch package: "
                f"{_launch_summary_text(bundle['safe_summary'])}, "
                f"localStorage={len(local_storage_items)}"
            )
        },
    )
    _emit(
        event_queue,
        config.instance_id,
        "health",
        {
            "game_launch_context": "captured",
            "launch": bundle["safe_summary"],
            "local_storage_items": len(local_storage_items),
        },
    )
    return bundle


async def _handoff_to_headless(config, runtime, pw, state, event_queue, ws_parser):
    if pw is None:
        _emit(event_queue, config.instance_id, "error", {"message": "headless handoff failed: playwright not started"})
        return False
    bundle = runtime.get("launch_bundle")
    if not bundle:
        bundle = await _capture_game_launch_context(config, runtime, event_queue)
    if not bundle:
        return False

    standard_viewport = _baccarat_standard_viewport()
    launch_options: dict[str, Any] = {"headless": True}
    proxy = _playwright_proxy(config.proxy)
    if proxy:
        launch_options["proxy"] = proxy
    if config.browser_channel:
        launch_options["channel"] = config.browser_channel
    launch_options["args"] = [
        f"--window-size={standard_viewport['width']},{standard_viewport['height']}",
        "--force-device-scale-factor=1",
    ]

    target_browser = None
    target_context = None
    try:
        _emit(
            event_queue,
            config.instance_id,
            "log",
            {"message": f"Starting headless baccarat handoff: {_launch_summary_text(bundle['safe_summary'])}"},
        )
        _emit(
            event_queue,
            config.instance_id,
            "health",
            {
                "game_launch_context": "headless_launching",
                "handoff_step": "launching_headless_browser",
                "launch": bundle["safe_summary"],
            },
        )
        try:
            target_browser = await pw.chromium.launch(**launch_options)
        except Exception as exc:
            if not config.browser_channel:
                raise
            _emit(
                event_queue,
                config.instance_id,
                "log",
                {"message": f"Chrome channel unavailable for headless handoff, fallback to Chromium: {type(exc).__name__}"},
            )
            launch_options.pop("channel", None)
            target_browser = await pw.chromium.launch(**launch_options)

        context_options: dict[str, Any] = {
            "viewport": dict(standard_viewport),
            "screen": dict(standard_viewport),
            "device_scale_factor": 1,
            "ignore_https_errors": True,
        }
        storage_state = bundle.get("storage_state")
        if storage_state:
            context_options["storage_state"] = storage_state
        user_agent = str(bundle.get("user_agent") or "")
        if user_agent:
            context_options["user_agent"] = user_agent
        target_context = await target_browser.new_context(**context_options)
        await _install_context_probes(config, target_context)
        await install_local_storage_init_script(target_context, bundle.get("local_storage_items") or [])
        target_page = await target_context.new_page()
        await target_page.set_viewport_size(dict(standard_viewport))
        _attach_network_collectors(config, target_context, target_page, state, event_queue, ws_parser)

        started = time.time()
        await target_page.goto(str(bundle["url"]), wait_until="domcontentloaded", timeout=60000)
        await target_page.set_viewport_size(dict(standard_viewport))
        _emit(
            event_queue,
            config.instance_id,
            "health",
            {
                "game_launch_context": "document_opened",
                "handoff_step": "opened_real_document_url",
                "launch": bundle["safe_summary"],
            },
        )
        _emit(
            event_queue,
            config.instance_id,
            "health",
            {
                "game_launch_context": "locating_hall_buttons",
                "handoff_step": "locating_four_room_buttons",
                "launch": bundle["safe_summary"],
            },
        )
        ready, hall_entry_buttons = await _wait_for_headless_hall_entry_buttons(
            config,
            event_queue,
            target_page,
            timeout_ms=240000,
            poll_ms=500,
        )
        elapsed_ms = int((time.time() - started) * 1000)
        geometry_issue = _standard_hall_buttons_issue(hall_entry_buttons)
        if geometry_issue:
            debug_path = await _save_headless_handoff_debug(target_page, config.instance_id, ready)
            _emit(
                event_queue,
                config.instance_id,
                "error",
                {
                    "message": (
                        f"headless handoff timeout: buttons={len(hall_entry_buttons)}, "
                        f"geometry={geometry_issue}, {ready.safe_summary()}, debug={debug_path or '-'}"
                    )
                },
            )
            await _close_context_and_browser(target_context, target_browser)
            return False

        old_context = runtime.get("context")
        old_browser = runtime.get("browser")
        runtime.update(
            {
                "context": target_context,
                "browser": target_browser,
                "page": target_page,
                "mode": "headless",
                "headless_hall_ready": bool(ready.hall_ready),
                "hall_entry_buttons": hall_entry_buttons,
                "launch_bundle": bundle,
            }
        )
        _emit(
            event_queue,
            config.instance_id,
            "health",
            {
                "game_launch_context": "headless_ready",
                "mode": "headless",
                "elapsed_ms": elapsed_ms,
                "ready": ready.safe_summary(),
                "hall_entry_buttons": hall_entry_buttons,
                "hall_entry_button_count": len(hall_entry_buttons),
                "hall_entry_geometry_issue": "",
                "coordinate_base_size": {
                    "width": BACCARAT_COORDINATE_BASE_SIZE[0],
                    "height": BACCARAT_COORDINATE_BASE_SIZE[1],
                },
            },
        )
        _emit(
            event_queue,
            config.instance_id,
            "log",
            {
                "message": (
                    "Headless baccarat ready: "
                    f"scene={ready.scene_name or '-'}, rooms={ready.room_count}, "
                    f"buttons={len(hall_entry_buttons)}, elapsed={elapsed_ms}ms"
                )
            },
        )
        await _close_replaced_browser(old_context, old_browser, target_context, target_browser)
        return True
    except Exception as exc:
        if target_context is not None or target_browser is not None:
            await _close_context_and_browser(target_context, target_browser)
        _emit(
            event_queue,
            config.instance_id,
            "error",
            {"message": f"headless handoff failed: {type(exc).__name__}", "traceback": traceback.format_exc()},
        )
        return False


async def _resolve_hall_entry_candidates(page, room_index: int) -> list[dict[str, Any]]:
    canvas_candidates = await resolve_baccarat_hall_room_entry_candidates(page, room_index)
    visual_candidates = await _detect_hall_entry_candidates_from_screenshot(page, room_index)
    combined: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for candidate in [*canvas_candidates, *visual_candidates]:
        try:
            key = (int(candidate["x"]), int(candidate["y"]))
        except Exception:
            continue
        if key in seen:
            continue
        seen.add(key)
        combined.append(candidate)
    return combined


async def _wait_for_headless_hall_entry_buttons(
    config: ClusterWorkerConfig,
    event_queue: mp.Queue,
    page,
    *,
    timeout_ms: int,
    poll_ms: int,
) -> tuple[Any, list[dict[str, Any]]]:
    started = time.time()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(1, timeout_ms) / 1000.0
    last_emit = 0.0
    last = await read_baccarat_load_state(page)
    buttons: list[dict[str, Any]] = []
    geometry_issue = "not_checked"
    while True:
        last = await read_baccarat_load_state(page)
        if last.hall_ready:
            buttons = await resolve_baccarat_hall_entry_buttons(page)
            geometry_issue = _standard_hall_buttons_issue(buttons)
            if not geometry_issue:
                return last, buttons
        now = loop.time()
        if now - last_emit >= 5.0:
            elapsed_ms = int((time.time() - started) * 1000)
            loading_state = "locating_hall_buttons" if last.hall_ready else "loading_real_document"
            _emit(
                event_queue,
                config.instance_id,
                "health",
                {
                    "game_launch_context": loading_state,
                    "handoff_step": loading_state,
                    "elapsed_ms": elapsed_ms,
                    "hall_entry_button_count": len(buttons),
                    "hall_entry_geometry_issue": geometry_issue,
                    "coordinate_base_size": {
                        "width": BACCARAT_COORDINATE_BASE_SIZE[0],
                        "height": BACCARAT_COORDINATE_BASE_SIZE[1],
                    },
                    "ready": last.safe_summary(),
                },
            )
            last_emit = now
        if now >= deadline:
            return last, buttons
        await page.wait_for_timeout(max(100, int(poll_ms)))


def _hall_button_click_candidates(button: Mapping[str, Any]) -> list[dict[str, Any]]:
    try:
        base_x = int(button["x"])
        base_y = int(button["y"])
    except Exception:
        return []
    try:
        room_index = int(button.get("room_index") or 0)
    except Exception:
        room_index = 0
    candidates: list[dict[str, Any]] = []
    offsets: list[tuple[int, int]] = []
    seen_offsets: set[tuple[int, int]] = set()
    for dx, dy in (
        (0, 0),
        (-10, 0),
        (10, 0),
        (0, -6),
        (0, 6),
        (-18, 0),
        (18, 0),
    ):
        key = (dx, dy)
        if key in seen_offsets:
            continue
        seen_offsets.add(key)
        offsets.append(key)
    for index, (dx, dy) in enumerate(offsets):
        candidate = dict(button)
        candidate.update(
            {
                "source": (
                    f"center_{button.get('source') or 'hall_button'}"
                    if (dx, dy) == (0, 0)
                    else f"cached_{button.get('source') or 'hall_button'}"
                ),
                "attempt_index": index,
                "x": base_x + dx,
                "y": base_y + dy,
                "dx": dx,
                "dy": dy,
            }
        )
        candidates.append(candidate)
    return candidates


_HALL_ROOM_ID_KEYS = {"roomid", "roomno", "tableid", "tableno", "deskid", "deskno", "sn", "cid"}


def _hall_room_label_from_text(value: object) -> str:
    text = str(value or "").strip().upper()
    direct = _normalized_room_label(text)
    if direct:
        return direct
    match = re.search(r"\bT(\d{3,4})\b", text)
    if match:
        return f"T{int(match.group(1)):03d}"
    return ""


def _hall_room_identity_from_fields(
    context_path: str,
    fields: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    room_id = ""
    room_label = ""
    for field in fields:
        key = str(field.get("key") or "").lower()
        path = str(field.get("path") or context_path or "").lower()
        value = field.get("value")
        label = _hall_room_label_from_text(value)
        if label and not room_label:
            room_label = label
        text = str(value or "").strip()
        if key in _HALL_ROOM_ID_KEYS:
            if key in {"sn", "cid"} and not any(token in path for token in ("gamelist", "room", "scene", "table", "desk")):
                continue
            if re.fullmatch(r"\d{1,12}", text):
                room_id = room_id or text[:20]
    if not room_label and room_id:
        room_label = _room_label_hint_from_id(room_id)
    room_index = _hall_room_index_from_identity(context_path, room_id=room_id, room_label=room_label)
    return {
        "room_index": room_index,
        "room_id": room_id,
        "room_label": room_label,
        "context_path": str(context_path or "")[:220],
    }


def _hall_room_index_from_identity(context_path: str = "", *, room_id: object = "", room_label: object = "") -> int | None:
    label = _hall_room_label_from_text(room_label) or _room_label_hint_from_id(room_id)
    match = re.fullmatch(r"T(\d{3,4})", label or "")
    if match:
        index = int(match.group(1))
        if 1 <= index <= 4:
            return index
    numbers = [int(item) for item in re.findall(r"(?<!\d)(\d{1,3})(?!\d)", str(context_path or ""))]
    for value in reversed(numbers):
        if 0 <= value <= 3:
            return value + 1
        if 1 <= value <= 4:
            return value
    return None


def _field_groups_from_frontend_raw(raw: Mapping[str, Any]) -> list[tuple[str, list[Mapping[str, Any]]]]:
    groups: list[tuple[str, list[Mapping[str, Any]]]] = []
    for context in raw.get("contexts") or []:
        if not isinstance(context, Mapping):
            continue
        path = str(context.get("path") or "")
        fields = [field for field in context.get("fields") or [] if isinstance(field, Mapping)]
        if fields:
            groups.append((path, fields))
    if groups:
        return groups
    by_parent: dict[str, list[Mapping[str, Any]]] = {}
    for hit in raw.get("hits") or []:
        if not isinstance(hit, Mapping):
            continue
        path = str(hit.get("path") or "")
        parent = path.rsplit(".", 1)[0] if "." in path else str(raw.get("source") or raw.get("event") or "")
        by_parent.setdefault(parent, []).append(hit)
    return list(by_parent.items())


def _hall_room_identities_from_raw_events(raw_events: Sequence[Mapping[str, Any]]) -> dict[int, dict[str, Any]]:
    identities: dict[int, dict[str, Any]] = {}
    for raw in raw_events:
        for context_path, fields in _field_groups_from_frontend_raw(raw):
            identity = _hall_room_identity_from_fields(context_path, fields)
            room_index = identity.get("room_index")
            if not isinstance(room_index, int) or room_index not in {1, 2, 3, 4}:
                continue
            if not (identity.get("room_id") or identity.get("room_label")):
                continue
            identities.setdefault(room_index, identity)
    return identities


async def _scan_hall_room_identities(page) -> dict[int, dict[str, Any]]:
    raw_events: list[dict[str, Any]] = []
    try:
        frames = list(page.frames)
    except Exception:
        frames = []
    for frame_index, frame in enumerate(frames):
        for script in (FRONTEND_BOUND_ROOM_SCAN_JS, FRONTEND_OBJECT_SCAN_JS):
            try:
                raw = await frame.evaluate(script)
            except Exception:
                raw = None
            if isinstance(raw, dict):
                raw.setdefault("frame_index", frame_index)
                raw.setdefault("frame_url", str(getattr(frame, "url", "") or "")[:240])
                raw_events.append(raw)
    return _hall_room_identities_from_raw_events(raw_events)


async def _drain_frontend_probe_raw_events(page) -> list[dict[str, Any]]:
    raw_events: list[dict[str, Any]] = []
    try:
        frames = list(page.frames)
    except Exception:
        frames = []
    for frame_index, frame in enumerate(frames):
        try:
            await frame.evaluate(FRONTEND_STATE_PROBE_JS)
        except Exception:
            pass
        try:
            drained = await frame.evaluate(
                "() => window.__betDesktopDrainFrontendStateEvents ? window.__betDesktopDrainFrontendStateEvents() : []"
            )
        except Exception:
            drained = []
        for raw in drained if isinstance(drained, list) else []:
            if isinstance(raw, dict):
                raw.setdefault("frame_index", frame_index)
                raw.setdefault("frame_url", str(getattr(frame, "url", "") or "")[:240])
                raw_events.append(raw)
    return raw_events


def _frontend_event_matches_hall_entry_room(
    event: FrontendStateEvent,
    room_index: int,
    expected_identity: Mapping[str, Any] | None,
) -> bool:
    if not (event.room_id or event.room_label):
        return False
    expected = expected_identity or {}
    expected_id = str(expected.get("room_id") or "").strip()
    expected_label = _hall_room_label_from_text(expected.get("room_label")) or _room_label_hint_from_id(expected_id)
    event_label = _hall_room_label_from_text(event.room_label) or _room_label_hint_from_id(event.room_id)
    if expected_id and event.room_id and str(event.room_id) == expected_id:
        return True
    if expected_label and event_label and event_label == expected_label:
        return True
    return _hall_room_index_from_identity(event.context_path, room_id=event.room_id, room_label=event.room_label) == room_index


def _room_identity_log_text(identity: Mapping[str, Any] | None) -> str:
    if not identity:
        return "-"
    room_label = str(identity.get("room_label") or "")
    room_id = str(identity.get("room_id") or "")
    if room_label and room_id:
        return f"{room_label}/{room_id}"
    return room_label or room_id or "-"


def _hall_entry_loading_clip(width: int, height: int, *, clip_width: int = 180, clip_height: int = 160) -> dict[str, int]:
    return {
        "x": max(0, int((width - clip_width) / 2)),
        "y": max(0, int((height - clip_height) / 2)),
        "width": min(clip_width, max(1, width)),
        "height": min(clip_height, max(1, height)),
    }


def _hall_entry_gray_ratio(image) -> float:
    pixels = image.convert("RGB").getdata()
    total = 0
    grayish = 0
    for r, g, b in pixels:
        total += 1
        if abs(r - g) <= 14 and abs(g - b) <= 14 and 55 <= r <= 205:
            grayish += 1
    return grayish / max(1, total)


def _hall_entry_diff_metrics(before, after) -> dict[str, float]:
    if ImageChops is None:
        return {"mean_diff": 0.0, "changed_ratio": 0.0}
    diff = ImageChops.difference(before, after).convert("RGB")
    total = 0
    changed = 0
    diff_sum = 0
    for r, g, b in diff.getdata():
        total += 1
        value = max(r, g, b)
        diff_sum += value
        if value >= 28:
            changed += 1
    return {
        "mean_diff": round(diff_sum / max(1, total), 2),
        "changed_ratio": round(changed / max(1, total), 4),
    }


def _hall_entry_visual_reason(metrics: Mapping[str, float], gray_delta: float) -> str:
    if float(metrics.get("mean_diff") or 0) >= 12 and float(metrics.get("changed_ratio") or 0) >= 0.10:
        return "visual_loading"
    if gray_delta >= 0.08 and float(metrics.get("mean_diff") or 0) >= 7:
        return "gray_loading_overlay"
    if float(metrics.get("changed_ratio") or 0) >= 0.18:
        return "visual_changed_area"
    return ""


async def _capture_hall_entry_visual_baseline(page) -> dict[str, Any]:
    if Image is None or ImageChops is None:
        return {"ok": False, "reason": "pil_unavailable"}
    try:
        started = now_ms()
        png = await page.screenshot(type="png", full_page=False)
        screenshot_ms = now_ms() - started
        image = Image.open(BytesIO(png)).convert("RGB")
        width, height = image.size
        clip = _hall_entry_loading_clip(width, height)
        crop = image.crop((clip["x"], clip["y"], clip["x"] + clip["width"], clip["y"] + clip["height"]))
        return {
            "ok": True,
            "clip": clip,
            "crop": crop,
            "gray_ratio": _hall_entry_gray_ratio(crop),
            "screenshot_ms": screenshot_ms,
        }
    except Exception as exc:
        return {"ok": False, "reason": f"visual_baseline_failed:{type(exc).__name__}"}


async def _wait_for_hall_entry_visual_confirmation(
    page,
    baseline: Mapping[str, Any],
    *,
    timeout_ms: int = 1500,
    poll_ms: int = 40,
) -> dict[str, Any]:
    if not baseline.get("ok") or Image is None:
        return {"confirmed": False, "reason": str(baseline.get("reason") or "visual_baseline_unavailable")}
    clip = baseline.get("clip")
    before_crop = baseline.get("crop")
    if not isinstance(clip, Mapping) or before_crop is None:
        return {"confirmed": False, "reason": "visual_baseline_invalid"}

    started = now_ms()
    deadline = started + max(1, int(timeout_ms))
    samples = 0
    last_sample: dict[str, Any] = {}
    while now_ms() <= deadline:
        samples += 1
        sample_started = now_ms()
        png = await page.screenshot(type="png", full_page=False, clip=dict(clip))
        shot_ms = now_ms() - sample_started
        image = Image.open(BytesIO(png)).convert("RGB")
        metrics = _hall_entry_diff_metrics(before_crop, image)
        gray = _hall_entry_gray_ratio(image)
        gray_delta = round(gray - float(baseline.get("gray_ratio") or 0), 4)
        reason = _hall_entry_visual_reason(metrics, gray_delta)
        last_sample = {
            "ms_after_click": sample_started - started,
            "screenshot_ms": shot_ms,
            "analysis_ms": now_ms() - sample_started,
            "metrics": metrics,
            "gray_delta": gray_delta,
            "clip": dict(clip),
        }
        if reason:
            return {
                "confirmed": True,
                "reason": reason,
                "ms_after_click": last_sample["ms_after_click"],
                "total_ms": now_ms() - started,
                "sample_count": samples,
                "sample": last_sample,
            }
        await page.wait_for_timeout(max(20, int(poll_ms)))
    return {
        "confirmed": False,
        "reason": "visual_loading_timeout",
        "total_ms": now_ms() - started,
        "sample_count": samples,
        "sample": last_sample,
    }


async def _wait_for_hall_entry_click_confirmation(
    page,
    room_index: int,
    *,
    expected_identity: Mapping[str, Any] | None,
    before,
    timeout_ms: int = 5000,
    poll_ms: int = 200,
) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(1, timeout_ms) / 1000.0
    last_ready = before
    seen_events = 0
    while True:
        try:
            last_ready = await read_baccarat_load_state(page)
        except Exception:
            last_ready = before
        if getattr(last_ready, "game_ready", False):
            return {"confirmed": True, "reason": "game_ready", "ready": last_ready.safe_summary()}
        if getattr(before, "hall_ready", False) and not getattr(last_ready, "hall_ready", False):
            return {"confirmed": True, "reason": "hall_scene_left", "ready": last_ready.safe_summary()}
        if getattr(last_ready, "scene_name", "") and getattr(before, "scene_name", ""):
            if last_ready.scene_name != before.scene_name:
                return {"confirmed": True, "reason": "scene_changed", "ready": last_ready.safe_summary()}

        raw_events = await _drain_frontend_probe_raw_events(page)
        seen_events += len(raw_events)
        for raw in raw_events:
            matching_event = next(
                (
                    event
                    for event in parse_frontend_state_events(raw)
                    if _frontend_event_matches_hall_entry_room(event, room_index, expected_identity)
                ),
                None,
            )
            if matching_event:
                return {
                    "confirmed": True,
                    "reason": "room_identity_packet",
                    "room_id": matching_event.room_id,
                    "room_label": matching_event.room_label,
                    "context_path": matching_event.context_path,
                    "event_type": matching_event.event_type,
                    "ready": last_ready.safe_summary(),
                    "seen_events": seen_events,
                }
        if loop.time() >= deadline:
            return {
                "confirmed": False,
                "reason": "no_click_confirmation",
                "ready": last_ready.safe_summary() if hasattr(last_ready, "safe_summary") else {},
                "seen_events": seen_events,
            }
        await page.wait_for_timeout(max(50, int(poll_ms)))


async def _save_headless_handoff_debug(page, instance_id: str, ready) -> str:
    debug_dir = Path(__file__).resolve().parents[1] / "artifacts" / "headless_handoff_debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{instance_id}_{now_ms()}"
    screenshot_path = debug_dir / f"{stem}.png"
    json_path = debug_dir / f"{stem}.json"
    page_info: dict[str, Any] = {}
    try:
        page_info = await page.evaluate(
            """
() => ({
  href: String(location.href || ""),
  title: String(document.title || ""),
  readyState: document.readyState,
  innerWidth: window.innerWidth,
  innerHeight: window.innerHeight,
  dpr: window.devicePixelRatio,
  bodyText: String((document.body && document.body.innerText) || "").slice(0, 500),
  canvasCount: document.querySelectorAll("canvas").length,
  frameCount: window.frames ? window.frames.length : 0,
})
"""
        )
    except Exception as exc:
        page_info = {"page_info_error": type(exc).__name__}
    try:
        await page.screenshot(path=str(screenshot_path), type="png", full_page=False)
    except Exception:
        screenshot_path = Path("")
    payload = {
        "instance_id": instance_id,
        "ready": ready.safe_summary() if hasattr(ready, "safe_summary") else {},
        "page_info": page_info if isinstance(page_info, dict) else {},
        "screenshot_path": str(screenshot_path) if screenshot_path else "",
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(json_path)


async def _detect_hall_entry_candidates_from_screenshot(page, room_index: int) -> list[dict[str, Any]]:
    if Image is None:
        return []
    try:
        png = await page.screenshot(type="png", full_page=False)
    except Exception:
        return []
    try:
        image = Image.open(BytesIO(png)).convert("RGB")
    except Exception:
        return []

    width, height = image.size
    canvas_rect = await _best_hall_canvas_rect(page)
    if canvas_rect:
        left = int(canvas_rect.get("left", 0) + canvas_rect.get("width", width) * 0.72)
        right = int(canvas_rect.get("left", 0) + canvas_rect.get("width", width) * 0.97)
        top = int(canvas_rect.get("top", 0) + canvas_rect.get("height", height) * 0.30)
        bottom = int(canvas_rect.get("top", 0) + canvas_rect.get("height", height) * 0.92)
    else:
        left = int(width * 0.72)
        right = int(width * 0.97)
        top = int(height * 0.30)
        bottom = int(height * 0.92)
    left = max(0, min(left, width - 1))
    right = max(left + 1, min(right, width))
    top = max(0, min(top, height - 1))
    bottom = max(top + 1, min(bottom, height))

    crop_width = right - left
    crop_height = bottom - top
    pixels = image.load()
    visited: set[tuple[int, int]] = set()
    components: list[dict[str, Any]] = []

    def is_gold(px: tuple[int, int, int]) -> bool:
        r, g, b = px
        return (r >= 145 and g >= 95 and b <= 110 and r >= g and g >= b + 15) or (
            r >= 175 and g >= 125 and b <= 130 and r >= g
        )

    for cy in range(crop_height):
        for cx in range(crop_width):
            point = (cx, cy)
            if point in visited or not is_gold(pixels[left + cx, top + cy]):
                continue
            stack = [point]
            visited.add(point)
            min_x = max_x = cx
            min_y = max_y = cy
            area = 0
            while stack:
                x, y = stack.pop()
                area += 1
                min_x = min(min_x, x)
                max_x = max(max_x, x)
                min_y = min(min_y, y)
                max_y = max(max_y, y)
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if nx < 0 or ny < 0 or nx >= crop_width or ny >= crop_height:
                        continue
                    next_point = (nx, ny)
                    if next_point in visited or not is_gold(pixels[left + nx, top + ny]):
                        continue
                    visited.add(next_point)
                    stack.append(next_point)
            box_width = max_x - min_x + 1
            box_height = max_y - min_y + 1
            if area < 220 or box_width < 42 or box_height < 16 or box_width > 180 or box_height > 80:
                continue
            components.append(
                {
                    "left": left + min_x,
                    "top": top + min_y,
                    "right": left + max_x,
                    "bottom": top + max_y,
                    "area": area,
                    "width": box_width,
                    "height": box_height,
                    "center_x": left + (min_x + max_x) // 2,
                    "center_y": top + (min_y + max_y) // 2,
                }
            )

    rows: list[dict[str, Any]] = []
    for component in sorted(components, key=lambda item: item["center_y"]):
        if rows and abs(int(rows[-1]["center_y"]) - int(component["center_y"])) < 20:
            if int(component["area"]) > int(rows[-1]["area"]):
                rows[-1] = component
            continue
        rows.append(component)
    if not (1 <= room_index <= len(rows)):
        return []

    target = rows[room_index - 1]
    offsets = ((0, 0), (-10, 0), (10, 0), (0, -6), (0, 6), (-18, 0), (18, 0))
    return [
        {
            "ok": True,
            "source": "screenshot_gold_button",
            "room_index": room_index,
            "attempt_index": index,
            "x": int(target["center_x"] + dx),
            "y": int(target["center_y"] + dy),
            "dx": dx,
            "dy": dy,
            "detected_button": target,
            "detected_rows": len(rows),
            "canvas_rect": canvas_rect or {},
        }
        for index, (dx, dy) in enumerate(offsets)
    ]


async def _best_hall_canvas_rect(page) -> dict[str, Any] | None:
    buttons = await resolve_baccarat_hall_entry_buttons(page)
    for button in buttons:
        rect = button.get("canvas_rect")
        if isinstance(rect, dict) and int(rect.get("width") or 0) > 0 and int(rect.get("height") or 0) > 0:
            return rect
    return None


async def _save_hall_entry_debug(
    page,
    instance_id: str,
    room_index: int,
    candidates: Sequence[Mapping[str, Any]],
    attempts: Sequence[Mapping[str, Any]],
    ready,
) -> str:
    debug_dir = Path(__file__).resolve().parents[1] / "artifacts" / "hall_entry_debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{instance_id}_room{room_index}_{now_ms()}"
    image_path = debug_dir / f"{stem}.png"
    json_path = debug_dir / f"{stem}.json"
    try:
        png = await page.screenshot(type="png", full_page=False)
        if Image is not None and ImageDraw is not None:
            image = Image.open(BytesIO(png)).convert("RGB")
            draw = ImageDraw.Draw(image)
            for index, candidate in enumerate(candidates[:36], start=1):
                x = int(candidate.get("x") or 0)
                y = int(candidate.get("y") or 0)
                color = "yellow" if str(candidate.get("source")) == "screenshot_gold_button" else "red"
                radius = 5
                draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=color, width=2)
                if index <= 12:
                    draw.text((x + 7, y - 7), str(index), fill=color)
            image.save(image_path)
        else:
            image_path.write_bytes(png)
    except Exception:
        image_path = Path("")
    payload = {
        "instance_id": instance_id,
        "room_index": room_index,
        "ready": ready.safe_summary() if hasattr(ready, "safe_summary") else {},
        "attempts": list(attempts),
        "candidate_count": len(candidates),
        "candidates": list(candidates[:36]),
        "screenshot_path": str(image_path) if image_path else "",
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(json_path)


async def _enter_headless_room(config, runtime, event_queue, room_index: int, state: _RuntimeState | None = None) -> bool:
    if room_index not in {1, 2, 3, 4}:
        _emit(event_queue, config.instance_id, "error", {"message": f"enter room failed: invalid room {room_index}"})
        return False
    pending_room = runtime.get("room_entry_pending")
    if pending_room:
        _emit(
            event_queue,
            config.instance_id,
            "health",
            {"room_entry": "entering", "room_index": pending_room, "ready": {}},
        )
        _emit(
            event_queue,
            config.instance_id,
            "log",
            {"message": f"Room entry already pending: room={pending_room}"},
        )
        return False
    if runtime.get("mode") != "headless" or not runtime.get("headless_hall_ready"):
        _emit(
            event_queue,
            config.instance_id,
            "error",
            {"message": "enter room failed: headless hall is not ready"},
        )
        return False
    page = runtime.get("page")
    if page is None:
        _emit(event_queue, config.instance_id, "error", {"message": "enter room failed: no headless page"})
        return False
    await _force_baccarat_standard_page_viewport(page)
    before = await read_baccarat_load_state(page)
    if not before.hall_ready:
        _emit(
            event_queue,
            config.instance_id,
            "error",
            {"message": f"enter room failed: current scene is not hall: {before.safe_summary()}"},
        )
        return False
    room_identities = await _scan_hall_room_identities(page)
    expected_identity = room_identities.get(room_index)
    live_candidates = await _resolve_hall_entry_candidates(page, room_index)
    cached_buttons = runtime.get("hall_entry_buttons") if isinstance(runtime.get("hall_entry_buttons"), list) else []
    cached_button = next(
        (
            item
            for item in cached_buttons
            if isinstance(item, Mapping) and int(item.get("room_index") or 0) == room_index
        ),
        None,
    )
    cached_candidates = _hall_button_click_candidates(cached_button) if cached_button else []
    standard_live_candidates = _standard_hall_candidates(live_candidates)
    standard_cached_candidates = _standard_hall_candidates(cached_candidates)
    candidates = standard_live_candidates or standard_cached_candidates
    if not candidates:
        geometry_issue = _standard_hall_buttons_issue(cached_buttons) if cached_buttons else "no_cached_hall_buttons"
        if live_candidates:
            geometry_issue = _standard_hall_candidate_issue(live_candidates[0]) or geometry_issue
        _emit(
            event_queue,
            config.instance_id,
            "error",
            {
                "message": (
                    f"enter room failed: no standard 960x620 hall entry center for room {room_index}: "
                    f"{geometry_issue}"
                )
            },
        )
        return False

    candidate = candidates[0]
    room_lock_switched = False
    if state is not None:
        identity_for_lock = expected_identity or {}
        room_lock_switched = _switch_room_lock_for_entry(
            state,
            room_index=room_index,
            room_id=identity_for_lock.get("room_id", ""),
            room_label=identity_for_lock.get("room_label", ""),
        )
        if room_lock_switched:
            _emit_state(event_queue, state.snapshot())
    runtime["room_entry_pending"] = room_index
    ready = before
    attempts: list[dict[str, Any]] = []
    click_confirmation: dict[str, Any] = {}
    try:
        await _drain_frontend_probe_raw_events(page)
        visual_baseline = await _capture_hall_entry_visual_baseline(page)
        await _send_hall_entry_click(page, candidate)
        attempts.append(_hall_entry_attempt_summary(1, candidate, before, "center_one_click_sent"))
        _emit(
            event_queue,
            config.instance_id,
            "health",
            {
                "room_entry": "entering",
                "room_index": room_index,
                "ready": before.safe_summary(),
                "attempts": attempts[-5:],
                "debug_path": "",
                "click_confirmation": {"confirmed": False, "reason": "waiting_for_visual_loading"},
            },
        )
        _emit(
            event_queue,
            config.instance_id,
            "log",
            {
                "message": (
                    f"Room {room_index} entry click sent once: "
                    f"point=({int(candidate['x'])}, {int(candidate['y'])}), "
                    f"base_point={candidate.get('base_point') or '-'}, "
                    f"canvas_rect={candidate.get('canvas_rect') or '-'}, "
                    f"mode=center_one_click, expected={_room_identity_log_text(expected_identity)}"
                )
            },
        )
        click_confirmation = await _wait_for_hall_entry_visual_confirmation(
            page,
            visual_baseline,
            timeout_ms=1500,
            poll_ms=40,
        )
        if not click_confirmation.get("confirmed"):
            click_confirmation = await _wait_for_hall_entry_click_confirmation(
                page,
                room_index,
                expected_identity=expected_identity,
                before=before,
                timeout_ms=5000,
                poll_ms=200,
            )
        if state is not None and click_confirmation.get("confirmed"):
            identity_for_lock = click_confirmation if _room_identity_log_text(click_confirmation) != "-" else expected_identity
            identity_for_lock = identity_for_lock or {}
            room_lock_switched = _switch_room_lock_for_entry(
                state,
                room_index=room_index,
                room_id=identity_for_lock.get("room_id", ""),
                room_label=identity_for_lock.get("room_label", ""),
            )
            if room_lock_switched:
                _emit_state(event_queue, state.snapshot())
        _emit(
            event_queue,
            config.instance_id,
            "health",
            {
                "room_entry": "entering",
                "room_index": room_index,
                "ready": click_confirmation.get("ready") or before.safe_summary(),
                "attempts": attempts[-5:],
                "debug_path": "",
                "click_confirmation": click_confirmation,
            },
        )
        _emit(
            event_queue,
            config.instance_id,
            "log",
            {
                "message": (
                    f"Room {room_index} entry click confirmation: "
                    f"confirmed={bool(click_confirmation.get('confirmed'))}, "
                    f"reason={click_confirmation.get('reason') or '-'}, "
                    f"ms={click_confirmation.get('total_ms', click_confirmation.get('ms_after_click', '-'))}, "
                    f"room={_room_identity_log_text(click_confirmation) or _room_identity_log_text(expected_identity)}"
                )
            },
        )
        if click_confirmation.get("confirmed"):
            runtime["headless_hall_ready"] = False
            runtime["room_entry_loading_until_ms"] = now_ms() + 180000
            ready_summary = click_confirmation.get("ready") if isinstance(click_confirmation.get("ready"), dict) else {}
            _emit(
                event_queue,
                config.instance_id,
                "health",
                {
                    "room_entry": "click_confirmed",
                    "room_index": room_index,
                    "ready": ready_summary or before.safe_summary(),
                    "attempts": attempts[-5:],
                    "debug_path": "",
                    "click_confirmation": click_confirmation,
                },
            )
            _emit(
                event_queue,
                config.instance_id,
                "log",
                {
                    "message": (
                        f"Room {room_index} entry click accepted: "
                        f"reason={click_confirmation.get('reason') or '-'}, "
                        f"ms={click_confirmation.get('total_ms', click_confirmation.get('ms_after_click', '-'))}"
                    )
                },
            )
            return True

        ready = await _wait_for_baccarat_game_scene(page, timeout_ms=15000, poll_ms=500)
        if state is not None and ready.game_ready and not room_lock_switched:
            identity_for_lock = expected_identity or {}
            room_lock_switched = _switch_room_lock_for_entry(
                state,
                room_index=room_index,
                room_id=identity_for_lock.get("room_id", ""),
                room_label=identity_for_lock.get("room_label", ""),
            )
            if room_lock_switched:
                _emit_state(event_queue, state.snapshot())
        attempts.append(_hall_entry_attempt_summary(1, candidate, ready, "center_one_click"))
    except Exception as exc:
        _emit(
            event_queue,
            config.instance_id,
            "error",
            {"message": f"enter room failed after verified click path: {type(exc).__name__}"},
        )
        return False
    finally:
        runtime["room_entry_pending"] = None

    runtime["headless_hall_ready"] = bool(ready.hall_ready)
    debug_path = ""
    if not ready.game_ready:
        debug_path = await _save_hall_entry_debug(page, config.instance_id, room_index, candidates[:1], attempts, ready)
    _emit(
        event_queue,
        config.instance_id,
        "health",
        {
            "room_entry": "game_ready" if ready.game_ready else "timeout",
            "room_index": room_index,
            "ready": ready.safe_summary(),
            "attempts": attempts[-5:],
            "debug_path": debug_path,
            "click_confirmation": click_confirmation,
        },
    )
    _emit(
        event_queue,
        config.instance_id,
        "log",
        {
            "message": (
                f"Room {room_index} entry confirmed: attempts={len(attempts)}, "
                f"game_ready={ready.game_ready}, scene={ready.scene_name or '-'}, debug={debug_path or '-'}"
            )
        },
    )
    return bool(ready.game_ready)


async def _click_hall_entry_candidate(
    page,
    candidate: Mapping[str, Any],
    *,
    timeout_ms: int,
    poll_ms: int,
):
    await _send_hall_entry_click(page, candidate)
    return await _wait_for_baccarat_game_scene(page, timeout_ms=timeout_ms, poll_ms=poll_ms)


async def _send_hall_entry_click(page, candidate: Mapping[str, Any]) -> None:
    x = int(candidate["x"])
    y = int(candidate["y"])
    await page.mouse.move(x, y)
    await page.wait_for_timeout(120)
    await page.mouse.down()
    await page.wait_for_timeout(80)
    await page.mouse.up()


def _hall_entry_attempt_summary(
    attempt_number: int,
    candidate: Mapping[str, Any],
    ready,
    click_style: str,
) -> dict[str, Any]:
    return {
        "attempt": attempt_number,
        "source": candidate.get("source"),
        "x": candidate.get("x"),
        "y": candidate.get("y"),
        "frame_index": candidate.get("frame_index"),
        "dx": candidate.get("dx"),
        "dy": candidate.get("dy"),
        "click_style": click_style,
        "game_ready": ready.game_ready,
        "hall_ready": ready.hall_ready,
        "scene_name": ready.scene_name,
        "room_count": ready.room_count,
    }


async def _report_headless_status(config, runtime, event_queue) -> bool:
    mode = str(runtime.get("mode") or "")
    page = runtime.get("page")
    if mode != "headless" or page is None:
        runtime["headless_hall_ready"] = False
        runtime["hall_entry_buttons"] = []
        _emit(
            event_queue,
            config.instance_id,
            "health",
            {
                "game_launch_context": "headless_status",
                "mode": mode or "visible",
                "headless_active": False,
                "headless_hall_ready": False,
                "hall_entry_button_count": 0,
            },
        )
        _emit(event_queue, config.instance_id, "log", {"message": "Headless status refreshed: inactive"})
        return False

    try:
        ready = await read_baccarat_load_state(page)
        hall_entry_buttons: list[dict[str, Any]] = []
        if ready.hall_ready:
            hall_entry_buttons = await resolve_baccarat_hall_entry_buttons(page)
        geometry_issue = _standard_hall_buttons_issue(hall_entry_buttons)
        runtime["headless_hall_ready"] = bool(ready.hall_ready and not geometry_issue)
        runtime["hall_entry_buttons"] = hall_entry_buttons
        _emit(
            event_queue,
            config.instance_id,
            "health",
            {
                "game_launch_context": "headless_status",
                "mode": "headless",
                "headless_active": True,
                "headless_hall_ready": runtime["headless_hall_ready"],
                "ready": ready.safe_summary(),
                "hall_entry_button_count": len(hall_entry_buttons),
                "hall_entry_geometry_issue": geometry_issue,
                "coordinate_base_size": {
                    "width": BACCARAT_COORDINATE_BASE_SIZE[0],
                    "height": BACCARAT_COORDINATE_BASE_SIZE[1],
                },
                "hall_entry_buttons": hall_entry_buttons,
            },
        )
        _emit(
            event_queue,
            config.instance_id,
            "log",
            {
                "message": (
                    "Headless status refreshed: "
                    f"scene={ready.scene_name or '-'}, "
                    f"hall_ready={ready.hall_ready}, game_ready={ready.game_ready}, "
                    f"buttons={len(hall_entry_buttons)}"
                )
            },
        )
        return True
    except Exception as exc:
        runtime["headless_hall_ready"] = False
        runtime["hall_entry_buttons"] = []
        _emit(
            event_queue,
            config.instance_id,
            "health",
            {
                "game_launch_context": "headless_status",
                "mode": "headless_error",
                "headless_active": False,
                "headless_hall_ready": False,
                "hall_entry_button_count": 0,
            },
        )
        _emit(
            event_queue,
            config.instance_id,
            "error",
            {"message": f"headless status refresh failed: {type(exc).__name__}: {exc}"},
        )
        return False


async def _release_headless_session(config, runtime, event_queue) -> bool:
    was_headless = runtime.get("mode") == "headless"
    context = runtime.get("context") if was_headless else None
    browser = runtime.get("browser") if was_headless else None
    if context is not None or browser is not None:
        await _close_context_and_browser(context, browser)
    runtime.update(
        {
            "context": None,
            "browser": None,
            "page": None,
            "mode": "released",
            "headless_hall_ready": False,
            "hall_entry_buttons": [],
            "launch_bundle": None,
        }
    )
    _emit(
        event_queue,
        config.instance_id,
        "health",
        {
            "game_launch_context": "headless_released",
            "mode": "released",
            "headless_active": False,
            "headless_hall_ready": False,
            "hall_entry_button_count": 0,
        },
    )
    _emit(event_queue, config.instance_id, "log", {"message": "Headless baccarat session released"})
    return bool(was_headless)


async def _wait_for_baccarat_game_scene(page, *, timeout_ms: int = 60000, poll_ms: int = 500):
    deadline = asyncio.get_running_loop().time() + max(1, timeout_ms) / 1000.0
    last = await read_baccarat_load_state(page)
    while True:
        last = await read_baccarat_load_state(page)
        if last.game_ready:
            return last
        if asyncio.get_running_loop().time() >= deadline:
            return last
        await page.wait_for_timeout(max(100, int(poll_ms)))


def _launch_summary_text(summary: Mapping[str, Any]) -> str:
    host = str(summary.get("host") or "-")
    url_hash = str(summary.get("url_hash") or "-")
    length = summary.get("length", 0)
    keys = ",".join(str(key) for key in summary.get("query_keys", [])[:8])
    return f"host={host}, hash={url_hash}, length={length}, keys={keys}"


async def _close_replaced_browser(old_context, old_browser, new_context, new_browser) -> None:
    if old_context is not None and old_context is not new_context:
        try:
            await old_context.close()
        except Exception:
            pass
    if old_browser is not None and old_browser is not new_browser:
        try:
            await old_browser.close()
        except Exception:
            pass


async def _close_context_and_browser(context, browser) -> None:
    if context is not None:
        try:
            await context.close()
        except Exception:
            pass
    if browser is not None:
        try:
            await browser.close()
        except Exception:
            pass


def _playwright_proxy(proxy_dict):
    if not proxy_dict or not proxy_dict.get("host"): return None
    host = str(proxy_dict.get("host") or "").strip()
    port = str(proxy_dict.get("port") or "").strip()
    server = host if re.match(r"^[a-z]+://", host, re.I) else f"http://{host}"
    if port and ":" not in server.rsplit("/", 1)[-1]:
        server = f"{server}:{port}"
    p = {"server": server}
    if proxy_dict.get("username"):
        p["username"] = proxy_dict["username"]
        p["password"] = proxy_dict["password"]
    return p


def _load_ws_parser(path):
    if path:
        try:
            module_name, _, attr = path.partition(":")
            module = importlib.import_module(module_name)
            parser_cls = getattr(module, attr) if attr else module
            parser = parser_cls() if isinstance(parser_cls, type) else parser_cls
            if isinstance(parser, AbstractWSParser):
                return parser
        except Exception:
            return DEFAULT_WS_PARSER
    return DEFAULT_WS_PARSER


class ClusterProcessController:
    def __init__(self, configs):
        self.configs = configs
        self._processes = {}
        self._event_queue = mp.Queue()
        self._command_queues = {}
        self._stop_events = {}

    def start(self):
        self.start_configs(self.configs)

    def start_configs(self, configs):
        for cfg in configs:
            if cfg.instance_id in self._processes and self._processes[cfg.instance_id].is_alive():
                continue
            cmd_q = mp.Queue()
            stop_evt = mp.Event()
            p = mp.Process(target=_worker_process_entry, args=(cfg, self._event_queue, cmd_q, stop_evt))
            p.start()
            self._processes[cfg.instance_id] = p
            self._command_queues[cfg.instance_id] = cmd_q
            self._stop_events[cfg.instance_id] = stop_evt
        known = {cfg.instance_id: cfg for cfg in self.configs}
        for cfg in configs:
            known[cfg.instance_id] = cfg
        self.configs = list(known.values())

    def running_instance_ids(self):
        return {
            instance_id
            for instance_id, process in self._processes.items()
            if process.is_alive()
        }

    def stop_instances(self, instance_ids):
        target_ids = {str(instance_id) for instance_id in instance_ids}
        for instance_id in target_ids:
            evt = self._stop_events.get(instance_id)
            if evt is not None:
                evt.set()
        for instance_id in target_ids:
            process = self._processes.get(instance_id)
            if process is None:
                continue
            process.join(timeout=3)
            if process.is_alive():
                process.terminate()
                process.join(timeout=1)
            self._processes.pop(instance_id, None)
            self._command_queues.pop(instance_id, None)
            self._stop_events.pop(instance_id, None)
        self.configs = [cfg for cfg in self.configs if cfg.instance_id not in target_ids]

    def stop(self):
        for evt in self._stop_events.values(): evt.set()
        for p in self._processes.values(): p.join(timeout=2)

    def poll_events(self, max_items=128):
        events = []
        for _ in range(max_items):
            try: events.append(self._event_queue.get_nowait())
            except queue.Empty: break
        return events

    def send_command(self, instance_id, cmd):
        if instance_id in self._command_queues:
            self._command_queues[instance_id].put(cmd)
            return True
        return False


def _worker_process_entry(config, event_queue, command_queue, stop_event):
    asyncio.run(_run_worker(config, event_queue, command_queue, stop_event))
