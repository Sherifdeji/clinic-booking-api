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


def test_unknown_booking_returns_required_error(client):
    reset(client, [])
    response = client.get("/bookings/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {"error": "booking not found"}
