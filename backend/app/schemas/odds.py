"""赔率相关 Schema（Pydantic v2）

OddsItem            单条赔率记录
OddsListResponse    赔率列表响应
OddsConfirmResponse 赔率确认响应
OddsRefreshResponse 赔率刷新响应（含期号状态）
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class OddsItem(BaseModel):
    """单条赔率记录，与 account_odds 表一一对应。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "key_code": "DX1",
                "odds_value": 20530,
                "confirmed": True,
                "fetched_at": "2025-01-15 10:30:00",
                "confirmed_at": "2025-01-15 10:35:00",
            }
        }
    )

    key_code: str = Field(..., description="玩法编码")
    odds_value: int = Field(..., ge=1, le=9999999, description="赔率值 ×10000")
    confirmed: bool = Field(..., description="是否已确认")
    fetched_at: str = Field(..., description="获取时间 UTC")
    confirmed_at: str | None = Field(None, description="确认时间 UTC，未确认时为 null")


class OddsListResponse(BaseModel):
    """赔率列表响应。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "account_id": 363,
                "items": [
                    {
                        "key_code": "DX1",
                        "odds_value": 20530,
                        "confirmed": True,
                        "fetched_at": "2025-01-15 10:30:00",
                        "confirmed_at": "2025-01-15 10:35:00",
                    }
                ],
                "has_unconfirmed": False,
            }
        }
    )

    account_id: int
    items: list[OddsItem]
    has_unconfirmed: bool = Field(..., description="是否存在未确认赔率")


class OddsConfirmResponse(BaseModel):
    """赔率确认响应。"""

    model_config = ConfigDict(
        json_schema_extra={"example": {"confirmed_count": 5}}
    )

    confirmed_count: int = Field(..., description="本次确认的记录数")


class PeriodInfo(BaseModel):
    """当前期号信息。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "issue": "3405106",
                "state": 1,
                "state_label": "开盘中",
                "close_countdown_sec": 31,
                "open_countdown_sec": 51,
                "pre_issue": "3405105",
                "pre_result": "7,5,0",
            }
        }
    )

    issue: str = Field(..., description="当前期号")
    state: int = Field(..., description="状态 1=开盘 其他=封盘")
    state_label: str = Field(..., description="状态中文标签")
    close_countdown_sec: int = Field(..., description="距封盘秒数")
    open_countdown_sec: int = Field(0, description="距开奖秒数")
    pre_issue: str = Field("", description="上期期号")
    pre_result: str = Field("", description="上期开奖结果")


class OddsRefreshResponse(BaseModel):
    """赔率刷新响应，包含期号状态和赔率数据。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "account_id": 363,
                "period": {
                    "issue": "3405106",
                    "state": 1,
                    "state_label": "开盘中",
                    "close_countdown_sec": 31,
                    "open_countdown_sec": 51,
                    "pre_issue": "3405105",
                    "pre_result": "7,5,0",
                },
                "odds_count": 88,
                "synced": True,
                "message": "赔率获取成功，共88项",
            }
        }
    )

    account_id: int
    period: PeriodInfo | None = Field(None, description="期号信息，获取失败时为 null")
    odds_count: int = Field(0, description="本次获取的赔率数量")
    synced: bool = Field(False, description="是否成功同步到数据库")
    message: str = Field("", description="操作结果描述")
