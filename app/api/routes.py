from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse
from pydantic import EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Booking, Slot
from app.db.session import get_db
from app.api.auth import require_api_key
from app.schemas.bookings import BookingCreate, BookingResponse
from app.schemas.pagination import BookingListResponse
from app.schemas.slots import ResetRequest, SlotResponse, SlotsResponse
from app.services.bookings import (
    DomainError,
    cancel_booking,
    create_booking,
    create_idempotent_booking,
    get_booking,
    list_bookings,
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
def book_slot(
    payload: BookingCreate,
    db: DbSession,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if idempotency_key is not None:
        status_code, response_body = create_idempotent_booking(
            db, payload, idempotency_key
        )
        return JSONResponse(status_code=status_code, content=response_body)
    booking = create_booking(db, payload)
    return BookingResponse.model_validate(booking)


@router.get("/bookings/{booking_id}", response_model=BookingResponse)
def read_booking(booking_id: str, db: DbSession) -> BookingResponse:
    booking = get_booking(db, booking_id)
    return BookingResponse.model_validate(booking)


@router.delete("/bookings/{booking_id}", response_model=BookingResponse)
def delete_booking(booking_id: str, db: DbSession) -> BookingResponse:
    booking = cancel_booking(db, booking_id)
    return BookingResponse.model_validate(booking)


@router.get("/bookings", response_model=BookingListResponse)
def list_person_bookings(
    db: DbSession,
    email: EmailStr = Query(...),
    limit: int = Query(default=10, ge=1, le=50),
    cursor: str | None = Query(default=None),
) -> BookingListResponse:
    bookings, next_cursor = list_bookings(db, str(email), limit, cursor)
    return BookingListResponse(
        bookings=[BookingResponse.model_validate(booking) for booking in bookings],
        next_cursor=next_cursor,
    )
