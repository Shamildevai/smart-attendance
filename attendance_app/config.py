from __future__ import annotations

import os
import secrets
import sys
import tempfile
from pathlib import Path

APP_NAME = "SmartAttendance"
APP_VERSION = "1.1.15"
APP_TITLE = "نظام متابعة الحضور"
APP_SUBTITLE = "الحضور بالباركود وإشعار الأولياء"
APP_AUTHOR = "د. بوزويرة عزيز"
APP_AUTHOR_LATIN = "Dr. Bouzouira Aziz"
APP_AUTHOR_HANDLE = "shamildev"
APP_LOCATION = "سعيدة، الجزائر"
APP_LOCATION_LATIN = "Saida, Algeria"
APP_DEDICATION = "صدقة عن والدي المرحوم بوزويرة قادة"
APP_REPO_URL = "https://github.com/Shamildevai/smart-attendance"

UPDATE_FEED_URL = "https://fetsync.org/Update/latest.json"
UPDATE_PUBLIC_KEY = "wfjSP8we/c/RCTDi0DsC7xKYb+pKtzD1RZJLU8DT/sA="
UPDATE_CHECK_INTERVAL = 6 * 60 * 60

GATEWAY_APP_NAME = "Attendance SMS Gateway"
GATEWAY_APP_VERSION = "1.0.1"
GATEWAY_APP_DOWNLOAD_URL = (
    f"https://fetsync.org/Update/Attendance-SMS-Gateway-{GATEWAY_APP_VERSION}.apk"
)
WHATSAPP_DOWNLOAD_URL = "https://www.whatsapp.com/download"

ROLE_LABELS = {
    "admin": "مدير",
    "user": "مستخدم",
}

APP_TRANSLATIONS = {
    "en": {
        "title": "Smart Attendance System",
        "subtitle": "Barcode attendance & parent notifications",
        "author": "Dr. Bouzouira Aziz",
        "location": "Saida, Algeria",
        "dedication": "A continuous charity in memory of my late father, Bouzouira Kada.",
    },
    "fr": {
        "title": "Système de suivi de présence",
        "subtitle": "Présence par code-barres et notifications aux parents",
        "author": "Dr. Bouzouira Aziz",
        "location": "Saïda, Algérie",
        "dedication": "Une œuvre caritative à la mémoire de mon défunt père, Bouzouira Kada.",
    },
}


def app_metadata(lang: str) -> dict[str, str]:
    meta = APP_TRANSLATIONS.get(lang, {})
    return {
        "app_title": meta.get("title", APP_TITLE),
        "app_subtitle": meta.get("subtitle", APP_SUBTITLE),
        "app_author": meta.get("author", APP_AUTHOR),
        "app_location": meta.get("location", APP_LOCATION),
        "app_dedication": meta.get("dedication", APP_DEDICATION),
    }


PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    if _frozen():
        return Path(getattr(sys, "_MEIPASS")) / "attendance_app"
    return PACKAGE_DIR


def template_dir() -> str:
    return str(bundle_dir() / "templates")


def static_dir() -> str:
    return str(bundle_dir() / "static")


def executable_dir() -> Path:
    if _frozen():
        return Path(sys.executable).resolve().parent
    return PROJECT_DIR


def _is_writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".write-test-", delete=True):
            pass
        return True
    except OSError:
        return False


def default_data_dir() -> Path:
    """Portable data folder next to the app, with a safe LocalAppData fallback."""
    override = os.environ.get("ATTENDANCE_DATA_DIR")
    if override:
        path = Path(override).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    candidate = executable_dir() / "data"
    if _is_writable(candidate):
        return candidate

    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    fallback = Path(local) / "SmartStudentAttendance" / "data"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def ensure_data_layout(data_dir: Path) -> dict[str, Path]:
    data_dir = Path(data_dir)
    layout = {
        "root": data_dir,
        "photos": data_dir / "photos",
        "uploads": data_dir / "uploads",
        "tmp": data_dir / "tmp",
        "logs": data_dir / "logs",
        "updates": data_dir / "updates",
    }
    for path in layout.values():
        path.mkdir(parents=True, exist_ok=True)
    return layout


def load_session_secret(data_dir: Path) -> str:
    """Keep sessions valid across restarts without putting a secret in the repository."""
    path = Path(data_dir) / ".session_secret"
    try:
        if path.exists():
            value = path.read_text(encoding="ascii").strip()
            if len(value) >= 32:
                return value
        value = secrets.token_hex(32)
        path.write_text(value, encoding="ascii")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return value
    except OSError:
        return secrets.token_hex(32)

