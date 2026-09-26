from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base

# Role tiers, ordered from least to most privileged.
ROLE_USER = "USER"
ROLE_STAFF = "STAFF"
ROLE_ADMIN = "ADMIN"
ROLE_SUPER_ADMIN = "SUPER_ADMIN"
ALL_ROLES = [ROLE_USER, ROLE_STAFF, ROLE_ADMIN, ROLE_SUPER_ADMIN]


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    handle = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    is_admin = Column(Boolean, default=False)
    # New: explicit role tier. Kept alongside is_admin (not replacing it) so
    # existing code paths that check is_admin keep working unmodified.
    role = Column(String, nullable=False, default=ROLE_USER)
    department = Column(String, nullable=True)
    profile_image = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # --- Login attempt protection ---
    failed_login_attempts = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime, nullable=True)
    last_failed_login = Column(DateTime, nullable=True)
    last_login = Column(DateTime, nullable=True)
    last_login_ip = Column(String, nullable=True)

    # --- Password policy bookkeeping ---
    password_changed_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)

    # --- MFA (TOTP) ---
    mfa_enabled = Column(Boolean, nullable=False, default=False)
    # Fernet-encrypted base32 TOTP secret. Set as soon as enrollment starts;
    # mfa_enabled only flips to True once the user confirms a valid code.
    mfa_secret_encrypted = Column(String, nullable=True)
    mfa_enrolled_at = Column(DateTime, nullable=True)


class Post(Base):
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, index=True)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    tier = Column(String, nullable=False)
    message = Column(String, nullable=False)
    department = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    attachment_url = Column(String, nullable=True)
    attachment_filename = Column(String, nullable=True)
    attachment_type = Column(String, nullable=True)
    # Private/direct message: set only for a DM created via POST /posts/dm.
    # NULL for every ordinary department/general post. See migrate.py -- this
    # column must exist on an already-created posts table before the app can
    # serve GET /posts/ (every query filters on it).
    dm_recipient_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    author = relationship("User", foreign_keys=[author_id])
    dm_recipient = relationship("User", foreign_keys=[dm_recipient_id])
    comments = relationship("Comment", order_by="Comment.created_at.desc()", cascade="all, delete-orphan")


class Comment(Base):
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True, index=True)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=False)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    author = relationship("User")


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    from_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=True)
    preview = Column(String, nullable=True)
    is_read = Column(Boolean, default=False)
    is_mention = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", foreign_keys=[user_id])
    from_user = relationship("User", foreign_keys=[from_user_id])
    post = relationship("Post")

    @property
    def post_tier(self):
        return self.post.tier if self.post else None


class AccessLog(Base):
    __tablename__ = "access_log"

    id = Column(Integer, primary_key=True, index=True)
    actor_name = Column(String, nullable=False)
    event = Column(String, nullable=False)
    ok = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # New optional fields for richer security auditing. Nullable so existing
    # rows (and existing log_event() calls that don't pass them) still work.
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    event_type = Column(String, nullable=True)
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)


class MFARecoveryCode(Base):
    __tablename__ = "mfa_recovery_codes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    code_hash = Column(String, nullable=False)
    used = Column(Boolean, nullable=False, default=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)