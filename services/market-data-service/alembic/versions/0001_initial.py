"""initial

Revision ID: 0001
Revises: 
Create Date: 2026-08-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '0001'
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table('symbols',
    sa.Column('symbol', sa.String(length=10), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('seed_price', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('symbol')
    )
    
    op.create_table('trades',
    sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('trade_id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('symbol', sa.String(length=10), nullable=False),
    sa.Column('price', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('quantity', sa.BigInteger(), nullable=False),
    sa.Column('aggressor_side', sa.String(length=4), nullable=True),
    sa.Column('executed_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('trade_id')
    )
    op.create_index('ix_trades_symbol_time', 'trades', ['symbol', 'executed_at'], unique=False)

    op.create_table('candles',
    sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('symbol', sa.String(length=10), nullable=False),
    sa.Column('interval', sa.String(length=4), nullable=False),
    sa.Column('bucket_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('open', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('high', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('low', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('close', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('volume', sa.BigInteger(), nullable=False),
    sa.Column('trade_count', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('symbol', 'interval', 'bucket_start', name='uq_candle_bucket')
    )
    op.create_index('ix_candles_lookup', 'candles', ['symbol', 'interval', 'bucket_start'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_candles_lookup', table_name='candles')
    op.drop_table('candles')
    op.drop_index('ix_trades_symbol_time', table_name='trades')
    op.drop_table('trades')
    op.drop_table('symbols')
