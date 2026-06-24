from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from bet_desktop.ui.lightweight_browser_adapter import FakeBrowserControlAdapter
from bet_desktop.ui.lightweight_config_store import LightweightConfigStore
from bet_desktop.ui.lightweight_cluster_adapter import LightweightClusterAdapter, normalize_login_url, platform_slot_to_cluster_config
from bet_desktop.ui.lightweight_controller import LightweightController, _now_ms
from bet_desktop.ui.lightweight_dashboard import LightweightDashboard
from bet_desktop.ui.lightweight_models import ACCOUNT_IDS, PlatformSlot, parse_proxy_bundle_lines, resolve_sub_accounts


def test_lightweight_config_round_trip_and_legacy_adapter(tmp_path: Path) -> None:
    config_path = tmp_path / "platform_proxy_profiles.json"
    legacy_payload = {
        "platform_slots": {
            "a1": {"name": "p1", "proxy_expires": "2026-07-01", "account_username": "u1", "account_password": "p1"},
            "a2": {"name": "p2", "proxy_expires": "2026-07-02", "account_username": "u2", "account_password": "p2"},
            "a3": {"name": "p3", "proxy_expires": "2026-07-03", "account_username": "u3", "account_password": "p3"},
            "a4": {"name": "p4", "proxy_expires": "2026-07-04", "account_username": "u4", "account_password": "p4"},
        },
        "lightweight_ui": {
            "main_account": "a2",
            "amount_min": 5,
            "amount_max": 25,
            "click_interval_ms": 111,
            "min_countdown": 6,
            "confirm_ms": 777,
            "room_index": 2,
            "custom_note": "保留字段",
        },
    }
    config_path.write_text(json.dumps(legacy_payload, ensure_ascii=False), encoding="utf-8")

    store = LightweightConfigStore(config_path)
    snapshot = store.load()
    assert len(snapshot.platform_slots) == 4
    assert snapshot.platform_slots[0].proxy_expire_at == "2026-07-01"
    assert snapshot.platform_slots[1].display_name == "p2"
    assert snapshot.execution_config.main_account == "a2"
    assert snapshot.execution_config.amount_min == 5
    assert snapshot.execution_config.extra["custom_note"] == "保留字段"

    updated = replace(snapshot.execution_config, main_account="a3", amount_min=7)
    updated_payload = json.loads(json.dumps({**snapshot.execution_config.extra}))  # ensure serializable
    snapshot = snapshot.__class__(
        platform_slots=snapshot.platform_slots,
        execution_config=replace(updated, extra={**updated_payload}),
        raw_payload=snapshot.raw_payload,
    )
    store.save(snapshot)
    loaded = store.load()
    assert loaded.execution_config.main_account == "a3"
    assert loaded.execution_config.amount_min == 7


def test_main_account_sub_account_calculation() -> None:
    assert resolve_sub_accounts("a1") == ("a2", "a3", "a4")
    assert resolve_sub_accounts("a2") == ("a1", "a3", "a4")
    assert resolve_sub_accounts("a3") == ("a1", "a2", "a4")


def test_proxy_bundle_lines_split_by_account_order() -> None:
    parsed = parse_proxy_bundle_lines(
        "\n".join(
            [
                "117.68.75.165|8578|revf18h1|IVSrG6hd|2026-07-18",
                "117.68.75.166|8579|revf18h2|IVSrG6he|2026-07-19",
            ]
        )
    )
    assert parsed["a1"]["proxy_host"] == "117.68.75.165"
    assert parsed["a1"]["proxy_port"] == "8578"
    assert parsed["a1"]["proxy_username"] == "revf18h1"
    assert parsed["a1"]["proxy_password"] == "IVSrG6hd"
    assert parsed["a1"]["proxy_expire_at"] == "2026-07-18"
    assert parsed["a2"]["proxy_host"] == "117.68.75.166"
    assert parsed["a2"]["proxy_username"] == "revf18h2"
    assert "a3" not in parsed


def test_proxy_bundle_saved_as_split_fields_in_controller(tmp_path: Path) -> None:
    controller = LightweightController(
        config_store=LightweightConfigStore(tmp_path / "lightweight.json"),
        adapter=FakeBrowserControlAdapter(max_log_entries=20),
    )
    controller.update_platform_slot(
        "a1",
        {
            "proxy_bundle": "117.68.75.165|8578|revf18h1|IVSrG6hd|2026-07-18",
            "proxy_host": "117.68.75.165",
            "proxy_port": "8578",
            "proxy_username": "revf18h1",
            "proxy_password": "IVSrG6hd",
            "proxy_expire_at": "2026-07-18",
        },
    )
    controller.save_config()

    saved = LightweightConfigStore(tmp_path / "lightweight.json").load()
    slot = next(item for item in saved.platform_slots if item.account_id == "a1")
    assert slot.proxy_bundle == "117.68.75.165|8578|revf18h1|IVSrG6hd|2026-07-18"
    assert slot.proxy_host == "117.68.75.165"
    assert slot.proxy_port == "8578"
    assert slot.proxy_username == "revf18h1"
    assert slot.proxy_password == "IVSrG6hd"
    assert slot.proxy_expire_at == "2026-07-18"


def test_dashboard_open_login_syncs_current_form_fields(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    adapter = FakeBrowserControlAdapter(max_log_entries=20)
    controller = LightweightController(
        config_store=LightweightConfigStore(tmp_path / "lightweight.json"),
        adapter=adapter,
    )
    dashboard = LightweightDashboard(controller=controller)

    dashboard.slot_cards["a1"]["login_url"].setText("72991.com")
    dashboard.slot_cards["a1"]["account_username"].setText("xy111222")
    dashboard.slot_cards["a1"]["account_password"].setText("Xy888999")
    dashboard.slot_cards["a1"]["proxy_bundle"].setText("125.75.69.116|9198|cjls11b1|SHAsqzgN|2026-07-11")

    dashboard._on_open_login_pages()

    slot = next(item for item in controller.platform_slots if item.account_id == "a1")
    assert slot.login_url == "72991.com"
    assert slot.account_username == "xy111222"
    assert slot.proxy_host == "125.75.69.116"
    assert adapter.commands[-1] == "open_login_pages accounts=a1,a2,a3,a4"
    dashboard.close()
    app.processEvents()


def test_dashboard_enter_room_uses_selected_room(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    adapter = FakeBrowserControlAdapter(max_log_entries=20)
    controller = LightweightController(
        config_store=LightweightConfigStore(tmp_path / "lightweight.json"),
        adapter=adapter,
    )
    dashboard = LightweightDashboard(controller=controller)

    dashboard._set_room_index(3, log=False)
    dashboard._on_enter_room()

    assert controller.config.room_index == 3
    assert adapter.commands[-1] == "enter_room room_index=3 accounts=a1,a3,a4"
    dashboard.close()
    app.processEvents()


def test_dashboard_close_shuts_down_runtime(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])

    class ShutdownTrackingAdapter(FakeBrowserControlAdapter):
        def __init__(self) -> None:
            super().__init__(max_log_entries=20)
            self.shutdown_count = 0

        def shutdown(self) -> None:
            self.shutdown_count += 1
            super().shutdown()

    adapter = ShutdownTrackingAdapter()
    controller = LightweightController(
        config_store=LightweightConfigStore(tmp_path / "lightweight.json"),
        adapter=adapter,
    )
    dashboard = LightweightDashboard(controller=controller)
    dashboard.show()
    app.processEvents()

    dashboard.close()
    app.processEvents()

    assert adapter.shutdown_count >= 1
    assert dashboard._runtime_poll_timer.isActive() is False


def test_fake_adapter_command_logging_and_limit() -> None:
    adapter = FakeBrowserControlAdapter(max_log_entries=10)
    adapter.start_accounts(["a1", "a2"])
    adapter.fill_login(["a3"])
    adapter.stop_accounts(["a4"])
    assert len(adapter.commands) == 3
    assert adapter.commands[0].startswith("start_accounts")
    assert adapter.commands[1].startswith("fill_login")
    assert adapter.commands[2].startswith("stop_accounts")
    assert any("[fake:start_accounts] accounts=a1,a2" in line for line in adapter.log_lines)

    limited_adapter = FakeBrowserControlAdapter(max_log_entries=3)
    limited_adapter.start_accounts(["a1"])
    limited_adapter.fill_login(["a2"])
    limited_adapter.stop_accounts(["a3"])
    assert len(limited_adapter.log_lines) <= 3


def test_default_execution_config_matches_lightweight_probe_defaults(tmp_path: Path) -> None:
    snapshot = LightweightConfigStore(tmp_path / "missing.json").load()
    assert snapshot.execution_config.amount_min == 80
    assert snapshot.execution_config.amount_max == 150
    assert snapshot.execution_config.click_interval_ms == 200
    assert snapshot.execution_config.min_countdown == 10
    assert snapshot.execution_config.confirm_ms == 1200


def test_controller_start_trigger_and_status_change(tmp_path: Path) -> None:
    adapter = FakeBrowserControlAdapter(max_log_entries=20)
    controller = LightweightController(
        config_store=LightweightConfigStore(tmp_path / "lightweight.json"),
        adapter=adapter,
    )
    assert controller.config.main_account == "a2"
    assert controller.main_account in ACCOUNT_IDS
    assert controller.config.main_account == controller.main_account

    logs: list[str] = []
    controller.on("operator_log_appended", lambda message: logs.append(str(message)))

    controller.start_clicked()
    assert controller.execution_state == "running"
    assert any("启动轻量控制" in item for item in logs)
    assert not adapter.commands
    assert all(summary.state_label == "待命" for summary in controller.account_status)


def test_controller_start_does_not_start_accounts(tmp_path: Path) -> None:
    adapter = FakeBrowserControlAdapter(max_log_entries=20)
    controller = LightweightController(
        config_store=LightweightConfigStore(tmp_path / "lightweight.json"),
        adapter=adapter,
    )

    controller.start_clicked()
    assert adapter.commands == []


def test_qt_checked_false_uses_default_account_targets(tmp_path: Path) -> None:
    adapter = FakeBrowserControlAdapter(max_log_entries=20)
    controller = LightweightController(
        config_store=LightweightConfigStore(tmp_path / "lightweight.json"),
        adapter=adapter,
    )

    controller.open_login_pages_clicked(False)
    assert adapter.commands[-1] == "open_login_pages accounts=a1,a2,a3,a4"

    controller.fill_login_clicked(False)
    assert adapter.commands[-1] == "fill_login accounts=a1,a2,a3,a4"


def test_batch_handoff_targets_follow_main_account(tmp_path: Path) -> None:
    adapter = FakeBrowserControlAdapter(max_log_entries=20)
    controller = LightweightController(
        config_store=LightweightConfigStore(tmp_path / "lightweight.json"),
        adapter=adapter,
    )

    controller.set_main_account("a3")
    adapter.commands.clear()
    controller.batch_handoff_clicked()
    assert adapter.commands[-1] == "handoff_to_headless accounts=a1,a2,a4"

    controller.set_main_account("a2")
    adapter.commands.clear()
    controller.batch_handoff_clicked()
    assert adapter.commands[-1] == "handoff_to_headless accounts=a1,a3,a4"


def test_batch_enter_room_uses_config_room_index_for_button_click(tmp_path: Path) -> None:
    adapter = FakeBrowserControlAdapter(max_log_entries=20)
    controller = LightweightController(
        config_store=LightweightConfigStore(tmp_path / "lightweight.json"),
        adapter=adapter,
    )

    controller.apply_execution_config({"room_index": 4})
    controller.batch_enter_room_clicked(False)
    assert adapter.commands[-1] == "enter_room room_index=4 accounts=a1,a3,a4"

    controller.batch_enter_room_clicked(0)
    assert adapter.commands[-1] == "enter_room room_index=1 accounts=a1,a3,a4"


def test_runtime_state_event_updates_account_cards(tmp_path: Path) -> None:
    controller = LightweightController(
        config_store=LightweightConfigStore(tmp_path / "lightweight.json"),
        adapter=FakeBrowserControlAdapter(max_log_entries=20),
    )

    controller._handle_runtime_event(
        "a3",
        "state",
        {
            "batch_id": "202606250001",
            "exact_countdown": 12,
            "ocr_balance": "1,234.50",
            "timestamp_captured_ms": 1782350000000,
            "safe_summary": {
                "room_label": "1房",
                "runtime_betting_open": True,
                "runtime_phase_label": "下注中",
            },
        },
    )

    summary = next(item for item in controller.account_status if item.account_id == "a3")
    assert summary.room_label == "1房"
    assert summary.round_id == "202606250001"
    assert summary.countdown == 12
    assert str(summary.balance) == "1234.50"
    assert summary.state_label == "可下注"
    assert summary.state_machine_label == "下注中 · 可下注"


def test_runtime_state_countdown_decays_and_marks_stale(tmp_path: Path) -> None:
    controller = LightweightController(
        config_store=LightweightConfigStore(tmp_path / "lightweight.json"),
        adapter=FakeBrowserControlAdapter(max_log_entries=20),
    )

    controller._handle_runtime_event(
        "a2",
        "state",
        {
            "batch_id": "202606250002",
            "exact_countdown": 12,
            "timestamp_captured_ms": _now_ms() - 2200,
            "safe_summary": {
                "room_label": "1房",
                "runtime_betting_open": True,
                "runtime_phase_label": "下注中",
            },
        },
    )

    summary = next(item for item in controller.account_status if item.account_id == "a2")
    assert summary.countdown in {9, 10}
    assert summary.state_label == "可下注"
    assert summary.state_machine_label == "下注中 · 可下注"
    assert summary.stale is False

    controller._handle_runtime_event(
        "a2",
        "state",
        {
            "batch_id": "202606250002",
            "exact_countdown": 3,
            "timestamp_captured_ms": _now_ms() - 6500,
            "safe_summary": {
                "room_label": "1房",
                "runtime_betting_open": True,
                "runtime_phase_label": "下注中",
            },
        },
    )

    stale_summary = next(item for item in controller.account_status if item.account_id == "a2")
    assert stale_summary.countdown == 0
    assert stale_summary.state_label == "数据过期"
    assert stale_summary.state_machine_label.startswith("数据过期")
    assert stale_summary.betting_open is False
    assert stale_summary.stale is True


def test_runtime_state_auto_reenters_when_account_returns_to_hall(tmp_path: Path) -> None:
    adapter = FakeBrowserControlAdapter(max_log_entries=20)
    controller = LightweightController(
        config_store=LightweightConfigStore(tmp_path / "lightweight.json"),
        adapter=adapter,
    )

    controller.batch_enter_room_clicked(2)
    adapter._commands.clear()
    controller._handle_runtime_event(
        "a1",
        "state",
        {
            "timestamp_captured_ms": _now_ms(),
            "safe_summary": {
                "hall_ready": True,
                "game_ready": False,
                "room_entry_expected_room_index": 2,
            },
        },
    )

    assert adapter.commands == ["enter_room room_index=2 accounts=a1"]
    command_count = len(adapter.commands)
    controller._handle_runtime_event(
        "a1",
        "state",
        {
            "timestamp_captured_ms": _now_ms(),
            "safe_summary": {
                "hall_ready": True,
                "game_ready": False,
                "room_entry_expected_room_index": 2,
            },
        },
    )
    assert len(adapter.commands) == command_count
    assert any("自动回房" in item for item in controller.logs)


def test_runtime_room_evidence_overrides_hall_marker(tmp_path: Path) -> None:
    controller = LightweightController(
        config_store=LightweightConfigStore(tmp_path / "lightweight.json"),
        adapter=FakeBrowserControlAdapter(max_log_entries=20),
    )

    controller._handle_runtime_event(
        "a2",
        "state",
        {
            "batch_id": "",
            "exact_countdown": 6,
            "ocr_balance": "3165.29",
            "timestamp_captured_ms": _now_ms(),
            "safe_summary": {
                "hall_ready": True,
                "game_ready": False,
                "runtime_room_label": "T001",
                "label_internal_game_no": "50-1782342022-8540540455-1253",
                "runtime_phase_label": "派奖中",
            },
        },
    )

    summary = next(item for item in controller.account_status if item.account_id == "a2")
    assert summary.room_label == "T001"
    assert summary.round_id == "50-1782342022-8540540455-1253"
    assert summary.state_label != "大厅"
    assert "派奖中" in summary.state_machine_label


def test_refresh_headless_defaults_to_sub_accounts(tmp_path: Path) -> None:
    adapter = FakeBrowserControlAdapter(max_log_entries=20)
    controller = LightweightController(
        config_store=LightweightConfigStore(tmp_path / "lightweight.json"),
        adapter=adapter,
    )

    controller.set_main_account("a3")
    controller.refresh_headless_clicked()
    assert adapter.commands[-1] == "refresh_headless accounts=a1,a2,a4"

    controller.refresh_headless_clicked(["a2"])
    assert adapter.commands[-1] == "refresh_headless accounts=a2"


def test_platform_slot_to_cluster_config_mapping() -> None:
    slot = PlatformSlot(
        account_id="a2",
        login_url="https://login.example.com",
        account_username="u2",
        account_password="p2",
        target_url="https://target.example.com",
        proxy_host="127.0.0.1",
        proxy_port="8080",
        proxy_username="px",
        proxy_password="pxpwd",
        proxy_expire_at="2026-12-31",
    )
    config = platform_slot_to_cluster_config(slot)

    assert config.instance_id == "a2"
    assert config.login_url == "https://login.example.com"
    assert config.target_url == ""
    assert config.username == "u2"
    assert config.password == "p2"
    assert config.proxy == {
        "host": "127.0.0.1",
        "port": "8080",
        "username": "px",
        "password": "pxpwd",
    }
    assert config.headless is False
    assert config.browser_channel == "chrome"
    assert config.viewport_width == 960
    assert config.viewport_height == 620
    assert config.runtime_shadow_interval_ms >= 1000

    probe_config = platform_slot_to_cluster_config(
        slot,
        enable_frontend_probe=True,
        enable_canvas_probe=True,
        enable_runtime_scan=True,
    )
    assert probe_config.enable_frontend_probe is True
    assert probe_config.enable_canvas_probe is True
    assert probe_config.enable_runtime_scan is True


def test_normalize_login_url_adds_https_for_short_domains() -> None:
    assert normalize_login_url("72991.com") == "https://72991.com"
    assert normalize_login_url("https://72991.com") == "https://72991.com"
    assert normalize_login_url("") == ""


def test_cluster_adapter_command_order(monkeypatch) -> None:
    captured: list[object] = []

    class FakeWorkerController:
        def __init__(self, configs: list) -> None:
            self.configs = list(configs)
            self.actions: list[tuple[str, object]] = []

        def start(self) -> None:
            self.actions.append(("start", None))

        def start_configs(self, configs: list) -> None:
            self.actions.append(("start_configs", tuple(cfg.instance_id for cfg in configs)))

        def send_command(self, account_id: str, command: dict[str, object]) -> None:
            self.actions.append(("send", account_id, command.get("command"), dict(command)))

        def stop_instances(self, account_ids: list[str]) -> None:
            self.actions.append(("stop_instances", tuple(account_ids)))

        def running_instance_ids(self):
            return {cfg.instance_id for cfg in self.configs}

        def poll_events(self, max_items: int = 128):
            return []

        def stop(self) -> None:
            self.actions.append(("stop", None))

    def fake_factory(configs: list) -> FakeWorkerController:
        worker = FakeWorkerController(configs)
        captured.append(worker)
        return worker

    monkeypatch.setattr(
        "bet_desktop.ui.lightweight_cluster_adapter.ClusterProcessController",
        fake_factory,
    )

    adapter = LightweightClusterAdapter()
    slots = (
        PlatformSlot(account_id="a1", login_url="https://a1.local/login", account_username="u1", account_password="p1"),
        PlatformSlot(account_id="a2", login_url="https://a2.local/login", account_username="u2", account_password="p2"),
        PlatformSlot(account_id="a3", login_url="https://a3.local/login", account_username="u3", account_password="p3"),
    )
    adapter.refresh_runtime_environment(slots)

    adapter.open_login_pages(["a1", "a2"])
    adapter.fill_login(["a1", "a2"])
    adapter.handoff_to_headless(["a1"])
    adapter.enter_room(["a1"], 3)
    adapter.refresh_headless(["a1"])
    adapter.release_headless(["a1"])

    assert captured, "cluster factory should be created"
    assert captured[0].configs[0].enable_runtime_scan is True
    assert captured[0].configs[0].enable_frontend_probe is False
    assert captured[0].configs[0].enable_canvas_probe is False
    actions = captured[0].actions
    assert actions[0][0] == "start"
    assert actions[1] == ("send", "a1", "navigate", {"command": "navigate", "url": "https://a1.local/login"})
    assert actions[2] == ("send", "a2", "navigate", {"command": "navigate", "url": "https://a2.local/login"})
    assert actions[3] == ("send", "a1", "fill_login", {"command": "fill_login", "username": "u1", "password": "p1"})
    assert actions[4] == ("send", "a2", "fill_login", {"command": "fill_login", "username": "u2", "password": "p2"})
    assert actions[5] == ("send", "a1", "handoff_to_headless", {"command": "handoff_to_headless"})
    assert actions[6] == ("send", "a1", "enter_room", {"command": "enter_room", "room_index": 3})
    assert actions[7] == ("send", "a1", "capture_game_launch_context", {"command": "capture_game_launch_context"})
    assert actions[8] == ("send", "a1", "release_headless", {"command": "release_headless"})

    adapter.shutdown()
    assert actions[9] == ("stop_instances", ("a1", "a2"))


def test_cluster_adapter_enter_room_starts_missing_workers(monkeypatch) -> None:
    captured: list[object] = []

    class FakeWorkerController:
        def __init__(self, configs: list) -> None:
            self.configs = list(configs)
            self.actions: list[tuple[str, object]] = []

        def start(self) -> None:
            self.actions.append(("start", tuple(cfg.instance_id for cfg in self.configs)))

        def send_command(self, account_id: str, command: dict[str, object]) -> bool:
            self.actions.append(("send", account_id, command.get("command"), dict(command)))
            return True

        def running_instance_ids(self):
            return {cfg.instance_id for cfg in self.configs}

        def stop_instances(self, account_ids: list[str]) -> None:
            self.actions.append(("stop_instances", tuple(account_ids)))

        def poll_events(self, max_items: int = 128):
            return []

    def fake_factory(configs: list) -> FakeWorkerController:
        worker = FakeWorkerController(configs)
        captured.append(worker)
        return worker

    monkeypatch.setattr(
        "bet_desktop.ui.lightweight_cluster_adapter.ClusterProcessController",
        fake_factory,
    )

    adapter = LightweightClusterAdapter()
    adapter.refresh_runtime_environment(
        (
            PlatformSlot(account_id="a1", login_url="https://a1.local/login", account_username="u1", account_password="p1"),
            PlatformSlot(account_id="a3", login_url="https://a3.local/login", account_username="u3", account_password="p3"),
        )
    )

    code, _stdout, stderr = adapter.enter_room(["a1", "a3"], 2)

    assert code == 0, stderr
    assert captured
    assert captured[0].actions[0] == ("start", ("a1", "a3"))
    assert captured[0].actions[1] == ("send", "a1", "enter_room", {"command": "enter_room", "room_index": 2})
    assert captured[0].actions[2] == ("send", "a3", "enter_room", {"command": "enter_room", "room_index": 2})
