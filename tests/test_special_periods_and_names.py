from __future__ import annotations


def _period_payload(**overrides):
    payload = {
        "period_name": "الفطور",
        "period_type": "BREAKFAST",
        "start_time": "09:00",
        "on_time_cutoff": "09:10",
        "absent_cutoff_time": "09:30",
        "is_active": True,
        "sort_order": 6,
    }
    payload.update(overrides)
    return payload


def test_special_period_type_round_trip(client, csrf):
    created = client.post("/api/periods", json=_period_payload(), headers=csrf)
    assert created.status_code == 200, created.get_json()
    period = created.get_json()["period"]
    assert period["period_type"] == "BREAKFAST"
    assert period["period_type_label"] == "وقت الفطور"

    listed = client.get("/api/periods").get_json()["periods"]
    match = next(item for item in listed if item["id"] == period["id"])
    assert match["period_type"] == "BREAKFAST"

    updated = client.put(
        f"/api/periods/{period['id']}",
        json={"period_type": "SUPPORT"},
        headers=csrf,
    )
    assert updated.status_code == 200, updated.get_json()
    assert updated.get_json()["period"]["period_type"] == "SUPPORT"
    assert updated.get_json()["period"]["period_type_label"] == "دروس الدعم"


def test_unknown_period_type_rejected(client, csrf):
    response = client.post(
        "/api/periods", json=_period_payload(period_type="LUNCH"), headers=csrf
    )
    assert response.status_code == 400
    assert "نوع الفترة" in response.get_json()["error"]["message"]


def test_special_period_accepts_scans_and_absences(client, csrf, make_student):
    student = make_student(student_nin="1099008801")
    period = client.post(
        "/api/periods", json=_period_payload(), headers=csrf
    ).get_json()["period"]
    scan = client.post(
        "/api/gate/scan",
        json={"code": student["student_nin"], "period_id": period["id"]},
        headers=csrf,
    )
    assert scan.status_code == 200, scan.get_json()
    assert scan.get_json()["action"] in ("present", "late")

    day = client.get(
        f"/api/students/{student['student_nin']}", headers=csrf
    ).get_json()
    assert any(log["period_id"] == period["id"] for log in day["logs"])


def test_gate_page_has_manual_name_panel(client):
    page = client.get("/gate")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'id="gate-first-name"' in html
    assert 'id="gate-last-name"' in html


def test_name_lookup_finds_student(client, csrf, make_student):
    student = make_student(
        student_nin="1099008802", first_name="سارة", last_name="بلقاسم"
    )
    found = client.get(
        "/api/gate/name-lookup",
        query_string={"first_name": "سار", "last_name": "بلقاسم"},
        headers=csrf,
    )
    assert found.status_code == 200, found.get_json()
    payload = found.get_json()
    assert payload["count"] >= 1
    assert student["student_nin"] in [item["student_nin"] for item in payload["students"]]

    empty = client.get("/api/gate/name-lookup", headers=csrf)
    assert empty.get_json()["count"] == 0


def test_student_delete_permanent_without_history(client, csrf, make_student):
    student = make_student(student_nin="1099008803")
    deleted = client.delete(f"/api/students/{student['student_nin']}", headers=csrf)
    assert deleted.status_code == 200, deleted.get_json()
    assert deleted.get_json()["deleted"] is True
    missing = client.get(f"/api/students/{student['student_nin']}")
    assert missing.status_code == 404


def test_student_delete_archives_when_history_exists(
    client, csrf, make_student, period
):
    student = make_student(student_nin="1099008804")
    client.post(
        "/api/gate/scan",
        json={"code": student["student_nin"], "period_id": period["id"]},
        headers=csrf,
    )
    deleted = client.delete(f"/api/students/{student['student_nin']}", headers=csrf)
    assert deleted.status_code == 200, deleted.get_json()
    assert deleted.get_json()["deleted"] is False
    assert deleted.get_json()["archived"] is True
    kept = client.get(f"/api/students/{student['student_nin']}").get_json()
    assert kept["student"]["is_active"] is False
