"""Registration, login, and password change.

Login is timing-safe: an unknown identifier still runs a bcrypt comparison
(against a fixed dummy hash) before returning 401, so "wrong password" and
"unknown user" take the same wall-clock time and can't be used to enumerate
registered accounts. A disabled account is only revealed as such *after* the
password has already been verified — checking is_active first would let an
attacker probe which emails belong to disabled accounts without a password.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from common.config import settings
from common.security import CurrentUser, create_access_token, get_current_user

from .deps import get_session
from .models import User
from .passwords import burn_verify_time, hash_password, verify_password
from .schemas import ChangePasswordRequest, LoginJsonRequest, Token, UserCreate, UserResponse

log = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

GENERIC_LOGIN_ERROR = "incorrect username/email or password"


def _issue_token(user: User) -> Token:
    token = create_access_token(user.id, user.email, user.username)
    return Token(
        access_token=token,
        expires_in=settings.JWT_EXPIRE_MINUTES * 60,
        user=UserResponse.model_validate(user),
    )


async def _find_by_identifier(session: AsyncSession, identifier: str) -> User | None:
    # Username uniqueness is case-insensitive (see the migration's functional
    # index), so lookups must be too, or a user who registered as "Ada" could
    # never log in typing "ada".
    ident = identifier.strip().lower()
    stmt = select(User).where(or_(func.lower(User.username) == ident, User.email == ident))
    return (await session.execute(stmt)).scalars().first()


async def _authenticate(session: AsyncSession, identifier: str, password: str) -> User:
    user = await _find_by_identifier(session, identifier)
    if user is None:
        burn_verify_time()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=GENERIC_LOGIN_ERROR,
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not verify_password(password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=GENERIC_LOGIN_ERROR,
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account disabled")
    return user


@router.post("/auth/register", response_model=Token, status_code=status.HTTP_201_CREATED)
async def register(user_in: UserCreate, session: AsyncSession = Depends(get_session)):
    user = User(
        email=user_in.email.lower(),
        username=user_in.username,
        full_name=user_in.full_name,
        password_hash=hash_password(user_in.password),
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        # Never trust a pre-check SELECT — a concurrent registration can win
        # the race between it and this INSERT. The unique constraints (email,
        # and the functional index on lower(username)) are the real guard.
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="email or username already registered")
    await session.refresh(user)
    return _issue_token(user)


@router.post("/auth/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends(), session: AsyncSession = Depends(get_session)):
    # OAuth2 spec calls the field "username"; we accept a username or an email in it.
    user = await _authenticate(session, form_data.username, form_data.password)
    return _issue_token(user)


@router.post("/auth/login-json", response_model=Token)
async def login_json(body: LoginJsonRequest, session: AsyncSession = Depends(get_session)):
    user = await _authenticate(session, body.email_or_username, body.password)
    return _issue_token(user)


@router.post("/auth/change-password", response_model=UserResponse)
async def change_password(
    body: ChangePasswordRequest,
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    user = (await session.execute(select(User).where(User.id == current.id))).scalars().first()
    if user is None or not user.is_active:
        # The token decodes fine, but the account behind it is gone or
        # disabled — force a fresh login rather than confirm which is true.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    await session.commit()
    await session.refresh(user)
    return user
