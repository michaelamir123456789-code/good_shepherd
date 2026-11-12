"""
utils/db_upgrade.py — Tiny, safe, idempotent schema upgrades.

Existing databases created by an older version get new columns/tables added
automatically at startup (no data is removed or changed, except that user phone
numbers are written in one normalised format so phone sign-in works).
"""
from sqlalchemy import inspect, text


def _cols(insp, table):
    return {c["name"] for c in insp.get_columns(table)}


def ensure_schema(db) -> list[str]:
    done = []
    insp = inspect(db.engine)
    tables = insp.get_table_names()
    sqlite = db.engine.dialect.name == "sqlite"
    dt = "DATETIME" if sqlite else "TIMESTAMP"
    true = "1" if sqlite else "TRUE"

    def add(table, name, ddl):
        with db.engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
        done.append(f"{table}.{name}")

    if "kids" in tables:
        have = _cols(insp, "kids")
        for name, ddl in (("photo_filename", "VARCHAR(64)"), ("mother_name", "VARCHAR(120)"),
                          ("school", "VARCHAR(120)"), ("siblings", "VARCHAR(500)"),
                          ("confession_father", "VARCHAR(120)")):
            if name not in have:
                add("kids", name, ddl)
    if "grades" in tables and "absence_weeks" not in _cols(insp, "grades"):
        add("grades", "absence_weeks", "INTEGER")

    if "users" in tables:
        have = _cols(insp, "users")
        if "username" not in have:
            add("users", "username", "VARCHAR(40)")
        if "approval_status" not in have:
            add("users", "approval_status", "VARCHAR(10) NOT NULL DEFAULT 'approved'")
        if "requested_grade_id" not in have:
            add("users", "requested_grade_id", "INTEGER")
        if "requested_admin_id" not in have:
            add("users", "requested_admin_id", "INTEGER")
        if "requested_at" not in have:
            add("users", "requested_at", dt)
        with db.engine.begin() as conn:
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users (username)"))
        if "grade_admins" not in tables:
            from models import grade_admins
            grade_admins.create(bind=db.engine, checkfirst=True)
            done.append("table grade_admins")

    if "settings" in tables and "signup_open" not in _cols(insp, "settings"):
        add("settings", "signup_open", f"BOOLEAN NOT NULL DEFAULT {true}")

    if "users" in tables and any(d.startswith("users.") for d in done):
        _normalise_user_phones(db, done)
    return done


def _normalise_user_phones(db, done):
    """Write every user's phone in the canonical format (once, right after upgrading).
    Plain SQL on two columns only, so it can't break on an unusual old table."""
    from utils.identity import normalize_phone
    insp = inspect(db.engine)
    if "phone" not in _cols(insp, "users"):
        return
    changed = 0
    with db.engine.begin() as conn:
        rows = conn.execute(text("SELECT id, phone FROM users WHERE phone IS NOT NULL")).fetchall()
        for uid, phone in rows:
            new = normalize_phone(phone)
            if new != phone:
                conn.execute(text("UPDATE users SET phone = :p WHERE id = :i"), {"p": new, "i": uid})
                changed += 1
    if changed:
        done.append(f"normalised {changed} phone number(s)")
