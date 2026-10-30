from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import RateLimitAttempt
from app.db.session import SessionLocal
from app.main import app


def reset(client, slots=None):
    response = client.post("/reset", json={"slots": slots or []})
    assert response.status_code == 200


def test_sixth_attempt_is_rate_limited_and_other_emails_are_independent(client):
    reset(client)
    payload = {
        "slot_id": "missing",
        "name": "Ada Obi",
        "email": " ADA@example.com ",
    }
    responses = [client.post("/bookings", json=payload) for _ in range(6)]

    assert [response.status_code for response in responses[:5]] == [404] * 5
    assert responses[5].status_code == 429
    assert responses[5].json() == {"error": "too many booking attempts"}
    assert int(responses[5].headers["Retry-After"]) > 0
    assert responses[5].headers["Retry-After"].isdigit()

    other = client.post(
        "/bookings",
        json={**payload, "email": "other@example.com"},
    )
    assert other.status_code == 404


def test_invalid_email_with_identifiable_text_counts_before_validation(client):
    reset(client)
    responses = [
        client.post(
            "/bookings",
            json={
                "slot_id": "missing",
                "name": "Ada",
                "email": "not-an-email",
            },
        )
        for _ in range(6)
    ]
    assert [response.status_code for response in responses[:5]] == [400] * 5
    assert responses[5].status_code == 429


def test_missing_email_preserves_validation_without_rate_limit_bucket(client):
    reset(client)
    responses = [
        client.post(
            "/bookings",
            json={"slot_id": "missing", "name": "Ada"},
        )
        for _ in range(6)
    ]
    assert all(response.status_code == 400 for response in responses)


def test_expired_attempts_are_removed_and_retry_after_uses_oldest_active_attempt(
    client,
):
    reset(client)
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.add_all(
            [
                RateLimitAttempt(
                    id=f"old-{index}",
                    email="window@example.com",
                    attempted_at=now - timedelta(seconds=61),
                )
                for index in range(5)
            ]
        )
        db.commit()

    allowed = client.post(
        "/bookings",
        json={
            "slot_id": "missing",
            "name": "Ada",
            "email": "window@example.com",
        },
    )
    assert allowed.status_code == 404

    with SessionLocal() as db:
        db.add_all(
            [
                RateLimitAttempt(
                    id=f"active-{index}",
                    email="retry@example.com",
                    attempted_at=now - timedelta(seconds=1 + index),
                )
                for index in range(5)
            ]
        )
        db.commit()

    blocked = client.post(
        "/bookings",
        json={
            "slot_id": "missing",
            "name": "Ada",
            "email": "retry@example.com",
        },
    )
    assert blocked.status_code == 429
    assert 50 <= int(blocked.headers["Retry-After"]) <= 60


def test_concurrent_attempts_allow_only_five_requests(client):
    reset(client)
    barrier = Barrier(6)

    def attempt(_):
        with TestClient(app, headers={"X-API-Key": "test-key"}) as concurrent:
            barrier.wait()
            return concurrent.post(
                "/bookings",
                json={
                    "slot_id": "missing",
                    "name": "Ada",
                    "email": "concurrent-rate@example.com",
                },
            )

    with ThreadPoolExecutor(max_workers=6) as executor:
        responses = list(executor.map(attempt, range(6)))

    assert sorted(response.status_code for response in responses) == [404] * 5 + [
        429
    ]
    with SessionLocal() as db:
        assert db.scalar(
            select(RateLimitAttempt.id).where(
                RateLimitAttempt.email == "concurrent-rate@example.com"
            ).order_by(RateLimitAttempt.attempted_at.desc())
        ) is not None
        assert len(
            db.query(RateLimitAttempt)
            .filter(RateLimitAttempt.email == "concurrent-rate@example.com")
            .all()
        ) == 5


def test_rate_limit_runs_before_idempotency_replay(client):
    reset(
        client,
        [
            {
                "id": "idempotent-rate",
                "provider": "Dr Ade",
                "start": "2099-12-10T09:00:00Z",
                "end": "2099-12-10T09:30:00Z",
            }
        ],
    )
    headers = {"Idempotency-Key": "rate-idempotency"}
    payload = {
        "slot_id": "idempotent-rate",
        "name": "Ada",
        "email": "replay-rate@example.com",
    }
    first = client.post("/bookings", headers=headers, json=payload)
    assert first.status_code == 201

    replays = [
        client.post("/bookings", headers=headers, json=payload) for _ in range(5)
    ]
    assert all(response.status_code == 201 for response in replays[:4])
    assert replays[4].status_code == 429


def test_unauthenticated_requests_do_not_consume_attempts(client):
    reset(client)
    client.headers.pop("X-API-Key")
    for _ in range(6):
        response = client.post(
            "/bookings",
            json={
                "slot_id": "missing",
                "name": "Ada",
                "email": "unauthenticated@example.com",
            },
        )
        assert response.status_code == 401

    client.headers["X-API-Key"] = "test-key"
    allowed = client.post(
        "/bookings",
        json={
            "slot_id": "missing",
            "name": "Ada",
            "email": "unauthenticated@example.com",
        },
    )
    assert allowed.status_code == 404
