"""Generate a realistic demo database (30 students, 40 days of attendance, SMS samples).

Usage:
    python scripts/seed_demo.py --data-dir .qa-data --force

The script wipes the target data directory only when --force is provided.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import struct
import sys
import zlib
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from attendance_app import create_app  # noqa: E402
from attendance_app.db import save_settings  # noqa: E402


def _png(width: int, height: int, pixel) -> bytes:
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw.extend(pixel(x, y))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(
            ">I", zlib.crc32(tag + payload) & 0xFFFFFFFF
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def _logo_pixel(x: int, y: int) -> tuple[int, int, int, int]:
    dx, dy = x - 120, y - 120
    distance = dx * dx + dy * dy
    if distance > 110 * 110:
        return (0, 0, 0, 0)
    if distance > 96 * 96:
        return (255, 255, 255, 255)
    if 60 <= y <= 100 and 70 <= x <= 170 and (x - 70) % 14 < 7:
        return (255, 255, 255, 255)
    if 70 <= x <= 170 and 120 <= y <= 160:
        return (255, 255, 255, 255)
    return (14, 124, 102, 255)


def _scan_time(status: str, index: int, day_offset: int) -> str | None:
    if status == "PRESENT":
        total = 7 * 3600 + 42 * 60 + ((index + day_offset) % 18) * 60
    elif status == "LATE":
        total = 8 * 3600 + 12 * 60 + ((index + day_offset) % 12) * 60
    else:
        return None
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:00"


def _birth_date(index: int) -> str:
    return f"{2008 + index % 3}-{1 + index % 9:02d}-{10 + index % 9:02d}"


def seed(data_dir: Path, force: bool) -> None:
    if data_dir.exists() and any(data_dir.iterdir()):
        if not force:
            raise SystemExit(
                f"Data directory already exists: {data_dir}. Use --force to replace it."
            )
        shutil.rmtree(data_dir)

    app = create_app({"DATA_DIR": str(data_dir), "SCHEDULER_INTERVAL": 86400})
    db_path = Path(app.config["DB_PATH"])
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    logo_path = Path(app.config["UPLOADS_DIR"]) / "logo_demo.png"
    logo_path.write_bytes(_png(240, 240, _logo_pixel))

    school = "متوسطة الأمير عبد القادر"
    save_settings(
        conn,
        {
            "school_name": school,
            "academic_year": "2026/2025",
            "header_text": (
                "الجمهورية الجزائرية الديمقراطية الشعبية\n"
                "وزارة التربية الوطنية\n"
                "مديرية التربية لولاية الجزائر"
            ),
            "principal_name": "أ. كريم بوعلام",
            "logo_path": "/media/uploads/logo_demo.png",
            "school_weekdays": "0,1,2,3,4,6",
            "anti_passback_minutes": 5,
            "sms_test_mode": "1",
        },
    )
    conn.commit()

    periods = conn.execute("SELECT * FROM periods ORDER BY sort_order").fetchall()
    morning = periods[0]
    if len(periods) < 2:
        conn.execute(
            """INSERT INTO periods
               (period_name, start_time, on_time_cutoff, absent_cutoff_time, sort_order)
               VALUES(?, ?, ?, ?, ?)""",
            ("الفترة المسائية", "13:00", "13:15", "13:35", 2),
        )
        conn.commit()
    evening = conn.execute(
        "SELECT * FROM periods ORDER BY sort_order LIMIT 1 OFFSET 1"
    ).fetchone()

    firsts = [
        "محمد أمين", "ياسمين", "عبد الرحمن", "نور الهدى", "إسلام", "فاطمة الزهراء",
        "أيمن", "خديجة", "يوسف", "سارة", "أمين", "مريم", "زكرياء", "إكرام", "حمزة",
        "أسماء", "سفيان", "هبة", "بلال", "ريان", "أنس", "سلسبيل", "إلياس", "شهد",
        "عادل", "نسرين", "وليد", "أميرة", "منير", "دنيا",
    ]
    lasts = [
        "بن علي", "مرزوقي", "بوزيد", "حمداني", "عمراني", "بلقاسم", "قاسمي", "زروقي",
        "لعروسي", "بن يحيى", "سعيدي", "مخلوفي", "بوعزة", "شريف", "قندوز", "بلعباس",
        "حديد", "مزيان", "خلفي", "بوناب", "طالبي", "بن ساسي", "دحماني", "عبدلي",
        "صغير", "لعمامرة", "بن ناصر", "قواسمي", "حواس", "بلحاج",
    ]
    classes = ["1AS1", "1AS2", "2AS1"]
    students = []
    for index in range(30):
        nin = f"1099{index:06d}"
        cls = classes[index % len(classes)]
        phone = f"05{50 + index % 3}{index:06d}"
        conn.execute(
            """INSERT INTO students
               (student_nin, first_name, last_name, birth_date, class_group, parent_phone, is_active)
               VALUES(?, ?, ?, ?, ?, ?, ?)""",
            (
                nin, firsts[index], lasts[index], _birth_date(index), cls, phone,
                0 if index == 28 else 1,
            ),
        )
        students.append((nin, firsts[index], lasts[index], cls, phone))
    conn.commit()

    today = date.today()
    statuses = ["PRESENT", "PRESENT", "PRESENT", "LATE", "ABSENT", "EXCUSED", "PRESENT", "LATE", "ABSENT", "PRESENT"]
    for day_offset in range(0, 40):
        day = today - timedelta(days=day_offset)
        if day.weekday() == 5 and day_offset > 0:
            continue
        for index, (nin, first, last, cls, phone) in enumerate(students):
            if index == 28:
                continue
            if day_offset == 0:
                status = statuses[index % len(statuses)]
            else:
                status = "PRESENT" if (index + day_offset) % 7 else ("LATE" if day_offset % 3 else "ABSENT")
            scan = _scan_time(status, index, day_offset)
            note = "شهادة طبية" if status == "EXCUSED" else None
            try:
                conn.execute(
                    """INSERT INTO attendance_logs
                       (student_nin, period_id, attendance_date, scan_time, status, note, source)
                       VALUES(?, ?, ?, ?, ?, ?, ?)""",
                    (nin, morning["id"], day.isoformat(), scan, status, note, "GATE"),
                )
            except sqlite3.IntegrityError:
                pass
            if day_offset == 0 and status == "ABSENT" and index % 10 == 4:
                sms_status = "SENT" if index % 20 == 4 else "FAILED"
                conn.execute(
                    """INSERT OR IGNORE INTO sms_logs
                       (student_nin, period_id, attendance_date, parent_phone, message_body,
                        delivery_status, attempts, error_msg, sent_at, is_test)
                       VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        nin, morning["id"], day.isoformat(), phone,
                        f"تعلـمكم إدارة {school} أن التلميذ(ة) {first} {last} القسـم {cls} قد تغيب عن الفترة الصباحية ليوم {day.strftime('%d/%m/%Y')}.",
                        sms_status, 1,
                        "تعذر الاتصال بالبوابة." if sms_status == "FAILED" else None,
                        f"{day.isoformat()} 08:40:00" if sms_status == "SENT" else None,
                        0,
                    ),
                )
    conn.commit()

    absents = conn.execute(
        """SELECT s.student_nin, s.first_name, s.last_name, s.class_group, s.parent_phone
           FROM attendance_logs a JOIN students s ON s.student_nin = a.student_nin
           WHERE a.attendance_date = ? AND a.period_id = ? AND a.status = 'ABSENT'
           ORDER BY a.id""",
        (today.isoformat(), morning["id"]),
    ).fetchall()
    for row in absents:
        existing = conn.execute(
            "SELECT id FROM sms_logs WHERE student_nin = ? AND period_id = ? AND attendance_date = ?",
            (row["student_nin"], morning["id"], today.isoformat()),
        ).fetchone()
        if existing:
            continue
        conn.execute(
            """INSERT INTO sms_logs
               (student_nin, period_id, attendance_date, parent_phone, message_body, delivery_status)
               VALUES(?, ?, ?, ?, ?, ?)""",
            (
                row["student_nin"], morning["id"], today.isoformat(), row["parent_phone"],
                f"تعلـمكم إدارة {school} أن التلميذ(ة) {row['first_name']} {row['last_name']} القسـم {row['class_group']} قد تغيب اليوم. يرجى التقرب من الإدارة لتبرير الغياب.",
                "PENDING",
            ),
        )
    conn.commit()

    conn.execute(
        """INSERT OR IGNORE INTO period_closures
           (period_id, closure_date, closed_by, sms_queued, closed_at)
           VALUES(?, ?, ?, ?, ?)""",
        (morning["id"], today.isoformat(), "MANUAL", 1, f"{today.isoformat()} 08:35:00"),
    )
    pending = conn.execute(
        "SELECT id FROM sms_logs WHERE delivery_status = 'PENDING' ORDER BY id"
    ).fetchall()
    if len(pending) > 1:
        conn.execute(
            """UPDATE sms_logs
               SET delivery_status = 'CANCELLED', error_msg = 'أُلغيت بعد تبرير الغياب.'
               WHERE id = ?""",
            (pending[-1]["id"],),
        )
    conn.commit()

    print(
        f"Seeded {db_path} | students: "
        f"{conn.execute('SELECT COUNT(*) FROM students').fetchone()[0]} | logs: "
        f"{conn.execute('SELECT COUNT(*) FROM attendance_logs').fetchone()[0]} | "
        f"periods: {len(periods) + (0 if len(periods) >= 2 else 1)} | "
        f"morning closed, evening open"
    )
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed a demo attendance database.")
    parser.add_argument(
        "--data-dir",
        default=str(ROOT / "data"),
        help="Target data directory (default: <project>/data).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete an existing data directory before seeding.",
    )
    args = parser.parse_args()
    seed(Path(args.data_dir).resolve(), args.force)


if __name__ == "__main__":
    main()
