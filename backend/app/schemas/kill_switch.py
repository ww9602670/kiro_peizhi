""" Schema

GlobalKillSwitchRequest  
GlobalKillSwitchInfo     
"""
from pydantic import BaseModel, ConfigDict, Field


class GlobalKillSwitchRequest(BaseModel):
    """"""

    model_config = ConfigDict(
        json_schema_extra={"example": {"enabled": True}}
    )

    enabled: bool = Field(..., description="true=, false=")


class GlobalKillSwitchInfo(BaseModel):
    """"""

    model_config = ConfigDict(
        json_schema_extra={"example": {"enabled": False}}
    )

    enabled: bool = Field(..., description="")
