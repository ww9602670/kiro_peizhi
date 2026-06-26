"""Configuration persistence helpers for the lightweight hedge UI."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from bet_desktop.ui.lightweight_models import (
    ACCOUNT_IDS,
    DEFAULT_MAIN_ACCOUNT,
    PlatformSlot,
    ExecutionConfig,
    resolve_main_account,
)


def _project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


PROJECT_ROOT = _project_root()
DEFAULT_CONFIG_CANDIDATES: tuple[Path, ...] = (
    PROJECT_ROOT / "dist" / "BetDesktop" / "bet_desktop" / "artifacts" / "platform_proxy_profiles.json",
    PROJECT_ROOT / "bet_desktop" / "artifacts" / "platform_proxy_profiles.json",
)


def _coerce_dict(payload: Any) -> dict[str, Any]:
    return payload if isinstance(payload, dict) else {}


def default_platform_slots() -> tuple[PlatformSlot, ...]:
    return tuple(
        PlatformSlot(
            account_id=account_id,
            display_name="",
            login_url="",
            target_url="",
            target_room="",
            proxy_bundle="",
            proxy_host="",
            proxy_port="",
            proxy_username="",
            proxy_password="",
            proxy_expire_at="",
            account_username="",
            account_password="",
            extra={},
        )
        for account_id in ACCOUNT_IDS
    )


def default_execution_config() -> ExecutionConfig:
    return ExecutionConfig(
        accounts=ACCOUNT_IDS,
        main_account=DEFAULT_MAIN_ACCOUNT,
        amount_min=80,
        amount_max=150,
        min_balance_yuan=0,
        click_interval_ms=200,
        min_countdown=10,
        confirm_ms=1200,
        room_index=1,
    )


@dataclass(frozen=True)
class LightweightConfigSnapshot:
    platform_slots: tuple[PlatformSlot, ...]
    execution_config: ExecutionConfig
    raw_payload: dict[str, Any]


def _coerce_account_map(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for key, value in payload.items():
        if isinstance(value, dict):
            result[str(key)] = value
    return result


class LightweightConfigStore:
    """Read/write lightweight UI configuration with legacy adapter support."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = self._resolve_path(path)

    @staticmethod
    def _resolve_path(path: str | Path | None) -> Path:
        if path is not None:
            return Path(path)
        for candidate in DEFAULT_CONFIG_CANDIDATES:
            if candidate.exists():
                return candidate
        return DEFAULT_CONFIG_CANDIDATES[0]

    @staticmethod
    def _read_text(path: Path) -> str:
        if not path.exists():
            return "{}"
        return path.read_text(encoding="utf-8")

    def _load_raw(self) -> dict[str, Any]:
        try:
            loaded = json.loads(self._read_text(self.path))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        payload = _coerce_dict(loaded)
        if "platforms" in payload and "platform_slots" not in payload:
            payload["platform_slots"] = {}
        payload.setdefault("platform_slots", {})
        payload.setdefault("lightweight_ui", {})
        payload.setdefault("platforms", {})
        return payload

    def load(self) -> LightweightConfigSnapshot:
        payload = self._load_raw()
        platform_slots_payload = _coerce_account_map(payload.get("platform_slots"))
        legacy_slots = _coerce_account_map(payload.get("platforms"))
        slots: list[PlatformSlot] = []
        for account_id in ACCOUNT_IDS:
            slot_data = platform_slots_payload.get(account_id, {})
            if slot_data:
                slot = PlatformSlot.from_dict(account_id=account_id, payload=slot_data)
            else:
                legacy_data = legacy_slots.get(account_id, {})
                slot = PlatformSlot.from_legacy_data(account_id=account_id, payload=legacy_data) if legacy_data else PlatformSlot(account_id=account_id)
            slots.append(slot)

        ui_payload = _coerce_dict(payload.get("lightweight_ui"))
        execution_config = ExecutionConfig.from_dict(ui_payload)
        main_account = resolve_main_account(execution_config.main_account)
        execution_config = replace(execution_config, main_account=main_account)
        return LightweightConfigSnapshot(
            platform_slots=tuple(slots),
            execution_config=execution_config,
            raw_payload=payload,
        )

    def save(self, snapshot: LightweightConfigSnapshot) -> None:
        payload = dict(snapshot.raw_payload)
        payload.setdefault("platforms", {})
        payload["platform_slots"] = {
            slot.account_id: slot.to_dict() for slot in snapshot.platform_slots
        }
        payload["lightweight_ui"] = snapshot.execution_config.to_dict()
        payload["platform_slots"] = dict(payload["platform_slots"])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)

    def read_or_default(self) -> tuple[tuple[PlatformSlot, ...], ExecutionConfig]:
        snapshot = self.load()
        if not snapshot.platform_slots:
            return default_platform_slots(), default_execution_config()
        return snapshot.platform_slots, snapshot.execution_config
