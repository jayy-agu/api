from datetime import datetime

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from jose import JWTError

from database import get_db
from security import decode_access_token
import models

bearer_scheme = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> models.User:
    token = credentials.credentials
    try:
        payload = decode_access_token(token)
        if payload.get("type") != "access":
            # Rejects an mfa_pending pre-auth token (or anything else) from
            # ever being used to reach an authenticated endpoint.
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        user_id = int(payload.get("sub"))
    except JWTError:
        # Covers both a malformed/invalid signature AND an expired token
        # (jose raises ExpiredSignatureError, a JWTError subclass, for the
        # latter) so an expired session is always rejected server-side.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired, please log in again")

    # --- Server-side session check -----------------------------------------
    # An access token is only valid if its jti maps to a live (non-revoked)
    # AuthSession row. This is what makes logout and "sign out of all
    # devices" enforceable: revoking the row kills the token immediately,
    # not at its 2-hour expiry. Tokens WITHOUT a jti were issued before
    # session tracking existed; accept them (they still carry exp and die
    # within ACCESS_TOKEN_EXPIRE_MINUTES).
    jti = payload.get("jti")
    if jti:
        session = (
            db.query(models.AuthSession)
            .filter(models.AuthSession.token_jti == jti)
            .first()
        )
        if session is None or session.revoked:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expired or signed out elsewhere",
            )
        # Throttled touch: refresh last_seen_at at most once a minute so a
        # DB write doesn't happen on every single authenticated request.
        now = datetime.utcnow()
        if session.last_seen_at is None or (now - session.last_seen_at).total_seconds() > 60:
            session.last_seen_at = now
            db.commit()

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account is deactivated")
    return user


require_authenticated_user = get_current_user


def require_role(*allowed_roles: str):
    """
    Returns a FastAPI dependency that only allows through users whose role is
    in allowed_roles. Usage: Depends(require_role("ADMIN", "SUPER_ADMIN")).
    Always checks the *server-side* role stored on the authenticated user —
    never trusts anything the client sends.
    """
    def _dependency(current_user: models.User = Depends(get_current_user)) -> models.User:
        if current_user.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient privileges")
        return current_user
    return _dependency


def require_admin(current_user: models.User = Depends(get_current_user)) -> models.User:
    if current_user.role not in (models.ROLE_ADMIN, models.ROLE_SUPER_ADMIN):
        raise HTTPException(status_code=403, detail="Admins only")
    return current_user


def require_super_admin(current_user: models.User = Depends(get_current_user)) -> models.User:
    if current_user.role != models.ROLE_SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Super admins only")
    return current_user