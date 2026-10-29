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
