"""tool rules that nothing enforced are dropped

`permission_rules` held patterns like `Bash(rm -rf:*) → deny`, with a count of hits in the last day. The
sample wrote them; nothing in the runtime ever read one, and nothing counted a hit. The rules NeuroCode
really applies are a project's answer to its first test run, kept as a setting the test step reads, and
GET /permissions/rules lists those. A table of rules that look enforced and are not is a false promise,
so it goes. Its rows are sample rows or nothing, which is why there is nothing to carry across.

Revision ID: 42112be887ac
Revises: 586508bc5ed0
Create Date: 2026-09-16 21:10:52.149810

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '42112be887ac'
down_revision: Union[str, Sequence[str], None] = '586508bc5ed0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table('permission_rules')


def downgrade() -> None:
    # The enum types are shared with other tables and still exist, so they are named, not created.
    risk = postgresql.ENUM('LOW', 'MEDIUM', 'HIGH', 'CRITICAL', name='risk', create_type=False)
    scope = postgresql.ENUM('global', 'project', 'local', name='scope', create_type=False)
    op.create_table(
        'permission_rules',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('pattern', sa.Text(), nullable=False),
        sa.Column('tool', sa.String(length=120), server_default='', nullable=False),
        sa.Column('effect', sa.String(length=20), server_default='ask', nullable=False),
        sa.Column('risk', risk, server_default='LOW', nullable=False),
        sa.Column('scope', scope, server_default='global', nullable=False),
        sa.Column('hits_24h', sa.Integer(), server_default='0', nullable=False),
        sa.Column('note', sa.Text(), server_default='', nullable=False),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_permission_rules')),
    )
