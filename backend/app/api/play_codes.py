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
        "group_name": "DW3 Big/Small",
        "items": [
            {"key_code": "DW3_BS_BBB", "name": "Big-Big-Big"},
            {"key_code": "DW3_BS_BBS", "name": "Big-Big-Small"},
            {"key_code": "DW3_BS_BSB", "name": "Big-Small-Big"},
            {"key_code": "DW3_BS_BSS", "name": "Big-Small-Small"},
            {"key_code": "DW3_BS_SBB", "name": "Small-Big-Big"},
            {"key_code": "DW3_BS_SBS", "name": "Small-Big-Small"},
            {"key_code": "DW3_BS_SSB", "name": "Small-Small-Big"},
            {"key_code": "DW3_BS_SSS", "name": "Small-Small-Small"},
        ],
    },
    {
        "group_name": "DW3 Odd/Even",
        "items": [
            {"key_code": "DW3_OE_OOO", "name": "Odd-Odd-Odd"},
            {"key_code": "DW3_OE_OOE", "name": "Odd-Odd-Even"},
            {"key_code": "DW3_OE_OEO", "name": "Odd-Even-Odd"},
            {"key_code": "DW3_OE_OEE", "name": "Odd-Even-Even"},
            {"key_code": "DW3_OE_EOO", "name": "Even-Odd-Odd"},
            {"key_code": "DW3_OE_EOE", "name": "Even-Odd-Even"},
            {"key_code": "DW3_OE_EEO", "name": "Even-Even-Odd"},
            {"key_code": "DW3_OE_EEE", "name": "Even-Even-Even"},
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
