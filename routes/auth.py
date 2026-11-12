"""routes/auth.py — Login, logout, password change, language."""
from datetime import datetime
from urllib.parse import urlparse
from flask import (Blueprint, render_template, redirect, url_for,
                   request, flash, session, current_app, abort)
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash

from extensions import db, limiter
from models import User, Grade, Settings
from utils.i18n import tr, get_lang
from utils.identity import (find_user_by_identifier, identifier_key, identity_from_form)
from utils.signup import total_pending, MAX_PENDING
from utils.validators import validate_password
from utils.security import (register_login_attempt, log_action, get_client_ip,
                            is_safe_redirect, DUMMY_HASH)

auth_bp = Blueprint("auth", __name__)

DEFAULT_ADMIN_PASSWORD = "ChangeMe@2026!"


def _login_key():
    """Limit per (IP + who is being tried): one shared church Wi-Fi can't lock everyone out."""
    ident = request.form.get("identifier") or request.form.get("email") or ""
    return f"{get_client_ip()}|{identifier_key(ident)}"


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("8 per 15 minutes", key_func=_login_key, methods=["POST"])
@limiter.limit("100 per 15 minutes", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.home"))

    next_url = request.args.get("next") or request.form.get("next") or ""
    settings = Settings.get()
    if request.method == "POST":
        ident = (request.form.get("identifier") or request.form.get("email") or "").strip()[:120]
        password = (request.form.get("password") or "")[:256]
        cfg = current_app.config
        user = find_user_by_identifier(ident)
        bad = tr("اسم المستخدم / رقم الهاتف أو كلمة المرور غير صحيحة",
                 "Incorrect username / phone or password")

        def again(msg, cat="danger"):
            flash(msg, cat)
            return render_template("auth/login.html", identifier=ident, next=next_url,
                                   signup_open=settings.signup_open)

        if user is None:
            check_password_hash(DUMMY_HASH, password)    # same cost as a real check
            log_action("login_failed_unknown", target=ident[:100])
            db.session.commit()
            return again(bad)

        if user.is_locked_out:
            log_action("login_locked", user_id=user.id)
            db.session.commit()
            minutes = max(int((user.locked_until - datetime.utcnow()).total_seconds() / 60), 1)
            return again(tr(f"الحساب مقفول مؤقتاً بسبب محاولات كثيرة. حاول بعد {minutes} دقيقة.",
                            f"Account temporarily locked. Try again in {minutes} minute(s)."))

        if not user.check_password(password):
            register_login_attempt(user, False, cfg["MAX_LOGIN_ATTEMPTS"],
                                   cfg["LOGIN_LOCKOUT_MINUTES"])
            return again(bad)

        # Password is right — only now say anything about the account's state.
        if user.is_pending:
            log_action("login_pending", user_id=user.id)
            db.session.commit()
            approver = db.session.get(User, user.requested_admin_id) if user.requested_admin_id else None
            who = approver.display_name(get_lang()) if approver else tr("المسؤول", "the admin")
            return again(tr(f"طلب حسابك قيد المراجعة عند {who}. سيمكنك الدخول بعد الموافقة عليه.",
                            f"Your account request is waiting for {who}. You can sign in once it is approved."),
                         "warning")
        if not user.is_active:
            log_action("login_disabled", user_id=user.id)
            db.session.commit()
            return again(tr("هذا الحساب معطّل. تواصل مع المسؤول.",
                            "This account is disabled. Contact your admin."))

        register_login_attempt(user, True, cfg["MAX_LOGIN_ATTEMPTS"], cfg["LOGIN_LOCKOUT_MINUTES"])
        lang = user.language
        session.clear()                       # new session id => no fixation
        login_user(user, remember=False)
        session["lang"] = lang
        session.permanent = True

        if user.must_change_password:
            return redirect(url_for("auth.change_password"))
        if next_url and is_safe_redirect(next_url):
            return redirect(next_url)
        return redirect(url_for("dashboard.home"))

    return render_template("auth/login.html", next=next_url, signup_open=settings.signup_open)


# --------------------------------------------------------------- sign-up
def _admins_by_grade(grades):
    out = {}
    for g in grades:
        out[g.id] = [a for a in g.admins if a.is_active and a.approval_status == "approved"
                     and a.is_grade_admin]
    return out


@auth_bp.route("/register", methods=["GET", "POST"])
@limiter.limit("30 per hour", methods=["POST"])
def register():
    """A servant asks for an account. An admin of the chosen grade approves it."""
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.home"))
    settings = Settings.get()
    if not settings.signup_open:
        return render_template("auth/register_closed.html")

    grades = Grade.query.filter_by(is_active=True).order_by(Grade.sort_order, Grade.id).all()
    admins = _admins_by_grade(grades)

    def show(form=None):
        return render_template("auth/register.html", grades=grades, admins=admins, form=form or {})

    if request.method == "POST":
        if request.form.get("website"):               # honeypot: bots fill hidden fields
            return redirect(url_for("auth.register_sent"))
        if total_pending() >= MAX_PENDING:
            flash(tr("هناك طلبات كثيرة قيد الانتظار حالياً. حاول لاحقاً.",
                     "Too many requests are waiting right now. Try again later."), "warning")
            return show(request.form)

        data, errors = identity_from_form(request.form, require_username=True, require_phone=True)
        password = request.form.get("password") or ""
        errors += validate_password(password, name=data["name_ar"], email=data["email"])

        grade = db.session.get(Grade, request.form.get("grade_id", type=int) or 0)
        if not grade or not grade.is_active:
            errors.append(tr("اختر المرحلة التي ستخدم فيها", "Choose the grade you will serve in"))
        admin_id = request.form.get("admin_id", type=int)
        approver = None
        if grade and admin_id:
            approver = next((a for a in admins.get(grade.id, []) if a.id == admin_id), None)
            if not approver:
                errors.append(tr("المسؤول المختار ليس من هذه المرحلة", "That admin is not responsible for this grade"))
        if errors:
            for e in errors:
                flash(e, "danger")
            return show(request.form)

        user = User(role="servant", is_active=False, approval_status="pending",
                    must_change_password=False, language=get_lang(),
                    requested_grade_id=grade.id,
                    requested_admin_id=approver.id if approver else None,
                    requested_at=datetime.utcnow(), **data)
        user.set_password(password)
        db.session.add(user)
        db.session.flush()
        log_action("signup_requested", target=f"User:{user.id} grade:{grade.id}")
        db.session.commit()
        session["signup_to"] = (approver.display_name(get_lang()) if approver else
                                tr("مسؤول المرحلة", "the grade admin"))
        return redirect(url_for("auth.register_sent"))

    return show()


@auth_bp.route("/register/sent")
def register_sent():
    return render_template("auth/register_sent.html", to=session.pop("signup_to", None))


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    forced = current_user.must_change_password
    if request.method == "POST":
        current = request.form.get("current_password") or ""
        new_password = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""
        errors = []
        if not current_user.check_password(current):
            errors.append(tr("كلمة المرور الحالية غير صحيحة",
                             "Current password is incorrect"))
        if new_password != confirm:
            errors.append(tr("كلمتا المرور غير متطابقتين", "Passwords do not match"))
        errors += validate_password(new_password, name=current_user.name_ar,
                                    email=current_user.email)
        if new_password == DEFAULT_ADMIN_PASSWORD:
            errors.append(tr("لا يمكن استخدام كلمة المرور الافتراضية",
                             "You cannot use the default password"))
        if current and new_password == current:
            errors.append(tr("كلمة المرور الجديدة يجب أن تختلف عن الحالية",
                             "The new password must differ from the current one"))
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("auth/change_password.html", forced=forced)

        current_user.set_password(new_password)
        current_user.must_change_password = False
        log_action("password_changed", user_id=current_user.id)
        db.session.commit()
        lang = session.get("lang")
        # The session id embeds a password stamp, so refresh it: this device
        # stays signed in, every other device is signed out.
        login_user(current_user, remember=False)
        session["lang"] = lang
        session.permanent = True
        flash(tr("تم تغيير كلمة المرور بنجاح", "Password changed successfully"),
              "success")
        return redirect(url_for("dashboard.home"))

    return render_template("auth/change_password.html", forced=forced)


@auth_bp.route("/account", methods=["GET", "POST"])
@login_required
def account():
    """Everyone can change their own name, username, phone and (optional) e-mail."""
    if request.method == "POST":
        data, errors = identity_from_form(request.form, existing=current_user)
        identity_changed = any(data[k] != getattr(current_user, k)
                               for k in ("username", "phone", "email"))
        if identity_changed and not current_user.check_password(request.form.get("current_password") or ""):
            errors.append(tr("أدخل كلمة المرور الحالية الصحيحة لتغيير اسم المستخدم أو الهاتف أو البريد",
                             "Enter your correct current password to change your username, phone or e-mail"))
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("auth/account.html", form=request.form)
        before = current_user.login_label
        for k, v in data.items():
            setattr(current_user, k, v)
        log_action("account_updated", user_id=current_user.id,
                   target="login details changed" if identity_changed else None)
        db.session.commit()
        flash(tr("تم حفظ بيانات حسابك", "Your account was saved"), "success")
        return redirect(url_for("auth.account"))
    return render_template("auth/account.html", form={})


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    log_action("logout", user_id=current_user.id)
    db.session.commit()
    logout_user()
    session.clear()
    flash(tr("تم تسجيل الخروج", "Signed out"), "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/language/<lang>")
def set_language(lang: str):
    if lang not in current_app.config["SUPPORTED_LANGUAGES"]:
        lang = current_app.config["DEFAULT_LANGUAGE"]
    session["lang"] = lang
    if current_user.is_authenticated:
        current_user.language = lang
        db.session.commit()
    ref = request.referrer
    if ref and urlparse(ref).netloc == request.host:
        return redirect(ref)
    return redirect(url_for("dashboard.home"))
