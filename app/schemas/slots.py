from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, field_serializer


class SlotInput(BaseModel):
    id: str
    provider: str
    start: datetime
    end: datetime


class ResetRequest(BaseModel):
    slots: list[SlotInput]


class SlotResponse(SlotInput):
    available: bool

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("start", "end")
    def serialize_datetime(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class SlotsResponse(BaseModel):
    slots: list[SlotResponse]
