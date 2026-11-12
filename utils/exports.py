"""utils/exports.py — Excel export helpers (formula-injection safe)."""
import io
from datetime import date
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill

HEADER_FILL = PatternFill("solid", fgColor="256B3E")
HEADER_FONT = Font(bold=True, color="FFFFFF")


def safe_cell(value):
    """
    Neutralise spreadsheet formula injection: a text cell that starts with
    = + - @ (or tab/CR) would be executed by Excel — prefix it with a quote.
    """
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def _style_header(ws, row_idx: int, num_cols: int):
    for col in range(1, num_cols + 1):
        cell = ws.cell(row=row_idx, column=col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")


def _finish(wb) -> io.BytesIO:
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def export_daily_attendance(klass, target_date: date, rows: list) -> io.BytesIO:
    """rows = [(kid, record_or_None)] — every active kid, recorded or not."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Attendance"
    ws.sheet_view.rightToLeft = True

    ws["A1"] = "الراعي الصالح — نظام إدارة خدمة مدارس الأحد"
    ws["A1"].font = Font(bold=True, size=14, color="256B3E")
    ws.merge_cells("A1:F1")
    ws["A2"] = safe_cell(f"الفصل: {klass.name_ar} / {klass.name_en}")
    ws["A3"] = f"التاريخ: {target_date.isoformat()}"

    header_row = 5
    headers = ["#", "الاسم بالعربية", "Name (English)", "الحالة", "ملاحظة", "سجّلها"]
    for col, h in enumerate(headers, start=1):
        ws.cell(row=header_row, column=col, value=h)
    _style_header(ws, header_row, len(headers))

    status_ar = {"present": "حاضر", "absent": "غائب", "excused": "معذور"}
    for i, (kid, rec) in enumerate(rows, start=1):
        r = header_row + i
        ws.cell(row=r, column=1, value=i)
        ws.cell(row=r, column=2, value=safe_cell(kid.name_ar))
        ws.cell(row=r, column=3, value=safe_cell(kid.name_en))
        ws.cell(row=r, column=4,
                value=status_ar.get(rec.status, rec.status) if rec else "لم يُسجَّل")
        ws.cell(row=r, column=5,
                value=safe_cell(rec.excuse_note or "") if rec else "")
        ws.cell(row=r, column=6,
                value=safe_cell(rec.taken_by.name_ar) if rec and rec.taken_by else "—")

    for col, width in zip("ABCDEF", [5, 28, 28, 12, 28, 22]):
        ws.column_dimensions[col].width = width
    return _finish(wb)


def export_class_summary(rows: list, target_date: date) -> io.BytesIO:
    """rows = output of utils.queries.class_attendance_overview()."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.sheet_view.rightToLeft = True

    ws["A1"] = "الراعي الصالح — ملخص الحضور"
    ws["A1"].font = Font(bold=True, size=14, color="256B3E")
    ws.merge_cells("A1:G1")
    ws["A2"] = f"التاريخ: {target_date.isoformat()}"

    header_row = 4
    headers = ["المرحلة", "الفصل", "حاضر", "غائب", "معذور", "الإجمالي", "النسبة %"]
    for col, h in enumerate(headers, start=1):
        ws.cell(row=header_row, column=col, value=h)
    _style_header(ws, header_row, len(headers))

    for i, row in enumerate(rows, start=1):
        r = header_row + i
        pct = round(row["present"] * 100 / row["total"]) if row["total"] else 0
        values = [safe_cell(row["grade"].name_ar), safe_cell(row["klass"].name_ar),
                  row["present"], row["absent"], row["excused"], row["total"], pct]
        for c, v in enumerate(values, start=1):
            ws.cell(row=r, column=c, value=v)

    for col, width in zip("ABCDEFG", [24, 24, 10, 10, 10, 12, 12]):
        ws.column_dimensions[col].width = width
    return _finish(wb)
