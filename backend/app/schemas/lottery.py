"""Lottery API schemas."""
from enum import Enum, IntEnum

from pydantic import BaseModel, ConfigDict, Field


class LotteryState(IntEnum):
    """Normalized lottery market state."""

    UNKNOWN = 0
    OPEN = 1
    CLOSED = 2
    DRAWING = 3


class MarketDataState(str, Enum):
    """Public state of current market data source."""

    SHARED_HIT = "shared_hit"
    LOCAL_FALLBACK = "local_fallback"
    PROCESSING = "processing"


class CurrentInstallResponse(BaseModel):
    """Current issue payload for countdown display."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "installments": "3403606",
                "state": 1,
                "close_countdown_sec": 149,
                "open_countdown_sec": 159,
                "pre_lottery_result": "0,3,0",
                "pre_installments": "3403605",
                "template_code": "JNDPCDD",
                "market_data_state": "shared_hit",
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
        default=MarketDataState.LOCAL_FALLBACK,
        description="Public market data state: shared_hit/local_fallback/processing",
    )
