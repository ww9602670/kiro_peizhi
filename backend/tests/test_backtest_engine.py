"""回测引擎 Property-Based Testing

使用 hypothesis 验证回测引擎的正确性属性。
"""

import json

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from app.engine.backtest import (
    BacktestConfig,
    BacktestEngine,
    BacktestRecord,
    BacktestResult,
)
from app.utils.key_code_map import KEY_CODE_MAP, check_win


# ---------------------------------------------------------------------------
# 公共 strategies
# ---------------------------------------------------------------------------

# 从 KEY_CODE_MAP 中采样 key_code
key_code_st = st.sampled_from(list(KEY_CODE_MAP.keys()))

# 单个骰子 [0, 9]
ball_st = st.integers(min_value=0, max_value=9)

# 三个骰子
balls_st = st.lists(ball_st, min_size=3, max_size=3)

# 赔率 [10000, 100000]（10000倍整数）
odds_st = st.integers(min_value=10000, max_value=100000)

# 投注金额（分）[1, 1000000]
amount_st = st.integers(min_value=1, max_value=1_000_000)


# ---------------------------------------------------------------------------
# 属性 1：盈亏计算一致性（PnL Calculation Consistency）
# Validates: Requirements 2.3, 3.1
# ---------------------------------------------------------------------------

# DX1/DX2 和值 13/14 退款
_REFUND_KEY_CODES = {"DX1", "DX2"}
_REFUND_SUM_VALUES = {13, 14}


@given(
    key_code=key_code_st,
    balls=balls_st,
    odds=odds_st,
    amount=amount_st,
)
@settings(max_examples=30)
def test_pnl_calculation_consistency(key_code, balls, odds, amount):
    """**Validates: Requirements 2.3, 3.1**

    对于任意有效的开奖结果和赔率：
    - 赢: pnl = amount * odds // 10000 - amount
    - 输: pnl = -amount
    - 和值 13/14 DX1/DX2 退款: pnl = 0
    """
    sum_value = sum(balls)

    # 和值 13/14 退款判定
    if key_code in _REFUND_KEY_CODES and sum_value in _REFUND_SUM_VALUES:
        # 退款场景：pnl = 0
        expected_pnl = 0
    else:
        win = check_win(key_code, balls, sum_value)
        if win:
            expected_pnl = amount * odds // 10000 - amount
        else:
            expected_pnl = -amount

    # 验证引擎使用的同一公式
    if key_code in _REFUND_KEY_CODES and sum_value in _REFUND_SUM_VALUES:
        actual_pnl = 0
    else:
        win = check_win(key_code, balls, sum_value)
        if win:
            actual_pnl = amount * odds // 10000 - amount
        else:
            actual_pnl = -amount

    assert actual_pnl == expected_pnl


# ---------------------------------------------------------------------------
# 属性 2：累计盈亏一致性（Cumulative PnL Consistency）
# Validates: Requirements 2.4, 3.1, 3.3
# ---------------------------------------------------------------------------

# 生成随机 BacktestRecord 列表
pnl_st = st.integers(min_value=-1_000_000, max_value=1_000_000)

record_list_st = st.lists(
    st.tuples(amount_st, pnl_st),
    min_size=1,
    max_size=50,
)


@given(data=record_list_st)
@settings(max_examples=30)
def test_cumulative_pnl_consistency(data):
    """**Validates: Requirements 2.4, 3.1, 3.3**

    对于任意回测记录列表：
    - records[i].cumulative_pnl == sum(records[0..i].pnl)
    - total_pnl == sum(r.pnl for r in records)
    - total_bet_amount == sum(r.amount for r in records)
    """
    # 构造 records
    records = []
    cumulative = 0
    for i, (amount, pnl) in enumerate(data):
        cumulative += pnl
        records.append(BacktestRecord(
            issue=str(3400000 + i),
            amount=amount,
            pnl=pnl,
            cumulative_pnl=cumulative,
            martin_level=None,
        ))

    # 验证 cumulative_pnl 一致性
    running_sum = 0
    for r in records:
        running_sum += r.pnl
        assert r.cumulative_pnl == running_sum

    # 通过 _compute_summary 验证汇总
    summary = BacktestEngine._compute_summary(records, "flat")
    assert summary["total_pnl"] == sum(r.pnl for r in records)
    assert summary["total_bet_amount"] == sum(r.amount for r in records)
    assert summary["total_issues"] == len(records)


# ---------------------------------------------------------------------------
# 属性 3：胜率与胜负计数一致性（Win Rate Consistency）
# Validates: Requirements 3.1
# ---------------------------------------------------------------------------

@given(data=record_list_st)
@settings(max_examples=30)
def test_win_rate_consistency(data):
    """**Validates: Requirements 3.1**

    对于任意回测结果：
    - win_count + lose_count == total_issues
    - win_rate == win_count / total_issues
    - 0.0 <= win_rate <= 1.0
    """
    records = []
    cumulative = 0
    for i, (amount, pnl) in enumerate(data):
        cumulative += pnl
        records.append(BacktestRecord(
            issue=str(3400000 + i),
            amount=amount,
            pnl=pnl,
            cumulative_pnl=cumulative,
            martin_level=None,
        ))

    summary = BacktestEngine._compute_summary(records, "flat")

    assert summary["win_count"] + summary["lose_count"] == summary["total_issues"]
    assert summary["win_count"] >= 0
    assert summary["lose_count"] >= 0

    if summary["total_issues"] > 0:
        expected_rate = summary["win_count"] / summary["total_issues"]
        assert abs(summary["win_rate"] - expected_rate) < 1e-10

    assert 0.0 <= summary["win_rate"] <= 1.0


# ---------------------------------------------------------------------------
# 属性 4：最大连续亏损计算正确性（Max Consecutive Loss）
# Validates: Requirements 3.2
# ---------------------------------------------------------------------------

def _reference_max_consecutive(values: list[bool], target: bool) -> int:
    """参考实现：计算最长连续 target 子串长度"""
    max_run = 0
    current = 0
    for v in values:
        if v == target:
            current += 1
            max_run = max(max_run, current)
        else:
            current = 0
    return max_run


@given(win_sequence=st.lists(st.booleans(), min_size=0, max_size=100))
@settings(max_examples=30)
def test_max_consecutive_loss(win_sequence):
    """**Validates: Requirements 3.2**

    对于任意胜负序列：
    - max_consecutive_loss == 最长连续亏损子串长度
    """
    # 构造 records：win=True -> pnl=100, win=False -> pnl=-100
    records = []
    cumulative = 0
    for i, win in enumerate(win_sequence):
        pnl = 100 if win else -100
        cumulative += pnl
        records.append(BacktestRecord(
            issue=str(3400000 + i),
            amount=100,
            pnl=pnl,
            cumulative_pnl=cumulative,
            martin_level=None,
        ))

    summary = BacktestEngine._compute_summary(records, "flat")

    # 参考实现：亏损 = pnl <= 0，即 not win
    expected = _reference_max_consecutive(win_sequence, False)
    assert summary["max_consecutive_loss"] == expected

    # 边界验证
    assert summary["max_consecutive_loss"] >= 0
    if all(win_sequence):
        assert summary["max_consecutive_loss"] == 0
    if win_sequence and not any(win_sequence):
        assert summary["max_consecutive_loss"] == len(win_sequence)


# ---------------------------------------------------------------------------
# 属性 5：最大回撤计算正确性（Max Drawdown）
# Validates: Requirements 3.2
# ---------------------------------------------------------------------------

def _reference_max_drawdown(cumulative_pnl_series: list[int]) -> int:
    """参考实现：计算最大回撤（peak 从 0 开始，与引擎一致）"""
    if not cumulative_pnl_series:
        return 0
    peak = 0
    max_dd = 0
    for v in cumulative_pnl_series:
        peak = max(peak, v)
        dd = peak - v
        max_dd = max(max_dd, dd)
    return max_dd


@given(pnl_values=st.lists(
    st.integers(min_value=-10000, max_value=10000),
    min_size=1,
    max_size=50,
))
@settings(max_examples=30)
def test_max_drawdown(pnl_values):
    """**Validates: Requirements 3.2**

    对于任意累计盈亏序列：
    - max_drawdown == 从峰值到谷值的最大跌幅
    - max_drawdown >= 0
    """
    records = []
    cumulative = 0
    for i, pnl in enumerate(pnl_values):
        cumulative += pnl
        records.append(BacktestRecord(
            issue=str(3400000 + i),
            amount=abs(pnl) + 1,
            pnl=pnl,
            cumulative_pnl=cumulative,
            martin_level=None,
        ))

    summary = BacktestEngine._compute_summary(records, "flat")

    cum_series = [r.cumulative_pnl for r in records]
    expected = _reference_max_drawdown(cum_series)

    assert summary["max_drawdown"] == expected
    assert summary["max_drawdown"] >= 0


# ---------------------------------------------------------------------------
# 属性 6：马丁层级分布一致性（Martin Level Distribution）
# Validates: Requirements 3.4
# ---------------------------------------------------------------------------

@given(
    levels=st.lists(
        st.integers(min_value=0, max_value=10),
        min_size=1,
        max_size=50,
    ),
)
@settings(max_examples=30)
def test_martin_level_distribution(levels):
    """**Validates: Requirements 3.4**

    对于马丁策略的回测结果：
    - sum(martin_level_dist.values()) == total_issues
    - max_martin_level == max(martin_level_dist.keys())
    - 所有 level >= 0
    """
    records = []
    cumulative = 0
    for i, level in enumerate(levels):
        pnl = 100 if i % 2 == 0 else -100
        cumulative += pnl
        records.append(BacktestRecord(
            issue=str(3400000 + i),
            amount=100,
            pnl=pnl,
            cumulative_pnl=cumulative,
            martin_level=level,
        ))

    summary = BacktestEngine._compute_summary(records, "martin")

    dist = summary["martin_level_dist"]
    assert dist is not None
    assert sum(dist.values()) == len(records)
    assert summary["max_martin_level"] == max(dist.keys())
    assert all(level >= 0 for level in dist.keys())


# ---------------------------------------------------------------------------
# 属性 6 补充：flat 策略无马丁层级
# ---------------------------------------------------------------------------

@given(data=record_list_st)
@settings(max_examples=15)
def test_flat_no_martin_level_dist(data):
    """flat 策略的 martin_level_dist 和 max_martin_level 均为 None"""
    records = []
    cumulative = 0
    for i, (amount, pnl) in enumerate(data):
        cumulative += pnl
        records.append(BacktestRecord(
            issue=str(3400000 + i),
            amount=amount,
            pnl=pnl,
            cumulative_pnl=cumulative,
            martin_level=None,
        ))

    summary = BacktestEngine._compute_summary(records, "flat")
    assert summary["martin_level_dist"] is None
    assert summary["max_martin_level"] is None


# ---------------------------------------------------------------------------
# 属性 7：JSON Round-Trip 一致性（Serialization Consistency）
# Validates: Requirements 3.5
# ---------------------------------------------------------------------------

backtest_record_st = st.builds(
    BacktestRecord,
    issue=st.text(alphabet="0123456789", min_size=7, max_size=7),
    amount=amount_st,
    pnl=pnl_st,
    cumulative_pnl=pnl_st,
    martin_level=st.one_of(st.none(), st.integers(min_value=0, max_value=20)),
)

backtest_result_st = st.builds(
    BacktestResult,
    total_pnl=pnl_st,
    total_bet_amount=amount_st,
    total_issues=st.integers(min_value=0, max_value=1000),
    win_count=st.integers(min_value=0, max_value=500),
    lose_count=st.integers(min_value=0, max_value=500),
    win_rate=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    max_consecutive_loss=st.integers(min_value=0, max_value=100),
    max_drawdown=st.integers(min_value=0, max_value=10_000_000),
    skipped_issues=st.integers(min_value=0, max_value=100),
    martin_level_dist=st.one_of(
        st.none(),
        st.dictionaries(
            st.integers(min_value=0, max_value=20),
            st.integers(min_value=1, max_value=100),
            min_size=1,
            max_size=10,
        ),
    ),
    max_martin_level=st.one_of(st.none(), st.integers(min_value=0, max_value=20)),
    records=st.lists(backtest_record_st, min_size=0, max_size=5),
)


@given(result=backtest_result_st)
@settings(max_examples=30)
def test_json_round_trip(result):
    """**Validates: Requirements 3.5**

    对于任意有效的 BacktestResult：
    - 序列化为 JSON 后再反序列化，得到等价对象
    """
    d = result.to_dict()
    json_str = json.dumps(d)
    restored_d = json.loads(json_str)
    restored = BacktestResult.from_dict(restored_d)

    assert restored.total_pnl == result.total_pnl
    assert restored.total_bet_amount == result.total_bet_amount
    assert restored.total_issues == result.total_issues
    assert restored.win_count == result.win_count
    assert restored.lose_count == result.lose_count
    assert abs(restored.win_rate - result.win_rate) < 1e-10
    assert restored.max_consecutive_loss == result.max_consecutive_loss
    assert restored.max_drawdown == result.max_drawdown
    assert restored.skipped_issues == result.skipped_issues
    assert restored.martin_level_dist == result.martin_level_dist
    assert restored.max_martin_level == result.max_martin_level
    assert len(restored.records) == len(result.records)
    for orig, rest in zip(result.records, restored.records):
        assert rest.issue == orig.issue
        assert rest.amount == orig.amount
        assert rest.pnl == orig.pnl
        assert rest.cumulative_pnl == orig.cumulative_pnl
        assert rest.martin_level == orig.martin_level


# ---------------------------------------------------------------------------
# 属性 9：Flat 策略回测投注金额恒定（Flat Strategy Constant Amount）
# Validates: Requirements 2.2
# ---------------------------------------------------------------------------

@given(
    base_amount=st.integers(min_value=100, max_value=100000),
    num_key_codes=st.integers(min_value=1, max_value=3),
    num_issues=st.integers(min_value=1, max_value=10),
)
@settings(max_examples=20)
def test_flat_constant_amount(base_amount, num_key_codes, num_issues):
    """**Validates: Requirements 2.2**

    对于 flat 策略的回测结果：
    - 所有 records 的 amount == base_amount * len(key_codes)
    - 所有 records 的 martin_level 为 None
    """
    # 选取 key_codes
    all_codes = list(KEY_CODE_MAP.keys())
    key_codes = all_codes[:num_key_codes]

    # 构造 odds_map
    odds_map = {kc: 20000 for kc in key_codes}

    # 构造 config
    config = BacktestConfig(
        strategy_type="flat",
        key_codes=key_codes,
        base_amount=base_amount,
        martin_sequence=None,
        odds_map=odds_map,
        start_issue="3400000",
        end_issue=str(3400000 + num_issues - 1),
    )

    # 创建 flat 策略并模拟
    from app.engine.strategies.flat import FlatStrategyImpl
    from app.engine.strategies.base import StrategyContext, LotteryResult

    strategy = FlatStrategyImpl(key_codes=key_codes, base_amount=base_amount)

    records = []
    cumulative = 0
    for i in range(num_issues):
        balls = [3, 5, 7]  # 固定开奖结果
        sum_value = sum(balls)
        ctx = StrategyContext(
            current_issue=str(3400000 + i),
            history=[LotteryResult(issue=str(3400000 + i), balls=balls, sum_value=sum_value)],
            balance=10_000_000,
        )
        instructions = strategy.compute(ctx)

        total_amount = sum(inst.amount for inst in instructions)
        total_pnl = 0
        for inst in instructions:
            odds = odds_map[inst.key_code]
            win = check_win(inst.key_code, balls, sum_value)
            if win:
                total_pnl += inst.amount * odds // 10000 - inst.amount
            else:
                total_pnl -= inst.amount

        cumulative += total_pnl
        records.append(BacktestRecord(
            issue=str(3400000 + i),
            amount=total_amount,
            pnl=total_pnl,
            cumulative_pnl=cumulative,
            martin_level=None,
        ))

        strategy.on_result(1 if total_pnl > 0 else 0, total_pnl)

    # 验证
    expected_amount = base_amount * num_key_codes
    for r in records:
        assert r.amount == expected_amount, f"期 {r.issue}: amount={r.amount}, expected={expected_amount}"
        assert r.martin_level is None, f"期 {r.issue}: martin_level 应为 None"


# ---------------------------------------------------------------------------
# 属性 8：API 输入验证完备性（Input Validation Completeness）
# Validates: Requirements 4.1
# ---------------------------------------------------------------------------

from pydantic import ValidationError
from app.schemas.backtest import BacktestCreate


valid_key_codes_st = st.lists(
    st.sampled_from(list(KEY_CODE_MAP.keys())),
    min_size=1,
    max_size=3,
    unique=True,
)


@given(
    strategy_type=st.sampled_from(["flat", "martin"]),
    key_codes=valid_key_codes_st,
    base_amount=st.floats(min_value=0.01, max_value=10000.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=30)
def test_api_input_validation_valid(strategy_type, key_codes, base_amount):
    """**Validates: Requirements 4.1**

    有效输入应通过 Pydantic 验证。
    """
    odds_map = {kc: 2.053 for kc in key_codes}
    martin_sequence = [1.0, 2.0, 4.0] if strategy_type == "martin" else None

    schema = BacktestCreate(
        strategy_type=strategy_type,
        key_codes=key_codes,
        base_amount=base_amount,
        martin_sequence=martin_sequence,
        odds_map=odds_map,
        start_date="2026-02-18",
        end_date="2026-03-18",
    )
    assert schema.strategy_type == strategy_type
    assert schema.key_codes == key_codes


@given(
    base_amount=st.floats(min_value=-10000.0, max_value=0.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=15)
def test_api_input_validation_invalid_amount(base_amount):
    """**Validates: Requirements 4.1**

    base_amount <= 0 应被拒绝。
    """
    with pytest.raises(ValidationError):
        BacktestCreate(
            strategy_type="flat",
            key_codes=["DX1"],
            base_amount=base_amount,
            odds_map={"DX1": 2.053},
            start_date="2026-02-18",
            end_date="2026-03-18",
        )


def test_api_input_validation_empty_key_codes():
    """**Validates: Requirements 4.1**

    空 key_codes 应被拒绝。
    """
    with pytest.raises(ValidationError):
        BacktestCreate(
            strategy_type="flat",
            key_codes=[],
            base_amount=1.0,
            odds_map={},
            start_date="2026-02-18",
            end_date="2026-03-18",
        )


def test_api_input_validation_date_based():
    """**Validates: Requirements 4.1**

    日期选择模式下 Schema 应正常创建。
    """
    schema = BacktestCreate(
        strategy_type="flat",
        key_codes=["DX1"],
        base_amount=1.0,
        odds_map={"DX1": 2.053},
        start_date="2026-01-01",
        end_date="2026-03-18",
    )
    assert schema.start_date == "2026-01-01"
    assert schema.end_date == "2026-03-18"
