"""Bridge adapter for lightweight UI to legacy cluster worker runtime."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from bet_desktop.backend.cluster_process_worker import ClusterProcessController, ClusterWorkerConfig
from bet_desktop.browser.session_manager import BrowserInstanceConfig
from bet_desktop.ui.lightweight_browser_adapter import BrowserControlAdapter
from bet_desktop.ui.lightweight_models import PlatformSlot


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE_ROOT = PROJECT_ROOT / "bet_desktop" / "artifacts" / "profiles"


def _safe_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed


def _runtime_shadow_interval_ms() -> int:
    return max(1000, _safe_int(os.environ.get("BET_DESKTOP_RUNTIME_SHADOW_INTERVAL_MS", "1000"), 1000))


def platform_slot_to_cluster_config(
    slot: PlatformSlot,
    *,
    runtime_pipeline: str = "legacy",
    state_poll_interval_ms: int = 1000,
    enable_frontend_probe: bool = False,
    enable_canvas_probe: bool = False,
    enable_runtime_scan: bool = False,
) -> ClusterWorkerConfig:
    """Convert lightweight platform data to cluster worker config."""
    return ClusterWorkerConfig(
        instance_id=slot.account_id,
        login_url=slot.login_url,
        target_url="",
        username=slot.account_username,
        password=slot.account_password,
        auto_fill_login=False,
        headless=False,
        proxy={
            "host": slot.proxy_host,
            "port": slot.proxy_port,
            "username": slot.proxy_username,
            "password": slot.proxy_password,
        },
        viewport_width=960,
        viewport_height=620,
        user_data_dir=str(DEFAULT_PROFILE_ROOT / slot.account_id),
        browser_channel="chrome",
        runtime_pipeline=runtime_pipeline,
        runtime_shadow_interval_ms=_runtime_shadow_interval_ms(),
        state_poll_interval_ms=state_poll_interval_ms,
        enable_frontend_probe=enable_frontend_probe,
        enable_canvas_probe=enable_canvas_probe,
        enable_runtime_scan=enable_runtime_scan,
    )


def platform_slot_to_browser_instance(slot: PlatformSlot) -> BrowserInstanceConfig:
    from bet_desktop.browser.session_manager import AccountConfig, ProxyConfig

    return BrowserInstanceConfig(
        instance_id=slot.account_id,
        login_url=slot.login_url,
        target_url=slot.target_url,
        proxy=ProxyConfig(
            host=slot.proxy_host,
            port=slot.proxy_port,
            username=slot.proxy_username,
            password=slot.proxy_password,
        ),
        account=AccountConfig(
            username=slot.account_username,
            password=slot.account_password,
        ),
        headless=False,
    )


def _normalize_events(raw_events: Iterable[Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for event in raw_events:
        if event is None:
            continue
        if isinstance(event, dict):
            payload = event.get("payload", {})
            if not isinstance(payload, dict):
                payload = {}
            events.append(
                {
                    "event_type": event.get("event_type"),
                    "instance_id": event.get("instance_id"),
                    "payload": payload,
                    "timestamp_ms": _safe_int(event.get("timestamp_ms"), 0),
                },
            )
            continue
        event_type = getattr(event, "event_type", None)
        payload = getattr(event, "payload", {}) or {}
        if not isinstance(payload, dict):
            payload = {}
        events.append(
            {
                "event_type": event_type,
                "instance_id": getattr(event, "instance_id", ""),
                "payload": payload,
                "timestamp_ms": _safe_int(getattr(event, "timestamp_ms", 0), 0),
            },
        )
    return events


class LightweightClusterAdapter(BrowserControlAdapter):
    """Adapter that translates lightweight actions into cluster worker commands."""

    def __init__(
        self,
        *,
        state_poll_interval_ms: int = 1000,
        runtime_pipeline: str = "legacy",
        enable_frontend_probe: bool = False,
        enable_canvas_probe: bool = False,
        enable_runtime_scan: bool = False,
        max_log_entries: int = 200,
        on_log: Any = None,
    ) -> None:
        super().__init__(max_log_entries=max_log_entries, on_log=on_log)
        self.state_poll_interval_ms = max(250, state_poll_interval_ms)
        self.runtime_pipeline = runtime_pipeline
        self.enable_frontend_probe = enable_frontend_probe
        self.enable_canvas_probe = enable_canvas_probe
        self.enable_runtime_scan = enable_runtime_scan
        self._platform_slots: dict[str, PlatformSlot] = {}
        self._controller: ClusterProcessController | None = None

    def refresh_runtime_environment(self, platform_slots: Iterable[PlatformSlot]) -> None:
        self._platform_slots = {slot.account_id: slot for slot in platform_slots}

    def _to_cluster_config(self, slot: PlatformSlot) -> ClusterWorkerConfig:
        return platform_slot_to_cluster_config(
            slot,
            runtime_pipeline=self.runtime_pipeline,
            state_poll_interval_ms=self.state_poll_interval_ms,
            enable_frontend_probe=self.enable_frontend_probe,
            enable_canvas_probe=self.enable_canvas_probe,
            enable_runtime_scan=self.enable_runtime_scan,
        )

    @property
    def _slot_ids(self) -> list[str]:
        return sorted(self._platform_slots.keys())

    def _select_slots(self, account_ids: list[str] | None) -> list[PlatformSlot]:
        if not account_ids:
            return [self._platform_slots[account_id] for account_id in self._slot_ids if account_id in self._platform_slots]
        return [self._platform_slots[account_id] for account_id in account_ids if account_id in self._platform_slots]

    def _ensure_controller(self, account_ids: list[str] | None = None) -> None:
        if self._controller is not None:
            return
        slots = self._select_slots(account_ids)
        if not slots:
            return
        configs = [self._to_cluster_config(slot) for slot in slots]
        self._controller = ClusterProcessController(configs)
        self._controller.start()

    def _send_command(self, account_id: str, command: dict[str, Any]) -> None:
        if not self._controller:
            return
        self._controller.send_command(account_id, command)

    def _start_instances(self, account_ids: list[str] | None = None, *, auto_fill_login: bool = False) -> None:
        slots = self._select_slots(account_ids)
        if not slots:
            return
        configs: list[ClusterWorkerConfig] = []
        for slot in slots:
            cfg = self._to_cluster_config(slot)
            if auto_fill_login:
                cfg = ClusterWorkerConfig(
                    **{
                        **cfg.__dict__,
                        "auto_fill_login": True,
                    },
                )
            configs.append(cfg)
        if self._controller is None:
            self._controller = ClusterProcessController(configs)
            self._controller.start()
            return
        self._controller.start_configs(configs)

    def open_login_pages(self, account_ids: list[str] | None = None) -> None:
        slots = self._select_slots(account_ids)
        if not slots:
            return
        self._start_instances([slot.account_id for slot in slots], auto_fill_login=False)
        for slot in slots:
            if not slot.login_url:
                continue
            self._send_command(slot.account_id, {"command": "navigate", "url": slot.login_url})

    def run_command(self, command: list[str] | tuple[str, ...]) -> tuple[int, str, str]:
        action = str(command[0]) if command else ""
        self._append_log(f"cluster command: {action}")
        return 0, "", ""

    def start_accounts(self, account_ids: list[str]) -> tuple[int, str, str]:
        self._start_instances(account_ids, auto_fill_login=False)
        self._append_log(f"start_accounts accounts={','.join(account_ids)}")
        return 0, "ok", ""

    def fill_login(self, account_ids: list[str]) -> tuple[int, str, str]:
        slots = self._select_slots(account_ids)
        if not slots:
            return 0, "noop", ""
        if self._controller is None:
            self.open_login_pages([slot.account_id for slot in slots])
        for slot in slots:
            self._send_command(
                slot.account_id,
                {
                    "command": "fill_login",
                    "username": slot.account_username,
                    "password": slot.account_password,
                },
            )
            self._append_log(f"fill_login accounts={slot.account_id}")
        return 0, "ok", ""

    def handoff_to_headless(self, account_ids: list[str]) -> tuple[int, str, str]:
        for slot in self._select_slots(account_ids):
            self._send_command(slot.account_id, {"command": "handoff_to_headless"})
            self._append_log(f"handoff_to_headless accounts={slot.account_id}")
        return 0, "ok", ""

    def enter_room(self, account_ids: list[str], room_index: int) -> tuple[int, str, str]:
        for slot in self._select_slots(account_ids):
            self._send_command(
                slot.account_id,
                {
                    "command": "enter_room",
                    "room_index": int(room_index),
                },
            )
            self._append_log(f"enter_room accounts={slot.account_id} room_index={room_index}")
        return 0, "ok", ""

    def refresh_headless(self, account_ids: list[str]) -> tuple[int, str, str]:
        for slot in self._select_slots(account_ids):
            self._send_command(slot.account_id, {"command": "capture_game_launch_context"})
            self._append_log(f"refresh_headless accounts={slot.account_id}")
        return 0, "ok", ""

    def release_headless(self, account_ids: list[str]) -> tuple[int, str, str]:
        for slot in self._select_slots(account_ids):
            self._send_command(slot.account_id, {"command": "release_headless"})
            self._append_log(f"release_headless accounts={slot.account_id}")
        return 0, "ok", ""

    def stop_accounts(self, account_ids: list[str]) -> tuple[int, str, str]:
        if self._controller is None:
            return 0, "noop", ""
        slots = self._select_slots(account_ids)
        if not slots:
            return 0, "noop", ""
        ids = [slot.account_id for slot in slots]
        self._controller.stop_instances(ids)
        self._append_log(f"stop_accounts accounts={','.join(ids)}")
        return 0, "ok", ""

    def poll_events(self, max_items: int = 128) -> list[dict[str, Any]]:
        if self._controller is None:
            return []
        return _normalize_events(self._controller.poll_events(max_items=max_items))

    def shutdown(self) -> None:
        if self._controller is None:
            return
        self._controller.stop()
