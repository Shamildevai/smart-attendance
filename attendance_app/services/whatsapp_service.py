from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from ..config import APP_VERSION, WHATSAPP_DOWNLOAD_URL

DEFAULT_COUNTRY_CODE = "213"
MAX_CLOUD_BODY = 1024
DEFAULT_API_VERSION = "v23.0"
DEFAULT_TEMPLATE_LANG = "ar"


class WhatsAppError(RuntimeError):
    pass


class WhatsAppConfigError(WhatsAppError):
    """Raised when the Meta Cloud API settings are incomplete or disabled."""


class WhatsAppConnectionError(WhatsAppError):
    """Raised when graph.facebook.com cannot be reached."""


class WhatsAppSendError(WhatsAppError):
    pass

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


def _value(settings: dict[str, Any], key: str, default: str = "") -> str:
    return str(settings.get(key) or default).strip()


def _cloud_parts(settings: dict[str, Any]) -> tuple[str, str, str, str, str]:
    phone_id = re.sub(r"\D", "", _value(settings, "whatsapp_phone_number_id"))
    token = _value(settings, "whatsapp_access_token")
    template_name = _value(settings, "whatsapp_template_name")
    template_lang = _value(settings, "whatsapp_template_lang") or DEFAULT_TEMPLATE_LANG
    api_version = _value(settings, "whatsapp_api_version") or DEFAULT_API_VERSION
    if not re.fullmatch(r"v\d{1,2}\.\d", api_version):
        api_version = DEFAULT_API_VERSION
    return phone_id, token, template_name, template_lang, api_version


def cloud_configured(settings: dict[str, Any]) -> bool:
    phone_id, token, template_name, _, _ = _cloud_parts(settings)
    return bool(phone_id and token and template_name)


def cloud_status(settings: dict[str, Any]) -> dict[str, Any]:
    provider = "cloud" if _value(settings, "whatsapp_provider") == "cloud" else "manual"
    enabled = _value(settings, "whatsapp_enabled", "1") == "1"
    test_mode = _value(settings, "sms_test_mode", "1") == "1"
    phone_id, token, template_name, template_lang, api_version = _cloud_parts(settings)
    missing = []
    if not phone_id:
        missing.append("Phone Number ID")
    if not token:
        missing.append("رمز الوصول")
    if not template_name:
        missing.append("اسم القالب")
    configured = not missing
    if provider == "manual":
        ready = enabled
        detail = "الإرسال اليدوي عبر واتساب لسطح المكتب مفعّل." if enabled else "قناة واتساب معطّلة."
    elif not enabled:
        ready = False
        detail = "إرسال واتساب معطّل من الإعدادات."
    elif configured:
        ready = True
        detail = (
            f"Meta Cloud API جاهز: القالب {template_name} ({template_lang})."
            if not test_mode
            else f"Meta Cloud API مهيأ: القالب {template_name} ({template_lang}) — وضع الاختبار مفعّل."
        )
    elif test_mode:
        ready = True
        detail = "وضع الاختبار مفعّل: يمكن تجربة واتساب كمحاكاة رغم نقص بيانات Cloud API."
    else:
        ready = False
        detail = "بيانات Cloud API غير مكتملة: " + "، ".join(missing) + "."
    return {
        "provider": provider,
        "enabled": enabled,
        "configured": configured,
        "ready": ready,
        "test_mode": test_mode,
        "phone_number_id": phone_id,
        "template_name": template_name,
        "template_lang": template_lang,
        "api_version": api_version,
        "missing": missing,
        "detail": detail,
        "download_url": WHATSAPP_DOWNLOAD_URL,
    }


def build_cloud_payload(
    settings: dict[str, Any], phone: Any, message: Any
) -> dict[str, Any]:
    number = normalize_whatsapp_number(phone, settings.get("whatsapp_country_code", DEFAULT_COUNTRY_CODE))
    if not number:
        raise WhatsAppConfigError("رقم هاتف الولي غير صالح لواتساب.")
    text = str(message or "").strip()
    if not text:
        raise WhatsAppConfigError("لا يمكن إرسال رسالة واتساب فارغة.")
    if len(text) > MAX_CLOUD_BODY:
        raise WhatsAppConfigError(
            f"نص الرسالة يتجاوز حد واتساب ({MAX_CLOUD_BODY} حرف). اختصر النص ثم أعد المحاولة."
        )
    _, _, template_name, template_lang, _ = _cloud_parts(settings)
    if not template_name:
        raise WhatsAppConfigError("اسم قالب واتساب غير مضبوط في الإعدادات.")
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": number,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": template_lang},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": text}],
                }
            ],
        },
    }


def send_via_cloud(
    settings: dict[str, Any], phone: Any, message: Any, timeout: float = 15.0
) -> dict[str, Any]:
    if _value(settings, "sms_test_mode", "1") == "1":
        return {
            "ok": True,
            "simulated": True,
            "provider": "whatsapp_cloud",
            "gateway": "whatsapp_cloud",
            "message_id": "",
            "detail": "وضع الاختبار: تمت محاكاة إرسال واتساب دون استخدام الشبكة.",
        }
    if _value(settings, "whatsapp_provider") != "cloud":
        raise WhatsAppConfigError(
            "اختر «تلقائي عبر Meta WhatsApp Cloud API» في إعدادات واتساب أولًا."
        )
    if _value(settings, "whatsapp_enabled", "1") != "1":
        raise WhatsAppConfigError("قناة واتساب معطّلة في إعدادات الرسائل.")
    phone_id, token, _, _, api_version = _cloud_parts(settings)
    if not token:
        raise WhatsAppConfigError(
            "رمز وصول Meta غير مضبوط. أضف رمز وصول دائمًا من إعدادات واتساب."
        )
    if not phone_id:
        raise WhatsAppConfigError(
            "Phone Number ID غير مضبوط. انسخه من لوحة Meta for Developers إلى الإعدادات."
        )
    payload = build_cloud_payload(settings, phone, message)
    url = f"https://graph.facebook.com/{api_version}/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": f"SmartAttendance/{APP_VERSION}",
    }
    timeout = max(5.0, min(float(timeout or 15.0), 60.0))
    try:
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=timeout,
            allow_redirects=False,
            stream=True,
        )
    except requests.Timeout as exc:
        raise WhatsAppConnectionError(
            "انتهت مهلة الاتصال بخدمة واتساب. تحقق من اتصال الإنترنت ثم أعد المحاولة."
        ) from exc
    except requests.ConnectionError as exc:
        raise WhatsAppConnectionError(
            "تعذر الاتصال بخدمة واتساب. تحقق من اتصال الإنترنت ثم أعد المحاولة."
        ) from exc
    except requests.RequestException as exc:
        raise WhatsAppSendError(f"خطأ في الاتصال بخدمة واتساب: {exc}") from exc

    status_code = response.status_code
    try:
        body_text = (response.text or "").strip()
    except requests.RequestException:
        body_text = ""
    finally:
        response.close()
    payload_body: dict[str, Any] = {}
    if body_text:
        try:
            parsed = json.loads(body_text)
            if isinstance(parsed, dict):
                payload_body = parsed
        except ValueError:
            payload_body = {}

    if not (200 <= status_code < 300):
        error = payload_body.get("error") if isinstance(payload_body.get("error"), dict) else {}
        meta_message = str(error.get("message") or "").strip()[:300]
        suffix = f" ({meta_message})" if meta_message else ""
        if status_code in (401, 403):
            raise WhatsAppConfigError(
                "رفضت Meta رمز الوصول. ولّد رمز وصول دائمًا صالحًا وضعه في إعدادات واتساب."
            )
        if status_code == 404:
            raise WhatsAppConfigError(
                "Phone Number ID أو إصدار Graph API غير صحيح. راجع بيانات Meta Cloud API."
            )
        if status_code == 429:
            raise WhatsAppSendError(
                "تجاوزت حدود إرسال واتساب مؤقتًا. انتظر قليلًا ثم أعد المحاولة." + suffix
            )
        if status_code in (400, 422):
            raise WhatsAppConfigError(
                f"رفضت Meta الرسالة (HTTP {status_code}). تحقق من اعتماد القالب "
                "واسمه ولغته، ومن أنه يحتوي متغيرًا واحدًا في النص {{{{1}}}}." + suffix
            )
        if status_code >= 500:
            raise WhatsAppConnectionError(
                "خدمة واتساب غير متاحة مؤقتًا. أعد المحاولة بعد قليل."
            )
        raise WhatsAppSendError(
            f"رفضت خدمة واتساب الطلب (HTTP {status_code}){': ' + body_text[:180] if body_text else ''}"
        )

    messages = payload_body.get("messages") if isinstance(payload_body.get("messages"), list) else []
    message_id = ""
    if messages and isinstance(messages[0], dict):
        message_id = str(messages[0].get("id") or "")
    return {
        "ok": True,
        "simulated": False,
        "provider": "whatsapp_cloud",
        "gateway": "whatsapp_cloud",
        "message_id": message_id,
        "detail": f"WhatsApp Cloud API — HTTP {status_code}",
    }


def test_cloud(settings: dict[str, Any], phone: Any) -> dict[str, Any]:
    if _value(settings, "whatsapp_provider") != "cloud":
        raise WhatsAppConfigError(
            "اختر «تلقائي عبر Meta WhatsApp Cloud API» في إعدادات واتساب قبل الاختبار."
        )
    result = send_via_cloud(
        settings,
        phone,
        "رسالة اختبار من نظام متابعة الحضور. إعداد واتساب يعمل بنجاح.",
    )
    if result.get("simulated"):
        return {
            "sent": False,
            "simulated": True,
            "message": "وضع الاختبار مفعّل: تمت محاكاة إرسال واتساب ولم تُرسل رسالة حقيقية.",
        }
    return {
        "sent": True,
        "simulated": False,
        "message": "تم إرسال رسالة واتساب التجريبية عبر Meta Cloud API.",
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
