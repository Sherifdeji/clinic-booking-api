from pydantic import BaseModel

from app.schemas.bookings import BookingResponse


class BookingListResponse(BaseModel):
    bookings: list[BookingResponse]
    next_cursor: str | None
