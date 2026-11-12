"""utils/queries.py — Reusable, set-based database queries (no per-row loops)."""
from datetime import date, timedelta
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from extensions import db
from models import Kid, Class, Grade, User, AttendanceRecord, Visit


# ---------------------------------------------------------------- counts
def count_grades(grade_ids=None):
    q = Grade.query.filter_by(is_active=True)
    if grade_ids is not None:
        q = q.filter(Grade.id.in_(grade_ids))
    return q.count()


def count_classes(grade_ids=None):
    q = Class.query.filter_by(is_active=True)
    if grade_ids is not None:
        q = q.filter(Class.grade_id.in_(grade_ids))
    return q.count()


def count_servants(grade_ids=None):
    """Servants; for a grade admin only those who serve in his/her grades."""
    q = User.query.filter_by(role="servant", is_active=True, approval_status="approved")
    if grade_ids is not None:
        q = q.filter(User.classes.any(Class.grade_id.in_(grade_ids)))
    return q.count()


def count_kids(class_ids=None):
    q = Kid.query.filter_by(is_active=True)
    if class_ids is not None:
        if not class_ids:
            return 0
        q = q.filter(Kid.class_id.in_(class_ids))
    return q.count()


# ------------------------------------------------------- absence streaks
def absence_streaks(class_ids=None, today: date | None = None,
                    lookback_weeks: int = 26) -> dict[int, int]:
    """
    {kid_id: consecutive 'absent' records, counted back from the latest record}.
    'excused' or 'present' breaks the streak. One query for all kids.
    """
    if class_ids is not None and not class_ids:
        return {}
    since = (today or date.today()) - timedelta(weeks=lookback_weeks)
    q = (db.session.query(AttendanceRecord.kid_id, AttendanceRecord.status)
         .join(Kid, Kid.id == AttendanceRecord.kid_id)
         .filter(Kid.is_active.is_(True), AttendanceRecord.date >= since)
         .order_by(AttendanceRecord.kid_id, AttendanceRecord.date.desc()))
    if class_ids is not None:
        q = q.filter(Kid.class_id.in_(class_ids))
    streaks: dict[int, int] = {}
    done: set[int] = set()
    for kid_id, status in q:
        if kid_id in done:
            continue
        if status == "absent":
            streaks[kid_id] = streaks.get(kid_id, 0) + 1
        else:
            done.add(kid_id)
    return streaks


def alert_thresholds(default: int = 3) -> dict[int, int]:
    """{class_id: weeks} — each grade's admin may set his own number; empty = the global default."""
    rows = (db.session.query(Class.id, Grade.absence_weeks)
            .join(Grade, Grade.id == Class.grade_id).all())
    return {cid: (weeks or default) for cid, weeks in rows}


def kids_with_absence_alert(threshold: int = 3, class_ids=None):
    """[(kid, streak)] for kids absent N+ times in a row, N = their grade's setting (or the default)."""
    tmap = alert_thresholds(threshold)
    streaks = absence_streaks(class_ids)
    if not streaks:
        return []
    kids = (Kid.query.options(joinedload(Kid.klass))
            .filter(Kid.id.in_(streaks.keys())).all())
    rows = [(k, streaks[k.id]) for k in kids
            if streaks[k.id] >= tmap.get(k.class_id, threshold)]
    rows.sort(key=lambda r: (-r[1], r[0].name_ar))
    return rows


# ---------------------------------------------------- class attendance state
def class_attendance_overview(target_date: date, class_ids=None):
    """One row per active class that has active kids, with counts for the date."""
    if class_ids is not None and not class_ids:
        return []
    cq = (Class.query.join(Grade).options(joinedload(Class.grade))
          .filter(Class.is_active.is_(True))
          .order_by(Grade.sort_order, Grade.id, Class.name_ar))
    if class_ids is not None:
        cq = cq.filter(Class.id.in_(class_ids))
    classes = cq.all()

    totals = dict(db.session.query(Kid.class_id, func.count(Kid.id))
                  .filter(Kid.is_active.is_(True)).group_by(Kid.class_id).all())
    counts: dict[int, dict[str, int]] = {}
    for cid, status, n in (db.session.query(
            Kid.class_id, AttendanceRecord.status, func.count(AttendanceRecord.id))
            .join(Kid, Kid.id == AttendanceRecord.kid_id)
            .filter(AttendanceRecord.date == target_date, Kid.is_active.is_(True))
            .group_by(Kid.class_id, AttendanceRecord.status).all()):
        counts.setdefault(cid, {})[status] = n

    rows = []
    for c in classes:
        total = totals.get(c.id, 0)
        if total == 0:
            continue
        c_counts = counts.get(c.id, {})
        recorded = sum(c_counts.values())
        rows.append({
            "klass": c, "grade": c.grade, "total": total,
            "present": c_counts.get("present", 0),
            "absent": c_counts.get("absent", 0),
            "excused": c_counts.get("excused", 0),
            "recorded": recorded,
            "taken": recorded > 0,
            "complete": recorded >= total,
        })
    return rows


def weekly_trend(n: int = 8, class_ids=None):
    """Attendance % for the last n dates that have records (oldest → newest)."""
    if class_ids is not None and not class_ids:
        return []
    q = (db.session.query(AttendanceRecord.date, AttendanceRecord.status,
                          func.count(AttendanceRecord.id))
         .join(Kid, Kid.id == AttendanceRecord.kid_id)
         .group_by(AttendanceRecord.date, AttendanceRecord.status))
    if class_ids is not None:
        q = q.filter(Kid.class_id.in_(class_ids))
    by_date: dict[date, dict[str, int]] = {}
    for d, status, cnt in q:
        by_date.setdefault(d, {})[status] = cnt
    out = []
    for d in sorted(by_date)[-n:]:
        s = by_date[d]
        total = sum(s.values())
        present = s.get("present", 0)
        out.append({"date": d, "present": present, "total": total,
                    "pct": round(present * 100 / total) if total else 0})
    return out


# ------------------------------------------------------------- visitation
VISIT_ALERT_GRACE_DAYS = 7


def visit_candidates(days: int = 30, area: str | None = None, class_ids=None,
                     threshold: int = 3, today: date | None = None):
    """
    Kids who need a visit, most urgent first:
      1. absent `threshold`+ times in a row and not visited in the last week
      2. not visited in `days` days (or never)
    """
    if class_ids is not None and not class_ids:
        return []
    today = today or date.today()
    cutoff = today - timedelta(days=days)
    last_visit = (db.session.query(Visit.kid_id,
                                   func.max(Visit.visit_date).label("last"))
                  .group_by(Visit.kid_id).subquery())
    q = (db.session.query(Kid, last_visit.c.last)
         .outerjoin(last_visit, last_visit.c.kid_id == Kid.id)
         .options(joinedload(Kid.klass))
         .filter(Kid.is_active.is_(True)))
    if area:
        q = q.filter(Kid.area == area)
    if class_ids is not None:
        q = q.filter(Kid.class_id.in_(class_ids))
    streaks = absence_streaks(class_ids, today)
    tmap = alert_thresholds(threshold)

    rows = []
    for kid, last in q:
        streak = streaks.get(kid.id, 0)
        recently = last is not None and (today - last).days <= VISIT_ALERT_GRACE_DAYS
        alert = streak >= tmap.get(kid.class_id, threshold) and not recently
        overdue = last is None or last < cutoff
        if not (alert or overdue):
            continue
        rows.append({"kid": kid, "last_visit": last, "streak": streak,
                     "alert": alert, "never": last is None})
    rows.sort(key=lambda r: (not r["alert"], -r["streak"],
                             r["last_visit"] or date.min, r["kid"].name_ar))
    return rows


def get_all_areas(class_ids=None):
    if class_ids is not None and not class_ids:
        return []
    q = (db.session.query(Kid.area)
         .filter(Kid.is_active.is_(True), Kid.area.isnot(None), Kid.area != ""))
    if class_ids is not None:
        q = q.filter(Kid.class_id.in_(class_ids))
    return [r[0] for r in q.distinct().order_by(Kid.area).all()]
