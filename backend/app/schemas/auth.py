""" Schema

LoginRequest   
TokenResponse  Token 
"""
from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    """"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"username": "operator1", "password": "pass123456"}
        }
    )

    username: str = Field(..., min_length=3, max_length=32, description="")
    password: str = Field(..., min_length=6, description="")


class TokenResponse(BaseModel):
    """Token """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "token": "eyJhbGciOiJIUzI1NiIs...",
                "expire_at": "2025-01-02T12:00:00Z",
            }
        }
    )

    token: str
    expire_at: str  # ISO 8601


class OperatorChangePasswordRequest(BaseModel):
    """"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "old_password": "pass123456",
                "new_password": "pass1234567",
            }
        }
    )

    old_password: str = Field(..., min_length=6, description="")
    new_password: str = Field(..., min_length=6, description="")
