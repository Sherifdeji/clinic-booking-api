"""Coordinate destructive contract reset with normal transactional mutations."""

from sqlalchemy import text
from sqlalchemy.orm import Session


def lock_booking_state(db: Session, *, exclusive: bool = False) -> None:
    # Two-int advisory locks occupy a different namespace from per-email bigint
    # locks. Shared locks allow ordinary mutations to proceed concurrently; reset
    # alone takes the exclusive lock, always before any row or idempotency locks.
    function = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
    db.execute(text(f"SELECT {function}(1263486273, 1)"))
