from __future__ import annotations

import argparse
import asyncio
import json
import random
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
from bet_desktop.browser.login_flow import fill_login_form_when_visible  # noqa: E402
from bet_desktop.core.decomposition import decompose_value  # noqa: E402
from bet_desktop.vision.live_game_regions import BASE_SIZE, LIVE_GAME_REGIONS  # noqa: E402


ACCOUNT_IDS = ("a2", "a3", "a4")
DENOMINATIONS = (4, 10, 20, 50, 100, 200)
MAIN_AMOUNTS = (84, 88, 94, 108, 118, 124, 128, 134, 144, 148)
SIDE_REGION = {"player": "bet_player", "banker": "bet_banker"}
SIDE_CN = {"player": "xian", "banker": "zhuang"}
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
    (-18, 0),
    (18, 0),
)


@dataclass
class SlotConfig:
    instance_id: str
    login_url: str
    target_url: str
    proxy: dict[str, str]
    username: str
    password: str


@dataclass
class AccountRuntime:
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
    keys: list[str] = []
    if parsed.query:
        for part in parsed.query.split("&"):
            key = part.split("=", 1)[0].strip()
            if key:
                keys.append(key[:80])
    return {"host": parsed.netloc, "path": parsed.path[:120], "query_keys": keys[:20]}


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
            target_url=normalize_url(str(item.get("target_url") or "")),
            proxy=proxy_from_slot(item),
            username=str(item.get("account_username") or ""),
            password=str(item.get("account_password") or ""),
        )
    return result


def parse_account_set(value: str) -> set[str]:
    return {item.strip() for item in str(value or "").split(",") if item.strip()}


def proxy_from_slot(slot: dict[str, Any]) -> dict[str, str]:
    host = str(slot.get("proxy_host") or slot.get("host") or "").strip()
    port = str(slot.get("proxy_port") or slot.get("port") or "").strip()
    if not host:
        return {}
    server = host if re.match(r"^[a-z]+://", host, flags=re.IGNORECASE) else f"http://{host}"
    if port and ":" not in server.rsplit("/", 1)[-1]:
        server = f"{server}:{port}"
    proxy = {"server": server}
    username = str(slot.get("proxy_username") or slot.get("username") or "").strip()
    password = str(slot.get("proxy_password") or slot.get("password") or "").strip()
    if username:
        proxy["username"] = username
        proxy["password"] = password
    return proxy


def region_center(name: str, viewport: dict[str, Any] | None = None) -> tuple[float, float] | None:
    data = LIVE_GAME_REGIONS.get(name)
    if not isinstance(data, dict):
        return None
    width = int((viewport or {}).get("width") or BASE_SIZE[0])
    height = int((viewport or {}).get("height") or BASE_SIZE[1])
    sx = width / BASE_SIZE[0]
    sy = height / BASE_SIZE[1]
    if isinstance(data.get("center"), tuple):
        x, y = data["center"]
    else:
        x1, y1, x2, y2 = data["bbox"]
        x = (float(x1) + float(x2)) / 2.0
        y = (float(y1) + float(y2)) / 2.0
    return float(x) * sx, float(y) * sy


def finance_from_snapshot(snapshot: Any) -> dict[str, int | None]:
    frame = getattr(snapshot, "frame", None) if snapshot is not None else None
    if frame is None:
        return {"balance_cents": None, "pending_cents": None}
    return {
        "balance_cents": as_int(getattr(frame, "balance_cents", None)),
        "pending_cents": as_int(getattr(frame, "pending_chip_cents", None)),
    }


def as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def amount_from_observed_delta(delta: int, planned_amount: int) -> float:
    if planned_amount > 0 and delta >= planned_amount * 10:
        return round(delta / 100.0, 2)
    return float(delta)


def compute_settlement(
    pre_finance: dict[str, int | None],
    post_finance: dict[str, int | None],
    planned_amount: int,
) -> dict[str, Any]:
    pre_balance = pre_finance.get("balance_cents")
    pre_pending = pre_finance.get("pending_cents")
    post_balance = post_finance.get("balance_cents")
    post_pending = post_finance.get("pending_cents")

    actual_by_balance: float | None = None
    actual_by_pending: float | None = None
    if pre_balance is not None and post_balance is not None:
        delta = pre_balance - post_balance
        if delta >= 0:
            actual_by_balance = amount_from_observed_delta(delta, planned_amount)
    if pre_pending is not None and post_pending is not None:
        delta = post_pending - pre_pending
        if delta >= 0:
            actual_by_pending = amount_from_observed_delta(delta, planned_amount)

    candidates: list[tuple[str, float]] = []
    if actual_by_balance is not None:
        candidates.append(("balance", actual_by_balance))
    if actual_by_pending is not None:
        candidates.append(("pending", actual_by_pending))
    if candidates:
        evidence, actual_amount = max(candidates, key=lambda item: item[1])
        actual_amount = min(float(planned_amount), float(actual_amount))
        if actual_by_balance is not None and actual_by_pending is not None and actual_by_balance != actual_by_pending:
            evidence = "balance_pending"
    else:
        evidence = "unknown"
        actual_amount = 0.0

    missing = max(0.0, float(planned_amount) - actual_amount)
    status = "COMPLETE"
    if not candidates:
        status = "UNKNOWN"
    if actual_amount < planned_amount:
        status = "INCOMPLETE"
    return {
        "status": status,
        "actual_amount": int(actual_amount) if float(actual_amount).is_integer() else actual_amount,
        "missing_amount": int(missing) if float(missing).is_integer() else missing,
        "evidence": evidence,
        "actual_by_balance": actual_by_balance,
        "actual_by_pending": actual_by_pending,
    }


def build_round_plan(round_index: int) -> dict[str, Any]:
    main_amount = MAIN_AMOUNTS[(round_index - 1) % len(MAIN_AMOUNTS)]
    main_side = "banker" if round_index % 2 else "player"
    sub_side = "player" if main_side == "banker" else "banker"
    sub_a3, sub_a4 = split_amount(main_amount, round_index)
    legs = [
        build_leg("a2", "main", main_side, main_amount),
        build_leg("a3", "sub", sub_side, sub_a3),
        build_leg("a4", "sub", sub_side, sub_a4),
    ]
    return {
        "round_index": round_index,
        "main_amount": main_amount,
        "main_side": main_side,
        "sub_side": sub_side,
        "legs": legs,
    }


def split_amount(amount: int, round_index: int) -> tuple[int, int]:
    candidates: list[tuple[int, int, int]] = []
    for left in range(4, amount, 2):
        right = amount - left
        try:
            left_chips = decompose_value(left, DENOMINATIONS, max_steps=5)
            right_chips = decompose_value(right, DENOMINATIONS, max_steps=5)
        except Exception:
            continue
        balance_score = abs(left - right)
        step_score = abs(len(left_chips) - len(right_chips))
        candidates.append((balance_score * 10 + step_score, left, right))
    if not candidates:
        raise ValueError(f"cannot split amount {amount}")
    candidates.sort()
    _, left, right = candidates[(round_index - 1) % min(len(candidates), 4)]
    if round_index % 2:
        return left, right
    return right, left


def build_leg(account_id: str, role: str, side: str, amount: int) -> dict[str, Any]:
    chips = decompose_value(int(amount), DENOMINATIONS, max_steps=5)
    return {
        "account_id": account_id,
        "role": role,
        "side": side,
        "side_text": SIDE_CN[side],
        "amount": int(amount),
        "chips": [int(item) for item in chips],
        "chip_steps": len(chips),
    }


async def launch_account(playwright: Any, slot: SlotConfig, args: argparse.Namespace) -> AccountRuntime:
    width = int(args.width)
    height = int(args.height)
    profile_dir = Path(args.profile_root) / slot.instance_id
    profile_dir.mkdir(parents=True, exist_ok=True)
    headed = slot.instance_id == args.main_account or slot.instance_id in parse_account_set(args.bootstrap_headed_accounts)
    options: dict[str, Any] = {
        "headless": not headed,
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
    return AccountRuntime(slot.instance_id, slot, context, page, headed)


async def close_accounts(accounts: dict[str, AccountRuntime]) -> None:
    for account in accounts.values():
        try:
            await account.context.close()
        except Exception:
            pass
        if account.browser is not None:
            try:
                await account.browser.close()
            except Exception:
                pass


async def prepare_account(
    account: AccountRuntime,
    args: argparse.Namespace,
    output_path: Path,
    *,
    stop_at_hall: bool = False,
) -> dict[str, Any]:
    started = now_ms()
    setup_timeout = int(args.setup_timeout_seconds)
    page = await pick_best_page(account)
    account.page = page
    ready = await wait_until_game_or_hall(page, timeout_seconds=8, poll_ms=int(args.poll_ms))
    if ready.get("game_ready"):
        return record_setup(account, started, "game_ready_existing", ready, output_path)
    if stop_at_hall and ready.get("hall_ready"):
        return record_setup(account, started, "hall_ready_existing", ready, output_path)
    if stop_at_hall and await manual_hall_ready(page, args):
        return record_setup(account, started, "manual_hall_ready_existing", ready, output_path)

    nav_url = account.slot.target_url or account.slot.login_url
    if nav_url:
        try:
            await page.goto(nav_url, wait_until="domcontentloaded", timeout=int(args.goto_timeout_ms))
        except Exception as exc:
            append_jsonl(
                output_path,
                {
                    "event": "setup_navigation_error",
                    "instance_id": account.instance_id,
                    "error": type(exc).__name__,
                    "url": safe_url_summary(nav_url),
                    "timestamp_ms": now_ms(),
                },
            )
    await maybe_fill_login(account, page, output_path)
    if bool(args.manual_hall_confirm):
        if stop_at_hall and await manual_hall_ready(page, args):
            return record_setup(account, started, "manual_hall_ready_after_login", ready, output_path)
        if not stop_at_hall and await manual_hall_ready(page, args) and int(args.room_index) in {1, 2, 3, 4}:
            await enter_room_with_fallback(account, page, int(args.room_index), args, output_path, event_prefix="setup_manual")
            state = await wait_for_baccarat_ready(
                page,
                timeout_ms=int(args.room_entry_timeout_seconds) * 1000,
                poll_ms=int(args.poll_ms),
            )
            ready = load_state_summary(state)
            if ready.get("game_ready"):
                return record_setup(account, started, "game_ready_after_manual_room_entry", ready, output_path)
    ready = await wait_until_game_or_hall(page, timeout_seconds=setup_timeout, poll_ms=int(args.poll_ms))
    if ready.get("game_ready"):
        return record_setup(account, started, "game_ready_after_login", ready, output_path)
    if stop_at_hall and ready.get("hall_ready"):
        return record_setup(account, started, "hall_ready_after_login", ready, output_path)
    if stop_at_hall and await manual_hall_ready(page, args):
        return record_setup(account, started, "manual_hall_ready_after_login", ready, output_path)
    if ready.get("hall_ready") and int(args.room_index) in {1, 2, 3, 4}:
        await enter_room_with_fallback(account, page, int(args.room_index), args, output_path, event_prefix="setup")
        state = await wait_for_baccarat_ready(
            page,
            timeout_ms=int(args.room_entry_timeout_seconds) * 1000,
            poll_ms=int(args.poll_ms),
        )
        ready = load_state_summary(state)
        if ready.get("game_ready"):
            return record_setup(account, started, "game_ready_after_room_entry", ready, output_path)
    if await manual_hall_ready(page, args) and int(args.room_index) in {1, 2, 3, 4}:
        await enter_room_with_fallback(account, page, int(args.room_index), args, output_path, event_prefix="setup_manual")
        state = await wait_for_baccarat_ready(
            page,
            timeout_ms=int(args.room_entry_timeout_seconds) * 1000,
            poll_ms=int(args.poll_ms),
        )
        ready = load_state_summary(state)
        if ready.get("game_ready"):
            return record_setup(account, started, "game_ready_after_manual_room_entry", ready, output_path)
    return record_setup(account, started, "not_game_ready", ready, output_path)


async def handoff_account_to_headless(
    playwright: Any,
    account: AccountRuntime,
    args: argparse.Namespace,
    output_path: Path,
) -> AccountRuntime:
    started = now_ms()
    append_jsonl(
        output_path,
        {
            "event": "handoff_start",
            "instance_id": account.instance_id,
            "timestamp_ms": now_ms(),
        },
    )
    candidate = await wait_for_launch_candidate(
        account.page,
        timeout_seconds=int(args.handoff_capture_timeout_seconds),
        poll_ms=int(args.poll_ms),
    )
    if candidate is None:
        append_jsonl(
            output_path,
            {
                "event": "handoff_failed",
                "instance_id": account.instance_id,
                "reason": "launch_candidate_missing",
                "timestamp_ms": now_ms(),
            },
        )
        raise RuntimeError(f"{account.instance_id} launch candidate missing")

    storage_state = await account.context.storage_state()
    local_storage_items = await capture_local_storage_for_candidate(account.page, candidate)
    user_agent = ""
    try:
        user_agent = str(await account.page.evaluate("() => navigator.userAgent") or "")
    except Exception:
        user_agent = ""
    append_jsonl(
        output_path,
        {
            "event": "handoff_captured",
            "instance_id": account.instance_id,
            "launch": safe_url_summary(candidate.url),
            "local_storage_items": len(local_storage_items),
            "timestamp_ms": now_ms(),
        },
    )

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
    browser = None
    headless_context = None
    try:
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
        headless_context = await browser.new_context(**context_options)
        await install_local_storage_init_script(headless_context, local_storage_items)
        page = await headless_context.new_page()
        await page.set_viewport_size({"width": width, "height": height})
        await page.goto(candidate.url, wait_until="domcontentloaded", timeout=int(args.goto_timeout_ms))
        ready = await wait_until_game_or_hall(
            page,
            timeout_seconds=int(args.handoff_ready_timeout_seconds),
            poll_ms=int(args.poll_ms),
        )
        if (
            not ready.get("game_ready")
            and int(args.room_index) in {1, 2, 3, 4}
            and (ready.get("hall_ready") or bool(args.manual_hall_confirm))
        ):
            await enter_room_with_fallback(account, page, int(args.room_index), args, output_path, event_prefix="handoff")
            state = await wait_for_baccarat_ready(
                page,
                timeout_ms=int(args.room_entry_timeout_seconds) * 1000,
                poll_ms=int(args.poll_ms),
            )
            ready = load_state_summary(state)
        event = {
            "event": "handoff_ready",
            "instance_id": account.instance_id,
            "ready": ready,
            "elapsed_ms": now_ms() - started,
            "timestamp_ms": now_ms(),
        }
        append_jsonl(output_path, event)
        print_event(
            {
                "event": "handoff_ready",
                "instance_id": account.instance_id,
                "game_ready": bool(ready.get("game_ready")),
                "hall_ready": bool(ready.get("hall_ready")),
                "elapsed_ms": event["elapsed_ms"],
            }
        )
        if not ready.get("game_ready"):
            raise RuntimeError(f"{account.instance_id} headless handoff not game ready")
        await account.context.close()
        return AccountRuntime(account.instance_id, account.slot, headless_context, page, headed=False, browser=browser)
    except Exception:
        if headless_context is not None:
            try:
                await headless_context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        raise


async def wait_for_launch_candidate(page: Any, *, timeout_seconds: int, poll_ms: int) -> Any | None:
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


async def manual_hall_ready(page: Any, args: argparse.Namespace) -> bool:
    if not bool(args.manual_hall_confirm):
        return False
    if int(args.manual_hall_wait_seconds) > 0:
        await page.wait_for_timeout(int(args.manual_hall_wait_seconds) * 1000)
    if bool(args.manual_hall_requires_launch_candidate):
        candidate = await wait_for_launch_candidate(
            page,
            timeout_seconds=int(args.handoff_capture_timeout_seconds),
            poll_ms=int(args.poll_ms),
        )
        return candidate is not None
    return True


async def enter_room_with_fallback(
    account: AccountRuntime,
    page: Any,
    room_index: int,
    args: argparse.Namespace,
    output_path: Path,
    *,
    event_prefix: str,
) -> None:
    try:
        clicked = await enter_baccarat_hall_room(page, int(room_index))
        ok = bool(clicked.get("ok", True)) if isinstance(clicked, dict) else True
        append_jsonl(
            output_path,
            {
                "event": f"{event_prefix}_room_entry_click",
                "instance_id": account.instance_id,
                "room_index": int(room_index),
                "method": "detected_hall",
                "ok": ok,
                "timestamp_ms": now_ms(),
            },
        )
        if ok:
            return
    except Exception as exc:
        append_jsonl(
            output_path,
            {
                "event": f"{event_prefix}_room_entry_error",
                "instance_id": account.instance_id,
                "room_index": int(room_index),
                "method": "detected_hall",
                "error": type(exc).__name__,
                "timestamp_ms": now_ms(),
            },
        )
    if not bool(args.allow_fixed_hall_click):
        return
    attempts = list(HALL_ENTRY_OFFSETS)[: max(1, int(args.fixed_hall_click_attempts))]
    for attempt_index, (dx, dy) in enumerate(attempts, start=1):
        x, y = fixed_hall_room_point(room_index, int(args.width), int(args.height), dx=dx, dy=dy)
        await page.mouse.click(x, y)
        state = await wait_for_baccarat_ready(
            page,
            timeout_ms=int(args.hall_click_attempt_timeout_ms),
            poll_ms=int(args.poll_ms),
        )
        ready = load_state_summary(state)
        append_jsonl(
            output_path,
            {
                "event": f"{event_prefix}_room_entry_click",
                "instance_id": account.instance_id,
                "room_index": int(room_index),
                "method": "fixed_hall_coordinate",
                "attempt": attempt_index,
                "dx": dx,
                "dy": dy,
                "x": round(float(x), 2),
                "y": round(float(y), 2),
                "ready": ready,
                "timestamp_ms": now_ms(),
            },
        )
        if ready.get("game_ready"):
            return


def fixed_hall_room_point(room_index: int, width: int, height: int, *, dx: int = 0, dy: int = 0) -> tuple[float, float]:
    base_x, base_y = HALL_ROOM_POINTS.get(int(room_index), HALL_ROOM_POINTS[1])
    return (
        float(base_x + dx) * (float(width) / BASE_SIZE[0]),
        float(base_y + dy) * (float(height) / BASE_SIZE[1]),
    )


async def pick_best_page(account: AccountRuntime) -> Any:
    pages = []
    try:
        pages = [page for page in account.context.pages if not page.is_closed()]
    except Exception:
        pages = []
    if not pages:
        return await account.context.new_page()
    best_page = pages[0]
    best_score = -1
    for page in pages:
        score = 0
        try:
            url = str(page.url or "")
        except Exception:
            url = ""
        if "gameId=" in url or "route=" in url:
            score += 20
        try:
            state = await read_baccarat_load_state(page)
            if state.game_ready:
                score += 100
            if state.hall_ready:
                score += 50
        except Exception:
            pass
        if score > best_score:
            best_score = score
            best_page = page
    return best_page


async def maybe_fill_login(account: AccountRuntime, page: Any, output_path: Path) -> None:
    if not account.slot.username or not account.slot.password:
        return
    try:
        result = await fill_login_form_when_visible(
            account.context,
            page,
            username=account.slot.username,
            password=account.slot.password,
            timeout_seconds=12,
        )
    except Exception as exc:
        append_jsonl(
            output_path,
            {
                "event": "setup_login_fill_error",
                "instance_id": account.instance_id,
                "error": type(exc).__name__,
                "timestamp_ms": now_ms(),
            },
        )
        return
    append_jsonl(
        output_path,
        {
            "event": "setup_login_fill",
            "instance_id": account.instance_id,
            "ok": bool(result.get("ok")) if isinstance(result, dict) else False,
            "stage": str(result.get("stage") or "")[:80] if isinstance(result, dict) else "",
            "timestamp_ms": now_ms(),
        },
    )


async def wait_until_game_or_hall(page: Any, *, timeout_seconds: int, poll_ms: int) -> dict[str, Any]:
    deadline = time.time() + max(1, timeout_seconds)
    last: dict[str, Any] = {}
    while time.time() < deadline:
        try:
            state = await read_baccarat_load_state(page)
            last = load_state_summary(state)
            if last.get("game_ready") or last.get("hall_ready"):
                return last
        except Exception:
            pass
        await page.wait_for_timeout(max(100, poll_ms))
    return last


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


def record_setup(
    account: AccountRuntime,
    started_ms: int,
    stage: str,
    ready: dict[str, Any],
    output_path: Path,
) -> dict[str, Any]:
    event = {
        "event": "setup_account",
        "instance_id": account.instance_id,
        "headed": account.headed,
        "stage": stage,
        "elapsed_ms": now_ms() - started_ms,
        "ready": ready,
        "page": safe_url_summary(getattr(account.page, "url", "")),
        "timestamp_ms": now_ms(),
    }
    append_jsonl(output_path, event)
    print_event(
        {
            "event": "setup_account",
            "instance_id": account.instance_id,
            "stage": stage,
            "elapsed_ms": event["elapsed_ms"],
            "game_ready": bool(ready.get("game_ready")),
            "hall_ready": bool(ready.get("hall_ready")),
        }
    )
    return event


async def read_snapshot(account: AccountRuntime) -> Any:
    return await read_live_runtime_snapshot(
        account.page,
        instance_id=account.instance_id,
        include_geometry=False,
    )


async def wait_for_betting_round(
    accounts: dict[str, AccountRuntime],
    *,
    last_game_no: str,
    min_countdown: int,
    timeout_seconds: int,
    poll_ms: int,
    output_path: Path,
) -> dict[str, Any] | None:
    deadline = time.time() + max(1, timeout_seconds)
    next_emit = 0.0
    latest: dict[str, Any] = {}
    while time.time() < deadline:
        snapshots = await asyncio.gather(
            *(read_snapshot(account) for account in accounts.values()),
            return_exceptions=True,
        )
        by_id: dict[str, Any] = {}
        for instance_id, snapshot in zip(accounts.keys(), snapshots):
            if isinstance(snapshot, Exception):
                by_id[instance_id] = None
            else:
                by_id[instance_id] = snapshot
        summary = runtime_summary(by_id)
        latest = summary
        ready = [
            item
            for item in summary.values()
            if item.get("betting_open")
            and item.get("countdown_seconds") is not None
            and int(item["countdown_seconds"]) >= min_countdown
            and item.get("game_no")
        ]
        game_nos = {str(item.get("game_no") or "") for item in ready}
        if len(ready) == len(accounts) and len(game_nos) == 1:
            game_no = next(iter(game_nos))
            if game_no and game_no != last_game_no:
                return {"game_no": game_no, "snapshots": by_id, "summary": summary}
        now = time.time()
        if now >= next_emit:
            event = {
                "event": "waiting_betting_round",
                "last_game_no": last_game_no,
                "min_countdown": min_countdown,
                "summary": summary,
                "timestamp_ms": now_ms(),
            }
            append_jsonl(output_path, event)
            print_event(
                {
                    "event": "waiting_betting_round",
                    "min_countdown": min_countdown,
                    "accounts_ready": len(ready),
                    "game_nos": sorted(list(game_nos))[:3],
                }
            )
            next_emit = now + 10.0
        await asyncio.sleep(max(100, poll_ms) / 1000.0)
    append_jsonl(
        output_path,
        {
            "event": "wait_betting_timeout",
            "last_game_no": last_game_no,
            "latest": latest,
            "timestamp_ms": now_ms(),
        },
    )
    return None


def runtime_summary(by_id: dict[str, Any]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for instance_id, snapshot in by_id.items():
        frame = getattr(snapshot, "frame", None) if snapshot is not None else None
        summary[instance_id] = {
            "game_no": str(getattr(snapshot, "game_no", "") or "")[:80] if snapshot is not None else "",
            "countdown_seconds": getattr(snapshot, "countdown_seconds", None) if snapshot is not None else None,
            "betting_open": bool(getattr(snapshot, "betting_open", False)) if snapshot is not None else False,
            "phase_key": str(getattr(snapshot, "phase_key", "") or "")[:80] if snapshot is not None else "",
            "pending_cents": as_int(getattr(frame, "pending_chip_cents", None)) if frame is not None else None,
            "balance_cents": as_int(getattr(frame, "balance_cents", None)) if frame is not None else None,
        }
    return summary


async def execute_round(
    accounts: dict[str, AccountRuntime],
    plan: dict[str, Any],
    round_context: dict[str, Any],
    args: argparse.Namespace,
    output_path: Path,
) -> dict[str, Any]:
    started = now_ms()
    leg_by_id = {leg["account_id"]: leg for leg in plan["legs"]}
    tasks = []
    for instance_id, account in accounts.items():
        pre_snapshot = round_context["snapshots"].get(instance_id)
        tasks.append(execute_leg(account, leg_by_id[instance_id], pre_snapshot, args))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    clean_results: list[dict[str, Any]] = []
    for instance_id, result in zip(accounts.keys(), results):
        if isinstance(result, Exception):
            clean_results.append(
                {
                    "instance_id": instance_id,
                    "status": "ERROR",
                    "error": type(result).__name__,
                    "message": str(result)[:220],
                }
            )
        else:
            clean_results.append(result)

    total_missing = sum(float(item.get("missing_amount") or 0) for item in clean_results)
    total_actual = sum(float(item.get("actual_amount") or 0) for item in clean_results)
    round_event = {
        "event": "round_result",
        "round_index": plan["round_index"],
        "game_no": round_context["game_no"],
        "plan": plan,
        "results": clean_results,
        "total_actual": int(total_actual) if float(total_actual).is_integer() else total_actual,
        "total_missing": int(total_missing) if float(total_missing).is_integer() else total_missing,
        "elapsed_ms": now_ms() - started,
        "timestamp_ms": now_ms(),
    }
    append_jsonl(output_path, round_event)
    print_event(
        {
            "event": "round_result",
            "round_index": plan["round_index"],
            "game_no": round_context["game_no"],
            "elapsed_ms": round_event["elapsed_ms"],
            "total_missing": round_event["total_missing"],
            "accounts": [
                {
                    "id": item.get("instance_id"),
                    "planned": item.get("planned_amount"),
                    "actual": item.get("actual_amount"),
                    "missing": item.get("missing_amount"),
                    "click_ms": item.get("click_sequence_ms"),
                    "status": item.get("status"),
                }
                for item in clean_results
            ],
        }
    )
    return round_event


async def execute_leg(account: AccountRuntime, leg: dict[str, Any], pre_snapshot: Any, args: argparse.Namespace) -> dict[str, Any]:
    side_region = SIDE_REGION[str(leg["side"])]
    viewport = {"width": int(args.width), "height": int(args.height)}
    side_point = region_center(side_region, viewport)
    if side_point is None:
        raise RuntimeError(f"missing side coordinate {side_region}")
    chip_points: dict[int, tuple[float, float]] = {}
    for chip in sorted(set(int(item) for item in leg["chips"])):
        point = region_center(f"chip_{chip}", viewport)
        if point is None:
            raise RuntimeError(f"missing chip coordinate {chip}")
        chip_points[chip] = point

    preflight = await read_snapshot(account)
    pre_countdown = getattr(preflight, "countdown_seconds", None) if preflight is not None else None
    pre_game_no = str(getattr(preflight, "game_no", "") or "") if preflight is not None else ""
    if not preflight or not getattr(preflight, "betting_open", False):
        return rejected_leg(account, leg, "preflight_not_betting_open", preflight)
    if pre_countdown is None or int(pre_countdown) < int(args.min_click_countdown):
        return rejected_leg(account, leg, "preflight_countdown_too_low", preflight)

    pre_finance = finance_from_snapshot(pre_snapshot or preflight)
    started = now_ms()
    steps: list[dict[str, Any]] = []
    chips = [int(item) for item in leg["chips"]]
    for index, chip in enumerate(chips, start=1):
        chip_x, chip_y = chip_points[chip]
        step_started = now_ms()
        await account.page.mouse.click(chip_x, chip_y)
        chip_clicked = now_ms()
        chip_to_side_delay = random.randint(int(args.delay_min_ms), int(args.delay_max_ms))
        await account.page.wait_for_timeout(chip_to_side_delay)
        await account.page.mouse.click(side_point[0], side_point[1])
        side_clicked = now_ms()
        step: dict[str, Any] = {
            "step_index": index,
            "chip": chip,
            "chip_to_side_delay_ms": chip_to_side_delay,
            "step_elapsed_ms": side_clicked - step_started,
        }
        if index < len(chips):
            next_delay = random.randint(int(args.delay_min_ms), int(args.delay_max_ms))
            step["next_delay_ms"] = next_delay
            await account.page.wait_for_timeout(next_delay)
        steps.append(step)
    click_done = now_ms()
    await account.page.wait_for_timeout(int(args.confirm_delay_ms))
    post_snapshot = await read_snapshot(account)
    post_finance = finance_from_snapshot(post_snapshot)
    settlement = compute_settlement(pre_finance, post_finance, int(leg["amount"]))
    result = {
        "instance_id": account.instance_id,
        "role": leg["role"],
        "side": leg["side"],
        "side_text": leg["side_text"],
        "planned_amount": int(leg["amount"]),
        "chips": chips,
        "status": settlement["status"],
        "actual_amount": settlement["actual_amount"],
        "missing_amount": settlement["missing_amount"],
        "evidence": settlement["evidence"],
        "click_sequence_ms": click_done - started,
        "elapsed_ms": now_ms() - started,
        "pre_countdown": pre_countdown,
        "post_countdown": getattr(post_snapshot, "countdown_seconds", None) if post_snapshot is not None else None,
        "pre_game_no": pre_game_no[:80],
        "post_game_no": str(getattr(post_snapshot, "game_no", "") or "")[:80] if post_snapshot is not None else "",
        "pre_finance": pre_finance,
        "post_finance": post_finance,
        "steps": steps,
    }
    return result


def rejected_leg(account: AccountRuntime, leg: dict[str, Any], reason: str, snapshot: Any) -> dict[str, Any]:
    return {
        "instance_id": account.instance_id,
        "role": leg["role"],
        "side": leg["side"],
        "side_text": leg["side_text"],
        "planned_amount": int(leg["amount"]),
        "chips": [int(item) for item in leg["chips"]],
        "status": "REJECTED",
        "reason": reason,
        "actual_amount": 0,
        "missing_amount": int(leg["amount"]),
        "pre_countdown": getattr(snapshot, "countdown_seconds", None) if snapshot is not None else None,
        "pre_game_no": str(getattr(snapshot, "game_no", "") or "")[:80] if snapshot is not None else "",
    }


def summarize_rounds(round_events: list[dict[str, Any]]) -> dict[str, Any]:
    account_stats: dict[str, dict[str, Any]] = {}
    for event in round_events:
        for result in event.get("results", []):
            instance_id = str(result.get("instance_id") or "")
            if not instance_id:
                continue
            stats = account_stats.setdefault(
                instance_id,
                {
                    "legs": 0,
                    "complete": 0,
                    "incomplete": 0,
                    "unknown": 0,
                    "rejected": 0,
                    "planned": 0.0,
                    "actual": 0.0,
                    "missing": 0.0,
                    "max_click_sequence_ms": 0,
                    "avg_click_sequence_ms": 0.0,
                    "_click_ms": [],
                },
            )
            stats["legs"] += 1
            status = str(result.get("status") or "")
            if status == "COMPLETE":
                stats["complete"] += 1
            elif status == "INCOMPLETE":
                stats["incomplete"] += 1
            elif status == "UNKNOWN":
                stats["unknown"] += 1
            elif status == "REJECTED":
                stats["rejected"] += 1
            stats["planned"] += float(result.get("planned_amount") or 0)
            stats["actual"] += float(result.get("actual_amount") or 0)
            stats["missing"] += float(result.get("missing_amount") or 0)
            click_ms = result.get("click_sequence_ms")
            if isinstance(click_ms, (int, float)):
                stats["_click_ms"].append(int(click_ms))
                stats["max_click_sequence_ms"] = max(int(stats["max_click_sequence_ms"]), int(click_ms))
    for stats in account_stats.values():
        values = stats.pop("_click_ms", [])
        stats["avg_click_sequence_ms"] = round(sum(values) / len(values), 1) if values else 0
        for key in ("planned", "actual", "missing"):
            value = float(stats[key])
            stats[key] = int(value) if value.is_integer() else round(value, 2)
    total_missing = sum(float(item.get("total_missing") or 0) for item in round_events)
    return {
        "rounds": len(round_events),
        "complete_rounds": sum(1 for item in round_events if float(item.get("total_missing") or 0) <= 0),
        "total_missing": int(total_missing) if float(total_missing).is_integer() else round(total_missing, 2),
        "accounts": account_stats,
    }


async def run(args: argparse.Namespace) -> int:
    if not args.live:
        print_event({"event": "refused", "reason": "missing --live flag"})
        return 2
    random.seed(int(args.seed) if args.seed is not None else None)
    account_ids = tuple(args.accounts.split(","))
    if account_ids != ACCOUNT_IDS:
        print_event({"event": "refused", "reason": "this probe is fixed to a2,a3,a4 for this test"})
        return 2

    output_path = Path(args.output)
    slots = load_slots(Path(args.config), account_ids)
    start_event = {
        "event": "probe_start",
        "accounts": list(account_ids),
        "main_account": args.main_account,
        "headless_accounts": sorted(parse_account_set(args.headless_accounts)),
        "bootstrap_headed_accounts": sorted(parse_account_set(args.bootstrap_headed_accounts)),
        "rounds": int(args.rounds),
        "amount_range": [80, 150],
        "delay_ms": [int(args.delay_min_ms), int(args.delay_max_ms)],
        "config_path": str(Path(args.config).resolve()),
        "profile_root": str(Path(args.profile_root).resolve()),
        "timestamp_ms": now_ms(),
    }
    append_jsonl(output_path, start_event)
    print_event(
        {
            "event": "probe_start",
            "accounts": list(account_ids),
            "rounds": int(args.rounds),
            "delay_ms": start_event["delay_ms"],
            "output": str(output_path.resolve()),
        }
    )

    accounts: dict[str, AccountRuntime] = {}
    round_events: list[dict[str, Any]] = []
    async with async_playwright_context() as playwright:
        try:
            headless_ids = parse_account_set(args.headless_accounts)
            launched = await asyncio.gather(*(launch_account(playwright, slots[item], args) for item in account_ids))
            accounts = {account.instance_id: account for account in launched}
            setup_results = await asyncio.gather(
                *(
                    prepare_account(
                        account,
                        args,
                        output_path,
                        stop_at_hall=account.instance_id in headless_ids,
                    )
                    for account in accounts.values()
                )
            )
            bootstrap_not_ready = []
            for item in setup_results:
                instance_id = str(item.get("instance_id") or "")
                ready = item.get("ready", {}) if isinstance(item.get("ready"), dict) else {}
                if instance_id in headless_ids:
                    if not (ready.get("hall_ready") or ready.get("game_ready")):
                        bootstrap_not_ready.append(instance_id)
                elif not ready.get("game_ready"):
                    bootstrap_not_ready.append(instance_id)
            if bootstrap_not_ready:
                append_jsonl(
                    output_path,
                    {
                        "event": "probe_abort",
                        "reason": "bootstrap_accounts_not_ready",
                        "accounts": bootstrap_not_ready,
                        "timestamp_ms": now_ms(),
                    },
                )
                print_event(
                    {
                        "event": "probe_abort",
                        "reason": "bootstrap_accounts_not_ready",
                        "accounts": bootstrap_not_ready,
                    }
                )
                return 4

            for instance_id in sorted(headless_ids):
                if instance_id not in accounts:
                    continue
                accounts[instance_id] = await handoff_account_to_headless(
                    playwright,
                    accounts[instance_id],
                    args,
                    output_path,
                )

            final_ready = await final_game_ready(accounts)
            final_not_ready = [instance_id for instance_id, ready in final_ready.items() if not ready.get("game_ready")]
            append_jsonl(
                output_path,
                {
                    "event": "final_game_ready_check",
                    "ready": final_ready,
                    "timestamp_ms": now_ms(),
                },
            )
            if final_not_ready:
                append_jsonl(
                    output_path,
                    {
                        "event": "probe_abort",
                        "reason": "accounts_not_game_ready_after_handoff",
                        "accounts": final_not_ready,
                        "timestamp_ms": now_ms(),
                    },
                )
                print_event(
                    {
                        "event": "probe_abort",
                        "reason": "accounts_not_game_ready_after_handoff",
                        "accounts": final_not_ready,
                    }
                )
                return 4

            last_game_no = ""
            for round_index in range(1, int(args.rounds) + 1):
                plan = build_round_plan(round_index)
                append_jsonl(output_path, {"event": "round_plan", "plan": plan, "timestamp_ms": now_ms()})
                print_event(
                    {
                        "event": "round_plan",
                        "round_index": round_index,
                        "main_amount": plan["main_amount"],
                        "main_side": plan["main_side"],
                        "legs": [
                            {
                                "id": leg["account_id"],
                                "amount": leg["amount"],
                                "chips": leg["chips"],
                                "side": leg["side"],
                            }
                            for leg in plan["legs"]
                        ],
                    }
                )
                round_context = await wait_for_betting_round(
                    accounts,
                    last_game_no=last_game_no,
                    min_countdown=int(args.min_round_countdown),
                    timeout_seconds=int(args.round_timeout_seconds),
                    poll_ms=int(args.poll_ms),
                    output_path=output_path,
                )
                if round_context is None:
                    break
                result = await execute_round(accounts, plan, round_context, args, output_path)
                round_events.append(result)
                last_game_no = str(round_context.get("game_no") or last_game_no)

            summary = summarize_rounds(round_events)
            summary_event = {"event": "probe_summary", **summary, "timestamp_ms": now_ms()}
            append_jsonl(output_path, summary_event)
            summary_path = output_path.with_suffix(".summary.json")
            summary_path.write_text(json.dumps(summary_event, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            print_event({"event": "probe_summary", **summary, "summary_path": str(summary_path.resolve())})
            return 0 if len(round_events) == int(args.rounds) else 5
        finally:
            await close_accounts(accounts)


async def final_game_ready(accounts: dict[str, AccountRuntime]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for instance_id, account in accounts.items():
        try:
            state = await read_baccarat_load_state(account.page)
            result[instance_id] = load_state_summary(state)
        except Exception as exc:
            result[instance_id] = {"game_ready": False, "error": type(exc).__name__}
    return result


class async_playwright_context:
    async def __aenter__(self) -> Any:
        from playwright.async_api import async_playwright

        self._manager = async_playwright()
        return await self._manager.start()

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self._manager.stop()


def build_parser() -> argparse.ArgumentParser:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    default_output = PROJECT_ROOT / "dist" / "BetDesktop" / "live_logs" / f"fast_click_probe_{stamp}.jsonl"
    parser = argparse.ArgumentParser(description="Live fast click probe for a2/a3/a4.")
    parser.add_argument("--live", action="store_true", help="Required: allow real betting clicks.")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "dist" / "BetDesktop" / "bet_desktop" / "artifacts" / "platform_proxy_profiles.json"))
    parser.add_argument("--profile-root", default=str(PROJECT_ROOT / "dist" / "BetDesktop" / "bet_desktop" / "artifacts" / "profiles"))
    parser.add_argument("--output", default=str(default_output))
    parser.add_argument("--accounts", default="a2,a3,a4")
    parser.add_argument("--main-account", default="a2")
    parser.add_argument("--headless-accounts", default="a3,a4")
    parser.add_argument("--bootstrap-headed-accounts", default="a3,a4")
    parser.add_argument("--manual-hall-confirm", action="store_true")
    parser.add_argument("--manual-hall-wait-seconds", type=int, default=0)
    parser.add_argument("--manual-hall-requires-launch-candidate", action="store_true", default=True)
    parser.add_argument("--allow-fixed-hall-click", action="store_true")
    parser.add_argument("--fixed-hall-click-attempts", type=int, default=10)
    parser.add_argument("--hall-click-attempt-timeout-ms", type=int, default=3500)
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--room-index", type=int, default=1)
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=620)
    parser.add_argument("--browser-channel", default="chrome")
    parser.add_argument("--delay-min-ms", type=int, default=200)
    parser.add_argument("--delay-max-ms", type=int, default=300)
    parser.add_argument("--confirm-delay-ms", type=int, default=1500)
    parser.add_argument("--min-round-countdown", type=int, default=10)
    parser.add_argument("--min-click-countdown", type=int, default=8)
    parser.add_argument("--poll-ms", type=int, default=250)
    parser.add_argument("--setup-timeout-seconds", type=int, default=120)
    parser.add_argument("--handoff-capture-timeout-seconds", type=int, default=60)
    parser.add_argument("--handoff-ready-timeout-seconds", type=int, default=90)
    parser.add_argument("--room-entry-timeout-seconds", type=int, default=45)
    parser.add_argument("--round-timeout-seconds", type=int, default=240)
    parser.add_argument("--goto-timeout-ms", type=int, default=60000)
    parser.add_argument("--seed", type=int, default=None)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
