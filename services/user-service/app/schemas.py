import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator

from .models import MAX_FULL_NAME_LEN, MAX_USERNAME_LEN
from .passwords import MAX_PASSWORD_BYTES

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,32}$")


class UserBase(BaseModel):
    email: EmailStr
    username: str
    full_name: str | None = None

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        if not USERNAME_RE.match(v):
            raise ValueError("username must be 3-32 characters: letters, digits, underscore")
        return v

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, v: str | None) -> str | None:
        if v is not None and len(v) > MAX_FULL_NAME_LEN:
            raise ValueError(f"full_name must be at most {MAX_FULL_NAME_LEN} characters")
        return v


class UserCreate(UserBase):
    password: str

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("password must be at least 8 characters")
        if len(v.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError(f"password must be at most {MAX_PASSWORD_BYTES} bytes")
        return v


class UserUpdate(BaseModel):
    """PATCH /users/me. Email and username are immutable via this endpoint —
    changing either would desync the claims already baked into live JWTs."""

    full_name: str | None = None

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, v: str | None) -> str | None:
        if v is not None and len(v) > MAX_FULL_NAME_LEN:
            raise ValueError(f"full_name must be at most {MAX_FULL_NAME_LEN} characters")
        return v


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    username: str
    full_name: str | None
    created_at: datetime


class LoginJsonRequest(BaseModel):
    """POST /auth/login-json — same semantics as the OAuth2 form login:
    `username` accepts a username OR an email."""

    username: str
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
