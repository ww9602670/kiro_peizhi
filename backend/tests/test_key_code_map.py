"""
KeyCode 

get_key_code_namecheck_win 
"""
import pytest

from app.utils.key_code_map import (
    KEY_CODE_MAP,
    get_key_code_name,
    check_win,
)


# =========================================================================
# get_key_code_name
# =========================================================================

class TestGetKeyCodeName:
    """KeyCode  """

    def test_dx(self):
        assert get_key_code_name("DX1") == "大"
        assert get_key_code_name("DX2") == "小"

    def test_ds(self):
        assert get_key_code_name("DS3") == "单"
        assert get_key_code_name("DS4") == "双"

    def test_jdx(self):
        assert get_key_code_name("JDX5") == "极大"
        assert get_key_code_name("JDX6") == "极小"

    def test_zh(self):
        assert get_key_code_name("ZH7") == "大单"
        assert get_key_code_name("ZH8") == "大双"
        assert get_key_code_name("ZH9") == "小单"
        assert get_key_code_name("ZH10") == "小双"

    def test_sb(self):
        assert get_key_code_name("SB1") == "红波"
        assert get_key_code_name("SB2") == "绿波"
        assert get_key_code_name("SB3") == "蓝波"

    def test_bz(self):
        assert get_key_code_name("BZ4") == "豹子"

    def test_lhh(self):
        assert get_key_code_name("LHH_L") == "龙"
        assert get_key_code_name("LHH_H") == "虎"
        assert get_key_code_name("LHH_HE") == "和"

    def test_hz_range(self):
        """HZ1~HZ28 对应 和值0~和值27"""
        for i in range(1, 29):
            assert get_key_code_name(f"HZ{i}") == f"和值{i - 1}"

    def test_single_ball_number(self):
        """B{n}QH{d} 返回 第n球d"""
        assert get_key_code_name("B1QH0") == "第一球0"
        assert get_key_code_name("B2QH5") == "第二球5"
        assert get_key_code_name("B3QH9") == "第三球9"

    def test_single_ball_lm(self):
        """B{n}LM_{suffix} 返回 第n球大/小/单/双"""
        assert get_key_code_name("B1LM_DA") == "第一球大"
        assert get_key_code_name("B1LM_X") == "第一球小"
        assert get_key_code_name("B2LM_D") == "第二球单"
        assert get_key_code_name("B3LM_S") == "第三球双"

    def test_unknown_returns_original(self):
        """ KeyCode """
        assert get_key_code_name("UNKNOWN") == "UNKNOWN"
        assert get_key_code_name("") == ""

    def test_map_completeness(self):
        """2+2+2+4+3+1+3+28+30+12 = 87 """
        assert len(KEY_CODE_MAP) >= 87


# =========================================================================
# check_win  
# =========================================================================

class TestCheckWinDX:
    def test_da_win(self):
        assert check_win("DX1", [5, 5, 4], 14) is True

    def test_da_lose(self):
        assert check_win("DX1", [5, 5, 3], 13) is False

    def test_xiao_win(self):
        assert check_win("DX2", [5, 5, 3], 13) is True

    def test_xiao_lose(self):
        assert check_win("DX2", [5, 5, 4], 14) is False

    def test_boundary_14(self):
        """=14  """
        assert check_win("DX1", [5, 5, 4], 14) is True
        assert check_win("DX2", [5, 5, 4], 14) is False

    def test_boundary_13(self):
        """=13  """
        assert check_win("DX1", [5, 5, 3], 13) is False
        assert check_win("DX2", [5, 5, 3], 13) is True


# =========================================================================
# check_win  
# =========================================================================

class TestCheckWinDS:
    def test_dan_win(self):
        assert check_win("DS3", [5, 5, 3], 13) is True  # 13 

    def test_dan_lose(self):
        assert check_win("DS3", [5, 5, 4], 14) is False  # 14 

    def test_shuang_win(self):
        assert check_win("DS4", [5, 5, 4], 14) is True

    def test_shuang_lose(self):
        assert check_win("DS4", [5, 5, 3], 13) is False


# =========================================================================
# check_win  
# =========================================================================

class TestCheckWinJDX:
    def test_jida_win(self):
        assert check_win("JDX5", [8, 7, 7], 22) is True

    def test_jida_boundary(self):
        assert check_win("JDX5", [7, 8, 6], 21) is False

    def test_jixiao_win(self):
        assert check_win("JDX6", [1, 2, 2], 5) is True

    def test_jixiao_boundary(self):
        assert check_win("JDX6", [2, 2, 2], 6) is False


# =========================================================================
# check_win  
# =========================================================================

class TestCheckWinZH:
    def test_dadan(self):
        """: sum>=14  """
        assert check_win("ZH7", [5, 5, 5], 15) is True
        assert check_win("ZH7", [5, 5, 4], 14) is False  # 14 

    def test_dashuang(self):
        """: sum>=14  """
        assert check_win("ZH8", [5, 5, 4], 14) is True
        assert check_win("ZH8", [5, 5, 5], 15) is False  # 15 

    def test_xiaodan(self):
        """: sum<=13  """
        assert check_win("ZH9", [5, 5, 3], 13) is True
        assert check_win("ZH9", [5, 5, 2], 12) is False  # 12 

    def test_xiaoshuang(self):
        """: sum<=13  """
        assert check_win("ZH10", [5, 5, 2], 12) is True
        assert check_win("ZH10", [5, 5, 3], 13) is False  # 13 


# =========================================================================
# check_win  
# =========================================================================

class TestCheckWinHZ:
    def test_hz1_sum0(self):
        """HZ1  0"""
        assert check_win("HZ1", [0, 0, 0], 0) is True
        assert check_win("HZ1", [0, 0, 1], 1) is False

    def test_hz14_sum13(self):
        """HZ14  13"""
        assert check_win("HZ14", [5, 5, 3], 13) is True
        assert check_win("HZ14", [5, 5, 4], 14) is False

    def test_hz28_sum27(self):
        """HZ28  27"""
        assert check_win("HZ28", [9, 9, 9], 27) is True
        assert check_win("HZ28", [9, 9, 8], 26) is False

    def test_all_hz_exactly_one_match(self):
        """ HZ """
        for s in range(28):
            wins = [check_win(f"HZ{i}", [0, 0, s], s) for i in range(1, 29)]
            assert sum(wins) == 1, f"{s}  HZ"


# =========================================================================
# check_win  
# =========================================================================

class TestCheckWinSB:
    """// + (0,13,14,27)"""

    @pytest.mark.parametrize("s", [3, 6, 9, 12, 15, 18, 21, 24])
    def test_red(self, s):
        assert check_win("SB1", [0, 0, s], s) is True
        assert check_win("SB2", [0, 0, s], s) is False
        assert check_win("SB3", [0, 0, s], s) is False

    @pytest.mark.parametrize("s", [1, 4, 7, 10, 16, 19, 22, 25])
    def test_green(self, s):
        assert check_win("SB2", [0, 0, s], s) is True
        assert check_win("SB1", [0, 0, s], s) is False
        assert check_win("SB3", [0, 0, s], s) is False

    @pytest.mark.parametrize("s", [2, 5, 8, 11, 17, 20, 23, 26])
    def test_blue(self, s):
        assert check_win("SB3", [0, 0, s], s) is True
        assert check_win("SB1", [0, 0, s], s) is False
        assert check_win("SB2", [0, 0, s], s) is False

    @pytest.mark.parametrize("s", [0, 13, 14, 27])
    def test_no_color(self, s):
        """"""
        assert check_win("SB1", [0, 0, s], s) is False
        assert check_win("SB2", [0, 0, s], s) is False
        assert check_win("SB3", [0, 0, s], s) is False


# =========================================================================
# check_win  
# =========================================================================

class TestCheckWinBZ:
    def test_baozi_win(self):
        assert check_win("BZ4", [3, 3, 3], 9) is True

    def test_baozi_lose(self):
        assert check_win("BZ4", [3, 3, 4], 10) is False

    def test_baozi_all_zeros(self):
        assert check_win("BZ4", [0, 0, 0], 0) is True


# =========================================================================
# check_win  
# =========================================================================

class TestCheckWinBallNumber:
    def test_b1qh(self):
        assert check_win("B1QH3", [3, 5, 7], 15) is True
        assert check_win("B1QH3", [4, 5, 7], 16) is False

    def test_b2qh(self):
        assert check_win("B2QH5", [3, 5, 7], 15) is True
        assert check_win("B2QH5", [3, 6, 7], 16) is False

    def test_b3qh(self):
        assert check_win("B3QH7", [3, 5, 7], 15) is True
        assert check_win("B3QH7", [3, 5, 8], 16) is False

    def test_b1qh0(self):
        assert check_win("B1QH0", [0, 5, 7], 12) is True


# =========================================================================
# check_win  
# =========================================================================

class TestCheckWinBallLM:
    def test_b1_da(self):
        """: ball >= 5"""
        assert check_win("B1LM_DA", [5, 0, 0], 5) is True
        assert check_win("B1LM_DA", [4, 0, 0], 4) is False

    def test_b1_xiao(self):
        """: ball < 5"""
        assert check_win("B1LM_X", [4, 0, 0], 4) is True
        assert check_win("B1LM_X", [5, 0, 0], 5) is False

    def test_b2_dan(self):
        """: ball """
        assert check_win("B2LM_D", [0, 3, 0], 3) is True
        assert check_win("B2LM_D", [0, 4, 0], 4) is False

    def test_b3_shuang(self):
        """: ball """
        assert check_win("B3LM_S", [0, 0, 6], 6) is True
        assert check_win("B3LM_S", [0, 0, 7], 7) is False

    def test_b1_xiao_zero(self):
        """0 """
        assert check_win("B1LM_X", [0, 0, 0], 0) is True

    def test_b1_shuang_zero(self):
        """0 """
        assert check_win("B1LM_S", [0, 0, 0], 0) is True


# =========================================================================
# check_win  
# =========================================================================

class TestCheckWinLHH:
    def test_long(self):
        """: ball1 > ball3"""
        assert check_win("LHH_L", [7, 5, 3], 15) is True
        assert check_win("LHH_L", [3, 5, 7], 15) is False

    def test_hu(self):
        """: ball1 < ball3"""
        assert check_win("LHH_H", [3, 5, 7], 15) is True
        assert check_win("LHH_H", [7, 5, 3], 15) is False

    def test_he(self):
        """: ball1 == ball3"""
        assert check_win("LHH_HE", [5, 3, 5], 13) is True
        assert check_win("LHH_HE", [5, 3, 6], 14) is False


# =========================================================================
# check_win   KeyCode
# =========================================================================

class TestCheckWinUnknown:
    def test_unknown_returns_false(self):
        assert check_win("UNKNOWN", [1, 2, 3], 6) is False

    def test_empty_returns_false(self):
        assert check_win("", [1, 2, 3], 6) is False


# =========================================================================
# PLAY_CODE_GROUPS / COMMON_GROUPS 分组常量验证
# =========================================================================

from app.utils.key_code_map import PLAY_CODE_GROUPS, COMMON_GROUPS


class TestPlayCodeGroups:
    """验证分组常量的完整性和一致性"""

    def test_group_count(self):
        """应包含 10 个分组"""
        assert len(PLAY_CODE_GROUPS) == 10

    def test_group_order(self):
        """分组顺序：大小、单双、极值、组合、色波、豹子、龙虎和、和值、单球猜号、单球大小单双"""
        expected = ["大小", "单双", "极值", "组合", "色波", "豹子", "龙虎和", "和值", "单球猜号", "单球大小单双"]
        actual = [g["group_name"] for g in PLAY_CODE_GROUPS]
        assert actual == expected

    def test_total_items_87(self):
        """全部分组合计 87 种玩法"""
        total = sum(len(g["items"]) for g in PLAY_CODE_GROUPS)
        assert total == 87

    def test_all_key_codes_in_map(self):
        """每个 item 的 key_code 必须在 KEY_CODE_MAP 中存在"""
        for group in PLAY_CODE_GROUPS:
            for item in group["items"]:
                assert item["key_code"] in KEY_CODE_MAP, (
                    f"{group['group_name']} 中 {item['key_code']} 不在 KEY_CODE_MAP"
                )

    def test_names_match_map(self):
        """每个 item 的 name 必须与 KEY_CODE_MAP 中的值一致"""
        for group in PLAY_CODE_GROUPS:
            for item in group["items"]:
                assert item["name"] == KEY_CODE_MAP[item["key_code"]], (
                    f"{item['key_code']}: 分组中 '{item['name']}' != MAP 中 '{KEY_CODE_MAP[item['key_code']]}'"
                )

    def test_no_duplicate_key_codes(self):
        """分组中不应有重复的 key_code"""
        all_codes = [item["key_code"] for g in PLAY_CODE_GROUPS for item in g["items"]]
        assert len(all_codes) == len(set(all_codes))

    def test_common_groups_count(self):
        """常用分组应包含 7 个"""
        assert len(COMMON_GROUPS) == 7

    def test_common_groups_are_valid(self):
        """常用分组名称必须在 PLAY_CODE_GROUPS 中存在"""
        all_group_names = {g["group_name"] for g in PLAY_CODE_GROUPS}
        assert COMMON_GROUPS.issubset(all_group_names)

    def test_common_groups_content(self):
        """常用分组应为前 7 个分组"""
        expected = {"大小", "单双", "极值", "组合", "色波", "豹子", "龙虎和"}
        assert COMMON_GROUPS == expected


# =========================================================================
# 属性测试：PLAY_CODE_GROUPS 一致性
# =========================================================================

from hypothesis import given, strategies as st
from app.utils.key_code_map import PLAY_CODE_GROUPS, COMMON_GROUPS


# 构建所有 (group_index, item_index) 对的策略
_all_group_item_pairs = [
    (gi, ii)
    for gi, group in enumerate(PLAY_CODE_GROUPS)
    for ii in range(len(group["items"]))
]


class TestPlayCodeGroupsProperties:
    """PLAY_CODE_GROUPS 属性测试

    **Validates: Requirements 6.1, 8.3**
    """

    @given(pair=st.sampled_from(_all_group_item_pairs))
    def test_all_group_items_exist_in_key_code_map(self, pair):
        """属性：PLAY_CODE_GROUPS 中每个 item 的 key_code 必须在 KEY_CODE_MAP 中存在，
        且 name 与 get_key_code_name 返回值一致。"""
        gi, ii = pair
        item = PLAY_CODE_GROUPS[gi]["items"][ii]
        key_code = item["key_code"]
        name = item["name"]
        assert key_code in KEY_CODE_MAP, f"{key_code} 不在 KEY_CODE_MAP 中"
        assert get_key_code_name(key_code) == name, (
            f"{key_code}: 分组中 name={name}, get_key_code_name 返回 {get_key_code_name(key_code)}"
        )

    def test_groups_count(self):
        """PLAY_CODE_GROUPS 包含 10 个分组"""
        assert len(PLAY_CODE_GROUPS) == 10

    def test_total_items_count(self):
        """所有分组合计 87 种玩法"""
        total = sum(len(g["items"]) for g in PLAY_CODE_GROUPS)
        assert total == 87

    def test_common_groups_count(self):
        """COMMON_GROUPS 包含 7 个常用分组"""
        assert len(COMMON_GROUPS) == 7

    def test_common_groups_are_valid(self):
        """COMMON_GROUPS 中的名称都在 PLAY_CODE_GROUPS 的 group_name 中"""
        group_names = {g["group_name"] for g in PLAY_CODE_GROUPS}
        for name in COMMON_GROUPS:
            assert name in group_names, f"常用分组 '{name}' 不在 PLAY_CODE_GROUPS 中"

    def test_group_order(self):
        """分组顺序：大小、单双、极值、组合、色波、豹子、龙虎和、和值、单球猜号、单球大小单双"""
        expected = ["大小", "单双", "极值", "组合", "色波", "豹子", "龙虎和", "和值", "单球猜号", "单球大小单双"]
        actual = [g["group_name"] for g in PLAY_CODE_GROUPS]
        assert actual == expected
