from __future__ import annotations

import pytest

from attendance_app.db import connect


def login(client, username: str, password: str = ""):
    client.get("/login")
    with client.session_transaction() as sess:
        token = sess.get("csrf_token")
    return client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": token},
        follow_redirects=False,
    )


def active_user(app, username: str):
    conn = connect(app.config["DB_PATH"])
    try:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


@pytest.fixture()
def anon(app):
    return app.test_client()


def test_seeded_admin_can_login_without_password(app, anon):
    admin = active_user(app, "admin")
    assert admin["role"] == "admin"
    assert admin["password_hash"] is None

    response = login(anon, "admin", "")
    assert response.status_code == 302
    with anon.session_transaction() as sess:
        assert sess["user_id"] == admin["id"]

    dashboard = anon.get("/")
    assert dashboard.status_code == 200


def test_wrong_blank_login_is_rejected_after_password_set(app, anon, client, csrf):
    created = client.post(
        "/api/users",
        json={"username": "amina", "display_name": "أمينة", "role": "user", "password": "secret12"},
        headers=csrf,
    )
    assert created.status_code == 200, created.get_json()

    assert login(anon, "amina", "").status_code == 401
    assert login(anon, "amina", "wrongpass").status_code == 401
    assert login(anon, "amina", "secret12").status_code == 302


def test_inactive_user_cannot_login(app, anon, client, csrf):
    created = client.post(
        "/api/users",
        json={
            "username": "karim",
            "display_name": "كريم",
            "role": "user",
            "password": "secret12",
            "is_active": False,
        },
        headers=csrf,
    )
    assert created.status_code == 200
    assert login(anon, "karim", "secret12").status_code == 401


def test_anonymous_requests_are_guarded(anon):
    page = anon.get("/")
    assert page.status_code == 302
    assert "/login?next=/" in page.headers["Location"]

    api = anon.get("/api/students", headers={"X-Requested-With": "StudentAttendance"})
    assert api.status_code == 401
    assert api.get_json()["error"]["code"] == "auth_required"


def test_open_redirects_are_blocked(anon):
    response = anon.get("/login?next=https://evil.example/")
    assert response.status_code == 200
    assert 'name="next" value="/"' in response.get_data(as_text=True)


def test_non_admin_cannot_reach_admin_pages_or_apis(app, client, csrf):
    created = client.post(
        "/api/users",
        json={"username": "sara", "display_name": "سارة", "role": "user"},
        headers=csrf,
    )
    assert created.status_code == 200

    non_admin = app.test_client()
    assert login(non_admin, "sara", "").status_code == 302

    assert non_admin.get("/settings").status_code == 403
    assert non_admin.get("/users").status_code == 403
    assert non_admin.get("/api/settings").status_code == 403
    assert non_admin.get("/api/users").status_code == 403
    assert non_admin.post("/api/system/shutdown", headers=csrf).status_code == 403
    assert non_admin.post("/api/update/check", headers=csrf).status_code == 403

    assert non_admin.get("/").status_code == 200
    assert non_admin.get("/api/students").status_code == 200
    assert non_admin.get("/api/periods").status_code == 200


def test_admin_can_manage_accounts(client, csrf):
    created = client.post(
        "/api/users",
        json={"username": "teacher1", "display_name": "الأستاذ", "role": "user", "password": "secret12"},
        headers=csrf,
    )
    assert created.status_code == 200, created.get_json()
    user = created.get_json()["user"]
    assert user["has_password"] is True
    assert user["role"] == "user"

    updated = client.put(
        f"/api/users/{user['id']}",
        json={"display_name": "الأستاذ محمد", "role": "admin", "is_active": True},
        headers=csrf,
    )
    assert updated.status_code == 200
    assert updated.get_json()["user"]["role"] == "admin"

    cleared = client.put(
        f"/api/users/{user['id']}",
        json={"display_name": "الأستاذ محمد", "role": "admin", "clear_password": True},
        headers=csrf,
    )
    assert cleared.status_code == 200
    assert cleared.get_json()["user"]["has_password"] is False

    removed = client.delete(f"/api/users/{user['id']}", headers=csrf)
    assert removed.status_code == 200

    listing = client.get("/api/users")
    usernames = [item["username"] for item in listing.get_json()["users"]]
    assert "teacher1" not in usernames


def test_duplicate_username_is_rejected(client, csrf):
    payload = {"username": "duplicate", "display_name": "مكرر", "role": "user"}
    assert client.post("/api/users", json=payload, headers=csrf).status_code == 200
    duplicate = client.post("/api/users", json=payload, headers=csrf)
    assert duplicate.status_code == 400
    assert duplicate.get_json()["error"]["code"] == "account"


def test_last_admin_and_self_protection(client, csrf):
    self_demote = client.put(
        "/api/users/1",
        json={"display_name": "مدير النظام", "role": "user", "is_active": True},
        headers=csrf,
    )
    assert self_demote.status_code == 400

    self_disable = client.put(
        "/api/users/1",
        json={"display_name": "مدير النظام", "role": "admin", "is_active": False},
        headers=csrf,
    )
    assert self_disable.status_code == 400

    self_delete = client.delete("/api/users/1", headers=csrf)
    assert self_delete.status_code == 400

    created = client.post(
        "/api/users",
        json={"username": "secondadmin", "display_name": "مدير ثان", "role": "admin"},
        headers=csrf,
    )
    assert created.status_code == 200
    second_id = created.get_json()["user"]["id"]

    demote_other = client.put(
        f"/api/users/{second_id}",
        json={"display_name": "مدير ثان", "role": "user", "is_active": True},
        headers=csrf,
    )
    assert demote_other.status_code == 200


def test_invalid_username_is_rejected(client, csrf):
    response = client.post(
        "/api/users",
        json={"username": "a b", "display_name": "خطأ", "role": "user"},
        headers=csrf,
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "account"


def test_login_requires_csrf(app, anon):
    anon.get("/login")
    response = anon.post("/login", data={"username": "admin", "password": ""})
    assert response.status_code == 400
    with anon.session_transaction() as sess:
        assert "user_id" not in sess
