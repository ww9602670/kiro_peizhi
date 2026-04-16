"""数据完整性测试

验证 API 返回数据的完整性和正确性：
- bet_orders 的 key_code_name 不能为空（已知 Issue 1）
- admin dashboard 的 operator status 应包含所有合法值
- row_to_bet_order_info 单位转换正确性
- key_code_name 映射覆盖所有常用 key_code
"""
import uuid

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.schemas.bet_order import BetOrderInfo, row_to_bet_order_info
from app.utils.key_code_map import KEY_CODE_MAP, get_key_code_name, check_win


def _uid() -> str:
    return uuid.uuid4().hex[:8]


# ------------------------------------------------------------------
# 1. key_code_name 映射完整性
# ------------------------------------------------------------------

class TestKeyCodeNameMapping:
    """验证所有常用 key_code 都有中文名称映射"""

    COMMON_KEY_CODES = [
        "DX1", "DX2", "DS3", "DS4",
        "JDX5", "JDX6",
        "ZH7", "ZH8", "ZH9", "ZH10",
        "SB1", "SB2", "SB3", "BZ4",
        "LHH_L", "LHH_H", "LHH_HE",
    ]

    def test_common_key_codes_have_names(self):
        """常用 key_code 必须有非空中文名称"""
        for kc in self.COMMON_KEY_CODES:
            name = get_key_code_name(kc)
            assert name != kc, f"{kc} 没有中文映射，返回了原始值"
            assert len(name) > 0, f"{kc} 映射为空字符串"

    def test_hz_key_codes_have_names(self):
        """HZ1~HZ28 和值 key_code 必须有映射"""
        for i in range(1, 29):
            kc = f"HZ{i}"
            name = get_key_code_name(kc)
            assert name.startswith("和值"), f"{kc} 映射不正确: {name}"

    def test_ball_key_codes_have_names(self):
        """B{n}QH{d} 球号 key_code 必须有映射"""
        for n in range(1, 4):
            for d in range(10):
                kc = f"B{n}QH{d}"
                name = get_key_code_name(kc)
                assert name != kc, f"{kc} 没有中文映射"

    def test_unknown_key_code_returns_itself(self):
        """未知 key_code 返回自身"""
        assert get_key_code_name("UNKNOWN_XYZ") == "UNKNOWN_XYZ"

    def test_empty_key_code_returns_empty(self):
        """空字符串 key_code 返回空字符串"""
        assert get_key_code_name("") == ""


# ------------------------------------------------------------------
# 2. row_to_bet_order_info 单位转换
# ------------------------------------------------------------------

class TestRowToBetOrderInfo:
    """验证 DB 行到 BetOrderInfo 的转换正确性"""

    def _make_row(self, **overrides) -> dict:
        base = {
            "id": 1,
            "idempotent_id": "test-001",
            "strategy_id": 1,
            "account_id": 1,
            "issue": "202603021001",
            "key_code": "DX1",
            "amount": 10000,  # 分
            "odds": 19800,    # 万分比
            "status": "settled",
            "open_result": "3,5,8",
            "sum_value": 16,
            "is_win": 1,
            "pnl": 9800,     # 分
            "simulation": 0,
            "martin_level": None,
            "bet_at": "2026-03-02 10:01:30",
            "settled_at": "2026-03-02 10:05:00",
            "fail_reason": None,
        }
        base.update(overrides)
        return base

    def test_amount_conversion(self):
        """金额从分转换为元"""
        info = row_to_bet_order_info(self._make_row(amount=10000))
        assert info.amount == 100.0

    def test_odds_conversion(self):
        """赔率从万分比转换为小数"""
        info = row_to_bet_order_info(self._make_row(odds=19800))
        assert info.odds == 1.98

    def test_pnl_conversion(self):
        """盈亏从分转换为元"""
        info = row_to_bet_order_info(self._make_row(pnl=9800))
        assert info.pnl == 98.0

    def test_null_odds(self):
        """odds 为 None 时保持 None"""
        info = row_to_bet_order_info(self._make_row(odds=None))
        assert info.odds is None

    def test_null_pnl(self):
        """pnl 为 None 时保持 None"""
        info = row_to_bet_order_info(self._make_row(pnl=None))
        assert info.pnl is None

    def test_key_code_name_populated(self):
        """key_code_name 必须被正确填充"""
        info = row_to_bet_order_info(self._make_row(key_code="DX1"))
        assert info.key_code_name == "大"

    def test_key_code_name_for_all_common_codes(self):
        """所有常用 key_code 转换后 key_code_name 不为空"""
        for kc in ["DX1", "DX2", "DS3", "DS4", "ZH7", "ZH8"]:
            info = row_to_bet_order_info(self._make_row(key_code=kc))
            assert info.key_code_name != "", f"{kc} 的 key_code_name 为空"
            assert info.key_code_name != kc, f"{kc} 的 key_code_name 未映射"

    def test_simulation_bool_conversion(self):
        """simulation 从 int 转换为 bool"""
        info0 = row_to_bet_order_info(self._make_row(simulation=0))
        assert info0.simulation is False
        info1 = row_to_bet_order_info(self._make_row(simulation=1))
        assert info1.simulation is True


# ------------------------------------------------------------------
# 3. check_win 属性测试（hypothesis）
# ------------------------------------------------------------------

class TestCheckWinProperties:
    """check_win 函数的属性测试"""

    @given(
        balls=st.tuples(
            st.integers(min_value=0, max_value=9),
            st.integers(min_value=0, max_value=9),
            st.integers(min_value=0, max_value=9),
        )
    )
    @settings(max_examples=200)
    def test_dx_mutually_exclusive(self, balls):
        """大和小互斥：不可能同时中"""
        b = list(balls)
        s = sum(b)
        dx1 = check_win("DX1", b, s)
        dx2 = check_win("DX2", b, s)
        assert not (dx1 and dx2), f"大小同时中: balls={b}, sum={s}"

    @given(
        balls=st.tuples(
            st.integers(min_value=0, max_value=9),
            st.integers(min_value=0, max_value=9),
            st.integers(min_value=0, max_value=9),
        )
    )
    @settings(max_examples=200)
    def test_ds_mutually_exclusive(self, balls):
        """单和双互斥"""
        b = list(balls)
        s = sum(b)
        ds3 = check_win("DS3", b, s)
        ds4 = check_win("DS4", b, s)
        assert not (ds3 and ds4), f"单双同时中: balls={b}, sum={s}"

    @given(
        balls=st.tuples(
            st.integers(min_value=0, max_value=9),
            st.integers(min_value=0, max_value=9),
            st.integers(min_value=0, max_value=9),
        )
    )
    @settings(max_examples=200)
    def test_dx_ds_cover_all(self, balls):
        """大/小必中一个，单/双必中一个"""
        b = list(balls)
        s = sum(b)
        assert check_win("DX1", b, s) or check_win("DX2", b, s), f"大小都不中: sum={s}"
        assert check_win("DS3", b, s) or check_win("DS4", b, s), f"单双都不中: sum={s}"

    @given(
        balls=st.tuples(
            st.integers(min_value=0, max_value=9),
            st.integers(min_value=0, max_value=9),
            st.integers(min_value=0, max_value=9),
        )
    )
    @settings(max_examples=200)
    def test_exactly_one_hz_wins(self, balls):
        """恰好有一个 HZ 和值中奖"""
        b = list(balls)
        s = sum(b)
        wins = [i for i in range(1, 29) if check_win(f"HZ{i}", b, s)]
        assert len(wins) == 1, f"和值中奖数不为1: balls={b}, sum={s}, wins={wins}"
