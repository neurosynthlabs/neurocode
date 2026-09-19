"""sessions asked through an agent

- `chats.agent`: the agent a session is asked through ("Ask <agent>") — `custom:<id>`, `file:<name>` or a
  built-in agent's id; null for an ordinary session.

Revision ID: e78fbd4aef84
Revises: 20aa35c6db5e
Create Date: 2026-09-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e78fbd4aef84'
down_revision: Union[str, Sequence[str], None] = '20aa35c6db5e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('chats', sa.Column('agent', sa.String(length=120), nullable=True))


def downgrade() -> None:
    op.drop_column('chats', 'agent')
