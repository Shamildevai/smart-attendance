"""Phone scanner API for the free edition.

A phone pairs once with an 8-digit connect code shown in the PC settings, keeps
its own copy of the roster, scans offline, then pushes the queue automatically
when the PC is reachable. Every queued scan carries a per-device client id, so a
retry is never recorded twice, and the original scan timestamp decides the
attendance date, period and PRESENT/LATE status.

The API is deliberately scan-only: no student records are exposed beyond the
roster needed to recognise a card, and access is limited to the local network by
the application's request guard.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta

from flask import Blueprint, current_app, g, jsonify, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import auth
from .config import APP_VERSION
from .db import get_db, get_settings, int_setting
from .services import attendance_service as attendance
from .utils import ValidationError

mobile_bp = Blueprint("mobile", __name__)

MOBILE_API_VERSION = 1
TOKEN_SALT = "student-attendance-mobile-token-v1"
CONNECT_CODE_SALT = "student-attendance-connect-v1"
TOKEN_MAX_AGE = 365 * 24 * 60 * 60
MAX_PAGE_SIZE = 100
MAX_SYNC_SCANS = 300
MAX_OFFLINE_BACKLOG_DAYS = 60
CLOCK_SKEW_SECONDS = 120
MAX_MOBILE_PAYLOAD = 256 * 1024
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")
CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
CONNECT_CODE_RE = re.compile(r"[0-9]{8}")
DIGITS_TABLE = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

FEATURES = ["offline_roster", "offline_scans", "code_pairing"]
PUBLIC_ENDPOINTS = {"mobile.ping", "mobile.hello", "mobile.login_code", "mobile.device_session"}


def error(message: str, status: int = 400, code: str = "bad_request"):
    return jsonify(ok=False, error={"code": code, "message": message}), status


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _digits(value) -> str:
    return value.translate(DIGITS_TABLE) if isinstance(value, str) else ""


def _secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def connect_code_hash(code: str) -> str:
    """Keyed hash so the database alone never reveals the connect code."""
    key = str(current_app.config["SECRET_KEY"]).encode("utf-8")
    return hmac.new(key, f"connect:{code}".encode("utf-8"), hashlib.sha256).hexdigest()


def new_connect_code() -> str:
    """Eight digits, never leading with zero, easy to read aloud."""
    return str(secrets.randbelow(90_000_000) + 10_000_000)


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=TOKEN_SALT)


def _issue_token(row) -> str:
    return _serializer().dumps(
        {"device_id": row["device_id"], "sig": row["secret_hash"][:16], "scope": "scanner"}
    )


def _client_ip() -> str:
    return (request.remote_addr or "local")[:64]


def _device_row(conn: sqlite3.Connection, device_id: str):
    return conn.execute(
        "SELECT * FROM mobile_devices WHERE device_id = ?", (device_id,)
    ).fetchone()


def _device_public(row) -> dict:
    return {
        "id": row["device_id"],
        "name": row["name"] or "هاتف",
        "platform": row["platform"] or "",
        "app_version": row["app_version"] or "",
        "status": row["status"] or "pending",
        "approved_at": row["approved_at"],
        "last_seen": row["last_seen"],
        "last_sync": row["last_sync"],
    }


def _matches(value, expected: str) -> bool:
    return isinstance(value, str) and hmac.compare_digest(
        value.encode("utf-8"), expected.encode("utf-8")
    )


def _bearer_device():
    """Returns (row, error_response) for the device behind the bearer token."""
    header = request.headers.get("Authorization", "")
    token = header[7:].strip() if header[:7].lower() == "bearer " else ""
    if not token:
        return None, error("سجّل الهاتف من جديد. رمز الجلسة مفقود.", 401, "auth_required")
    try:
        data = _serializer().loads(token, max_age=TOKEN_MAX_AGE)
    except SignatureExpired:
        return None, error("انتهت صلاحية جلسة الهاتف. أعد الربط من الإعدادات.", 401, "auth_required")
    except BadSignature:
        return None, error("رمز جلسة الهاتف غير صالح. أعد الربط من الإعدادات.", 401, "auth_required")
    if not isinstance(data, dict) or data.get("scope") != "scanner":
        return None, error("رمز جلسة الهاتف غير صالح.", 401, "auth_required")
    device_id = str(data.get("device_id") or "")
    if not DEVICE_ID_RE.fullmatch(device_id):
        return None, error("معرّف الجهاز غير صالح.", 401, "invalid_device")
    conn = get_db()
    row = _device_row(conn, device_id)
    if row is None or not row["secret_hash"] or not _matches(
        data.get("sig"), row["secret_hash"][:16]
    ):
        return None, error(
            "هذا الهاتف غير معروف لهذا الحاسوب. أعد الربط من الإعدادات.",
            401,
            "device_unknown",
        )
    status = row["status"] or "pending"
    if status == "blocked":
        return None, error("هذا الهاتف محظور. راجع مدير النظام في الحاسوب.", 403, "device_blocked")
    if status != "approved":
        body = {
            "ok": False,
            "error": {
                "code": "device_pending",
                "message": "بانتظار موافقة الحاسوب على اقتران هذا الهاتف.",
            },
            "device": _device_public(row),
        }
        return None, (jsonify(body), 403)
    return row, None


@mobile_bp.before_request
def authenticate():
    request.max_content_length = min(
        current_app.config.get("MAX_CONTENT_LENGTH") or MAX_MOBILE_PAYLOAD,
        MAX_MOBILE_PAYLOAD,
    )
    if request.content_length and request.content_length > request.max_content_length:
        return error("الطلب أكبر من المسموح.", 413, "payload_too_large")
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    row, denied = _bearer_device()
    if denied:
        return denied
    g.mobile_device = row
    return None


@mobile_bp.errorhandler(ValidationError)
def invalid_input(problem):
    return error(str(problem), getattr(problem, "status", 400), getattr(problem, "code", "invalid_input"))


@mobile_bp.after_request
def no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


def _server_info(settings: dict) -> dict:
    return {
        "app": "StudentAttendance",
        "version": APP_VERSION,
        "mobile_api": MOBILE_API_VERSION,
        "school_name": settings.get("school_name", ""),
        "academic_year": settings.get("academic_year", ""),
        "server_date": date.today().isoformat(),
        "server_time": datetime.now().strftime("%H:%M:%S"),
    }


def _session_response(conn, row, secret: str = ""):
    settings = get_settings(conn)
    return jsonify(
        ok=True,
        token=_issue_token(row),
        expires_in=TOKEN_MAX_AGE,
        user=_device_public(row),
        server=_server_info(settings),
        features=FEATURES,
        device=_device_public(row),
        device_secret=secret,
        approval_required=False,
    )


@mobile_bp.get("/ping")
def ping():
    settings = get_settings(get_db())
    return jsonify(ok=True, **_server_info(settings))


@mobile_bp.post("/hello")
def hello():
    settings = get_settings(get_db())
    return jsonify(ok=True, **_server_info(settings), features=FEATURES)


@mobile_bp.post("/login-code")
def login_code():
    """First-time pairing with the connect code shown in the PC settings."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error("أرسل بيانات الهاتف بصيغة JSON.", 400, "invalid_request")
    device_id = str(data.get("device_id") or "").strip()
    if not DEVICE_ID_RE.fullmatch(device_id):
        return error("أرسل معرّف الجهاز أولًا.", 400, "invalid_device")
    code = _digits(data.get("passcode"))

    key = f"connect|{_client_ip()}"
    remaining = auth._attempt_remaining(key)
    if remaining:
        return error(
            f"محاولات كثيرة. انتظر {remaining} ثانية ثم أدخل الرمز مجددًا.",
            429,
            "too_many_attempts",
        )
    if not CONNECT_CODE_RE.fullmatch(code):
        auth._record_failure(key)
        return error(
            "رمز الدخول غير صحيح. اطلب رمزًا جديدًا من شاشة الإعدادات.",
            401,
            "invalid_passcode",
        )

    conn = get_db()
    code_row = conn.execute(
        "SELECT * FROM scan_connect_codes WHERE code_hash = ? AND revoked_at IS NULL",
        (connect_code_hash(code),),
    ).fetchone()
    if code_row is None:
        auth._record_failure(key)
        return error(
            "رمز الدخول غير صحيح أو أُبطل. اطلب رمزًا جديدًا من شاشة الإعدادات.",
            401,
            "invalid_passcode",
        )
    existing = _device_row(conn, device_id)
    if existing is not None and (existing["status"] or "pending") == "blocked":
        return error("هذا الهاتف محظور. راجع مدير النظام في الحاسوب.", 403, "device_blocked")

    secret = secrets.token_urlsafe(32)
    now = _now()
    name = str(data.get("device_name") or "").strip()[:80]
    platform = str(data.get("platform") or "").strip()[:20]
    app_version = str(data.get("app_version") or "").strip()[:20]
    with conn:
        conn.execute(
            """INSERT INTO mobile_devices
                   (device_id, name, platform, app_version, created_at, last_seen,
                    status, secret_hash, approved_at, last_ip)
               VALUES (?, ?, ?, ?, ?, ?, 'approved', ?, ?, ?)
               ON CONFLICT(device_id) DO UPDATE SET
                   name = excluded.name,
                   platform = excluded.platform,
                   app_version = excluded.app_version,
                   last_seen = excluded.last_seen,
                   status = 'approved',
                   secret_hash = excluded.secret_hash,
                   approved_at = excluded.approved_at,
                   last_ip = excluded.last_ip""",
            (device_id, name, platform, app_version, now, now, _secret_hash(secret), now, _client_ip()),
        )
        conn.execute(
            "UPDATE scan_connect_codes SET last_used_at = ? WHERE id = ?",
            (now, code_row["id"]),
        )
    auth._clear_failures(key)
    return _session_response(conn, _device_row(conn, device_id), secret)


@mobile_bp.post("/devices/session")
def device_session():
    """Password-free reconnect for an already paired phone."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error("أرسل بيانات الهاتف بصيغة JSON.", 400, "invalid_request")
    device_id = str(data.get("device_id") or "").strip()
    secret = str(data.get("secret") or "")
    if not DEVICE_ID_RE.fullmatch(device_id) or not secret or len(secret) > 128:
        return error("بيانات الهاتف غير صالحة.", 401, "device_unknown")
    conn = get_db()
    row = _device_row(conn, device_id)
    if (
        row is None
        or not row["secret_hash"]
        or not hmac.compare_digest(_secret_hash(secret), row["secret_hash"])
    ):
        return error(
            "هذا الهاتف غير معروف لهذا الحاسوب. أعد الربط برمز الدخول من الإعدادات.",
            401,
            "device_unknown",
        )
    status = row["status"] or "pending"
    if status == "blocked":
        return error("هذا الهاتف محظور. راجع مدير النظام في الحاسوب.", 403, "device_blocked")
    if status != "approved":
        body = {
            "ok": False,
            "error": {
                "code": "device_pending",
                "message": "بانتظار موافقة الحاسوب على اقتران هذا الهاتف.",
            },
            "device": _device_public(row),
        }
        return jsonify(body), 403
    now = _now()
    app_version = str(data.get("app_version") or "").strip()[:20]
    with conn:
        conn.execute(
            """UPDATE mobile_devices SET last_seen = ?, last_ip = ?, app_version = ?
               WHERE device_id = ? AND status = 'approved' AND secret_hash = ?""",
            (now, _client_ip(), app_version or row["app_version"], device_id, row["secret_hash"]),
        )
    return _session_response(conn, _device_row(conn, device_id))


def _roster_version(students: list[dict], periods: list[dict]) -> str:
    raw = json.dumps(
        {"students": students, "periods": periods},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@mobile_bp.get("/offline/roster")
def offline_roster():
    """Snapshot the phone keeps so scanning works without a connection."""
    device_id = (request.args.get("device_id") or "").strip()
    if device_id and device_id != g.mobile_device["device_id"]:
        return error("الجلسة مرتبطة بهاتف آخر.", 403, "device_mismatch")
    conn = get_db()
    settings = get_settings(conn)
    rows = conn.execute(
        """SELECT student_nin, first_name, last_name, class_group, is_active
           FROM students ORDER BY class_group, last_name, first_name"""
    ).fetchall()
    students = [
        {
            "student_nin": row["student_nin"],
            "first_name": row["first_name"],
            "last_name": row["last_name"],
            "full_name": f"{row['first_name']} {row['last_name']}".strip(),
            "class_group": row["class_group"] or "",
            "is_active": bool(row["is_active"]),
        }
        for row in rows
    ]
    periods = attendance.list_periods(conn, include_inactive=False)
    version = _roster_version(students, periods)
    since = (request.args.get("since") or "").strip()
    now = datetime.now()
    if since and _matches(since, version):
        return jsonify(
            ok=True,
            version=version,
            unchanged=True,
            server_date=now.date().isoformat(),
            server_time=now.strftime("%H:%M:%S"),
        )
    return jsonify(
        ok=True,
        version=version,
        unchanged=False,
        school_name=settings.get("school_name", ""),
        academic_year=settings.get("academic_year", ""),
        server_date=now.date().isoformat(),
        server_time=now.strftime("%H:%M:%S"),
        counts={
            "students": len(students),
            "classes": len({item["class_group"] for item in students if item["class_group"]}),
        },
        periods=[
            {
                "id": period["id"],
                "name": period["period_name"],
                "period_type": period["period_type"],
                "start_time": period["start_time"],
                "on_time_cutoff": period["on_time_cutoff"],
                "absent_cutoff_time": period["absent_cutoff_time"],
                "sort_order": period.get("sort_order", 0),
            }
            for period in periods
        ],
        students=students,
    )


def _parse_scan_time(raw) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise ValidationError("وقت المسح مفقود.")
    value = raw.strip()
    if len(value) > 40:
        raise ValidationError("وقت المسح غير صالح.")
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValidationError("وقت المسح غير صالح.") from None
    if at.tzinfo is not None:
        at = at.astimezone().replace(tzinfo=None)
    now = datetime.now()
    if at > now + timedelta(seconds=CLOCK_SKEW_SECONDS):
        at = now
    if at < now - timedelta(days=MAX_OFFLINE_BACKLOG_DAYS):
        raise ValidationError("وقت المسح أقدم من الحد المسموح للمزامنة.")
    return at.replace(microsecond=0)


def _student_brief(student: dict | None) -> dict | None:
    if not student:
        return None
    return {
        "student_nin": student.get("student_nin", ""),
        "full_name": student.get("full_name", ""),
        "class_group": student.get("class_group", ""),
    }


def _scan_result(client_id: str, result: dict, at: datetime) -> dict:
    action = result.get("action") or "invalid"
    student = result.get("student") or None
    period = result.get("period") or None
    return {
        "client_id": client_id,
        "ok": action not in ("not_found", "preview_card", "inactive", "invalid"),
        "status": action,
        "action": action,
        "flash": result.get("flash"),
        "message": result.get("message") or "",
        "scan_time": at.isoformat(timespec="seconds"),
        "attendance_date": at.date().isoformat(),
        "student": _student_brief(student),
        "period": {"id": period["id"], "name": period["period_name"]} if period else None,
        "attendance": result.get("attendance"),
    }


def _cached_scan_result(conn, row) -> dict:
    student = conn.execute(
        "SELECT student_nin, first_name, last_name, class_group FROM students WHERE student_nin = ?",
        (row["student_nin"],),
    ).fetchone() if row["student_nin"] else None
    period = attendance.get_period(conn, row["period_id"]) if row["period_id"] else None
    return {
        "client_id": row["client_id"],
        "ok": row["action"] not in ("not_found", "preview_card", "inactive", "invalid"),
        "status": "duplicate",
        "action": "duplicate",
        "flash": "warning",
        "message": "سبق استلام هذه القراءة من هذا الهاتف.",
        "scan_time": row["scanned_at"],
        "attendance_date": row["attendance_date"],
        "student": _student_brief(
            {
                "student_nin": student["student_nin"],
                "full_name": f"{student['first_name']} {student['last_name']}".strip(),
                "class_group": student["class_group"] or "",
            }
        ) if student else None,
        "period": {"id": period["id"], "name": period["period_name"]} if period else None,
        "attendance": None,
    }


@mobile_bp.post("/scans/sync")
def sync_scans():
    """Accept a phone's offline queue; each client id is applied at most once."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error("أرسل القراءات بصيغة JSON.", 400, "invalid_request")
    device_id = str(data.get("device_id") or "").strip()
    if device_id != g.mobile_device["device_id"]:
        return error("الجلسة مرتبطة بهاتف آخر.", 403, "device_mismatch")
    scans = data.get("scans")
    if not isinstance(scans, list) or not scans:
        return error("لا توجد قراءات للمزامنة.", 400, "empty_batch")
    if len(scans) > MAX_SYNC_SCANS:
        return error(f"الحد الأقصى {MAX_SYNC_SCANS} قراءة في الدفعة.", 413, "batch_too_large")

    device_name = str(data.get("device_name") or "").strip()[:80]
    platform = str(data.get("platform") or "").strip()[:20]
    app_version = str(data.get("app_version") or "").strip()[:20]

    conn = get_db()
    settings = get_settings(conn)
    anti_passback = int_setting(settings, "anti_passback_minutes", 5, 1, 60)
    school_weekdays = settings.get("school_weekdays", "0,1,2,3,4")
    now = datetime.now()
    now_iso = now.isoformat(timespec="seconds")
    results: list[dict] = []
    processed = duplicates = rejected = 0

    for item in scans:
        if not isinstance(item, dict):
            rejected += 1
            results.append(
                {
                    "client_id": "",
                    "ok": False,
                    "status": "invalid",
                    "action": "invalid",
                    "message": "قراءة غير صالحة.",
                }
            )
            continue
        client_id = str(item.get("client_id") or "").strip()
        if not CLIENT_ID_RE.fullmatch(client_id):
            rejected += 1
            results.append(
                {
                    "client_id": client_id[:64],
                    "ok": False,
                    "status": "invalid",
                    "action": "invalid",
                    "message": "معرّف القراءة غير صالح.",
                }
            )
            continue
        try:
            code = str(item.get("code") or "").strip()
            if not code or len(code) > 128:
                raise ValidationError("الباركود فارغ أو طويل جدًا.")
            at = _parse_scan_time(item.get("at"))
        except ValidationError as problem:
            rejected += 1
            results.append(
                {
                    "client_id": client_id,
                    "ok": False,
                    "status": "invalid",
                    "action": "invalid",
                    "message": str(problem),
                }
            )
            continue

        cached = conn.execute(
            "SELECT * FROM mobile_scans WHERE device_id = ? AND client_id = ?",
            (device_id, client_id),
        ).fetchone()
        if cached:
            duplicates += 1
            results.append(_cached_scan_result(conn, cached))
            continue

        try:
            scan = attendance.scan_student(
                conn,
                code,
                period_id=None,
                now=at,
                anti_passback_minutes=anti_passback,
                school_weekdays=school_weekdays,
            )
        except ValidationError as problem:
            rejected += 1
            results.append(
                {
                    "client_id": client_id,
                    "ok": False,
                    "status": "invalid",
                    "action": "invalid",
                    "message": str(problem),
                }
            )
            continue

        student = scan.get("student") or None
        period = scan.get("period") or None
        recorded = scan.get("attendance") or None
        try:
            with conn:
                conn.execute(
                    """INSERT INTO mobile_scans
                       (device_id, client_id, code, scanned_at, received_at, action,
                        student_nin, period_id, attendance_date)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        device_id,
                        client_id,
                        code,
                        at.isoformat(timespec="seconds"),
                        now_iso,
                        scan.get("action") or "invalid",
                        (student or {}).get("student_nin"),
                        (period or {}).get("id"),
                        at.date().isoformat(),
                    ),
                )
                if recorded and scan.get("action") in ("present", "late", "updated_from_absent"):
                    conn.execute(
                        "UPDATE attendance_logs SET source = 'MOBILE' WHERE id = ?",
                        (recorded["id"],),
                    )
                    recorded["source"] = "MOBILE"
        except sqlite3.IntegrityError:  # a retry raced this request
            cached = conn.execute(
                "SELECT * FROM mobile_scans WHERE device_id = ? AND client_id = ?",
                (device_id, client_id),
            ).fetchone()
            duplicates += 1
            results.append(_cached_scan_result(conn, cached) if cached else {
                "client_id": client_id,
                "ok": False,
                "status": "invalid",
                "action": "invalid",
                "message": "تعذر حفظ القراءة.",
            })
            continue

        processed += 1
        results.append(_scan_result(client_id, scan, at))

    with conn:
        conn.execute(
            """UPDATE mobile_devices
               SET name = COALESCE(NULLIF(?, ''), name),
                   platform = COALESCE(NULLIF(?, ''), platform),
                   app_version = COALESCE(NULLIF(?, ''), app_version),
                   last_seen = ?, last_sync = ?
               WHERE device_id = ?""",
            (device_name, platform, app_version, now_iso, now_iso, device_id),
        )

    return jsonify(
        ok=True,
        device_id=device_id,
        server_date=now.date().isoformat(),
        server_time=now.strftime("%H:%M:%S"),
        summary={
            "received": len(scans),
            "processed": processed,
            "duplicates": duplicates,
            "rejected": rejected,
        },
        results=results,
    )
