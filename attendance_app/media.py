from __future__ import annotations

import hashlib
from pathlib import Path

from werkzeug.datastructures import FileStorage

from .utils import ValidationError

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _image_extension(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    return None


def save_image(
    file: FileStorage | None,
    dest_dir: str | Path,
    url_prefix: str,
    name_prefix: str,
    max_bytes: int = MAX_IMAGE_BYTES,
) -> dict:
    if not file or not file.filename:
        raise ValidationError("يرجى اختيار صورة.")
    data = file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValidationError(f"حجم الصورة يتجاوز {max_bytes // (1024 * 1024)} ميغابايت.")
    if not data:
        raise ValidationError("ملف الصورة فارغ.")
    extension = _image_extension(data)
    if not extension:
        raise ValidationError("صيغة الصورة غير مدعومة. استعمل JPG أو PNG أو WEBP.")
    digest = hashlib.sha256(data).hexdigest()[:20]
    filename = f"{name_prefix}_{digest}{extension}"
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / filename
    if not target.exists():
        target.write_bytes(data)
    return {
        "filename": filename,
        "url": f"{url_prefix.rstrip('/')}/{filename}",
        "size": len(data),
    }


def safe_delete_media(url: str | None, directory: str | Path, url_prefix: str) -> bool:
    if not url or not url.startswith(url_prefix.rstrip("/") + "/"):
        return False
    filename = url.rsplit("/", 1)[-1]
    if "/" in filename or "\\" in filename or filename.startswith("."):
        return False
    path = Path(directory) / filename
    try:
        resolved = path.resolve()
        if resolved.parent != Path(directory).resolve():
            return False
        if resolved.is_file():
            resolved.unlink()
            return True
    except OSError:
        return False
    return False
