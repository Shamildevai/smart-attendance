from __future__ import annotations

import html as html_lib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from flask import g, has_request_context, request
from jinja2 import pass_context

LANG_COOKIE = "attendance_lang"
DEFAULT_LANG = "ar"
LANGUAGES = {
    "ar": {"name": "العربية", "dir": "rtl", "label": "AR"},
    "en": {"name": "English", "dir": "ltr", "label": "EN"},
    "fr": {"name": "Français", "dir": "ltr", "label": "FR"},
}
LOCALES_DIR = Path(__file__).resolve().parent / "locales"
_ARABIC = re.compile(r"[\u0600-\u06FF]")
_PLACEHOLDER = re.compile(r"\{(\d+)\}")
_SKIP_CONTENT = {"script", "style", "textarea", "pre", "code"}
_TRANSLATABLE_ATTRS = "placeholder|title|aria-label|alt"
_ATTR_RE = re.compile(
    rf"""(\s)({_TRANSLATABLE_ATTRS})(\s*=\s*)(["'])(.*?)\4""",
    re.IGNORECASE | re.DOTALL,
)
_TAG_NAME_RE = re.compile(r"</?\s*([a-zA-Z][\w:.-]*)")
_END_TAG_RE = re.compile(r"</\s*([a-zA-Z][\w:.-]*)\s*>")


def normalize_lang(value: Any) -> str:
    code = str(value or "").strip().lower().replace("_", "-").split("-")[0]
    return code if code in LANGUAGES else DEFAULT_LANG


def current_lang() -> str:
    if has_request_context():
        lang = getattr(g, "lang", None)
        if lang:
            return lang
    return DEFAULT_LANG


def lang_dir(lang: str | None = None) -> str:
    return LANGUAGES[normalize_lang(lang or current_lang())]["dir"]


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


@lru_cache(maxsize=8)
def catalog(lang: str) -> dict[str, Any]:
    lang = normalize_lang(lang)
    if lang == "ar":
        return {"strings": {}, "patterns": []}
    path = LOCALES_DIR / f"{lang}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"strings": {}, "patterns": []}
    strings = {
        _normalize_space(key): value
        for key, value in (data.get("strings") or {}).items()
        if isinstance(value, str) and value
    }
    patterns = []
    for item in data.get("patterns") or []:
        try:
            source, replacement = item["ar"], item["tr"]
        except (TypeError, KeyError):
            continue
        compiled = _compile_pattern(source)
        if compiled:
            patterns.append((compiled, replacement, source))
    patterns.sort(key=lambda row: len(_PLACEHOLDER.sub("", row[2])), reverse=True)
    return {"strings": strings, "patterns": patterns}


def _compile_pattern(source: str) -> re.Pattern[str] | None:
    parts = []
    last = 0
    groups = 0
    for match in _PLACEHOLDER.finditer(source):
        literal = re.sub(r"\s+", " ", source[last : match.start()])
        parts.append(re.escape(literal.lstrip() if groups == 0 else literal))
        groups += 1
        parts.append("(.+?)")
        last = match.end()
    if not groups:
        return None
    tail = re.sub(r"\s+", " ", source[last:]).rstrip()
    parts.append(re.escape(tail))
    return re.compile("^" + "".join(parts) + "$", re.DOTALL)


def _apply_pattern(
    replacement: str,
    match: re.Match[str],
    code: str,
    depth: int,
) -> str:
    def replace(token: re.Match[str]) -> str:
        try:
            value = match.group(int(token.group(1)) + 1) or ""
        except IndexError:
            return ""
        if depth < 2 and _ARABIC.search(value):
            return _translate(value, code, depth + 1)
        return value

    return _PLACEHOLDER.sub(replace, replacement)


def _translate(value: Any, code: str, depth: int = 0) -> str:
    text = "" if value is None else str(value)
    if not text or code == "ar" or not _ARABIC.search(text):
        return text
    data = catalog(code)
    normalized = _normalize_space(text)

    def lookup(core: str) -> str | None:
        exact = data["strings"].get(core)
        if exact is not None:
            return exact
        for pattern, replacement, _source in data["patterns"]:
            match = pattern.match(core)
            if match:
                return _apply_pattern(replacement, match, code, depth)
        return None

    translated = lookup(normalized)
    if translated is not None:
        return translated
    tail = re.match(r"^(.*?)([.!?؟…]+)$", normalized, re.DOTALL)
    if tail and tail.group(1).strip():
        translated = lookup(tail.group(1).rstrip())
        if translated is not None:
            return translated + tail.group(2)
    return text


def translate(value: Any, lang: str | None = None) -> str:
    return _translate(value, normalize_lang(lang or current_lang()))


def translate_preserving_space(value: str, lang: str | None = None) -> str:
    if not value or not _ARABIC.search(value):
        return value
    code = normalize_lang(lang or current_lang())
    match = re.match(r"^(\s*)([\s\S]*?)(\s*)$", value)
    if not match:
        return value
    leading, core, trailing = match.groups()
    if not core:
        return value
    translated = _translate(core, code)
    if translated == core:
        return value
    return leading + translated + trailing


def _find_tag_end(document: str, start: int) -> int:
    quote = ""
    index = start + 1
    length = len(document)
    while index < length:
        char = document[index]
        if quote:
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == ">":
            return index
        index += 1
    return -1


def _translate_text(raw: str, code: str) -> str:
    if not _ARABIC.search(raw):
        return raw
    decoded = html_lib.unescape(raw)
    translated = translate_preserving_space(decoded, code)
    if translated == decoded:
        return raw
    return html_lib.escape(translated, quote=False)


def _translate_tag(tag: str, code: str) -> str:
    if not _ARABIC.search(tag):
        return tag

    def replace_attr(match: re.Match[str]) -> str:
        value = html_lib.unescape(match.group(5))
        translated = translate_preserving_space(value, code)
        if translated == value:
            return match.group(0)
        escaped = html_lib.escape(translated, quote=True)
        return (
            f"{match.group(1)}{match.group(2)}{match.group(3)}"
            f"{match.group(4)}{escaped}{match.group(4)}"
        )

    return _ATTR_RE.sub(replace_attr, tag)


def translate_html(document: str, lang: str | None = None) -> str:
    code = normalize_lang(lang or current_lang())
    if code == "ar" or not document:
        return document
    parts: list[str] = []
    index = 0
    length = len(document)
    while index < length:
        tag_start = document.find("<", index)
        if tag_start < 0:
            parts.append(_translate_text(document[index:], code))
            break
        if tag_start > index:
            parts.append(_translate_text(document[index:tag_start], code))
        if document.startswith("<!--", tag_start):
            tag_end = document.find("-->", tag_start + 4)
            if tag_end < 0:
                parts.append(document[tag_start:])
                break
            parts.append(document[tag_start : tag_end + 3])
            index = tag_end + 3
            continue
        if document.startswith("<![CDATA[", tag_start):
            tag_end = document.find("]]>", tag_start + 9)
            if tag_end < 0:
                parts.append(document[tag_start:])
                break
            parts.append(document[tag_start : tag_end + 3])
            index = tag_end + 3
            continue
        tag_end = _find_tag_end(document, tag_start)
        if tag_end < 0:
            parts.append(_translate_text(document[tag_start:], code))
            break
        tag = document[tag_start : tag_end + 1]
        name_match = _TAG_NAME_RE.match(tag)
        name = name_match.group(1).lower() if name_match else ""
        is_end = tag.startswith("</")
        self_closing = tag.rstrip().endswith("/>")
        no_i18n = "data-no-i18n" in tag.lower()
        if not is_end and name and (name in _SKIP_CONTENT or no_i18n) and not self_closing:
            close_match = _END_TAG_RE.search(document, tag_end + 1)
            if close_match and close_match.group(1).lower() == name:
                parts.append(tag)
                parts.append(document[tag_end + 1 : close_match.start()])
                parts.append(close_match.group(0))
                index = close_match.end()
                continue
        parts.append(tag if is_end else _translate_tag(tag, code))
        index = tag_end + 1
    return "".join(parts)


_JSON_TEXT_KEYS = frozenset(
    {
        "message",
        "title",
        "subtitle",
        "period_label",
        "class_label",
        "status_label",
        "sms_status_label",
        "scope_label",
        "channel_label",
        "error_msg",
        "month_display",
    }
)


def translate_json_payload(value: Any, lang: str | None = None) -> Any:
    """Translate user-facing labels and messages in an API payload."""
    code = normalize_lang(lang or current_lang())
    if code == "ar":
        return value

    def walk(node: Any, key: str | None = None) -> Any:
        if isinstance(node, dict):
            return {item_key: walk(item_value, item_key) for item_key, item_value in node.items()}
        if isinstance(node, list):
            return [walk(item) for item in node]
        if isinstance(node, str) and key in _JSON_TEXT_KEYS:
            return translate_preserving_space(node, code)
        return node

    translated = walk(value)
    if isinstance(translated, dict):
        for field in translated.get("fields") or []:
            if not isinstance(field, dict):
                continue
            for field_key in ("label", "hint"):
                if isinstance(field.get(field_key), str):
                    field[field_key] = translate_preserving_space(field[field_key], code)
    return translated


def i18n_javascript(lang: str) -> str:
    code = normalize_lang(lang)
    data = catalog(code)
    patterns = []
    for compiled, replacement, _source in data["patterns"]:
        pattern = compiled.pattern
        if pattern.startswith("^"):
            pattern = pattern[1:]
        if pattern.endswith("$"):
            pattern = pattern[:-1]
        patterns.append([pattern, replacement])
    payload = {
        "lang": code,
        "dir": lang_dir(code),
        "strings": data["strings"],
        "patterns": patterns,
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    body = body.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return f"window.AppI18n={body};"


def init_app(app) -> None:
    @app.before_request
    def _set_request_language():
        g.lang = normalize_lang(request.cookies.get(LANG_COOKIE))
        g.lang_dir = lang_dir(g.lang)

    @app.after_request
    def _localize_response(response):
        lang = getattr(g, "lang", DEFAULT_LANG)
        if lang != "ar" and response.status_code < 500:
            try:
                if response.mimetype == "text/html":
                    body = response.get_data(as_text=True)
                    localized = translate_html(body, lang)
                    if localized != body:
                        response.set_data(localized)
                elif response.is_json and not response.direct_passthrough:
                    payload = json.loads(response.get_data(as_text=True))
                    localized = translate_json_payload(payload, lang)
                    if localized != payload:
                        response.set_data(
                            json.dumps(localized, ensure_ascii=False, separators=(",", ":"))
                        )
                        response.mimetype = "application/json"
            except Exception:
                pass
        response.headers["Content-Language"] = lang
        response.headers.add("Vary", "Cookie")
        return response

    @pass_context
    def _t_filter(_context, value):
        return translate(value)

    app.jinja_env.filters["t"] = _t_filter
    app.jinja_env.globals["t"] = translate
