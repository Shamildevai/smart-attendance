from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import threading
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import segno
from barcode import Code128
from barcode.writer import SVGWriter
from flask import Blueprint, Response, current_app, g, jsonify, request, send_file

from .auth import AccountError, create_user, delete_user, list_users, update_user
from .config import GATEWAY_APP_VERSION
from .db import (
    DEFAULT_PRINT_CONFIG,
    bool_setting,
    get_db,
    get_print_config,
    get_settings,
    int_setting,
    save_settings,
    update_meta,
)
from .importer import commit_import, parse_file, preview_import
from .media import save_image
from .services import attendance_service as attendance
from .services import messaging_service, report_service, sms_service, update_service, whatsapp_service
from .services.sms_service import SmsGatewayError
from .services.update_service import UpdateError
from .utils import (
    STATUS_ORDER,
    ValidationError,
    clean_name,
    full_name,
    is_school_day,
    normalize_nin,
    normalize_phone,
    render_sms_template,
    sms_variables,
    squash,
    today_iso,
    validate_iso_date,
    validate_nin,
    validate_time,
)

api_bp = Blueprint("api", __name__)


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400, code: str = "bad_request", details: Any = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.details = details


def ok(**payload: Any):
    return jsonify({"ok": True, **payload})


def _error(message: str, status: int = 400, code: str = "bad_request", details: Any = None):
    body: dict[str, Any] = {"ok": False, "error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return jsonify(body), status


@api_bp.errorhandler(ValidationError)
def handle_validation_error(error: ValidationError):
    return _error(str(error), 400, "validation")


@api_bp.errorhandler(ApiError)
def handle_api_error(error: ApiError):
    return _error(error.message, error.status, error.code, error.details)


@api_bp.errorhandler(SmsGatewayError)
def handle_sms_gateway_error(error: SmsGatewayError):
    return _error(str(error), 502, "sms_gateway")


@api_bp.errorhandler(sqlite3.IntegrityError)
def handle_integrity_error(error: sqlite3.IntegrityError):
    text = str(error).lower()
    if "unique" in text or "primary" in text:
        return _error("توجد بيانات مكررة تمنع الحفظ (رقم التعريف مستعمل مسبقًا).", 409, "duplicate")
    if "foreign key" in text:
        return _error("لا يمكن تنفيذ العملية لارتباط السجل ببيانات أخرى.", 409, "foreign_key")
    return _error("تعذر حفظ البيانات بسبب قيد في قاعدة البيانات.", 409, "integrity")


def _payload() -> dict[str, Any]:
    data = request.get_json(silent=True)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ApiError("صيغة الطلب غير صالحة.", 400)
    return data


def _as_bool(value: Any, label: str, default: bool | None = None) -> bool:
    if value is None and default is not None:
        return default
    if isinstance(value, bool):
        return value
    if value in (1, "1", "true", "on", "yes"):
        return True
    if value in (0, "0", "false", "off", "no"):
        return False
    raise ApiError(f"قيمة {label} غير صالحة.", 400)


def _as_int(value: Any, label: str, minimum: int, maximum: int, default: int | None = None) -> int:
    if value in (None, "") and default is not None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ApiError(f"{label} يجب أن يكون رقمًا.", 400)
    if number < minimum or number > maximum:
        raise ApiError(f"{label} يجب أن يكون بين {minimum} و{maximum}.", 400)
    return number


def _int_arg(name: str, default: int | None = None) -> int | None:
    raw = request.args.get(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError:
        raise ApiError(f"المعامل {name} غير صالح.", 400)


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _client_is_local() -> bool:
    return request.remote_addr in {"127.0.0.1", "::1", "localhost"}


def _require_local() -> None:
    if not _client_is_local():
        raise ApiError("هذه العملية متاحة من نفس جهاز الخادم فقط.", 403, "forbidden")


def _json_object(raw: str, label: str) -> dict:
    try:
        value = json.loads(raw or "{}")
    except ValueError as exc:
        raise ApiError(f"{label} ليست JSON صالحًا: {exc}", 400)
    if not isinstance(value, dict):
        raise ApiError(f"{label} يجب أن تكون كائن JSON.", 400)
    return value


# ---------------------------------------------------------------------------
# Health / settings / branding
# ---------------------------------------------------------------------------


@api_bp.get("/health")
def health():
    return ok(app="StudentAttendance", version=current_app.config["APP_VERSION"], time=datetime.now().isoformat(timespec="seconds"))


def _public_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    settings = get_settings(conn)
    db_path = Path(current_app.config["DB_PATH"])
    try:
        db_size = db_path.stat().st_size
    except OSError:
        db_size = 0
    return {
        "school_name": settings.get("school_name", ""),
        "academic_year": settings.get("academic_year", ""),
        "header_text": settings.get("header_text", ""),
        "principal_name": settings.get("principal_name", ""),
        "logo_url": settings.get("logo_path", ""),
        "stamp_url": settings.get("stamp_path", ""),
        "sms_template": settings.get("sms_template", ""),
        "sms_gateway": settings.get("sms_gateway", "local"),
        "sms_api_url": settings.get("sms_api_url", ""),
        "sms_api_key": settings.get("sms_api_key", ""),
        "sms_mobile_url": settings.get("sms_mobile_url", ""),
        "sms_mobile_key": settings.get("sms_mobile_key", ""),
        "sms_cloud_url": settings.get("sms_cloud_url", ""),
        "sms_cloud_key": settings.get("sms_cloud_key", ""),
        "sms_headers": settings.get("sms_headers", "{}"),
        "sms_payload_template": settings.get("sms_payload_template", ""),
        "sms_test_mode": bool_setting(settings, "sms_test_mode", True),
        "sms_timeout": int_setting(settings, "sms_timeout", 10, 3, 120),
        "sms_retry_max": int_setting(settings, "sms_retry_max", 3, 1, 10),
        "whatsapp_enabled": bool_setting(settings, "whatsapp_enabled", True),
        "whatsapp_country_code": settings.get("whatsapp_country_code", "213"),
        "whatsapp_provider": settings.get("whatsapp_provider", "manual"),
        "whatsapp_phone_number_id": settings.get("whatsapp_phone_number_id", ""),
        "whatsapp_access_token": settings.get("whatsapp_access_token", ""),
        "whatsapp_template_name": settings.get("whatsapp_template_name", ""),
        "whatsapp_template_lang": settings.get("whatsapp_template_lang", "ar"),
        "whatsapp_api_version": settings.get("whatsapp_api_version", "v23.0"),
        "school_weekdays": [
            int(part) for part in settings.get("school_weekdays", "0,1,2,3,4").split(",")
            if part.strip().isdigit()
        ],
        "auto_sweep": bool_setting(settings, "auto_sweep", True),
        "anti_passback_minutes": int_setting(settings, "anti_passback_minutes", 5, 1, 60),
        "print_config": get_print_config(settings),
        "data_dir": current_app.config["DATA_DIR"],
        "db_size": db_size,
        "version": current_app.config["APP_VERSION"],
        "students_count": conn.execute("SELECT COUNT(*) FROM students").fetchone()[0],
        "active_students": conn.execute("SELECT COUNT(*) FROM students WHERE is_active = 1").fetchone()[0],
        "periods_count": conn.execute("SELECT COUNT(*) FROM periods").fetchone()[0],
        "users_count": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "auto_update": bool_setting(settings, "auto_update", True),
        "update_feed_url": settings.get("update_feed_url", current_app.config["UPDATE_FEED_URL"]),
        "update_last_check": settings.get("update_last_check", ""),
    }


@api_bp.get("/users")
def get_users_api():
    return ok(users=list_users())


@api_bp.post("/users")
def create_user_api():
    data = _payload()
    try:
        user = create_user(
            username=str(data.get("username") or ""),
            display_name=str(data.get("display_name") or ""),
            role=str(data.get("role") or "user"),
            password=str(data.get("password") or "") or None,
            is_active=_as_bool(data.get("is_active"), "حالة الحساب", True),
        )
    except AccountError as error:
        raise ApiError(str(error), 400, "account") from error
    return ok(user=user, message="تم إنشاء المستخدم بنجاح.")


@api_bp.put("/users/<int:user_id>")
def update_user_api(user_id: int):
    data = _payload()
    try:
        user = update_user(
            user_id=user_id,
            display_name=str(data.get("display_name") or ""),
            role=str(data.get("role") or "user"),
            password=str(data.get("password") or "") or None,
            clear_password=_as_bool(data.get("clear_password"), "خيار حذف كلمة المرور", False),
            is_active=_as_bool(data.get("is_active"), "حالة الحساب", True),
            acting_user_id=g.user["id"],
        )
    except AccountError as error:
        raise ApiError(str(error), 400, "account") from error
    return ok(user=user, message="تم تحديث بيانات المستخدم.")


@api_bp.delete("/users/<int:user_id>")
def delete_user_api(user_id: int):
    try:
        delete_user(user_id, g.user["id"])
    except AccountError as error:
        raise ApiError(str(error), 400, "account") from error
    return ok(message="تم حذف المستخدم.")


@api_bp.get("/settings")
def get_settings_api():
    return ok(settings=_public_settings(get_db()))


def _validate_settings_update(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, str]:
    updates: dict[str, str] = {}
    text_limits = {
        "school_name": 160,
        "academic_year": 40,
        "header_text": 600,
        "principal_name": 120,
        "sms_template": 2000,
        "sms_api_url": 500,
        "sms_api_key": 1000,
        "sms_mobile_url": 500,
        "sms_mobile_key": 1000,
        "sms_cloud_url": 500,
        "sms_cloud_key": 1000,
        "whatsapp_phone_number_id": 30,
        "whatsapp_access_token": 1000,
        "whatsapp_template_name": 120,
        "whatsapp_template_lang": 20,
        "whatsapp_api_version": 20,
        "update_feed_url": 500,
    }
    for key, limit in text_limits.items():
        if key in data:
            value = str(data[key] or "").strip() if not key.endswith("_key") else str(data[key] or "")
            if len(value) > limit:
                raise ApiError(f"الحقل {key} أطول من المسموح ({limit} حرف).", 400)
            updates[key] = value
    if "school_name" in updates and not updates["school_name"]:
        raise ApiError("اسم المؤسسة مطلوب.", 400)
    if "sms_template" in updates and not updates["sms_template"]:
        raise ApiError("نص رسالة SMS لا يمكن أن يكون فارغًا.", 400)
    if "sms_gateway" in data:
        if data["sms_gateway"] not in ("local", "cloud"):
            raise ApiError("نوع بوابة SMS غير صالح.", 400)
        updates["sms_gateway"] = data["sms_gateway"]
    for url_key in ("sms_api_url", "sms_mobile_url", "sms_cloud_url"):
        if url_key in updates and updates[url_key]:
            if url_key == "sms_mobile_url" and "://" not in updates[url_key]:
                normalized = sms_service.normalize_mobile_url(updates[url_key])
                if not normalized:
                    raise ApiError("رابط بوابة الهاتف غير صالح.", 400)
                updates[url_key] = normalized
            if not re.match(r"^https?://", updates[url_key], re.IGNORECASE):
                raise ApiError("رابط بوابة SMS يجب أن يبدأ بـ http:// أو https://.", 400)
    if "sms_headers" in data:
        obj = _json_object(str(data.get("sms_headers") or "{}"), "ترويسات HTTP")
        updates["sms_headers"] = json.dumps(obj, ensure_ascii=False, indent=2)
    if "sms_payload_template" in data:
        raw = str(data.get("sms_payload_template") or "").strip()
        if not raw:
            raw = '{"phone": "{{phone}}", "message": "{{message}}"}'
        obj = _json_object(raw, "قالب JSON")
        updates["sms_payload_template"] = json.dumps(obj, ensure_ascii=False, indent=2)
    for key, label in (
        ("sms_test_mode", "وضع الاختبار"),
        ("auto_sweep", "الرصد التلقائي"),
        ("whatsapp_enabled", "إرسال واتساب"),
        ("auto_update", "التحديث التلقائي"),
    ):
        if key in data:
            updates[key] = "1" if _as_bool(data[key], label) else "0"
    if "update_feed_url" in updates:
        feed = updates["update_feed_url"].strip()
        if not re.match(r"^https://", feed, re.IGNORECASE):
            raise ApiError("رابط تحديث التطبيق يجب أن يبدأ بـ https://.", 400)
        updates["update_feed_url"] = feed
    if "whatsapp_country_code" in data:
        code = re.sub(r"\D", "", str(data.get("whatsapp_country_code") or ""))
        if not code or len(code) > 4:
            raise ApiError("رمز الدولة يجب أن يتكون من 1 إلى 4 أرقام.", 400)
        updates["whatsapp_country_code"] = code
    if "whatsapp_provider" in data:
        provider = str(data.get("whatsapp_provider") or "").strip().lower()
        if provider not in ("manual", "cloud"):
            raise ApiError("طريقة إرسال واتساب غير صالحة.", 400)
        updates["whatsapp_provider"] = provider
    if "whatsapp_phone_number_id" in updates:
        phone_id = re.sub(r"\D", "", updates["whatsapp_phone_number_id"])
        if updates["whatsapp_phone_number_id"] and not phone_id:
            raise ApiError("Phone Number ID يجب أن يتكون من أرقام فقط.", 400)
        updates["whatsapp_phone_number_id"] = phone_id
    if "whatsapp_template_name" in updates:
        template_name = updates["whatsapp_template_name"]
        if template_name and not re.fullmatch(r"[a-z0-9_]{1,120}", template_name):
            raise ApiError(
                "اسم قالب واتساب يجب أن يحتوي حروفًا إنجليزية صغيرة وأرقامًا وشرطات سفلية فقط.",
                400,
            )
    if "whatsapp_template_lang" in updates:
        lang = updates["whatsapp_template_lang"] or "ar"
        if not re.fullmatch(r"[A-Za-z]{2,3}(?:_[A-Za-z]{2})?", lang):
            raise ApiError("لغة قالب واتساب غير صالحة (مثال: ar أو en_US).", 400)
        updates["whatsapp_template_lang"] = lang
    if "whatsapp_api_version" in updates:
        version = updates["whatsapp_api_version"] or "v23.0"
        if re.fullmatch(r"\d{1,2}\.\d", version):
            version = f"v{version}"
        if not re.fullmatch(r"v\d{1,2}\.\d", version):
            raise ApiError("إصدار Graph API غير صالح (مثال: v23.0).", 400)
        updates["whatsapp_api_version"] = version
    for key, label, low, high in (
        ("sms_timeout", "مهلة بوابة SMS", 3, 120),
        ("sms_retry_max", "عدد محاولات الإرسال", 1, 10),
        ("anti_passback_minutes", "مدة منع التكرار", 1, 60),
    ):
        if key in data:
            updates[key] = str(_as_int(data[key], label, low, high))
    if "school_weekdays" in data:
        raw_days = data["school_weekdays"]
        if isinstance(raw_days, str):
            raw_days = [part for part in raw_days.split(",") if part.strip()]
        if not isinstance(raw_days, list):
            raise ApiError("أيام الدراسة غير صالحة.", 400)
        days = set()
        for item in raw_days:
            try:
                day = int(item)
            except (TypeError, ValueError):
                raise ApiError("أيام الدراسة غير صالحة.", 400)
            if day < 0 or day > 6:
                raise ApiError("أيام الدراسة غير صالحة.", 400)
            days.add(day)
        updates["school_weekdays"] = ",".join(str(day) for day in sorted(days))
    merged = {**get_settings(conn), **updates}
    if (
        merged.get("sms_test_mode", "1") != "1"
        and not any(
            merged.get(key)
            for key in ("sms_api_url", "sms_mobile_url", "sms_cloud_url")
        )
        and not (
            merged.get("whatsapp_provider") == "cloud"
            and whatsapp_service.cloud_configured(merged)
        )
    ):
        raise ApiError(
            "أدخل رابط بوابة SMS واحدة أو أكمل بيانات Meta WhatsApp Cloud API قبل تعطيل وضع الاختبار.",
            400,
        )
    return updates


@api_bp.put("/settings")
def update_settings_api():
    conn = get_db()
    data = _payload()
    updates = _validate_settings_update(conn, data)
    with conn:
        save_settings(conn, updates)
    return ok(settings=_public_settings(conn), message="تم حفظ الإعدادات بنجاح.")


@api_bp.get("/update/status")
def update_status_api():
    return ok(update=update_service.status_payload())


@api_bp.post("/update/check")
def update_check_api():
    try:
        status = update_service.check_for_update(force=True, raise_errors=True, trigger="manual")
    except UpdateError as error:
        raise ApiError(str(error), 502, "update") from error
    if status["staged"]:
        message = f"تم تنزيل الإصدار {status['latest_version']} والتحقق من توقيعه."
    elif status["update_available"]:
        message = f"يتوفر الإصدار {status['latest_version']}."
    else:
        message = "أنت تستخدم أحدث إصدار متاح."
    return ok(update=status, message=message)


@api_bp.post("/update/install")
def update_install_api():
    try:
        update_service.install_staged(trigger="manual")
    except UpdateError as error:
        raise ApiError(str(error), 400, "update") from error
    return ok(message="سيتم تثبيت التحديث وإعادة تشغيل النظام الآن.")


@api_bp.post("/settings/test-sms")
def test_sms_api():
    conn = get_db()
    data = _payload()
    phone = normalize_phone(data.get("phone"))
    settings = get_settings(conn)
    resolved = messaging_service.resolve_channel_settings(
        settings, settings.get("sms_gateway", "local")
    )
    result = sms_service.test_gateway(resolved, phone)
    return ok(result=result)


@api_bp.post("/settings/test-whatsapp")
def test_whatsapp_api():
    conn = get_db()
    data = _payload()
    phone = normalize_phone(data.get("phone"))
    if not re.fullmatch(r"\+?\d{8,15}", phone or ""):
        raise ApiError("رقم الهاتف التجريبي غير صالح.", 400)
    try:
        result = whatsapp_service.test_cloud(get_settings(conn), phone)
    except whatsapp_service.WhatsAppConfigError as error:
        raise ApiError(str(error), 400, "whatsapp_config") from error
    except whatsapp_service.WhatsAppError as error:
        raise ApiError(str(error), 502, "whatsapp") from error
    return ok(result=result)


@api_bp.post("/uploads/<kind>")
def upload_brand_image(kind: str):
    if kind not in ("logo", "stamp"):
        raise ApiError("نوع الصورة غير معروف.", 404)
    conn = get_db()
    file = request.files.get("file")
    result = save_image(
        file,
        current_app.config["UPLOADS_DIR"],
        "/media/uploads",
        kind,
        max_bytes=3 * 1024 * 1024,
    )
    key = f"{kind}_path"
    old_url = get_settings(conn).get(key, "")
    with conn:
        save_settings(conn, {key: result["url"]})
    if old_url and old_url != result["url"]:
        from .media import safe_delete_media

        safe_delete_media(old_url, current_app.config["UPLOADS_DIR"], "/media/uploads")
    return ok(url=result["url"], message="تم رفع الصورة بنجاح.")


@api_bp.delete("/uploads/<kind>")
def clear_brand_image(kind: str):
    if kind not in ("logo", "stamp"):
        raise ApiError("نوع الصورة غير معروف.", 404)
    from .media import safe_delete_media

    conn = get_db()
    key = f"{kind}_path"
    old_url = get_settings(conn).get(key, "")
    with conn:
        save_settings(conn, {key: ""})
    if old_url:
        safe_delete_media(old_url, current_app.config["UPLOADS_DIR"], "/media/uploads")
    return ok(message="تم حذف الصورة.")


# ---------------------------------------------------------------------------
# Periods
# ---------------------------------------------------------------------------


def _validate_period(conn: sqlite3.Connection, data: dict[str, Any], period_id: int | None = None) -> dict[str, Any]:
    current = None
    if period_id:
        current = conn.execute("SELECT * FROM periods WHERE id = ?", (period_id,)).fetchone()
        if not current:
            raise ApiError("الفترة غير موجودة.", 404, "not_found")
    name = squash(data.get("period_name", current["period_name"] if current else ""))[:80]
    if not name:
        raise ApiError("اسم الفترة مطلوب.", 400)
    start = validate_time(data.get("start_time", current["start_time"] if current else ""), "وقت البداية")
    cutoff = validate_time(data.get("on_time_cutoff", current["on_time_cutoff"] if current else ""), "حد الحضور")
    absent = validate_time(
        data.get("absent_cutoff_time", current["absent_cutoff_time"] if current else ""),
        "حد الغياب",
    )
    from .utils import time_to_seconds

    start_s, cutoff_s, absent_s = time_to_seconds(start), time_to_seconds(cutoff), time_to_seconds(absent)
    if not (start_s <= cutoff_s <= absent_s):
        raise ApiError("يجب أن يكون وقت البداية ≤ حد الحضور ≤ حد الغياب.", 400)
    if absent_s <= start_s:
        raise ApiError("حد الغياب يجب أن يكون بعد وقت البداية.", 400)
    is_active = _as_bool(data.get("is_active", current["is_active"] if current else True), "تفعيل الفترة")
    if current and current["is_active"] and not is_active:
        other_active = conn.execute(
            "SELECT COUNT(*) FROM periods WHERE is_active = 1 AND id <> ?", (period_id,)
        ).fetchone()[0]
        if other_active == 0:
            raise ApiError("يجب الإبقاء على فترة نشطة واحدة على الأقل.", 400)
    duplicate = conn.execute(
        "SELECT id FROM periods WHERE LOWER(period_name) = LOWER(?) AND id <> ?",
        (name, period_id or 0),
    ).fetchone()
    if duplicate:
        raise ApiError("يوجد فترة أخرى بهذا الاسم.", 409, "duplicate")
    if "sort_order" in data:
        sort_order = _as_int(data.get("sort_order"), "الترتيب", 0, 999, 0)
    elif current:
        sort_order = current["sort_order"]
    else:
        sort_order = (conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM periods").fetchone()[0])
    return {
        "period_name": name,
        "start_time": start,
        "on_time_cutoff": cutoff,
        "absent_cutoff_time": absent,
        "is_active": 1 if is_active else 0,
        "sort_order": sort_order,
    }


@api_bp.get("/periods")
def list_periods_api():
    conn = get_db()
    day = request.args.get("date")
    periods = attendance.list_periods(conn, include_inactive=True)
    if day:
        validate_iso_date(day)
        for period in periods:
            closure = attendance.get_closure(conn, period["id"], day)
            period["closure"] = attendance.closure_public(closure)
    return ok(periods=periods)


@api_bp.post("/periods")
def create_period_api():
    conn = get_db()
    values = _validate_period(conn, _payload())
    with conn:
        cursor = conn.execute(
            """INSERT INTO periods
               (period_name, start_time, on_time_cutoff, absent_cutoff_time, is_active, sort_order)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                values["period_name"], values["start_time"], values["on_time_cutoff"],
                values["absent_cutoff_time"], values["is_active"], values["sort_order"],
            ),
        )
        period_id = cursor.lastrowid
    return ok(period=attendance.get_period(conn, period_id), message="تمت إضافة الفترة.")


@api_bp.put("/periods/<int:period_id>")
def update_period_api(period_id: int):
    conn = get_db()
    values = _validate_period(conn, _payload(), period_id=period_id)
    with conn:
        conn.execute(
            """UPDATE periods SET period_name = ?, start_time = ?, on_time_cutoff = ?,
                   absent_cutoff_time = ?, is_active = ?, sort_order = ?,
                   updated_at = datetime('now', 'localtime')
               WHERE id = ?""",
            (
                values["period_name"], values["start_time"], values["on_time_cutoff"],
                values["absent_cutoff_time"], values["is_active"], values["sort_order"],
                period_id,
            ),
        )
    return ok(period=attendance.get_period(conn, period_id), message="تم تحديث الفترة.")


@api_bp.delete("/periods/<int:period_id>")
def delete_period_api(period_id: int):
    conn = get_db()
    period = attendance.get_period(conn, period_id)
    if not period:
        raise ApiError("الفترة غير موجودة.", 404, "not_found")
    references = conn.execute(
        "SELECT COUNT(*) FROM attendance_logs WHERE period_id = ?", (period_id,)
    ).fetchone()[0]
    closures = conn.execute(
        "SELECT COUNT(*) FROM period_closures WHERE period_id = ?", (period_id,)
    ).fetchone()[0]
    if references or closures:
        raise ApiError("لا يمكن حذف فترة لها سجلات حضور. عطّلها بدل الحذف.", 409, "period_in_use")
    with conn:
        conn.execute("DELETE FROM periods WHERE id = ?", (period_id,))
    return ok(message="تم حذف الفترة.")


# ---------------------------------------------------------------------------
# Students
# ---------------------------------------------------------------------------


def _student_dict(row) -> dict[str, Any]:
    data = dict(row)
    data["full_name"] = full_name(data.get("first_name"), data.get("last_name"))
    data["is_active"] = bool(data.get("is_active"))
    data["class_group"] = data.get("class_group") or ""
    return data


def _student_values(data: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
    if current is None:
        nin_raw = data.get("student_nin")
        nin = normalize_nin(nin_raw)
        validate_nin(nin)
    else:
        nin = current["student_nin"]
    first_name = clean_name(data.get("first_name", current["first_name"] if current else ""))[:120]
    last_name = clean_name(data.get("last_name", current["last_name"] if current else ""))[:120]
    if not first_name and not last_name:
        raise ApiError("يجب إدخال الاسم أو اللقب على الأقل.", 400)
    class_group = clean_name(data.get("class_group", current["class_group"] if current else ""))[:80]
    raw_phone = squash(data.get("parent_phone", current["parent_phone"] if current else ""))
    phone = normalize_phone(raw_phone)
    if raw_phone and not re.fullmatch(r"\+?\d{8,15}", phone or ""):
        raise ApiError("رقم هاتف الولي غير صالح.", 400)
    birth_raw = squash(data.get("birth_date", current["birth_date"] if current else ""))
    birth_date = None
    if birth_raw:
        from .utils import normalize_date

        birth_date = normalize_date(birth_raw)
        if not birth_date:
            raise ApiError("تاريخ الميلاد غير صالح. استعمل YYYY-MM-DD.", 400)
        if date.fromisoformat(birth_date) > date.today():
            raise ApiError("تاريخ الميلاد لا يمكن أن يكون في المستقبل.", 400)
    if current is not None and "birth_date" not in data:
        birth_date = current["birth_date"]
    is_active = _as_bool(data.get("is_active", current["is_active"] if current else True), "حالة التلميذ")
    return {
        "student_nin": nin,
        "first_name": first_name,
        "last_name": last_name,
        "birth_date": birth_date,
        "class_group": class_group,
        "parent_phone": phone,
        "is_active": 1 if is_active else 0,
    }


@api_bp.get("/students")
def list_students_api():
    conn = get_db()
    q = squash(request.args.get("q", ""))
    class_filter = request.args.get("class_group", "")
    active = request.args.get("active", "all")
    page = _as_int(request.args.get("page", 1), "الصفحة", 1, 1000000, 1)
    per_page = _as_int(request.args.get("per_page", 20), "حجم الصفحة", 1, 200, 20)
    filters = ["1=1"]
    params: list[Any] = []
    if q:
        like = _like(q)
        filters.append(
            "(s.first_name LIKE ? ESCAPE '\\' OR s.last_name LIKE ? ESCAPE '\\' "
            "OR s.student_nin LIKE ? ESCAPE '\\' OR s.parent_phone LIKE ? ESCAPE '\\')"
        )
        params.extend([like, like, like, like])
    if class_filter == "__none__":
        filters.append("s.class_group = ''")
    elif class_filter:
        filters.append("s.class_group = ?")
        params.append(class_filter)
    if active in ("1", "0"):
        filters.append("s.is_active = ?")
        params.append(int(active))
    where = " AND ".join(filters)
    total = conn.execute(f"SELECT COUNT(*) FROM students s WHERE {where}", params).fetchone()[0]
    rows = conn.execute(
        f"""SELECT s.* FROM students s WHERE {where}
            ORDER BY CASE WHEN s.class_group = '' THEN 1 ELSE 0 END, s.class_group,
                     s.last_name, s.first_name, s.student_nin
            LIMIT ? OFFSET ?""",
        [*params, per_page, (page - 1) * per_page],
    ).fetchall()
    counts = {
        "all": conn.execute("SELECT COUNT(*) FROM students").fetchone()[0],
        "active": conn.execute("SELECT COUNT(*) FROM students WHERE is_active = 1").fetchone()[0],
        "inactive": conn.execute("SELECT COUNT(*) FROM students WHERE is_active = 0").fetchone()[0],
    }
    return ok(
        items=[_student_dict(row) for row in rows],
        total=total,
        page=page,
        per_page=per_page,
        pages=max(1, (total + per_page - 1) // per_page),
        counts=counts,
        classes=attendance.class_options(conn),
    )


@api_bp.get("/students/classes")
def student_classes_api():
    conn = get_db()
    return ok(classes=attendance.class_options(conn))


@api_bp.get("/students/<path:nin>")
def get_student_api(nin: str):
    conn = get_db()
    normalized = normalize_nin(nin)
    row = conn.execute("SELECT * FROM students WHERE student_nin = ?", (normalized,)).fetchone()
    if not row:
        raise ApiError("التلميذ غير موجود.", 404, "not_found")
    day = request.args.get("date") or today_iso()
    day = validate_iso_date(day)
    logs = conn.execute(
        """SELECT a.*, p.period_name FROM attendance_logs a
           JOIN periods p ON p.id = a.period_id
           WHERE a.student_nin = ? AND a.attendance_date = ?
           ORDER BY p.sort_order, p.start_time""",
        (normalized, day),
    ).fetchall()
    return ok(
        student=_student_dict(row),
        day=day,
        today=attendance.get_student_day_stats(conn, normalized, day),
        logs=[attendance.attendance_public(log) | {"period_name": log["period_name"]} for log in logs],
    )


@api_bp.post("/students")
def create_student_api():
    conn = get_db()
    values = _student_values(_payload())
    with conn:
        cursor = conn.execute(
            """INSERT INTO students
               (student_nin, first_name, last_name, birth_date, class_group, parent_phone, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                values["student_nin"], values["first_name"], values["last_name"],
                values["birth_date"], values["class_group"], values["parent_phone"], values["is_active"],
            ),
        )
        student_id = cursor.lastrowid
    row = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
    return ok(student=_student_dict(row), message="تمت إضافة التلميذ.")


@api_bp.put("/students/<path:nin>")
def update_student_api(nin: str):
    conn = get_db()
    normalized = normalize_nin(nin)
    row = conn.execute("SELECT * FROM students WHERE student_nin = ?", (normalized,)).fetchone()
    if not row:
        raise ApiError("التلميذ غير موجود.", 404, "not_found")
    values = _student_values(_payload(), current=dict(row))
    with conn:
        conn.execute(
            """UPDATE students SET first_name = ?, last_name = ?, birth_date = ?,
                   class_group = ?, parent_phone = ?, is_active = ?,
                   updated_at = datetime('now', 'localtime')
               WHERE student_nin = ?""",
            (
                values["first_name"], values["last_name"], values["birth_date"],
                values["class_group"], values["parent_phone"], values["is_active"], normalized,
            ),
        )
    updated = conn.execute("SELECT * FROM students WHERE student_nin = ?", (normalized,)).fetchone()
    return ok(student=_student_dict(updated), message="تم تحديث بيانات التلميذ.")


@api_bp.post("/students/<path:nin>/photo")
def upload_student_photo(nin: str):
    conn = get_db()
    normalized = normalize_nin(nin)
    row = conn.execute("SELECT * FROM students WHERE student_nin = ?", (normalized,)).fetchone()
    if not row:
        raise ApiError("التلميذ غير موجود.", 404, "not_found")
    result = save_image(
        request.files.get("photo"),
        current_app.config["PHOTOS_DIR"],
        "/media/photos",
        "student",
    )
    with conn:
        conn.execute(
            """UPDATE students SET photo_url = ?, updated_at = datetime('now', 'localtime')
               WHERE student_nin = ?""",
            (result["url"], normalized),
        )
    return ok(url=result["url"], message="تم تحديث صورة التلميذ.")


# ---------------------------------------------------------------------------
# Smart import
# ---------------------------------------------------------------------------


@api_bp.post("/import/parse")
def import_parse_api():
    file = request.files.get("file")
    if not file or not file.filename:
        raise ApiError("يرجى اختيار ملف للاستيراد.", 400)
    data = file.read()
    result = parse_file(Path(current_app.config["TMP_DIR"]), file.filename, data)
    return ok(**result)


@api_bp.post("/import/preview")
def import_preview_api():
    data = _payload()
    token = str(data.get("token") or "")
    mapping = data.get("mapping") or {}
    result = preview_import(
        get_db(), Path(current_app.config["TMP_DIR"]), token, mapping
    )
    return ok(**result)


@api_bp.post("/import/commit")
def import_commit_api():
    data = _payload()
    token = str(data.get("token") or "")
    mapping = data.get("mapping") or {}
    result = commit_import(
        get_db(),
        Path(current_app.config["TMP_DIR"]),
        Path(current_app.config["PHOTOS_DIR"]),
        token,
        mapping,
    )
    return ok(**result, message=f"تم استيراد {result['total']} تلميذًا بنجاح.")


# ---------------------------------------------------------------------------
# Dashboard / gate
# ---------------------------------------------------------------------------


@api_bp.get("/dashboard")
def dashboard_api():
    conn = get_db()
    day = request.args.get("date") or today_iso()
    day = validate_iso_date(day)
    if date.fromisoformat(day) > date.today():
        raise ApiError("لا يمكن عرض إحصاءات تاريخ مستقبلي.", 400)
    period_id = _int_arg("period_id")
    result = attendance.dashboard_data(conn, period_id, day)
    settings = get_settings(conn)
    result["day_is_school_day"] = is_school_day(date.fromisoformat(day), settings.get("school_weekdays", "0,1,2,3,4"))
    result["school_name"] = settings.get("school_name", "")
    return ok(**result)


@api_bp.get("/gate/context")
def gate_context_api():
    conn = get_db()
    period_id = _int_arg("period_id")
    settings = get_settings(conn)
    context = attendance.gate_context(
        conn,
        period_id=period_id,
        anti_passback_minutes=int_setting(settings, "anti_passback_minutes", 5, 1, 60),
    )
    context["server_date"] = datetime.now().date().isoformat()
    return ok(**context)


@api_bp.post("/gate/scan")
def gate_scan_api():
    conn = get_db()
    data = _payload()
    raw_code = str(data.get("code") or "").strip()
    if len(raw_code) > 128:
        raise ApiError("الباركود طويل جدًا.", 400)
    period_id = data.get("period_id")
    if period_id in ("", None):
        period_id = None
    else:
        period_id = _as_int(period_id, "الفترة", 1, 10**9)
    settings = get_settings(conn)
    result = attendance.scan_student(
        conn,
        raw_code,
        period_id=period_id,
        anti_passback_minutes=int_setting(settings, "anti_passback_minutes", 5, 1, 60),
    )
    if result["action"] in ("not_found", "preview_card"):
        current_app.logger.info(
            "Gate scan unrecognized: action=%s code=%r lookup=%s",
            result["action"],
            result.get("scanned_code"),
            result.get("lookup"),
        )
    result["server_time"] = datetime.now().strftime("%H:%M:%S")
    return ok(**result)


# ---------------------------------------------------------------------------
# Attendance management
# ---------------------------------------------------------------------------


def _attach_attendance_whatsapp(conn: sqlite3.Connection, result: dict[str, Any]) -> None:
    settings = get_settings(conn)
    enabled = bool_setting(settings, "whatsapp_enabled", True)
    country_code = settings.get("whatsapp_country_code", "213")
    template = settings.get("sms_template", "")
    periods: dict[int, dict[str, Any]] = {}
    for item in result.get("items") or []:
        stored_message = item.pop("sms_message_body", "") or ""
        if not enabled or item.get("status") != "ABSENT" or not item.get("parent_phone"):
            continue
        message = stored_message
        if not message:
            period = periods.get(item["period_id"])
            if period is None:
                row = conn.execute("SELECT * FROM periods WHERE id = ?", (item["period_id"],)).fetchone()
                period = dict(row) if row else {}
                periods[item["period_id"]] = period
            message = render_sms_template(
                template,
                sms_variables(item, period, date.fromisoformat(item["attendance_date"]), settings),
            )
        item.update(whatsapp_service.build_links(item["parent_phone"], message, country_code))


@api_bp.get("/attendance/list")
def attendance_list_api():
    conn = get_db()
    day = validate_iso_date(request.args.get("date") or today_iso())
    period_id = _int_arg("period_id")
    class_group = request.args.get("class_group") or ""
    status = request.args.get("status") or ""
    q = request.args.get("q") or ""
    page = _as_int(request.args.get("page", 1), "الصفحة", 1, 1000000, 1)
    per_page = _as_int(request.args.get("per_page", 25), "حجم الصفحة", 5, 100, 25)
    result = attendance.list_attendance(
        conn,
        day_iso=day,
        period_id=period_id,
        class_group=None if class_group in ("", "__all__") else class_group,
        status=status or None,
        query=q or None,
        page=page,
        per_page=per_page,
    )
    _attach_attendance_whatsapp(conn, result)
    return ok(**result)


@api_bp.get("/attendance/absence-preview")
def absence_preview_api():
    conn = get_db()
    period_id = _int_arg("period_id")
    if not period_id:
        raise ApiError("يرجى تحديد الفترة.", 400)
    day = validate_iso_date(request.args.get("date") or today_iso())
    result = attendance.absence_preview(conn, period_id, day)
    settings = get_settings(conn)
    result["test_mode"] = bool_setting(settings, "sms_test_mode", True)
    result["gateway"] = settings.get("sms_gateway", "local")
    result["sms_api_ready"] = bool(settings.get("sms_api_url", ""))
    return ok(**result)


def _start_sms_job(ids: list[int] | None):
    manager: sms_service.SmsJobManager = current_app.extensions["sms_jobs"]
    app = current_app._get_current_object()
    job, started = manager.start(app, ids=ids)
    return job, started


def _dispatch_response(conn, period_id: int, day: str, approved: bool) -> dict[str, Any]:
    if not approved:
        raise ApiError("يجب الموافقة صراحةً قبل إرسال رسائل SMS.", 400, "approval_required")
    settings = get_settings(conn)
    queued = sms_service.queue_absences(conn, period_id, day, settings)
    job = None
    started = False
    if queued["pending_ids"]:
        job, started = _start_sms_job(queued["pending_ids"])
    return {
        "queue": queued,
        "job": job,
        "job_started": started,
        "test_mode": bool_setting(settings, "sms_test_mode", True),
    }


@api_bp.post("/attendance/close")
def close_period_api():
    conn = get_db()
    data = _payload()
    period_id = _as_int(data.get("period_id"), "الفترة", 1, 10**9)
    day = validate_iso_date(str(data.get("date") or today_iso()))
    send_sms = _as_bool(data.get("send_sms", False), "إرسال الرسائل")
    approved = _as_bool(data.get("approved", False), "الموافقة على الإرسال")
    if send_sms and not approved:
        raise ApiError("فعّل مربع الموافقة قبل إرسال رسائل الغياب.", 400, "approval_required")
    result = attendance.close_period(conn, period_id, day, closed_by="MANUAL")
    response: dict[str, Any] = {"close": result, "sms": None, "sms_error": None}
    if send_sms:
        try:
            response.update(_dispatch_response(conn, period_id, day, approved))
        except (ValidationError, SmsGatewayError, ApiError) as exc:
            response["sms_error"] = str(exc)
    return ok(**response)


@api_bp.post("/attendance/send-absences")
def send_absences_api():
    conn = get_db()
    data = _payload()
    period_id = _as_int(data.get("period_id"), "الفترة", 1, 10**9)
    day = validate_iso_date(str(data.get("date") or today_iso()))
    approved = _as_bool(data.get("approved", False), "الموافقة على الإرسال")
    closure = attendance.get_closure(conn, period_id, day)
    if not closure:
        raise ApiError("يجب إغلاق الفترة أولًا ليتم رصد الغيابات.", 400, "period_not_closed")
    result = _dispatch_response(conn, period_id, day, approved)
    return ok(**result)


@api_bp.post("/attendance/<int:log_id>/excuse")
def excuse_api(log_id: int):
    data = _payload()
    reason = str(data.get("reason") or "")
    result = attendance.excuse_attendance(get_db(), log_id, reason)
    return ok(**result)


# ---------------------------------------------------------------------------
# SMS queue
# ---------------------------------------------------------------------------


@api_bp.get("/sms/queue")
def sms_queue_api():
    conn = get_db()
    status = request.args.get("status") or ""
    day = request.args.get("date") or ""
    if day:
        validate_iso_date(day)
    period_id = _int_arg("period_id")
    q = request.args.get("q") or ""
    page = _as_int(request.args.get("page", 1), "الصفحة", 1, 1000000, 1)
    per_page = _as_int(request.args.get("per_page", 20), "حجم الصفحة", 5, 100, 20)
    result = sms_service.list_sms_queue(
        conn, status_filter=status or None, day_iso=day or None,
        period_id=period_id, query=q or None, page=page, per_page=per_page,
    )
    settings = get_settings(conn)
    manager: sms_service.SmsJobManager = current_app.extensions["sms_jobs"]
    result["test_mode"] = bool_setting(settings, "sms_test_mode", True)
    result["gateway"] = settings.get("sms_gateway", "local")
    result["sms_api_ready"] = bool(settings.get("sms_api_url", ""))
    result["active_job"] = manager.current()
    result["latest_job"] = manager.latest()
    return ok(**result)


@api_bp.post("/sms/dispatch")
def dispatch_sms_api():
    data = _payload()
    approved = _as_bool(data.get("approved", False), "الموافقة على الإرسال")
    if not approved:
        raise ApiError("يجب الموافقة صراحةً قبل إرسال الرسائل.", 400, "approval_required")
    conn = get_db()
    ids = data.get("ids")
    if ids is not None:
        if not isinstance(ids, list) or not ids:
            raise ApiError("قائمة الرسائل غير صالحة.", 400)
        try:
            ids = [int(item) for item in ids]
        except (TypeError, ValueError):
            raise ApiError("قائمة الرسائل غير صالحة.", 400)
    day = str(data.get("date") or "")
    raw_period = data.get("period_id")
    if day:
        validate_iso_date(day)
    period_id = None if raw_period in (None, "") else _as_int(raw_period, "الفترة", 1, 10**9)
    if ids is None and (day or period_id):
        filters = ["delivery_status = 'PENDING'"]
        params: list[Any] = []
        if day:
            filters.append("attendance_date = ?")
            params.append(day)
        if period_id:
            filters.append("period_id = ?")
            params.append(period_id)
        ids = [
            row["id"]
            for row in conn.execute(
                f"SELECT id FROM sms_logs WHERE {' AND '.join(filters)} ORDER BY id", params
            ).fetchall()
        ]
        if not ids:
            raise ApiError("لا توجد رسائل معلّقة مطابقة للفترة أو التاريخ المحدد.", 400, "nothing_to_send")
    job, started = _start_sms_job(ids)
    return ok(job=job, job_started=started, message="بدأ إرسال الرسائل المعلّقة." if started else "يوجد إرسال جارٍ بالفعل.")


@api_bp.post("/sms/<int:sms_id>/retry")
def retry_sms_api(sms_id: int):
    data = _payload()
    approved = _as_bool(data.get("approved", False), "الموافقة على الإرسال")
    if not approved:
        raise ApiError("أكّد الموافقة على إعادة المحاولة.", 400, "approval_required")
    conn = get_db()
    sms_service.reset_failed_sms(conn, sms_id)
    job, started = _start_sms_job([sms_id])
    return ok(job=job, job_started=started, message="تمت جدولة إعادة المحاولة.")


@api_bp.get("/sms/jobs/current")
def current_sms_job_api():
    manager: sms_service.SmsJobManager = current_app.extensions["sms_jobs"]
    return ok(job=manager.current())


@api_bp.get("/sms/jobs/<job_id>")
def sms_job_api(job_id: str):
    manager: sms_service.SmsJobManager = current_app.extensions["sms_jobs"]
    job = manager.get(job_id)
    if not job:
        raise ApiError("مهمة الإرسال غير موجودة.", 404, "not_found")
    return ok(job=job)


# ---------------------------------------------------------------------------
# Bulk messaging center
# ---------------------------------------------------------------------------


def _message_scope(data: dict[str, Any]) -> dict[str, Any]:
    scope = str(data.get("scope") or "all")
    if scope not in ("all", "class", "selected"):
        raise ApiError("نطاق المستلمين غير صالح.", 400)
    raw_nins = data.get("student_nins")
    if raw_nins is not None and not isinstance(raw_nins, list):
        raise ApiError("قائمة التلاميذ المحددين غير صالحة.", 400)
    student_nins = (
        [str(item) for item in raw_nins if str(item or "").strip()][:5000]
        if isinstance(raw_nins, list)
        else []
    )
    return {
        "scope": scope,
        "class_group": str(data.get("class_group") or "").strip() or None,
        "student_nins": student_nins,
        "query": str(data.get("q") or "").strip() or None,
    }


@api_bp.get("/messages/gateway")
def message_gateway_api():
    force = str(request.args.get("force") or "").lower() in ("1", "true", "yes")
    status = messaging_service.gateway_status(get_settings(get_db()), force=force)
    return ok(gateway=status)


@api_bp.post("/messages/gateway/pair")
def pair_gateway_api():
    data = _payload()
    conn = get_db()
    settings = get_settings(conn)
    url = str(data.get("url") or "").strip()
    if not url:
        resolved = messaging_service.resolve_channel_settings(settings, "mobile")
        url = resolved.get("sms_api_url", "")
    try:
        result = sms_service.pair_gateway(
            url,
            data.get("pin"),
            timeout=min(int_setting(settings, "sms_timeout", 10, 3, 120), 15),
        )
    except sms_service.GatewayPairError as error:
        raise ApiError(str(error), 400, "pairing") from error
    updates = {
        "sms_mobile_url": result["endpoint"],
        "sms_mobile_key": result["api_key"],
    }
    if settings.get("sms_gateway", "local") == "local":
        updates["sms_api_url"] = result["endpoint"]
        updates["sms_api_key"] = result["api_key"]
    with conn:
        save_settings(conn, updates)
    status = messaging_service.gateway_status(get_settings(conn), force=True)
    message = (
        f"تم اقتران الهاتف بنجاح (الإصدار {result['version'] or '—'}). "
        "حُفظ مفتاح البوابة تلقائيًا ولا حاجة لنسخه يدويًا."
    )
    if result["outdated"]:
        message += (
            f" يلزم تحديث تطبيق الهاتف إلى {GATEWAY_APP_VERSION} من الرابط أسفل البطاقة."
        )
    return ok(gateway=status, pairing=result, message=message)


@api_bp.get("/messages/students")
def message_students_api():
    conn = get_db()
    settings = get_settings(conn)
    students = messaging_service.fetch_recipients(conn, scope="all")
    country_code = settings.get("whatsapp_country_code", "213")
    sms_ready = 0
    whatsapp_ready = 0
    for student in students:
        phone = normalize_phone(student.get("parent_phone"))
        student["sms_ready"] = bool(phone and re.fullmatch(r"\+?\d{8,15}", phone))
        student["whatsapp_ready"] = bool(
            whatsapp_service.normalize_whatsapp_number(student.get("parent_phone"), country_code)
        )
        if student["sms_ready"]:
            sms_ready += 1
        if student["whatsapp_ready"]:
            whatsapp_ready += 1
    return ok(
        students=students,
        counts={
            "total": len(students),
            "sms_ready": sms_ready,
            "whatsapp_ready": whatsapp_ready,
            "missing_phone": len(students) - sms_ready,
        },
        classes=messaging_service.class_summary(students),
        channels=messaging_service.channels_payload(settings),
        defaults={
            "sms_template": settings.get("sms_template", ""),
            "school_name": settings.get("school_name", ""),
            "academic_year": settings.get("academic_year", ""),
        },
    )


@api_bp.post("/messages/preview")
def preview_message_campaign_api():
    data = _payload()
    scope = _message_scope(data)
    preview = messaging_service.preview_campaign(
        get_db(),
        scope=scope["scope"],
        channel=str(data.get("channel") or "mobile"),
        message=str(data.get("message") or ""),
        class_group=scope["class_group"],
        student_nins=scope["student_nins"],
        query=scope["query"],
    )
    return ok(preview=preview)


@api_bp.post("/messages/send")
def send_message_campaign_api():
    data = _payload()
    approved = _as_bool(data.get("approved", False), "الموافقة على الإرسال")
    scope = _message_scope(data)
    conn = get_db()
    manager: messaging_service.MessageJobManager = current_app.extensions["message_jobs"]
    channel = str(data.get("channel") or "mobile")
    settings = get_settings(conn)
    manual_whatsapp = (
        channel == "whatsapp"
        and settings.get("whatsapp_provider", "manual") != "cloud"
    )
    if not manual_whatsapp and manager.current():
        raise ApiError(
            "هناك حملة رسائل قيد الإرسال. انتظر اكتمالها قبل بدء حملة جديدة.",
            409,
            "busy",
        )
    result = messaging_service.create_campaign(
        conn,
        scope=scope["scope"],
        channel=channel,
        message=str(data.get("message") or ""),
        class_group=scope["class_group"],
        student_nins=scope["student_nins"],
        query=scope["query"],
        approved=approved,
        created_by=g.user["display_name"],
    )
    campaign = result["campaign"]
    response: dict[str, Any] = {
        "campaign": campaign,
        "test_mode": result["test_mode"],
    }
    if campaign["status"] == "PREPARED":
        response["items"] = result["items"]
        response["whatsapp_provider"] = "manual"
        response["message"] = (
            f"تم تجهيز محادثات واتساب. العدد: {len(result['items'])}. راجع كل رسالة وأرسلها يدويًا من واتساب."
        )
    else:
        job, started = manager.start(current_app._get_current_object(), campaign["id"])
        if not started:
            messaging_service.delete_campaign(conn, campaign["id"])
            raise ApiError(
                "هناك حملة رسائل قيد الإرسال. انتظر اكتمالها قبل بدء حملة جديدة.",
                409,
                "busy",
            )
        response["job"] = job
        response["job_started"] = True
        if campaign["channel"] == "whatsapp":
            response["whatsapp_provider"] = "cloud"
            response["message"] = "بدأ إرسال رسائل واتساب عبر Meta Cloud API إلى الأولياء."
        else:
            response["message"] = "بدأ إرسال الرسائل إلى الأولياء."
    return ok(**response)


@api_bp.get("/messages/campaigns")
def message_campaigns_api():
    page = _as_int(request.args.get("page", 1), "الصفحة", 1, 1000000, 1)
    per_page = _as_int(request.args.get("per_page", 10), "حجم الصفحة", 5, 50, 10)
    return ok(**messaging_service.list_campaigns(get_db(), page=page, per_page=per_page))


@api_bp.get("/messages/campaigns/<campaign_id>")
def message_campaign_api(campaign_id: str):
    page = _as_int(request.args.get("page", 1), "الصفحة", 1, 1000000, 1)
    per_page = _as_int(request.args.get("per_page", 25), "حجم الصفحة", 5, 100, 25)
    status = request.args.get("status") or ""
    return ok(
        **messaging_service.campaign_detail(
            get_db(), campaign_id, page=page, per_page=per_page, status_filter=status
        )
    )


@api_bp.get("/messages/jobs/current")
def current_message_job_api():
    manager: messaging_service.MessageJobManager = current_app.extensions["message_jobs"]
    return ok(job=manager.current())


@api_bp.get("/messages/jobs/<job_id>")
def message_job_api(job_id: str):
    manager: messaging_service.MessageJobManager = current_app.extensions["message_jobs"]
    job = manager.get(job_id)
    if not job:
        raise ApiError("مهمة الإرسال غير موجودة.", 404, "not_found")
    return ok(job=job)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def _report_filters() -> tuple[str | None, str | None]:
    class_group = request.args.get("class_group") or None
    status = request.args.get("status") or None
    return class_group, status


@api_bp.get("/reports/daily")
def daily_report_api():
    conn = get_db()
    day = validate_iso_date(request.args.get("date") or today_iso())
    period_id = _int_arg("period_id")
    class_group, status = _report_filters()
    report = report_service.daily_report(conn, day, period_id, class_group, status)
    report["periods"] = attendance.list_periods(conn, include_inactive=True)
    report["classes"] = attendance.class_options(conn)
    return ok(**report)


@api_bp.get("/reports/monthly")
def monthly_report_api():
    conn = get_db()
    month = request.args.get("month") or datetime.now().strftime("%Y-%m")
    period_id = _int_arg("period_id")
    class_group, _ = _report_filters()
    report = report_service.monthly_report(conn, month, period_id, class_group)
    report["periods"] = attendance.list_periods(conn, include_inactive=True)
    report["classes"] = attendance.class_options(conn)
    return ok(**report)


@api_bp.get("/reports/daily.xlsx")
def daily_report_excel():
    conn = get_db()
    day = validate_iso_date(request.args.get("date") or today_iso())
    period_id = _int_arg("period_id")
    class_group, status = _report_filters()
    report = report_service.daily_report(conn, day, period_id, class_group, status)
    data = report_service.daily_workbook(report)
    filename = f"تقرير_الحضور_اليومي_{day}.xlsx"
    return send_file(
        BytesIO(data), as_attachment=True, download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@api_bp.get("/reports/monthly.xlsx")
def monthly_report_excel():
    conn = get_db()
    month = request.args.get("month") or datetime.now().strftime("%Y-%m")
    period_id = _int_arg("period_id")
    class_group, _ = _report_filters()
    report = report_service.monthly_report(conn, month, period_id, class_group)
    data = report_service.monthly_workbook(report)
    filename = f"تقرير_الغياب_الشهري_{month}.xlsx"
    return send_file(
        BytesIO(data), as_attachment=True, download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ---------------------------------------------------------------------------
# Print studio helpers
# ---------------------------------------------------------------------------


@api_bp.put("/print/config")
def update_print_config_api():
    conn = get_db()
    data = _payload()
    current = get_print_config(get_settings(conn))
    allowed = set(DEFAULT_PRINT_CONFIG)
    unknown = set(data) - allowed
    if unknown:
        raise ApiError("إعدادات طباعة غير معروفة.", 400)
    config = dict(current)
    for key, value in data.items():
        if key in ("accent_color", "header_color"):
            if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
                raise ApiError("اللون يجب أن يكون بصيغة #RRGGBB.", 400)
            config[key] = value
        elif key.startswith("show_"):
            config[key] = _as_bool(value, key)
        elif key == "card_layout":
            config[key] = _as_int(value, "توزيع البطاقات", 8, 10)
            if config[key] not in (8, 10):
                raise ApiError("عدد البطاقات يجب أن يكون 8 أو 10.", 400)
        elif key == "sticker_layout":
            config[key] = _as_int(value, "توزيع الملصقات", 24, 30)
            if config[key] not in (24, 30):
                raise ApiError("عدد الملصقات يجب أن يكون 24 أو 30.", 400)
        elif key == "border_radius":
            config[key] = _as_int(value, "انحناء الزوايا", 0, 6)
    with conn:
        save_settings(conn, {"print_config": json.dumps(config, ensure_ascii=False)})
    return ok(print_config=config, message="تم حفظ إعدادات التصميم.")


@api_bp.get("/barcode/<path:nin>")
def barcode_svg(nin: str):
    code = normalize_nin(nin)
    validate_nin(code)
    buffer = BytesIO()
    try:
        product = Code128(code, writer=SVGWriter())
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
    except Exception as exc:
        raise ApiError("تعذر توليد الباركود لهذا الرقم.", 400) from exc
    svg = buffer.getvalue().decode("utf-8")
    svg = re.sub(r"<\?xml.*?\?>", "", svg, flags=re.DOTALL)
    svg = re.sub(r"<!DOCTYPE.*?>", "", svg, flags=re.DOTALL)
    svg = svg.replace("<svg ", '<svg preserveAspectRatio="none" ', 1)
    response = Response(svg, mimetype="image/svg+xml")
    response.headers["Cache-Control"] = "public, max-age=604800"
    return response


@api_bp.get("/qr/<path:nin>")
def qr_svg(nin: str):
    code = normalize_nin(nin)
    validate_nin(code)
    buffer = BytesIO()
    try:
        qr = segno.make(code, error="m")
        qr.save(buffer, kind="svg", scale=10, border=2, dark="#000000", light="#ffffff")
    except Exception as exc:
        raise ApiError("تعذر توليد رمز QR.", 400) from exc
    svg = buffer.getvalue().decode("utf-8")
    svg = re.sub(r"<\?xml.*?\?>", "", svg, flags=re.DOTALL)
    response = Response(svg, mimetype="image/svg+xml")
    response.headers["Cache-Control"] = "public, max-age=604800"
    return response


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------


@api_bp.get("/system/backup")
def backup_database():
    _require_local()
    conn = get_db()
    fd, temp_name = tempfile.mkstemp(prefix="attendance-backup-", suffix=".db", dir=current_app.config["TMP_DIR"])
    os.close(fd)
    try:
        destination = sqlite3.connect(temp_name)
        try:
            conn.backup(destination)
        finally:
            destination.close()
        data = Path(temp_name).read_bytes()
    finally:
        try:
            Path(temp_name).unlink(missing_ok=True)
        except OSError:
            pass
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    return send_file(
        BytesIO(data),
        as_attachment=True,
        download_name=f"attendance_backup_{stamp}.db",
        mimetype="application/octet-stream",
    )


@api_bp.post("/system/shutdown")
def shutdown_api():
    _require_local()
    callback = current_app.extensions.get("shutdown_callback")
    if not callable(callback):
        raise ApiError("إغلاق الخادم غير متاح في وضع التشغيل الحالي.", 409, "shutdown_unavailable")
    threading.Timer(0.4, callback).start()
    return ok(message="سيتم إغلاق النظام الآن. يمكنك إغلاق هذه النافذة.")
