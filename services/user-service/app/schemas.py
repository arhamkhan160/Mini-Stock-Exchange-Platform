import re
import uuid
from datetime import datetime

import email_validator
from pydantic import BaseModel, ConfigDict, EmailStr, field_validator

from .models import MAX_FULL_NAME_LEN
from .passwords import MAX_PASSWORD_BYTES

# email-validator treats ".local" as a reserved/special-use TLD (RFC 6762) and
# rejects it outright — but every seed script, selfcheck, and this project's
# own docs use @mse.local for bots and test accounts. RFC 6762 only says
# applications "MAY" treat it as special, so opting out for this one fake TLD
# is a deliberate, documented choice, not a validation hole: every other
# reserved name (localhost, invalid, onion, arpa, ...) still gets rejected.
email_validator.SPECIAL_USE_DOMAIN_NAMES = [d for d in email_validator.SPECIAL_USE_DOMAIN_NAMES if d != "local"]

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,30}$")
HAS_LETTER_RE = re.compile(r"[A-Za-z]")
HAS_DIGIT_RE = re.compile(r"\d")


def check_username(v: str) -> str:
    if not USERNAME_RE.match(v):
        raise ValueError("username must be 3-30 characters: letters, digits, underscore")
    return v


def check_full_name(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    if not v:
        return None  # whitespace-only full_name is stored as NULL, not ""
    if len(v) > MAX_FULL_NAME_LEN:
        raise ValueError(f"full_name must be at most {MAX_FULL_NAME_LEN} characters")
    return v


def check_password(v: str) -> str:
    if len(v) < 8:
        raise ValueError("password must be at least 8 characters")
    if len(v.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError(f"password must be at most {MAX_PASSWORD_BYTES} bytes")
    if not HAS_LETTER_RE.search(v) or not HAS_DIGIT_RE.search(v):
        raise ValueError("password must contain at least one letter and one digit")
    return v


class UserBase(BaseModel):
    email: EmailStr
    username: str
    full_name: str | None = None

    @field_validator("username")
    @classmethod
    def _username(cls, v: str) -> str:
        return check_username(v)

    @field_validator("full_name")
    @classmethod
    def _full_name(cls, v: str | None) -> str | None:
        return check_full_name(v)


class UserCreate(UserBase):
    password: str

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        return check_password(v)


class UserUpdate(BaseModel):
    """PATCH /users/me. Email is immutable via this endpoint — it is baked
    into every already-issued JWT as a claim, and changing it here would
    desync every live token until the user re-authenticates."""

    full_name: str | None = None
    username: str | None = None

    @field_validator("full_name")
    @classmethod
    def _full_name(cls, v: str | None) -> str | None:
        return check_full_name(v)

    @field_validator("username")
    @classmethod
    def _username(cls, v: str | None) -> str | None:
        return None if v is None else check_username(v)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _new_password(cls, v: str) -> str:
        return check_password(v)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    username: str
    full_name: str | None
    is_active: bool
    created_at: datetime


class LoginJsonRequest(BaseModel):
    """POST /auth/login-json — `email_or_username` accepts either."""

    email_or_username: str
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class HealthOut(BaseModel):
    status: str
    service: str


class ReadyOut(BaseModel):
    status: str
    service: str
    database: str
