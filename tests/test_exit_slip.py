from __future__ import annotations

import html as html_lib
import re
from datetime import date

from attendance_app.db import SCHEMA_VERSION


def _issue(client, csrf, code: str, **overrides):
    payload = {
        "code": code,
        "reason": "مرض",
        "guardian_name": "محمد بن علي",
        "guardian_phone": "0550123456",
        "note": "غادر مع الولي",
    }
    payload.update(overrides)
    response = client.post("/api/exit-slip", json=payload, headers=csrf)
    assert response.status_code == 200, response.get_json()
    return response.get_json()["slip"]


def test_exit_slip_schema_and_context(client, db, make_student):
    make_student(student_nin="4099000101")
    assert db.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION
    columns = {
        row["name"] for row in db.execute("PRAGMA table_info(exit_slips)").fetchall()
    }
    assert {
        "slip_code",
        "barcode",
        "student_nin",
        "exit_date",
        "exit_time",
        "exit_reason",
        "guardian_name",
        "guardian_phone",
        "note",
        "issued_by",
        "signature",
        "print_count",
        "returned_at",
        "entry_slip_code",
    } <= columns

    response = client.get("/api/exit-slip/context")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["stats"] == {"issued": 0, "returned": 0, "active": 0}
    assert len(payload["reasons"]) == 5
    assert payload["date"] == date.today().isoformat()
    assert payload["recent"] == []

    page = client.get("/exit-slip")
    assert page.status_code == 200
    assert "ورقة الخروج" in page.get_data(as_text=True)


def test_exit_lookup_issue_print_and_verify(client, csrf, db, make_student):
    student = make_student(student_nin="4099000111")
    nin = student["student_nin"]

    lookup = client.post(
        "/api/exit-slip/lookup", json={"code": nin}, headers=csrf
    )
    assert lookup.status_code == 200
    found = lookup.get_json()
    assert found["action"] == "ready"
    assert found["can_issue"] is True
    assert found["scan_kind"] == "student"
    assert found["student"]["student_nin"] == nin
    assert found["exit_slip"] is None

    slip = _issue(client, csrf, nin)
    day = date.today()
    assert slip["slip_code"].startswith(f"XS-{day:%Y%m%d}-")
    assert re.fullmatch(r"99\d{10}", slip["barcode"])
    assert len(slip["signature"]) == 64
    assert slip["qr_payload"].startswith("SAXS1|")
    assert slip["reason"] == "مرض"
    assert slip["guardian_name"] == "محمد بن علي"
    assert slip["guardian_phone"] == "0550123456"
    assert slip["returned"] is False
    assert slip["print_url"].endswith(slip["slip_code"])
    assert slip["verify_url"].startswith("/exit-slip/verify?code=")

    scanned = client.post(
        "/api/exit-slip/lookup", json={"code": slip["barcode"]}, headers=csrf
    ).get_json()
    assert scanned["scan_kind"] == "exit_receipt"
    assert scanned["exit_slip"]["slip_code"] == slip["slip_code"]
    assert scanned["student"]["student_nin"] == nin

    receipt = client.get(f"/exit-slip/print/{slip['slip_code']}?print=1")
    assert receipt.status_code == 200
    body = receipt.get_data(as_text=True)
    assert slip["slip_code"] in body
    assert student["full_name"] in body
    assert "محمد بن علي" in body
    assert f"/api/exit-slip/{slip['slip_code']}/qr" in body
    assert f"/api/exit-slip/{slip['slip_code']}/barcode" in body
    assert 'data-autoprint="1"' in body
    assert db.execute(
        "SELECT print_count FROM exit_slips WHERE slip_code = ?", (slip["slip_code"],)
    ).fetchone()[0] == 1

    qr = client.get(f"/api/exit-slip/{slip['slip_code']}/qr")
    assert qr.status_code == 200
    assert qr.mimetype == "image/svg+xml"
    assert "<svg" in qr.get_data(as_text=True)
    assert "<?xml" not in qr.get_data(as_text=True)

    barcode = client.get(f"/api/exit-slip/{slip['slip_code']}/barcode")
    assert barcode.status_code == 200
    assert barcode.mimetype == "image/svg+xml"
    assert "<svg" in barcode.get_data(as_text=True)
    assert barcode.headers["Cache-Control"] == "no-store"

    verified = client.get(
        "/api/exit-slip/verify", query_string={"code": slip["qr_payload"]}
    ).get_json()
    assert verified["valid"] is True
    assert verified["checked_signature"] is True
    assert verified["slip"]["slip_code"] == slip["slip_code"]

    plain = client.get(
        "/api/exit-slip/verify", query_string={"code": slip["slip_code"]}
    ).get_json()
    assert plain["valid"] is None
    assert plain["checked_signature"] is False

    parts = slip["qr_payload"].split("|")
    parts[2] = "9999999999"
    tampered = client.get(
        "/api/exit-slip/verify", query_string={"code": "|".join(parts)}
    ).get_json()
    assert tampered["valid"] is False
    assert tampered["slip"]["slip_code"] == slip["slip_code"]

    context = client.get("/api/exit-slip/context").get_json()
    assert context["stats"] == {"issued": 1, "returned": 0, "active": 1}
    assert context["recent"][0]["slip_code"] == slip["slip_code"]


def test_exit_duplicate_active_slip_and_return(client, csrf, db, make_student):
    student = make_student(student_nin="4099000121")
    nin = student["student_nin"]
    slip = _issue(client, csrf, nin)

    again = client.post(
        "/api/exit-slip/lookup", json={"code": nin}, headers=csrf
    ).get_json()
    assert again["action"] == "already_issued"
    assert again["slip"]["slip_code"] == slip["slip_code"]

    duplicate = client.post(
        "/api/exit-slip",
        json={"code": nin, "reason": "سبب آخر"},
        headers=csrf,
    ).get_json()
    assert duplicate["already_issued"] is True
    assert duplicate["slip"]["slip_code"] == slip["slip_code"]
    assert db.execute("SELECT COUNT(*) FROM exit_slips").fetchone()[0] == 1

    returned = client.post(
        "/api/exit-slip/return", json={"code": slip["barcode"]}, headers=csrf
    )
    assert returned.status_code == 200
    assert returned.get_json()["already_returned"] is False
    assert returned.get_json()["slip"]["returned"] is True
    assert returned.get_json()["slip"]["returned_at"]

    repeated = client.post(
        "/api/exit-slip/return", json={"code": slip["slip_code"]}, headers=csrf
    ).get_json()
    assert repeated["already_returned"] is True

    context = client.get("/api/exit-slip/context").get_json()
    assert context["stats"] == {"issued": 1, "returned": 1, "active": 0}

    fresh = _issue(client, csrf, nin, reason="موعد طبي")
    assert fresh["slip_code"] != slip["slip_code"]
    assert fresh["returned"] is False
    assert db.execute("SELECT COUNT(*) FROM exit_slips").fetchone()[0] == 2


def test_exit_receipt_barcode_creates_entry_slip(client, csrf, db, make_student, period):
    student = make_student(student_nin="4099000131")
    nin = student["student_nin"]
    exit_slip = _issue(client, csrf, nin)

    lookup = client.post(
        "/api/entry-slip/lookup",
        json={"code": exit_slip["barcode"], "period_id": period["id"]},
        headers=csrf,
    )
    assert lookup.status_code == 200
    found = lookup.get_json()
    assert found["student"]["student_nin"] == nin
    assert found["exit_slip"]["slip_code"] == exit_slip["slip_code"]
    assert found["scanned_code"] == exit_slip["barcode"]
    assert found["can_issue"] is True

    issued = client.post(
        "/api/entry-slip",
        json={
            "code": exit_slip["barcode"],
            "period_id": period["id"],
            "reason": "عودة من موعد طبي",
        },
        headers=csrf,
    )
    assert issued.status_code == 200, issued.get_json()
    entry = issued.get_json()["slip"]
    closed = issued.get_json()["exit_slip"]
    assert closed["slip_code"] == exit_slip["slip_code"]
    assert closed["returned"] is True
    assert closed["entry_slip_code"] == entry["slip_code"]

    row = db.execute(
        "SELECT returned_at, entry_slip_code FROM exit_slips WHERE slip_code = ?",
        (exit_slip["slip_code"],),
    ).fetchone()
    assert row["returned_at"]
    assert row["entry_slip_code"] == entry["slip_code"]

    context = client.get("/api/exit-slip/context").get_json()
    assert context["stats"] == {"issued": 1, "returned": 1, "active": 0}

    verified = client.get(
        "/api/entry-slip/verify", query_string={"code": entry["qr_payload"]}
    ).get_json()
    assert verified["valid"] is True


def test_exit_print_templates_and_translations(
    client, csrf, db, make_student
):
    student = make_student(student_nin="4099000141")
    slip = _issue(client, csrf, student["student_nin"], note="ملاحظة ولي الأمر")
    url = f"/exit-slip/print/{slip['slip_code']}"

    full = client.get(url).get_data(as_text=True)
    assert 'data-template="full"' in full
    assert "تصريح خروج تلميذ من المؤسسة" in full
    assert "باركود العودة" in full
    assert "تاريخ الميلاد" in full
    assert "ملاحظة ولي الأمر" in full
    assert "receipt-notice" in full
    assert "طابعة حرارية 80 مم" in full

    medium = client.get(f"{url}?template=medium").get_data(as_text=True)
    assert 'data-template="medium"' in medium
    assert "ملاحظة ولي الأمر" in medium
    assert "باركود العودة" in medium
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
    assert "Fiche de sortie" in french
    assert "Code-barres de retour" in french
    assert "Signature numérique vérifiée" in french
    assert "ورقة الخروج" not in french

    client.set_cookie("attendance_lang", "en")
    english = html_lib.unescape(client.get(url).get_data(as_text=True))
    assert "Exit slip" in english
    assert "Return barcode" in english
    assert "Verified digital signature" in english

    verify_page = client.get(f"/exit-slip/verify?code={slip['qr_payload']}")
    assert verify_page.status_code == 200
    assert "Valid and verified signature" in verify_page.get_data(as_text=True)


def test_exit_slip_validations_and_missing_records(client, csrf, db, make_student):
    student = make_student(student_nin="4099000151")
    nin = student["student_nin"]

    missing = client.post(
        "/api/exit-slip/lookup", json={"code": "9999999999"}, headers=csrf
    ).get_json()
    assert missing["action"] == "not_found"
    assert missing["student"] is None

    too_short = client.post(
        "/api/exit-slip", json={"code": nin, "reason": "x"}, headers=csrf
    )
    assert too_short.status_code == 400
    too_long = client.post(
        "/api/exit-slip", json={"code": nin, "reason": "س" * 301}, headers=csrf
    )
    assert too_long.status_code == 400
    unknown = client.post(
        "/api/exit-slip", json={"code": "9999999999", "reason": "مرض"}, headers=csrf
    )
    assert unknown.status_code == 400
    assert db.execute("SELECT COUNT(*) FROM exit_slips").fetchone()[0] == 0

    empty_verify = client.get("/api/exit-slip/verify")
    assert empty_verify.status_code == 400
    unknown_verify = client.get(
        "/api/exit-slip/verify", query_string={"code": "XS-19990101-0001"}
    ).get_json()
    assert unknown_verify["valid"] is False
    assert unknown_verify["slip"] is None

    assert client.get("/exit-slip/print/XS-19990101-0001").status_code == 404
    assert client.get("/api/exit-slip/XS-19990101-0001/qr").status_code == 404
    assert client.get("/api/exit-slip/XS-19990101-0001/barcode").status_code == 404
    assert client.post(
        "/api/exit-slip/return", json={"code": "XS-19990101-0001"}, headers=csrf
    ).status_code == 400


def test_exit_slip_issue_requires_csrf_header(client, db, make_student):
    student = make_student(student_nin="4099000161")
    response = client.post(
        "/api/exit-slip", json={"code": student["student_nin"], "reason": "مرض"}
    )
    assert response.status_code == 403
    assert db.execute("SELECT COUNT(*) FROM exit_slips").fetchone()[0] == 0


def test_exit_slip_page_translations(client):
    client.set_cookie("attendance_lang", "en")
    page = client.get("/exit-slip")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "Exit slip" in body
    assert "Scan the student card to issue an exit slip" in body
    assert "ورقة الخروج" not in body
    assert 'dir="ltr"' in body

    verify = client.get("/exit-slip/verify").get_data(as_text=True)
    assert "Exit slip verification" in verify
    assert "QR code or exit slip number" in verify

    client.set_cookie("attendance_lang", "fr")
    page = client.get("/exit-slip")
    body = page.get_data(as_text=True)
    assert "Fiche de sortie" in body
    assert "Scannez la carte de l'élève pour émettre une fiche de sortie" in body
    assert "ورقة الخروج" not in body
    assert 'dir="ltr"' in body
