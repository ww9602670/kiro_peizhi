"""Models for the lightweight hedge UI phase-1/2/3 implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

ACCOUNT_IDS = ("a1", "a2", "a3", "a4")
DEFAULT_MAIN_ACCOUNT = "a2"
ALLOWED_ACCOUNT_IDS = set(ACCOUNT_IDS)
SUPPORTED_ACCOUNT_IDS = tuple(ACCOUNT_IDS)


def normalize_account_ids(account_ids: Sequence[str]) -> tuple[str, ...]:
    """Keep configured account ids in deterministic a1-a4 order and deduplicate."""
    ordered = []
    seen = set()
    for account_id in account_ids:
        if account_id not in ALLOWED_ACCOUNT_IDS or account_id in seen:
            continue
        ordered.append(account_id)
        seen.add(account_id)
    return tuple(ordered)


def resolve_sub_accounts(main_account: str) -> tuple[str, ...]:
    """Return sub-account ids for the chosen main account."""
    return tuple(account_id for account_id in SUPPORTED_ACCOUNT_IDS if account_id != main_account)


def resolve_main_account(main_account: str | None) -> str:
    """Sanitize main account id to allowed values with default fallback."""
    if main_account in ALLOWED_ACCOUNT_IDS:
        return main_account
    return DEFAULT_MAIN_ACCOUNT


def parse_proxy_bundle_line(line: str) -> dict[str, str]:
    parts = [part.strip() for part in str(line).strip().split("|")]
    if len(parts) != 5 or not all(parts):
        raise ValueError("proxy line must be IP|port|username|password|expire")
    host, port, username, password, expire_at = parts
    return {
        "proxy_bundle": str(line).strip(),
        "proxy_host": host,
        "proxy_port": port,
        "proxy_username": username,
        "proxy_password": password,
        "proxy_expire_at": expire_at,
    }


def parse_proxy_bundle_lines(text: str, account_ids: Sequence[str] = ACCOUNT_IDS) -> dict[str, dict[str, str]]:
    lines = [line.strip() for line in str(text).splitlines() if line.strip()]
    result: dict[str, dict[str, str]] = {}
    for account_id, line in zip(account_ids, lines):
        result[account_id] = parse_proxy_bundle_line(line)
    return result


@dataclass(frozen=True)
class PlatformSlot:
    account_id: str
    display_name: str = ""
    login_url: str = ""
    target_url: str = ""
    target_room: str = ""
    proxy_bundle: str = ""
    proxy_host: str = ""
    proxy_port: str = ""
    proxy_username: str = ""
    proxy_password: str = ""
    proxy_expire_at: str = ""
    account_username: str = ""
    account_password: str = ""
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_legacy_data(cls, account_id: str, payload: Mapping[str, Any]) -> "PlatformSlot":
        """Adapter for old `platform_slots` entries, including `proxy_expires`."""
        return cls(
            account_id=str(account_id),
            display_name=str(payload.get("name", "")),
            login_url=str(payload.get("login_url", "")),
            target_url=str(payload.get("target_url", "")),
            target_room=str(payload.get("target_room", "")),
            proxy_bundle=str(payload.get("proxy_bundle", "")),
            proxy_host=str(payload.get("proxy_host", "")),
            proxy_port=str(payload.get("proxy_port", "")),
            proxy_username=str(payload.get("proxy_username", "")),
            proxy_password=str(payload.get("proxy_password", "")),
            proxy_expire_at=str(payload.get("proxy_expire_at", payload.get("proxy_expires", ""))),
            account_username=str(payload.get("account_username", "")),
            account_password=str(payload.get("account_password", "")),
            extra={key: value for key, value in payload.items() if key not in {
                "name",
                "login_url",
                "target_url",
                "target_room",
                "proxy_bundle",
                "proxy_host",
                "proxy_port",
                "proxy_username",
                "proxy_password",
                "proxy_expire_at",
                "proxy_expires",
                "account_username",
                "account_password",
            }},
        )

    @classmethod
    def from_dict(cls, account_id: str, payload: Mapping[str, Any]) -> "PlatformSlot":
        if not isinstance(payload, Mapping):
            return cls(account_id=account_id)
        return cls(
            account_id=str(account_id),
            display_name=str(payload.get("display_name", payload.get("name", ""))),
            login_url=str(payload.get("login_url", "")),
            target_url=str(payload.get("target_url", "")),
            target_room=str(payload.get("target_room", "")),
            proxy_bundle=str(payload.get("proxy_bundle", "")),
            proxy_host=str(payload.get("proxy_host", "")),
            proxy_port=str(payload.get("proxy_port", "")),
            proxy_username=str(payload.get("proxy_username", "")),
            proxy_password=str(payload.get("proxy_password", "")),
            proxy_expire_at=str(payload.get("proxy_expire_at", payload.get("proxy_expires", ""))),
            account_username=str(payload.get("account_username", "")),
            account_password=str(payload.get("account_password", "")),
            extra={key: value for key, value in payload.items() if key not in {
                "display_name",
                "name",
                "login_url",
                "target_url",
                "target_room",
                "proxy_bundle",
                "proxy_host",
                "proxy_port",
                "proxy_username",
                "proxy_password",
                "proxy_expire_at",
                "proxy_expires",
                "account_username",
                "account_password",
            }},
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize for lightweight UI config persistence."""
        payload: dict[str, Any] = {
            "display_name": self.display_name,
            "login_url": self.login_url,
            "target_url": self.target_url,
            "target_room": self.target_room,
            "proxy_bundle": self.proxy_bundle,
            "proxy_host": self.proxy_host,
            "proxy_port": self.proxy_port,
            "proxy_username": self.proxy_username,
            "proxy_password": self.proxy_password,
            "proxy_expire_at": self.proxy_expire_at,
            "account_username": self.account_username,
            "account_password": self.account_password,
        }
        payload.update(self.extra)
        return payload

    def merge(self, updates: Mapping[str, Any]) -> "PlatformSlot":
        merged = dict(self.to_dict())
        for key in [
            "display_name",
            "login_url",
            "target_url",
            "target_room",
            "proxy_bundle",
            "proxy_host",
            "proxy_port",
            "proxy_username",
            "proxy_password",
            "proxy_expire_at",
            "account_username",
            "account_password",
        ]:
            if key in updates:
                merged[key] = updates[key]
        merged.update({key: value for key, value in updates.items() if key not in merged})
        return PlatformSlot(
            account_id=self.account_id,
            display_name=str(merged.get("display_name", merged.get("name", ""))),
            login_url=str(merged.get("login_url", "")),
            target_url=str(merged.get("target_url", "")),
            target_room=str(merged.get("target_room", "")),
            proxy_bundle=str(merged.get("proxy_bundle", "")),
            proxy_host=str(merged.get("proxy_host", "")),
            proxy_port=str(merged.get("proxy_port", "")),
            proxy_username=str(merged.get("proxy_username", "")),
            proxy_password=str(merged.get("proxy_password", "")),
            proxy_expire_at=str(merged.get("proxy_expire_at", merged.get("proxy_expires", ""))),
            account_username=str(merged.get("account_username", "")),
            account_password=str(merged.get("account_password", "")),
            extra={key: value for key, value in merged.items() if key not in {
                "display_name",
                "name",
                "login_url",
                "target_url",
                "target_room",
                "proxy_bundle",
                "proxy_host",
                "proxy_port",
                "proxy_username",
                "proxy_password",
                "proxy_expire_at",
                "proxy_expires",
                "account_username",
                "account_password",
            }},
        )


@dataclass
class ExecutionConfig:
    accounts: tuple[str, ...] = SUPPORTED_ACCOUNT_IDS
    main_account: str = DEFAULT_MAIN_ACCOUNT
    amount_min: int = 80
    amount_max: int = 150
    click_interval_ms: int = 200
    min_countdown: int = 10
    confirm_ms: int = 1200
    room_index: int = 1
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "accounts": list(self.accounts),
            "main_account": self.main_account,
            "amount_min": self.amount_min,
            "amount_max": self.amount_max,
            "click_interval_ms": self.click_interval_ms,
            "min_countdown": self.min_countdown,
            "confirm_ms": self.confirm_ms,
            "room_index": self.room_index,
        }
        payload.update(self.extra)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExecutionConfig":
        if not isinstance(payload, Mapping):
            return cls()
        accounts = payload.get("accounts", SUPPORTED_ACCOUNT_IDS)
        if isinstance(accounts, Iterable):
            normalized = tuple(str(item) for item in accounts if str(item) in ALLOWED_ACCOUNT_IDS)
            if not normalized:
                normalized = SUPPORTED_ACCOUNT_IDS
        else:
            normalized = SUPPORTED_ACCOUNT_IDS
        main_account = resolve_main_account(str(payload.get("main_account", DEFAULT_MAIN_ACCOUNT)))
        if main_account not in normalized:
            main_account = normalized[0] if normalized else DEFAULT_MAIN_ACCOUNT
        return cls(
            accounts=tuple(normalized),
            main_account=main_account,
            amount_min=int(payload.get("amount_min", 80)),
            amount_max=int(payload.get("amount_max", 150)),
            click_interval_ms=int(payload.get("click_interval_ms", 200)),
            min_countdown=int(payload.get("min_countdown", 10)),
            confirm_ms=int(payload.get("confirm_ms", 1200)),
            room_index=int(payload.get("room_index", 1)),
            extra={key: value for key, value in payload.items() if key not in {
                "accounts",
                "main_account",
                "amount_min",
                "amount_max",
                "click_interval_ms",
                "min_countdown",
                "confirm_ms",
                "room_index",
            }},
        )


@dataclass(frozen=True)
class AccountStatusSummary:
    account_id: str
    display_name: str
    mode: str
    role: str
    room_label: str = ""
    round_id: str = ""
    countdown: int | None = None
    betting_open: bool = False
    balance: Decimal | None = None
    pending_amount: Decimal | None = None
    state_label: str = ""
    updated_at_ms: int = 0


@dataclass(frozen=True)
class RoundResult:
    round_id: str
    room_label: str = ""
    send_countdowns: dict[str, int] = field(default_factory=dict)
    click_interval_ms: int = 200
    legs: list[Any] = field(default_factory=list)
    max_elapsed_ms: int = 0
    missing_total: Decimal = Decimal("0")
    status: str = "skipped"
    reason: str = ""
