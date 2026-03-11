""" Schema

AccountCreate    
AccountInfo      
KillSwitchUpdate  
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


def mask_password(password: str) -> str:
    """2+****<2  '****'"""
    if len(password) < 2:
        return "****"
    return password[:2] + "****"


class AccountCreate(BaseModel):
    """"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "account_name": "player001",
                "password": "mypassword",
                "platform_type": "JND28WEB",
            }
        }
    )

    account_name: str = Field(..., min_length=1, description="")
    password: str = Field(..., min_length=1, description="")
    platform_type: Literal["JND28WEB", "JND282"] = Field(
        ..., description="JND28WEB JND2822.0"
    )


class AccountInfo(BaseModel):
    """"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": 1,
                "account_name": "player001",
                "password_masked": "my****",
                "platform_type": "JND28WEB",
                "status": "inactive",
                "balance": 0.0,
                "kill_switch": False,
                "last_login_at": None,
            }
        }
    )

    id: int
    account_name: str
    password_masked: str  # 2+****
    platform_type: str
    status: str
    balance: float  # API int / 100
    kill_switch: bool
    last_login_at: Optional[str] = None


class KillSwitchUpdate(BaseModel):
    """"""

    model_config = ConfigDict(
        json_schema_extra={"example": {"enabled": True}}
    )

    enabled: bool = Field(..., description="true=, false=")
