import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from common.config import settings
from common.security import CurrentUser, create_access_token, get_current_user, require_internal_key

from .deps import get_session
from .models import User
from .passwords import hash_password, verify_password
from .schemas import LoginJsonRequest, Token, UserCreate, UserResponse, UserUpdate

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
    stmt = select(User).where(or_(User.username == identifier, User.email == identifier.lower()))
    return (await session.execute(stmt)).scalars().first()


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
        # A concurrent registration can win the race between the pre-check
        # (there is none, deliberately — the unique index is the real guard)
        # and this commit; the constraint is the single source of truth.
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="email or username already registered")
    await session.refresh(user)
    return _issue_token(user)


@router.post("/auth/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends(), session: AsyncSession = Depends(get_session)):
    # OAuth2 spec calls the field "username"; we accept a username or an email in it.
    user = await _find_by_identifier(session, form_data.username)
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=GENERIC_LOGIN_ERROR,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _issue_token(user)


@router.post("/auth/login-json", response_model=Token)
async def login_json(body: LoginJsonRequest, session: AsyncSession = Depends(get_session)):
    user = await _find_by_identifier(session, body.username)
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=GENERIC_LOGIN_ERROR,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _issue_token(user)


@router.get("/users/me", response_model=UserResponse)
async def get_me(user: CurrentUser = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    row = (await session.execute(select(User).where(User.id == user.id))).scalars().first()
    if row is None:
        # The JWT is valid but the account is gone — a stale token, not a bug.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    return row


@router.patch("/users/me", response_model=UserResponse)
async def update_me(
    body: UserUpdate,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    row = (await session.execute(select(User).where(User.id == user.id))).scalars().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    if body.full_name is not None:
        row.full_name = body.full_name
    await session.commit()
    await session.refresh(row)
    return row


@router.get(
    "/internal/users/{user_id}",
    response_model=UserResponse,
    dependencies=[Depends(require_internal_key)],
)
async def internal_get_user(user_id: str, session: AsyncSession = Depends(get_session)):
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    row = (await session.execute(select(User).where(User.id == uid))).scalars().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    return row
