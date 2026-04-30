"""Strategy API schemas."""

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.utils.strategy_timing import (
    BET_TIMING_MAX,
    BET_TIMING_MIN,
    WAVE_STRATEGY_TYPES,
    normalize_wave_strategy_play_code,
)
from app.utils.omission_random import (
    AI_RANDOM_WEIGHT_MODE,
    OMISSION_WEIGHT_MODE,
    build_omission_play_code,
    is_ai_random_type,
    is_random_pick_martin_type,
    is_random_pick_type,
    normalize_strategy_config,
)

PlatformType = Literal["JND28WEB", "JND282", "LUCKYSB"]
StrategyPermissionType = Literal[
    "flat",
    "martin",
    "dw3_flat",
    "dw3_martin",
    "red_wave_double_martin",
    "green_wave_single_martin",
    "omission_random_flat",
    "omission_random_martin",
    "ai_random_flat",
    "ai_random_martin",
]

STRATEGY_PERMISSION_TYPES: tuple[str, ...] = (
    "flat",
    "martin",
    "dw3_flat",
    "dw3_martin",
    "red_wave_double_martin",
    "green_wave_single_martin",
    "omission_random_flat",
    "omission_random_martin",
    "ai_random_flat",
    "ai_random_martin",
)
_STRATEGY_PERMISSION_TYPE_SET = set(STRATEGY_PERMISSION_TYPES)

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


def normalize_strategy_permission_type(value: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized not in _STRATEGY_PERMISSION_TYPE_SET:
        raise ValueError(f"unsupported strategy permission type: {value}")
    return normalized


def derive_strategy_permission_type(strategy_type: str, play_code: str) -> str:
    normalized_type = (strategy_type or "").strip().lower()
    if has_dw3_prefix(play_code) or is_dw3_group_play_code(play_code):
        if normalized_type not in {"flat", "martin"}:
            raise ValueError("DW3 permission only supports flat or martin")
        return f"dw3_{normalized_type}"
    return normalize_strategy_permission_type(normalized_type)


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
    type: Literal[
        "flat",
        "martin",
        "red_wave_double_martin",
        "green_wave_single_martin",
        "omission_random_flat",
        "omission_random_martin",
        "ai_random_flat",
        "ai_random_martin",
    ]
    play_code: str = Field(..., min_length=1)
    base_amount: float = Field(..., gt=0)
    martin_sequence: Optional[list[float]] = None
    strategy_config: Optional[dict[str, Any]] = None
    bet_timing: int = Field(default=30, ge=BET_TIMING_MIN, le=BET_TIMING_MAX)
    simulation: bool = False
    stop_loss: Optional[float] = Field(default=None, gt=0)
    take_profit: Optional[float] = Field(default=None, gt=0)
    gate_window_issues: Optional[int] = Field(default=None, ge=1)
    platform_type: PlatformType = "JND28WEB"

    @model_validator(mode="after")
    def validate_strategy(self):
        if is_random_pick_type(self.type):
            if not isinstance(self.strategy_config, dict):
                raise ValueError("strategy_config")
            weight_mode = AI_RANDOM_WEIGHT_MODE if is_ai_random_type(self.type) else OMISSION_WEIGHT_MODE
            self.strategy_config = normalize_strategy_config(
                self.strategy_config,
                weight_mode=weight_mode,
            )
            self.play_code = build_omission_play_code(self.strategy_config["categories"])
            if is_random_pick_martin_type(self.type):
                if not self.martin_sequence:
                    raise ValueError("martin_sequence")
                for value in self.martin_sequence:
                    if value <= 0:
                        raise ValueError("martin_sequence item must be > 0")
            else:
                self.martin_sequence = None
            return self

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
    strategy_config: Optional[dict[str, Any]] = None
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


class StrategyPermissionUpdate(BaseModel):
    strategy_types: list[StrategyPermissionType] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_strategy_types(self):
        deduped: list[str] = []
        for item in self.strategy_types:
            normalized = normalize_strategy_permission_type(item)
            if normalized not in deduped:
                deduped.append(normalized)
        self.strategy_types = deduped  # type: ignore[assignment]
        return self


class AccountStrategyPermissionInfo(BaseModel):
    operator_id: int
    account_id: int
    account_name: str
    game_type: str
    allowed_strategy_types: list[StrategyPermissionType] = Field(default_factory=list)


class SharedMarketUncoveredUrlInfo(BaseModel):
    id: int
    normalized_url: str
    first_seen_at: str
    last_seen_at: str
    hit_count: int
    detection_status: str
    last_account_id: int | None = None
    last_platform_type: str | None = None
    sample_raw_url: str | None = None
    status: str
    failure_reason: str | None = None
    shared_group_id: int | None = None
    shared_group_key: str | None = None


class SharedMarketGroupInfo(BaseModel):
    id: int
    group_key: str
    enabled: bool | int
    collector_platform_type: str | None = None
    collector_account_name: str | None = None
    primary_url: str | None = None
    source_status: str | None = None
    last_error: str | None = None
    snapshot_issue: str | None = None
    snapshot_pre_issue: str | None = None
    snapshot_open_result: str | None = None
    snapshot_fetched_at: str | None = None
    snapshot_updated_at: str | None = None

class SharedMarketUncoveredJoinGroupRequest(BaseModel):
    shared_group_id: int


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
    strategy_config: Optional[dict[str, Any]] = None
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
