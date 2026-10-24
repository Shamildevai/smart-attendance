from __future__ import annotations

from datetime import date, datetime, time

from attendance_app.db import get_settings
from attendance_app.services import attendance_service, sms_service


def _at(hour: int, minute: int) -> datetime:
    return datetime.combine(date.today(), time(hour, minute))


def _insert_absent(db, nin: str, period_id: int, sms_status: str | None = None) -> int:
    with db:
        cursor = db.execute(
            """INSERT INTO attendance_logs
               (student_nin, period_id, attendance_date, status, source, note)
               VALUES (?, ?, ?, 'ABSENT', 'SWEEP', 'رصد آلي')""",
            (nin, period_id, date.today().isoformat()),
        )
        log_id = cursor.lastrowid
        if sms_status:
            db.execute(
                """INSERT INTO sms_logs
                   (student_nin, period_id, attendance_date, parent_phone, message_body, delivery_status)
                   VALUES (?, ?, ?, '0550123456', 'رسالة غياب تجريبية', ?)""",
                (nin, period_id, date.today().isoformat(), sms_status),
            )
    return log_id


def test_gate_scan_and_anti_passback(client, csrf, db, make_student, period):
    student = make_student(student_nin="2099000301")
    nin = student["student_nin"]

    first = attendance_service.scan_student(
        db, nin, period_id=period["id"], now=_at(7, 50), anti_passback_minutes=5
    )
    assert first["success"] is True
    assert first["action"] == "present"
    assert first["attendance"]["status"] == "PRESENT"
    assert first["attendance"]["scan_time"] == "07:50:00"

    duplicate = attendance_service.scan_student(
        db, nin, period_id=period["id"], now=_at(7, 52), anti_passback_minutes=5
    )
    assert duplicate["duplicate"] is True
    assert duplicate["action"] == "duplicate"
    assert "لا يمكن التسجيل مرتين" in duplicate["message"]

    later = attendance_service.scan_student(
        db, nin, period_id=period["id"], now=_at(8, 20), anti_passback_minutes=5
    )
    assert later["duplicate"] is True
    assert later["action"] == "already_recorded"

    count = db.execute(
        "SELECT COUNT(*) FROM attendance_logs WHERE student_nin = ?", (nin,)
    ).fetchone()[0]
    assert count == 1


def test_gate_api_flow(client, csrf, make_student, period):
    student = make_student(student_nin="2099000311")
    first = client.post(
        "/api/gate/scan",
        json={"code": student["student_nin"], "period_id": period["id"]},
        headers=csrf,
    )
    assert first.status_code == 200
    payload = first.get_json()
    assert payload["ok"] is True
    assert payload["action"] in ("present", "late")

    second = client.post(
        "/api/gate/scan",
        json={"code": student["student_nin"], "period_id": period["id"]},
        headers=csrf,
    )
    assert second.get_json()["action"] == "duplicate"

    unknown = client.post(
        "/api/gate/scan",
        json={"code": "9999999999", "period_id": period["id"]},
        headers=csrf,
    )
    assert unknown.status_code == 200
    assert unknown.get_json()["action"] == "not_found"

    client.put(
        f"/api/students/{student['student_nin']}",
        json={"is_active": False},
        headers=csrf,
    )
    inactive = client.post(
        "/api/gate/scan",
        json={"code": student["student_nin"], "period_id": period["id"]},
        headers=csrf,
    )
    assert inactive.get_json()["action"] == "inactive"


def test_absent_to_arrival_cancels_pending_sms(db, make_student, period):
    pending_student = make_student(student_nin="2099000321")
    _insert_absent(db, pending_student["student_nin"], period["id"], sms_status="PENDING")

    pending = attendance_service.scan_student(
        db, pending_student["student_nin"], period_id=period["id"], now=_at(8, 20)
    )
    assert pending["action"] == "updated_from_absent"
    assert pending["success"] is True
    assert pending["attendance"]["status"] == "LATE"
    assert pending["attendance"]["scan_time"] == "08:20:00"
    assert "تم إلغاء رسالة الغياب" in pending["sms_notice"]
    sms_status = db.execute(
        "SELECT delivery_status FROM sms_logs WHERE student_nin = ?",
        (pending_student["student_nin"],),
    ).fetchone()[0]
    assert sms_status == "CANCELLED"

    sent_student = make_student(student_nin="2099000322")
    _insert_absent(db, sent_student["student_nin"], period["id"], sms_status="SENT")

    sent = attendance_service.scan_student(
        db, sent_student["student_nin"], period_id=period["id"], now=_at(7, 55)
    )
    assert sent["action"] == "updated_from_absent"
    assert sent["attendance"]["status"] == "PRESENT"
    assert "سبق إرسال رسالة غياب" in sent["sms_notice"]
    assert "حاضر" in sent["sms_notice"]


def test_period_close_queue_and_excuse(client, csrf, db, make_student, period):
    present = make_student(student_nin="2099000331")
    absent = make_student(student_nin="2099000332")
    attendance_service.scan_student(
        db, present["student_nin"], period_id=period["id"], now=_at(7, 50)
    )

    refused = client.post(
        "/api/attendance/close",
        json={"period_id": period["id"], "date": date.today().isoformat(), "send_sms": True, "approved": False},
        headers=csrf,
    )
    assert refused.status_code == 400
    assert refused.get_json()["error"]["code"] == "approval_required"
    assert db.execute("SELECT COUNT(*) FROM period_closures").fetchone()[0] == 0

    closed = client.post(
        "/api/attendance/close",
        json={"period_id": period["id"], "date": date.today().isoformat()},
        headers=csrf,
    )
    assert closed.status_code == 200
    result = closed.get_json()["close"]
    assert result["closed_now"] is True
    assert result["inserted"] == 1
    assert result["already_closed"] is False

    absent_status = db.execute(
        "SELECT status FROM attendance_logs WHERE student_nin = ? AND period_id = ?",
        (absent["student_nin"], period["id"]),
    ).fetchone()[0]
    assert absent_status == "ABSENT"
    assert db.execute("SELECT COUNT(*) FROM sms_logs").fetchone()[0] == 0

    again = client.post(
        "/api/attendance/close",
        json={"period_id": period["id"], "date": date.today().isoformat()},
        headers=csrf,
    )
    assert again.get_json()["close"]["already_closed"] is True
    assert again.get_json()["close"]["inserted"] == 0

    no_approval = client.post(
        "/api/attendance/send-absences",
        json={"period_id": period["id"], "date": date.today().isoformat()},
        headers=csrf,
    )
    assert no_approval.status_code == 400
    assert no_approval.get_json()["error"]["code"] == "approval_required"

    queued = sms_service.queue_absences(
        db, period["id"], date.today().isoformat(), get_settings(db)
    )
    assert queued["created"] == 1
    assert len(queued["pending_ids"]) == 1

    absent_log_id = db.execute(
        "SELECT id FROM attendance_logs WHERE student_nin = ? AND period_id = ?",
        (absent["student_nin"], period["id"]),
    ).fetchone()[0]
    excused = client.post(
        f"/api/attendance/{absent_log_id}/excuse",
        json={"reason": "شهادة طبية"},
        headers=csrf,
    )
    assert excused.status_code == 200
    payload = excused.get_json()
    assert payload["cancelled_sms"] == 1
    assert payload["attendance"]["status"] == "EXCUSED"
    assert payload["attendance"]["note"] == "شهادة طبية"
    assert payload["sms_status"] == "CANCELLED"

    sms_status = db.execute(
        "SELECT delivery_status FROM sms_logs WHERE student_nin = ?",
        (absent["student_nin"],),
    ).fetchone()[0]
    assert sms_status == "CANCELLED"

    present_log_id = db.execute(
        "SELECT id FROM attendance_logs WHERE student_nin = ? AND period_id = ?",
        (present["student_nin"], period["id"]),
    ).fetchone()[0]
    not_excusable = client.post(
        f"/api/attendance/{present_log_id}/excuse",
        json={"reason": "محاولة غير صالحة"},
        headers=csrf,
    )
    assert not_excusable.status_code == 400
