"""Controller bridge for the lightweight hedge UI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from time import time
from typing import Any

from bet_desktop.ui.lightweight_browser_adapter import BrowserControlAdapter, FakeBrowserControlAdapter
from bet_desktop.ui.lightweight_config_store import (
    LightweightConfigSnapshot,
    LightweightConfigStore,
    default_execution_config,
    default_platform_slots,
)
from bet_desktop.ui.lightweight_models import (
    ACCOUNT_IDS,
    AccountStatusSummary,
    ExecutionConfig,
    PlatformSlot,
    RoundResult,
    resolve_main_account,
    resolve_sub_accounts,
)


def _now_ms() -> int:
    return int(time() * 1000)


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text and text.lower() not in {"none", "null"}:
            return text
    return ""


def _public_round_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"none", "null", "-"}:
        return ""
    parts = text.split("-")
    if len(parts) >= 4 and parts[-1].strip().isdigit():
        return "-".join(part.strip() for part in parts[:3] if part.strip())
    return text


def _as_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _as_decimal(value: Any) -> Decimal | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace(",", "").replace("￥", "").replace("元", "").strip()
    try:
        return Decimal(normalized)
    except (InvalidOperation, ValueError):
        return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "open", "can_bet", "betting"}


ROUND_STALE_LABEL = "\u5c40\u53f7\u505c\u66f4"
STATE_STALE_MS = 15_000
AUTO_REENTRY_COOLDOWN_MS = 30_000


def _safe_summary_from(payload: dict[str, Any]) -> dict[str, Any]:
    safe_summary = payload.get("safe_summary", {}) if isinstance(payload, dict) else {}
    return safe_summary if isinstance(safe_summary, dict) else {}


def _snapshot_timestamp_ms(snapshot: dict[str, Any]) -> int:
    return _as_int(snapshot.get("timestamp_captured_ms")) or _now_ms()


def _decayed_countdown(snapshot: dict[str, Any], safe_summary: dict[str, Any], *, current_ms: int) -> int | None:
    countdown = _as_int(snapshot.get("exact_countdown"))
    if countdown is None:
        countdown = _as_int(
            _first_text(
                safe_summary.get("runtime_countdown"),
                safe_summary.get("label_countdown"),
                safe_summary.get("frontend_countdown"),
                safe_summary.get("canvas_countdown"),
            ),
        )
    if countdown is None:
        return None
    anchor_ms = _snapshot_timestamp_ms(snapshot)
    if _truthy(safe_summary.get("ui_reference_countdown")):
        anchor_ms = (
            _as_int(snapshot.get("ui_countdown_started_ms"))
            or _as_int(safe_summary.get("ui_countdown_started_ms"))
            or anchor_ms
        )
    age_seconds = max(0, int((current_ms - anchor_ms) / 1000))
    return max(0, countdown - age_seconds)


def _runtime_room_label_from_summary(safe_summary: dict[str, Any]) -> str:
    return _first_text(
        safe_summary.get("display_room_label"),
        safe_summary.get("room_label"),
        safe_summary.get("locked_room_label"),
        safe_summary.get("runtime_room_label"),
        safe_summary.get("label_room_label"),
        safe_summary.get("frontend_room_label"),
        safe_summary.get("canvas_room_label"),
    )


def _round_id_from_snapshot(snapshot: dict[str, Any], safe_summary: dict[str, Any]) -> str:
    return _public_round_id(_first_text(
        snapshot.get("batch_id") if isinstance(snapshot, dict) else "",
        safe_summary.get("display_game_no"),
        safe_summary.get("round_id"),
        safe_summary.get("runtime_memory_game_no"),
        safe_summary.get("label_game_no"),
        safe_summary.get("label_internal_game_no"),
        safe_summary.get("frontend_batch_id"),
        safe_summary.get("frontend_short_batch_id"),
        safe_summary.get("canvas_game_no"),
        safe_summary.get("canvas_internal_game_no"),
        "-",
    )) or "-"


def _phase_label_from_summary(safe_summary: dict[str, Any]) -> str:
    return _first_text(
        safe_summary.get("display_phase"),
        safe_summary.get("runtime_phase_label"),
        safe_summary.get("label_phase_text"),
        safe_summary.get("frontend_phase_text"),
        safe_summary.get("canvas_phase_text"),
        safe_summary.get("phase_text"),
    )


def _has_room_runtime_evidence(payload: dict[str, Any], safe_summary: dict[str, Any]) -> bool:
    if _runtime_room_label_from_summary(safe_summary):
        return True
    if _round_id_from_snapshot(payload, safe_summary) != "-":
        return True
    if safe_summary.get("display_room_label"):
        return True
    if _decayed_countdown(payload, safe_summary, current_ms=_now_ms()) is not None:
        return True
    if _phase_label_from_summary(safe_summary):
        return True
    runtime_coordinates = safe_summary.get("runtime_coordinates")
    if isinstance(runtime_coordinates, dict):
        if runtime_coordinates.get("bet_regions") or runtime_coordinates.get("chips"):
            return True
    if _truthy(safe_summary.get("runtime_betting_open")) or _truthy(safe_summary.get("runtime_is_can_betting")):
        return True
    return bool(_first_text(
        safe_summary.get("runtime_action"),
        safe_summary.get("runtime_current_load_type"),
        safe_summary.get("runtime_timed"),
    ))


def _payload_game_ready(payload: dict[str, Any], safe_summary: dict[str, Any] | None = None) -> bool:
    summary = safe_summary if safe_summary is not None else _safe_summary_from(payload)
    return (
        _truthy(payload.get("game_ready"))
        or _truthy(summary.get("game_ready"))
        or _has_room_runtime_evidence(payload, summary)
    )


def _payload_hall_without_game(payload: dict[str, Any], safe_summary: dict[str, Any] | None = None) -> bool:
    summary = safe_summary if safe_summary is not None else _safe_summary_from(payload)
    hall_ready = _truthy(payload.get("hall_ready")) or _truthy(summary.get("hall_ready"))
    return bool(hall_ready and not _payload_game_ready(payload, summary))


def _preserve_ui_countdown_anchor(previous: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    safe_summary = _safe_summary_from(payload)
    if not (_truthy(safe_summary.get("ui_reference_countdown")) and _truthy(safe_summary.get("runtime_betting_open"))):
        return payload
    previous_safe = _safe_summary_from(previous)
    if not _truthy(previous_safe.get("ui_reference_countdown")):
        return payload
    current_round = _round_id_from_snapshot(payload, safe_summary)
    previous_round = _round_id_from_snapshot(previous, previous_safe)
    if not current_round or current_round == "-" or current_round != previous_round:
        return payload
    previous_anchor = _as_int(previous.get("ui_countdown_started_ms")) or _as_int(previous_safe.get("ui_countdown_started_ms"))
    if previous_anchor is None:
        return payload
    merged = dict(payload)
    merged["ui_countdown_started_ms"] = previous_anchor
    merged_safe = dict(safe_summary)
    merged_safe["ui_countdown_started_ms"] = previous_anchor
    merged["safe_summary"] = merged_safe
    return merged


def _state_machine_label(safe_summary: dict[str, Any], *, betting_open: bool, stale: bool, age_ms: int) -> str:
    if stale:
        return f"数据过期 {max(1, int(age_ms / 1000))}秒"
    if _payload_hall_without_game({}, safe_summary):
        return "大厅"
    phase_label = _phase_label_from_summary(safe_summary)
    parts: list[str] = []
    if phase_label:
        parts.append(phase_label)
    elif _payload_game_ready({}, safe_summary):
        parts.append("房间内")
    if betting_open:
        parts.append("可下注")
    timed = _first_text(safe_summary.get("runtime_timed"), safe_summary.get("runtime_countdown"))
    action = _first_text(safe_summary.get("runtime_action"))
    load_type = _first_text(safe_summary.get("runtime_current_load_type"))
    if timed:
        parts.append(f"时间{timed}")
    if action:
        parts.append(f"动作{action}")
    if load_type:
        parts.append(f"装载{load_type}")
    return " · ".join(parts) if parts else "等待状态"


def _snapshot_round_stale(snapshot: dict[str, Any], safe_summary: dict[str, Any]) -> bool:
    if not isinstance(snapshot, dict):
        return False
    probe_status = snapshot.get("probe_status")
    return bool(
        snapshot.get("round_stale")
        or safe_summary.get("round_stale")
        or (isinstance(probe_status, dict) and probe_status.get("round_stale"))
    )


def _round_stale_age_ms(snapshot: dict[str, Any], safe_summary: dict[str, Any]) -> int | None:
    value = snapshot.get("round_stale_age_ms") or safe_summary.get("round_stale_age_ms")
    if value is None and isinstance(snapshot.get("probe_status"), dict):
        value = snapshot["probe_status"].get("round_stale_age_ms")
    return _as_int(value)


class LightweightController:
    """Phase-1~3 controller with lightweight actions and summary events."""

    def __init__(
        self,
        config_store: LightweightConfigStore | None = None,
        adapter: BrowserControlAdapter | None = None,
        max_log_lines: int = 200,
    ) -> None:
        self.config_store = config_store or LightweightConfigStore()
        self.adapter = adapter or FakeBrowserControlAdapter(max_log_entries=max_log_lines)
        self._event_handlers: dict[str, list[Callable[[Any], None]]] = {}
        self._max_log_lines = max_log_lines
        self._log_lines: list[str] = []
        if getattr(self.adapter, "on_log", None) is None:
            self.adapter.on_log = self._append_log

        snapshot = self._load_snapshot()
        self.platform_slots = list(snapshot.platform_slots)
        self.config = snapshot.execution_config
        self.execution_state = "idle"
        self.main_account = resolve_main_account(self.config.main_account)
        self.config = replace(self.config, main_account=self.main_account)
        self._account_modes = {account_id: "待命" for account_id in ACCOUNT_IDS}
        self._runtime_status = {account_id: "待命" for account_id in ACCOUNT_IDS}
        self._runtime_snapshots: dict[str, dict[str, Any]] = {}
        self._runtime_errors: dict[str, str] = {}
        self._round_results: list[RoundResult] = []
        self._current_plan: dict[str, Any] = {}
        self._plan_account_states: dict[str, str] = {account_id: "normal" for account_id in ACCOUNT_IDS}
        self._room_entry_requested: dict[str, int] = {}
        self._room_entry_details: dict[str, str] = {}
        self._auto_reentry_cooldowns_ms: dict[str, int] = {}
        self._auto_reentry_enabled = True
        self._update_platform_roles()
        self.adapter.refresh_runtime_environment(self.platform_slots)
        self._sync_participation_states_to_adapter()
        self._emit_account_status()

    def _load_snapshot(self) -> LightweightConfigSnapshot:
        try:
            snapshot = self.config_store.load()
            if snapshot.platform_slots:
                return snapshot
        except OSError:
            pass
        return LightweightConfigSnapshot(
            platform_slots=default_platform_slots(),
            execution_config=default_execution_config(),
            raw_payload={},
        )

    def _emit(self, event_name: str, payload: Any) -> None:
        for callback in self._event_handlers.get(event_name, ()):
            callback(payload)

    def on(self, event_name: str, callback: Callable[[Any], None]) -> None:
        self._event_handlers.setdefault(event_name, []).append(callback)

    def _append_log(self, message: str) -> None:
        self._log_lines.append(message)
        if len(self._log_lines) > self._max_log_lines:
            self._log_lines = self._log_lines[-self._max_log_lines :]
        self._emit("operator_log_appended", message)

    @property
    def logs(self) -> list[str]:
        return list(self._log_lines)

    @property
    def account_status(self) -> list[AccountStatusSummary]:
        return self._build_account_statuses()

    @property
    def round_results(self) -> list[RoundResult]:
        return list(self._round_results)

    @property
    def current_plan(self) -> dict[str, Any]:
        return dict(self._current_plan)

    @property
    def plan_account_states(self) -> dict[str, str]:
        return dict(self._plan_account_states)

    def _emit_account_status(self) -> None:
        self._emit("account_status_updated", self.account_status)

    def _emit_round_results(self) -> None:
        self._emit("round_results_updated", self.round_results)

    def _emit_current_plan(self) -> None:
        self._emit("hedge_plan_updated", self.current_plan)

    def _emit_plan_account_states(self) -> None:
        self._emit("plan_account_states_updated", self.plan_account_states)

    def _emit_platform_summary(self) -> None:
        self._emit("platform_summary_updated", self.platform_slots)

    def _emit_error(self, message: str) -> None:
        self._emit("error_banner_updated", message)

    def _update_platform_roles(self) -> None:
        self.sub_account_ids = resolve_sub_accounts(self.main_account)

    def _sync_participation_states_to_adapter(self) -> None:
        self.adapter.set_account_participation_states(self._plan_account_states)

    def set_plan_account_state(self, account_id: str, state: str) -> None:
        if account_id not in ACCOUNT_IDS:
            return
        normalized = str(state or "normal").strip() or "normal"
        self._plan_account_states[account_id] = normalized
        self._sync_participation_states_to_adapter()
        self._emit_plan_account_states()

    def set_plan_account_states(self, states: dict[str, str]) -> None:
        merged = {account_id: "normal" for account_id in ACCOUNT_IDS}
        for account_id, state in dict(states or {}).items():
            if account_id in merged:
                merged[account_id] = str(state or "normal").strip() or "normal"
        self._plan_account_states = merged
        self._sync_participation_states_to_adapter()
        self._emit_plan_account_states()

    def _normalize_account_ids(self, account_ids: list[str]) -> list[str]:
        return [account_id for account_id in account_ids if account_id in ACCOUNT_IDS]

    def _resolve_account_ids(
        self,
        account_ids: list[str] | bool | None = None,
        *,
        default_to_all: bool = False,
    ) -> list[str]:
        if isinstance(account_ids, bool):
            account_ids = None
        if account_ids is None:
            if not default_to_all:
                return []
            return self.platform_slots_account_ids()
        return self._normalize_account_ids(account_ids)

    def _sub_account_ids(self) -> list[str]:
        return [account_id for account_id in resolve_sub_accounts(self.main_account) if account_id in self.platform_slots_account_ids()]

    def _mark_runtime_status(self, account_ids: list[str], status: str) -> None:
        for account_id in self._normalize_account_ids(account_ids):
            self._runtime_status[account_id] = status

    def get_snapshot(self) -> LightweightConfigSnapshot:
        return LightweightConfigSnapshot(
            platform_slots=tuple(self.platform_slots),
            execution_config=self.config,
            raw_payload={},
        )

    def _build_account_statuses(self) -> list[AccountStatusSummary]:
        statuses: list[AccountStatusSummary] = []
        current_ms = _now_ms()
        for slot in self.platform_slots:
            role = "main" if slot.account_id == self.main_account else "sub"
            account_mode = self._account_modes.get(slot.account_id, "idle")
            runtime_status = self._runtime_status.get(slot.account_id, "待命")
            snapshot = self._runtime_snapshots.get(slot.account_id, {})
            safe_summary = _safe_summary_from(snapshot) if isinstance(snapshot, dict) else {}
            runtime_room_label = _runtime_room_label_from_summary(safe_summary)
            room_label = _first_text(runtime_room_label, "-")
            round_id = _round_id_from_snapshot(snapshot, safe_summary)
            countdown = _decayed_countdown(snapshot, safe_summary, current_ms=current_ms) if snapshot else None
            balance = _as_decimal(snapshot.get("ocr_balance") if isinstance(snapshot, dict) else None)
            updated_at_ms = _snapshot_timestamp_ms(snapshot) if snapshot else current_ms
            age_ms = max(0, current_ms - updated_at_ms) if snapshot else 0
            stale = bool(snapshot and age_ms > STATE_STALE_MS)
            round_stale = bool(snapshot and _snapshot_round_stale(snapshot, safe_summary) and not stale)
            raw_betting_open = _truthy(
                safe_summary.get("runtime_betting_open")
                if "runtime_betting_open" in safe_summary
                else safe_summary.get("runtime_is_can_betting")
            )
            countdown_expired = (
                _truthy(safe_summary.get("ui_reference_countdown"))
                and countdown is not None
                and countdown <= 0
            )
            betting_open = (not stale) and (not round_stale) and raw_betting_open and not countdown_expired
            phase_label = _phase_label_from_summary(safe_summary)
            state_machine_label = _state_machine_label(safe_summary, betting_open=betting_open, stale=stale, age_ms=age_ms)
            if round_stale:
                round_age_ms = _round_stale_age_ms(snapshot, safe_summary)
                if round_age_ms is not None:
                    state_machine_label = f"{ROUND_STALE_LABEL} {max(1, int(round_age_ms / 1000))}秒"
                else:
                    state_machine_label = ROUND_STALE_LABEL
            state_label = runtime_status if runtime_status != "待命" else account_mode
            if snapshot:
                if stale:
                    state_label = "数据过期"
                elif round_stale:
                    state_label = ROUND_STALE_LABEL
                elif _payload_hall_without_game(snapshot, safe_summary):
                    state_label = "大厅"
                elif betting_open:
                    state_label = "可下注"
                elif _payload_game_ready(snapshot, safe_summary):
                    state_label = "不可下注"
                elif phase_label:
                    state_label = phase_label
                elif room_label != "-":
                    state_label = "不可下注"
            elif slot.account_id in self._runtime_errors:
                state_label = "异常"
            target_room_index = int(self._room_entry_requested.get(slot.account_id) or self.config.room_index or 1)
            target_room_label = f"{target_room_index}房"
            statuses.append(
                AccountStatusSummary(
                    account_id=slot.account_id,
                    display_name=slot.display_name or slot.account_id,
                    mode=runtime_status,
                    role=role,
                    room_label=room_label,
                    round_id=round_id,
                    countdown=countdown,
                    betting_open=betting_open,
                    balance=balance,
                    pending_amount=None,
                    state_label=state_label,
                    updated_at_ms=updated_at_ms,
                    state_machine_label=state_machine_label,
                    stale=stale,
                    age_ms=age_ms,
                    target_room_label=target_room_label,
                    room_entry_detail=self._room_entry_details.get(slot.account_id, ""),
                )
            )
        return statuses

    def set_main_account(self, account_id: str) -> None:
        account_id = resolve_main_account(account_id)
        if account_id not in [slot.account_id for slot in self.platform_slots]:
            raise ValueError(f"Invalid account id: {account_id}")
        if account_id == self.main_account:
            return
        self.main_account = account_id
        self.config = replace(self.config, main_account=account_id)
        self._update_platform_roles()
        self._append_log(f"账号切换至{account_id}")
        self._emit("main_account_changed", account_id)
        self._emit_account_status()

    def update_platform_slot(self, account_id: str, updates: dict[str, Any]) -> None:
        found = False
        updated: list[PlatformSlot] = []
        for slot in self.platform_slots:
            if slot.account_id == account_id:
                updated.append(slot.merge(updates))
                found = True
            else:
                updated.append(slot)
        if not found:
            return
        self.platform_slots = updated
        self._append_log(f"配置更新: {account_id}")
        self.adapter.refresh_runtime_environment(self.platform_slots)
        self._emit_platform_summary()
        self._emit_account_status()

    def save_config(self) -> None:
        snapshot = LightweightConfigSnapshot(
            platform_slots=tuple(self.platform_slots),
            execution_config=self.config,
            raw_payload=self.config_store.load().raw_payload,
        )
        self.config_store.save(snapshot)
        self._append_log("配置落盘成功")

    def start_clicked(self) -> None:
        self.execution_state = "running"
        account_ids = self.platform_slots_account_ids()
        sub_accounts = tuple(account_id for account_id in self.get_sub_accounts() if account_id in account_ids)
        if self.main_account not in account_ids or not sub_accounts:
            self._emit_error("主号或副号配置不完整")
            return
        self._append_log(
            f"启动持续对冲: 主号={self.main_account} 副号={','.join(sub_accounts)} "
            f"金额={self.config.amount_min}-{self.config.amount_max} 间隔={self.config.click_interval_ms}ms"
        )
        self._sync_participation_states_to_adapter()
        self.adapter.start_hedge(
            account_ids=account_ids,
            main_account=self.main_account,
            sub_accounts=sub_accounts,
            room_index=int(self.config.room_index),
            amount_min=int(self.config.amount_min),
            amount_max=int(self.config.amount_max),
            main_successor_account=str(self.config.main_successor_account or ""),
            min_balance_yuan=int(self.config.min_balance_yuan),
            click_interval_ms=int(self.config.click_interval_ms),
            confirm_ms=int(self.config.confirm_ms),
            min_countdown=int(self.config.min_countdown),
        )
        self._emit("gate_status_updated", "运行中")
        self._emit_account_status()

    def pause_clicked(self) -> None:
        self.execution_state = "paused"
        self.adapter.pause_hedge()
        self._append_log("系统暂停：停止发号，保留浏览器现场")
        self._emit("gate_status_updated", "暂停")
        self._emit_account_status()

    def stop_clicked(self) -> None:
        self.execution_state = "stopped"
        self._append_log("系统急停")
        self.adapter.stop_hedge()
        self.adapter.stop_accounts(self.platform_slots_account_ids())
        self.shutdown_runtime()
        for account_id in self.platform_slots_account_ids():
            self._account_modes[account_id] = "stopped"
            self._runtime_status[account_id] = "停止"
        self._emit("gate_status_updated", "停止")
        self._emit_account_status()

    def start_batch_clicked(self) -> None:
        self.start_batch(self.platform_slots_account_ids())

    def open_login_pages_clicked(self, account_ids: list[str] | bool | None = None) -> None:
        targets = self._resolve_account_ids(account_ids, default_to_all=True)
        if not targets:
            self._emit_error("无可用账号")
            return
        self._append_log(f"打开登录页: {','.join(targets)}")
        self.adapter.open_login_pages(targets)
        self._mark_runtime_status(targets, "打开登录页")

    def fill_login_clicked(self, account_ids: list[str] | bool | None = None) -> None:
        targets = self._resolve_account_ids(account_ids, default_to_all=True)
        if not targets:
            self._emit_error("无可用账号")
            return
        self._append_log(f"批量填写登录: {','.join(targets)}")
        self.adapter.fill_login(targets)
        self._mark_runtime_status(targets, "登录中")
        self._emit_account_status()

    def batch_fill_login_clicked(self) -> None:
        self.fill_login_clicked(self.platform_slots_account_ids())

    def fill_account_btn_clicked(self, account_id: str) -> None:
        if account_id not in self.platform_slots_account_ids():
            self._emit_error("不存在的账号")
            return
        self._append_log(f"账号 {account_id} 执行填写登录")
        self.fill_login_clicked([account_id])

    def restart_account_clicked(self, account_id: str) -> None:
        if account_id not in self.platform_slots_account_ids():
            self._emit_error("不存在的账号")
            return
        self._append_log(f"账号 {account_id} 重启")
        self.adapter.restart_accounts([account_id])
        self._room_entry_requested.pop(account_id, None)
        self._room_entry_details.pop(account_id, None)
        self._auto_reentry_cooldowns_ms.pop(account_id, None)
        self._mark_runtime_status([account_id], "重启中")
        self._emit_account_status()

    def handoff_account_clicked(self, account_id: str) -> None:
        if account_id not in self.platform_slots_account_ids():
            self._emit_error("不存在的账号")
            return
        self._append_log(f"账号 {account_id} 接管")
        self.adapter.handoff_to_headless([account_id])
        self._mark_runtime_status([account_id], "接管中")
        self._emit_account_status()

    def enter_room_account_clicked(self, account_id: str, room_index: int | bool | None = None) -> None:
        if account_id not in self.platform_slots_account_ids():
            self._emit_error("不存在的账号")
            return
        if isinstance(room_index, bool) or room_index is None:
            resolved_room_index = int(self.config.room_index)
        else:
            resolved_room_index = int(room_index)
        if resolved_room_index < 1:
            resolved_room_index = 1
        self._append_log(f"账号 {account_id} 进房: 房间{resolved_room_index}")
        code, stdout, stderr = self.adapter.enter_room([account_id], room_index=resolved_room_index)
        if code != 0:
            self._emit_error(stderr or stdout or "单账号进房命令未能发送")
            self._mark_runtime_status([account_id], "进房失败")
        else:
            self._room_entry_requested[account_id] = resolved_room_index
            self._room_entry_details[account_id] = "已提交进房命令"
            self._mark_runtime_status([account_id], "进房中")
        self._emit_account_status()

    def batch_handoff_clicked(self) -> None:
        targets = self._sub_account_ids()
        if not targets:
            self._append_log("接管副号：无可接管账号")
            return
        self._append_log(f"接管副号: {','.join(targets)}")
        self.adapter.handoff_to_headless(targets)
        self._mark_runtime_status(targets, "接管中")
        self._emit_account_status()

    def batch_enter_room_clicked(self, room_index: int | bool | None = None) -> None:
        if isinstance(room_index, bool) or room_index is None:
            resolved_room_index = int(self.config.room_index)
        else:
            resolved_room_index = int(room_index)
        if resolved_room_index < 1:
            resolved_room_index = 1
        targets = self._sub_account_ids()
        if not targets:
            self._append_log("批量进房：无可用账号")
            return
        self._append_log(f"批量进房: 房间{resolved_room_index} {','.join(targets)}")
        code, stdout, stderr = self.adapter.enter_room(targets, room_index=resolved_room_index)
        if code != 0:
            self._emit_error(stderr or stdout or "批量进房命令未能发送")
            self._mark_runtime_status(targets, "进房失败")
        else:
            for account_id in targets:
                self._room_entry_requested[account_id] = resolved_room_index
                self._room_entry_details[account_id] = "已提交进房命令"
            self._mark_runtime_status(targets, "进房中")
        self._emit_account_status()

    def enter_room_all_clicked(self, room_index: int | bool | None = None) -> None:
        if isinstance(room_index, bool) or room_index is None:
            resolved_room_index = int(self.config.room_index)
        else:
            resolved_room_index = int(room_index)
        if resolved_room_index < 1:
            resolved_room_index = 1
        targets = self.platform_slots_account_ids()
        if not targets:
            self._append_log("全部进房：无可用账号")
            return
        self._append_log(f"全部进房: 房间{resolved_room_index} {','.join(targets)}")
        code, stdout, stderr = self.adapter.enter_room(targets, room_index=resolved_room_index)
        if code != 0:
            self._emit_error(stderr or stdout or "全部进房命令未能发送")
            self._mark_runtime_status(targets, "进房失败")
        else:
            for account_id in targets:
                self._room_entry_requested[account_id] = resolved_room_index
                self._room_entry_details[account_id] = "已提交进房命令"
            self._mark_runtime_status(targets, "进房中")
        self._emit_account_status()

    def refresh_headless_clicked(self, account_ids: list[str] | bool | None = None) -> None:
        targets = self._resolve_account_ids(account_ids, default_to_all=False) if account_ids else self._sub_account_ids()
        if not targets:
            self._append_log("刷新无头：无可用账号")
            return
        self._append_log(f"刷新无头: {','.join(targets)}")
        self.adapter.refresh_headless(targets)

    def batch_release_clicked(self) -> None:
        targets = self._sub_account_ids()
        if not targets:
            self._append_log("释放无头：无可用账号")
            return
        self._append_log(f"释放无头: {','.join(targets)}")
        self.adapter.release_headless(targets)
        for account_id in targets:
            self._room_entry_requested.pop(account_id, None)
            self._room_entry_details.pop(account_id, None)
            self._auto_reentry_cooldowns_ms.pop(account_id, None)
        self._mark_runtime_status(targets, "已释放")
        self._emit_account_status()

    def batch_stop_clicked(self) -> None:
        self.stop_clicked()

    def test_one_round_clicked(self) -> None:
        self._execute_probe_rounds(1)

    def test_ten_rounds_clicked(self) -> None:
        self._execute_probe_rounds(10)

    def _execute_probe_rounds(self, rounds: int) -> None:
        account_ids = self.platform_slots_account_ids()
        sub_accounts = tuple(account_id for account_id in self.get_sub_accounts() if account_id in account_ids)
        if self.main_account not in account_ids or not sub_accounts:
            self._emit_error("主号或副号配置不完整")
            return
        self.execution_state = "running"
        self._append_log(
            f"提交对冲测试: {rounds}轮 主号={self.main_account} 副号={','.join(sub_accounts)} "
            f"间隔={self.config.click_interval_ms}ms"
        )
        self._sync_participation_states_to_adapter()
        self.adapter.execute_rounds(
            account_ids=account_ids,
            main_account=self.main_account,
            sub_accounts=sub_accounts,
            rounds=int(rounds),
            room_index=int(self.config.room_index),
            amount_min=int(self.config.amount_min),
            amount_max=int(self.config.amount_max),
            main_successor_account=str(self.config.main_successor_account or ""),
            min_balance_yuan=int(self.config.min_balance_yuan),
            click_interval_ms=int(self.config.click_interval_ms),
            confirm_ms=int(self.config.confirm_ms),
            min_countdown=int(self.config.min_countdown),
        )

    def start_batch(self, account_ids: list[str] | None = None) -> None:
        targets = self._normalize_account_ids(account_ids or self.platform_slots_account_ids())
        if not targets:
            self._emit_error("当前无可启动账号")
            return
        self.execution_state = "running"
        for account_id in targets:
            self._account_modes[account_id] = "running"
        self._append_log(f"批量启动: {','.join(targets)}")
        self.adapter.start_accounts(targets)
        self._mark_runtime_status(targets, "启动中")
        self._emit("gate_status_updated", "运行中")
        self._emit_account_status()

    def platform_slots_account_ids(self) -> list[str]:
        return [slot.account_id for slot in self.platform_slots]

    def get_sub_accounts(self) -> tuple[str, ...]:
        return resolve_sub_accounts(self.main_account)

    def apply_execution_config(self, updates: dict[str, Any]) -> None:
        self.config = ExecutionConfig(
            accounts=self.config.accounts,
            main_account=self.config.main_account,
            main_successor_account=str(updates.get("main_successor_account", self.config.main_successor_account) or ""),
            amount_min=int(updates.get("amount_min", self.config.amount_min)),
            amount_max=int(updates.get("amount_max", self.config.amount_max)),
            min_balance_yuan=int(updates.get("min_balance_yuan", self.config.min_balance_yuan)),
            click_interval_ms=int(updates.get("click_interval_ms", self.config.click_interval_ms)),
            min_countdown=int(updates.get("min_countdown", self.config.min_countdown)),
            confirm_ms=int(updates.get("confirm_ms", self.config.confirm_ms)),
            room_index=int(updates.get("room_index", self.config.room_index)),
            extra={**self.config.extra, **{
                k: v
                for k, v in updates.items()
                if k
                not in {
                    "amount_min",
                    "amount_max",
                    "min_balance_yuan",
                    "main_successor_account",
                    "click_interval_ms",
                    "min_countdown",
                    "confirm_ms",
                    "room_index",
                }
            }},
        )
        self._append_log("执行参数更新")

    def _money_decimal(self, value: Any) -> Decimal:
        parsed = _as_decimal(value)
        return parsed if parsed is not None else Decimal("0")

    def _status_text_from_results(self, results: list[dict[str, Any]]) -> str:
        if not results:
            return "empty"
        if any(str(item.get("status") or "").upper() in {"ERROR", "REJECTED"} for item in results):
            return "error"
        if any(self._money_decimal(item.get("missing_amount")) > 0 for item in results):
            return "incomplete"
        if all(str(item.get("status") or "").upper() == "COMPLETE" for item in results):
            return "complete"
        return "unknown"

    def _round_result_from_payload(self, payload: dict[str, Any]) -> RoundResult:
        raw_results = payload.get("results", [])
        results = [dict(item) for item in raw_results if isinstance(item, dict)] if isinstance(raw_results, list) else []
        raw_legs = payload.get("legs", [])
        legs = [dict(item) for item in raw_legs if isinstance(item, dict)] if isinstance(raw_legs, list) else []
        raw_countdowns = payload.get("send_countdowns", {})
        send_countdowns: dict[str, int] = {}
        if isinstance(raw_countdowns, dict):
            for account_id, value in raw_countdowns.items():
                countdown = _as_int(value)
                if countdown is not None:
                    send_countdowns[str(account_id)] = countdown
        if not send_countdowns:
            for result in results:
                account_id = str(result.get("instance_id") or "")
                countdown = _as_int(result.get("pre_countdown"))
                if account_id and countdown is not None:
                    send_countdowns[account_id] = countdown
        missing_total = sum((self._money_decimal(item.get("missing_amount")) for item in results), Decimal("0"))
        round_number = _as_int(payload.get("round_number")) or len(self._round_results) + 1
        click_interval = _as_int(payload.get("delay_ms")) or _as_int(payload.get("click_interval_ms")) or int(self.config.click_interval_ms)
        elapsed_ms = _as_int(payload.get("elapsed_ms")) or 0
        max_elapsed_ms = 0
        for result in results:
            max_elapsed_ms = max(
                max_elapsed_ms,
                _as_int(result.get("elapsed_ms")) or 0,
                _as_int(result.get("click_sequence_ms")) or 0,
            )
        return RoundResult(
            round_id=_public_round_id(payload.get("round_id") or payload.get("batch_id")),
            round_number=int(round_number),
            room_label=str(payload.get("room_label") or ""),
            send_countdowns=send_countdowns,
            click_interval_ms=int(click_interval),
            legs=legs,
            results=results,
            elapsed_ms=int(elapsed_ms),
            max_elapsed_ms=int(max_elapsed_ms),
            missing_total=missing_total,
            status=self._status_text_from_results(results),
            reason=str(payload.get("reason") or ""),
        )

    def _reconcile_plan_account_states(self, payload: dict[str, Any]) -> None:
        excluded = payload.get("excluded_accounts", {})
        excluded_reasons = excluded if isinstance(excluded, dict) else {}
        updated = dict(self._plan_account_states)
        changed = False
        for account_id in ACCOUNT_IDS:
            state = updated.get(account_id, "normal")
            reason = str(excluded_reasons.get(account_id) or "")
            next_state = state
            if state == "pending_exclude" and account_id in excluded_reasons:
                next_state = "excluded"
            elif state == "pending_restore":
                next_state = "restore_failed" if account_id in excluded_reasons else "normal"
            elif state == "restore_failed" and account_id not in excluded_reasons:
                next_state = "normal"
            if reason.startswith("恢复失败"):
                next_state = "restore_failed"
            if next_state != state:
                updated[account_id] = next_state
                changed = True
        if changed:
            self._plan_account_states = updated
            self._sync_participation_states_to_adapter()
            self._emit_plan_account_states()

    def _handle_plan_event(self, payload: dict[str, Any]) -> None:
        self._current_plan = dict(payload)
        self._reconcile_plan_account_states(self._current_plan)
        self._emit_current_plan()
        round_number = payload.get("round_number") or "-"
        legs = payload.get("legs", [])
        leg_count = len(legs) if isinstance(legs, list) else 0
        self._append_log(f"hedge plan ready: round={round_number} legs={leg_count}")

    def _handle_round_event(self, payload: dict[str, Any]) -> None:
        result = self._round_result_from_payload(payload)
        self._round_results.append(result)
        self._emit_round_results()
        self._append_log(
            "hedge round done: "
            f"round={result.round_number} interval={result.click_interval_ms}ms "
            f"elapsed={result.elapsed_ms}ms max_click={result.max_elapsed_ms}ms "
            f"missing={result.missing_total} status={result.status}"
        )

    def poll_runtime_events(self) -> None:
        events = self.adapter.poll_events()
        if not isinstance(events, list):
            return
        had_update = False
        for event in events:
            if not isinstance(event, dict):
                continue
            instance_id = str(event.get("instance_id", ""))
            payload = event.get("payload", {})
            if not isinstance(payload, dict):
                payload = {}
            event_type = str(event.get("event_type", ""))
            self._handle_runtime_event(instance_id, event_type, payload)
            had_update = had_update or bool(instance_id)
        if had_update:
            self._emit_account_status()

    def _handle_runtime_event(self, instance_id: str, event_type: str, payload: dict[str, Any]) -> None:
        if event_type == "plan":
            self._handle_plan_event(payload)
            return
        if event_type == "round":
            self._handle_round_event(payload)
            return
        if not instance_id:
            return
        if event_type == "error":
            message = payload.get("message") or payload.get("error") or payload
            self._append_log(f"runtime error [{instance_id}]: {message}")
            self._runtime_status[instance_id] = "异常"
            self._runtime_errors[instance_id] = str(message)
            if "enter room" in str(message).lower() or "进房" in str(message):
                self._room_entry_details[instance_id] = f"失败：{message}"
            return
        if event_type == "round":
            elapsed = payload.get("elapsed_ms", "-")
            delay = payload.get("delay_ms", "-")
            missing = 0
            for result in payload.get("results", []) if isinstance(payload.get("results"), list) else []:
                missing += int(result.get("missing_amount") or 0)
            self._append_log(f"对冲轮次完成: 间隔={delay}ms 耗时={elapsed}ms 缺口={missing}")
            return
        if event_type not in {"state", "health"}:
            return

        if event_type == "state":
            payload = _preserve_ui_countdown_anchor(self._runtime_snapshots.get(instance_id, {}), dict(payload))
            self._runtime_snapshots[instance_id] = dict(payload)
            self._runtime_errors.pop(instance_id, None)
            self._runtime_status[instance_id] = "状态已更新"
            safe_summary = _safe_summary_from(payload)
            if _payload_game_ready(payload, safe_summary):
                self._runtime_status[instance_id] = "房间已打开"
                self._room_entry_requested.setdefault(instance_id, self._room_index_from_payload(payload))
                room_label = _runtime_room_label_from_summary(safe_summary)
                if room_label:
                    self._room_entry_details[instance_id] = f"进房完成：{room_label}"
                else:
                    self._room_entry_details[instance_id] = "进房完成"
                self._auto_reentry_cooldowns_ms.pop(instance_id, None)
            elif _payload_hall_without_game(payload, safe_summary):
                self._runtime_status[instance_id] = "已回大厅"
                self._maybe_auto_reenter_room(instance_id, payload)

        game_launch_context = str(payload.get("game_launch_context") or "")
        room_entry = str(payload.get("room_entry") or "")
        if game_launch_context:
            self._runtime_status[instance_id] = {
                "headless_ready": "大厅就绪",
                "headless_released": "未接管",
                "headless_launching": "启动中",
                "document_opened": "已打开目标页",
            }.get(game_launch_context, self._runtime_status.get(instance_id, "待命"))
            self._append_log(
                f"runtime[{instance_id}] launch_status={game_launch_context} "
                f"ready={payload.get('game_ready', '')}"
            )
        if room_entry:
            self._runtime_status[instance_id] = {
                "preparing": "准备进房",
                "entering": "进房中",
                "click_confirmed": "确认进房",
                "game_ready": "房间已打开",
                "timeout": "进房超时",
            }.get(room_entry, self._runtime_status.get(instance_id, "待命"))
            self._room_entry_details[instance_id] = self._room_entry_detail(room_entry, payload)
            if room_entry in {"preparing", "entering", "click_confirmed", "game_ready"}:
                self._room_entry_requested.setdefault(instance_id, self._room_index_from_payload(payload))
            if room_entry == "game_ready":
                self._auto_reentry_cooldowns_ms.pop(instance_id, None)

    def _room_entry_detail(self, room_entry: str, payload: dict[str, Any]) -> str:
        confirmation = payload.get("click_confirmation")
        if not isinstance(confirmation, dict):
            confirmation = {}
        reason = str(confirmation.get("reason") or payload.get("reason") or "")
        ms = confirmation.get("total_ms", confirmation.get("ms_after_click", ""))
        suffix = f" {ms}ms" if str(ms).strip() else ""
        if room_entry == "preparing":
            return "准备大厅和房间按钮"
        if room_entry == "entering":
            if reason:
                return f"已点击，等待确认：{reason}{suffix}"
            return "已发送进房点击，等待平台响应"
        if room_entry == "click_confirmed":
            if reason:
                return f"点击已确认，加载房间：{reason}{suffix}"
            return "点击已确认，正在加载房间"
        if room_entry == "game_ready":
            return "进房完成"
        if room_entry == "timeout":
            debug_path = str(payload.get("debug_path") or "")
            return f"进房超时{('，已留截图' if debug_path else '')}"
        return ""

    def _room_index_from_payload(self, payload: dict[str, Any]) -> int:
        safe_summary = _safe_summary_from(payload)
        room_index = _as_int(payload.get("room_index")) or _as_int(safe_summary.get("room_entry_expected_room_index"))
        if room_index is not None and room_index >= 1:
            return room_index
        return max(1, int(self.config.room_index or 1))

    def _maybe_auto_reenter_room(self, account_id: str, payload: dict[str, Any]) -> None:
        if not self._auto_reentry_enabled:
            return
        room_index = int(self._room_entry_requested.get(account_id) or self._room_index_from_payload(payload))
        if room_index < 1:
            return
        current_ms = _now_ms()
        if current_ms < int(self._auto_reentry_cooldowns_ms.get(account_id, 0) or 0):
            return
        self._auto_reentry_cooldowns_ms[account_id] = current_ms + AUTO_REENTRY_COOLDOWN_MS
        self._room_entry_requested[account_id] = room_index
        self._append_log(f"自动回房: {account_id} 检测到大厅，重进 {room_index} 房")
        self.adapter.enter_room([account_id], room_index=room_index)
        self._runtime_status[account_id] = "自动回房中"

    def shutdown_runtime(self) -> None:
        self.adapter.shutdown()
