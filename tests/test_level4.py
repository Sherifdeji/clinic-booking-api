from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Booking, IdempotencyRecord
from app.db.session import SessionLocal
from app.main import app


def reset(client, slots):
    response = client.post("/reset", json={"slots": slots})
    assert response.status_code == 200


def slot(slot_id: str, day: int) -> dict[str, str]:
    return {
        "id": slot_id,
        "provider": "Dr Ade",
        "start": f"2099-12-{day:02d}T09:00:00Z",
        "end": f"2099-12-{day:02d}T09:30:00Z",
    }


def booking_payload(slot_id: str, email: str = "ada@example.com"):
    return {"slot_id": slot_id, "name": "Ada Obi", "email": email}


def test_idempotent_booking_replays_and_persists_result(client):
    reset(client, [slot("idempotent", 10)])
    headers = {"Idempotency-Key": "booking-key"}
    first = client.post(
        "/bookings", headers=headers, json=booking_payload("idempotent")
    )
    second = client.post(
        "/bookings", headers=headers, json=booking_payload("idempotent")
    )

    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    with SessionLocal() as db:
        record = db.get(IdempotencyRecord, "booking-key")
        assert record is not None
        assert record.response_status == 201
        assert record.response_body == first.json()
        assert len(
            db.scalars(
                select(Booking).where(Booking.slot_id == "idempotent")
            ).all()
        ) == 1


def test_idempotency_key_cannot_be_reused_with_different_body(client):
    reset(client, [slot("first", 11), slot("second", 12)])
    headers = {"Idempotency-Key": "body-key"}
    first = client.post(
        "/bookings", headers=headers, json=booking_payload("first")
    )
    mismatch = client.post(
        "/bookings", headers=headers, json=booking_payload("second")
    )

    assert first.status_code == 201
    assert mismatch.status_code == 422
    assert mismatch.json() == {
        "error": "idempotency key reused with a different request"
    }
    assert client.get(
        f"/bookings/{first.json()['id']}"
    ).json() == first.json()


def test_concurrent_identical_idempotent_requests_replay_one_result(client):
    reset(client, [slot("same-key", 13)])
    barrier = Barrier(6)

    def attempt(_):
        with TestClient(app, headers={"X-API-Key": "test-key"}) as concurrent:
            barrier.wait()
            return concurrent.post(
                "/bookings",
                headers={"Idempotency-Key": "concurrent-key"},
                json=booking_payload("same-key"),
            )

    with ThreadPoolExecutor(max_workers=6) as executor:
        responses = list(executor.map(attempt, range(6)))

    assert all(response.status_code == 201 for response in responses)
    assert len({response.json()["id"] for response in responses}) == 1
    with SessionLocal() as db:
        assert len(
            db.scalars(
                select(Booking).where(Booking.slot_id == "same-key")
            ).all()
        ) == 1


def test_concurrent_different_bodies_share_one_idempotency_result(client):
    reset(client, [slot("different-a", 16), slot("different-b", 17)])
    barrier = Barrier(2)

    def attempt(slot_id):
        with TestClient(app, headers={"X-API-Key": "test-key"}) as concurrent:
            barrier.wait()
            return concurrent.post(
                "/bookings",
                headers={"Idempotency-Key": "different-key"},
                json=booking_payload(slot_id),
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(attempt, ["different-a", "different-b"]))

    assert sorted(response.status_code for response in responses) == [201, 422]
    with SessionLocal() as db:
        assert len(
            db.scalars(
                select(Booking).where(
                    Booking.slot_id.in_(["different-a", "different-b"])
                )
            ).all()
        ) == 1


def test_booking_history_uses_cursor_pagination_and_includes_cancelled(client):
    slots = [slot(f"page-{day}", day) for day in range(1, 13)]
    reset(client, slots)
    booking_ids = []
    for day in range(1, 13):
        response = client.post(
            "/bookings",
            json=booking_payload(f"page-{day}", "ADA@example.com"),
        )
        assert response.status_code == 201
        booking_ids.append(response.json()["id"])

    cancelled = client.delete(f"/bookings/{booking_ids[3]}")
    assert cancelled.status_code == 200

    collected = []
    cursor = None
    while True:
        params = {"email": "ada@example.com", "limit": 5}
        if cursor is not None:
            params["cursor"] = cursor
        response = client.get("/bookings", params=params)
        assert response.status_code == 200
        body = response.json()
        collected.extend(body["bookings"])
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert [booking["id"] for booking in collected] == booking_ids
    assert collected[3]["status"] == "cancelled"
    assert len({booking["id"] for booking in collected}) == 12


def test_booking_history_validation_and_edge_cases(client):
    reset(client, [slot("edge", 14)])
    assert client.get("/bookings", params={"email": "nobody@example.com"}).json() == {
        "bookings": [],
        "next_cursor": None,
    }
    for limit in (0, 51):
        response = client.get(
            "/bookings", params={"email": "ada@example.com", "limit": limit}
        )
        assert response.status_code == 400
        assert set(response.json()) == {"error"}

    missing_email = client.get("/bookings")
    assert missing_email.status_code == 400
    invalid_cursor = client.get(
        "/bookings",
        params={"email": "ada@example.com", "cursor": "not-a-cursor"},
    )
    assert invalid_cursor.status_code == 400
    assert invalid_cursor.json() == {"error": "invalid cursor"}


def test_idempotency_is_optional(client):
    reset(client, [slot("optional", 15)])
    response = client.post(
        "/bookings", json=booking_payload("optional")
    )
    assert response.status_code == 201
