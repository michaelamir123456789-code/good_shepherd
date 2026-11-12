"""routes/profiles.py — Class roster, kid profile, notes."""
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, abort, send_from_directory)
from flask_login import login_required, current_user

from extensions import db
from models import Kid, Class, Note, AttendanceRecord, Visit, Settings
from utils.decorators import can_access_class, can_access_kid
from utils.i18n import tr
from utils.queries import absence_streaks, alert_thresholds
from utils.security import log_action
from utils.validators import clean
from utils.photos import save_photo, delete_photo, photos_dir, is_valid_filename

profiles_bp = Blueprint("profiles", __name__)


@profiles_bp.route("/class/<int:class_id>")
@login_required
def roster(class_id):
    klass = db.get_or_404(Class, class_id)
    if not can_access_class(current_user, klass):
        abort(403)
    kids = klass.kids.filter_by(is_active=True).order_by(Kid.name_ar).all()
    streaks = absence_streaks([klass.id])
    threshold = alert_thresholds(Settings.get().absence_threshold).get(klass.id, 3)
    return render_template("profiles/roster.html", klass=klass, kids=kids,
                           streaks=streaks, threshold=threshold)


@profiles_bp.route("/<int:kid_id>")
@login_required
def view(kid_id):
    kid = db.get_or_404(Kid, kid_id)
    if not can_access_kid(current_user, kid):
        abort(403)
    notes = kid.notes.order_by(Note.created_at.desc()).limit(100).all()
    attendance = (kid.attendance.order_by(AttendanceRecord.date.desc())
                  .limit(12).all())
    visits = kid.visits.order_by(Visit.visit_date.desc()).limit(20).all()
    streak = absence_streaks([kid.class_id] if kid.class_id else []).get(kid.id, 0)
    default = Settings.get().absence_threshold
    return render_template("profiles/kid.html", kid=kid, notes=notes,
                           attendance=attendance, visits=visits, streak=streak,
                           threshold=alert_thresholds(default).get(kid.class_id, default))


@profiles_bp.route("/<int:kid_id>/note", methods=["POST"])
@login_required
def add_note(kid_id):
    kid = db.get_or_404(Kid, kid_id)
    if not can_access_kid(current_user, kid):
        abort(403)
    content = clean(request.form.get("content"), 2000)
    category = request.form.get("category")
    if category not in Note.NOTE_CATEGORIES:
        category = "general"
    if not content:
        flash(tr("الملاحظة فارغة", "The note is empty"), "danger")
        return redirect(url_for("profiles.view", kid_id=kid.id))
    db.session.add(Note(kid_id=kid.id, author_id=current_user.id,
                        content=content, category=category))
    log_action("note_added", user_id=current_user.id, target=f"Kid:{kid.id}")
    db.session.commit()
    flash(tr("تمت إضافة الملاحظة", "Note added"), "success")
    return redirect(url_for("profiles.view", kid_id=kid.id))


@profiles_bp.route("/<int:kid_id>/note/<int:note_id>/delete", methods=["POST"])
@login_required
def delete_note(kid_id, note_id):
    kid = db.get_or_404(Kid, kid_id)
    if not can_access_kid(current_user, kid):
        abort(403)
    note = db.get_or_404(Note, note_id)
    if note.kid_id != kid.id:
        abort(404)
    if not current_user.is_manager and note.author_id != current_user.id:
        abort(403)                               # only my own notes
    db.session.delete(note)
    log_action("note_deleted", user_id=current_user.id, target=f"Kid:{kid.id}")
    db.session.commit()
    flash(tr("تم حذف الملاحظة", "Note deleted"), "info")
    return redirect(url_for("profiles.view", kid_id=kid.id))


# ------------------------------------------------------------------ photos
@profiles_bp.route("/<int:kid_id>/photo")
@login_required
def photo(kid_id):
    """Serve a kid's photo — only to people who may see this kid."""
    kid = db.get_or_404(Kid, kid_id)
    if not can_access_kid(current_user, kid):
        abort(403)
    if not is_valid_filename(kid.photo_filename):
        abort(404)
    resp = send_from_directory(photos_dir(), kid.photo_filename,
                               mimetype="image/jpeg", max_age=600)
    resp.headers["Cache-Control"] = "private, max-age=600"
    return resp


@profiles_bp.route("/<int:kid_id>/photo/update", methods=["POST"])
@login_required
def update_photo(kid_id):
    kid = db.get_or_404(Kid, kid_id)
    if not can_access_kid(current_user, kid):
        abort(403)
    back = redirect(url_for("profiles.view", kid_id=kid.id))

    if request.form.get("action") == "remove":
        old, kid.photo_filename = kid.photo_filename, None
        log_action("kid_photo_removed", user_id=current_user.id, target=f"Kid:{kid.id}")
        db.session.commit()
        delete_photo(old)
        flash(tr("تم حذف الصورة", "Photo removed"), "info")
        return back

    file = request.files.get("photo")
    if not file or not file.filename:
        flash(tr("اختر صورة أولاً", "Choose a photo first"), "danger")
        return back
    try:
        new_name = save_photo(file)
    except ValueError as e:
        flash(str(e), "danger")
        return back
    old, kid.photo_filename = kid.photo_filename, new_name
    log_action("kid_photo_set", user_id=current_user.id, target=f"Kid:{kid.id}")
    db.session.commit()
    delete_photo(old)
    flash(tr("تم حفظ الصورة", "Photo saved"), "success")
    return back
