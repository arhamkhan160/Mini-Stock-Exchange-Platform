import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from common.db import Base


def _utcnow() -> datetime:
    """Python-side timestamp default.

    `server_default` alone leaves the attribute unloaded on a newly inserted
    row, so serialising that row triggers a lazy refresh — which in async
    SQLAlchemy raises MissingGreenlet. Populating it client-side means the
    object is complete the moment it is flushed, with no extra round trip.
    The server_default stays as the guarantee for rows written outside the ORM.
    """
    return datetime.now(timezone.utc)


# --- the state machine -------------------------------------------------------
PENDING = "PENDING"
NEW = "NEW"
PARTIALLY_FILLED = "PARTIALLY_FILLED"
FILLED = "FILLED"
CANCEL_PENDING = "CANCEL_PENDING"
CANCELLED = "CANCELLED"
REJECTED = "REJECTED"

TERMINAL = frozenset({FILLED, CANCELLED, REJECTED})
OPEN = frozenset({NEW, PARTIALLY_FILLED})
CANCELLABLE = frozenset({NEW, PARTIALLY_FILLED})

# Every allowed edge. `transition()` refuses anything not listed here, which is
# what stops a late fill from resurrecting a CANCELLED order.
ALLOWED: dict[str, frozenset[str]] = {
    PENDING: frozenset({NEW, REJECTED}),
    NEW: frozenset({PARTIALLY_FILLED, FILLED, CANCEL_PENDING, CANCELLED, REJECTED}),
    PARTIALLY_FILLED: frozenset({PARTIALLY_FILLED, FILLED, CANCEL_PENDING, CANCELLED}),
    CANCEL_PENDING: frozenset({CANCELLED, FILLED, PARTIALLY_FILLED, NEW}),
    FILLED: frozenset(),
    CANCELLED: frozenset(),
    REJECTED: frozenset(),
}

MAX_REASON_LEN = 255


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    # Client idempotency key: a double-clicked Buy button must not buy twice.
    client_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    order_type: Mapped[str] = mapped_column(String(8), nullable=False)
    price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    filled_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    avg_fill_price: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal("0"), server_default="0"
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=PENDING, server_default=PENDING)
    # Audit trail only — Account owns the real hold. Useful when a reconciliation
    # argument starts at 2am.
    reserved_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(MAX_REASON_LEN), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=_utcnow, onupdate=_utcnow, server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("user_id", "client_order_id", name="uq_user_client_order"),
        CheckConstraint("filled_quantity >= 0 AND filled_quantity <= quantity", name="ck_fill_bounds"),
        CheckConstraint("quantity > 0", name="ck_qty_positive"),
        Index("ix_orders_user_created", "user_id", "created_at"),
    )


class OrderEvent(Base):
    """Append-only audit trail. Every status write goes through `transition()`,
    and `transition()` always writes one of these — so the row history IS the
    proof that the saga did what it says it did."""

    __tablename__ = "order_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    note: Mapped[str | None] = mapped_column(String(MAX_REASON_LEN), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )


class ProcessedEvent(Base):
    """The AUTHORITY on event idempotency, not Redis.

    Inserted in the same transaction as the work it describes, so a crash can
    never leave "handled" recorded for work that did not commit.
    """

    __tablename__ = "processed_events"

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    event_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    handled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
