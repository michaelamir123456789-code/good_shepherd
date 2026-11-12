"""utils/locks.py — Attendance session lock helpers (one servant at a time)."""
from datetime import date
from flask import current_app
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import AttendanceSession


def _minutes() -> int:
    return current_app.config.get("ATTENDANCE_LOCK_MINUTES", 15)


def get_or_create_session(class_id: int, target_date: date) -> AttendanceSession:
    session = AttendanceSession.query.filter_by(
        class_id=class_id, date=target_date).first()
    if session:
        return session
    session = AttendanceSession(class_id=class_id, date=target_date)
    db.session.add(session)
    try:
        db.session.commit()
    except IntegrityError:          # someone created it a millisecond earlier
        db.session.rollback()
        session = AttendanceSession.query.filter_by(
            class_id=class_id, date=target_date).first()
    return session


def acquire_lock(class_id: int, target_date: date, user_id: int):
    """Take (or refresh) the lock. Returns (ok, holder_user_or_None)."""
    session = get_or_create_session(class_id, target_date)
    if session.is_locked(_minutes()) and session.locked_by_id != user_id:
        return False, session.locked_by
    session.lock(user_id)
    db.session.commit()
    return True, None


def release_lock(class_id: int, target_date: date, user_id: int | None = None,
                 force: bool = False) -> bool:
    """Release only if the caller holds it (or force=True for the leader)."""
    session = AttendanceSession.query.filter_by(
        class_id=class_id, date=target_date).first()
    if not session:
        return False
    if (not force and user_id is not None and session.locked_by_id
            and session.locked_by_id != user_id and session.is_locked(_minutes())):
        return False
    session.unlock()
    db.session.commit()
    return True


def lock_status(class_id: int, target_date: date, current_user_id: int) -> dict:
    session = AttendanceSession.query.filter_by(
        class_id=class_id, date=target_date).first()
    if not session or not session.is_locked(_minutes()):
        return {"locked": False, "by": None, "is_me": False}
    holder = session.locked_by
    return {
        "locked": True,
        "is_me": session.locked_by_id == current_user_id,
        "by": {"name_ar": holder.name_ar, "name_en": holder.name_en}
        if holder else None,
    }
