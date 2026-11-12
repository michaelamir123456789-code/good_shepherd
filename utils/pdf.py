"""
utils/pdf.py — Attendance reports as PDF (Arabic right-to-left or English left-to-right).

Arabic text needs two steps before ReportLab can draw it: letter SHAPING (letters change
form inside a word) and BIDI re-ordering. Both are done in `shape()`. Noto Sans Arabic is
bundled in static/fonts/pdf, so the output looks the same on every machine.
"""
import io
import os
import re

import arabic_reshaper
try:                                     # python-bidi 0.4.x: full algorithm incl. bracket mirroring
    from bidi.algorithm import get_display
    _MIRROR_NEEDED = False
except ImportError:                      # pragma: no cover  (python-bidi >= 0.5 does NOT mirror brackets)
    from bidi import get_display
    _MIRROR_NEEDED = True
_MIRROR = str.maketrans("()[]{}<>«»", ")(][}{><»«")
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "fonts", "pdf")
_ARABIC = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")
_registered = False
# Latin words, codes and numbers (School-77, Ref-4821, 2026-11-09 10:30, 49%, john@x.com, Peter Maged)
# must stay ONE left-to-right unit inside Arabic text, otherwise the bidi step scrambles them.
_WORD = r"[A-Za-z0-9][A-Za-z0-9_.,:/@+%\-]*"
_NUMBERS = re.compile(rf"{_WORD}(?: {_WORD})*")

GREEN = colors.HexColor("#226a41")
INK = colors.HexColor("#1d2925")
MUTED = colors.HexColor("#6a7770")
LINE = colors.HexColor("#e7e2d4")
STRIPE = colors.HexColor("#f6f3ea")
OK, BAD, WARN = colors.HexColor("#1f7a45"), colors.HexColor("#b4352b"), colors.HexColor("#9a6a0b")

_reshaper = arabic_reshaper.ArabicReshaper(configuration={"delete_harakat": True, "support_ligatures": True})


def _register():
    global _registered
    if _registered:
        return
    pdfmetrics.registerFont(TTFont("NotoAr", os.path.join(_FONT_DIR, "NotoSansArabic-Regular.ttf")))
    pdfmetrics.registerFont(TTFont("NotoAr-Bold", os.path.join(_FONT_DIR, "NotoSansArabic-Bold.ttf")))
    pdfmetrics.registerFontFamily("NotoAr", normal="NotoAr", bold="NotoAr-Bold")
    _registered = True


def shape(text) -> str:
    """Arabic text -> what must be drawn, left to right, on the page."""
    text = "" if text is None else str(text)
    if not _ARABIC.search(text):
        return text
    # Numbers (dates, times, 49%, phone numbers) must stay left-to-right inside Arabic text
    text = _NUMBERS.sub(lambda m: "\u202a" + m.group() + "\u202c", text)
    visual = get_display(_reshaper.reshape(text)).replace("\u200e", "")
    return visual.translate(_MIRROR) if _MIRROR_NEEDED else visual


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fit(text, font, size, width) -> str:
    """Shape the text, cutting it (with …) so it fits `width` points."""
    text = "" if text is None else " ".join(str(text).split())
    if pdfmetrics.stringWidth(shape(text), font, size) <= width:
        return shape(text)
    while text and pdfmetrics.stringWidth(shape(text + "…"), font, size) > width:
        text = text[:-1]
    return shape(text + "…" if text else "")


MAX_CELL_LINES = 30      # a table row can't span pages, so one cell may never be taller than a page


def wrap_lines(text, font, size, width) -> list[str]:
    """Break text into lines that fit `width`; every line is shaped on its own (correct for Arabic)."""
    out = []
    for para in ("" if text is None else str(text)).replace("\r", "").split("\n"):
        words, line = para.split(), []
        for w in words:
            trial = " ".join(line + [w])
            if not line or pdfmetrics.stringWidth(shape(trial), font, size) <= width:
                line.append(w)
                continue
            out.append(" ".join(line))
            line = [w]
        if line or not words:
            out.append(" ".join(line))
    fixed = []
    for ln in out:                                     # a single very long word: cut it by characters
        while pdfmetrics.stringWidth(shape(ln), font, size) > width and len(ln) > 1:
            cut = len(ln)
            while cut > 1 and pdfmetrics.stringWidth(shape(ln[:cut]), font, size) > width:
                cut -= 1
            fixed.append(ln[:cut])
            ln = ln[cut:]
        fixed.append(ln)
    if len(fixed) > MAX_CELL_LINES:
        fixed = fixed[:MAX_CELL_LINES - 1] + [fixed[MAX_CELL_LINES - 1] + " …"]
    return [shape(x) for x in fixed]


def _styles(rtl):
    align = TA_RIGHT if rtl else TA_LEFT
    base = dict(fontName="NotoAr", fontSize=10, leading=14, textColor=INK, alignment=align)
    return {
        "cell": ParagraphStyle("cell", **base),
        "cellc": ParagraphStyle("cellc", **{**base, "alignment": TA_CENTER}),
        "head": ParagraphStyle("head", **{**base, "fontName": "NotoAr-Bold", "textColor": colors.white, "fontSize": 9.5}),
        "headc": ParagraphStyle("headc", **{**base, "fontName": "NotoAr-Bold", "textColor": colors.white,
                                           "fontSize": 9.5, "alignment": TA_CENTER}),
        "title": ParagraphStyle("title", **{**base, "fontName": "NotoAr-Bold", "fontSize": 18, "leading": 24,
                                           "textColor": GREEN}),
        "sub": ParagraphStyle("sub", **{**base, "fontSize": 11, "textColor": MUTED}),
        "brand": ParagraphStyle("brand", **{**base, "fontName": "NotoAr-Bold", "fontSize": 10, "textColor": MUTED}),
    }


def _para(text, style, width, font="NotoAr", size=10, color=None, wrap=False):
    if wrap:
        s = "<br/>".join(_esc(x) for x in wrap_lines(text, font, size, width - 12))
    else:
        s = _esc(fit(text, font, size, width - 12))
    if color is not None:
        s = f'<font color="{color.hexval().replace("0x", "#")}">{s}</font>'
    return Paragraph(s, style)


def _build(story, rtl, footer_text, title, pagesize=A4):
    buf = io.BytesIO()
    page_w = pagesize[0]

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("NotoAr", 8)
        canvas.setFillColor(MUTED)
        y = 10 * mm
        page = shape(("صفحة " if rtl else "Page ") + str(doc.page))
        foot = shape(footer_text)
        if rtl:
            canvas.drawRightString(page_w - 15 * mm, y, foot)
            canvas.drawString(15 * mm, y, page)
        else:
            canvas.drawString(15 * mm, y, foot)
            canvas.drawRightString(page_w - 15 * mm, y, page)
        canvas.setStrokeColor(LINE)
        canvas.line(15 * mm, y + 4 * mm, page_w - 15 * mm, y + 4 * mm)
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=pagesize, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=16 * mm, bottomMargin=20 * mm, title=title, author="Good Shepherd")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


def _table(headers, rows, widths, rtl, st, center_cols=(), colors_by_cell=None, wrap_cols=()):
    """Header + rows. For right-to-left the columns are mirrored so the first one is on the right."""
    order = list(range(len(headers)))
    if rtl:
        order.reverse()
    total = [[_para(headers[i], st["headc"] if i in center_cols else st["head"], widths[i], "NotoAr-Bold", 9.5)
              for i in order]]
    for ri, r in enumerate(rows):
        line = []
        for i in order:
            color = (colors_by_cell or {}).get((ri, i))
            line.append(_para(r[i], st["cellc"] if i in center_cols else st["cell"], widths[i],
                              "NotoAr-Bold" if color is not None else "NotoAr", 10, color, wrap=i in wrap_cols))
        total.append(line)
    t = Table(total, colWidths=[widths[i] for i in order], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), GREEN),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, STRIPE]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def _section(title, st, rtl):
    return Paragraph(_esc(shape(title)), ParagraphStyle("sec", parent=st["cell"], fontName="NotoAr-Bold",
                                                         fontSize=12, leading=16, textColor=GREEN,
                                                         spaceBefore=8, spaceAfter=3))


def _header(brand, title, subtitle, st, rtl):
    return [Paragraph(_esc(shape(brand)), st["brand"]), Spacer(1, 2 * mm),
            Paragraph(_esc(shape(title)), st["title"]),
            Paragraph(_esc(shape(subtitle)), st["sub"]), Spacer(1, 6 * mm)]


def _chips(items, st, rtl, total_w):
    """A row of small summary boxes: [(label, value, color)]."""
    cells = [[_para(f"{label}: {value}", st["cellc"], total_w / len(items), "NotoAr-Bold", 10.5, col)
              for label, value, col in (reversed(items) if rtl else items)]]
    t = Table(cells, colWidths=[total_w / len(items)] * len(items))
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, LINE), ("INNERGRID", (0, 0), (-1, -1), 0.6, LINE),
                           ("BACKGROUND", (0, 0), (-1, -1), STRIPE), ("TOPPADDING", (0, 0), (-1, -1), 7),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    return t


# =====================================================================  public API
def daily_pdf(brand, class_name, grade_name, date_text, rows, stats, lang, generated_by, generated_at):
    """rows = [{'name','status','note','by'}]; status in present|absent|excused|None."""
    _register()
    rtl = lang == "ar"
    st = _styles(rtl)
    L = (lambda ar, en: ar if rtl else en)
    W = A4[0] - 30 * mm
    labels = {"present": L("حاضر", "Present"), "absent": L("غائب", "Absent"),
              "excused": L("معذور", "Excused"), None: L("لم يُسجَّل", "Pending")}
    col = {"present": OK, "absent": BAD, "excused": WARN, None: MUTED}
    widths = [10 * mm, W * 0.34, 24 * mm, W * 0.26, W - (10 * mm + W * 0.34 + 24 * mm + W * 0.26)]
    headers = ["#", L("الطفل", "Kid"), L("الحالة", "Status"), L("ملاحظة", "Note"), L("سجّلها", "Recorded by")]
    body, cc = [], {}
    for i, r in enumerate(rows):
        body.append([str(i + 1), r["name"], labels[r["status"]], r.get("note") or "", r.get("by") or "—"])
        cc[(i, 2)] = col[r["status"]]
    story = _header(brand, L("تقرير حضور الفصل", "Class attendance report") + " — " + class_name,
                    f"{grade_name}  ·  {date_text}", st, rtl)
    story.append(_chips([(L("حاضر", "Present"), stats["present"], OK), (L("غائب", "Absent"), stats["absent"], BAD),
                         (L("معذور", "Excused"), stats["excused"], WARN),
                         (L("الإجمالي", "Total"), len(rows), INK)], st, rtl, W))
    story.append(Spacer(1, 5 * mm))
    story.append(_table(headers, body, widths, rtl, st, center_cols=(0, 2), colors_by_cell=cc))
    return _build(story, rtl, f"{generated_by} · {generated_at}", L("تقرير الحضور", "Attendance report"))


def summary_pdf(brand, date_text, rows, totals, lang, generated_by, generated_at, scope_name=""):
    """rows = [{'grade','klass','present','absent','excused','total','taken'}]."""
    _register()
    rtl = lang == "ar"
    st = _styles(rtl)
    L = (lambda ar, en: ar if rtl else en)
    W = A4[0] - 30 * mm
    widths = [W * 0.22, W * 0.24, 17 * mm, 17 * mm, 17 * mm, 17 * mm, W - (W * 0.46 + 68 * mm)]
    headers = [L("المرحلة", "Grade"), L("الفصل", "Class"), L("حاضر", "Present"), L("غائب", "Absent"),
               L("معذور", "Excused"), L("الإجمالي", "Total"), L("النسبة", "Rate")]
    body = []
    for r in rows:
        if r["taken"]:
            pct = f'{round(r["present"] * 100 / r["total"])}%' if r["total"] else "—"
            body.append([r["grade"], r["klass"], str(r["present"]), str(r["absent"]), str(r["excused"]),
                         str(r["total"]), pct])
        else:
            body.append([r["grade"], r["klass"], "—", "—", "—", str(r["total"]), L("لم يُسجَّل", "Pending")])
    story = _header(brand, L("ملخص الحضور", "Attendance summary") + (f" — {scope_name}" if scope_name else ""),
                    date_text, st, rtl)
    story.append(_chips([(L("حاضر", "Present"), totals["present"], OK), (L("غائب", "Absent"), totals["absent"], BAD),
                         (L("معذور", "Excused"), totals["excused"], WARN),
                         (L("النسبة", "Rate"), f'{totals["percent"]}%', GREEN)], st, rtl, W))
    story.append(Spacer(1, 5 * mm))
    story.append(_table(headers, body, widths, rtl, st, center_cols=(2, 3, 4, 5, 6)))
    return _build(story, rtl, f"{generated_by} · {generated_at}", L("ملخص الحضور", "Attendance summary"))


# =====================================================================  more reports
def _L(rtl):
    return lambda ar, en: ar if rtl else en


def _fit_widths(fixed, total, wrap_index):
    """fixed = list of widths (None for the flexible column)."""
    rest = total - sum(w for w in fixed if w)
    return [w if w else rest for w in fixed]


def kids_pdf(brand, class_name, grade_name, rows, lang, generated_by, generated_at):
    """The class list with contact details. rows: dicts (name, mother_name, father_phone, mother_phone, school, area, address, birth)."""
    _register()
    rtl = lang == "ar"
    st = _styles(rtl)
    L = _L(rtl)
    page = landscape(A4)
    W = page[0] - 30 * mm
    widths = _fit_widths([8 * mm, 40 * mm, 32 * mm, 28 * mm, 28 * mm, 32 * mm, 22 * mm, None, 26 * mm], W, 7)
    headers = ["#", L("الاسم", "Name"), L("اسم الأم", "Mother"), L("هاتف الأب", "Father phone"),
               L("هاتف الأم", "Mother phone"), L("المدرسة", "School"), L("المنطقة", "Area"),
               L("العنوان", "Address"), L("الميلاد", "Born")]
    body = [[str(i + 1), r["name"], r.get("mother_name") or "—", r.get("father_phone") or "—",
             r.get("mother_phone") or "—", r.get("school") or "—", r.get("area") or "—",
             r.get("address") or "—", r.get("birth") or "—"] for i, r in enumerate(rows)]
    story = _header(brand, L("قائمة أطفال الفصل", "Class list") + " — " + class_name,
                    f"{grade_name}  ·  {len(rows)} " + L("طفل", "kids"), st, rtl)
    story.append(_table(headers, body, widths, rtl, st, center_cols=(0,), wrap_cols=(1, 2, 5, 7)))
    return _build(story, rtl, f"{generated_by} · {generated_at}", L("قائمة الأطفال", "Kids list"), page)


def class_period_pdf(brand, class_name, grade_name, dates, rows, lang, generated_by, generated_at):
    """Who came on each of the last meetings. dates: [date]; rows: [{'name','marks':{date: status},'present','absent','excused'}]."""
    _register()
    rtl = lang == "ar"
    st = _styles(rtl)
    L = _L(rtl)
    page = landscape(A4)
    W = page[0] - 30 * mm
    letter = {"present": L("ح", "P"), "absent": L("غ", "A"), "excused": L("ع", "E")}
    col = {"present": OK, "absent": BAD, "excused": WARN}
    date_w = min(15 * mm, max(10 * mm, (W - 8 * mm - 55 * mm - 3 * 13 * mm) / max(len(dates), 1)))
    name_w = W - 8 * mm - date_w * len(dates) - 3 * 13 * mm          # the name column takes the rest
    widths = [8 * mm, name_w] + [date_w] * len(dates) + [13 * mm] * 3
    headers = ["#", L("الطفل", "Kid")] + [f"{d.day}/{d.month}" for d in dates] + \
              [L("حاضر", "Pres."), L("غائب", "Abs."), L("معذور", "Exc.")]
    body, cc = [], {}
    for ri, r in enumerate(rows):
        line = [str(ri + 1), r["name"]]
        for ci, d in enumerate(dates):
            stt = r["marks"].get(d)
            line.append(letter.get(stt, "—"))
            if stt:
                cc[(ri, 2 + ci)] = col[stt]
        line += [str(r["present"]), str(r["absent"]), str(r["excused"])]
        body.append(line)
    story = _header(brand, L("حضور الفصل عبر الاجتماعات", "Class attendance over time") + " — " + class_name,
                    f"{grade_name}  ·  " + (f"{dates[0]} {L('إلى', 'to')} {dates[-1]}" if dates else ""), st, rtl)
    story.append(Paragraph(_esc(shape(L("ح = حاضر   غ = غائب   ع = معذور   — = لم يُسجَّل",
                                         "P = present   A = absent   E = excused   — = not recorded"))), st["sub"]))
    story.append(Spacer(1, 3 * mm))
    center = tuple(i for i in range(len(headers)) if i != 1)
    story.append(_table(headers, body, widths, rtl, st, center_cols=center, colors_by_cell=cc, wrap_cols=(1,)))
    return _build(story, rtl, f"{generated_by} · {generated_at}", L("حضور الفصل", "Class attendance"), page)


def visits_pdf(brand, class_label, period_label, by_label, chips, visit_rows, not_visited, lang,
               generated_by, generated_at):
    """
    The visitation trace. visit_rows: [{'date','kid','klass','type','by','notes'}] (full notes, wrapped);
    not_visited: [{'kid','klass','phone','area'}].  chips = (visits, kids_visited, kids_not_visited).
    """
    _register()
    rtl = lang == "ar"
    st = _styles(rtl)
    L = _L(rtl)
    page = landscape(A4)
    W = page[0] - 30 * mm
    story = _header(brand, L("تقرير الافتقاد", "Visitation report") + " — " + class_label,
                    f"{period_label}  ·  {by_label}", st, rtl)
    story.append(_chips([(L("افتقادات", "Visits"), chips[0], INK),
                         (L("أطفال تم افتقادهم", "Kids visited"), chips[1], OK),
                         (L("لم يُفتقدوا بعد", "Not visited yet"), chips[2], BAD)], st, rtl, W))
    story.append(_section(L("سجل الافتقاد", "Visit log"), st, rtl))
    if visit_rows:
        widths = _fit_widths([24 * mm, 42 * mm, 30 * mm, 26 * mm, 34 * mm, None], W, 5)
        headers = [L("التاريخ", "Date"), L("الطفل", "Kid"), L("الفصل", "Class"), L("النوع", "Type"),
                   L("افتقده", "Visited by"), L("الملاحظات", "Notes")]
        body = [[r["date"], r["kid"], r["klass"], r["type"], r["by"] or "—", r["notes"] or "—"] for r in visit_rows]
        story.append(_table(headers, body, widths, rtl, st, center_cols=(0,), wrap_cols=(1, 2, 3, 4, 5)))
    else:
        story.append(Paragraph(_esc(shape(L("لا توجد افتقادات في هذه الفترة.", "No visits in this period."))), st["sub"]))
    story.append(_section(L("لم يُفتقدوا بعد", "Not visited yet"), st, rtl))
    if not_visited:
        widths = _fit_widths([10 * mm, 60 * mm, 40 * mm, 36 * mm, None], W, 4)
        headers = ["#", L("الطفل", "Kid"), L("الفصل", "Class"), L("المنطقة", "Area"), L("الهاتف", "Phone")]
        body = [[str(i + 1), r["kid"], r["klass"], r["area"] or "—", r["phone"] or "—"] for i, r in enumerate(not_visited)]
        story.append(_table(headers, body, widths, rtl, st, center_cols=(0,), wrap_cols=(1, 2)))
    else:
        story.append(Paragraph(_esc(shape(L("تم افتقاد جميع الأطفال مرة على الأقل.", "Every kid has been visited at least once."))), st["sub"]))
    return _build(story, rtl, f"{generated_by} · {generated_at}", L("تقرير الافتقاد", "Visitation report"), page)


def kid_profile_pdf(brand, kid, attendance, visits, notes, stats, photo_path, lang, generated_by, generated_at):
    """
    One kid, everything we know. kid = dict of labelled fields [(label, value)] plus 'name','klass';
    attendance = [{'date','status','note'}]; visits = [{'date','type','by','notes'}]; notes = [{'date','by','cat','text'}].
    """
    _register()
    rtl = lang == "ar"
    st = _styles(rtl)
    L = _L(rtl)
    W = A4[0] - 30 * mm
    labels = {"present": L("حاضر", "Present"), "absent": L("غائب", "Absent"), "excused": L("معذور", "Excused")}
    col = {"present": OK, "absent": BAD, "excused": WARN}

    title = Paragraph(_esc(shape(kid["name"])), st["title"])
    sub = Paragraph(_esc(shape(kid.get("klass") or "")), st["sub"])
    head_cells = [[title], [sub]]
    block = Table(head_cells, colWidths=[W - 34 * mm])
    block.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    parts = [block]
    if photo_path and os.path.exists(photo_path):
        try:
            parts = [block, Image(photo_path, width=28 * mm, height=28 * mm)]
        except Exception:                                  # unreadable photo: just leave it out
            parts = [block]
    if rtl:
        parts.reverse()
    widths = [W - 34 * mm, 34 * mm] if len(parts) == 2 else [W]
    if rtl and len(parts) == 2:
        widths.reverse()
    top = Table([parts], colWidths=widths)
    top.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                             ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story = [Paragraph(_esc(shape(brand)), st["brand"]), Spacer(1, 2 * mm), top, Spacer(1, 4 * mm)]

    # details: two columns of "label  value"
    fields = [(a, b) for a, b in kid["fields"] if b]
    if fields:
        w2 = [W * 0.26, W * 0.74]
        body = [[a, b] for a, b in fields]
        t = _table([L("البيان", "Field"), L("القيمة", "Value")], body, w2, rtl, st, wrap_cols=(1,))
        story.append(t)

    story.append(_chips([(L("حاضر", "Present"), stats["present"], OK), (L("غائب", "Absent"), stats["absent"], BAD),
                         (L("معذور", "Excused"), stats["excused"], WARN), (L("النسبة", "Rate"), f'{stats["percent"]}%', GREEN)],
                        st, rtl, W))
    if attendance:
        story.append(_section(L("سجل الحضور", "Attendance"), st, rtl))
        w3 = _fit_widths([30 * mm, 24 * mm, None], W, 2)
        story.append(_table([L("التاريخ", "Date"), L("الحالة", "Status"), L("ملاحظة", "Note")],
                            [[a["date"], labels[a["status"]], a.get("note") or ""] for a in attendance], w3, rtl, st,
                            center_cols=(0, 1), wrap_cols=(2,),
                            colors_by_cell={(i, 1): col[a["status"]] for i, a in enumerate(attendance)}))
    story.append(_section(L("الافتقاد", "Visits"), st, rtl))
    if visits:
        w4 = _fit_widths([26 * mm, 24 * mm, 36 * mm, None], W, 3)
        story.append(_table([L("التاريخ", "Date"), L("النوع", "Type"), L("افتقده", "By"), L("الملاحظات", "Notes")],
                            [[v["date"], v["type"], v["by"] or "—", v["notes"] or "—"] for v in visits], w4, rtl, st,
                            center_cols=(0,), wrap_cols=(2, 3)))
    else:
        story.append(Paragraph(_esc(shape(L("لم يُفتقد بعد.", "Not visited yet."))), st["sub"]))
    if notes:
        story.append(_section(L("الملاحظات", "Notes"), st, rtl))
        w5 = _fit_widths([26 * mm, 36 * mm, 22 * mm, None], W, 3)
        story.append(_table([L("التاريخ", "Date"), L("كتبها", "By"), L("النوع", "Type"), L("الملاحظة", "Note")],
                            [[n["date"], n["by"] or "—", n["cat"], n["text"]] for n in notes], w5, rtl, st,
                            center_cols=(0,), wrap_cols=(1, 3)))
    return _build(story, rtl, f"{generated_by} · {generated_at}", kid["name"])
