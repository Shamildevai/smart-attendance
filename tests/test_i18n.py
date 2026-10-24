from __future__ import annotations

import json
import re
from pathlib import Path

from attendance_app import i18n

LOCALES_DIR = Path(__file__).resolve().parents[1] / "attendance_app" / "locales"


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
