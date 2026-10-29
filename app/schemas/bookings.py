from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, EmailStr, field_serializer, field_validator


class BookingCreate(BaseModel):
    slot_id: str
    name: str
    email: EmailStr

    @field_validator("slot_id", "name", "email", mode="before")
    @classmethod
    def trim_required_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("slot_id", "name", "email")
    @classmethod
    def reject_empty_text(cls, value: str) -> str:
        if not value:
            raise ValueError("must not be empty")
        return value


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
