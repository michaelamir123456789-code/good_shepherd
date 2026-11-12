"""routes/dashboard.py — Role-based home screens."""
from flask import Blueprint, render_template, redirect, url_for
from flask_login import login_required, current_user

from models import Settings
from utils.dates import today_local, latest_attendance_date
from utils.decorators import manager_required, class_ids_for, grade_scope
from utils.queries import (
    count_grades, count_classes, count_servants, count_kids,
    kids_with_absence_alert, class_attendance_overview, weekly_trend,
    visit_candidates,
)

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
@login_required
def home():
    if current_user.is_manager:
        return redirect(url_for("dashboard.leader"))
    return redirect(url_for("dashboard.servant"))


@dashboard_bp.route("/leader")
@manager_required
def leader():
    """Leader sees everything; a grade admin sees the same screen for HIS grades only."""
    settings = Settings.get()
    today = today_local()
    att_date = latest_attendance_date(settings.attendance_day, today)
    class_ids = class_ids_for(current_user)          # None = all
    grade_ids = grade_scope(current_user)            # None = all
    overview = class_attendance_overview(att_date, class_ids)
    pending = [r for r in overview if not r["taken"]]
    alerts = kids_with_absence_alert(settings.absence_threshold, class_ids)
    present = sum(r["present"] for r in overview)
    total = sum(r["total"] for r in overview)
    stats = {
        "grades": count_grades(grade_ids), "classes": count_classes(grade_ids),
        "servants": count_servants(grade_ids), "kids": count_kids(class_ids),
        "present": present, "total": total,
        "percent": round(present * 100 / total) if total else 0,
    }
    mine_ids = [c.id for c in current_user.classes if c.is_active]          # an admin is a servant too
    my_overview = class_attendance_overview(att_date, mine_ids) if mine_ids else []
    visits_due = len(visit_candidates(30, class_ids=class_ids,
                                      threshold=settings.absence_threshold, today=today))
    return render_template(
        "dashboard/leader.html", stats=stats, today=today, att_date=att_date,
        overview=overview, pending=pending, alerts=alerts[:8],
        alerts_total=len(alerts), trend=weekly_trend(8, class_ids),
        threshold=settings.absence_threshold, visits_due=visits_due, my_overview=my_overview,
    )


@dashboard_bp.route("/servant")
@login_required
def servant():
    if current_user.is_manager:
        return redirect(url_for("dashboard.leader"))
    settings = Settings.get()
    today = today_local()
    att_date = latest_attendance_date(settings.attendance_day, today)
    class_ids = [c.id for c in current_user.classes if c.is_active]
    overview = class_attendance_overview(att_date, class_ids)
    alerts = kids_with_absence_alert(settings.absence_threshold, class_ids)
    return render_template(
        "dashboard/servant.html", today=today, att_date=att_date,
        overview=overview, alerts=alerts[:6], alerts_total=len(alerts),
        threshold=settings.absence_threshold,
        has_classes=bool(class_ids),
    )
