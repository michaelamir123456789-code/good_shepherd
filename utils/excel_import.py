"""
utils/excel_import.py — Read an Excel / CSV sheet of kids WITHOUT losing data.

Principles
----------
* The class is chosen by the person importing — the sheet never decides it.
* Only the kid's NAME is required. Anything missing stays empty.
* Nothing is thrown away: a column the system has no field for, a phone cell with two
  numbers, a date it can't read, text that doesn't fit — all of it is appended to the
  kid's NOTES so it can be seen later.
* Column names are recognised flexibly (Arabic / English / spelling variants) and the
  header row can be anywhere in the first rows of the sheet.
"""
import csv
import io
import re
from datetime import datetime, date, timedelta

from openpyxl import load_workbook

from utils.identity import normalize_phone

# ------------------------------------------------------------------ labels
FIELD_LABELS = {
    "name_ar": "الاسم", "name_en": "الاسم بالإنجليزية", "mother_name": "اسم الأم",
    "father_phone": "هاتف الأب", "mother_phone": "هاتف الأم", "kid_phone": "هاتف الطفل",
    "school": "المدرسة", "address": "العنوان", "area": "المنطقة",
    "birth_date": "تاريخ الميلاد", "notes": "ملاحظات", "siblings": "الإخوة والأخوات",
    "confession_father": "أب الاعتراف", "gender": "النوع",
}
FIELD_MAX = {"name_ar": 120, "name_en": 120, "mother_name": 120, "school": 120, "address": 255,
             "area": 80, "siblings": 500, "confession_father": 120}
KID_FIELDS = list(FIELD_LABELS)

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_TASHKEEL = re.compile(r"[\u064B-\u065F\u0670\u0640\u200c\u200d\u200e\u200f]")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def norm(text) -> str:
    """Lower-case, strip diacritics, unify Arabic letter variants and punctuation."""
    s = str(text or "").lower()
    s = _TASHKEEL.sub("", s)
    s = (s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ٱ", "ا")
          .replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي"))
    s = re.sub(r"[:：()\[\]{}/\\\-_.,،؛;*#?؟'\"’`]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ----------------------------------------------------- header recognition
_MOTHER = {"ام", "الام", "امي", "ماما", "mother", "mothers", "mom", "mama", "mum"}
_FATHER = {"اب", "الاب", "ابو", "ابي", "بابا", "father", "fathers", "dad", "papa", "guardian", "parent"}
_KID = {"الطفل", "طفل", "الولد", "ولد", "البنت", "بنت", "kid", "child", "الطفله", "student", "الطالب"}
_PHONE_AR = ("تليفون", "تلفون", "موبايل", "محمول", "هاتف", "رقم", "واتس", "جوال")
_PHONE_EN = {"phone", "mobile", "tel", "cell", "whatsapp", "number", "mob", "contact"}
_NAME_WORDS = {"اسم", "الاسم", "الطفل", "طفل", "الولد", "ولد", "البنت", "بنت", "بالكامل", "الكامل",
               "كامل", "رباعي", "ثلاثي", "ثنائي", "name", "child", "kid", "full", "student", "الطالب",
               "الاطفال", "الاسماء", "اسماء", "العربي", "بالعربي", "بالعربيه", "عربي"}
_SERIAL = {"م", "#", "مسلسل", "رقم", "no", "id", "serial", "ر", "ك", "n", "كود"}


def classify_header(raw):
    """-> field name, 'ignore' (row numbers / grade+class columns) or None (unknown => goes to notes)."""
    h = norm(raw)
    if not h:
        return "ignore"
    if h in _SERIAL:
        return "ignore"
    words = set(h.split())
    if "اعتراف" in h or "confess" in h:
        return "confession_father"
    if (any(k in h for k in ("اخوه", "اخوات", "sibling", "brother", "sister"))
            or "اخت" in words or "اخ" in words):
        return "siblings"
    if "انجليز" in h or "english" in h:
        return "name_en"
    has_phone = any(w in h for w in _PHONE_AR) or bool(words & _PHONE_EN)
    if has_phone:
        if words & _MOTHER:
            return "mother_phone"
        if words & _FATHER or "ولي" in words:
            return "father_phone"
        if words & _KID:
            return "kid_phone"
        return "father_phone"                       # a lone "phone" column = the family's number
    if words & _MOTHER:
        return "mother_name"
    if words & _FATHER:
        return None                                 # father's name: kept in notes
    if "مدرس" in h or "school" in h:
        return "school"
    if "عنوان" in h or "address" in h or "location" in h or "لوكيشن" in h or "موقع" in h or "سكن" in h:
        return "address"
    if "منطق" in h or "area" in h or h in ("حي", "الحي", "district"):
        return "area"
    if "ميلاد" in h or "birth" in h or "dob" in h:
        return "birth_date"
    if "ملاحظ" in h or "تعليق" in h or "comment" in h or "note" in h or "remark" in h:
        return "notes"
    if "جنس" in h or "النوع" in h or h in ("نوع", "gender", "sex"):
        return "gender"
    if ("مرحل" in h or "فصل" in h or "grade" in h or "class" in h or "stage" in h
            or ("الصف" in h and "دراس" not in h)):
        return "ignore"
    if words and words <= _NAME_WORDS and ("اسم" in h or "الاسم" in h or "name" in h
                                           or words & {"الطفل", "طفل", "الولد", "ولد", "child", "kid"}):
        return "name_ar"
    return None


# ------------------------------------------------------------ cell helpers
def cell_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, (datetime, date)):
        d = v.date() if isinstance(v, datetime) else v
        return d.isoformat()
    s = _CTRL.sub("", str(v)).replace("\r\n", "\n").replace("\r", "\n")
    return s.strip()


def one_line(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


_PHONE_TOKEN = re.compile(r"\+?\d[\d\s\-().]{5,}\d")


def extract_phones(text: str):
    """-> (valid_numbers, leftovers).  leftovers = pieces that must be kept in notes."""
    t = text.translate(_DIGITS)
    numbers, leftovers = [], []
    for m in _PHONE_TOKEN.finditer(t):
        digits = re.sub(r"\D", "", m.group())
        if len(digits) > 15:                      # several numbers glued together by spaces
            chunks = re.findall(r"01\d{9}", digits)
            if chunks and "".join(chunks) == digits:
                numbers += [normalize_phone(c) for c in chunks]
            else:
                leftovers.append(one_line(m.group()))
            continue
        n = normalize_phone(m.group())
        if n and 8 <= len(n) <= 15:
            numbers.append(n)
        else:
            leftovers.append(one_line(m.group()))
    rest = _PHONE_TOKEN.sub(" ", t)
    if re.search(r"[^\W\d_]", rest):              # real words next to the number (e.g. "ماما")
        leftovers.append(one_line(text))
    elif not numbers and not leftovers and re.search(r"\d", t):
        leftovers.append(one_line(text))          # short digit string like "123"
    # keep order, drop repeats
    seen, uniq = set(), []
    for n in numbers:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq, leftovers


def parse_date_text(s: str, today: date):
    """-> date | None. Day-first (Egypt), also y-m-d, Excel serials and 2-digit years."""
    t = s.translate(_DIGITS).strip()
    if not t:
        return None
    if re.fullmatch(r"\d{5}(\.\d+)?", t):                     # Excel serial number
        try:
            d = (datetime(1899, 12, 30) + timedelta(days=float(t))).date()
            return d if today.year - 30 <= d.year <= today.year else None
        except (OverflowError, ValueError):
            return None
    m = re.fullmatch(r"(\d{1,4})[/\-.\s\\](\d{1,2})[/\-.\s\\](\d{1,4})", t)
    if not m:
        return None
    a, b, c = m.groups()
    tries = []
    if len(a) == 4:
        tries.append((int(a), int(b), int(c)))                # y-m-d
    else:
        y = int(c)
        if len(c) <= 2:
            y += 2000 if 2000 + y <= today.year else 1900
        tries.append((y, int(b), int(a)))                     # d/m/y  (preferred)
        tries.append((y, int(a), int(b)))                     # m/d/y  (only if d/m is impossible)
    for y, mo, d in tries:
        try:
            dt = date(y, mo, d)
        except ValueError:
            continue
        return dt if today.year - 30 <= dt.year <= today.year else None
    return None


def parse_gender(value):
    s = norm(value)
    if s in ("male", "m", "boy", "ولد", "ذكر", "ذكور", "اولاد"):
        return "male"
    if s in ("female", "f", "girl", "بنت", "انثي", "اناث", "بنات"):
        return "female"
    return None


# ---------------------------------------------------------------- reading
def _read_xlsx(file_bytes):
    wb = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    sheets = []
    for ws in wb.worksheets:
        rows = []
        for i, r in enumerate(ws.iter_rows(values_only=True)):
            rows.append(list(r))
            if i > 25000:
                break
        if any(any(c not in (None, "") for c in r) for r in rows):
            sheets.append((ws.title, rows))
    wb.close()
    return sheets


def _read_csv(file_bytes):
    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1256", "latin-1"):
        try:
            text = file_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("تعذّر قراءة الملف — احفظه بصيغة UTF-8 أو Excel (.xlsx)")
    sample = text[:4096]
    delim = max((",", ";", "\t"), key=sample.count)
    return [("CSV", [list(r) for r in csv.reader(io.StringIO(text), delimiter=delim)])]


def _find_header(rows):
    """The row (within the first 15) that looks most like a header and has a name column."""
    best = None
    for i, row in enumerate(rows[:15]):
        kinds = [classify_header(c) if c not in (None, "") else None for c in row]
        known = [k for k in kinds if k not in (None, "ignore")]
        if "name_ar" not in known:
            for j, c in enumerate(row):               # fallback: any header containing اسم / name
                hc = norm(c)
                if kinds[j] is None and ("اسم" in hc or "name" in hc) and not (set(hc.split()) & (_MOTHER | _FATHER)):
                    kinds[j] = "name_ar"
                    known.append("name_ar")
                    break
        if "name_ar" in known:
            score = len(known)
            if best is None or score > best[0]:
                best = (score, i, kinds)
    return best


# -------------------------------------------------------------------- main
def parse_file(filename: str, file_bytes: bytes, max_rows: int = 3000, today: date | None = None) -> dict:
    today = today or date.today()
    result = {"ok": False, "errors": [], "warnings": [], "notes": [], "rows": [],
              "mapping": [], "ignored": [], "extras": [], "sheet": "", "total_rows": 0}
    fname = (filename or "").lower()
    try:
        if fname.endswith((".xlsx", ".xlsm")):
            sheets = _read_xlsx(file_bytes)
        elif fname.endswith(".csv"):
            sheets = _read_csv(file_bytes)
        elif fname.endswith(".xls"):
            result["errors"].append("ملفات .xls القديمة غير مدعومة. افتحه في Excel واحفظه بصيغة .xlsx ثم ارفعه.")
            return result
        else:
            result["errors"].append("نوع الملف غير مدعوم. استخدم .xlsx أو .csv")
            return result
    except ValueError as e:
        result["errors"].append(str(e))
        return result
    except Exception:
        result["errors"].append("تعذّر قراءة الملف. تأكد أنه ملف Excel (.xlsx) أو CSV سليم.")
        return result

    if not sheets:
        result["errors"].append("الملف فارغ")
        return result

    chosen = None
    for title, rows in sheets:
        found = _find_header(rows)
        if found:
            chosen = (title, rows, found)
            break
    if not chosen:
        first = next((r for r in sheets[0][1] if any(cell_text(c) for c in r)), [])
        seen = [one_line(cell_text(c)) for c in first if cell_text(c)][:8]
        result["errors"].append("لم أجد عمود الاسم. يجب أن يحتوي الجدول على عمود عنوانه «الاسم» (أو «اسم الطفل»).")
        if seen:
            result["errors"].append("العناوين التي وجدتُها: " + "، ".join(seen))
        return result

    title, rows, (_, header_idx, kinds) = chosen
    header = rows[header_idx]
    result["sheet"] = title

    # ---- column plan (the first column wins for each field; repeats go to notes)
    plan, used = {}, set()
    for j, kind in enumerate(kinds):
        raw = cell_text(header[j]) if j < len(header) else ""
        if kind == "ignore":
            if raw:
                result["ignored"].append(raw)
            continue
        if kind is None or kind in used:
            if raw:
                plan[j] = ("extra", raw)
                result["extras"].append(raw)
            continue
        used.add(kind)
        plan[j] = (kind, raw)
        result["mapping"].append({"header": raw, "field": kind, "label": FIELD_LABELS[kind]})

    data_rows = rows[header_idx + 1:]
    if len(data_rows) > max_rows:
        result["errors"].append(f"الملف كبير جداً ({len(data_rows)} صف). الحد الأقصى {max_rows} صف في المرة.")
        return result

    for number, raw_row in enumerate(data_rows, start=header_idx + 2):
        if not any(c not in (None, "") for c in raw_row):
            continue
        result["total_rows"] += 1
        row = {k: None for k in KID_FIELDS}
        row["row_number"] = number
        extra_lines, conv_notes, own_notes, row_info = [], [], "", []

        for j, (kind, label) in plan.items():
            if j >= len(raw_row):
                continue
            val = raw_row[j]
            text = cell_text(val)
            if not text:
                continue
            if kind == "extra":
                extra_lines.append(f"{label}: {one_line(text)}")
            elif kind == "notes":
                own_notes = text
            elif kind in ("father_phone", "mother_phone", "kid_phone"):
                nums, left = extract_phones(text)
                if nums:
                    row[kind] = nums[0]
                    for n in nums[1:]:
                        conv_notes.append(f"رقم إضافي ({FIELD_LABELS[kind]}): {n}")
                for piece in left:
                    conv_notes.append(f"{FIELD_LABELS[kind]} (كما في الملف): {piece}")
                if left or len(nums) > 1:
                    row_info.append(f"الصف {number}: {FIELD_LABELS[kind]} فيه أكثر من رقم أو نص — أُضيف الباقي إلى الملاحظات")
            elif kind == "birth_date":
                if isinstance(val, datetime):
                    d = val.date()
                elif isinstance(val, date):
                    d = val
                else:
                    d = parse_date_text(text, today)
                if isinstance(d, date) and today.year - 30 <= d.year <= today.year:
                    row["birth_date"] = d
                else:
                    conv_notes.append(f"تاريخ الميلاد (كما في الملف): {one_line(text)}")
                    row_info.append(f"الصف {number}: تاريخ الميلاد غير مفهوم — أُضيف إلى الملاحظات")
            elif kind == "gender":
                g = parse_gender(text)
                if g:
                    row["gender"] = g
                else:
                    conv_notes.append(f"النوع (كما في الملف): {one_line(text)}")
            else:                                            # plain text fields
                t = one_line(text)
                limit = FIELD_MAX.get(kind, 255)
                if len(t) > limit:
                    conv_notes.append(f"{FIELD_LABELS[kind]} (النص الكامل): {t}")
                    t = t[:limit]
                row[kind] = t

        if not row["name_ar"]:
            lost = "؛ ".join(one_line(cell_text(c)) for c in raw_row if cell_text(c))[:120]
            result["warnings"].append(f"الصف {number}: تم تخطيه — لا يوجد اسم ({lost})")
            continue
        result["notes"].extend(row_info)
        row["name_en"] = row["name_en"] or row["name_ar"]
        notes = "\n".join(x for x in [own_notes, *extra_lines, *conv_notes] if x)
        row["notes"] = notes[:5000] or None
        result["rows"].append(row)

    if not result["rows"]:
        result["errors"].append("لم يتم العثور على أي صفوف تحتوي على اسم")
        return result
    result["ok"] = True
    return result
