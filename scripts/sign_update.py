"""Create a signed latest.json update manifest for SmartAttendance.

Run after building the EXE/setup:

    python scripts/sign_update.py --version 1.1.14 \
        --notes "إصلاحات وتحسينات" \
        --portable dist/SmartAttendance.exe \
        --setup dist/SmartAttendance_Setup_1.1.14.exe \
        --out dist/update/latest.json

Then upload latest.json plus the artifact files to https://fetsync.org/Update
at the paths recorded in the manifest. Keep secrets/update_signing_key.pem
offline and never publish it.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from attendance_app.services.update_service import canonical_payload, verify_manifest  # noqa: E402

DEFAULT_KEY = ROOT / "secrets" / "update_signing_key.pem"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_entry(kind: str, path: Path, base_url: str) -> dict:
    path = path.resolve()
    if not path.is_file():
        raise SystemExit(f"الملف غير موجود: {path}")
    if path.suffix.lower() != ".exe":
        raise SystemExit(f"ملف التحديث يجب أن يكون EXE: {path}")
    filename = path.name
    return {
        "kind": kind,
        "url": f"{base_url.rstrip('/')}/builds/{quote(filename)}",
        "sha256": sha256_file(path),
        "size": path.stat().st_size,
        "name": filename,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Sign a SmartAttendance update manifest")
    parser.add_argument("--version", required=True, help="target version, e.g. 1.1.7")
    parser.add_argument("--notes", default="", help="release notes shown in the app")
    parser.add_argument("--notes-file", help="read release notes from a UTF-8 text file")
    parser.add_argument("--portable", help="portable SmartAttendance.exe")
    parser.add_argument("--setup", help="Inno Setup installer .exe")
    parser.add_argument("--base-url", default="https://fetsync.org/Update", help="hosting base URL")
    parser.add_argument("--out", default=str(ROOT / "dist" / "update" / "latest.json"))
    parser.add_argument("--private-key", default=str(DEFAULT_KEY))
    parser.add_argument("--published-at", default="")
    args = parser.parse_args()

    if not args.portable and not args.setup:
        raise SystemExit("حدد --portable و/أو --setup.")
    if not args.base_url.startswith("https://"):
        raise SystemExit("عنوان النشر يجب أن يبدأ بـ https://.")

    notes = args.notes
    if args.notes_file:
        notes = Path(args.notes_file).read_text(encoding="utf-8").strip()
    if len(notes) > 5000:
        raise SystemExit("وصف التحديث أطول من 5000 حرف.")

    files = []
    if args.portable:
        files.append(build_entry("portable", Path(args.portable), args.base_url))
    if args.setup:
        files.append(build_entry("setup", Path(args.setup), args.base_url))

    published_at = args.published_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest = {
        "schema": 1,
        "version": args.version.strip(),
        "notes": notes,
        "published_at": published_at,
        "files": files,
    }

    key_path = Path(args.private_key)
    if not key_path.is_file():
        raise SystemExit(f"مفتاح التوقيع غير موجود: {key_path}")
    private_key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        raise SystemExit("المفتاح ليس Ed25519.")
    signature = private_key.sign(canonical_payload(manifest))
    manifest["signature"] = base64.b64encode(signature).decode("ascii")

    verified = verify_manifest(manifest)
    if verified["version"] != manifest["version"]:
        raise SystemExit("فشل التحقق من التوقيع بعد الإنشاء.")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"تم إنشاء: {out}")
    for entry in manifest["files"]:
        print(f"  {entry['kind']}: {entry['name']} ({entry['size']} bytes)")
        print(f"    sha256: {entry['sha256']}")
        print(f"    url: {entry['url']}")
    print("ارفع latest.json والملفات إلى خادم التحديثات بنفس المسارات أعلاه.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
