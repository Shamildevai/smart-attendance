from __future__ import annotations

from datetime import date
from urllib.parse import parse_qs, urlparse

from attendance_app.services.whatsapp_service import build_links, normalize_whatsapp_number


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
