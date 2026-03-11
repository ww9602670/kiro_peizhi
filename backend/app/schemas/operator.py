""" Schema

OperatorCreate   
OperatorUpdate   
OperatorInfo     
StatusUpdate     /
"""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class OperatorCreate(BaseModel):
    """"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "username": "operator1",
                "password": "pass123456",
                "max_accounts": 1,
                "expire_date": "2025-12-31",
            }
        }
    )

    username: str = Field(..., min_length=3, max_length=32, description="")
    password: str = Field(..., min_length=6, description="")
    max_accounts: int = Field(default=1, ge=1, le=100, description="")
    expire_date: Optional[str] = Field(default=None, description="ISO 8601")


class OperatorUpdate(BaseModel):
    """"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "max_accounts": 5,
                "expire_date": "2026-06-30",
            }
        }
    )

    max_accounts: Optional[int] = Field(default=None, ge=1, le=100, description="")
    expire_date: Optional[str] = Field(default=None, description="ISO 8601")


class OperatorInfo(BaseModel):
    """"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": 2,
                "username": "operator1",
                "role": "operator",
                "status": "active",
                "max_accounts": 1,
                "expire_date": "2025-12-31",
                "created_at": "2025-01-01T00:00:00",
            }
        }
    )

    id: int
    username: str
    role: str
    status: str
    max_accounts: int
    expire_date: Optional[str] = None
    created_at: str


class StatusUpdate(BaseModel):
    """/"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"status": "disabled"}
        }
    )

    status: str = Field(..., pattern="^(active|disabled)$", description="active  disabled")
