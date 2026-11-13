from __future__ import annotations

import json
import re
from typing import Any

import requests

MAX_BODY = 4096
API_ROOT = "https://api.telegram.org"
API_TIMEOUT = 15.0


class TelegramError(RuntimeError):
    pass


class TelegramConfigError(TelegramError):
    """Raised when the bot token or chat id is missing, disabled, or rejected."""


class TelegramConnectionError(TelegramError):
    """Raised when api.telegram.org cannot be reached."""


class TelegramSendError(TelegramError):
    pass


def _value(settings: dict[str, Any], key: str, default: str = "") -> str:
    return str(settings.get(key) or default).strip()


def normalize_chat_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("@"):
        return text if re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{3,31}", text) else ""
    digits = re.sub(r"\D", "", text)
    if text.lstrip("-").isdigit() and 5 <= len(digits) <= 20:
        return ("-" if text.startswith("-") else "") + digits
    return ""


def bot_token(settings: dict[str, Any]) -> str:
    return _value(settings, "telegram_bot_token")


def token_valid(token: str) -> bool:
    return bool(re.fullmatch(r"\d{5,20}:[A-Za-z0-9_-]{20,}", token or ""))


def telegram_configured(settings: dict[str, Any]) -> bool:
    token = bot_token(settings)
    chat_id = normalize_chat_id(_value(settings, "telegram_chat_id"))
    return bool(token_valid(token) and chat_id)


def telegram_status(settings: dict[str, Any]) -> dict[str, Any]:
    token = bot_token(settings)
    chat_id = normalize_chat_id(_value(settings, "telegram_chat_id"))
    enabled = _value(settings, "telegram_enabled", "0") == "1"
    test_mode = _value(settings, "sms_test_mode", "1") == "1"
    missing = []
    if not token:
        missing.append("رمز البوت (Bot Token)")
    elif not token_valid(token):
        missing.append("رمز البوت (تنسيق غير صحيح)")
    if not chat_id:
        missing.append("معرّف المحادثة (Chat ID)")
    configured = not missing
    if not enabled:
        ready = False
        detail = "قناة تيليغرام معطّلة من الإعدادات."
    elif configured and not test_mode:
        ready = True
        detail = f"تيليغرام جاهز للإرسال إلى المحادثة {chat_id}."
    elif configured:
        ready = True
        detail = f"بيانات تيليغرام مكتملة ({chat_id}) — وضع الاختبار مفعّل."
    elif test_mode:
        ready = True
        detail = "وضع الاختبار مفعّل: يمكن تجربة تيليغرام كمحاكاة رغم نقص البيانات."
    else:
        ready = False
        detail = "بيانات تيليغرام غير مكتملة: " + "، ".join(missing) + "."
    return {
        "enabled": enabled,
        "configured": configured,
        "ready": ready,
        "test_mode": test_mode,
        "chat_id": chat_id,
        "missing": missing,
        "detail": detail,
    }


def _request(method: str, token: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    url = f"{API_ROOT}/bot{token}/{method}"
    try:
        response = requests.post(
            url,
            json=payload,
            timeout=max(5.0, min(float(timeout or API_TIMEOUT), 60.0)),
            allow_redirects=False,
        )
    except requests.Timeout as exc:
        raise TelegramConnectionError(
            "انتهت مهلة الاتصال بخدمة تيليغرام. تحقق من اتصال الإنترنت ثم أعد المحاولة."
        ) from exc
    except requests.ConnectionError as exc:
        raise TelegramConnectionError(
            "تعذر الاتصال بخدمة تيليغرام. تحقق من اتصال الإنترنت ثم أعد المحاولة."
        ) from exc
    except requests.RequestException as exc:
        raise TelegramSendError(f"خطأ في الاتصال بخدمة تيليغرام: {exc}") from exc

    status_code = response.status_code
    try:
        body_text = (response.text or "").strip()
    finally:
        response.close()
    body: dict[str, Any] = {}
    if body_text:
        try:
            parsed = json.loads(body_text)
            if isinstance(parsed, dict):
                body = parsed
        except ValueError:
            body = {}
    if status_code == 401:
        raise TelegramConfigError(
            "رفضت تيليغرام رمز البوت. تأكد من نسخ التوكن كاملًا من @BotFather."
        )
    if status_code == 404:
        raise TelegramConfigError(
            "طريقة تيليغرام غير موجودة. تحقق من رمز البوت ثم أعد المحاولة."
        )
    if status_code == 429:
        raise TelegramSendError(
            "تجاوزت حدود إرسال تيليغرام مؤقتًا. انتظر قليلًا ثم أعد المحاولة."
        )
    if status_code >= 500:
        raise TelegramConnectionError("خدمة تيليغرام غير متاحة مؤقتًا. أعد المحاولة بعد قليل.")
    if status_code >= 400 or not body.get("ok"):
        description = str(body.get("description") or "").strip()[:300]
        if "chat not found" in description.lower() or "chat_id" in description.lower():
            raise TelegramConfigError(
                "معرّف المحادثة غير صحيح أو أن البوت ليس عضوًا فيها. "
                "أرسل رسالة إلى البوت أولًا أو أضفه إلى المجموعة، ثم تحقق من Chat ID."
            )
        raise TelegramSendError(
            f"رفضت تيليغرام الطلب (HTTP {status_code})"
            + (f": {description}" if description else "")
        )
    result = body.get("result")
    return result if isinstance(result, dict) else {}


def send_message(
    settings: dict[str, Any], text: Any, timeout: float = API_TIMEOUT
) -> dict[str, Any]:
    body = str(text or "").strip()
    if not body:
        raise TelegramConfigError("لا يمكن إرسال رسالة تيليغرام فارغة.")
    if len(body) > MAX_BODY:
        raise TelegramConfigError(
            f"نص الرسالة يتجاوز حد تيليغرام ({MAX_BODY} حرف). اختصر النص ثم أعد المحاولة."
        )
    if _value(settings, "telegram_enabled", "0") != "1":
        raise TelegramConfigError("قناة تيليغرام معطّلة في إعدادات الرسائل.")
    if _value(settings, "sms_test_mode", "1") == "1":
        return {
            "ok": True,
            "simulated": True,
            "provider": "telegram",
            "gateway": "telegram",
            "message_id": "",
            "detail": "وضع الاختبار: تمت محاكاة إرسال تيليغرام دون استخدام الشبكة.",
        }
    token = bot_token(settings)
    chat_id = normalize_chat_id(_value(settings, "telegram_chat_id"))
    if not token_valid(token):
        raise TelegramConfigError(
            "رمز بوت تيليغرام غير مضبوط أو غير صحيح. انسخه من @BotFather في الإعدادات."
        )
    if not chat_id:
        raise TelegramConfigError(
            "معرّف محادثة تيليغرام غير مضبوط. أضف البوت إلى المحادثة ثم أدخل Chat ID."
        )
    result = _request(
        "sendMessage",
        token,
        {"chat_id": chat_id, "text": body, "disable_web_page_preview": True},
        timeout,
    )
    return {
        "ok": True,
        "simulated": False,
        "provider": "telegram",
        "gateway": "telegram",
        "message_id": str(result.get("message_id") or ""),
        "detail": f"Telegram Bot API — المحادثة {chat_id}",
    }


def verify_bot(settings: dict[str, Any], timeout: float = API_TIMEOUT) -> dict[str, Any]:
    token = bot_token(settings)
    if not token_valid(token):
        raise TelegramConfigError(
            "رمز بوت تيليغرام غير مضبوط أو غير صحيح. انسخه من @BotFather في الإعدادات."
        )
    if _value(settings, "sms_test_mode", "1") == "1":
        return {
            "ok": True,
            "simulated": True,
            "name": "",
            "username": "",
            "detail": "وضع الاختبار: تم التحقق من تنسيق رمز البوت دون اتصال بالشبكة.",
        }
    result = _request("getMe", token, {}, timeout)
    return {
        "ok": True,
        "simulated": False,
        "name": str(result.get("first_name") or ""),
        "username": str(result.get("username") or ""),
        "detail": (
            f"تم التحقق من البوت @{result.get('username')}"
            if result.get("username")
            else "تم التحقق من رمز البوت."
        ),
    }
