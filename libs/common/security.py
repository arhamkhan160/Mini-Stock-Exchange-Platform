"""JWT issuing/validation and the FastAPI dependencies every service shares.

Contract:
  * HS256, secret = env JWT_SECRET (IDENTICAL in every container).
  * Claims: sub (user id, string UUID), email, username, iat, exp, type="access".
  * Clients send `Authorization: Bearer <token>`. The gateway validates it AND
    forwards it unchanged; each service validates it again (defence in depth).
"""

import time
import uuid

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel

from .config import settings

# auto_error=False so we can return our own 401 body shape.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


class CurrentUser(BaseModel):
    id: uuid.UUID
    email: str
    username: str


def create_access_token(user_id, email: str, username: str) -> str:
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "email": email,
        "username": username,
        "iat": now,
        "exp": now + settings.JWT_EXPIRE_MINUTES * 60,
        "type": "access",
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        claims = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if claims.get("type") != "access":
        raise HTTPException(status_code=401, detail="wrong token type")
    return claims


async def get_current_user(token: str | None = Depends(oauth2_scheme)) -> CurrentUser:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    claims = decode_token(token)
    try:
        user_id = uuid.UUID(claims["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=401, detail="malformed token subject")
    return CurrentUser(
        id=user_id,
        email=claims.get("email", ""),
        username=claims.get("username", ""),
    )


async def require_internal_key(x_internal_key: str | None = Header(default=None)) -> None:
    """Guard for /internal/* endpoints. The gateway refuses to proxy /internal/,
    so these are only reachable from inside the Docker network."""
    if x_internal_key != settings.INTERNAL_API_KEY:
        raise HTTPException(status_code=403, detail="internal endpoint")
