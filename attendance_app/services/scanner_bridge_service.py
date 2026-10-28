"""Collect barcode scans from one or more Android gateways acting as phone scanners.

The phones keep serving their local HTTP API, so the desktop pulls new scans
instead of exposing its own server to the LAN. Sequence numbers guarantee
each scan is handled once per phone, and a resync handshake skips scans
recorded before the desktop started listening. Every phone keeps its own
cursor, so several entrances can scan at the same time.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlparse

import requests

from ..config import APP_VERSION
from .sms_service import normalize_mobile_url

_SCANS_TIMEOUT = 2.5
_MAX_BATCH = 40
MAX_PHONES = 8
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


_STATE_LOCK = threading.Lock()
_CURSORS: dict[str, int] = {}
_ENDPOINT_LOCKS: dict[str, threading.Lock] = {}


def _endpoint_lock(endpoint: str) -> threading.Lock:
    with _STATE_LOCK:
        lock = _ENDPOINT_LOCKS.get(endpoint)
        if lock is None:
            lock = threading.Lock()
            _ENDPOINT_LOCKS[endpoint] = lock
        return lock


def _get_cursor(endpoint: str) -> int | None:
    with _STATE_LOCK:
        return _CURSORS.get(endpoint)


def _set_cursor(endpoint: str, value: int) -> None:
    with _STATE_LOCK:
        _CURSORS[endpoint] = value


def reset(url: str = "") -> None:
    with _STATE_LOCK:
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

    lock = _endpoint_lock(endpoint)
    if not lock.acquire(blocking=False):
        return _blank("busy")
    try:
        return _poll_locked(endpoint, api_key or "")
    finally:
        lock.release()


def _poll_locked(endpoint: str, api_key: str) -> dict[str, Any]:
    cursor = _get_cursor(endpoint)
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
            _set_cursor(endpoint, last_seq)
            result = _blank("outdated", "حدّث تطبيق الهاتف لتفعيل القارئ اللاسلكي.")
            result["version"] = version
            result["last_seq"] = last_seq
            return result
        if last_seq < cursor:
            _set_cursor(endpoint, 0)
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

    _set_cursor(endpoint, highest)
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


# ---------------------------------------------------------------------------
# Additional scanning phones
# ---------------------------------------------------------------------------


def phone_id(url: str) -> str:
    endpoint = scans_endpoint(url)
    if not endpoint:
        return ""
    return hashlib.sha1(endpoint.encode("utf-8")).hexdigest()[:12]


def _clean_phone(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    endpoint = normalize_mobile_url(raw.get("url") or "")
    if not endpoint:
        return None
    return {
        "id": phone_id(endpoint),
        "name": str(raw.get("name") or "").strip()[:40],
        "url": endpoint,
        "key": str(raw.get("key") or "").strip(),
    }


def parse_phones(raw: Any) -> list[dict[str, str]]:
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw) if raw.strip() else []
        except ValueError:
            data = []
    if not isinstance(data, list):
        return []
    phones: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in data:
        phone = _clean_phone(item)
        if not phone or phone["id"] in seen:
            continue
        seen.add(phone["id"])
        phones.append(phone)
        if len(phones) >= MAX_PHONES:
            break
    return phones


def dump_phones(phones: list[dict[str, str]]) -> str:
    return json.dumps(phones, ensure_ascii=False)


def additional_phones(settings: dict[str, str]) -> list[dict[str, str]]:
    return parse_phones(settings.get("scanner_phones"))


def public_phones(phones: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{key: phone[key] for key in ("id", "name", "url")} for phone in phones]


def scan_targets(settings: dict[str, str]) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    seen: set[str] = set()
    primary = _clean_phone(
        {
            "url": settings.get("sms_mobile_url", ""),
            "key": settings.get("sms_mobile_key", ""),
        }
    )
    if primary:
        targets.append({**primary, "primary": True})
        seen.add(primary["id"])
    for phone in additional_phones(settings):
        if phone["id"] in seen:
            continue
        targets.append({**phone, "primary": False})
        seen.add(phone["id"])
    return targets


def register_phone(settings: dict[str, str], *, name: str, url: str, key: str) -> str:
    phone = _clean_phone({"name": name, "url": url, "key": key})
    if not phone:
        raise ValueError("رابط بوابة الهاتف غير صالح.")
    phones = additional_phones(settings)
    existing = next((item for item in phones if item["id"] == phone["id"]), None)
    if existing is not None:
        existing["key"] = phone["key"]
        if phone["name"]:
            existing["name"] = phone["name"]
        return dump_phones(phones)
    if len(phones) >= MAX_PHONES:
        raise ValueError("لا يمكن إضافة المزيد من هواتف المسح. أزل هاتفًا أولًا.")
    phones.append(phone)
    return dump_phones(phones)


def remove_phone(settings: dict[str, str], target_id: str) -> tuple[str, bool]:
    phones = additional_phones(settings)
    kept = [phone for phone in phones if phone["id"] != target_id]
    return dump_phones(kept), len(kept) != len(phones)


def _phone_row(target: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": target["id"],
        "name": target["name"],
        "url": target["url"],
        "primary": bool(target["primary"]),
        "state": result.get("state") or "error",
        "version": result.get("version") or "",
        "pending": int(result.get("pending") or 0),
        "detail": result.get("detail") or "",
    }


def _aggregate_state(states: list[str]) -> str:
    if not states:
        return "not_configured"
    working = [state for state in states if state in ("ok", "resynced", "busy")]
    if working:
        if len(working) == len(states):
            if all(state in ("resynced", "busy") for state in states) and "resynced" in states:
                return "resynced"
            return "ok"
        return "partial"
    if all(state == "busy" for state in states):
        return "busy"
    for candidate in ("unauthorized", "outdated", "error", "offline", "busy", "resynced"):
        if candidate in states:
            return candidate
    return "error"


def poll_all(settings: dict[str, str]) -> dict[str, Any]:
    targets = scan_targets(settings)
    if not targets:
        return {**_blank("not_configured"), "phones": []}

    with ThreadPoolExecutor(max_workers=min(MAX_PHONES + 1, len(targets))) as pool:
        results = list(pool.map(lambda target: poll(target["url"], target["key"]), targets))

    scans: list[dict[str, Any]] = []
    phones: list[dict[str, Any]] = []
    states: list[str] = []
    pending = 0
    version = ""
    last_seq = 0
    handshake = False
    failure_detail = ""
    failure_name = ""
    for target, result in zip(targets, results):
        for item in result.get("scans") or []:
            scans.append(
                {
                    **item,
                    "phone": target["name"],
                    "phone_id": target["id"],
                    "primary": bool(target["primary"]),
                }
            )
        pending += int(result.get("pending") or 0)
        if not version and result.get("version"):
            version = str(result["version"])
        last_seq = max(last_seq, int(result.get("last_seq") or 0))
        handshake = handshake or bool(result.get("handshake"))
        states.append(result.get("state") or "error")
        if not failure_detail and result.get("state") not in ("ok", "resynced", "busy"):
            failure_detail = str(result.get("detail") or "")
            failure_name = target["name"]
        phones.append(_phone_row(target, result))

    state = _aggregate_state(states)
    detail = ""
    if state == "partial":
        failed = len([value for value in states if value not in ("ok", "resynced", "busy")])
        detail = f"تعذر الوصول إلى {failed} من {len(states)} هواتف مسح. ما زال المسح يعمل من الهواتف المتصلة."
    elif state not in ("ok", "resynced", "busy"):
        detail = failure_detail

    result = _blank(state, detail)
    result.update(
        version=version,
        scans=scans,
        pending=pending,
        last_seq=last_seq,
        handshake=handshake,
        phones=phones,
    )
    return result


def _probe(endpoint: str, api_key: str) -> dict[str, Any]:
    try:
        response = _get(endpoint, api_key, {"resync": 1})
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
    if version and _version_tuple(version) < _version_tuple(_REQUIRED_SCANNER_VERSION):
        result = _blank("outdated", "حدّث تطبيق الهاتف لتفعيل القارئ اللاسلكي.")
        result["version"] = version
        return result
    result = _blank("ok")
    result.update(version=version, last_seq=_as_int(payload.get("lastSeq")))
    return result


def probe_phones(settings: dict[str, str]) -> dict[str, Any]:
    targets = scan_targets(settings)
    if not targets:
        return {"primary": None, "phones": [], "configured": False}
    with ThreadPoolExecutor(max_workers=min(MAX_PHONES + 1, len(targets))) as pool:
        results = list(
            pool.map(lambda target: _probe(scans_endpoint(target["url"]), target["key"]), targets)
        )
    primary = None
    phones = []
    for target, result in zip(targets, results):
        row = _phone_row(target, result)
        if target["primary"]:
            primary = row
        else:
            phones.append(row)
    return {"primary": primary, "phones": phones, "configured": True}


def _as_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0
