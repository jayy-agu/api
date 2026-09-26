from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from jose import JWTError

import models
import schemas
from database import get_db
from deps import get_current_user
from audit import log_event
from security import (
    decode_access_token,
    create_access_token,
    verify_password,
    MAX_FAILED_LOGIN_ATTEMPTS,
    LOCKOUT_MINUTES,
)
import mfa as mfa_lib

router = APIRouter(prefix="/auth/mfa", tags=["mfa"])

LOCKED_MESSAGE = "Account temporarily locked. Please try again later."


def _client_meta(request: Request):
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    return ip, ua


@router.get("/status", response_model=schemas.MFAStatusResponse)
def mfa_status(current_user: models.User = Depends(get_current_user)):
    return {"mfa_enabled": current_user.mfa_enabled}


@router.post("/enroll", response_model=schemas.MFAEnrollResponse)
def enroll(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if current_user.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA is already enabled. Disable it first to re-enroll.")

    secret = mfa_lib.generate_totp_secret()
    current_user.mfa_secret_encrypted = mfa_lib.encrypt_secret(secret)
    db.commit()

    uri = mfa_lib.provisioning_uri(secret, current_user.handle)
    return {
        "secret": secret,
        "otpauth_uri": uri,
        "qr_code": mfa_lib.qr_code_data_uri(uri),
    }


@router.post("/enroll/confirm", response_model=schemas.MFAConfirmResponse)
def confirm_enroll(
    payload: schemas.MFAConfirmRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if current_user.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA is already enabled.")
    if not current_user.mfa_secret_encrypted:
        raise HTTPException(status_code=400, detail="Start enrollment first at /auth/mfa/enroll.")

    try:
        secret = mfa_lib.decrypt_secret(current_user.mfa_secret_encrypted)
    except ValueError:
        log_event(db, current_user.name, "MFA secret undecryptable during enrollment (key mismatch?)", ok=False,
                  user_id=current_user.id, event_type="mfa_verify_failed")
        raise HTTPException(
            status_code=400,
            detail="Something went wrong generating your MFA secret. Please restart enrollment.",
        )
    if not mfa_lib.verify_totp_code(secret, payload.code):
        log_event(db, current_user.name, "MFA enrollment code invalid", ok=False,
                  user_id=current_user.id, event_type="mfa_verify_failed")
        raise HTTPException(status_code=400, detail="Invalid authentication code")

    current_user.mfa_enabled = True
    current_user.mfa_enrolled_at = datetime.utcnow()

    # Replace any old recovery codes (e.g. leftover from a prior enrollment)
    # with a fresh set, shown to the user exactly once, right now.
    db.query(models.MFARecoveryCode).filter(models.MFARecoveryCode.user_id == current_user.id).delete()
    plain_codes = mfa_lib.generate_recovery_codes()
    for code in plain_codes:
        db.add(models.MFARecoveryCode(user_id=current_user.id, code_hash=mfa_lib.hash_recovery_code(code)))
    db.commit()

    log_event(db, current_user.name, "MFA enabled", user_id=current_user.id, event_type="mfa_enrolled")
    return {"ok": True, "recovery_codes": plain_codes}


@router.post("/disable")
def disable(
    payload: schemas.MFADisableRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    # Require the current password again before turning off a security
    # feature -- a stolen unlocked session alone shouldn't be enough.
    if not verify_password(payload.password, current_user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect password")

    current_user.mfa_enabled = False
    current_user.mfa_secret_encrypted = None
    current_user.mfa_enrolled_at = None
    db.query(models.MFARecoveryCode).filter(models.MFARecoveryCode.user_id == current_user.id).delete()
    db.commit()

    log_event(db, current_user.name, "MFA disabled", user_id=current_user.id, event_type="mfa_disabled")
    return {"ok": True}


@router.post("/recovery-codes/regenerate", response_model=schemas.MFAConfirmResponse)
def regenerate_recovery_codes(
    payload: schemas.MFADisableRequest,  # reuses the {password} shape
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if not current_user.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA is not enabled")
    if not verify_password(payload.password, current_user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect password")

    db.query(models.MFARecoveryCode).filter(models.MFARecoveryCode.user_id == current_user.id).delete()
    plain_codes = mfa_lib.generate_recovery_codes()
    for code in plain_codes:
        db.add(models.MFARecoveryCode(user_id=current_user.id, code_hash=mfa_lib.hash_recovery_code(code)))
    db.commit()

    log_event(db, current_user.name, "MFA recovery codes regenerated", user_id=current_user.id, event_type="mfa_recovery_regenerated")
    return {"ok": True, "recovery_codes": plain_codes}


@router.post("/verify", response_model=schemas.Token)
def verify_mfa(payload: schemas.MFAVerifyRequest, request: Request, db: Session = Depends(get_db)):
    """
    Completes a login that returned {"mfa_required": true}. Not protected by
    get_current_user -- the caller isn't logged in yet -- instead it's gated
    entirely by the short-lived pre_auth_token from /auth/login.
    """
    ip, ua = _client_meta(request)

    try:
        token_payload = decode_access_token(payload.pre_auth_token)
        if token_payload.get("type") != "mfa_pending":
            raise HTTPException(status_code=401, detail="Invalid or expired session, please log in again")
        user_id = int(token_payload.get("sub"))
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired session, please log in again")

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user or not user.mfa_enabled:
        raise HTTPException(status_code=401, detail="Invalid or expired session, please log in again")

    now = datetime.utcnow()
    if user.locked_until and user.locked_until > now:
        log_event(db, user.name, "MFA rejected: account locked", ok=False,
                  user_id=user.id, event_type="account_locked", ip_address=ip, user_agent=ua)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=LOCKED_MESSAGE)

    verified = False
    used_recovery_code_row = None

    if payload.code:
        try:
            secret = mfa_lib.decrypt_secret(user.mfa_secret_encrypted)
        except ValueError:
            # The stored secret can't be decrypted with the server's current
            # MFA_ENCRYPTION_KEY (key rotated, or the row is corrupted).
            # Previously this raised straight through as an unhandled 500,
            # which the frontend shows as a blank "Something went wrong" --
            # give a real, actionable error instead.
            log_event(db, user.name, "MFA secret undecryptable (key mismatch?)", ok=False,
                      user_id=user.id, event_type="mfa_verify_failed", ip_address=ip, user_agent=ua)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Your MFA setup could not be verified. Please ask an admin to reset your MFA so you can re-enroll.",
            )
        verified = mfa_lib.verify_totp_code(secret, payload.code)
    elif payload.recovery_code:
        candidates = db.query(models.MFARecoveryCode).filter(
            models.MFARecoveryCode.user_id == user.id,
            models.MFARecoveryCode.used == False,
        ).all()
        for row in candidates:
            if mfa_lib.verify_recovery_code(payload.recovery_code, row.code_hash):
                verified = True
                used_recovery_code_row = row
                break
    else:
        raise HTTPException(status_code=400, detail="Provide either code or recovery_code")

    if not verified:
        # Wrong MFA codes count against the SAME lockout counter as wrong
        # passwords -- otherwise an attacker who already has the password
        # could brute-force the 6-digit code with no rate limit at all.
        if user.locked_until and user.locked_until <= now:
            user.failed_login_attempts = 0
            user.locked_until = None
        user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
        user.last_failed_login = now
        locked_now = user.failed_login_attempts >= MAX_FAILED_LOGIN_ATTEMPTS
        if locked_now:
            user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
        db.commit()

        event = "mfa_verification_failed" if payload.code else "mfa_recovery_code_invalid"
        log_event(db, user.name, "invalid MFA attempt" + (" (account now locked)" if locked_now else ""),
                  ok=False, user_id=user.id, event_type=event, ip_address=ip, user_agent=ua)
        if locked_now:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=LOCKED_MESSAGE)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication code")

    if used_recovery_code_row:
        used_recovery_code_row.used = True
        used_recovery_code_row.used_at = now
        log_event(db, user.name, "logged in using a recovery code", user_id=user.id,
                  event_type="mfa_recovery_code_used", ip_address=ip, user_agent=ua)

    # Full login now finalized.
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login = now
    user.last_login_ip = ip
    db.commit()

    log_event(db, user.name, "logged in", user_id=user.id, event_type="login_success", ip_address=ip, user_agent=ua)
    token = create_access_token({"sub": str(user.id), "handle": user.handle, "role": user.role})
    return {"access_token": token, "user": user}
