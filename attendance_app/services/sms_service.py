from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from datetime import date, datetime
from typing import Any
from urllib.parse import quote

import requests

from ..db import bool_setting, get_settings, int_setting
from .whatsapp_service import build_links
from ..utils import (
    SMS_STATUS_AR,
    ValidationError,
    full_name,
    normalize_phone,
    render_sms_template,
    short_date,
    sms_variables,
    validate_iso_date,
)

MAX_ERROR_LEN = 500


class SmsGatewayError(RuntimeError):
    pass


def _json_object(raw: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except ValueError as exc:
        raise SmsGatewayError(f"{label} ليست JSON صالحًا: {exc}") from exc
    if not isinstance(value, dict):
        raise SmsGatewayError(f"{label} يجب أن تكون كائن JSON.")
    return value


def _substitute(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {k: _substitute(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(item, variables) for item in value]
    if isinstance(value, str):
        for key, replacement in variables.items():
            value = value.replace("{{" + key + "}}", str(replacement))
        return value
    return value


def _validate_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise SmsGatewayError("لم يتم ضبط رابط بوابة الرسائل في الإعدادات.")
    if not re.match(r"^https?://", url, re.IGNORECASE):
        raise SmsGatewayError("رابط بوابة الرسائل يجب أن يبدأ بـ http:// أو https://.")
    return url


def send_via_gateway(settings: dict[str, str], phone: str, message: str) -> dict[str, Any]:
    if settings.get("sms_test_mode", "1") == "1":
        return {
            "ok": True,
            "simulated": True,
            "gateway": settings.get("sms_gateway", "local"),
            "detail": "وضع الاختبار: تمت محاكاة الإرسال دون استخدام الشبكة.",
        }

    gateway = settings.get("sms_gateway", "local")
    url = _validate_url(settings.get("sms_api_url", ""))
    timeout = int_setting(settings, "sms_timeout", 10, 3, 120)
    api_key = settings.get("sms_api_key", "")

    if gateway == "local":
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if api_key:
            headers["X-Api-Key"] = api_key
        payload = {"phone": phone, "message": message}
        label = "بوابة أندرويد المحلية"
    else:
        headers = _json_object(settings.get("sms_headers", "{}"), "ترويسات HTTP")
        headers = _substitute(headers, {"api_key": api_key, "phone": phone})
        headers = {str(k): str(v) for k, v in headers.items()}
        if not any(k.lower() == "content-type" for k in headers):
            headers["Content-Type"] = "application/json"
        template = settings.get("sms_payload_template") or '{"phone": "{{phone}}", "message": "{{message}}"}'
        payload = _json_object(template, "قالب JSON")
        payload = _substitute(payload, {"phone": phone, "message": message, "api_key": api_key})
        url = _substitute(url, {
            "phone": quote(phone, safe=""),
            "message": quote(message, safe=""),
            "api_key": quote(api_key, safe=""),
        })
        label = "واجهة سحابية"

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=timeout, allow_redirects=False)
    except requests.Timeout as exc:
        raise SmsGatewayError("انتهت مهلة الاتصال ببوابة الرسائل.") from exc
    except requests.ConnectionError as exc:
        raise SmsGatewayError("تعذر الاتصال ببوابة الرسائل. تحقق من عنوان الخدمة والشبكة.") from exc
    except requests.RequestException as exc:
        raise SmsGatewayError(f"خطأ في الاتصال ببوابة الرسائل: {exc}") from exc

    if not (200 <= response.status_code < 300):
        snippet = (response.text or "").strip().replace("\n", " ")[:220]
        raise SmsGatewayError(f"رفضت البوابة الطلب (HTTP {response.status_code}){': ' + snippet if snippet else ''}")
    return {
        "ok": True,
        "simulated": False,
        "gateway": "local" if gateway == "local" else "cloud",
        "detail": f"{label} — HTTP {response.status_code}",
    }


def queue_absences(
    conn: sqlite3.Connection,
    period_id: int,
    day_iso: str,
    settings: dict[str, str] | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings(conn)
    day_iso = validate_iso_date(day_iso)
    period = conn.execute("SELECT * FROM periods WHERE id = ?", (period_id,)).fetchone()
    if not period:
        raise ValidationError("الفترة غير موجودة.")

    rows = conn.execute(
        """SELECT s.* FROM attendance_logs a
           JOIN students s ON s.student_nin = a.student_nin
           WHERE a.period_id = ? AND a.attendance_date = ?
             AND a.status = 'ABSENT' AND s.is_active = 1
           ORDER BY s.class_group, s.last_name, s.first_name""",
        (period_id, day_iso),
    ).fetchall()

    template = settings.get("sms_template") or ""
    created = 0
    skipped_no_phone = 0
    skipped_invalid_phone = 0
    existing = 0
    with conn:
        for student_row in rows:
            student = dict(student_row)
            phone = normalize_phone(student.get("parent_phone"))
            if not phone:
                skipped_no_phone += 1
                continue
            if not re.fullmatch(r"\+?\d{8,15}", phone):
                skipped_invalid_phone += 1
                continue
            exists = conn.execute(
                """SELECT 1 FROM sms_logs
                   WHERE student_nin = ? AND period_id = ? AND attendance_date = ?""",
                (student["student_nin"], period_id, day_iso),
            ).fetchone()
            if exists:
                existing += 1
                continue
            message = render_sms_template(
                template,
                sms_variables(student, dict(period), date.fromisoformat(day_iso), settings),
            )
            if not message:
                message = "غياب التلميذ عن الفترة. يرجى التقرب من الإدارة لتبرير الغياب."
            cursor = conn.execute(
                """INSERT INTO sms_logs
                   (student_nin, period_id, attendance_date, parent_phone, message_body, delivery_status)
                   VALUES (?, ?, ?, ?, ?, 'PENDING')""",
                (student["student_nin"], period_id, day_iso, phone, message),
            )
            if cursor.rowcount:
                created += 1
        conn.execute(
            """UPDATE period_closures SET sms_queued = 1
               WHERE period_id = ? AND closure_date = ?""",
            (period_id, day_iso),
        )
    pending_rows = conn.execute(
        """SELECT id FROM sms_logs WHERE period_id = ? AND attendance_date = ?
           AND delivery_status = 'PENDING' ORDER BY id""",
        (period_id, day_iso),
    ).fetchall()
    return {
        "created": created,
        "existing": existing,
        "skipped_no_phone": skipped_no_phone,
        "skipped_invalid_phone": skipped_invalid_phone,
        "absent_total": len(rows),
        "pending_ids": [row["id"] for row in pending_rows],
        "sms_summary": sms_summary(conn, period_id, day_iso),
    }


def sms_summary(conn: sqlite3.Connection, period_id: int, day_iso: str) -> dict[str, int]:
    summary = {"PENDING": 0, "SENT": 0, "FAILED": 0, "CANCELLED": 0}
    for row in conn.execute(
        """SELECT delivery_status, COUNT(*) AS count FROM sms_logs
           WHERE period_id = ? AND attendance_date = ? GROUP BY delivery_status""",
        (period_id, day_iso),
    ).fetchall():
        summary[row["delivery_status"]] = row["count"]
    summary["total"] = sum(summary.values())
    return summary


def list_sms_queue(
    conn: sqlite3.Connection,
    status_filter: str | None = None,
    day_iso: str | None = None,
    period_id: int | None = None,
    query: str | None = None,
    page: int = 1,
    per_page: int = 20,
) -> dict[str, Any]:
    page = max(1, int(page))
    per_page = min(100, max(5, int(per_page)))
    filters: list[str] = []
    params: list[Any] = []
    if status_filter in ("PENDING", "SENT", "FAILED", "CANCELLED"):
        filters.append("sm.delivery_status = ?")
        params.append(status_filter)
    if day_iso:
        validate_iso_date(day_iso)
        filters.append("sm.attendance_date = ?")
        params.append(day_iso)
    if period_id:
        filters.append("sm.period_id = ?")
        params.append(period_id)
    if query:
        like = f"%{query.strip()}%"
        filters.append("(s.first_name LIKE ? OR s.last_name LIKE ? OR sm.student_nin LIKE ? OR sm.parent_phone LIKE ?)")
        params.extend([like, like, like, like])
    where = ("WHERE " + " AND ".join(filters)) if filters else ""

    stat_filters = [f for f in filters if not f.startswith("sm.delivery_status")]
    stat_params = [p for f, p in zip(filters, params) if not f.startswith("sm.delivery_status")]
    where_stats = ("WHERE " + " AND ".join(stat_filters)) if stat_filters else ""
    counts = {"PENDING": 0, "SENT": 0, "FAILED": 0, "CANCELLED": 0}
    for row in conn.execute(
        f"""SELECT sm.delivery_status, COUNT(*) AS count FROM sms_logs sm
            JOIN students s ON s.student_nin = sm.student_nin
            {where_stats} GROUP BY sm.delivery_status""",
        stat_params,
    ).fetchall():
        counts[row["delivery_status"]] = row["count"]
    counts["total"] = sum(counts.values())

    total = conn.execute(
        f"""SELECT COUNT(*) FROM sms_logs sm
            JOIN students s ON s.student_nin = sm.student_nin {where}""",
        params,
    ).fetchone()[0]
    rows = conn.execute(
        f"""SELECT sm.*, s.first_name, s.last_name, s.class_group, s.photo_url,
                   p.period_name
            FROM sms_logs sm
            JOIN students s ON s.student_nin = sm.student_nin
            JOIN periods p ON p.id = sm.period_id
            {where}
            ORDER BY CASE sm.delivery_status WHEN 'PENDING' THEN 0 WHEN 'FAILED' THEN 1
                     WHEN 'SENT' THEN 2 ELSE 3 END,
                     sm.created_at DESC, sm.id DESC
            LIMIT ? OFFSET ?""",
        [*params, per_page, (page - 1) * per_page],
    ).fetchall()
    settings = get_settings(conn)
    whatsapp_enabled = bool_setting(settings, "whatsapp_enabled", True)
    country_code = settings.get("whatsapp_country_code", "213")
    items = []
    for row in rows:
        data = dict(row)
        item = {
            "id": data["id"],
            "student_nin": data["student_nin"],
            "full_name": full_name(data["first_name"], data["last_name"]),
            "class_group": data["class_group"] or "بدون قسم",
            "photo_url": data["photo_url"],
            "parent_phone": data["parent_phone"],
            "period_id": data["period_id"],
            "period_name": data["period_name"],
            "attendance_date": data["attendance_date"],
            "attendance_date_display": short_date(data["attendance_date"]),
            "message_body": data["message_body"],
            "sent_at": data["sent_at"],
            "delivery_status": data["delivery_status"],
            "delivery_status_label": SMS_STATUS_AR.get(data["delivery_status"], data["delivery_status"]),
            "error_msg": data["error_msg"] or "",
            "attempts": data["attempts"],
            "gateway": data["gateway"] or "",
            "is_test": bool(data["is_test"]),
            "created_at": data["created_at"],
        }
        if whatsapp_enabled:
            item.update(build_links(data["parent_phone"], data["message_body"], country_code))
        items.append(item)
    return {
        "items": items,
        "total": total,
        "page": page,
        "pages": max(1, (total + per_page - 1) // per_page),
        "per_page": per_page,
        "counts": counts,
        "whatsapp_enabled": whatsapp_enabled,
    }


def reset_failed_sms(conn: sqlite3.Connection, sms_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM sms_logs WHERE id = ?", (sms_id,)).fetchone()
    if not row:
        raise ValidationError("رسالة SMS غير موجودة.")
    if row["delivery_status"] not in ("FAILED", "PENDING"):
        raise ValidationError("يمكن إعادة محاولة الرسائل الفاشلة أو المعلّقة فقط.")
    with conn:
        conn.execute(
            """UPDATE sms_logs SET delivery_status = 'PENDING', error_msg = NULL,
                   attempts = 0, updated_at = datetime('now', 'localtime')
               WHERE id = ?""",
            (sms_id,),
        )
    updated = conn.execute("SELECT * FROM sms_logs WHERE id = ?", (sms_id,)).fetchone()
    return dict(updated)


class SmsJobManager:
    """Single background dispatcher with in-memory progress + persistent queue rows."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._current_id: str | None = None

    def _snapshot(self, job: dict[str, Any]) -> dict[str, Any]:
        return dict(job)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return self._snapshot(job) if job else None

    def current(self) -> dict[str, Any] | None:
        with self._lock:
            if self._current_id:
                job = self._jobs.get(self._current_id)
                if job and job["status"] == "running":
                    return self._snapshot(job)
            for job in reversed(list(self._jobs.values())):
                if job["status"] == "running":
                    return self._snapshot(job)
            return None

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            if not self._jobs:
                return None
            job = list(self._jobs.values())[-1]
            return self._snapshot(job)

    def start(self, app, ids: list[int] | None = None) -> tuple[dict[str, Any], bool]:
        with self._lock:
            running = self.current()
            if running:
                return running, False
            job_id = uuid.uuid4().hex[:12]
            job = {
                "id": job_id,
                "status": "running",
                "total": 0,
                "processed": 0,
                "sent": 0,
                "failed": 0,
                "cancelled": 0,
                "skipped": 0,
                "simulated": 0,
                "started_at": datetime.now().strftime("%H:%M:%S"),
                "finished_at": None,
                "current_name": "",
                "message": "جارٍ تحضير قائمة الرسائل…",
                "test_mode": False,
                "error": None,
            }
            self._jobs[job_id] = job
            self._current_id = job_id
            # Keep only a small history.
            if len(self._jobs) > 20:
                for old_id in list(self._jobs)[:-20]:
                    if old_id != self._current_id:
                        self._jobs.pop(old_id, None)
        thread = threading.Thread(
            target=self._run,
            args=(app, job_id, ids),
            name="sms-dispatch",
            daemon=True,
        )
        thread.start()
        return self._snapshot(job), True

    def _update(self, job_id: str, **values: Any) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(values)

    def _run(self, app, job_id: str, ids: list[int] | None) -> None:
        from ..db import connect

        conn = None
        try:
            with app.app_context():
                conn = connect(app.config["DB_PATH"])
                settings = get_settings(conn)
                test_mode = settings.get("sms_test_mode", "1") == "1"
                retry_max = int_setting(settings, "sms_retry_max", 3, 1, 10)
                self._update(job_id, test_mode=test_mode)
                if ids:
                    clean_ids = [int(i) for i in ids]
                    placeholders = ",".join("?" for _ in clean_ids)
                    rows = conn.execute(
                        f"""SELECT sm.*, s.first_name, s.last_name, a.status AS attendance_status
                           FROM sms_logs sm
                           JOIN students s ON s.student_nin = sm.student_nin
                           LEFT JOIN attendance_logs a ON a.student_nin = sm.student_nin
                             AND a.period_id = sm.period_id AND a.attendance_date = sm.attendance_date
                           WHERE sm.id IN ({placeholders}) AND sm.delivery_status = 'PENDING'
                           ORDER BY sm.id""",
                        clean_ids,
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """SELECT sm.*, s.first_name, s.last_name, a.status AS attendance_status
                           FROM sms_logs sm
                           JOIN students s ON s.student_nin = sm.student_nin
                           LEFT JOIN attendance_logs a ON a.student_nin = sm.student_nin
                             AND a.period_id = sm.period_id AND a.attendance_date = sm.attendance_date
                           WHERE sm.delivery_status = 'PENDING'
                           ORDER BY sm.id"""
                    ).fetchall()
                self._update(job_id, total=len(rows))
                if not rows:
                    self._update(
                        job_id,
                        status="done",
                        finished_at=datetime.now().strftime("%H:%M:%S"),
                        message="لا توجد رسائل معلّقة للإرسال.",
                    )
                    return

                counts = {"sent": 0, "failed": 0, "cancelled": 0, "skipped": 0, "simulated": 0}
                for index, row in enumerate(rows, start=1):
                    name = full_name(row["first_name"], row["last_name"])
                    self._update(job_id, current_name=name)
                    fresh = conn.execute(
                        "SELECT delivery_status FROM sms_logs WHERE id = ?", (row["id"],)
                    ).fetchone()
                    if not fresh or fresh["delivery_status"] != "PENDING":
                        counts["skipped"] += 1
                        self._update(job_id, processed=index, **counts)
                        continue
                    if row["attendance_status"] != "ABSENT":
                        with conn:
                            conn.execute(
                                """UPDATE sms_logs SET delivery_status = 'CANCELLED',
                                       error_msg = 'أُلغيت: حالة الغياب تغيّرت قبل الإرسال',
                                       updated_at = datetime('now', 'localtime')
                                   WHERE id = ?""",
                                (row["id"],),
                            )
                        counts["cancelled"] += 1
                        self._update(job_id, processed=index, **counts)
                        continue

                    success = False
                    simulated = False
                    last_error = ""
                    gateway_label = ""
                    for attempt in range(1, retry_max + 1):
                        with conn:
                            conn.execute(
                                "UPDATE sms_logs SET attempts = attempts + 1 WHERE id = ?",
                                (row["id"],),
                            )
                        try:
                            result = send_via_gateway(settings, row["parent_phone"], row["message_body"])
                            success = True
                            simulated = bool(result.get("simulated"))
                            gateway_label = str(result.get("gateway") or "")
                            break
                        except SmsGatewayError as exc:
                            last_error = str(exc)[:MAX_ERROR_LEN]
                            app.logger.warning(
                                "SMS attempt %s/%s failed for %s: %s",
                                attempt, retry_max, row["student_nin"], last_error,
                            )
                            if attempt < retry_max:
                                time.sleep(1.2)
                        except Exception as exc:  # pragma: no cover - defensive
                            last_error = f"خطأ غير متوقع: {exc}"[:MAX_ERROR_LEN]
                            app.logger.exception("Unexpected SMS error")
                            break

                    if success:
                        with conn:
                            conn.execute(
                                """UPDATE sms_logs
                                   SET delivery_status = 'SENT',
                                       sent_at = datetime('now', 'localtime'),
                                       error_msg = ?, gateway = ?, is_test = ?,
                                       updated_at = datetime('now', 'localtime')
                                   WHERE id = ?""",
                                (
                                    "وضع الاختبار: تمت محاكاة الإرسال دون استخدام الشبكة."
                                    if simulated else None,
                                    gateway_label,
                                    1 if simulated else 0,
                                    row["id"],
                                ),
                            )
                            if not simulated:
                                conn.execute(
                                    """UPDATE attendance_logs SET sms_sent = 1,
                                           updated_at = datetime('now', 'localtime')
                                       WHERE student_nin = ? AND period_id = ?
                                         AND attendance_date = ?""",
                                    (row["student_nin"], row["period_id"], row["attendance_date"]),
                                )
                        counts["sent"] += 1
                        if simulated:
                            counts["simulated"] += 1
                    else:
                        with conn:
                            conn.execute(
                                """UPDATE sms_logs SET delivery_status = 'FAILED',
                                       error_msg = ?, gateway = ?,
                                       updated_at = datetime('now', 'localtime')
                                   WHERE id = ?""",
                                (last_error or "فشل غير معروف", gateway_label, row["id"]),
                            )
                        counts["failed"] += 1
                    self._update(job_id, processed=index, **counts)
                self._update(
                    job_id,
                    status="done",
                    finished_at=datetime.now().strftime("%H:%M:%S"),
                    message=(
                        "اكتملت المعالجة: "
                        f"{counts['sent']} ناجحة، {counts['failed']} فاشلة، "
                        f"{counts['cancelled']} ملغاة."
                    ),
                    current_name="",
                )
        except Exception as exc:  # pragma: no cover - defensive
            app.logger.exception("SMS dispatch job failed")
            self._update(
                job_id,
                status="done",
                finished_at=datetime.now().strftime("%H:%M:%S"),
                error=str(exc)[:MAX_ERROR_LEN],
                message="توقف الإرسال بسبب خطأ غير متوقع. راجع سجل التطبيق.",
            )
        finally:
            if conn is not None:
                conn.close()
            with self._lock:
                self._current_id = None


def test_gateway(settings: dict[str, str], phone: str) -> dict[str, Any]:
    phone = normalize_phone(phone)
    if not re.fullmatch(r"\+?\d{8,15}", phone):
        raise ValidationError("رقم الهاتف التجريبي غير صالح.")
    message = "رسالة تجريبية من نظام متابعة الحضور. إن وصلتك هذه الرسالة فإن بوابة SMS تعمل بنجاح."
    result = send_via_gateway(settings, phone, message)
    if result.get("simulated"):
        return {
            "sent": False,
            "simulated": True,
            "message": "وضع الاختبار مفعّل: تمت المحاكاة بنجاح ولم تُرسل رسالة حقيقية. عطّل وضع الاختبار لإجراء اختبار فعلي.",
        }
    return {
        "sent": True,
        "simulated": False,
        "message": "تم إرسال الرسالة التجريبية عبر بوابة الرسائل.",
    }
