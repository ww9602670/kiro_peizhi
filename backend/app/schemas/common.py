""" Schema

ApiResponse[T]   API 
PagedData[T]    
"""
from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """{code, message, data}"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"code": 0, "message": "success", "data": None}
        }
    )

    code: int = 0
    message: str = "success"
    data: Optional[T] = None


class PagedData(BaseModel, Generic[T]):
    """"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "items": [],
                "total": 0,
                "page": 1,
                "page_size": 20,
            }
        }
    )

    items: list[T]
    total: int
    page: int
    page_size: int
