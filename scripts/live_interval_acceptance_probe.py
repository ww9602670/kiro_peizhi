from __future__ import annotations

import argparse
import asyncio
import json
import queue
import random
import re
import sys
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bet_desktop.backend import cluster_process_worker as cw  # noqa: E402
from bet_desktop.browser.game_launch_url import read_baccarat_load_state  # noqa: E402
from bet_desktop.browser.login_flow import fill_login_form_when_visible  # noqa: E402
from bet_desktop.browser.live_runtime_state import read_live_runtime_snapshot  # noqa: E402
from bet_desktop.core.decomposition import decompose_value  # noqa: E402


DEFAULT_ACCOUNT_IDS = ("a2", "a3", "a4")
DEFAULT_MAIN_ACCOUNT_ID = "a2"
DENOMINATIONS = (4, 10, 20, 50, 100, 200)
MAIN_AMOUNT_SEQUENCE = (84, 88, 94, 98, 134, 138, 144, 148)


class LocalStopEvent:
    def __init__(self) -> None:
        self._stopped = False

    def is_set(self) -> bool:
        return self._stopped

    def set(self) -> None:
        self._stopped = True


class JsonEventQueue:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def put_nowait(self, event: Any) -> None:
        payload = event_to_json(event)
        if payload.get("event_type") == "ws_raw":
            payload["payload"] = {"omitted": "ws_raw"}
        append_jsonl(self.path, payload)


def event_to_json(event: Any) -> dict[str, Any]:
    if is_dataclass(event):
        data = asdict(event)
    elif isinstance(event, dict):
        data = dict(event)
    else:
        data = {"event": repr(event)[:200]}
    payload = data.get("payload")
    if isinstance(payload, dict):
        data["payload"] = shrink_payload(payload)
    return data


def shrink_payload(payload: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in payload.items():
        if key in {"url", "token", "password", "account", "storage_state", "local_storage_items"}:
            safe[key] = "<redacted>"
        elif isinstance(value, str):
            safe[key] = value[:500]
        else:
            safe[key] = value
    return safe


def now_ms() -> int:
    return int(time.time() * 1000)


def append_jsonl(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def print_event(event: dict[str, Any]) -> None:
    print(json.dumps(event, ensure_ascii=False, sort_keys=True), flush=True)


def normalize_url(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if re.match(r"^[a-z][a-z0-9+.-]*:", text, flags=re.IGNORECASE):
        return text
    return f"https://{text}"


def load_platform_slots(path: Path) -> dict[str, dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    slots = raw.get("platform_slots") if isinstance(raw, dict) else None
    if not isinstance(slots, dict):
        raise ValueError("platform profile does not contain platform_slots")
    return slots


def parse_account_ids(value: str) -> tuple[str, ...]:
    account_ids = tuple(item.strip() for item in str(value or "").split(",") if item.strip())
    if not account_ids:
        raise ValueError("account list is empty")
    if len(set(account_ids)) != len(account_ids):
        raise ValueError(f"duplicate account ids: {value}")
    return account_ids


def resolve_account_layout(args: argparse.Namespace) -> tuple[tuple[str, ...], str, tuple[str, ...], tuple[str, ...]]:
    account_ids = parse_account_ids(str(args.accounts))
    main_account_id = str(args.main_account or "").strip()
    if main_account_id not in account_ids:
        raise ValueError(f"main account {main_account_id!r} is not in accounts {account_ids}")
    if str(args.headless_accounts or "").strip():
        headless_account_ids = parse_account_ids(str(args.headless_accounts))
    else:
        headless_account_ids = tuple(account_id for account_id in account_ids if account_id != main_account_id)
    unknown = [account_id for account_id in headless_account_ids if account_id not in account_ids]
    if unknown:
        raise ValueError(f"headless accounts not in account list: {unknown}")
    if main_account_id in headless_account_ids:
        raise ValueError("main account cannot be in headless accounts")
    sub_account_ids = tuple(account_id for account_id in account_ids if account_id != main_account_id)
    if not sub_account_ids:
        raise ValueError("at least one sub account is required")
    return account_ids, main_account_id, headless_account_ids, sub_account_ids


def config_from_slot(instance_id: str, slot: dict[str, Any], args: argparse.Namespace) -> cw.ClusterWorkerConfig:
    profile_dir = Path(args.profile_root) / instance_id
    proxy = {
        "host": str(slot.get("proxy_host") or ""),
        "port": str(slot.get("proxy_port") or ""),
        "username": str(slot.get("proxy_username") or ""),
        "password": str(slot.get("proxy_password") or ""),
    }
    return cw.ClusterWorkerConfig(
        instance_id=instance_id,
        login_url=normalize_url(str(slot.get("login_url") or "")),
        target_url="",
        username=str(slot.get("account_username") or ""),
        password=str(slot.get("account_password") or ""),
        auto_fill_login=not bool(args.no_auto_fill_login),
        headless=False,
        proxy=proxy,
        user_data_dir=str(profile_dir),
        browser_channel=str(args.browser_channel or "chrome"),
        ws_parser_path="bet_desktop.parsers.ws_protocol_parser:HeuristicWSParser",
        runtime_pipeline="legacy",
        runtime_shadow_interval_ms=1000,
        login_timeout_seconds=int(args.login_fill_timeout_seconds),
        debug_port=0,
    )


async def launch_account(playwright: Any, config: cw.ClusterWorkerConfig, event_queue: JsonEventQueue) -> dict[str, Any]:
    state = cw._RuntimeState(config.instance_id)
    context, browser = await cw._open_browser_context(playwright, config, event_queue)
    await cw._install_context_probes(config, context)
    page = await cw._active_page(context)
    ws_parser = cw._load_ws_parser(config.ws_parser_path)
    runtime: dict[str, Any] = {
        "context": context,
        "browser": browser,
        "page": page,
        "mode": "headed",
        "ws_parser": ws_parser,
        "launch_bundle": None,
    }
    cw._attach_network_collectors(config, context, page, state, event_queue, ws_parser, runtime=runtime)
    login_fill_submitted = False
    if config.login_url:
        try:
            await page.goto(config.login_url, wait_until="domcontentloaded", timeout=int(60000))
        except Exception:
            pass
        if config.auto_fill_login and config.username and config.password:
            try:
                result = await fill_login_form_when_visible(
                    context,
                    page,
                    username=config.username,
                    password=config.password,
                    timeout_seconds=int(config.login_timeout_seconds),
                )
                login_fill_submitted = bool(result.get("ok"))
                append_jsonl(
                    event_queue.path,
                    {
                        "event": "login_auto_fill",
                        "account_id": config.instance_id,
                        "ok": bool(result.get("ok")),
                        "stage": result.get("stage"),
                        "timestamp_ms": now_ms(),
                    },
                )
            except TimeoutError:
                append_jsonl(
                    event_queue.path,
                    {
                        "event": "login_auto_fill",
                        "account_id": config.instance_id,
                        "ok": False,
                        "stage": "form_not_visible",
                        "timestamp_ms": now_ms(),
                    },
                )
            except Exception as exc:
                append_jsonl(
                    event_queue.path,
                    {
                        "event": "login_auto_fill",
                        "account_id": config.instance_id,
                        "ok": False,
                        "stage": type(exc).__name__,
                        "timestamp_ms": now_ms(),
                    },
                )
    return {
        "id": config.instance_id,
        "config": config,
        "state": state,
        "runtime": runtime,
        "event_queue": event_queue,
        "ws_parser": ws_parser,
        "login_fill_submitted": login_fill_submitted,
    }


def start_state_tasks(item: dict[str, Any]) -> None:
    stop_event = LocalStopEvent()
    item["stop_event"] = stop_event
    tasks = [
        asyncio.create_task(
            cw._poll_runtime_state(
                item["config"],
                item["runtime"],
                item["state"],
                item["event_queue"],
                stop_event,
            )
        ),
        asyncio.create_task(
            cw._poll_frontend_state(
                item["config"],
                item["runtime"],
                item["state"],
                item["event_queue"],
                stop_event,
            )
        ),
    ]
    item["state_tasks"] = tasks


async def close_accounts(accounts: dict[str, dict[str, Any]]) -> None:
    for item in accounts.values():
        stop_event = item.get("stop_event")
        if stop_event is not None:
            try:
                stop_event.set()
            except Exception:
                pass
        for task in item.get("state_tasks") or []:
            try:
                if not task.done():
                    task.cancel()
            except Exception:
                pass
        runtime = item.get("runtime") or {}
        context = runtime.get("context")
        browser = runtime.get("browser")
        try:
            if context is not None:
                await context.close()
        except Exception:
            pass
        try:
            if browser is not None:
                await browser.close()
        except Exception:
            pass


async def try_auto_fill_login(item: dict[str, Any], output: Path, *, reason: str) -> dict[str, Any]:
    config = item.get("config")
    runtime = item.get("runtime") or {}
    if config is None or not bool(getattr(config, "auto_fill_login", False)):
        return {"ok": False, "stage": "disabled"}
    if not getattr(config, "username", "") or not getattr(config, "password", ""):
        return {"ok": False, "stage": "missing_credentials"}
    if bool(item.get("login_fill_submitted")):
        return {"ok": True, "stage": "already_submitted"}
    context = runtime.get("context")
    page = runtime.get("page")
    if context is None or page is None:
        return {"ok": False, "stage": "missing_context"}
    try:
        active_page = await cw._active_page(context)
        runtime["page"] = active_page
        result = await fill_login_form_when_visible(
            context,
            active_page,
            username=config.username,
            password=config.password,
            timeout_seconds=int(config.login_timeout_seconds),
        )
        ok = bool(result.get("ok"))
        if ok:
            item["login_fill_submitted"] = True
        event = {
            "event": "login_auto_fill",
            "account_id": config.instance_id,
            "reason": reason,
            "ok": ok,
            "stage": result.get("stage"),
            "timestamp_ms": now_ms(),
        }
    except TimeoutError:
        event = {
            "event": "login_auto_fill",
            "account_id": config.instance_id,
            "reason": reason,
            "ok": False,
            "stage": "form_not_visible",
            "timestamp_ms": now_ms(),
        }
    except Exception as exc:
        event = {
            "event": "login_auto_fill",
            "account_id": config.instance_id,
            "reason": reason,
            "ok": False,
            "stage": type(exc).__name__,
            "timestamp_ms": now_ms(),
        }
    append_jsonl(output, event)
    return event


async def try_auto_fill_waiting_accounts(
    accounts: dict[str, dict[str, Any]],
    output: Path,
    *,
    reason: str,
    min_interval_seconds: float = 25.0,
) -> None:
    now = time.time()
    tasks = []
    for item in accounts.values():
        if bool(item.get("login_fill_submitted")):
            continue
        last_attempt = float(item.get("last_login_fill_attempt_at") or 0.0)
        if now - last_attempt < min_interval_seconds:
            continue
        item["last_login_fill_attempt_at"] = now
        tasks.append(try_auto_fill_login(item, output, reason=reason))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def capture_launch_bundle_for_account(
    account_id: str,
    item: dict[str, Any],
    output: Path,
    *,
    force_refresh: bool = False,
) -> str:
    runtime = item["runtime"]
    previous = runtime.get("launch_bundle")
    if previous and not force_refresh:
        return "cached"
    if force_refresh:
        runtime["launch_bundle"] = None
    try:
        bundle = await cw._capture_game_launch_context(item["config"], runtime, item["event_queue"])
    except Exception as exc:
        if previous is not None:
            runtime["launch_bundle"] = previous
        return f"error:{type(exc).__name__}"
    if bundle:
        return "captured"
    if previous is not None:
        runtime["launch_bundle"] = previous
        return "cached_after_missing"
    return "missing"


async def capture_headless_launch_bundles(
    accounts: dict[str, dict[str, Any]],
    output: Path,
    *,
    headless_account_ids: tuple[str, ...],
    force_refresh: bool = False,
) -> dict[str, str]:
    results: dict[str, str] = {}
    for account_id in headless_account_ids:
        item = accounts.get(account_id)
        if not item:
            continue
        results[account_id] = await capture_launch_bundle_for_account(
            account_id,
            item,
            output,
            force_refresh=force_refresh,
        )
    append_jsonl(
        output,
        {
            "event": "launch_bundle_probe",
            "force_refresh": force_refresh,
            "results": results,
            "timestamp_ms": now_ms(),
        },
    )
    return results


async def wait_for_gate(
    path: Path,
    output: Path,
    label: str,
    timeout_seconds: int,
    *,
    accounts: dict[str, dict[str, Any]] | None = None,
    headless_account_ids: tuple[str, ...] = (),
    launch_capture_interval_seconds: float = 15.0,
) -> bool:
    if path.exists():
        path.unlink()
    deadline = time.time() + max(1, timeout_seconds)
    next_emit = 0.0
    next_capture = 0.0
    next_login_fill = 0.0
    while time.time() < deadline:
        if path.exists():
            append_jsonl(output, {"event": "gate_detected", "label": label, "gate_file": str(path), "timestamp_ms": now_ms()})
            print_event({"event": "gate_detected", "label": label})
            return True
        now = time.time()
        if accounts and now >= next_login_fill:
            await try_auto_fill_waiting_accounts(accounts, output, reason=f"waiting_{label}", min_interval_seconds=20.0)
            next_login_fill = now + 20.0
        if accounts and now >= next_capture:
            await capture_headless_launch_bundles(
                accounts,
                output,
                headless_account_ids=headless_account_ids,
                force_refresh=True,
            )
            next_capture = now + max(1.0, float(launch_capture_interval_seconds))
        if now >= next_emit:
            append_jsonl(output, {"event": "waiting_gate", "label": label, "gate_file": str(path), "timestamp_ms": now_ms()})
            print_event({"event": "waiting_gate", "label": label, "gate_file": str(path)})
            next_emit = now + 15.0
        await asyncio.sleep(1.0)
    return False


async def refresh_account_state(item: dict[str, Any]) -> dict[str, Any]:
    config = item["config"]
    runtime = item["runtime"]
    state = item["state"]
    context = runtime.get("context")
    if context is None:
        return {"game_ready": False, "hall_ready": False, "error": "missing_context"}
    page = await cw._active_page(context)
    runtime["page"] = page
    load = await read_baccarat_load_state(page)
    snapshot = await read_live_runtime_snapshot(page, instance_id=config.instance_id)
    if snapshot:
        cw._update_from_page_runtime_state(state, snapshot)
    return status_from_snapshot(item, load, snapshot)


def status_from_snapshot(item: dict[str, Any], load: Any, snapshot: Any) -> dict[str, Any]:
    frame = getattr(snapshot, "frame", None) if snapshot is not None else None
    try:
        trusted = item["state"].snapshot()
        summary = dict(getattr(trusted, "safe_summary", {}) or {})
    except Exception:
        trusted = None
        summary = {}
    try:
        guard = cw._live_fire_plan_guard_snapshot(item.get("runtime") or {}, item["state"])
    except Exception:
        guard = {}
    snapshot_game_no = str(getattr(snapshot, "game_no", "") or "") if snapshot is not None else ""
    guard_game_no = str(guard.get("observed_batch_id") or "")
    snapshot_countdown = getattr(snapshot, "countdown_seconds", None) if snapshot is not None else None
    guard_countdown = guard.get("countdown")
    countdown = snapshot_countdown
    try:
        guard_countdown_int = int(guard_countdown)
    except (TypeError, ValueError):
        guard_countdown_int = -1
    if countdown is None and guard_countdown_int >= 0:
        countdown = guard_countdown_int
    betting_open = bool(getattr(snapshot, "betting_open", False)) if snapshot is not None else False
    if not betting_open:
        betting_open = bool(guard.get("betting_open"))
    room_id = str(getattr(frame, "room_id", "") or summary.get("room_id") or "") if frame is not None else str(summary.get("room_id") or "")
    room_label = str(getattr(frame, "table_label", "") or summary.get("room_label") or "") if frame is not None else str(summary.get("room_label") or "")
    return {
        "account_id": item["id"],
        "mode": str((item.get("runtime") or {}).get("mode") or ""),
        "game_ready": bool(getattr(load, "game_ready", False)),
        "hall_ready": bool(getattr(load, "hall_ready", False)),
        "scene": str(getattr(load, "scene_name", "") or ""),
        "game_no": guard_game_no or snapshot_game_no,
        "snapshot_game_no": snapshot_game_no,
        "guard_game_no": guard_game_no,
        "game_no_source": "guard" if guard_game_no else ("snapshot" if snapshot_game_no else ""),
        "countdown": countdown,
        "betting_open": betting_open,
        "room_id": room_id,
        "room_label": room_label,
        "locked_room_id": str(summary.get("locked_room_id") or ""),
        "locked_room_label": str(summary.get("locked_room_label") or ""),
        "balance_cents": getattr(frame, "balance_cents", None) if frame is not None else None,
        "pending_cents": getattr(frame, "pending_chip_cents", None) if frame is not None else None,
        "guard_phase": str(guard.get("phase") or ""),
        "guard_shadow_fresh": bool(guard.get("shadow_fresh")),
        "status_ts_ms": now_ms(),
    }


def as_int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def status_room_identity(status: dict[str, Any], *, room_index: int) -> str:
    label = str(status.get("locked_room_label") or status.get("room_label") or "").strip()
    room_id = str(status.get("locked_room_id") or status.get("room_id") or "").strip()
    if label:
        try:
            normalized = str(cw._normalized_room_label(label) or "").strip()
        except Exception:
            normalized = ""
        return normalized or label
    if room_id:
        try:
            hinted = str(cw._room_label_hint_from_id(room_id) or "").strip()
        except Exception:
            hinted = ""
        return hinted or room_id
    if bool(status.get("game_ready")) and room_index > 0:
        return f"room_index:{int(room_index)}"
    return ""


def status_round_guard_key(status: dict[str, Any]) -> str:
    game_no = str(status.get("game_no") or "").strip()
    if not game_no:
        return ""
    try:
        return str(cw._manual_keepalive_batch_guard_key(game_no) or "").strip()
    except Exception:
        parts = game_no.split("-")
        if len(parts) >= 3:
            return "-".join(parts[:3])
        return game_no


def compact_status(status: dict[str, Any], *, room_index: int) -> dict[str, Any]:
    return {
        "game": bool(status.get("game_ready")),
        "hall": bool(status.get("hall_ready")),
        "room": status_room_identity(status, room_index=room_index) or "-",
        "round": status.get("game_no") or "-",
        "round_key": status_round_guard_key(status) or "-",
        "cd": status.get("countdown"),
        "open": bool(status.get("betting_open")),
    }


async def collect_statuses(accounts: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    items = list(accounts.items())
    results = await asyncio.gather(
        *(refresh_account_state(item) for _, item in items),
        return_exceptions=True,
    )
    statuses: dict[str, dict[str, Any]] = {}
    for (account_id, item), result in zip(items, results):
        if isinstance(result, Exception):
            status = {"account_id": account_id, "error": type(result).__name__, "game_ready": False, "hall_ready": False}
        else:
            status = dict(result or {})
            status.setdefault("account_id", account_id)
        item["last_status"] = status
        statuses[account_id] = status
    return statuses


async def write_status(accounts: dict[str, dict[str, Any]], path: Path, output: Path) -> dict[str, dict[str, Any]]:
    statuses = await collect_statuses(accounts)
    lines = [
        (
            f"{sid} {s.get('mode', '-')} game={s.get('game_ready')} hall={s.get('hall_ready')} "
            f"room={status_room_identity(s, room_index=0) or '-'} "
            f"round={s.get('game_no') or '-'} cd={s.get('countdown')} open={s.get('betting_open')} "
            f"pending={s.get('pending_cents')}"
        )
        for sid, s in statuses.items()
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    append_jsonl(output, {"event": "status", "statuses": statuses, "timestamp_ms": now_ms()})
    return statuses


def coordinator_readiness(
    accounts: dict[str, dict[str, Any]],
    statuses: dict[str, dict[str, Any]],
    *,
    min_countdown: int,
    room_index: int,
    allow_countdown_window: bool,
) -> dict[str, Any]:
    rooms: dict[str, str] = {}
    round_keys: dict[str, str] = {}
    round_parts: list[str] = []
    countdowns: dict[str, int] = {}
    fallback_accounts: list[str] = []
    account_ids = list(accounts.keys())
    for account_id in account_ids:
        status = statuses.get(account_id) or {}
        if not status:
            return {"ok": False, "reason": f"{account_id}:status_missing"}
        if not bool(status.get("game_ready")):
            return {"ok": False, "reason": f"{account_id}:not_in_game", "account_id": account_id}
        room_key = status_room_identity(status, room_index=room_index)
        if not room_key:
            return {"ok": False, "reason": f"{account_id}:room_missing", "account_id": account_id}
        game_no = str(status.get("game_no") or "").strip()
        if not game_no:
            return {"ok": False, "reason": f"{account_id}:round_missing", "account_id": account_id}
        round_key = status_round_guard_key(status)
        if not round_key:
            return {"ok": False, "reason": f"{account_id}:round_key_missing", "account_id": account_id}
        countdown = as_int_or_none(status.get("countdown"))
        if countdown is None:
            return {"ok": False, "reason": f"{account_id}:countdown_missing", "account_id": account_id}
        if countdown < int(min_countdown):
            return {
                "ok": False,
                "reason": f"{account_id}:countdown_lt_{int(min_countdown)}",
                "account_id": account_id,
                "countdown": countdown,
            }
        if not bool(status.get("betting_open")):
            if not allow_countdown_window:
                return {"ok": False, "reason": f"{account_id}:betting_closed", "account_id": account_id}
            fallback_accounts.append(account_id)
        rooms[account_id] = room_key
        round_keys[account_id] = round_key
        countdowns[account_id] = countdown
        round_parts.append(f"{account_id}:{game_no}")
    unique_rooms = sorted(set(rooms.values()))
    if len(unique_rooms) != 1:
        return {"ok": False, "reason": "room_mismatch", "rooms": rooms}
    unique_round_keys = sorted(set(round_keys.values()))
    if len(unique_round_keys) != 1:
        return {"ok": False, "reason": "round_mismatch", "round_keys": round_keys}
    return {
        "ok": True,
        "reason": "ready",
        "room_key": unique_rooms[0],
        "round_key": unique_round_keys[0],
        "round_id": "|".join(round_parts),
        "countdowns": countdowns,
        "fallback_accounts": fallback_accounts,
    }


async def coordinator_pre_send_check(
    accounts: dict[str, dict[str, Any]],
    status_path: Path,
    output: Path,
    *,
    min_countdown: int,
    room_index: int,
    allow_countdown_window: bool,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    statuses = await write_status(accounts, status_path, output)
    decision = coordinator_readiness(
        accounts,
        statuses,
        min_countdown=min_countdown,
        room_index=room_index,
        allow_countdown_window=allow_countdown_window,
    )
    append_jsonl(
        output,
        {
            "event": "coordinator_presend_check",
            "decision": decision,
            "statuses": {account_id: compact_status(status, room_index=room_index) for account_id, status in statuses.items()},
            "timestamp_ms": now_ms(),
        },
    )
    return decision, statuses


async def handoff_and_enter_room(
    accounts: dict[str, dict[str, Any]],
    args: argparse.Namespace,
    output: Path,
    *,
    headless_account_ids: tuple[str, ...],
) -> bool:
    async def run_one(account_id: str) -> bool:
        item = accounts[account_id]
        config = item["config"]
        runtime = item["runtime"]
        state = item["state"]
        event_queue = item["event_queue"]
        ws_parser = item["ws_parser"]
        handoff_ok = False
        attempts = max(1, int(args.handoff_attempts))
        for attempt in range(1, attempts + 1):
            refresh_result = await capture_launch_bundle_for_account(
                account_id,
                item,
                output,
                force_refresh=attempt == 1 or bool(args.force_refresh_launch_on_retry),
            )
            append_jsonl(
                output,
                {
                    "event": "handoff_launch_bundle",
                    "account_id": account_id,
                    "attempt": attempt,
                    "result": refresh_result,
                    "timestamp_ms": now_ms(),
                },
            )
            handoff_ok = bool(await cw._handoff_to_headless(config, runtime, item["playwright"], state, event_queue, ws_parser))
            append_jsonl(
                output,
                {
                    "event": "handoff_result",
                    "account_id": account_id,
                    "attempt": attempt,
                    "attempts": attempts,
                    "ok": handoff_ok,
                    "timestamp_ms": now_ms(),
                },
            )
            print_event({"event": "handoff_result", "account_id": account_id, "attempt": attempt, "attempts": attempts, "ok": handoff_ok})
            if handoff_ok:
                break
            if attempt < attempts:
                await asyncio.sleep(max(0.1, float(args.handoff_retry_delay_seconds)))
        if not handoff_ok:
            return False
        enter_ok = await cw._enter_headless_room(config, runtime, event_queue, int(args.room_index), state)
        append_jsonl(output, {"event": "enter_room_result", "account_id": account_id, "ok": bool(enter_ok), "timestamp_ms": now_ms()})
        print_event({"event": "enter_room_result", "account_id": account_id, "ok": bool(enter_ok)})
        return bool(enter_ok)

    results = await asyncio.gather(*(run_one(account_id) for account_id in headless_account_ids))
    return all(results)


async def wait_all_game_ready(
    accounts: dict[str, dict[str, Any]],
    status_path: Path,
    output: Path,
    *,
    room_index: int,
    timeout_seconds: int,
) -> bool:
    deadline = time.time() + max(1, timeout_seconds)
    while time.time() < deadline:
        statuses = await write_status(accounts, status_path, output)
        if all(bool(status.get("game_ready")) for status in statuses.values()):
            return True
        await retry_headless_room_entries(accounts, statuses, output, room_index=room_index)
        await asyncio.sleep(1.5)
    return False


async def wait_account_game_ready(
    accounts: dict[str, dict[str, Any]],
    status_path: Path,
    output: Path,
    account_id: str,
    *,
    room_index: int,
    timeout_seconds: int,
) -> bool:
    deadline = time.time() + max(1, timeout_seconds)
    next_emit = 0.0
    while time.time() < deadline:
        statuses = await write_status(accounts, status_path, output)
        status = statuses.get(account_id, {})
        if bool(status.get("game_ready")):
            append_jsonl(output, {"event": "account_game_ready", "account_id": account_id, "timestamp_ms": now_ms()})
            print_event({"event": "account_game_ready", "account_id": account_id})
            return True
        await retry_headless_room_entries(accounts, statuses, output, room_index=room_index, account_ids=(account_id,))
        now = time.time()
        if now >= next_emit:
            append_jsonl(
                output,
                {"event": "waiting_account_game_ready", "account_id": account_id, "status": status, "timestamp_ms": now_ms()},
            )
            print_event(
                {
                    "event": "waiting_account_game_ready",
                    "account_id": account_id,
                    "game_ready": bool(status.get("game_ready")),
                    "hall_ready": bool(status.get("hall_ready")),
                    "scene": status.get("scene"),
                }
            )
            next_emit = now + 10.0
        await asyncio.sleep(1.5)
    return False


async def retry_headless_room_entries(
    accounts: dict[str, dict[str, Any]],
    statuses: dict[str, dict[str, Any]],
    output: Path,
    *,
    room_index: int,
    account_ids: tuple[str, ...] | None = None,
) -> None:
    now = time.time()
    if account_ids is None:
        account_ids = tuple(accounts.keys())
    for account_id in account_ids:
        status = statuses.get(account_id, {})
        if status.get("game_ready") or not status.get("hall_ready"):
            continue
        item = accounts.get(account_id)
        if not item:
            continue
        runtime = item["runtime"]
        if runtime.get("mode") != "headless":
            continue
        last_retry = float(item.get("last_room_retry_at") or 0.0)
        if now - last_retry < 12.0:
            continue
        item["last_room_retry_at"] = now
        page = runtime.get("page")
        if status.get("hall_ready") and not status.get("game_ready"):
            runtime["room_entry_pending"] = None
            runtime["headless_hall_ready"] = True
            if page is not None and not runtime.get("hall_entry_buttons"):
                try:
                    runtime["hall_entry_buttons"] = await cw.resolve_baccarat_hall_entry_buttons(page)
                except Exception:
                    runtime["hall_entry_buttons"] = []
            append_jsonl(
                output,
                {
                    "event": "retry_enter_room_hall_ready_restored",
                    "account_id": account_id,
                    "button_count": len(runtime.get("hall_entry_buttons") or []),
                    "timestamp_ms": now_ms(),
                },
            )
        append_jsonl(output, {"event": "retry_enter_room", "account_id": account_id, "timestamp_ms": now_ms()})
        print_event({"event": "retry_enter_room", "account_id": account_id})
        try:
            enter_ok = await cw._enter_headless_room(
                item["config"],
                item["runtime"],
                item["event_queue"],
                int(room_index),
                item["state"],
            )
        except Exception as exc:
            append_jsonl(
                output,
                {
                    "event": "retry_enter_room_failed",
                    "account_id": account_id,
                    "error": type(exc).__name__,
                    "timestamp_ms": now_ms(),
                },
            )
            continue
        append_jsonl(
            output,
            {"event": "retry_enter_room_result", "account_id": account_id, "ok": bool(enter_ok), "timestamp_ms": now_ms()},
        )
        print_event({"event": "retry_enter_room_result", "account_id": account_id, "ok": bool(enter_ok)})


def split_sub_amounts(amount: int, count: int) -> tuple[int, ...]:
    if count <= 0:
        raise ValueError("sub account count must be positive")
    if amount < count * 4:
        raise ValueError(f"cannot split amount {amount} into {count} accounts")

    candidates: list[tuple[float, tuple[int, ...]]] = []

    def walk(remaining: int, slots: int, prefix: tuple[int, ...]) -> None:
        if slots == 1:
            value = remaining
            if value < 4:
                return
            values = (*prefix, value)
            try:
                chips = [decompose_value(item, DENOMINATIONS, max_steps=5) for item in values]
            except Exception:
                return
            target = amount / count
            score = (
                (max(values) - min(values)) * 100
                + (max(len(item) for item in chips) - min(len(item) for item in chips)) * 10
                + sum(abs(item - target) for item in values)
            )
            candidates.append((score, values))
            return

        min_remaining = 4 * (slots - 1)
        for value in range(4, remaining - min_remaining + 1, 2):
            try:
                decompose_value(value, DENOMINATIONS, max_steps=5)
            except Exception:
                continue
            walk(remaining - value, slots - 1, (*prefix, value))

    walk(amount, count, ())
    if not candidates:
        raise ValueError(f"cannot split amount {amount} into {count} accounts")
    return sorted(candidates, key=lambda item: item[0])[0][1]


def build_plan(round_index: int, main_account_id: str, sub_account_ids: tuple[str, ...]) -> list[dict[str, Any]]:
    amount = MAIN_AMOUNT_SEQUENCE[(round_index - 1) % len(MAIN_AMOUNT_SEQUENCE)]
    main_side = "banker" if round_index % 2 else "player"
    sub_side = "player" if main_side == "banker" else "banker"
    sub_amounts = split_sub_amounts(amount, len(sub_account_ids))
    if sub_amounts:
        offset = (round_index - 1) % len(sub_amounts)
        sub_amounts = (*sub_amounts[offset:], *sub_amounts[:offset])
    legs = [{"account_id": main_account_id, "role": "main", "side": main_side, "amount": amount}]
    for account_id, sub_amount in zip(sub_account_ids, sub_amounts):
        legs.append({"account_id": account_id, "role": "sub", "side": sub_side, "amount": sub_amount})
    for leg in legs:
        leg["chips"] = decompose_value(int(leg["amount"]), DENOMINATIONS, max_steps=5)
        leg["steps"] = len(leg["chips"])
    return legs


async def wait_betting_round(
    accounts: dict[str, dict[str, Any]],
    status_path: Path,
    output: Path,
    *,
    last_round: str,
    min_countdown: int,
    room_index: int,
    timeout_seconds: int,
    allow_countdown_window: bool,
    coordinator_status_refresh_ms: int,
    coordinator_poll_ms: int,
) -> tuple[str, dict[str, dict[str, Any]]] | None:
    deadline = time.time() + max(1, timeout_seconds)
    refresh_interval = max(0.2, int(coordinator_status_refresh_ms) / 1000.0)
    poll_interval = max(0.05, int(coordinator_poll_ms) / 1000.0)
    last_statuses: dict[str, dict[str, Any]] = {}
    next_refresh_at = 0.0
    presend_checked_at_by_round: dict[str, float] = {}
    while time.time() < deadline:
        current = time.time()
        if current >= next_refresh_at or not last_statuses:
            last_statuses = await write_status(accounts, status_path, output)
            await retry_headless_room_entries(accounts, last_statuses, output, room_index=room_index)
            next_refresh_at = time.time() + refresh_interval

        decision = coordinator_readiness(
            accounts,
            last_statuses,
            min_countdown=min_countdown,
            room_index=room_index,
            allow_countdown_window=allow_countdown_window,
        )
        round_id = str(decision.get("round_id") or "")
        if bool(decision.get("ok")) and round_id and round_id != last_round:
            checked_at = presend_checked_at_by_round.get(round_id, 0.0)
            if time.time() - checked_at >= refresh_interval:
                presend_checked_at_by_round[round_id] = time.time()
                append_jsonl(
                    output,
                    {
                        "event": "coordinator_candidate_ready",
                        "decision": decision,
                        "timestamp_ms": now_ms(),
                    },
                )
                final_decision, final_statuses = await coordinator_pre_send_check(
                    accounts,
                    status_path,
                    output,
                    min_countdown=min_countdown,
                    room_index=room_index,
                    allow_countdown_window=allow_countdown_window,
                )
                final_round_id = str(final_decision.get("round_id") or "")
                if bool(final_decision.get("ok")) and final_round_id and final_round_id != last_round:
                    fallback_accounts = list(final_decision.get("fallback_accounts") or [])
                    if fallback_accounts:
                        append_jsonl(
                            output,
                            {
                                "event": "countdown_window_fallback",
                                "accounts": fallback_accounts,
                                "round_id": final_round_id,
                                "min_countdown": min_countdown,
                                "timestamp_ms": now_ms(),
                            },
                        )
                    return final_round_id, final_statuses
                last_statuses = final_statuses
                next_refresh_at = time.time() + refresh_interval
        await asyncio.sleep(poll_interval)
    return None


class DelayPatch:
    def __init__(self, delay_ms: int, confirm_ms: int) -> None:
        self.delay_ms = int(delay_ms)
        self.confirm_ms = int(confirm_ms)
        self.old: dict[str, int] = {}

    def __enter__(self) -> None:
        names = {
            "LIVE_FIRE_PLAN_CHIP_TO_SIDE_DELAY_MIN_MS": self.delay_ms,
            "LIVE_FIRE_PLAN_CHIP_TO_SIDE_DELAY_MAX_MS": self.delay_ms,
            "LIVE_FIRE_PLAN_SAME_CHIP_DELAY_MIN_MS": self.delay_ms,
            "LIVE_FIRE_PLAN_SAME_CHIP_DELAY_MAX_MS": self.delay_ms,
            "LIVE_FIRE_PLAN_SWITCH_CHIP_DELAY_MIN_MS": self.delay_ms,
            "LIVE_FIRE_PLAN_SWITCH_CHIP_DELAY_MAX_MS": self.delay_ms,
            "LIVE_FIRE_PLAN_CONFIRM_DELAY_MS": self.confirm_ms,
        }
        for name, value in names.items():
            self.old[name] = int(getattr(cw, name))
            setattr(cw, name, int(value))

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        for name, value in self.old.items():
            setattr(cw, name, value)


def prime_countdown_window_for_execution(
    accounts: dict[str, dict[str, Any]],
    statuses: dict[str, dict[str, Any]],
    output: Path,
    *,
    room_index: int,
) -> None:
    primed: dict[str, dict[str, Any]] = {}
    for account_id, item in accounts.items():
        status = statuses.get(account_id) or {}
        countdown = as_int_or_none(status.get("countdown"))
        game_no = str(status.get("game_no") or "").strip()
        if countdown is None or countdown <= 0 or not game_no:
            continue
        room_label = str(status.get("room_label") or status.get("locked_room_label") or status_room_identity(status, room_index=room_index) or "")
        room_id = str(status.get("room_id") or status.get("locked_room_id") or "")
        safe_summary = {
            "room_id": room_id,
            "room_label": room_label,
            "frontend_room_id": room_id,
            "frontend_room_label": room_label,
            "runtime_room_id": room_id,
            "runtime_room_label": room_label,
            "phase_text": "betting_open",
            "frontend_phase_text": "betting_open",
            "runtime_phase": "betting_open",
            "runtime_phase_label": "betting_open",
            "canvas_phase_text": "betting_open",
            "canvas_game_no": game_no,
            "runtime_betting_open": True,
            "runtime_is_can_betting": True,
            "frontend_runtime_is_can_betting": True,
            "runtime_timed": countdown,
            "frontend_runtime_timed": countdown,
        }
        shadow_state = {
            "batch_id": game_no,
            "room_id": room_id,
            "room_label": room_label,
            "countdown": countdown,
            "betting_open": True,
            "is_can_betting": True,
            "phase_key": "betting_open",
            "phase_label": "betting_open",
            "source": "interval_probe_presend",
        }
        try:
            runtime = item.get("runtime") or {}
            runtime["runtime_v2_shadow_previous_decision"] = None
            runtime["runtime_v2_shadow_last_ms"] = now_ms()
            runtime["runtime_v2_shadow_last_payload"] = {
                "stable_state": dict(shadow_state),
                "accepted_state": dict(shadow_state),
            }
            item["state"].update(
                batch_id=game_no,
                room_id=room_id,
                room_label=room_label,
                phase_text="betting_open",
                countdown=countdown,
                source="canvas_text_runtime_interval_probe_presend",
                confidence=1.0,
                safe_summary=safe_summary,
                runtime_state={"frame": {"timed": countdown, "is_can_betting": True}},
            )
            primed[account_id] = {"round": game_no, "countdown": countdown, "room": room_label or room_id or "-"}
        except Exception as exc:
            primed[account_id] = {"round": game_no, "countdown": countdown, "error": type(exc).__name__}
    append_jsonl(output, {"event": "execution_window_primed", "accounts": primed, "timestamp_ms": now_ms()})


async def run_worker_plan_with_timing(item: dict[str, Any], cmd: dict[str, Any]) -> dict[str, Any]:
    timing = {
        "queued_ms": now_ms(),
        "worker_call_start_ms": 0,
        "worker_call_end_ms": 0,
        "worker_wall_ms": 0,
        "execution_id": cmd.get("execution_id"),
        "batch_id": cmd.get("batch_id"),
    }
    timing["worker_call_start_ms"] = now_ms()
    try:
        result = await cw._execute_live_fire_bet_plan(
            item["config"],
            item["runtime"],
            item["state"],
            item["event_queue"],
            cmd,
        )
        return {"result": result, "timing": timing}
    except Exception as exc:
        return {"error": type(exc).__name__, "timing": timing}
    finally:
        timing["worker_call_end_ms"] = now_ms()
        timing["worker_wall_ms"] = timing["worker_call_end_ms"] - timing["worker_call_start_ms"]


def load_click_trace_records(trace_ids: set[str], *, max_lines: int = 6000) -> dict[str, dict[str, Any]]:
    if not trace_ids:
        return {}
    try:
        path = cw._live_fire_click_trace_log_path()
    except Exception:
        return {}
    if not path.exists():
        return {}
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return {}
    records: dict[str, dict[str, Any]] = {}
    for line in reversed(lines[-max_lines:]):
        try:
            record = json.loads(line)
        except Exception:
            continue
        trace_id = str(record.get("trace_id") or "")
        if trace_id in trace_ids and trace_id not in records:
            records[trace_id] = record
            if len(records) == len(trace_ids):
                break
    return records


def summarize_click_trace(record: dict[str, Any], *, confirm_ms: int) -> dict[str, Any]:
    steps = list(record.get("steps") or [])
    compact_steps: list[dict[str, Any]] = []
    first_start: int | None = None
    last_end: int | None = None
    chip_click_total = 0
    side_click_total = 0
    chip_to_side_wait_total = 0
    next_wait_total = 0
    for step in steps:
        chip_start = as_int_or_none(step.get("chip_click_start_ms"))
        chip_end = as_int_or_none(step.get("chip_click_end_ms"))
        side_start = as_int_or_none(step.get("side_click_start_ms"))
        side_end = as_int_or_none(step.get("side_click_end_ms"))
        chip_click_ms = max(0, int(chip_end - chip_start)) if chip_start is not None and chip_end is not None else None
        side_click_ms = max(0, int(side_end - side_start)) if side_start is not None and side_end is not None else None
        chip_to_side_delay = as_int_or_none(step.get("chip_to_side_delay_ms")) or 0
        next_delay = as_int_or_none(step.get("next_delay_ms")) or 0
        if chip_click_ms is not None:
            chip_click_total += chip_click_ms
        if side_click_ms is not None:
            side_click_total += side_click_ms
        chip_to_side_wait_total += chip_to_side_delay
        next_wait_total += next_delay
        starts = [value for value in (chip_start, side_start) if value is not None]
        ends = [value for value in (chip_end, side_end) if value is not None]
        if starts:
            step_first = min(starts)
            first_start = step_first if first_start is None else min(first_start, step_first)
        if ends:
            step_last = max(ends)
            last_end = step_last if last_end is None else max(last_end, step_last)
        compact_steps.append(
            {
                "step_index": step.get("step_index"),
                "chip": step.get("chip"),
                "status": step.get("status"),
                "chip_click_ms": chip_click_ms,
                "chip_to_side_delay_ms": chip_to_side_delay,
                "side_click_ms": side_click_ms,
                "next_delay_ms": next_delay,
                "step_span_ms": max(0, int(max(ends) - min(starts))) if starts and ends else None,
            }
        )
    click_span_ms = max(0, int(last_end - first_start)) if first_start is not None and last_end is not None else None
    return {
        "trace_id": record.get("trace_id"),
        "status": record.get("status"),
        "planned_amount": record.get("planned_amount"),
        "actual_amount": record.get("actual_amount"),
        "missing_amount": record.get("missing_amount"),
        "worker_elapsed_ms": record.get("elapsed_ms"),
        "step_count": record.get("step_count"),
        "countdown_at_start": record.get("countdown_at_start"),
        "click_span_ms": click_span_ms,
        "first_click_start_ms": first_start,
        "last_click_end_ms": last_end,
        "chip_click_total_ms": chip_click_total,
        "side_click_total_ms": side_click_total,
        "chip_to_side_wait_total_ms": chip_to_side_wait_total,
        "next_wait_total_ms": next_wait_total,
        "confirm_wait_config_ms": int(confirm_ms),
        "steps": compact_steps,
    }


async def execute_round(
    accounts: dict[str, dict[str, Any]],
    legs: list[dict[str, Any]],
    round_id: str,
    statuses: dict[str, dict[str, Any]],
    delay_ms: int,
    confirm_ms: int,
    output: Path,
    *,
    room_index: int,
    allow_countdown_window: bool,
) -> dict[str, Any]:
    started = now_ms()
    prime_started = 0
    prime_finished = 0
    tasks = []
    if allow_countdown_window:
        prime_started = now_ms()
        prime_countdown_window_for_execution(accounts, statuses, output, room_index=room_index)
        prime_finished = now_ms()
    dispatch_started = now_ms()
    task_meta: list[dict[str, Any]] = []
    with DelayPatch(delay_ms, confirm_ms):
        for leg in legs:
            item = accounts[str(leg["account_id"])]
            account_status = statuses.get(str(leg["account_id"])) or {}
            account_round_id = str(account_status.get("game_no") or round_id).strip()
            execution_id = f"interval_probe:{round_id}:{leg['account_id']}:{delay_ms}:{now_ms()}"
            cmd = {
                "side": str(leg["side"]),
                "batch_id": account_round_id,
                "amount": int(leg["amount"]),
                "chip_sequence": list(leg["chips"]),
                "execution_id": execution_id,
                "execution_contract": {
                    "execution_id": f"interval_probe:{round_id}:{leg['account_id']}:{delay_ms}",
                    "max_worker_required_ms": max(5000, int(len(leg["chips"]) * delay_ms * 2 + confirm_ms + 1200)),
                },
            }
            task_meta.append(
                {
                    "account_id": str(leg["account_id"]),
                    "batch_id": account_round_id,
                    "execution_id": execution_id,
                    "queued_ms": now_ms(),
                }
            )
            tasks.append(run_worker_plan_with_timing(item, cmd))
        dispatch_enqueued = now_ms()
        gather_started = now_ms()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        gather_finished = now_ms()
    clean_results: list[dict[str, Any]] = []
    trace_ids: set[str] = set()
    worker_timings: dict[str, dict[str, Any]] = {}
    for leg, result in zip(legs, results):
        if isinstance(result, Exception):
            result_payload = None
            timing = {"worker_wall_ms": 0, "error": type(result).__name__}
            clean = {
                "account_id": leg["account_id"],
                "status": "ERROR",
                "planned_amount": leg["amount"],
                "actual_amount": 0,
                "missing_amount": leg["amount"],
                "error": type(result).__name__,
            }
        else:
            wrapper = dict(result or {})
            timing = dict(wrapper.get("timing") or {})
            if wrapper.get("error"):
                result_payload = None
                clean = {
                    "account_id": leg["account_id"],
                    "status": "ERROR",
                    "planned_amount": leg["amount"],
                    "actual_amount": 0,
                    "missing_amount": leg["amount"],
                    "error": wrapper.get("error"),
                }
            else:
                result_payload = wrapper.get("result")
                clean = dict(result_payload or {})
        account_id = str(leg["account_id"])
        worker_timings[account_id] = timing
        clean["probe_timing"] = timing
        trace_id = str(((clean.get("evidence") or {}) if isinstance(clean.get("evidence"), dict) else {}).get("click_trace_id") or "")
        if trace_id:
            trace_ids.add(trace_id)
        clean_results.append(clean)
    click_trace_records = load_click_trace_records(trace_ids)
    for result in clean_results:
        trace_id = str(((result.get("evidence") or {}) if isinstance(result.get("evidence"), dict) else {}).get("click_trace_id") or "")
        if trace_id and trace_id in click_trace_records:
            result["click_trace_timing"] = summarize_click_trace(click_trace_records[trace_id], confirm_ms=confirm_ms)
    round_finished = now_ms()
    per_account_timing = {
        str(result.get("account_id") or ""): {
            "worker_wall_ms": (result.get("probe_timing") or {}).get("worker_wall_ms"),
            "worker_elapsed_ms": result.get("elapsed_ms"),
            "click_span_ms": (result.get("click_trace_timing") or {}).get("click_span_ms"),
            "step_count": result.get("step_count"),
            "status": result.get("status"),
            "actual_amount": result.get("actual_amount"),
            "missing_amount": result.get("missing_amount"),
        }
        for result in clean_results
    }
    event = {
        "event": "round_result",
        "round_id": round_id,
        "delay_ms": int(delay_ms),
        "confirm_ms": int(confirm_ms),
        "legs": legs,
        "results": clean_results,
        "timing": {
            "round_start_ms": started,
            "prime_start_ms": prime_started,
            "prime_end_ms": prime_finished,
            "prime_elapsed_ms": max(0, prime_finished - prime_started) if prime_started and prime_finished else 0,
            "dispatch_start_ms": dispatch_started,
            "dispatch_enqueued_ms": dispatch_enqueued,
            "dispatch_enqueue_elapsed_ms": max(0, dispatch_enqueued - dispatch_started),
            "gather_start_ms": gather_started,
            "gather_end_ms": gather_finished,
            "gather_elapsed_ms": max(0, gather_finished - gather_started),
            "round_end_ms": round_finished,
            "round_elapsed_ms": round_finished - started,
            "per_account": per_account_timing,
        },
        "task_meta": task_meta,
        "elapsed_ms": round_finished - started,
        "timestamp_ms": round_finished,
    }
    append_jsonl(output, {"event": "round_timing", "round_id": round_id, "delay_ms": int(delay_ms), "timing": event["timing"], "timestamp_ms": round_finished})
    append_jsonl(output, event)
    print_event(
        {
            "event": "round_result",
            "round_id": round_id,
            "delay_ms": delay_ms,
            "elapsed_ms": event["elapsed_ms"],
            "results": [
                {
                    "id": r.get("account_id"),
                    "status": r.get("status"),
                    "planned": r.get("planned_amount"),
                    "actual": r.get("actual_amount"),
                    "missing": r.get("missing_amount"),
                    "ms": r.get("elapsed_ms"),
                }
                for r in clean_results
            ],
        }
    )
    return event


def summarize(rounds: list[dict[str, Any]]) -> dict[str, Any]:
    by_delay: dict[str, dict[str, Any]] = {}
    for event in rounds:
        key = str(event.get("delay_ms"))
        bucket = by_delay.setdefault(key, {"rounds": 0, "complete_rounds": 0, "missing_total": 0.0, "max_leg_ms": 0})
        bucket["rounds"] += 1
        missing = 0.0
        for result in event.get("results", []):
            missing += float(result.get("missing_amount") or 0)
            bucket["max_leg_ms"] = max(int(bucket["max_leg_ms"]), int(result.get("elapsed_ms") or 0))
        bucket["missing_total"] += missing
        if missing <= 0:
            bucket["complete_rounds"] += 1
    return {"event": "summary", "by_delay": by_delay, "timestamp_ms": now_ms()}


async def run(args: argparse.Namespace) -> int:
    if not args.live:
        print_event({"event": "refused", "reason": "missing --live"})
        return 2
    output = Path(args.output)
    event_log = Path(args.event_log)
    status_path = Path(args.status_file)
    prepared_gate = Path(args.prepared_gate_file)
    output.parent.mkdir(parents=True, exist_ok=True)
    status_path.parent.mkdir(parents=True, exist_ok=True)

    slots = load_platform_slots(Path(args.config))
    intervals = [int(item) for item in str(args.intervals).split(",") if str(item).strip()]
    account_ids, main_account_id, headless_account_ids, sub_account_ids = resolve_account_layout(args)
    append_jsonl(
        output,
        {
            "event": "start",
            "accounts": account_ids,
            "main_account": main_account_id,
            "headless_accounts": headless_account_ids,
            "sub_accounts": sub_account_ids,
            "intervals": intervals,
            "rounds_per_interval": int(args.rounds_per_interval),
            "prepared_gate_file": str(prepared_gate),
            "status_file": str(status_path),
            "timestamp_ms": now_ms(),
        },
    )
    print_event({"event": "start", "prepared_gate_file": str(prepared_gate), "status_file": str(status_path)})

    from playwright.async_api import async_playwright

    accounts: dict[str, dict[str, Any]] = {}
    rounds: list[dict[str, Any]] = []
    async with async_playwright() as playwright:
        try:
            for account_id in account_ids:
                event_queue = JsonEventQueue(event_log)
                config = config_from_slot(account_id, slots.get(account_id, {}), args)
                item = await launch_account(playwright, config, event_queue)
                item["playwright"] = playwright
                start_state_tasks(item)
                accounts[account_id] = item
            await write_status(accounts, status_path, output)
            print_event({"event": "headed_accounts_launched", "accounts": list(account_ids)})

            if not await wait_for_gate(
                prepared_gate,
                output,
                "manual_prepared",
                int(args.wait_prepared_timeout_seconds),
                accounts=accounts,
                headless_account_ids=headless_account_ids,
                launch_capture_interval_seconds=float(args.launch_capture_interval_seconds),
            ):
                append_jsonl(output, {"event": "stopped", "reason": "manual_prepared_timeout", "timestamp_ms": now_ms()})
                return 3
            await capture_headless_launch_bundles(accounts, output, headless_account_ids=headless_account_ids)

            if not await wait_account_game_ready(
                accounts,
                status_path,
                output,
                main_account_id,
                room_index=int(args.room_index),
                timeout_seconds=int(args.wait_main_room_timeout_seconds),
            ):
                statuses = await write_status(accounts, status_path, output)
                reason = f"{main_account_id}_not_in_room"
                print_event({"event": "abort", "reason": reason})
                append_jsonl(output, {"event": "abort", "reason": reason, "statuses": statuses, "timestamp_ms": now_ms()})
                return 4

            if not await handoff_and_enter_room(accounts, args, output, headless_account_ids=headless_account_ids):
                append_jsonl(output, {"event": "abort", "reason": "headless_handoff_or_enter_failed", "timestamp_ms": now_ms()})
                print_event({"event": "abort", "reason": "headless_handoff_or_enter_failed"})
                return 5

            if not await wait_all_game_ready(
                accounts,
                status_path,
                output,
                room_index=int(args.room_index),
                timeout_seconds=int(args.wait_room_timeout_seconds),
            ):
                append_jsonl(output, {"event": "abort", "reason": "not_all_accounts_in_room", "timestamp_ms": now_ms()})
                print_event({"event": "abort", "reason": "not_all_accounts_in_room"})
                return 6

            last_round = ""
            round_index = 0
            for delay_ms in intervals:
                for _ in range(int(args.rounds_per_interval)):
                    round_index += 1
                    ready_round = await wait_betting_round(
                        accounts,
                        status_path,
                        output,
                        last_round=last_round,
                        min_countdown=int(args.min_countdown),
                        room_index=int(args.room_index),
                        timeout_seconds=int(args.round_timeout_seconds),
                        allow_countdown_window=bool(args.allow_countdown_window),
                        coordinator_status_refresh_ms=int(args.coordinator_status_refresh_ms),
                        coordinator_poll_ms=int(args.coordinator_poll_ms),
                    )
                    if ready_round is None:
                        append_jsonl(output, {"event": "round_wait_timeout", "delay_ms": delay_ms, "timestamp_ms": now_ms()})
                        continue
                    round_id, _statuses = ready_round
                    last_round = round_id
                    legs = build_plan(round_index, main_account_id, sub_account_ids)
                    append_jsonl(
                        output,
                        {"event": "round_plan", "round_id": round_id, "delay_ms": delay_ms, "legs": legs, "timestamp_ms": now_ms()},
                    )
                    result = await execute_round(
                        accounts,
                        legs,
                        round_id,
                        _statuses,
                        delay_ms,
                        int(args.confirm_ms),
                        output,
                        room_index=int(args.room_index),
                        allow_countdown_window=bool(args.allow_countdown_window),
                    )
                    rounds.append(result)

            summary = summarize(rounds)
            append_jsonl(output, summary)
            summary_path = output.with_suffix(".summary.json")
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            print_event({"event": "summary", "summary_path": str(summary_path), "by_delay": summary["by_delay"]})
            return 0
        finally:
            if bool(args.close_on_exit):
                await close_accounts(accounts)


def build_parser() -> argparse.ArgumentParser:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = PROJECT_ROOT / "dist" / "BetDesktop" / "live_logs"
    parser = argparse.ArgumentParser(description="Manual-gated live interval acceptance probe.")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "dist" / "BetDesktop" / "bet_desktop" / "artifacts" / "platform_proxy_profiles.json"))
    parser.add_argument("--profile-root", default=str(PROJECT_ROOT / "dist" / "BetDesktop" / "bet_desktop" / "artifacts" / "profiles"))
    parser.add_argument("--output", default=str(log_dir / f"live_interval_acceptance_{stamp}.jsonl"))
    parser.add_argument("--event-log", default=str(log_dir / f"live_interval_acceptance_events_{stamp}.jsonl"))
    parser.add_argument("--status-file", default=str(log_dir / f"live_interval_acceptance_status_{stamp}.txt"))
    parser.add_argument("--prepared-gate-file", default=str(log_dir / f"live_interval_acceptance_prepared_{stamp}.go"))
    parser.add_argument("--accounts", default=",".join(DEFAULT_ACCOUNT_IDS))
    parser.add_argument("--main-account", default=DEFAULT_MAIN_ACCOUNT_ID)
    parser.add_argument("--headless-accounts", default="")
    parser.add_argument("--room-index", type=int, default=1)
    parser.add_argument("--intervals", default="200,250,300,400")
    parser.add_argument("--rounds-per-interval", type=int, default=2)
    parser.add_argument("--confirm-ms", type=int, default=1200)
    parser.add_argument("--min-countdown", type=int, default=10)
    parser.add_argument("--allow-countdown-window", action="store_true")
    parser.add_argument("--coordinator-status-refresh-ms", type=int, default=1000)
    parser.add_argument("--coordinator-poll-ms", type=int, default=200)
    parser.add_argument("--round-timeout-seconds", type=int, default=240)
    parser.add_argument("--wait-prepared-timeout-seconds", type=int, default=7200)
    parser.add_argument("--wait-main-room-timeout-seconds", type=int, default=180)
    parser.add_argument("--wait-room-timeout-seconds", type=int, default=180)
    parser.add_argument("--handoff-attempts", type=int, default=3)
    parser.add_argument("--handoff-retry-delay-seconds", type=float, default=5.0)
    parser.add_argument("--force-refresh-launch-on-retry", action="store_true")
    parser.add_argument("--launch-capture-interval-seconds", type=float, default=10.0)
    parser.add_argument("--login-fill-timeout-seconds", type=int, default=12)
    parser.add_argument("--no-auto-fill-login", action="store_true")
    parser.add_argument("--browser-channel", default="chrome")
    parser.add_argument("--close-on-exit", action="store_true")
    return parser


def main() -> int:
    return asyncio.run(run(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
