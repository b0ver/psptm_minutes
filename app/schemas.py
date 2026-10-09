from datetime import date
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=1500)
    owner: str | None = Field(default=None, max_length=200)
    segment_index: int = Field(ge=0)
    evidence: str = Field(min_length=1, max_length=2000)


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minutes: str = Field(min_length=1, max_length=10000)
    actions: list[Candidate] = Field(max_length=50)


class EditAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=1500)
    owner: str | None = Field(default=None, max_length=200)
    due_on: date | None = None
    timezone: str = "Europe/Moscow"

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError("Unknown timezone") from None
        return value


class Approval(BaseModel):
    version: int = Field(ge=1)


class ChangeStatus(Approval):
    status: Literal["in_progress", "done"]
