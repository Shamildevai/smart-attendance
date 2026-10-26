from __future__ import annotations

import json
import re
from pathlib import Path

from attendance_app import config, i18n

LOCALES_DIR = Path(__file__).resolve().parents[1] / "attendance_app" / "locales"
ARABIC_INDIC_DIGITS = re.compile(r"[\u0660-\u0669\u06F0-\u06F9]")


def test_language_switch_sets_cookie_and_local_redirect(client):
    response = client.get("/language/en?next=/reports")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/reports")
    cookie = response.headers["Set-Cookie"]
    assert f"{i18n.LANG_COOKIE}=en" in cookie


def test_language_switch_rejects_external_redirect(client):
    response = client.get("/language/fr?next=https://evil.example/steal")
    assert response.status_code == 302
    assert "evil.example" not in response.headers["Location"]


def test_unknown_language_falls_back_to_arabic(client):
    response = client.get("/language/de?next=/")
    assert f"{i18n.LANG_COOKIE}=ar" in response.headers["Set-Cookie"]


def test_english_dashboard_is_ltr_and_translated(client):
    client.set_cookie(i18n.LANG_COOKIE, "en")
    html = client.get("/").get_data(as_text=True)
    assert re.search(r'<html lang="en" dir="ltr"', html)
    assert "Scan Gate" in html
    assert "Attendance Log" in html
    assert "بوابة المسح" not in html


def test_french_dashboard_is_ltr_and_translated(client):
    client.set_cookie(i18n.LANG_COOKIE, "fr")
    html = client.get("/").get_data(as_text=True)
    assert re.search(r'<html lang="fr" dir="ltr"', html)
    assert "Portail de scan" in html
    assert "Journal de présence" in html


def test_arabic_remains_the_rtl_default(client):
    html = client.get("/").get_data(as_text=True)
    assert re.search(r'<html lang="ar" dir="rtl"', html)
    assert "بوابة المسح" in html


def test_report_json_labels_are_localized(client):
    client.set_cookie(i18n.LANG_COOKIE, "en")
    payload = client.get("/api/reports/daily?date=2024-01-02").get_json()
    assert payload["title"] == "Daily attendance report"
    assert payload["period_label"] == "All periods"
    assert payload["class_label"] == "All classes"

    client.set_cookie(i18n.LANG_COOKIE, "fr")
    monthly = client.get("/api/reports/monthly?month=2024-01").get_json()
    assert monthly["title"] == "Rapport mensuel des absences"
    assert monthly["period_label"] == "Toutes les périodes"
    assert re.match(r"^[a-zéû]+ 2024$", monthly["month_display"])


def test_period_names_are_not_rewritten_in_api_payloads(client, period):
    client.set_cookie(i18n.LANG_COOKIE, "en")
    periods = client.get("/api/periods").get_json()["periods"]
    assert any(item["period_name"] == period["period_name"] for item in periods)


def test_print_templates_render_localized_labels(client):
    client.set_cookie(i18n.LANG_COOKIE, "en")
    html = client.get("/reports/print?date=2024-01-02").get_data(as_text=True)
    assert re.search(r'<html lang="en" dir="ltr"', html)
    assert "A4 paper" in html
    assert "ورق A4" not in html


def test_i18n_script_exposes_catalog(client):
    client.set_cookie(i18n.LANG_COOKIE, "en")
    response = client.get("/assets/i18n.js")
    assert response.status_code == 200
    assert response.mimetype == "application/javascript"
    body = response.get_data(as_text=True)
    assert body.startswith("window.AppI18n=")
    data = json.loads(body[len("window.AppI18n=") :].rstrip(";"))
    assert data["lang"] == "en"
    assert data["dir"] == "ltr"
    assert data["strings"]["بوابة المسح"] == "Scan Gate"
    assert data["patterns"]


def test_bundled_font_is_served(client):
    response = client.get("/static/fonts/cairo-arabic.woff2")
    assert response.status_code == 200
    assert response.data[:4] == b"wOF2"


def test_catalogs_have_no_dead_placeholder_entries():
    for code in ("en", "fr"):
        data = json.loads((LOCALES_DIR / f"{code}.json").read_text(encoding="utf-8"))
        keys = [key for key in data["strings"] if re.search(r"\{\d+\}", key)]
        assert not keys, f"{code} strings with placeholders: {keys[:3]}"
        patterns = [item for item in data["patterns"] if not re.search(r"\{\d+\}", item["ar"])]
        assert not patterns, f"{code} patterns without placeholders"


def test_translation_keeps_trailing_punctuation():
    assert i18n.translate("الفترة مفتوحة ولم تُغلق بعد.", "en") == (
        "The period is open and not yet closed."
    )
    assert i18n.translate("الفترة مفتوحة ولم تُغلق بعد.", "fr").endswith("clôturée.")


def test_catalogs_use_western_digits_only():
    for code in ("en", "fr"):
        data = json.loads((LOCALES_DIR / f"{code}.json").read_text(encoding="utf-8"))
        for key, value in data["strings"].items():
            assert not ARABIC_INDIC_DIGITS.search(key), f"{code} key: {key}"
            assert not ARABIC_INDIC_DIGITS.search(value), f"{code} value: {value}"
        for pattern in data["patterns"]:
            assert not ARABIC_INDIC_DIGITS.search(pattern["ar"]), (
                f"{code} pattern source: {pattern['ar']}"
            )
            assert not ARABIC_INDIC_DIGITS.search(pattern["tr"]), (
                f"{code} pattern translation: {pattern['tr']}"
            )


def test_western_digits_helper_handles_none_and_mixed_text():
    from attendance_app.utils import western_digits

    assert western_digits(None) == ""
    assert western_digits("صفحة ١٢ من ۲۰") == "صفحة 12 من 20"


def test_rendered_pages_use_western_digits_only(client):
    pages = (
        "/", "/messages", "/settings", "/about", "/help", "/attendance", "/reports",
        "/students", "/users", "/gate", "/import", "/print", "/print/sheet",
        "/reports/print", "/entry-slip", "/entry-slip/verify",
    )
    for lang in ("ar", "en", "fr"):
        client.set_cookie(i18n.LANG_COOKIE, lang)
        for path in pages:
            response = client.get(path)
            assert response.status_code == 200, (lang, path)
            html = response.get_data(as_text=True)
            found = ARABIC_INDIC_DIGITS.search(html)
            assert not found, f"{lang} {path} rendered {found.group()!r}"


def test_json_responses_use_western_digits_only(client):
    endpoints = (
        "/api/dashboard", "/api/students", "/api/attendance/list", "/api/periods",
        "/api/settings", "/api/reports/daily", "/api/messages/campaigns", "/api/users",
        "/api/entry-slip/context", "/api/gate/context",
    )
    for lang in ("ar", "en", "fr"):
        client.set_cookie(i18n.LANG_COOKIE, lang)
        for path in endpoints:
            response = client.get(path)
            assert response.status_code == 200, (lang, path)
            body = response.get_data(as_text=True)
            found = ARABIC_INDIC_DIGITS.search(body)
            assert not found, f"{lang} {path} rendered {found.group()!r}"


def test_response_filter_converts_arabic_digits(app, client):
    def html_probe():
        return "<p>العدد ١٢٣٤٥٦٧٨٩٠ والفارسي ۱۲۳۴۵۶۷۸۹۰</p>"

    def json_probe():
        return {"note": "العدد ١٢٣", "value": 12}

    def binary_probe():
        return b"\x00\xd9\xa7\xd9\xa2\xff", 200, {"Content-Type": "application/octet-stream"}

    app.add_url_rule("/__digits_html", "digits_html", html_probe)
    app.add_url_rule("/__digits_json", "digits_json", json_probe)
    app.add_url_rule("/__digits_binary", "digits_binary", binary_probe)

    html = client.get("/__digits_html").get_data(as_text=True)
    assert html.count("1234567890") == 2
    assert not ARABIC_INDIC_DIGITS.search(html)

    payload = client.get("/__digits_json").get_json()
    assert payload == {"note": "العدد 123", "value": 12}

    assert client.get("/__digits_binary").data == b"\x00\xd9\xa7\xd9\xa2\xff"


def test_version_and_gateway_links_render_with_western_digits(client):
    html = client.get("/about").get_data(as_text=True)
    assert config.APP_VERSION in html
    assert config.GATEWAY_APP_VERSION in html
    assert config.GATEWAY_APP_DOWNLOAD_URL in html


def test_pairing_and_cloud_ui_strings_are_translated(client):
    assert i18n.translate("اقتران الهاتف برمز", "en") == "Pair phone with code"
    assert i18n.translate("الاقتران برمز 4 أرقام", "fr").startswith("Appairer")
    approval = i18n.translate(
        "أوافق على محاكاة إرسال رسائل واتساب عبر Meta Cloud API إلى المستلمين "
        "في النطاق المحدد (العدد: 5) في وضع الاختبار، دون استخدام الشبكة.",
        "en",
    )
    assert approval.startswith("I agree to simulate") and "(count: 5)" in approval
    assert i18n.translate("Meta Cloud API مهيأ — القالب absence_notice", "en") == (
        "Meta Cloud API ready — template absence_notice"
    )

    client.set_cookie(i18n.LANG_COOKIE, "en")
    settings_html = client.get("/settings").get_data(as_text=True)
    assert "Pair with a 4-digit code" in settings_html
    assert "Permanent access token" in settings_html
    messages_html = client.get("/messages").get_data(as_text=True)
    assert "Pair phone with code" in messages_html
    assert "Manual or automatic sending, per settings" in messages_html
