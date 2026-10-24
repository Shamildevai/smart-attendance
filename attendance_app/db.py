from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from flask import current_app, g

from .utils import academic_year_start, dump_json, parse_weekdays, safe_json_loads

SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
    password_hash TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    last_login_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role, is_active);

CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_nin TEXT NOT NULL UNIQUE COLLATE NOCASE,
    first_name TEXT NOT NULL DEFAULT '',
    last_name TEXT NOT NULL DEFAULT '',
    birth_date TEXT,
    class_group TEXT NOT NULL DEFAULT '',
    parent_phone TEXT NOT NULL DEFAULT '',
    photo_url TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_students_class ON students(class_group);
CREATE INDEX IF NOT EXISTS idx_students_name ON students(last_name, first_name);
CREATE INDEX IF NOT EXISTS idx_students_active ON students(is_active);

CREATE TABLE IF NOT EXISTS periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_name TEXT NOT NULL,
    start_time TEXT NOT NULL,
    on_time_cutoff TEXT NOT NULL,
    absent_cutoff_time TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS attendance_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_nin TEXT NOT NULL,
    period_id INTEGER NOT NULL,
    attendance_date TEXT NOT NULL,
    scan_time TEXT,
    status TEXT NOT NULL CHECK (status IN ('PRESENT', 'LATE', 'ABSENT', 'EXCUSED')),
    note TEXT,
    sms_sent INTEGER NOT NULL DEFAULT 0 CHECK (sms_sent IN (0, 1)),
    source TEXT NOT NULL DEFAULT 'GATE',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (student_nin, period_id, attendance_date),
    FOREIGN KEY (student_nin) REFERENCES students(student_nin)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (period_id) REFERENCES periods(id)
        ON UPDATE CASCADE ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_att_date_period ON attendance_logs(attendance_date, period_id);
CREATE INDEX IF NOT EXISTS idx_att_student_date ON attendance_logs(student_nin, attendance_date);
CREATE INDEX IF NOT EXISTS idx_att_status ON attendance_logs(status);

CREATE TABLE IF NOT EXISTS sms_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_nin TEXT NOT NULL,
    period_id INTEGER NOT NULL,
    attendance_date TEXT NOT NULL,
    parent_phone TEXT NOT NULL,
    message_body TEXT NOT NULL,
    sent_at TEXT,
    delivery_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (delivery_status IN ('PENDING', 'SENT', 'FAILED', 'CANCELLED')),
    error_msg TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    gateway TEXT,
    is_test INTEGER NOT NULL DEFAULT 0 CHECK (is_test IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (student_nin, period_id, attendance_date),
    FOREIGN KEY (student_nin) REFERENCES students(student_nin)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (period_id) REFERENCES periods(id)
        ON UPDATE CASCADE ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_sms_status ON sms_logs(delivery_status);
CREATE INDEX IF NOT EXISTS idx_sms_date ON sms_logs(attendance_date);

CREATE TABLE IF NOT EXISTS period_closures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_id INTEGER NOT NULL,
    closure_date TEXT NOT NULL,
    closed_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    closed_by TEXT NOT NULL DEFAULT 'MANUAL',
    sms_queued INTEGER NOT NULL DEFAULT 0 CHECK (sms_queued IN (0, 1)),
    note TEXT,
    UNIQUE (period_id, closure_date),
    FOREIGN KEY (period_id) REFERENCES periods(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS message_campaigns (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL CHECK (channel IN ('mobile', 'cloud', 'whatsapp')),
    scope TEXT NOT NULL CHECK (scope IN ('all', 'class', 'selected')),
    class_group TEXT,
    message_template TEXT NOT NULL,
    recipients_total INTEGER NOT NULL DEFAULT 0,
    eligible_total INTEGER NOT NULL DEFAULT 0,
    skipped_total INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'RUNNING', 'DONE', 'PREPARED')),
    test_mode INTEGER NOT NULL DEFAULT 0 CHECK (test_mode IN (0, 1)),
    created_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_campaigns_created ON message_campaigns(created_at DESC);

CREATE TABLE IF NOT EXISTS message_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id TEXT NOT NULL,
    student_nin TEXT NOT NULL,
    student_name TEXT NOT NULL DEFAULT '',
    class_group TEXT,
    parent_phone TEXT NOT NULL,
    message_body TEXT NOT NULL,
    channel TEXT NOT NULL CHECK (channel IN ('mobile', 'cloud', 'whatsapp')),
    delivery_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (delivery_status IN ('PENDING', 'SENT', 'FAILED', 'CANCELLED', 'MANUAL')),
    attempts INTEGER NOT NULL DEFAULT 0,
    error_msg TEXT,
    gateway TEXT,
    is_test INTEGER NOT NULL DEFAULT 0 CHECK (is_test IN (0, 1)),
    sent_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (campaign_id) REFERENCES message_campaigns(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_outbox_campaign ON message_outbox(campaign_id);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON message_outbox(delivery_status);
"""

DEFAULT_PRINT_CONFIG = {
    "accent_color": "#0e7c66",
    "header_color": "#0b3b31",
    "show_logo": True,
    "show_photo": True,
    "show_birth_date": True,
    "show_qr": False,
    "show_stamp": True,
    "show_header": True,
    "card_layout": 10,
    "sticker_layout": 30,
    "border_radius": 3,
}


def default_sms_template() -> str:
    return (
        "تعلـمكم إدارة [اسم_المؤسسة] أن التلميذ(ة) [اسم_التلميذ] القسـم [القسم] "
        "قد تغيب عن [اسم_الفترة] ليوم [التاريخ]. يرجى التقرب من الإدارة لتبرير الغياب."
    )


DEFAULT_SETTINGS: dict[str, str] = {
    "school_name": "اسم المؤسسة التعليمية",
    "academic_year": academic_year_start(),
    "header_text": "الجمهورية الجزائرية الديمقراطية الشعبية\nوزارة التربية الوطنية",
    "principal_name": "مدير المؤسسة",
    "logo_path": "",
    "stamp_path": "",
    "sms_template": default_sms_template(),
    "sms_gateway": "local",
    "sms_api_url": "",
    "sms_api_key": "",
    "sms_mobile_url": "",
    "sms_mobile_key": "",
    "sms_cloud_url": "",
    "sms_cloud_key": "",
    "sms_headers": "{}",
    "sms_payload_template": '{"phone": "{{phone}}", "message": "{{message}}"}',
    "sms_test_mode": "1",
    "sms_timeout": "10",
    "sms_retry_max": "3",
    "whatsapp_enabled": "1",
    "whatsapp_country_code": "213",
    "school_weekdays": "0,1,2,3,4",
    "auto_sweep": "1",
    "anti_passback_minutes": "5",
    "print_config": dump_json(DEFAULT_PRINT_CONFIG),
    "auto_update": "1",
    "update_feed_url": "https://update.fetsync.org/latest.json",
    "update_last_check": "",
}

SEED_PERIODS = [
    {
        "period_name": "الفترة الصباحية",
        "start_time": "07:45",
        "on_time_cutoff": "08:10",
        "absent_cutoff_time": "08:30",
        "sort_order": 1,
    }
]


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DB_PATH"])
    return g.db


def close_db(_exception=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_app(app) -> None:
    app.teardown_appcontext(close_db)


def init_db(db_path: str | Path) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.executescript(SCHEMA)
        current_version = conn.execute("PRAGMA user_version").fetchone()[0]
        if current_version < SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO NOTHING",
                (key, value),
            )

        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if user_count == 0:
            conn.execute(
                """INSERT INTO users(username, display_name, role, password_hash, is_active)
                   VALUES('admin', 'مدير النظام', 'admin', NULL, 1)"""
            )

        initialized = conn.execute(
            "SELECT value FROM app_meta WHERE key = 'initialized_at'"
        ).fetchone()
        if not initialized:
            period_count = conn.execute("SELECT COUNT(*) FROM periods").fetchone()[0]
            if period_count == 0:
                for period in SEED_PERIODS:
                    conn.execute(
                        """INSERT INTO periods
                           (period_name, start_time, on_time_cutoff, absent_cutoff_time, sort_order)
                           VALUES(?, ?, ?, ?, ?)""",
                        (
                            period["period_name"],
                            period["start_time"],
                            period["on_time_cutoff"],
                            period["absent_cutoff_time"],
                            period["sort_order"],
                        ),
                    )
            conn.execute(
                "INSERT INTO app_meta(key, value) VALUES('initialized_at', datetime('now', 'localtime'))"
            )
        conn.commit()
    finally:
        conn.close()


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def rows_dict(rows) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def get_settings(conn: sqlite3.Connection) -> dict[str, str]:
    stored = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings")}
    return {**DEFAULT_SETTINGS, **stored}


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return DEFAULT_SETTINGS.get(key, default)
    return row["value"]


def save_settings(conn: sqlite3.Connection, values: dict[str, Any]) -> None:
    for key, value in values.items():
        if key not in DEFAULT_SETTINGS:
            continue
        conn.execute(
            """INSERT INTO settings(key, value, updated_at)
               VALUES(?, ?, datetime('now', 'localtime'))
               ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                   updated_at = datetime('now', 'localtime')""",
            (key, str(value)),
        )


def get_print_config(settings: dict[str, str]) -> dict[str, Any]:
    config = dict(DEFAULT_PRINT_CONFIG)
    loaded = safe_json_loads(settings.get("print_config"), {})
    if isinstance(loaded, dict):
        config.update({k: v for k, v in loaded.items() if k in DEFAULT_PRINT_CONFIG})
    return config


def bool_setting(settings: dict[str, str], key: str, default: bool = False) -> bool:
    value = settings.get(key)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def int_setting(settings: dict[str, str], key: str, default: int, minimum: int = 0, maximum: int = 1000) -> int:
    try:
        value = int(settings.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def school_weekdays(settings: dict[str, str]) -> list[int]:
    return parse_weekdays(settings.get("school_weekdays", "0,1,2,3,4"))


def update_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def db_overview(conn: sqlite3.Connection) -> dict[str, Any]:
    counts = {}
    for table in ("students", "periods", "attendance_logs", "sms_logs"):
        counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    counts["active_students"] = conn.execute(
        "SELECT COUNT(*) FROM students WHERE is_active = 1"
    ).fetchone()[0]
    return counts


def print_config_json(config: dict[str, Any]) -> str:
    return json.dumps(config, ensure_ascii=False)
