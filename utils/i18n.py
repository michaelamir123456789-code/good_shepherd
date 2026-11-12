"""utils/i18n.py — Tiny bilingual helper (Arabic first)."""
from flask import session, request, g, current_app
from flask_login import current_user


def get_lang() -> str:
    cached = getattr(g, "_lang", None)
    if cached:
        return cached
    supported = current_app.config["SUPPORTED_LANGUAGES"]
    lang = session.get("lang")
    if lang not in supported:
        lang = None
        if current_user.is_authenticated and current_user.language in supported:
            lang = current_user.language
    if not lang:
        lang = (request.accept_languages.best_match(supported)
                or current_app.config["DEFAULT_LANGUAGE"])
    g._lang = lang
    return lang


def tr(ar: str, en: str | None = None) -> str:
    """Pick the Arabic or English text for the current request."""
    if en is None:
        return ar
    return ar if get_lang() == "ar" else en
