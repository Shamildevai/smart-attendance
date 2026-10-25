from __future__ import annotations

import json
import time

import pytest
import requests

from attendance_app import config
from attendance_app.services import messaging_service, sms_service
from attendance_app.services.messaging_service import (
    channels_payload,
    resolve_channel_settings,
)
from attendance_app.services.sms_service import SmsGatewayError


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


def _health_payload(**overrides):
    payload = {
        "ok": True,
        "app": config.GATEWAY_APP_NAME,
        "version": config.GATEWAY_APP_VERSION,
        "status": "running",
        "authRequired": False,
    }
    payload.update(overrides)
    return payload


def test_normalize_mobile_url_accepts_bare_host_and_health_path():
    assert sms_service.normalize_mobile_url("") == ""
    assert sms_service.normalize_mobile_url("ftp://192.168.1.20") == ""
    assert (
        sms_service.normalize_mobile_url("192.168.1.20:8080")
        == "http://192.168.1.20:8080/send"
    )
    assert (
        sms_service.normalize_mobile_url("192.168.1.20:8080/")
        == "http://192.168.1.20:8080/send"
    )
    assert (
        sms_service.normalize_mobile_url("http://192.168.1.20:8080/health")
        == "http://192.168.1.20:8080/send"
    )
    assert (
        sms_service.normalize_mobile_url("https://gateway.example/api/send?mode=sms")
        == "https://gateway.example/api/send?mode=sms"
    )


def test_probe_gateway_reports_connected_with_identity_headers(monkeypatch):
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers", {})
        return _FakeResponse(200, _health_payload())

    monkeypatch.setattr(sms_service.requests, "get", fake_get)
    result = sms_service.probe_gateway(
        "http://192.168.1.50:8080/send", "", force=True
    )
    assert result["status"] == "connected"
    assert result["connected"] is True
    assert result["can_send"] is True
    assert result["version"] == config.GATEWAY_APP_VERSION
    assert result["health_url"] == "http://192.168.1.50:8080/health"
    assert captured["url"] == result["health_url"]
    assert captured["headers"]["X-Client-App"] == "SmartAttendance"
    assert captured["headers"]["User-Agent"] == f"SmartAttendance/{config.APP_VERSION}"


def test_probe_gateway_flags_outdated_version(monkeypatch):
    monkeypatch.setattr(
        sms_service.requests,
        "get",
        lambda url, **kwargs: _FakeResponse(200, _health_payload(version="0.9.0")),
    )
    result = sms_service.probe_gateway("http://192.168.1.51:8080/send", "", force=True)
    assert result["status"] == "outdated"
    assert result["outdated"] is True
    assert result["can_send"] is True
    assert "0.9.0" in result["detail"]


def test_probe_gateway_requires_key_when_gateway_asks_for_it(monkeypatch):
    monkeypatch.setattr(
        sms_service.requests,
        "get",
        lambda url, **kwargs: _FakeResponse(200, _health_payload(authRequired=True)),
    )
    result = sms_service.probe_gateway("http://192.168.1.52:8080/send", "", force=True)
    assert result["status"] == "auth_required"
    assert result["auth_required"] is True
    assert result["can_send"] is False

    monkeypatch.setattr(
        sms_service.requests,
        "get",
        lambda url, **kwargs: _FakeResponse(401),
    )
    rejected = sms_service.probe_gateway("http://192.168.1.53:8080/send", "", force=True)
    assert rejected["status"] == "auth_required"
    assert rejected["connected"] is True
    assert rejected["can_send"] is False


def test_probe_gateway_rejects_wrong_api_key_without_sending_sms(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        captured["headers"] = kwargs.get("headers", {})
        return _FakeResponse(401)

    monkeypatch.setattr(
        sms_service.requests,
        "get",
        lambda url, **kwargs: _FakeResponse(200, _health_payload(authRequired=True)),
    )
    monkeypatch.setattr(sms_service.requests, "post", fake_post)
    result = sms_service.probe_gateway(
        "http://192.168.1.56:8080/send", "wrong-key", force=True
    )
    assert result["status"] == "auth_required"
    assert result["auth_valid"] is False
    assert result["can_send"] is False
    assert "غير صحيح" in result["detail"]
    assert captured["url"] == "http://192.168.1.56:8080/send"
    assert captured["json"] == {"phone": "0", "message": ""}
    assert captured["headers"]["X-Api-Key"] == "wrong-key"


def test_probe_gateway_accepts_valid_api_key(monkeypatch):
    monkeypatch.setattr(
        sms_service.requests,
        "get",
        lambda url, **kwargs: _FakeResponse(200, _health_payload(authRequired=True)),
    )
    monkeypatch.setattr(
        sms_service.requests,
        "post",
        lambda url, **kwargs: _FakeResponse(400, {"ok": False, "error": "Invalid phone number"}),
    )
    result = sms_service.probe_gateway(
        "http://192.168.1.57:8080/send", "good-key", force=True
    )
    assert result["status"] == "connected"
    assert result["auth_valid"] is True
    assert result["can_send"] is True


def test_send_via_gateway_reports_rejected_key(monkeypatch):
    monkeypatch.setattr(
        sms_service.requests,
        "post",
        lambda url, **kwargs: _FakeResponse(401),
    )
    settings = {
        "sms_gateway": "local",
        "sms_test_mode": "0",
        "sms_api_url": "http://192.168.1.58:8288/send",
        "sms_api_key": "wrong-key",
    }
    with pytest.raises(SmsGatewayError) as error:
        sms_service.send_via_gateway(settings, "0550123456", "اختبار")
    assert "مفتاح API" in str(error.value)
    assert "HTTP 401" in str(error.value)


def test_probe_gateway_rejects_wrong_service(monkeypatch):
    monkeypatch.setattr(
        sms_service.requests,
        "get",
        lambda url, **kwargs: _FakeResponse(200, {"ok": True, "app": "Some Other Server"}),
    )
    result = sms_service.probe_gateway("http://192.168.1.54:8080/send", "", force=True)
    assert result["status"] == "wrong_service"
    assert result["can_send"] is False


def test_probe_gateway_reports_offline_with_actionable_hint(monkeypatch):
    def fail(url, **kwargs):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(sms_service.requests, "get", fail)
    result = sms_service.probe_gateway("http://192.168.1.55:8080/send", "", force=True)
    assert result["status"] == "offline"
    assert result["connected"] is False
    assert result["can_send"] is False
    assert "8080" in result["hint"]


def test_probe_gateway_requires_configured_url():
    result = sms_service.probe_gateway("", "", force=True)
    assert result["status"] == "not_configured"
    invalid = sms_service.probe_gateway("ftp://gateway", "", force=True)
    assert invalid["status"] == "invalid_url"
    assert invalid["can_send"] is False


def test_gateway_endpoint_exposes_phone_app_metadata(client):
    response = client.get("/api/messages/gateway")
    assert response.status_code == 200
    gateway = response.get_json()["gateway"]
    assert gateway["gateway_app"]["name"] == config.GATEWAY_APP_NAME
    assert gateway["gateway_app"]["version"] == config.GATEWAY_APP_VERSION
    assert (
        gateway["gateway_app"]["download_url"]
        == f"https://fetsync.org/Update/Attendance-SMS-Gateway-{config.GATEWAY_APP_VERSION}.apk"
    )
    assert gateway["mobile"]["status"] == "not_configured"
    assert gateway["mobile"]["can_send"] is False
    assert "installed" in gateway["whatsapp_desktop"]
    assert gateway["whatsapp_desktop"]["download_url"] == config.WHATSAPP_DOWNLOAD_URL


def test_test_sms_endpoint_uses_mobile_channel_for_local_gateway(
    client, csrf, monkeypatch
):
    captured = {}

    def fake_test_gateway(resolved, phone):
        captured.update(resolved)
        captured["phone"] = phone
        return {"sent": False, "simulated": True, "message": "ok"}

    monkeypatch.setattr(sms_service, "test_gateway", fake_test_gateway)
    response = client.post(
        "/api/settings/test-sms", json={"phone": "0550123456"}, headers=csrf
    )
    assert response.status_code == 200, response.get_json()
    assert captured["sms_gateway"] == "local"
    assert captured["phone"] == "0550123456"


def test_test_sms_endpoint_uses_cloud_channel_for_cloud_gateway(
    client, csrf, db, monkeypatch
):
    from attendance_app.db import save_settings

    with db:
        save_settings(
            db,
            {
                "sms_gateway": "cloud",
                "sms_cloud_url": "https://sms.example.test/send",
                "sms_cloud_key": "cloud-key",
            },
        )
    captured = {}

    def fake_test_gateway(resolved, phone):
        captured.update(resolved)
        return {"sent": False, "simulated": True, "message": "ok"}

    monkeypatch.setattr(sms_service, "test_gateway", fake_test_gateway)
    response = client.post(
        "/api/settings/test-sms", json={"phone": "0550123456"}, headers=csrf
    )
    assert response.status_code == 200, response.get_json()
    assert captured["sms_gateway"] == "cloud"
    assert captured["sms_api_url"] == "https://sms.example.test/send"
    assert captured["sms_api_key"] == "cloud-key"


def test_settings_normalize_bare_mobile_gateway_host(client, csrf):
    response = client.put(
        "/api/settings",
        json={"sms_mobile_url": "192.168.1.20:8080"},
        headers=csrf,
    )
    assert response.status_code == 200, response.get_json()
    assert (
        response.get_json()["settings"]["sms_mobile_url"]
        == "http://192.168.1.20:8080/send"
    )


def test_real_mobile_campaign_blocked_when_gateway_unreachable(
    client, csrf, make_student, monkeypatch
):
    make_student(student_nin="2099000861", parent_phone="0550123456")
    saved = client.put(
        "/api/settings",
        json={
            "sms_mobile_url": "http://192.168.1.20:8080/send",
            "sms_test_mode": False,
        },
        headers=csrf,
    )
    assert saved.status_code == 200, saved.get_json()
    probed = {}

    def fake_probe(url, api_key="", **kwargs):
        probed["url"] = url
        probed["api_key"] = api_key
        return {
            "status": "offline",
            "connected": False,
            "can_send": False,
            "detail": "تعذر الاتصال ببوابة الهاتف.",
            "hint": "شغّل خادم البوابة داخل تطبيق الهاتف.",
        }

    monkeypatch.setattr(messaging_service, "probe_gateway", fake_probe)
    response = client.post(
        "/api/messages/send",
        json={"scope": "all", "channel": "mobile", "message": "اختبار", "approved": True},
        headers=csrf,
    )
    assert response.status_code == 400
    error = response.get_json()["error"]["message"]
    assert "ثبّت تطبيق بوابة الرسائل" in error
    assert probed["url"] == "http://192.168.1.20:8080/send"
    assert client.get("/api/messages/campaigns").get_json()["total"] == 0


def test_pages_expose_phone_app_and_whatsapp_download_links(client):
    for path in ("/messages", "/settings", "/about"):
        html = client.get(path).get_data(as_text=True)
        assert config.GATEWAY_APP_DOWNLOAD_URL in html, path
        assert config.WHATSAPP_DOWNLOAD_URL in html, path


def test_normalize_pair_pin_accepts_local_and_western_digits():
    assert sms_service.normalize_pair_pin("2983") == "2983"
    assert sms_service.normalize_pair_pin(" ٢٩٨٣ ") == "2983"
    assert sms_service.normalize_pair_pin("۲۹۸۳") == "2983"
    assert sms_service.normalize_pair_pin("29-83") == "2983"
    assert sms_service.normalize_pair_pin("298") == ""
    assert sms_service.normalize_pair_pin("abcd") == ""
    assert sms_service.normalize_pair_pin(None) == ""


def test_pair_gateway_exchanges_pin_for_api_key(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        captured["headers"] = kwargs.get("headers", {})
        return _FakeResponse(
            200,
            {
                "ok": True,
                "apiKey": "paired-secret-key-123456",
                "app": config.GATEWAY_APP_NAME,
                "version": config.GATEWAY_APP_VERSION,
                "port": 8080,
                "authRequired": True,
                "pairing": True,
            },
        )

    monkeypatch.setattr(sms_service.requests, "post", fake_post)
    result = sms_service.pair_gateway("192.168.1.20:8080", "٢٩٨٣")
    assert captured["url"] == "http://192.168.1.20:8080/pair"
    assert captured["json"] == {
        "pin": "2983",
        "client": f"SmartAttendance/{config.APP_VERSION}",
    }
    assert captured["headers"]["X-Client-App"] == "SmartAttendance"
    assert captured["headers"]["User-Agent"] == f"SmartAttendance/{config.APP_VERSION}"
    assert result["api_key"] == "paired-secret-key-123456"
    assert result["endpoint"] == "http://192.168.1.20:8080/send"
    assert result["pair_url"] == "http://192.168.1.20:8080/pair"
    assert result["outdated"] is False


def test_pair_gateway_flags_outdated_phone_app(monkeypatch):
    monkeypatch.setattr(
        sms_service.requests,
        "post",
        lambda url, **kwargs: _FakeResponse(
            200, {"apiKey": "paired-secret-key-123456", "version": "0.9.0"}
        ),
    )
    result = sms_service.pair_gateway("http://192.168.1.20:8080/send", "2983")
    assert result["outdated"] is True


def test_pair_gateway_maps_wrong_pin_rate_limit_and_bad_response(monkeypatch):
    monkeypatch.setattr(
        sms_service.requests, "post", lambda url, **kwargs: _FakeResponse(401)
    )
    with pytest.raises(sms_service.GatewayPairError) as wrong_pin:
        sms_service.pair_gateway("http://192.168.1.20:8080/send", "2983")
    assert "رمز الاقتران غير صحيح" in str(wrong_pin.value)

    monkeypatch.setattr(
        sms_service.requests,
        "post",
        lambda url, **kwargs: _FakeResponse(429, {"error": "too many", "retryAfter": 60}),
    )
    with pytest.raises(sms_service.GatewayPairError) as limited:
        sms_service.pair_gateway("http://192.168.1.20:8080/send", "2983")
    assert "60" in str(limited.value)

    monkeypatch.setattr(
        sms_service.requests,
        "post",
        lambda url, **kwargs: _FakeResponse(200, {"ok": True}),
    )
    with pytest.raises(sms_service.GatewayPairError) as missing_key:
        sms_service.pair_gateway("http://192.168.1.20:8080/send", "2983")
    assert "دون مفتاح صالح" in str(missing_key.value)

    with pytest.raises(sms_service.GatewayPairError) as invalid_pin:
        sms_service.pair_gateway("http://192.168.1.20:8080/send", "12ab")
    assert "4 أرقام" in str(invalid_pin.value)


def test_pair_endpoint_saves_key_and_updates_default_gateway(
    client, csrf, monkeypatch
):
    pair_calls = {}

    def fake_pair(url, pin, **kwargs):
        pair_calls["url"] = url
        pair_calls["pin"] = pin
        return {
            "api_key": "paired-secret-key-123456",
            "app": config.GATEWAY_APP_NAME,
            "version": config.GATEWAY_APP_VERSION,
            "port": 8080,
            "pair_url": "http://192.168.1.20:8080/pair",
            "endpoint": "http://192.168.1.20:8080/send",
            "outdated": False,
            "download_url": config.GATEWAY_APP_DOWNLOAD_URL,
            "required_version": config.GATEWAY_APP_VERSION,
        }

    monkeypatch.setattr(sms_service, "pair_gateway", fake_pair)
    monkeypatch.setattr(
        messaging_service,
        "gateway_status",
        lambda settings, force=False: {"mobile": {"status": "connected"}},
    )
    response = client.post(
        "/api/messages/gateway/pair",
        json={"url": "192.168.1.20:8080", "pin": "2983"},
        headers=csrf,
    )
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data["gateway"]["mobile"]["status"] == "connected"
    assert data["pairing"]["api_key"] == "paired-secret-key-123456"
    assert "اقتران الهاتف بنجاح" in data["message"]
    assert "لا حاجة لنسخه يدويًا" in data["message"]
    assert pair_calls == {"url": "192.168.1.20:8080", "pin": "2983"}

    settings = client.get("/api/settings").get_json()["settings"]
    assert settings["sms_mobile_url"] == "http://192.168.1.20:8080/send"
    assert settings["sms_mobile_key"] == "paired-secret-key-123456"
    assert settings["sms_api_url"] == "http://192.168.1.20:8080/send"
    assert settings["sms_api_key"] == "paired-secret-key-123456"


def test_pair_endpoint_reports_pairing_errors(client, csrf, monkeypatch):
    def fail_pair(url, pin, **kwargs):
        raise sms_service.GatewayPairError("رمز الاقتران غير صحيح. تحقق من الرمز الظاهر في تطبيق الهاتف.")

    monkeypatch.setattr(sms_service, "pair_gateway", fail_pair)
    response = client.post(
        "/api/messages/gateway/pair",
        json={"url": "192.168.1.20:8080", "pin": "0000"},
        headers=csrf,
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "pairing"
    assert "رمز الاقتران غير صحيح" in response.get_json()["error"]["message"]


def test_pair_endpoint_uses_saved_mobile_url_when_omitted(client, csrf, monkeypatch):
    saved = client.put(
        "/api/settings",
        json={"sms_mobile_url": "http://192.168.1.21:8080/send"},
        headers=csrf,
    )
    assert saved.status_code == 200, saved.get_json()
    seen = {}

    def fake_pair(url, pin, **kwargs):
        seen["url"] = url
        return {
            "api_key": "paired-secret-key-123456",
            "app": config.GATEWAY_APP_NAME,
            "version": config.GATEWAY_APP_VERSION,
            "port": 8080,
            "pair_url": "http://192.168.1.21:8080/pair",
            "endpoint": "http://192.168.1.21:8080/send",
            "outdated": False,
            "download_url": config.GATEWAY_APP_DOWNLOAD_URL,
            "required_version": config.GATEWAY_APP_VERSION,
        }

    monkeypatch.setattr(sms_service, "pair_gateway", fake_pair)
    monkeypatch.setattr(
        messaging_service,
        "gateway_status",
        lambda settings, force=False: {"mobile": {"status": "connected"}},
    )
    response = client.post(
        "/api/messages/gateway/pair", json={"pin": "2983"}, headers=csrf
    )
    assert response.status_code == 200, response.get_json()
    assert seen["url"] == "http://192.168.1.21:8080/send"


def test_whatsapp_cloud_settings_validation_and_defaults(client, csrf):
    response = client.put(
        "/api/settings",
        json={
            "whatsapp_provider": "cloud",
            "whatsapp_phone_number_id": "123 456 789",
            "whatsapp_access_token": "EAAG-test-token",
            "whatsapp_template_name": "absence_notice",
            "whatsapp_template_lang": "ar",
            "whatsapp_api_version": "23.0",
        },
        headers=csrf,
    )
    assert response.status_code == 200, response.get_json()
    settings = response.get_json()["settings"]
    assert settings["whatsapp_provider"] == "cloud"
    assert settings["whatsapp_phone_number_id"] == "123456789"
    assert settings["whatsapp_api_version"] == "v23.0"
    assert settings["whatsapp_template_lang"] == "ar"

    assert (
        client.put(
            "/api/settings", json={"whatsapp_provider": "email"}, headers=csrf
        ).status_code
        == 400
    )
    assert (
        client.put(
            "/api/settings",
            json={"whatsapp_template_name": "Absence Notice"},
            headers=csrf,
        ).status_code
        == 400
    )
    assert (
        client.put(
            "/api/settings", json={"whatsapp_template_lang": "ar-EG!"}, headers=csrf
        ).status_code
        == 400
    )
    assert (
        client.put(
            "/api/settings", json={"whatsapp_api_version": "latest"}, headers=csrf
        ).status_code
        == 400
    )


def test_channels_payload_reports_cloud_mode(client, csrf):
    saved = client.put(
        "/api/settings",
        json={
            "whatsapp_provider": "cloud",
            "whatsapp_phone_number_id": "123456789",
            "whatsapp_access_token": "EAAG-test-token",
            "whatsapp_template_name": "absence_notice",
        },
        headers=csrf,
    )
    assert saved.status_code == 200, saved.get_json()
    data = client.get("/api/messages/students").get_json()
    whatsapp = next(
        item for item in data["channels"]["channels"] if item["id"] == "whatsapp"
    )
    assert data["channels"]["whatsapp_provider"] == "cloud"
    assert data["channels"]["whatsapp_cloud"]["configured"] is True
    assert whatsapp["mode"] == "cloud"
    assert "Meta" in whatsapp["label"]


def test_messages_page_exposes_pairing_and_cloud_controls(client):
    html = client.get("/messages").get_data(as_text=True)
    assert "msg-gateway-pair" in html
    assert "whatsapp" in html
    settings_html = client.get("/settings").get_data(as_text=True)
    assert "sms-pair-pin" in settings_html
    assert "whatsapp-provider" in settings_html
    assert "whatsapp-access-token" in settings_html
