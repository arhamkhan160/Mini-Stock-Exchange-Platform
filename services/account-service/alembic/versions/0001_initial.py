"""initial account schema

Revision ID: 0001
Revises:
Create Date: 2026-08-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cash_balance", sa.Numeric(18, 4), server_default="0", nullable=False),
        sa.Column("held_balance", sa.Numeric(18, 4), server_default="0", nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="USD", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
        # If a bug ever breaks one of these invariants, Postgres aborts the
        # transaction instead of silently corrupting money.
        sa.CheckConstraint("cash_balance >= 0", name="ck_cash_non_negative"),
        sa.CheckConstraint("held_balance >= 0", name="ck_held_non_negative"),
        sa.CheckConstraint("held_balance <= cash_balance", name="ck_held_le_cash"),
    )

    op.create_table(
        "transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("balance_after", sa.Numeric(18, 4), nullable=False),
        sa.Column("reference_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_transactions_user_id", "transactions", ["user_id"])
    op.create_index("ix_transactions_reference_id", "transactions", ["reference_id"])
    # The ledger listing is always "this user's newest first" — index it that way.
    op.create_index("ix_tx_user_created", "transactions", ["user_id", "created_at"])

    op.create_table(
        "reservations",
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount_held", sa.Numeric(18, 4), nullable=False),
        sa.Column("amount_consumed", sa.Numeric(18, 4), server_default="0", nullable=False),
        sa.Column("amount_released", sa.Numeric(18, 4), server_default="0", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="HELD", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("order_id"),
    )
    op.create_index("ix_reservations_user_id", "reservations", ["user_id"])

    op.create_table(
        "processed_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=True),
        sa.Column("handled_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )


def downgrade() -> None:
    op.drop_table("processed_events")
    op.drop_index("ix_reservations_user_id", table_name="reservations")
    op.drop_table("reservations")
    op.drop_index("ix_tx_user_created", table_name="transactions")
    op.drop_index("ix_transactions_reference_id", table_name="transactions")
    op.drop_index("ix_transactions_user_id", table_name="transactions")
    op.drop_table("transactions")
    op.drop_table("accounts")
