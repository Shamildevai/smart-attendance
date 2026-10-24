from __future__ import annotations

import sqlite3
import threading
from datetime import date, datetime, time
from typing import Any

from ..db import bool_setting, int_setting, row_dict, rows_dict
from ..utils import (
    STATUS_AR,
    STATUS_ORDER,
    ValidationError,
    clean_name,
    full_name,
    normalize_nin,
    short_date,
    time_to_seconds,
    today_iso,
    validate_iso_date,
)

# All attendance writes funnel through this lock (gate scans, period closing, excuses).
# It keeps the read-then-write anti-passback/upsert logic atomic inside one process.
_ATTENDANCE_WRITE_LOCK = threading.RLock()


def period_public(row: sqlite3.Row | dict) -> dict[str, Any]:
    data = dict(row)
    return {
        "id": data["id"],
        "period_name": data["period_name"],
        "start_time": data["start_time"][:5],
        "on_time_cutoff": data["on_time_cutoff"][:5],
        "absent_cutoff_time": data["absent_cutoff_time"][:5],
        "is_active": bool(data["is_active"]),
        "sort_order": data.get("sort_order", 0),
    }


def student_public(row: sqlite3.Row | dict) -> dict[str, Any]:
    data = dict(row)
    return {
        "id": data.get("id"),
        "student_nin": data["student_nin"],
        "first_name": data.get("first_name", ""),
        "last_name": data.get("last_name", ""),
        "full_name": full_name(data.get("first_name"), data.get("last_name")),
        "birth_date": data.get("birth_date"),
        "birth_date_display": short_date(data.get("birth_date")) if data.get("birth_date") else "—",
        "class_group": data.get("class_group", ""),
        "parent_phone": data.get("parent_phone", ""),
        "photo_url": data.get("photo_url"),
        "is_active": bool(data.get("is_active", 1)),
    }


def attendance_public(row: sqlite3.Row | dict | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    return {
        "id": data["id"],
        "student_nin": data["student_nin"],
        "period_id": data["period_id"],
        "attendance_date": data["attendance_date"],
        "scan_time": data.get("scan_time"),
        "scan_time_display": (data.get("scan_time") or "")[:5] or "—",
        "status": data["status"],
        "status_label": STATUS_AR.get(data["status"], data["status"]),
        "note": data.get("note") or "",
        "sms_sent": bool(data.get("sms_sent")),
        "source": data.get("source", "GATE"),
    }


def list_periods(conn: sqlite3.Connection, include_inactive: bool = True) -> list[dict[str, Any]]:
    where = "" if include_inactive else "WHERE is_active = 1"
    rows = conn.execute(
        f"SELECT * FROM periods {where} ORDER BY sort_order, start_time, id"
    ).fetchall()
    return [period_public(row) for row in rows]


def get_period(conn: sqlite3.Connection, period_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM periods WHERE id = ?", (period_id,)).fetchone()
    return period_public(row) if row else None


def resolve_auto_period(
    conn: sqlite3.Connection, at: datetime | None = None
) -> dict[str, Any] | None:
    at = at or datetime.now()
    periods = [p for p in list_periods(conn, include_inactive=False)]
    if not periods:
        return None
    now_seconds = at.hour * 3600 + at.minute * 60 + at.second
    current, started = [], []
    for period in periods:
        start = time_to_seconds(period["start_time"])
        end = time_to_seconds(period["absent_cutoff_time"])
        if start <= now_seconds <= end:
            current.append(period)
        if start <= now_seconds:
            started.append(period)
    if current:
        return max(current, key=lambda p: time_to_seconds(p["start_time"]))
    if started:
        return max(started, key=lambda p: time_to_seconds(p["start_time"]))
    return min(periods, key=lambda p: time_to_seconds(p["start_time"]))


def choose_period(
    conn: sqlite3.Connection, period_id: int | None = None, require_active: bool = True
) -> dict[str, Any] | None:
    if period_id:
        period = get_period(conn, int(period_id))
        if not period:
            raise ValidationError("الفترة المحددة غير موجودة.")
        if require_active and not period["is_active"]:
            raise ValidationError("الفترة المحددة معطّلة. اختر فترة نشطة.")
        return period
    return resolve_auto_period(conn)


def get_closure(conn: sqlite3.Connection, period_id: int, day_iso: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM period_closures WHERE period_id = ? AND closure_date = ?",
        (period_id, day_iso),
    ).fetchone()
    return row_dict(row)


def closure_public(row: sqlite3.Row | dict | None) -> dict[str, Any] | None:
    if not row:
        return None
    data = dict(row)
    return {
        "id": data["id"],
        "period_id": data["period_id"],
        "closure_date": data["closure_date"],
        "closed_at": data["closed_at"],
        "closed_by": data["closed_by"],
        "sms_queued": bool(data.get("sms_queued")),
        "note": data.get("note") or "",
    }


def get_period_stats(conn: sqlite3.Connection, period_id: int, day_iso: str) -> dict[str, Any]:
    total = conn.execute(
        "SELECT COUNT(*) FROM students WHERE is_active = 1"
    ).fetchone()[0]
    rows = conn.execute(
        """SELECT a.status, COUNT(*) AS count
           FROM attendance_logs a
           JOIN students s ON s.student_nin = a.student_nin
           WHERE a.period_id = ? AND a.attendance_date = ? AND s.is_active = 1
           GROUP BY a.status""",
        (period_id, day_iso),
    ).fetchall()
    counts = {status: 0 for status in STATUS_ORDER}
    for row in rows:
        counts[row["status"]] = row["count"]
    logged = sum(counts.values())
    missing = max(0, total - logged)
    return {
        "total": total,
        "present": counts["PRESENT"],
        "late": counts["LATE"],
        "absent": counts["ABSENT"],
        "excused": counts["EXCUSED"],
        "logged": logged,
        "unscanned": missing,
        "attendance_rate": round(((counts["PRESENT"] + counts["LATE"]) / total) * 100, 1) if total else 0.0,
    }


def get_student_day_stats(
    conn: sqlite3.Connection, student_nin: str, day_iso: str
) -> dict[str, Any]:
    periods = conn.execute(
        "SELECT * FROM periods WHERE is_active = 1 ORDER BY sort_order, start_time"
    ).fetchall()
    logs = {
        row["period_id"]: row
        for row in conn.execute(
            "SELECT * FROM attendance_logs WHERE student_nin = ? AND attendance_date = ?",
            (student_nin, day_iso),
        ).fetchall()
    }
    items = []
    attended = 0
    for period in periods:
        log = logs.get(period["id"])
        status = log["status"] if log else None
        if status in ("PRESENT", "LATE"):
            attended += 1
        items.append(
            {
                "period_id": period["id"],
                "period_name": period["period_name"],
                "start_time": period["start_time"][:5],
                "status": status,
                "status_label": STATUS_AR.get(status or "", "لم يُسجل"),
                "scan_time": (log["scan_time"] or "")[:5] if log else None,
            }
        )
    return {
        "attended_periods": attended,
        "total_periods": len(periods),
        "periods": items,
    }


def get_sms_summary(conn: sqlite3.Connection, period_id: int, day_iso: str) -> dict[str, Any]:
    rows = conn.execute(
        """SELECT delivery_status, COUNT(*) AS count
           FROM sms_logs WHERE period_id = ? AND attendance_date = ?
           GROUP BY delivery_status""",
        (period_id, day_iso),
    ).fetchall()
    summary = {"PENDING": 0, "SENT": 0, "FAILED": 0, "CANCELLED": 0}
    for row in rows:
        summary[row["delivery_status"]] = row["count"]
    summary["total"] = sum(summary.values())
    return summary


def _minutes_between(day_iso: str, scan_time: str | None, at: datetime) -> float:
    if not scan_time:
        return 10_000.0
    try:
        day = date.fromisoformat(day_iso)
        previous = datetime.combine(day, time.fromisoformat(scan_time))
        return (at - previous).total_seconds() / 60
    except ValueError:
        return 10_000.0


def _scan_base(
    student: dict[str, Any] | None,
    period: dict[str, Any] | None,
    at: datetime,
) -> dict[str, Any]:
    return {
        "success": False,
        "action": "",
        "flash": "error",
        "message": "",
        "student": student,
        "period": period,
        "attendance": None,
        "duplicate": False,
        "scan_time": at.strftime("%H:%M:%S"),
        "date": at.date().isoformat(),
        "date_display": short_date(at.date().isoformat()),
        "sms_notice": "",
        "stats": {},
        "student_today": None,
        "closure": None,
    }


def scan_student(
    conn: sqlite3.Connection,
    raw_code: str,
    period_id: int | None = None,
    now: datetime | None = None,
    anti_passback_minutes: int = 5,
) -> dict[str, Any]:
    at = now or datetime.now()
    day_iso = at.date().isoformat()
    code = normalize_nin(raw_code)
    if not code:
        raise ValidationError("الباركود فارغ أو غير صالح.")

    period = choose_period(conn, period_id, require_active=True)
    if not period:
        raise ValidationError("لا توجد فترة نشطة. أضف فترة من الإعدادات أولًا.")

    with _ATTENDANCE_WRITE_LOCK:
        student_row = conn.execute(
            "SELECT * FROM students WHERE student_nin = ?", (code,)
        ).fetchone()
        closure = get_closure(conn, period["id"], day_iso)
        base = _scan_base(student_public(student_row) if student_row else None, period, at)
        base["closure"] = closure_public(closure)

        if not student_row:
            base.update(
                action="not_found",
                flash="error",
                message=f"الباركود «{code}» غير مسجل في قاعدة البيانات.",
            )
            return _finalize_scan(conn, base, period["id"], day_iso, None)

        student = student_public(student_row)
        if not student["is_active"]:
            base.update(
                action="inactive",
                flash="warning",
                message="هذا التلميذ غير نشط (محذوف أو موقوف) ولا يمكن تسجيل حضوره.",
            )
            return _finalize_scan(conn, base, period["id"], day_iso, None)

        existing = conn.execute(
            """SELECT * FROM attendance_logs
               WHERE student_nin = ? AND period_id = ? AND attendance_date = ?""",
            (code, period["id"], day_iso),
        ).fetchone()

        now_seconds = at.hour * 3600 + at.minute * 60 + at.second
        cutoff_seconds = time_to_seconds(period["on_time_cutoff"])
        computed_status = "PRESENT" if now_seconds <= cutoff_seconds else "LATE"
        expected_label = STATUS_AR[computed_status]

        if existing:
            existing_status = existing["status"]
            if existing_status in ("PRESENT", "LATE"):
                elapsed = _minutes_between(day_iso, existing["scan_time"], at)
                if elapsed <= anti_passback_minutes:
                    minutes = int(round(elapsed))
                    if minutes < 1:
                        elapsed_text = "منذ لحظات"
                    elif minutes == 1:
                        elapsed_text = "قبل دقيقة واحدة"
                    elif 2 <= minutes <= 10:
                        elapsed_text = f"قبل {minutes} دقائق"
                    else:
                        elapsed_text = f"قبل {minutes} دقيقة"
                    base.update(
                        action="duplicate",
                        flash="warning",
                        message=f"تم مسح هذا التلميذ مسبقًا {elapsed_text}. لا يمكن التسجيل مرتين.",
                        duplicate=True,
                        attendance=attendance_public(existing),
                    )
                else:
                    base.update(
                        action="already_recorded",
                        flash="warning",
                        message=f"حضور التلميذ مسجل مسبقًا في هذه الفترة ({STATUS_AR.get(existing_status, existing_status)} — {(existing['scan_time'] or '')[:5]}).",
                        duplicate=True,
                        attendance=attendance_public(existing),
                    )
                return _finalize_scan(conn, base, period["id"], day_iso, None)

            if existing_status == "EXCUSED":
                base.update(
                    action="excused",
                    flash="warning",
                    message="غياب هذا التلميذ مبرر مسبقًا. يرجى مراجعة مستشار التربية قبل أي تعديل.",
                    attendance=attendance_public(existing),
                )
                return _finalize_scan(conn, base, period["id"], day_iso, None)

            if existing_status == "ABSENT":
                sms_rows = conn.execute(
                    """SELECT id, delivery_status FROM sms_logs
                       WHERE student_nin = ? AND period_id = ? AND attendance_date = ?""",
                    (code, period["id"], day_iso),
                ).fetchall()
                sms_was_sent = any(r["delivery_status"] == "SENT" for r in sms_rows)
                with conn:
                    conn.execute(
                        """UPDATE attendance_logs
                           SET status = ?, scan_time = ?, source = 'GATE',
                               note = 'تسجيل وصول بعد رصد الغياب',
                               updated_at = datetime('now', 'localtime')
                           WHERE id = ?""",
                        (computed_status, at.strftime("%H:%M:%S"), existing["id"]),
                    )
                    cancelled = conn.execute(
                        """UPDATE sms_logs
                           SET delivery_status = 'CANCELLED',
                               error_msg = 'أُلغيت: تم تسجيل وصول التلميذ في البوابة',
                               updated_at = datetime('now', 'localtime')
                           WHERE student_nin = ? AND period_id = ? AND attendance_date = ?
                             AND delivery_status IN ('PENDING', 'FAILED')""",
                        (code, period["id"], day_iso),
                    ).rowcount
                updated = conn.execute(
                    "SELECT * FROM attendance_logs WHERE id = ?", (existing["id"],)
                ).fetchone()
                sms_notice = ""
                if sms_was_sent:
                    sms_notice = f"تنبيه: سبق إرسال رسالة غياب إلى الولي، وقد تم تسجيل الوصول الآن كـ {expected_label}."
                    base["flash"] = "warning"
                elif cancelled:
                    sms_notice = "تم إلغاء رسالة الغياب التي كانت في قائمة الانتظار."
                    base["flash"] = "success"
                else:
                    base["flash"] = "success"
                base.update(
                    action="updated_from_absent",
                    message=f"تم تحديث حالة التلميذ من غائب إلى {expected_label}.",
                    attendance=attendance_public(updated),
                    sms_notice=sms_notice,
                    success=True,
                )
                return _finalize_scan(conn, base, period["id"], day_iso, None)

        # No previous record for this period: register arrival.
        with conn:
            cursor = conn.execute(
                """INSERT INTO attendance_logs
                   (student_nin, period_id, attendance_date, scan_time, status, source)
                   VALUES (?, ?, ?, ?, ?, 'GATE')""",
                (code, period["id"], day_iso, at.strftime("%H:%M:%S"), computed_status),
            )
            log_id = cursor.lastrowid
        created = conn.execute(
            "SELECT * FROM attendance_logs WHERE id = ?", (log_id,)
        ).fetchone()
        base.update(
            success=True,
            action="present" if computed_status == "PRESENT" else "late",
            flash="success" if computed_status == "PRESENT" else "warning",
            message=f"تم تسجيل {expected_label} للتلميذ بنجاح.",
            attendance=attendance_public(created),
        )
        return _finalize_scan(conn, base, period["id"], day_iso, None)


def _finalize_scan(
    conn: sqlite3.Connection,
    base: dict[str, Any],
    period_id: int,
    day_iso: str,
    student_nin: str | None,
) -> dict[str, Any]:
    base["stats"] = get_period_stats(conn, period_id, day_iso)
    student = base.get("student")
    if student:
        base["student_today"] = get_student_day_stats(conn, student["student_nin"], day_iso)
    return base


def close_period(
    conn: sqlite3.Connection,
    period_id: int,
    day_iso: str,
    closed_by: str = "MANUAL",
    note: str | None = None,
) -> dict[str, Any]:
    period = get_period(conn, period_id)
    if not period:
        raise ValidationError("الفترة غير موجودة.")
    day_iso = validate_iso_date(day_iso)
    if date.fromisoformat(day_iso) > date.today():
        raise ValidationError("لا يمكن إغلاق فترة في تاريخ مستقبلي.")

    with _ATTENDANCE_WRITE_LOCK:
        existing_closure = get_closure(conn, period_id, day_iso)
        inserted = 0
        if existing_closure:
            closed = False
        else:
            with conn:
                cursor = conn.execute(
                    """INSERT INTO attendance_logs
                       (student_nin, period_id, attendance_date, status, source, note)
                       SELECT s.student_nin, ?, ?, 'ABSENT', 'SWEEP', 'رصد آلي عند إغلاق الفترة'
                       FROM students s
                       WHERE s.is_active = 1
                         AND NOT EXISTS (
                             SELECT 1 FROM attendance_logs a
                             WHERE a.student_nin = s.student_nin
                               AND a.period_id = ?
                               AND a.attendance_date = ?
                         )""",
                    (period_id, day_iso, period_id, day_iso),
                )
                inserted = cursor.rowcount
                conn.execute(
                    """INSERT INTO period_closures
                       (period_id, closure_date, closed_by, note)
                       VALUES (?, ?, ?, ?)""",
                    (period_id, day_iso, closed_by, note or ""),
                )
            closed = True
        closure = get_closure(conn, period_id, day_iso)

    stats = get_period_stats(conn, period_id, day_iso)
    absentees = conn.execute(
        """SELECT COUNT(*) FROM attendance_logs a
           JOIN students s ON s.student_nin = a.student_nin
           WHERE a.period_id = ? AND a.attendance_date = ?
             AND a.status = 'ABSENT' AND s.is_active = 1""",
        (period_id, day_iso),
    ).fetchone()[0]
    with_phone = conn.execute(
        """SELECT COUNT(*) FROM attendance_logs a
           JOIN students s ON s.student_nin = a.student_nin
           WHERE a.period_id = ? AND a.attendance_date = ?
             AND a.status = 'ABSENT' AND s.is_active = 1 AND s.parent_phone <> ''""",
        (period_id, day_iso),
    ).fetchone()[0]
    return {
        "period": period,
        "date": day_iso,
        "already_closed": existing_closure is not None,
        "closed_now": closed,
        "inserted": inserted,
        "absentees": absentees,
        "with_phone": with_phone,
        "without_phone": max(0, absentees - with_phone),
        "closure": closure_public(closure),
        "stats": stats,
    }


def absence_preview(
    conn: sqlite3.Connection, period_id: int, day_iso: str
) -> dict[str, Any]:
    period = get_period(conn, period_id)
    if not period:
        raise ValidationError("الفترة غير موجودة.")
    day_iso = validate_iso_date(day_iso)
    closure = get_closure(conn, period_id, day_iso)

    missing_rows = conn.execute(
        """SELECT s.* FROM students s
           WHERE s.is_active = 1
             AND NOT EXISTS (
                SELECT 1 FROM attendance_logs a
                WHERE a.student_nin = s.student_nin AND a.period_id = ? AND a.attendance_date = ?
             )
           ORDER BY CASE WHEN s.class_group = '' THEN 1 ELSE 0 END, s.class_group, s.last_name, s.first_name""",
        (period_id, day_iso),
    ).fetchall()

    existing_absences = conn.execute(
        """SELECT s.*, a.status, a.id AS log_id
           FROM attendance_logs a JOIN students s ON s.student_nin = a.student_nin
           WHERE a.period_id = ? AND a.attendance_date = ? AND a.status = 'ABSENT' AND s.is_active = 1
           ORDER BY CASE WHEN s.class_group = '' THEN 1 ELSE 0 END, s.class_group, s.last_name""",
        (period_id, day_iso),
    ).fetchall()

    classes: dict[str, int] = {}
    for row in missing_rows:
        cls = row["class_group"] or "بدون قسم"
        classes[cls] = classes.get(cls, 0) + 1

    with_phone = sum(1 for row in missing_rows if (row["parent_phone"] or "").strip())
    sms_summary = get_sms_summary(conn, period_id, day_iso)
    absent_with_phone = sum(
        1 for row in existing_absences if (row["parent_phone"] or "").strip()
    )
    return {
        "period": period,
        "date": day_iso,
        "closed": bool(closure),
        "closure": closure_public(closure),
        "missing_count": len(missing_rows) if not closure else 0,
        "with_phone": with_phone if not closure else absent_with_phone,
        "without_phone": (len(missing_rows) - with_phone) if not closure else max(0, len(existing_absences) - absent_with_phone),
        "classes": [{"class_group": cls, "count": count} for cls, count in classes.items()],
        "sample": [
            {
                "student_nin": row["student_nin"],
                "full_name": full_name(row["first_name"], row["last_name"]),
                "class_group": row["class_group"] or "بدون قسم",
                "parent_phone": row["parent_phone"] or "",
            }
            for row in missing_rows[:12]
        ],
        "existing_absences": len(existing_absences),
        "absent_phone_count": absent_with_phone,
        "sms_summary": sms_summary,
    }


def list_attendance(
    conn: sqlite3.Connection,
    day_iso: str,
    period_id: int | None = None,
    class_group: str | None = None,
    status: str | None = None,
    query: str | None = None,
    page: int = 1,
    per_page: int = 25,
) -> dict[str, Any]:
    day_iso = validate_iso_date(day_iso)
    page = max(1, int(page))
    per_page = min(100, max(5, int(per_page)))
    filters = ["a.attendance_date = ?"]
    params: list[Any] = [day_iso]
    if period_id:
        filters.append("a.period_id = ?")
        params.append(period_id)
    if class_group == "__none__":
        filters.append("s.class_group = ''")
    elif class_group:
        filters.append("s.class_group = ?")
        params.append(class_group)
    if status and status in STATUS_ORDER:
        filters.append("a.status = ?")
        params.append(status)
    if query:
        filters.append("(s.first_name LIKE ? OR s.last_name LIKE ? OR s.student_nin LIKE ?)")
        like = f"%{query.strip()}%"
        params.extend([like, like, like])
    where_sql = " AND ".join(filters)
    total = conn.execute(
        f"""SELECT COUNT(*) FROM attendance_logs a JOIN students s ON s.student_nin = a.student_nin
            WHERE {where_sql}""",
        params,
    ).fetchone()[0]
    rows = conn.execute(
        f"""SELECT a.*, s.first_name, s.last_name, s.class_group, s.photo_url, s.parent_phone,
                   p.period_name, p.start_time,
                   sm.delivery_status AS sms_status, sm.is_test AS sms_is_test, sm.id AS sms_id,
                   sm.message_body AS sms_message_body
            FROM attendance_logs a
            JOIN students s ON s.student_nin = a.student_nin
            JOIN periods p ON p.id = a.period_id
            LEFT JOIN sms_logs sm ON sm.student_nin = a.student_nin
                AND sm.period_id = a.period_id AND sm.attendance_date = a.attendance_date
            WHERE {where_sql}
            ORDER BY p.sort_order, p.start_time, CASE WHEN s.class_group = '' THEN 1 ELSE 0 END,
                     s.class_group, s.last_name, s.first_name
            LIMIT ? OFFSET ?""",
        [*params, per_page, (page - 1) * per_page],
    ).fetchall()
    items = []
    for row in rows:
        data = dict(row)
        items.append(
            {
                "id": data["id"],
                "student_nin": data["student_nin"],
                "full_name": full_name(data["first_name"], data["last_name"]),
                "first_name": data["first_name"],
                "last_name": data["last_name"],
                "class_group": data["class_group"] or "بدون قسم",
                "photo_url": data["photo_url"],
                "parent_phone": data["parent_phone"],
                "period_id": data["period_id"],
                "period_name": data["period_name"],
                "attendance_date": data["attendance_date"],
                "scan_time": (data["scan_time"] or "")[:5],
                "status": data["status"],
                "status_label": STATUS_AR.get(data["status"], data["status"]),
                "note": data["note"] or "",
                "sms_sent": bool(data["sms_sent"]),
                "sms_status": data.get("sms_status"),
                "sms_is_test": bool(data.get("sms_is_test")),
                "sms_id": data.get("sms_id"),
                "sms_message_body": data.get("sms_message_body") or "",
                "source": data["source"],
            }
        )

    day_summary = {"PRESENT": 0, "LATE": 0, "ABSENT": 0, "EXCUSED": 0}
    summary_params: list[Any] = [day_iso]
    summary_filter = "a.attendance_date = ?"
    if period_id:
        summary_filter += " AND a.period_id = ?"
        summary_params.append(period_id)
    if class_group == "__none__":
        summary_filter += " AND s.class_group = ''"
    elif class_group:
        summary_filter += " AND s.class_group = ?"
        summary_params.append(class_group)
    for row in conn.execute(
        f"""SELECT a.status, COUNT(*) AS count FROM attendance_logs a
            JOIN students s ON s.student_nin = a.student_nin
            WHERE {summary_filter} GROUP BY a.status""",
        summary_params,
    ).fetchall():
        day_summary[row["status"]] = row["count"]
    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": max(1, (total + per_page - 1) // per_page),
        "summary": day_summary,
    }


def excuse_attendance(
    conn: sqlite3.Connection, log_id: int, reason: str
) -> dict[str, Any]:
    reason = clean_name(reason)
    if len(reason) < 2:
        raise ValidationError("يرجى كتابة سبب التبرير (حرفان على الأقل).")
    if len(reason) > 500:
        raise ValidationError("سبب التبرير طويل جدًا (500 حرف كحد أقصى).")
    with _ATTENDANCE_WRITE_LOCK:
        row = conn.execute(
            """SELECT a.*, s.first_name, s.last_name, s.class_group, p.period_name
               FROM attendance_logs a
               JOIN students s ON s.student_nin = a.student_nin
               JOIN periods p ON p.id = a.period_id
               WHERE a.id = ?""",
            (log_id,),
        ).fetchone()
        if not row:
            raise ValidationError("سجل الحضور غير موجود.")
        if row["status"] not in ("ABSENT", "EXCUSED"):
            raise ValidationError("يمكن تبرير الغياب فقط، وليس سجل حضور أو تأخر.")
        with conn:
            conn.execute(
                """UPDATE attendance_logs SET status = 'EXCUSED', note = ?,
                       updated_at = datetime('now', 'localtime') WHERE id = ?""",
                (reason, log_id),
            )
            cancelled = conn.execute(
                """UPDATE sms_logs SET delivery_status = 'CANCELLED',
                       error_msg = 'أُلغيت: الغياب اعتُبر مبررًا قبل الإرسال',
                       updated_at = datetime('now', 'localtime')
                   WHERE student_nin = ? AND period_id = ? AND attendance_date = ?
                     AND delivery_status IN ('PENDING', 'FAILED')""",
                (row["student_nin"], row["period_id"], row["attendance_date"]),
            ).rowcount
            conn.execute(
                """UPDATE sms_logs SET error_msg = 'ملاحظة: أُضيف تبرير بعد إرسال رسالة الغياب',
                       updated_at = datetime('now', 'localtime')
                   WHERE student_nin = ? AND period_id = ? AND attendance_date = ?
                     AND delivery_status = 'SENT'""",
                (row["student_nin"], row["period_id"], row["attendance_date"]),
            )
        updated = conn.execute(
            "SELECT * FROM attendance_logs WHERE id = ?", (log_id,)
        ).fetchone()
        sms_row = conn.execute(
            "SELECT delivery_status FROM sms_logs WHERE student_nin = ? AND period_id = ? AND attendance_date = ?",
            (row["student_nin"], row["period_id"], row["attendance_date"]),
        ).fetchone()
        sms_status = sms_row["delivery_status"] if sms_row else None
        full = full_name(row["first_name"], row["last_name"])
        if sms_status == "SENT":
            message = f"تم تبرير غياب {full}. تنبيه: سبق إرسال رسالة الغياب إلى الولي."
        else:
            message = f"تم تبرير غياب {full}، ولن تُرسل رسالة الغياب."
        return {
            "attendance": attendance_public(updated),
            "cancelled_sms": cancelled,
            "sms_status": sms_status,
            "message": message,
            "warning": sms_status == "SENT",
        }


def gate_context(
    conn: sqlite3.Connection,
    period_id: int | None = None,
    now: datetime | None = None,
    anti_passback_minutes: int = 5,
) -> dict[str, Any]:
    at = now or datetime.now()
    day_iso = at.date().isoformat()
    active_periods = list_periods(conn, include_inactive=False)
    auto_period = resolve_auto_period(conn, at)
    selected_id = period_id
    selected_period = None
    if selected_id:
        selected_period = next((p for p in active_periods if p["id"] == selected_id), None)
    is_missing = selected_period is None
    if is_missing:
        selected_period = auto_period
    stats = get_period_stats(conn, selected_period["id"], day_iso) if selected_period else {
        "total": 0, "present": 0, "late": 0, "absent": 0, "excused": 0,
        "logged": 0, "unscanned": 0, "attendance_rate": 0.0,
    }
    closure = (
        get_closure(conn, selected_period["id"], day_iso) if selected_period else None
    )
    recent = []
    if selected_period:
        recent = [
            {
                "student_nin": row["student_nin"],
                "full_name": full_name(row["first_name"], row["last_name"]),
                "class_group": row["class_group"] or "بدون قسم",
                "status": row["status"],
                "status_label": STATUS_AR.get(row["status"], row["status"]),
                "scan_time": (row["scan_time"] or "")[:5],
                "photo_url": row["photo_url"],
            }
            for row in conn.execute(
                """SELECT a.*, s.first_name, s.last_name, s.class_group, s.photo_url
                   FROM attendance_logs a JOIN students s ON s.student_nin = a.student_nin
                   WHERE a.period_id = ? AND a.attendance_date = ?
                     AND a.status IN ('PRESENT', 'LATE') AND a.scan_time IS NOT NULL
                   ORDER BY a.scan_time DESC, a.id DESC LIMIT 8""",
                (selected_period["id"], day_iso),
            ).fetchall()
        ]
    return {
        "periods": active_periods,
        "auto_period": auto_period,
        "selected_period": selected_period,
        "selected_is_auto": not period_id or is_missing,
        "stats": stats,
        "closure": closure_public(closure),
        "recent": recent,
        "date": day_iso,
        "server_time": at.strftime("%H:%M:%S"),
        "total_active_students": conn.execute(
            "SELECT COUNT(*) FROM students WHERE is_active = 1"
        ).fetchone()[0],
        "anti_passback_minutes": anti_passback_minutes,
    }


def class_options(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT class_group, COUNT(*) AS total,
                  SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) AS active
           FROM students
           GROUP BY class_group
           ORDER BY CASE WHEN class_group = '' THEN 1 ELSE 0 END, class_group"""
    ).fetchall()
    options = []
    for row in rows:
        data = dict(row)
        raw = data["class_group"] or ""
        options.append(
            {
                "value": raw if raw else "__none__",
                "label": raw if raw else "بدون قسم",
                "active": int(data["active"] or 0),
                "total": int(data["total"] or 0),
            }
        )
    return options


def classes_breakdown(conn: sqlite3.Connection, period_id: int, day_iso: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT s.class_group AS cls,
                  COUNT(*) AS total,
                  SUM(CASE WHEN a.status = 'PRESENT' THEN 1 ELSE 0 END) AS present,
                  SUM(CASE WHEN a.status = 'LATE' THEN 1 ELSE 0 END) AS late,
                  SUM(CASE WHEN a.status = 'ABSENT' THEN 1 ELSE 0 END) AS absent,
                  SUM(CASE WHEN a.status = 'EXCUSED' THEN 1 ELSE 0 END) AS excused,
                  SUM(CASE WHEN a.id IS NULL THEN 1 ELSE 0 END) AS unscanned
           FROM students s
           LEFT JOIN attendance_logs a
             ON a.student_nin = s.student_nin AND a.period_id = ? AND a.attendance_date = ?
           WHERE s.is_active = 1
           GROUP BY s.class_group
           ORDER BY CASE WHEN s.class_group = '' THEN 1 ELSE 0 END, s.class_group""",
        (period_id, day_iso),
    ).fetchall()
    result = []
    for row in rows:
        data = dict(row)
        data["class_group"] = data.pop("cls") or "بدون قسم"
        total = data["total"] or 0
        data["attendance_rate"] = round(
            ((data["present"] + data["late"]) / total) * 100, 1
        ) if total else 0.0
        result.append(data)
    return result


def dashboard_data(
    conn: sqlite3.Connection, period_id: int | None, day_iso: str
) -> dict[str, Any]:
    day_iso = validate_iso_date(day_iso)
    periods = list_periods(conn, include_inactive=True)
    if not periods:
        return {
            "periods": [],
            "period": None,
            "closure": None,
            "stats": None,
            "classes": [],
            "recent": [],
            "absentees": [],
            "sms": {},
            "date": day_iso,
            "total_active_students": conn.execute(
                "SELECT COUNT(*) FROM students WHERE is_active = 1"
            ).fetchone()[0],
        }
    selected = None
    if period_id:
        selected = get_period(conn, period_id)
    if not selected:
        auto = resolve_auto_period(conn)
        selected = next((p for p in periods if p["id"] == (auto or {}).get("id")), periods[0])
    stats = get_period_stats(conn, selected["id"], day_iso)
    closure = get_closure(conn, selected["id"], day_iso)
    recent = [
        {
            "student_nin": row["student_nin"],
            "full_name": full_name(row["first_name"], row["last_name"]),
            "class_group": row["class_group"] or "بدون قسم",
            "status": row["status"],
            "status_label": STATUS_AR.get(row["status"], row["status"]),
            "scan_time": (row["scan_time"] or "")[:5],
            "photo_url": row["photo_url"],
        }
        for row in conn.execute(
            """SELECT a.*, s.first_name, s.last_name, s.class_group, s.photo_url
               FROM attendance_logs a JOIN students s ON s.student_nin = a.student_nin
               WHERE a.period_id = ? AND a.attendance_date = ?
                 AND a.status IN ('PRESENT','LATE') AND a.scan_time IS NOT NULL
               ORDER BY a.scan_time DESC, a.id DESC LIMIT 8""",
            (selected["id"], day_iso),
        ).fetchall()
    ]
    absentees = [
        {
            "log_id": row["id"],
            "student_nin": row["student_nin"],
            "full_name": full_name(row["first_name"], row["last_name"]),
            "class_group": row["class_group"] or "بدون قسم",
            "parent_phone": row["parent_phone"],
            "note": row["note"] or "",
            "sms_status": row["sms_status"],
        }
        for row in conn.execute(
            """SELECT a.id, a.student_nin, a.note, s.first_name, s.last_name, s.class_group,
                      s.parent_phone, sm.delivery_status AS sms_status
               FROM attendance_logs a
               JOIN students s ON s.student_nin = a.student_nin
               LEFT JOIN sms_logs sm ON sm.student_nin = a.student_nin
                   AND sm.period_id = a.period_id AND sm.attendance_date = a.attendance_date
               WHERE a.period_id = ? AND a.attendance_date = ? AND a.status = 'ABSENT'
                 AND s.is_active = 1
               ORDER BY s.class_group, s.last_name, s.first_name LIMIT 8""",
            (selected["id"], day_iso),
        ).fetchall()
    ]
    return {
        "periods": periods,
        "period": selected,
        "date": day_iso,
        "stats": stats,
        "attendance_rate": stats["attendance_rate"],
        "classes": classes_breakdown(conn, selected["id"], day_iso),
        "closure": closure_public(closure),
        "recent": recent,
        "absentees": absentees,
        "sms": get_sms_summary(conn, selected["id"], day_iso),
        "total_active_students": stats["total"],
        "generated_at": datetime.now().strftime("%H:%M:%S"),
        "day_is_school_day": True,
    }
