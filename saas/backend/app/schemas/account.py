""" Schema

AccountCreate    
AccountInfo      
KillSwitchUpdate  
"""
from typing import Optional

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
                "platform_url": "https://example.com",
            }
        }
    )

    account_name: str = Field(..., min_length=1, description="")
    password: str = Field(..., min_length=1, description="")
    platform_type: str = Field(
        ..., min_length=1, description="盘口类型，如 JND28WEB、JND282"
    )
    platform_url: Optional[str] = Field(
        default=None, description="平台地址，留空则使用盘口类型的默认地址"
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
    platform_url: Optional[str] = None
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
