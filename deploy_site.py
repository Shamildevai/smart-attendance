from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from ftplib import FTP, FTP_TLS, error_perm
from pathlib import Path

APP_TITLE = "نشر موقع SmartAttendance"
CONFIG_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "SmartAttendanceDeploy"
CONFIG_FILE = CONFIG_DIR / "config.json"


def _default_staging() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
        candidates = [base / "site_upload", base.parent / "site_upload", Path.cwd() / "site_upload"]
        for candidate in candidates:
            if candidate.is_dir():
                return candidate
        return candidates[0]
    return Path(__file__).resolve().parent / "site_upload"


STAGING_DIR = _default_staging()

CATEGORY_ORDER = ("assets", "builds", "archive", "notes", "pages", "manifests")
CATEGORY_LABELS = {
    "pages": "الصفحات (index.html + archive)",
    "assets": "الصور (imgs)",
    "manifests": "ملفات JSON الموقّعة",
    "builds": "الملفات الحالية (builds)",
    "archive": "الأرشيف (builds/archive)",
    "notes": "ملاحظات الإصدار",
}
ALWAYS_SEND = {"pages", "manifests", "notes"}
TEXT_SUFFIXES = {".html", ".json", ".txt"}
CHUNK = 64 * 1024

DEFAULT_CONFIG = {
    "protocol": "ftps",
    "host": "",
    "port": 21,
    "username": "",
    "password": "",
    "site1_root": "/public_html/smartattendance",
    "site2_root": "/public_html/Update",
    "site1_url": "https://smartattendance.fetsync.org/",
    "site2_url": "https://fetsync.org/Update/",
    "remember_password": False,
    "skip_same_size": True,
    "verify": True,
}


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"


def category_for(relative: str) -> str | None:
    parts = relative.split("/")
    if relative in ("index.html", "archive/index.html"):
        return "pages"
    if parts[0] == "imgs" and len(parts) > 1:
        return "assets"
    if relative in ("latest.json", "full/latest.json", "companion/latest.json"):
        return "manifests"
    if parts[0] == "builds" and len(parts) == 2:
        return "builds"
    if parts[0] == "builds" and len(parts) == 3 and "archive" in parts:
        return "archive"
    if relative.startswith("release_notes_") and relative.endswith(".txt"):
        return "notes"
    if parts[0] == "full" and len(parts) == 2 and parts[1].startswith("release_notes_"):
        return "notes"
    return None


@dataclass
class DeployItem:
    relative: str
    size: int
    category: str
    local: Path
    targets: list[tuple[str, str]] = field(default_factory=list)
    status: str = "pending"
    detail: str = ""


def build_plan(
    staging: Path,
    categories: set[str],
    site1_root: str,
    site2_root: str,
) -> list[DeployItem]:
    staging = Path(staging)
    roots = []
    for root in (site1_root, site2_root):
        root = (root or "").replace("\\", "/").strip()
        if root and root not in roots:
            roots.append(root if root.startswith("/") else "/" + root)
    items: list[DeployItem] = []
    for path in staging.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(staging).as_posix()
        if relative in ("UPLOAD.txt", "SHA256SUMS.txt") or relative.startswith("licensing_patch/"):
            continue
        category = category_for(relative)
        if category is None or category not in categories:
            continue
        item = DeployItem(
            relative=relative,
            size=path.stat().st_size,
            category=category,
            local=path,
        )
        for root in roots:
            item.targets.append((root, posixpath.join(root, relative)))
        items.append(item)
    items.sort(key=lambda entry: (CATEGORY_ORDER.index(entry.category), entry.relative))
    return items


def preflight(staging: Path) -> list[str]:
    staging = Path(staging)
    warnings: list[str] = []
    versions = {}
    for manifest in ("latest.json", "full/latest.json", "companion/latest.json"):
        path = staging / manifest
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            versions[manifest] = str(data.get("version") or "")
        except (OSError, ValueError) as error:
            warnings.append(f"{manifest}: غير قابل للقراءة ({error})")
    page = staging / "index.html"
    try:
        page_text = page.read_text(encoding="utf-8")
    except OSError as error:
        page_text = ""
        warnings.append(f"index.html: غير قابل للقراءة ({error})")
    for manifest, version in versions.items():
        if version and page_text and version not in page_text:
            warnings.append(f"index.html لا يذكر الإصدار {version} الموجود في {manifest}")
    references = set(re.findall(r"https://fetsync\.org/Update/builds/([\w.\-]+)", page_text))
    references |= set(re.findall(r"https://fetsync\.org/Update/builds/([\w.\-]+)", "".join(
        (staging / name).read_text(encoding="utf-8", errors="replace")
        for name in ("latest.json", "full/latest.json", "companion/latest.json")
        if (staging / name).is_file()
    )))
    for name in sorted(references):
        if "/" in name:
            continue
        if not (staging / "builds" / name).is_file() and not (staging / "builds" / "archive" / name).is_file():
            warnings.append(f"رابط في الصفحة/المانيفست يشير إلى ملف غير موجود محليًا: builds/{name}")
    archive_page = staging / "archive" / "index.html"
    if archive_page.is_file():
        archive_text = archive_page.read_text(encoding="utf-8", errors="replace")
        archive_refs = set(re.findall(r"https://fetsync\.org/Update/builds/archive/([\w.\-]+)", archive_text))
        for name in sorted(archive_refs):
            if not (staging / "builds" / "archive" / name).is_file():
                warnings.append(f"صفحة الأرشيف تشير إلى ملف غير موجود: builds/archive/{name}")
    return warnings


class Uploader:
    def __init__(self, config: dict, log):
        self.config = config
        self.log = log
        self.ftp = None

    def connect(self) -> None:
        protocol = (self.config.get("protocol") or "ftps").lower()
        host = self.config["host"].strip()
        if not host:
            raise RuntimeError("اسم الخادم مطلوب")
        port = int(self.config.get("port") or 21)
        timeout = 60
        if protocol in ("ftps", "ftp+tls", "explicit"):
            context = ssl.create_default_context()
            self.ftp = FTP_TLS(context=context, timeout=timeout)
            self.ftp.encoding = "utf-8"
            self.ftp.connect(host, port)
            self.ftp.auth()
            self.ftp.login(self.config["username"], self.config["password"])
            self.ftp.prot_p()
        elif protocol == "ftp":
            self.ftp = FTP(timeout=timeout)
            self.ftp.encoding = "utf-8"
            self.ftp.connect(host, port)
            self.ftp.login(self.config["username"], self.config["password"])
        else:
            raise RuntimeError(f"بروتوكول غير مدعوم: {protocol}")
        self.ftp.set_pasv(True)
        self.log(f"تم الاتصال بـ {host}:{port} ({protocol.upper()})")

    def close(self) -> None:
        if self.ftp is None:
            return
        try:
            self.ftp.quit()
        except Exception:
            try:
                self.ftp.close()
            except Exception:
                pass
        self.ftp = None

    def ensure_dir(self, directory: str) -> None:
        parts = [part for part in directory.split("/") if part]
        current = "/" if directory.startswith("/") else ""
        for part in parts:
            current = posixpath.join(current, part) if current not in ("", "/") else "/" + part
            try:
                self.ftp.cwd(current)
            except error_perm:
                try:
                    self.ftp.mkd(current)
                except error_perm as error:
                    raise RuntimeError(f"تعذر إنشاء المجلد {current}: {error}") from error
        self.ftp.cwd("/")

    def remote_size(self, remote: str) -> int | None:
        try:
            return self.ftp.size(remote)
        except error_perm:
            return None
        except Exception:
            return None

    def upload(self, local: Path, remote: str, progress=None) -> None:
        directory = posixpath.dirname(remote)
        self.ensure_dir(directory)
        temp = f"{remote}.part-{os.getpid()}"
        sent = 0
        total = local.stat().st_size

        def handle(chunk: bytes) -> None:
            nonlocal sent
            sent += len(chunk)
            if progress:
                progress(sent, total)

        try:
            with local.open("rb") as handle_file:
                self.ftp.storbinary(f"STOR {temp}", handle_file, blocksize=CHUNK, callback=handle)
            try:
                self.ftp.rename(temp, remote)
            except error_perm:
                try:
                    self.ftp.delete(remote)
                except error_perm:
                    pass
                self.ftp.rename(temp, remote)
        except Exception:
            try:
                self.ftp.delete(temp)
            except Exception:
                pass
            raise


def http_sha(url: str) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache", "User-Agent": "DeploySite/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        digest = hashlib.sha256()
        for chunk in iter(lambda: response.read(CHUNK), b""):
            digest.update(chunk)
        return response.status, digest.hexdigest()


def http_size(url: str) -> tuple[int, int | None]:
    request = urllib.request.Request(
        url, method="HEAD", headers={"Cache-Control": "no-cache", "User-Agent": "DeploySite/1.0"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        length = response.headers.get("Content-Length")
        return response.status, int(length) if length and length.isdigit() else None


def verify_item(item: DeployItem, config: dict, log) -> None:
    if not config.get("verify", True):
        return
    bases = []
    if config.get("site1_url"):
        bases.append(config["site1_url"].rstrip("/") + "/")
    if config.get("site2_url"):
        bases.append(config["site2_url"].rstrip("/") + "/")
    if not bases:
        return
    local_hash = None
    for base in bases:
        url = base + item.relative
        try:
            if item.local.suffix.lower() in TEXT_SUFFIXES and item.size <= 4 * 1024 * 1024:
                local_hash = local_hash or _sha256(item.local)
                status, remote_hash = http_sha(url)
                if status == 200 and remote_hash == local_hash:
                    item.status = "verified"
                    item.detail = ""
                    return
            else:
                status, size = http_size(url)
                if status == 200 and (size is None or size == item.size):
                    item.status = "verified"
                    item.detail = ""
                    return
        except urllib.error.HTTPError as error:
            if error.code == 404:
                continue
            log(f"تنبيه: تعذر التحقق من {url} (HTTP {error.code})")
        except Exception as error:
            log(f"تنبيه: تعذر التحقق من {url}: {error}")
    item.status = "uploaded"
    item.detail = "لم تُطابق النسخة المنشورة الملف المحلي"
    log(f"تنبيه: {item.relative} رُفع لكن النسخة على الموقع لم تُطابق الملف المحلي")


def http_size(url: str) -> tuple[int, int | None]:
    request = urllib.request.Request(
        url, method="HEAD", headers={"Cache-Control": "no-cache", "User-Agent": "DeploySite/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            length = response.headers.get("Content-Length")
            return response.status, int(length) if length and length.isdigit() else None
    except urllib.error.HTTPError as error:
        if error.code not in (403, 405):
            raise
    request = urllib.request.Request(
        url,
        headers={
            "Range": "bytes=0-0",
            "Cache-Control": "no-cache",
            "User-Agent": "DeploySite/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        content_range = response.headers.get("Content-Range") or ""
        match = re.search(r"/(\d+)$", content_range)
        return response.status, int(match.group(1)) if match else None


def run_deploy(
    config: dict,
    categories: set[str],
    staging: Path,
    log,
    progress=None,
    force: bool = False,
    dry_run: bool = False,
) -> list[DeployItem]:
    items = build_plan(staging, categories, config.get("site1_root", ""), config.get("site2_root", ""))
    if dry_run:
        for item in items:
            log(f"{item.category:9} {_human(item.size):>10}  {item.relative}  ->  {item.targets[0][1]}")
        log(f"المجموع: {len(items)} ملفًا، {_human(sum(entry.size for entry in items))}")
        return items
    uploader = Uploader(config, log)
    uploader.connect()
    try:
        for index, item in enumerate(items, 1):
            label = f"[{index}/{len(items)}] {item.relative}"
            targets = [remote for _root, remote in item.targets]
            if config.get("skip_same_size", True) and item.category not in ALWAYS_SEND and not force:
                existing = [uploader.remote_size(remote) for remote in targets]
                if existing and all(size == item.size for size in existing):
                    item.status = "skipped"
                    log(f"{label}: موجود بنفس الحجم — تم التخطي")
                    if progress:
                        progress(index, len(items), item)
                    continue
            log(f"{label} ({_human(item.size)})")
            for _root, remote in item.targets:
                last = {"pct": -1}

                def on_chunk(done: int, total: int, _label=label) -> None:
                    pct = int(done * 100 / total) if total else 100
                    if pct >= last["pct"] + 10 or pct == 100:
                        last["pct"] = pct
                        log(f"    {_label}: {pct}%")

                try:
                    uploader.upload(item.local, remote, on_chunk)
                except Exception as error:
                    raise RuntimeError(f"فشل رفع {item.relative} إلى {remote}: {error}") from error
            item.status = "uploaded"
            verify_item(item, config, log)
            if progress:
                progress(index, len(items), item)
    finally:
        uploader.close()
    return items


def _load_config(path: Path) -> dict:
    data = dict(DEFAULT_CONFIG)
    try:
        loaded = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data.update({key: value for key, value in loaded.items() if key in DEFAULT_CONFIG})
    except (OSError, ValueError):
        pass
    return data


def _save_config(config: dict, path: Path) -> None:
    data = dict(config)
    if not data.get("remember_password"):
        data["password"] = ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="رفع ملفات site_upload إلى الخادم والتحقق منها")
    parser.add_argument("--config", default=str(CONFIG_FILE))
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--protocol", choices=["ftps", "ftp"])
    parser.add_argument("--user")
    parser.add_argument("--password")
    parser.add_argument("--site1-root")
    parser.add_argument("--site2-root")
    parser.add_argument("--site1-url")
    parser.add_argument("--site2-url")
    parser.add_argument("--only", help="مجموعات مفصولة بفاصلة: pages,assets,manifests,builds,archive,notes")
    parser.add_argument("--staging", default=str(STAGING_DIR))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-verify", action="store_true")
    args = parser.parse_args(argv)

    config = _load_config(Path(args.config))
    for name in ("host", "protocol", "user", "password", "site1_root", "site2_root", "site1_url", "site2_url"):
        value = getattr(args, name)
        if value is not None:
            config["username" if name == "user" else name] = value
    if args.port is not None:
        config["port"] = args.port
    if args.no_verify:
        config["verify"] = False
    if args.only:
        categories = {part.strip() for part in args.only.split(",") if part.strip()}
    else:
        categories = set(CATEGORY_ORDER)

    staging = Path(args.staging)
    warnings = preflight(staging)
    for warning in warnings:
        print(f"تنبيه: {warning}")
    try:
        items = run_deploy(
            config,
            categories,
            staging,
            log=print,
            force=args.force,
            dry_run=args.dry_run,
        )
    except Exception as error:
        print(f"فشل: {error}", file=sys.stderr)
        return 1
    if not args.dry_run:
        done = sum(1 for item in items if item.status in ("uploaded", "verified", "skipped"))
        print(f"انتهى: {done}/{len(items)} ملفًا")
        if any(item.status == "uploaded" and item.detail for item in items):
            return 2
    return 0


def gui() -> int:
    import tkinter as tk
    from tkinter import messagebox, ttk

    config = _load_config(CONFIG_FILE)
    root = tk.Tk()
    root.title(APP_TITLE)
    root.geometry("880x760")
    root.minsize(760, 640)

    padding = {"padx": 8, "pady": 4}
    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True, padx=10, pady=8)

    server_tab = ttk.Frame(notebook)
    site_tab = ttk.Frame(notebook)
    deploy_tab = ttk.Frame(notebook)
    notebook.add(server_tab, text="الخادم")
    notebook.add(site_tab, text="مسارات الموقع")
    notebook.add(deploy_tab, text="الرفع والسجل")

    fields: dict[str, tk.Variable] = {}

    def add_field(parent, label, key, row, show=None, width=48):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", **padding)
        var = tk.StringVar(value=str(config.get(key, "")))
        entry = ttk.Entry(parent, textvariable=var, width=width, show=show)
        entry.grid(row=row, column=1, sticky="we", **padding)
        fields[key] = var
        return entry

    ttk.Label(server_tab, text="بيانات حساب FTP أو FTPS (تُحفظ محليًا فقط)").grid(
        row=0, column=0, columnspan=2, sticky="w", **padding
    )
    ttk.Label(server_tab, text="البروتوكول").grid(row=1, column=0, sticky="w", **padding)
    protocol_var = tk.StringVar(value=str(config.get("protocol") or "ftps"))
    fields["protocol"] = protocol_var
    ttk.Combobox(server_tab, textvariable=protocol_var, values=["ftps", "ftp"], width=10).grid(
        row=1, column=1, sticky="w", **padding
    )
    add_field(server_tab, "الخادم", "host", 2)
    add_field(server_tab, "المنفذ", "port", 3)
    add_field(server_tab, "اسم المستخدم", "username", 4)
    password_entry = add_field(server_tab, "كلمة المرور", "password", 5, show="*")
    remember = tk.BooleanVar(value=bool(config.get("remember_password")))
    ttk.Checkbutton(server_tab, text="حفظ كلمة المرور محليًا", variable=remember).grid(
        row=6, column=1, sticky="w", **padding
    )

    def toggle_password() -> None:
        password_entry.configure(show="" if password_entry.cget("show") else "*")

    ttk.Button(server_tab, text="إظهار/إخفاء", command=toggle_password).grid(row=6, column=2, sticky="w", **padding)
    status = tk.StringVar(value="جاهز")
    ttk.Label(server_tab, textvariable=status, foreground="#0b6e4f").grid(
        row=7, column=0, columnspan=3, sticky="w", **padding
    )

    ttk.Label(site_tab, text="مجلد جذر كل موقع داخل حساب FTP").grid(
        row=0, column=0, columnspan=2, sticky="w", **padding
    )
    add_field(site_tab, "جذر smartattendance", "site1_root", 1)
    add_field(site_tab, "جذر fetsync/Update", "site2_root", 2)
    add_field(site_tab, "رابط smartattendance", "site1_url", 3)
    add_field(site_tab, "رابط fetsync/Update", "site2_url", 4)
    ttk.Label(
        site_tab,
        text="مثال: /public_html/smartattendance و/public_html/Update أو /home/USER/public_html/...",
        foreground="#666",
    ).grid(row=5, column=0, columnspan=3, sticky="w", **padding)

    def test_connection() -> None:
        collect()
        testing = dict(config)

        def work() -> None:
            try:
                uploader = Uploader(testing, lambda text: root.after(0, log_line, text))
                uploader.connect()
                for root_path in (testing.get("site1_root"), testing.get("site2_root")):
                    if root_path:
                        uploader.ftp.cwd(root_path)
                        uploader.ftp.cwd("/")
                        root.after(0, log_line, f"المسار موجود: {root_path}")
                uploader.close()
                root.after(0, status.set, "الاتصال ناجح")
            except Exception as error:
                root.after(0, status.set, f"فشل الاتصال: {error}")
                root.after(0, log_line, f"فشل الاتصال: {error}")

        threading.Thread(target=work, daemon=True).start()

    ttk.Button(site_tab, text="اختبار الاتصال والمسارات", command=test_connection).grid(
        row=6, column=1, sticky="w", **padding
    )

    category_vars: dict[str, tk.BooleanVar] = {}
    ttk.Label(deploy_tab, text=f"مجلد الرفع: {STAGING_DIR}").grid(
        row=0, column=0, columnspan=2, sticky="w", **padding
    )
    ttk.Label(deploy_tab, text="ما الذي تريد رفعه؟").grid(row=1, column=0, sticky="w", **padding)
    for index, category in enumerate(CATEGORY_ORDER):
        var = tk.BooleanVar(value=True)
        category_vars[category] = var
        ttk.Checkbutton(deploy_tab, text=CATEGORY_LABELS[category], variable=var).grid(
            row=2 + index // 2, column=index % 2, sticky="w", **padding
        )
    force = tk.BooleanVar(value=False)
    verify = tk.BooleanVar(value=bool(config.get("verify", True)))
    skip_same = tk.BooleanVar(value=bool(config.get("skip_same_size", True)))
    ttk.Checkbutton(deploy_tab, text="إعادة رفع كل شيء (تجاهل تطابق الحجم)", variable=force).grid(
        row=6, column=0, columnspan=2, sticky="w", **padding
    )
    ttk.Checkbutton(deploy_tab, text="التحقق من النسخة المنشورة بعد الرفع", variable=verify).grid(
        row=7, column=0, columnspan=2, sticky="w", **padding
    )
    ttk.Checkbutton(deploy_tab, text="تخطي الملفات ذات الحجم نفسه", variable=skip_same).grid(
        row=8, column=0, columnspan=2, sticky="w", **padding
    )

    progress = ttk.Progressbar(deploy_tab, maximum=100)
    progress.grid(row=9, column=0, columnspan=2, sticky="we", **padding)
    log_box = tk.Text(deploy_tab, height=16, wrap="word", state="disabled")
    log_box.grid(row=10, column=0, columnspan=2, sticky="nsew", **padding)
    deploy_tab.rowconfigure(10, weight=1)
    deploy_tab.columnconfigure(0, weight=1)
    deploy_tab.columnconfigure(1, weight=1)
    server_tab.columnconfigure(1, weight=1)
    site_tab.columnconfigure(1, weight=1)

    def log_line(text: str) -> None:
        log_box.configure(state="normal")
        log_box.insert("end", text + "\n")
        log_box.see("end")
        log_box.configure(state="disabled")

    def collect() -> None:
        for key, var in fields.items():
            value = var.get()
            config[key] = int(value) if key == "port" and str(value).isdigit() else value
        config["remember_password"] = bool(remember.get())
        config["verify"] = bool(verify.get())
        config["skip_same_size"] = bool(skip_same.get())

    busy = {"active": False}
    buttons: list[ttk.Button] = []

    def preview() -> None:
        if busy["active"]:
            return
        collect()
        categories = {name for name, var in category_vars.items() if var.get()}
        warnings = preflight(STAGING_DIR)
        log_line("— معاينة —")
        for warning in warnings:
            log_line(f"تنبيه: {warning}")
        items = build_plan(STAGING_DIR, categories, config["site1_root"], config["site2_root"])
        for item in items:
            log_line(f"{item.category:9} {_human(item.size):>10}  {item.relative}")
        log_line(f"المجموع: {len(items)} ملفًا، {_human(sum(item.size for item in items))}")

    def deploy() -> None:
        if busy["active"]:
            return
        collect()
        _save_config(config, CONFIG_FILE)
        categories = {name for name, var in category_vars.items() if var.get()}
        if not categories:
            messagebox.showwarning(APP_TITLE, "اختر مجموعة واحدة على الأقل")
            return
        warnings = preflight(STAGING_DIR)
        for warning in warnings:
            log_line(f"تنبيه: {warning}")
        if not messagebox.askyesno(APP_TITLE, "سيتم استبدال ملفات الموقع والمانيفستات. المتابعة؟"):
            return
        busy["active"] = True
        for button in buttons:
            button.configure(state="disabled")
        status.set("جارٍ الرفع…")

        def on_progress(index: int, total: int, item: DeployItem) -> None:
            root.after(0, lambda: progress.configure(value=int(index * 100 / total)))
            root.after(0, lambda: status.set(f"{index}/{total} — {item.status}"))

        def work() -> None:
            started = time.time()
            try:
                run_deploy(
                    config,
                    categories,
                    STAGING_DIR,
                    log=lambda text: root.after(0, log_line, text),
                    progress=on_progress,
                    force=bool(force.get()),
                )
                root.after(0, lambda: status.set(f"انتهى في {int(time.time() - started)} ثانية"))
            except Exception as error:
                root.after(0, lambda: status.set("توقف بسبب خطأ"))
                root.after(0, log_line, f"خطأ: {error}")
                root.after(0, messagebox.showerror, APP_TITLE, str(error))
            finally:
                busy["active"] = False
                for button in buttons:
                    root.after(0, lambda b=button: b.configure(state="normal"))

        threading.Thread(target=work, daemon=True).start()

    preview_button = ttk.Button(deploy_tab, text="معاينة الخطة", command=preview)
    preview_button.grid(row=11, column=0, sticky="we", **padding)
    deploy_button = ttk.Button(deploy_tab, text="رفع الآن", command=deploy)
    deploy_button.grid(row=11, column=1, sticky="we", **padding)
    buttons.extend([preview_button, deploy_button])

    root.mainloop()
    return 0


def main() -> int:
    _configure_console()
    if len(sys.argv) > 1 and sys.argv[1] in ("--cli", "upload"):
        argv = sys.argv[2:] if sys.argv[1] == "--cli" else sys.argv[1:]
        return cli(argv)
    if len(sys.argv) > 1:
        return cli(sys.argv[1:])
    return gui()


if __name__ == "__main__":
    raise SystemExit(main())
