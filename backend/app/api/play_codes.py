"""
玩法列表 API

GET /play-codes            返回全部 10 个分组
GET /play-codes?common_only=true  仅返回 7 个常用分组
"""
from fastapi import APIRouter, Query

from app.schemas.common import ApiResponse
from app.utils.key_code_map import COMMON_GROUPS, PLAY_CODE_GROUPS

router = APIRouter()


@router.get("/play-codes", response_model=ApiResponse[list[dict]])
async def list_play_codes(common_only: bool = Query(False)):
    """返回按分组排列的玩法列表"""
    if common_only:
        data = [g for g in PLAY_CODE_GROUPS if g["group_name"] in COMMON_GROUPS]
    else:
        data = PLAY_CODE_GROUPS
    return ApiResponse(data=data)
