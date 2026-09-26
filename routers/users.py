from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from typing import List
from audit import log_event
from database import get_db
from constants import DEPARTMENTS
import models
import schemas
from deps import get_current_user, require_admin
from security import hash_password, password_policy_errors
from routers.auth import make_handle
import shutil
import os
import uuid

router = APIRouter(prefix="/users", tags=["users"])

UPLOAD_DIR = "static/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.get("/public")
def public_roster(db: Session = Depends(get_db)):
    users = db.query(models.User).all()
    return [
        {
            "name": u.name,
            "handle": u.handle,
            "profile_image": u.profile_image,
            "department": u.department,
            "is_admin": u.is_admin,
        }
        for u in users
    ]


@router.get("/", response_model=List[schemas.UserOut])
def list_users(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    query = db.query(models.User)
    if not current_user.is_admin:
        if current_user.department:
            query = query.filter(
                (models.User.department == current_user.department)
                | (models.User.department.is_(None))
                | (models.User.is_admin == True)
            )
        else:
            query = query.filter(
                (models.User.department.is_(None)) | (models.User.is_admin == True)
            )
    return query.order_by(models.User.name).all()


@router.post("/", response_model=schemas.UserOut)
def add_teammate(
    payload: schemas.UserCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin),
):
    policy_errors = password_policy_errors(payload.password)
    if policy_errors:
        raise HTTPException(status_code=400, detail="Password must contain " + ", ".join(policy_errors))
    if payload.department and payload.department not in DEPARTMENTS:
        raise HTTPException(status_code=400, detail="Invalid department")

    # Role assignment is controlled server-side, never trusted blindly from
    # the client. Only SUPER_ADMIN may grant ADMIN/SUPER_ADMIN; a plain ADMIN
    # can only create STAFF/USER accounts.
    requested_role = payload.role or models.ROLE_USER
    if requested_role not in models.ALL_ROLES:
        raise HTTPException(status_code=400, detail="Invalid role")
    elevated_roles = (models.ROLE_ADMIN, models.ROLE_SUPER_ADMIN)
    if requested_role in elevated_roles and current_user.role != models.ROLE_SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Only a super admin can grant admin roles")

    user = models.User(
        name=payload.name.strip(),
        handle=make_handle(payload.name, db),
        password_hash=hash_password(payload.password),
        is_admin=requested_role in elevated_roles,
        role=requested_role,
        department=payload.department,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    log_event(db, current_user.name, f"added teammate {user.name}", user_id=current_user.id, event_type="user_created")
    return user


@router.post("/{user_id}/profile-image", response_model=schemas.UserOut)
def upload_profile_image(
    user_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if current_user.id != user_id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not allowed")

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    ext = file.filename.split(".")[-1].lower()
    if ext not in ("jpg", "jpeg", "png", "gif", "webp"):
        raise HTTPException(status_code=400, detail="Invalid image format")

    filename = f"{uuid.uuid4()}.{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)

    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    user.profile_image = f"/static/uploads/{filename}"
    db.commit()
    db.refresh(user)
    return user