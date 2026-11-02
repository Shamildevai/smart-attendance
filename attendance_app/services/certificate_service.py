"""School certificates: the official Algerian form plus editable templates.

The printed form follows the wording of the ministry certificate: republic and
ministry headers, the director's attestation, the pupil's identity, the school
year, the class and the registration number. Every label is stored in
``certificate_templates`` so a school in another country can rewrite the whole
document, or switch to a free-form body of its own.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from io import BytesIO
from typing import Any

import segno
from barcode import Code128
from barcode.writer import SVGWriter

from ..db import get_settings
from ..utils import ValidationError, full_name, short_date, today_iso, western_digits

SCHEMA = """
CREATE TABLE IF NOT EXISTS certificate_templates (
    kind TEXT PRIMARY KEY,
    data TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS certificate_profiles (
    student_nin TEXT PRIMARY KEY,
    gender TEXT NOT NULL DEFAULT '' CHECK (gender IN ('', 'male', 'female')),
    birth_place TEXT NOT NULL DEFAULT '',
    latin_first_name TEXT NOT NULL DEFAULT '',
    latin_last_name TEXT NOT NULL DEFAULT '',
    level TEXT NOT NULL DEFAULT '',
    section TEXT NOT NULL DEFAULT '',
    registration_no TEXT NOT NULL DEFAULT '',
    exit_date TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
"""

KIND = "school"

DEFAULT_TEMPLATE: dict[str, Any] = {
    "layout": "algerian",
    "direction": "rtl",
    "title": "شهادة مدرسية",
    "body": (
        "يشهد(ت) مدير(ة) المؤسسة أن التلميذ(ة): {{student_name}}\n"
        "المولود(ة) بتاريخ: {{birth_date}}\n"
        "مسجل(ة) بهذه المؤسسة خلال السنة الدراسية {{academic_year}} في القسم: "
        "{{class_group}}\n\n"
        "وقد سُلِّمت له/لها هذه الشهادة بناءً على طلبه/طلبها لتقديمها عند الحاجة."
    ),
    "footer": "حُرِّرت بـ{{school_name}} في {{date_display}}",
    "extra": {
        "republic_header": "الجمهورية الجزائرية الديمقراطية الشعبية",
        "ministry_header": "وزارة التربية الوطنية",
        "directorate": "مديرية التربية لولاية {{state}}",
        "institution": "{{school_name}}",
        "institution_type": "المدرسة الابتدائية",
        "cycle_word": "ابتدائي",
        "state": "",
        "signature_label": "مدير(ة) {{institution_label}}",
        "cert_line": "يشهد السيد(ة) مدير(ة) {{institution_label}} أن {{student_label}}:",
        "previous_name_label": "الكتابة السابقة للاسم واللقب",
        "issued_line": "حُرر بـ {{state}} في: {{date_display}}",
        "registration_current": "إلى يومنا هذا",
        "registration_exit": "تاريخ الخروج: {{exit_date}}",
        "show_barcode": True,
        "show_qr": True,
        "show_stamp": True,
    },
}

TEXT_LIMITS = {
    "title": 200,
    "body": 12000,
    "footer": 3000,
    "republic_header": 160,
    "ministry_header": 160,
    "directorate": 160,
    "institution": 160,
    "institution_type": 80,
    "cycle_word": 40,
    "state": 80,
    "signature_label": 160,
    "cert_line": 300,
    "previous_name_label": 160,
    "issued_line": 200,
    "registration_current": 120,
    "registration_exit": 200,
}

PROFILE_LIMITS = {
    "birth_place": 120,
    "latin_first_name": 80,
    "latin_last_name": 80,
    "level": 60,
    "section": 80,
    "registration_no": 40,
    "exit_date": 10,
}


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _clean_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "on", "yes")


def _merge(template: dict[str, Any], stored: dict[str, Any]) -> dict[str, Any]:
    merged = {
        "layout": stored.get("layout") or template["layout"],
        "direction": stored.get("direction") or template["direction"],
        "title": stored.get("title", template["title"]),
        "body": stored.get("body", template["body"]),
        "footer": stored.get("footer", template["footer"]),
        "extra": {**template["extra"], **(stored.get("extra") or {})},
    }
    if merged["layout"] == "custom" and not merged["body"].strip():
        merged["body"] = template["body"]
    return merged


def get_template(conn: sqlite3.Connection) -> dict[str, Any]:
    ensure_schema(conn)
    row = conn.execute(
        "SELECT data FROM certificate_templates WHERE kind = ?", (KIND,)
    ).fetchone()
    stored: dict[str, Any] = {}
    if row:
        try:
            parsed = json.loads(row["data"])
            if isinstance(parsed, dict):
                stored = parsed
        except (TypeError, ValueError):
            stored = {}
    template = _merge(DEFAULT_TEMPLATE, stored)
    template["customized"] = bool(row)
    return template


def save_template(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    layout = str(payload.get("layout") or "algerian").strip().lower()
    if layout not in ("algerian", "custom"):
        raise ValidationError("نمط الشهادة غير معروف.")
    direction = str(payload.get("direction") or "rtl").strip().lower()
    if direction not in ("rtl", "ltr"):
        raise ValidationError("اتجاه الشهادة غير معروف.")
    extra_in = payload.get("extra") or {}
    if not isinstance(extra_in, dict):
        raise ValidationError("إعدادات الشهادة غير صالحة.")
    extra = {
        key: _clean_text(extra_in.get(key, DEFAULT_TEMPLATE["extra"].get(key, "")), TEXT_LIMITS[key])
        for key in TEXT_LIMITS
        if key in DEFAULT_TEMPLATE["extra"]
    }
    for key in ("show_barcode", "show_qr", "show_stamp"):
        extra[key] = _as_bool(extra_in.get(key), DEFAULT_TEMPLATE["extra"][key])
    data = {
        "layout": layout,
        "direction": direction,
        "title": _clean_text(payload.get("title"), TEXT_LIMITS["title"]),
        "body": _clean_text(payload.get("body"), TEXT_LIMITS["body"]),
        "footer": _clean_text(payload.get("footer"), TEXT_LIMITS["footer"]),
        "extra": extra,
    }
    if not data["title"]:
        raise ValidationError("عنوان الشهادة مطلوب.")
    ensure_schema(conn)
    with conn:
        conn.execute(
            """INSERT INTO certificate_templates(kind, data, updated_at)
               VALUES(?, ?, datetime('now', 'localtime'))
               ON CONFLICT(kind) DO UPDATE SET
                   data = excluded.data,
                   updated_at = excluded.updated_at""",
            (KIND, json.dumps(data, ensure_ascii=False)),
        )
    return get_template(conn)


def get_profile(conn: sqlite3.Connection, student_nin: str) -> dict[str, Any]:
    ensure_schema(conn)
    row = conn.execute(
        "SELECT * FROM certificate_profiles WHERE student_nin = ?", (student_nin,)
    ).fetchone()
    base = {
        "gender": "",
        "birth_place": "",
        "latin_first_name": "",
        "latin_last_name": "",
        "level": "",
        "section": "",
        "registration_no": "",
        "exit_date": "",
    }
    if not row:
        return base
    for key in base:
        base[key] = row[key] or ""
    return base


def save_profile(conn: sqlite3.Connection, student_nin: str, payload: dict[str, Any]) -> dict[str, Any]:
    exists = conn.execute(
        "SELECT 1 FROM students WHERE student_nin = ?", (student_nin,)
    ).fetchone()
    if not exists:
        raise ValidationError("التلميذ غير موجود.")
    gender = str(payload.get("gender") or "").strip().lower()
    if gender not in ("", "male", "female"):
        raise ValidationError("الجنس غير صالح.")
    clean = {key: _clean_text(payload.get(key), limit) for key, limit in PROFILE_LIMITS.items()}
    exit_date = clean.pop("exit_date")
    if exit_date:
        try:
            date.fromisoformat(exit_date)
        except ValueError as exc:
            raise ValidationError("تاريخ الخروج غير صالح.") from exc
    ensure_schema(conn)
    with conn:
        conn.execute(
            """INSERT INTO certificate_profiles(
                   student_nin, gender, birth_place, latin_first_name, latin_last_name,
                   level, section, registration_no, exit_date, updated_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
               ON CONFLICT(student_nin) DO UPDATE SET
                   gender = excluded.gender,
                   birth_place = excluded.birth_place,
                   latin_first_name = excluded.latin_first_name,
                   latin_last_name = excluded.latin_last_name,
                   level = excluded.level,
                   section = excluded.section,
                   registration_no = excluded.registration_no,
                   exit_date = excluded.exit_date,
                   updated_at = excluded.updated_at""",
            (
                student_nin,
                gender,
                clean["birth_place"],
                clean["latin_first_name"],
                clean["latin_last_name"],
                clean["level"],
                clean["section"],
                clean["registration_no"],
                exit_date,
            ),
        )
    return get_profile(conn, student_nin)


def _student_label(gender: str) -> str:
    if gender == "male":
        return "التلميذ"
    if gender == "female":
        return "التلميذة"
    return "التلميذ(ة)"


def _continuation(gender: str, active: bool) -> str:
    if active:
        if gender == "male":
            return "يتابع دراسته"
        if gender == "female":
            return "تتابع دراستها"
        return "يتابع(ت) دراسته(ها)"
    if gender == "male":
        return "تابع دراسته"
    if gender == "female":
        return "تابعت دراستها"
    return "تابع(ت) دراسته(ها)"


def _fill(text: str, values: dict[str, Any]) -> str:
    rendered = str(text or "")
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", "" if value is None else str(value))
    return rendered


def _academic_year(settings: dict[str, str]) -> str:
    year = str(settings.get("academic_year") or "").strip()
    if year:
        return year
    today = date.today()
    start = today.year if today.month >= 9 else today.year - 1
    return f"{start}/{start + 1}"


def list_students(conn: sqlite3.Connection, query: str = "", limit: int = 200) -> list[dict[str, Any]]:
    like = f"%{str(query or '').strip()}%"
    rows = conn.execute(
        """SELECT * FROM students
           WHERE first_name LIKE ? OR last_name LIKE ?
              OR student_nin LIKE ? OR class_group LIKE ?
           ORDER BY is_active DESC, class_group, last_name, first_name LIMIT ?""",
        (like, like, like, like, max(1, min(int(limit), 500))),
    )
    return [
        {
            "student_nin": row["student_nin"],
            "full_name": full_name(row["first_name"], row["last_name"]),
            "class_group": row["class_group"] or "",
            "is_active": bool(row["is_active"]),
        }
        for row in rows
    ]


def _row_to_context(
    row: sqlite3.Row,
    settings: dict[str, str],
    template: dict[str, Any],
    profile: dict[str, Any],
    extra_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    extra = dict(template["extra"])
    if extra_override:
        extra.update(extra_override)
    nin = row["student_nin"]
    active = bool(row["is_active"])
    state = str(extra.get("state") or "").strip() or "…"
    school_name = str(settings.get("school_name") or "").strip()
    institution_label = str(extra.get("institution_type") or "").strip() or "المؤسسة"
    cycle_word = str(extra.get("cycle_word") or "").strip()
    level = profile.get("level") or row["class_group"] or ""
    section = profile.get("section") or ""
    registration_no = profile.get("registration_no") or nin
    exit_date_iso = profile.get("exit_date") or ("" if active else today_iso())
    exit_date = short_date(exit_date_iso) if exit_date_iso else ""
    latin_name = " ".join(
        part for part in (profile.get("latin_last_name"), profile.get("latin_first_name")) if part
    )
    birth_date = short_date(row["birth_date"]) if row["birth_date"] else ""
    values = {
        "student_name": full_name(row["first_name"], row["last_name"]),
        "first_name": row["first_name"],
        "last_name": row["last_name"],
        "student_nin": western_digits(nin),
        "birth_date": birth_date,
        "birth_place": profile.get("birth_place") or "",
        "gender": profile.get("gender") or "",
        "student_label": _student_label(profile.get("gender") or ""),
        "continuation": _continuation(profile.get("gender") or "", active),
        "class_group": row["class_group"] or "",
        "level": level,
        "section": section,
        "level_line": " ".join(part for part in (level, cycle_word if cycle_word and cycle_word not in level else "", section) if part),
        "registration_no": western_digits(registration_no),
        "registration_suffix": _fill(
            str(extra.get("registration_current" if active else "registration_exit") or ""),
            {"exit_date": exit_date},
        ),
        "latin_name": latin_name,
        "school_name": school_name,
        "institution_label": institution_label,
        "cycle_word": cycle_word,
        "academic_year": _academic_year(settings),
        "state": state,
        "date": today_iso(),
        "date_display": short_date(today_iso()),
        "principal": settings.get("principal_name", ""),
    }
    extra_rendered = {
        key: _fill(value, values) if isinstance(value, str) else value for key, value in extra.items()
    }
    extra_rendered["state"] = state
    extra_rendered["show_barcode"] = _as_bool(extra.get("show_barcode"), True)
    extra_rendered["show_qr"] = _as_bool(extra.get("show_qr"), True)
    extra_rendered["show_stamp"] = _as_bool(extra.get("show_stamp"), True)
    qr_payload = "   ".join(
        part
        for part in (
            values["student_name"],
            f"تاريخ ومكان الميلاد: {birth_date}" if birth_date else "",
            f"بـ: {values['birth_place']}" if values["birth_place"] else "",
            f"{institution_label} {school_name}".strip(),
            f"الفوج التربوي: {values['level_line']}" if values["level_line"] else "",
            f"صدرت بـ: {state} {values['date_display']}",
            values["student_nin"],
        )
        if part
    )
    direction = "rtl" if template["layout"] == "algerian" else template["direction"]
    return {
        "template": template,
        "layout": template["layout"],
        "direction": direction,
        "title": _fill(template["title"], values),
        "body": _fill(template["body"], values),
        "footer_text": _fill(template["footer"], values),
        "extra": extra_rendered,
        "values": values,
        "student": {
            "student_nin": nin,
            "first_name": row["first_name"],
            "last_name": row["last_name"],
            "full_name": values["student_name"],
            "class_group": row["class_group"] or "",
            "birth_date": row["birth_date"] or "",
            "is_active": active,
        },
        "profile": profile,
        "school_name": school_name,
        "academic_year": values["academic_year"],
        "principal_name": settings.get("principal_name", ""),
        "state": state,
        "date_display": values["date_display"],
        "qr_payload": qr_payload,
        "has_profile": any(profile.get(key) for key in profile),
    }


def certificate_context(conn: sqlite3.Connection, student_nin: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM students WHERE student_nin = ?", (student_nin,)
    ).fetchone()
    if not row:
        raise ValidationError("التلميذ غير موجود.")
    settings = get_settings(conn)
    template = get_template(conn)
    profile = get_profile(conn, student_nin)
    return _row_to_context(row, settings, template, profile)


def barcode_svg(nin: str) -> str:
    buffer = BytesIO()
    try:
        product = Code128(western_digits(nin), writer=SVGWriter())
        product.write(
            buffer,
            options={
                "module_width": 0.25,
                "module_height": 12.0,
                "quiet_zone": 2.0,
                "write_text": False,
                "background": "#FFFFFF",
                "foreground": "#000000",
                "dpi": 300,
                "font_size": 0,
            },
        )
    except Exception as exc:  # pragma: no cover - barcode library failures
        raise ValidationError("تعذر توليد باركود الشهادة.") from exc
    svg = buffer.getvalue().decode("utf-8")
    svg = re.sub(r"<\?xml.*?\?>", "", svg, flags=re.DOTALL)
    svg = re.sub(r"<!DOCTYPE.*?>", "", svg, flags=re.DOTALL)
    svg = svg.replace("<svg ", '<svg preserveAspectRatio="none" ', 1)
    return svg.strip()


def qr_svg(payload: str) -> str:
    buffer = BytesIO()
    qr = segno.make(payload or " ", error="m")
    qr.save(buffer, kind="svg", scale=10, border=2, dark="#000000", light="#ffffff")
    svg = buffer.getvalue().decode("utf-8")
    svg = re.sub(r"<\?xml.*?\?>", "", svg, flags=re.DOTALL)
    return svg.strip()
