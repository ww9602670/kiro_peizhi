""" Schema """
from enum import IntEnum

from pydantic import BaseModel, ConfigDict, Field


class LotteryState(IntEnum):
    """"""

    UNKNOWN = 0  # 
    OPEN = 1  # 
    CLOSED = 2  # 
    DRAWING = 3  # 


class CurrentInstallResponse(BaseModel):
    """"""

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
            }
        }
    )

    installments: str = Field(..., description="")
    state: int = Field(..., description="1=2=3=0=")
    close_countdown_sec: int = Field(..., description="", ge=0)
    open_countdown_sec: int = Field(..., description="", ge=0)
    pre_lottery_result: str = Field(..., description="")
    pre_installments: str = Field(..., description="")
    template_code: str = Field(..., description="")
