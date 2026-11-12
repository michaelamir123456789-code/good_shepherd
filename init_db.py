"""
init_db.py — One-time database bootstrap.

    python init_db.py

Creates the tables and the first leader (admin) account.
If ADMIN_PASSWORD is not set in .env, a strong random one is generated and shown ONCE.
"""
import os
import secrets
import string
from dotenv import load_dotenv
load_dotenv()

from app import create_app
from extensions import db
from models import User, Settings
from utils.validators import validate_password


def random_password(n: int = 16) -> str:
    pools = [string.ascii_uppercase, string.ascii_lowercase, string.digits, "!@#$%^&*"]
    chars = [secrets.choice(p) for p in pools]
    allchars = "".join(pools)
    chars += [secrets.choice(allchars) for _ in range(n - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def seed():
    app = create_app()
    with app.app_context():
        db.create_all()
        Settings.get()
        print("[OK] Tables ready")

        email = (os.environ.get("ADMIN_EMAIL") or "admin@church.com").strip().lower()
        if User.query.filter_by(email=email).first():
            print(f"[i] Leader account already exists: {email}")
            return

        password = os.environ.get("ADMIN_PASSWORD") or ""
        generated = False
        if not password:
            password, generated = random_password(), True
        errors = validate_password(password, name="Leader", email=email)
        if errors:
            print("[X] ADMIN_PASSWORD doesn't meet the password rules:")
            for e in errors:
                print("    -", e)
            return

        admin = User(name_ar="الأب الراعي", name_en="Leader", email=email,
                     role="admin", language="ar", must_change_password=True)
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()
        print("=" * 56)
        print("[OK] Leader account created")
        print(f"     E-mail: {email}")
        if generated:
            print(f"     Temporary password (shown once): {password}")
        else:
            print("     Password: the ADMIN_PASSWORD from your .env")
        print("     You must change it at first sign-in.")
        print("     Then DELETE ADMIN_PASSWORD from .env.")
        print("=" * 56)


if __name__ == "__main__":
    seed()
