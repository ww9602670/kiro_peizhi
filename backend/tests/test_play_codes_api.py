"""Play-codes API tests."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_list_all_groups():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/play-codes")

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    groups = body["data"]
    assert len(groups) == 10
    for group in groups:
        assert "group_name" in group
        assert "items" in group
        for item in group["items"]:
            assert "key_code" in item
            assert "name" in item


@pytest.mark.asyncio
async def test_common_only():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/play-codes", params={"common_only": "true"})

    assert resp.status_code == 200
    groups = resp.json()["data"]
    assert len(groups) == 7


@pytest.mark.asyncio
async def test_luckysb_play_codes():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/play-codes", params={"platform_type": "LUCKYSB"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    groups = body["data"]
    assert len(groups) == 10
    items = [item for group in groups for item in group["items"]]
    assert len(items) == 100
    assert items[0]["key_code"] == "LUCKYSB_B1_01"


@pytest.mark.asyncio
async def test_no_auth_required():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/play-codes")

    assert resp.status_code == 200
    assert resp.json()["code"] == 0


@pytest.mark.asyncio
async def test_dw3_play_codes_returns_16_group_tokens():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/play-codes", params={"dw3": "true"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    groups = body["data"]
    assert len(groups) == 2

    items = [item for group in groups for item in group["items"]]
    assert len(items) == 16
    key_codes = {item["key_code"] for item in items}
    assert key_codes == {
        "DW3_BS_BBB",
        "DW3_BS_BBS",
        "DW3_BS_BSB",
        "DW3_BS_BSS",
        "DW3_BS_SBB",
        "DW3_BS_SBS",
        "DW3_BS_SSB",
        "DW3_BS_SSS",
        "DW3_OE_OOO",
        "DW3_OE_OOE",
        "DW3_OE_OEO",
        "DW3_OE_OEE",
        "DW3_OE_EOO",
        "DW3_OE_EOE",
        "DW3_OE_EEO",
        "DW3_OE_EEE",
    }
