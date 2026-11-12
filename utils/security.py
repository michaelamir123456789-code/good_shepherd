"""utils/security.py — Login security helpers + audit logging."""
from urllib.parse import urlparse, urljoin
from flask import request
from werkzeug.security import generate_password_hash

from extensions import db
from models import AuditLog

# Used to burn the same CPU time when the e-mail is unknown (anti user-enumeration)
DUMMY_HASH = generate_password_hash("not-a-real-password", method="pbkdf2:sha256:600000")


def get_client_ip() -> str:
    """
    Real client IP. X-Forwarded-For is NOT read here: when behind a proxy,
    set TRUSTED_PROXY_COUNT and werkzeug's ProxyFix fixes request.remote_addr.
    """
    return request.remote_addr or "unknown"


def is_safe_redirect(target: str | None) -> bool:
    """Only allow same-site relative redirects (prevents open redirects)."""
    if not target:
        return False
    host = urlparse(request.host_url)
    test = urlparse(urljoin(request.host_url, target))
    return test.scheme in ("http", "https") and host.netloc == test.netloc


def log_action(action: str, user_id=None, target=None):
    AuditLog.log(
        action=action,
        user_id=user_id,
        target=(target or "")[:120] or None,
        ip=get_client_ip(),
        user_agent=request.headers.get("User-Agent", ""),
    )


def register_login_attempt(user, success: bool, max_attempts: int,
                           lockout_minutes: int):
    ip = get_client_ip()
    if success:
        user.register_successful_login(ip=ip)
        log_action("login_success", user_id=user.id)
    else:
        user.register_failed_login(max_attempts, lockout_minutes)
        if user.is_locked_out:
            log_action("account_locked", user_id=user.id,
                       target=f"until {user.locked_until.isoformat()}")
        else:
            log_action("login_failed", user_id=user.id)
    db.session.commit()
