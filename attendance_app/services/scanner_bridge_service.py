"""Collect barcode scans from the Android gateway acting as a phone scanner.

The phone keeps serving its local HTTP API, so the desktop pulls new scans
instead of exposing its own server to the LAN. Sequence numbers guarantee
each scan is handled once, and a resync handshake skips scans recorded before
the desktop started listening.
"""

from __future__ import annotations

import re
import threading
from typing import Any
from urllib.parse import urlparse

import requests

from ..config import APP_VERSION, GATEWAY_APP_VERSION
from .sms_service import normalize_mobile_url

_SCANS_TIMEOUT = 2.5
_MAX_BATCH = 40
_REQUIRED_SCANNER_VERSION = "1.1.0"


def _version_tuple(value: Any) -> tuple[int, ...]:
    parts = re.findall(r"\d+", str(value or ""))
    return tuple(int(part) for part in parts[:4]) if parts else ()


def scans_endpoint(url: str) -> str:
    normalized = normalize_mobile_url(url)
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    if not parsed.hostname:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc}/scans"


def _blank(state: str, detail: str = "") -> dict[str, Any]:
    return {
        "state": state,
        "detail": detail,
        "version": "",
        "scans": [],
        "pending": 0,
        "last_seq": 0,
        "handshake": False,
        "required_version": _REQUIRED_SCANNER_VERSION,
    }


_LOCK = threading.Lock()
_CURSORS: dict[str, int] = {}


def reset(url: str = "") -> None:
    with _LOCK:
        if url:
            endpoint = scans_endpoint(url)
            if endpoint:
                _CURSORS.pop(endpoint, None)
        else:
            _CURSORS.clear()


def _headers(api_key: str) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "User-Agent": f"SmartAttendance/{APP_VERSION}",
        "X-Client-App": "SmartAttendance",
        "Connection": "close",
    }
    if api_key:
        headers["X-Api-Key"] = api_key
    return headers


def _get(endpoint: str, api_key: str, params: dict[str, Any]) -> requests.Response:
    return requests.get(
        endpoint,
        params=params,
        headers=_headers(api_key),
        timeout=_SCANS_TIMEOUT,
        allow_redirects=False,
    )


def poll(url: str, api_key: str = "") -> dict[str, Any]:
    endpoint = scans_endpoint(url)
    if not endpoint:
        if not str(url or "").strip():
            return _blank("not_configured")
        return _blank("invalid_url")

    if not _LOCK.acquire(blocking=False):
        return _blank("busy")
    try:
        return _poll_locked(endpoint, api_key or "")
    finally:
        _LOCK.release()


def _poll_locked(endpoint: str, api_key: str) -> dict[str, Any]:
    cursor = _CURSORS.get(endpoint)
    handshake = cursor is None
    params: dict[str, Any] = {"resync": 1} if handshake else {"after": cursor}
    try:
        response = _get(endpoint, api_key, params)
    except requests.exceptions.Timeout:
        return _blank("offline", "انتهت مهلة الاتصال بتطبيق الهاتف.")
    except requests.exceptions.ConnectionError:
        return _blank("offline", "تعذر الوصول إلى تطبيق الهاتف على الشبكة.")
    except requests.RequestException:
        return _blank("offline", "تعذر الاتصال بتطبيق الهاتف.")

    if response.status_code in (401, 403):
        return _blank("unauthorized", "مفتاح واجهة الهاتف غير صحيح.")
    if response.status_code == 404:
        return _blank("outdated", "تطبيق الهاتف لا يدعم القارئ اللاسلكي.")
    if response.status_code != 200:
        return _blank("error", f"استجابة غير متوقعة من الهاتف ({response.status_code}).")

    try:
        payload = response.json()
    except ValueError:
        return _blank("error", "استجابة الهاتف ليست JSON صالحًا.")
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return _blank("error", "رفض تطبيق الهاتف الطلب.")

    version = str(payload.get("version") or "")
    last_seq = _as_int(payload.get("lastSeq"))

    if not handshake:
        if payload.get("scanner") is False or (
            version and _version_tuple(version) < _version_tuple(_REQUIRED_SCANNER_VERSION)
        ):
            _CURSORS[endpoint] = last_seq
            result = _blank("outdated", "حدّث تطبيق الهاتف لتفعيل القارئ اللاسلكي.")
            result["version"] = version
            result["last_seq"] = last_seq
            return result
        if last_seq < cursor:
            _CURSORS[endpoint] = 0
            result = _blank("resynced", "أُعيدت مزامنة عدّاد المسوحات بعد إعادة تشغيل الهاتف.")
            result["version"] = version
            result["last_seq"] = last_seq
            return result

    scans: list[dict[str, Any]] = []
    highest = cursor if cursor is not None else last_seq
    for item in payload.get("scans") or []:
        if not isinstance(item, dict):
            continue
        seq = _as_int(item.get("seq"))
        code = str(item.get("code") or "").strip()
        if seq <= 0 or not code or (cursor is not None and seq <= cursor):
            continue
        scans.append({"seq": seq, "code": code, "at": _as_int(item.get("at"))})
        highest = max(highest, seq)
        if len(scans) >= _MAX_BATCH:
            break

    if handshake:
        pass
    elif not scans and last_seq > highest:
        highest = last_seq

    _CURSORS[endpoint] = highest
    pending = max(0, last_seq - highest)
    result = _blank("ok")
    result.update(
        version=version,
        scans=scans,
        pending=pending,
        last_seq=last_seq,
        handshake=handshake,
    )
    return result


def _as_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0
