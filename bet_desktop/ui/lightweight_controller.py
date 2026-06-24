"""Controller bridge for the lightweight hedge UI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
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
    AccountStatusSummary,
    ACCOUNT_IDS,
    ExecutionConfig,
    PlatformSlot,
    resolve_main_account,
    resolve_sub_accounts,
)


def _now_ms() -> int:
    return int(time() * 1000)


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

        snapshot = self._load_snapshot()
        self.platform_slots = list(snapshot.platform_slots)
        self.config = snapshot.execution_config
        self.execution_state = "idle"
        self.main_account = resolve_main_account(self.config.main_account)
        self.config = replace(self.config, main_account=self.main_account)
        self._account_modes = {account_id: "idle" for account_id in ACCOUNT_IDS}
        self._update_platform_roles()
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

    def _emit_account_status(self) -> None:
        self._emit("account_status_updated", self.account_status)

    def _emit_platform_summary(self) -> None:
        self._emit("platform_summary_updated", self.platform_slots)

    def _emit_error(self, message: str) -> None:
        self._emit("error_banner_updated", message)

    def _update_platform_roles(self) -> None:
        self.sub_account_ids = resolve_sub_accounts(self.main_account)

    def _normalize_account_ids(self, account_ids: list[str]) -> list[str]:
        return [account_id for account_id in account_ids if account_id in ACCOUNT_IDS]

    def get_snapshot(self) -> LightweightConfigSnapshot:
        return LightweightConfigSnapshot(
            platform_slots=tuple(self.platform_slots),
            execution_config=self.config,
            raw_payload={},
        )

    def _build_account_statuses(self) -> list[AccountStatusSummary]:
        statuses: list[AccountStatusSummary] = []
        state_labels = {
            "idle": "待命",
            "running": "运行中",
            "paused": "已暂停",
            "stopped": "已停止",
        }
        for slot in self.platform_slots:
            role = "main" if slot.account_id == self.main_account else "sub"
            account_mode = self._account_modes.get(slot.account_id, "idle")
            mode = "stopped" if account_mode == "idle" else account_mode
            statuses.append(
                AccountStatusSummary(
                    account_id=slot.account_id,
                    display_name=slot.display_name or slot.account_id,
                    mode=mode,
                    role=role,
                    room_label=slot.target_room or "-",
                    round_id="-",
                    countdown=None,
                    betting_open=False,
                    balance=None,
                    pending_amount=None,
                    state_label=state_labels.get(account_mode, "未知"),
                    updated_at_ms=_now_ms(),
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
        self._append_log(f"主号切换为 {account_id}")
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
        self._emit_platform_summary()
        self._emit_account_status()

    def save_config(self) -> None:
        snapshot = LightweightConfigSnapshot(
            platform_slots=tuple(self.platform_slots),
            execution_config=self.config,
            raw_payload=self.config_store.load().raw_payload,
        )
        self.config_store.save(snapshot)
        self._append_log("配置已保存")

    def start_clicked(self) -> None:
        self.execution_state = "running"
        self._append_log("对冲系统已启动")
        self._emit("gate_status_updated", "运行中")
        self._emit_account_status()

    def pause_clicked(self) -> None:
        self.execution_state = "paused"
        self._append_log("运行已暂停")
        self._emit("gate_status_updated", "已暂停")
        self._emit_account_status()

    def stop_clicked(self) -> None:
        self.execution_state = "stopped"
        self._append_log("运行已停止")
        self.adapter.stop_accounts(self.platform_slots_account_ids())
        for account_id in self.platform_slots_account_ids():
            self._account_modes[account_id] = "stopped"
        self._emit("gate_status_updated", "已停止")
        self._emit_account_status()

    def batch_start_clicked(self) -> None:
        self.start_batch(self.platform_slots_account_ids())

    def batch_fill_login_clicked(self) -> None:
        self._append_log("开始批量触发登录-一键填入")
        self.adapter.fill_login(self.platform_slots_account_ids())

    def fill_account_btn_clicked(self, account_id: str) -> None:
        if account_id not in self.platform_slots_account_ids():
            self._emit_error("不存在的账号")
            return
        self._append_log(f"账号 {account_id} 触发一键填入")
        self.adapter.fill_login([account_id])

    def batch_handoff_clicked(self) -> None:
        self._emit_error("headless 接管为待二次审核功能（未接入）")

    def batch_enter_room_clicked(self, room_index: int = 1) -> None:
        self._emit_error("进房流程为待二次审核功能（未接入）")

    def batch_release_clicked(self) -> None:
        self._emit_error("释放流程为待二次审核功能（未接入）")

    def batch_stop_clicked(self) -> None:
        self.stop_clicked()

    def test_one_round_clicked(self) -> None:
        self._append_log("单次测试仅做摘要模拟，不会下发真实执行")

    def test_ten_rounds_clicked(self) -> None:
        self._append_log("十局模拟测试仅做摘要模拟，不会下发真实执行")

    def start_batch(self, account_ids: list[str] | None = None) -> None:
        targets = self._normalize_account_ids(account_ids or self.platform_slots_account_ids())
        if not targets:
            self._emit_error("无可启动账号")
            return
        self.execution_state = "running"
        for account_id in targets:
            self._account_modes[account_id] = "running"
        self._append_log(f"启动批量控制: {','.join(targets)}")
        self.adapter.start_accounts(targets)
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
            amount_min=int(updates.get("amount_min", self.config.amount_min)),
            amount_max=int(updates.get("amount_max", self.config.amount_max)),
            click_interval_ms=int(updates.get("click_interval_ms", self.config.click_interval_ms)),
            min_countdown=int(updates.get("min_countdown", self.config.min_countdown)),
            confirm_ms=int(updates.get("confirm_ms", self.config.confirm_ms)),
            room_index=int(updates.get("room_index", self.config.room_index)),
            extra={**self.config.extra, **{k: v for k, v in updates.items() if k not in {
                "amount_min",
                "amount_max",
                "click_interval_ms",
                "min_countdown",
                "confirm_ms",
                "room_index",
            }}},
        )
        self._append_log("执行参数已更新")
