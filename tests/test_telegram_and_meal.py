from __future__ import annotations

import sqlite3
import time
from datetime import date

from attendance_app.db import connect, init_db
from attendance_app.services import telegram_service

TOKEN = "123456789:AAHtest-token-value-1234567890"
CHAT_ID = "-1001234567890"


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


def _enable_telegram(client, csrf, **extra):
    payload = {
        "telegram_enabled": True,
        "telegram_bot_token": TOKEN,
        "telegram_chat_id": CHAT_ID,
    }
    payload.update(extra)
    response = client.put("/api/settings", json=payload, headers=csrf)
    assert response.status_code == 200, response.get_json()
    return response.get_json()["settings"]


def test_init_db_migrates_old_message_channel_check(tmp_path):
    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(
        """
        CREATE TABLE message_campaigns (
            id TEXT PRIMARY KEY,
            channel TEXT NOT NULL CHECK (channel IN ('mobile', 'cloud', 'whatsapp')),
            scope TEXT NOT NULL CHECK (scope IN ('all', 'class', 'selected')),
            class_group TEXT,
            message_template TEXT NOT NULL,
            recipients_total INTEGER NOT NULL DEFAULT 0,
            eligible_total INTEGER NOT NULL DEFAULT 0,
            skipped_total INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'PENDING',
            test_mode INTEGER NOT NULL DEFAULT 0,
            created_by TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            finished_at TEXT
        );
        CREATE TABLE message_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id TEXT NOT NULL,
            student_nin TEXT NOT NULL,
            student_name TEXT NOT NULL DEFAULT '',
            class_group TEXT,
            parent_phone TEXT NOT NULL,
            message_body TEXT NOT NULL,
            channel TEXT NOT NULL CHECK (channel IN ('mobile', 'cloud', 'whatsapp')),
            delivery_status TEXT NOT NULL DEFAULT 'PENDING',
            attempts INTEGER NOT NULL DEFAULT 0,
            error_msg TEXT,
            gateway TEXT,
            is_test INTEGER NOT NULL DEFAULT 0,
            sent_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (campaign_id) REFERENCES message_campaigns(id) ON DELETE CASCADE
        );
        INSERT INTO message_campaigns (id, channel, scope, message_template)
        VALUES ('legacy01', 'whatsapp', 'all', 'قديمة');
        INSERT INTO message_outbox (campaign_id, student_nin, parent_phone, message_body, channel)
        VALUES ('legacy01', '1099000001', '0550123456', 'قديمة', 'whatsapp');
        """
    )
    legacy.commit()
    legacy.close()

    init_db(path)

    conn = connect(path)
    try:
        campaign = conn.execute(
            "SELECT message_template FROM message_campaigns WHERE id = 'legacy01'"
        ).fetchone()
        assert campaign["message_template"] == "قديمة"
        outbox_count = conn.execute("SELECT COUNT(*) FROM message_outbox").fetchone()[0]
        assert outbox_count == 1
        with conn:
            conn.execute(
                """INSERT INTO message_campaigns (id, channel, scope, message_template)
                   VALUES ('telegram01', 'telegram', 'all', 'جديدة')"""
            )
            conn.execute(
                """INSERT INTO message_outbox
                   (campaign_id, student_nin, parent_phone, message_body, channel)
                   VALUES ('telegram01', '1099000002', '', 'جديدة', 'telegram')"""
            )
        assert conn.execute(
            "SELECT channel FROM message_outbox WHERE campaign_id = 'telegram01'"
        ).fetchone()["channel"] == "telegram"
    finally:
        conn.close()


def test_chat_id_normalization_and_token_validation():
    assert telegram_service.normalize_chat_id("123456789") == "123456789"
    assert telegram_service.normalize_chat_id("-1001234567890") == "-1001234567890"
    assert telegram_service.normalize_chat_id(" @school_channel ") == "@school_channel"
    assert telegram_service.normalize_chat_id(" 123456789 ") == "123456789"
    assert telegram_service.normalize_chat_id("+213555123456") == ""
    assert telegram_service.normalize_chat_id("abc") == ""
    assert telegram_service.normalize_chat_id("-12") == ""
    assert telegram_service.token_valid(TOKEN) is True
    assert telegram_service.token_valid("12345:short") is False
    assert telegram_service.token_valid("") is False


def test_telegram_status_tracks_enabled_and_configuration():
    base = {
        "sms_test_mode": "1",
        "telegram_enabled": "1",
        "telegram_bot_token": TOKEN,
        "telegram_chat_id": CHAT_ID,
    }
    status = telegram_service.telegram_status(base)
    assert status["configured"] is True
    assert status["ready"] is True
    assert status["missing"] == []
    assert status["chat_id"] == CHAT_ID

    disabled = telegram_service.telegram_status({**base, "telegram_enabled": "0"})
    assert disabled["configured"] is True
    assert disabled["ready"] is False

    incomplete = telegram_service.telegram_status(
        {"sms_test_mode": "0", "telegram_enabled": "1", "telegram_chat_id": ""}
    )
    assert incomplete["configured"] is False
    assert incomplete["ready"] is False
    assert len(incomplete["missing"]) == 2


def test_settings_store_nouns_meal_label_and_telegram(client, csrf):
    settings = _enable_telegram(
        client,
        csrf,
        student_noun="طالب",
        student_noun_plural="طلبة",
        meal_period_label="فترة الغداء",
    )
    assert settings["student_noun"] == "طالب"
    assert settings["student_noun_plural"] == "طلبة"
    assert settings["meal_period_label"] == "فترة الغداء"
    assert settings["telegram_enabled"] is True
    assert settings["telegram_bot_token"] == TOKEN
    assert settings["telegram_chat_id"] == CHAT_ID

    fetched = client.get("/api/settings").get_json()["settings"]
    assert fetched["student_noun"] == "طالب"
    assert fetched["meal_period_label"] == "فترة الغداء"
    assert fetched["telegram_chat_id"] == CHAT_ID


def test_settings_reject_invalid_telegram_values(client, csrf):
    bad_token = client.put(
        "/api/settings", json={"telegram_bot_token": "bad-token"}, headers=csrf
    )
    assert bad_token.status_code == 400
    bad_chat = client.put(
        "/api/settings", json={"telegram_chat_id": "!!!"}, headers=csrf
    )
    assert bad_chat.status_code == 400


def test_telegram_test_endpoint_simulates_in_test_mode(client, csrf):
    _enable_telegram(client, csrf)
    response = client.post("/api/settings/test-telegram", json={}, headers=csrf)
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data["result"]["simulated"] is True
    assert data["bot"]["simulated"] is True


def test_telegram_campaign_reaches_chat_without_phone_numbers(
    client, csrf, make_student
):
    make_student(student_nin="3099000101", parent_phone="")
    _enable_telegram(client, csrf)
    preview = client.post(
        "/api/messages/preview",
        json={
            "scope": "all",
            "channel": "telegram",
            "message": "تذكير: [اسم_التلميذ]",
        },
        headers=csrf,
    )
    assert preview.status_code == 200, preview.get_json()
    preview_data = preview.get_json()["preview"]
    assert preview_data["eligible_total"] == 1
    assert preview_data["segments"] == 0

    response = client.post(
        "/api/messages/send",
        json={
            "scope": "all",
            "channel": "telegram",
            "message": "تذكير: [اسم_التلميذ]",
            "approved": True,
        },
        headers=csrf,
    )
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data["job_started"] is True
    job = _wait_for_job(client, data["job"]["id"])
    assert job["sent"] == 1
    assert job["failed"] == 0
    assert job["simulated"] == 1

    detail = client.get(f"/api/messages/campaigns/{data['campaign']['id']}").get_json()
    assert detail["campaign"]["channel"] == "telegram"
    assert detail["campaign"]["status"] == "DONE"
    assert detail["items"][0]["delivery_status"] == "SENT"


def test_telegram_campaign_requires_channel_enabled(client, csrf, make_student):
    make_student(student_nin="3099000102")
    response = client.post(
        "/api/messages/send",
        json={
            "scope": "all",
            "channel": "telegram",
            "message": "اختبار",
            "approved": True,
        },
        headers=csrf,
    )
    assert response.status_code == 400
    assert "تيليغرام" in response.get_json()["error"]["message"]


def test_telegram_send_message_real_mode_calls_bot_api(monkeypatch):
    calls = {}

    class FakeResponse:
        status_code = 200
        text = '{"ok": true, "result": {"message_id": 42}}'

        def close(self):
            return None

    def fake_post(url, json=None, **kwargs):
        calls["url"] = url
        calls["json"] = json
        return FakeResponse()

    monkeypatch.setattr(telegram_service.requests, "post", fake_post)
    settings = {
        "sms_test_mode": "0",
        "telegram_enabled": "1",
        "telegram_bot_token": TOKEN,
        "telegram_chat_id": CHAT_ID,
    }
    result = telegram_service.send_message(settings, "رسالة حقيقية")
    assert result["simulated"] is False
    assert result["message_id"] == "42"
    assert calls["url"].endswith(f"/bot{TOKEN}/sendMessage")
    assert calls["json"]["chat_id"] == CHAT_ID
    assert calls["json"]["text"] == "رسالة حقيقية"


def test_meal_period_accepts_1130_1230_window(client, csrf):
    response = client.post(
        "/api/periods",
        json={
            "period_name": "فترة الإطعام",
            "period_type": "MEAL",
            "start_time": "11:00",
            "on_time_cutoff": "11:15",
            "absent_cutoff_time": "12:30",
            "is_active": True,
            "sort_order": 7,
        },
        headers=csrf,
    )
    assert response.status_code == 200, response.get_json()
    period = response.get_json()["period"]
    assert period["period_type"] == "MEAL"
    assert period["absent_cutoff_time"].startswith("12:30")


def test_meal_absences_appear_in_daily_and_monthly_reports(
    client, csrf, db, make_student
):
    created = client.post(
        "/api/periods",
        json={
            "period_name": "فترة الإطعام",
            "period_type": "MEAL",
            "start_time": "11:00",
            "on_time_cutoff": "11:15",
            "absent_cutoff_time": "12:30",
            "is_active": True,
            "sort_order": 7,
        },
        headers=csrf,
    ).get_json()["period"]
    client.put("/api/settings", json={"meal_period_label": "فترة الغداء"}, headers=csrf)
    student = make_student(student_nin="3099000201")
    day = date.today().isoformat()
    with db:
        db.execute(
            """INSERT INTO attendance_logs
               (student_nin, period_id, attendance_date, status, source, note)
               VALUES (?, ?, ?, 'ABSENT', 'SWEEP', 'رصد آلي')""",
            (student["student_nin"], created["id"], day),
        )

    daily = client.get("/api/reports/daily", query_string={"date": day})
    assert daily.status_code == 200, daily.get_json()
    report = daily.get_json()
    assert report["meal_label"] == "فترة الغداء"
    assert report["meal_absent_total"] == 1
    assert [item["student_nin"] for item in report["meal_absences"]] == [
        student["student_nin"]
    ]

    monthly = client.get(
        "/api/reports/monthly", query_string={"month": day[:7]}
    )
    assert monthly.status_code == 200, monthly.get_json()
    month_report = monthly.get_json()
    assert month_report["meal_label"] == "فترة الغداء"
    assert month_report["meal_absent_total"] == 1
    assert month_report["meal_absences"][0]["count"] == 1
