from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from . import i18n
from .auth import admin_required
from .db import get_db, get_print_config, get_settings
from .services import attendance_service as attendance
from .services import report_service
from .utils import (
    STATUS_AR,
    STATUS_ORDER,
    ValidationError,
    month_label,
    short_date,
    validate_iso_date,
)

views_bp = Blueprint("views", __name__)


@views_bp.app_template_filter("short_date")
def _short_date_filter(value):
    return short_date(value)


@views_bp.app_template_filter("month_label")
def _month_label_filter(value):
    return month_label(value)


def _page_context(active: str, title: str, subtitle: str = "") -> dict:
    conn = get_db()
    settings = get_settings(conn)
    return {
        "active": active,
        "page_title": title,
        "page_subtitle": subtitle,
        "settings": settings,
        "school_name": settings.get("school_name", ""),
        "academic_year": settings.get("academic_year", ""),
        "print_config": get_print_config(settings),
        "periods": attendance.list_periods(conn, include_inactive=True),
        "classes": attendance.class_options(conn),
        "today": date.today().isoformat(),
        "today_display": short_date(date.today().isoformat()),
        "now_time": datetime.now().strftime("%H:%M"),
        "status_labels": STATUS_AR,
    }


@views_bp.get("/")
def dashboard_page():
    return render_template("dashboard.html", **_page_context("dashboard", "لوحة المتابعة", "نظرة شاملة على حضور اليوم ورسائل الأولياء"))


@views_bp.get("/gate")
def gate_page():
    return render_template("gate.html", **_page_context("gate", "بوابة المسح", "تسجيل فوري بحركة باركود سريعة أو كاميرا"))


@views_bp.get("/students")
def students_page():
    return render_template("students.html", **_page_context("students", "التلاميذ", "إدارة قاعدة بيانات التلاميذ وأرقام التعريف والصور"))


@views_bp.get("/import")
def import_page():
    return render_template("import.html", **_page_context("import", "الاستيراد الذكي", "استيراد قوائم التلاميذ من XLS أو XLSX أو HTML"))


@views_bp.get("/attendance")
def attendance_page():
    return render_template("attendance.html", **_page_context("attendance", "سجل الحضور", "متابعة السجلات وتبرير الغيابات وإدارة رسائل SMS"))


@views_bp.get("/messages")
def messages_page():
    return render_template(
        "messages.html",
        **_page_context(
            "messages",
            "مركز الرسائل",
            "إرسال رسائل جماعية لكل التلاميذ أو قسم أو اختيار محدد عبر SMS أو واتساب",
        ),
    )


@views_bp.get("/reports")
def reports_page():
    context = _page_context("reports", "التقارير والإحصاءات", "تقارير يومية وشهرية قابلة للتصدير والطباعة")
    context["current_month"] = date.today().strftime("%Y-%m")
    context["status_order"] = STATUS_ORDER
    return render_template("reports.html", **context)


@views_bp.get("/print")
def print_studio_page():
    return render_template("print_studio.html", **_page_context("print", "استوديو الطباعة", "بطاقات تعريف التلاميذ والملصقات بالباركود"))


@views_bp.get("/settings")
@admin_required
def settings_page():
    return render_template("settings.html", **_page_context("settings", "الإعدادات", "بيانات المؤسسة والفترات وبوابة الرسائل"))


@views_bp.get("/users")
@admin_required
def users_page():
    conn = get_db()
    open_admin = conn.execute(
        "SELECT 1 FROM users WHERE role = 'admin' AND is_active = 1 AND password_hash IS NULL LIMIT 1"
    ).fetchone()
    return render_template(
        "users.html",
        open_admin=bool(open_admin),
        **_page_context("users", "المستخدمون", "حسابات الدخول والأدوار وكلمات المرور"),
    )


@views_bp.get("/help")
def help_page():
    return render_template(
        "help.html",
        **_page_context(
            "help",
            "دليل الاستخدام",
            "شرح مصوّر لطريقة عمل النظام وإرسال رسائل الأولياء",
        ),
    )


@views_bp.get("/about")
def about_page():
    return render_template(
        "about.html",
        **_page_context("about", "حول التطبيق", "معلومات الإصدار والمطوّر والإهداء"),
    )


def _local_redirect_target(value: str | None) -> str | None:
    if not value:
        return None
    target = value.strip()
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        return None
    if any(char in target for char in ("\r", "\n", "\0")):
        return None
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        return None
    return target


@views_bp.get("/language/<code>")
def set_language(code: str):
    lang = i18n.normalize_lang(code)
    target = _local_redirect_target(request.args.get("next"))
    if not target:
        referrer = request.referrer
        if referrer:
            parsed = urlsplit(referrer)
            if not parsed.netloc or parsed.netloc == request.host:
                target = _local_redirect_target(parsed.path + (f"?{parsed.query}" if parsed.query else ""))
    if not target:
        target = url_for("views.dashboard_page")
    response = redirect(target)
    response.set_cookie(
        i18n.LANG_COOKIE,
        lang,
        max_age=60 * 60 * 24 * 365,
        path="/",
        samesite="Lax",
        httponly=True,
    )
    return response


@views_bp.get("/assets/i18n.js")
def i18n_script():
    body = i18n.i18n_javascript(i18n.current_lang())
    response = current_app.response_class(body, mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-store"
    return response


@views_bp.get("/media/photos/<path:filename>")
def media_photo(filename: str):
    return send_from_directory(
        current_app.config["PHOTOS_DIR"], filename, max_age=604800, conditional=True
    )


@views_bp.get("/media/uploads/<path:filename>")
def media_upload(filename: str):
    return send_from_directory(
        current_app.config["UPLOADS_DIR"], filename, max_age=604800, conditional=True
    )


@views_bp.get("/favicon.ico")
def favicon():
    path = Path(current_app.static_folder) / "img" / "favicon.ico"
    if not path.exists():
        abort(404)
    return send_from_directory(path.parent, path.name, max_age=86400)


def _print_students(scope: str, class_group: str, nin: str) -> list[dict]:
    conn = get_db()
    filters = ["s.is_active = 1"]
    params: list = []
    if scope == "student":
        codes = [code.strip() for code in nin.split(",") if code.strip()]
        if not codes:
            return []
        placeholders = ",".join("?" for _ in codes)
        filters.append(f"s.student_nin IN ({placeholders})")
        params.extend(codes)
    elif scope == "class":
        if class_group == "__none__":
            filters.append("s.class_group = ''")
        else:
            filters.append("s.class_group = ?")
            params.append(class_group)
    elif scope != "all":
        return []
    rows = conn.execute(
        f"""SELECT * FROM students s WHERE {' AND '.join(filters)}
            ORDER BY CASE WHEN s.class_group = '' THEN 1 ELSE 0 END,
                     s.class_group, s.last_name, s.first_name, s.student_nin""",
        params,
    ).fetchall()
    return [attendance.student_public(row) for row in rows]


def _parse_layout(mode: str, raw: str | None) -> int:
    if mode == "stickers":
        return 30 if str(raw) == "30" else 24
    return 10 if str(raw) != "8" else 8


def _print_config_from_query(base: dict, args) -> dict:
    config = dict(base)
    for key in ("show_logo", "show_photo", "show_birth_date", "show_qr", "show_stamp", "show_header"):
        if key in args:
            config[key] = str(args.get(key)).lower() in ("1", "true", "on", "yes")
    for key in ("accent_color", "header_color"):
        value = (args.get(key) or "").strip()
        if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            config[key] = value
    try:
        radius = int(args.get("border_radius", config.get("border_radius", 3)))
        config["border_radius"] = max(0, min(6, radius))
    except (TypeError, ValueError):
        pass
    return config


@views_bp.get("/print/sheet")
def print_sheet_page():
    conn = get_db()
    settings = get_settings(conn)
    print_config = _print_config_from_query(get_print_config(settings), request.args)
    mode = "stickers" if request.args.get("mode") == "stickers" else "cards"
    default_layout = print_config["sticker_layout"] if mode == "stickers" else print_config["card_layout"]
    layout = _parse_layout(mode, request.args.get("layout") or str(default_layout))
    scope = request.args.get("scope") or "all"
    if scope not in ("all", "class", "student"):
        scope = "all"
    class_group = request.args.get("class_group") or ""
    nin = request.args.get("nin") or request.args.get("nins") or ""
    try:
        students = _print_students(scope, class_group, nin)
    except ValidationError:
        students = []
    per_page = layout
    pages = [students[index : index + per_page] for index in range(0, len(students), per_page)] or [[]]
    scope_label = {
        "all": "كل التلاميذ النشطين",
        "class": (
            "بدون قسم"
            if class_group == "__none__"
            else (class_group or "كل الأقسام")
        ),
        "student": "اختيار محدد",
    }[scope]
    return render_template(
        "print_sheet.html",
        settings=settings,
        print_config=print_config,
        mode=mode,
        layout=layout,
        scope=scope,
        class_group=class_group,
        nin=nin,
        pages=pages,
        student_count=len(students),
        scope_label=scope_label,
        school_name=settings.get("school_name", ""),
        academic_year=settings.get("academic_year", ""),
        logo_url=settings.get("logo_path", ""),
        stamp_url=settings.get("stamp_path", ""),
        principal_name=settings.get("principal_name", ""),
        header_text=settings.get("header_text", ""),
        generated_at=datetime.now().strftime("%H:%M — %Y-%m-%d"),
    )


@views_bp.get("/reports/print")
def report_print_page():
    conn = get_db()
    settings = get_settings(conn)
    kind = "monthly" if request.args.get("kind") == "monthly" else "daily"
    class_group = request.args.get("class_group") or None
    status = request.args.get("status") or None
    period_id = request.args.get("period_id", type=int)
    try:
        if kind == "monthly":
            month = request.args.get("month") or date.today().strftime("%Y-%m")
            report = report_service.monthly_report(conn, month, period_id, class_group)
        else:
            day = validate_iso_date(request.args.get("date") or date.today().isoformat())
            report = report_service.daily_report(conn, day, period_id, class_group, status)
    except ValidationError:
        abort(400)
    return render_template(
        "report_print.html",
        report=report,
        settings=settings,
        school_name=settings.get("school_name", ""),
        logo_url=settings.get("logo_path", ""),
        stamp_url=settings.get("stamp_path", ""),
        principal_name=settings.get("principal_name", ""),
        header_text=settings.get("header_text", ""),
        generated_at=report.get("generated_at", ""),
    )
