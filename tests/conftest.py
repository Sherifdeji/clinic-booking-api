import os
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="session", autouse=True)
def migrate_database() -> None:
    subprocess.run(["alembic", "upgrade", "head"], check=True)


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
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
