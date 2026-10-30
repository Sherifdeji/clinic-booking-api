import hashlib
import math
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.db.models import RateLimitAttempt
from app.db.session import SessionLocal
from app.schemas.bookings import normalize_email_identity
from app.services.coordination import lock_booking_state

RATE_LIMIT = 5
WINDOW = timedelta(seconds=60)


class RateLimitUnavailable(Exception):
    pass


def _lock_key(email: str) -> int:
    return int.from_bytes(hashlib.sha256(email.encode()).digest()[:8], "big", signed=True)


def record_booking_attempt(email: str) -> int | None:
    email = normalize_email_identity(email)
    if len(email) > 320 or "\u0000" in email:
        # Keep invalid oversized identities countable without overflowing VARCHAR(320).
        email = "invalid:" + hashlib.sha256(email.encode(errors="backslashreplace")).hexdigest()
    try:
        with SessionLocal.begin() as db:
            lock_booking_state(db)
            db.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": _lock_key(email)},
            )
            # Lock waits may cross a window boundary; never timestamp before acquisition.
            now = datetime.now(timezone.utc)
            window_start = now - WINDOW
            db.execute(
                delete(RateLimitAttempt).where(
                    RateLimitAttempt.email == email,
                    RateLimitAttempt.attempted_at < window_start,
                )
            )
            oldest_attempt = db.scalar(
                select(func.min(RateLimitAttempt.attempted_at)).where(
                    RateLimitAttempt.email == email,
                    RateLimitAttempt.attempted_at >= window_start,
                )
            )
            if oldest_attempt is not None:
                attempt_count = db.scalar(
                    select(func.count(RateLimitAttempt.id)).where(
                        RateLimitAttempt.email == email,
                        RateLimitAttempt.attempted_at >= window_start,
                    )
                )
                if attempt_count >= RATE_LIMIT:
                    retry_after = math.ceil(
                        (oldest_attempt + WINDOW - now).total_seconds()
                    )
                    return max(1, retry_after)

            db.add(
                RateLimitAttempt(
                    id=str(uuid4()),
                    email=email,
                    attempted_at=now,
                )
            )
            return None
    except SQLAlchemyError as exc:
        raise RateLimitUnavailable from exc
