import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from common.db import Base

# Notification types (also the values the frontend switches on for icons).
ORDER_FILLED = "ORDER_FILLED"
ORDER_PARTIALLY_FILLED = "ORDER_PARTIALLY_FILLED"
ORDER_CANCELLED = "ORDER_CANCELLED"
ORDER_REJECTED = "ORDER_REJECTED"

MAX_MESSAGE_LEN = 500
MAX_TITLE_LEN = 120


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(MAX_TITLE_LEN), nullable=False)
    message: Mapped[str] = mapped_column(String(MAX_MESSAGE_LEN), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(10), nullable=True)
    reference_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_notif_user_created", "user_id", "created_at"),)


class ProcessedEvent(Base):
    """Second line of idempotency defence, next to the Redis marker.

    Redis can be flushed; this table cannot. Both are checked because a
    duplicated fill alert is the most visible bug a user can see.
    """

    __tablename__ = "processed_events"

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    event_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    handled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
