import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, Index, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from common.db import Base

ZERO = Decimal("0.0000")

# Transaction.type values — the frontend switches on these for badge color.
DEPOSIT = "DEPOSIT"
HOLD = "HOLD"
RELEASE = "RELEASE"
TRADE_BUY = "TRADE_BUY"
TRADE_SELL = "TRADE_SELL"

# Reservation.status values.
HELD = "HELD"
CONSUMED = "CONSUMED"
RELEASED = "RELEASED"

MAX_DESCRIPTION_LEN = 500


class Account(Base):
    __tablename__ = "accounts"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    cash_balance: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=ZERO, server_default="0")
    held_balance: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=ZERO, server_default="0")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD", server_default="USD")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # If a bug ever breaks one of these invariants, Postgres aborts the
        # transaction instead of silently corrupting money.
        CheckConstraint("cash_balance >= 0", name="ck_cash_non_negative"),
        CheckConstraint("held_balance >= 0", name="ck_held_non_negative"),
        CheckConstraint("held_balance <= cash_balance", name="ck_held_le_cash"),
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    # Signed: positive = cash/available increases, negative = decreases.
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    reference_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(String(MAX_DESCRIPTION_LEN), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_tx_user_created", "user_id", "created_at"),)


class Reservation(Base):
    """One row per order that has ever held buyer cash — rows are never
    deleted, only advanced through status HELD -> CONSUMED|RELEASED, so the
    release endpoint can tell "already resolved" (200, remaining 0) apart
    from "never reserved at all" (404).

    remaining = amount_held - amount_consumed - amount_released
    """

    __tablename__ = "reservations"

    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    amount_held: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    amount_consumed: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=ZERO, server_default="0")
    amount_released: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=ZERO, server_default="0")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=HELD, server_default=HELD)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    @property
    def remaining(self) -> Decimal:
        return self.amount_held - self.amount_consumed - self.amount_released


class ProcessedEvent(Base):
    """Database-first idempotency guard, next to the Redis fast-path marker.

    Redis can be flushed; this table cannot. See common.redis_client.seen_event
    for the exact commit ordering this table depends on.
    """

    __tablename__ = "processed_events"

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    event_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    handled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
