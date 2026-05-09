"""Lottery API schemas."""
from enum import Enum, IntEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LotteryState(IntEnum):
    """Normalized lottery market state."""

    UNKNOWN = 0
    OPEN = 1
    CLOSED = 2
    DRAWING = 3


class MarketDataState(str, Enum):
    """Public state of current market data source."""

    SHARED_OK = "shared_ok"
    SHARED_ERROR = "shared_error"
    SHARED_STALE = "shared_stale"
    MARKET_CLOSED = "market_closed"


class DrawState(str, Enum):
    """Draw refresh state in shared snapshot."""

    NORMAL = "normal"
    DRAW_PENDING = "draw_pending"
    DRAW_WAIT_RETRY = "draw_wait_retry"


class CurrentInstallResponse(BaseModel):
    """Current issue payload for countdown display."""

    model_config = ConfigDict(
        validate_assignment=True,
        json_schema_extra={
            "example": {
                "installments": "3403606",
                "state": 1,
                "close_countdown_sec": 149,
                "open_countdown_sec": 159,
                "pre_lottery_result": "0,3,0",
                "pre_installments": "3403605",
                "template_code": "JNDPCDD",
                "market_data_state": "shared_ok",
                "draw_state": "normal",
                "next_normal_refresh_at": "2026-05-03 10:00:25",
                "next_draw_retry_at": "2026-05-03 10:00:10",
                "snapshot_version": 12,
                "message_code": "SHARED-002",
                "message_text": "数据更新变慢，可能影响投注，请联系管理员处理。",
            }
        }
    )

    installments: str = Field(..., description="Current issue number")
    state: LotteryState = Field(..., description="0=unknown,1=open,2=closed,3=drawing")
    close_countdown_sec: int = Field(..., ge=0, description="Seconds until close")
    open_countdown_sec: int = Field(..., ge=0, description="Seconds until draw")
    pre_lottery_result: str = Field(..., description="Previous lottery result")
    pre_installments: str = Field(..., description="Previous issue number")
    template_code: str = Field(..., description="Template code")
    market_data_state: MarketDataState = Field(
        default=MarketDataState.SHARED_OK,
        description="Public market data state: shared_ok/shared_error/shared_stale/market_closed",
    )
    draw_state: DrawState = Field(
        default=DrawState.NORMAL,
        description="Draw refresh state: normal/draw_pending/draw_wait_retry",
    )
    next_normal_refresh_at: str | None = Field(
        default=None,
        description="Next scheduled normal refresh timestamp",
    )
    next_draw_retry_at: str | None = Field(
        default=None,
        description="Next draw retry timestamp",
    )
    snapshot_version: int = Field(
        default=0,
        ge=0,
        description="Monotonic snapshot version",
    )
    message_code: str | None = Field(
        default=None,
        description="Operator-facing message code",
    )
    message_text: str | None = Field(
        default=None,
        description="Operator-facing message text",
    )

    @field_validator("market_data_state", mode="before")
    @classmethod
    def _normalize_market_data_state(cls, value: object) -> str:
        normalized = str(value or "").strip().lower()
        if normalized == "shared_hit":
            return MarketDataState.SHARED_OK.value
        if normalized == "local_fallback":
            return MarketDataState.SHARED_ERROR.value
        if normalized == "processing":
            return MarketDataState.SHARED_OK.value
        if normalized in {
            MarketDataState.SHARED_OK.value,
            MarketDataState.SHARED_ERROR.value,
            MarketDataState.SHARED_STALE.value,
            MarketDataState.MARKET_CLOSED.value,
        }:
            return normalized
        return MarketDataState.SHARED_OK.value

    @field_validator("draw_state", mode="before")
    @classmethod
    def _normalize_draw_state(cls, value: object) -> str:
        normalized = str(value or "").strip().lower()
        if normalized == "processing":
            return DrawState.DRAW_PENDING.value
        if normalized in {
            DrawState.NORMAL.value,
            DrawState.DRAW_PENDING.value,
            DrawState.DRAW_WAIT_RETRY.value,
        }:
            return normalized
        return DrawState.NORMAL.value
