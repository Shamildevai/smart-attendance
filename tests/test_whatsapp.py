from __future__ import annotations

import json
import time
from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest

from attendance_app.services import whatsapp_service
from attendance_app.services.whatsapp_service import (
    WhatsAppConfigError,
    build_links,
    build_cloud_payload,
    normalize_whatsapp_number,
    send_via_cloud,
)

CLOUD_SETTINGS = {
    "sms_test_mode": "0",
    "whatsapp_provider": "cloud",
    "whatsapp_enabled": "1",
    "whatsapp_phone_number_id": "1234567890",
    "whatsapp_access_token": "EAAG-test-token",
    "whatsapp_template_name": "absence_notice",
    "whatsapp_template_lang": "ar",
    "whatsapp_api_version": "v23.0",
    "whatsapp_country_code": "213",
}


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self):
        if self._payload is None:
            raise ValueError("no json payload")
        return self._payload

    def close(self):
        pass


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



def test_normalize_whatsapp_number_variants():
    assert normalize_whatsapp_number("0550123456") == "213550123456"
    assert normalize_whatsapp_number("+213 550 123 456") == "213550123456"
    assert normalize_whatsapp_number("00213550123456") == "213550123456"
    assert normalize_whatsapp_number("550123456") == "213550123456"
    assert normalize_whatsapp_number("0550123456", "33") == "33550123456"
    assert normalize_whatsapp_number("+33612345678") == "33612345678"
    assert normalize_whatsapp_number("") == ""
    assert normalize_whatsapp_number("12") == ""


def test_build_links_encodes_message_and_phone():
    links = build_links("0550123456", "غياب التلميذ: أمين بن علي", "213")
    parsed = urlparse(links["whatsapp_url"])
    assert parsed.scheme == "https"
    assert parsed.netloc == "wa.me"
    assert parsed.path == "/213550123456"
    assert parse_qs(parsed.query)["text"] == ["غياب التلميذ: أمين بن علي"]
    assert links["whatsapp_phone"] == "213550123456"
    assert links["whatsapp_app_url"].startswith("whatsapp://send?phone=213550123456&text=")


def test_build_links_rejects_invalid_phone():
    assert build_links("", "رسالة") == {}
    assert build_links("123", "رسالة") == {}


def _make_absent(db, nin: str, period_id: int, with_sms: bool = True) -> None:
    today = date.today().isoformat()
    with db:
        db.execute(
            """INSERT INTO attendance_logs
               (student_nin, period_id, attendance_date, status, source, note)
               VALUES (?, ?, ?, 'ABSENT', 'SWEEP', 'رصد آلي')""",
            (nin, period_id, today),
        )
        if with_sms:
            db.execute(
                """INSERT INTO sms_logs
                   (student_nin, period_id, attendance_date, parent_phone, message_body, delivery_status)
                   VALUES (?, ?, ?, '0550123456', 'غياب التلميذ عن الفترة الصباحية.', 'PENDING')""",
                (nin, period_id, today),
            )


def test_sms_queue_and_attendance_expose_whatsapp_links(client, db, make_student, period):
    student = make_student(student_nin="2099000701", parent_phone="0550123456")
    _make_absent(db, student["student_nin"], period["id"])
    today = date.today().isoformat()

    queue = client.get(f"/api/sms/queue?date={today}&period_id={period['id']}").get_json()
    assert queue["whatsapp_enabled"] is True
    sms_item = queue["items"][0]
    assert sms_item["whatsapp_phone"] == "213550123456"
    assert sms_item["whatsapp_url"].startswith("https://wa.me/213550123456?text=")
    assert sms_item["whatsapp_app_url"].startswith("whatsapp://send?phone=213550123456&text=")

    logs = client.get(f"/api/attendance/list?date={today}&period_id={period['id']}").get_json()
    log_item = logs["items"][0]
    assert log_item["whatsapp_url"].startswith("https://wa.me/213550123456?text=")
    assert "sms_message_body" not in log_item


def test_attendance_whatsapp_uses_template_when_no_sms_row(client, db, make_student, period):
    student = make_student(student_nin="2099000702", parent_phone="0551112233")
    _make_absent(db, student["student_nin"], period["id"], with_sms=False)
    today = date.today().isoformat()

    logs = client.get(f"/api/attendance/list?date={today}&period_id={period['id']}").get_json()
    log_item = logs["items"][0]
    parsed = urlparse(log_item["whatsapp_url"])
    assert parsed.path == "/213551112233"
    assert parse_qs(parsed.query)["text"]


def test_whatsapp_settings_disable_links_and_validate_country(client, csrf, db, make_student, period):
    student = make_student(student_nin="2099000703")
    _make_absent(db, student["student_nin"], period["id"])
    today = date.today().isoformat()

    initial = client.get("/api/settings").get_json()["settings"]
    assert initial["whatsapp_enabled"] is True
    assert initial["whatsapp_country_code"] == "213"

    updated = client.put(
        "/api/settings",
        json={"whatsapp_enabled": False, "whatsapp_country_code": "33"},
        headers=csrf,
    )
    assert updated.status_code == 200
    settings = updated.get_json()["settings"]
    assert settings["whatsapp_enabled"] is False
    assert settings["whatsapp_country_code"] == "33"

    queue = client.get(f"/api/sms/queue?date={today}&period_id={period['id']}").get_json()
    assert queue["whatsapp_enabled"] is False
    assert "whatsapp_url" not in queue["items"][0]

    bad = client.put(
        "/api/settings",
        json={"whatsapp_country_code": "abc"},
        headers=csrf,
    )
    assert bad.status_code == 400


def test_build_cloud_payload_uses_approved_template_with_one_parameter():
    payload = build_cloud_payload(CLOUD_SETTINGS, "0550123456", "غياب التلميذ أمين")
    assert payload["messaging_product"] == "whatsapp"
    assert payload["recipient_type"] == "individual"
    assert payload["to"] == "213550123456"
    assert payload["type"] == "template"
    template = payload["template"]
    assert template["name"] == "absence_notice"
    assert template["language"] == {"code": "ar"}
    assert template["components"] == [
        {
            "type": "body",
            "parameters": [{"type": "text", "text": "غياب التلميذ أمين"}],
        }
    ]

    with pytest.raises(WhatsAppConfigError):
        build_cloud_payload(CLOUD_SETTINGS, "12", "رسالة")
    with pytest.raises(WhatsAppConfigError):
        build_cloud_payload(CLOUD_SETTINGS, "0550123456", "   ")
    with pytest.raises(WhatsAppConfigError):
        build_cloud_payload(CLOUD_SETTINGS, "0550123456", "ا" * 1025)
    without_template = {**CLOUD_SETTINGS, "whatsapp_template_name": ""}
    with pytest.raises(WhatsAppConfigError):
        build_cloud_payload(without_template, "0550123456", "رسالة")


def test_send_via_cloud_posts_to_meta_graph(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        captured["headers"] = kwargs.get("headers", {})
        return _FakeResponse(200, {"messages": [{"id": "wamid.TEST123"}]})

    monkeypatch.setattr(whatsapp_service.requests, "post", fake_post)
    result = send_via_cloud(CLOUD_SETTINGS, "0550123456", "غياب التلميذ")
    assert (
        captured["url"]
        == "https://graph.facebook.com/v23.0/1234567890/messages"
    )
    assert captured["headers"]["Authorization"] == "Bearer EAAG-test-token"
    assert captured["headers"]["Content-Type"] == "application/json"
    assert captured["json"]["template"]["name"] == "absence_notice"
    assert result == {
        "ok": True,
        "simulated": False,
        "provider": "whatsapp_cloud",
        "gateway": "whatsapp_cloud",
        "message_id": "wamid.TEST123",
        "detail": "WhatsApp Cloud API — HTTP 200",
    }


def test_send_via_cloud_simulates_in_test_mode_without_network(monkeypatch):
    def fail_post(*args, **kwargs):
        raise AssertionError("test mode must not reach the network")

    monkeypatch.setattr(whatsapp_service.requests, "post", fail_post)
    result = send_via_cloud(
        {**CLOUD_SETTINGS, "sms_test_mode": "1"}, "0550123456", "رسالة"
    )
    assert result["simulated"] is True
    assert result["provider"] == "whatsapp_cloud"
    assert result["message_id"] == ""


def test_send_via_cloud_requires_cloud_provider_and_credentials():
    with pytest.raises(WhatsAppConfigError) as manual:
        send_via_cloud({**CLOUD_SETTINGS, "whatsapp_provider": "manual"}, "0550123456", "رسالة")
    assert "Cloud" in str(manual.value)

    without_token = {**CLOUD_SETTINGS, "whatsapp_access_token": ""}
    with pytest.raises(WhatsAppConfigError) as token:
        send_via_cloud(without_token, "0550123456", "رسالة")
    assert "رمز وصول" in str(token.value)

    without_phone_id = {**CLOUD_SETTINGS, "whatsapp_phone_number_id": ""}
    with pytest.raises(WhatsAppConfigError) as phone_id:
        send_via_cloud(without_phone_id, "0550123456", "رسالة")
    assert "Phone Number ID" in str(phone_id.value)


def test_send_via_cloud_maps_meta_errors(monkeypatch):
    monkeypatch.setattr(
        whatsapp_service.requests,
        "post",
        lambda url, **kwargs: _FakeResponse(
            401, {"error": {"message": "Invalid OAuth access token"}}
        ),
    )
    with pytest.raises(WhatsAppConfigError) as auth:
        send_via_cloud(CLOUD_SETTINGS, "0550123456", "رسالة")
    assert "رمز الوصول" in str(auth.value)

    monkeypatch.setattr(
        whatsapp_service.requests,
        "post",
        lambda url, **kwargs: _FakeResponse(
            400, {"error": {"message": "Template name does not exist"}}
        ),
    )
    with pytest.raises(WhatsAppConfigError) as template:
        send_via_cloud(CLOUD_SETTINGS, "0550123456", "رسالة")
    assert "القالب" in str(template.value)
    assert "Template name does not exist" in str(template.value)

    monkeypatch.setattr(
        whatsapp_service.requests,
        "post",
        lambda url, **kwargs: _FakeResponse(429, {"error": {"message": "rate limited"}}),
    )
    with pytest.raises(whatsapp_service.WhatsAppSendError) as limited:
        send_via_cloud(CLOUD_SETTINGS, "0550123456", "رسالة")
    assert "حدود إرسال" in str(limited.value)


def test_test_whatsapp_endpoint_simulates_in_test_mode(client, csrf):
    saved = client.put(
        "/api/settings",
        json={
            "whatsapp_provider": "cloud",
            "whatsapp_phone_number_id": "1234567890",
            "whatsapp_access_token": "EAAG-test-token",
            "whatsapp_template_name": "absence_notice",
        },
        headers=csrf,
    )
    assert saved.status_code == 200, saved.get_json()
    response = client.post(
        "/api/settings/test-whatsapp", json={"phone": "0550123456"}, headers=csrf
    )
    assert response.status_code == 200, response.get_json()
    result = response.get_json()["result"]
    assert result["simulated"] is True
    assert result["sent"] is False

    invalid = client.post(
        "/api/settings/test-whatsapp", json={"phone": "12"}, headers=csrf
    )
    assert invalid.status_code == 400


def test_test_whatsapp_endpoint_requires_cloud_provider(client, csrf):
    response = client.post(
        "/api/settings/test-whatsapp", json={"phone": "0550123456"}, headers=csrf
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "whatsapp_config"


def test_cloud_whatsapp_campaign_runs_automatically(client, csrf, make_student):
    saved = client.put(
        "/api/settings",
        json={
            "whatsapp_provider": "cloud",
            "whatsapp_phone_number_id": "1234567890",
            "whatsapp_access_token": "EAAG-test-token",
            "whatsapp_template_name": "absence_notice",
        },
        headers=csrf,
    )
    assert saved.status_code == 200, saved.get_json()
    make_student(student_nin="2099000901", parent_phone="0550123456")
    response = client.post(
        "/api/messages/send",
        json={
            "scope": "all",
            "channel": "whatsapp",
            "message": "غياب [اسم_التلميذ] عن الحصة",
            "approved": True,
        },
        headers=csrf,
    )
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data["whatsapp_provider"] == "cloud"
    assert data["campaign"]["status"] == "PENDING"
    assert "job" in data
    job = _wait_for_job(client, data["job"]["id"])
    assert job["sent"] == 1
    assert job["failed"] == 0
    assert job["simulated"] == 1

    detail = client.get(f"/api/messages/campaigns/{data['campaign']['id']}").get_json()
    assert detail["campaign"]["status"] == "DONE"
    assert detail["items"][0]["delivery_status"] == "SENT"
    assert detail["items"][0]["is_test"] == 1
    assert "whatsapp_url" not in detail["items"][0]


def test_cloud_whatsapp_campaign_requires_meta_config_when_test_mode_off(
    client, csrf, db, make_student
):
    from attendance_app.db import save_settings

    with db:
        save_settings(
            db,
            {
                "sms_test_mode": "0",
                "whatsapp_provider": "cloud",
                "whatsapp_enabled": "1",
            },
        )
    make_student(student_nin="2099000902")
    response = client.post(
        "/api/messages/send",
        json={
            "scope": "all",
            "channel": "whatsapp",
            "message": "رسالة",
            "approved": True,
        },
        headers=csrf,
    )
    assert response.status_code == 400
    assert "Meta" in response.get_json()["error"]["message"]
    assert client.get("/api/messages/campaigns").get_json()["total"] == 0


def test_disable_test_mode_allowed_with_complete_cloud_config(client, csrf):
    response = client.put(
        "/api/settings",
        json={
            "sms_test_mode": False,
            "whatsapp_provider": "cloud",
            "whatsapp_phone_number_id": "1234567890",
            "whatsapp_access_token": "EAAG-test-token",
            "whatsapp_template_name": "absence_notice",
        },
        headers=csrf,
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["settings"]["sms_test_mode"] is False

