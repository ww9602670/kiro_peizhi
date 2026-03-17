""" Schema

AlertInfo  
"""
from typing import Optional

from pydantic import BaseModel, ConfigDict


class AlertInfo(BaseModel):
    """"""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": 1,
                "operator_id": 2,
                "type": "login_fail",
                "level": "critical",
                "title": "",
                "detail": '{"reason": "", "retry_count": 3}',
                "is_read": 0,
                "created_at": "2025-01-01 12:00:00",
            }
        },
    )

    id: int
    operator_id: int
    type: str
    level: str
    title: str
    detail: Optional[str] = None
    is_read: int = 0
    created_at: str
