"""routes/visitation.py — افتقاد: who needs a visit, what was done, who was never visited."""
from datetime import timedelta

from flask import (Blueprint, render_template, request, redirect, url_for, flash, abort, current_app)
from flask_login import login_required, current_user
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from extensions import db
from models import Kid, Visit, Class, Grade, Settings
from routes.reports import _brand, _pdf_missing, _pdf_response, _pdf_tools
from utils.dates import today_local, parse_date, format_date, now_local
from utils.decorators import can_access_kid, class_ids_for
from utils.i18n import tr, get_lang
from utils.queries import visit_candidates, get_all_areas
from utils.security import log_action
from utils.validators import clean

visitation_bp = Blueprint("visitation", __name__)

PERIODS = {"30": 30, "90": 90, "365": 365, "all": None}
PER_PAGE = 40


def _type_labels():
    return {"home": tr("زيارة منزلية", "Home visit"), "phone": tr("اتصال هاتفي", "Phone call"),
            "church": tr("في الكنيسة", "At church")}


# ------------------------------------------------------------------ scope
def _scoped_classes():
    """The classes this person may look at (servant: his own, admin: his grades, leader: all)."""
    q = Class.query.join(Grade).options(joinedload(Class.grade)).filter(Class.is_active.is_(True))
    ids = class_ids_for(current_user)
    if ids is not None:
        q = q.filter(Class.id.in_(ids or [0]))
    return q.order_by(Grade.sort_order, Grade.id, Class.name_ar).all()


def _filters():
    """(classes, selected_class_id|None, class_ids_for_queries|None)"""
    classes_ = _scoped_classes()
    wanted = request.args.get("class_id", type=int)
    selected = wanted if wanted and any(c.id == wanted for c in classes_) else None
    ids = [selected] if selected else class_ids_for(current_user)
    return classes_, selected, ids


def _attach_last_visit(rows):
    """Add 'last_by' (who made the latest visit) to each row — one query for all kids."""
    kid_ids = [r["kid"].id for r in rows]
    if not kid_ids:
        return rows
    seen = {}
    for v in (Visit.query.options(joinedload(Visit.servant)).filter(Visit.kid_id.in_(kid_ids))
              .order_by(Visit.kid_id, Visit.visit_date.desc(), Visit.id.desc())):
        seen.setdefault(v.kid_id, v)
    for r in rows:
        v = seen.get(r["kid"].id)
        r["last_by"] = v.servant if v else None
        r["last_note"] = v.notes if v else None
    return rows


# -------------------------------------------------------------- who needs a visit
@visitation_bp.route("/")
@login_required
def plan():
    classes_, selected, ids = _filters()
    areas = get_all_areas(ids)
    area = request.args.get("area") or ""
    if area and area not in areas:        # never filter by an area I can't see
        area = ""
    rows = _attach_last_visit(visit_candidates(
        days=current_app.config["VISIT_OVERDUE_DAYS"], area=area or None, class_ids=ids,
        threshold=Settings.get().absence_threshold, today=today_local()))
    return render_template("visitation/plan.html", areas=areas, selected_area=area, rows=rows,
                           classes=classes_, selected_class=selected, today=today_local(),
                           days=current_app.config["VISIT_OVERDUE_DAYS"],
                           alerts=sum(1 for r in rows if r["alert"]))


@visitation_bp.route("/log/<int:kid_id>", methods=["POST"])
@login_required
def log_visit(kid_id):
    kid = db.get_or_404(Kid, kid_id)
    if not can_access_kid(current_user, kid):
        abort(403)
    back = redirect(url_for("visitation.plan", area=request.form.get("return_area") or None,
                            class_id=request.form.get("return_class") or None))
    visit_type = request.form.get("visit_type")
    if visit_type not in Visit.VISIT_TYPES:
        visit_type = "home"
    when = today_local()
    raw = (request.form.get("visit_date") or "").strip()
    if raw:
        parsed = parse_date(raw)
        if not parsed or parsed > today_local() or parsed < today_local() - timedelta(days=730):
            flash(tr("تاريخ الافتقاد غير صالح (لا يمكن أن يكون في المستقبل)",
                     "Invalid visit date (it can't be in the future)"), "danger")
            return back
        when = parsed
    notes = clean(request.form.get("notes"), 2000) or None
    db.session.add(Visit(kid_id=kid.id, servant_id=current_user.id, visit_date=when,
                         visit_type=visit_type, notes=notes))
    log_action("visit_logged", user_id=current_user.id, target=f"Kid:{kid.id}")
    db.session.commit()
    flash(tr("تم تسجيل الافتقاد", "Visit recorded"), "success")
    return back


@visitation_bp.route("/visit/<int:visit_id>/delete", methods=["POST"])
@login_required
def delete_visit(visit_id):
    """Remove a visit entered by mistake: its author, or an admin who can see that kid."""
    visit = db.get_or_404(Visit, visit_id)
    if not can_access_kid(current_user, visit.kid):
        abort(403)
    if visit.servant_id != current_user.id and not current_user.is_manager:
        abort(403)
    log_action("visit_deleted", user_id=current_user.id, target=f"Kid:{visit.kid_id}")
    db.session.delete(visit)
    db.session.commit()
    flash(tr("تم حذف سجل الافتقاد", "Visit entry deleted"), "info")
    return redirect(request.form.get("back") if (request.form.get("back") or "").startswith("/visitation/")
                    else url_for("visitation.history"))


# ----------------------------------------------------------------------- history
def _history_args():
    classes_, selected, ids = _filters()
    period = request.args.get("period") if request.args.get("period") in PERIODS else "90"
    by = "me" if request.args.get("by") == "me" else "all"
    return classes_, selected, ids, period, by


def _visit_query(ids, period, by):
    q = (Visit.query.join(Kid, Kid.id == Visit.kid_id)
         .options(joinedload(Visit.kid).joinedload(Kid.klass), joinedload(Visit.servant)))
    if ids is not None:
        q = q.filter(Kid.class_id.in_(ids or [0]))
    days = PERIODS[period]
    if days:
        q = q.filter(Visit.visit_date >= today_local() - timedelta(days=days))
    if by == "me":
        q = q.filter(Visit.servant_id == current_user.id)
    return q


def _not_visited_query(ids):
    q = (Kid.query.options(joinedload(Kid.klass)).filter(Kid.is_active.is_(True), ~Kid.visits.any()))
    if ids is not None:
        q = q.filter(Kid.class_id.in_(ids or [0]))
    return q.order_by(Kid.class_id, Kid.name_ar)


@visitation_bp.route("/history")
@login_required
def history():
    classes_, selected, ids, period, by = _history_args()
    page = max(request.args.get("page", 1, type=int), 1)
    q = _visit_query(ids, period, by)
    pagination = q.order_by(Visit.visit_date.desc(), Visit.id.desc()).paginate(
        page=page, per_page=PER_PAGE, error_out=False)
    visited_kids = q.with_entities(func.count(func.distinct(Visit.kid_id))).scalar() or 0
    not_visited = _not_visited_query(ids).all()
    return render_template(
        "visitation/history.html", classes=classes_, selected_class=selected, period=period, by=by,
        pagination=pagination, visits=pagination.items, visits_total=pagination.total,
        visited_kids=visited_kids, not_visited=not_visited, types=_type_labels(),
        filter_args={"class_id": selected or None, "period": period, "by": by})


@visitation_bp.route("/history/pdf")
@login_required
def history_pdf():
    """The visitation trace as a PDF: every visit with its full notes + the kids never visited."""
    pdf = _pdf_tools()
    if pdf is None:
        return _pdf_missing()
    classes_, selected, ids, period, by = _history_args()
    lang = get_lang()
    visits = (_visit_query(ids, period, by).order_by(Visit.visit_date.desc(), Visit.id.desc())
              .limit(1500).all())
    not_visited = _not_visited_query(ids).all()
    types = _type_labels()
    rows = [{"date": v.visit_date.isoformat(), "kid": v.kid.display_name(lang),
             "klass": v.kid.klass.display_name(lang) if v.kid.klass else "—",
             "type": types.get(v.visit_type, v.visit_type or ""),
             "by": v.servant.display_name(lang) if v.servant else "", "notes": v.notes or ""} for v in visits]
    nv = [{"kid": k.display_name(lang), "klass": k.klass.display_name(lang) if k.klass else "—",
           "phone": k.primary_phone, "area": k.area} for k in not_visited]
    class_label = (next(c for c in classes_ if c.id == selected).display_name(lang) if selected
                   else tr("كل الفصول", "All classes"))
    period_label = {"30": tr("آخر 30 يوماً", "Last 30 days"), "90": tr("آخر 90 يوماً", "Last 90 days"),
                    "365": tr("آخر سنة", "Last year"), "all": tr("كل الفترات", "All time")}[period]
    by_label = tr("زياراتي فقط", "My visits only") if by == "me" else tr("كل الخدام", "All servants")
    chips = (len(rows), len({v.kid_id for v in visits}), len(nv))
    data = pdf.visits_pdf(_brand(lang), class_label, period_label, by_label, chips, rows, nv, lang,
                          current_user.display_name(lang), now_local().strftime("%Y-%m-%d %H:%M"))
    return _pdf_response(data, f"visitation_report_{today_local()}.pdf")


# -------------------------------------------------------------------------- print
@visitation_bp.route("/print")
@login_required
def print_list():
    ids = class_ids_for(current_user)
    area = request.args.get("area") or ""
    if area and area not in get_all_areas(ids):
        abort(404)
    rows = visit_candidates(days=current_app.config["VISIT_OVERDUE_DAYS"], area=area or None,
                            class_ids=ids, threshold=Settings.get().absence_threshold, today=today_local())
    return render_template("visitation/print_list.html", area=area, rows=rows, today=today_local())
