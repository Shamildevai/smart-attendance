from __future__ import annotations

import re

import pytest

from attendance_app.db import connect

CODE_RE = re.compile(r"^[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$")


@pytest.fixture()
def anon(app):
    return app.test_client()


def login(client, username: str, password: str = ""):
    client.get("/login")
    with client.session_transaction() as sess:
        token = sess.get("csrf_token")
    return client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": token},
        follow_redirects=False,
    )


def forgot(client, username: str, code: str, password: str):
    client.get("/forgot-password")
    with client.session_transaction() as sess:
        token = sess.get("csrf_token")
    return client.post(
        "/forgot-password",
        data={
            "username": username,
            "recovery_code": code,
            "password": password,
            "csrf_token": token,
        },
        follow_redirects=False,
    )


def make_user(client, csrf, username="teacher", password="secret12"):
    created = client.post(
        "/api/users",
        json={"username": username, "display_name": "أستاذ", "role": "user", "password": password},
        headers=csrf,
    )
    assert created.status_code == 200, created.get_json()
    return created.get_json()["user"]


def test_admin_generates_code_and_resets_password(app, anon, client, csrf):
    user = make_user(client, csrf)
    generated = client.post(f"/api/users/{user['id']}/recovery", headers=csrf)
    assert generated.status_code == 200, generated.get_json()
    code = generated.get_json()["code"]
    assert CODE_RE.match(code), code

    page = anon.get("/forgot-password")
    assert page.status_code == 200
    assert "رمز الاسترداد" in page.get_data(as_text=True)

    reset = forgot(anon, "teacher", code.lower().replace("-", " "), "newpass123")
    assert reset.status_code == 302
    assert reset.headers["Location"].endswith("/login?reset=1")

    assert login(anon, "teacher", "secret12").status_code == 401
    assert login(anon, "teacher", "newpass123").status_code == 302

    conn = connect(app.config["DB_PATH"])
    try:
        row = conn.execute(
            "SELECT recovery_hash FROM users WHERE username = 'teacher'"
        ).fetchone()
    finally:
        conn.close()
    assert row["recovery_hash"] is None

    again = forgot(anon, "teacher", code, "another12")
    assert again.status_code == 400
    assert login(anon, "teacher", "newpass123").status_code == 302


def test_wrong_code_is_rejected(app, anon, client, csrf):
    user = make_user(client, csrf, username="wrongcode")
    client.post(f"/api/users/{user['id']}/recovery", headers=csrf)

    response = forgot(anon, "wrongcode", "AAAA-BBBB-CCCC", "newpass123")
    assert response.status_code == 400
    assert "غير صحيح" in response.get_data(as_text=True)
    assert login(anon, "wrongcode", "newpass123").status_code == 401
    assert login(anon, "wrongcode", "secret12").status_code == 302


def test_recovery_generation_requires_admin(app, client, csrf):
    user = make_user(client, csrf, username="staff")
    as_user = app.test_client()
    with as_user.session_transaction() as sess:
        sess["user_id"] = user["id"]
        sess["csrf_token"] = "user-csrf"
    response = as_user.post(
        f"/api/users/{user['id']}/recovery",
        headers={"X-Requested-With": "StudentAttendance", "X-CSRF-Token": "user-csrf"},
    )
    assert response.status_code == 403
