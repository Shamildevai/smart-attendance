from __future__ import annotations

from datetime import date


def test_reset_requires_typed_confirmation(client, csrf, make_student):
    make_student(student_nin="4099000101")
    response = client.post(
        "/api/system/reset", json={"confirm": "delete"}, headers=csrf
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "confirm_required"
    assert client.get("/api/students").get_json()["total"] == 1


def test_reset_clears_records_and_keeps_configuration(
    client, csrf, make_student, period
):
    student = make_student(student_nin="4099000102")
    client.put(
        "/api/settings",
        json={"student_noun": "طالب", "meal_period_label": "فترة الغداء"},
        headers=csrf,
    )
    client.post(
        "/api/gate/scan",
        json={"code": student["student_nin"], "period_id": period["id"]},
        headers=csrf,
    )

    response = client.post(
        "/api/system/reset", json={"confirm": "حذف"}, headers=csrf
    )
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data["counts"]["students"] == 1
    assert data["counts"]["attendance_logs"] == 1
    assert "periods" not in data["counts"]

    assert client.get("/api/students").get_json()["total"] == 0
    day = date.today().isoformat()
    report = client.get("/api/reports/daily", query_string={"date": day}).get_json()
    assert report["meal_absent_total"] == 0
    assert report["total"] == 0

    settings = client.get("/api/settings").get_json()["settings"]
    assert settings["student_noun"] == "طالب"
    assert settings["meal_period_label"] == "فترة الغداء"
    assert len(client.get("/api/periods").get_json()["periods"]) >= 1
    users = client.get("/api/users").get_json()["users"]
    assert any(user["username"] == "admin" for user in users)


def test_reset_can_restore_default_periods(client, csrf, make_student, period):
    created = client.post(
        "/api/periods",
        json={
            "period_name": "فترة الإطعام",
            "period_type": "MEAL",
            "start_time": "11:00",
            "on_time_cutoff": "11:15",
            "absent_cutoff_time": "12:30",
            "is_active": True,
            "sort_order": 9,
        },
        headers=csrf,
    )
    assert created.status_code == 200, created.get_json()
    response = client.post(
        "/api/system/reset",
        json={"confirm": "حذف", "reset_periods": True},
        headers=csrf,
    )
    assert response.status_code == 200, response.get_json()
    periods = client.get("/api/periods").get_json()["periods"]
    assert periods
    assert all(item["period_type"] != "MEAL" for item in periods)
    assert all(item["id"] <= len(periods) for item in periods)
