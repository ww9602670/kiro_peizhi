"""玩法列表 API 测试"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_list_all_groups():
    """GET /play-codes 返回全部 10 个分组"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/play-codes")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    groups = body["data"]
    assert len(groups) == 10
    # 验证分组顺序
    names = [g["group_name"] for g in groups]
    assert names == ["大小", "单双", "极值", "组合", "色波", "豹子", "龙虎和", "和值", "单球猜号", "单球大小单双"]
    # 验证每个 item 结构
    for g in groups:
        assert "group_name" in g
        assert "items" in g
        for item in g["items"]:
            assert "key_code" in item
            assert "name" in item


@pytest.mark.asyncio
async def test_common_only():
    """GET /play-codes?common_only=true 仅返回 7 个常用分组"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/play-codes", params={"common_only": "true"})
    assert resp.status_code == 200
    body = resp.json()
    groups = body["data"]
    assert len(groups) == 7
    names = {g["group_name"] for g in groups}
    assert names == {"大小", "单双", "极值", "组合", "色波", "豹子", "龙虎和"}


@pytest.mark.asyncio
async def test_no_auth_required():
    """无需鉴权即可访问"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/play-codes")
    # 不带任何 token 也应返回 200
    assert resp.status_code == 200
    assert resp.json()["code"] == 0
