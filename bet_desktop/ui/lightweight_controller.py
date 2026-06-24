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
    ACCOUNT_IDS,
    AccountStatusSummary,
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
        self._account_modes = {account_id: "待命" for account_id in ACCOUNT_IDS}
        self._runtime_status = {account_id: "待命" for account_id in ACCOUNT_IDS}
        self._update_platform_roles()
        self.adapter.refresh_runtime_environment(self.platform_slots)
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
        for slot in self.platform_slots:
            role = "main" if slot.account_id == self.main_account else "sub"
            account_mode = self._account_modes.get(slot.account_id, "idle")
            runtime_status = self._runtime_status.get(slot.account_id, "待命")
            statuses.append(
                AccountStatusSummary(
                    account_id=slot.account_id,
                    display_name=slot.display_name or slot.account_id,
                    mode=runtime_status,
                    role=role,
                    room_label=slot.target_room or "-",
                    round_id="-",
                    countdown=None,
                    betting_open=False,
                    balance=None,
                    pending_amount=None,
                    state_label=runtime_status if runtime_status != "待命" else account_mode,
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
        self._append_log("启动轻量控制：系统总开关")
        self._emit("gate_status_updated", "运行中")
        self._emit_account_status()

    def pause_clicked(self) -> None:
        self.execution_state = "paused"
        self._append_log("系统暂停")
        self._emit("gate_status_updated", "暂停")
        self._emit_account_status()

    def stop_clicked(self) -> None:
        self.execution_state = "stopped"
        self._append_log("系统急停")
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
        self.adapter.enter_room(targets, room_index=resolved_room_index)
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
        self._mark_runtime_status(targets, "已释放")
        self._emit_account_status()

    def batch_stop_clicked(self) -> None:
        self.stop_clicked()

    def test_one_round_clicked(self) -> None:
        self._append_log("测试入口：单轮对冲暂未接入")

    def test_ten_rounds_clicked(self) -> None:
        self._append_log("测试入口：十轮对冲暂未接入")

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
            amount_min=int(updates.get("amount_min", self.config.amount_min)),
            amount_max=int(updates.get("amount_max", self.config.amount_max)),
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
                    "click_interval_ms",
                    "min_countdown",
                    "confirm_ms",
                    "room_index",
                }
            }},
        )
        self._append_log("执行参数更新")

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
        if not instance_id:
            return
        if event_type == "error":
            message = payload.get("message") or payload.get("error") or payload
            self._append_log(f"runtime error [{instance_id}]: {message}")
            self._runtime_status[instance_id] = "异常"
            return
        if event_type not in {"state", "health"}:
            return

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
                "entering": "进房中",
                "click_confirmed": "确认进房",
                "game_ready": "房间已打开",
                "timeout": "进房超时",
            }.get(room_entry, self._runtime_status.get(instance_id, "待命"))

    def shutdown_runtime(self) -> None:
        self.adapter.shutdown()
