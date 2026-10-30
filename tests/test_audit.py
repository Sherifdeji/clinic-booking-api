"""Destructive PostgreSQL regressions: run serially on the guarded test database."""

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import EmailStr, TypeAdapter
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError

from app.db.models import Booking, IdempotencyRecord, RateLimitAttempt, Slot
from app.db.session import SessionLocal, engine
from app.main import app
from app.services import rate_limit


ROOT = Path(__file__).resolve().parents[1]
TIMESTAMP = datetime(2099, 12, 10, 9, tzinfo=timezone.utc)


def slot(slot_id="audit-slot"):
    return {
        "id": slot_id,
        "provider": "Audit Provider",
        "start": "2099-12-10T09:00:00Z",
        "end": "2099-12-10T09:30:00Z",
    }


def payload(slot_id="audit-slot", email="audit@example.com"):
    return {"slot_id": slot_id, "name": "Audit Patient", "email": email}


def reset(client, slots=None):
    response = client.post("/reset", json={"slots": slots or []})
    assert response.status_code == 200


def snapshot():
    with SessionLocal() as db:
        return {
            model.__tablename__: db.execute(
                select(model.__table__).order_by(*model.__table__.primary_key.columns)
            ).all()
            for model in (Slot, Booking, IdempotencyRecord, RateLimitAttempt)
        }


def encoded_cursor(value):
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode()


@pytest.fixture
def http_client(client):
    # Observe real HTTP 500 responses rather than raising server exceptions locally.
    with TestClient(
        app, headers={"X-API-Key": "test-key"}, raise_server_exceptions=False
    ) as test_client:
        yield test_client


@pytest.mark.parametrize("api_key", [None, "wrong-key"], ids=["missing", "wrong"])
@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/reset"),
        ("GET", "/slots"),
        ("POST", "/bookings"),
        ("GET", "/bookings/{id}"),
        ("DELETE", "/bookings/{id}"),
        ("GET", "/bookings"),
    ],
)
def test_all_six_contract_endpoints_require_auth_without_mutation(client, api_key, method, path):
    reset(client, [slot()])
    created = client.post(
        "/bookings", json=payload(), headers={"Idempotency-Key": "auth-sentinel"}
    )
    assert created.status_code == 201
    path = path.replace("{id}", created.json()["id"])
    before = snapshot()
    client.headers.pop("X-API-Key")
    headers = {} if api_key is None else {"X-API-Key": api_key}
    kwargs = {"headers": headers}
    if path == "/reset":
        kwargs["json"] = {"slots": []}
    elif method == "POST":
        kwargs["json"] = payload()
    elif path == "/bookings":
        kwargs["params"] = {"email": "audit@example.com"}

    response = client.request(method, path, **kwargs)

    assert response.status_code == 401
    assert response.json() == {"error": "invalid or missing API key"}
    assert snapshot() == before


def test_fresh_process_replays_booking_and_observes_persisted_rate_limit(client):
    reset(client, [slot()])
    first = client.post(
        "/bookings", json=payload(), headers={"Idempotency-Key": "process-replay"}
    )
    assert first.status_code == 201
    for _ in range(5):
        assert client.post(
            "/bookings", json=payload("missing", "process-rate@example.com")
        ).status_code == 404

    code = """
import json
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.db.session import SessionLocal
from app.main import app

# No migration, reset, or pytest/conftest import in this independent interpreter.
with SessionLocal() as db:
    identity = dict(db.execute(text(
        "SELECT current_database() AS database, pg_backend_pid() AS backend_pid"
    )).mappings().one())
with TestClient(app, headers={"X-API-Key": "test-key"}) as client:
    replay = client.post("/bookings", headers={"Idempotency-Key": "process-replay"},
        json={"slot_id": "audit-slot", "name": "Audit Patient", "email": "audit@example.com"})
    limited = client.post("/bookings", json={"slot_id": "missing",
        "name": "Audit Patient", "email": "process-rate@example.com"})
print(json.dumps({"identity": identity, "replay_status": replay.status_code,
    "replay": replay.json(), "limited_status": limited.status_code,
    "limited": limited.json(), "retry_after": limited.headers.get("Retry-After")}))
"""
    env = os.environ.copy()
    env["API_KEY"] = "test-key"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with SessionLocal() as db:
        assert db.bind.dialect.name == "postgresql"
        parent = db.execute(text(
            "SELECT current_database() AS database, pg_backend_pid() AS backend_pid"
        )).mappings().one()
        result = subprocess.run(
            [sys.executable, "-B", "-c", code], cwd=ROOT, env=env,
            capture_output=True, text=True, check=True, timeout=30,
        )
        child = json.loads(result.stdout)
        assert child["identity"]["database"] == parent["database"]
        assert parent["database"].endswith("_test")
        assert child["identity"]["backend_pid"] != parent["backend_pid"]
    assert child["replay_status"] == 201
    assert child["replay"] == first.json()
    assert child["limited_status"] == 429
    assert child["limited"] == {"error": "too many booking attempts"}
    assert int(child["retry_after"]) > 0
    with SessionLocal() as db:
        assert len(db.scalars(select(Booking)).all()) == 1
        assert len(db.scalars(select(RateLimitAttempt).where(
            RateLimitAttempt.email == "process-rate@example.com"
        )).all()) == 5


def test_equal_timestamp_pagination_splits_ties_in_id_order(client):
    reset(client, [slot()])
    ids = ["audit-d", "audit-b", "audit-e", "audit-a", "audit-c"]
    with SessionLocal.begin() as db:
        db.add_all([
            Booking(
                id=booking_id, slot_id="audit-slot", name="Audit Patient",
                email="ties@example.com", status="cancelled", created_at=TIMESTAMP,
            )
            for booking_id in ids
        ])
        db.add(Booking(
            id="audit-other", slot_id="audit-slot", name="Other Patient",
            email="other@example.com", status="cancelled", created_at=TIMESTAMP,
        ))

    collected = []
    cursor = None
    seen_cursors = set()
    for page_number in range(3):
        params = {"email": "ties@example.com", "limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        response = client.get("/bookings", params=params)
        assert response.status_code == 200
        body = response.json()
        page_ids = [booking["id"] for booking in body["bookings"]]
        assert page_ids == sorted(ids)[page_number * 2:page_number * 2 + 2]
        collected.extend(page_ids)
        cursor = body["next_cursor"]
        if page_number < 2:
            assert cursor is not None and cursor not in seen_cursors
            seen_cursors.add(cursor)
        else:
            assert cursor is None
    assert collected == sorted(ids)
    assert len(set(collected)) == len(ids)


@pytest.mark.parametrize("failure_status", [404, 409], ids=["missing-slot", "cancelled-conflict"])
def test_failed_idempotent_result_replays_after_slot_state_changes(client, failure_status):
    reset(client, [slot()] if failure_status == 409 else [])
    if failure_status == 409:
        original = client.post(
            "/bookings", json=payload(email="original@example.com")
        )
        assert original.status_code == 201
    headers = {"Idempotency-Key": "failed-result"}
    failed = client.post("/bookings", json=payload(), headers=headers)
    assert failed.status_code == failure_status
    if failure_status == 409:
        assert client.delete(f"/bookings/{original.json()['id']}").status_code == 200
    else:
        with SessionLocal.begin() as db:
            db.add(Slot(
                id="audit-slot", provider="Audit Provider", start_time=TIMESTAMP,
                end_time=TIMESTAMP + timedelta(minutes=30),
            ))

    replay = client.post("/bookings", json=payload(), headers=headers)
    assert replay.status_code == failed.status_code
    assert replay.json() == failed.json()
    assert client.get("/slots").json()["slots"][0]["available"] is True
    with SessionLocal() as db:
        record = db.get(IdempotencyRecord, "failed-result")
        assert record.response_status == failure_status
        assert record.response_body == failed.json()
    assert client.post(
        "/bookings", json=payload(), headers={"Idempotency-Key": "fresh-result"}
    ).status_code == 201


def test_keyless_ui_and_contract_share_replay_and_rate_limit_bucket(client):
    reset(client, [slot()])
    client.headers.pop("X-API-Key")
    assert client.get("/ui-api/slots").status_code == 200
    headers = {"Idempotency-Key": "shared-ui-key"}
    first = client.post("/ui-api/bookings", json=payload(), headers=headers)
    assert first.status_code == 201
    api_headers = {**headers, "X-API-Key": "test-key"}
    replay = client.post("/bookings", json=payload(), headers=api_headers)
    assert replay.status_code == 201
    assert replay.json() == first.json()
    for _ in range(3):
        replay = client.post("/ui-api/bookings", json=payload(), headers=headers)
        assert replay.status_code == 201
        assert replay.json() == first.json()
    for path, request_headers in (
        ("/bookings", api_headers), ("/ui-api/bookings", headers)
    ):
        blocked = client.post(path, json=payload(), headers=request_headers)
        assert blocked.status_code == 429
        assert blocked.json() == {"error": "too many booking attempts"}
        assert int(blocked.headers["Retry-After"]) > 0
    with SessionLocal() as db:
        assert len(db.scalars(select(Booking)).all()) == 1
        assert len(db.scalars(select(RateLimitAttempt)).all()) == 5


@pytest.mark.parametrize(
    "forms",
    [
        ("jose\u0301@example.com", "josé@example.com"),
        ("ada@bücher.de", "ada@xn--bcher-kva.de"),
        (" JOSE\u0301@BÜCHER.DE ", "josé@xn--bcher-kva.de"),
    ],
    ids=["nfc-local-part", "idna-domain", "nfc-idna-case-whitespace"],
)
def test_unicode_email_forms_share_one_bucket_across_ui_and_contract(client, forms):
    reset(client)
    adapter = TypeAdapter(EmailStr)
    canonical = {str(adapter.validate_python(value.strip())).lower() for value in forms}
    assert len(canonical) == 1
    client.headers.pop("X-API-Key")
    for number in range(6):
        path = "/ui-api/bookings" if number % 2 == 0 else "/bookings"
        headers = {} if number % 2 == 0 else {"X-API-Key": "test-key"}
        response = client.post(
            path, json=payload("missing", forms[number % 2]), headers=headers
        )
        assert response.status_code == (404 if number < 5 else 429)
    with SessionLocal() as db:
        attempts = db.scalars(select(RateLimitAttempt)).all()
        assert len(attempts) == 5
        assert {attempt.email for attempt in attempts} == canonical


@pytest.mark.parametrize("path", ["/bookings", "/ui-api/bookings"])
@pytest.mark.parametrize("field", ["name", "slot_id", "idempotency_key"])
def test_oversized_booking_fields_return_400_without_booking(http_client, path, field):
    reset(http_client, [slot()])
    body = payload()
    headers = {}
    if field == "idempotency_key":
        headers["Idempotency-Key"] = "k" * 256
    else:
        body[field] = "x" * (201 if field == "name" else 101)
    if path.startswith("/ui-api"):
        http_client.headers.pop("X-API-Key")
    response = http_client.post(path, json=body, headers=headers)
    # The contract requires 404 for an unknown slot, even a very long identifier.
    assert response.status_code == (404 if field == "slot_id" else 400)
    assert set(response.json()) == {"error"}
    with SessionLocal() as db:
        assert db.scalars(select(Booking)).all() == []
        assert db.scalars(select(IdempotencyRecord)).all() == []
    assert client_slot_available(http_client)


def client_slot_available(client):
    return client.get("/ui-api/slots").json()["slots"][0]["available"] is True


@pytest.mark.parametrize("path", ["/bookings", "/ui-api/bookings"])
def test_booking_field_storage_limits_are_inclusive(http_client, path):
    slot_id = "s" * 100
    reset(http_client, [slot(slot_id)])
    if path.startswith("/ui-api"):
        http_client.headers.pop("X-API-Key")
    response = http_client.post(
        path, json={**payload(slot_id), "name": "n" * 200},
        headers={"Idempotency-Key": "k" * 255},
    )
    assert response.status_code == 201
    assert response.json()["name"] == "n" * 200
    assert response.json()["slot_id"] == slot_id


@pytest.mark.parametrize("path", ["/bookings", "/ui-api/bookings"])
def test_oversized_invalid_email_returns_400_then_429(http_client, path):
    reset(http_client)
    if path.startswith("/ui-api"):
        http_client.headers.pop("X-API-Key")
    body = payload("missing", "x" * 400 + "@example.com")
    for number in range(6):
        response = http_client.post(path, json=body)
        assert response.status_code == (400 if number < 5 else 429)
        assert set(response.json()) == {"error"}
    with SessionLocal() as db:
        assert len(db.scalars(select(RateLimitAttempt)).all()) == 5
        assert db.scalars(select(Booking)).all() == []


@pytest.mark.parametrize(
    "cursor_payload",
    [
        None, [], 42, True, "not-an-object", {},
        {"created_at": "2099-12-10T09:00:00Z"},
        {"id": "audit-a"},
        {"created_at": None, "id": "audit-a"},
        {"created_at": [], "id": "audit-a"},
        {"created_at": 42, "id": "audit-a"},
        {"created_at": "2099-12-10T09:00:00", "id": "audit-a"},
        {"created_at": "2099-12-10T09:00:00Zjunk", "id": "audit-a"},
        *[
            {"created_at": "2099-12-10T09:00:00Z", "id": bad_id}
            for bad_id in (None, 42, [], {}, "", "bad\x00id")
        ],
    ],
)
def test_cursor_malformed_json_shapes_and_field_types_return_400(http_client, cursor_payload):
    reset(http_client)
    response = http_client.get("/bookings", params={
        "email": "audit@example.com", "cursor": encoded_cursor(cursor_payload),
    })
    assert response.status_code == 400
    assert response.json() == {"error": "invalid cursor"}


@pytest.mark.parametrize("junk", ["!", "\n", "{}"], ids=["base64-junk", "base64-whitespace", "json-junk"])
def test_cursor_trailing_junk_is_rejected(http_client, junk):
    reset(http_client)
    value = {"created_at": "2099-12-10T09:00:00Z", "id": "audit-a"}
    valid = encoded_cursor(value)
    assert http_client.get("/bookings", params={
        "email": "audit@example.com", "cursor": valid,
    }).status_code == 200
    cursor = (
        base64.urlsafe_b64encode((json.dumps(value) + junk).encode()).decode()
        if junk == "{}" else valid + junk
    )
    response = http_client.get("/bookings", params={
        "email": "audit@example.com", "cursor": cursor,
    })
    assert response.status_code == 400
    assert response.json() == {"error": "invalid cursor"}


def test_partial_index_definition_and_named_unique_violation(client):
    reset(client, [slot()])
    with SessionLocal() as db:
        index = db.execute(text("""
            SELECT i.indisunique, i.indisvalid, i.indisready, i.indnkeyatts,
                   pg_get_expr(i.indpred, i.indrelid) AS predicate,
                   pg_get_indexdef(i.indexrelid, 1, true) AS indexed_column
            FROM pg_index AS i
            JOIN pg_class AS c ON c.oid = i.indexrelid
            WHERE i.indrelid = 'bookings'::regclass
              AND c.relname = 'uq_bookings_one_confirmed_per_slot'
        """)).mappings().one()
        assert index["indisunique"] and index["indisvalid"] and index["indisready"]
        assert index["indnkeyatts"] == 1
        assert index["indexed_column"] == "slot_id"
        predicate = index["predicate"].replace("::text", "")
        assert "".join(predicate.split()).replace("(", "").replace(")", "") == "status='confirmed'"
        db.add_all([
            Booking(
                id=booking_id, slot_id="audit-slot", name="Audit Patient",
                email="index@example.com", status=status, created_at=TIMESTAMP,
            )
            for booking_id, status in (
                ("index-cancel-a", "cancelled"), ("index-cancel-b", "cancelled"),
                ("index-confirmed", "confirmed"),
            )
        ])
        db.commit()
        db.add(Booking(
            id="index-duplicate", slot_id="audit-slot", name="Duplicate",
            email="index@example.com", status="confirmed", created_at=TIMESTAMP,
        ))
        with pytest.raises(IntegrityError) as duplicate:
            db.commit()
        assert duplicate.value.orig.sqlstate == "23505"
        assert duplicate.value.orig.diag.constraint_name == "uq_bookings_one_confirmed_per_slot"
        db.rollback()
        cancelled = db.get(Booking, "index-cancel-a")
        cancelled.status = "confirmed"
        with pytest.raises(IntegrityError) as transition:
            db.commit()
        assert transition.value.orig.sqlstate == "23505"
        assert transition.value.orig.diag.constraint_name == "uq_bookings_one_confirmed_per_slot"
        db.rollback()
        assert len(db.scalars(select(Booking)).all()) == 3


@pytest.mark.parametrize(
    "start,end",
    [
        ("2099-12-11T09:00:00", "2099-12-11T09:30:00Z"),
        ("2099-12-11T09:00:00Z", "2099-12-11T09:30:00"),
        ("2099-12-11T09:00:00", "2099-12-11T09:30:00"),
        ("2099-12-11T09:30:00Z", "2099-12-11T09:00:00Z"),
        ("2099-12-11T09:00:00Z", "2099-12-11T09:00:00Z"),
        ("2099-12-11T09:00:00+01:00", "2099-12-11T09:30:00+02:00"),
    ],
    ids=["naive-start", "naive-end", "both-naive", "reversed", "equal", "reversed-utc"],
)
def test_invalid_slot_times_reject_reset_without_changing_database(http_client, start, end):
    reset(http_client, [slot()])
    assert http_client.post(
        "/bookings", json=payload(), headers={"Idempotency-Key": "reset-sentinel"}
    ).status_code == 201
    before = snapshot()
    response = http_client.post("/reset", json={"slots": [
        slot("valid-replacement"), {**slot("invalid-replacement"), "start": start, "end": end},
    ]})
    assert response.status_code == 400
    assert set(response.json()) == {"error"}
    assert snapshot() == before


def test_rate_limit_uses_time_after_advisory_lock_without_sleep(client, monkeypatch):
    reset(client)
    before_lock = datetime(2030, 1, 1, tzinfo=timezone.utc)
    after_lock = before_lock + timedelta(seconds=2)
    clock = {"now": before_lock}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is timezone.utc
            return clock["now"]

    with SessionLocal.begin() as db:
        db.add_all([
            RateLimitAttempt(
                id=f"lock-clock-{number}", email="lock-clock@example.com",
                attempted_at=before_lock - timedelta(seconds=59),
            )
            for number in range(5)
        ])

    def acquired_lock(connection, cursor, statement, parameters, context, executemany):
        if "pg_advisory_xact_lock" in statement:
            clock["now"] = after_lock

    monkeypatch.setattr(rate_limit, "datetime", Clock)
    event.listen(engine, "after_cursor_execute", acquired_lock)
    try:
        # Simulate time passing during lock acquisition without a real wait/race.
        retry_after = rate_limit.record_booking_attempt("lock-clock@example.com")
    finally:
        event.remove(engine, "after_cursor_execute", acquired_lock)
    assert clock["now"] == after_lock
    assert retry_after is None
    with SessionLocal() as db:
        attempts = db.scalars(select(RateLimitAttempt)).all()
        assert len(attempts) == 1
        assert attempts[0].attempted_at == after_lock


@pytest.mark.parametrize(
    "test_url,existing_url",
    [
        (None, None),
        ("not-a-url", None),
        ("sqlite:///audit_test", None),
        ("postgresql+psycopg://audit@localhost/production", None),
        ("postgresql+psycopg://audit@localhost/audit_test?dbname=production", None),
        ("postgresql+psycopg://audit@localhost/audit_test?service=production", None),
        ("postgresql+psycopg://audit@localhost/audit_test", "postgresql+psycopg://audit@localhost/audit_test"),
        ("postgresql+psycopg://audit@127.0.0.1/audit_test", "postgresql://other@localhost:5432/audit_test"),
    ],
    ids=["missing", "malformed", "sqlite", "non-test-name", "dbname-override", "service-override", "same-url", "same-target"],
)
def test_database_guard_rejects_unsafe_targets_before_app_import(test_url, existing_url):
    env = os.environ.copy()
    for name, value in (("TEST_DATABASE_URL", test_url), ("DATABASE_URL", existing_url)):
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    code = """
import builtins
import runpy
import pytest

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == "app" or name.startswith("app."):
        raise AssertionError("application imported before database guard")
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
try:
    runpy.run_path("tests/conftest.py")
except pytest.UsageError:
    print("rejected before app import")
else:
    raise AssertionError("unsafe database configuration was accepted")
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", code], cwd=ROOT, env=env,
        capture_output=True, text=True, check=True, timeout=15,
    )
    assert result.stdout.strip() == "rejected before app import"


@pytest.mark.parametrize("path", ["/bookings", "/ui-api/bookings"])
@pytest.mark.parametrize("field", ["name", "email", "slot_id"])
def test_null_characters_are_validation_errors_not_database_errors(http_client, path, field):
    reset(http_client, [slot()])
    body = {**payload(), field: "invalid" + chr(0) + "value"}
    response = http_client.post(path, json=body)
    assert response.status_code == 400
    assert set(response.json()) == {"error"}
    assert client_slot_available(http_client)


@pytest.mark.parametrize("path", ["/bookings", "/ui-api/bookings"])
def test_invalid_unicode_is_rejected_without_server_error(http_client, path):
    reset(http_client)
    response = http_client.post(path, content=b'{"email":"\\ud800","name":"Ada","slot_id":"missing"}',
                                headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert set(response.json()) == {"error"}


@pytest.mark.parametrize("field", ["id", "provider"])
def test_null_slot_text_does_not_replace_existing_records(http_client, field):
    reset(http_client, [slot()])
    before = snapshot()
    response = http_client.post("/reset", json={"slots": [{**slot(), field: "bad" + chr(0)}]})
    assert response.status_code == 400
    assert snapshot() == before
