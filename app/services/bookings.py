import base64
import binascii
import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Booking, IdempotencyRecord, RateLimitAttempt, Slot
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
            db.execute(delete(IdempotencyRecord))
            db.execute(delete(RateLimitAttempt))
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
    try:
        with db.begin():
            slot = db.scalar(
                select(Slot).where(Slot.id == payload.slot_id).with_for_update()
            )
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
                name=payload.name,
                email=str(payload.email).lower(),
                status="confirmed",
                created_at=datetime.now(timezone.utc),
            )
            db.add(booking)
    except IntegrityError as exc:
        db.rollback()
        diag = getattr(exc.orig, "diag", None)
        if getattr(diag, "constraint_name", None) == "uq_bookings_one_confirmed_per_slot":
            raise DomainError(409, "slot already booked") from exc
        raise

    return booking


def request_fingerprint(payload: BookingCreate) -> str:
    canonical_body = {
        "email": str(payload.email).lower(),
        "name": payload.name,
        "slot_id": payload.slot_id,
    }
    encoded_body = json.dumps(
        canonical_body, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(encoded_body).hexdigest()


def _encoded_booking(booking: Booking) -> dict:
    from app.schemas.bookings import BookingResponse

    return BookingResponse.model_validate(booking).model_dump(mode="json")


def create_idempotent_booking(
    db: Session, payload: BookingCreate, idempotency_key: str
) -> tuple[int, dict]:
    fingerprint = request_fingerprint(payload)
    try:
        with db.begin():
            claim = insert(IdempotencyRecord).values(
                key=idempotency_key,
                request_fingerprint=fingerprint,
                response_status=0,
                response_body={},
                created_at=datetime.now(timezone.utc),
            ).on_conflict_do_nothing(
                index_elements=[IdempotencyRecord.key]
            ).returning(IdempotencyRecord.key)
            claimed = db.scalar(claim) is not None

            if not claimed:
                record = db.scalar(
                    select(IdempotencyRecord)
                    .where(IdempotencyRecord.key == idempotency_key)
                    .with_for_update()
                )
                if record is None:
                    raise DomainError(500, "idempotency record unavailable")
                if record.request_fingerprint != fingerprint:
                    raise DomainError(
                        422, "idempotency key reused with a different request"
                    )
                return record.response_status, record.response_body

            slot = db.scalar(
                select(Slot).where(Slot.id == payload.slot_id).with_for_update()
            )
            if slot is None:
                status_code, response_body = 404, {"error": "slot not found"}
            elif slot.start_time <= datetime.now(timezone.utc):
                status_code, response_body = 400, {
                    "error": "slot has already started"
                }
            else:
                already_booked = db.scalar(
                    select(Booking.id).where(
                        Booking.slot_id == slot.id,
                        Booking.status == "confirmed",
                    )
                )
                if already_booked is not None:
                    status_code, response_body = 409, {
                        "error": "slot already booked"
                    }
                else:
                    booking = Booking(
                        id=str(uuid4()),
                        slot_id=slot.id,
                        name=payload.name,
                        email=str(payload.email).lower(),
                        status="confirmed",
                        created_at=datetime.now(timezone.utc),
                    )
                    db.add(booking)
                    db.flush()
                    status_code, response_body = 201, _encoded_booking(booking)

            record = db.scalar(
                select(IdempotencyRecord)
                .where(IdempotencyRecord.key == idempotency_key)
                .with_for_update()
            )
            if record is None:
                raise DomainError(500, "idempotency record unavailable")
            record.response_status = status_code
            record.response_body = response_body
            return status_code, response_body
    except IntegrityError as exc:
        db.rollback()
        diag = getattr(exc.orig, "diag", None)
        if getattr(diag, "constraint_name", None) == (
            "uq_bookings_one_confirmed_per_slot"
        ):
            raise DomainError(409, "slot already booked") from exc
        raise


def encode_cursor(created_at: datetime, booking_id: str) -> str:
    payload = json.dumps(
        {"created_at": created_at.astimezone(timezone.utc).isoformat(), "id": booking_id},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        padded_cursor = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded_cursor).decode())
        created_at = datetime.fromisoformat(payload["created_at"])
        booking_id = payload["id"]
        if created_at.tzinfo is None or not isinstance(booking_id, str) or not booking_id:
            raise ValueError
        return created_at.astimezone(timezone.utc), booking_id
    except (
        ValueError,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        binascii.Error,
    ):
        raise DomainError(400, "invalid cursor")


def list_bookings(
    db: Session,
    email: str,
    limit: int,
    cursor: str | None,
) -> tuple[list[Booking], str | None]:
    statement = (
        select(Booking)
        .where(Booking.email == email.lower())
        .order_by(Booking.created_at.asc(), Booking.id.asc())
        .limit(limit + 1)
    )
    if cursor is not None:
        created_at, booking_id = decode_cursor(cursor)
        statement = statement.where(
            or_(
                Booking.created_at > created_at,
                (Booking.created_at == created_at) & (Booking.id > booking_id),
            )
        )

    bookings = db.scalars(statement).all()
    has_next_page = len(bookings) > limit
    page = list(bookings[:limit])
    next_cursor = (
        encode_cursor(page[-1].created_at, page[-1].id)
        if has_next_page
        else None
    )
    return page, next_cursor


def cancel_booking(db: Session, booking_id: str) -> Booking:
    with db.begin():
        existing_booking = db.scalar(
            select(Booking).where(Booking.id == booking_id)
        )
        if existing_booking is None:
            raise DomainError(404, "booking not found")

        slot = db.scalar(
            select(Slot).where(Slot.id == existing_booking.slot_id).with_for_update()
        )
        if slot is None:
            raise DomainError(404, "slot not found")

        booking = db.scalar(
            select(Booking)
            .where(Booking.id == booking_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if booking is None:
            raise DomainError(404, "booking not found")
        if booking.status == "cancelled":
            raise DomainError(409, "booking already cancelled")

        booking.status = "cancelled"

    return booking


def get_booking(db: Session, booking_id: str) -> Booking:
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise DomainError(404, "booking not found")
    return booking
