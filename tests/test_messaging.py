from __future__ import annotations

import time

from attendance_app.services.messaging_service import (
    channels_payload,
    resolve_channel_settings,
)


def _wait_for_job(client, job_id: str, timeout: float = 8.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/api/messages/jobs/{job_id}")
        assert response.status_code == 200
        job = response.get_json()["job"]
        if job["status"] == "done":
            return job
        time.sleep(0.05)
    raise AssertionError("timed out waiting for message job")


def test_channel_readiness_and_legacy_fallback():
    base = {
        "sms_gateway": "local",
        "sms_test_mode": "1",
        "whatsapp_enabled": "1",
        "sms_api_url": "http://127.0.0.1:8080/send",
        "sms_api_key": "legacy-key",
    }
    payload = channels_payload(base)
    assert payload["test_mode"] is True
    mobile = next(item for item in payload["channels"] if item["id"] == "mobile")
    cloud = next(item for item in payload["channels"] if item["id"] == "cloud")
    whatsapp = next(item for item in payload["channels"] if item["id"] == "whatsapp")
    assert mobile["ready"] and mobile["configured"] is True
    assert cloud["ready"] and cloud["configured"] is False
    assert whatsapp["ready"] and whatsapp["configured"] is True
    assert resolve_channel_settings(base, "mobile")["sms_api_url"] == base["sms_api_url"]
    assert resolve_channel_settings(base, "mobile")["sms_api_key"] == "legacy-key"
    assert resolve_channel_settings(base, "cloud")["sms_api_url"] == ""

    configured = {
        **base,
        "sms_mobile_url": "http://192.168.1.20:8080/send",
        "sms_mobile_key": "phone-key",
        "sms_cloud_url": "https://sms.example.com/send",
        "sms_cloud_key": "cloud-key",
    }
    channels = channels_payload(configured)
    assert channels["channels"][0]["configured"] is True
    assert channels["channels"][1]["configured"] is True
    assert resolve_channel_settings(configured, "mobile")["sms_api_key"] == "phone-key"
    assert resolve_channel_settings(configured, "cloud")["sms_api_key"] == "cloud-key"


def test_messages_students_endpoint_counts(client, make_student):
    make_student(student_nin="2099000801", class_group="1AS1", parent_phone="0550123456")
    make_student(student_nin="2099000802", class_group="1AS2", parent_phone="")
    data = client.get("/api/messages/students").get_json()
    assert data["counts"]["total"] == 2
    assert data["counts"]["sms_ready"] == 1
    assert data["counts"]["whatsapp_ready"] == 1
    assert data["counts"]["missing_phone"] == 1
    assert {item["id"] for item in data["channels"]["channels"]} == {
        "mobile",
        "cloud",
        "whatsapp",
    }


def test_preview_scopes_and_eligibility(client, csrf, make_student):
    make_student(student_nin="2099000811", class_group="1AS1", parent_phone="0550123456")
    make_student(student_nin="2099000812", class_group="1AS2", parent_phone="0551112233")
    make_student(student_nin="2099000813", class_group="1AS2", parent_phone="")

    all_preview = client.post(
        "/api/messages/preview",
        json={"scope": "all", "channel": "mobile", "message": "مرحبا [اسم_التلميذ]"},
        headers=csrf,
    ).get_json()["preview"]
    assert all_preview["recipients_total"] == 3
    assert all_preview["eligible_total"] == 2
    assert all_preview["skipped_total"] == 1
    assert all_preview["samples"][0]["message_body"].startswith("مرحبا ")

    class_preview = client.post(
        "/api/messages/preview",
        json={
            "scope": "class",
            "class_group": "1AS2",
            "channel": "mobile",
            "message": "رسالة قسم",
        },
        headers=csrf,
    ).get_json()["preview"]
    assert class_preview["recipients_total"] == 2
    assert class_preview["eligible_total"] == 1

    selected_preview = client.post(
        "/api/messages/preview",
        json={
            "scope": "selected",
            "student_nins": ["2099000811", "2099000812"],
            "channel": "whatsapp",
            "message": "رسالة محددة",
        },
        headers=csrf,
    ).get_json()["preview"]
    assert selected_preview["recipients_total"] == 2
    assert selected_preview["eligible_total"] == 2
    assert selected_preview["samples"][0]["whatsapp_url"].startswith("https://wa.me/")


def test_preview_requires_message(client, csrf):
    response = client.post(
        "/api/messages/preview",
        json={"scope": "all", "channel": "mobile", "message": "   "},
        headers=csrf,
    )
    assert response.status_code == 400


def test_send_requires_explicit_approval(client, csrf, make_student):
    make_student(student_nin="2099000821")
    response = client.post(
        "/api/messages/send",
        json={"scope": "all", "channel": "mobile", "message": "اختبار", "approved": False},
        headers=csrf,
    )
    assert response.status_code == 400
    campaigns = client.get("/api/messages/campaigns").get_json()
    assert campaigns["total"] == 0


def test_test_mode_sms_campaign_completes_with_simulation(client, csrf, make_student):
    make_student(student_nin="2099000831", parent_phone="0550123456")
    response = client.post(
        "/api/messages/send",
        json={"scope": "all", "channel": "mobile", "message": "رسالة جماعية", "approved": True},
        headers=csrf,
    )
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data["job_started"] is True
    campaign_id = data["campaign"]["id"]
    job = _wait_for_job(client, data["job"]["id"])
    assert job["sent"] == 1
    assert job["failed"] == 0
    assert job["simulated"] == 1

    detail = client.get(f"/api/messages/campaigns/{campaign_id}").get_json()
    assert detail["campaign"]["status"] == "DONE"
    assert detail["campaign"]["counts"]["SENT"] == 1
    assert detail["items"][0]["delivery_status"] == "SENT"
    assert detail["items"][0]["is_test"] == 1


def test_whatsapp_campaign_returns_manual_links_without_job(client, csrf, make_student):
    make_student(student_nin="2099000841", parent_phone="0550123456")
    response = client.post(
        "/api/messages/send",
        json={
            "scope": "all",
            "channel": "whatsapp",
            "message": "مرحبا [اسم_التلميذ]",
            "approved": True,
        },
        headers=csrf,
    )
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert "job" not in data
    assert len(data["items"]) == 1
    assert data["items"][0]["whatsapp_url"].startswith("https://wa.me/")
    assert data["campaign"]["status"] == "PREPARED"

    detail = client.get(f"/api/messages/campaigns/{data['campaign']['id']}").get_json()
    assert detail["items"][0]["delivery_status"] == "MANUAL"
    assert detail["campaign"]["counts"]["MANUAL"] == 1


def test_send_rejects_new_campaign_while_job_running(app, client, csrf, make_student):
    make_student(student_nin="2099000851")
    manager = app.extensions["message_jobs"]
    with manager._lock:
        manager._jobs["busy-test"] = {
            "id": "busy-test",
            "campaign_id": "other",
            "status": "running",
            "total": 1,
            "processed": 0,
            "sent": 0,
            "failed": 0,
            "cancelled": 0,
            "simulated": 0,
        }
        manager._current_id = "busy-test"
    try:
        response = client.post(
            "/api/messages/send",
            json={"scope": "all", "channel": "cloud", "message": "اختبار", "approved": True},
            headers=csrf,
        )
        assert response.status_code == 409
        assert response.get_json()["error"]["code"] == "busy"
        assert client.get("/api/messages/campaigns").get_json()["total"] == 0
    finally:
        with manager._lock:
            manager._jobs.pop("busy-test", None)
            manager._current_id = None


def test_settings_accept_channel_endpoints_and_reject_bad_urls(client, csrf):
    response = client.put(
        "/api/settings",
        json={
            "sms_mobile_url": "http://192.168.1.20:8080/send",
            "sms_mobile_key": "phone-key",
            "sms_cloud_url": "https://sms.example.com/send",
            "sms_cloud_key": "cloud-key",
        },
        headers=csrf,
    )
    assert response.status_code == 200, response.get_json()
    settings = response.get_json()["settings"]
    assert settings["sms_mobile_url"] == "http://192.168.1.20:8080/send"
    assert settings["sms_mobile_key"] == "phone-key"
    assert settings["sms_cloud_url"] == "https://sms.example.com/send"
    assert settings["sms_cloud_key"] == "cloud-key"

    bad = client.put(
        "/api/settings",
        json={"sms_mobile_url": "ftp://invalid"},
        headers=csrf,
    )
    assert bad.status_code == 400


def test_messages_page_renders(client):
    response = client.get("/messages")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "msg-channels" in html
    assert "msg-body" in html
    assert "msg-approve" in html
