from __future__ import annotations

import html as html_lib
from datetime import date, datetime, time

from attendance_app.db import SCHEMA_VERSION
from attendance_app.services import attendance_service, entry_slip_service


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


def test_entry_slip_schema_and_context(client, db, make_student, period):
    make_student(student_nin="3099000401")
    assert db.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION
    columns = {
        row["name"]
        for row in db.execute("PRAGMA table_info(entry_slips)").fetchall()
    }
    assert {
        "slip_code",
        "student_nin",
        "period_id",
        "slip_date",
        "check_in_time",
        "previous_status",
        "new_status",
        "reason",
        "note",
        "issued_by",
        "signature",
        "print_count",
    } <= columns

    response = client.get(f"/api/entry-slip/context?period_id={period['id']}")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["selected_period"]["id"] == period["id"]
    assert payload["stats"]["total"] == 1
    assert len(payload["reasons"]) == 6
    assert payload["date"] == date.today().isoformat()


def test_entry_lookup_and_issue_converts_absence(client, csrf, db, make_student, period):
    student = make_student(student_nin="3099000411")
    nin = student["student_nin"]
    _insert_absent(db, nin, period["id"], sms_status="PENDING")

    lookup = client.post(
        "/api/entry-slip/lookup",
        json={"code": nin, "period_id": period["id"]},
        headers=csrf,
    )
    assert lookup.status_code == 200
    found = lookup.get_json()
    assert found["action"] == "absent"
    assert found["can_issue"] is True
    assert found["new_status_preview"] in ("PRESENT", "LATE")
    assert found["student"]["student_nin"] == nin

    issued = client.post(
        "/api/entry-slip",
        json={"code": nin, "period_id": period["id"], "reason": "موعد طبي", "note": "شهادة"},
        headers=csrf,
    )
    assert issued.status_code == 200
    slip = issued.get_json()["slip"]
    assert slip["slip_code"].startswith(f"ES-{date.today():%Y%m%d}-")
    assert len(slip["signature"]) == 64
    assert slip["qr_payload"].startswith("SAES1|")
    assert slip["print_url"].endswith(slip["slip_code"])
    assert slip["verify_url"].startswith("/entry-slip/verify?code=")
    assert slip["reason"] == "موعد طبي"

    log = db.execute(
        "SELECT * FROM attendance_logs WHERE student_nin = ? AND period_id = ?",
        (nin, period["id"]),
    ).fetchone()
    assert log["status"] == slip["new_status"]
    assert log["source"] == "ENTRY_SLIP"
    assert slip["slip_code"] in log["note"]
    sms_status = db.execute(
        "SELECT delivery_status FROM sms_logs WHERE student_nin = ?", (nin,)
    ).fetchone()[0]
    assert sms_status == "CANCELLED"

    repeat = client.post(
        "/api/entry-slip",
        json={"code": nin, "period_id": period["id"], "reason": "سبب آخر"},
        headers=csrf,
    )
    assert repeat.status_code == 200
    assert repeat.get_json()["already_issued"] is True
    assert repeat.get_json()["slip"]["slip_code"] == slip["slip_code"]
    assert db.execute("SELECT COUNT(*) FROM entry_slips").fetchone()[0] == 1

    context = client.get(f"/api/entry-slip/context?period_id={period['id']}").get_json()
    assert context["stats"]["issued"] == 1
    assert context["stats"]["absent"] == 0
    assert context["recent"][0]["slip_code"] == slip["slip_code"]

    verified = client.get(
        "/api/entry-slip/verify", query_string={"code": slip["qr_payload"]}
    )
    assert verified.status_code == 200
    verdict = verified.get_json()
    assert verdict["valid"] is True
    assert verdict["checked_signature"] is True
    assert verdict["slip"]["slip_code"] == slip["slip_code"]


def test_entry_slip_verification_detects_tampering(client, csrf, db, make_student, period):
    student = make_student(student_nin="3099000421")
    _insert_absent(db, student["student_nin"], period["id"])
    slip = client.post(
        "/api/entry-slip",
        json={"code": student["student_nin"], "period_id": period["id"], "reason": "ظرف عائلي"},
        headers=csrf,
    ).get_json()["slip"]

    parts = slip["qr_payload"].split("|")
    parts[2] = "9999999999"
    tampered = client.get(
        "/api/entry-slip/verify", query_string={"code": "|".join(parts)}
    ).get_json()
    assert tampered["valid"] is False
    assert tampered["slip"]["slip_code"] == slip["slip_code"]

    forged = slip["qr_payload"][:-4] + "0000"
    assert client.get(
        "/api/entry-slip/verify", query_string={"code": forged}
    ).get_json()["valid"] is False

    plain = client.get(
        "/api/entry-slip/verify", query_string={"code": slip["slip_code"]}
    ).get_json()
    assert plain["valid"] is None
    assert plain["checked_signature"] is False
    assert plain["slip"]["slip_code"] == slip["slip_code"]

    missing = client.get(
        "/api/entry-slip/verify", query_string={"code": "ES-19990101-0001"}
    ).get_json()
    assert missing["valid"] is False
    assert missing["slip"] is None


def test_entry_slip_blocks_recorded_and_excused(
    client, csrf, db, make_student, period
):
    present = make_student(student_nin="3099000431")
    attendance_service.scan_student(
        db, present["student_nin"], period_id=period["id"], now=_at(7, 50)
    )
    lookup = client.post(
        "/api/entry-slip/lookup",
        json={"code": present["student_nin"], "period_id": period["id"]},
        headers=csrf,
    ).get_json()
    assert lookup["action"] == "already_recorded"
    assert lookup["can_issue"] is False
    blocked = client.post(
        "/api/entry-slip",
        json={"code": present["student_nin"], "period_id": period["id"], "reason": "سبب"},
        headers=csrf,
    )
    assert blocked.status_code == 400
    assert db.execute("SELECT COUNT(*) FROM entry_slips").fetchone()[0] == 0

    excused = make_student(student_nin="3099000432")
    log_id = _insert_absent(db, excused["student_nin"], period["id"])
    attendance_service.excuse_attendance(db, log_id, "شهادة طبية")
    lookup = client.post(
        "/api/entry-slip/lookup",
        json={"code": excused["student_nin"], "period_id": period["id"]},
        headers=csrf,
    ).get_json()
    assert lookup["action"] == "excused"
    assert lookup["can_issue"] is False
    refused = client.post(
        "/api/entry-slip",
        json={"code": excused["student_nin"], "period_id": period["id"], "reason": "سبب"},
        headers=csrf,
    )
    assert refused.status_code == 400


def test_entry_slip_records_unlogged_arrival(db, make_student, period):
    student = make_student(student_nin="3099000441")
    result = entry_slip_service.issue_slip(
        db,
        student["student_nin"],
        period["id"],
        "تأخر صباحي",
        "",
        "المشرف",
        "test-secret",
        now=_at(7, 45),
    )
    slip = result["slip"]
    assert result["already_issued"] is False
    assert slip["new_status"] == "PRESENT"
    assert slip["previous_status_label"] == "بدون تسجيل"
    log = db.execute(
        "SELECT * FROM attendance_logs WHERE student_nin = ?", (student["student_nin"],)
    ).fetchone()
    assert log["status"] == "PRESENT"
    assert log["source"] == "ENTRY_SLIP"
    verified = entry_slip_service.verify_entry(db, slip["qr_payload"], "test-secret")
    assert verified["valid"] is True
    wrong = entry_slip_service.verify_entry(db, slip["qr_payload"], "other-secret")
    assert wrong["valid"] is False


def test_entry_slip_validates_reason(client, csrf, db, make_student, period):
    student = make_student(student_nin="3099000451")
    _insert_absent(db, student["student_nin"], period["id"])
    too_short = client.post(
        "/api/entry-slip",
        json={"code": student["student_nin"], "period_id": period["id"], "reason": "x"},
        headers=csrf,
    )
    assert too_short.status_code == 400
    too_long = client.post(
        "/api/entry-slip",
        json={
            "code": student["student_nin"],
            "period_id": period["id"],
            "reason": "س" * 301,
        },
        headers=csrf,
    )
    assert too_long.status_code == 400
    assert db.execute("SELECT COUNT(*) FROM entry_slips").fetchone()[0] == 0


def test_entry_slip_print_page_and_qr(client, csrf, db, make_student, period):
    student = make_student(student_nin="3099000461")
    _insert_absent(db, student["student_nin"], period["id"])
    slip = client.post(
        "/api/entry-slip",
        json={"code": student["student_nin"], "period_id": period["id"], "reason": "نقل مدرسي"},
        headers=csrf,
    ).get_json()["slip"]

    receipt = client.get(f"/entry-slip/print/{slip['slip_code']}?print=1")
    assert receipt.status_code == 200
    body = receipt.get_data(as_text=True)
    assert slip["slip_code"] in body
    assert student["full_name"] in body
    assert f"/api/entry-slip/{slip['slip_code']}/qr" in body
    assert 'data-autoprint="1"' in body
    assert db.execute(
        "SELECT print_count FROM entry_slips WHERE slip_code = ?", (slip["slip_code"],)
    ).fetchone()[0] == 1

    qr = client.get(f"/api/entry-slip/{slip['slip_code']}/qr")
    assert qr.status_code == 200
    assert qr.mimetype == "image/svg+xml"
    svg = qr.get_data(as_text=True)
    assert "<svg" in svg
    assert "<?xml" not in svg

    page = client.get(f"/entry-slip/verify?code={slip['qr_payload']}")
    assert page.status_code == 200
    assert "توقيع صحيح وموثّق" in page.get_data(as_text=True)


def test_entry_slip_page_translations(client):
    client.set_cookie("attendance_lang", "en")
    page = client.get("/entry-slip")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "Entry slip" in body
    assert "Ready to scan an absent student's card" in body
    assert "ورقة الدخول" not in body
    assert 'dir="ltr"' in body

    client.set_cookie("attendance_lang", "fr")
    page = client.get("/entry-slip")
    body = page.get_data(as_text=True)
    assert "Fiche d'entrée" in body
    assert "Prêt à scanner la carte d'un élève absent" in body
    assert 'dir="ltr"' in body


def test_entry_slip_receipt_relocalizes_per_request(client, csrf, db, make_student, period):
    student = make_student(student_nin="3099000481")
    _insert_absent(db, student["student_nin"], period["id"])
    slip = client.post(
        "/api/entry-slip",
        json={"code": student["student_nin"], "period_id": period["id"], "reason": "موعد طبي"},
        headers=csrf,
    ).get_json()["slip"]

    url = f"/entry-slip/print/{slip['slip_code']}"
    client.set_cookie("attendance_lang", "fr")
    french = html_lib.unescape(client.get(url).get_data(as_text=True))
    assert "Fiche d'entrée" in french
    assert "Signature numérique vérifiée" in french
    assert "Entry slip" not in french

    client.set_cookie("attendance_lang", "en")
    english = html_lib.unescape(client.get(url).get_data(as_text=True))
    assert "Entry slip" in english
    assert "Verified digital signature" in english
    assert "Fiche d'entrée" not in english

    client.set_cookie("attendance_lang", "ar")
    arabic = client.get(url).get_data(as_text=True)
    assert "ورقة الدخول" in arabic
    assert "Entry slip" not in arabic


def test_entry_slip_receipt_templates(client, csrf, db, make_student, period):
    student = make_student(student_nin="3099000491")
    _insert_absent(db, student["student_nin"], period["id"])
    slip = client.post(
        "/api/entry-slip",
        json={
            "code": student["student_nin"],
            "period_id": period["id"],
            "reason": "موعد طبي",
            "note": "ملاحظة ولي الأمر",
        },
        headers=csrf,
    ).get_json()["slip"]

    url = f"/entry-slip/print/{slip['slip_code']}"

    full = client.get(url).get_data(as_text=True)
    assert 'data-template="full"' in full
    assert "تاريخ الميلاد" in full
    assert "الحالة السابقة" in full
    assert "receipt-notice" in full
    assert "طابعة حرارية 80 مم" in full
    assert "entry-slip-print-small.css" not in full

    medium = client.get(f"{url}?template=medium").get_data(as_text=True)
    assert 'data-template="medium"' in medium
    assert "طابعة حرارية 80 مم" in medium
    assert "ملاحظة ولي الأمر" in medium
    assert "أصدرها" in medium
    assert "تاريخ الميلاد" not in medium
    assert "الحالة السابقة" not in medium
    assert "receipt-notice" not in medium
    assert "entry-slip-print-small.css" not in medium

    small = client.get(f"{url}?template=small").get_data(as_text=True)
    assert 'data-template="small"' in small
    assert "receipt--small" in small
    assert "entry-slip-print-small.css" in small
    assert "طابعة حرارية 58 مم" in small
    assert student["full_name"] in small
    assert "ملاحظة ولي الأمر" not in small
    assert "رقم التعريف" not in small
    assert "أصدرها" not in small

    fallback = client.get(f"{url}?template=bogus").get_data(as_text=True)
    assert 'data-template="full"' in fallback


def test_entry_slip_issue_requires_csrf_header(client, db, make_student, period):
    student = make_student(student_nin="3099000471")
    _insert_absent(db, student["student_nin"], period["id"])
    response = client.post(
        "/api/entry-slip",
        json={"code": student["student_nin"], "period_id": period["id"], "reason": "سبب"},
    )
    assert response.status_code == 403
    assert db.execute("SELECT COUNT(*) FROM entry_slips").fetchone()[0] == 0
