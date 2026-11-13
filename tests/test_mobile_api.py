from __future__ import annotations

from datetime import datetime

DEVICE_ID = "android-test-device-0001"


def _pair(client, csrf):
    created = client.post("/api/scanner/mobile/code", headers=csrf)
    assert created.status_code == 200, created.get_json()
    passcode = created.get_json()["passcode"]
    response = client.post(
        "/api/mobile/login-code",
        json={
            "device_id": DEVICE_ID,
            "device_name": "Test phone",
            "platform": "android",
            "app_version": "4.4.2",
            "passcode": passcode,
        },
    )
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["device"]["status"] == "approved"
    return {
        "Authorization": f"Bearer {body['token']}",
        "X-Requested-With": "StudentAttendance",
    }


def test_roster_refresh_after_new_student(client, csrf, make_student):
    headers = _pair(client, csrf)
    make_student(student_nin="1099000001")

    first = client.get(
        f"/api/mobile/offline/roster?device_id={DEVICE_ID}", headers=headers
    )
    assert first.status_code == 200
    roster = first.get_json()
    assert roster["unchanged"] is False
    assert [row["student_nin"] for row in roster["students"]] == ["1099000001"]

    same = client.get(
        f"/api/mobile/offline/roster?device_id={DEVICE_ID}&since={roster['version']}",
        headers=headers,
    )
    assert same.get_json()["unchanged"] is True

    make_student(student_nin="1099000002", first_name="سارة", last_name="مرابط")
    refreshed = client.get(
        f"/api/mobile/offline/roster?device_id={DEVICE_ID}&since={roster['version']}",
        headers=headers,
    )
    body = refreshed.get_json()
    assert body["unchanged"] is False
    assert body["version"] != roster["version"]
    assert {row["student_nin"] for row in body["students"]} == {
        "1099000001",
        "1099000002",
    }


def test_scan_sync_is_idempotent_by_client_id(client, csrf, make_student):
    headers = _pair(client, csrf)
    make_student(student_nin="1099000003")
    scan = {
        "client_id": "scan-a1",
        "code": "1099000003",
        "at": datetime.now().isoformat(timespec="seconds"),
    }

    def push():
        response = client.post(
            "/api/mobile/scans/sync",
            json={
                "device_id": DEVICE_ID,
                "device_name": "Test phone",
                "platform": "android",
                "app_version": "4.4.2",
                "scans": [scan],
            },
            headers=headers,
        )
        assert response.status_code == 200, response.get_json()
        return response.get_json()

    first = push()
    assert first["summary"]["processed"] == 1
    assert first["results"][0]["student"]["student_nin"] == "1099000003"

    second = push()
    assert second["summary"]["duplicates"] == 1
    assert second["results"][0]["status"] == "duplicate"

    log = client.get(
        "/api/attendance/list?per_page=100&date=" + datetime.now().date().isoformat()
    )
    assert log.status_code == 200
    rows = log.get_json()["items"]
    assert len([row for row in rows if row["student_nin"] == "1099000003"]) == 1


def test_unknown_device_is_rejected(client):
    response = client.get(
        "/api/mobile/offline/roster?device_id=android-unknown-0001",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "auth_required"
