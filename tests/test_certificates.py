from __future__ import annotations

from datetime import date

from attendance_app.services import certificate_service as certificates


def _print(client, nin, query=None):
    return client.get(f"/certificates/print/{nin}", query_string=query or {})


def test_certificate_page_renders_editor_and_picker(client):
    page = client.get("/certificates")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "certificates.js" in html
    assert "قالب الشهادة" in html
    assert "النموذج الرسمي الجزائري" in html
    assert "إصدار شهادة مدرسية" in html


def test_school_certificate_prints_official_algerian_form(client, make_student):
    student = make_student(
        student_nin="5099000101",
        first_name="مريم",
        last_name="عبدلي",
        birth_date="2009-04-17",
        class_group="2AS2",
    )
    printed = _print(client, student["student_nin"])
    assert printed.status_code == 200
    text = printed.get_data(as_text=True)
    assert "شهادة مدرسية" in text
    assert "الجمهورية الجزائرية الديمقراطية الشعبية" in text
    assert "وزارة التربية الوطنية" in text
    assert "مريم" in text and "عبدلي" in text
    assert "17/04/2009" in text
    assert "يتابع(ت) دراسته(ها)" in text
    assert f"/certificates/students/{student['student_nin']}/barcode.svg" in text
    assert f"/certificates/students/{student['student_nin']}/qr.svg" in text


def test_certificate_profile_roundtrip_changes_printed_values(client, csrf, make_student):
    student = make_student(student_nin="5099000102", first_name="أمين", last_name="بن علي")
    saved = client.post(
        f"/api/certificates/students/{student['student_nin']}/profile",
        json={
            "gender": "male",
            "birth_place": "سعيدة",
            "latin_first_name": "AMINE",
            "latin_last_name": "BEN ALI",
            "level": "الثانية ثانوي",
            "section": "علوم تجريبية",
            "registration_no": "123456",
            "exit_date": "",
        },
        headers=csrf,
    )
    assert saved.status_code == 200, saved.get_json()
    text = _print(client, student["student_nin"]).get_data(as_text=True)
    assert "يتابع دراسته" in text
    assert "سعيدة" in text
    assert "BEN ALI AMINE" in text
    assert "الثانية ثانوي" in text
    assert "علوم تجريبية" in text
    assert "123456" in text
    assert "إلى يومنا هذا" in text

    profile = client.get(f"/api/certificates/students/{student['student_nin']}/profile").get_json()
    assert profile["profile"]["birth_place"] == "سعيدة"


def test_certificate_profile_requires_existing_student(client, csrf):
    response = client.post(
        "/api/certificates/students/9999999999/profile",
        json={"gender": "male"},
        headers=csrf,
    )
    assert response.status_code == 400


def test_archived_student_uses_past_tense_and_exit_date(client, csrf, make_student):
    student = make_student(student_nin="5099000103", first_name="ليلى", last_name="حداد")
    client.post(
        f"/api/certificates/students/{student['student_nin']}/profile",
        json={"gender": "female", "exit_date": "2024-06-30"},
        headers=csrf,
    )
    client.put(f"/api/students/{student['student_nin']}", json={"is_active": False}, headers=csrf)
    text = _print(client, student["student_nin"]).get_data(as_text=True)
    assert "تابعت دراستها" in text
    assert "تاريخ الخروج: 30/06/2024" in text


def test_certificate_template_can_switch_to_custom_layout(client, csrf, make_student):
    student = make_student(student_nin="5099000104", first_name="سارة", last_name="مرزوق")
    saved = client.post(
        "/api/certificates/settings",
        json={
            "layout": "custom",
            "direction": "ltr",
            "title": "School Certificate",
            "body": "This certifies that {{student_name}} studies in {{class_group}}.",
            "footer": "Issued at {{school_name}} on {{date_display}}",
            "extra": {
                "republic_header": "Kingdom of Testland",
                "ministry_header": "Ministry of Education",
                "directorate": "Regional Directorate",
                "institution": "{{school_name}}",
                "signature_label": "Principal",
                "show_barcode": False,
                "show_qr": True,
                "show_stamp": False,
            },
        },
        headers=csrf,
    )
    assert saved.status_code == 200, saved.get_json()
    text = _print(client, student["student_nin"]).get_data(as_text=True)
    assert "School Certificate" in text
    assert "This certifies that سارة مرزوق studies in 1AS1." in text
    assert "Kingdom of Testland" in text
    assert "Principal" in text
    assert 'class="cert-frame" dir="ltr"' in text
    assert "barcode.svg" not in text


def test_certificate_assets_are_svg(client, make_student):
    student = make_student(student_nin="5099000105")
    barcode = client.get(f"/api/certificates/students/{student['student_nin']}/barcode.svg")
    assert barcode.status_code == 200
    assert barcode.mimetype == "image/svg+xml"
    assert b"<svg" in barcode.data
    qr = client.get(f"/api/certificates/students/{student['student_nin']}/qr.svg")
    assert qr.status_code == 200
    assert qr.mimetype == "image/svg+xml"
    assert b"<svg" in qr.data


def test_missing_certificate_student_returns_404(client):
    assert _print(client, "9999999999").status_code == 404
    assert client.get("/api/certificates/students/9999999999/barcode.svg").status_code == 404


def test_certificate_values_use_school_settings(client, db, make_student):
    from attendance_app.db import save_settings

    with db:
        save_settings(
            db,
            {
                "school_name": "مدرسة النور",
                "academic_year": "2025/2026",
                "principal_name": "عبد القادر",
            },
        )
    student = make_student(student_nin="5099000106")
    text = _print(client, student["student_nin"]).get_data(as_text=True)
    assert "مدرسة النور" in text
    assert "2025/2026" in text
    assert "عبد القادر" in text


def test_certificate_service_rejects_invalid_profile_values(client, csrf, make_student):
    student = make_student(student_nin="5099000107")
    bad_gender = client.post(
        f"/api/certificates/students/{student['student_nin']}/profile",
        json={"gender": "unknown"},
        headers=csrf,
    )
    assert bad_gender.status_code == 400
    bad_date = client.post(
        f"/api/certificates/students/{student['student_nin']}/profile",
        json={"exit_date": "31/12/2020"},
        headers=csrf,
    )
    assert bad_date.status_code == 400


def test_certificate_template_rejects_unknown_layout(client, csrf):
    response = client.post(
        "/api/certificates/settings",
        json={"layout": "poster", "title": "x"},
        headers=csrf,
    )
    assert response.status_code == 400


def test_certificate_template_rejects_unknown_direction(client, csrf):
    response = client.post(
        "/api/certificates/settings",
        json={"layout": "custom", "direction": "up", "title": "x"},
        headers=csrf,
    )
    assert response.status_code == 400


def test_default_template_layout_is_algerian(app):
    from attendance_app.db import connect

    conn = connect(app.config["DB_PATH"])
    try:
        template = certificates.get_template(conn)
        assert template["layout"] == "algerian"
        assert template["extra"]["show_barcode"] is True
    finally:
        conn.close()


def test_certificate_uses_today_when_issued(client, make_student):
    student = make_student(student_nin="5099000108")
    text = _print(client, student["student_nin"]).get_data(as_text=True)
    today = date.today().strftime("%d/%m/%Y")
    assert today in text
