from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, joinedload
from typing import List

from database import get_db
import models
import schemas
from deps import get_current_user

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/", response_model=List[schemas.NotificationOut])
def list_notifications(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    notifs = (
        db.query(models.Notification)
        .options(joinedload(models.Notification.from_user), joinedload(models.Notification.post))
        .filter(models.Notification.user_id == current_user.id)
        .order_by(models.Notification.created_at.desc())
        .limit(50)
        .all()
    )
    return notifs


@router.get("/unread-count")
def unread_count(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    count = db.query(models.Notification).filter(
        models.Notification.user_id == current_user.id,
        models.Notification.is_read == False
    ).count()
    return {"count": count}


@router.post("/read-all")
def mark_all_read(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    db.query(models.Notification).filter(
        models.Notification.user_id == current_user.id
    ).update({"is_read": True})
    db.commit()
    return {"ok": True}


@router.post("/read-visible")
def mark_visible_read(
    payload: schemas.ReadVisibleNotifs,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if payload.post_ids:
        db.query(models.Notification).filter(
            models.Notification.user_id == current_user.id,
            models.Notification.post_id.in_(payload.post_ids),
            models.Notification.is_read == False
        ).update({"is_read": True})
        db.commit()
    return {"ok": True}