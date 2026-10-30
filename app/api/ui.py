from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Booking, Slot
from app.db.session import get_db
from app.schemas.bookings import BookingCreate, BookingResponse
from app.schemas.slots import SlotResponse, SlotsResponse
from app.services.bookings import (
    create_booking,
    create_idempotent_booking,
)
from app.services.rate_limit import RateLimitUnavailable, record_booking_attempt

router = APIRouter(prefix="/ui-api")
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/slots", response_model=SlotsResponse)
def list_ui_slots(
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


async def rate_limit_ui_booking(request: Request) -> None:
    try:
        body = await request.json()
    except ValueError:
        return
    if not isinstance(body, dict):
        return

    raw_email = body.get("email")
    if not isinstance(raw_email, str) or not raw_email.strip():
        return

    try:
        retry_after = record_booking_attempt(raw_email.strip().lower())
    except RateLimitUnavailable as exc:
        raise HTTPException(status_code=503, detail="rate limiter unavailable") from exc

    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="too many booking attempts",
            headers={"Retry-After": str(retry_after)},
        )


@router.post(
    "/bookings",
    response_model=BookingResponse,
    status_code=201,
    dependencies=[Depends(rate_limit_ui_booking)],
)
def create_ui_booking(
    payload: dict,
    db: DbSession,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        booking_payload = BookingCreate.model_validate(payload)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc

    if idempotency_key is not None:
        status_code, response_body = create_idempotent_booking(
            db, booking_payload, idempotency_key
        )
        return JSONResponse(status_code=status_code, content=response_body)

    booking = create_booking(db, booking_payload)
    return BookingResponse.model_validate(booking)
