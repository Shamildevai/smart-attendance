from __future__ import annotations

import hmac
import re
import secrets
import threading
import time
from datetime import datetime
from functools import wraps
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    current_app,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from .config import ROLE_LABELS
from .db import get_db, get_settings

auth_bp = Blueprint("auth", __name__)

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
ROLES = {"admin", "user"}
PASSWORD_MIN = 6
PASSWORD_MAX = 128

PUBLIC_ENDPOINTS = {
    "auth.login_page",
    "auth.login_submit",
    "views.set_language",
    "views.i18n_script",
    "views.favicon",
    "views.media_upload",
    "api.health",
}

ADMIN_API_PREFIXES = ("/api/settings", "/api/uploads", "/api/users")
ADMIN_API_PATHS = {
    "/api/system/backup",
    "/api/system/shutdown",
    "/api/update/check",
    "/api/update/install",
}

_ATTEMPT_LOCK = threading.Lock()
_ATTEMPTS: dict[str, list[float]] = {}
_ATTEMPT_WINDOW = 300
_ATTEMPT_LIMIT = 5


class AccountError(ValueError):
    pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def safe_next(value: str | None, fallback: str = "/") -> str:
    candidate = (value or "").strip()
    if not candidate or "\\" in candidate or any(ord(ch) < 32 for ch in candidate):
        return fallback
    if not candidate.startswith("/") or candidate.startswith("//"):
        return fallback
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc:
        return fallback
    return candidate


def get_user(user_id: int | None) -> dict | None:
    if not user_id:
        return None
    row = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_username(username: str) -> dict | None:
    row = get_db().execute(
        "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
    ).fetchone()
    return dict(row) if row else None


def _public_user(row: dict) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
        "role_label": ROLE_LABELS.get(row["role"], row["role"]),
        "is_active": bool(row["is_active"]),
        "has_password": bool(row["password_hash"]),
        "created_at": row["created_at"],
        "last_login_at": row["last_login_at"],
    }


def list_users() -> list[dict]:
    rows = get_db().execute(
        "SELECT * FROM users ORDER BY CASE role WHEN 'admin' THEN 0 ELSE 1 END, username"
    ).fetchall()
    return [_public_user(dict(row)) for row in rows]


def _normalize_username(value: str) -> str:
    username = (value or "").strip()
    if not USERNAME_RE.fullmatch(username):
        raise AccountError("اسم المستخدم يجب أن يكون بين 3 و32 حرفًا لاتينيًا أو رقمًا أو . _ -")
    return username


def _normalize_display_name(value: str, username: str) -> str:
    display_name = (value or "").strip() or username
    if len(display_name) > 80:
        raise AccountError("الاسم المعروض أطول من 80 حرفًا.")
    return display_name


def _normalize_role(value: str) -> str:
    role = (value or "user").strip().lower()
    if role not in ROLES:
        raise AccountError("الدور المحدد غير صالح.")
    return role


def _normalize_password(password: str | None) -> str | None:
    value = password or ""
    if not value:
        return None
    if len(value) < PASSWORD_MIN:
        raise AccountError(f"كلمة المرور يجب ألا تقل عن {PASSWORD_MIN} أحرف.")
    if len(value) > PASSWORD_MAX:
        raise AccountError(f"كلمة المرور أطول من {PASSWORD_MAX} حرفًا.")
    return value


def _active_admins(conn, excluding: int | None = None) -> int:
    sql = "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1"
    params: tuple = ()
    if excluding is not None:
        sql += " AND id <> ?"
        params = (excluding,)
    return conn.execute(sql, params).fetchone()[0]


def create_user(
    username: str,
    display_name: str,
    role: str,
    password: str | None = None,
    is_active: bool = True,
) -> dict:
    conn = get_db()
    username = _normalize_username(username)
    display_name = _normalize_display_name(display_name, username)
    role = _normalize_role(role)
    password = _normalize_password(password)
    now = _now()
    try:
        with conn:
            cursor = conn.execute(
                """INSERT INTO users(username, display_name, role, password_hash, is_active,
                                     created_at, updated_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?)""",
                (
                    username,
                    display_name,
                    role,
                    generate_password_hash(password) if password else None,
                    1 if is_active else 0,
                    now,
                    now,
                ),
            )
    except Exception as error:
        if "UNIQUE" in str(error).upper():
            raise AccountError("اسم المستخدم مستخدم بالفعل. اختر اسمًا آخر.") from error
        raise
    row = conn.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _public_user(dict(row))


def update_user(
    user_id: int,
    display_name: str,
    role: str,
    password: str | None = None,
    clear_password: bool = False,
    is_active: bool = True,
    acting_user_id: int | None = None,
) -> dict:
    conn = get_db()
    current = get_user(user_id)
    if not current:
        raise AccountError("المستخدم غير موجود.")
    role = _normalize_role(role)
    display_name = _normalize_display_name(display_name, current["username"])
    password = _normalize_password(password)
    if acting_user_id == user_id and (role != "admin" or not is_active):
        raise AccountError("لا يمكنك إزالة صلاحية مدير النظام عن حسابك أو تعطيله.")
    losing_admin = (
        current["role"] == "admin"
        and current["is_active"]
        and (role != "admin" or not is_active)
    )
    if losing_admin and _active_admins(conn, excluding=user_id) == 0:
        raise AccountError("يجب أن يبقى مدير نظام واحد نشط على الأقل.")

    password_hash = current["password_hash"]
    if clear_password:
        password_hash = None
    elif password:
        password_hash = generate_password_hash(password)

    with conn:
        conn.execute(
            """UPDATE users
               SET display_name = ?, role = ?, password_hash = ?, is_active = ?,
                   updated_at = ?
               WHERE id = ?""",
            (
                display_name,
                role,
                password_hash,
                1 if is_active else 0,
                _now(),
                user_id,
            ),
        )
    return _public_user(get_user(user_id))


def delete_user(user_id: int, acting_user_id: int) -> None:
    conn = get_db()
    current = get_user(user_id)
    if not current:
        raise AccountError("المستخدم غير موجود.")
    if user_id == acting_user_id:
        raise AccountError("لا يمكنك حذف حسابك الحالي.")
    if current["role"] == "admin" and current["is_active"] and _active_admins(conn, excluding=user_id) == 0:
        raise AccountError("يجب أن يبقى مدير نظام واحد نشط على الأقل.")
    with conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def set_user_password(user_id: int, password: str | None) -> None:
    conn = get_db()
    value = _normalize_password(password)
    with conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (generate_password_hash(value) if value else None, _now(), user_id),
        )


def _csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def _check_csrf() -> bool:
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    stored = session.get("csrf_token")
    return bool(sent and stored and hmac.compare_digest(str(sent), str(stored)))


def _attempt_key(username: str) -> str:
    return f"{(username or '').strip().lower()}|{request.remote_addr or 'local'}"


def _attempt_remaining(key: str) -> int:
    now = time.time()
    with _ATTEMPT_LOCK:
        attempts = [stamp for stamp in _ATTEMPTS.get(key, []) if now - stamp < _ATTEMPT_WINDOW]
        _ATTEMPTS[key] = attempts
        if len(attempts) < _ATTEMPT_LIMIT:
            return 0
        return max(1, int(_ATTEMPT_WINDOW - (now - attempts[0])))


def _record_failure(key: str) -> None:
    with _ATTEMPT_LOCK:
        _ATTEMPTS.setdefault(key, []).append(time.time())


def _clear_failures(key: str) -> None:
    with _ATTEMPT_LOCK:
        _ATTEMPTS.pop(key, None)


def _login_context(error: str = "", status: int = 200, username: str = "", next_url: str = ""):
    settings = get_settings(get_db())
    open_admin = get_db().execute(
        "SELECT 1 FROM users WHERE role = 'admin' AND is_active = 1 AND password_hash IS NULL LIMIT 1"
    ).fetchone()
    return (
        render_template(
            "login.html",
            error=error,
            username=username,
            next_url=next_url,
            school_name=settings.get("school_name", ""),
            logo_path=settings.get("logo_path", ""),
            academic_year=settings.get("academic_year", ""),
            open_admin=bool(open_admin),
        ),
        status,
    )


@auth_bp.get("/login")
def login_page():
    next_url = safe_next(request.args.get("next"), "/")
    user = get_user(session.get("user_id"))
    if user and user["is_active"]:
        return redirect(next_url)
    return _login_context(next_url=next_url)


@auth_bp.post("/login")
def login_submit():
    next_url = safe_next(request.form.get("next"), "/")
    username = str(request.form.get("username") or "").strip()
    password = str(request.form.get("password") or "")
    if not _check_csrf():
        return _login_context("انتهت صلاحية الجلسة. أعد تحميل الصفحة ثم حاول مجددًا.", 400, username, next_url)

    key = _attempt_key(username)
    remaining = _attempt_remaining(key)
    if remaining:
        return _login_context(
            f"محاولات دخول كثيرة. انتظر {remaining} ثانية ثم حاول مجددًا.",
            429,
            username,
            next_url,
        )

    user = get_user_by_username(username)
    valid = False
    if user and user["is_active"]:
        if user["password_hash"]:
            valid = check_password_hash(user["password_hash"], password)
        else:
            valid = password == ""
    if not valid:
        _record_failure(key)
        return _login_context("اسم المستخدم أو كلمة المرور غير صحيحة.", 401, username, next_url)

    _clear_failures(key)
    session.clear()
    session.permanent = True
    session["user_id"] = user["id"]
    session["csrf_token"] = secrets.token_urlsafe(32)
    conn = get_db()
    with conn:
        conn.execute(
            "UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?",
            (_now(), _now(), user["id"]),
        )
    return redirect(next_url)


@auth_bp.post("/logout")
def logout():
    if not _check_csrf():
        return redirect(url_for("auth.login_page"))
    session.clear()
    return redirect(url_for("auth.login_page"))


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = g.get("user")
        if not user or user.get("role") != "admin":
            if request.path.startswith("/api/"):
                return jsonify(
                    ok=False,
                    error={"code": "forbidden", "message": "ليست لديك صلاحية الوصول إلى هذه العملية."},
                ), 403
            return render_template(
                "error.html",
                code=403,
                message="ليست لديك صلاحية الوصول إلى هذه الصفحة. ارجع إلى مدير النظام.",
            ), 403
        return view(*args, **kwargs)

    return wrapped


def _is_admin_api_request() -> bool:
    path = request.path
    if path in ADMIN_API_PATHS:
        return True
    if any(path == prefix or path.startswith(prefix + "/") for prefix in ADMIN_API_PREFIXES):
        return True
    if path.startswith("/api/periods") and request.method in ("POST", "PUT", "PATCH", "DELETE"):
        return True
    return False


def _deny(message: str, status: int):
    if request.path.startswith("/api/"):
        code = "auth_required" if status == 401 else "forbidden"
        return jsonify(ok=False, error={"code": code, "message": message}), status
    return redirect(url_for("auth.login_page", next=safe_next(request.full_path, "/")))


def init_app(app) -> None:
    app.register_blueprint(auth_bp)

    @app.before_request
    def require_account():
        if request.endpoint is None:
            return None
        if request.endpoint in PUBLIC_ENDPOINTS or request.path.startswith("/static/"):
            return None
        user_id = session.get("user_id")
        if not user_id:
            return _deny("يجب تسجيل الدخول للمتابعة.", 401)
        user = get_user(user_id)
        if not user or not user["is_active"]:
            session.clear()
            return _deny("انتهت جلسة الدخول. سجّل الدخول من جديد.", 401)
        g.user = user
        if _is_admin_api_request() and user["role"] != "admin":
            return _deny("ليست لديك صلاحية الوصول إلى هذه العملية.", 403)
        return None

    @app.context_processor
    def inject_auth_globals():
        user = g.get("user")
        return {
            "current_user": user,
            "is_admin": bool(user and user["role"] == "admin"),
            "role_label": ROLE_LABELS.get(user["role"], "") if user else "",
            "csrf_token": _csrf_token,
        }
