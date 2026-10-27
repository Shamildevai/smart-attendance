from __future__ import annotations

import hashlib
import hmac
import sqlite3
from datetime import datetime
from typing import Any

from ..utils import ValidationError, clean_name, short_date
from . import attendance_service as attendance

EXIT_REASON_PRESETS = (
    "مرض",
    "استدعاء الولي",
    "ظرف عائلي",
    "موعد طبي",
    "سبب آخر",
)

RECEIPT_TEMPLATES = ("full", "medium", "small")

QR_PREFIX = "SAXS1"
_QR_SIGNATURE_CHARS = 32


def _canonical(
    slip_code: str,
    student_nin: str,
    exit_date: str,
    exit_time: str,
    exit_reason: str,
    guardian_name: str,
    guardian_phone: str,
    issued_by: str,
) -> str:
    return "|".join(
        (
            QR_PREFIX,
            slip_code,
            student_nin,
            exit_date,
            exit_time[:5],
            exit_reason,
            guardian_name,
            guardian_phone,
            issued_by,
        )
    )


def _sign(secret: str, **fields: Any) -> str:
    payload = _canonical(**fields).encode("utf-8")
    return hmac.new(str(secret).encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _qr_payload(slip: dict[str, Any]) -> str:
    return "|".join(
        (
            QR_PREFIX,
            slip["slip_code"],
            slip["student_nin"],
            slip["exit_date"],
            slip["exit_time"][:5],
            slip["signature"][:_QR_SIGNATURE_CHARS],
        )
    )


def _slip_public(row: sqlite3.Row | dict) -> dict[str, Any]:
    data = dict(row)
    student = attendance.student_public(
        {
            "id": data.get("student_id"),
            "student_nin": data["student_nin"],
            "first_name": data.get("first_name", ""),
            "last_name": data.get("last_name", ""),
            "birth_date": data.get("birth_date"),
            "class_group": data.get("class_group", ""),
            "parent_phone": data.get("parent_phone", ""),
            "photo_url": data.get("photo_url"),
            "is_active": data.get("is_active", 1),
        }
    )
    returned_at = data.get("returned_at")
    slip = {
        "id": data["id"],
        "slip_code": data["slip_code"],
        "barcode": data["barcode"],
        "student_nin": data["student_nin"],
        "exit_date": data["exit_date"],
        "date_display": short_date(data["exit_date"]),
        "exit_time": data["exit_time"],
        "exit_time_display": data["exit_time"][:5],
        "reason": data["exit_reason"],
        "guardian_name": data.get("guardian_name") or "",
        "guardian_phone": data.get("guardian_phone") or "",
        "note": data.get("note") or "",
        "issued_by": data.get("issued_by") or "",
        "signature": data["signature"],
        "signature_short": data["signature"][:12].upper(),
        "print_count": int(data.get("print_count") or 0),
        "returned": bool(returned_at),
        "returned_at": returned_at or "",
        "returned_display": (returned_at or "")[11:16],
        "entry_slip_code": data.get("entry_slip_code") or "",
        "created_at": data.get("created_at"),
        "student": student,
    }
    slip["qr_payload"] = _qr_payload(data)
    return slip


_SLIP_SELECT = """
SELECT e.*, s.id AS student_id, s.first_name, s.last_name, s.birth_date,
       s.class_group, s.parent_phone, s.photo_url, s.is_active
FROM exit_slips e
JOIN students s ON s.student_nin = e.student_nin
"""


def get_slip(conn: sqlite3.Connection, slip_code: str) -> dict[str, Any] | None:
    code = clean_name(slip_code).upper()
    if not code:
        return None
    row = conn.execute(
        f"{_SLIP_SELECT} WHERE UPPER(e.slip_code) = ? OR e.barcode = ?",
        (code, code),
    ).fetchone()
    return _slip_public(row) if row else None


def find_by_scan(conn: sqlite3.Connection, raw_code: str) -> dict[str, Any] | None:
    text = clean_name(raw_code).upper()
    if not text:
        return None
    if text.startswith(f"{QR_PREFIX}|"):
        parts = text.split("|")
        if len(parts) >= 2:
            text = parts[1]
    return get_slip(conn, text)


def active_slip_for_student(
    conn: sqlite3.Connection, student_nin: str, day_iso: str
) -> dict[str, Any] | None:
    row = conn.execute(
        f"{_SLIP_SELECT} WHERE e.student_nin = ? AND e.exit_date = ? "
        "AND e.returned_at IS NULL ORDER BY e.id DESC LIMIT 1",
        (student_nin, day_iso),
    ).fetchone()
    return _slip_public(row) if row else None


def list_recent_slips(
    conn: sqlite3.Connection, day_iso: str, limit: int = 10
) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"{_SLIP_SELECT} WHERE e.exit_date = ? ORDER BY e.created_at DESC, e.id DESC LIMIT ?",
        (day_iso, limit),
    ).fetchall()
    return [_slip_public(row) for row in rows]


def _next_code(conn: sqlite3.Connection, day_iso: str) -> tuple[str, str]:
    prefix = f"XS-{day_iso.replace('-', '')}-"
    barcode_prefix = "99" + day_iso.replace("-", "")[2:]
    sequence = (
        conn.execute(
            "SELECT COUNT(*) FROM exit_slips WHERE exit_date = ?", (day_iso,)
        ).fetchone()[0]
        + 1
    )
    while True:
        code = f"{prefix}{sequence:04d}"
        barcode = f"{barcode_prefix}{sequence:04d}"
        taken = conn.execute(
            "SELECT 1 FROM exit_slips WHERE slip_code = ? OR barcode = ?",
            (code, barcode),
        ).fetchone()
        if not taken:
            return code, barcode
        sequence += 1


def lookup_exit(
    conn: sqlite3.Connection,
    raw_code: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    at = now or datetime.now()
    day_iso = at.date().isoformat()
    direct = find_by_scan(conn, raw_code)
    scan = attendance.lookup_student_scan(
        conn, direct["student_nin"] if direct else raw_code
    )
    student = scan["student"]
    base: dict[str, Any] = {
        "student": student,
        "scanned_code": direct["barcode"] if direct else scan["display_code"],
        "lookup": scan["lookup"],
        "exit_slip": direct,
        "scan_kind": "exit_receipt" if direct else "student",
        "action": "",
        "message": "",
        "can_issue": False,
        "reasons": list(EXIT_REASON_PRESETS),
        "date": day_iso,
        "date_display": short_date(day_iso),
        "scan_time": at.strftime("%H:%M:%S"),
        "exit_time": at.strftime("%H:%M:%S"),
        "today": None,
    }
    if not student:
        base.update(
            action="not_found",
            message="الباركود غير مسجل في قاعدة بيانات التلاميذ. تحقق من البطاقة أو أعد طباعتها.",
        )
        return base
    base["today"] = attendance.get_student_day_stats(
        conn, student["student_nin"], day_iso
    )
    if not student["is_active"]:
        base.update(
            action="inactive",
            message="هذا التلميذ غير نشط ولا يمكن إصدار ورقة خروج له.",
        )
        return base

    active = active_slip_for_student(conn, student["student_nin"], day_iso)
    if active:
        base.update(
            action="already_issued",
            slip=active,
            message="توجد ورقة خروج سارية لهذا التلميذ اليوم. يمكنك إعادة طباعتها أو تسجيل عودته.",
        )
        return base

    base.update(
        action="ready",
        can_issue=True,
        message="جاهز لإصدار ورقة خروج لهذا التلميذ مع إشعار الولي.",
    )
    if direct:
        base["message"] = "تم التعرف على التلميذ من ورقة خروج سابقة. يمكنك إصدار ورقة خروج جديدة عند الحاجة."
    return base


def issue_slip(
    conn: sqlite3.Connection,
    raw_code: str,
    reason: str,
    note: str,
    guardian_name: str,
    guardian_phone: str,
    issued_by: str,
    secret: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    at = now or datetime.now()
    day_iso = at.date().isoformat()
    reason = clean_name(reason)
    note = clean_name(note)
    guardian_name = clean_name(guardian_name)
    guardian_phone = clean_name(guardian_phone)
    if len(reason) < 2:
        raise ValidationError("يرجى تحديد سبب الخروج (حرفان على الأقل).")
    if len(reason) > 300:
        raise ValidationError("سبب الخروج طويل جدًا (300 حرف كحد أقصى).")
    if len(note) > 300:
        raise ValidationError("الملاحظة طويلة جدًا (300 حرف كحد أقصى).")
    if len(guardian_name) > 120:
        raise ValidationError("اسم الولي طويل جدًا (120 حرفًا كحد أقصى).")
    if len(guardian_phone) > 30:
        raise ValidationError("رقم هاتف الولي طويل جدًا (30 رقمًا كحد أقصى).")

    direct = find_by_scan(conn, raw_code)
    scan = attendance.lookup_student_scan(
        conn, direct["student_nin"] if direct else raw_code
    )
    student = scan["student"]
    if not student:
        raise ValidationError("الباركود غير مسجل في قاعدة البيانات. تحقق من البطاقة.")
    if not student["is_active"]:
        raise ValidationError("هذا التلميذ غير نشط ولا يمكن إصدار ورقة خروج له.")

    with attendance._ATTENDANCE_WRITE_LOCK:
        existing = active_slip_for_student(conn, student["student_nin"], day_iso)
        if existing:
            return {"slip": existing, "already_issued": True}

        slip_code, barcode = _next_code(conn, day_iso)
        exit_time = at.strftime("%H:%M:%S")
        signature = _sign(
            secret,
            slip_code=slip_code,
            student_nin=student["student_nin"],
            exit_date=day_iso,
            exit_time=exit_time,
            exit_reason=reason,
            guardian_name=guardian_name,
            guardian_phone=guardian_phone,
            issued_by=issued_by,
        )
        with conn:
            conn.execute(
                """INSERT INTO exit_slips
                   (slip_code, barcode, student_nin, exit_date, exit_time,
                    exit_reason, guardian_name, guardian_phone, note,
                    issued_by, signature)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    slip_code,
                    barcode,
                    student["student_nin"],
                    day_iso,
                    exit_time,
                    reason,
                    guardian_name,
                    guardian_phone,
                    note or None,
                    issued_by,
                    signature,
                ),
            )
    return {"slip": get_slip(conn, slip_code), "already_issued": False}


def mark_returned(
    conn: sqlite3.Connection,
    raw_code: str,
    entry_slip_code: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    slip = find_by_scan(conn, raw_code)
    if not slip:
        raise ValidationError("ورقة الخروج غير موجودة.")
    if slip["returned"]:
        return {"slip": slip, "already_returned": True}
    at = now or datetime.now()
    with conn:
        conn.execute(
            """UPDATE exit_slips
               SET returned_at = ?, entry_slip_code = ?
               WHERE id = ?""",
            (
                at.strftime("%Y-%m-%d %H:%M:%S"),
                entry_slip_code or slip["entry_slip_code"] or None,
                slip["id"],
            ),
        )
    return {"slip": get_slip(conn, slip["slip_code"]), "already_returned": False}


def register_print(conn: sqlite3.Connection, slip_code: str) -> dict[str, Any]:
    slip = get_slip(conn, slip_code)
    if not slip:
        raise ValidationError("ورقة الخروج غير موجودة.")
    with conn:
        conn.execute(
            "UPDATE exit_slips SET print_count = print_count + 1 WHERE id = ?",
            (slip["id"],),
        )
    slip["print_count"] += 1
    return slip


def exit_context(
    conn: sqlite3.Connection,
    now: datetime | None = None,
) -> dict[str, Any]:
    at = now or datetime.now()
    day_iso = at.date().isoformat()
    issued = conn.execute(
        "SELECT COUNT(*) FROM exit_slips WHERE exit_date = ?", (day_iso,)
    ).fetchone()[0]
    active = conn.execute(
        "SELECT COUNT(*) FROM exit_slips WHERE exit_date = ? AND returned_at IS NULL",
        (day_iso,),
    ).fetchone()[0]
    return {
        "stats": {
            "issued": issued,
            "returned": max(0, issued - active),
            "active": active,
        },
        "recent": list_recent_slips(conn, day_iso),
        "reasons": list(EXIT_REASON_PRESETS),
        "date": day_iso,
        "date_display": short_date(day_iso),
        "server_time": at.strftime("%H:%M:%S"),
    }


def verify_exit(
    conn: sqlite3.Connection, raw_payload: str, secret: str
) -> dict[str, Any]:
    text = str(raw_payload or "").strip()
    if not text:
        raise ValidationError("أدخل رمز QR أو رقم ورقة الخروج للتحقق.")
    parts = text.replace("\r", "").replace("\n", "").split("|")
    if parts and parts[0] == QR_PREFIX:
        if len(parts) != 6:
            return {
                "valid": False,
                "checked_signature": True,
                "slip": None,
                "message": "صيغة رمز QR غير مكتملة أو تالفة.",
            }
        _, code, nin, exit_date, exit_time, signature_short = parts
        row = conn.execute(
            f"{_SLIP_SELECT} WHERE e.slip_code = ?", (code.upper(),)
        ).fetchone()
        if not row:
            return {
                "valid": False,
                "checked_signature": True,
                "slip": None,
                "message": "لا توجد ورقة خروج بهذا الرمز.",
            }
        expected = _sign(
            secret,
            slip_code=row["slip_code"],
            student_nin=row["student_nin"],
            exit_date=row["exit_date"],
            exit_time=row["exit_time"],
            exit_reason=row["exit_reason"],
            guardian_name=row["guardian_name"] or "",
            guardian_phone=row["guardian_phone"] or "",
            issued_by=row["issued_by"] or "",
        )
        fields_match = (
            row["student_nin"] == nin
            and row["exit_date"] == exit_date
            and row["exit_time"][:5] == exit_time
        )
        signature_valid = hmac.compare_digest(
            expected[:_QR_SIGNATURE_CHARS].lower(), signature_short.lower()
        )
        valid = bool(fields_match and signature_valid)
        return {
            "valid": valid,
            "checked_signature": True,
            "slip": _slip_public(row),
            "message": (
                "التوقيع صحيح والبيانات مطابقة للسجل الرسمي."
                if valid
                else "تعذر التحقق: التوقيع أو بيانات الرمز لا تطابق السجل."
            ),
        }

    slip = get_slip(conn, text)
    if not slip:
        return {
            "valid": False,
            "checked_signature": False,
            "slip": None,
            "message": "لا توجد ورقة خروج بهذا الرقم.",
        }
    return {
        "valid": None,
        "checked_signature": False,
        "slip": slip,
        "message": "تم العثور على الورقة. امسح رمز QR نفسه للتحقق من التوقيع الرقمي.",
    }
