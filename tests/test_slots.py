def reset(client, slots):
    response = client.post("/reset", json={"slots": slots})
    assert response.status_code == 200
    return response


def test_reset_replaces_slots_and_accepts_past_slot(client):
    reset(
        client,
        [
            {
                "id": "old",
                "provider": "Old Provider",
                "start": "2099-01-01T09:00:00Z",
                "end": "2099-01-01T09:30:00Z",
            }
        ],
    )
    response = reset(
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
    assert response.json() == {"message": "reset successful"}
    assert client.get("/slots").json()["slots"] == [
        {
            "id": "past",
            "provider": "Dr Ade",
            "start": "2020-01-01T09:00:00Z",
            "end": "2020-01-01T09:30:00Z",
            "available": True,
        }
    ]


def test_slots_are_sorted_filtered_exactly_and_show_availability(
    client, future_slots
):
    reset(client, future_slots)
    response = client.get("/slots")
    assert [slot["id"] for slot in response.json()["slots"]] == ["s2", "s1"]
    assert client.get("/slots?provider=Dr Ade").json()["slots"][0]["id"] == "s1"
    assert client.get("/slots?provider=dr ade").json()["slots"] == []
    assert all(slot["available"] for slot in response.json()["slots"])


def test_empty_slots_returns_empty_array(client):
    reset(client, [])
    assert client.get("/slots").json() == {"slots": []}


def test_reset_deletes_existing_bookings(client):
    slots = [
        {
            "id": "s1",
            "provider": "Dr Ade",
            "start": "2099-12-10T09:00:00Z",
            "end": "2099-12-10T09:30:00Z",
        }
    ]
    reset(client, slots)
    booking = client.post(
        "/bookings",
        json={"slot_id": "s1", "name": "Ada Obi", "email": "ada@example.com"},
    ).json()

    reset(
        client,
        [
            {
                "id": "s2",
                "provider": "Dr Bello",
                "start": "2099-12-11T09:00:00Z",
                "end": "2099-12-11T09:30:00Z",
            }
        ],
    )

    assert client.get(f"/bookings/{booking['id']}").json() == {
        "error": "booking not found"
    }


def test_invalid_reset_rolls_back_without_partial_replacement(client):
    original = [
        {
            "id": "original",
            "provider": "Dr Ade",
            "start": "2099-12-10T09:00:00Z",
            "end": "2099-12-10T09:30:00Z",
        }
    ]
    reset(client, original)

    response = client.post(
        "/reset",
        json={
            "slots": [
                {
                    "id": "duplicate",
                    "provider": "Dr Ade",
                    "start": "2099-12-11T09:00:00Z",
                    "end": "2099-12-11T09:30:00Z",
                },
                {
                    "id": "duplicate",
                    "provider": "Dr Bello",
                    "start": "2099-12-12T09:00:00Z",
                    "end": "2099-12-12T09:30:00Z",
                },
            ]
        },
    )

    assert response.status_code == 400
    assert response.json() == {"error": "invalid slot data"}
    assert client.get("/slots").json()["slots"][0]["id"] == "original"
