"""Play-code list API."""

from fastapi import APIRouter, Query

from app.schemas.common import ApiResponse
from app.utils.key_code_map import COMMON_GROUPS, PLAY_CODE_GROUPS
from app.utils.luckysb_play_codes import (
    LUCKYSB_PLATFORM_TYPE,
    LUCKYSB_PLAY_CODE_GROUPS,
)

router = APIRouter()

DW3_PLAY_CODE_GROUPS: list[dict] = [
    {
        "group_name": "三字定位大小组",
        "items": [
            {"key_code": "DW3_BS_BBB", "name": "大大大"},
            {"key_code": "DW3_BS_BBS", "name": "大大小"},
            {"key_code": "DW3_BS_BSB", "name": "大小大"},
            {"key_code": "DW3_BS_BSS", "name": "大小小"},
            {"key_code": "DW3_BS_SBB", "name": "小大大"},
            {"key_code": "DW3_BS_SBS", "name": "小大小"},
            {"key_code": "DW3_BS_SSB", "name": "小小大"},
            {"key_code": "DW3_BS_SSS", "name": "小小小"},
        ],
    },
    {
        "group_name": "三字定位单双组",
        "items": [
            {"key_code": "DW3_OE_OOO", "name": "单单单"},
            {"key_code": "DW3_OE_OOE", "name": "单单双"},
            {"key_code": "DW3_OE_OEO", "name": "单双单"},
            {"key_code": "DW3_OE_OEE", "name": "单双双"},
            {"key_code": "DW3_OE_EOO", "name": "双单单"},
            {"key_code": "DW3_OE_EOE", "name": "双单双"},
            {"key_code": "DW3_OE_EEO", "name": "双双单"},
            {"key_code": "DW3_OE_EEE", "name": "双双双"},
        ],
    },
]


@router.get("/play-codes", response_model=ApiResponse[list[dict]])
async def list_play_codes(
    common_only: bool = Query(False),
    platform_type: str | None = Query(None),
    dw3: bool = Query(False),
):
    if dw3:
        return ApiResponse(data=DW3_PLAY_CODE_GROUPS)

    if (platform_type or "").upper() == LUCKYSB_PLATFORM_TYPE:
        return ApiResponse(data=LUCKYSB_PLAY_CODE_GROUPS)

    if common_only:
        data = [group for group in PLAY_CODE_GROUPS if group["group_name"] in COMMON_GROUPS]
    else:
        data = PLAY_CODE_GROUPS
    return ApiResponse(data=data)
