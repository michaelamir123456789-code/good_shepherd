"""
utils/identity.py — Usernames, phone numbers and "who is signing in?".

A person can sign in with ANY of: username, phone number or e-mail.
* username : starts with a letter, 3-30 chars of a-z 0-9 . _ -   (case-insensitive)
* phone    : stored normalised (01012345678) so "+20 101 234 5678", "0101-234-5678"
             and Arabic digits "٠١٠١٢٣٤٥٦٧٨" are all the same number
* e-mail   : optional. Accounts without one get an invisible placeholder because the
             database column is mandatory.
"""
import re
import secrets
from sqlalchemy import func

from utils.i18n import tr
from utils.validators import clean, validate_email_format, validate_name

USERNAME_RE = re.compile(r"^[a-z][a-z0-9_.\-]{2,29}$")
PLACEHOLDER_DOMAIN = "no-email.invalid"
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_PHONE_LIKE = re.compile(r"^[+\d\s\-().٠-٩۰-۹]{7,25}$")
RESERVED = {"admin", "administrator", "root", "support", "system", "null", "none", "me"}


# ------------------------------------------------------------------ phone
def normalize_phone(raw) -> str | None:
    """'+20 101-234 5678' / '٠١٠١٢٣٤٥٦٧٨' / '1012345678' -> '01012345678'."""
    if not raw:
        return None
    s = str(raw).translate(_DIGITS)
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("20") and len(digits) == 12:        # +20 1xx xxx xxxx
        digits = "0" + digits[2:]
    elif len(digits) == 10 and digits.startswith("1"):       # Excel/phones drop the 0
        digits = "0" + digits
    return digits[:15]


def validate_phone_number(phone: str | None) -> list[str]:
    if phone is None:
        return []
    if not (8 <= len(phone) <= 15):
        return [tr("رقم الهاتف غير صالح", "Invalid phone number")]
    return []


# --------------------------------------------------------------- username
def clean_username(raw) -> str:
    return (raw or "").strip().lower()


def validate_username(username: str) -> list[str]:
    if not USERNAME_RE.match(username):
        return [tr("اسم المستخدم: 3 إلى 30 حرفاً إنجليزياً صغيراً أو أرقاماً (. _ -)، ويبدأ بحرف",
                   "Username: 3-30 letters/numbers (. _ -), starting with a letter")]
    if username in RESERVED:
        return [tr("اسم المستخدم غير متاح، اختر غيره", "That username is not available")]
    return []


# ----------------------------------------------------------------- e-mail
def placeholder_email() -> str:
    """Random + unique, so it can never collide when usernames/phones change later."""
    return f"u{secrets.token_hex(8)}@{PLACEHOLDER_DOMAIN}"


def is_placeholder_email(email: str | None) -> bool:
    return bool(email) and email.endswith("@" + PLACEHOLDER_DOMAIN)


# ----------------------------------------------------------------- lookup
def find_user_by_identifier(identifier: str):
    """Username, phone or e-mail -> User (or None)."""
    from models import User
    ident = (identifier or "").strip()[:120]
    if not ident:
        return None
    low = ident.lower()
    if "@" in low:
        return User.query.filter(func.lower(User.email) == low).first()
    if _PHONE_LIKE.match(ident):
        phone = normalize_phone(ident)
        return User.query.filter(User.phone == phone).first() if phone else None
    return User.query.filter(User.username == low).first()


def identifier_key(identifier: str) -> str:
    """Stable key for rate limiting (same person typing it differently = same key)."""
    ident = (identifier or "").strip()[:120]
    if "@" not in ident and _PHONE_LIKE.match(ident):
        return normalize_phone(ident) or ident.lower()
    return ident.lower()


# ------------------------------------------------- shared form processing
def identity_from_form(form, existing=None, require_username=False, require_phone=False):
    """
    Read + validate name / username / phone / e-mail from a form (admin forms, the
    account page and the sign-up page all use this one function).
    A field that is ABSENT from the form keeps the existing value; a field that is
    present but empty clears it.   Returns (data_dict, errors).
    """
    from models import User

    def pick(key, cleaner):
        if key in form:
            return cleaner(form.get(key))
        return None

    errors: list[str] = []
    name_ar = clean(form.get("name_ar"), 120)
    if "name_en" in form:
        name_en = clean(form.get("name_en"), 120) or name_ar
    else:                                   # forms without an English field keep the old one
        name_en = (existing.name_en if existing and existing.name_en else name_ar)
    errors += validate_name(name_ar, tr("الاسم", "Name"))
    errors += validate_name(name_en, tr("الاسم بالإنجليزية", "English name"))

    # username
    if "username" in form:
        username = clean_username(form.get("username")) or None
    else:
        username = existing.username if existing else None
    if username and not (existing and existing.username == username):
        errors += validate_username(username)

    # phone
    if "phone" in form:
        phone = normalize_phone(clean(form.get("phone"), 40))
    else:
        phone = existing.phone if existing else None
    errors += validate_phone_number(phone)

    # e-mail (optional)
    if "email" in form:
        email = clean(form.get("email"), 120).lower()
    else:
        email = ("" if (not existing or is_placeholder_email(existing.email)) else existing.email)
    if email:
        errors += validate_email_format(email)

    if require_username and not username:
        errors.append(tr("اكتب اسم مستخدم", "Choose a username"))
    if require_phone and not phone:
        errors.append(tr("اكتب رقم الهاتف", "Enter a phone number"))
    # After this change the person must still have at least ONE way to sign in
    if not (username or phone or email):
        errors.append(tr("اترك على الأقل وسيلة دخول واحدة: اسم مستخدم أو رقم هاتف أو بريد إلكتروني",
                         "Keep at least one way to sign in: a username, a phone number or an e-mail"))

    # uniqueness
    me = existing.id if existing else None
    if username:
        c = User.query.filter(User.username == username).first()
        if c and c.id != me:
            errors.append(tr("اسم المستخدم مستخدم بالفعل", "This username is already taken"))
    if phone:
        c = User.query.filter(User.phone == phone).first()
        if c and c.id != me:
            errors.append(tr("رقم الهاتف مسجّل بحساب آخر", "This phone number is already registered"))
    if email:
        c = User.query.filter(func.lower(User.email) == email).first()
        if c and c.id != me:
            errors.append(tr("هذا البريد الإلكتروني مستخدم بالفعل", "This e-mail is already in use"))

    if email:
        final_email = email
    elif existing and is_placeholder_email(existing.email):
        final_email = existing.email                      # already has an invisible placeholder
    else:
        final_email = placeholder_email()
    data = {"name_ar": name_ar, "name_en": name_en, "username": username,
            "phone": phone, "email": final_email}
    return data, errors
