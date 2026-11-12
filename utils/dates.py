"""utils/dates.py — Timezone-aware 'today', attendance-day logic, Arabic dates."""
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("Africa/Cairo")
except Exception:  # tzdata missing — fall back to a fixed UTC+2
    _TZ = timezone(timedelta(hours=2))

WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}
WEEKDAY_LABELS = {
    "ar": {"monday": "الاثنين", "tuesday": "الثلاثاء", "wednesday": "الأربعاء",
           "thursday": "الخميس", "friday": "الجمعة", "saturday": "السبت",
           "sunday": "الأحد"},
    "en": {k: k.capitalize() for k in WEEKDAYS},
}
_AR_DAYS = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
_AR_MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو",
              "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
_EN_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
            "Saturday", "Sunday"]
_EN_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December"]


def now_local() -> datetime:
    return datetime.now(_TZ)


def today_local() -> date:
    return now_local().date()


def latest_attendance_date(day_name: str = "friday",
                           today: date | None = None) -> date:
    """Most recent attendance day on or before today (never in the future)."""
    today = today or today_local()
    target = WEEKDAYS.get((day_name or "friday").lower(), 4)
    return today - timedelta(days=(today.weekday() - target) % 7)


def parse_date(value: str | None) -> date | None:
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def format_date(d, lang: str = "ar", weekday: bool = True,
                year: bool = True) -> str:
    """'الجمعة ٣ نوفمبر ٢٠٢٦' style (Western digits for consistency)."""
    if not d:
        return "—"
    if isinstance(d, datetime):
        d = d.date()
    days, months = (_AR_DAYS, _AR_MONTHS) if lang == "ar" else (_EN_DAYS, _EN_MONTHS)
    parts = []
    if weekday:
        parts.append(days[d.weekday()] + ("،" if lang == "ar" else ","))
    parts.append(f"{d.day} {months[d.month - 1]}")
    if year:
        parts.append(str(d.year))
    return " ".join(parts)


def format_short(d, lang: str = "ar") -> str:
    if not d:
        return "—"
    months = _AR_MONTHS if lang == "ar" else _EN_MONTHS
    return f"{d.day} {months[d.month - 1]}"


def utc_to_local(dt: datetime | None) -> datetime | None:
    if not dt:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone(_TZ)
