"""utils/signup.py — Who may see / decide a servant's account request."""
from sqlalchemy import false

from models import User, ROLE_SERVANT

MAX_PENDING = 300       # public form safety valve


def pending_query_for(user):
    """
    Requests waiting for a decision that `user` is allowed to see:
      leader       -> all of them
      grade admin  -> requests for the grades he/she manages
      anyone else  -> none
    (Every admin of a grade sees the grade's requests, so a request never gets stuck
    because the admin it was addressed to is away; the chosen admin is highlighted.)
    """
    q = User.query.filter(User.approval_status == "pending", User.role == ROLE_SERVANT)
    if user.is_admin:
        return q
    if user.is_grade_admin and user.managed_grade_ids:
        return q.filter(User.requested_grade_id.in_(user.managed_grade_ids))
    return q.filter(false())


def count_pending_for(user) -> int:
    if not getattr(user, "is_authenticated", False) or not user.is_manager:
        return 0
    return pending_query_for(user).count()


def total_pending() -> int:
    return User.query.filter(User.approval_status == "pending").count()
