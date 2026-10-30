from pathlib import Path


def reset(client, slots):
    response = client.post("/reset", json={"slots": slots})
    assert response.status_code == 200


def test_frontend_is_served_without_exposing_api_key(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "Koya Clinic" in response.text
    assert "X-API-Key" not in response.text

    frontend_dir = Path(__file__).parents[1] / "frontend"
    assert "API_KEY" not in (frontend_dir / "app.js").read_text()


def test_ui_slots_use_real_data_and_exact_provider_filter(client, future_slots):
    reset(client, future_slots)

    response = client.get("/ui-api/slots")
    assert response.status_code == 200
    assert [slot["id"] for slot in response.json()["slots"]] == ["s2", "s1"]

    response = client.get("/ui-api/slots?provider=Dr%20Ade")
    assert [slot["id"] for slot in response.json()["slots"]] == ["s1"]
    assert client.get("/ui-api/slots?provider=dr%20ade").json() == {"slots": []}


def test_ui_booking_uses_shared_booking_and_idempotency_flow(client, future_slots):
    reset(client, future_slots[:1])
    payload = {
        "slot_id": "s1",
        "name": "  Ada Obi  ",
        "email": " ADA@EXAMPLE.COM ",
    }
    headers = {"Idempotency-Key": "ui-booking-1"}

    response = client.post("/ui-api/bookings", json=payload, headers=headers)
    assert response.status_code == 201
    booking = response.json()
    assert booking["name"] == "Ada Obi"
    assert booking["email"] == "ada@example.com"
    assert booking["status"] == "confirmed"

    replay = client.post("/ui-api/bookings", json=payload, headers=headers)
    assert replay.status_code == 201
    assert replay.json() == booking

    assert client.get("/ui-api/slots").json()["slots"][0]["available"] is False


def test_ui_booking_reports_validation_and_slot_conflict(client, future_slots):
    reset(client, future_slots[:1])

    invalid = client.post(
        "/ui-api/bookings",
        json={"slot_id": "s1", "name": "", "email": "not-an-email"},
    )
    assert invalid.status_code == 400
    assert invalid.json() == {"error": "invalid request"}

    first = client.post(
        "/ui-api/bookings",
        json={
            "slot_id": "s1",
            "name": "Ada Obi",
            "email": "first@example.com",
        },
        headers={"Idempotency-Key": "ui-booking-first"},
    )
    assert first.status_code == 201

    conflict = client.post(
        "/ui-api/bookings",
        json={
            "slot_id": "s1",
            "name": "Another Patient",
            "email": "second@example.com",
        },
        headers={"Idempotency-Key": "ui-booking-second"},
    )
    assert conflict.status_code == 409
    assert conflict.json() == {"error": "slot already booked"}
