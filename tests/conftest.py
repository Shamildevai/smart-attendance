from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from attendance_app import create_app  # noqa: E402
from attendance_app.db import connect  # noqa: E402


@pytest.fixture()
def app(tmp_path):
    application = create_app(
        {
            "TESTING": True,
            "DATA_DIR": str(tmp_path / "data"),
            "SECRET_KEY": "test-secret-key",
            "SCHEDULER_INTERVAL": 86400,
        }
    )
    yield application


@pytest.fixture()
def client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["csrf_token"] = "test-csrf"
    return client


@pytest.fixture()
def csrf():
    return {"X-Requested-With": "StudentAttendance"}


@pytest.fixture()
def db(app):
    connection = connect(app.config["DB_PATH"])
    yield connection
    connection.close()


@pytest.fixture()
def make_student(client, csrf):
    counter = {"value": 0}

    def _make(**overrides):
        counter["value"] += 1
        payload = {
            "student_nin": f"109900{counter['value']:04d}",
            "first_name": "أمين",
            "last_name": "بن علي",
            "birth_date": "2010-05-12",
            "class_group": "1AS1",
            "parent_phone": "0550123456",
        }
        payload.update(overrides)
        response = client.post("/api/students", json=payload, headers=csrf)
        assert response.status_code == 200, response.get_json()
        return response.get_json()["student"]

    return _make


@pytest.fixture()
def period(client):
    response = client.get("/api/periods")
    assert response.status_code == 200
    periods = response.get_json()["periods"]
    assert periods
    return periods[0]
