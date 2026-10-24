from __future__ import annotations

import io
from datetime import date
from pathlib import Path

from openpyxl import Workbook


HEADERS = [
    "رقم التعريف الوطني",
    "اللقب",
    "الاسم",
    "تاريخ الميلاد",
    "القسم",
    "رقم هاتف الولي",
]


def _xlsx(rows: list[list[str]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "التلاميذ"
    sheet.append(HEADERS)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _parse(client, csrf, data: bytes, filename: str):
    response = client.post(
        "/api/import/parse",
        data={"file": (io.BytesIO(data), filename)},
        headers=csrf,
        content_type="multipart/form-data",
    )
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _preview(client, csrf, token: str, mapping: dict):
    response = client.post(
        "/api/import/preview",
        json={"token": token, "mapping": mapping},
        headers=csrf,
    )
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _commit(client, csrf, token: str, mapping: dict):
    response = client.post(
        "/api/import/commit",
        json={"token": token, "mapping": mapping},
        headers=csrf,
    )
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def test_xlsx_import_updates_without_losing_attendance(client, csrf, db, period):
    data = _xlsx(
        [
            ["2099000101", "بوقرة", "أمينة", "2010-04-15", "1AS3", "0550110201"],
            ["2099000102", "مرزوقي", "إلياس", "2009-11-02", "1AS3", "0550110202"],
        ]
    )
    parsed = _parse(client, csrf, data, "students.xlsx")
    assert parsed["file_type"].startswith("XLSX")
    assert parsed["total_rows"] == 2
    assert parsed["suggested_mapping"]["student_nin"] == 0
    assert len(parsed["preview"]) == 2

    mapping = parsed["suggested_mapping"]
    preview = _preview(client, csrf, parsed["token"], mapping)
    assert preview["summary"]["new"] == 2
    assert preview["summary"]["existing"] == 0
    assert preview["summary"]["invalid"] == 0

    result = _commit(client, csrf, parsed["token"], mapping)
    assert result["inserted"] == 2
    assert result["updated"] == 0
    assert "تم استيراد 2" in result["message"]

    student = client.get("/api/students/2099000101").get_json()["student"]
    assert student["last_name"] == "بوقرة"
    assert student["class_group"] == "1AS3"

    with db:
        db.execute(
            """INSERT INTO attendance_logs
               (student_nin, period_id, attendance_date, scan_time, status, source)
               VALUES (?, ?, ?, ?, 'PRESENT', 'GATE')""",
            ("2099000101", period["id"], date.today().isoformat(), "07:50:00"),
        )

    second = _xlsx(
        [
            ["2099000101", "بوقرة", "أمينة", "2010-04-15", "1AS4", "0550110299"],
            ["2099000103", "حديد", "نسرين", "2011-01-20", "1AS4", "0550110203"],
        ]
    )
    parsed = _parse(client, csrf, second, "updated.xlsx")
    preview = _preview(client, csrf, parsed["token"], parsed["suggested_mapping"])
    assert preview["summary"]["new"] == 1
    assert preview["summary"]["existing"] == 1

    result = _commit(client, csrf, parsed["token"], parsed["suggested_mapping"])
    assert result["inserted"] == 1
    assert result["updated"] == 1
    assert result["total"] == 2

    updated = client.get("/api/students/2099000101").get_json()["student"]
    assert updated["class_group"] == "1AS4"
    assert updated["parent_phone"] == "0550110299"

    logs = db.execute(
        "SELECT COUNT(*) FROM attendance_logs WHERE student_nin = ?", ("2099000101",)
    ).fetchone()[0]
    assert logs == 1


def test_html_disguised_as_xls_is_imported(client, csrf):
    html = """<!doctype html>
<html><head><meta charset="utf-8"></head><body>
<table>
  <tr><th>رقم التعريف الوطني</th><th>اللقب</th><th>الاسم</th><th>تاريخ الميلاد</th><th>القسم</th><th>رقم هاتف الولي</th></tr>
  <tr><td>2099000201</td><td>بلحاج</td><td>ريان</td><td>11/03/2009</td><td>1AS4</td><td>0550110301</td></tr>
  <tr><td>2099000202</td><td>سعيدي</td><td>مريم</td><td>2010-07-19</td><td>1AS4</td><td>0550110302</td></tr>
</table>
</body></html>""".encode("utf-8")

    parsed = _parse(client, csrf, html, "export.xls")
    assert parsed["file_type"] == "HTML table"
    assert parsed["total_rows"] == 2

    mapping = parsed["suggested_mapping"]
    preview = _preview(client, csrf, parsed["token"], mapping)
    assert preview["summary"]["valid"] == 2

    result = _commit(client, csrf, parsed["token"], mapping)
    assert result["inserted"] == 2

    student = client.get("/api/students/2099000201").get_json()["student"]
    assert student["birth_date"] == "2009-03-11"
    assert student["first_name"] == "ريان"


def test_invalid_file_returns_validation_error(client, csrf):
    response = client.post(
        "/api/import/parse",
        data={"file": (io.BytesIO(b"this is not a spreadsheet"), "broken.xlsx")},
        headers=csrf,
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "validation"


def test_shipped_samples_are_valid(client, csrf):
    samples = Path(__file__).resolve().parents[1] / "attendance_app" / "static" / "samples"
    xlsx = _parse(client, csrf, (samples / "students_sample.xlsx").read_bytes(), "students_sample.xlsx")
    assert xlsx["file_type"].startswith("XLSX")
    assert xlsx["total_rows"] == 6
    assert all(xlsx["suggested_mapping"][key] is not None for key in ("student_nin", "last_name", "first_name", "birth_date", "class_group", "parent_phone"))

    xls = _parse(client, csrf, (samples / "students_sample.xls").read_bytes(), "students_sample.xls")
    assert xls["file_type"] == "HTML table"
    assert xls["total_rows"] == 4
    assert xls["suggested_mapping"]["student_nin"] == 0
