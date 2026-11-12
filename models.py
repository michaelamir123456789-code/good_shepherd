"""
models.py — Database schema for الراعي الصالح.

(The schema is identical to the previous version, so existing databases keep working.)
"""
import hashlib
from datetime import datetime, date, timedelta
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import Index, UniqueConstraint
from extensions import db


class_servants = db.Table(
    "class_servants",
    db.Column("class_id", db.Integer,
              db.ForeignKey("classes.id", ondelete="CASCADE"), primary_key=True),
    db.Column("user_id", db.Integer,
              db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
)


# Which grades a "grade admin" is responsible for
grade_admins = db.Table(
    "grade_admins",
    db.Column("user_id", db.Integer,
              db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    db.Column("grade_id", db.Integer,
              db.ForeignKey("grades.id", ondelete="CASCADE"), primary_key=True),
)

ROLE_ADMIN = "admin"            # service leader: full access, manages admins
ROLE_GRADE_ADMIN = "grade_admin"  # sees & manages only the grades assigned to him/her
ROLE_SERVANT = "servant"


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name_ar = db.Column(db.String(120), nullable=False)
    name_en = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(30))
    username = db.Column(db.String(40), unique=True, index=True)   # optional login name

    # Self-registration: a new servant is "pending" until an admin approves
    approval_status = db.Column(db.String(10), nullable=False, default="approved",
                                server_default="approved")
    requested_grade_id = db.Column(db.Integer)      # grade the servant asked to join
    requested_admin_id = db.Column(db.Integer)      # admin the request was addressed to
    requested_at = db.Column(db.DateTime)

    password_hash = db.Column(db.String(255), nullable=False)
    failed_login_count = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime)
    last_login_at = db.Column(db.DateTime)
    last_login_ip = db.Column(db.String(45))
    must_change_password = db.Column(db.Boolean, default=False, nullable=False)

    role = db.Column(db.String(20), nullable=False, default="servant")
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    language = db.Column(db.String(5), nullable=False, default="ar")

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    notes_written = db.relationship("Note", back_populates="author",
                                    lazy="dynamic", foreign_keys="Note.author_id")
    attendance_taken = db.relationship("AttendanceRecord",
                                       back_populates="taken_by", lazy="dynamic")
    visits_made = db.relationship("Visit", back_populates="servant",
                                  lazy="dynamic")
    classes = db.relationship("Class", secondary=class_servants,
                              back_populates="servants")
    managed_grades = db.relationship("Grade", secondary=grade_admins,
                                     back_populates="admins")
    audit_logs = db.relationship("AuditLog", back_populates="user",
                                 lazy="dynamic")

    # ---- passwords ------------------------------------------------------
    def set_password(self, raw_password: str) -> None:
        self.password_hash = generate_password_hash(
            raw_password, method="pbkdf2:sha256:600000")

    def check_password(self, raw_password: str) -> bool:
        return check_password_hash(self.password_hash, raw_password)

    # ---- session id: changes whenever the password changes -------------
    # This logs the user out everywhere after a password change / reset.
    def _pw_stamp(self) -> str:
        return hashlib.sha256(
            (self.password_hash or "").encode("utf-8")).hexdigest()[:16]

    def get_id(self) -> str:
        return f"{self.id}.{self._pw_stamp()}"

    # ---- helpers --------------------------------------------------------
    @property
    def is_admin(self) -> bool:
        """Full-access service leader."""
        return self.role == ROLE_ADMIN

    @property
    def is_grade_admin(self) -> bool:
        return self.role == ROLE_GRADE_ADMIN

    @property
    def is_manager(self) -> bool:
        """Leader OR grade admin (anyone who manages kids/classes)."""
        return self.role in (ROLE_ADMIN, ROLE_GRADE_ADMIN)

    @property
    def is_pending(self) -> bool:
        return self.approval_status == "pending"

    @property
    def display_email(self) -> str:
        """The real e-mail, or '' for accounts that have none (hidden placeholder)."""
        from utils.identity import is_placeholder_email
        return "" if is_placeholder_email(self.email) else (self.email or "")

    @property
    def login_label(self) -> str:
        """What this person types to sign in: username · phone · e-mail."""
        return " · ".join(x for x in (self.username, self.phone, self.display_email) if x)

    @property
    def managed_grade_ids(self) -> list:
        return [g.id for g in self.managed_grades]

    def display_name(self, lang: str = "ar") -> str:
        return (self.name_ar if lang == "ar" else self.name_en) or self.name_ar

    @property
    def initials(self) -> str:
        parts = (self.name_ar or "?").split()
        return "".join(p[0] for p in parts[:2]) or "?"

    @property
    def is_locked_out(self) -> bool:
        return bool(self.locked_until and self.locked_until > datetime.utcnow())

    def register_failed_login(self, max_attempts: int, lockout_minutes: int):
        self.failed_login_count = (self.failed_login_count or 0) + 1
        if self.failed_login_count >= max_attempts:
            self.locked_until = datetime.utcnow() + timedelta(minutes=lockout_minutes)
            self.failed_login_count = 0

    def register_successful_login(self, ip: str | None = None):
        self.failed_login_count = 0
        self.locked_until = None
        self.last_login_at = datetime.utcnow()
        if ip:
            self.last_login_ip = ip

    def __repr__(self):
        return f"<User {self.email} ({self.role})>"


class Grade(db.Model):
    __tablename__ = "grades"

    id = db.Column(db.Integer, primary_key=True)
    name_ar = db.Column(db.String(120), nullable=False)
    name_en = db.Column(db.String(120), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    # Alert after this many consecutive absences (empty = use the global default)
    absence_weeks = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    classes = db.relationship("Class", back_populates="grade", lazy="dynamic",
                              cascade="all, delete-orphan")
    admins = db.relationship("User", secondary=grade_admins,
                             back_populates="managed_grades")

    def display_name(self, lang: str = "ar") -> str:
        return (self.name_ar if lang == "ar" else self.name_en) or self.name_ar


class Class(db.Model):
    __tablename__ = "classes"

    id = db.Column(db.Integer, primary_key=True)
    name_ar = db.Column(db.String(120), nullable=False)
    name_en = db.Column(db.String(120), nullable=False)
    age_group = db.Column(db.String(50))
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    grade_id = db.Column(db.Integer,
                         db.ForeignKey("grades.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    grade = db.relationship("Grade", back_populates="classes")

    kids = db.relationship("Kid", back_populates="klass", lazy="dynamic",
                           cascade="all, delete-orphan")
    servants = db.relationship("User", secondary=class_servants,
                               back_populates="classes")

    def display_name(self, lang: str = "ar") -> str:
        return (self.name_ar if lang == "ar" else self.name_en) or self.name_ar


class Kid(db.Model):
    __tablename__ = "kids"

    id = db.Column(db.Integer, primary_key=True)
    name_ar = db.Column(db.String(120), nullable=False)
    name_en = db.Column(db.String(120), nullable=False)
    birth_date = db.Column(db.Date)
    gender = db.Column(db.String(10))

    mother_name = db.Column(db.String(120))
    kid_phone = db.Column(db.String(30))
    father_phone = db.Column(db.String(30))
    mother_phone = db.Column(db.String(30))
    school = db.Column(db.String(120))
    siblings = db.Column(db.String(500))            # brothers / sisters (free text)
    confession_father = db.Column(db.String(120))   # أب الاعتراف

    address = db.Column(db.String(255))
    area = db.Column(db.String(80), index=True)
    general_notes = db.Column(db.Text)

    photo_filename = db.Column(db.String(64))

    class_id = db.Column(db.Integer,
                         db.ForeignKey("classes.id", ondelete="SET NULL"),
                         index=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    klass = db.relationship("Class", back_populates="kids")
    attendance = db.relationship("AttendanceRecord", back_populates="kid",
                                 lazy="dynamic", cascade="all, delete-orphan")
    notes = db.relationship("Note", back_populates="kid", lazy="dynamic",
                            cascade="all, delete-orphan")
    visits = db.relationship("Visit", back_populates="kid", lazy="dynamic",
                             cascade="all, delete-orphan")

    def display_name(self, lang: str = "ar") -> str:
        return (self.name_ar if lang == "ar" else self.name_en) or self.name_ar

    @property
    def initials(self) -> str:
        parts = (self.name_ar or "?").split()
        return "".join(p[0] for p in parts[:2]) or "?"

    @property
    def age(self):
        if not self.birth_date:
            return None
        today = date.today()
        return today.year - self.birth_date.year - (
            (today.month, today.day) < (self.birth_date.month, self.birth_date.day))

    @property
    def primary_phone(self):
        return self.father_phone or self.mother_phone or self.kid_phone


class AttendanceRecord(db.Model):
    __tablename__ = "attendance_records"
    __table_args__ = (
        UniqueConstraint("kid_id", "date", name="uq_kid_date"),
        Index("ix_attendance_date", "date"),
    )

    id = db.Column(db.Integer, primary_key=True)
    kid_id = db.Column(db.Integer,
                       db.ForeignKey("kids.id", ondelete="CASCADE"),
                       nullable=False)
    date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(10), nullable=False, default="present")
    excuse_note = db.Column(db.String(255))
    taken_by_id = db.Column(db.Integer,
                            db.ForeignKey("users.id", ondelete="SET NULL"))
    taken_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    kid = db.relationship("Kid", back_populates="attendance")
    taken_by = db.relationship("User", back_populates="attendance_taken")


class AttendanceSession(db.Model):
    __tablename__ = "attendance_sessions"
    __table_args__ = (
        UniqueConstraint("class_id", "date", name="uq_class_date_session"),
    )

    id = db.Column(db.Integer, primary_key=True)
    class_id = db.Column(db.Integer,
                         db.ForeignKey("classes.id", ondelete="CASCADE"),
                         nullable=False)
    date = db.Column(db.Date, nullable=False)
    locked_by_id = db.Column(db.Integer,
                             db.ForeignKey("users.id", ondelete="SET NULL"))
    locked_at = db.Column(db.DateTime)

    locked_by = db.relationship("User", foreign_keys=[locked_by_id])
    klass = db.relationship("Class")

    def is_locked(self, timeout_minutes: int = 15) -> bool:
        if not self.locked_by_id or not self.locked_at:
            return False
        return (datetime.utcnow() - self.locked_at
                < timedelta(minutes=timeout_minutes))

    def lock(self, user_id: int) -> None:
        self.locked_by_id = user_id
        self.locked_at = datetime.utcnow()

    def unlock(self) -> None:
        self.locked_by_id = None
        self.locked_at = None


class Note(db.Model):
    __tablename__ = "notes"
    __table_args__ = (Index("ix_notes_kid", "kid_id"),)

    NOTE_CATEGORIES = ("general", "behavior", "health", "spiritual")

    id = db.Column(db.Integer, primary_key=True)
    kid_id = db.Column(db.Integer,
                       db.ForeignKey("kids.id", ondelete="CASCADE"),
                       nullable=False)
    author_id = db.Column(db.Integer,
                          db.ForeignKey("users.id", ondelete="SET NULL"))
    content = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    kid = db.relationship("Kid", back_populates="notes")
    author = db.relationship("User", back_populates="notes_written")


class Visit(db.Model):
    __tablename__ = "visits"
    __table_args__ = (Index("ix_visits_kid", "kid_id"),)

    VISIT_TYPES = ("home", "phone", "church")

    id = db.Column(db.Integer, primary_key=True)
    kid_id = db.Column(db.Integer,
                       db.ForeignKey("kids.id", ondelete="CASCADE"),
                       nullable=False)
    servant_id = db.Column(db.Integer,
                           db.ForeignKey("users.id", ondelete="SET NULL"))
    visit_date = db.Column(db.Date, nullable=False, default=date.today)
    visit_type = db.Column(db.String(30), default="home")
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    kid = db.relationship("Kid", back_populates="visits")
    servant = db.relationship("User", back_populates="visits_made")


class Settings(db.Model):
    __tablename__ = "settings"

    id = db.Column(db.Integer, primary_key=True)
    absence_threshold = db.Column(db.Integer, default=3, nullable=False)
    attendance_day = db.Column(db.String(10), default="friday", nullable=False)
    church_name_ar = db.Column(db.String(120), default="الراعي الصالح")
    church_name_en = db.Column(db.String(120), default="The Good Shepherd")
    signup_open = db.Column(db.Boolean, nullable=False, default=True, server_default="1")
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    @classmethod
    def get(cls):
        s = cls.query.first()
        if not s:
            s = cls()
            db.session.add(s)
            db.session.commit()
        return s


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_created", "created_at"),
        Index("ix_audit_action", "action"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    action = db.Column(db.String(80), nullable=False)
    target = db.Column(db.String(120))
    ip_address = db.Column(db.String(45))
    user_agent = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", back_populates="audit_logs")

    @classmethod
    def log(cls, action, user_id=None, target=None, ip=None, user_agent=None):
        entry = cls(
            action=action, user_id=user_id, target=target, ip_address=ip,
            user_agent=(user_agent or "")[:255] or None,
        )
        db.session.add(entry)
