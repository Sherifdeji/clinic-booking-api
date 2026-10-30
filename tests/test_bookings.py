from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.models import Booking
from app.db.session import SessionLocal
from app.main import app


def reset(client, slots):
    response = client.post("/reset", json={"slots": slots})
    assert response.status_code == 200


def test_booking_is_normalized_persisted_and_makes_slot_unavailable(client):
    reset(
        client,
        [
            {
                "id": "s1",
                "provider": "Dr Ade",
                "start": "2099-12-10T09:00:00Z",
                "end": "2099-12-10T09:30:00Z",
            }
        ],
    )
    response = client.post(
        "/bookings",
        json={"slot_id": "s1", "name": " Ada Obi ", "email": " ADA@EXAMPLE.COM "},
    )
    assert response.status_code == 201
    booking = response.json()
    assert set(booking) == {
        "id",
        "slot_id",
        "name",
        "email",
        "status",
        "created_at",
    }
    assert booking["name"] == "Ada Obi"
    assert booking["email"] == "ada@example.com"
    assert booking["status"] == "confirmed"
    assert client.get(f"/bookings/{booking['id']}").json() == booking
    assert client.get("/slots").json()["slots"][0]["available"] is False


def test_booking_missing_slot_and_already_booked_slot_are_json_errors(client):
    reset(
        client,
        [
            {
                "id": "s1",
                "provider": "Dr Ade",
                "start": "2099-12-10T09:00:00Z",
                "end": "2099-12-10T09:30:00Z",
            }
        ],
    )
    missing = client.post(
        "/bookings",
        json={"slot_id": "missing", "name": "Ada", "email": "ada@example.com"},
    )
    assert missing.status_code == 404
    assert missing.json() == {"error": "slot not found"}

    first = client.post(
        "/bookings",
        json={"slot_id": "s1", "name": "Ada", "email": "ada@example.com"},
    )
    second = client.post(
        "/bookings",
        json={"slot_id": "s1", "name": "Bob", "email": "bob@example.com"},
    )
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json() == {"error": "slot already booked"}


def test_booking_validation_returns_400_for_missing_empty_and_invalid_fields(client):
    reset(
        client,
        [
            {
                "id": "s1",
                "provider": "Dr Ade",
                "start": "2099-12-10T09:00:00Z",
                "end": "2099-12-10T09:30:00Z",
            }
        ],
    )
    cases = [
        {"name": "Ada", "email": "ada@example.com"},
        {"slot_id": "s1", "email": "ada@example.com"},
        {"slot_id": "s1", "name": "Ada"},
        {"slot_id": "", "name": "Ada", "email": "ada@example.com"},
        {"slot_id": "s1", "name": " ", "email": "ada@example.com"},
        {"slot_id": "s1", "name": "Ada", "email": " "},
        {"slot_id": "s1", "name": "Ada", "email": "not-an-email"},
    ]
    for payload in cases:
        response = client.post("/bookings", json=payload)
        assert response.status_code == 400
        assert set(response.json()) == {"error"}

    assert client.get("/slots").json()["slots"][0]["available"] is True


def test_unauthorized_booking_does_not_create_booking(client):
    reset(
        client,
        [
            {
                "id": "s1",
                "provider": "Dr Ade",
                "start": "2099-12-10T09:00:00Z",
                "end": "2099-12-10T09:30:00Z",
            }
        ],
    )
    response = client.post(
        "/bookings",
        headers={"X-API-Key": "wrong-key"},
        json={"slot_id": "s1", "name": "Ada", "email": "ada@example.com"},
    )
    assert response.status_code == 401
    assert client.get("/slots").json()["slots"][0]["available"] is True


def test_past_slot_is_rejected_without_creating_booking(client):
    reset(
        client,
        [
            {
                "id": "past",
                "provider": "Dr Ade",
                "start": "2020-01-01T09:00:00Z",
                "end": "2020-01-01T09:30:00Z",
            }
        ],
    )
    response = client.post(
        "/bookings",
        json={"slot_id": "past", "name": "Ada", "email": "ada@example.com"},
    )
    assert response.status_code == 400
    assert response.json() == {"error": "slot has already started"}
    assert client.get("/slots").json()["slots"][0]["available"] is True


def test_missing_api_key_is_rejected_for_each_contract_endpoint(client):
    client.headers.pop("X-API-Key")
    requests = [
        ("post", "/reset", {"slots": []}),
        ("get", "/slots", None),
        (
            "post",
            "/bookings",
            {"slot_id": "missing", "name": "Ada", "email": "ada@example.com"},
        ),
        ("get", "/bookings/unknown", None),
    ]
    for method, path, payload in requests:
        if method == "get":
            response = getattr(client, method)(path)
        else:
            response = getattr(client, method)(path, json=payload)
        assert response.status_code == 401
        assert response.json() == {"error": "invalid or missing API key"}


def test_unknown_booking_returns_required_error(client):
    reset(client, [])
    response = client.get("/bookings/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {"error": "booking not found"}


def test_concurrent_bookings_allow_exactly_one_success(client):
    reset(
        client,
        [
            {
                "id": "concurrent",
                "provider": "Dr Ade",
                "start": "2099-12-10T09:00:00Z",
                "end": "2099-12-10T09:30:00Z",
            }
        ],
    )
    barrier = Barrier(8)

    def attempt(number: int):
        with TestClient(app, headers={"X-API-Key": "test-key"}) as concurrent_client:
            barrier.wait()
            return concurrent_client.post(
                "/bookings",
                json={
                    "slot_id": "concurrent",
                    "name": f"Patient {number}",
                    "email": f"patient{number}@example.com",
                },
            )

    with ThreadPoolExecutor(max_workers=8) as executor:
        responses = list(executor.map(attempt, range(8)))

    assert [response.status_code for response in responses].count(201) == 1
    assert [response.status_code for response in responses].count(409) == 7
    with SessionLocal() as db:
        assert db.scalar(
            text(
                "SELECT count(*) FROM bookings "
                "WHERE slot_id = 'concurrent' AND status = 'confirmed'"
            )
        ) == 1


def test_partial_unique_index_exists_and_is_enforced(client):
    reset(
        client,
        [
            {
                "id": "indexed",
                "provider": "Dr Ade",
                "start": "2099-12-10T09:00:00Z",
                "end": "2099-12-10T09:30:00Z",
            }
        ],
    )
    first = client.post(
        "/bookings",
        json={"slot_id": "indexed", "name": "Ada", "email": "ada@example.com"},
    )
    assert first.status_code == 201

    with SessionLocal() as db:
        index_exists = db.scalar(
            text(
                "SELECT 1 FROM pg_indexes "
                "WHERE indexname = 'uq_bookings_one_confirmed_per_slot'"
            )
        )
        assert index_exists == 1

        db.add(
            Booking(
                id="manual-duplicate",
                slot_id="indexed",
                name="Duplicate",
                email="duplicate@example.com",
                status="confirmed",
                created_at=datetime.fromisoformat(
                    first.json()["created_at"].replace("Z", "+00:00")
                ),
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_cancellation_releases_slot_and_preserves_history(client):
    reset(
        client,
        [
            {
                "id": "cancel-me",
                "provider": "Dr Ade",
                "start": "2099-12-10T09:00:00Z",
                "end": "2099-12-10T09:30:00Z",
            }
        ],
    )
    booking = client.post(
        "/bookings",
        json={"slot_id": "cancel-me", "name": "Ada", "email": "ada@example.com"},
    ).json()

    response = client.delete(f"/bookings/{booking['id']}")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert client.get(f"/bookings/{booking['id']}").json()["status"] == "cancelled"
    assert client.get("/slots").json()["slots"][0]["available"] is True


def test_cancellation_errors_and_rebooking(client):
    reset(
        client,
        [
            {
                "id": "rebook",
                "provider": "Dr Ade",
                "start": "2099-12-10T09:00:00Z",
                "end": "2099-12-10T09:30:00Z",
            }
        ],
    )
    missing = client.delete("/bookings/missing")
    assert missing.status_code == 404
    assert missing.json() == {"error": "booking not found"}

    booking = client.post(
        "/bookings",
        json={"slot_id": "rebook", "name": "Ada", "email": "ada@example.com"},
    ).json()
    cancelled = client.delete(f"/bookings/{booking['id']}")
    assert cancelled.status_code == 200

    again = client.delete(f"/bookings/{booking['id']}")
    assert again.status_code == 409
    assert again.json() == {"error": "booking already cancelled"}

    replacement = client.post(
        "/bookings",
        json={"slot_id": "rebook", "name": "Bob", "email": "bob@example.com"},
    )
    assert replacement.status_code == 201
    assert replacement.json()["id"] != booking["id"]
    assert client.get(f"/bookings/{booking['id']}").json()["status"] == "cancelled"
    assert client.get("/slots").json()["slots"][0]["available"] is False


def test_concurrent_cancellations_have_one_success(client):
    reset(
        client,
        [
            {
                "id": "cancel-concurrent",
                "provider": "Dr Ade",
                "start": "2099-12-10T09:00:00Z",
                "end": "2099-12-10T09:30:00Z",
            }
        ],
    )
    booking = client.post(
        "/bookings",
        json={
            "slot_id": "cancel-concurrent",
            "name": "Ada",
            "email": "ada@example.com",
        },
    ).json()
    barrier = Barrier(2)

    def cancel():
        with TestClient(app, headers={"X-API-Key": "test-key"}) as concurrent_client:
            barrier.wait()
            return concurrent_client.delete(f"/bookings/{booking['id']}")

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: cancel(), range(2)))

    statuses = sorted(response.status_code for response in responses)
    assert statuses == [200, 409]
    assert client.get(f"/bookings/{booking['id']}").json()["status"] == "cancelled"
