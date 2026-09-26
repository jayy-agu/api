import os
import re
import uuid
import bcrypt
from jose import jwt
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY is not set. Copy .env.example to .env and set a real "
        "SECRET_KEY (e.g. `python -c \"import secrets; print(secrets.token_hex(32))\"`)."
    )

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "120"))
PRE_AUTH_TOKEN_EXPIRE_MINUTES = int(os.environ.get("PRE_AUTH_TOKEN_EXPIRE_MINUTES", "5"))
MAX_FAILED_LOGIN_ATTEMPTS = int(os.environ.get("MAX_FAILED_LOGIN_ATTEMPTS", "3"))
LOCKOUT_MINUTES = int(os.environ.get("LOCKOUT_MINUTES", "30"))
MFA_MANDATORY = os.environ.get("MFA_MANDATORY", "true").strip().lower() != "false"


def hash_password(password: str) -> str:
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def create_access_token(data: dict) -> tuple[str, str]:
    """Mint an access token. Returns (token, jti). The jti (JWT ID) is a
    unique identifier the server records in auth_sessions; revoking that
    row kills the token instantly, which is what makes server-side logout
    and 'sign out of all devices' possible."""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    jti = uuid.uuid4().hex
    to_encode.update({"exp": expire, "type": "access", "jti": jti})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM), jti


def create_pre_auth_token(user_id: int) -> str:
    """Issued after a correct password when MFA is still required. Cannot be
    used to hit any authenticated endpoint (deps.get_current_user only
    accepts type=="access"); it's only valid at POST /auth/mfa/verify.
    Deliberately session-less: no AuthSession is created for it."""
    expire = datetime.utcnow() + timedelta(minutes=PRE_AUTH_TOKEN_EXPIRE_MINUTES)
    to_encode = {"sub": str(user_id), "type": "mfa_pending", "exp": expire}
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    # jose.jwt.decode already verifies "exp" and raises jose.JWTError
    # (specifically ExpiredSignatureError, a JWTError subclass) if expired.
    # deps.py already catches JWTError and returns 401, so expiry is
    # enforced server-side regardless of what the frontend does.
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


# --- Password policy ---
_PASSWORD_RULES = [
    (lambda p: len(p) >= 10, "at least 10 characters"),
    (lambda p: re.search(r"[A-Z]", p) is not None, "an uppercase letter"),
    (lambda p: re.search(r"[a-z]", p) is not None, "a lowercase letter"),
    (lambda p: re.search(r"[0-9]", p) is not None, "a number"),
    (lambda p: re.search(r"[^A-Za-z0-9]", p) is not None, "a special character"),
]


def password_policy_errors(password: str) -> list[str]:
    """Return a list of human-readable unmet requirements (empty if valid)."""
    return [msg for check, msg in _PASSWORD_RULES if not check(password)]