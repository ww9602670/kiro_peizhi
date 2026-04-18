"""Odds API schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OddsItem(BaseModel):
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

    key_code: str
    odds_value: int = Field(..., ge=1, le=9_999_999)
    confirmed: bool
    fetched_at: str
    confirmed_at: str | None = None


class OddsListResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "account_id": 363,
                "platform_type": "JND28WEB",
                "items": [],
                "has_unconfirmed": False,
            }
        }
    )

    account_id: int
    platform_type: str
    items: list[OddsItem]
    has_unconfirmed: bool


class OddsConfirmResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"confirmed_count": 5}})

    confirmed_count: int


class PeriodInfo(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "issue": "3405106",
                "state": 1,
                "state_label": "open",
                "close_countdown_sec": 31,
                "open_countdown_sec": 51,
                "pre_issue": "3405105",
                "pre_result": "7,5,0",
            }
        }
    )

    issue: str
    state: int
    state_label: str
    close_countdown_sec: int
    open_countdown_sec: int = 0
    pre_issue: str = ""
    pre_result: str = ""


class OddsRefreshResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "account_id": 363,
                "platform_type": "JND28WEB",
                "period": None,
                "odds_count": 88,
                "odds_synced": True,
                "odds_message": "odds synced",
                "synced": True,
                "message": "odds synced",
            }
        }
    )

    account_id: int
    platform_type: str
    period: PeriodInfo | None = None
    odds_count: int = 0
    odds_synced: bool = False
    odds_message: str = ""
    synced: bool = False
    message: str = ""

    @model_validator(mode="after")
    def _sync_compat_fields(self):
        self.synced = self.odds_synced
        self.message = self.odds_message
        return self

