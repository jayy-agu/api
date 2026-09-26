import re
import os
import uuid
import shutil
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional

from database import get_db
from constants import DEPARTMENTS, ARCHIVE_DAYS
import models
import schemas
from deps import get_current_user
from ratelimit import limiter, user_or_ip_key
from sqlalchemy import func
from datetime import datetime, timedelta

router = APIRouter(prefix="/posts", tags=["posts"])

MENTION_RE = re.compile(r"@(\w+)")

UPLOAD_DIR = "static/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.get("/", response_model=List[schemas.PostOut])
def list_posts(
    department: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    query = db.query(models.Post).options(
        joinedload(models.Post.author),
        joinedload(models.Post.comments).joinedload(models.Comment.author)
    )

    # Private DMs are NEVER part of any department/general feed.
    query = query.filter(models.Post.dm_recipient_id.is_(None))

    if current_user.is_admin:
        if department:
            query = query.filter(models.Post.department == department)
        else:
            query = query.filter(models.Post.department.is_(None))
    else:
        if department:
            # Strict: only show your own department
            if department != current_user.department:
                raise HTTPException(status_code=403, detail="Not your department")
            query = query.filter(models.Post.department == department)
        else:
            # All Updates = general posts + your own department's posts
            query = query.filter(or_(
                models.Post.department.is_(None),
                models.Post.department == current_user.department,
            ))

    posts = query.order_by(models.Post.created_at.desc()).all()
    return posts


@router.get("/dms", response_model=List[schemas.PostOut])
def list_dms(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """All private messages where the current user is sender or recipient."""
    return (
        db.query(models.Post)
        .options(
            joinedload(models.Post.author),
            joinedload(models.Post.comments).joinedload(models.Comment.author)
        )
        .filter(models.Post.dm_recipient_id.isnot(None))
        .filter(or_(
            models.Post.author_id == current_user.id,
            models.Post.dm_recipient_id == current_user.id,
        ))
        .order_by(models.Post.created_at.desc())
        .all()
    )


@router.get("/dept-counts")
def department_counts(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Active (non-archived) post counts per department, for the department
    pills. Deliberately a separate endpoint rather than derived from
    GET /posts/ on the frontend: that endpoint's result shape changes
    depending on whether a department filter is active AND whether the
    caller is an admin (admins get ONLY general posts back when no
    department is selected), so counting departments out of whichever
    /posts/ response happened to be cached undercounts every department
    except the one currently open. This endpoint always looks at every
    department the caller is allowed to see, regardless of which one (if
    any) they're currently viewing.
    """
    cutoff = datetime.utcnow() - timedelta(days=ARCHIVE_DAYS)

    query = db.query(models.Post.department, func.count(models.Post.id)).filter(
        models.Post.dm_recipient_id.is_(None),
        models.Post.department.isnot(None),
        models.Post.created_at >= cutoff,
    )
    if not current_user.is_admin:
        query = query.filter(models.Post.department == current_user.department)

    counts = dict(query.group_by(models.Post.department).all())
    return counts


@router.post("/", response_model=schemas.PostOut)
def create_post(
    payload: schemas.PostCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if payload.tier not in ("urgent", "update"):
        raise HTTPException(status_code=400, detail="Invalid tier")
    if not payload.message.strip() and not payload.attachment_url:
        raise HTTPException(status_code=400, detail="Message or attachment required")
    if payload.department and payload.department not in DEPARTMENTS:
        raise HTTPException(status_code=400, detail="Invalid department")
    if payload.dm_recipient_id:
        raise HTTPException(
            status_code=400,
            detail="Private messages must be sent via POST /posts/dm",
        )

    post_department = payload.department
    if not current_user.is_admin:
        if post_department and post_department != current_user.department:
            raise HTTPException(
                status_code=403,
                detail="You can only post to your own department",
            )

    post = models.Post(
        author_id=current_user.id,
        tier=payload.tier,
        message=payload.message.strip(),
        department=post_department,
        attachment_url=payload.attachment_url,
        attachment_filename=payload.attachment_filename,
        attachment_type=payload.attachment_type,
    )
    db.add(post)
    db.commit()
    db.refresh(post)

    mentioned_handles = set()
    tag_all = False
    for match in MENTION_RE.finditer(payload.message):
        h = match.group(1).lower()
        if h == "all":
            tag_all = True
        else:
            mentioned_handles.add(h)

    # IMPORTANT: a notification's preview text leaks the message content to
    # whoever receives it, even if they can never open the post itself (a
    # department-scoped post is invisible to outsiders via GET /posts/).
    # So @mentions and @all must NEVER reach further than the post itself
    # would -- otherwise tagging someone outside the department (or @all)
    # is a way to leak a department-only message to the whole company via
    # the notification bell. Admins are exempt since they can already see
    # every post regardless of department.
    def _can_see_post(user: models.User) -> bool:
        if user.is_admin:
            return True
        if not post_department:
            return True
        return user.department == post_department

    if tag_all:
        recipients = [
            u for u in db.query(models.User).filter(models.User.id != current_user.id).all()
            if _can_see_post(u)
        ]
        is_mention = True
    elif mentioned_handles:
        recipients = [
            u for u in db.query(models.User)
            .filter(models.User.handle.in_(mentioned_handles), models.User.id != current_user.id)
            .all()
            if _can_see_post(u)
        ]
        is_mention = True
    else:
        # No mentions: notify everyone allowed to see this post, so the
        # notification bell actually moves on new updates.
        q = db.query(models.User).filter(models.User.id != current_user.id)
        if post_department:
            q = q.filter(models.User.department == post_department)
        recipients = q.all()
        is_mention = False

    preview = payload.message.strip()[:80] or (payload.attachment_filename or "Attachment")
    for recipient in recipients:
        db.add(models.Notification(
            user_id=recipient.id,
            from_user_id=current_user.id,
            post_id=post.id,
            preview=preview,
            is_mention=is_mention,
        ))
    db.commit()

    return db.query(models.Post).options(
        joinedload(models.Post.author),
        joinedload(models.Post.comments).joinedload(models.Comment.author)
    ).filter(models.Post.id == post.id).first()


@router.post("/dm", response_model=schemas.PostOut)
def create_dm(
    payload: schemas.DMCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if payload.recipient_id == current_user.id:
        raise HTTPException(status_code=400, detail="You can't DM yourself")
    recipient = db.query(models.User).filter(models.User.id == payload.recipient_id).first()
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient not found")
    if not payload.message.strip() and not payload.attachment_url:
        raise HTTPException(status_code=400, detail="Message or attachment required")

    post = models.Post(
        author_id=current_user.id,
        tier="update",
        message=payload.message.strip(),
        department=None,
        attachment_url=payload.attachment_url,
        attachment_filename=payload.attachment_filename,
        attachment_type=payload.attachment_type,
        dm_recipient_id=payload.recipient_id,
    )
    db.add(post)
    db.commit()
    db.refresh(post)

    db.add(models.Notification(
        user_id=recipient.id,
        from_user_id=current_user.id,
        post_id=post.id,
        preview=payload.message.strip()[:80] or (payload.attachment_filename or "Attachment"),
        is_mention=True,
    ))
    db.commit()

    return db.query(models.Post).options(
        joinedload(models.Post.author),
        joinedload(models.Post.comments).joinedload(models.Comment.author)
    ).filter(models.Post.id == post.id).first()


@router.delete("/{post_id}")
def delete_post(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    post = db.query(models.Post).filter(models.Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Not found")
    if post.author_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="You can only delete your own posts")
    db.delete(post)
    db.commit()
    return {"ok": True}


@router.post("/upload")
@limiter.limit("10/minute", key_func=user_or_ip_key)  # per-user: caps attachment spam/storage abuse
def upload_post_attachment(
    request: Request,
    file: UploadFile = File(...),
    current_user: models.User = Depends(get_current_user),
):
    ext = file.filename.split(".")[-1].lower()
    allowed = ("jpg", "jpeg", "png", "gif", "webp", "mp4", "mov", "webm", "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "zip")
    if ext not in allowed:
        raise HTTPException(status_code=400, detail="Invalid file format")

    filename = f"{uuid.uuid4()}.{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)

    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    if ext in ("jpg", "jpeg", "png", "gif", "webp"):
        file_type = "image"
    elif ext in ("mp4", "mov", "webm"):
        file_type = "video"
    else:
        file_type = "file"

    return {
        "url": f"/static/uploads/{filename}",
        "filename": file.filename,
        "type": file_type,
    }


@router.post("/{post_id}/comments", response_model=schemas.CommentOut)
def add_comment(
    post_id: int,
    payload: schemas.CommentCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    post = db.query(models.Post).filter(models.Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    # DMs: only the two participants may comment
    if post.dm_recipient_id is not None:
        if current_user.id not in (post.author_id, post.dm_recipient_id) and not current_user.is_admin:
            raise HTTPException(status_code=403, detail="Not allowed")

    comment = models.Comment(
        post_id=post_id,
        author_id=current_user.id,
        text=payload.text.strip(),
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return comment