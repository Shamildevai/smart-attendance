from __future__ import annotations

import logging
import os
from datetime import timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from . import config, i18n
from .db import get_db, get_settings
from .db import init_app as init_db_app
from .db import init_db
from .services.messaging_service import MessageJobManager
from .services.scanner_bridge_service import MAX_PHONES as SCAN_PHONE_LIMIT
from .services.scheduler import start_scheduler
from .services.sms_service import SmsJobManager
from .services.update_service import cleanup_on_startup


def _setup_logging(app: Flask) -> None:
    log_dir = Path(app.config["LOGS_DIR"])
    log_dir.mkdir(parents=True, exist_ok=True)
    if app.config.get("TESTING"):
        app.logger.setLevel(logging.WARNING)
        return
    handler = RotatingFileHandler(
        log_dir / "app.log", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    handler.setLevel(logging.INFO)
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(
        __name__,
        template_folder=config.template_dir(),
        static_folder=config.static_dir(),
    )
    data_dir = Path(
        (test_config or {}).get("DATA_DIR")
        or os.environ.get("ATTENDANCE_DATA_DIR")
        or config.default_data_dir()
    )
    layout = config.ensure_data_layout(data_dir)
    secret_key = (
        (test_config or {}).get("SECRET_KEY")
        or os.environ.get("ATTENDANCE_SECRET")
        or config.load_session_secret(data_dir)
    )
    app.config.update(
        SECRET_KEY=secret_key,
        DATA_DIR=str(data_dir),
        DB_PATH=str(layout["root"] / "attendance.db"),
        PHOTOS_DIR=str(layout["photos"]),
        UPLOADS_DIR=str(layout["uploads"]),
        TMP_DIR=str(layout["tmp"]),
        LOGS_DIR=str(layout["logs"]),
        UPDATES_DIR=str(layout["updates"]),
        MAX_CONTENT_LENGTH=30 * 1024 * 1024,
        JSON_AS_ASCII=False,
        TEMPLATES_AUTO_RELOAD=not config._frozen(),
        SCHEDULER_INTERVAL=25,
        APP_VERSION=config.APP_VERSION,
        APP_REPO_URL=config.APP_REPO_URL,
        UPDATE_FEED_URL=config.UPDATE_FEED_URL,
        UPDATE_PUBLIC_KEY=config.UPDATE_PUBLIC_KEY,
        UPDATE_CHECK_INTERVAL=config.UPDATE_CHECK_INTERVAL,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=False,
    )
    if test_config:
        app.config.update(test_config)
        if "DATA_DIR" in test_config and "DB_PATH" not in test_config:
            test_layout = config.ensure_data_layout(Path(test_config["DATA_DIR"]))
            app.config.update(
                DB_PATH=str(test_layout["root"] / "attendance.db"),
                PHOTOS_DIR=str(test_layout["photos"]),
                UPLOADS_DIR=str(test_layout["uploads"]),
                TMP_DIR=str(test_layout["tmp"]),
                LOGS_DIR=str(test_layout["logs"]),
                UPDATES_DIR=str(test_layout["updates"]),
            )

    init_db(app.config["DB_PATH"])
    init_db_app(app)
    _setup_logging(app)
    app.extensions["sms_jobs"] = SmsJobManager()
    app.extensions["message_jobs"] = MessageJobManager()
    app.json.ensure_ascii = False
    i18n.init_app(app)

    from . import auth

    auth.init_app(app)

    from .api import api_bp
    from .views import views_bp

    app.register_blueprint(views_bp)
    app.register_blueprint(api_bp, url_prefix="/api")

    @app.before_request
    def csrf_guard():
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.path.startswith("/api/"):
            if request.headers.get("X-Requested-With") != "StudentAttendance":
                return jsonify(
                    ok=False,
                    error={"code": "csrf", "message": "طلب غير مصرح به. أعد تحميل الصفحة."},
                ), 403
        return None

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
            "font-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'",
        )
        if request.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.errorhandler(413)
    def too_large(_error):
        if request.path.startswith("/api/"):
            return jsonify(ok=False, error={"code": "too_large", "message": "حجم الملف كبير جدًا (الحد 30 ميغابايت)."}), 413
        return render_template("error.html", code=413, message="حجم الطلب كبير جدًا."), 413

    @app.errorhandler(404)
    def not_found(_error):
        if request.path.startswith("/api/"):
            return jsonify(ok=False, error={"code": "not_found", "message": "المسار غير موجود."}), 404
        return render_template("error.html", code=404, message="الصفحة غير موجودة."), 404

    @app.errorhandler(403)
    def forbidden(_error):
        if request.path.startswith("/api/"):
            return jsonify(ok=False, error={"code": "forbidden", "message": "ليست لديك صلاحية الوصول إلى هذه العملية."}), 403
        return render_template("error.html", code=403, message="ليست لديك صلاحية الوصول إلى هذه الصفحة."), 403

    @app.errorhandler(500)
    def server_error(error):
        app.logger.exception("Unhandled error: %s", error)
        if request.path.startswith("/api/"):
            return jsonify(ok=False, error={"code": "server_error", "message": "حدث خطأ داخلي غير متوقع. راجع سجل التطبيق."}), 500
        return render_template("error.html", code=500, message="حدث خطأ داخلي غير متوقع."), 500

    @app.context_processor
    def inject_app_globals():
        lang = i18n.current_lang()
        try:
            color_theme = get_settings(get_db()).get(
                "color_theme", config.DEFAULT_COLOR_THEME
            )
        except Exception:
            color_theme = config.DEFAULT_COLOR_THEME
        if color_theme not in config.COLOR_THEMES:
            color_theme = config.DEFAULT_COLOR_THEME
        return {
            **config.app_metadata(lang),
            "lang": lang,
            "dir": i18n.lang_dir(lang),
            "color_theme": color_theme,
            "lang_options": i18n.LANGUAGES,
            "app_version": config.APP_VERSION,
            "app_repo_url": config.APP_REPO_URL,
            "app_author_latin": config.APP_AUTHOR_LATIN,
            "app_author_handle": config.APP_AUTHOR_HANDLE,
            "app_location_latin": config.APP_LOCATION_LATIN,
            "gateway_app_name": config.GATEWAY_APP_NAME,
            "gateway_app_version": config.GATEWAY_APP_VERSION,
            "gateway_app_download_url": config.GATEWAY_APP_DOWNLOAD_URL,
            "scan_phone_limit": SCAN_PHONE_LIMIT,
            "whatsapp_download_url": config.WHATSAPP_DOWNLOAD_URL,
            "current_year": __import__("datetime").datetime.now().year,
        }

    cleanup_on_startup(app)
    start_scheduler(app)
    app.logger.info("Application started. Data directory: %s", data_dir)
    return app
