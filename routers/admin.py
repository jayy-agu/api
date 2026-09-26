from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime, timedelta

from database import get_db
from constants import DEPARTMENTS
import models
import schemas
from deps import require_admin, require_super_admin
from audit import log_event
from security import hash_password, password_policy_errors

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/stats", response_model=schemas.StatsOut)
def get_stats(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin),
):
    week_ago = datetime.utcnow() - timedelta(days=7)
    return {
        "total_staff": db.query(models.User).count(),
        "posts_this_week": db.query(models.Post).filter(models.Post.created_at >= week_ago).count(),
        "urgent_this_week": db.query(models.Post).filter(
            models.Post.created_at >= week_ago, models.Post.tier == "urgent"
        ).count(),
        "unread_notifications_total": db.query(models.Notification).filter(
            models.Notification.is_read == False
        ).count(),
    }


@router.get("/access-log", response_model=List[schemas.AccessLogOut])
def get_access_log(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin),
):
    return (
        db.query(models.AccessLog)
        .order_by(models.AccessLog.created_at.desc())
        .limit(100)
        .all()
    )


@router.delete("/users/{user_id}")
def remove_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin),
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="You can't remove your own account")
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Not found")
    if user.role == models.ROLE_SUPER_ADMIN and current_user.role != models.ROLE_SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Only a super admin can remove a super admin")
    log_event(db, current_user.name, f"removed teammate {user.name}", user_id=current_user.id, event_type="user_deleted")
    db.delete(user)
    db.commit()
    return {"ok": True}


@router.put("/users/{user_id}/role")
def update_user_role(
    user_id: int,
    payload: schemas.UserUpdateRole,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_super_admin),
):
    if payload.role not in models.ALL_ROLES:
        raise HTTPException(status_code=400, detail="Invalid role")
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Not found")
    old_role = user.role
    user.role = payload.role
    user.is_admin = payload.role in (models.ROLE_ADMIN, models.ROLE_SUPER_ADMIN)
    db.commit()
    log_event(db, current_user.name, f"changed {user.name}'s role from {old_role} to {payload.role}",
              user_id=current_user.id, event_type="role_changed")
    return {"ok": True}


@router.put("/users/{user_id}/department")
def update_user_department(
    user_id: int,
    payload: schemas.UserUpdateDept,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin),
):
    if payload.department and payload.department not in DEPARTMENTS:
        raise HTTPException(status_code=400, detail="Invalid department")
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Not found")
    if user.role == models.ROLE_SUPER_ADMIN and current_user.role != models.ROLE_SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Only a super admin can modify a super admin")
    old_dept = user.department
    user.department = payload.department
    db.commit()
    log_event(db, current_user.name, f"changed {user.name}'s department from {old_dept or 'None'} to {payload.department or 'None'}")
    return {"ok": True}


@router.put("/users/{user_id}/reset-password")
def reset_user_password(
    user_id: int,
    payload: schemas.PasswordReset,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin),
):
    policy_errors = password_policy_errors(payload.new_password)
    if policy_errors:
        raise HTTPException(status_code=400, detail="Password must contain " + ", ".join(policy_errors))
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Not found")
    if user.role == models.ROLE_SUPER_ADMIN and current_user.role != models.ROLE_SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Only a super admin can reset a super admin's password")
    user.password_hash = hash_password(payload.new_password)
    user.failed_login_attempts = 0
    user.locked_until = None
    user.password_changed_at = datetime.utcnow()
    db.commit()
    log_event(db, current_user.name, f"reset password for {user.name}", user_id=current_user.id, event_type="password_change")
    return {"ok": True}


@router.get("/users", response_model=List[schemas.UserOut])
def list_all_users(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin),
):
    return db.query(models.User).order_by(models.User.name).all()


@router.post("/users/{user_id}/mfa-reset")
def reset_user_mfa(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin),
):
    """
    Clears a user's MFA enrollment (e.g. they lost their authenticator
    device). There is intentionally no self-service "disable my MFA"
    endpoint anywhere in this app -- under mandatory MFA, only an admin can
    turn it back off for someone, and doing so simply routes that user back
    through forced enrollment on their next login (mfa_enabled=False plus
    MFA_MANDATORY=True in security.py means /auth/login will issue them a
    fresh pre-auth token with enrollment_required=True, exactly like a
    brand-new account). This does not touch the password, RBAC role, lockout
    state, or any other field.
    """
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Not found")
    if user.role == models.ROLE_SUPER_ADMIN and current_user.role != models.ROLE_SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Only a super admin can reset a super admin's MFA")

    user.mfa_enabled = False
    user.mfa_secret_encrypted = None
    user.mfa_enrolled_at = None
    db.query(models.MFARecoveryCode).filter(models.MFARecoveryCode.user_id == user.id).delete()
    db.commit()

    log_event(db, current_user.name, f"reset MFA for {user.name}", user_id=current_user.id, event_type="mfa_reset")
    return {"ok": True}