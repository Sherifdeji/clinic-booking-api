from datetime import datetime, timezone

from pydantic import (
    BaseModel, ConfigDict, EmailStr, Field, TypeAdapter, ValidationError,
    field_serializer, field_validator,
)


_email_adapter = TypeAdapter(EmailStr)


def normalize_email_identity(value: str) -> str:
    value = value.strip()
    try:
        return str(_email_adapter.validate_python(value)).lower()
    except ValidationError:
        # Invalid but identifiable emails still consume their own attempt allowance.
        return value.lower().encode(errors="backslashreplace").decode()


class BookingCreate(BaseModel):
    slot_id: str
    name: str = Field(max_length=200)
    email: EmailStr

    @field_validator("name", "email", mode="before")
    @classmethod
    def trim_required_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("slot_id", "name", "email")
    @classmethod
    def reject_empty_text(cls, value: str) -> str:
        if not value.strip() or "\u0000" in value:
            raise ValueError("must contain non-empty text without null characters")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("must contain valid Unicode text") from exc
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
