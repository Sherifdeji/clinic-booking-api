from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Booking, Slot
from app.db.session import get_db
from app.api.auth import require_api_key
from app.schemas.bookings import BookingCreate, BookingResponse
from app.schemas.slots import ResetRequest, SlotResponse, SlotsResponse
from app.services.bookings import (
    DomainError,
    create_booking,
    get_booking,
    reset_slots,
)

router = APIRouter(dependencies=[Depends(require_api_key)])
DbSession = Annotated[Session, Depends(get_db)]


@router.post("/reset", status_code=200)
def reset_database(payload: ResetRequest, db: DbSession) -> dict[str, str]:
    reset_slots(db, payload.slots)
    return {"message": "reset successful"}


@router.get("/slots", response_model=SlotsResponse)
def list_slots(
    db: DbSession, provider: str | None = Query(default=None)
) -> SlotsResponse:
    statement = select(Slot).order_by(Slot.start_time.asc())
    if provider is not None:
        statement = statement.where(Slot.provider == provider)

    slots = db.scalars(statement).all()
    booked_slot_ids = set(
        db.scalars(
            select(Booking.slot_id).where(
                Booking.status == "confirmed",
                Booking.slot_id.in_([slot.id for slot in slots]),
            )
        ).all()
    )
    return SlotsResponse(
        slots=[
            SlotResponse.model_validate(
                {
                    "id": slot.id,
                    "provider": slot.provider,
                    "start": slot.start_time,
                    "end": slot.end_time,
                    "available": slot.id not in booked_slot_ids,
                }
            )
            for slot in slots
        ]
    )


@router.post("/bookings", response_model=BookingResponse, status_code=201)
def book_slot(payload: BookingCreate, db: DbSession) -> BookingResponse:
    booking = create_booking(db, payload)
    return BookingResponse.model_validate(booking)


@router.get("/bookings/{booking_id}", response_model=BookingResponse)
def read_booking(booking_id: str, db: DbSession) -> BookingResponse:
    booking = get_booking(db, booking_id)
    return BookingResponse.model_validate(booking)
