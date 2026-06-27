"""Adapter that binds the lightweight UI to the interval-probe runtime core."""

from __future__ import annotations

import asyncio
import json
import math
import re
import sys
import threading
import time
from collections import deque
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Coroutine

from bet_desktop.ui.lightweight_browser_adapter import BrowserControlAdapter
from bet_desktop.ui.lightweight_models import PlatformSlot
from scripts import live_interval_acceptance_probe as probe


def _project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


PROJECT_ROOT = _project_root()
DEFAULT_ARTIFACT_ROOT = PROJECT_ROOT / "dist" / "BetDesktop" / "bet_desktop" / "artifacts"
DEFAULT_PROFILE_ROOT = DEFAULT_ARTIFACT_ROOT / "profiles"
DEFAULT_LOG_ROOT = PROJECT_ROOT / "dist" / "BetDesktop" / "live_logs"
UI_REFERENCE_BETTING_COUNTDOWN_SECONDS = 12
STABLE_BET_DENOMINATIONS = tuple(probe.DENOMINATIONS)
MANUAL_EXCLUDE_STATES = {"pending_exclude", "excluded"}
MANUAL_RESTORE_STATES = {"pending_restore"}
PLAN_STATE_STALE_MS = 15_000
LIMIT_LABEL_KEYS = (
    "limit_label",
    "runtime_limit_label",
    "frontend_limit_label",
    "canvas_limit_label",
    "label_limit_label",
    "table_limit_label",
    "runtime_table_limit",
)


class _MirroringEventQueue:
    def __init__(self, base: Any, emit_event: Any) -> None:
        self._base = base
        self._emit_event = emit_event

    def put_nowait(self, event: Any) -> None:
        self._base.put_nowait(event)
        try:
            payload = probe.event_to_json(event)
        except Exception:
            return
        if str(payload.get("event_type") or "") in {"health", "error", "log"}:
            self._emit_event(payload)


def normalize_login_url(url: str) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    if "://" in value:
        return value
    return f"https://{value}"


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _money_from_cents(value: Any) -> str:
    cents = _safe_int(value)
    if cents is None:
        return ""
    return f"{cents / 100:.2f}"


SIDE_TEXT_CN = {"banker": "庄", "player": "闲", "tie": "和"}


def _side_text(side: Any) -> str:
    value = str(side or "").strip()
    return SIDE_TEXT_CN.get(value, value)


def _status_balance_cents(status: dict[str, Any] | None) -> int | None:
    if not isinstance(status, dict):
        return None
    for key in ("display_balance_cents", "balance_cents"):
        balance = _safe_int(status.get(key))
        if balance is not None:
            return balance
    return None


def _balance_covers(status: dict[str, Any] | None, amount: int, min_balance_yuan: int = 0) -> bool:
    balance = _status_balance_cents(status)
    if balance is None:
        return True
    required_cents = max(int(amount), int(min_balance_yuan or 0)) * 100
    return balance >= required_cents


def _positive_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        text = str(value).replace(",", "").strip()
        if not text:
            return None
        parsed = float(text)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed


def _parse_limit_label(value: Any) -> tuple[int, int] | None:
    text = str(value or "").replace(",", "").strip()
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*[-_/~]\s*(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    low = _positive_number(match.group(1))
    high = _positive_number(match.group(2))
    if low is None or high is None or high < low:
        return None
    return math.ceil(low), math.floor(high)


def _status_limit_sources(status: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(status, dict):
        return []
    sources = [status]
    for key in ("safe_summary", "summary", "runtime_frame", "display_summary"):
        value = status.get(key)
        if isinstance(value, dict):
            sources.append(value)
    return sources


def _status_amount_bounds(status: dict[str, Any] | None) -> tuple[int, int] | None:
    for source in _status_limit_sources(status):
        for low_key, high_key in (
            ("user_min_bet_cents", "user_max_bet_cents"),
            ("runtime_user_min_bet_cents", "runtime_user_max_bet_cents"),
            ("frontend_user_min_bet_cents", "frontend_user_max_bet_cents"),
        ):
            low_cents = _safe_int(source.get(low_key))
            high_cents = _safe_int(source.get(high_key))
            if low_cents is not None and high_cents is not None and high_cents >= low_cents > 0:
                return ((low_cents + 99) // 100, high_cents // 100)

        for low_key, high_key in (
            ("table_min", "table_max"),
            ("user_min_bet", "user_max_bet"),
            ("min_bet", "max_bet"),
        ):
            low = _positive_number(source.get(low_key))
            high = _positive_number(source.get(high_key))
            if low is not None and high is not None and high >= low:
                return (math.ceil(low), math.floor(high))

        for key in LIMIT_LABEL_KEYS:
            parsed = _parse_limit_label(source.get(key))
            if parsed is not None:
                return parsed
    return None


def _status_amount_allowed(status: dict[str, Any] | None, amount: int) -> bool:
    bounds = _status_amount_bounds(status)
    if bounds is None:
        return True
    low, high = bounds
    return low <= int(amount) <= high


def _status_reject_reason(status: dict[str, Any] | None, amount: int, min_balance_yuan: int) -> str:
    if not _balance_covers(status, amount, min_balance_yuan):
        return "余额不足"
    if not _status_amount_allowed(status, amount):
        return "超出限红"
    return ""


def _status_fresh(status: dict[str, Any] | None, *, now_ms: int | None = None) -> bool:
    if not isinstance(status, dict):
        return False
    timestamp_ms = _safe_int(
        status.get("status_ts_ms")
        or status.get("timestamp_captured_ms")
        or status.get("timestamp_ms")
    )
    if timestamp_ms is None:
        return True
    current_ms = int(now_ms or probe.now_ms())
    return max(0, current_ms - timestamp_ms) <= PLAN_STATE_STALE_MS


def _status_game_ready(status: dict[str, Any] | None) -> bool:
    return bool(isinstance(status, dict) and status.get("game_ready"))


def _status_betting_open(status: dict[str, Any] | None) -> bool:
    if not isinstance(status, dict):
        return False
    for key in (
        "display_betting_open",
        "betting_open",
        "runtime_betting_open",
        "runtime_is_can_betting",
        "frontend_runtime_is_can_betting",
    ):
        value = status.get(key)
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in {"1", "true", "yes", "open", "can_bet", "betting"}:
            return True
    return False


def _status_room_key(status: dict[str, Any] | None) -> str:
    if not isinstance(status, dict):
        return ""
    return _first_text(
        status.get("display_room_label"),
        status.get("room_label"),
        status.get("locked_room_label"),
        status.get("runtime_room_label"),
        status.get("frontend_room_label"),
        status.get("room_id"),
        status.get("locked_room_id"),
    )


def _status_round_key(status: dict[str, Any] | None) -> str:
    if not isinstance(status, dict):
        return ""
    return _public_game_no(
        _first_text(
            status.get("display_game_no"),
            status.get("game_no"),
            status.get("round_id"),
            status.get("batch_id"),
            status.get("frontend_batch_id"),
            status.get("runtime_memory_game_no"),
        )
    )


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text and text.lower() not in {"none", "null"}:
            return text
    return ""


def _public_game_no(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"none", "null", "-"}:
        return ""
    parts = text.split("-")
    if len(parts) >= 4 and parts[-1].strip().isdigit():
        return "-".join(part.strip() for part in parts[:3] if part.strip())
    return text


def _action_to_betting(raw_action: Any) -> bool | None:
    text = str(raw_action or "").strip().lower()
    if not text:
        return None
    if text.isdigit():
        try:
            code = int(text)
        except ValueError:
            return None
        if code == 3:
            return True
        if code == 5:
            return False
        return None
    if text in {"betting", "open", "can_bet", "canbet", "true", "yes"}:
        return True
    if text in {"closed", "close", "false", "no", "hall"}:
        return False
    return None


def _stable_chip_sequence(amount: int) -> list[int]:
    return probe.decompose_value(int(amount), STABLE_BET_DENOMINATIONS, max_steps=5)


def _amount_matches_bounds(amount: int, bounds: tuple[int, int] | None) -> bool:
    if bounds is None:
        return True
    low, high = bounds
    return int(low) <= int(amount) <= int(high)


def _split_stable_sub_amounts(
    amount: int,
    count: int,
    *,
    seed: int = 0,
    amount_bounds: tuple[tuple[int, int] | None, ...] | None = None,
) -> tuple[int, ...]:
    total = int(amount)
    slots = int(count)
    if slots <= 0:
        raise ValueError("sub account count must be positive")
    if total < slots * 4:
        raise ValueError(f"cannot split amount {total} into {slots} stable sub amounts")
    bounds = tuple(amount_bounds or ())
    if bounds and len(bounds) != slots:
        raise ValueError("sub account bounds must match sub account count")

    candidates: list[tuple[int, int, float, tuple[int, ...]]] = []

    def walk(remaining: int, remaining_slots: int, prefix: tuple[int, ...]) -> None:
        if remaining_slots == 1:
            value = remaining
            try:
                chips = [_stable_chip_sequence(item) for item in (*prefix, value)]
            except Exception:
                return
            values = (*prefix, value)
            if bounds and any(not _amount_matches_bounds(item, bound) for item, bound in zip(values, bounds)):
                return
            target = total / slots
            spread = max(values) - min(values)
            step_span = max(len(item) for item in chips) - min(len(item) for item in chips)
            deviation = sum(abs(item - target) for item in values)
            candidates.append((spread, step_span, deviation, values))
            return

        min_remaining = 4 * (remaining_slots - 1)
        for value in range(4, remaining - min_remaining + 1, 2):
            try:
                _stable_chip_sequence(value)
            except Exception:
                continue
            walk(remaining - value, remaining_slots - 1, (*prefix, value))

    walk(total, slots, ())
    if not candidates:
        raise ValueError(f"cannot split amount {total} into {slots} stable sub amounts")
    target = total / slots
    min_value = max(4, int(target * 0.35))
    max_value = max(min_value, int(target * 1.55))
    bounded = [
        item
        for item in candidates
        if min(item[3]) >= min_value and max(item[3]) <= max_value
    ]
    pool = bounded or candidates
    non_flat = [item for item in pool if item[0] > 0]
    pool = non_flat or pool
    ranked = sorted(pool, key=lambda item: (-item[0], item[1], item[2], item[3]))
    window = ranked[: min(8, len(ranked))]
    return window[int(seed) % len(window)][3]


def _mark_chrome_profile_clean(profile_dir: Path) -> None:
    for relative_path in ("Local State", "Default/Preferences"):
        path = profile_dir / relative_path
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            profile = data.setdefault("profile", {})
            if not isinstance(profile, dict):
                continue
            profile["exit_type"] = "Normal"
            profile["exited_cleanly"] = True
            path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        except Exception:
            continue


def _looks_like_closed_browser_error(value: Any) -> bool:
    text = f"{type(value).__name__}: {value}".lower()
    return any(
        marker in text
        for marker in (
            "targetclosederror",
            "target closed",
            "browser has been closed",
            "context has been closed",
            "page has been closed",
            "connection closed",
            "no browser context",
        )
    )


def status_to_state_event(account_id: str, status: dict[str, Any]) -> dict[str, Any]:
    """Convert interval-probe status into the UI controller's state payload."""
    display_game_no = _public_game_no(_first_text(
        status.get("display_game_no"),
        status.get("game_no"),
        status.get("snapshot_game_no"),
        status.get("guard_game_no"),
        status.get("canvas_game_no"),
    ))
    source_countdown = _safe_int(status.get("countdown")) or _safe_int(status.get("backend_countdown"))
    display_betting_open = status.get("display_betting_open")
    if display_betting_open is None:
        display_betting_open = bool(status.get("betting_open"))
        by_action = _action_to_betting(status.get("runtime_action"))
        if by_action is None:
            by_action = _action_to_betting(status.get("frontend_runtime_action"))
        if by_action is not None:
            display_betting_open = bool(by_action)
    else:
        display_betting_open = bool(display_betting_open)
    display_room_label = _first_text(
        status.get("display_room_label"),
        status.get("locked_room_label"),
        status.get("room_label"),
        status.get("runtime_room_label"),
        status.get("frontend_room_label"),
        status.get("canvas_room_label"),
    )
    display_balance_cents = (
        status.get("display_balance_cents")
        if status.get("display_balance_cents") is not None
        else status.get("balance_cents")
    )
    display_phase = _first_text(
        status.get("display_phase"),
        status.get("runtime_phase"),
        status.get("runtime_phase_label"),
        status.get("canvas_phase_text"),
        status.get("frontend_phase_text"),
        status.get("guard_phase"),
    )
    if not display_phase:
        if display_betting_open:
            display_phase = "betting_open"
        elif bool(status.get("game_ready")):
            display_phase = "game_ready"
        elif bool(status.get("hall_ready")):
            display_phase = "hall"
        else:
            display_phase = "unknown"
    display_source = _first_text(
        status.get("display_source"),
        status.get("game_no_source"),
    )
    display_coordinates = status.get("runtime_coordinates")
    if display_coordinates is None:
        display_coordinates = status.get("runtime_coords")
    if display_coordinates is None:
        safe_summary_source = status.get("safe_summary")
        if isinstance(safe_summary_source, dict):
            display_coordinates = safe_summary_source.get("runtime_coordinates")
    room_id = _first_text(
        status.get("locked_room_id"),
        status.get("room_id"),
        status.get("runtime_room_id"),
        status.get("frontend_room_id"),
    )
    room_label = _first_text(
        status.get("locked_room_label"),
        status.get("room_label"),
        display_room_label,
        status.get("runtime_room_label"),
        status.get("frontend_room_label"),
        status.get("canvas_room_label"),
    )
    ui_countdown = UI_REFERENCE_BETTING_COUNTDOWN_SECONDS if display_betting_open else (0 if bool(status.get("game_ready")) else None)
    status_ts_ms = _safe_int(status.get("status_ts_ms")) or probe.now_ms()
    safe_summary = {
        "game_ready": bool(status.get("game_ready")),
        "hall_ready": bool(status.get("hall_ready")),
        "room_id": room_id,
        "room_label": room_label,
        "locked_room_id": str(status.get("locked_room_id") or ""),
        "locked_room_label": str(status.get("locked_room_label") or ""),
        "runtime_room_id": room_id,
        "runtime_room_label": display_room_label,
        "frontend_room_id": room_id,
        "frontend_room_label": display_room_label,
        "display_room_label": display_room_label,
        "round_id": display_game_no,
        "runtime_memory_game_no": display_game_no,
        "runtime_action": str(status.get("runtime_action") or ""),
        "frontend_runtime_action": str(status.get("frontend_runtime_action") or ""),
        "frontend_batch_id": display_game_no,
        "frontend_short_batch_id": display_game_no,
        "canvas_game_no": display_game_no,
        "runtime_betting_open": display_betting_open,
        "runtime_is_can_betting": display_betting_open,
        "frontend_runtime_is_can_betting": display_betting_open,
        "runtime_countdown": ui_countdown if ui_countdown is not None else "",
        "runtime_timed": ui_countdown if ui_countdown is not None else "",
        "frontend_runtime_timed": ui_countdown if ui_countdown is not None else "",
        "runtime_current_load_type": str(status.get("runtime_current_load_type") or ""),
        "ui_reference_countdown": True,
        "ui_countdown_started_ms": status_ts_ms if display_betting_open else "",
        "backend_countdown": source_countdown if source_countdown is not None else "",
        "runtime_phase": display_phase,
        "runtime_phase_label": display_phase,
        "frontend_phase_text": display_phase,
        "canvas_phase_text": display_phase,
        "display_phase": display_phase,
        "display_source": display_source,
        "display_game_no": display_game_no,
        "display_balance_cents": display_balance_cents,
        "display_betting_open": display_betting_open,
        "runtime_coordinates": display_coordinates,
    }
    payload = {
        "game_ready": bool(status.get("game_ready")),
        "hall_ready": bool(status.get("hall_ready")),
        "batch_id": display_game_no,
        "exact_countdown": ui_countdown,
        "ui_countdown_started_ms": status_ts_ms if display_betting_open else None,
        "backend_countdown": source_countdown,
        "ocr_balance": _money_from_cents(display_balance_cents),
        "safe_summary": safe_summary,
        "timestamp_captured_ms": status_ts_ms,
        "probe_status": dict(status),
    }
    return {
        "event_type": "state",
        "instance_id": account_id,
        "payload": payload,
        "timestamp_ms": int(payload["timestamp_captured_ms"]),
    }

class LightweightProbeAdapter(BrowserControlAdapter):
    """Runs the tested interval-probe core behind the lightweight dashboard."""

    def __init__(
        self,
        *,
        profile_root: Path | str = DEFAULT_PROFILE_ROOT,
        log_root: Path | str = DEFAULT_LOG_ROOT,
        status_poll_seconds: float = 2.0,
        max_log_entries: int = 200,
        on_log: Any = None,
    ) -> None:
        super().__init__(max_log_entries=max_log_entries, on_log=on_log)
        self.profile_root = Path(profile_root)
        self.log_root = Path(log_root)
        self.status_poll_seconds = max(0.5, float(status_poll_seconds))
        self._platform_slots: dict[str, PlatformSlot] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._events: deque[dict[str, Any]] = deque(maxlen=1000)
        self._events_lock = threading.Lock()
        self._log_lock = threading.Lock()
        self._accounts: dict[str, dict[str, Any]] = {}
        self._account_lock: asyncio.Lock | None = None
        self._playwright: Any = None
        self._status_task: asyncio.Task | None = None
        self._closed = False
        self._room_entry_targets: dict[str, int] = {}
        self._hedge_task: asyncio.Task | None = None
        self._hedge_kwargs: dict[str, Any] = {}
        self._hedge_paused = False
        self._hedge_stop_requested = False
        self._participation_lock = threading.Lock()
        self._participation_states: dict[str, str] = {}
        self._output_path = self.log_root / f"lightweight_ui_probe_{probe.datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        self._event_log_path = self.log_root / f"lightweight_ui_probe_events_{probe.datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        self._status_path = self.log_root / "lightweight_ui_probe.status.txt"

    def refresh_runtime_environment(self, platform_slots) -> None:
        self._platform_slots = {slot.account_id: slot for slot in platform_slots}

    def set_account_participation_states(self, states: dict[str, str]) -> tuple[int, str, str]:
        clean: dict[str, str] = {}
        for account_id, state in dict(states or {}).items():
            account_text = str(account_id or "").strip()
            state_text = str(state or "normal").strip()
            if account_text and state_text != "normal":
                clean[account_text] = state_text
        with self._participation_lock:
            self._participation_states = clean
        return 0, "ok", ""

    def _participation_states_snapshot(self) -> dict[str, str]:
        with self._participation_lock:
            return dict(self._participation_states)

    def _emit_event(self, event: dict[str, Any]) -> None:
        with self._events_lock:
            self._events.append(event)

    def _append_probe_log(self, message: str, *, account_id: str = "") -> None:
        with self._log_lock:
            self._log_lines.append(message)
            if len(self._log_lines) > self.max_log_entries:
                self._log_lines = self._log_lines[-self.max_log_entries :]
        try:
            probe.append_jsonl(
                self._output_path,
                {
                    "event": "ui_log",
                    "account_id": account_id,
                    "message": message,
                    "timestamp_ms": probe.now_ms(),
                },
            )
        except Exception:
            pass
        self._emit_event(
            {
                "event_type": "log",
                "instance_id": account_id,
                "payload": {"message": message},
                "timestamp_ms": probe.now_ms(),
            }
        )

    def _ensure_loop(self) -> None:
        if self._thread is not None and self._thread.is_alive() and self._loop is not None:
            return
        self._closed = False
        self._ready.clear()
        self._thread = threading.Thread(target=self._thread_main, name="lightweight-probe-runtime", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)
        if self._loop is None:
            raise RuntimeError("lightweight probe runtime did not start")

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._account_lock = asyncio.Lock()
        self._status_task = loop.create_task(self._periodic_status_loop())
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    def _submit(self, label: str, coro: Coroutine[Any, Any, Any]) -> tuple[int, str, str]:
        try:
            self._ensure_loop()
        except Exception as exc:
            message = f"{label} 鍚姩杩愯鐜澶辫触: {type(exc).__name__}"
            self._append_probe_log(message)
            return 1, "", message
        assert self._loop is not None
        asyncio.run_coroutine_threadsafe(self._command_wrapper(label, coro), self._loop)
        self._append_probe_log(f"{label} command_queued")
        return 0, "queued", ""

    async def _command_wrapper(self, label: str, coro: Coroutine[Any, Any, Any]) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = f"{label} 澶辫触: {type(exc).__name__}: {exc}"
            self._append_probe_log(message)
            self._emit_event(
                {
                    "event_type": "error",
                    "instance_id": "",
                    "payload": {"message": message},
                    "timestamp_ms": probe.now_ms(),
                }
            )

    def _args(self, *, auto_fill_login: bool) -> SimpleNamespace:
        return SimpleNamespace(
            profile_root=str(self.profile_root),
            no_auto_fill_login=not bool(auto_fill_login),
            browser_channel="chrome",
            login_fill_timeout_seconds=15,
        )

    def _slot_payload(self, slot: PlatformSlot) -> dict[str, Any]:
        payload = slot.to_dict()
        payload["login_url"] = normalize_login_url(slot.login_url)
        payload["name"] = slot.display_name
        return payload

    def _config_from_current_slot(self, account_id: str, *, auto_fill_login: bool) -> Any:
        slot = self._platform_slots.get(account_id)
        if slot is None:
            raise ValueError(f"missing platform slot: {account_id}")
        return probe.config_from_slot(account_id, self._slot_payload(slot), self._args(auto_fill_login=auto_fill_login))

    def _refresh_account_config(self, account_id: str, item: dict[str, Any], *, auto_fill_login: bool) -> None:
        item["config"] = self._config_from_current_slot(account_id, auto_fill_login=auto_fill_login)
        if auto_fill_login:
            item["login_fill_submitted"] = False

    async def _ensure_playwright(self) -> Any:
        if self._playwright is None:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
        return self._playwright

    async def _account_context_alive(self, item: dict[str, Any]) -> bool:
        runtime = item.get("runtime") or {}
        context = runtime.get("context")
        if context is None:
            return False
        try:
            pages = list(getattr(context, "pages", []) or [])
            if pages and all(bool(getattr(page, "is_closed", lambda: False)()) for page in pages):
                return False
            page = await probe.cw._active_page(context)
            if bool(getattr(page, "is_closed", lambda: False)()):
                return False
            runtime["page"] = page
            return True
        except Exception as exc:
            if _looks_like_closed_browser_error(exc):
                return False
            return False

    async def _discard_account(self, account_id: str, reason: str) -> None:
        item = self._accounts.pop(account_id, None)
        if item is None:
            return
        try:
            await probe.close_accounts({account_id: item})
        except Exception:
            pass
        self._append_probe_log(f"drop_stale_account: {account_id} reason={reason}", account_id=account_id)

    async def _ensure_account(self, account_id: str, *, auto_fill_login: bool = False) -> dict[str, Any]:
        existing = self._accounts.get(account_id)
        if existing is not None:
            if await self._account_context_alive(existing):
                self._refresh_account_config(account_id, existing, auto_fill_login=auto_fill_login)
                return existing
            await self._discard_account(account_id, "stale_context")
        slot = self._platform_slots.get(account_id)
        if slot is None:
            raise ValueError(f"missing platform slot: {account_id}")
        assert self._account_lock is not None
        async with self._account_lock:
            existing = self._accounts.get(account_id)
            if existing is not None:
                if await self._account_context_alive(existing):
                    self._refresh_account_config(account_id, existing, auto_fill_login=auto_fill_login)
                    return existing
                await self._discard_account(account_id, "stale_context_locked")
            playwright = await self._ensure_playwright()
            event_queue = _MirroringEventQueue(probe.JsonEventQueue(self._event_log_path), self._emit_event)
            config = self._config_from_current_slot(account_id, auto_fill_login=auto_fill_login)
            launch_config = replace(config, login_url="", auto_fill_login=False)
            _mark_chrome_profile_clean(Path(launch_config.user_data_dir))
            item = await probe.launch_account(playwright, launch_config, event_queue)
            item["config"] = config
            item["playwright"] = playwright
            probe.start_state_tasks(item)
            self._accounts[account_id] = item
            self._append_probe_log(f"璐﹀彿鍚姩: {account_id}", account_id=account_id)
            return item

    async def _open_login_pages(self, account_ids: list[str]) -> None:
        for account_id in account_ids:
            try:
                item = await self._ensure_account(account_id, auto_fill_login=False)
                slot = self._platform_slots.get(account_id)
                login_url = normalize_login_url(slot.login_url if slot else "")
                if not login_url:
                    self._append_probe_log(f"鎵撳紑鐧诲綍椤佃烦杩? {account_id} 鏈厤缃櫥褰曢〉", account_id=account_id)
                    continue
                page = await probe.cw._active_page(item["runtime"]["context"])
                item["runtime"]["page"] = page
                self._append_probe_log(f"鎵撳紑鐧诲綍椤电洰鏍? {account_id} {login_url}", account_id=account_id)
                try:
                    await page.goto(login_url, wait_until="commit", timeout=20000)
                except Exception as exc:
                    if "commit" in str(exc) and "wait_until" in str(exc):
                        await page.goto(login_url, wait_until="domcontentloaded", timeout=20000)
                    else:
                        raise
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=20000)
                except Exception as exc:
                    self._append_probe_log(
                        f"鐧诲綍椤电瓑寰呰秴鏃?澶辫触: {account_id} url={page.url} {type(exc).__name__}",
                        account_id=account_id,
                    )
                self._append_probe_log(f"鎵撳紑鐧诲綍椤靛畬鎴? {account_id} url={page.url}", account_id=account_id)
            except Exception as exc:
                message = f"鎵撳紑鐧诲綍椤靛け璐? {account_id} {type(exc).__name__}: {exc}"
                self._append_probe_log(message, account_id=account_id)
                self._emit_event(
                    {
                        "event_type": "error",
                        "instance_id": account_id,
                        "payload": {"message": message},
                        "timestamp_ms": probe.now_ms(),
                    }
                )

    async def _fill_login(self, account_ids: list[str]) -> None:
        for account_id in account_ids:
            item = await self._ensure_account(account_id, auto_fill_login=True)
            result = await probe.try_auto_fill_login(item, self._output_path, reason="ui_fill_login")
            ok = bool(result.get("ok"))
            stage = str(result.get("stage") or "")
            self._append_probe_log(f"濉啓鐧诲綍: {account_id} ok={ok} stage={stage}", account_id=account_id)

    async def _handoff_one_to_headless(self, account_id: str) -> None:
        try:
            item = await self._ensure_account(account_id, auto_fill_login=False)
            bundle_result = await probe.capture_launch_bundle_for_account(account_id, item, self._output_path, force_refresh=True)
            ok = await probe.cw._handoff_to_headless(
                item["config"],
                item["runtime"],
                item["playwright"],
                item["state"],
                item["event_queue"],
                item["ws_parser"],
            )
            self._append_probe_log(
                f"无头接管: {account_id} bundle={bundle_result} ok={bool(ok)}",
                account_id=account_id,
            )
            if not ok:
                self._emit_event(
                    {
                        "event_type": "error",
                        "instance_id": account_id,
                        "payload": {"message": f"无头接管失败: {account_id}"},
                        "timestamp_ms": probe.now_ms(),
                    }
                )
        except Exception as exc:
            message = f"无头接管失败: {account_id} {type(exc).__name__}: {exc}"
            self._append_probe_log(message, account_id=account_id)
            self._emit_event(
                {
                    "event_type": "error",
                    "instance_id": account_id,
                    "payload": {"message": message},
                    "timestamp_ms": probe.now_ms(),
                }
            )

    async def _handoff_to_headless(self, account_ids: list[str]) -> None:
        semaphore = asyncio.Semaphore(2)

        async def run_limited(account_id: str) -> None:
            async with semaphore:
                await self._handoff_one_to_headless(account_id)

        await asyncio.gather(*(run_limited(account_id) for account_id in account_ids), return_exceptions=False)

    def _emit_room_entry_progress(self, account_id: str, room_index: int, room_entry: str, reason: str = "") -> None:
        self._emit_event(
            {
                "event_type": "health",
                "instance_id": account_id,
                "payload": {
                    "room_entry": room_entry,
                    "room_index": int(room_index),
                    "reason": reason,
                },
                "timestamp_ms": probe.now_ms(),
            }
        )

    async def _prepare_headless_room_entry(self, account_id: str, item: dict[str, Any], room_index: int) -> None:
        runtime = item["runtime"]
        self._emit_room_entry_progress(account_id, room_index, "preparing")
        if runtime.get("mode") != "headless":
            return
        page = runtime.get("page")
        if page is None and runtime.get("context") is not None:
            try:
                page = await probe.cw._active_page(runtime["context"])
                runtime["page"] = page
            except Exception:
                page = None
        if page is None:
            return
        try:
            ready = await probe.read_baccarat_load_state(page)
        except Exception:
            ready = None
        if ready is not None and bool(getattr(ready, "hall_ready", False)) and not bool(getattr(ready, "game_ready", False)):
            runtime["room_entry_pending"] = None
            runtime["headless_hall_ready"] = True
        if not runtime.get("hall_entry_buttons"):
            try:
                runtime["hall_entry_buttons"] = await probe.cw.resolve_baccarat_hall_entry_buttons(page)
            except Exception:
                runtime["hall_entry_buttons"] = []

    async def _enter_room_one(self, account_id: str, room_index: int) -> None:
        try:
            item = await self._ensure_account(account_id, auto_fill_login=False)
            self._room_entry_targets[account_id] = int(room_index)
            await self._prepare_headless_room_entry(account_id, item, int(room_index))
            ok = await probe.cw._enter_headless_room(
                item["config"],
                item["runtime"],
                item["event_queue"],
                int(room_index),
                item["state"],
            )
            self._append_probe_log(f"进房: {account_id} room={room_index} ok={bool(ok)}", account_id=account_id)
            if not ok:
                self._emit_event(
                    {
                        "event_type": "error",
                        "instance_id": account_id,
                        "payload": {"message": f"进房失败: {account_id}"},
                        "timestamp_ms": probe.now_ms(),
                    }
                )
        except Exception as exc:
            message = f"进房失败: {account_id} {type(exc).__name__}: {exc}"
            self._append_probe_log(message, account_id=account_id)
            self._emit_event(
                {
                    "event_type": "error",
                    "instance_id": account_id,
                    "payload": {"message": message},
                    "timestamp_ms": probe.now_ms(),
                }
            )

    async def _refresh_headless(self, account_ids: list[str]) -> None:
        for account_id in account_ids:
            item = await self._ensure_account(account_id, auto_fill_login=False)
            result = await probe.capture_launch_bundle_for_account(account_id, item, self._output_path, force_refresh=True)
            ok = await probe.cw._handoff_to_headless(
                item["config"],
                item["runtime"],
                item["playwright"],
                item["state"],
                item["event_queue"],
                item["ws_parser"],
            )
            self._append_probe_log(
                f"refresh_headless: {account_id} bundle={result} ok={bool(ok)}",
                account_id=account_id,
            )
            if not ok:
                self._emit_event(
                    {
                        "event_type": "error",
                        "instance_id": account_id,
                        "payload": {"message": f"refresh_headless failed: {account_id}"},
                        "timestamp_ms": probe.now_ms(),
                    }
                )

    async def _enter_room(self, account_ids: list[str], room_index: int) -> None:
        await asyncio.gather(
            *(self._enter_room_one(account_id, int(room_index)) for account_id in account_ids),
            return_exceptions=False,
        )

    async def _release_headless(self, account_ids: list[str]) -> None:
        for account_id in account_ids:
            item = self._accounts.get(account_id)
            if not item:
                continue
            await probe.cw._release_headless_session(item["config"], item["runtime"], item["event_queue"])
            self._room_entry_targets.pop(account_id, None)
            self._append_probe_log(f"閲婃斁鏃犲ご: {account_id}", account_id=account_id)

    def _restore_failure_reason(
        self,
        account_id: str,
        status: dict[str, Any] | None,
        reference_statuses: list[dict[str, Any]],
        *,
        min_balance_yuan: int,
    ) -> str:
        if not isinstance(status, dict):
            return "状态缺失"
        if not _status_fresh(status):
            return "状态过期"
        if not _status_game_ready(status):
            return "不在房间"
        if _status_balance_cents(status) is None:
            return "余额未知"
        if not _balance_covers(status, 0, int(min_balance_yuan)):
            return "余额不足"
        if not _status_betting_open(status):
            return "等待可下注"

        reference_rooms = {_status_room_key(item) for item in reference_statuses if _status_room_key(item)}
        own_room = _status_room_key(status)
        if reference_rooms and own_room not in reference_rooms:
            return "房间不一致"

        reference_rounds = {_status_round_key(item) for item in reference_statuses if _status_round_key(item)}
        own_round = _status_round_key(status)
        if reference_rounds and own_round not in reference_rounds:
            return "局号不一致"

        return ""

    def _participation_pool(
        self,
        account_pool: list[str],
        status_map: dict[str, dict[str, Any]],
        manual_states: dict[str, str],
        *,
        min_balance_yuan: int,
    ) -> tuple[list[str], dict[str, str]]:
        excluded: dict[str, str] = {}
        active_pool: list[str] = []
        pending_restore: list[str] = []
        for account_id in account_pool:
            state = str(manual_states.get(account_id, "normal") or "normal")
            if state in MANUAL_EXCLUDE_STATES:
                excluded[account_id] = "人工剔除"
            elif state in MANUAL_RESTORE_STATES:
                pending_restore.append(account_id)
            elif state == "restore_failed":
                excluded[account_id] = "恢复失败"
            else:
                active_pool.append(account_id)

        reference_statuses = [
            status_map.get(account_id) or {}
            for account_id in active_pool
            if isinstance(status_map.get(account_id), dict)
        ]
        for account_id in pending_restore:
            reason = self._restore_failure_reason(
                account_id,
                status_map.get(account_id),
                reference_statuses,
                min_balance_yuan=int(min_balance_yuan),
            )
            if reason:
                excluded[account_id] = f"恢复失败：{reason}"
            else:
                active_pool.append(account_id)
                reference_statuses.append(status_map.get(account_id) or {})
        return active_pool, excluded

    def _build_plan(
        self,
        round_number: int,
        *,
        main_account: str,
        sub_accounts: tuple[str, ...],
        amount_min: int,
        amount_max: int,
        statuses: dict[str, dict[str, Any]] | None = None,
        main_successor_account: str = "",
        min_balance_yuan: int = 0,
        manual_account_states: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        low = min(int(amount_min), int(amount_max))
        high = max(int(amount_min), int(amount_max))
        valid_amounts: list[int] = []
        for amount in range(low, high + 1):
            try:
                _stable_chip_sequence(amount)
            except Exception:
                continue
            valid_amounts.append(int(amount))
        if not valid_amounts:
            return {
                "ok": False,
                "reason": f"金额区间{low}-{high}没有可点击筹码组合",
                "legs": [],
                "excluded_accounts": {},
            }
        offset = (int(round_number) - 1) % len(valid_amounts)
        ordered_amounts = (*valid_amounts[offset:], *valid_amounts[:offset])
        account_pool = list(dict.fromkeys([main_account, *sub_accounts]))
        status_map = statuses or {}
        manual_states = (
            dict(manual_account_states)
            if manual_account_states is not None
            else self._participation_states_snapshot()
        )
        active_pool, base_excluded = self._participation_pool(
            account_pool,
            status_map,
            manual_states,
            min_balance_yuan=int(min_balance_yuan),
        )
        if len(active_pool) < 3:
            return {
                "ok": False,
                "reason": "少于 3 个账号，无法对冲",
                "legs": [],
                "excluded_accounts": base_excluded,
            }
        successor = str(main_successor_account or "").strip()

        for amount in ordered_amounts:
            main_side = "banker" if round_number % 2 else "player"
            sub_side = "player" if main_side == "banker" else "banker"
            excluded: dict[str, str] = dict(base_excluded)
            main_candidates = [
                account_id
                for account_id in active_pool
                if _balance_covers(status_map.get(account_id), int(amount), int(min_balance_yuan))
                and _status_amount_allowed(status_map.get(account_id), int(amount))
            ]
            if not main_candidates:
                continue
            if main_account in main_candidates:
                effective_main = main_account
            else:
                if main_account in active_pool:
                    excluded[main_account] = _status_reject_reason(
                        status_map.get(main_account),
                        int(amount),
                        int(min_balance_yuan),
                    ) or "不可用"
                if successor and successor in main_candidates:
                    effective_main = successor
                else:
                    effective_main = max(
                        main_candidates,
                        key=lambda account_id: _status_balance_cents(status_map.get(account_id)) or -1,
                    )

            sub_pool = [
                account_id
                for account_id in active_pool
                if account_id != effective_main and account_id not in excluded
            ]
            while True:
                if len(sub_pool) < 2:
                    break
                try:
                    sub_amounts = _split_stable_sub_amounts(
                        int(amount),
                        len(sub_pool),
                        seed=int(round_number) + len(sub_pool) * 17,
                        amount_bounds=tuple(_status_amount_bounds(status_map.get(account_id)) for account_id in sub_pool),
                    )
                except Exception:
                    break
                if sub_amounts and not any(_status_amount_bounds(status_map.get(account_id)) for account_id in sub_pool):
                    sub_offset = (int(round_number) - 1) % len(sub_amounts)
                    sub_amounts = (*sub_amounts[sub_offset:], *sub_amounts[:sub_offset])
                paired = list(zip(sub_pool, sub_amounts))
                insufficient = [
                    account_id
                    for account_id, sub_amount in paired
                    if _status_reject_reason(
                        status_map.get(account_id),
                        int(sub_amount),
                        int(min_balance_yuan),
                    )
                ]
                if not insufficient:
                    legs = [
                        {
                            "account_id": effective_main,
                            "role": "main",
                            "side": main_side,
                            "side_text": _side_text(main_side),
                            "amount": int(amount),
                        }
                    ]
                    for account_id, sub_amount in paired:
                        legs.append(
                            {
                                "account_id": account_id,
                                "role": "sub",
                                "side": sub_side,
                                "side_text": _side_text(sub_side),
                                "amount": int(sub_amount),
                            }
                        )
                    for leg in legs:
                        leg["chips"] = _stable_chip_sequence(int(leg["amount"]))
                        leg["steps"] = len(leg["chips"])
                    return {
                        "ok": True,
                        "reason": "ready",
                        "legs": legs,
                        "excluded_accounts": excluded,
                        "effective_main_account": effective_main,
                        "amount": int(amount),
                    }
                for account_id in insufficient:
                    sub_amount = next(
                        int(amount)
                        for paired_account, amount in paired
                        if paired_account == account_id
                    )
                    excluded[account_id] = _status_reject_reason(
                        status_map.get(account_id),
                        sub_amount,
                        int(min_balance_yuan),
                    ) or "不可用"
                sub_pool = [account_id for account_id in sub_pool if account_id not in insufficient]

        final_excluded = dict(base_excluded)
        for account_id in active_pool:
            if not _balance_covers(status_map.get(account_id), low, int(min_balance_yuan)):
                final_excluded[account_id] = "余额不足"
            elif _status_amount_bounds(status_map.get(account_id)) is not None and not any(
                _status_amount_allowed(status_map.get(account_id), int(amount))
                for amount in valid_amounts
            ):
                final_excluded[account_id] = "超出限红"
        return {
            "ok": False,
            "reason": "可用余额或限红不足，无法组成1主2副",
            "legs": [],
            "excluded_accounts": final_excluded,
        }

    async def _wait_planned_betting_round(
        self,
        *,
        account_ids: list[str],
        main_account: str,
        sub_accounts: tuple[str, ...],
        round_number: int,
        room_index: int,
        amount_min: int,
        amount_max: int,
        main_successor_account: str,
        min_balance_yuan: int,
        min_countdown: int,
        last_round: str,
        timeout_seconds: int,
        allow_countdown_window: bool,
        coordinator_status_refresh_ms: int,
        coordinator_poll_ms: int,
    ) -> tuple[str, str, dict[str, dict[str, Any]], dict[str, Any]] | None:
        deadline = time.time() + max(1, int(timeout_seconds))
        refresh_interval = max(0.2, int(coordinator_status_refresh_ms) / 1000.0)
        poll_interval = max(0.05, int(coordinator_poll_ms) / 1000.0)
        last_statuses: dict[str, dict[str, Any]] = {}
        last_plan: dict[str, Any] = {}
        next_refresh_at = 0.0
        presend_checked_at_by_round: dict[str, float] = {}

        while time.time() < deadline:
            current = time.time()
            if current >= next_refresh_at or not last_statuses:
                last_statuses = await probe.write_status(self._accounts, self._status_path, self._output_path)
                last_plan = self._build_plan(
                    round_number,
                    main_account=main_account,
                    sub_accounts=sub_accounts,
                    amount_min=amount_min,
                    amount_max=amount_max,
                    statuses=last_statuses,
                    main_successor_account=main_successor_account,
                    min_balance_yuan=int(min_balance_yuan),
                )
                planned_ids = tuple(
                    str(leg.get("account_id"))
                    for leg in list(last_plan.get("legs", []))
                    if isinstance(leg, dict) and str(leg.get("account_id") or "") in self._accounts
                )
                excluded_ids = set((last_plan.get("excluded_accounts") or {}).keys())
                retry_ids = planned_ids or tuple(
                    account_id
                    for account_id in account_ids
                    if account_id in self._accounts and account_id not in excluded_ids
                )
                if retry_ids:
                    await probe.retry_headless_room_entries(
                        self._accounts,
                        last_statuses,
                        self._output_path,
                        room_index=int(room_index),
                        account_ids=retry_ids,
                    )
                next_refresh_at = time.time() + refresh_interval

            if not last_plan.get("ok"):
                await asyncio.sleep(poll_interval)
                continue

            planned_ids = tuple(
                str(leg.get("account_id"))
                for leg in list(last_plan.get("legs", []))
                if isinstance(leg, dict) and str(leg.get("account_id") or "") in self._accounts
            )
            if len(planned_ids) < 3:
                await asyncio.sleep(poll_interval)
                continue

            planned_accounts = {account_id: self._accounts[account_id] for account_id in planned_ids}
            planned_statuses = {account_id: last_statuses.get(account_id) or {} for account_id in planned_ids}
            decision = probe.coordinator_readiness(
                planned_accounts,
                planned_statuses,
                min_countdown=int(min_countdown),
                room_index=int(room_index),
                allow_countdown_window=bool(allow_countdown_window),
            )
            round_key = str(decision.get("round_key") or "")
            round_id = str(decision.get("round_id") or "")
            if bool(decision.get("ok")) and round_key and round_key != last_round:
                checked_at = presend_checked_at_by_round.get(round_key, 0.0)
                if time.time() - checked_at < refresh_interval:
                    await asyncio.sleep(poll_interval)
                    continue
                presend_checked_at_by_round[round_key] = time.time()
                probe.append_jsonl(
                    self._output_path,
                    {
                        "event": "coordinator_candidate_ready",
                        "decision": decision,
                        "planned_accounts": list(planned_ids),
                        "excluded_accounts": last_plan.get("excluded_accounts", {}),
                        "timestamp_ms": probe.now_ms(),
                    },
                )
                final_statuses = await probe.write_status(self._accounts, self._status_path, self._output_path)
                final_plan = self._build_plan(
                    round_number,
                    main_account=main_account,
                    sub_accounts=sub_accounts,
                    amount_min=amount_min,
                    amount_max=amount_max,
                    statuses=final_statuses,
                    main_successor_account=main_successor_account,
                    min_balance_yuan=int(min_balance_yuan),
                )
                final_planned_ids = tuple(
                    str(leg.get("account_id"))
                    for leg in list(final_plan.get("legs", []))
                    if isinstance(leg, dict) and str(leg.get("account_id") or "") in self._accounts
                )
                final_accounts = {account_id: self._accounts[account_id] for account_id in final_planned_ids}
                final_planned_statuses = {
                    account_id: final_statuses.get(account_id) or {} for account_id in final_planned_ids
                }
                final_decision = probe.coordinator_readiness(
                    final_accounts,
                    final_planned_statuses,
                    min_countdown=int(min_countdown),
                    room_index=int(room_index),
                    allow_countdown_window=bool(allow_countdown_window),
                )
                probe.append_jsonl(
                    self._output_path,
                    {
                        "event": "coordinator_presend_check",
                        "decision": final_decision,
                        "planned_accounts": list(final_planned_ids),
                        "excluded_accounts": final_plan.get("excluded_accounts", {}),
                        "statuses": {
                            account_id: probe.compact_status(status, room_index=int(room_index))
                            for account_id, status in final_statuses.items()
                        },
                        "timestamp_ms": probe.now_ms(),
                    },
                )
                final_round_key = str(final_decision.get("round_key") or "")
                final_round_id = str(final_decision.get("round_id") or round_id)
                if bool(final_decision.get("ok")) and final_round_key and final_round_key != last_round:
                    fallback_accounts = list(final_decision.get("fallback_accounts") or [])
                    if fallback_accounts:
                        probe.append_jsonl(
                            self._output_path,
                            {
                                "event": "countdown_window_fallback",
                                "accounts": fallback_accounts,
                                "round_id": final_round_id,
                                "min_countdown": min_countdown,
                                "timestamp_ms": probe.now_ms(),
                            },
                        )
                    return final_round_id, final_round_key, final_statuses, final_plan
                last_statuses = final_statuses
                last_plan = final_plan
                next_refresh_at = time.time() + refresh_interval

            await asyncio.sleep(poll_interval)
        return None

    async def _run_one_hedge_round(
        self,
        *,
        account_ids: list[str],
        main_account: str,
        sub_accounts: tuple[str, ...],
        round_number: int,
        room_index: int,
        amount_min: int,
        amount_max: int,
        main_successor_account: str,
        min_balance_yuan: int,
        click_interval_ms: int,
        confirm_ms: int,
        min_countdown: int,
        last_round: str,
    ) -> str:
        ready = await self._wait_planned_betting_round(
            account_ids=account_ids,
            main_account=main_account,
            sub_accounts=sub_accounts,
            round_number=round_number,
            room_index=room_index,
            amount_min=amount_min,
            amount_max=amount_max,
            main_successor_account=main_successor_account,
            min_balance_yuan=int(min_balance_yuan),
            last_round=last_round,
            min_countdown=int(min_countdown),
            timeout_seconds=90,
            allow_countdown_window=True,
            coordinator_status_refresh_ms=500,
            coordinator_poll_ms=100,
        )
        if ready is None:
            self._append_probe_log(f"wait_betting_round timeout: {round_number}")
            return last_round
        round_id, round_key, statuses, plan = ready
        legs = list(plan.get("legs", []))
        planned_ids = tuple(
            str(leg.get("account_id") or "")
            for leg in legs
            if isinstance(leg, dict) and str(leg.get("account_id") or "") in self._accounts
        )
        planned_accounts = {account_id: self._accounts[account_id] for account_id in planned_ids}
        planned_statuses = {account_id: statuses.get(account_id) or {} for account_id in planned_ids}
        countdowns: dict[str, int] = {}
        for account_id, status in planned_statuses.items():
            try:
                countdowns[str(account_id)] = int(status.get("countdown") or 0)
            except (TypeError, ValueError):
                countdowns[str(account_id)] = 0
        room_label = ""
        for status in planned_statuses.values():
            room_label = str(
                status.get("display_room_label")
                or status.get("room_label")
                or status.get("locked_room_label")
                or ""
            )
            if room_label:
                break
        self._emit_event(
            {
                "event_type": "plan",
                "instance_id": "",
                "payload": {
                    "round_number": int(round_number),
                    "round_id": round_id,
                    "room_label": room_label,
                    "send_countdowns": countdowns,
                    "click_interval_ms": int(click_interval_ms),
                    "min_countdown": int(min_countdown),
                    "confirm_ms": int(confirm_ms),
                    "effective_main_account": plan.get("effective_main_account", main_account),
                    "excluded_accounts": plan.get("excluded_accounts", {}),
                    "planned_accounts": list(planned_ids),
                    "reason": plan.get("reason", ""),
                    "legs": legs,
                },
                "timestamp_ms": probe.now_ms(),
            }
        )
        if not plan.get("ok"):
            self._append_probe_log(
                f"round={round_number} skipped: {plan.get('reason', 'plan unavailable')}"
            )
            self._emit_event(
                {
                    "event_type": "round",
                    "instance_id": "",
                    "payload": {
                        "round_number": int(round_number),
                        "round_id": round_id,
                        "room_label": room_label,
                        "send_countdowns": countdowns,
                        "click_interval_ms": int(click_interval_ms),
                        "confirm_ms": int(confirm_ms),
                        "legs": [],
                        "results": [],
                        "elapsed_ms": 0,
                        "status": "skipped",
                        "reason": str(plan.get("reason", "")),
                    },
                    "timestamp_ms": probe.now_ms(),
                }
            )
            return round_id
        planned_countdowns = [
            int(status.get("countdown") or 0)
            for status in planned_statuses.values()
        ]
        self._append_probe_log(
            f"round={round_number} start click_interval_ms={click_interval_ms} "
            f"countdown_min={min(planned_countdowns) if planned_countdowns else 0} "
            f"planned_accounts={','.join(planned_ids)}"
        )
        result = await probe.execute_round(
            planned_accounts,
            legs,
            round_id,
            planned_statuses,
            int(click_interval_ms),
            int(confirm_ms),
            self._output_path,
            room_index=int(room_index),
            allow_countdown_window=True,
        )
        result_payload = dict(result)
        result_payload["round_number"] = int(round_number)
        result_payload["room_label"] = room_label
        result_payload["send_countdowns"] = countdowns
        self._emit_event(
            {
                "event_type": "round",
                "instance_id": "",
                "payload": result_payload,
                "timestamp_ms": probe.now_ms(),
            }
        )
        self._append_probe_log(
            f"execute_round_done: round={round_number} elapsed_ms={result.get('elapsed_ms', '-') }ms"
        )
        return round_key or round_id

    async def _execute_rounds(
        self,
        *,
        account_ids: list[str],
        main_account: str,
        sub_accounts: tuple[str, ...],
        rounds: int,
        room_index: int,
        amount_min: int,
        amount_max: int,
        main_successor_account: str = "",
        min_balance_yuan: int = 0,
        click_interval_ms: int,
        confirm_ms: int,
        min_countdown: int,
    ) -> None:
        for account_id in account_ids:
            await self._ensure_account(account_id, auto_fill_login=False)
        last_round = ""
        for index in range(1, max(1, int(rounds)) + 1):
            last_round = await self._run_one_hedge_round(
                account_ids=account_ids,
                main_account=main_account,
                sub_accounts=sub_accounts,
                round_number=index,
                room_index=room_index,
                amount_min=amount_min,
                amount_max=amount_max,
                main_successor_account=main_successor_account,
                min_balance_yuan=int(min_balance_yuan),
                click_interval_ms=click_interval_ms,
                confirm_ms=confirm_ms,
                min_countdown=min_countdown,
                last_round=last_round,
            )

    async def _hedge_loop(self, **kwargs) -> None:
        self._hedge_stop_requested = False
        self._hedge_paused = False
        self._hedge_kwargs = dict(kwargs)
        account_ids = list(self._hedge_kwargs["account_ids"])
        for account_id in account_ids:
            await self._ensure_account(account_id, auto_fill_login=False)
        last_round = ""
        round_number = 0
        self._append_probe_log("hedge_loop_started")
        while not self._closed and not self._hedge_stop_requested:
            if self._hedge_paused:
                await asyncio.sleep(0.3)
                continue
            active_kwargs = dict(self._hedge_kwargs or kwargs)
            account_ids = list(active_kwargs["account_ids"])
            for account_id in account_ids:
                await self._ensure_account(account_id, auto_fill_login=False)
            round_number += 1
            last_round = await self._run_one_hedge_round(
                account_ids=account_ids,
                main_account=str(active_kwargs["main_account"]),
                sub_accounts=tuple(active_kwargs["sub_accounts"]),
                round_number=round_number,
                room_index=int(active_kwargs["room_index"]),
                amount_min=int(active_kwargs["amount_min"]),
                amount_max=int(active_kwargs["amount_max"]),
                main_successor_account=str(active_kwargs.get("main_successor_account") or ""),
                min_balance_yuan=int(active_kwargs.get("min_balance_yuan") or 0),
                click_interval_ms=int(active_kwargs["click_interval_ms"]),
                confirm_ms=int(active_kwargs["confirm_ms"]),
                min_countdown=int(active_kwargs["min_countdown"]),
                last_round=last_round,
            )
        self._append_probe_log("hedge_round_done")

    async def _start_hedge(self, **kwargs) -> None:
        self._hedge_kwargs = dict(kwargs)
        if self._hedge_task is not None and not self._hedge_task.done():
            self._hedge_paused = False
            self._hedge_stop_requested = False
            self._append_probe_log("hedge_restarted")
            return
        self._hedge_task = asyncio.create_task(self._hedge_loop(**kwargs))
        self._hedge_task.add_done_callback(self._on_hedge_task_done)

    def _on_hedge_task_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            self._append_probe_log(f"鎸佺画瀵瑰啿寮傚父鍋滄: {type(exc).__name__}: {exc}")
            self._emit_event(
                {
                    "event_type": "error",
                    "instance_id": "",
                    "payload": {"message": f"鎸佺画瀵瑰啿寮傚父鍋滄: {type(exc).__name__}: {exc}"},
                    "timestamp_ms": probe.now_ms(),
                }
            )

    async def _pause_hedge(self) -> None:
        self._hedge_paused = True
        self._append_probe_log("hedge_paused")

    async def _stop_hedge(self) -> None:
        self._hedge_stop_requested = True
        self._hedge_paused = False
        if self._hedge_task is not None and not self._hedge_task.done():
            self._hedge_task.cancel()
            await asyncio.gather(self._hedge_task, return_exceptions=True)
        self._hedge_task = None
        self._append_probe_log("hedge_stop_requested")

    async def _stop_accounts(self, account_ids: list[str]) -> None:
        selected = {account_id: self._accounts[account_id] for account_id in account_ids if account_id in self._accounts}
        await probe.close_accounts(selected)
        for account_id in selected:
            self._accounts.pop(account_id, None)
            self._room_entry_targets.pop(account_id, None)
            self._append_probe_log(f"鍋滄璐﹀彿: {account_id}", account_id=account_id)

    async def _restart_accounts(self, account_ids: list[str]) -> None:
        await self._stop_accounts(account_ids)
        await self._open_login_pages(account_ids)

    async def _periodic_status_loop(self) -> None:
        while not self._closed:
            try:
                if self._accounts:
                    statuses = await probe.collect_statuses(self._accounts)
                    for account_id, status in statuses.items():
                        self._emit_event(status_to_state_event(account_id, status))
                    for account_id, status in list(statuses.items()):
                        if _looks_like_closed_browser_error(status.get("error", "")):
                            await self._discard_account(account_id, str(status.get("error") or "closed"))
                    grouped: dict[int, list[str]] = {}
                    for account_id, room_index in self._room_entry_targets.items():
                        if account_id in self._accounts:
                            grouped.setdefault(int(room_index), []).append(account_id)
                    for room_index, account_ids in grouped.items():
                        await probe.retry_headless_room_entries(
                            self._accounts,
                            statuses,
                            self._output_path,
                            room_index=int(room_index),
                            account_ids=tuple(account_ids),
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._append_probe_log(f"鐘舵€佸埛鏂板け璐? {type(exc).__name__}: {exc}")
            await asyncio.sleep(self.status_poll_seconds)

    def start_accounts(self, account_ids: list[str]) -> tuple[int, str, str]:
        return self._submit("start_accounts", self._open_login_pages(list(account_ids)))

    def restart_accounts(self, account_ids: list[str]) -> tuple[int, str, str]:
        return self._submit("restart_accounts", self._restart_accounts(list(account_ids)))

    def open_login_pages(self, account_ids: list[str]) -> tuple[int, str, str]:
        return self._submit("open_login_pages", self._open_login_pages(list(account_ids)))

    def fill_login(self, account_ids: list[str]) -> tuple[int, str, str]:
        return self._submit("fill_login", self._fill_login(list(account_ids)))

    def handoff_to_headless(self, account_ids: list[str]) -> tuple[int, str, str]:
        return self._submit("鏃犲ご鎺ョ", self._handoff_to_headless(list(account_ids)))

    def enter_room(self, account_ids: list[str], room_index: int) -> tuple[int, str, str]:
        return self._submit("鎵归噺杩涙埧", self._enter_room(list(account_ids), int(room_index)))

    def refresh_headless(self, account_ids: list[str]) -> tuple[int, str, str]:
        return self._submit("鍒锋柊鎺ョ", self._refresh_headless(list(account_ids)))

    def release_headless(self, account_ids: list[str]) -> tuple[int, str, str]:
        return self._submit("閲婃斁鏃犲ご", self._release_headless(list(account_ids)))

    def stop_accounts(self, account_ids: list[str]) -> tuple[int, str, str]:
        return self._submit("鍋滄璐﹀彿", self._stop_accounts(list(account_ids)))

    def execute_rounds(self, **kwargs) -> tuple[int, str, str]:
        return self._submit("鎵ц瀵瑰啿娴嬭瘯", self._execute_rounds(**kwargs))

    def start_hedge(self, **kwargs) -> tuple[int, str, str]:
        return self._submit("鍚姩鎸佺画瀵瑰啿", self._start_hedge(**kwargs))

    def pause_hedge(self) -> tuple[int, str, str]:
        return self._submit("鏆傚仠鎸佺画瀵瑰啿", self._pause_hedge())

    def stop_hedge(self) -> tuple[int, str, str]:
        return self._submit("鍋滄鎸佺画瀵瑰啿", self._stop_hedge())

    def poll_events(self, max_items: int = 128) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        with self._events_lock:
            while self._events and len(items) < max_items:
                items.append(self._events.popleft())
        return items

    async def _shutdown_async(self) -> None:
        self._closed = True
        await self._stop_hedge()
        if self._status_task is not None and not self._status_task.done():
            self._status_task.cancel()
            await asyncio.gather(self._status_task, return_exceptions=True)
        if self._accounts:
            await probe.close_accounts(dict(self._accounts))
            self._accounts.clear()
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    def shutdown(self) -> None:
        if self._loop is None:
            return
        loop = self._loop
        future = asyncio.run_coroutine_threadsafe(self._shutdown_async(), loop)
        try:
            future.result(timeout=10)
        except Exception as exc:
            self._append_probe_log(f"鍏抽棴杩愯鐜瓒呮椂/澶辫触: {type(exc).__name__}")
        finally:
            loop.call_soon_threadsafe(loop.stop)
            if self._thread is not None and self._thread.is_alive():
                self._thread.join(timeout=5)
            self._loop = None
            self._thread = None
            self._ready.clear()
