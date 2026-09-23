"""panel default group ids

On a group-based panel (newer PasarGuard), inbound access comes from group
membership. Users created with no group exist but receive no link.

Revision ID: b7d1c2e9a4f0
Revises: 8381efd24bd8
Create Date: 2026-09-23 22:35:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7d1c2e9a4f0'
down_revision: Union[str, None] = '8381efd24bd8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable, no default: existing panels keep provisioning exactly as they
    # did until an operator chooses their groups.
    op.add_column('panels', sa.Column('default_group_ids', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('panels', 'default_group_ids')
