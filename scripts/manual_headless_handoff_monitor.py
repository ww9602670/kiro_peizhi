from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bet_desktop.browser.game_launch_url import (  # noqa: E402
    capture_local_storage_for_candidate,
    enter_baccarat_hall_room,
    extract_game_launch_url_from_page,
    install_local_storage_init_script,
    read_baccarat_load_state,
    wait_for_baccarat_ready,
)
from bet_desktop.browser.live_runtime_state import read_live_runtime_snapshot  # noqa: E402


BASE_SIZE = (960, 620)
HALL_ROOM_POINTS = {
    1: (858, 268),
    2: (858, 360),
    3: (858, 451),
    4: (858, 542),
}
HALL_ENTRY_OFFSETS = (
    (0, 0),
    (0, 24),
    (0, 36),
    (0, 48),
    (-18, 24),
    (-36, 24),
    (18, 24),
    (36, 24),
)


@dataclass
class SlotConfig:
    instance_id: str
    login_url: str
    proxy: dict[str, str]


@dataclass
class RuntimeAccount:
    instance_id: str
    slot: SlotConfig
    context: Any
    page: Any
    headed: bool
    browser: Any | None = None


def now_ms() -> int:
    return int(time.time() * 1000)


def print_event(event: dict[str, Any]) -> None:
    print(json.dumps(event, ensure_ascii=False, sort_keys=True), flush=True)


def append_jsonl(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def normalize_url(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if re.match(r"^[a-z][a-z0-9+.-]*:", text, flags=re.IGNORECASE):
        return text
    return f"https://{text}"


def safe_url_summary(value: str) -> dict[str, Any]:
    text = str(value or "").strip()
    if not text:
        return {"host": "", "path": "", "query_keys": []}
    try:
        parsed = urlparse(text)
    except Exception:
        return {"host": "", "path": "", "query_keys": []}
    keys = []
    for part in parsed.query.split("&") if parsed.query else []:
        key = part.split("=", 1)[0].strip()
        if key:
            keys.append(key[:80])
    return {"host": parsed.netloc, "path": parsed.path[:120], "query_keys": keys[:20]}


def parse_ids(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in str(value or "").split(",") if item.strip())


def load_slots(path: Path, account_ids: tuple[str, ...]) -> dict[str, SlotConfig]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    slots = raw.get("platform_slots") if isinstance(raw, dict) else None
    if not isinstance(slots, dict):
        raise ValueError("platform profile does not contain platform_slots")
    result: dict[str, SlotConfig] = {}
    for instance_id in account_ids:
        item = slots.get(instance_id)
        if not isinstance(item, dict):
            raise ValueError(f"missing platform slot for {instance_id}")
        result[instance_id] = SlotConfig(
            instance_id=instance_id,
            login_url=normalize_url(str(item.get("login_url") or "")),
            proxy=proxy_from_slot(item),
        )
    return result


def proxy_from_slot(slot: dict[str, Any]) -> dict[str, str]:
    host = str(slot.get("proxy_host") or "").strip()
    port = str(slot.get("proxy_port") or "").strip()
    if not host:
        return {}
    server = host if re.match(r"^[a-z]+://", host, flags=re.IGNORECASE) else f"http://{host}"
    if port and ":" not in server.rsplit("/", 1)[-1]:
        server = f"{server}:{port}"
    proxy = {"server": server}
    username = str(slot.get("proxy_username") or "").strip()
    password = str(slot.get("proxy_password") or "").strip()
    if username:
        proxy["username"] = username
        proxy["password"] = password
    return proxy


async def launch_headed_account(playwright: Any, slot: SlotConfig, args: argparse.Namespace) -> RuntimeAccount:
    width = int(args.width)
    height = int(args.height)
    profile_dir = Path(args.profile_root) / slot.instance_id
    profile_dir.mkdir(parents=True, exist_ok=True)
    options: dict[str, Any] = {
        "headless": False,
        "viewport": {"width": width, "height": height},
        "device_scale_factor": 1,
        "ignore_https_errors": True,
        "args": [f"--window-size={width},{height}", "--force-device-scale-factor=1"],
    }
    if slot.proxy:
        options["proxy"] = slot.proxy
    if args.browser_channel:
        options["channel"] = args.browser_channel
    try:
        context = await playwright.chromium.launch_persistent_context(str(profile_dir), **options)
    except Exception:
        if not args.browser_channel:
            raise
        options.pop("channel", None)
        context = await playwright.chromium.launch_persistent_context(str(profile_dir), **options)
    page = context.pages[0] if context.pages else await context.new_page()
    await page.set_viewport_size({"width": width, "height": height})
    if slot.login_url:
        try:
            await page.goto(slot.login_url, wait_until="domcontentloaded", timeout=int(args.goto_timeout_ms))
        except Exception:
            pass
    return RuntimeAccount(slot.instance_id, slot, context, page, headed=True)


async def wait_for_gate(args: argparse.Namespace, output: Path) -> bool:
    gate = Path(args.gate_file)
    deadline = time.time() + int(args.wait_gate_timeout_seconds)
    next_emit = 0.0
    while time.time() < deadline:
        if gate.exists():
            append_jsonl(output, {"event": "manual_gate_detected", "gate_file": str(gate), "timestamp_ms": now_ms()})
            print_event({"event": "manual_gate_detected"})
            return True
        now = time.time()
        if now >= next_emit:
            append_jsonl(output, {"event": "waiting_manual_gate", "gate_file": str(gate), "timestamp_ms": now_ms()})
            print_event({"event": "waiting_manual_gate", "gate_file": str(gate)})
            next_emit = now + 15.0
        await asyncio.sleep(1.0)
    return False


async def handoff_to_headless(
    playwright: Any,
    account: RuntimeAccount,
    args: argparse.Namespace,
    output: Path,
) -> RuntimeAccount:
    started = now_ms()
    candidate = await wait_for_launch_candidate(account.page, int(args.capture_timeout_seconds), int(args.poll_ms))
    if candidate is None:
        append_jsonl(
            output,
            {
                "event": "handoff_failed",
                "instance_id": account.instance_id,
                "reason": "launch_candidate_missing",
                "page": safe_url_summary(getattr(account.page, "url", "")),
                "timestamp_ms": now_ms(),
            },
        )
        raise RuntimeError(f"{account.instance_id}: launch candidate missing")

    storage_state = await account.context.storage_state()
    local_storage_items = await capture_local_storage_for_candidate(account.page, candidate)
    user_agent = ""
    try:
        user_agent = str(await account.page.evaluate("() => navigator.userAgent") or "")
    except Exception:
        pass

    width = int(args.width)
    height = int(args.height)
    launch_options: dict[str, Any] = {
        "headless": True,
        "args": [f"--window-size={width},{height}", "--force-device-scale-factor=1"],
    }
    if account.slot.proxy:
        launch_options["proxy"] = account.slot.proxy
    if args.browser_channel:
        launch_options["channel"] = args.browser_channel
    try:
        browser = await playwright.chromium.launch(**launch_options)
    except Exception:
        if not args.browser_channel:
            raise
        launch_options.pop("channel", None)
        browser = await playwright.chromium.launch(**launch_options)

    context_options: dict[str, Any] = {
        "viewport": {"width": width, "height": height},
        "screen": {"width": width, "height": height},
        "device_scale_factor": 1,
        "ignore_https_errors": True,
        "storage_state": storage_state,
    }
    if user_agent:
        context_options["user_agent"] = user_agent
    context = await browser.new_context(**context_options)
    await install_local_storage_init_script(context, local_storage_items)
    page = await context.new_page()
    await page.set_viewport_size({"width": width, "height": height})
    await page.goto(candidate.url, wait_until="domcontentloaded", timeout=int(args.goto_timeout_ms))

    room_result = await enter_room_with_offsets(page, int(args.room_index), args)
    append_jsonl(
        output,
        {
            "event": "headless_room_entry",
            "instance_id": account.instance_id,
            "room_index": int(args.room_index),
            "result": room_result,
            "elapsed_ms": now_ms() - started,
            "timestamp_ms": now_ms(),
        },
    )
    print_event(
        {
            "event": "headless_room_entry",
            "instance_id": account.instance_id,
            "game_ready": bool(room_result.get("game_ready")),
            "method": room_result.get("method", ""),
            "elapsed_ms": now_ms() - started,
        }
    )

    await account.context.close()
    return RuntimeAccount(account.instance_id, account.slot, context, page, headed=False, browser=browser)


async def wait_for_launch_candidate(page: Any, timeout_seconds: int, poll_ms: int) -> Any | None:
    deadline = time.time() + max(1, timeout_seconds)
    while time.time() < deadline:
        try:
            candidate = await extract_game_launch_url_from_page(page)
            if candidate is not None:
                return candidate
        except Exception:
            pass
        await page.wait_for_timeout(max(100, poll_ms))
    return None


async def enter_room_with_offsets(page: Any, room_index: int, args: argparse.Namespace) -> dict[str, Any]:
    try:
        clicked = await enter_baccarat_hall_room(page, room_index)
        if isinstance(clicked, dict) and clicked.get("ok", True):
            state = await wait_for_baccarat_ready(
                page,
                timeout_ms=int(args.room_entry_confirm_ms),
                poll_ms=int(args.poll_ms),
            )
            ready = load_state_summary(state)
            if ready.get("game_ready"):
                return {"game_ready": True, "method": "detected_hall", "ready": ready}
    except Exception:
        pass

    for attempt, (dx, dy) in enumerate(HALL_ENTRY_OFFSETS[: int(args.fixed_click_attempts)], start=1):
        x, y = fixed_hall_room_point(room_index, int(args.width), int(args.height), dx=dx, dy=dy)
        await page.mouse.click(x, y)
        state = await wait_for_baccarat_ready(
            page,
            timeout_ms=int(args.room_entry_confirm_ms),
            poll_ms=int(args.poll_ms),
        )
        ready = load_state_summary(state)
        if ready.get("game_ready"):
            return {
                "game_ready": True,
                "method": "fixed_hall_coordinate",
                "attempt": attempt,
                "dx": dx,
                "dy": dy,
                "x": round(float(x), 2),
                "y": round(float(y), 2),
                "ready": ready,
            }
    return {"game_ready": False, "method": "fixed_hall_coordinate", "attempts": int(args.fixed_click_attempts)}


def fixed_hall_room_point(room_index: int, width: int, height: int, *, dx: int = 0, dy: int = 0) -> tuple[float, float]:
    base_x, base_y = HALL_ROOM_POINTS.get(int(room_index), HALL_ROOM_POINTS[1])
    return (
        float(base_x + dx) * (float(width) / BASE_SIZE[0]),
        float(base_y + dy) * (float(height) / BASE_SIZE[1]),
    )


def load_state_summary(state: Any) -> dict[str, Any]:
    if state is None:
        return {}
    try:
        return state.safe_summary()
    except Exception:
        return {
            "ready": bool(getattr(state, "ready", False)),
            "hall_ready": bool(getattr(state, "hall_ready", False)),
            "game_ready": bool(getattr(state, "game_ready", False)),
            "scene_name": str(getattr(state, "scene_name", "") or ""),
        }


async def monitor(accounts: dict[str, RuntimeAccount], ids: tuple[str, ...], args: argparse.Namespace, output: Path) -> None:
    status_file = Path(args.status_file)
    while True:
        lines: list[str] = []
        for instance_id in ids:
            account = accounts.get(instance_id)
            if account is None:
                continue
            status = await read_account_status(account)
            append_jsonl(output, {"event": "monitor_status", **status, "timestamp_ms": now_ms()})
            lines.append(format_status(status))
        text = "\n".join(lines) + ("\n" if lines else "")
        status_file.write_text(text, encoding="utf-8")
        print_event({"event": "monitor_tick", "status": lines})
        await asyncio.sleep(max(0.5, int(args.monitor_interval_ms) / 1000.0))


async def read_account_status(account: RuntimeAccount) -> dict[str, Any]:
    load_summary: dict[str, Any] = {}
    runtime_summary: dict[str, Any] = {}
    try:
        load_summary = load_state_summary(await read_baccarat_load_state(account.page))
    except Exception as exc:
        load_summary = {"error": type(exc).__name__}
    try:
        snapshot = await read_live_runtime_snapshot(account.page, instance_id=account.instance_id, include_geometry=False)
        frame = getattr(snapshot, "frame", None) if snapshot is not None else None
        runtime_summary = {
            "game_no": str(getattr(snapshot, "game_no", "") or "")[:80] if snapshot is not None else "",
            "countdown_seconds": getattr(snapshot, "countdown_seconds", None) if snapshot is not None else None,
            "betting_open": bool(getattr(snapshot, "betting_open", False)) if snapshot is not None else False,
            "phase_key": str(getattr(snapshot, "phase_key", "") or "")[:80] if snapshot is not None else "",
            "balance_cents": getattr(frame, "balance_cents", None) if frame is not None else None,
            "pending_cents": getattr(frame, "pending_chip_cents", None) if frame is not None else None,
        }
    except Exception as exc:
        runtime_summary = {"error": type(exc).__name__}
    return {
        "instance_id": account.instance_id,
        "mode": "headed" if account.headed else "headless",
        "load": load_summary,
        "runtime": runtime_summary,
    }


def format_status(status: dict[str, Any]) -> str:
    load = status.get("load", {}) if isinstance(status.get("load"), dict) else {}
    runtime = status.get("runtime", {}) if isinstance(status.get("runtime"), dict) else {}
    return (
        f"{status.get('instance_id')} {status.get('mode')} "
        f"game={bool(load.get('game_ready'))} hall={bool(load.get('hall_ready'))} "
        f"round={runtime.get('game_no') or '-'} cd={runtime.get('countdown_seconds')} "
        f"open={bool(runtime.get('betting_open'))} pending={runtime.get('pending_cents')}"
    )


async def run(args: argparse.Namespace) -> int:
    output = Path(args.output)
    gate = Path(args.gate_file)
    status_file = Path(args.status_file)
    if gate.exists():
        gate.unlink()
    output.parent.mkdir(parents=True, exist_ok=True)
    status_file.parent.mkdir(parents=True, exist_ok=True)

    account_ids = parse_ids(args.accounts)
    handoff_ids = parse_ids(args.handoff_accounts)
    slots = load_slots(Path(args.config), account_ids)
    append_jsonl(
        output,
        {
            "event": "start",
            "accounts": account_ids,
            "handoff_accounts": handoff_ids,
            "room_index": int(args.room_index),
            "gate_file": str(gate),
            "status_file": str(status_file),
            "timestamp_ms": now_ms(),
        },
    )
    print_event({"event": "start", "gate_file": str(gate), "status_file": str(status_file)})

    from playwright.async_api import async_playwright

    accounts: dict[str, RuntimeAccount] = {}
    async with async_playwright() as playwright:
        launched = await asyncio.gather(*(launch_headed_account(playwright, slots[item], args) for item in account_ids))
        accounts = {account.instance_id: account for account in launched}
        append_jsonl(output, {"event": "headed_accounts_launched", "accounts": account_ids, "timestamp_ms": now_ms()})
        print_event({"event": "headed_accounts_launched", "accounts": account_ids})

        if not await wait_for_gate(args, output):
            append_jsonl(output, {"event": "stopped", "reason": "manual_gate_timeout", "timestamp_ms": now_ms()})
            return 3

        for instance_id in handoff_ids:
            accounts[instance_id] = await handoff_to_headless(playwright, accounts[instance_id], args, output)

        append_jsonl(output, {"event": "monitor_started", "accounts": handoff_ids, "timestamp_ms": now_ms()})
        print_event({"event": "monitor_started", "accounts": handoff_ids})
        await monitor(accounts, handoff_ids, args, output)
    return 0


def build_parser() -> argparse.ArgumentParser:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = PROJECT_ROOT / "dist" / "BetDesktop" / "live_logs"
    parser = argparse.ArgumentParser(description="Manual-gated a3/a4 headless handoff monitor.")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "dist" / "BetDesktop" / "bet_desktop" / "artifacts" / "platform_proxy_profiles.json"))
    parser.add_argument("--profile-root", default=str(PROJECT_ROOT / "dist" / "BetDesktop" / "bet_desktop" / "artifacts" / "profiles"))
    parser.add_argument("--output", default=str(log_dir / f"manual_handoff_monitor_{stamp}.jsonl"))
    parser.add_argument("--gate-file", default=str(log_dir / f"manual_handoff_gate_{stamp}.go"))
    parser.add_argument("--status-file", default=str(log_dir / f"manual_handoff_status_{stamp}.txt"))
    parser.add_argument("--accounts", default="a2,a3,a4")
    parser.add_argument("--handoff-accounts", default="a3,a4")
    parser.add_argument("--room-index", type=int, default=1)
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=620)
    parser.add_argument("--browser-channel", default="chrome")
    parser.add_argument("--poll-ms", type=int, default=500)
    parser.add_argument("--monitor-interval-ms", type=int, default=1500)
    parser.add_argument("--wait-gate-timeout-seconds", type=int, default=1800)
    parser.add_argument("--capture-timeout-seconds", type=int, default=90)
    parser.add_argument("--fixed-click-attempts", type=int, default=8)
    parser.add_argument("--room-entry-confirm-ms", type=int, default=3500)
    parser.add_argument("--goto-timeout-ms", type=int, default=60000)
    return parser


def main() -> int:
    return asyncio.run(run(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
