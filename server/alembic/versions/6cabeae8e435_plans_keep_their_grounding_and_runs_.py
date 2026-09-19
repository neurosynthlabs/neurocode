"""plans keep their grounding, and runs their push

Two records the screens will show, so neither can be a guess later:

- `plans.grounding`: the code and documents retrieval handed the compiler, `[{kind, ref, path}]`. A
  plan that names a file can then be checked against what it was shown, the way `cited` already
  lets a reader check the memory facts. Plans compiled before this were shown nothing, so `[]` is
  their true value.
- `runs.pushed`: the run's branch as pushed to the project's own remote. Null until someone pushes.

Revision ID: 6cabeae8e435
Revises: 42112be887ac
Create Date: 2026-09-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '6cabeae8e435'
down_revision: Union[str, Sequence[str], None] = '42112be887ac'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('plans', sa.Column('grounding', postgresql.JSONB(astext_type=sa.Text()),
                                     server_default='[]', nullable=False))
    op.add_column('runs', sa.Column('pushed', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('runs', 'pushed')
    op.drop_column('plans', 'grounding')
