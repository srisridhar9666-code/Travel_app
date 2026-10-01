"""Request and response bodies for the authentication endpoints."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.enums import Designation, Gender, Role


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class UserProfile(BaseModel):
    """The signed-in user, as the client needs them."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    full_name: str
    employee_code: str | None = None
    role: Role
    designation: Designation | None = None
    gender: Gender
    phone: str | None = None
    base_location: str | None = None
    theme_preference: str
    is_active: bool
    last_login_at: datetime | None = None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserProfile


class SetPasswordRequest(BaseModel):
    """Redeems an invite or reset token and sets the first/replacement password."""

    token: str = Field(min_length=10, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class TokenPreview(BaseModel):
    """What a token redemption page shows before asking for a password.

    Deliberately thin: enough to confirm the right link was opened, never enough
    to enumerate accounts by guessing tokens.
    """

    full_name: str
    email: EmailStr
    purpose: str


class ThemePreferenceRequest(BaseModel):
    theme_preference: str = Field(pattern="^(light|dark|system)$")


class MessageResponse(BaseModel):
    detail: str


class InviteLinkResponse(BaseModel):
    """Returned to an admin after creating or re-inviting a user.

    The link is emailed to the person, and also returned here so the admin can
    pass it on when mail is off or bounced. The raw token appears here exactly
    once and is not stored.
    """

    detail: str
    invite_url: str | None = None
    expires_at: datetime | None = None
    #: Whether the link reached the mail server. None on the forgot-password
    #: answer, which must not reveal anything about the account.
    email_sent: bool | None = None
    #: Why it did not, when it did not - "EMAIL_ENABLED is not true", an SMTP
    #: authentication error - so the admin can fix the cause, not just retry.
    email_detail: str | None = None
