from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from attendance_app.services import update_service
from attendance_app.services.update_service import (
    UpdateError,
    canonical_payload,
    is_newer,
    verify_manifest,
)


@pytest.fixture()
def signing_key(monkeypatch):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    monkeypatch.setattr(update_service, "UPDATE_PUBLIC_KEY", base64.b64encode(public).decode("ascii"))
    return private


def build_manifest(private, version="9.9.9", payload=b"exe-bytes", kind="portable"):
    manifest = {
        "schema": 1,
        "version": version,
        "notes": "تحديث تجريبي",
        "published_at": "2026-10-24T10:00:00+00:00",
        "files": [
            {
                "kind": kind,
                "url": f"https://update.fetsync.org/builds/{kind}.exe",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
                "name": "SmartAttendance.exe",
            }
        ],
    }
    manifest["signature"] = base64.b64encode(private.sign(canonical_payload(manifest))).decode("ascii")
    return manifest


def test_version_comparison():
    assert is_newer("1.2.0", "1.1.0")
    assert is_newer("1.1.10", "1.1.9")
    assert not is_newer("1.1.0", "1.1.0")
    assert not is_newer("1.0.9", "1.1.0")
    assert not is_newer("bad", "1.1.0")


def test_valid_signature_is_accepted(signing_key):
    manifest = verify_manifest(build_manifest(signing_key))
    assert manifest["version"] == "9.9.9"
    assert manifest["files"][0]["kind"] == "portable"


def test_unsigned_manifest_is_rejected(signing_key):
    manifest = build_manifest(signing_key)
    manifest.pop("signature")
    with pytest.raises(UpdateError, match="غير موقّع"):
        verify_manifest(manifest)


def test_tampered_manifest_is_rejected(signing_key):
    manifest = build_manifest(signing_key)
    manifest["version"] = "9.9.10"
    with pytest.raises(UpdateError, match="توقيع"):
        verify_manifest(manifest)


def test_insecure_artifact_url_is_rejected(signing_key):
    private = signing_key
    manifest = {
        "schema": 1,
        "version": "9.9.9",
        "notes": "",
        "published_at": "",
        "files": [
            {
                "kind": "portable",
                "url": "http://update.fetsync.org/builds/app.exe",
                "sha256": hashlib.sha256(b"x").hexdigest(),
                "size": 1,
                "name": "app.exe",
            }
        ],
    }
    manifest["signature"] = base64.b64encode(private.sign(canonical_payload(manifest))).decode("ascii")
    with pytest.raises(UpdateError, match="https"):
        verify_manifest(manifest)


def test_check_update_stages_signed_release(app, client, signing_key, monkeypatch):
    manifest = build_manifest(signing_key)
    calls = {"staged": 0}

    monkeypatch.setattr(update_service, "fetch_manifest", lambda _url: json.loads(json.dumps(manifest)))

    def fake_stage(trigger="manual"):
        calls["staged"] += 1
        return {"version": manifest["version"], "kind": "portable", "filename": "update.exe"}

    monkeypatch.setattr(update_service, "stage_update", fake_stage)

    with app.app_context():
        status = update_service.check_for_update(force=True, raise_errors=True)
    assert status["update_available"] is True
    assert status["latest_version"] == "9.9.9"
    assert calls["staged"] == 1

    response = client.get("/api/update/status")
    assert response.status_code == 200
    update = response.get_json()["update"]
    assert update["latest_version"] == "9.9.9"
    assert update["auto_update"] is True


def test_check_update_reports_bad_signature(app, client, signing_key, monkeypatch):
    manifest = build_manifest(signing_key)
    manifest["notes"] = "tampered"
    monkeypatch.setattr(update_service, "fetch_manifest", lambda _url: manifest)

    response = client.post("/api/update/check", headers={"X-Requested-With": "StudentAttendance"})
    assert response.status_code == 502
    assert response.get_json()["error"]["code"] == "update"

    status = client.get("/api/update/status").get_json()["update"]
    assert status["update_available"] is False
    assert "توقيع" in status["last_error"]


def test_select_asset_prefers_setup_for_installed_build(monkeypatch):
    manifest = {
        "files": [
            {"kind": "portable", "url": "https://update.fetsync.org/builds/app.exe"},
            {"kind": "setup", "url": "https://update.fetsync.org/builds/setup.exe"},
        ]
    }
    monkeypatch.setattr(update_service.sys, "frozen", True, raising=False)
    monkeypatch.setattr(update_service, "_installed_via_setup", lambda: True)
    assert update_service.select_asset(manifest)["kind"] == "setup"
    monkeypatch.setattr(update_service, "_installed_via_setup", lambda: False)
    assert update_service.select_asset(manifest)["kind"] == "portable"


def test_verify_staged_rejects_tampered_artifact(app, signing_key):
    payload = b"original-bytes"
    manifest = verify_manifest(build_manifest(signing_key, payload=payload))
    state = {
        "update": manifest,
        "download": {
            "version": manifest["version"],
            "filename": "update_test.exe",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        },
    }
    with app.app_context():
        staged = Path(app.config["UPDATES_DIR"]) / "update_test.exe"
        staged.write_bytes(payload)
        path, verified, _download = update_service._verify_staged(state)
        assert path == staged.resolve()
        staged.write_bytes(b"tampered-bytes")
        with pytest.raises(UpdateError, match="تغيّر"):
            update_service._verify_staged(state)


def test_clean_env_drops_pyinstaller_bootstrap(monkeypatch):
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", r"C:\Temp\_MEI123")
    monkeypatch.setenv("_MEIPASS", r"C:\Temp\_MEI123")
    monkeypatch.setenv("NORMAL_SETTING", "keep")
    env = update_service._clean_env()
    assert "NORMAL_SETTING" in env
    assert not any(key.startswith("_PYI_") for key in env)
    assert "_MEIPASS" not in env


def test_install_is_refused_outside_packaged_build(app, client, signing_key):
    with app.app_context():
        state = update_service.load_state()
        state["update"] = verify_manifest(build_manifest(signing_key))
        update_service.save_state(state)
    response = client.post("/api/update/install", headers={"X-Requested-With": "StudentAttendance"})
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "update"
