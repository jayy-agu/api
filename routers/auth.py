from typing import List, Optional, Union
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from jose import JWTError

from audit import log_event
from database import get_db
from constants import DEPARTMENTS
import models
import schemas
import mfa
from security import (
    hash_password,
    verify_password,
    create_access_token,
    create_pre_auth_token,
    decode_access_token,
    password_policy_errors,
    MAX_FAILED_LOGIN_ATTEMPTS,
    LOCKOUT_MINUTES,
    MFA_MANDATORY,
)
from deps import get_current_user
from ratelimit import limiter

router = APIRouter(prefix="/auth", tags=["auth"])

GENERIC_LOGIN_ERROR = "Wrong handle or password"
LOCKED_MESSAGE = "Account temporarily locked. Please try again later."

# Second bearer extractor used ONLY to recover the current request's jti for
# session endpoints (get_current_user already guarantees the token is valid).
_bearer = HTTPBearer()


def make_handle(name: str, db: Session) -> str:
    base = "".join(ch for ch in name.strip().split(" ")[0] if ch.isalnum()).lower() or "user"
    handle = base
    i = 1
    while db.query(models.User).filter(models.User.handle == handle).first():
        i += 1
        handle = f"{base}{i}"
    return handle


def _client_meta(request: Request):
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    return ip, ua


def _current_jti(credentials: HTTPAuthorizationCredentials) -> Optional[str]:
    try:
        return decode_access_token(credentials.credentials).get("jti")
    except JWTError:
        return None


def _issue_access_token(db: Session, user: models.User, request: Request) -> str:
    """Mint an access token AND register the server-side session it belongs
    to. Every login path (bootstrap, password-only, MFA enrollment confirm,
    MFA verify) funnels through here so a session can never be forgotten
    by a new code path.

    Also raises a login-anomaly notification when the IP or user-agent
    differs from the user's most recent previous session — the user is the
    best detection layer for "someone has my password"."""
    ip, ua = _client_meta(request)
    token, jti = create_access_token({"sub": str(user.id), "handle": user.handle, "role": user.role})

    prev = (
        db.query(models.AuthSession)
        .filter(models.AuthSession.user_id == user.id)
        .order_by(models.AuthSession.created_at.desc())
        .first()
    )
    if prev and (prev.ip_address != ip or (prev.user_agent or "") != (ua or "")):
        db.add(models.Notification(
            user_id=user.id,
            from_user_id=user.id,
            post_id=None,
            preview=f"New sign-in from {ip or 'an unknown IP'} — was this you?",
            is_mention=False,
        ))

    db.add(models.AuthSession(user_id=user.id, token_jti=jti, ip_address=ip, user_agent=ua))
    db.commit()
    return token


@router.get("/status")
def status_check(db: Session = Depends(get_db)):
    has_users = db.query(models.User).first() is not None
    return {"hasUsers": has_users}


@router.post("/bootstrap", response_model=schemas.Token)
def bootstrap(payload: schemas.UserCreate, request: Request, db: Session = Depends(get_db)):
    if db.query(models.User).first() is not None:
        raise HTTPException(status_code=400, detail="Team already set up. Please log in.")

    policy_errors = password_policy_errors(payload.password)
    if policy_errors:
        raise HTTPException(status_code=400, detail="Password must contain " + ", ".join(policy_errors))
    if payload.department and payload.department not in DEPARTMENTS:
        raise HTTPException(status_code=400, detail="Invalid department")

    # The very first account becomes SUPER_ADMIN (it's setting up the team).
    user = models.User(
        name=payload.name.strip(),
        handle=make_handle(payload.name, db),
        password_hash=hash_password(payload.password),
        is_admin=True,
        role=models.ROLE_SUPER_ADMIN,
        department=payload.department,
        password_changed_at=datetime.utcnow(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = _issue_access_token(db, user, request)
    log_event(db, user.name, "created the team (first admin account)", user_id=user.id, event_type="user_created")
    return {"access_token": token, "user": user}


@router.post("/login", response_model=Union[schemas.Token, schemas.MFAChallengeResponse])
@limiter.limit("5/minute")  # per-IP: blunts credential stuffing & spraying
# NOTE: MFAChallengeResponse now doubles as the "please enroll first"
# response (enrollment_required=True) as well as the normal "enter your
# code" challenge (enrollment_required=False, the pre-existing behavior).
def login(payload: schemas.UserLogin, request: Request, db: Session = Depends(get_db)):
    ip, ua = _client_meta(request)
    handle = payload.handle.strip().lower()
    user = db.query(models.User).filter(models.User.handle == handle).first()
    now = datetime.utcnow()

    # Account-enumeration-safe: whether the handle exists or not, a bad
    # login always looks the same to the client (same message, same status
    # code, no timing signal beyond bcrypt's already-constant-time compare).
    if user and user.locked_until and user.locked_until > now:
        log_event(db, handle, "login rejected: account locked", ok=False,
                  user_id=user.id, event_type="account_locked", ip_address=ip, user_agent=ua)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=LOCKED_MESSAGE)

    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        if user:
            if user.locked_until and user.locked_until <= now:
                user.failed_login_attempts = 0
                user.locked_until = None

            user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
            user.last_failed_login = now
            if user.failed_login_attempts >= MAX_FAILED_LOGIN_ATTEMPTS:
                user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
                db.commit()
                log_event(db, handle, "account locked after repeated failed logins", ok=False,
                          user_id=user.id, event_type="account_locked", ip_address=ip, user_agent=ua)
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=LOCKED_MESSAGE)
            db.commit()

        log_event(db, handle, "failed login attempt", ok=False,
                  user_id=user.id if user else None, event_type="login_failed", ip_address=ip, user_agent=ua)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=GENERIC_LOGIN_ERROR)

    # Password is correct. If MFA is enabled, don't finalize the login yet --
    # issue a short-lived pre-auth token and make the client complete the
    # challenge at /auth/mfa/verify. Lockout counters are NOT reset here;
    # they're only reset once the full login (including MFA) succeeds, so a
    # correct password alone can't be used to "launder" a lockout.
    if user.mfa_enabled:
        log_event(db, user.name, "password verified, awaiting MFA code", user_id=user.id,
                  event_type="mfa_challenge_issued", ip_address=ip, user_agent=ua)
        return {"mfa_required": True, "pre_auth_token": create_pre_auth_token(user.id)}

    # MFA not enabled on this account. If MFA is mandatory (the default),
    # the password alone does NOT earn a real access token -- the user is
    # routed into forced enrollment first, using the exact same pre-auth
    # token mechanism as the normal MFA challenge above.
    if MFA_MANDATORY:
        log_event(db, user.name, "password verified, MFA enrollment required", user_id=user.id,
                  event_type="mfa_enrollment_required", ip_address=ip, user_agent=ua)
        return {
            "mfa_required": True,
            "pre_auth_token": create_pre_auth_token(user.id),
            "enrollment_required": True,
        }

    # MFA not mandatory (MFA_MANDATORY=false, local dev only): finalize the
    # login now, exactly as before this change.
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login = now
    user.last_login_ip = ip
    db.commit()

    log_event(db, user.name, "logged in", user_id=user.id, event_type="login_success", ip_address=ip, user_agent=ua)
    token = _issue_access_token(db, user, request)
    return {"access_token": token, "user": user}


# ---------------------------------------------------------------------------
# MFA: pre-auth-token helper
# ---------------------------------------------------------------------------
def _user_from_pre_auth_token(token: str, db: Session) -> models.User:
    """Decode a pre-auth token (type == 'mfa_pending') and return the user it
    names. Raises 401 for anything else -- expired, malformed, wrong type,
    or a token for a user that no longer exists/is inactive. This is the
    ONLY way the enroll/confirm/verify endpoints below identify a user;
    none of them accept a real Bearer access token, so a fully-authenticated
    session is never required (and, for mandatory MFA, never exists yet)."""
    try:
        payload = decode_access_token(token)
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired, please log in again")
    if payload.get("type") != "mfa_pending":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user = db.query(models.User).filter(models.User.id == int(payload.get("sub"))).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


# ---------------------------------------------------------------------------
# MFA: enrollment (start)
# ---------------------------------------------------------------------------
@router.post("/mfa/enroll", response_model=schemas.MFAEnrollResponse)
def mfa_enroll(payload: schemas.MFAEnrollRequest, db: Session = Depends(get_db)):
    user = _user_from_pre_auth_token(payload.pre_auth_token, db)

    if user.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA is already enabled on this account")

    secret = mfa.generate_totp_secret()
    user.mfa_secret_encrypted = mfa.encrypt_secret(secret)
    db.commit()

    uri = mfa.provisioning_uri(secret, account_name=user.handle)
    qr = mfa.qr_code_data_uri(uri)

    # Never log the secret itself -- only that enrollment started.
    log_event(db, user.name, "started MFA enrollment", user_id=user.id, event_type="mfa_enroll_started")

    return {"secret": secret, "otpauth_uri": uri, "qr_code": qr}


# ---------------------------------------------------------------------------
# MFA: enrollment (confirm + activate)
# ---------------------------------------------------------------------------
@router.post("/mfa/confirm", response_model=schemas.MFAConfirmResponse)
@limiter.limit("10/minute")  # per-IP: a wrong code here is one step from full access
def mfa_confirm(payload: schemas.MFAConfirmRequest, request: Request, db: Session = Depends(get_db)):
    ip, ua = _client_meta(request)
    user = _user_from_pre_auth_token(payload.pre_auth_token, db)

    if user.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA is already enabled on this account")
    if not user.mfa_secret_encrypted:
        raise HTTPException(status_code=400, detail="Start enrollment first")

    secret = mfa.decrypt_secret(user.mfa_secret_encrypted)
    if not mfa.verify_totp_code(secret, payload.code):
        log_event(db, user.name, "MFA enrollment: invalid code", ok=False,
                  user_id=user.id, event_type="mfa_enroll_failed", ip_address=ip, user_agent=ua)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid code")

    # Code verified -- activate MFA and mint one-time recovery codes.
    now = datetime.utcnow()
    user.mfa_enabled = True
    user.mfa_enrolled_at = now

    db.query(models.MFARecoveryCode).filter(models.MFARecoveryCode.user_id == user.id).delete()
    plaintext_codes = mfa.generate_recovery_codes()
    for code in plaintext_codes:
        db.add(models.MFARecoveryCode(user_id=user.id, code_hash=mfa.hash_recovery_code(code), created_at=now))

    # This is the moment the full login (password + MFA) has now succeeded,
    # so -- and only now -- clear lockout counters and issue the real token.
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login = now
    user.last_login_ip = ip
    db.commit()

    log_event(db, user.name, "MFA enabled", user_id=user.id, event_type="mfa_enabled", ip_address=ip, user_agent=ua)
    log_event(db, user.name, "logged in", user_id=user.id, event_type="login_success", ip_address=ip, user_agent=ua)

    token = _issue_access_token(db, user, request)
    return {"ok": True, "recovery_codes": plaintext_codes, "access_token": token, "token_type": "bearer", "user": user}


# ---------------------------------------------------------------------------
# MFA: login challenge (account already enrolled)
# ---------------------------------------------------------------------------
@router.post("/mfa/verify", response_model=schemas.Token)
@limiter.limit("5/minute")  # per-IP: 6-digit TOTP codes must not be brute-forceable
def mfa_verify(payload: schemas.MFAVerifyRequest, request: Request, db: Session = Depends(get_db)):
    ip, ua = _client_meta(request)
    user = _user_from_pre_auth_token(payload.pre_auth_token, db)

    if not user.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA is not enabled on this account")

    verified = False
    used_recovery = False

    if payload.code:
        secret = mfa.decrypt_secret(user.mfa_secret_encrypted)
        verified = mfa.verify_totp_code(secret, payload.code)
    elif payload.recovery_code:
        candidates = db.query(models.MFARecoveryCode).filter(
            models.MFARecoveryCode.user_id == user.id,
            models.MFARecoveryCode.used == False,  # noqa: E712
        ).all()
        for row in candidates:
            if mfa.verify_recovery_code(payload.recovery_code, row.code_hash):
                row.used = True
                row.used_at = datetime.utcnow()
                verified = True
                used_recovery = True
                break
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Provide a code or recovery code")

    if not verified:
        log_event(db, user.name, "MFA verification failed", ok=False,
                  user_id=user.id, event_type="mfa_verify_failed", ip_address=ip, user_agent=ua)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid code")

    now = datetime.utcnow()
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login = now
    user.last_login_ip = ip
    db.commit()

    event_type = "mfa_recovery_code_used" if used_recovery else "mfa_verify_success"
    log_event(db, user.name, "logged in via MFA" + (" (recovery code)" if used_recovery else ""),
              user_id=user.id, event_type=event_type, ip_address=ip, user_agent=ua)
    log_event(db, user.name, "logged in", user_id=user.id, event_type="login_success", ip_address=ip, user_agent=ua)

    token = _issue_access_token(db, user, request)
    return {"access_token": token, "user": user}


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------
@router.get("/sessions", response_model=List[schemas.SessionOut])
@limiter.limit("30/minute")
def list_sessions(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """All live sessions for the current user, newest first, with the
    session making this request flagged as `current`."""
    jti = _current_jti(credentials)
    rows = (
        db.query(models.AuthSession)
        .filter(
            models.AuthSession.user_id == current_user.id,
            models.AuthSession.revoked == False,  # noqa: E712
        )
        .order_by(models.AuthSession.created_at.desc())
        .all()
    )
    return [
        schemas.SessionOut(
            id=s.id,
            ip_address=s.ip_address,
            user_agent=s.user_agent,
            created_at=s.created_at,
            last_seen_at=s.last_seen_at,
            current=(jti is not None and s.token_jti == jti),
        )
        for s in rows
    ]


@router.post("/sessions/{session_id}/revoke")
def revoke_session(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Revoke one session (sign that device out). Users can only revoke
    their own; admins may revoke anyone's."""
    s = db.query(models.AuthSession).filter(models.AuthSession.id == session_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    if s.user_id != current_user.id and current_user.role not in (models.ROLE_ADMIN, models.ROLE_SUPER_ADMIN):
        raise HTTPException(status_code=403, detail="Not your session")
    s.revoked = True
    db.commit()
    log_event(db, current_user.name, "signed out a session", user_id=current_user.id,
              event_type="session_revoked",
              ip_address=request.client.host if request.client else None)
    return {"ok": True}


@router.post("/sessions/revoke-all")
def revoke_all_other_sessions(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Kill every session except the one making this request — the
    'sign out of all other devices' button for a suspected compromise."""
    jti = _current_jti(credentials)
    (
        db.query(models.AuthSession)
        .filter(
            models.AuthSession.user_id == current_user.id,
            models.AuthSession.revoked == False,  # noqa: E712
            models.AuthSession.token_jti != jti,
        )
        .update({"revoked": True}, synchronize_session=False)
    )
    db.commit()
    log_event(db, current_user.name, "signed out of all other sessions", user_id=current_user.id,
              event_type="sessions_revoked_all",
              ip_address=request.client.host if request.client else None)
    return {"ok": True}


@router.post("/logout")
def logout(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """REAL, server-side logout: revokes the session behind the presented
    token so the token dies immediately — instead of lingering, fully
    valid, until its 2-hour exp because the client only cleared
    localStorage."""
    jti = _current_jti(credentials)
    if jti:
        s = db.query(models.AuthSession).filter(models.AuthSession.token_jti == jti).first()
        if s and not s.revoked:
            s.revoked = True
            db.commit()
    log_event(db, current_user.name, "logged out", user_id=current_user.id,
              event_type="logout",
              ip_address=request.client.host if request.client else None)
    return {"ok": True}


@router.get("/me", response_model=schemas.UserOut)
def me(current_user: models.User = Depends(get_current_user)):
    return current_user