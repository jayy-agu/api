from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List


class UserCreate(BaseModel):
    name: str
    password: str
    department: Optional[str] = None
    role: Optional[str] = None


class UserLogin(BaseModel):
    handle: str
    password: str


class UserOut(BaseModel):
    id: int
    name: str
    handle: str
    is_admin: bool
    role: str
    department: Optional[str] = None
    profile_image: Optional[str] = None
    mfa_enabled: bool = False

    model_config = {"from_attributes": True}


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class PostCreate(BaseModel):
    tier: str
    message: str
    department: Optional[str] = None
    attachment_url: Optional[str] = None
    attachment_filename: Optional[str] = None
    attachment_type: Optional[str] = None
    dm_recipient_id: Optional[int] = None


class DMCreate(BaseModel):
    recipient_id: int
    message: str
    attachment_url: Optional[str] = None
    attachment_filename: Optional[str] = None
    attachment_type: Optional[str] = None


class CommentOut(BaseModel):
    id: int
    text: str
    created_at: datetime
    author: UserOut

    model_config = {"from_attributes": True}


class PostOut(BaseModel):
    id: int
    tier: str
    message: str
    department: Optional[str] = None
    created_at: datetime
    author: UserOut
    attachment_url: Optional[str] = None
    attachment_filename: Optional[str] = None
    attachment_type: Optional[str] = None
    dm_recipient_id: Optional[int] = None
    comments: List[CommentOut] = []

    model_config = {"from_attributes": True}


class CommentCreate(BaseModel):
    text: str


class NotificationOut(BaseModel):
    id: int
    preview: Optional[str]
    is_read: bool
    is_mention: bool
    created_at: datetime
    from_user: UserOut
    post_tier: Optional[str] = None
    post_id: Optional[int] = None

    model_config = {"from_attributes": True}


class AccessLogOut(BaseModel):
    id: int
    actor_name: str
    event: str
    ok: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class StatsOut(BaseModel):
    total_staff: int
    posts_this_week: int
    urgent_this_week: int
    unread_notifications_total: int


class UserUpdateDept(BaseModel):
    department: Optional[str] = None


class PasswordReset(BaseModel):
    new_password: str


class UserUpdateRole(BaseModel):
    role: str


class MFAEnrollRequest(BaseModel):
    pre_auth_token: str


class MFAEnrollResponse(BaseModel):
    secret: str
    otpauth_uri: str
    qr_code: str  # data: URI, ready for <img src>


class MFAConfirmRequest(BaseModel):
    pre_auth_token: str
    code: str


class MFAConfirmResponse(BaseModel):
    ok: bool
    recovery_codes: List[str]
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class MFAStatusResponse(BaseModel):
    mfa_enabled: bool


class MFADisableRequest(BaseModel):
    password: str


class MFAVerifyRequest(BaseModel):
    pre_auth_token: str
    code: Optional[str] = None
    recovery_code: Optional[str] = None


class MFAChallengeResponse(BaseModel):
    mfa_required: bool = True
    pre_auth_token: str
    enrollment_required: bool = False


class ReadVisibleNotifs(BaseModel):
    post_ids: List[int]


class SessionOut(BaseModel):
    """One live server-side session, as shown in the profile modal."""
    id: int
    ip_address: Optional[str]
    user_agent: Optional[str]
    created_at: datetime
    last_seen_at: Optional[datetime]
    current: bool = False  # True for the session presenting the request