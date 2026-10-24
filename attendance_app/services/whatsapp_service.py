from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..config import WHATSAPP_DOWNLOAD_URL

DEFAULT_COUNTRY_CODE = "213"

_DESKTOP_CACHE: tuple[float, dict[str, Any]] | None = None
_DESKTOP_CACHE_SECONDS = 30.0


def _country_code(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits or DEFAULT_COUNTRY_CODE


def normalize_whatsapp_number(value: Any, country_code: Any = DEFAULT_COUNTRY_CODE) -> str:
    """Return an international WhatsApp number (digits only) for Algerian-style records."""
    cc = _country_code(country_code)
    raw = str(value or "").strip()
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    if raw.startswith("+"):
        international = digits
    elif digits.startswith("00"):
        international = digits[2:]
    elif digits.startswith(cc) and len(digits) >= len(cc) + 8:
        international = digits
    elif digits.startswith("0"):
        international = cc + digits[1:]
    elif len(digits) == 9:
        international = cc + digits
    else:
        international = digits
    if not re.fullmatch(r"[1-9]\d{7,14}", international):
        return ""
    return international


def build_links(phone: Any, message: str, country_code: Any = DEFAULT_COUNTRY_CODE) -> dict[str, str]:
    number = normalize_whatsapp_number(phone, country_code)
    if not number:
        return {}
    text = quote(message or "", safe="")
    return {
        "whatsapp_phone": number,
        "whatsapp_url": f"https://wa.me/{number}?text={text}",
        "whatsapp_app_url": f"whatsapp://send?phone={number}&text={text}",
    }


def _windows_whatsapp_installed() -> bool | None:
    try:
        import winreg
    except ImportError:
        return None
    for root, key_path in (
        (winreg.HKEY_CURRENT_USER, r"Software\Classes\whatsapp"),
        (winreg.HKEY_CLASSES_ROOT, "whatsapp"),
    ):
        try:
            with winreg.OpenKey(root, key_path):
                return True
        except OSError:
            continue
    local = os.environ.get("LOCALAPPDATA")
    if local:
        packages = Path(local) / "Packages"
        try:
            if packages.is_dir() and any(packages.glob("5319275A.WhatsAppDesktop_*")):
                return True
        except OSError:
            pass
        if (Path(local) / "WhatsApp").is_dir():
            return True
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(variable)
        if base and (Path(base) / "WhatsApp").is_dir():
            return True
    return False


def desktop_status(*, force: bool = False) -> dict[str, Any]:
    global _DESKTOP_CACHE
    now = time.monotonic()
    if not force and _DESKTOP_CACHE and now - _DESKTOP_CACHE[0] < _DESKTOP_CACHE_SECONDS:
        return dict(_DESKTOP_CACHE[1])
    platform = "windows" if sys.platform == "win32" else sys.platform
    installed: bool | None
    if sys.platform == "win32":
        installed = _windows_whatsapp_installed()
    else:
        installed = None
    if installed is True:
        detail = "تطبيق واتساب لسطح المكتب مثبّت على هذا الحاسوب."
    elif installed is False:
        detail = (
            "لم يُعثر على تطبيق واتساب لسطح المكتب. ثبّته لتفتح روابط الإرسال تلقائيًا، "
            "أو استخدم واتساب على الهاتف."
        )
    else:
        detail = "يمكن فتح روابط واتساب عبر المتصفح أو تطبيق واتساب إن كان مثبّتًا."
    result = {
        "platform": platform,
        "supported": sys.platform == "win32",
        "installed": installed,
        "download_url": WHATSAPP_DOWNLOAD_URL,
        "detail": detail,
    }
    _DESKTOP_CACHE = (now, dict(result))
    return result
