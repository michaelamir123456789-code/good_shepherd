"""
app.py — Application factory for الراعي الصالح.

Run locally:   python app.py          (127.0.0.1 only)
Production:    gunicorn "app:create_app()"   (behind HTTPS)
"""
import os
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import current_user
from flask_wtf.csrf import CSRFError
from werkzeug.middleware.proxy_fix import ProxyFix

from config import get_config, ensure_secret_key
from extensions import db, login_manager, migrate, csrf, limiter
from utils.i18n import get_lang, tr
from utils import dates as dt

CSP = ("default-src 'self'; img-src 'self' data:; font-src 'self'; "
       "style-src 'self'; script-src 'self'; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; "
       "object-src 'none'")

# Endpoints reachable while a password change is still pending
_PW_CHANGE_ALLOWED = {"auth.change_password", "auth.logout",
                      "auth.set_language", "static"}


def create_app(config_name: str | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(get_config(config_name))
    ensure_secret_key(app)
    os.makedirs(app.instance_path, exist_ok=True)
    # Make sure the folder that will hold the SQLite file exists
    from sqlalchemy.engine import make_url
    url = make_url(app.config["SQLALCHEMY_DATABASE_URI"])
    if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(url.database)), exist_ok=True)

    if app.config["TRUSTED_PROXY_COUNT"] > 0:
        n = app.config["TRUSTED_PROXY_COUNT"]
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n)

    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    limiter.init_app(app)

    # Add columns introduced by newer versions to an existing database
    with app.app_context():
        try:
            from utils.db_upgrade import ensure_schema
            for change in ensure_schema(db):
                app.logger.info("Database upgraded: %s", change)
        except Exception:
            app.logger.exception("Automatic database upgrade failed")

    @limiter.request_filter
    def _skip_static():
        return request.endpoint == "static"

    # ------------------------------------------------------------ auth
    @login_manager.user_loader
    def load_user(session_id):
        """Session id = '<user id>.<password stamp>' (see User.get_id)."""
        from models import User
        try:
            uid, _, stamp = str(session_id).partition(".")
            user = db.session.get(User, int(uid))
        except (ValueError, TypeError):
            return None
        if not user or not user.is_active:          # deactivated => logged out
            return None
        if stamp != user._pw_stamp():               # password changed => logged out
            return None
        return user

    @app.before_request
    def _force_password_change():
        if (current_user.is_authenticated and current_user.must_change_password
                and request.endpoint not in _PW_CHANGE_ALLOWED):
            return redirect(url_for("auth.change_password"))

    # -------------------------------------------------------- blueprints
    from routes.auth import auth_bp
    from routes.dashboard import dashboard_bp
    from routes.admin import admin_bp
    from routes.attendance import attendance_bp
    from routes.reports import reports_bp
    from routes.profiles import profiles_bp
    from routes.visitation import visitation_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(attendance_bp, url_prefix="/attendance")
    app.register_blueprint(reports_bp, url_prefix="/reports")
    app.register_blueprint(profiles_bp, url_prefix="/kids")
    app.register_blueprint(visitation_bp, url_prefix="/visitation")

    # --------------------------------------------------- template helpers
    @app.template_filter("fdate")
    def _fdate(d, weekday=True, year=True):
        return dt.format_date(d, get_lang(), weekday, year)

    @app.template_filter("fshort")
    def _fshort(d):
        return dt.format_short(d, get_lang())

    @app.template_filter("ftime")
    def _ftime(value):
        local = dt.utc_to_local(value)
        return local.strftime("%Y-%m-%d %H:%M") if local else "—"

    @app.context_processor
    def inject_globals():
        from models import Settings
        try:
            settings = Settings.get()
        except Exception:
            settings = None
        lang = get_lang()
        try:
            from utils.signup import count_pending_for
            pending_requests = count_pending_for(current_user)
        except Exception:
            pending_requests = 0
        church = None
        if settings:
            church = (settings.church_name_ar if lang == "ar"
                      else settings.church_name_en)
        c = app.config
        return {
            "t": tr,
            "current_lang": lang,
            "dir": "rtl" if lang == "ar" else "ltr",
            "settings": settings,
            "pending_requests": pending_requests,
            "brand": church or (c["SITE_NAME_AR"] if lang == "ar"
                                else c["SITE_NAME_EN"]),
            "tagline": (c["SITE_TAGLINE_AR"] if lang == "ar"
                        else c["SITE_TAGLINE_EN"]),
            "verse": c["VERSE_AR"] if lang == "ar" else c["VERSE_EN"],
            "verse_ref": c["VERSE_REF_AR"] if lang == "ar" else c["VERSE_REF_EN"],
            "weekday_labels": dt.WEEKDAY_LABELS[lang],
        }

    # ------------------------------------------------- security headers
    @app.after_request
    def _headers(resp):
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()")
        if app.config["ENABLE_HSTS"] and request.is_secure:
            resp.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains")
        if request.endpoint == "static":
            resp.headers["Cache-Control"] = "public, max-age=86400"
        elif request.endpoint == "profiles.photo" and resp.status_code == 200:
            pass   # kid photos: private cache set by the route (avoids 40 re-downloads per page)
        else:
            # Pages hold children's personal data: never cache them (shared phones!)
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # ----------------------------------------------------- error pages
    def _error(code, title, message):
        return render_template("errors/error.html", code=code,
                               title=title, message=message), code

    @app.errorhandler(CSRFError)
    def csrf_error(e):
        flash(tr("انتهت صلاحية الصفحة. أعد المحاولة.",
                 "The page expired. Please try again."), "warning")
        if current_user.is_authenticated:
            return redirect(url_for("dashboard.home"))
        return redirect(url_for("auth.login"))

    @app.errorhandler(403)
    def forbidden(e):
        return _error(403, tr("ممنوع الوصول", "Access denied"),
                      tr("ليس لديك صلاحية للوصول إلى هذه الصفحة.",
                         "You don't have permission to open this page."))

    @app.errorhandler(404)
    def not_found(e):
        return _error(404, tr("الصفحة غير موجودة", "Page not found"),
                      tr("الرابط غير صحيح أو تم حذف الصفحة.",
                         "The link is wrong or the page was removed."))

    @app.errorhandler(413)
    def too_large(e):
        return _error(413, tr("الملف كبير جداً", "File too large"),
                      tr("الحد الأقصى لحجم الملف 15 ميجا.", "Maximum size is 15 MB."))

    @app.errorhandler(429)
    def too_many(e):
        return _error(429, tr("محاولات كثيرة", "Too many attempts"),
                      tr("من فضلك انتظر قليلاً ثم حاول مرة أخرى.",
                         "Please wait a little and try again."))

    @app.errorhandler(500)
    def server_error(e):
        db.session.rollback()
        return _error(500, tr("حدث خطأ", "Something went wrong"),
                      tr("حدث خطأ غير متوقع. تم تسجيله، حاول مرة أخرى.",
                         "An unexpected error occurred. Please try again."))

    return app

# 👇 VERCEL NEEDS THIS LINE
app = create_app()

if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        db.create_all()
    # Local use only: 127.0.0.1. For production use gunicorn behind HTTPS.
    app.run(host="127.0.0.1", port=5000, debug=app.config["DEBUG"])
