from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, field_serializer


class BookingCreate(BaseModel):
    slot_id: str
    name: str
    email: str


class BookingResponse(BaseModel):
    id: str
    slot_id: str
    name: str
    email: str
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
