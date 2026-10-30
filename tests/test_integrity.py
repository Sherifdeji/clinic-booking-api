"""Destructive integration regressions; run serially on an isolated PostgreSQL DB.

Never run alongside a browser, application, or another suite using the same DB.
Every race is contained within one test, and all worker transactions finish before
another fixture reset can run. No migration or reset is performed by a child process.
"""

import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from queue import Queue
from threading import Event, Thread
from time import monotonic

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text

from app.api.auth import require_api_key
from app.db.models import Booking, IdempotencyRecord, RateLimitAttempt, Slot
from app.db.session import SessionLocal, engine
from app.main import app
from app.schemas.bookings import BookingCreate
from app.services.bookings import (
    cancel_booking,
    create_booking,
    create_idempotent_booking,
    request_fingerprint,
)
from app.services.rate_limit import record_booking_attempt


LOCK_CLASS = 1263486273
LOCK_OBJECT = 1
POLL_TIMEOUT = 5
GATE_TIMEOUT = 10
WORKER_TIMEOUT = 15


def slot(slot_id="s1"):
    return {
        "id": slot_id,
        "provider": "Integrity Provider",
        "start": "2099-12-10T09:00:00Z",
        "end": "2099-12-10T09:30:00Z",
    }


def body(slot_id="s1"):
    return {"slot_id": slot_id, "name": "Ada Obi", "email": "ada@example.com"}


def reset(client):
    response = client.post("/reset", json={"slots": [slot()]})
    assert response.status_code == 200


def counts():
    with SessionLocal() as db:
        return {
            model.__tablename__: db.scalar(select(func.count()).select_from(model))
            for model in (Slot, Booking, IdempotencyRecord, RateLimitAttempt)
        }


def raw_fingerprint(value):
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@pytest.fixture
def http_client(client):
    with TestClient(
        app, headers={"X-API-Key": "test-key"}, raise_server_exceptions=False
    ) as test_client:
        yield test_client


class PendingCall:
    def __init__(self, operation):
        self.started = Event()
        self.done = Event()
        self.results = Queue(maxsize=1)

        def run():
            self.started.set()
            try:
                self.results.put((True, operation()))
            except BaseException as exc:
                self.results.put((False, exc))
            finally:
                self.done.set()

        # A broken server callback must not prevent interpreter shutdown.
        self.thread = Thread(target=run, daemon=True)
        self.thread.start()
        assert self.started.wait(POLL_TIMEOUT), "worker did not start"

    def finish(self):
        assert self.done.wait(WORKER_TIMEOUT), "database worker did not finish in time"
        self.thread.join(timeout=1)
        assert not self.thread.is_alive(), "database worker did not exit"
        success, result = self.results.get_nowait()
        if not success:
            raise result
        return result


@pytest.fixture
def workers():
    pending = []

    # Bound real database waits as well as Python synchronization. SET LOCAL is
    # transaction-scoped, so pooled connections retain no test timeout settings.
    def set_timeouts(connection):
        if connection.get_execution_options().get("isolation_level") != "AUTOCOMMIT":
            connection.exec_driver_sql("SET LOCAL statement_timeout = '12s'")
            connection.exec_driver_sql("SET LOCAL lock_timeout = '10s'")

    event.listen(engine, "begin", set_timeouts)

    def start(operation):
        call = PendingCall(operation)
        pending.append(call)
        return call

    try:
        yield start
    finally:
        try:
            for call in pending:
                call.thread.join(timeout=WORKER_TIMEOUT)
            assert all(not call.thread.is_alive() for call in pending), (
                "a worker is still active; do not reset or reuse this database"
            )
        finally:
            event.remove(engine, "begin", set_timeouts)


def route_request(method, path, **kwargs):
    # Each worker has its own TestClient and obtains independent database sessions.
    with TestClient(
        app, headers={"X-API-Key": "test-key"}, raise_server_exceptions=False
    ) as client:
        return client.request(method, path, **kwargs)


@contextmanager
def held_state_lock(*, exclusive):
    function = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            pid = connection.scalar(text("SELECT pg_backend_pid()"))
            connection.execute(
                text(f"SELECT {function}(:class_id, :object_id)"),
                {"class_id": LOCK_CLASS, "object_id": LOCK_OBJECT},
            )
            yield pid
        finally:
            transaction.rollback()


def wait_for_advisory_wait(blocker_pid, mode, pending):
    query = text("""
        SELECT a.pid, a.wait_event_type, a.wait_event, l.mode,
               pg_blocking_pids(a.pid) AS blockers
        FROM pg_locks AS l
        JOIN pg_stat_activity AS a ON a.pid = l.pid
        WHERE l.locktype = 'advisory' AND NOT l.granted
          AND l.classid = :class_id AND l.objid = :object_id AND l.objsubid = 2
          AND l.database = (SELECT oid FROM pg_database WHERE datname = current_database())
          AND a.datname = current_database()
          AND l.mode = :mode AND a.wait_event_type = 'Lock' AND a.wait_event = 'advisory'
          AND :blocker_pid = ANY(pg_blocking_pids(a.pid))
    """)
    deadline = monotonic() + POLL_TIMEOUT
    pause = Event()
    # Autocommit refreshes pg_stat_activity's transaction-local snapshot each poll.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as observer:
        while monotonic() < deadline:
            waiting = observer.execute(query, {
                "class_id": LOCK_CLASS, "object_id": LOCK_OBJECT,
                "mode": mode, "blocker_pid": blocker_pid,
            }).mappings().all()
            if waiting:
                assert len(waiting) == 1
                assert waiting[0]["pid"] != blocker_pid
                assert not pending.done.is_set(), "request finished while its lock was blocked"
                return dict(waiting[0])
            if pending.done.is_set():
                pytest.fail("operation completed without the expected advisory-lock wait")
            pause.wait(0.02)
    pytest.fail(f"no {mode} advisory wait observed within {POLL_TIMEOUT}s")


@contextmanager
def pause_after_statement(predicate):
    acquired = Event()
    release = Event()
    state = {}

    def pause(connection, cursor, statement, parameters, context, executemany):
        if not acquired.is_set() and predicate(statement):
            state["pid"] = connection.connection.driver_connection.info.backend_pid
            acquired.set()
            assert release.wait(GATE_TIMEOUT), "test transaction gate timed out"

    event.listen(engine, "after_cursor_execute", pause)
    try:
        yield acquired, release, state
    finally:
        release.set()
        event.remove(engine, "after_cursor_execute", pause)


def test_held_shared_lock_blocks_reset_before_any_deletion(client, workers):
    reset(client)
    response = client.post(
        "/bookings", json=body(), headers={"Idempotency-Key": "shared-reset-sentinel"}
    )
    assert response.status_code == 201
    before = counts()
    with held_state_lock(exclusive=False) as blocker_pid:
        pending = workers(lambda: route_request("POST", "/reset", json={"slots": []}))
        waiting = wait_for_advisory_wait(blocker_pid, "ExclusiveLock", pending)
        assert blocker_pid in waiting["blockers"]
        assert counts() == before
    response = pending.finish()
    assert response.status_code == 200, response.text
    assert all(count == 0 for count in counts().values())


@pytest.mark.parametrize("operation", ["booking", "idempotent", "cancellation", "rate-attempt"])
def test_exclusive_lock_blocks_mutations_before_row_or_key_locks(client, workers, operation):
    reset(client)
    booking_id = None
    if operation == "cancellation":
        response = client.post("/bookings", json=body())
        assert response.status_code == 201
        booking_id = response.json()["id"]
    before = counts()

    def mutate():
        if operation == "rate-attempt":
            return record_booking_attempt("exclusive@example.com")
        with SessionLocal() as db:
            if operation == "booking":
                return create_booking(db, BookingCreate.model_validate(body())).status
            if operation == "idempotent":
                return create_idempotent_booking(
                    db, BookingCreate.model_validate(body()), "exclusive-idempotent",
                    raw_fingerprint(body()),
                )[0]
            return cancel_booking(db, booking_id).status

    with held_state_lock(exclusive=True) as blocker_pid:
        pending = workers(mutate)
        waiting = wait_for_advisory_wait(blocker_pid, "ShareLock", pending)
        with SessionLocal() as observer:
            # In particular, the rate-limit session must not acquire its per-email
            # bigint advisory lock before entering the global shared-lock protocol.
            locks = observer.execute(text("""
                SELECT locktype FROM pg_locks
                WHERE pid = :pid AND granted AND (
                    locktype IN ('advisory', 'tuple') OR relation IN (
                        'slots'::regclass, 'bookings'::regclass,
                        'idempotency_records'::regclass, 'rate_limit_attempts'::regclass
                    )
                )
            """), {"pid": waiting["pid"]}).all()
            assert locks == []
        assert counts() == before
    result = pending.finish()
    expected = {"booking": "confirmed", "idempotent": 201, "cancellation": "cancelled", "rate-attempt": None}
    assert result == expected[operation]


def test_reset_racing_creation_cannot_leave_successful_idempotency_orphan(client, workers):
    reset(client)

    def create():
        with SessionLocal() as db:
            return create_idempotent_booking(
                db, BookingCreate.model_validate(body()), "racing-create", raw_fingerprint(body())
            )

    with pause_after_statement(
        lambda statement: "pg_advisory_xact_lock_shared" in statement
    ) as (acquired, release, state):
        creator = workers(create)
        try:
            assert acquired.wait(POLL_TIMEOUT), "creation did not acquire its shared lock"
            # Creation is inside its service transaction but has not claimed a key
            # or inserted a booking. Reset must wait, not pass this transaction.
            resetter = workers(lambda: route_request("POST", "/reset", json={"slots": []}))
            wait_for_advisory_wait(state["pid"], "ExclusiveLock", resetter)
        finally:
            release.set()
        status, created = creator.finish()
        reset_response = resetter.finish()
    assert status == 201
    assert reset_response.status_code == 200, reset_response.text
    assert all(count == 0 for count in counts().values())
    with SessionLocal() as db:
        assert db.get(IdempotencyRecord, "racing-create") is None
        assert db.get(Booking, created["id"]) is None
    missing = client.get(f"/bookings/{created['id']}")
    assert missing.status_code == 404


@pytest.mark.parametrize("first", ["cancellation", "reset"])
def test_reset_and_cancellation_lock_order_never_deadlocks_or_returns_500(client, workers, first):
    reset(client)
    created = client.post("/bookings", json=body())
    assert created.status_code == 201
    booking_id = created.json()["id"]

    def predicate(statement):
        if first == "cancellation":
            # Pause before cancellation locks the booking row. Reset must wait
            # without deleting/locking that booking while the slot is held.
            return "FROM slots" in statement and "FOR UPDATE" in statement
        return "pg_advisory_xact_lock(" in statement and str(LOCK_CLASS) in statement

    def cancel_request():
        return route_request("DELETE", f"/bookings/{booking_id}")

    def reset_request():
        return route_request("POST", "/reset", json={"slots": []})

    if first == "cancellation":
        first_request, second_request = cancel_request, reset_request
        mode = "ExclusiveLock"
    else:
        first_request, second_request = reset_request, cancel_request
        mode = "ShareLock"

    with pause_after_statement(predicate) as (acquired, release, state):
        leading = workers(first_request)
        try:
            assert acquired.wait(POLL_TIMEOUT), "leading transaction did not reach its gate"
            trailing = workers(second_request)
            waiting = wait_for_advisory_wait(state["pid"], mode, trailing)
            assert waiting["pid"] != state["pid"]
        finally:
            release.set()
        leading_response = leading.finish()
        trailing_response = trailing.finish()
    assert leading_response.status_code == 200, leading_response.text
    expected = 200 if first == "cancellation" else 404
    assert trailing_response.status_code == expected, trailing_response.text
    if first == "reset":
        assert trailing_response.json() == {"error": "booking not found"}
    else:
        assert leading_response.json()["status"] == "cancelled"
    assert all(count == 0 for count in counts().values())


@pytest.mark.parametrize("path", ["/bookings", "/ui-api/bookings"])
def test_v2_raw_json_reordered_keys_and_serialization_whitespace_replay(http_client, path):
    reset(http_client)
    if path.startswith("/ui-api"):
        http_client.headers.pop("X-API-Key")
    headers = {"Idempotency-Key": "raw-order", "Content-Type": "application/json"}
    original = body()
    first = http_client.post(path, json=original, headers=headers)
    assert first.status_code == 201
    reordered = {key: original[key] for key in reversed(original)}
    replay = http_client.post(
        path, content=json.dumps(reordered, indent=4), headers=headers
    )
    assert replay.status_code == 201
    assert replay.json() == first.json()
    with SessionLocal() as db:
        record = db.get(IdempotencyRecord, "raw-order")
        assert record.fingerprint_version == 2
        assert record.request_fingerprint == raw_fingerprint(original)
        assert record.response_body == first.json()
        assert len(db.scalars(select(Booking)).all()) == 1


@pytest.mark.parametrize("path", ["/bookings", "/ui-api/bookings"])
@pytest.mark.parametrize(
    "changes",
    [
        {"name": " Ada Obi"},
        {"name": "Ada Obi "},
        {"name": "Ada  Obi"},
        {"email": "ADA@EXAMPLE.COM"},
        {"email": " ada@example.com "},
        {"slot_id": " s1 "},
        {"extra": {"nested": True}},
    ],
    ids=["leading-name-space", "trailing-name-space", "name-value", "email-case", "email-space", "slot-value", "extra-field"],
)
def test_v2_raw_body_value_or_extra_changes_reject_key_reuse(http_client, path, changes):
    reset(http_client)
    if path.startswith("/ui-api"):
        http_client.headers.pop("X-API-Key")
    headers = {"Idempotency-Key": "raw-change"}
    first = http_client.post(path, json=body(), headers=headers)
    assert first.status_code == 201
    changed = http_client.post(path, json={**body(), **changes}, headers=headers)
    assert changed.status_code == 422
    assert changed.json() == {"error": "idempotency key reused with a different request"}
    with SessionLocal() as db:
        record = db.get(IdempotencyRecord, "raw-change")
        assert record.fingerprint_version == 2
        assert record.request_fingerprint == raw_fingerprint(body())
        assert record.response_body == first.json()
        assert len(db.scalars(select(Booking)).all()) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "  Ada Obi  "},
        {"email": "ADA@EXAMPLE.COM"},
        {"email": " ada@example.com "},
        {"name": " Ada Obi ", "email": " ADA@EXAMPLE.COM ", "extra": "legacy-ignored"},
    ],
    ids=["name-trimming", "email-case", "email-trimming", "combined-with-extra"],
)
def test_legacy_v1_record_replays_normalized_variants_and_is_preserved(http_client, changes):
    reset(http_client)
    original = http_client.post("/bookings", json=body())
    assert original.status_code == 201
    fingerprint = request_fingerprint(BookingCreate.model_validate(body()))
    with SessionLocal.begin() as db:
        # Deliberately omit fingerprint_version: exercise the migrated server
        # default used by pre-v2 records, not an explicitly supplied Python value.
        record = IdempotencyRecord(
            key="legacy-v1", request_fingerprint=fingerprint,
            response_status=201, response_body=original.json(),
            created_at=datetime.now(timezone.utc),
        )
        db.add(record)
        db.flush()
        assert record.fingerprint_version == 1
    replay = http_client.post(
        "/bookings", json={**body(), **changes}, headers={"Idempotency-Key": "legacy-v1"}
    )
    assert replay.status_code == 201
    assert replay.json() == original.json()
    with SessionLocal() as db:
        persisted = db.get(IdempotencyRecord, "legacy-v1")
        assert persisted is not None
        assert persisted.fingerprint_version == 1
        assert persisted.request_fingerprint == fingerprint
        assert persisted.response_status == 201
        assert persisted.response_body == original.json()
        assert len(db.scalars(select(Booking)).all()) == 1


@pytest.mark.parametrize("path", ["/bookings", "/ui-api/bookings"])
@pytest.mark.parametrize("idempotent", [False, True], ids=["plain", "idempotent"])
def test_slot_ids_preserve_exact_whitespace(http_client, path, idempotent):
    response = http_client.post("/reset", json={"slots": [slot(" s1 ")]})
    assert response.status_code == 200
    if path.startswith("/ui-api"):
        http_client.headers.pop("X-API-Key")
    missing = http_client.post(path, json=body("s1"))
    assert missing.status_code == 404
    assert missing.json() == {"error": "slot not found"}
    headers = {"Idempotency-Key": "exact-slot"} if idempotent else {}
    response = http_client.post(path, json=body(" s1 "), headers=headers)
    assert response.status_code == 201
    assert response.json()["slot_id"] == " s1 "
    with SessionLocal() as db:
        assert db.get(Slot, " s1 ") is not None
        assert db.get(Slot, "s1") is None
        assert db.get(Booking, response.json()["id"]).slot_id == " s1 "


@pytest.mark.parametrize(
    "timestamp",
    ["0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"],
    ids=["utc-underflow", "utc-overflow"],
)
def test_cursor_timezone_conversion_overflow_returns_400(http_client, timestamp):
    reset(http_client)
    cursor = base64.urlsafe_b64encode(json.dumps({
        "created_at": timestamp, "id": "integrity-cursor",
    }).encode()).decode().rstrip("=")
    response = http_client.get("/bookings", params={
        "email": "ada@example.com", "cursor": cursor,
    })
    assert response.status_code == 400
    assert response.json() == {"error": "invalid cursor"}


@pytest.mark.parametrize("path", ["/bookings", "/ui-api/bookings"])
@pytest.mark.parametrize("idempotent", [False, True], ids=["plain", "idempotent"])
@pytest.mark.parametrize("content", [b"{", b'{"email":"ada@example.com",}', b"\xff"], ids=["truncated", "trailing-comma", "invalid-utf8"])
def test_malformed_json_returns_400_not_fingerprint_dependency_500(http_client, path, idempotent, content):
    reset(http_client)
    before = counts()
    if path.startswith("/ui-api"):
        http_client.headers.pop("X-API-Key")
    headers = {"Content-Type": "application/json"}
    if idempotent:
        headers["Idempotency-Key"] = "malformed-json"
    response = http_client.post(path, content=content, headers=headers)
    assert response.status_code == 400
    assert set(response.json()) == {"error"}
    assert counts() == before


@pytest.mark.parametrize("expected_key", ["test-key", "clé-secrète"], ids=["ascii-config", "unicode-config"])
def test_wrong_unicode_api_key_is_401_not_compare_digest_type_error(monkeypatch, expected_key):
    monkeypatch.setenv("API_KEY", expected_key)
    with pytest.raises(HTTPException) as rejected:
        require_api_key("incorrect-clé-🔑")
    assert rejected.value.status_code == 401
    assert rejected.value.detail == "invalid or missing API key"
