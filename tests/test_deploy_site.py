from __future__ import annotations

import json
from ftplib import error_perm
from pathlib import Path

import pytest

import deploy_site


def make_staging(root: Path) -> Path:
    files = {
        "index.html": "<html>1.1.32 1.7.4 4.5.1</html>",
        "archive/index.html": "<html>archive</html>",
        "imgs/a.png": "png",
        "latest.json": json.dumps({"version": "1.1.32", "files": [{"url": "https://fetsync.org/Update/builds/SmartAttendance.exe"}]}),
        "full/latest.json": json.dumps({"version": "1.7.4"}),
        "companion/latest.json": json.dumps({"version": "4.5.1", "url": "https://fetsync.org/Update/builds/Attendance-Gateway-Companion-4.5.1.apk"}),
        "builds/SmartAttendance.exe": "exe",
        "builds/Attendance-Gateway-Companion-4.5.1.apk": "apk",
        "builds/archive/SmartAttendance_Setup_1.1.31.exe": "old",
        "release_notes_1.1.32.txt": "notes",
        "full/release_notes_1.7.4.txt": "notes",
        "UPLOAD.txt": "ignore",
        "SHA256SUMS.txt": "ignore",
        "licensing_patch/providers.php": "ignore",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def test_category_for_maps_staging_layout():
    assert deploy_site.category_for("index.html") == "pages"
    assert deploy_site.category_for("archive/index.html") == "pages"
    assert deploy_site.category_for("imgs/01.png") == "assets"
    assert deploy_site.category_for("latest.json") == "manifests"
    assert deploy_site.category_for("full/latest.json") == "manifests"
    assert deploy_site.category_for("builds/SmartAttendance.exe") == "builds"
    assert deploy_site.category_for("builds/archive/x.exe") == "archive"
    assert deploy_site.category_for("release_notes_1.1.32.txt") == "notes"
    assert deploy_site.category_for("full/release_notes_1.7.4.txt") == "notes"
    assert deploy_site.category_for("UPLOAD.txt") is None
    assert deploy_site.category_for("licensing_patch/providers.php") is None


def test_build_plan_targets_both_roots_and_orders_manifests_last(tmp_path):
    staging = make_staging(tmp_path)
    plan = deploy_site.build_plan(
        staging,
        {"pages", "assets", "manifests", "builds", "archive", "notes"},
        "/public_html/Update",
        "/home/user/public_html/smartattendance",
    )
    relative_paths = [item.relative for item in plan]
    assert "UPLOAD.txt" not in relative_paths
    assert not any(relative.startswith("licensing_patch/") for relative in relative_paths)
    assert {item.category for item in plan[-3:]} == {"manifests"}
    assert sorted(item.relative for item in plan[-3:]) == [
        "companion/latest.json",
        "full/latest.json",
        "latest.json",
    ]
    item = next(entry for entry in plan if entry.relative == "imgs/a.png")
    assert item.targets == [
        ("/public_html/Update", "/public_html/Update/imgs/a.png"),
        ("/home/user/public_html/smartattendance", "/home/user/public_html/smartattendance/imgs/a.png"),
    ]


def test_build_plan_honors_categories(tmp_path):
    staging = make_staging(tmp_path)
    plan = deploy_site.build_plan(staging, {"manifests"}, "/a", "/b")
    assert {item.category for item in plan} == {"manifests"}
    assert len(plan) == 3


def test_preflight_flags_missing_build_and_version(tmp_path):
    staging = make_staging(tmp_path)
    (staging / "builds" / "SmartAttendance.exe").unlink()
    warnings = deploy_site.preflight(staging)
    assert any("SmartAttendance.exe" in warning for warning in warnings)
    assert not any("1.7.4" in warning for warning in warnings)


def test_preflight_reports_version_not_in_page(tmp_path):
    staging = make_staging(tmp_path)
    (staging / "index.html").write_text("<html>1.1.32 4.5.1</html>", encoding="utf-8")
    warnings = deploy_site.preflight(staging)
    assert any("1.7.4" in warning for warning in warnings)


class FakeFtp:
    def __init__(self, existing: dict[str, int] | None = None):
        self.dirs = {"/"}
        self.files = dict(existing or {})
        self.renames: list[tuple[str, str]] = []
        self.deleted: list[str] = []

    def cwd(self, path):
        if path != "/" and path not in self.dirs:
            raise error_perm("550 no such directory")

    def mkd(self, path):
        if path in self.dirs:
            raise error_perm("550 exists")
        self.dirs.add(path)

    def size(self, path):
        if path not in self.files:
            raise error_perm("550 no file")
        return self.files[path]

    def storbinary(self, command, handle, blocksize=8192, callback=None):
        path = command.split(" ", 1)[1]
        data = handle.read()
        self.files[path] = len(data)
        if callback:
            callback(data)

    def rename(self, old, new):
        if old not in self.files:
            raise error_perm("550 missing")
        self.files[new] = self.files.pop(old)
        self.renames.append((old, new))

    def delete(self, path):
        self.files.pop(path, None)
        self.deleted.append(path)


def test_uploader_creates_dirs_and_renames_atomically(tmp_path):
    uploader = deploy_site.Uploader({"host": "example"}, lambda text: None)
    uploader.ftp = FakeFtp()
    local = tmp_path / "latest.json"
    local.write_text("{}", encoding="utf-8")
    uploader.upload(local, "/public_html/Update/latest.json")
    ftp = uploader.ftp
    assert "/public_html" in ftp.dirs and "/public_html/Update" in ftp.dirs
    assert "/public_html/Update/latest.json" in ftp.files
    assert ftp.renames[0][1] == "/public_html/Update/latest.json"
    assert not any(name.endswith(".part") for name in ftp.files)


def test_uploader_replaces_existing_target(tmp_path):
    uploader = deploy_site.Uploader({"host": "example"}, lambda text: None)
    uploader.ftp = FakeFtp({"/public_html/Update/latest.json": 1})
    uploader.ftp.dirs.add("/public_html")
    uploader.ftp.dirs.add("/public_html/Update")
    local = tmp_path / "latest.json"
    local.write_text("{\"version\": 2}", encoding="utf-8")

    original_rename = uploader.ftp.rename
    attempts = {"count": 0}

    def rename(old, new):
        if attempts["count"] == 0:
            attempts["count"] += 1
            raise error_perm("550 file exists")
        original_rename(old, new)

    uploader.ftp.rename = rename
    uploader.upload(local, "/public_html/Update/latest.json")
    assert "/public_html/Update/latest.json" in uploader.ftp.files
    assert uploader.ftp.deleted


def test_verify_item_falls_back_to_second_site(tmp_path, monkeypatch):
    local = tmp_path / "index.html"
    local.write_text("hello", encoding="utf-8")
    item = deploy_site.DeployItem("index.html", local.stat().st_size, "pages", local)
    calls = []

    def fake_sha(url):
        calls.append(url)
        return (200, "wrong" if "smart" in url else deploy_site._sha256(local))

    monkeypatch.setattr(deploy_site, "http_sha", fake_sha)
    deploy_site.verify_item(
        item,
        {"site1_url": "https://smart.example/", "site2_url": "https://ok.example/Update/"},
        lambda text: None,
    )
    assert item.status == "verified"
    assert calls[0].startswith("https://smart.example/")
    assert calls[-1] == "https://ok.example/Update/index.html"


def test_verify_item_marks_uploaded_when_all_copies_differ(tmp_path, monkeypatch):
    local = tmp_path / "latest.json"
    local.write_text("{}", encoding="utf-8")
    item = deploy_site.DeployItem("latest.json", local.stat().st_size, "manifests", local)
    monkeypatch.setattr(deploy_site, "http_sha", lambda url: (200, "different"))
    deploy_site.verify_item(item, {"site1_url": "https://a.example/"}, lambda text: None)
    assert item.status == "uploaded"
    assert "تُطابق" in item.detail or "تطابق" in item.detail


def test_skip_same_size_logic(tmp_path, monkeypatch):
    staging = make_staging(tmp_path)
    ftp = FakeFtp()
    item_size = (staging / "imgs" / "a.png").stat().st_size
    ftp.dirs.update({"/a", "/a/imgs", "/b", "/b/imgs"})
    ftp.files["/a/imgs/a.png"] = item_size
    ftp.files["/b/imgs/a.png"] = item_size

    class FakeUploader:
        def connect(self):
            return None

        def close(self):
            return None

        def remote_size(self, remote):
            return ftp.size(remote)

        def upload(self, local, remote, progress=None):
            ftp.storbinary(f"STOR {remote}", local.open("rb"), callback=progress)

    monkeypatch.setattr(deploy_site, "Uploader", lambda config, log: FakeUploader())
    items = deploy_site.run_deploy(
        {"site1_root": "/a", "site2_root": "/b", "verify": False, "skip_same_size": True},
        {"assets"},
        staging,
        lambda text: None,
    )
    assert [item.status for item in items] == ["skipped"]
    assert ftp.renames == []
