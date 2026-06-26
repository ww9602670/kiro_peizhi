from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication

from scripts import live_interval_acceptance_probe as probe
from bet_desktop.ui.lightweight_browser_adapter import FakeBrowserControlAdapter
from bet_desktop.ui.lightweight_config_store import LightweightConfigStore
from bet_desktop.ui.lightweight_cluster_adapter import LightweightClusterAdapter, normalize_login_url, platform_slot_to_cluster_config
from bet_desktop.ui.lightweight_controller import _payload_hall_without_game, LightweightController, _now_ms
from bet_desktop.ui.lightweight_dashboard import LightweightDashboard
from bet_desktop.ui.lightweight_models import ACCOUNT_IDS, PlatformSlot, parse_proxy_bundle_lines, resolve_sub_accounts
from bet_desktop.ui.lightweight_probe_adapter import LightweightProbeAdapter, status_to_state_event


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
            "main_successor_account": "a4",
            "amount_min": 5,
            "amount_max": 25,
            "min_balance_yuan": 60,
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
    assert snapshot.execution_config.main_successor_account == "a4"
    assert snapshot.execution_config.amount_min == 5
    assert snapshot.execution_config.min_balance_yuan == 60
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
    assert loaded.platform_slots[0].display_name == "p1"
    assert loaded.platform_slots[1].display_name == "p2"


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


def test_dashboard_start_syncs_current_form_fields(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    adapter = FakeBrowserControlAdapter(max_log_entries=20)
    controller = LightweightController(
        config_store=LightweightConfigStore(tmp_path / "lightweight.json"),
        adapter=adapter,
    )
    dashboard = LightweightDashboard(controller=controller)

    dashboard.amount_min_input.setValue(120)
    dashboard.amount_max_input.setValue(180)
    dashboard.slot_cards["a1"]["account_username"].setText("hjgd00111")

    dashboard._on_start()

    assert controller.config.amount_min == 120
    assert controller.config.amount_max == 180
    assert next(item for item in controller.platform_slots if item.account_id == "a1").account_username == "hjgd00111"
    assert adapter.commands[-1] == "start_hedge"
    assert dashboard._run_started_at is not None
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


def test_dashboard_profit_uses_deposit_withdraw_corrections(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    adapter = FakeBrowserControlAdapter(max_log_entries=20)
    controller = LightweightController(
        config_store=LightweightConfigStore(tmp_path / "lightweight.json"),
        adapter=adapter,
    )
    dashboard = LightweightDashboard(controller=controller)

    dashboard._on_account_status_updated(
        [
            SimpleNamespace(account_id="a1", balance=Decimal("100")),
            SimpleNamespace(account_id="a2", balance=Decimal("0")),
            SimpleNamespace(account_id="a3", balance=Decimal("0")),
            SimpleNamespace(account_id="a4", balance=Decimal("0")),
        ]
    )

    row = dashboard.pnl_rows["a1"]
    assert row["initial"].text() == "初始 100.00"
    row["deposit_input"].setText("40")
    row["deposit_btn"].click()
    row["withdraw_input"].setText("10")
    row["withdraw_btn"].click()

    dashboard._on_account_status_updated([SimpleNamespace(account_id="a1", balance=Decimal("150"))])
    assert row["deposit"].text() == "累计充值 40.00"
    assert row["withdraw"].text() == "累计提现 10.00"
    assert row["profit"].text().endswith("+20.00")
    assert row["profit"].property("tone") == "positive"

    dashboard._on_account_status_updated([SimpleNamespace(account_id="a1", balance=Decimal("170"))])
    assert row["profit"].text().endswith("+40.00")
    assert row["profit"].property("tone") == "positive"

    dashboard._on_account_status_updated([SimpleNamespace(account_id="a1", balance=Decimal("80"))])
    assert row["profit"].text().endswith("-50.00")
    assert row["profit"].property("tone") == "negative"
    dashboard.close()
    app.processEvents()



def test_dashboard_profit_initial_balance_persists_after_restart(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    adapter = FakeBrowserControlAdapter(max_log_entries=20)
    controller = LightweightController(
        config_store=LightweightConfigStore(tmp_path / "lightweight.json"),
        adapter=adapter,
    )
    dashboard = LightweightDashboard(controller=controller)

    dashboard._on_account_status_updated(
        [
            SimpleNamespace(account_id="a1", balance=Decimal("100")),
            SimpleNamespace(account_id="a2", balance=Decimal("0")),
            SimpleNamespace(account_id="a3", balance=Decimal("0")),
            SimpleNamespace(account_id="a4", balance=Decimal("0")),
        ]
    )
    row = dashboard.pnl_rows["a1"]
    assert row["initial"].text() == "初始 100.00"
    assert row["profit"].text().endswith("+0.00")

    dashboard._on_start()
    dashboard.controller.pause_clicked()
    dashboard._on_account_status_updated(
        [
            SimpleNamespace(account_id="a1", balance=Decimal("120")),
            SimpleNamespace(account_id="a2", balance=Decimal("0")),
            SimpleNamespace(account_id="a3", balance=Decimal("0")),
            SimpleNamespace(account_id="a4", balance=Decimal("0")),
        ]
    )
    assert row["initial"].text() == "初始 100.00"
    dashboard._reset_profit_tracking()
    assert row["initial"].text() == "初始 100.00"

    dashboard.controller.stop_clicked()
    dashboard._on_account_status_updated(
        [
            SimpleNamespace(account_id="a1", balance=Decimal("130")),
            SimpleNamespace(account_id="a2", balance=Decimal("0")),
            SimpleNamespace(account_id="a3", balance=Decimal("0")),
            SimpleNamespace(account_id="a4", balance=Decimal("0")),
        ]
    )
    assert row["initial"].text() == "初始 100.00"

    dashboard._on_start()
    dashboard._on_account_status_updated(
        [
            SimpleNamespace(account_id="a1", balance=Decimal("160")),
            SimpleNamespace(account_id="a2", balance=Decimal("0")),
            SimpleNamespace(account_id="a3", balance=Decimal("0")),
            SimpleNamespace(account_id="a4", balance=Decimal("0")),
        ]
    )
    assert row["initial"].text() == "初始 100.00"
    assert row["profit"].text().endswith("+60.00")
    dashboard.close()
    app.processEvents()


def test_dashboard_turnover_uses_actual_amount_and_reset_only_clear_turnover(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    adapter = FakeBrowserControlAdapter(max_log_entries=20)
    controller = LightweightController(
        config_store=LightweightConfigStore(tmp_path / "lightweight.json"),
        adapter=adapter,
    )
    dashboard = LightweightDashboard(controller=controller)

    dashboard.controller._handle_runtime_event(
        "",
        "round",
        {
            "round_number": 1,
            "round_id": "50-9-8",
            "results": [
                {"instance_id": "a1", "actual_amount": 20},
                {"instance_id": "a2", "actual_amount": 30},
                {"instance_id": "a1", "amount": 999},
            ],
        },
    )
    dashboard._refresh_turnover_display(dashboard.controller.round_results)
    assert dashboard.turnover_labels["a1"]["value"].text() == "20"
    assert dashboard.turnover_labels["a2"]["value"].text() == "30"

    dashboard._on_reset_turnover()
    assert dashboard.turnover_labels["a1"]["value"].text() == "0"
    assert dashboard.turnover_labels["a2"]["value"].text() == "0"

    dashboard.controller._handle_runtime_event(
        "",
        "round",
        {
            "round_number": 2,
            "round_id": "50-9-8-1",
            "results": [
                {"instance_id": "a1", "actual_amount": 70},
                {"instance_id": "a2", "actual_amount": 10},
            ],
        },
    )
    dashboard._refresh_turnover_display(dashboard.controller.round_results)
    assert dashboard.turnover_labels["a1"]["value"].text() == "70"
    assert dashboard.turnover_labels["a2"]["value"].text() == "10"

    dashboard._on_account_status_updated([SimpleNamespace(account_id="a1", balance=Decimal("100")), SimpleNamespace(account_id="a2", balance=Decimal("200")), SimpleNamespace(account_id="a3", balance=Decimal("300")), SimpleNamespace(account_id="a4", balance=Decimal("400"))]
    )
    assert dashboard.pnl_rows["a1"]["initial"].text() == "初始 100.00"
    dashboard.close()
    app.processEvents()


def test_dashboard_plan_exclude_restore_buttons_are_ui_only(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    controller = LightweightController(
        config_store=LightweightConfigStore(tmp_path / "lightweight.json"),
        adapter=FakeBrowserControlAdapter(max_log_entries=20),
    )
    dashboard = LightweightDashboard(controller=controller)

    dashboard._on_plan_state_action("a1")
    assert dashboard._plan_account_states["a1"] == "pending_exclude"
    assert dashboard.plan_rows["a1"]["state"].text() == "待剔除"
    assert dashboard.plan_rows["a1"]["action"].text() == "取消剔除"

    dashboard._on_hedge_plan_updated({"round_number": 1, "legs": []})
    assert dashboard._plan_account_states["a1"] == "excluded"
    assert dashboard.plan_rows["a1"]["state"].text() == "已剔除"
    assert dashboard.plan_rows["a1"]["action"].text() == "下局恢复"
    assert controller.current_plan == {}

    dashboard._on_plan_state_action("a1")
    assert dashboard._plan_account_states["a1"] == "pending_restore"
    assert dashboard.plan_rows["a1"]["state"].text() == "待恢复"
    assert dashboard.plan_rows["a1"]["action"].text() == "取消恢复"

    dashboard._on_hedge_plan_updated({"round_number": 2, "legs": []})
    assert dashboard._plan_account_states["a1"] == "normal"
    assert dashboard.plan_rows["a1"]["state"].text() == "正常"
    assert dashboard.plan_rows["a1"]["action"].text() == "下局剔除"

    dashboard.close()
    app.processEvents()


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


def test_probe_status_maps_to_ui_state_payload() -> None:
    event = status_to_state_event(
        "a2",
        {
            "game_ready": True,
            "hall_ready": False,
            "game_no": "50-1782342022-8540540455-1253",
            "countdown": 9,
            "betting_open": True,
            "room_label": "T001",
            "balance_cents": 316529,
            "guard_phase": "betting_open",
            "status_ts_ms": 123456,
        },
    )

    payload = event["payload"]
    safe_summary = payload["safe_summary"]
    assert event["event_type"] == "state"
    assert event["instance_id"] == "a2"
    assert payload["batch_id"] == "50-1782342022-8540540455"
    assert payload["exact_countdown"] == 12
    assert payload["backend_countdown"] == 9
    assert payload["ocr_balance"] == "3165.29"
    assert safe_summary["runtime_room_label"] == "T001"
    assert safe_summary["runtime_betting_open"] is True
    assert safe_summary["ui_reference_countdown"] is True


def test_probe_status_to_state_event_prefers_display_fields_and_runtime_action() -> None:
    event = status_to_state_event(
        "a2",
        {
            "hall_ready": True,
            "game_ready": False,
            "game_no": "legacy-game-no",
            "display_game_no": "50-1782342022-8540540455-1253",
            "display_room_label": "T001",
            "display_balance_cents": 316529,
            "runtime_coordinates": {"bet_regions": [1, 2]},
            "display_phase": "betting_open",
            "display_source": "canvas_game_no+runtime_action",
            "runtime_action": "3",
            "frontend_runtime_action": "5",
            "status_ts_ms": 1000,
            "countdown": 8,
        },
    )

    payload = event["payload"]
    safe_summary = payload["safe_summary"]
    assert event["instance_id"] == "a2"
    assert payload["batch_id"] == "50-1782342022-8540540455"
    assert payload["exact_countdown"] == 12
    assert payload["backend_countdown"] == 8
    assert safe_summary["display_game_no"] == "50-1782342022-8540540455"
    assert safe_summary["display_room_label"] == "T001"
    assert safe_summary["display_phase"] == "betting_open"
    assert safe_summary["display_source"] == "canvas_game_no+runtime_action"
    assert safe_summary["display_betting_open"] is True
    assert safe_summary["runtime_coordinates"] == {"bet_regions": [1, 2]}
    assert safe_summary["runtime_phase"] == "betting_open"


def test_display_betting_closed_when_runtime_action_is_5() -> None:
    event = status_to_state_event(
        "a1",
        {
            "game_no": "2026-xx-yy",
            "runtime_action": "5",
            "hall_ready": False,
            "game_ready": False,
            "status_ts_ms": 2000,
            "countdown": 10,
        },
    )

    assert event["payload"]["batch_id"] == "2026-xx-yy"
    assert event["payload"]["exact_countdown"] is None
    assert event["payload"]["safe_summary"]["display_betting_open"] is False


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
    assert len(logs) > 0
    assert adapter.commands == ["start_hedge"]
    assert all(summary.state_label is not None for summary in controller.account_status)


def test_controller_start_uses_hedge_loop_not_account_start(tmp_path: Path) -> None:
    adapter = FakeBrowserControlAdapter(max_log_entries=20)
    controller = LightweightController(
        config_store=LightweightConfigStore(tmp_path / "lightweight.json"),
        adapter=adapter,
    )

    controller.start_clicked()
    assert adapter.commands == ["start_hedge"]


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


def test_single_account_fill_login_routes_every_account(tmp_path: Path) -> None:
    adapter = FakeBrowserControlAdapter(max_log_entries=20)
    controller = LightweightController(
        config_store=LightweightConfigStore(tmp_path / "lightweight.json"),
        adapter=adapter,
    )

    for account_id in ("a1", "a2", "a3", "a4"):
        adapter.commands.clear()
        controller.fill_account_btn_clicked(account_id)
        assert adapter.commands[-1] == f"fill_login accounts={account_id}"


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


def test_enter_room_all_includes_main_account(tmp_path: Path) -> None:
    adapter = FakeBrowserControlAdapter(max_log_entries=20)
    controller = LightweightController(
        config_store=LightweightConfigStore(tmp_path / "lightweight.json"),
        adapter=adapter,
    )

    controller.set_main_account("a3")
    controller.enter_room_all_clicked(2)

    assert adapter.commands[-1] == "enter_room room_index=2 accounts=a1,a2,a3,a4"


def test_room_entry_progress_is_exposed_on_account_status(tmp_path: Path) -> None:
    controller = LightweightController(
        config_store=LightweightConfigStore(tmp_path / "lightweight.json"),
        adapter=FakeBrowserControlAdapter(max_log_entries=20),
    )

    controller.batch_enter_room_clicked(2)
    controller._handle_runtime_event("a1", "health", {"room_entry": "preparing", "room_index": 2})
    summary = next(item for item in controller.account_status if item.account_id == "a1")

    assert summary.target_room_label.startswith("2")
    assert summary.room_entry_detail


def test_room_entry_loading_detail_is_cleared_after_room_state(tmp_path: Path) -> None:
    controller = LightweightController(
        config_store=LightweightConfigStore(tmp_path / "lightweight.json"),
        adapter=FakeBrowserControlAdapter(max_log_entries=20),
    )

    controller._handle_runtime_event(
        "a1",
        "health",
        {
            "room_entry": "click_confirmed",
            "room_index": 1,
            "click_confirmation": {"reason": "visual_loading", "elapsed_ms": 43},
        },
    )
    controller._handle_runtime_event(
        "a1",
        "state",
        {"safe_summary": {"display_room_label": "T001", "round_id": "50-1234"}},
    )
    summary = next(item for item in controller.account_status if item.account_id == "a1")

    assert summary.room_entry_detail.startswith("进房完成")
    assert "visual_loading" not in summary.room_entry_detail


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
            "timestamp_captured_ms": _now_ms(),
            "safe_summary": {
                "room_label": "1",
                "runtime_betting_open": True,
                "runtime_phase_label": "betting_open",
            },
        },
    )

    summary = next(item for item in controller.account_status if item.account_id == "a3")
    assert summary.room_label == "1"
    assert summary.round_id == "202606250001"
    assert summary.countdown == 12
    assert str(summary.balance) == "1234.50"
    assert summary.state_label != ""
    assert "betting_open" in summary.state_machine_label


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
                "room_label": "1",
                "runtime_betting_open": True,
                "runtime_phase_label": "betting_open",
            },
        },
    )

    summary = next(item for item in controller.account_status if item.account_id == "a2")
    assert summary.countdown in {9, 10}
    assert summary.state_label != ""
    assert "betting_open" in summary.state_machine_label
    assert summary.stale is False

    controller._handle_runtime_event(
        "a2",
        "state",
        {
            "batch_id": "202606250002",
            "exact_countdown": 3,
                "timestamp_captured_ms": _now_ms() - 16500,
            "safe_summary": {
                "room_label": "1",
                "runtime_betting_open": True,
                "runtime_phase_label": "settling",
            },
        },
    )

    stale_summary = next(item for item in controller.account_status if item.account_id == "a2")
    assert stale_summary.countdown == 0
    assert stale_summary.state_label == "数据过期"
    assert stale_summary.state_machine_label.startswith("数据过期")
    assert stale_summary.betting_open is False
    assert stale_summary.stale is True


def test_ui_reference_countdown_does_not_reset_on_same_round_refresh(tmp_path: Path) -> None:
    controller = LightweightController(
        config_store=LightweightConfigStore(tmp_path / "lightweight.json"),
        adapter=FakeBrowserControlAdapter(max_log_entries=20),
    )
    started_ms = _now_ms() - 3200

    controller._handle_runtime_event(
        "a2",
        "state",
        {
            "batch_id": "202606250099",
            "exact_countdown": 12,
            "ui_countdown_started_ms": started_ms,
            "timestamp_captured_ms": started_ms,
            "safe_summary": {
                "room_label": "T001",
                "runtime_betting_open": True,
                "ui_reference_countdown": True,
                "ui_countdown_started_ms": started_ms,
            },
        },
    )
    first = next(item for item in controller.account_status if item.account_id == "a2")

    refreshed_ms = _now_ms()
    controller._handle_runtime_event(
        "a2",
        "state",
        {
            "batch_id": "202606250099",
            "exact_countdown": 12,
            "ui_countdown_started_ms": refreshed_ms,
            "timestamp_captured_ms": refreshed_ms,
            "safe_summary": {
                "room_label": "T001",
                "runtime_betting_open": True,
                "ui_reference_countdown": True,
                "ui_countdown_started_ms": refreshed_ms,
            },
        },
    )
    second = next(item for item in controller.account_status if item.account_id == "a2")

    assert first.countdown is not None and first.countdown < 12
    assert second.countdown is not None and second.countdown <= first.countdown
    assert controller._runtime_snapshots["a2"]["ui_countdown_started_ms"] == started_ms


def test_ui_reference_countdown_closes_betting_display_at_zero(tmp_path: Path) -> None:
    controller = LightweightController(
        config_store=LightweightConfigStore(tmp_path / "lightweight.json"),
        adapter=FakeBrowserControlAdapter(max_log_entries=20),
    )
    started_ms = _now_ms() - 13_000

    controller._handle_runtime_event(
        "a2",
        "state",
        {
            "batch_id": "202606250100",
            "exact_countdown": 12,
            "ui_countdown_started_ms": started_ms,
            "timestamp_captured_ms": _now_ms(),
            "safe_summary": {
                "room_label": "T001",
                "runtime_betting_open": True,
                "ui_reference_countdown": True,
                "ui_countdown_started_ms": started_ms,
            },
        },
    )

    summary = next(item for item in controller.account_status if item.account_id == "a2")
    assert summary.countdown == 0
    assert summary.betting_open is False
    assert summary.stale is False


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
    assert any("enter_room room_index" in item for item in adapter.commands)


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
                "runtime_phase_label": "settling",
            },
        },
    )

    summary = next(item for item in controller.account_status if item.account_id == "a2")
    assert summary.room_label == "T001"
    assert summary.round_id == "50-1782342022-8540540455"
    assert summary.state_label != "澶у巺"
    assert "settling" in summary.state_machine_label



def test_hall_ready_with_runtime_coordinates_is_not_hall(tmp_path: Path) -> None:
    payload = {
        "timestamp_captured_ms": _now_ms(),
        "safe_summary": {
            "hall_ready": True,
            "game_ready": False,
            "runtime_coordinates": {
                "bet_regions": [1, 2, 3],
                "chips": [100],
            },
        },
    }

    assert _payload_hall_without_game(payload, payload["safe_summary"]) is False

    controller = LightweightController(
        config_store=LightweightConfigStore(tmp_path / "lightweight.json"),
        adapter=FakeBrowserControlAdapter(max_log_entries=20),
    )
    controller._handle_runtime_event("a2", "state", payload)
    summary = next(item for item in controller.account_status if item.account_id == "a2")
    assert summary.state_label != "澶у巺"

def test_probe_status_uses_lightweight_room_evidence_for_bet_gate() -> None:
    class FakeState:
        def snapshot(self):
            return SimpleNamespace(
                exact_countdown=11,
                safe_summary={
                    "canvas_game_no": "50-1782377104-8541781943-1286",
                    "runtime_room_label": "T001",
                    "runtime_room_id": "182020001",
                    "runtime_action": "3",
                    "runtime_coordinates": {
                        "bet_regions": {"bet_player": {"center": [224, 218]}},
                        "chips": {"chip_10": {"center": [333, 489]}},
                    },
                },
            )

    status = probe.status_from_snapshot(
        {"id": "a2", "runtime": {"mode": "headless"}, "state": FakeState()},
        SimpleNamespace(game_ready=False, hall_ready=True, scene_name="BjlGameRoomHallSceneView"),
        None,
    )

    assert status["game_ready"] is True
    assert status["hall_ready"] is False
    assert status["game_no"] == "50-1782377104-8541781943"
    assert status["countdown"] == 11
    assert status["betting_open"] is True


def test_public_game_no_drops_account_suffix() -> None:
    assert probe.public_game_no("50-1782380930-8541930580-1280") == "50-1782380930-8541930580"
    assert probe.public_game_no("50-1782380760-8541924223-11248") == "50-1782380760-8541924223"


def test_interval_probe_uses_trusted_preflight_fast_click_executor(monkeypatch) -> None:
    clicks: list[tuple[float, float]] = []
    waits: list[int] = []

    class FakeMouse:
        async def click(self, x, y):
            clicks.append((x, y))

    class FakePage:
        mouse = FakeMouse()

        async def wait_for_timeout(self, ms):
            waits.append(int(ms))

    async def fake_read_snapshot(account):
        return SimpleNamespace(
            countdown_seconds=9,
            game_no="50-1782380930-8541930580-9999",
            betting_open=True,
            frame=SimpleNamespace(balance_cents=9920, pending_chip_cents=80),
        )

    monkeypatch.setattr(probe.fast_click_probe, "read_snapshot", fake_read_snapshot)
    item = {
        "id": "a2",
        "config": SimpleNamespace(
            instance_id="a2",
            login_url="https://example.test",
            target_url="",
            username="u2",
            password="p2",
            viewport_width=960,
            viewport_height=620,
        ),
        "runtime": {"context": object(), "page": FakePage(), "mode": "headed"},
    }
    cmd = {
        "side": "player",
        "role": "main",
        "amount": 80,
        "chip_sequence": [50, 20, 10],
        "delay_ms": 200,
        "confirm_ms": 1200,
        "execution_id": "test-exec",
        "batch_id": "50-1782380930-8541930580",
        "trusted_status": {
            "game_ready": True,
            "betting_open": True,
            "countdown": 11,
            "game_no": "50-1782380930-8541930580-1280",
            "display_balance_cents": 10000,
            "pending_cents": 0,
        },
    }

    wrapper = asyncio.run(probe.run_worker_plan_with_timing(item, cmd))

    assert wrapper.get("error") is None
    result = wrapper["result"]
    assert result["account_id"] == "a2"
    assert result["execution_id"] == "test-exec"
    assert result["status"] == "COMPLETE"
    assert result["pre_game_no"] == "50-1782380930-8541930580"
    assert result["post_game_no"] == "50-1782380930-8541930580"
    assert len(clicks) == 6
    assert waits[-1] == 1200
    assert result["click_trace_timing"]["confirm_wait_config_ms"] == 1200


def test_click_page_selection_prefers_matching_game_round(monkeypatch) -> None:
    class FakePage:
        def __init__(self, url: str, game_no: str, betting_open: bool) -> None:
            self.url = url
            self.game_no = game_no
            self.betting_open = betting_open

        def is_closed(self) -> bool:
            return False

    class FakeContext:
        def __init__(self, pages: list[FakePage]) -> None:
            self.pages = pages

    old_page = FakePage("https://example.test/old", "50-1-2", False)
    current_page = FakePage("https://example.test/current", "50-9-8-1234", True)

    async def fake_load_state(page):
        return SimpleNamespace(game_ready=True)

    async def fake_runtime_snapshot(page, instance_id=""):
        return SimpleNamespace(game_no=page.game_no, betting_open=page.betting_open)

    monkeypatch.setattr(probe, "read_baccarat_load_state", fake_load_state)
    monkeypatch.setattr(probe, "read_live_runtime_snapshot", fake_runtime_snapshot)

    item = {
        "id": "a2",
        "config": SimpleNamespace(instance_id="a2"),
        "runtime": {"context": FakeContext([old_page, current_page]), "page": old_page},
    }

    selected = asyncio.run(
        probe.select_active_game_page_for_click(
            item,
            {"game_no": "50-9-8-5678", "betting_open": True},
        )
    )

    assert selected is current_page
    assert item["runtime"]["page"] is current_page


def test_controller_records_plan_and_round_events(tmp_path: Path) -> None:
    controller = LightweightController(
        config_store=LightweightConfigStore(tmp_path / "lightweight.json"),
        adapter=FakeBrowserControlAdapter(max_log_entries=20),
    )
    plan_events: list[dict] = []
    round_events: list[list] = []
    controller.on("hedge_plan_updated", plan_events.append)
    controller.on("round_results_updated", round_events.append)

    controller._handle_runtime_event(
        "",
        "plan",
        {
            "round_number": 1,
            "round_id": "50-9-8-1234",
            "click_interval_ms": 200,
            "legs": [{"account_id": "a2", "role": "main", "side_text": "banker", "amount": 80, "chips": [50, 20, 10]}],
        },
    )
    controller._handle_runtime_event(
        "",
        "round",
        {
            "round_number": 1,
            "round_id": "50-9-8-1234",
            "send_countdowns": {"a2": 10},
            "delay_ms": 200,
            "elapsed_ms": 1800,
            "results": [
                {
                    "instance_id": "a2",
                    "status": "INCOMPLETE",
                    "actual_amount": 60,
                    "missing_amount": 20,
                    "click_sequence_ms": 700,
                }
            ],
        },
    )

    assert plan_events[-1]["round_id"] == "50-9-8-1234"
    assert controller.round_results[-1].round_id == "50-9-8"
    assert controller.round_results[-1].missing_total == 20
    assert controller.round_results[-1].status == "incomplete"
    assert round_events[-1][-1].max_elapsed_ms == 700


def test_round_summary_is_uncapped_but_tables_show_recent_rows(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    controller = LightweightController(
        config_store=LightweightConfigStore(tmp_path / "lightweight.json"),
        adapter=FakeBrowserControlAdapter(max_log_entries=20),
    )
    dashboard = LightweightDashboard(controller=controller)

    for round_number in range(1, 106):
        controller._handle_runtime_event(
            "",
            "round",
            {
                "round_number": round_number,
                "round_id": f"50-9-{round_number}-1234",
                "send_countdowns": {"a2": 10},
                "delay_ms": 200,
                "elapsed_ms": 1000 + round_number,
                "results": [
                    {
                        "instance_id": "a2",
                        "status": "COMPLETE",
                        "actual_amount": 20,
                        "missing_amount": 0,
                        "click_sequence_ms": 500 + round_number,
                    }
                ],
            },
        )

    assert len(controller.round_results) == 105
    assert dashboard.health_labels["rounds"].text() == "105"
    assert dashboard.round_table.rowCount() == 10
    assert dashboard.round_table.item(0, 0).text() == "#105"
    assert dashboard.records_table.rowCount() == 10

    dashboard.close()


def _balance_status(yuan: int) -> dict[str, int]:
    return {"display_balance_cents": int(yuan) * 100}


def _ready_game_status(yuan: int) -> dict[str, object]:
    return {
        "display_balance_cents": int(yuan) * 100,
        "balance_cents": int(yuan) * 100,
        "game_ready": True,
        "hall_ready": False,
        "room_label": "T001",
        "locked_room_label": "T001",
        "game_no": "50-1-2-9999",
        "countdown": 12,
        "betting_open": True,
    }


def test_probe_plan_uses_configured_amount_and_chinese_side(tmp_path: Path) -> None:
    adapter = LightweightProbeAdapter(profile_root=tmp_path / "profiles", log_root=tmp_path)
    statuses = {account_id: _balance_status(1000) for account_id in ACCOUNT_IDS}

    plan = adapter._build_plan(
        1,
        main_account="a2",
        sub_accounts=("a1", "a3", "a4"),
        amount_min=100,
        amount_max=100,
        statuses=statuses,
    )

    assert plan["ok"] is True
    main_leg = next(leg for leg in plan["legs"] if leg["role"] == "main")
    assert main_leg["account_id"] == "a2"
    assert main_leg["amount"] == 100
    assert main_leg["side"] == "banker"
    assert main_leg["side_text"] == "\u5e84"


def test_probe_plan_excludes_low_balance_main_and_uses_highest_balance_successor(tmp_path: Path) -> None:
    adapter = LightweightProbeAdapter(profile_root=tmp_path / "profiles", log_root=tmp_path)
    statuses = {
        "a1": _balance_status(900),
        "a2": _balance_status(10),
        "a3": _balance_status(500),
        "a4": _balance_status(700),
    }

    plan = adapter._build_plan(
        1,
        main_account="a2",
        sub_accounts=("a1", "a3", "a4"),
        amount_min=80,
        amount_max=80,
        statuses=statuses,
    )

    assert plan["ok"] is True
    assert plan["effective_main_account"] == "a1"
    assert plan["excluded_accounts"] == {"a2": "余额不足"}
    assert [leg["account_id"] for leg in plan["legs"]] == ["a1", "a3", "a4"]


def test_probe_plan_prefers_configured_successor_when_main_is_low_balance(tmp_path: Path) -> None:
    adapter = LightweightProbeAdapter(profile_root=tmp_path / "profiles", log_root=tmp_path)
    statuses = {
        "a1": _balance_status(900),
        "a2": _balance_status(10),
        "a3": _balance_status(500),
        "a4": _balance_status(700),
    }

    plan = adapter._build_plan(
        1,
        main_account="a2",
        sub_accounts=("a1", "a3", "a4"),
        amount_min=80,
        amount_max=80,
        statuses=statuses,
        main_successor_account="a4",
    )

    assert plan["ok"] is True
    assert plan["effective_main_account"] == "a4"
    assert plan["excluded_accounts"] == {"a2": "余额不足"}
    assert [leg["account_id"] for leg in plan["legs"]] == ["a4", "a1", "a3"]


def test_probe_plan_excludes_accounts_below_configured_min_balance(tmp_path: Path) -> None:
    adapter = LightweightProbeAdapter(profile_root=tmp_path / "profiles", log_root=tmp_path)
    statuses = {
        "a1": _balance_status(900),
        "a2": _balance_status(900),
        "a3": _balance_status(50),
        "a4": _balance_status(700),
    }

    plan = adapter._build_plan(
        1,
        main_account="a2",
        sub_accounts=("a1", "a3", "a4"),
        amount_min=84,
        amount_max=84,
        statuses=statuses,
        min_balance_yuan=100,
    )

    assert plan["ok"] is True
    assert plan["excluded_accounts"] == {"a3": "余额不足"}
    assert [leg["account_id"] for leg in plan["legs"]] == ["a2", "a1", "a4"]


def test_probe_plan_supports_four_yuan_chip_sequences(tmp_path: Path) -> None:
    adapter = LightweightProbeAdapter(profile_root=tmp_path / "profiles", log_root=tmp_path)
    statuses = {account_id: _balance_status(1000) for account_id in ACCOUNT_IDS}

    plan = adapter._build_plan(
        1,
        main_account="a2",
        sub_accounts=("a1", "a3", "a4"),
        amount_min=84,
        amount_max=84,
        statuses=statuses,
    )

    assert plan["ok"] is True
    assert plan["amount"] == 84
    assert any(4 in leg["chips"] for leg in plan["legs"])
    assert all(len(leg["chips"]) <= 5 for leg in plan["legs"])
    assert sum(int(leg["amount"]) for leg in plan["legs"] if leg["role"] == "sub") == 84


def test_probe_plan_sub_amounts_are_irregular_but_clickable(tmp_path: Path) -> None:
    adapter = LightweightProbeAdapter(profile_root=tmp_path / "profiles", log_root=tmp_path)
    statuses = {account_id: _balance_status(1000) for account_id in ACCOUNT_IDS}
    seen: set[tuple[int, ...]] = set()

    for round_number in range(1, 7):
        plan = adapter._build_plan(
            round_number,
            main_account="a2",
            sub_accounts=("a1", "a3", "a4"),
            amount_min=140,
            amount_max=140,
            statuses=statuses,
        )
        sub_legs = [leg for leg in plan["legs"] if leg["role"] == "sub"]
        amounts = tuple(int(leg["amount"]) for leg in sub_legs)
        seen.add(amounts)
        assert sum(amounts) == 140
        assert len(set(amounts)) > 1
        assert max(amounts) - min(amounts) >= 30
        assert all(len(leg["chips"]) <= 5 for leg in sub_legs)

    assert len(seen) >= 3


def test_probe_plan_three_account_sub_amounts_are_irregular(tmp_path: Path) -> None:
    adapter = LightweightProbeAdapter(profile_root=tmp_path / "profiles", log_root=tmp_path)
    statuses = {account_id: _balance_status(1000) for account_id in ACCOUNT_IDS}
    seen: set[tuple[int, ...]] = set()

    for round_number in range(1, 7):
        plan = adapter._build_plan(
            round_number,
            main_account="a2",
            sub_accounts=("a1", "a3"),
            amount_min=140,
            amount_max=140,
            statuses=statuses,
        )
        sub_legs = [leg for leg in plan["legs"] if leg["role"] == "sub"]
        amounts = tuple(int(leg["amount"]) for leg in sub_legs)
        seen.add(amounts)
        assert sum(amounts) == 140
        assert max(amounts) - min(amounts) >= 50
        assert all(len(leg["chips"]) <= 5 for leg in sub_legs)

    assert len(seen) >= 3


def test_probe_wait_gate_ignores_low_balance_account_outside_room(tmp_path: Path, monkeypatch) -> None:
    adapter = LightweightProbeAdapter(profile_root=tmp_path / "profiles", log_root=tmp_path)
    adapter._accounts = {account_id: {"runtime": {}} for account_id in ACCOUNT_IDS}
    statuses = {
        "a1": _ready_game_status(900),
        "a2": _ready_game_status(900),
        "a3": _ready_game_status(900),
        "a4": {
            "display_balance_cents": 50 * 100,
            "balance_cents": 50 * 100,
            "game_ready": False,
            "hall_ready": True,
            "countdown": None,
            "betting_open": False,
        },
    }
    retried: list[tuple[str, ...]] = []

    async def fake_write_status(accounts, status_path, output):
        return statuses

    async def fake_retry_room_entries(accounts, current_statuses, output, *, room_index, account_ids=None):
        retried.append(tuple(account_ids or accounts.keys()))

    monkeypatch.setattr(probe, "write_status", fake_write_status)
    monkeypatch.setattr(probe, "retry_headless_room_entries", fake_retry_room_entries)

    ready = asyncio.run(
        adapter._wait_planned_betting_round(
            account_ids=list(ACCOUNT_IDS),
            main_account="a2",
            sub_accounts=("a1", "a3", "a4"),
            round_number=1,
            room_index=1,
            amount_min=84,
            amount_max=84,
            main_successor_account="",
            min_balance_yuan=100,
            min_countdown=10,
            last_round="",
            timeout_seconds=1,
            allow_countdown_window=True,
            coordinator_status_refresh_ms=200,
            coordinator_poll_ms=50,
        )
    )

    assert ready is not None
    _round_id, round_key, _statuses, plan = ready
    assert round_key == "50-1-2"
    assert plan["excluded_accounts"] == {"a4": "余额不足"}
    assert [leg["account_id"] for leg in plan["legs"]] == ["a2", "a1", "a3"]
    assert retried
    assert all("a4" not in account_ids for account_ids in retried)


def test_probe_round_executes_only_planned_accounts_when_one_is_excluded(tmp_path: Path, monkeypatch) -> None:
    adapter = LightweightProbeAdapter(profile_root=tmp_path / "profiles", log_root=tmp_path)
    adapter._accounts = {account_id: {"runtime": {}} for account_id in ACCOUNT_IDS}
    statuses = {
        "a1": _ready_game_status(900),
        "a2": _ready_game_status(900),
        "a3": _ready_game_status(900),
        "a4": {
            "display_balance_cents": 50 * 100,
            "balance_cents": 50 * 100,
            "game_ready": False,
            "hall_ready": True,
            "countdown": None,
            "betting_open": False,
        },
    }
    plan = {
        "ok": True,
        "reason": "ready",
        "effective_main_account": "a2",
        "excluded_accounts": {"a4": "余额不足"},
        "legs": [
            {"account_id": "a2", "role": "main", "side": "banker", "side_text": "庄", "amount": 84, "chips": [4, 20, 20, 40]},
            {"account_id": "a1", "role": "sub", "side": "player", "side_text": "闲", "amount": 40, "chips": [20, 20]},
            {"account_id": "a3", "role": "sub", "side": "player", "side_text": "闲", "amount": 44, "chips": [4, 20, 20]},
        ],
    }
    captured: dict[str, object] = {}

    async def fake_wait_planned_betting_round(**kwargs):
        return "50-1-2-9999", "50-1-2", statuses, plan

    async def fake_execute_round(accounts, legs, round_id, statuses_arg, delay_ms, confirm_ms, output, *, room_index, allow_countdown_window):
        captured["account_ids"] = tuple(accounts.keys())
        captured["status_ids"] = tuple(statuses_arg.keys())
        captured["leg_ids"] = tuple(leg["account_id"] for leg in legs)
        return {
            "round_id": round_id,
            "status": "complete",
            "results": [],
            "elapsed_ms": 123,
            "max_elapsed_ms": 123,
            "missing_total": 0,
        }

    monkeypatch.setattr(adapter, "_wait_planned_betting_round", fake_wait_planned_betting_round)
    monkeypatch.setattr(probe, "execute_round", fake_execute_round)

    returned = asyncio.run(
        adapter._run_one_hedge_round(
            account_ids=list(ACCOUNT_IDS),
            main_account="a2",
            sub_accounts=("a1", "a3", "a4"),
            round_number=1,
            room_index=1,
            amount_min=84,
            amount_max=84,
            main_successor_account="",
            min_balance_yuan=100,
            click_interval_ms=200,
            confirm_ms=1200,
            min_countdown=10,
            last_round="",
        )
    )

    assert returned == "50-1-2"
    assert captured["account_ids"] == ("a2", "a1", "a3")
    assert captured["status_ids"] == ("a2", "a1", "a3")
    assert captured["leg_ids"] == ("a2", "a1", "a3")
    plan_events = [event for event in adapter._events if event.get("event_type") == "plan"]
    assert plan_events
    payload = plan_events[-1]["payload"]
    assert payload["planned_accounts"] == ["a2", "a1", "a3"]
    assert set(payload["send_countdowns"]) == {"a2", "a1", "a3"}


def test_delay_patch_ignores_removed_worker_delay_constants() -> None:
    with probe.DelayPatch(200, 1200):
        pass


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



