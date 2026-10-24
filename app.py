from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from waitress.server import create_server

from attendance_app import create_app
from attendance_app.config import APP_NAME, APP_TITLE, APP_VERSION, default_data_dir, ensure_data_layout

DEFAULT_PORT = 8765
HEALTH_TIMEOUT = 1.2
PORT_SCAN_SPAN = 30
INSTANCE_FILE = ".instance.json"


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def _console_print(message: str, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    if stream is None:
        return
    try:
        print(message, file=stream)
    except (OSError, ValueError):
        pass


def _is_our_app(port: int, host: str = "127.0.0.1") -> bool:
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{probe_host}:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=HEALTH_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload.get("ok") is True and payload.get("app") == "StudentAttendance"
    except (urllib.error.URLError, ValueError, OSError, TimeoutError):
        return False


def _port_is_free(port: int, host: str) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            return True
        except OSError:
            return False


def _choose_port(host: str, preferred: int, span: int = 30) -> int | None:
    for candidate in range(preferred, min(65535, preferred + span) + 1):
        if _port_is_free(candidate, host):
            return candidate
    return None


def _instance_mutex_name(data_dir: Path) -> str:
    digest = hashlib.sha1(str(Path(data_dir).resolve()).lower().encode("utf-8")).hexdigest()[:16]
    return f"Local\\SmartAttendance.{digest}"


def _acquire_instance_mutex(data_dir: Path):
    if os.name != "nt":
        return True
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.CreateMutexW(None, True, _instance_mutex_name(data_dir))
    if not handle:
        return True
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return None
    return handle


def _release_instance_mutex(handle) -> None:
    if os.name != "nt" or not handle or handle is True:
        return
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    try:
        kernel32.ReleaseMutex(handle)
    finally:
        kernel32.CloseHandle(handle)


def _instance_path(data_dir: Path) -> Path:
    return Path(data_dir) / INSTANCE_FILE


def _read_instance(data_dir: Path) -> dict:
    try:
        data = json.loads(_instance_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    try:
        data["pid"] = int(data.get("pid") or 0)
        data["port"] = int(data.get("port") or 0)
    except (TypeError, ValueError):
        return {}
    return data


def _write_instance(data_dir: Path, host: str, port: int) -> None:
    payload = {
        "pid": os.getpid(),
        "port": port,
        "host": host,
        "data_dir": str(data_dir),
        "started": time.time(),
    }
    try:
        _instance_path(data_dir).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _clear_instance(data_dir: Path) -> None:
    info = _read_instance(data_dir)
    if info and info.get("pid") not in (0, os.getpid()):
        return
    try:
        _instance_path(data_dir).unlink()
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _port_open(port: int, host: str) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        try:
            return sock.connect_ex((host, port)) == 0
        except OSError:
            return False


def _find_running_instance(data_dir: Path, host: str, preferred_port: int) -> dict:
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    info = _read_instance(data_dir)
    ports: list[int] = []
    if info.get("port"):
        ports.append(info["port"])
    if preferred_port:
        ports.append(preferred_port)
    ports.extend(range(DEFAULT_PORT, DEFAULT_PORT + PORT_SCAN_SPAN + 1))
    seen: set[int] = set()
    for port in ports:
        if not port or port in seen:
            continue
        seen.add(port)
        if not _port_open(port, probe_host):
            continue
        if _is_our_app(port, host):
            result = dict(info) if info.get("port") == port else {}
            result["port"] = port
            return result
    return {}


def _wait_for_instance(
    data_dir: Path, host: str, preferred_port: int, timeout: float = 8.0
) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        info = _read_instance(data_dir)
        if info.get("port") and _pid_alive(info.get("pid", 0)):
            if _is_our_app(info["port"], host):
                return info
        else:
            found = _find_running_instance(data_dir, host, preferred_port)
            if found:
                return found
        if time.monotonic() >= deadline:
            return _find_running_instance(data_dir, host, preferred_port)
        time.sleep(0.3)


def focus_app_window() -> bool:
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.FlashWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
    user32.FlashWindow.restype = wintypes.BOOL

    matches: list[int] = []

    def collect(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if APP_TITLE in buffer.value or APP_NAME in buffer.value:
            matches.append(hwnd)
        return True

    callback = enum_proc(collect)
    user32.EnumWindows(callback, 0)
    for hwnd in matches:
        user32.ShowWindow(hwnd, 9 if user32.IsIconic(hwnd) else 5)
        if user32.SetForegroundWindow(hwnd):
            return True
        user32.BringWindowToTop(hwnd)
        user32.FlashWindow(hwnd, True)
        return True
    return False


def show_app_window(url: str, data_dir: Path, started_at: float = 0.0) -> None:
    try:
        started = float(started_at or 0)
    except (TypeError, ValueError):
        started = 0.0
    startup_grace = 4.5 if started and (time.time() - started) < 8 else 0.9
    deadline = time.monotonic() + startup_grace
    while True:
        if focus_app_window():
            return
        if time.monotonic() >= deadline:
            break
        time.sleep(0.3)
    open_app_window(url, data_dir)


def _browser_candidates() -> list[Path]:
    env = os.environ
    local = env.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
    candidates = [
        Path(env.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
        Path(env.get("ProgramFiles", r"C:\Program Files")) / "Microsoft/Edge/Application/msedge.exe",
        Path(local) / "Microsoft/Edge/Application/msedge.exe",
        Path(env.get("ProgramFiles", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(env.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
        Path(local) / "Google/Chrome/Application/chrome.exe",
    ]
    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def open_app_window(url: str, data_dir: Path) -> subprocess.Popen | None:
    profile_dir = Path(data_dir) / "browser-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    arguments = [
        f"--app={url}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-mode",
        "--disable-session-crashed-bubble",
        "--window-size=1440,940",
        "--lang=ar",
        "--disable-features=Translate,TranslateUI",
    ]
    for executable in _browser_candidates():
        if not executable.exists():
            continue
        try:
            return subprocess.Popen(
                [str(executable), *arguments],
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            continue
    try:
        webbrowser.open(url, new=1)
    except Exception:
        pass
    return None


def show_error(message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(APP_TITLE, message)
        root.destroy()
    except Exception:
        _console_print(message, error=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"{APP_TITLE} — {APP_NAME} {APP_VERSION}")
    parser.add_argument("--host", default="127.0.0.1", help="عنوان الاستماع (الافتراضي 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"المنفذ المفضل (الافتراضي {DEFAULT_PORT})")
    parser.add_argument("--data-dir", default="", help="مجلد البيانات (قاعدة البيانات والصور والسجلات)")
    parser.add_argument("--no-browser", action="store_true", help="تشغيل الخادم دون فتح نافذة المتصفح")
    parser.add_argument("--no-scheduler", action="store_true", help="تعطيل الرصد التلقائي للغياب (وضع الاختبار)")
    parser.add_argument("--version", action="store_true", help="عرض الإصدار والخروج")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    args = parse_args(argv)
    if args.version:
        _console_print(f"{APP_TITLE} {APP_VERSION}")
        return 0

    if args.data_dir:
        os.environ["ATTENDANCE_DATA_DIR"] = str(Path(args.data_dir).expanduser().resolve())
    data_dir = default_data_dir()
    ensure_data_layout(data_dir)
    probe_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host

    mutex = _acquire_instance_mutex(data_dir)
    if mutex is None:
        existing = _wait_for_instance(data_dir, args.host, args.port)
        if existing:
            port = int(existing.get("port") or args.port)
            base_url = f"http://{probe_host}:{port}/"
            if not args.no_browser:
                show_app_window(base_url, data_dir, existing.get("started", 0))
            _console_print(f"{APP_TITLE} يعمل بالفعل على {base_url}")
            return 0
        message = (
            "التطبيق يعمل بالفعل في نسخة أخرى، لكن تعذر العثور على نافذته.\n"
            "أغلق النسخة القديمة أو أعد تشغيل الجهاز ثم حاول مجددًا."
        )
        show_error(message)
        _console_print(message, error=True)
        return 1

    port = _choose_port(args.host, args.port)
    if port is None:
        _release_instance_mutex(mutex)
        message = f"تعذر العثور على منفذ متاح بدءًا من {args.port}."
        show_error(message)
        _console_print(message, error=True)
        return 1
    if port != args.port:
        _console_print(f"المنفذ {args.port} غير متاح؛ سيُستخدم المنفذ {port}.")

    overrides = {"DISABLE_SCHEDULER": True} if args.no_scheduler else None
    try:
        app = create_app(overrides)
    except Exception as error:
        _release_instance_mutex(mutex)
        show_error(f"تعذر تشغيل قاعدة البيانات أو التطبيق:\n{error}")
        raise

    url = f"http://{probe_host}:{port}/"
    try:
        server = create_server(
            app,
            host=args.host,
            port=port,
            threads=10,
            channel_timeout=120,
            connection_limit=200,
            clear_untrusted_proxy_headers=True,
            ident=APP_NAME,
        )
    except Exception as error:
        _release_instance_mutex(mutex)
        show_error(f"تعذر تشغيل الخادم المحلي على المنفذ {port}:\n{error}")
        return 1
    _write_instance(data_dir, args.host, port)

    def shutdown() -> None:
        app.logger.info("Shutdown requested from the interface")
        try:
            server.close()
        except Exception:  # pragma: no cover - best effort shutdown
            pass

    app.extensions["shutdown_callback"] = shutdown

    def on_signal(_signum, _frame) -> None:
        threading.Thread(target=shutdown, daemon=True).start()

    try:
        import signal

        signal.signal(signal.SIGINT, on_signal)
        signal.signal(signal.SIGTERM, on_signal)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, on_signal)
    except (ValueError, OSError):
        pass

    _console_print(f"{APP_TITLE} {APP_VERSION}")
    _console_print(f"الخادم المحلي: {url}")
    _console_print(f"مجلد البيانات: {data_dir}")
    _console_print("أغلق النظام من داخل الواجهة أو اضغط Ctrl+C.")

    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(0.7), open_app_window(url, data_dir)), daemon=True).start()

    try:
        server.run()
    except KeyboardInterrupt:
        shutdown()
    finally:
        _clear_instance(data_dir)
        try:
            server.close()
        except Exception:
            pass
        _release_instance_mutex(mutex)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
