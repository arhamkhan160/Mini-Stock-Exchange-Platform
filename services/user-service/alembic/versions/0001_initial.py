"""initial user schema

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
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    # Case-insensitive uniqueness: "Ada" and "ada" must collide. A plain
    # unique index on `username` would allow both to exist at once.
    op.create_index(
        "ix_users_username_lower", "users", [sa.text("lower(username)")], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_users_username_lower", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
