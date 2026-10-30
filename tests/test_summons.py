from __future__ import annotations

import html as html_lib
import re
from datetime import date, timedelta

from attendance_app.db import SCHEMA_VERSION


def _issue(client, csrf, code: str, **overrides):
    payload = {
        "code": code,
        "reason": "غياب متكرر",
        "guardian_name": "محمد بن علي",
        "guardian_phone": "0550123456",
        "note": "يرجى الحضور مع الإيصال",
    }
    payload.update(overrides)
    response = client.post("/api/summons", json=payload, headers=csrf)
    assert response.status_code == 200, response.get_json()
    return response.get_json()["summon"]


def test_summons_schema_context_and_page(client, db, make_student):
    make_student(student_nin="4099000201")
    assert db.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION
    columns = {
        row["name"] for row in db.execute("PRAGMA table_info(summons)").fetchall()
    }
    assert {
        "summon_code",
        "barcode",
        "student_nin",
        "summon_date",
        "due_date",
        "due_time",
        "reason",
        "note",
        "guardian_name",
        "guardian_phone",
        "status",
        "attended_at",
        "issued_by",
        "signature",
        "print_count",
    } <= columns

    response = client.get("/api/summons/context")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["stats"] == {
        "issued": 0,
        "open": 0,
        "attended_today": 0,
        "overdue": 0,
    }
    assert len(payload["reasons"]) == 7
    assert payload["date"] == date.today().isoformat()
    assert payload["recent"] == []
    assert payload["open_items"] == []
    assert payload["classes"]

    page = client.get("/summons")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "الاستدعاء" in body
    assert 'id="summons-form"' in body
    assert "غياب متكرر" in body


def test_summons_lookup_issue_print_and_verify(client, csrf, db, make_student):
    student = make_student(student_nin="4099000211")
    nin = student["student_nin"]
    due = (date.today() + timedelta(days=1)).isoformat()

    lookup = client.post("/api/summons/lookup", json={"code": nin}, headers=csrf)
    assert lookup.status_code == 200
    found = lookup.get_json()
    assert found["action"] == "ready"
    assert found["can_issue"] is True
    assert found["scan_kind"] == "student"
    assert found["student"]["student_nin"] == nin
    assert found["summon"] is None

    summon = _issue(client, csrf, nin, due_date=due, due_time="08:30")
    day = date.today()
    assert summon["summon_code"].startswith(f"SM-{day:%Y%m%d}-")
    assert re.fullmatch(r"97\d{10}", summon["barcode"])
    assert len(summon["signature"]) == 64
    assert summon["qr_payload"].startswith("SAMS1|")
    assert summon["reason"] == "غياب متكرر"
    assert summon["guardian_name"] == "محمد بن علي"
    assert summon["guardian_phone"] == "0550123456"
    assert summon["due_date"] == due
    assert summon["due_time"] == "08:30"
    assert summon["status"] == "OPEN"
    assert summon["status_label"] == "بانتظار الولي"
    assert summon["print_url"].endswith(summon["summon_code"])
    assert summon["verify_url"].startswith("/summons/verify?code=")

    scanned = client.post(
        "/api/summons/lookup", json={"code": summon["barcode"]}, headers=csrf
    ).get_json()
    assert scanned["scan_kind"] == "summon_receipt"
    assert scanned["summon"]["summon_code"] == summon["summon_code"]
    assert scanned["student"]["student_nin"] == nin

    receipt = client.get(f"/summons/print/{summon['summon_code']}?print=1")
    assert receipt.status_code == 200
    body = receipt.get_data(as_text=True)
    assert summon["summon_code"] in body
    assert student["full_name"] in body
    assert "محمد بن علي" in body
    assert "غياب متكرر" in body
    assert f"/api/summons/{summon['summon_code']}/qr" in body
    assert f"/api/summons/{summon['summon_code']}/barcode" in body
    assert 'data-autoprint="1"' in body
    assert db.execute(
        "SELECT print_count FROM summons WHERE summon_code = ?",
        (summon["summon_code"],),
    ).fetchone()[0] == 1

    qr = client.get(f"/api/summons/{summon['summon_code']}/qr")
    assert qr.status_code == 200
    assert qr.mimetype == "image/svg+xml"
    assert "<svg" in qr.get_data(as_text=True)
    assert "<?xml" not in qr.get_data(as_text=True)
    assert qr.headers["Cache-Control"] == "no-store"

    barcode = client.get(f"/api/summons/{summon['summon_code']}/barcode")
    assert barcode.status_code == 200
    assert barcode.mimetype == "image/svg+xml"
    assert "<svg" in barcode.get_data(as_text=True)
    assert barcode.headers["Cache-Control"] == "no-store"

    verified = client.get(
        "/api/summons/verify", query_string={"code": summon["qr_payload"]}
    ).get_json()
    assert verified["valid"] is True
    assert verified["checked_signature"] is True
    assert verified["summon"]["summon_code"] == summon["summon_code"]

    plain = client.get(
        "/api/summons/verify", query_string={"code": summon["summon_code"]}
    ).get_json()
    assert plain["valid"] is None
    assert plain["checked_signature"] is False

    parts = summon["qr_payload"].split("|")
    parts[2] = "9999999999"
    tampered = client.get(
        "/api/summons/verify", query_string={"code": "|".join(parts)}
    ).get_json()
    assert tampered["valid"] is False
    assert tampered["summon"]["summon_code"] == summon["summon_code"]

    context = client.get("/api/summons/context").get_json()
    assert context["stats"] == {
        "issued": 1,
        "open": 1,
        "attended_today": 0,
        "overdue": 0,
    }
    assert context["recent"][0]["summon_code"] == summon["summon_code"]
    assert context["open_items"][0]["summon_code"] == summon["summon_code"]

    listing = client.get("/api/summons", query_string={"status": "open"}).get_json()
    assert [item["summon_code"] for item in listing["items"]] == [
        summon["summon_code"]
    ]


def test_summons_attendance_and_cancellation(client, csrf, db, make_student):
    student = make_student(student_nin="4099000221")
    nin = student["student_nin"]
    first = _issue(client, csrf, nin)
    second = _issue(client, csrf, nin, reason="نتائج دراسية")
    assert first["summon_code"] != second["summon_code"]
    assert db.execute("SELECT COUNT(*) FROM summons").fetchone()[0] == 2

    attended = client.post(
        f"/api/summons/{first['id']}/attend", headers=csrf
    )
    assert attended.status_code == 200
    assert attended.get_json()["already"] is False
    assert attended.get_json()["summon"]["status"] == "ATTENDED"
    assert attended.get_json()["summon"]["attended_at"]

    repeated = client.post(
        f"/api/summons/{first['id']}/attend", headers=csrf
    ).get_json()
    assert repeated["already"] is True

    blocked = client.post(f"/api/summons/{first['id']}/cancel", headers=csrf)
    assert blocked.status_code == 400

    cancelled = client.post(f"/api/summons/{second['id']}/cancel", headers=csrf)
    assert cancelled.status_code == 200
    assert cancelled.get_json()["summon"]["status"] == "CANCELLED"

    context = client.get("/api/summons/context").get_json()
    assert context["stats"] == {
        "issued": 2,
        "open": 0,
        "attended_today": 1,
        "overdue": 0,
    }


def test_summons_validations_and_missing_records(client, csrf, db, make_student):
    student = make_student(student_nin="4099000231")
    nin = student["student_nin"]

    missing = client.post(
        "/api/summons/lookup", json={"code": "9999999999"}, headers=csrf
    ).get_json()
    assert missing["action"] == "not_found"
    assert missing["student"] is None

    short = client.post(
        "/api/summons", json={"code": nin, "reason": "x"}, headers=csrf
    )
    assert short.status_code == 400
    long_reason = client.post(
        "/api/summons", json={"code": nin, "reason": "س" * 301}, headers=csrf
    )
    assert long_reason.status_code == 400
    past_due = client.post(
        "/api/summons",
        json={
            "code": nin,
            "reason": "مشكل سلوكي",
            "due_date": (date.today() - timedelta(days=1)).isoformat(),
        },
        headers=csrf,
    )
    assert past_due.status_code == 400
    time_without_date = client.post(
        "/api/summons",
        json={"code": nin, "reason": "مشكل سلوكي", "due_time": "09:00"},
        headers=csrf,
    )
    assert time_without_date.status_code == 400
    unknown = client.post(
        "/api/summons", json={"code": "9999999999", "reason": "مشكل سلوكي"}, headers=csrf
    )
    assert unknown.status_code == 400
    assert db.execute("SELECT COUNT(*) FROM summons").fetchone()[0] == 0

    empty_verify = client.get("/api/summons/verify")
    assert empty_verify.status_code == 400
    unknown_verify = client.get(
        "/api/summons/verify", query_string={"code": "SM-19990101-0001"}
    ).get_json()
    assert unknown_verify["valid"] is False
    assert unknown_verify["summon"] is None

    damaged = client.get(
        "/api/summons/verify", query_string={"code": "SAMS1|SM-20260101-0001"}
    ).get_json()
    assert damaged["valid"] is False
    assert damaged["checked_signature"] is True

    assert client.get("/summons/print/SM-19990101-0001").status_code == 404
    assert client.get("/api/summons/SM-19990101-0001/qr").status_code == 404
    assert client.get("/api/summons/SM-19990101-0001/barcode").status_code == 404
    assert client.post("/api/summons/9999/attend", headers=csrf).status_code == 400

    no_csrf = client.post("/api/summons", json={"code": nin, "reason": "غياب متكرر"})
    assert no_csrf.status_code == 403
    assert db.execute("SELECT COUNT(*) FROM summons").fetchone()[0] == 0


def test_summons_receipt_templates_and_translations(client, csrf, make_student):
    student = make_student(student_nin="4099000241")
    summon = _issue(client, csrf, student["student_nin"], note="ملاحظة ولي الأمر")
    url = f"/summons/print/{summon['summon_code']}"

    full = client.get(url).get_data(as_text=True)
    assert 'data-template="full"' in full
    assert "استدعاء ولي الأمر" in full
    assert "باركود الاستدعاء" in full
    assert "تاريخ الميلاد" in full
    assert "ملاحظة ولي الأمر" in full
    assert "receipt-notice" in full
    assert "طابعة حرارية 80 مم" in full

    medium = client.get(f"{url}?template=medium").get_data(as_text=True)
    assert 'data-template="medium"' in medium
    assert "ملاحظة ولي الأمر" in medium
    assert "تاريخ الميلاد" not in medium
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

    fallback = client.get(f"{url}?template=bogus").get_data(as_text=True)
    assert 'data-template="full"' in fallback

    client.set_cookie("attendance_lang", "fr")
    french = html_lib.unescape(client.get(url).get_data(as_text=True))
    assert "Convocation du tuteur" in french
    assert "Numéro de convocation" in french
    assert "Présence requise" in french
    assert "استدعاء ولي الأمر" not in french

    client.set_cookie("attendance_lang", "en")
    english = html_lib.unescape(client.get(url).get_data(as_text=True))
    assert "Guardian summons" in english
    assert "Summons number" in english
    assert "Required attendance" in english
    assert "استدعاء ولي الأمر" not in english

    page = client.get("/summons").get_data(as_text=True)
    assert "New summons" in page
    assert "Summons summary" in page
    assert "Summon a guardian and print a signed official receipt" in page
    assert "الاستدعاء" not in page
    assert 'dir="ltr"' in page

    verify = client.get(
        "/summons/verify", query_string={"code": summon["qr_payload"]}
    )
    assert verify.status_code == 200
    assert "Valid and verified signature" in verify.get_data(as_text=True)
