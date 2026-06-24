from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from bet_desktop.ui.lightweight_browser_adapter import FakeBrowserControlAdapter
from bet_desktop.ui.lightweight_config_store import LightweightConfigStore
from bet_desktop.ui.lightweight_controller import LightweightController
from bet_desktop.ui.lightweight_models import ACCOUNT_IDS, parse_proxy_bundle_lines, resolve_sub_accounts


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
    assert any("对冲系统已启动" in item for item in logs)
    assert not adapter.commands
    assert all(summary.state_label == "待命" for summary in controller.account_status)
