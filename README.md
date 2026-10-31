# Koya Clinic Booking API

Koya Clinic is a simple appointment-booking application. Patients can browse
appointment times, filter by healthcare provider, book a visit, and receive a
booking reference. The API also supports booking history and cancellation,
prevents double bookings, and safely handles repeated requests.

Built with Python, FastAPI, PostgreSQL, SQLAlchemy, and Alembic, with an HTML,
CSS, and vanilla JavaScript frontend.

## Run locally

You need Python 3.12+ and a running PostgreSQL server. From the project folder:

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e '.[test]'

# Create a database for a new installation only.
createdb -U postgres koya_clinic

# Adjust the connection details for your PostgreSQL setup.
export DATABASE_URL='postgresql+psycopg://postgres@localhost:5432/koya_clinic'
export API_KEY='choose-a-private-local-key'

alembic upgrade head
uvicorn app.main:app --reload
```

Open **http://127.0.0.1:8000/** to use the booking page, or
**http://127.0.0.1:8000/docs** to explore the API.

If your settings are already in `.env`, load them instead of the two `export`
commands above. Do not overwrite an existing `.env`:

```sh
set -a
. ./.env
set +a
```

For an existing installation, stop the application before running
`alembic upgrade head`, then restart it. Do not run old and new versions together.

## Book an appointment

1. Choose a provider and an available appointment.
2. Enter your name and email address.
3. Confirm the booking and keep your booking reference.

Appointment times use your browser's local time zone. Past or booked times
cannot be selected. No sample appointments are added automatically.

The browser uses `/ui-api/slots` and `/ui-api/bookings`. The private API key stays
on the server. Booking history and cancellation are available through the
protected API, not the public booking page.

## API

The six endpoints below require an `X-API-Key` header matching `API_KEY`.
See [the API contract](koya_api_contract.json) for request and response details.

| Method | Endpoint                          | Purpose                                      |
| ------ | --------------------------------- | -------------------------------------------- |
| POST   | `/reset`                          | Replace appointment slots and clear bookings |
| GET    | `/slots`                          | List slots; optionally filter by `provider`  |
| POST   | `/bookings`                       | Book an appointment                          |
| GET    | `/bookings/{id}`                  | Get a booking                                |
| DELETE | `/bookings/{id}`                  | Cancel a booking                             |
| GET    | `/bookings?email=ada@example.com` | List a patient's bookings                    |

**Warning:** `/reset` deletes all bookings and clears saved request results and
booking-attempt counts. Use it only on a disposable local database when adding
sample appointments, never on a database with records you need to keep.

- Send an `Idempotency-Key` when booking to retry the same request without
  creating another booking. A different request with the same key returns 422.
- Each email address has a limit of five booking attempts per minute. Failed
  attempts and repeated requests count. A 429 response includes `Retry-After`.
- Booking history uses `limit` (1–50, default 10) and `cursor`. Pass `next_cursor`
  back as `cursor` for the next page; `null` means there are no more pages.
- Errors use `{"error": "message"}`.

## Tests

Tests require a **separate PostgreSQL database** and delete its contents.
Never use your development or production database, and run one suite at a time.

```sh
createdb -U postgres koya_clinic_test
TEST_DATABASE_URL='postgresql+psycopg://postgres@localhost:5432/koya_clinic_test' python3 -m pytest
```

`TEST_DATABASE_URL` must name a database ending in `_test` and must differ from
any configured `DATABASE_URL`.
