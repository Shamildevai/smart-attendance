from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from flask import current_app

from ..config import APP_VERSION, UPDATE_CHECK_INTERVAL, UPDATE_FEED_URL, UPDATE_PUBLIC_KEY
from ..db import bool_setting, get_db, get_settings

MAX_MANIFEST_BYTES = 256 * 1024
MAX_ARTIFACT_BYTES = 400 * 1024 * 1024
CHUNK_SIZE = 128 * 1024
AUTO_INSTALL_DELAY = 120
IDLE_MINUTES = 15
RETRY_SECONDS = 30 * 60
VERSION_RE = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
KINDS = {"portable", "setup"}

_STATE_LOCK = threading.Lock()


class UpdateError(RuntimeError):
    pass


def version_tuple(value: str) -> tuple[int, ...]:
    if not VERSION_RE.fullmatch(value or ""):
        return (0, 0, 0)
    return tuple(int(part) for part in value.split("."))


def is_newer(candidate: str, reference: str = APP_VERSION) -> bool:
    return version_tuple(candidate) > version_tuple(reference)


def canonical_payload(manifest: dict) -> bytes:
    payload = {key: value for key, value in manifest.items() if key != "signature"}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_manifest(manifest) -> dict:
    if not isinstance(manifest, dict):
        raise UpdateError("ملف التحديث غير صالح.")
    signature = manifest.get("signature")
    if not isinstance(signature, str) or not signature.strip():
        raise UpdateError("التحديث غير موقّع ولن يتم تثبيته.")
    try:
        signature_bytes = base64.b64decode(signature, validate=True)
    except Exception as error:
        raise UpdateError("توقيع التحديث مشوّه.") from error

    version = str(manifest.get("version") or "").strip()
    if not VERSION_RE.fullmatch(version):
        raise UpdateError("رقم إصدار التحديث غير صالح.")
    notes = manifest.get("notes", "")
    if not isinstance(notes, str) or len(notes) > 5000:
        raise UpdateError("وصف التحديث غير صالح.")
    files = manifest.get("files")
    if not isinstance(files, list) or not files or len(files) > 10:
        raise UpdateError("قائمة ملفات التحديث غير صالحة.")
    clean_files = []
    for entry in files:
        if not isinstance(entry, dict):
            raise UpdateError("ملف تحديث غير صالح.")
        kind = str(entry.get("kind") or "")
        url = str(entry.get("url") or "").strip()
        digest = str(entry.get("sha256") or "").strip().lower()
        parsed = urlsplit(url)
        try:
            size = int(entry.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if kind not in KINDS:
            raise UpdateError("نوع ملف التحديث غير معروف.")
        if parsed.scheme != "https" or not parsed.hostname:
            raise UpdateError("روابط التحديث يجب أن تكون https.")
        if not SHA256_RE.fullmatch(digest):
            raise UpdateError("بصمة SHA-256 لملف التحديث غير صالحة.")
        if size <= 0 or size > MAX_ARTIFACT_BYTES:
            raise UpdateError("حجم ملف التحديث غير صالح.")
        clean_files.append(
            {
                "kind": kind,
                "url": url,
                "sha256": digest,
                "size": size,
                "name": str(entry.get("name") or "")[:120],
            }
        )
    try:
        schema = int(manifest.get("schema") or 1)
    except (TypeError, ValueError):
        raise UpdateError("إصدار صيغة التحديث غير صالح.")
    clean = {
        "schema": schema,
        "version": version,
        "notes": notes,
        "published_at": str(manifest.get("published_at") or ""),
        "files": clean_files,
        "signature": signature,
    }
    try:
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(UPDATE_PUBLIC_KEY))
        public_key.verify(signature_bytes, canonical_payload(clean))
    except (InvalidSignature, ValueError) as error:
        raise UpdateError("توقيع التحديث غير صالح. تم رفض الملف.") from error
    if not any(entry["kind"] == "portable" for entry in clean_files) and not any(
        entry["kind"] == "setup" for entry in clean_files
    ):
        raise UpdateError("لا يحتوي التحديث على ملف قابل للتثبيت.")
    return clean


def fetch_manifest(feed_url: str) -> dict:
    parsed = urlsplit(feed_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise UpdateError("رابط خادم التحديثات يجب أن يبدأ بـ https://.")
    request = Request(
        feed_url,
        headers={
            "User-Agent": f"SmartAttendance/{APP_VERSION}",
            "Accept": "application/json",
            "Cache-Control": "no-cache",
        },
    )
    try:
        with urlopen(request, timeout=15) as response:
            final_url = getattr(response, "geturl", lambda: feed_url)()
            if urlsplit(final_url).scheme != "https":
                raise UpdateError("أعاد خادم التحديثات اتصالًا غير آمن.")
            raw = response.read(MAX_MANIFEST_BYTES + 1)
    except UpdateError:
        raise
    except HTTPError as error:
        if error.code == 404:
            raise UpdateError("لم يُنشر أي تحديث على الخادم حتى الآن.") from error
        raise UpdateError("تعذر الاتصال بخادم التحديثات. تحقق من الاتصال بالإنترنت.") from error
    except Exception as error:
        raise UpdateError("تعذر الاتصال بخادم التحديثات. تحقق من الاتصال بالإنترنت.") from error
    if len(raw) > MAX_MANIFEST_BYTES:
        raise UpdateError("ملف التحديث أكبر من الحد المسموح.")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UpdateError("ملف التحديث ليس بصيغة JSON صالحة.") from error


def _updates_dir() -> Path:
    path = Path(current_app.config["UPDATES_DIR"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_path(directory: Path | None = None) -> Path:
    return (directory or _updates_dir()) / "state.json"


def load_state(directory: Path | None = None) -> dict:
    path = _state_path(directory)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict, directory: Path | None = None) -> None:
    path = _state_path(directory)
    tmp = path.with_suffix(".tmp")
    with _STATE_LOCK:
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)


def _installed_via_setup() -> bool:
    try:
        return (Path(sys.executable).resolve().parent / "unins000.exe").is_file()
    except OSError:
        return False


def select_asset(manifest: dict) -> dict:
    files = manifest.get("files") or []
    portable = next((item for item in files if item["kind"] == "portable"), None)
    setup = next((item for item in files if item["kind"] == "setup"), None)
    if getattr(sys, "frozen", False):
        if _installed_via_setup() and setup:
            return setup
        if portable:
            return portable
    return portable or setup


def _feed_url(settings: dict) -> str:
    return (settings.get("update_feed_url") or UPDATE_FEED_URL).strip()


def _safe_filename(asset: dict, version: str) -> str:
    original = Path(urlsplit(asset["url"]).path).name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", original or "").strip("._")
    if not stem.lower().endswith(".exe"):
        stem = f"SmartAttendance_{version}.exe"
    return f"update_{version}_{asset['kind']}_{stem}"


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_artifact(asset: dict, version: str) -> dict:
    directory = _updates_dir()
    filename = _safe_filename(asset, version)
    target = directory / filename
    if target.exists() and target.stat().st_size == asset["size"]:
        if _hash_file(target) == asset["sha256"]:
            return {
                "version": version,
                "kind": asset["kind"],
                "filename": filename,
                "sha256": asset["sha256"],
                "size": asset["size"],
                "staged_at": time.time(),
                "trigger": "auto",
            }
        target.unlink(missing_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    request = Request(
        asset["url"],
        headers={"User-Agent": f"SmartAttendance/{APP_VERSION}", "Accept": "application/octet-stream"},
    )
    digest = hashlib.sha256()
    received = 0
    try:
        with urlopen(request, timeout=30) as response:
            if urlsplit(getattr(response, "geturl", lambda: asset["url"])()).scheme != "https":
                raise UpdateError("أعاد خادم التحديثات اتصالًا غير آمن.")
            with part.open("wb") as handle:
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > MAX_ARTIFACT_BYTES:
                        raise UpdateError("ملف التحديث أكبر من الحد المسموح.")
                    digest.update(chunk)
                    handle.write(chunk)
    except UpdateError:
        part.unlink(missing_ok=True)
        raise
    except Exception as error:
        part.unlink(missing_ok=True)
        raise UpdateError("تعذر تنزيل ملف التحديث. أعد المحاولة لاحقًا.") from error
    if received != asset["size"]:
        part.unlink(missing_ok=True)
        raise UpdateError("حجم ملف التحديث لا يطابق البيانات الموقّعة.")
    if digest.hexdigest() != asset["sha256"]:
        part.unlink(missing_ok=True)
        raise UpdateError("بصمة الملف لا تطابق التوقيع. تم حذف التنزيل.")
    os.replace(part, target)
    for stale in directory.glob("update_*.exe"):
        if stale != target:
            stale.unlink(missing_ok=True)
    return {
        "version": version,
        "kind": asset["kind"],
        "filename": filename,
        "sha256": asset["sha256"],
        "size": asset["size"],
        "staged_at": time.time(),
        "trigger": "auto",
    }


def stage_update(trigger: str = "manual") -> dict:
    state = load_state()
    manifest = state.get("update")
    if not manifest:
        raise UpdateError("لا يوجد تحديث متاح للتنزيل.")
    manifest = verify_manifest(manifest)
    asset = select_asset(manifest)
    download = _download_artifact(asset, manifest["version"])
    download["trigger"] = trigger
    state["download"] = download
    state["last_error"] = ""
    save_state(state)
    return download


def _staged_file(state: dict) -> Path | None:
    download = state.get("download") or {}
    raw = download.get("path")
    directory = _updates_dir()
    if raw:
        path = Path(raw)
    elif download.get("filename"):
        path = directory / str(download["filename"])
    else:
        return None
    try:
        path = path.resolve()
        path.relative_to(directory.resolve())
    except (OSError, ValueError):
        return None
    return path if path.is_file() else None


def _verify_staged(state: dict) -> tuple[Path, dict, dict]:
    manifest = state.get("update")
    if not manifest:
        raise UpdateError("لا يوجد تحديث جاهز للتثبيت.")
    manifest = verify_manifest(manifest)
    download = state.get("download") or {}
    if download.get("version") != manifest["version"]:
        raise UpdateError("بيانات التحديث المنزّل غير متطابقة.")
    path = _staged_file(state)
    if path is None:
        raise UpdateError("ملف التحديث المنزّل غير موجود.")
    if path.stat().st_size != int(download.get("size") or 0) or _hash_file(path) != download.get("sha256"):
        raise UpdateError("تغيّر ملف التحديث بعد تنزيله. تم رفض التثبيت.")
    return path, manifest, download


def _clean_env() -> dict[str, str]:
    # PyInstaller's onefile bootstrap exports extraction paths; a relaunched copy
    # must not inherit them or it tries to reuse the terminated process's temp dir.
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("_PYI_") and key not in ("_MEIPASS", "_MEIPASS2")
    }


def install_staged(trigger: str = "manual") -> None:
    if os.name != "nt" or not getattr(sys, "frozen", False):
        raise UpdateError("التثبيت التلقائي متاح فقط في نسخة EXE المبنية من التطبيق.")
    state = load_state()
    path, manifest, download = _verify_staged(state)
    if not is_newer(manifest["version"]):
        raise UpdateError("الإصدار المثبت محدّث بالفعل.")
    callback = current_app.extensions.get("shutdown_callback")
    if not callable(callback):
        raise UpdateError("تعذر تجهيز إعادة التشغيل التلقائية.")

    directory = _updates_dir()
    if download.get("kind") == "portable":
        target = Path(sys.executable).resolve()
        if target == path:
            raise UpdateError("مسار التحديث غير صالح.")
        script = directory / "apply_update.ps1"
        source_literal = str(path).replace("'", "''")
        target_literal = str(target).replace("'", "''")
        cwd_literal = os.getcwd().replace("'", "''")
        launch_args = [arg for arg in sys.argv[1:] if arg]
        args_literal = ", ".join("'" + str(arg).replace("'", "''") + "'" for arg in launch_args)
        script.write_text(
            "$ErrorActionPreference = 'SilentlyContinue'\n"
            f"$targetPid = {os.getpid()}\n"
            "while (Get-Process -Id $targetPid -ErrorAction SilentlyContinue) { Start-Sleep -Seconds 1 }\n"
            "Start-Sleep -Seconds 1\n"
            "$copied = $false\n"
            "for ($i = 0; $i -lt 120; $i++) {\n"
            "  try {\n"
            f"    Copy-Item -LiteralPath '{source_literal}' -Destination '{target_literal}' -Force -ErrorAction Stop\n"
            "    $copied = $true\n"
            "    break\n"
            "  } catch { Start-Sleep -Milliseconds 500 }\n"
            "}\n"
            f"$appArgs = @({args_literal})\n"
            f"if ($copied) {{ if ($appArgs.Count -gt 0) {{ Start-Process -FilePath '{target_literal}' -WorkingDirectory '{cwd_literal}' -ArgumentList $appArgs }} else {{ Start-Process -FilePath '{target_literal}' -WorkingDirectory '{cwd_literal}' }} }}\n"
            "Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force\n",
            encoding="utf-8-sig",
        )
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-File",
                str(script),
            ],
            close_fds=True,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
            cwd=str(target.parent),
            env=_clean_env(),
        )
    else:
        subprocess.Popen(
            [str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/CLOSEAPPLICATIONS", "/NORESTART"],
            close_fds=True,
            env=_clean_env(),
        )

    state["installing"] = True
    state["install_started_at"] = time.time()
    state["install_trigger"] = trigger
    save_state(state)
    current_app.logger.info("Installing update %s (%s)", manifest["version"], trigger)
    threading.Timer(2.0, callback).start()


def _last_activity_iso() -> str | None:
    conn = get_db()
    row = conn.execute(
        """SELECT MAX(stamp) FROM (
               SELECT MAX(created_at) AS stamp FROM attendance_logs
               UNION ALL SELECT MAX(updated_at) FROM sms_logs
               UNION ALL SELECT MAX(updated_at) FROM students
           )"""
    ).fetchone()
    return row[0] if row else None


def _system_idle() -> bool:
    latest = _last_activity_iso()
    if not latest:
        return True
    try:
        stamped = datetime.fromisoformat(str(latest).replace("Z", ""))
    except ValueError:
        return True
    return datetime.now() - stamped >= timedelta(minutes=IDLE_MINUTES)


def status_payload() -> dict:
    state = load_state()
    settings = get_settings(get_db())
    manifest = state.get("update") or {}
    download = state.get("download") or {}
    staged = _staged_file(state) is not None
    auto_update = bool_setting(settings, "auto_update", True)
    available = bool(manifest) and is_newer(manifest.get("version", ""))
    asset = select_asset(manifest) if available else None
    return {
        "current_version": APP_VERSION,
        "auto_update": auto_update,
        "feed_url": _feed_url(settings),
        "update_available": available,
        "latest_version": manifest.get("version", "") if available else "",
        "notes": manifest.get("notes", "") if available else "",
        "published_at": manifest.get("published_at", "") if available else "",
        "download_size": asset["size"] if asset else 0,
        "staged": bool(available and staged and download.get("version") == manifest.get("version")),
        "can_install": bool(
            available and staged and os.name == "nt" and getattr(sys, "frozen", False)
        ),
        "installing": bool(state.get("installing")),
        "last_check": state.get("last_check", ""),
        "last_error": state.get("last_error", ""),
    }


def check_for_update(force: bool = False, raise_errors: bool = False, trigger: str = "manual") -> dict:
    state = load_state()
    settings = get_settings(get_db())
    now = time.time()
    last_ts = float(state.get("last_check_ts") or 0)
    if not force and last_ts and now - last_ts < UPDATE_CHECK_INTERVAL:
        return status_payload()
    error = ""
    try:
        manifest = verify_manifest(fetch_manifest(_feed_url(settings)))
        if is_newer(manifest["version"]):
            previous = state.get("update") or {}
            state["update"] = manifest
            if previous.get("version") != manifest["version"]:
                state.pop("download", None)
        else:
            state["update"] = None
            download = state.get("download") or {}
            if download and not is_newer(download.get("version", "")):
                state.pop("download", None)
    except UpdateError as exc:
        error = str(exc)
        if raise_errors:
            state["last_check"] = datetime.now().isoformat(timespec="seconds")
            state["last_check_ts"] = now
            state["last_error"] = error
            save_state(state)
            raise
    state["last_check"] = datetime.now().isoformat(timespec="seconds")
    state["last_check_ts"] = now
    state["last_error"] = error
    save_state(state)

    if not error and state.get("update") and bool_setting(settings, "auto_update", True):
        download = state.get("download") or {}
        if download.get("version") != state["update"]["version"]:
            try:
                stage_update(trigger=trigger)
            except UpdateError as exc:
                state = load_state()
                state["last_error"] = str(exc)
                save_state(state)
                if raise_errors:
                    raise
    return status_payload()


def auto_tick(app) -> None:
    if app.config.get("TESTING"):
        return
    with app.app_context():
        try:
            settings = get_settings(get_db())
            if not bool_setting(settings, "auto_update", True):
                return
            state = load_state()
            now = time.time()
            last_ts = float(state.get("last_check_ts") or 0)
            due = not last_ts or now - last_ts >= UPDATE_CHECK_INTERVAL
            retry = bool(state.get("last_error")) and now - last_ts >= RETRY_SECONDS
            if due or retry:
                check_for_update(force=True, trigger="auto")
            state = load_state()
            download = state.get("download") or {}
            manifest = state.get("update") or {}
            if (
                state.get("update")
                and download.get("version") == manifest.get("version")
                and not state.get("installing")
                and now - float(download.get("staged_at") or 0) >= AUTO_INSTALL_DELAY
                and _system_idle()
            ):
                install_staged(trigger="auto")
        except UpdateError as exc:
            app.logger.warning("Automatic update deferred: %s", exc)
        except Exception:
            app.logger.exception("Automatic update tick failed")


def cleanup_on_startup(app) -> None:
    directory = Path(app.config.get("UPDATES_DIR") or "")
    if not directory or not directory.is_dir():
        return
    state = load_state(directory)
    for part in directory.glob("*.part"):
        part.unlink(missing_ok=True)
    manifest = state.get("update") or {}
    if manifest and not is_newer(manifest.get("version", "")):
        state["update"] = None
        state["download"] = None
        state["installing"] = False
        save_state(state, directory)
        for stale in directory.glob("update_*.exe"):
            stale.unlink(missing_ok=True)
    elif state.get("installing"):
        state["installing"] = False
        save_state(state, directory)


class UpdateScheduler(threading.Thread):
    """Checks the signed update feed and installs verified releases when idle."""

    def __init__(self, app) -> None:
        super().__init__(name="update-checker", daemon=True)
        self.app = app
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        self._stop_event.wait(10.0)
        while not self._stop_event.is_set():
            auto_tick(self.app)
            self._stop_event.wait(300.0)
