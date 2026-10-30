import os
import subprocess

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


def _database_target(url):
    # Credentials and driver spelling do not make a shared database isolated.
    host = url.query.get("host", url.host) or "localhost"
    if host in ("127.0.0.1", "::1"):
        host = "localhost"
    return host, str(url.query.get("port", url.port) or 5432), url.database


def _configure_test_database() -> None:
    test_url = os.getenv("TEST_DATABASE_URL")
    if not test_url:
        raise pytest.UsageError(
            "Explicit TEST_DATABASE_URL is required: tests migrate and destructively "
            "reset an isolated PostgreSQL database."
        )
    try:
        target = make_url(test_url)
        if (
            target.get_backend_name() != "postgresql"
            or not target.database
            or not target.database.endswith("_test")
            or {"dbname", "database", "service", "servicefile"}.intersection(target.query)
        ):
            raise ValueError
        existing_url = os.getenv("DATABASE_URL")
        if existing_url and _database_target(target) == _database_target(
            make_url(existing_url)
        ):
            raise ValueError
    except (ArgumentError, TypeError, ValueError):
        raise pytest.UsageError(
            "TEST_DATABASE_URL must name an isolated PostgreSQL database ending "
            "in _test, without connection-target overrides, and must differ from "
            "the preexisting DATABASE_URL."
        ) from None
    os.environ["DATABASE_URL"] = test_url


# This must run before any application import constructs the database engine.
_configure_test_database()

from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def migrate_database() -> None:
    """Migrate a disposable database; tests DELETE all application data via /reset.

    Supply TEST_DATABASE_URL explicitly and run serially, never alongside another
    suite or application using that database. The URL guard is not provisioning or
    a substitute for a genuinely isolated PostgreSQL cluster/database.
    """
    subprocess.run(["alembic", "upgrade", "head"], check=True)


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setenv("API_KEY", "test-key")
    with TestClient(app, headers={"X-API-Key": "test-key"}) as test_client:
        yield test_client


@pytest.fixture
def future_slots() -> list[dict[str, str]]:
    return [
        {
            "id": "s1",
            "provider": "Dr Ade",
            "start": "2099-12-10T09:00:00Z",
            "end": "2099-12-10T09:30:00Z",
        },
        {
            "id": "s2",
            "provider": "Dr Bello",
            "start": "2099-12-09T09:00:00Z",
            "end": "2099-12-09T09:30:00Z",
        },
    ]


@pytest.fixture
def api_key() -> str:
    return "test-key"
