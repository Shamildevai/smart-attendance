"""Merge locale source fragments into runtime catalogs and report coverage."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
SRC = Path(__file__).resolve().parent / "locale_src"
OUT = ROOT / "attendance_app" / "locales"
LANGS = ("en", "fr")
PLACEHOLDER = re.compile(r"\{(\d+)\}")
ARABIC = re.compile(r"[\u0600-\u06FF]")
STRING_LITERAL = re.compile(r"""(["'])((?:\\.|(?!\1).)*\1)""", re.DOTALL)
SKIP_PARTS = ("/locales/", "/fonts/", "/vendor/")


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def load_language(lang: str) -> dict:
    strings: dict[str, str] = {}
    patterns: list[dict[str, str]] = []
    folder = SRC / lang
    if not folder.exists():
        return {"strings": strings, "patterns": patterns}
    for path in sorted(folder.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for key, value in (data.get("strings") or {}).items():
            key = normalize(key)
            if not key or not isinstance(value, str) or not value.strip():
                continue
            if key in strings and strings[key] != value:
                raise SystemExit(f"duplicate key in {lang}/{path.name}: {key!r}")
            strings[key] = value.strip()
        for item in data.get("patterns") or []:
            source = normalize(item.get("ar", ""))
            target = item.get("tr", "").strip()
            if not source or not target:
                continue
            source_slots = sorted(set(PLACEHOLDER.findall(source)))
            target_slots = sorted(set(PLACEHOLDER.findall(target)))
            if source_slots != target_slots:
                raise SystemExit(
                    f"placeholder mismatch in {lang}/{path.name}: {source!r} -> {target!r}"
                )
            patterns.append({"ar": source, "tr": target})
    patterns.sort(key=lambda row: len(PLACEHOLDER.sub("", row["ar"])), reverse=True)
    return {"strings": strings, "patterns": patterns}


def write_catalogs() -> dict[str, dict]:
    OUT.mkdir(parents=True, exist_ok=True)
    merged = {}
    for lang in LANGS:
        data = load_language(lang)
        merged[lang] = data
        path = OUT / f"{lang}.json"
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"{lang}: {len(data['strings'])} strings, {len(data['patterns'])} patterns -> {path}"
        )
    return merged


def candidate_sources() -> list[Path]:
    files = []
    for base in (ROOT / "attendance_app", ROOT / "scripts"):
        for pattern in ("*.py", "*.js", "*.html"):
            for path in base.rglob(pattern):
                text = str(path).replace("\\", "/")
                if any(part in text for part in SKIP_PARTS):
                    continue
                if path.name.startswith("_"):
                    continue
                files.append(path)
    return files


def coverage(data: dict) -> None:
    strings = data["strings"]
    patterns = [re.compile(
        "^" + "".join(
            re.escape(part) if index % 2 == 0 else "(.+?)"
            for index, part in enumerate(PLACEHOLDER.split(normalize(row["ar"])))
        ) + "$",
        re.DOTALL,
    ) for row in data["patterns"]]
    misses: dict[str, list[str]] = {}
    for path in candidate_sources():
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for quote, raw in STRING_LITERAL.findall(source):
            try:
                value = bytes(raw, "utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8")
            except (UnicodeDecodeError, UnicodeEncodeError):
                value = raw
            text = normalize(value)
            if len(text) < 2 or not ARABIC.search(text):
                continue
            if "{{" in text or "{%" in text or "}}" in text:
                continue
            if text in strings or any(pattern.match(text) for pattern in patterns):
                continue
            misses.setdefault(text, []).append(str(path.relative_to(ROOT)))
    if not misses:
        print("coverage: every extracted source string is translated")
        return
    print(f"coverage: {len(misses)} untranslated source strings")
    for text, files in sorted(misses.items()):
        print(f"  - {text!r}  [{', '.join(sorted(set(files))[:3])}]")


if __name__ == "__main__":
    catalogs = write_catalogs()
    if "--coverage" in sys.argv:
        coverage(catalogs["en"])
