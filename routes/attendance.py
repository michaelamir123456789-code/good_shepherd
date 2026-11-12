"""routes/attendance.py — Taking attendance with a one-servant-at-a-time lock."""
from datetime import datetime
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify, abort, current_app)
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload

from extensions import db
from models import Kid, Class, AttendanceRecord, AttendanceSession, Settings
from utils.dates import today_local, latest_attendance_date, parse_date
from utils.decorators import can_access_class, manager_required
from utils.i18n import tr
from utils.locks import acquire_lock, release_lock, lock_status
from utils.security import log_action
from utils.validators import clean

attendance_bp = Blueprint("attendance", __name__)

VALID_STATUS = ("present", "absent", "excused")


def _get_class_or_403(class_id: int) -> Class:
    klass = db.get_or_404(Class, class_id)
    if not can_access_class(current_user, klass):
        abort(403)
    return klass


def _resolve_date(raw: str | None):
    """
    Attendance is for a day that has already started — never the future.
    Servants may correct only the last N days; the leader may fix any past day.
    """
    settings = Settings.get()
    default = latest_attendance_date(settings.attendance_day)
    d = parse_date(raw) or default
    today = today_local()
    if d > today:
        return default
    window = current_app.config["ATTENDANCE_EDIT_WINDOW_DAYS"]
    if not current_user.is_manager and (today - d).days > window:
        return default
    return d


def _active_kids(klass):
    return (klass.kids.filter_by(is_active=True).order_by(Kid.name_ar).all())


def _marks_from_db(kids, target_date):
    ids = [k.id for k in kids]
    if not ids:
        return {}
    recs = AttendanceRecord.query.filter(
        AttendanceRecord.date == target_date,
        AttendanceRecord.kid_id.in_(ids)).all()
    return {r.kid_id: {"status": r.status, "note": r.excuse_note or ""}
            for r in recs}


@attendance_bp.route("/take/<int:class_id>", methods=["GET", "POST"])
@login_required
def take(class_id):
    klass = _get_class_or_403(class_id)
    target_date = _resolve_date(request.values.get("date"))
    kids = _active_kids(klass)

    # ---------------------------------------------------------- save
    if request.method == "POST":
        state = lock_status(class_id, target_date, current_user.id)
        if state["locked"] and not state["is_me"]:
            flash(tr("خادم آخر يسجّل الحضور الآن. لم يتم الحفظ.",
                     "Another servant is taking attendance. Nothing was saved."),
                  "warning")
            return redirect(url_for("attendance.take", class_id=class_id,
                                    date=target_date.isoformat()))

        posted, missing = {}, []
        for kid in kids:
            status = request.form.get(f"status_{kid.id}")
            note = clean(request.form.get(f"excuse_{kid.id}"), 255)
            if status in VALID_STATUS:
                posted[kid.id] = {"status": status, "note": note}
            else:
                missing.append(kid)

        if missing:
            flash(tr(f"لم يتم تحديد حالة {len(missing)} طفل. اختر حاضر أو غائب أو معذور لكل طفل.",
                     f"{len(missing)} kid(s) not marked yet. Mark every kid before saving."),
                  "danger")
            # keep the lock and everything the servant already entered
            acquire_lock(class_id, target_date, current_user.id)
            return render_template("attendance/take.html", klass=klass, kids=kids,
                                   target_date=target_date, marks=posted,
                                   missing_ids={k.id for k in missing},
                                   lock_minutes=current_app.config["ATTENDANCE_LOCK_MINUTES"])

        existing = {r.kid_id: r for r in AttendanceRecord.query.filter(
            AttendanceRecord.date == target_date,
            AttendanceRecord.kid_id.in_([k.id for k in kids])).all()}
        now = datetime.utcnow()
        for kid in kids:
            m = posted[kid.id]
            rec = existing.get(kid.id)
            if rec:
                rec.status, rec.excuse_note = m["status"], m["note"] or None
                rec.taken_by_id, rec.taken_at = current_user.id, now
            else:
                db.session.add(AttendanceRecord(
                    kid_id=kid.id, date=target_date, status=m["status"],
                    excuse_note=m["note"] or None, taken_by_id=current_user.id))
        log_action("attendance_saved", user_id=current_user.id,
                   target=f"Class:{class_id} {target_date}")
        db.session.commit()
        release_lock(class_id, target_date, current_user.id)
        flash(tr("تم حفظ الحضور بنجاح", "Attendance saved"), "success")
        return redirect(url_for("attendance.summary", class_id=class_id,
                                date=target_date.isoformat()))

    # ------------------------------------------------------------ open
    ok, holder = acquire_lock(class_id, target_date, current_user.id)
    if not ok:
        return render_template("attendance/locked.html", klass=klass,
                               target_date=target_date, locked_by=holder,
                               lock_minutes=current_app.config["ATTENDANCE_LOCK_MINUTES"])
    return render_template("attendance/take.html", klass=klass, kids=kids,
                           target_date=target_date,
                           marks=_marks_from_db(kids, target_date),
                           missing_ids=set(),
                           lock_minutes=current_app.config["ATTENDANCE_LOCK_MINUTES"])


@attendance_bp.route("/heartbeat/<int:class_id>", methods=["POST"])
@login_required
def heartbeat(class_id):
    """Called every ~30 s by the open form: keeps MY lock alive, reports theft."""
    _get_class_or_403(class_id)
    target_date = _resolve_date(request.values.get("date"))
    ok, holder = acquire_lock(class_id, target_date, current_user.id)
    if ok:
        return jsonify({"ok": True})
    return jsonify({"ok": False,
                    "by": holder.display_name(request.args.get("lang", "ar"))
                    if holder else None})


@attendance_bp.route("/release/<int:class_id>", methods=["POST"])
@login_required
def release(class_id):
    _get_class_or_403(class_id)
    target_date = _resolve_date(request.form.get("date"))
    release_lock(class_id, target_date, current_user.id)
    flash(tr("تم تحرير الجلسة", "Session released"), "info")
    return redirect(url_for("dashboard.home"))


@attendance_bp.route("/summary/<int:class_id>")
@login_required
def summary(class_id):
    klass = _get_class_or_403(class_id)
    target_date = _resolve_date(request.args.get("date"))
    kids = _active_kids(klass)
    recs = {r.kid_id: r for r in AttendanceRecord.query.options(
        joinedload(AttendanceRecord.taken_by)).filter(
        AttendanceRecord.date == target_date,
        AttendanceRecord.kid_id.in_([k.id for k in kids] or [0])).all()}
    stats = {s: sum(1 for r in recs.values() if r.status == s) for s in VALID_STATUS}
    stats["total"] = len(kids)
    return render_template("attendance/summary.html", klass=klass, kids=kids,
                           record_map=recs, stats=stats, target_date=target_date)


@attendance_bp.route("/force/<int:class_id>", methods=["POST"])
@manager_required
def force_takeover(class_id):
    klass = _get_class_or_403(class_id)
    target_date = _resolve_date(request.form.get("date"))
    session = AttendanceSession.query.filter_by(
        class_id=class_id, date=target_date).first()
    if not session:
        session = AttendanceSession(class_id=class_id, date=target_date)
        db.session.add(session)
    session.lock(current_user.id)
    log_action("attendance_force_takeover", user_id=current_user.id,
               target=f"Class:{class_id} {target_date}")
    db.session.commit()
    flash(tr("تم الاستيلاء على الجلسة", "Session taken over"), "info")
    return redirect(url_for("attendance.take", class_id=klass.id,
                            date=target_date.isoformat()))
