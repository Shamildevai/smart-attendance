from __future__ import annotations

import hashlib
import hmac
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any

from ..utils import (
    ValidationError,
    clean_name,
    short_date,
    squash,
    today_iso,
    validate_iso_date,
    validate_time,
)
from . import attendance_service as attendance

SUMMON_REASON_PRESETS = (
    "غياب متكرر",
    "تأخر متكرر",
    "مشكل سلوكي",
    "نتائج دراسية",
    "عدم إحضار الأدوات",
    "استدعاء إداري",
    "سبب آخر",
)

STATUS_LABELS = {
    "OPEN": "بانتظار الولي",
    "ATTENDED": "حضر الولي",
    "CANCELLED": "ملغى",
}

RECEIPT_TEMPLATES = ("full", "medium", "small")

QR_PREFIX = "SAMS1"
_QR_SIGNATURE_CHARS = 32
DUE_WINDOW_DAYS = 120


def _canonical(
    summon_code: str,
    student_nin: str,
    summon_date: str,
    due_date: str,
    due_time: str,
    reason: str,
    guardian_name: str,
    guardian_phone: str,
    issued_by: str,
) -> str:
    return "|".join(
        (
            QR_PREFIX,
            summon_code,
            student_nin,
            summon_date,
            due_date,
            due_time[:5],
            reason,
            guardian_name,
            guardian_phone,
            issued_by,
        )
    )


def _sign(secret: str, **fields: Any) -> str:
    payload = _canonical(**fields).encode("utf-8")
    return hmac.new(str(secret).encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _qr_payload(summon: dict[str, Any]) -> str:
    return "|".join(
        (
            QR_PREFIX,
            summon["summon_code"],
            summon["student_nin"],
            summon["summon_date"],
            summon["due_date"] or "-",
            summon["signature"][:_QR_SIGNATURE_CHARS],
        )
    )


def _summon_public(row: sqlite3.Row | dict) -> dict[str, Any]:
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
    due_date = data.get("due_date") or ""
    due_time = data.get("due_time") or ""
    summon = {
        "id": data["id"],
        "summon_code": data["summon_code"],
        "barcode": data["barcode"],
        "student_nin": data["student_nin"],
        "summon_date": data["summon_date"],
        "date_display": short_date(data["summon_date"]),
        "due_date": due_date,
        "due_date_display": short_date(due_date) if due_date else "",
        "due_time": due_time[:5],
        "due_display": " ".join(
            part for part in (short_date(due_date) if due_date else "", due_time[:5]) if part
        ),
        "reason": data["reason"],
        "note": data.get("note") or "",
        "guardian_name": data.get("guardian_name") or "",
        "guardian_phone": data.get("guardian_phone") or "",
        "status": data["status"],
        "status_label": STATUS_LABELS.get(data["status"], data["status"]),
        "attended_at": data.get("attended_at") or "",
        "issued_by": data.get("issued_by") or "",
        "signature": data["signature"],
        "signature_short": data["signature"][:12].upper(),
        "print_count": int(data.get("print_count") or 0),
        "created_at": data.get("created_at"),
        "student": student,
    }
    summon["qr_payload"] = _qr_payload(data)
    return summon


_SUMMON_SELECT = """
SELECT m.*, s.id AS student_id, s.first_name, s.last_name, s.birth_date,
       s.class_group, s.parent_phone, s.photo_url, s.is_active
FROM summons m
JOIN students s ON s.student_nin = m.student_nin
"""


def get_summon(conn: sqlite3.Connection, summon_code: str) -> dict[str, Any] | None:
    code = clean_name(summon_code).upper()
    if not code:
        return None
    row = conn.execute(
        f"{_SUMMON_SELECT} WHERE UPPER(m.summon_code) = ? OR m.barcode = ?",
        (code, code),
    ).fetchone()
    return _summon_public(row) if row else None


def find_by_scan(conn: sqlite3.Connection, raw_code: str) -> dict[str, Any] | None:
    text = clean_name(raw_code).upper()
    if not text:
        return None
    if text.startswith(f"{QR_PREFIX}|"):
        parts = text.split("|")
        if len(parts) >= 2:
            text = parts[1]
    return get_summon(conn, text)


def list_for_student(
    conn: sqlite3.Connection, student_nin: str, limit: int = 5
) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"{_SUMMON_SELECT} WHERE m.student_nin = ? ORDER BY m.id DESC LIMIT ?",
        (student_nin, max(1, min(int(limit), 50))),
    ).fetchall()
    return [_summon_public(row) for row in rows]


def list_recent_summons(
    conn: sqlite3.Connection, day_iso: str, limit: int = 10
) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"{_SUMMON_SELECT} WHERE m.summon_date = ? ORDER BY m.created_at DESC, m.id DESC LIMIT ?",
        (day_iso, limit),
    ).fetchall()
    return [_summon_public(row) for row in rows]


def admin_list(
    conn: sqlite3.Connection,
    *,
    status: str = "",
    query: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    values: list[Any] = []
    status = squash(status).upper()
    if status:
        if status not in STATUS_LABELS:
            raise ValidationError("حالة الاستدعاء غير معروفة.")
        clauses.append("m.status = ?")
        values.append(status)
    query = squash(query)
    if query:
        like = f"%{query}%"
        clauses.append(
            "(s.first_name LIKE ? OR s.last_name LIKE ? OR s.student_nin LIKE ? "
            "OR m.summon_code LIKE ? OR m.reason LIKE ?)"
        )
        values.extend([like, like, like, like, like])
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"""{_SUMMON_SELECT}
            {where}
            ORDER BY CASE m.status WHEN 'OPEN' THEN 0 ELSE 1 END, m.id DESC
            LIMIT ?""",
        (*values, max(1, min(int(limit), 500))),
    ).fetchall()
    return [_summon_public(row) for row in rows]


def lookup_student(conn: sqlite3.Connection, raw_code: str) -> dict[str, Any]:
    direct = find_by_scan(conn, raw_code)
    scan = attendance.lookup_student_scan(
        conn, direct["student_nin"] if direct else raw_code
    )
    student = scan["student"]
    day_iso = today_iso()
    base: dict[str, Any] = {
        "student": student,
        "scanned_code": direct["barcode"] if direct else scan["display_code"],
        "lookup": scan["lookup"],
        "summon": direct,
        "scan_kind": "summon_receipt" if direct else "student",
        "action": "",
        "message": "",
        "can_issue": False,
        "today": day_iso,
        "date_display": short_date(day_iso),
        "server_time": datetime.now().strftime("%H:%M"),
        "history": [],
    }
    if not student:
        base.update(
            action="not_found",
            message="الباركود غير مسجل في قاعدة بيانات التلاميذ. تحقق من البطاقة أو أعد طباعتها.",
        )
        return base
    if not student["is_active"]:
        base.update(
            action="inactive",
            message="هذا التلميذ غير نشط ولا يمكن إصدار استدعاء له.",
        )
        return base

    history = list_for_student(conn, student["student_nin"], 5)
    open_count = sum(1 for item in history if item["status"] == "OPEN")
    base.update(
        action="ready",
        can_issue=True,
        history=history,
        open_count=open_count,
        message="جاهز لإصدار استدعاء ولي الأمر وطباعة إيصاله.",
    )
    if direct:
        base["message"] = (
            f"تم التعرف على التلميذ من الاستدعاء {direct['summon_code']}. "
            "يمكنك إعادة طباعة الإيصال أو إصدار استدعاء جديد."
        )
    elif open_count:
        base["message"] = "لدى هذا التلميذ استدعاء سابق بانتظار حضور الولي."
    return base


def _next_code(conn: sqlite3.Connection, day_iso: str) -> tuple[str, str]:
    prefix = f"SM-{day_iso.replace('-', '')}-"
    barcode_prefix = "97" + day_iso.replace("-", "")[2:]
    sequence = (
        conn.execute(
            "SELECT COUNT(*) FROM summons WHERE summon_date = ?", (day_iso,)
        ).fetchone()[0]
        + 1
    )
    while True:
        code = f"{prefix}{sequence:04d}"
        barcode = f"{barcode_prefix}{sequence:04d}"
        taken = conn.execute(
            "SELECT 1 FROM summons WHERE summon_code = ? OR barcode = ?",
            (code, barcode),
        ).fetchone()
        if not taken:
            return code, barcode
        sequence += 1


def issue_summon(
    conn: sqlite3.Connection,
    raw_code: str,
    reason: str,
    due_date: str,
    due_time: str,
    guardian_name: str,
    guardian_phone: str,
    note: str,
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
        raise ValidationError("يرجى تحديد سبب الاستدعاء (حرفان على الأقل).")
    if len(reason) > 300:
        raise ValidationError("سبب الاستدعاء طويل جدًا (300 حرف كحد أقصى).")
    if len(note) > 300:
        raise ValidationError("الملاحظة طويلة جدًا (300 حرف كحد أقصى).")
    if len(guardian_name) > 120:
        raise ValidationError("اسم الولي طويل جدًا (120 حرفًا كحد أقصى).")
    if len(guardian_phone) > 30:
        raise ValidationError("رقم هاتف الولي طويل جدًا (30 رقمًا كحد أقصى).")

    due = ""
    if squash(due_date):
        due = validate_iso_date(squash(due_date), "تاريخ حضور الولي")
        today = date.fromisoformat(day_iso)
        if date.fromisoformat(due) < today:
            raise ValidationError("تاريخ حضور الولي لا يمكن أن يكون في الماضي.")
        if date.fromisoformat(due) > today + timedelta(days=DUE_WINDOW_DAYS):
            raise ValidationError(
                f"اختر موعد حضور خلال {DUE_WINDOW_DAYS} يومًا كحد أقصى."
            )
    due_clock = ""
    if squash(due_time):
        if not due:
            raise ValidationError("حدد تاريخ حضور الولي قبل الوقت.")
        due_clock = validate_time(squash(due_time), "وقت حضور الولي")

    direct = find_by_scan(conn, raw_code)
    scan = attendance.lookup_student_scan(
        conn, direct["student_nin"] if direct else raw_code
    )
    student = scan["student"]
    if not student:
        raise ValidationError("الباركود غير مسجل في قاعدة البيانات. تحقق من البطاقة.")
    if not student["is_active"]:
        raise ValidationError("هذا التلميذ غير نشط ولا يمكن إصدار استدعاء له.")

    summon_code, barcode = _next_code(conn, day_iso)
    signature = _sign(
        secret,
        summon_code=summon_code,
        student_nin=student["student_nin"],
        summon_date=day_iso,
        due_date=due,
        due_time=due_clock,
        reason=reason,
        guardian_name=guardian_name,
        guardian_phone=guardian_phone,
        issued_by=issued_by,
    )
    with conn:
        conn.execute(
            """INSERT INTO summons
               (summon_code, barcode, student_nin, summon_date, due_date, due_time,
                reason, note, guardian_name, guardian_phone, issued_by, signature)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                summon_code,
                barcode,
                student["student_nin"],
                day_iso,
                due or None,
                due_clock or None,
                reason,
                note or None,
                guardian_name,
                guardian_phone,
                issued_by,
                signature,
            ),
        )
    return {"summon": get_summon(conn, summon_code)}


def mark_attended(conn: sqlite3.Connection, summon_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM summons WHERE id = ?", (summon_id,)).fetchone()
    if row is None:
        raise ValidationError("الاستدعاء غير موجود.")
    if row["status"] == "CANCELLED":
        raise ValidationError("الاستدعاء ملغى ولا يمكن تسجيل حضور الولي.")
    if row["status"] == "ATTENDED":
        return {"summon": get_summon(conn, row["summon_code"]), "already": True}
    with conn:
        conn.execute(
            """UPDATE summons
               SET status = 'ATTENDED', attended_at = datetime('now', 'localtime')
               WHERE id = ?""",
            (summon_id,),
        )
    return {"summon": get_summon(conn, row["summon_code"]), "already": False}


def cancel_summon(conn: sqlite3.Connection, summon_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM summons WHERE id = ?", (summon_id,)).fetchone()
    if row is None:
        raise ValidationError("الاستدعاء غير موجود.")
    if row["status"] == "CANCELLED":
        return {"summon": get_summon(conn, row["summon_code"]), "already": True}
    if row["status"] == "ATTENDED":
        raise ValidationError("لا يمكن إلغاء استدعاء حضر وليه.")
    with conn:
        conn.execute(
            "UPDATE summons SET status = 'CANCELLED' WHERE id = ?", (summon_id,)
        )
    return {"summon": get_summon(conn, row["summon_code"]), "already": False}


def register_print(conn: sqlite3.Connection, summon_code: str) -> dict[str, Any]:
    summon = get_summon(conn, summon_code)
    if not summon:
        raise ValidationError("الاستدعاء غير موجود.")
    with conn:
        conn.execute(
            "UPDATE summons SET print_count = print_count + 1 WHERE id = ?",
            (summon["id"],),
        )
    summon["print_count"] += 1
    return summon


def summon_context(
    conn: sqlite3.Connection, now: datetime | None = None
) -> dict[str, Any]:
    at = now or datetime.now()
    day_iso = at.date().isoformat()
    issued = conn.execute(
        "SELECT COUNT(*) FROM summons WHERE summon_date = ?", (day_iso,)
    ).fetchone()[0]
    open_count = conn.execute(
        "SELECT COUNT(*) FROM summons WHERE status = 'OPEN'"
    ).fetchone()[0]
    attended_today = conn.execute(
        "SELECT COUNT(*) FROM summons WHERE status = 'ATTENDED' "
        "AND substr(attended_at, 1, 10) = ?",
        (day_iso,),
    ).fetchone()[0]
    overdue = conn.execute(
        "SELECT COUNT(*) FROM summons WHERE status = 'OPEN' "
        "AND due_date IS NOT NULL AND due_date < ?",
        (day_iso,),
    ).fetchone()[0]
    return {
        "stats": {
            "issued": issued,
            "open": open_count,
            "attended_today": attended_today,
            "overdue": overdue,
        },
        "recent": list_recent_summons(conn, day_iso),
        "open_items": admin_list(conn, status="OPEN", limit=20),
        "reasons": list(SUMMON_REASON_PRESETS),
        "classes": attendance.class_options(conn),
        "date": day_iso,
        "date_display": short_date(day_iso),
        "server_time": at.strftime("%H:%M:%S"),
    }


def verify_summon(
    conn: sqlite3.Connection, raw_payload: str, secret: str
) -> dict[str, Any]:
    text = str(raw_payload or "").strip()
    if not text:
        raise ValidationError("أدخل رمز QR أو رقم الاستدعاء للتحقق.")
    parts = text.replace("\r", "").replace("\n", "").split("|")
    if parts and parts[0] == QR_PREFIX:
        if len(parts) != 6:
            return {
                "valid": False,
                "checked_signature": True,
                "summon": None,
                "message": "صيغة رمز QR غير مكتملة أو تالفة.",
            }
        _, code, nin, summon_date, due_date, signature_short = parts
        row = conn.execute(
            f"{_SUMMON_SELECT} WHERE m.summon_code = ?", (code.upper(),)
        ).fetchone()
        if not row:
            return {
                "valid": False,
                "checked_signature": True,
                "summon": None,
                "message": "لا يوجد استدعاء بهذا الرقم.",
            }
        expected = _sign(
            secret,
            summon_code=row["summon_code"],
            student_nin=row["student_nin"],
            summon_date=row["summon_date"],
            due_date=row["due_date"] or "",
            due_time=row["due_time"] or "",
            reason=row["reason"],
            guardian_name=row["guardian_name"] or "",
            guardian_phone=row["guardian_phone"] or "",
            issued_by=row["issued_by"] or "",
        )
        fields_match = (
            row["student_nin"] == nin
            and row["summon_date"] == summon_date
            and (row["due_date"] or "-") == due_date
        )
        signature_valid = hmac.compare_digest(
            expected[:_QR_SIGNATURE_CHARS].lower(), signature_short.lower()
        )
        valid = bool(fields_match and signature_valid)
        return {
            "valid": valid,
            "checked_signature": True,
            "summon": _summon_public(row),
            "message": (
                "التوقيع صحيح والبيانات مطابقة للسجل الرسمي."
                if valid
                else "تعذر التحقق: التوقيع أو بيانات الرمز لا تطابق السجل."
            ),
        }

    summon = get_summon(conn, text)
    if not summon:
        return {
            "valid": False,
            "checked_signature": False,
            "summon": None,
            "message": "لا يوجد استدعاء بهذا الرقم.",
        }
    return {
        "valid": None,
        "checked_signature": False,
        "summon": summon,
        "message": "تم العثور على الاستدعاء. امسح رمز QR نفسه للتحقق من التوقيع الرقمي.",
    }
