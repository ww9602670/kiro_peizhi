"""
Task 1 Playwright 校验：后端 — 新增玩法分组常量

使用 Playwright 的 APIRequestContext 对后端 API 进行端到端校验，
同时验证 Python 常量层面的数据完整性。

校验项：
  1. PLAY_CODE_GROUPS 包含 10 个分组，87 种玩法
  2. 分组顺序：大小、单双、极值、组合、色波、豹子、龙虎和、和值、单球猜号、单球大小单双
  3. 所有 items 的 key_code 在 KEY_CODE_MAP 中存在
  4. 所有 items 的 name 与 KEY_CODE_MAP 中的值一致
  5. COMMON_GROUPS 包含 7 个常用分组名称
  6. API /api/v1/play-codes 返回与常量一致的数据
  7. API /api/v1/play-codes?common_only=true 仅返回常用分组
"""
import pytest
from playwright.sync_api import Playwright, sync_playwright

pytestmark = pytest.mark.e2e

# ── 常量层校验 ──

from app.utils.key_code_map import (
    KEY_CODE_MAP,
    PLAY_CODE_GROUPS,
    COMMON_GROUPS,
    get_key_code_name,
)

EXPECTED_GROUP_ORDER = [
    "大小", "单双", "极值", "组合", "色波", "豹子", "龙虎和",
    "和值", "单球猜号", "单球大小单双",
]

BASE_URL = "http://localhost:8888"
API_PREFIX = "/api/v1"


class TestTask1Constants:
    """纯 Python 常量层校验"""

    def test_group_count_is_10(self):
        assert len(PLAY_CODE_GROUPS) == 10, f"期望 10 个分组，实际 {len(PLAY_CODE_GROUPS)}"

    def test_total_items_is_87(self):
        total = sum(len(g["items"]) for g in PLAY_CODE_GROUPS)
        assert total == 87, f"期望 87 种玩法，实际 {total}"

    def test_group_order(self):
        actual = [g["group_name"] for g in PLAY_CODE_GROUPS]
        assert actual == EXPECTED_GROUP_ORDER, f"分组顺序不匹配：{actual}"

    def test_all_key_codes_exist_in_map(self):
        missing = []
        for group in PLAY_CODE_GROUPS:
            for item in group["items"]:
                if item["key_code"] not in KEY_CODE_MAP:
                    missing.append(item["key_code"])
        assert not missing, f"以下 key_code 不在 KEY_CODE_MAP 中：{missing}"

    def test_all_names_match_map(self):
        mismatches = []
        for group in PLAY_CODE_GROUPS:
            for item in group["items"]:
                expected_name = KEY_CODE_MAP.get(item["key_code"])
                if expected_name != item["name"]:
                    mismatches.append(
                        f"{item['key_code']}: 分组中={item['name']}, MAP中={expected_name}"
                    )
        assert not mismatches, f"名称不匹配：{mismatches}"

    def test_no_duplicate_key_codes(self):
        all_codes = [
            item["key_code"]
            for group in PLAY_CODE_GROUPS
            for item in group["items"]
        ]
        dupes = [c for c in all_codes if all_codes.count(c) > 1]
        assert not dupes, f"重复的 key_code：{set(dupes)}"

    def test_common_groups_count_is_7(self):
        assert len(COMMON_GROUPS) == 7, f"期望 7 个常用分组，实际 {len(COMMON_GROUPS)}"

    def test_common_groups_are_valid(self):
        all_group_names = {g["group_name"] for g in PLAY_CODE_GROUPS}
        invalid = COMMON_GROUPS - all_group_names
        assert not invalid, f"COMMON_GROUPS 中有无效分组名：{invalid}"

    def test_common_groups_content(self):
        expected = {"大小", "单双", "极值", "组合", "色波", "豹子", "龙虎和"}
        assert COMMON_GROUPS == expected, f"COMMON_GROUPS 内容不匹配：{COMMON_GROUPS}"

    def test_get_key_code_name_for_all_items(self):
        """验证 get_key_code_name 对所有分组中的 key_code 返回非空中文名称"""
        empty_names = []
        for group in PLAY_CODE_GROUPS:
            for item in group["items"]:
                name = get_key_code_name(item["key_code"])
                if not name or name == item["key_code"]:
                    empty_names.append(item["key_code"])
        # get_key_code_name 应返回中文名称而非原始 key_code
        # 但如果 key_code 本身就在 MAP 中，返回值应该是中文名
        # 这里只检查返回值非空
        for group in PLAY_CODE_GROUPS:
            for item in group["items"]:
                name = get_key_code_name(item["key_code"])
                assert name, f"{item['key_code']} 的 get_key_code_name 返回空"
                assert name == item["name"], (
                    f"{item['key_code']}: get_key_code_name={name}, 期望={item['name']}"
                )

    def test_unknown_key_code_returns_original(self):
        """未知 KeyCode 应返回原始值"""
        assert get_key_code_name("UNKNOWN_XYZ") == "UNKNOWN_XYZ"
        assert get_key_code_name("") == ""


class TestTask1API:
    """通过 Playwright APIRequestContext 校验后端 API

    使用 sync_playwright 直接在测试函数内创建上下文，
    避免与 pytest-asyncio 的事件循环冲突。
    """

    def _get_api_data(self, path: str = "/play-codes") -> dict:
        """辅助方法：调用 API 并返回解析后的 JSON"""
        with sync_playwright() as p:
            ctx = p.request.new_context(base_url=BASE_URL)
            url = f"{API_PREFIX}{path}"
            resp = ctx.get(url)
            assert resp.ok, f"API 返回 {resp.status}: {resp.text()}"
            body = resp.json()
            ctx.dispose()
            return body

    def test_play_codes_api_returns_all_groups(self):
        """GET /play-codes 返回 10 个分组"""
        body = self._get_api_data("/play-codes")
        assert body["code"] == 0
        data = body["data"]
        assert len(data) == 10, f"期望 10 个分组，API 返回 {len(data)}"

    def test_play_codes_api_group_order(self):
        """API 返回的分组顺序与常量一致"""
        data = self._get_api_data("/play-codes")["data"]
        api_order = [g["group_name"] for g in data]
        assert api_order == EXPECTED_GROUP_ORDER

    def test_play_codes_api_total_items(self):
        """API 返回的总玩法数为 87"""
        data = self._get_api_data("/play-codes")["data"]
        total = sum(len(g["items"]) for g in data)
        assert total == 87, f"API 返回 {total} 种玩法，期望 87"

    def test_play_codes_api_item_structure(self):
        """每个 item 包含 key_code 和 name 字段"""
        data = self._get_api_data("/play-codes")["data"]
        for group in data:
            assert "group_name" in group
            assert "items" in group
            for item in group["items"]:
                assert "key_code" in item, f"缺少 key_code: {item}"
                assert "name" in item, f"缺少 name: {item}"
                assert isinstance(item["key_code"], str)
                assert isinstance(item["name"], str)
                assert len(item["name"]) > 0, f"name 为空: {item}"

    def test_play_codes_api_consistency_with_constants(self):
        """API 返回数据与 PLAY_CODE_GROUPS 常量完全一致"""
        data = self._get_api_data("/play-codes")["data"]
        for i, group in enumerate(data):
            const_group = PLAY_CODE_GROUPS[i]
            assert group["group_name"] == const_group["group_name"], (
                f"分组 {i}: API={group['group_name']}, 常量={const_group['group_name']}"
            )
            assert len(group["items"]) == len(const_group["items"]), (
                f"分组 {group['group_name']}: API={len(group['items'])} items, "
                f"常量={len(const_group['items'])} items"
            )
            for j, item in enumerate(group["items"]):
                const_item = const_group["items"][j]
                assert item["key_code"] == const_item["key_code"]
                assert item["name"] == const_item["name"]

    def test_play_codes_api_common_only(self):
        """common_only=true 仅返回 7 个常用分组"""
        data = self._get_api_data("/play-codes?common_only=true")["data"]
        assert len(data) == 7, f"common_only=true 期望 7 个分组，实际 {len(data)}"
        api_names = {g["group_name"] for g in data}
        assert api_names == COMMON_GROUPS, f"常用分组不匹配：{api_names}"

    def test_play_codes_api_no_auth_required(self):
        """无需鉴权即可访问"""
        with sync_playwright() as p:
            ctx = p.request.new_context(base_url=BASE_URL)
            resp = ctx.get(f"{API_PREFIX}/play-codes")
            assert resp.status == 200, f"无鉴权访问返回 {resp.status}"
            ctx.dispose()
