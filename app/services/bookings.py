from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Booking, Slot
from app.schemas.bookings import BookingCreate
from app.schemas.slots import SlotInput


class DomainError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message


def reset_slots(db: Session, slots: list[SlotInput]) -> None:
    try:
        with db.begin():
            db.execute(delete(Booking))
            db.execute(delete(Slot))
            db.add_all(
                [
                    Slot(
                        id=slot.id,
                        provider=slot.provider,
                        start_time=slot.start,
                        end_time=slot.end,
                    )
                    for slot in slots
                ]
            )
    except IntegrityError as exc:
        raise DomainError(400, "invalid slot data") from exc


def create_booking(db: Session, payload: BookingCreate) -> Booking:
    slot = db.scalar(select(Slot).where(Slot.id == payload.slot_id))
    if slot is None:
        raise DomainError(404, "slot not found")

    if slot.start_time <= datetime.now(timezone.utc):
        raise DomainError(400, "slot has already started")

    already_booked = db.scalar(
        select(Booking.id).where(
            Booking.slot_id == slot.id,
            Booking.status == "confirmed",
        )
    )
    if already_booked is not None:
        raise DomainError(409, "slot already booked")

    booking = Booking(
        id=str(uuid4()),
        slot_id=slot.id,
        name=payload.name.strip(),
        email=payload.email.strip().lower(),
        status="confirmed",
        created_at=datetime.now(timezone.utc),
    )
    db.add(booking)
    db.commit()
    db.refresh(booking)
    return booking


def get_booking(db: Session, booking_id: str) -> Booking:
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise DomainError(404, "booking not found")
    return booking
