from __future__ import annotations

import math
import re
import sqlite3
import threading
import time
import uuid
from datetime import date, datetime
from typing import Any

from ..db import bool_setting, get_settings, int_setting
from ..utils import (
    ValidationError,
    full_name,
    normalize_phone,
    render_sms_template,
    sms_variables,
)
from . import whatsapp_service
from .sms_service import MAX_ERROR_LEN, SmsGatewayError, send_via_gateway

MAX_MESSAGE_LENGTH = 2000

CHANNEL_LABELS = {
    "mobile": "SMS عبر تطبيق الهاتف",
    "cloud": "SMS عبر الحاسوب أو السحابة",
    "whatsapp": "واتساب (إرسال يدوي)",
}

CAMPAIGN_STATUS_LABELS = {
    "PENDING": "في الانتظار",
    "RUNNING": "جارٍ الإرسال",
    "DONE": "مكتملة",
    "PREPARED": "جاهزة للإرسال اليدوي",
}

OUTBOX_STATUS_LABELS = {
    "PENDING": "في الانتظار",
    "SENT": "تم الإرسال",
    "FAILED": "فشل",
    "CANCELLED": "ملغاة",
    "MANUAL": "جاهزة في واتساب",
}

SCOPE_LABELS = {
    "all": "كل التلاميذ النشطين",
    "class": "قسم محدد",
    "selected": "تلاميذ محددون",
}


def _sms_phone(value: Any) -> str:
    phone = normalize_phone(value)
    return phone if re.fullmatch(r"\+?\d{8,15}", phone or "") else ""


def _eligible(student: dict[str, Any], channel: str, settings: dict[str, str]) -> bool:
    if channel == "whatsapp":
        return bool(
            whatsapp_service.normalize_whatsapp_number(
                student.get("parent_phone"), settings.get("whatsapp_country_code", "213")
            )
        )
    return bool(_sms_phone(student.get("parent_phone")))


def resolve_channel_settings(settings: dict[str, str], channel: str) -> dict[str, str]:
    """Return a settings copy pointing at the endpoint for the requested channel."""
    if channel not in ("mobile", "cloud"):
        raise ValidationError("قناة الإرسال غير صالحة.")
    resolved = dict(settings)
    if channel == "mobile":
        url = (settings.get("sms_mobile_url") or "").strip()
        key = settings.get("sms_mobile_key") or ""
        if not url and settings.get("sms_gateway") == "local":
            url = (settings.get("sms_api_url") or "").strip()
            key = settings.get("sms_api_key") or ""
        resolved["sms_gateway"] = "local"
    else:
        url = (settings.get("sms_cloud_url") or "").strip()
        key = settings.get("sms_cloud_key") or ""
        if not url and settings.get("sms_gateway") == "cloud":
            url = (settings.get("sms_api_url") or "").strip()
            key = settings.get("sms_api_key") or ""
        resolved["sms_gateway"] = "cloud"
    resolved["sms_api_url"] = url
    resolved["sms_api_key"] = key
    return resolved


def channels_payload(settings: dict[str, str]) -> dict[str, Any]:
    test_mode = settings.get("sms_test_mode", "1") == "1"
    whatsapp_enabled = bool_setting(settings, "whatsapp_enabled", True)
    channels = []
    for channel in ("mobile", "cloud"):
        resolved = resolve_channel_settings(settings, channel)
        configured = bool((resolved.get("sms_api_url") or "").strip())
        channels.append(
            {
                "id": channel,
                "label": CHANNEL_LABELS[channel],
                "configured": configured,
                "ready": configured or test_mode,
                "test_mode": test_mode,
            }
        )
    channels.append(
        {
            "id": "whatsapp",
            "label": CHANNEL_LABELS["whatsapp"],
            "configured": whatsapp_enabled,
            "ready": whatsapp_enabled,
            "test_mode": False,
        }
    )
    return {
        "channels": channels,
        "default_channel": settings.get("sms_gateway", "local"),
        "test_mode": test_mode,
        "whatsapp_enabled": whatsapp_enabled,
        "country_code": settings.get("whatsapp_country_code", "213"),
    }


def _clean_nins(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def fetch_recipients(
    conn: sqlite3.Connection,
    *,
    scope: str,
    class_group: str | None = None,
    student_nins: Any = None,
    query: str | None = None,
) -> list[dict[str, Any]]:
    filters = ["s.is_active = 1"]
    params: list[Any] = []
    if scope == "class":
        if class_group == "__none__":
            filters.append("s.class_group = ''")
        elif class_group:
            filters.append("s.class_group = ?")
            params.append(class_group)
        else:
            raise ValidationError("يرجى اختيار القسم قبل تجهيز الرسائل.")
    elif scope == "selected":
        nins = _clean_nins(student_nins)
        if not nins:
            raise ValidationError("يرجى تحديد تلميذ واحد على الأقل.")
        placeholders = ",".join("?" for _ in nins)
        filters.append(f"s.student_nin IN ({placeholders})")
        params.extend(nins)
    elif scope != "all":
        raise ValidationError("نطاق المستلمين غير صالح.")
    if query:
        like = f"%{query.strip()}%"
        filters.append(
            "(s.first_name LIKE ? OR s.last_name LIKE ? OR s.student_nin LIKE ? "
            "OR s.parent_phone LIKE ? OR s.class_group LIKE ?)"
        )
        params.extend([like, like, like, like, like])
    rows = conn.execute(
        f"""SELECT s.* FROM students s WHERE {' AND '.join(filters)}
            ORDER BY CASE WHEN s.class_group = '' THEN 1 ELSE 0 END,
                     s.class_group, s.last_name, s.first_name, s.student_nin""",
        params,
    ).fetchall()
    return [
        {
            "student_nin": row["student_nin"],
            "first_name": row["first_name"],
            "last_name": row["last_name"],
            "full_name": full_name(row["first_name"], row["last_name"]),
            "class_group": row["class_group"] or "بدون قسم",
            "class_group_raw": row["class_group"] or "",
            "parent_phone": row["parent_phone"] or "",
            "photo_url": row["photo_url"],
        }
        for row in rows
    ]


def class_summary(recipients: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for item in recipients:
        key = item["class_group"]
        counts[key] = counts.get(key, 0) + 1
    return [
        {"class_group": key, "count": count}
        for key, count in sorted(counts.items(), key=lambda row: (-row[1], row[0]))
    ]


def _render_message(template: str, student: dict[str, Any], settings: dict[str, str]) -> str:
    variables = sms_variables(student, {"period_name": ""}, date.today(), settings)
    variables["الهاتف"] = student.get("parent_phone", "")
    return render_sms_template(template, variables)


def _validate_message(message: Any) -> str:
    text = str(message or "").strip()
    if not text:
        raise ValidationError("يرجى كتابة نص الرسالة قبل المتابعة.")
    if len(text) > MAX_MESSAGE_LENGTH:
        raise ValidationError(f"نص الرسالة أطول من الحد المسموح ({MAX_MESSAGE_LENGTH} حرف).")
    return text


def _segment_count(text: str) -> int:
    single = 70 if re.search(r"[^\x00-\x7F]", text) else 160
    return max(1, math.ceil(len(text) / single))


def preview_campaign(
    conn: sqlite3.Connection,
    *,
    scope: str,
    channel: str,
    message: str,
    class_group: str | None = None,
    student_nins: Any = None,
    query: str | None = None,
) -> dict[str, Any]:
    if channel not in CHANNEL_LABELS:
        raise ValidationError("قناة الإرسال غير صالحة.")
    text = _validate_message(message)
    settings = get_settings(conn)
    recipients = fetch_recipients(
        conn,
        scope=scope,
        class_group=class_group,
        student_nins=student_nins,
        query=query,
    )
    eligible = [item for item in recipients if _eligible(item, channel, settings)]
    samples = []
    for student in eligible[:8]:
        body = _render_message(text, student, settings)
        sample = {
            "student_nin": student["student_nin"],
            "full_name": student["full_name"],
            "class_group": student["class_group"],
            "parent_phone": student["parent_phone"],
            "message_body": body,
        }
        if channel == "whatsapp":
            sample.update(
                whatsapp_service.build_links(
                    student["parent_phone"],
                    body,
                    settings.get("whatsapp_country_code", "213"),
                )
            )
        samples.append(sample)
    rendered_length = len(_render_message(text, recipients[0], settings)) if recipients else len(text)
    return {
        "scope": scope,
        "scope_label": SCOPE_LABELS.get(scope, ""),
        "channel": channel,
        "channel_label": CHANNEL_LABELS[channel],
        "recipients_total": len(recipients),
        "eligible_total": len(eligible),
        "skipped_total": len(recipients) - len(eligible),
        "classes": class_summary(recipients),
        "samples": samples,
        "message_length": rendered_length,
        "segments": _segment_count(text) if channel != "whatsapp" else 0,
        "test_mode": settings.get("sms_test_mode", "1") == "1",
        "whatsapp_enabled": bool_setting(settings, "whatsapp_enabled", True),
        "channels": channels_payload(settings),
    }


def create_campaign(
    conn: sqlite3.Connection,
    *,
    scope: str,
    channel: str,
    message: str,
    class_group: str | None = None,
    student_nins: Any = None,
    query: str | None = None,
    approved: bool,
    created_by: str = "",
) -> dict[str, Any]:
    if not approved:
        raise ValidationError("يجب الموافقة صراحةً قبل تجهيز أو إرسال الرسائل.")
    if channel not in CHANNEL_LABELS:
        raise ValidationError("قناة الإرسال غير صالحة.")
    text = _validate_message(message)
    settings = get_settings(conn)
    if channel == "whatsapp" and not bool_setting(settings, "whatsapp_enabled", True):
        raise ValidationError("أزرار واتساب معطّلة في الإعدادات. فعّلها أولًا من إعدادات الرسائل.")
    test_mode = settings.get("sms_test_mode", "1") == "1"
    if channel in ("mobile", "cloud") and not test_mode:
        resolved = resolve_channel_settings(settings, channel)
        if not (resolved.get("sms_api_url") or "").strip():
            raise ValidationError(
                "قناة الإرسال المختارة غير مهيأة. أضف رابط البوابة المناسب من إعدادات الرسائل."
            )
    recipients = fetch_recipients(
        conn,
        scope=scope,
        class_group=class_group,
        student_nins=student_nins,
        query=query,
    )
    eligible = [item for item in recipients if _eligible(item, channel, settings)]
    if not eligible:
        raise ValidationError(
            "لا يوجد مستلمون يحملون رقم هاتف صالحًا في النطاق المحدد. راجع أرقام أولياء التلاميذ."
        )
    campaign_id = uuid.uuid4().hex[:12]
    outbox_ids: list[int] = []
    items: list[dict[str, Any]] = []
    status = "MANUAL" if channel == "whatsapp" else "PENDING"
    campaign_status = "PREPARED" if channel == "whatsapp" else "PENDING"
    with conn:
        conn.execute(
            """INSERT INTO message_campaigns
               (id, channel, scope, class_group, message_template, recipients_total,
                eligible_total, skipped_total, status, test_mode, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                campaign_id,
                channel,
                scope,
                class_group or "",
                text,
                len(recipients),
                len(eligible),
                len(recipients) - len(eligible),
                campaign_status,
                1 if test_mode and channel != "whatsapp" else 0,
                created_by,
            ),
        )
        for student in eligible:
            body = _render_message(text, student, settings)
            cursor = conn.execute(
                """INSERT INTO message_outbox
                   (campaign_id, student_nin, student_name, class_group, parent_phone,
                    message_body, channel, delivery_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    campaign_id,
                    student["student_nin"],
                    student["full_name"],
                    student["class_group_raw"],
                    student["parent_phone"],
                    body,
                    channel,
                    status,
                ),
            )
            if status == "PENDING" and cursor.lastrowid:
                outbox_ids.append(int(cursor.lastrowid))
            if channel == "whatsapp":
                item = {
                    "id": cursor.lastrowid,
                    "student_nin": student["student_nin"],
                    "full_name": student["full_name"],
                    "class_group": student["class_group"],
                    "parent_phone": student["parent_phone"],
                    "message_body": body,
                }
                item.update(
                    whatsapp_service.build_links(
                        student["parent_phone"],
                        body,
                        settings.get("whatsapp_country_code", "213"),
                    )
                )
                items.append(item)
    campaign = get_campaign(conn, campaign_id)
    return {
        "campaign": campaign,
        "outbox_ids": outbox_ids,
        "items": items,
        "test_mode": test_mode and channel != "whatsapp",
    }


def _campaign_row(conn: sqlite3.Connection, campaign_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM message_campaigns WHERE id = ?", (campaign_id,)
    ).fetchone()


def _campaign_counts(conn: sqlite3.Connection, campaign_id: str) -> dict[str, int]:
    counts = {key: 0 for key in OUTBOX_STATUS_LABELS}
    for row in conn.execute(
        """SELECT delivery_status, COUNT(*) AS count FROM message_outbox
           WHERE campaign_id = ? GROUP BY delivery_status""",
        (campaign_id,),
    ).fetchall():
        counts[row["delivery_status"]] = row["count"]
    counts["total"] = sum(counts.values())
    return counts


def _campaign_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["channel_label"] = CHANNEL_LABELS.get(data["channel"], data["channel"])
    data["scope_label"] = SCOPE_LABELS.get(data["scope"], data["scope"])
    data["status_label"] = CAMPAIGN_STATUS_LABELS.get(data["status"], data["status"])
    data["counts"] = _campaign_counts(conn, data["id"])
    preview = conn.execute(
        "SELECT message_body FROM message_outbox WHERE campaign_id = ? ORDER BY id LIMIT 1",
        (data["id"],),
    ).fetchone()
    data["message_preview"] = preview["message_body"] if preview else data["message_template"]
    return data


def get_campaign(conn: sqlite3.Connection, campaign_id: str) -> dict[str, Any] | None:
    row = _campaign_row(conn, campaign_id)
    return _campaign_dict(conn, row) if row else None


def delete_campaign(conn: sqlite3.Connection, campaign_id: str) -> None:
    with conn:
        conn.execute("DELETE FROM message_outbox WHERE campaign_id = ?", (campaign_id,))
        conn.execute("DELETE FROM message_campaigns WHERE id = ?", (campaign_id,))


def list_campaigns(
    conn: sqlite3.Connection, page: int = 1, per_page: int = 10
) -> dict[str, Any]:
    page = max(1, int(page))
    per_page = min(50, max(5, int(per_page)))
    total = conn.execute("SELECT COUNT(*) FROM message_campaigns").fetchone()[0]
    rows = conn.execute(
        """SELECT * FROM message_campaigns ORDER BY created_at DESC, rowid DESC
           LIMIT ? OFFSET ?""",
        (per_page, (page - 1) * per_page),
    ).fetchall()
    return {
        "items": [_campaign_dict(conn, row) for row in rows],
        "total": total,
        "page": page,
        "pages": max(1, (total + per_page - 1) // per_page),
        "per_page": per_page,
    }


def campaign_detail(
    conn: sqlite3.Connection,
    campaign_id: str,
    page: int = 1,
    per_page: int = 25,
    status_filter: str = "",
) -> dict[str, Any]:
    campaign = get_campaign(conn, campaign_id)
    if not campaign:
        raise ValidationError("حملة الرسائل غير موجودة.")
    page = max(1, int(page))
    per_page = min(100, max(5, int(per_page)))
    filters = ["campaign_id = ?"]
    params: list[Any] = [campaign_id]
    if status_filter in OUTBOX_STATUS_LABELS:
        filters.append("delivery_status = ?")
        params.append(status_filter)
    where = " AND ".join(filters)
    total = conn.execute(
        f"SELECT COUNT(*) FROM message_outbox WHERE {where}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"""SELECT * FROM message_outbox WHERE {where}
            ORDER BY CASE delivery_status WHEN 'FAILED' THEN 0 WHEN 'PENDING' THEN 1
                     WHEN 'MANUAL' THEN 2 WHEN 'SENT' THEN 3 ELSE 4 END, id
            LIMIT ? OFFSET ?""",
        [*params, per_page, (page - 1) * per_page],
    ).fetchall()
    settings = get_settings(conn)
    whatsapp_enabled = bool_setting(settings, "whatsapp_enabled", True)
    country_code = settings.get("whatsapp_country_code", "213")
    items = []
    for row in rows:
        item = dict(row)
        item["status_label"] = OUTBOX_STATUS_LABELS.get(item["delivery_status"], item["delivery_status"])
        if whatsapp_enabled:
            item.update(whatsapp_service.build_links(item["parent_phone"], item["message_body"], country_code))
        items.append(item)
    return {
        "campaign": campaign,
        "items": items,
        "total": total,
        "page": page,
        "pages": max(1, (total + per_page - 1) // per_page),
        "per_page": per_page,
        "status_filter": status_filter,
        "whatsapp_enabled": whatsapp_enabled,
    }


class MessageJobManager:
    """Single background dispatcher for bulk SMS campaigns."""

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
            return self._snapshot(list(self._jobs.values())[-1])

    def start(self, app, campaign_id: str) -> tuple[dict[str, Any], bool]:
        with self._lock:
            running = self.current()
            if running:
                return running, False
            job_id = uuid.uuid4().hex[:12]
            job = {
                "id": job_id,
                "campaign_id": campaign_id,
                "status": "running",
                "total": 0,
                "processed": 0,
                "sent": 0,
                "failed": 0,
                "cancelled": 0,
                "simulated": 0,
                "started_at": datetime.now().strftime("%H:%M:%S"),
                "finished_at": None,
                "current_name": "",
                "message": "جارٍ تحضير قائمة المستلمين…",
                "test_mode": False,
                "error": None,
            }
            self._jobs[job_id] = job
            self._current_id = job_id
            if len(self._jobs) > 20:
                for old_id in list(self._jobs)[:-20]:
                    if old_id != self._current_id:
                        self._jobs.pop(old_id, None)
        thread = threading.Thread(
            target=self._run,
            args=(app, job_id, campaign_id),
            name="message-dispatch",
            daemon=True,
        )
        thread.start()
        return self._snapshot(job), True

    def _update(self, job_id: str, **values: Any) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(values)

    def _run(self, app, job_id: str, campaign_id: str) -> None:
        from ..db import connect

        conn = None
        try:
            with app.app_context():
                conn = connect(app.config["DB_PATH"])
                settings = get_settings(conn)
                campaign = _campaign_row(conn, campaign_id)
                if not campaign:
                    self._update(
                        job_id,
                        status="done",
                        finished_at=datetime.now().strftime("%H:%M:%S"),
                        message="حملة الرسائل غير موجودة.",
                    )
                    return
                channel = campaign["channel"]
                if channel not in ("mobile", "cloud"):
                    self._update(
                        job_id,
                        status="done",
                        finished_at=datetime.now().strftime("%H:%M:%S"),
                        message="هذه الحملة لا تُرسل آليًا.",
                    )
                    return
                send_settings = resolve_channel_settings(settings, channel)
                test_mode = settings.get("sms_test_mode", "1") == "1"
                retry_max = int_setting(settings, "sms_retry_max", 3, 1, 10)
                self._update(job_id, test_mode=test_mode)
                with conn:
                    conn.execute(
                        "UPDATE message_campaigns SET status = 'RUNNING' WHERE id = ?",
                        (campaign_id,),
                    )
                rows = conn.execute(
                    """SELECT * FROM message_outbox
                       WHERE campaign_id = ? AND delivery_status = 'PENDING'
                       ORDER BY id""",
                    (campaign_id,),
                ).fetchall()
                self._update(job_id, total=len(rows))
                if not rows:
                    with conn:
                        conn.execute(
                            "UPDATE message_campaigns SET status = 'DONE', finished_at = datetime('now', 'localtime') WHERE id = ?",
                            (campaign_id,),
                        )
                    self._update(
                        job_id,
                        status="done",
                        finished_at=datetime.now().strftime("%H:%M:%S"),
                        message="لا توجد رسائل معلّقة في هذه الحملة.",
                    )
                    return

                counts = {"sent": 0, "failed": 0, "cancelled": 0, "simulated": 0}
                for index, row in enumerate(rows, start=1):
                    self._update(job_id, current_name=row["student_name"])
                    fresh = conn.execute(
                        "SELECT delivery_status FROM message_outbox WHERE id = ?", (row["id"],)
                    ).fetchone()
                    if not fresh or fresh["delivery_status"] != "PENDING":
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
                                "UPDATE message_outbox SET attempts = attempts + 1 WHERE id = ?",
                                (row["id"],),
                            )
                        try:
                            result = send_via_gateway(
                                send_settings, row["parent_phone"], row["message_body"]
                            )
                            success = True
                            simulated = bool(result.get("simulated"))
                            gateway_label = str(result.get("gateway") or "")
                            break
                        except SmsGatewayError as exc:
                            last_error = str(exc)[:MAX_ERROR_LEN]
                            app.logger.warning(
                                "Message attempt %s/%s failed for %s: %s",
                                attempt,
                                retry_max,
                                row["student_nin"],
                                last_error,
                            )
                            if attempt < retry_max:
                                time.sleep(1.2)
                        except Exception as exc:  # pragma: no cover - defensive
                            last_error = f"خطأ غير متوقع: {exc}"[:MAX_ERROR_LEN]
                            app.logger.exception("Unexpected bulk message error")
                            break
                    if success:
                        with conn:
                            conn.execute(
                                """UPDATE message_outbox
                                   SET delivery_status = 'SENT',
                                       sent_at = datetime('now', 'localtime'),
                                       error_msg = ?, gateway = ?, is_test = ?,
                                       updated_at = datetime('now', 'localtime')
                                   WHERE id = ?""",
                                (
                                    "وضع الاختبار: تمت محاكاة الإرسال دون استخدام الشبكة."
                                    if simulated
                                    else None,
                                    gateway_label,
                                    1 if simulated else 0,
                                    row["id"],
                                ),
                            )
                        counts["sent"] += 1
                        if simulated:
                            counts["simulated"] += 1
                    else:
                        with conn:
                            conn.execute(
                                """UPDATE message_outbox
                                   SET delivery_status = 'FAILED', error_msg = ?, gateway = ?,
                                       updated_at = datetime('now', 'localtime')
                                   WHERE id = ?""",
                                (last_error or "فشل غير معروف", gateway_label, row["id"]),
                            )
                        counts["failed"] += 1
                    self._update(job_id, processed=index, **counts)
                with conn:
                    conn.execute(
                        """UPDATE message_campaigns SET status = 'DONE',
                               finished_at = datetime('now', 'localtime')
                           WHERE id = ?""",
                        (campaign_id,),
                    )
                self._update(
                    job_id,
                    status="done",
                    finished_at=datetime.now().strftime("%H:%M:%S"),
                    message=(
                        "اكتملت المعالجة: "
                        f"{counts['sent']} ناجحة، {counts['failed']} فاشلة، "
                        f"{counts['cancelled']} متجاوزة."
                    ),
                    current_name="",
                )
        except Exception as exc:  # pragma: no cover - defensive
            app.logger.exception("Bulk message dispatch job failed")
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
