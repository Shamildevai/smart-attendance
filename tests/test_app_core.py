from __future__ import annotations

import pytest


def test_health_and_security_headers(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["app"] == "StudentAttendance"

    page = client.get("/")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert '<html lang="ar" dir="rtl"' in html
    csp = page.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert page.headers["X-Frame-Options"] == "DENY"
    assert page.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize(
    "path",
    ["/", "/gate", "/students", "/import", "/attendance", "/reports", "/print", "/settings", "/help", "/about"],
)
def test_main_pages_render(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert "text/html" in response.content_type


def test_help_page_links_nav_and_infographic(client):
    response = client.get("/help")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'href="/help"' in html
    assert "دليل الاستخدام" in html
    assert "خريطة سير العمل" in html
    assert "/static/img/help-workflow-ar.svg" in html
    assert 'id="i-help"' in html

    svg = client.get("/static/img/help-workflow-ar.svg")
    assert svg.status_code == 200
    assert "image/svg+xml" in svg.content_type
    body = svg.get_data(as_text=True)
    assert "ولي الأمر" in body
    assert "المؤسسة" in body


def test_scan_pages_load_offline_camera_decoder(client):
    for path in ("/gate", "/entry-slip", "/exit-slip"):
        response = client.get(path)
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "/static/vendor/zxing.min.js" in html
        assert "/static/js/camera-scan.js" in html

    bundle = client.get("/static/vendor/zxing.min.js")
    assert bundle.status_code == 200
    assert "javascript" in bundle.content_type
    body = bundle.get_data(as_text=True)
    assert "ZXing" in body
    assert "https://" not in body
    assert "decodeFromVideoElement" in body or "BrowserMultiFormatReader" in body


def test_about_page_attribution_and_dedication(client):
    response = client.get("/about")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "د. بوزويرة عزيز" in html
    assert "Dr. Bouzouira Aziz" in html
    assert "shamildev" in html
    assert "سعيدة، الجزائر" in html
    assert "Saida, Algeria" in html
    assert "صدقة عن والدي المرحوم بوزويرة قادة" in html


def test_api_404_is_json(client):
    response = client.get("/api/unknown-endpoint")
    assert response.status_code == 404
    payload = response.get_json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "not_found"


def test_csrf_header_is_required_for_mutations(client, csrf):
    payload = {"student_nin": "1099000001", "first_name": "أمين"}
    blocked = client.post("/api/students", json=payload)
    assert blocked.status_code == 403
    assert blocked.get_json()["error"]["code"] == "csrf"

    allowed = client.post("/api/students", json={"first_name": "أمين"}, headers=csrf)
    assert allowed.status_code == 400


def test_student_crud_validation_and_search(client, csrf, make_student):
    student = make_student(
        student_nin="2099000101",
        first_name="ياسين",
        last_name="بوزيد",
        class_group="2AS1",
        parent_phone="0551112233",
    )
    assert student["student_nin"] == "2099000101"
    assert student["is_active"] is True
    assert student["parent_phone"] == "0551112233"

    duplicate = client.post(
        "/api/students",
        json={"student_nin": "2099000101", "first_name": "مكرر", "last_name": "اختبار"},
        headers=csrf,
    )
    assert duplicate.status_code == 409

    invalid_nin = client.post(
        "/api/students",
        json={"student_nin": "A!B", "first_name": "خطأ", "last_name": "رقم"},
        headers=csrf,
    )
    assert invalid_nin.status_code == 400

    invalid_phone = client.post(
        "/api/students",
        json={"student_nin": "2099000102", "first_name": "خطأ", "last_name": "هاتف", "parent_phone": "12"},
        headers=csrf,
    )
    assert invalid_phone.status_code == 400

    update = client.put(
        "/api/students/2099000101",
        json={"first_name": "ياسين", "last_name": "بوزيدي", "class_group": "2AS2", "is_active": False},
        headers=csrf,
    )
    assert update.status_code == 200
    updated = update.get_json()["student"]
    assert updated["last_name"] == "بوزيدي"
    assert updated["class_group"] == "2AS2"
    assert updated["is_active"] is False

    search = client.get("/api/students?q=بوزيدي")
    body = search.get_json()
    assert body["ok"] is True
    assert body["total"] == 1
    assert body["items"][0]["student_nin"] == "2099000101"

    archived = client.get("/api/students?active=0").get_json()
    assert archived["total"] == 1
    assert archived["counts"] == {"all": 1, "active": 0, "inactive": 1}


def test_settings_validation_and_roundtrip(client, csrf):
    initial = client.get("/api/settings").get_json()["settings"]
    assert initial["sms_test_mode"] is True
    assert initial["school_name"]

    updated = client.put(
        "/api/settings",
        json={
            "school_name": "متوسطة الأمير عبد القادر",
            "academic_year": "2026/2025",
            "sms_test_mode": True,
            "school_weekdays": [0, 1, 2, 3, 4, 6],
            "anti_passback_minutes": 7,
        },
        headers=csrf,
    )
    assert updated.status_code == 200
    settings = updated.get_json()["settings"]
    assert settings["school_name"] == "متوسطة الأمير عبد القادر"
    assert settings["academic_year"] == "2026/2025"
    assert settings["sms_test_mode"] is True
    assert settings["school_weekdays"] == [0, 1, 2, 3, 4, 6]
    assert settings["anti_passback_minutes"] == 7

    bad_gateway = client.put("/api/settings", json={"sms_gateway": "carrier-pigeon"}, headers=csrf)
    assert bad_gateway.status_code == 400

    bad_json = client.put("/api/settings", json={"sms_headers": "{not-json"}, headers=csrf)
    assert bad_json.status_code == 400

    unsafe_test_mode = client.put(
        "/api/settings",
        json={"sms_test_mode": False, "sms_api_url": ""},
        headers=csrf,
    )
    assert unsafe_test_mode.status_code == 400

    no_name = client.put("/api/settings", json={"school_name": "  "}, headers=csrf)
    assert no_name.status_code == 400


def test_color_theme_setting(client, csrf):
    initial = client.get("/api/settings").get_json()["settings"]
    assert initial["color_theme"] == "emerald"

    updated = client.put("/api/settings", json={"color_theme": "blue"}, headers=csrf)
    assert updated.status_code == 200
    assert updated.get_json()["settings"]["color_theme"] == "blue"

    page = client.get("/settings").get_data(as_text=True)
    assert 'data-color="blue"' in page
    assert page.count('class="theme-swatch') == 8

    invalid = client.put("/api/settings", json={"color_theme": "turquoise"}, headers=csrf)
    assert invalid.status_code == 400

    still_blue = client.get("/api/settings").get_json()["settings"]["color_theme"]
    assert still_blue == "blue"
