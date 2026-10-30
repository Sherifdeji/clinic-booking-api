# Koya Clinic Booking API

This repository contains the Level 5 implementation of the Koya clinic booking
assessment. It implements reset, slot listing, booking, booking retrieval,
API-key authentication, booking validation, race-safe booking, cancellation,
idempotent booking requests, cursor-paginated booking history, and persistent
booking-attempt rate limiting.

## Local setup

Python 3.12+ and PostgreSQL are required. Create a database and configure the
connection:

```bash
createdb koya_clinic
cp .env.example .env
export DATABASE_URL=postgresql+psycopg://postgres@localhost:5432/koya_clinic
export API_KEY=choose-a-local-api-key
python3 -m pip install -e '.[test]'
alembic upgrade head
uvicorn app.main:app --reload
```

The application does not create tables at startup; Alembic owns schema changes.

## Level 1 endpoints

```bash
curl -X POST http://127.0.0.1:8000/reset \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: choose-a-local-api-key' \
  -d '{"slots":[{"id":"s1","provider":"Dr Ade","start":"2099-12-10T09:00:00Z","end":"2099-12-10T09:30:00Z"}]}'

curl http://127.0.0.1:8000/slots \
  -H 'X-API-Key: choose-a-local-api-key'

curl -X POST http://127.0.0.1:8000/bookings \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: choose-a-local-api-key' \
  -d '{"slot_id":"s1","name":"Ada Obi","email":"ada@example.com"}'

curl http://127.0.0.1:8000/bookings/<booking-id> \
  -H 'X-API-Key: choose-a-local-api-key'
```

The reset response is `{"message":"reset successful"}` because the supplied
contract specifies only HTTP 200 and no success body. All contract endpoints
require `X-API-Key`. Booking uses a PostgreSQL row lock plus a partial unique
index so only one confirmed booking can exist for a slot. Cancellation marks
the booking as `cancelled` and releases the slot for rebooking.

Cancel a booking with:

```bash
curl -X DELETE http://127.0.0.1:8000/bookings/<booking-id> \
  -H 'X-API-Key: choose-a-local-api-key'
```

The cancellation response contains the original booking with
`"status":"cancelled"`. Frontend integration and deployment are intentionally
not implemented yet.

An idempotent booking can be submitted with:

```bash
curl -X POST http://127.0.0.1:8000/bookings \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: choose-a-local-api-key' \
  -H 'Idempotency-Key: booking-key-1' \
  -d '{"slot_id":"s1","name":"Ada Obi","email":"ada@example.com"}'
```

Repeating the request with the same key and body replays the original response.
Reusing the key with a different body returns HTTP 422. Booking history is
available at `/bookings?email=ada@example.com&limit=10`; pass its opaque
`next_cursor` value as `cursor` for the next page.

Booking attempts are limited to five per normalized email in a rolling
60-second window. Failed attempts and idempotent replays count. The sixth
attempt returns HTTP 429 with a calculated `Retry-After` header:

```json
{
  "error": "too many booking attempts"
}
```

Rate-limit attempts are stored in PostgreSQL and coordinated with a
transaction-scoped advisory lock per email, so the limit is shared across
application workers and survives restarts.

Run the tests with:

```bash
pytest
```
