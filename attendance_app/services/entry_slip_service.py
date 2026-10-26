from __future__ import annotations

import hashlib
import hmac
import sqlite3
from datetime import datetime
from typing import Any

from ..utils import (
    STATUS_AR,
    ValidationError,
    clean_name,
    short_date,
    time_to_seconds,
)
from . import attendance_service as attendance

ENTRY_REASON_PRESETS = (
    "تأخر صباحي",
    "موعد طبي",
    "ظرف عائلي",
    "نقل مدرسي",
    "عطل في وسيلة النقل",
    "سبب آخر",
)

RECEIPT_TEMPLATES = ("full", "medium", "small")

QR_PREFIX = "SAES1"
_QR_SIGNATURE_CHARS = 32


def _canonical(
    slip_code: str,
    student_nin: str,
    slip_date: str,
    check_in_time: str,
    period_id: int,
    new_status: str,
    reason: str,
    issued_by: str,
) -> str:
    return "|".join(
        (
            QR_PREFIX,
            slip_code,
            student_nin,
            slip_date,
            check_in_time[:5],
            str(period_id),
            new_status,
            reason,
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
            slip["slip_date"],
            slip["check_in_time"][:5],
            str(slip["period_id"]),
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
    period = {
        "id": data["period_id"],
        "period_name": data.get("period_name", ""),
        "start_time": (data.get("period_start") or "")[:5],
        "on_time_cutoff": (data.get("on_time_cutoff") or "")[:5],
        "absent_cutoff_time": (data.get("absent_cutoff_time") or "")[:5],
        "is_active": bool(data.get("period_active", 1)),
        "sort_order": data.get("period_sort", 0),
    }
    slip = {
        "id": data["id"],
        "slip_code": data["slip_code"],
        "student_nin": data["student_nin"],
        "period_id": data["period_id"],
        "slip_date": data["slip_date"],
        "date_display": short_date(data["slip_date"]),
        "check_in_time": data["check_in_time"],
        "check_in_display": data["check_in_time"][:5],
        "previous_status": data.get("previous_status"),
        "previous_status_label": STATUS_AR.get(data.get("previous_status") or "", "بدون تسجيل"),
        "new_status": data["new_status"],
        "new_status_label": STATUS_AR.get(data["new_status"], data["new_status"]),
        "reason": data["reason"],
        "note": data.get("note") or "",
        "issued_by": data.get("issued_by") or "",
        "signature": data["signature"],
        "signature_short": data["signature"][:12].upper(),
        "print_count": int(data.get("print_count") or 0),
        "created_at": data.get("created_at"),
        "student": student,
        "period": period,
    }
    slip["qr_payload"] = _qr_payload(data | {"signature": data["signature"]})
    return slip


_SLIP_SELECT = """
SELECT e.*, s.id AS student_id, s.first_name, s.last_name, s.birth_date,
       s.class_group, s.parent_phone, s.photo_url, s.is_active,
       p.period_name, p.start_time AS period_start, p.on_time_cutoff,
       p.absent_cutoff_time, p.is_active AS period_active, p.sort_order AS period_sort
FROM entry_slips e
JOIN students s ON s.student_nin = e.student_nin
JOIN periods p ON p.id = e.period_id
"""


def get_slip(conn: sqlite3.Connection, slip_code: str) -> dict[str, Any] | None:
    code = clean_name(slip_code).upper()
    if not code:
        return None
    row = conn.execute(f"{_SLIP_SELECT} WHERE e.slip_code = ?", (code,)).fetchone()
    return _slip_public(row) if row else None


def _slip_for_day(
    conn: sqlite3.Connection, student_nin: str, period_id: int, day_iso: str
) -> sqlite3.Row | None:
    return conn.execute(
        f"{_SLIP_SELECT} WHERE e.student_nin = ? AND e.period_id = ? AND e.slip_date = ?",
        (student_nin, period_id, day_iso),
    ).fetchone()


def list_recent_slips(
    conn: sqlite3.Connection, day_iso: str, limit: int = 8, period_id: int | None = None
) -> list[dict[str, Any]]:
    if period_id:
        rows = conn.execute(
            f"{_SLIP_SELECT} WHERE e.slip_date = ? AND e.period_id = ? "
            "ORDER BY e.created_at DESC, e.id DESC LIMIT ?",
            (day_iso, period_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            f"{_SLIP_SELECT} WHERE e.slip_date = ? ORDER BY e.created_at DESC, e.id DESC LIMIT ?",
            (day_iso, limit),
        ).fetchall()
    return [_slip_public(row) for row in rows]


def _next_code(conn: sqlite3.Connection, day_iso: str) -> str:
    prefix = f"ES-{day_iso.replace('-', '')}-"
    sequence = conn.execute(
        "SELECT COUNT(*) FROM entry_slips WHERE slip_date = ?", (day_iso,)
    ).fetchone()[0] + 1
    while True:
        code = f"{prefix}{sequence:04d}"
        exists = conn.execute(
            "SELECT 1 FROM entry_slips WHERE slip_code = ?", (code,)
        ).fetchone()
        if not exists:
            return code
        sequence += 1


def _status_for_now(period: dict[str, Any], at: datetime) -> str:
    now_seconds = at.hour * 3600 + at.minute * 60 + at.second
    return "PRESENT" if now_seconds <= time_to_seconds(period["on_time_cutoff"]) else "LATE"


def lookup_entry(
    conn: sqlite3.Connection,
    raw_code: str,
    period_id: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    at = now or datetime.now()
    day_iso = at.date().isoformat()
    period = attendance.choose_period(conn, period_id, require_active=True)
    if not period:
        raise ValidationError("لا توجد فترة نشطة. أضف فترة من الإعدادات أولًا.")
    scan = attendance.lookup_student_scan(conn, raw_code)
    student = scan["student"]
    base: dict[str, Any] = {
        "student": student,
        "period": period,
        "attendance": None,
        "slip": None,
        "today": None,
        "scanned_code": scan["display_code"],
        "lookup": scan["lookup"],
        "action": "",
        "message": "",
        "can_issue": False,
        "new_status_preview": None,
        "new_status_label": "",
        "reasons": list(ENTRY_REASON_PRESETS),
        "date": day_iso,
        "date_display": short_date(day_iso),
        "scan_time": at.strftime("%H:%M:%S"),
    }
    if not student:
        base.update(
            action="not_found",
            message="الباركود غير مسجل في قاعدة بيانات التلاميذ. تحقق من البطاقة أو أعد طباعتها.",
        )
        return base
    base["today"] = attendance.get_student_day_stats(conn, student["student_nin"], day_iso)
    if not student["is_active"]:
        base.update(
            action="inactive",
            message="هذا التلميذ غير نشط ولا يمكن إصدار ورقة دخول له.",
        )
        return base

    slip_row = _slip_for_day(conn, student["student_nin"], period["id"], day_iso)
    if slip_row:
        base.update(
            action="already_issued",
            slip=_slip_public(slip_row),
            message="توجد ورقة دخول صادرة لهذا التلميذ في هذه الفترة. يمكنك إعادة طباعتها.",
        )
        return base

    log = conn.execute(
        """SELECT * FROM attendance_logs
           WHERE student_nin = ? AND period_id = ? AND attendance_date = ?""",
        (student["student_nin"], period["id"], day_iso),
    ).fetchone()
    if log:
        base["attendance"] = attendance.attendance_public(log)
        status = log["status"]
        if status in ("PRESENT", "LATE"):
            base.update(
                action="already_recorded",
                message=f"حضور التلميذ مسجل بالفعل ({STATUS_AR.get(status, status)}) في هذه الفترة.",
            )
            return base
        if status == "EXCUSED":
            base.update(
                action="excused",
                message="غياب التلميذ مبرر مسبقًا. راجع مستشار التربية قبل تغيير الحالة.",
            )
            return base

    status_preview = _status_for_now(period, at)
    base.update(
        action="absent" if log else "not_recorded",
        can_issue=True,
        new_status_preview=status_preview,
        new_status_label=STATUS_AR[status_preview],
        message=(
            "التلميذ غائب في هذه الفترة. راجع سبب الدخول ثم أصدر ورقة الدخول."
            if log
            else "لا يوجد تسجيل بعد لهذه الفترة. ستُسجَّل ورقة الدخول حسب وقت الوصول."
        ),
    )
    return base


def issue_slip(
    conn: sqlite3.Connection,
    raw_code: str,
    period_id: int | None,
    reason: str,
    note: str,
    issued_by: str,
    secret: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    at = now or datetime.now()
    day_iso = at.date().isoformat()
    reason = clean_name(reason)
    note = clean_name(note)
    if len(reason) < 2:
        raise ValidationError("يرجى تحديد سبب الدخول (حرفان على الأقل).")
    if len(reason) > 300:
        raise ValidationError("سبب الدخول طويل جدًا (300 حرف كحد أقصى).")
    if len(note) > 300:
        raise ValidationError("الملاحظة طويلة جدًا (300 حرف كحد أقصى).")

    period = attendance.choose_period(conn, period_id, require_active=True)
    if not period:
        raise ValidationError("لا توجد فترة نشطة. أضف فترة من الإعدادات أولًا.")
    scan = attendance.lookup_student_scan(conn, raw_code)
    student = scan["student"]
    if not student:
        raise ValidationError("الباركود غير مسجل في قاعدة البيانات. تحقق من البطاقة.")
    if not student["is_active"]:
        raise ValidationError("هذا التلميذ غير نشط ولا يمكن إصدار ورقة دخول له.")

    with attendance._ATTENDANCE_WRITE_LOCK:
        existing_slip = _slip_for_day(conn, student["student_nin"], period["id"], day_iso)
        if existing_slip:
            return {
                "slip": _slip_public(existing_slip),
                "sms_notice": "",
                "already_issued": True,
            }

        log = conn.execute(
            """SELECT * FROM attendance_logs
               WHERE student_nin = ? AND period_id = ? AND attendance_date = ?""",
            (student["student_nin"], period["id"], day_iso),
        ).fetchone()
        if log and log["status"] in ("PRESENT", "LATE"):
            raise ValidationError("حضور التلميذ مسجل بالفعل في هذه الفترة، لا حاجة لورقة دخول.")
        if log and log["status"] == "EXCUSED":
            raise ValidationError("غياب التلميذ مبرر مسبقًا. راجع مستشار التربية قبل تغيير الحالة.")

        new_status = _status_for_now(period, at)
        previous_status = log["status"] if log else None
        slip_code = _next_code(conn, day_iso)
        check_in_time = at.strftime("%H:%M:%S")
        signature = _sign(
            secret,
            slip_code=slip_code,
            student_nin=student["student_nin"],
            slip_date=day_iso,
            check_in_time=check_in_time,
            period_id=period["id"],
            new_status=new_status,
            reason=reason,
            issued_by=issued_by,
        )
        log_note = f"ورقة دخول {slip_code}: {reason}"
        if note:
            log_note = f"{log_note} — {note}"

        sms_was_sent = False
        cancelled = 0
        with conn:
            if log:
                conn.execute(
                    """UPDATE attendance_logs
                       SET status = ?, scan_time = ?, source = 'ENTRY_SLIP',
                           note = ?, updated_at = datetime('now', 'localtime')
                       WHERE id = ?""",
                    (new_status, check_in_time, log_note, log["id"]),
                )
            else:
                conn.execute(
                    """INSERT INTO attendance_logs
                       (student_nin, period_id, attendance_date, scan_time, status,
                        note, sms_sent, source)
                       VALUES (?, ?, ?, ?, ?, ?, 0, 'ENTRY_SLIP')""",
                    (
                        student["student_nin"],
                        period["id"],
                        day_iso,
                        check_in_time,
                        new_status,
                        log_note,
                    ),
                )
            for row in conn.execute(
                """SELECT id, delivery_status FROM sms_logs
                   WHERE student_nin = ? AND period_id = ? AND attendance_date = ?""",
                (student["student_nin"], period["id"], day_iso),
            ).fetchall():
                if row["delivery_status"] == "SENT":
                    sms_was_sent = True
            cancelled = conn.execute(
                """UPDATE sms_logs
                   SET delivery_status = 'CANCELLED',
                       error_msg = 'أُلغيت: صدرت ورقة دخول للتلميذ',
                       updated_at = datetime('now', 'localtime')
                   WHERE student_nin = ? AND period_id = ? AND attendance_date = ?
                     AND delivery_status IN ('PENDING', 'FAILED')""",
                (student["student_nin"], period["id"], day_iso),
            ).rowcount
            conn.execute(
                """UPDATE sms_logs
                   SET error_msg = 'ملاحظة: صدرت ورقة دخول بعد إرسال رسالة الغياب',
                       updated_at = datetime('now', 'localtime')
                   WHERE student_nin = ? AND period_id = ? AND attendance_date = ?
                     AND delivery_status = 'SENT'""",
                (student["student_nin"], period["id"], day_iso),
            )
            conn.execute(
                """INSERT INTO entry_slips
                   (slip_code, student_nin, period_id, slip_date, check_in_time,
                    previous_status, new_status, reason, note, issued_by, signature)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    slip_code,
                    student["student_nin"],
                    period["id"],
                    day_iso,
                    check_in_time,
                    previous_status,
                    new_status,
                    reason,
                    note or None,
                    issued_by,
                    signature,
                ),
            )

        sms_notice = ""
        if sms_was_sent:
            sms_notice = "تنبيه: سبق إرسال رسالة غياب إلى الولي، وسُجّل الدخول الآن بورقة رسمية."
        elif cancelled:
            sms_notice = "أُلغيت رسالة الغياب التي كانت في قائمة الانتظار."

        slip = get_slip(conn, slip_code)
        return {"slip": slip, "sms_notice": sms_notice, "already_issued": False}


def register_print(conn: sqlite3.Connection, slip_code: str) -> dict[str, Any]:
    slip = get_slip(conn, slip_code)
    if not slip:
        raise ValidationError("ورقة الدخول غير موجودة.")
    with conn:
        conn.execute(
            """UPDATE entry_slips
               SET print_count = print_count + 1
               WHERE slip_code = ?""",
            (slip["slip_code"],),
        )
    slip["print_count"] += 1
    return slip


def entry_context(
    conn: sqlite3.Connection,
    period_id: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    at = now or datetime.now()
    day_iso = at.date().isoformat()
    periods = attendance.list_periods(conn, include_inactive=False)
    result: dict[str, Any] = {
        "periods": periods,
        "selected_period": None,
        "selected_is_auto": not bool(period_id),
        "closure": None,
        "stats": {"absent": 0, "late": 0, "issued": 0, "total": 0, "logged": 0},
        "recent": [],
        "reasons": list(ENTRY_REASON_PRESETS),
        "date": day_iso,
        "date_display": short_date(day_iso),
        "server_time": at.strftime("%H:%M:%S"),
    }
    if not periods:
        return result
    period = attendance.choose_period(conn, period_id, require_active=True)
    if not period:
        return result
    total = conn.execute("SELECT COUNT(*) FROM students WHERE is_active = 1").fetchone()[0]
    logged = conn.execute(
        """SELECT COUNT(*) FROM attendance_logs a
           JOIN students s ON s.student_nin = a.student_nin
           WHERE a.period_id = ? AND a.attendance_date = ? AND s.is_active = 1""",
        (period["id"], day_iso),
    ).fetchone()[0]
    absent = conn.execute(
        """SELECT COUNT(*) FROM attendance_logs a
           JOIN students s ON s.student_nin = a.student_nin
           WHERE a.period_id = ? AND a.attendance_date = ? AND a.status = 'ABSENT'
             AND s.is_active = 1""",
        (period["id"], day_iso),
    ).fetchone()[0]
    late = conn.execute(
        """SELECT COUNT(*) FROM attendance_logs a
           JOIN students s ON s.student_nin = a.student_nin
           WHERE a.period_id = ? AND a.attendance_date = ? AND a.status = 'LATE'
             AND s.is_active = 1""",
        (period["id"], day_iso),
    ).fetchone()[0]
    issued = conn.execute(
        "SELECT COUNT(*) FROM entry_slips WHERE period_id = ? AND slip_date = ?",
        (period["id"], day_iso),
    ).fetchone()[0]
    result.update(
        selected_period=period,
        closure=attendance.closure_public(
            attendance.get_closure(conn, period["id"], day_iso)
        ),
        stats={
            "absent": absent,
            "late": late,
            "issued": issued,
            "total": total,
            "logged": logged,
        },
        recent=list_recent_slips(conn, day_iso, period_id=period["id"]),
    )
    return result


def verify_entry(
    conn: sqlite3.Connection, raw_payload: str, secret: str
) -> dict[str, Any]:
    text = str(raw_payload or "").strip()
    if not text:
        raise ValidationError("أدخل رمز QR أو رقم ورقة الدخول للتحقق.")
    parts = text.replace("\r", "").replace("\n", "").split("|")
    if parts and parts[0] == QR_PREFIX:
        if len(parts) != 7:
            return {
                "valid": False,
                "checked_signature": True,
                "slip": None,
                "message": "صيغة رمز QR غير مكتملة أو تالفة.",
            }
        _, code, nin, slip_date, check_in, period_id, signature_short = parts
        row = conn.execute(f"{_SLIP_SELECT} WHERE e.slip_code = ?", (code.upper(),)).fetchone()
        if not row:
            return {
                "valid": False,
                "checked_signature": True,
                "slip": None,
                "message": "لا توجد ورقة دخول بهذا الرمز في قاعدة البيانات.",
            }
        expected = _sign(
            secret,
            slip_code=row["slip_code"],
            student_nin=row["student_nin"],
            slip_date=row["slip_date"],
            check_in_time=row["check_in_time"],
            period_id=row["period_id"],
            new_status=row["new_status"],
            reason=row["reason"],
            issued_by=row["issued_by"] or "",
        )
        fields_match = (
            row["student_nin"] == nin
            and row["slip_date"] == slip_date
            and row["check_in_time"][:5] == check_in
            and str(row["period_id"]) == str(period_id)
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
            "message": "لا توجد ورقة دخول بهذا الرقم.",
        }
    return {
        "valid": None,
        "checked_signature": False,
        "slip": slip,
        "message": "تم العثور على الورقة. امسح رمز QR نفسه للتحقق من التوقيع الرقمي.",
    }
