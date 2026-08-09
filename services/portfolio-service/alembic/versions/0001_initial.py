"""initial

Revision ID: 0001
Revises: 
Create Date: 2026-08-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table('holdings',
    sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('symbol', sa.String(length=10), nullable=False),
    sa.Column('quantity', sa.BigInteger(), nullable=False),
    sa.Column('reserved_quantity', sa.BigInteger(), nullable=False),
    sa.Column('avg_cost', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('realized_pnl', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.CheckConstraint('quantity >= 0', name='ck_qty_non_negative'),
    sa.CheckConstraint('reserved_quantity >= 0 AND reserved_quantity <= quantity', name='ck_reserved_bounds'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'symbol', name='uq_user_symbol')
    )
    op.create_index(op.f('ix_holdings_user_id'), 'holdings', ['user_id'], unique=False)

    op.create_table('processed_events',
    sa.Column('event_id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('event_type', sa.String(length=64), nullable=True),
    sa.Column('handled_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('event_id')
    )

    op.create_table('share_reservations',
    sa.Column('order_id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('symbol', sa.String(length=10), nullable=False),
    sa.Column('quantity', sa.BigInteger(), nullable=False),
    sa.Column('consumed', sa.BigInteger(), nullable=False),
    sa.Column('released', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.PrimaryKeyConstraint('order_id')
    )
    op.create_index(op.f('ix_share_reservations_user_id'), 'share_reservations', ['user_id'], unique=False)

    op.create_table('trade_history',
    sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('trade_id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('symbol', sa.String(length=10), nullable=False),
    sa.Column('side', sa.String(length=4), nullable=False),
    sa.Column('quantity', sa.BigInteger(), nullable=False),
    sa.Column('price', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('realized_pnl', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('executed_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('trade_id', 'user_id', name='uq_trade_user')
    )
    op.create_index(op.f('ix_trade_history_user_id'), 'trade_history', ['user_id'], unique=False)

def downgrade() -> None:
    op.drop_index(op.f('ix_trade_history_user_id'), table_name='trade_history')
    op.drop_table('trade_history')
    op.drop_index(op.f('ix_share_reservations_user_id'), table_name='share_reservations')
    op.drop_table('share_reservations')
    op.drop_table('processed_events')
    op.drop_index(op.f('ix_holdings_user_id'), table_name='holdings')
    op.drop_table('holdings')
