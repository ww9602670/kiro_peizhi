"""Strategy API schemas."""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.utils.strategy_timing import (
    BET_TIMING_MAX,
    BET_TIMING_MIN,
    WAVE_STRATEGY_TYPES,
    normalize_wave_strategy_play_code,
)

PlatformType = Literal["JND28WEB", "JND282", "LUCKYSB"]

DW3_BS_TOKENS: tuple[str, ...] = (
    "DW3_BS_BBB",
    "DW3_BS_BBS",
    "DW3_BS_BSB",
    "DW3_BS_BSS",
    "DW3_BS_SBB",
    "DW3_BS_SBS",
    "DW3_BS_SSB",
    "DW3_BS_SSS",
)
DW3_OE_TOKENS: tuple[str, ...] = (
    "DW3_OE_OOO",
    "DW3_OE_OOE",
    "DW3_OE_OEO",
    "DW3_OE_OEE",
    "DW3_OE_EOO",
    "DW3_OE_EOE",
    "DW3_OE_EEO",
    "DW3_OE_EEE",
)
DW3_GROUP_TOKENS: tuple[str, ...] = DW3_BS_TOKENS + DW3_OE_TOKENS
_DW3_TOKEN_SET = set(DW3_GROUP_TOKENS)


VALID_TRANSITIONS: dict[str, set[str]] = {
    "stopped": {"running"},
    "running": {"paused", "stopped"},
    "paused": {"running", "stopped"},
    "error": {"stopped"},
}


def validate_state_transition(current: str, target: str) -> bool:
    allowed = VALID_TRANSITIONS.get(current, set())
    return target in allowed


def split_play_codes(play_code: str) -> list[str]:
    return [token.strip().upper() for token in play_code.split(",") if token.strip()]


def normalize_dw3_group_play_code(play_code: str) -> str:
    tokens = split_play_codes(play_code)
    if not tokens:
        raise ValueError("DW3 play_code cannot be empty")
    if any(token not in _DW3_TOKEN_SET for token in tokens):
        raise ValueError("DW3 play_code must contain only 16 DW3 group tokens")
    unique_tokens = list(dict.fromkeys(tokens))
    return ",".join(unique_tokens)


def is_dw3_group_play_code(play_code: str) -> bool:
    tokens = split_play_codes(play_code)
    return bool(tokens) and all(token in _DW3_TOKEN_SET for token in tokens)


def has_dw3_prefix(play_code: str) -> bool:
    return any(token.startswith("DW3_") for token in split_play_codes(play_code))


class StrategyCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "account_id": 1,
                "name": "demo",
                "type": "flat",
                "play_code": "DX1",
                "base_amount": 10.0,
                "martin_sequence": None,
                "bet_timing": 30,
                "simulation": False,
                "stop_loss": None,
                "take_profit": None,
                "gate_window_issues": None,
                "platform_type": "JND28WEB",
            }
        }
    )

    account_id: int
    name: str = Field(..., min_length=1, max_length=64)
    type: Literal["flat", "martin", "red_wave_double_martin", "green_wave_single_martin"]
    play_code: str = Field(..., min_length=1)
    base_amount: float = Field(..., gt=0)
    martin_sequence: Optional[list[float]] = None
    bet_timing: int = Field(default=30, ge=BET_TIMING_MIN, le=BET_TIMING_MAX)
    simulation: bool = False
    stop_loss: Optional[float] = Field(default=None, gt=0)
    take_profit: Optional[float] = Field(default=None, gt=0)
    gate_window_issues: Optional[int] = Field(default=None, ge=1)
    platform_type: PlatformType = "JND28WEB"

    @model_validator(mode="after")
    def validate_strategy(self):
        if self.type == "martin" or self.type in WAVE_STRATEGY_TYPES:
            if not self.martin_sequence:
                raise ValueError("martin_sequence")
            for value in self.martin_sequence:
                if value <= 0:
                    raise ValueError("martin_sequence item must be > 0")
        elif self.type == "flat":
            self.martin_sequence = None

        if self.type in WAVE_STRATEGY_TYPES:
            self.play_code = normalize_wave_strategy_play_code(self.type, self.play_code)
            return self

        if has_dw3_prefix(self.play_code):
            if self.type not in ("flat", "martin"):
                raise ValueError("DW3 play_code only supports flat or martin")
            self.play_code = normalize_dw3_group_play_code(self.play_code)
            if self.gate_window_issues is None:
                raise ValueError("gate_window_issues is required for DW3 play_code")

        return self


class StrategyUpdate(BaseModel):
    """Only allowed while strategy is stopped."""

    model_config = ConfigDict(
        json_schema_extra={"example": {"name": "updated", "base_amount": 20.0, "bet_timing": 45}}
    )

    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    base_amount: Optional[float] = Field(default=None, gt=0)
    play_code: Optional[str] = Field(default=None, min_length=1)
    martin_sequence: Optional[list[float]] = None
    bet_timing: Optional[int] = Field(default=None, ge=BET_TIMING_MIN, le=BET_TIMING_MAX)
    simulation: Optional[bool] = None
    stop_loss: Optional[float] = Field(default=None, gt=0)
    take_profit: Optional[float] = Field(default=None, gt=0)
    gate_window_issues: Optional[int] = Field(default=None, ge=1)
    platform_type: Optional[PlatformType] = None

    @model_validator(mode="after")
    def validate_play_code(self):
        if self.play_code and has_dw3_prefix(self.play_code):
            self.play_code = normalize_dw3_group_play_code(self.play_code)
        return self


class StrategyInfo(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": 1,
                "account_id": 1,
                "name": "demo",
                "type": "flat",
                "play_code": "DX1",
                "base_amount": 10.0,
                "martin_sequence": None,
                "bet_timing": 30,
                "simulation": False,
                "status": "stopped",
                "martin_level": 0,
                "stop_loss": None,
                "take_profit": None,
                "daily_pnl": 0.0,
                "total_pnl": 0.0,
                "gate_window_issues": None,
            }
        }
    )

    id: int
    account_id: int
    name: str
    type: str
    play_code: str
    base_amount: float
    martin_sequence: Optional[list[float]]
    bet_timing: int
    simulation: bool
    status: str
    martin_level: int
    stop_loss: Optional[float]
    take_profit: Optional[float]
    daily_pnl: float
    total_pnl: float
    gate_window_issues: Optional[int] = None
    play_code_name: str = ""
    account_name: Optional[str] = None
    platform_type: Optional[str] = None
