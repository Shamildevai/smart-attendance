from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

DEFAULT_COUNTRY_CODE = "213"


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
