from datetime import datetime, timezone

from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field,
    field_serializer, field_validator, model_validator,
)


class SlotInput(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    provider: str = Field(min_length=1, max_length=200)
    start: AwareDatetime
    end: AwareDatetime

    @field_validator("id", "provider")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("invalid slot text")
        try:
            value.encode()
        except UnicodeEncodeError as exc:
            raise ValueError("invalid slot text") from exc
        return value

    @model_validator(mode="after")
    def validate_interval(self) -> "SlotInput":
        if self.end <= self.start:
            raise ValueError("slot end must be after start")
        return self


class ResetRequest(BaseModel):
    slots: list[SlotInput]


class SlotResponse(BaseModel):
    id: str
    provider: str
    start: datetime
    end: datetime
    available: bool

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("start", "end")
    def serialize_datetime(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class SlotsResponse(BaseModel):
    slots: list[SlotResponse]
