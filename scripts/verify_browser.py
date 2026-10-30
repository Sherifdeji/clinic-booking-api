"""Bounded browser smoke test; seeds ONLY an explicitly selected disposable database.

TEST_DATABASE_URL must identify a dedicated PostgreSQL database ending _test.
NODE_PATH must point to an optional Playwright install (not an app dependency).
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

from sqlalchemy.engine import make_url


def main() -> None:
    url = make_url(os.environ["TEST_DATABASE_URL"])
    if url.get_backend_name() != "postgresql" or not (url.database or "").endswith("_test"):
        raise SystemExit("Refusing to seed a non-test PostgreSQL database")
    if {"dbname", "database", "service", "servicefile"}.intersection(url.query):
        raise SystemExit("Connection-target overrides are not allowed")
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root))
    os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
    os.environ["API_KEY"] = "browser-verification-only-key"
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, timeout=30)

    from app.db.session import SessionLocal
    from app.schemas.bookings import BookingCreate
    from app.schemas.slots import SlotInput
    from app.services.bookings import create_booking, reset_slots

    rows = [("Dr Ade", 10, 9), ("Dr Bello", 10, 10), ("Dr Ade", 11, 9),
            ("Dr Bello", 11, 11), ("Dr Ade", 12, 10), ("Dr Bello", 12, 11)]
    with SessionLocal() as db:
        reset_slots(db, [SlotInput(
            id=f"audit-{number}", provider=provider,
            start=f"2099-12-{day:02d}T{hour:02d}:00:00Z",
            end=f"2099-12-{day:02d}T{hour:02d}:30:00Z",
        ) for number, (provider, day, hour) in enumerate(rows)] + [SlotInput(
            id="past", provider="Dr Ade", start="2020-01-01T09:00:00Z", end="2020-01-01T09:30:00Z",
        )])
    with SessionLocal() as db:
        create_booking(db, BookingCreate(
            slot_id="audit-5", name="Existing Patient", email="existing@example.com",
        ))
    # The child server is always terminated; this command is not a background dev server.
    with tempfile.NamedTemporaryFile(mode="w", prefix="koya-browser-", suffix=".log", delete=False) as log:
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "58123"],
            stdout=log, stderr=log,
        )
        try:
            for _ in range(50):
                if server.poll() is not None:
                    raise RuntimeError(f"Verification server exited; see {log.name}")
                try:
                    urllib.request.urlopen("http://127.0.0.1:58123/", timeout=1).close()
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("Verification server did not start")
            subprocess.run(["node", "tests/browser_flow.cjs"], check=True, timeout=90)
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


if __name__ == "__main__":
    main()
