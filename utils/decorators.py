"""utils/decorators.py — Role-based access + the "what can this person see" rules."""
from functools import wraps
from flask import abort
from flask_login import current_user, login_required


def admin_required(f):
    """Service leader only (settings, activity log, grades, managing admins)."""
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return wrapper


def manager_required(f):
    """Leader or grade admin. Each route must still check the item is in scope."""
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_manager:
            abort(403)
        return f(*args, **kwargs)
    return wrapper


def grade_scope(user):
    """None = every grade (leader). Otherwise the list of grade ids the user manages."""
    if user.is_admin:
        return None
    if user.is_grade_admin:
        return user.managed_grade_ids
    return []


def class_ids_for(user):
    """
    Which classes the user may see.
      leader        -> None (no restriction)
      grade admin   -> every class inside his/her grades
      servant       -> his/her own classes
    """
    from models import Class
    if user.is_admin:
        return None
    if user.is_grade_admin:
        ids = user.managed_grade_ids
        if not ids:
            return []
        return [c.id for c in Class.query.filter(Class.grade_id.in_(ids)).all()]
    return [c.id for c in user.classes]


def can_access_class(user, klass) -> bool:
    if user.is_admin:
        return True
    if user.is_grade_admin:
        return klass.grade_id in user.managed_grade_ids
    return klass in user.classes


def can_access_kid(user, kid) -> bool:
    if user.is_admin:
        return True
    if not kid.klass:                 # kids with no class are visible to the leader only
        return False
    return can_access_class(user, kid.klass)
