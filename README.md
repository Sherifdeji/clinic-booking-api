# Koya Clinic Booking API

This repository contains the Level 1 implementation of the Koya clinic booking
assessment. It currently implements only the basic reset, slot listing, booking,
and booking retrieval endpoints.

## Local setup

Python 3.12+ and PostgreSQL are required. Create a database and configure the
connection:

```bash
createdb koya_clinic
cp .env.example .env
export DATABASE_URL=postgresql+psycopg://postgres@localhost:5432/koya_clinic
python3 -m pip install -e '.[test]'
alembic upgrade head
uvicorn app.main:app --reload
```

The application does not create tables at startup; Alembic owns schema changes.

## Level 1 endpoints

```bash
curl -X POST http://127.0.0.1:8000/reset \
  -H 'Content-Type: application/json' \
  -d '{"slots":[{"id":"s1","provider":"Dr Ade","start":"2099-12-10T09:00:00Z","end":"2099-12-10T09:30:00Z"}]}'

curl http://127.0.0.1:8000/slots

curl -X POST http://127.0.0.1:8000/bookings \
  -H 'Content-Type: application/json' \
  -d '{"slot_id":"s1","name":"Ada Obi","email":"ada@example.com"}'

curl http://127.0.0.1:8000/bookings/<booking-id>
```

The reset response is `{"message":"reset successful"}` because the supplied
contract specifies only HTTP 200 and no success body. Level 1 performs a basic
availability check; database-level race protection is intentionally deferred
to Level 3.

Run the tests with:

```bash
pytest
```
