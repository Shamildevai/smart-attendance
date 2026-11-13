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


def _set_privileges(client, csrf, *names):
    response = client.put(
        f"/api/scanner/mobile/devices/{DEVICE_ID}/privileges",
        json={"privileges": list(names)},
        headers=csrf,
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["device"]["privileges"] == list(names)


def test_session_reports_phone_privileges(client, csrf):
    created = client.post("/api/scanner/mobile/code", headers=csrf)
    passcode = created.get_json()["passcode"]
    body = client.post(
        "/api/mobile/login-code",
        json={
            "device_id": DEVICE_ID,
            "device_name": "Test phone",
            "platform": "android",
            "app_version": "4.5.0",
            "passcode": passcode,
        },
    ).get_json()
    assert body["privileges"] == ["scan"]
    assert body["device"]["privileges"] == ["scan"]

    _set_privileges(client, csrf, "scan", "report", "entry", "exit", "summon")
    again = client.post(
        "/api/mobile/devices/session",
        json={
            "device_id": DEVICE_ID,
            "secret": body["device_secret"],
            "app_version": "4.5.0",
        },
    )
    assert again.status_code == 200, again.get_json()
    assert again.get_json()["privileges"] == [
        "scan",
        "report",
        "entry",
        "exit",
        "summon",
    ]


def test_period_report_requires_and_honours_privilege(client, csrf, make_student, period):
    headers = _pair(client, csrf)
    make_student(student_nin="1099000010")
    denied = client.get(
        f"/api/mobile/report/period?period_id={period['id']}", headers=headers
    )
    assert denied.status_code == 403
    assert denied.get_json()["error"]["code"] == "feature_denied"

    _set_privileges(client, csrf, "scan", "report")
    report = client.get(
        f"/api/mobile/report/period?period_id={period['id']}", headers=headers
    )
    assert report.status_code == 200, report.get_json()
    body = report.get_json()
    assert body["period"]["id"] == period["id"]
    assert body["stats"]["total"] == 1
    assert body["stats"]["unscanned"] == 1


def test_entry_slip_action_is_idempotent(client, csrf, make_student, period):
    headers = _pair(client, csrf)
    make_student(student_nin="1099000011")
    _set_privileges(client, csrf, "scan", "entry")
    action = {
        "client_id": "entry-a1",
        "kind": "entry",
        "code": "1099000011",
        "period_id": period["id"],
        "reason": "تأخر عن الحصة",
        "at": datetime.now().isoformat(timespec="seconds"),
    }

    def push():
        response = client.post(
            "/api/mobile/actions/sync",
            json={"device_id": DEVICE_ID, "actions": [action]},
            headers=headers,
        )
        assert response.status_code == 200, response.get_json()
        return response.get_json()

    first = push()
    assert first["summary"]["processed"] == 1
    assert first["results"][0]["ok"] is True
    assert first["results"][0]["code"].startswith("ES-")
    assert first["results"][0]["student"].startswith("أمين")

    second = push()
    assert second["summary"]["duplicates"] == 1
    assert second["results"][0]["status"] == "duplicate"
    assert second["results"][0]["code"] == first["results"][0]["code"]


def test_summon_action_is_denied_without_privilege(client, csrf, make_student):
    headers = _pair(client, csrf)
    make_student(student_nin="1099000012")
    response = client.post(
        "/api/mobile/actions/sync",
        json={
            "device_id": DEVICE_ID,
            "actions": [
                {
                    "client_id": "summon-a1",
                    "kind": "summon",
                    "code": "1099000012",
                    "reason": "تأخر متكرر",
                    "at": datetime.now().isoformat(timespec="seconds"),
                }
            ],
        },
        headers=headers,
    )
    assert response.status_code == 200, response.get_json()
    result = response.get_json()["results"][0]
    assert result["status"] == "denied"
    assert response.get_json()["summary"]["rejected"] == 1
