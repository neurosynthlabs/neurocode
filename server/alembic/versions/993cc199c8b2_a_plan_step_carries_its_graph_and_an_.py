"""a plan step carries its graph, and an interrupted run says so

Two things the runtime needed and the schema did not have:

- `plan_steps.after`, `.when` and `.retry` — what a workflow's graph says about a step, carried into the plan
  the run actually executes. Empty on every step that exists, which is the ordered list a compiled plan has
  always been.
- `run_status` gains `interrupted`. A process that stopped while a run was working did not fail: the run can
  carry on from the last step it finished, and calling that `failed` sent a person looking for a mistake that
  was never made. Alembic does not diff enum values, so the ALTER TYPE is written by hand, outside the
  migration's transaction, as Postgres requires.


Revision ID: 993cc199c8b2
Revises: 95bad98c334e
Create Date: 2026-09-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '993cc199c8b2'
down_revision: Union[str, Sequence[str], None] = '95bad98c334e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('plan_steps', sa.Column('after', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('plan_steps', sa.Column('when', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False))
    op.add_column('plan_steps', sa.Column('retry', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False))
    _add_interrupted()


def downgrade() -> None:
    # The `interrupted` value stays: Postgres cannot drop one without rebuilding the type, and a run
    # already marked with it would have nothing to be.
    op.drop_column('plan_steps', 'retry')
    op.drop_column('plan_steps', 'when')
    op.drop_column('plan_steps', 'after')


def _add_interrupted() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'interrupted'")
