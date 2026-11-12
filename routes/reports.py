"""routes/reports.py — Daily / per-class / per-kid reports + Excel export."""
import io
import re
from flask import (Blueprint, render_template, request, send_file, abort, flash, redirect, url_for)
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload

from extensions import db
from models import Class, Kid, AttendanceRecord, Settings, Visit, Note
from utils.dates import today_local, latest_attendance_date, parse_date
from utils.decorators import can_access_class, can_access_kid, class_ids_for, manager_required
from utils.exports import export_daily_attendance, export_class_summary
from utils.dates import format_date, now_local
from utils.i18n import get_lang, tr
from utils.queries import class_attendance_overview

reports_bp = Blueprint("reports", __name__)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _report_date(raw):
    settings = Settings.get()
    d = parse_date(raw)
    if d and d <= today_local():
        return d
    return latest_attendance_date(settings.attendance_day)


def _safe_filename(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]+", "_", text or "").strip("_") or "class"


@reports_bp.route("/")
@login_required
def hub():
    target_date = _report_date(request.args.get("date"))
    rows = class_attendance_overview(target_date, class_ids_for(current_user))
    present = sum(r["present"] for r in rows)
    absent = sum(r["absent"] for r in rows)
    excused = sum(r["excused"] for r in rows)
    total = sum(r["total"] for r in rows)
    overall = {"present": present, "absent": absent, "excused": excused,
               "total": total,
               "percent": round(present * 100 / total) if total else 0}
    pending = sum(1 for r in rows if not r["taken"])
    return render_template("reports/hub.html", rows=rows, overall=overall,
                           pending=pending, target_date=target_date)


@reports_bp.route("/export/summary")
@manager_required
def export_summary():
    target_date = _report_date(request.args.get("date"))
    rows = class_attendance_overview(target_date, class_ids_for(current_user))
    buf = export_class_summary(rows, target_date)
    return send_file(buf, as_attachment=True,
                     download_name=f"attendance_summary_{target_date}.xlsx",
                     mimetype=XLSX_MIME)


def _daily_rows(klass, target_date):
    kids = (klass.kids.filter_by(is_active=True).order_by(Kid.name_ar).all())
    recs = {r.kid_id: r for r in AttendanceRecord.query.options(
        joinedload(AttendanceRecord.taken_by)).filter(
        AttendanceRecord.date == target_date,
        AttendanceRecord.kid_id.in_([k.id for k in kids] or [0])).all()}
    return [(k, recs.get(k.id)) for k in kids]


@reports_bp.route("/daily/<int:class_id>")
@login_required
def daily(class_id):
    klass = db.get_or_404(Class, class_id)
    if not can_access_class(current_user, klass):
        abort(403)
    target_date = _report_date(request.args.get("date"))
    rows = _daily_rows(klass, target_date)
    stats = {s: sum(1 for _, r in rows if r and r.status == s)
             for s in ("present", "absent", "excused")}
    stats["missing"] = sum(1 for _, r in rows if r is None)
    return render_template("reports/daily.html", klass=klass, rows=rows,
                           stats=stats, target_date=target_date)


@reports_bp.route("/export/daily/<int:class_id>")
@login_required
def export_daily(class_id):
    klass = db.get_or_404(Class, class_id)
    if not can_access_class(current_user, klass):
        abort(403)
    target_date = _report_date(request.args.get("date"))
    buf = export_daily_attendance(klass, target_date, _daily_rows(klass, target_date))
    name = f"attendance_{_safe_filename(klass.name_en)}_{klass.id}_{target_date}.xlsx"
    return send_file(buf, as_attachment=True, download_name=name, mimetype=XLSX_MIME)


@reports_bp.route("/kid/<int:kid_id>")
@login_required
def kid_history(kid_id):
    kid = db.get_or_404(Kid, kid_id)
    if not can_access_kid(current_user, kid):
        abort(403)
    records = (kid.attendance.order_by(AttendanceRecord.date.desc()).limit(60).all())
    stats = {s: sum(1 for r in records if r.status == s)
             for s in ("present", "absent", "excused")}
    stats["total"] = len(records)
    stats["percent"] = round(stats["present"] * 100 / len(records)) if records else 0
    return render_template("reports/kid_history.html", kid=kid,
                           records=records, stats=stats)


# ------------------------------------------------------------------- PDF
def _pdf_tools():
    """Imported on demand so the app still starts if the PDF libraries are not installed yet."""
    try:
        from utils import pdf
        return pdf
    except ImportError:
        return None


def _pdf_missing():
    flash(tr("مكتبات PDF غير مثبّتة. شغّل: pip install -r requirements.txt ثم أعد تشغيل التطبيق.",
             "PDF libraries are not installed. Run: pip install -r requirements.txt and restart the app."), "danger")
    return redirect(url_for("reports.hub"))


def _pdf_response(data: bytes, name: str):
    return send_file(io.BytesIO(data), mimetype="application/pdf", as_attachment=True, download_name=name)


def _brand(lang):
    st = Settings.get()
    return (st.church_name_ar if lang == "ar" else st.church_name_en) or st.church_name_ar


@reports_bp.route("/pdf/daily/<int:class_id>")
@login_required
def pdf_daily(class_id):
    """One class, one day: who came and who didn't — as a PDF."""
    klass = db.get_or_404(Class, class_id)
    if not can_access_class(current_user, klass):
        abort(403)
    pdf = _pdf_tools()
    if pdf is None:
        return _pdf_missing()
    lang = get_lang()
    target_date = _report_date(request.args.get("date"))
    pairs = _daily_rows(klass, target_date)
    rows = [{"name": k.display_name(lang), "status": r.status if r else None,
             "note": r.excuse_note if r else "",
             "by": r.taken_by.display_name(lang) if r and r.taken_by else ""} for k, r in pairs]
    stats = {s_: sum(1 for x in rows if x["status"] == s_) for s_ in ("present", "absent", "excused")}
    data = pdf.daily_pdf(_brand(lang), klass.display_name(lang), klass.grade.display_name(lang),
                     format_date(target_date, lang), rows, stats, lang,
                     current_user.display_name(lang), now_local().strftime("%Y-%m-%d %H:%M"))
    return _pdf_response(data, f"attendance_class{klass.id}_{target_date}.pdf")


@reports_bp.route("/pdf/summary")
@login_required
def pdf_summary():
    """All the classes I may see, one day — as a PDF."""
    pdf = _pdf_tools()
    if pdf is None:
        return _pdf_missing()
    lang = get_lang()
    target_date = _report_date(request.args.get("date"))
    overview = class_attendance_overview(target_date, class_ids_for(current_user))
    rows = [{"grade": r["grade"].display_name(lang), "klass": r["klass"].display_name(lang),
             "present": r["present"], "absent": r["absent"], "excused": r["excused"],
             "total": r["total"], "taken": r["taken"]} for r in overview]
    present = sum(r["present"] for r in overview)
    total = sum(r["total"] for r in overview)
    totals = {"present": present, "absent": sum(r["absent"] for r in overview),
              "excused": sum(r["excused"] for r in overview),
              "percent": round(present * 100 / total) if total else 0}
    data = pdf.summary_pdf(_brand(lang), format_date(target_date, lang), rows, totals, lang,
                       current_user.display_name(lang), now_local().strftime("%Y-%m-%d %H:%M"))
    return _pdf_response(data, f"attendance_summary_{target_date}.pdf")


# ---------------------------------------------- more PDFs: kids, one kid, attendance over time
def _generated(lang):
    return current_user.display_name(lang), now_local().strftime("%Y-%m-%d %H:%M")


@reports_bp.route("/pdf/kids/<int:class_id>")
@login_required
def pdf_kids(class_id):
    """The class list with contact details."""
    klass = db.get_or_404(Class, class_id)
    if not can_access_class(current_user, klass):
        abort(403)
    pdf = _pdf_tools()
    if pdf is None:
        return _pdf_missing()
    lang = get_lang()
    kids = klass.kids.filter_by(is_active=True).order_by(Kid.name_ar).all()
    rows = [{"name": k.display_name(lang), "mother_name": k.mother_name, "father_phone": k.father_phone,
             "mother_phone": k.mother_phone, "school": k.school, "area": k.area, "address": k.address,
             "birth": k.birth_date.isoformat() if k.birth_date else ""} for k in kids]
    by, at = _generated(lang)
    data = pdf.kids_pdf(_brand(lang), klass.display_name(lang), klass.grade.display_name(lang), rows, lang, by, at)
    return _pdf_response(data, f"kids_class{klass.id}_{today_local()}.pdf")


@reports_bp.route("/pdf/period/<int:class_id>")
@login_required
def pdf_period(class_id):
    """Who came on each of the last N meetings (N = 4..12)."""
    klass = db.get_or_404(Class, class_id)
    if not can_access_class(current_user, klass):
        abort(403)
    pdf = _pdf_tools()
    if pdf is None:
        return _pdf_missing()
    lang = get_lang()
    weeks = min(max(request.args.get("weeks", 8, type=int), 4), 12)
    kids = klass.kids.filter_by(is_active=True).order_by(Kid.name_ar).all()
    kid_ids = [k.id for k in kids] or [0]
    found = (db.session.query(AttendanceRecord.date).filter(AttendanceRecord.kid_id.in_(kid_ids),
                                                            AttendanceRecord.date <= today_local())
             .distinct().order_by(AttendanceRecord.date.desc()).limit(weeks).all())
    dates = sorted(d for (d,) in found)
    marks = {}
    for r in AttendanceRecord.query.filter(AttendanceRecord.kid_id.in_(kid_ids), AttendanceRecord.date.in_(dates or [None])):
        marks.setdefault(r.kid_id, {})[r.date] = r.status
    rows = []
    for k in kids:
        m = {d: st for d, st in marks.get(k.id, {}).items() if d in dates}
        rows.append({"name": k.display_name(lang), "marks": m,
                     "present": sum(1 for x in m.values() if x == "present"),
                     "absent": sum(1 for x in m.values() if x == "absent"),
                     "excused": sum(1 for x in m.values() if x == "excused")})
    by, at = _generated(lang)
    data = pdf.class_period_pdf(_brand(lang), klass.display_name(lang), klass.grade.display_name(lang),
                                dates, rows, lang, by, at)
    return _pdf_response(data, f"attendance_period_class{klass.id}_{today_local()}.pdf")


@reports_bp.route("/pdf/kid/<int:kid_id>")
@login_required
def pdf_kid(kid_id):
    """One kid: all his data, attendance, visits (with who visited and the notes) and notes."""
    kid = db.get_or_404(Kid, kid_id)
    if not can_access_kid(current_user, kid):
        abort(403)
    pdf = _pdf_tools()
    if pdf is None:
        return _pdf_missing()
    from utils.photos import photos_dir, is_valid_filename
    import os
    lang = get_lang()
    L = lambda ar, en: ar if lang == "ar" else en
    fields = [(L("اسم الأم", "Mother"), kid.mother_name), (L("هاتف الأب", "Father phone"), kid.father_phone),
              (L("هاتف الأم", "Mother phone"), kid.mother_phone), (L("هاتف الطفل", "Kid phone"), kid.kid_phone),
              (L("المدرسة", "School"), kid.school), (L("أب الاعتراف", "Father of confession"), kid.confession_father),
              (L("الإخوة والأخوات", "Brothers & sisters"), kid.siblings),
              (L("تاريخ الميلاد", "Birth date"), kid.birth_date.isoformat() if kid.birth_date else ""),
              (L("المنطقة", "Area"), kid.area), (L("العنوان", "Address"), kid.address),
              (L("ملاحظات عامة", "General notes"), kid.general_notes)]
    records = kid.attendance.order_by(AttendanceRecord.date.desc()).all()
    stats = {s_: sum(1 for r in records if r.status == s_) for s_ in ("present", "absent", "excused")}
    stats["percent"] = round(stats["present"] * 100 / len(records)) if records else 0
    att = [{"date": r.date.isoformat(), "status": r.status, "note": r.excuse_note} for r in records[:40]]
    types = {"home": L("زيارة منزلية", "Home visit"), "phone": L("اتصال هاتفي", "Phone call"),
             "church": L("في الكنيسة", "At church")}
    visits = [{"date": v.visit_date.isoformat(), "type": types.get(v.visit_type, v.visit_type or ""),
               "by": v.servant.display_name(lang) if v.servant else "", "notes": v.notes}
              for v in kid.visits.options(joinedload(Visit.servant)).order_by(Visit.visit_date.desc(), Visit.id.desc()).limit(200)]
    cats = {"general": L("عام", "General"), "behavior": L("سلوك", "Behavior"), "health": L("صحة", "Health"),
            "spiritual": L("روحي", "Spiritual")}
    notes = [{"date": n.created_at.strftime("%Y-%m-%d"), "by": n.author.display_name(lang) if n.author else "",
              "cat": cats.get(n.category, n.category or ""), "text": n.content}
             for n in kid.notes.options(joinedload(Note.author)).order_by(Note.created_at.desc()).limit(200)]
    photo = os.path.join(photos_dir(), kid.photo_filename) if is_valid_filename(kid.photo_filename) else None
    klass_name = (f"{kid.klass.grade.display_name(lang)} — {kid.klass.display_name(lang)}" if kid.klass else "")
    by, at = _generated(lang)
    data = pdf.kid_profile_pdf(_brand(lang), {"name": kid.display_name(lang), "klass": klass_name, "fields": fields},
                               att, visits, notes, stats, photo, lang, by, at)
    return _pdf_response(data, f"kid_{kid.id}_{today_local()}.pdf")
