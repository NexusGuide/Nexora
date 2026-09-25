"""wallet, top-ups and payment settings

Adds a wallet balance to every user (0 for existing accounts), the append-only
wallet ledger, the top-up review queue, and a key/value settings table for
the card and crypto details customers are shown.

Revision ID: c3e8a1f5d2b7
Revises: b7d1c2e9a4f0
Create Date: 2026-09-25 16:10:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3e8a1f5d2b7'
down_revision: Union[str, None] = 'b7d1c2e9a4f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('wallet_balance', sa.Numeric(14, 2), server_default='0', nullable=False),
    )
    op.create_check_constraint(
        'wallet_balance_non_negative', 'users', 'wallet_balance >= 0'
    )

    op.create_table(
        'app_settings',
        sa.Column('key', sa.String(64), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('updated_by', sa.String(36), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name=op.f('fk_app_settings_updated_by_users'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('key', name=op.f('pk_app_settings')),
    )

    op.create_table(
        'wallet_topups',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('user_id', sa.String(36), nullable=False),
        sa.Column('method', sa.String(16), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('amount', sa.Numeric(14, 2), nullable=False),
        sa.Column('currency', sa.String(8), nullable=False),
        sa.Column('reference', sa.String(128), nullable=False),
        sa.Column('payer_note', sa.String(255), nullable=True),
        sa.Column('destination', sa.String(255), nullable=False),
        sa.Column('network', sa.String(16), nullable=True),
        sa.Column('asset', sa.String(16), nullable=True),
        sa.Column('crypto_amount', sa.Numeric(24, 6), nullable=True),
        sa.Column('rate', sa.Numeric(18, 2), nullable=True),
        sa.Column('order_id', sa.String(36), nullable=True),
        sa.Column('reviewed_by', sa.String(36), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('credited_amount', sa.Numeric(14, 2), nullable=True),
        sa.Column('reject_reason', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_wallet_topups_user_id_users'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name=op.f('fk_wallet_topups_order_id_orders'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], name=op.f('fk_wallet_topups_reviewed_by_users'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_wallet_topups')),
    )
    op.create_index(op.f('ix_wallet_topups_user_id'), 'wallet_topups', ['user_id'])
    op.create_index(op.f('ix_wallet_topups_status'), 'wallet_topups', ['status'])
    op.create_index(op.f('ix_wallet_topups_order_id'), 'wallet_topups', ['order_id'])
    op.create_index('ix_wallet_topups_status_created', 'wallet_topups', ['status', 'created_at'])
    op.create_index('ix_wallet_topups_method_reference', 'wallet_topups', ['method', 'reference'])

    op.create_table(
        'wallet_transactions',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('user_id', sa.String(36), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('amount', sa.Numeric(14, 2), nullable=False),
        sa.Column('balance_after', sa.Numeric(14, 2), nullable=False),
        sa.Column('currency', sa.String(8), nullable=False),
        sa.Column('idempotency_key', sa.String(80), nullable=False),
        sa.Column('topup_id', sa.String(36), nullable=True),
        sa.Column('order_id', sa.String(36), nullable=True),
        sa.Column('actor_id', sa.String(36), nullable=True),
        sa.Column('note', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_wallet_transactions_user_id_users'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['topup_id'], ['wallet_topups.id'], name=op.f('fk_wallet_transactions_topup_id_wallet_topups'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name=op.f('fk_wallet_transactions_order_id_orders'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['actor_id'], ['users.id'], name=op.f('fk_wallet_transactions_actor_id_users'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_wallet_transactions')),
        sa.UniqueConstraint('idempotency_key', name=op.f('uq_wallet_transactions_idempotency_key')),
    )
    op.create_index(op.f('ix_wallet_transactions_user_id'), 'wallet_transactions', ['user_id'])
    op.create_index(op.f('ix_wallet_transactions_topup_id'), 'wallet_transactions', ['topup_id'])
    op.create_index(op.f('ix_wallet_transactions_order_id'), 'wallet_transactions', ['order_id'])
    op.create_index('ix_wallet_tx_user_created', 'wallet_transactions', ['user_id', 'created_at'])


def downgrade() -> None:
    op.drop_table('wallet_transactions')
    op.drop_table('wallet_topups')
    op.drop_table('app_settings')
    op.drop_constraint('wallet_balance_non_negative', 'users', type_='check')
    op.drop_column('users', 'wallet_balance')
