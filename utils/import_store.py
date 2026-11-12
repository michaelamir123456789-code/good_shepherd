"""
utils/import_store.py — Server-side holding area for a parsed import.

The old code put every parsed row into the cookie session. Cookies are limited
to ~4 KB (and dates are not JSON-serialisable), so any real file silently broke.
Now the rows live in a small JSON file under instance/imports/ and the session
only holds a random token. Files are bound to the user who uploaded them and
expire after one hour.
"""
import json
import os
import re
import secrets
import time
from datetime import date
from flask import current_app

TTL_SECONDS = 3600
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{20,64}$")


def _dir() -> str:
    path = os.path.join(current_app.instance_path, "imports")
    os.makedirs(path, exist_ok=True)
    return path


def _purge_old() -> None:
    now = time.time()
    d = _dir()
    for name in os.listdir(d):
        p = os.path.join(d, name)
        try:
            if now - os.path.getmtime(p) > TTL_SECONDS:
                os.remove(p)
        except OSError:
            pass


def _ser(o):
    if isinstance(o, date):
        return o.isoformat()
    raise TypeError


def save(user_id: int, filename: str, rows: list, class_id: int | None = None) -> str:
    _purge_old()
    token = secrets.token_urlsafe(24)
    payload = {"user_id": user_id, "filename": filename, "rows": rows, "class_id": class_id}
    with open(os.path.join(_dir(), token + ".json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, default=_ser, ensure_ascii=False)
    return token


def load(token: str | None, user_id: int):
    """Return (rows, class_id) — dates restored — or None if missing/expired/not yours."""
    if not token or not _TOKEN_RE.match(token):
        return None
    path = os.path.join(_dir(), token + ".json")
    try:
        if time.time() - os.path.getmtime(path) > TTL_SECONDS:
            os.remove(path)
            return None
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return None
    if payload.get("user_id") != user_id:
        return None
    rows = payload.get("rows", [])
    class_id = payload.get("class_id")
    for r in rows:
        if r.get("birth_date"):
            try:
                r["birth_date"] = date.fromisoformat(r["birth_date"])
            except ValueError:
                r["birth_date"] = None
    return rows, class_id


def discard(token: str | None) -> None:
    if token and _TOKEN_RE.match(token):
        try:
            os.remove(os.path.join(_dir(), token + ".json"))
        except OSError:
            pass
