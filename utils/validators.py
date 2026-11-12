"""utils/validators.py — Server-side input validation."""
import re
from email_validator import validate_email, EmailNotValidError

SPECIAL_CHARS = r"!@#$%^&*(),.?\":{}|<>_\-+="

WEAK = {"password", "12345678", "qwerty123", "admin123", "iloveyou",
        "password1", "11111111", "changeme", "changeme@2026!"}


def password_rules(password: str) -> dict:
    """Each rule -> bool. Mirrored client-side for the live checklist."""
    return {
        "length": 8 <= len(password) <= 128,
        "upper": bool(re.search(r"[A-Z]", password)),
        "lower": bool(re.search(r"[a-z]", password)),
        "digit": bool(re.search(r"\d", password)),
        "special": bool(re.search(f"[{re.escape(SPECIAL_CHARS)}]", password)),
        "nospace": not re.search(r"\s", password),
    }


def validate_password(password: str, name: str = "", email: str = "") -> list[str]:
    errors: list[str] = []
    if not password:
        return ["كلمة المرور مطلوبة"]
    r = password_rules(password)
    if not r["nospace"]:
        errors.append("كلمة المرور لا يمكن أن تحتوي على مسافات")
    if len(password) < 8:
        errors.append("كلمة المرور يجب أن تكون 8 أحرف على الأقل")
    if len(password) > 128:
        errors.append("كلمة المرور طويلة جداً (الحد الأقصى 128 حرف)")
    if not r["upper"]:
        errors.append("يجب أن تحتوي على حرف كبير واحد على الأقل (A-Z)")
    if not r["lower"]:
        errors.append("يجب أن تحتوي على حرف صغير واحد على الأقل (a-z)")
    if not r["digit"]:
        errors.append("يجب أن تحتوي على رقم واحد على الأقل")
    if not r["special"]:
        errors.append("يجب أن تحتوي على رمز خاص واحد على الأقل (!@#$...)")
    if password.lower() in WEAK:
        errors.append("كلمة المرور شائعة جداً — اختر شيئاً أقوى")
    lowered = password.lower()
    if name and len(name.strip()) >= 3 and name.strip().lower() in lowered:
        errors.append("كلمة المرور لا يمكن أن تحتوي على اسمك")
    if email:
        local = email.split("@")[0].lower()
        if len(local) >= 3 and local in lowered:
            errors.append("كلمة المرور لا يمكن أن تحتوي على بريدك الإلكتروني")
    return errors


def validate_email_format(email: str) -> list[str]:
    if not email:
        return ["البريد الإلكتروني مطلوب"]
    if len(email) > 120:
        return ["البريد الإلكتروني طويل جداً"]
    try:
        validate_email(email, check_deliverability=False)
        return []
    except EmailNotValidError:
        return ["البريد الإلكتروني غير صالح"]


def validate_phone(phone: str) -> list[str]:
    if not phone:
        return []
    if not re.match(r"^\+?[\d\s\-()]{7,30}$", phone):
        return ["رقم الهاتف غير صالح"]
    return []


def validate_name(name: str, label: str = "الاسم", required: bool = True) -> list[str]:
    name = (name or "").strip()
    if not name:
        return [f"{label} مطلوب"] if required else []
    if len(name) < 2:
        return [f"{label} قصير جداً"]
    if len(name) > 120:
        return [f"{label} طويل جداً (الحد الأقصى 120 حرف)"]
    if not re.match(r"^[\w\s'\-\.()،,/\u064B-\u065F\u0670\u0640\u0300-\u036F]+$", name):   # letters, digits, tashkeel
        return [f"{label} يحتوي على رموز غير مسموحة"]
    return []


def clean(value, maxlen: int) -> str:
    """Trim, collapse NULs/control chars, and cap length."""
    s = (value or "")
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s).strip()
    return s[:maxlen]
