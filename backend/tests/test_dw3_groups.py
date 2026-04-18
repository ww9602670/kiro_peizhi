from decimal import Decimal

from app.utils.dw3_groups import (
    DW3_BS_BBB,
    DW3_BS_LABELS,
    DW3_BS_SSS,
    DW3_BS_TOKENS,
    DW3_BUCKETS,
    DW3_GROUP_LABELS,
    DW3_GROUP_TOKENS,
    DW3_OE_EEO,
    DW3_OE_LABELS,
    DW3_OE_OEO,
    DW3_OE_OOO,
    DW3_OE_TOKENS,
    build_dw3_bucket_map,
    build_dw3_token_map,
    calculate_dw3_theoretical_totals,
    decode_draw_to_signals,
    get_dw3_bucket_index,
    key_code_matches_bucket,
    number_matches_bs_token,
    number_matches_bucket,
    number_matches_oe_token,
)


def test_dw3_tokens_and_labels_are_complete():
    assert len(DW3_BS_TOKENS) == 8
    assert len(DW3_OE_TOKENS) == 8
    assert len(DW3_GROUP_TOKENS) == 16

    assert len(set(DW3_BS_TOKENS)) == 8
    assert len(set(DW3_OE_TOKENS)) == 8
    assert len(set(DW3_GROUP_TOKENS)) == 16

    assert set(DW3_BS_LABELS) == set(DW3_BS_TOKENS)
    assert set(DW3_OE_LABELS) == set(DW3_OE_TOKENS)
    assert set(DW3_GROUP_LABELS) == set(DW3_GROUP_TOKENS)

    assert len(DW3_BUCKETS) == 64
    assert len(set(DW3_BUCKETS)) == 64


def test_decode_draw_to_signals_supports_leading_zeros():
    assert decode_draw_to_signals("321") == (DW3_BS_SSS, DW3_OE_OEO)
    assert decode_draw_to_signals("021") == decode_draw_to_signals(21)
    assert decode_draw_to_signals("001") == (DW3_BS_SSS, DW3_OE_EEO)


def test_each_bs_and_oe_group_has_125_numbers():
    token_map = build_dw3_token_map()
    assert set(token_map) == set(DW3_GROUP_TOKENS)

    for token in DW3_BS_TOKENS:
        assert len(token_map[token]) == 125
    for token in DW3_OE_TOKENS:
        assert len(token_map[token]) == 125


def test_64_buckets_cover_dw3_000_to_dw3_999_exactly_once():
    bucket_map = build_dw3_bucket_map()

    assert set(bucket_map) == set(DW3_BUCKETS)
    assert len(bucket_map) == 64

    all_codes: list[str] = []
    for bucket, key_codes in bucket_map.items():
        for key_code in key_codes:
            assert get_dw3_bucket_index(key_code) == bucket
            all_codes.append(key_code)

    assert len(all_codes) == 1000
    assert len(set(all_codes)) == 1000
    assert set(all_codes) == {f"DW3_{number:03d}" for number in range(1000)}


def test_membership_helpers_work_for_number_and_key_code():
    assert number_matches_bs_token("579", DW3_BS_BBB)
    assert number_matches_oe_token("579", DW3_OE_OOO)
    assert number_matches_bucket("579", DW3_BS_BBB, DW3_OE_OOO)
    assert key_code_matches_bucket("DW3_579", DW3_BS_BBB, DW3_OE_OOO)

    assert number_matches_bucket("001", DW3_BS_SSS, DW3_OE_EEO)
    assert not number_matches_bucket("001", DW3_BS_BBB, DW3_OE_EEO)


def test_theoretical_totals_math_matches_dw3_formula():
    bs_blocked, oe_blocked = decode_draw_to_signals("321")

    total_units, total_stake = calculate_dw3_theoretical_totals(
        stage_amount=Decimal("2"),
        selected_bs_tokens=DW3_BS_TOKENS,
        selected_oe_tokens=DW3_OE_TOKENS,
        blocked_bs_tokens=[bs_blocked],
        blocked_oe_tokens=[oe_blocked],
    )
    assert total_units == 1750
    assert total_stake == Decimal("3500")

    single_units, single_stake = calculate_dw3_theoretical_totals(
        stage_amount=1,
        selected_bs_tokens=[DW3_BS_BBB],
        selected_oe_tokens=[DW3_OE_OOO],
    )
    assert single_units == 250
    assert single_stake == 250
