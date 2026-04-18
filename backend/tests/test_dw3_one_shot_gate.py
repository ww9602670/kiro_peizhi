import pytest

from app.engine.dw3_one_shot_gate import (
    MAX_BET_REFERENCE,
    build_dw3_full_coverage_capacity_payload,
    build_dw3_one_shot_gate_payload,
    parse_dw3_pre_result,
    synthetic_dw3_odds_map,
    validate_gate_payload_ready,
)


def test_build_one_shot_gate_payload_exceeds_page_reference_limit() -> None:
    payload = build_dw3_one_shot_gate_payload(
        issue="20260418002",
        pre_issue="20260418001",
        pre_result="3,2,1",
        odds_map=synthetic_dw3_odds_map(),
        amount_fen=1,
        gate_window_issues=1,
    )

    assert payload.summary["request_item_count"] == 988
    assert payload.summary["probe_mode"] == "strategy_gate"
    assert payload.summary["gate_bypassed"] is False
    assert payload.summary["unique_key_count"] == 988
    assert payload.summary["request_item_count"] > MAX_BET_REFERENCE
    assert payload.summary["exceeds_max_bet_reference"] is True
    assert payload.summary["blocked_groups"] == ["DW3_BS_SSS", "DW3_OE_OEO"]
    assert payload.summary["effective_groups"] == [
        "DW3_BS_BBB",
        "DW3_BS_BBS",
        "DW3_BS_BSB",
        "DW3_BS_BSS",
        "DW3_BS_SBB",
        "DW3_BS_SBS",
        "DW3_BS_SSB",
        "DW3_OE_EEE",
        "DW3_OE_EEO",
        "DW3_OE_EOE",
        "DW3_OE_EOO",
        "DW3_OE_OEE",
        "DW3_OE_OOE",
        "DW3_OE_OOO",
    ]
    assert payload.summary["total_amount_fen"] == 1750
    validate_gate_payload_ready(payload)


def test_build_full_coverage_capacity_payload_has_exactly_1000_items() -> None:
    payload = build_dw3_full_coverage_capacity_payload(
        issue="20260418002",
        odds_map=synthetic_dw3_odds_map(),
        amount_fen=1,
    )

    assert payload.summary["probe_mode"] == "full_1000_capacity"
    assert payload.summary["gate_bypassed"] is True
    assert payload.summary["request_item_count"] == 1000
    assert payload.summary["unique_key_count"] == 1000
    assert payload.summary["request_item_count"] > MAX_BET_REFERENCE
    assert payload.summary["exceeds_max_bet_reference"] is True
    assert payload.summary["blocked_groups"] == []
    assert payload.summary["total_amount_fen"] == 1000
    assert payload.betdata[0]["KeyCode"] == "DW3_000"
    assert payload.betdata[-1]["KeyCode"] == "DW3_999"
    validate_gate_payload_ready(payload)


def test_build_one_shot_gate_payload_reports_missing_odds() -> None:
    odds_map = synthetic_dw3_odds_map()
    odds_map.pop("DW3_000")

    payload = build_dw3_one_shot_gate_payload(
        issue="20260418002",
        pre_issue="20260418001",
        pre_result="3,2,1",
        odds_map=odds_map,
        amount_fen=1,
        gate_window_issues=1,
    )

    assert "DW3_000" in payload.missing_odds
    assert payload.summary["missing_odds_count"] == 1
    with pytest.raises(ValueError, match="odds missing"):
        validate_gate_payload_ready(payload)


def test_build_full_coverage_capacity_payload_reports_missing_odds() -> None:
    odds_map = synthetic_dw3_odds_map()
    odds_map.pop("DW3_999")

    payload = build_dw3_full_coverage_capacity_payload(
        issue="20260418002",
        odds_map=odds_map,
        amount_fen=1,
    )

    assert "DW3_999" in payload.missing_odds
    assert payload.summary["request_item_count"] == 999
    assert payload.summary["missing_odds_count"] == 1
    with pytest.raises(ValueError, match="odds missing"):
        validate_gate_payload_ready(payload)


def test_parse_dw3_pre_result_accepts_csv_and_compact_text() -> None:
    assert parse_dw3_pre_result("3,2,1") == [3, 2, 1]
    assert parse_dw3_pre_result("321") == [3, 2, 1]


def test_parse_dw3_pre_result_rejects_bad_values() -> None:
    with pytest.raises(ValueError):
        parse_dw3_pre_result("12")
