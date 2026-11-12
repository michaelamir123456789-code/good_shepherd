"""routes/admin.py — Leader-only management: grades, classes, servants, kids, import, settings, log."""
import io
import re
import logging
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, session, send_file, current_app, abort)
from flask_login import current_user, login_required
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from extensions import db
from models import Grade, Class, Kid, User, Settings, AuditLog, ROLE_ADMIN, ROLE_GRADE_ADMIN
from utils.decorators import (admin_required, manager_required, class_ids_for, grade_scope,
                              can_access_kid)
from utils.dates import WEEKDAYS, parse_date
from utils.i18n import tr
from utils.validators import (validate_password, validate_email_format,
                              validate_phone, validate_name, clean)
from utils.security import log_action
from utils.excel_import import parse_file
from utils.identity import identity_from_form, normalize_phone, validate_phone_number
from utils.signup import pending_query_for, count_pending_for
from utils.photos import save_photo, delete_photo
from utils import import_store

admin_bp = Blueprint("admin", __name__)
log = logging.getLogger(__name__)


def _flash_errors(errors):
    for e in errors:
        flash(e, "danger")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def _int(value, default=0, lo=-10**6, hi=10**6):
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


# ---- who may touch what -------------------------------------------------
# Leader: everything. Grade admin: only inside the grades assigned to him/her.
def _scope():
    """None = no restriction (leader); otherwise the list of my grade ids."""
    return grade_scope(current_user)


def _grade_ok(grade_id) -> bool:
    sc = _scope()
    return sc is None or grade_id in sc


def _require(ok: bool):
    if not ok:
        abort(403)


def _servant_visible(servant) -> bool:
    """A grade admin can see servants who serve in at least one of his/her grades."""
    sc = _scope()
    return sc is None or any(c.grade_id in sc for c in servant.classes)


def _servant_fully_mine(servant) -> bool:
    """True if ALL of the servant's classes are inside my grades (so I may disable/reset them)."""
    sc = _scope()
    return sc is None or (bool(servant.classes) and all(c.grade_id in sc for c in servant.classes))


def _class_choices():
    """The classes this person may work with (servant: his own, admin: his grades, leader: all)."""
    q = (Class.query.join(Grade).options(joinedload(Class.grade))
         .filter(Class.is_active.is_(True)))
    ids = class_ids_for(current_user)
    if ids is not None:
        q = q.filter(Class.id.in_(ids or [0]))
    return q.order_by(Grade.sort_order, Grade.id, Class.name_ar).all()


# ==========================================================================
# GRADES
# ==========================================================================
@admin_bp.route("/grades")
@manager_required
def grades():
    q = Grade.query
    if _scope() is not None:
        q = q.filter(Grade.id.in_(_scope()))
    items = q.order_by(Grade.sort_order, Grade.id).all()
    counts = dict(db.session.query(Class.grade_id, func.count(Class.id))
                  .group_by(Class.grade_id).all())
    return render_template("admin/grades.html", grades=items, counts=counts)


def _grade_data(form):
    name_ar = clean(form.get("name_ar"), 120)
    name_en = clean(form.get("name_en"), 120) or name_ar
    errors = (validate_name(name_ar, tr("اسم المرحلة", "Grade name"))
              + validate_name(name_en, tr("الاسم بالإنجليزية", "English name")))
    return {"name_ar": name_ar, "name_en": name_en,
            "sort_order": _int(form.get("sort_order"), 0, 0, 999)}, errors


@admin_bp.route("/grades/new", methods=["GET", "POST"])
@admin_required
def grade_new():
    if request.method == "POST":
        data, errors = _grade_data(request.form)
        if errors:
            _flash_errors(errors)
            return render_template("admin/grade_form.html", mode="new",
                                   form=request.form)
        grade = Grade(**data)
        db.session.add(grade)
        db.session.flush()
        log_action("grade_created", user_id=current_user.id, target=f"Grade:{grade.id}")
        db.session.commit()
        flash(tr("تمت إضافة المرحلة. الآن اختر مسؤول المرحلة.", "Grade added. Now choose its admin."), "success")
        return redirect(url_for("admin.grade_admins", grade_id=grade.id))
    return render_template("admin/grade_form.html", mode="new", form={})


@admin_bp.route("/grades/<int:grade_id>/edit", methods=["GET", "POST"])
@admin_required
def grade_edit(grade_id):
    grade = db.get_or_404(Grade, grade_id)
    if request.method == "POST":
        data, errors = _grade_data(request.form)
        if errors:
            _flash_errors(errors)
            return render_template("admin/grade_form.html", mode="edit",
                                   grade=grade, form=request.form)
        for k, v in data.items():
            setattr(grade, k, v)
        log_action("grade_updated", user_id=current_user.id, target=f"Grade:{grade.id}")
        db.session.commit()
        flash(tr("تم تحديث المرحلة", "Grade updated"), "success")
        return redirect(url_for("admin.grades"))
    return render_template("admin/grade_form.html", mode="edit", grade=grade, form={})


@admin_bp.route("/grades/<int:grade_id>/delete", methods=["POST"])
@admin_required
def grade_delete(grade_id):
    grade = db.get_or_404(Grade, grade_id)
    if grade.classes.count() > 0:
        flash(tr("لا يمكن حذف مرحلة بها فصول. احذف الفصول أولاً.",
                 "You can't delete a grade that has classes. Delete its classes first."),
              "danger")
        return redirect(url_for("admin.grades"))
    db.session.delete(grade)
    log_action("grade_deleted", user_id=current_user.id, target=f"Grade:{grade_id}")
    db.session.commit()
    flash(tr("تم حذف المرحلة", "Grade deleted"), "info")
    return redirect(url_for("admin.grades"))


# ---- who is admin of a grade -------------------------------------------------
def _serves_in(user, grade_id) -> bool:
    return any(c.grade_id == grade_id for c in user.classes)


def _admin_candidates(grade):
    """
    People the current user may make admin of `grade`.
      leader      -> any active, approved servant / grade admin (those serving in the grade come first)
      grade admin -> only people who SERVE in this grade
    """
    people = (User.query.options(joinedload(User.classes), joinedload(User.managed_grades))
              .filter(User.role.in_(["servant", ROLE_GRADE_ADMIN]), User.approval_status == "approved",
                      User.is_active.is_(True)).order_by(User.name_ar).all())
    already = {a.id for a in grade.admins}
    people = [u for u in people if u.id not in already]
    if not current_user.is_admin:
        people = [u for u in people if _serves_in(u, grade.id)]
    return people


@admin_bp.route("/grades/<int:grade_id>/admins")
@manager_required
def grade_admins(grade_id):
    grade = db.get_or_404(Grade, grade_id)
    _require(_grade_ok(grade.id))
    cands = _admin_candidates(grade)
    in_grade = [u for u in cands if _serves_in(u, grade.id)]
    others = [u for u in cands if not _serves_in(u, grade.id)][:500]
    admins_ = sorted(grade.admins, key=lambda a: (not a.is_active, a.name_ar))
    return render_template("admin/grade_admins.html", grade=grade, admins=admins_, in_grade=in_grade, others=others,
                           my_grade_ids=set(_scope() or []))


@admin_bp.route("/grades/<int:grade_id>/admins/add", methods=["POST"])
@manager_required
def grade_admin_add(grade_id):
    """Make someone an admin of this grade (he may already be an admin of other grades — they are kept)."""
    grade = db.get_or_404(Grade, grade_id)
    _require(_grade_ok(grade.id))
    target = next((u for u in _admin_candidates(grade) if u.id == request.form.get("user_id", type=int)), None)
    if not target:
        flash(tr("اختر خادماً من القائمة", "Choose someone from the list"), "danger")
        return redirect(url_for("admin.grade_admins", grade_id=grade.id))
    if target.role == "servant":
        target.role = ROLE_GRADE_ADMIN                       # promoted from servant; never to leader
    target.managed_grades.append(grade)
    log_action("grade_admin_added", user_id=current_user.id, target=f"User:{target.id} Grade:{grade.id}")
    db.session.commit()
    flash(tr(f"أصبح {target.name_ar} مسؤولاً عن {grade.name_ar}",
             f"{target.name_en or target.name_ar} is now an admin of {grade.name_en or grade.name_ar}"), "success")
    return redirect(url_for("admin.grade_admins", grade_id=grade.id))


@admin_bp.route("/grades/<int:grade_id>/admins/<int:user_id>/remove", methods=["POST"])
@manager_required
def grade_admin_remove(grade_id, user_id):
    """Remove him from THIS grade only. No grade left => back to a normal servant (classes kept)."""
    grade = db.get_or_404(Grade, grade_id)
    _require(_grade_ok(grade.id))
    target = db.get_or_404(User, user_id)
    if target.role != ROLE_GRADE_ADMIN or grade not in target.managed_grades:
        abort(404)
    if target.id == current_user.id:
        flash(tr("لا يمكنك إزالة نفسك", "You can't remove yourself"), "danger")
        return redirect(url_for("admin.grade_admins", grade_id=grade.id))
    target.managed_grades.remove(grade)
    if not target.managed_grades:
        target.role = "servant"
    log_action("grade_admin_removed", user_id=current_user.id, target=f"User:{target.id} Grade:{grade.id}")
    db.session.commit()
    flash(tr("تمت الإزالة من المرحلة", "Removed from the grade"), "info")
    return redirect(url_for("admin.grade_admins", grade_id=grade.id))


@admin_bp.route("/grades/<int:grade_id>/threshold", methods=["POST"])
@manager_required
def grade_threshold(grade_id):
    """How many consecutive absences trigger the alert for this grade (empty = global default)."""
    grade = db.get_or_404(Grade, grade_id)
    _require(_grade_ok(grade.id))
    weeks = _int(request.form.get("absence_weeks"), 0, 0, 20)
    grade.absence_weeks = weeks or None
    log_action("grade_threshold", user_id=current_user.id, target=f"Grade:{grade.id}={weeks}")
    db.session.commit()
    flash(tr("تم حفظ عدد أسابيع التنبيه", "Alert weeks saved"), "success")
    return redirect(url_for("admin.grades"))


# ==========================================================================
# CLASSES
# ==========================================================================
@admin_bp.route("/grades/<int:grade_id>/classes")
@manager_required
def classes(grade_id):
    grade = db.get_or_404(Grade, grade_id)
    _require(_grade_ok(grade.id))
    items = grade.classes.order_by(Class.name_ar).all()
    kid_counts = dict(db.session.query(Kid.class_id, func.count(Kid.id))
                      .filter(Kid.is_active.is_(True)).group_by(Kid.class_id).all())
    return render_template("admin/classes.html", grade=grade, classes=items,
                           kid_counts=kid_counts)


def _class_data(form):
    name_ar = clean(form.get("name_ar"), 120)
    name_en = clean(form.get("name_en"), 120) or name_ar
    errors = (validate_name(name_ar, tr("اسم الفصل", "Class name"))
              + validate_name(name_en, tr("الاسم بالإنجليزية", "English name")))
    return {"name_ar": name_ar, "name_en": name_en,
            "age_group": clean(form.get("age_group"), 50) or None}, errors


@admin_bp.route("/grades/<int:grade_id>/classes/new", methods=["GET", "POST"])
@manager_required
def class_new(grade_id):
    grade = db.get_or_404(Grade, grade_id)
    _require(_grade_ok(grade.id))
    if request.method == "POST":
        data, errors = _class_data(request.form)
        if errors:
            _flash_errors(errors)
            return render_template("admin/class_form.html", mode="new",
                                   grade=grade, form=request.form)
        klass = Class(grade_id=grade.id, **data)
        db.session.add(klass)
        db.session.flush()
        log_action("class_created", user_id=current_user.id, target=f"Class:{klass.id}")
        db.session.commit()
        flash(tr("تمت إضافة الفصل", "Class added"), "success")
        return redirect(url_for("admin.classes", grade_id=grade.id))
    return render_template("admin/class_form.html", mode="new", grade=grade, form={})


@admin_bp.route("/classes/<int:class_id>/edit", methods=["GET", "POST"])
@manager_required
def class_edit(class_id):
    klass = db.get_or_404(Class, class_id)
    _require(_grade_ok(klass.grade_id))
    if request.method == "POST":
        data, errors = _class_data(request.form)
        if errors:
            _flash_errors(errors)
            return render_template("admin/class_form.html", mode="edit",
                                   grade=klass.grade, klass=klass, form=request.form)
        for k, v in data.items():
            setattr(klass, k, v)
        log_action("class_updated", user_id=current_user.id, target=f"Class:{klass.id}")
        db.session.commit()
        flash(tr("تم تحديث الفصل", "Class updated"), "success")
        return redirect(url_for("admin.classes", grade_id=klass.grade_id))
    return render_template("admin/class_form.html", mode="edit",
                           grade=klass.grade, klass=klass, form={})


@admin_bp.route("/classes/<int:class_id>/delete", methods=["POST"])
@manager_required
def class_delete(class_id):
    klass = db.get_or_404(Class, class_id)
    _require(_grade_ok(klass.grade_id))
    grade_id = klass.grade_id
    if klass.kids.count() > 0:
        flash(tr("لا يمكن حذف فصل به أطفال. انقل الأطفال لفصل آخر أولاً.",
                 "You can't delete a class that still has kids. Move them first."),
              "danger")
        return redirect(url_for("admin.classes", grade_id=grade_id))
    db.session.delete(klass)
    log_action("class_deleted", user_id=current_user.id, target=f"Class:{class_id}")
    db.session.commit()
    flash(tr("تم حذف الفصل", "Class deleted"), "info")
    return redirect(url_for("admin.classes", grade_id=grade_id))


# ==========================================================================
# SERVANTS
# ==========================================================================
@admin_bp.route("/servants")
@manager_required
def servants():
    q = (User.query.options(joinedload(User.classes), joinedload(User.managed_grades))
         .filter(User.role.in_(["servant", ROLE_GRADE_ADMIN]), User.approval_status == "approved"))
    if _scope() is not None:
        q = q.filter(User.classes.any(Class.grade_id.in_(_scope())))
    items = q.order_by(User.is_active.desc(), User.name_ar).all()
    return render_template("admin/servants.html", servants=items,
                           fully_mine={s.id: _servant_fully_mine(s) for s in items},
                           my_grade_ids=set(_scope() or []))


def _servant_data(form, existing=None):
    data, errors = identity_from_form(form, existing)
    class_ids = {_int(c, 0) for c in request.form.getlist("class_ids")}
    classes_ = Class.query.filter(Class.id.in_(class_ids), Class.is_active.is_(True)).all() if class_ids else []
    if _scope() is not None:                       # grade admin: ignore classes outside my grades
        classes_ = [c for c in classes_ if c.grade_id in _scope()]
    return data, classes_, errors


@admin_bp.route("/servants/new", methods=["GET", "POST"])
@manager_required
def servant_new():
    if request.method == "POST":
        data, classes_, errors = _servant_data(request.form)
        password = request.form.get("password") or ""
        errors += validate_password(password, name=data["name_ar"], email=data["email"])
        if _scope() is not None and not classes_:   # a grade admin's servant must belong to his grade
            errors.append(tr("اختر فصلاً واحداً على الأقل من مراحلك",
                             "Choose at least one class from your grades"))
        if errors:
            _flash_errors(errors)
            return render_template("admin/servant_form.html", mode="new",
                                   form=request.form, all_classes=_class_choices(),
                                   selected=[c.id for c in classes_])
        servant = User(role="servant", language="ar", must_change_password=True, **data)
        servant.set_password(password)
        servant.classes = classes_
        db.session.add(servant)
        db.session.flush()
        log_action("servant_created", user_id=current_user.id, target=f"User:{servant.id}")
        db.session.commit()
        flash(tr("تمت إضافة الخادم. سيُطلب منه تغيير كلمة المرور عند أول دخول.",
                 "Servant added. They must change the password on first sign-in."),
              "success")
        return redirect(url_for("admin.servants"))
    return render_template("admin/servant_form.html", mode="new", form={},
                           all_classes=_class_choices(), selected=[])


@admin_bp.route("/servants/<int:servant_id>/edit", methods=["GET", "POST"])
@manager_required
def servant_edit(servant_id):
    servant = db.get_or_404(User, servant_id)
    if servant.role not in ("servant", ROLE_GRADE_ADMIN):
        flash(tr("لا يمكن تعديل هذا المستخدم", "This user can't be edited"), "danger")
        return redirect(url_for("admin.servants"))
    _require(_servant_visible(servant))
    # I may only change his CLASSES (nothing else) when he also serves outside my grades,
    # or when he is an admin and I am not the leader (a grade admin can't take over another admin's account).
    limited = (not _servant_fully_mine(servant)) or (servant.is_grade_admin and not current_user.is_admin)

    if request.method == "POST":
        data, classes_, errors = _servant_data(request.form, servant)
        new_password = request.form.get("new_password") or ""
        if limited:
            data, new_password, errors = {}, "", []
        elif new_password:
            errors += validate_password(new_password, name=data["name_ar"], email=data["email"])
        if errors:
            _flash_errors(errors)
            return render_template("admin/servant_form.html", mode="edit", servant=servant,
                                   form=request.form, all_classes=_class_choices(),
                                   selected=[c.id for c in classes_], limited=limited)
        for k, v in data.items():
            setattr(servant, k, v)
        sc = _scope()
        if sc is None:
            servant.classes = classes_
        else:                                       # keep classes outside my grades untouched
            servant.classes = [c for c in servant.classes if c.grade_id not in sc] + classes_
        if not limited:
            was_active = servant.is_active
            servant.is_active = bool(request.form.get("is_active"))
            if new_password:
                servant.set_password(new_password)      # also signs them out everywhere
                servant.must_change_password = True
                servant.failed_login_count, servant.locked_until = 0, None
                log_action("servant_password_reset", user_id=current_user.id,
                           target=f"User:{servant.id}")
            if was_active and not servant.is_active:
                log_action("servant_deactivated", user_id=current_user.id, target=f"User:{servant.id}")
        log_action("servant_updated", user_id=current_user.id, target=f"User:{servant.id}")
        db.session.commit()
        flash(tr("تم تحديث بيانات الخادم", "Servant updated"), "success")
        return redirect(url_for("admin.servants"))
    sc = _scope()
    return render_template("admin/servant_form.html", mode="edit", servant=servant,
                           form={}, all_classes=_class_choices(), limited=limited,
                           selected=[c.id for c in servant.classes if sc is None or c.grade_id in sc])


@admin_bp.route("/servants/<int:servant_id>/toggle", methods=["POST"])
@manager_required
def servant_toggle(servant_id):
    servant = db.get_or_404(User, servant_id)
    if servant.role != "servant":
        flash(tr("لا يمكن تغيير هذا المستخدم", "This user can't be changed"), "danger")
        return redirect(url_for("admin.servants"))
    _require(_servant_fully_mine(servant))
    servant.is_active = not servant.is_active
    log_action("servant_activated" if servant.is_active else "servant_deactivated",
               user_id=current_user.id, target=f"User:{servant.id}")
    db.session.commit()
    flash(tr("تم تفعيل الحساب", "Account activated") if servant.is_active
          else tr("تم تعطيل الحساب", "Account deactivated"), "info")
    return redirect(url_for("admin.servants"))


@admin_bp.route("/servants/<int:servant_id>/unlock", methods=["POST"])
@manager_required
def servant_unlock(servant_id):
    servant = db.get_or_404(User, servant_id)
    if servant.role != "servant":
        return redirect(url_for("admin.servants"))
    _require(_servant_visible(servant))
    servant.failed_login_count, servant.locked_until = 0, None
    log_action("servant_unlocked", user_id=current_user.id, target=f"User:{servant.id}")
    db.session.commit()
    flash(tr("تم فتح الحساب", "Account unlocked"), "success")
    return redirect(url_for("admin.servants"))


@admin_bp.route("/servants/<int:servant_id>/delete", methods=["POST"])
@manager_required
def servant_delete(servant_id):
    """Remove a servant for good (kids, attendance and notes stay; their name on them is cleared)."""
    servant = db.get_or_404(User, servant_id)
    if servant.role != "servant" or servant.is_pending or servant.id == current_user.id:
        abort(404)
    _require(_servant_fully_mine(servant))
    log_action("servant_deleted", user_id=current_user.id, target=f"@{servant.username or servant.phone or servant.id}")
    db.session.delete(servant)
    db.session.commit()
    flash(tr("تم حذف الخادم", "Servant deleted"), "info")
    return redirect(url_for("admin.servants"))


@admin_bp.route("/servants/<int:servant_id>/promote", methods=["GET", "POST"])
@manager_required
def servant_promote(servant_id):
    """Make a servant an admin — but only over grades the promoter himself manages."""
    servant = db.get_or_404(User, servant_id)
    if servant.role not in ("servant", ROLE_GRADE_ADMIN) or servant.is_pending or not servant.is_active:
        abort(404)
    _require(_servant_visible(servant))
    sc = _scope()
    grades_ = [g for g in _all_grades() if sc is None or g.id in sc]
    suggested = sorted({c.grade_id for c in servant.classes if sc is None or c.grade_id in sc})
    if request.method == "POST":
        picked = {_int(g, 0) for g in request.form.getlist("grade_ids")}
        chosen = [g for g in grades_ if g.id in picked]
        if not chosen:
            flash(tr("اختر مرحلة واحدة على الأقل", "Choose at least one grade"), "danger")
            return render_template("admin/servant_promote.html", servant=servant, grades=grades_,
                                   selected=sorted(picked))
        servant.role = ROLE_GRADE_ADMIN                              # never ROLE_ADMIN: only the leader's own account is that
        servant.managed_grades = list({g.id: g for g in (*servant.managed_grades, *chosen)}.values())   # ADD, never drop
        log_action("servant_promoted", user_id=current_user.id,
                   target=f"User:{servant.id} grades:{','.join(str(g.id) for g in chosen)}")
        db.session.commit()
        flash(tr(f"أصبح {servant.name_ar} مسؤولاً عن المراحل المختارة",
                 f"{servant.name_en or servant.name_ar} is now an admin of the chosen grade(s)"), "success")
        return redirect(url_for("admin.admins" if current_user.is_admin else "admin.team"))
    return render_template("admin/servant_promote.html", servant=servant, grades=grades_, selected=suggested)


# ==========================================================================
# KIDS — a servant manages his own class, an admin his grade, the leader all
# ==========================================================================
PER_PAGE = 40
UNSPECIFIED_CLASS = ("غير محدد", "Unspecified")      # placeholder class made by the OLD importer


def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _my_class_ids():
    """None = every class (leader); else the classes I may work with."""
    return class_ids_for(current_user)


def _class_ok(class_id) -> bool:
    ids = _my_class_ids()
    return ids is None or class_id in ids


@admin_bp.route("/kids")
@login_required
def kids():
    search = clean(request.args.get("q"), 80)
    class_id = request.args.get("class_id", type=int)
    status = request.args.get("status", "active")
    if status == "archived" and not current_user.is_manager:
        status = "active"
    page = max(request.args.get("page", 1, type=int), 1)

    query = Kid.query.options(joinedload(Kid.klass)).filter(
        Kid.is_active.is_(status != "archived"))
    mine = _my_class_ids()
    if mine is not None:
        query = query.filter(Kid.class_id.in_(mine))
        if class_id == 0:
            class_id = None
    if search:
        like = _like(search)
        query = query.filter(
            Kid.name_ar.ilike(like, escape="\\") | Kid.name_en.ilike(like, escape="\\")
            | Kid.area.ilike(like, escape="\\") | Kid.father_phone.ilike(like, escape="\\")
            | Kid.mother_phone.ilike(like, escape="\\") | Kid.kid_phone.ilike(like, escape="\\")
            | Kid.mother_name.ilike(like, escape="\\") | Kid.school.ilike(like, escape="\\"))
    if class_id == 0:                       # "no class yet" (leader only)
        query = query.filter(Kid.class_id.is_(None))
    elif class_id:
        query = query.filter(Kid.class_id == class_id)
    pagination = query.order_by(Kid.name_ar).paginate(page=page, per_page=PER_PAGE, error_out=False)
    return render_template("admin/kids.html", pagination=pagination, kids=pagination.items,
                           all_classes=_class_choices(), search=search,
                           selected_class=class_id, status=status)


def _kid_data(form, existing=None):
    name_ar = clean(form.get("name_ar"), 120)
    name_en = clean(form.get("name_en"), 120) or name_ar
    errors = (validate_name(name_ar, tr("الاسم", "Name"))
              + validate_name(name_en, tr("الاسم بالإنجليزية", "English name")))
    phones = {}
    for key in ("kid_phone", "father_phone", "mother_phone"):
        raw = clean(form.get(key), 40)
        phones[key] = normalize_phone(raw) if raw else None
        if raw and not phones[key]:
            errors.append(tr("رقم الهاتف غير صالح", "Invalid phone number"))
        elif phones[key]:
            errors += validate_phone_number(phones[key])

    class_id = form.get("class_id", type=int)
    klass_obj = db.session.get(Class, class_id) if class_id else None
    if class_id and not klass_obj:
        errors.append(tr("الفصل غير موجود", "Class not found"))
    elif class_id and not _class_ok(class_id):
        errors.append(tr("اختر فصلاً من فصولك", "Choose one of your classes"))
    if not class_id and not current_user.is_admin:
        errors.append(tr("اختر الفصل", "Choose the class"))

    birth_raw = (form.get("birth_date") or "").strip()
    birth_date = parse_date(birth_raw) if birth_raw else None
    if birth_raw and not birth_date:
        errors.append(tr("تاريخ الميلاد غير صالح", "Invalid birth date"))
    gender = form.get("gender")
    return {
        "name_ar": name_ar, "name_en": name_en, "birth_date": birth_date,
        "gender": gender if gender in ("male", "female") else None,
        "mother_name": clean(form.get("mother_name"), 120) or None,
        "kid_phone": phones["kid_phone"], "father_phone": phones["father_phone"],
        "mother_phone": phones["mother_phone"],
        "school": clean(form.get("school"), 120) or None,
        "siblings": clean(form.get("siblings"), 500) or None,
        "confession_father": clean(form.get("confession_father"), 120) or None,
        "area": clean(form.get("area"), 80) or None,
        "address": clean(form.get("address"), 255) or None,
        "general_notes": clean(form.get("general_notes"), 5000) or None,
        "class_id": class_id or None,
    }, errors


def _read_photo_upload():
    """(filename, error). No file chosen => (None, None)."""
    file = request.files.get("photo")
    if not file or not file.filename:
        return None, None
    try:
        return save_photo(file), None
    except ValueError as e:
        return None, str(e)


@admin_bp.route("/kids/new", methods=["GET", "POST"])
@login_required
def kid_new():
    mine = _my_class_ids()
    if mine is not None and not mine:
        flash(tr("لم يُعيَّن لك فصل بعد", "You have no class yet"), "warning")
        return redirect(url_for("dashboard.home"))
    if request.method == "POST":
        data, errors = _kid_data(request.form)
        new_photo = None
        if not errors:
            new_photo, photo_error = _read_photo_upload()
            if photo_error:
                errors.append(photo_error)
        if errors:
            _flash_errors(errors)
            return render_template("admin/kid_form.html", mode="new", form=request.form,
                                   all_classes=_class_choices())
        kid = Kid(photo_filename=new_photo, **data)
        db.session.add(kid)
        db.session.flush()
        log_action("kid_created", user_id=current_user.id, target=f"Kid:{kid.id}")
        db.session.commit()
        flash(tr("تمت إضافة الطفل", "Kid added"), "success")
        if kid.class_id and not current_user.is_manager:
            return redirect(url_for("profiles.roster", class_id=kid.class_id))
        return redirect(url_for("admin.kids", class_id=kid.class_id or None))
    pre = request.args.get("class_id", type=int)
    pre = pre if pre and _class_ok(pre) else ""
    if not pre and mine and len(mine) == 1:
        pre = mine[0]
    return render_template("admin/kid_form.html", mode="new", form={"class_id": pre},
                           all_classes=_class_choices())


@admin_bp.route("/kids/<int:kid_id>/edit", methods=["GET", "POST"])
@login_required
def kid_edit(kid_id):
    kid = db.get_or_404(Kid, kid_id)
    _require(can_access_kid(current_user, kid))
    if not kid.is_active and not current_user.is_manager:
        abort(404)
    if request.method == "POST":
        data, errors = _kid_data(request.form, kid)
        new_photo = None
        if not errors:
            new_photo, photo_error = _read_photo_upload()
            if photo_error:
                errors.append(photo_error)
        if errors:
            _flash_errors(errors)
            return render_template("admin/kid_form.html", mode="edit", kid=kid,
                                   form=request.form, all_classes=_class_choices())
        for k, v in data.items():
            setattr(kid, k, v)
        old_photo = kid.photo_filename
        if new_photo:
            kid.photo_filename = new_photo
        elif request.form.get("remove_photo"):
            kid.photo_filename = None
        log_action("kid_updated", user_id=current_user.id, target=f"Kid:{kid.id}")
        db.session.commit()
        if old_photo and old_photo != kid.photo_filename:
            delete_photo(old_photo)
        flash(tr("تم تحديث بيانات الطفل", "Kid updated"), "success")
        return redirect(url_for("profiles.view", kid_id=kid.id))
    return render_template("admin/kid_form.html", mode="edit", kid=kid, form={},
                           all_classes=_class_choices())


@admin_bp.route("/kids/<int:kid_id>/archive", methods=["POST"])
@login_required
def kid_archive(kid_id):
    """'Delete' a kid: hidden everywhere, but an admin can still restore him/her."""
    kid = db.get_or_404(Kid, kid_id)
    _require(can_access_kid(current_user, kid))
    back_class = kid.class_id
    kid.is_active = False
    log_action("kid_archived", user_id=current_user.id, target=f"Kid:{kid_id}")
    db.session.commit()
    flash(tr("تم حذف الطفل (يستطيع المسؤول استرجاعه من الأرشيف)",
             "Kid deleted (an admin can restore him/her from the archive)"), "info")
    if back_class and not current_user.is_manager:
        return redirect(url_for("profiles.roster", class_id=back_class))
    return redirect(url_for("admin.kids"))


@admin_bp.route("/kids/<int:kid_id>/restore", methods=["POST"])
@manager_required
def kid_restore(kid_id):
    kid = db.get_or_404(Kid, kid_id)
    _require(can_access_kid(current_user, kid))
    kid.is_active = True
    log_action("kid_restored", user_id=current_user.id, target=f"Kid:{kid_id}")
    db.session.commit()
    flash(tr("تم استرجاع الطفل", "Kid restored"), "success")
    return redirect(url_for("admin.kids", status="archived"))


# ==========================================================================
# KIDS — IMPORT INTO ONE CLASS (choose class + file -> preview -> confirm)
# ==========================================================================
MERGE_FIELDS = ("mother_name", "kid_phone", "father_phone", "mother_phone", "school", "siblings",
                "confession_father", "address", "area", "birth_date", "gender")
PHONE_FIELDS = ("father_phone", "mother_phone", "kid_phone")


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _phones_of(obj) -> set:
    get = obj.get if isinstance(obj, dict) else (lambda k: getattr(obj, k))
    return {get(f) for f in PHONE_FIELDS if not _blank(get(f))}


def _same_person(kid, row) -> int:
    """2 = shares a phone number, 1 = nothing contradicts, 0 = different people."""
    a, b = _phones_of(kid), _phones_of(row)
    if a & b:
        return 2
    if a and b:
        return 0
    return 1


def _merge_into(kid, row) -> bool:
    """Fill ONLY the empty fields of an existing kid. Never overwrites anything."""
    changed = False
    for f in MERGE_FIELDS:
        if _blank(getattr(kid, f)) and not _blank(row.get(f)):
            setattr(kid, f, row[f])
            changed = True
    note = (row.get("notes") or "").strip()
    if note and note not in (kid.general_notes or ""):
        kid.general_notes = ((kid.general_notes or "").rstrip() + "\n" + note).strip()
        changed = True
    return changed


def _apply_import(rows, klass):
    """
    Put every row into `klass`.
      * a kid already in the class with the same name  -> missing data is FILLED IN (re-importing
        a better sheet repairs a bad earlier import; nothing is ever overwritten)
      * a kid with the same name and NO class (left over from an old import) -> moved into the class
      * otherwise -> a new kid
    Used for the preview too (then rolled back), so the preview can never differ from the result.
    """
    ph = (Class.query.filter(Class.grade_id == klass.grade_id,
                             Class.name_ar == UNSPECIFIED_CLASS[0], Class.id != klass.id).all())
    pool = (Kid.query.filter(Kid.is_active.is_(True),
                             (Kid.class_id == klass.id) | Kid.class_id.is_(None)
                             | Kid.class_id.in_([c.id for c in ph] or [0])).all())
    by_name = {}
    for k in pool:
        by_name.setdefault(_norm(k.name_ar), []).append(k)

    out = {"created": 0, "updated": 0, "unchanged": 0, "adopted": 0}
    for r in rows:
        key = _norm(r["name_ar"])
        cands = [(k, _same_person(k, r)) for k in by_name.get(key, [])]
        cands = [(k, s) for k, s in cands if s > 0]
        cands.sort(key=lambda t: (-t[1], t[0].class_id != klass.id))
        if cands:
            kid = cands[0][0]
            adopted = kid.class_id != klass.id
            if adopted:
                kid.class_id = klass.id
                out["adopted"] += 1
            changed = _merge_into(kid, r)
            if not adopted:
                out["updated" if changed else "unchanged"] += 1
            continue
        kid = Kid(class_id=klass.id, is_active=True,
                  **{k: r.get(k) for k in ("name_ar", "name_en", "birth_date", "gender", "mother_name",
                                           "kid_phone", "father_phone", "mother_phone", "school", "siblings",
                                           "confession_father", "address", "area")},
                  general_notes=r.get("notes"))
        db.session.add(kid)
        by_name.setdefault(key, []).append(kid)
        out["created"] += 1
    return out


def _preview_import(rows, klass):
    """Run the real logic, read the numbers, then undo everything."""
    try:
        return _apply_import(rows, klass)
    finally:
        db.session.rollback()


def _import_class_or_none(class_id):
    klass = db.session.get(Class, class_id) if class_id else None
    return klass if klass and klass.is_active and _class_ok(klass.id) else None


@admin_bp.route("/kids/import", methods=["GET", "POST"])
@login_required
def kids_import():
    classes_ = _class_choices()
    if not classes_:
        flash(tr("لا توجد فصول متاحة لك للاستيراد", "You have no class to import into"), "warning")
        return redirect(url_for("dashboard.home"))

    def page(**kw):
        kw.setdefault("selected", None)
        return render_template("admin/kids_import.html", classes=classes_, parsed=None, **kw)

    if request.method == "POST":
        action = request.form.get("action", "upload")

        if action == "upload":
            klass = _import_class_or_none(request.form.get("class_id", type=int))
            file = request.files.get("file")
            if not klass:
                flash(tr("اختر الفصل أولاً", "Choose the class first"), "danger")
                return page()
            if not file or not file.filename:
                flash(tr("اختر ملفاً أولاً", "Choose a file first"), "danger")
                return page(selected=klass.id)
            data = file.read(current_app.config["IMPORT_MAX_BYTES"] + 1)
            if len(data) > current_app.config["IMPORT_MAX_BYTES"]:
                flash(tr("حجم الملف يتجاوز 5 ميجا", "File exceeds 5 MB"), "danger")
                return page(selected=klass.id)
            parsed = parse_file(file.filename, data, current_app.config["IMPORT_MAX_ROWS"])
            if not parsed["ok"]:
                _flash_errors(parsed["errors"])
                return page(selected=klass.id)
            import_store.discard(session.pop("import_token", None))
            session["import_token"] = import_store.save(
                current_user.id, file.filename[:120], parsed["rows"], klass.id)
            plan = _preview_import(parsed["rows"], klass)
            return render_template("admin/kids_import.html", classes=classes_, parsed=parsed,
                                   klass=db.session.get(Class, klass.id), selected=klass.id,
                                   filename=file.filename[:120], plan=plan, preview=parsed["rows"][:8])

        if action == "confirm":
            token = session.get("import_token")
            loaded = import_store.load(token, current_user.id)
            klass = _import_class_or_none(loaded[1]) if loaded else None
            if not loaded or not klass:
                flash(tr("انتهت صلاحية الملف المرفوع. ارفعه مرة أخرى.",
                         "The uploaded file expired. Please upload it again."), "danger")
                return redirect(url_for("admin.kids_import"))
            try:
                done = _apply_import(loaded[0], klass)
                log_action("kids_bulk_import", user_id=current_user.id,
                           target=f"Class:{klass.id} +{done['created']} ~{done['updated']} >{done['adopted']}")
                db.session.commit()
            except Exception:
                db.session.rollback()
                log.exception("Kid import failed")
                flash(tr("حدث خطأ أثناء الاستيراد ولم يتم حفظ أي شيء.",
                         "The import failed and nothing was saved."), "danger")
                return redirect(url_for("admin.kids_import", class_id=klass.id))
            import_store.discard(token)
            session.pop("import_token", None)
            msg = tr(f"تم الاستيراد إلى {klass.name_ar}: {done['created']} طفل جديد",
                     f"Imported into {klass.name_en or klass.name_ar}: {done['created']} new kid(s)")
            if done["adopted"]:
                msg += tr(f"، {done['adopted']} نُقلوا إلى الفصل", f", {done['adopted']} moved into the class")
            if done["updated"]:
                msg += tr(f"، {done['updated']} استُكملت بياناتهم", f", {done['updated']} had missing data filled in")
            flash(msg, "success")
            return redirect(url_for("profiles.roster", class_id=klass.id))

        if action == "cancel":
            import_store.discard(session.pop("import_token", None))
            return redirect(url_for("admin.kids_import"))

    pre = request.args.get("class_id", type=int)
    pre = pre if pre and any(c.id == pre for c in classes_) else (classes_[0].id if len(classes_) == 1 else None)
    return page(selected=pre)


@admin_bp.route("/kids/import/template")
@login_required
def kids_import_template():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "الأطفال"
    ws.sheet_view.rightToLeft = True
    headers = ["الاسم", "اسم الأم", "هاتف الأب", "هاتف الأم", "المدرسة", "العنوان", "المنطقة",
               "تاريخ الميلاد", "الإخوة والأخوات", "أب الاعتراف", "ملاحظات"]
    ws.append(headers)
    ws.append(["مينا سامر جرجس", "ماري مجدي", "01012345678", "01198765432", "مدرسة الأمل",
               "12 شارع الجيش", "شبرا", "2016-03-20", "مارك ومريم", "أبونا بيشوي", "يحب الرسم"])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="256B3E")
    for col in "ABCDEFGHIJK":
        ws.column_dimensions[col].width = 20
    for col in ("C", "D"):                      # keep phones as text so Excel keeps the leading 0
        for row in range(2, 502):
            ws[f"{col}{row}"].number_format = "@"
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="kids_template.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ==========================================================================
# ADMINS (leader only): create other admins and give each one their grades
# ==========================================================================
def _other_active_leaders(exclude_id) -> int:
    return User.query.filter(User.role == ROLE_ADMIN, User.is_active.is_(True),
                             User.id != exclude_id).count()


def _admin_data(form, existing=None):
    data, errors = identity_from_form(form, existing)
    admin_type = "full" if form.get("admin_type") == "full" else "grade"
    grade_ids = {_int(g, 0) for g in form.getlist("grade_ids")}
    grades_ = Grade.query.filter(Grade.id.in_(grade_ids)).all() if grade_ids else []
    if admin_type == "grade" and not grades_:
        errors.append(tr("اختر مرحلة واحدة على الأقل لمسؤول المرحلة",
                         "Choose at least one grade for a grade admin"))
    return data, admin_type, grades_, errors


def _classes_from_form():
    ids = {_int(c, 0) for c in request.form.getlist("class_ids")}
    return Class.query.filter(Class.id.in_(ids), Class.is_active.is_(True)).all() if ids else []


def _all_grades():
    return Grade.query.order_by(Grade.sort_order, Grade.id).all()


@admin_bp.route("/admins")
@admin_required
def admins():
    items = (User.query.options(joinedload(User.managed_grades))
             .filter(User.role.in_([ROLE_ADMIN, ROLE_GRADE_ADMIN]))
             .order_by(User.is_active.desc(), User.role, User.name_ar).all())
    return render_template("admin/admins.html", admins=items, team=False)


@admin_bp.route("/team")
@manager_required
def team():
    """Grade admins see the OTHER admins of their own grades — nobody else."""
    if current_user.is_admin:
        return redirect(url_for("admin.admins"))
    mine = set(current_user.managed_grade_ids)
    items = [a for a in User.query.options(joinedload(User.managed_grades))
             .filter(User.role == ROLE_GRADE_ADMIN, User.is_active.is_(True)).order_by(User.name_ar).all()
             if mine & {g.id for g in a.managed_grades}]
    return render_template("admin/admins.html", admins=items, team=True, my_grade_ids=mine)


@admin_bp.route("/admins/<int:admin_id>/demote", methods=["POST"])
@manager_required
def admin_demote(admin_id):
    """
    Take admin rights away inside MY grades. The leader removes all of them; a grade admin removes only
    the grades he manages — if that person is an admin of other grades too, he stays an admin there.
    With no grade left he becomes a normal servant again (keeping his classes).
    """
    target = db.get_or_404(User, admin_id)
    if target.role != ROLE_GRADE_ADMIN or target.id == current_user.id:
        abort(404)
    sc = _scope()
    mine = [g for g in target.managed_grades if sc is None or g.id in sc]
    _require(bool(mine))
    for g in mine:
        target.managed_grades.remove(g)
    if not target.managed_grades:
        target.role = "servant"
    log_action("admin_demoted", user_id=current_user.id, target=f"User:{target.id}")
    db.session.commit()
    flash(tr("تم إلغاء صلاحيات المسؤول", "Admin rights removed"), "info")
    return redirect(url_for("admin.admins" if current_user.is_admin else "admin.team"))


@admin_bp.route("/admins/new", methods=["GET", "POST"])
@admin_required
def admin_new():
    if request.method == "POST":
        data, admin_type, grades_, errors = _admin_data(request.form)
        password = request.form.get("password") or ""
        errors += validate_password(password, name=data["name_ar"], email=data["email"])
        if errors:
            _flash_errors(errors)
            return render_template("admin/admin_form.html", mode="new", form=request.form, all_classes=_class_choices(),
                                   selected_classes=[c.id for c in _classes_from_form()],
                                   all_grades=_all_grades(), selected=[g.id for g in grades_])
        user = User(role=ROLE_ADMIN if admin_type == "full" else ROLE_GRADE_ADMIN,
                    language="ar", must_change_password=True, **data)
        user.classes = _classes_from_form()
        user.set_password(password)
        user.managed_grades = grades_ if admin_type == "grade" else []
        db.session.add(user)
        db.session.flush()
        log_action("admin_created", user_id=current_user.id,
                   target=f"User:{user.id} {user.role}")
        db.session.commit()
        flash(tr("تمت إضافة المسؤول. سيُطلب منه تغيير كلمة المرور عند أول دخول.",
                 "Admin added. They must change the password on first sign-in."), "success")
        return redirect(url_for("admin.admins"))
    return render_template("admin/admin_form.html", mode="new", form={"admin_type": "grade"},
                           all_grades=_all_grades(), selected=[], all_classes=_class_choices(), selected_classes=[])


def _get_admin_or_404(admin_id):
    user = db.get_or_404(User, admin_id)
    if not user.is_manager:
        abort(404)
    return user


@admin_bp.route("/admins/<int:admin_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit(admin_id):
    target = _get_admin_or_404(admin_id)
    is_self = target.id == current_user.id
    if request.method == "POST":
        data, admin_type, grades_, errors = _admin_data(request.form, target)
        new_password = request.form.get("new_password") or ""
        if is_self:                                   # can't demote / disable / re-password yourself here
            admin_type, new_password = ("full" if target.is_admin else "grade"), ""
            errors = [e for e in errors if "مرحلة" not in e and "grade" not in e.lower()]
        elif new_password:
            errors += validate_password(new_password, name=data["name_ar"], email=data["email"])
        wants_active = True if is_self else bool(request.form.get("is_active"))
        new_role = ROLE_ADMIN if admin_type == "full" else ROLE_GRADE_ADMIN
        # never leave the system without an active full-access leader
        if (target.is_admin and target.is_active and (new_role != ROLE_ADMIN or not wants_active)
                and _other_active_leaders(target.id) == 0):
            errors.append(tr("لا يمكن إزالة آخر قائد بصلاحيات كاملة", "You can't remove the last full-access leader"))
        if errors:
            _flash_errors(errors)
            return render_template("admin/admin_form.html", mode="edit", target=target,
                                   is_self=is_self, form=request.form, all_grades=_all_grades(),
                                   all_classes=_class_choices(), selected_classes=[c.id for c in _classes_from_form()],
                                   selected=[g.id for g in grades_])
        for k, v in data.items():
            setattr(target, k, v)
        if request.form.get("classes_posted"):                 # an admin is a servant too: his own classes
            target.classes = _classes_from_form()
        if not is_self:
            target.role = new_role
            target.managed_grades = grades_ if new_role == ROLE_GRADE_ADMIN else []
            target.is_active = wants_active
            if new_password:
                target.set_password(new_password)
                target.must_change_password = True
                target.failed_login_count, target.locked_until = 0, None
                log_action("admin_password_reset", user_id=current_user.id, target=f"User:{target.id}")
        log_action("admin_updated", user_id=current_user.id, target=f"User:{target.id}")
        db.session.commit()
        flash(tr("تم تحديث بيانات المسؤول", "Admin updated"), "success")
        return redirect(url_for("admin.admins"))
    return render_template("admin/admin_form.html", mode="edit", target=target, is_self=is_self,
                           form={"admin_type": "full" if target.is_admin else "grade"},
                           all_grades=_all_grades(), selected=[g.id for g in target.managed_grades],
                           all_classes=_class_choices(), selected_classes=[c.id for c in target.classes])


@admin_bp.route("/admins/<int:admin_id>/toggle", methods=["POST"])
@admin_required
def admin_toggle(admin_id):
    target = _get_admin_or_404(admin_id)
    if target.id == current_user.id:
        flash(tr("لا يمكنك تعطيل حسابك", "You can't disable your own account"), "danger")
    elif target.is_admin and target.is_active and _other_active_leaders(target.id) == 0:
        flash(tr("لا يمكن تعطيل آخر قائد بصلاحيات كاملة", "You can't disable the last full-access leader"), "danger")
    else:
        target.is_active = not target.is_active
        log_action("admin_activated" if target.is_active else "admin_deactivated",
                   user_id=current_user.id, target=f"User:{target.id}")
        db.session.commit()
        flash(tr("تم تفعيل الحساب", "Account activated") if target.is_active
              else tr("تم تعطيل الحساب", "Account deactivated"), "info")
    return redirect(url_for("admin.admins"))


@admin_bp.route("/admins/<int:admin_id>/unlock", methods=["POST"])
@admin_required
def admin_unlock(admin_id):
    target = _get_admin_or_404(admin_id)
    target.failed_login_count, target.locked_until = 0, None
    log_action("admin_unlocked", user_id=current_user.id, target=f"User:{target.id}")
    db.session.commit()
    flash(tr("تم فتح الحساب", "Account unlocked"), "success")
    return redirect(url_for("admin.admins"))


# ==========================================================================
# SERVANT ACCOUNT REQUESTS (self-registration -> admin approves)
# ==========================================================================
def _get_request_or_404(user_id):
    """A pending request this admin is allowed to decide, else 404/403."""
    req = db.get_or_404(User, user_id)
    if not req.is_pending or req.role != "servant":
        abort(404)
    _require(pending_query_for(current_user).filter(User.id == req.id).first() is not None)
    return req


@admin_bp.route("/requests")
@manager_required
def requests_list():
    reqs = pending_query_for(current_user).order_by(User.requested_at).all()
    grades_ = {g.id: g for g in Grade.query.all()}
    names = {u.id: u for u in User.query.filter(User.role.in_([ROLE_ADMIN, ROLE_GRADE_ADMIN])).all()}
    classes_by_grade = {}
    for c in Class.query.filter(Class.is_active.is_(True)).order_by(Class.name_ar).all():
        classes_by_grade.setdefault(c.grade_id, []).append(c)
    return render_template("admin/requests.html", reqs=reqs, grades=grades_, names=names,
                           classes_by_grade=classes_by_grade)


@admin_bp.route("/requests/<int:user_id>/approve", methods=["POST"])
@manager_required
def request_approve(user_id):
    req = _get_request_or_404(user_id)
    class_ids = {_int(c, 0) for c in request.form.getlist("class_ids")}
    allowed = Class.query.filter(Class.id.in_(class_ids), Class.is_active.is_(True),
                                 Class.grade_id == req.requested_grade_id).all() if class_ids else []
    if not allowed:
        flash(tr("اختر فصلاً واحداً على الأقل من مرحلة الطلب قبل الموافقة",
                 "Choose at least one class from the requested grade before approving"), "danger")
        return redirect(url_for("admin.requests_list"))
    req.classes = allowed
    req.approval_status, req.is_active = "approved", True
    req.requested_grade_id = req.requested_admin_id = req.requested_at = None
    log_action("signup_approved", user_id=current_user.id, target=f"User:{req.id}")
    db.session.commit()
    flash(tr(f"تمت الموافقة. يمكن لـ {req.name_ar} تسجيل الدخول الآن.",
             f"Approved. {req.name_en or req.name_ar} can sign in now."), "success")
    return redirect(url_for("admin.requests_list"))


@admin_bp.route("/requests/<int:user_id>/reject", methods=["POST"])
@manager_required
def request_reject(user_id):
    req = _get_request_or_404(user_id)
    log_action("signup_rejected", user_id=current_user.id, target=f"@{req.username or req.phone}")
    db.session.delete(req)            # frees the username / phone so the person can re-apply
    db.session.commit()
    flash(tr("تم رفض الطلب", "Request declined"), "info")
    return redirect(url_for("admin.requests_list"))


# ==========================================================================
# SETTINGS + ACTIVITY LOG
# ==========================================================================
@admin_bp.route("/settings", methods=["GET", "POST"])
@admin_required
def settings():
    s = Settings.get()
    if request.method == "POST":
        day = request.form.get("attendance_day")
        s.absence_threshold = _int(request.form.get("absence_threshold"), 3, 1, 20)
        s.attendance_day = day if day in WEEKDAYS else "friday"
        s.church_name_ar = clean(request.form.get("church_name_ar"), 120) or s.church_name_ar
        s.church_name_en = clean(request.form.get("church_name_en"), 120) or s.church_name_en
        s.signup_open = bool(request.form.get("signup_open"))
        log_action("settings_updated", user_id=current_user.id)
        db.session.commit()
        flash(tr("تم حفظ الإعدادات", "Settings saved"), "success")
        return redirect(url_for("admin.settings"))
    return render_template("admin/settings.html", s=s, days=list(WEEKDAYS))


@admin_bp.route("/activity")
@admin_required
def activity():
    page = max(request.args.get("page", 1, type=int), 1)
    action = clean(request.args.get("action"), 80)
    q = AuditLog.query.options(joinedload(AuditLog.user))
    if action:
        q = q.filter(AuditLog.action == action)
    pagination = q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).paginate(
        page=page, per_page=50, error_out=False)
    actions = [a for (a,) in db.session.query(AuditLog.action).distinct().order_by(AuditLog.action)]
    return render_template("admin/activity.html", pagination=pagination,
                           logs=pagination.items, actions=actions, selected=action)
