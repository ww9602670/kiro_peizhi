""" Schema

StrategyCreate   API 
StrategyUpdate   
StrategyInfo     
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


#   
VALID_TRANSITIONS: dict[str, set[str]] = {
    "stopped": {"running"},
    "running": {"paused", "stopped"},
    "paused": {"running", "stopped"},
    "error": {"stopped"},
}


def validate_state_transition(current: str, target: str) -> bool:
    """"""
    allowed = VALID_TRANSITIONS.get(current, set())
    return target in allowed


class StrategyCreate(BaseModel):
    """"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "account_id": 1,
                "name": "",
                "type": "flat",
                "play_code": "DX1",
                "base_amount": 10.0,
                "martin_sequence": None,
                "bet_timing": 30,
                "simulation": False,
                "stop_loss": None,
                "take_profit": None,
            }
        }
    )

    account_id: int
    name: str = Field(..., min_length=1, max_length=64, description="")
    type: Literal["flat", "martin"] = Field(..., description="")
    play_code: str = Field(..., min_length=1, description=" KeyCode")
    base_amount: float = Field(..., gt=0, description="")
    martin_sequence: Optional[list[float]] = Field(
        default=None, description=""
    )
    bet_timing: int = Field(default=30, ge=5, le=180, description="")
    simulation: bool = Field(default=False, description="")
    stop_loss: Optional[float] = Field(
        default=None, gt=0, description=""
    )
    take_profit: Optional[float] = Field(
        default=None, gt=0, description=""
    )

    @model_validator(mode="after")
    def validate_martin(self):
        """>0"""
        if self.type == "martin":
            if not self.martin_sequence or len(self.martin_sequence) == 0:
                raise ValueError("martin_sequence")
            for v in self.martin_sequence:
                if v <= 0:
                    raise ValueError(" 0")
        elif self.type == "flat":
            self.martin_sequence = None
        return self


class StrategyUpdate(BaseModel):
    """ stopped """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "",
                "base_amount": 20.0,
                "bet_timing": 45,
            }
        }
    )

    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    base_amount: Optional[float] = Field(default=None, gt=0, description="")
    martin_sequence: Optional[list[float]] = None
    bet_timing: Optional[int] = Field(default=None, ge=5, le=180)
    simulation: Optional[bool] = None
    stop_loss: Optional[float] = Field(default=None, gt=0, description="")
    take_profit: Optional[float] = Field(default=None, gt=0, description="")


class StrategyInfo(BaseModel):
    """"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": 1,
                "account_id": 1,
                "name": "",
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
            }
        }
    )

    id: int
    account_id: int
    name: str
    type: str
    play_code: str
    base_amount: float  # 
    martin_sequence: Optional[list[float]]
    bet_timing: int
    simulation: bool
    status: str
    martin_level: int
    stop_loss: Optional[float]  # 
    take_profit: Optional[float]  # 
    daily_pnl: float  # 
    total_pnl: float  # 
