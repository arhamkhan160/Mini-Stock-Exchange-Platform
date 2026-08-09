import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from common.db import Base

MAX_EMAIL_LEN = 255
MAX_USERNAME_LEN = 50
MAX_FULL_NAME_LEN = 120


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(MAX_EMAIL_LEN), unique=True, index=True, nullable=False)
    # Uniqueness is enforced case-insensitively by a functional index
    # (ix_users_username_lower on lower(username)) in the migration, not by a
    # plain unique constraint on this column — "Ada" and "ada" must collide.
    username: Mapped[str] = mapped_column(String(MAX_USERNAME_LEN), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(MAX_FULL_NAME_LEN), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
