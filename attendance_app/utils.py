from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_ARABIC_DIGIT_RE = re.compile("[\u0660-\u0669\u06F0-\u06F9]")

STATUS_AR = {
    "PRESENT": "حاضر",
    "LATE": "متأخر",
    "ABSENT": "غائب",
    "EXCUSED": "غياب مبرر",
}
STATUS_ORDER = ("PRESENT", "LATE", "ABSENT", "EXCUSED")
SMS_STATUS_AR = {
    "PENDING": "في الانتظار",
    "SENT": "تم الإرسال",
    "FAILED": "فشل",
    "CANCELLED": "ملغاة",
}
ARABIC_WEEKDAYS = (
    "الاثنين",
    "الثلاثاء",
    "الأربعاء",
    "الخميس",
    "الجمعة",
    "السبت",
    "الأحد",
)
ARABIC_MONTHS = (
    "جانفي",
    "فيفري",
    "مارس",
    "أفريل",
    "ماي",
    "جوان",
    "جويلية",
    "أوت",
    "سبتمبر",
    "أكتوبر",
    "نوفمبر",
    "ديسمبر",
)
WEEKDAYS_JS = ("الأحد", "الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت")

NIN_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ValidationError(ValueError):
    pass


def now_local() -> datetime:
    return datetime.now()


def today_iso() -> str:
    return now_local().date().isoformat()


def now_stamp() -> str:
    return now_local().strftime("%Y-%m-%d %H:%M:%S")


def western_digits(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.translate(ARABIC_DIGITS) if _ARABIC_DIGIT_RE.search(text) else text


def normalize_digits(value: str) -> str:
    return western_digits(value)


def squash(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    return re.sub(r"\s+", " ", str(value).replace("\u00a0", " ").replace("\u200f", "")).strip()


def normalize_nin(value: Any) -> str:
    text = normalize_digits(squash(value)).replace(" ", "").upper()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text


def validate_nin(nin: str) -> str:
    if not nin:
        raise ValidationError("رقم التعريف الوطني مطلوب.")
    if not NIN_RE.fullmatch(nin):
        raise ValidationError("رقم التعريف يجب أن يحتوي على حروف أو أرقام لاتينية فقط (بدون رموز خاصة).")
    return nin


def normalize_phone(value: Any) -> str:
    text = normalize_digits(squash(value))
    digits = re.sub(r"[^\d+]", "", text)
    if digits.startswith("+213"):
        digits = "0" + digits[4:]
    elif digits.startswith("213") and len(digits) >= 11:
        digits = "0" + digits[3:]
    if digits.startswith("+") and not digits.startswith("+213"):
        return digits
    digits = digits.replace("+", "")
    if re.fullmatch(r"[5-7]\d{8}", digits):
        digits = "0" + digits
    return digits


def clean_name(value: Any) -> str:
    return squash(value)


def full_name(first_name: str | None, last_name: str | None) -> str:
    return squash(f"{first_name or ''} {last_name or ''}")


def initials(first_name: str | None, last_name: str | None) -> str:
    parts = [p for p in ((first_name or "").strip().split() + (last_name or "").strip().split()) if p]
    if not parts:
        return "؟"
    if len(parts) == 1:
        return parts[0][:2]
    return (parts[0][:1] + parts[-1][:1]).upper()


def normalize_date(value: Any) -> str | None:
    """Normalize common Algerian/French date formats to ISO. Returns None if invalid/empty."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = normalize_digits(squash(value))
    if not text:
        return None
    text = re.split(r"[ T]", text)[0]
    text = text.replace(".", "/").replace("-", "/")
    parts = [p for p in text.split("/") if p]
    if len(parts) == 3:
        if len(parts[0]) == 4:
            year, month, day = parts
        else:
            day, month, year = parts
        year = year[:4]
        try:
            return date(int(year), int(month), int(day)).isoformat()
        except ValueError:
            return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def validate_iso_date(value: str, field: str = "التاريخ") -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise ValidationError(f"{field} غير صالح.")


def validate_time(value: str, field: str = "الوقت") -> str:
    text = normalize_digits(squash(value))
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", text)
    if not match:
        raise ValidationError(f"{field} يجب أن يكون بصيغة HH:MM.")
    hour, minute, second = int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)
    if hour > 23 or minute > 59 or second > 59:
        raise ValidationError(f"{field} غير صالح.")
    return f"{hour:02d}:{minute:02d}:{second:02d}"


def time_to_seconds(value: str) -> int:
    hour, minute, second = (int(v) for v in validate_time(value).split(":"))
    return hour * 3600 + minute * 60 + second


def time_short(value: str | None) -> str:
    if not value:
        return "—"
    return validate_time(value)[:5]


def parse_time_on_date(day: date, time_value: str) -> datetime:
    hour, minute, second = (int(v) for v in validate_time(time_value).split(":"))
    return datetime(day.year, day.month, day.day, hour, minute, second)


def arabic_date(value: str | date | datetime | None, include_weekday: bool = False) -> str:
    if not value:
        return "—"
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value[:10])
        except ValueError:
            return value
    if isinstance(value, datetime):
        value = value.date()
    text = f"{value.day} {ARABIC_MONTHS[value.month - 1]} {value.year}"
    if include_weekday:
        text = f"{ARABIC_WEEKDAYS[value.weekday()]} {text}"
    return text


def short_date(value: str | None) -> str:
    if not value:
        return "—"
    try:
        d = date.fromisoformat(value[:10])
        return d.strftime("%d/%m/%Y")
    except ValueError:
        return value


def arabic_datetime(value: str | datetime | None) -> str:
    if not value:
        return "—"
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                value = datetime.strptime(value[:19], fmt)
                break
            except ValueError:
                continue
        else:
            return value
    return f"{arabic_date(value.date(), True)} — {value.strftime('%H:%M')}"


def academic_year_start(now: date | None = None) -> str:
    d = now or date.today()
    start = d.year if d.month >= 9 else d.year - 1
    return f"{start}/{start + 1}"


def month_label(month: str) -> str:
    try:
        year, mon = (int(p) for p in month.split("-"))
        return f"{ARABIC_MONTHS[mon - 1]} {year}"
    except (ValueError, IndexError):
        return month


def month_bounds(month: str) -> tuple[date, date]:
    try:
        year, mon = (int(p) for p in month.split("-"))
        start = date(year, mon, 1)
    except (ValueError, TypeError):
        raise ValidationError("الشهر غير صالح. استعمل الصيغة YYYY-MM.")
    if mon == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, mon + 1, 1) - timedelta(days=1)
    return start, end


def parse_weekdays(value: str | None) -> list[int]:
    days: list[int] = []
    for part in (value or "").split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 6 and int(part) not in days:
            days.append(int(part))
    return days


def js_weekday(day: date) -> int:
    return (day.weekday() + 1) % 7


def is_school_day(day: date, weekdays_setting: str) -> bool:
    return js_weekday(day) in parse_weekdays(weekdays_setting)


def dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def safe_json_loads(value: str | None, fallback: Any = None) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (ValueError, TypeError):
        return fallback


def mask_phone(phone: str | None) -> str:
    if not phone:
        return "—"
    if len(phone) < 6:
        return phone
    return f"{phone[:4]}****{phone[-2:]}"


def sms_variables(student: dict, period: dict, when: date, settings: dict) -> dict[str, str]:
    return {
        "اسم_المؤسسة": settings.get("school_name", ""),
        "اسم_التلميذ": full_name(student.get("first_name"), student.get("last_name")),
        "الاسم": student.get("first_name", ""),
        "اللقب": student.get("last_name", ""),
        "القسم": student.get("class_group") or "—",
        "اسم_الفترة": period.get("period_name", ""),
        "التاريخ": short_date(when.isoformat()),
        "السنة_الدراسية": settings.get("academic_year", ""),
        "الرقم_التعريفي": student.get("student_nin", ""),
    }


def render_sms_template(template: str, variables: dict[str, str]) -> str:
    def replace(match: re.Match) -> str:
        key = match.group(1).strip()
        return variables.get(key, match.group(0))

    return re.sub(r"\[([^\[\]]+)\]", replace, template or "").strip()
