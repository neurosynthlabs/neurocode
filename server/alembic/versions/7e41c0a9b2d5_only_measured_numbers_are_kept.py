"""only measured numbers are kept

A workspace no longer starts on sample work, and the columns that only the sample ever filled go with
it. Each one was a number shown as a measurement that nothing measured:

- `projects.memory_pct` was written by the seed loader and by nothing else, so every real project read 0.
- `projects.understood_pct` was a step count dressed as a percentage. It becomes the share of the files
  the scan found that the code index holds, and null until a project has been indexed — worked out here
  for the projects that already are.
- `agents.model`, `fallback_model`, `tasks_done`, `success_rate` and `avg_minutes` were never served:
  the roster screen derives all of them from the runs, the router and the ledger.
- `plans.confidence` becomes nullable, and loses the number the offline planner invented — a plan's
  confidence is only ever what a model said of it.

And the `workspace.seeded` marker, which recorded whether the sample had been offered, is deleted: there
is no sample to offer.

Revision ID: 7e41c0a9b2d5
Revises: 3134f34a6182
Create Date: 2026-09-16 18:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy          # autogenerate renders pgvector types without importing them


# revision identifiers, used by Alembic.
revision: str = '7e41c0a9b2d5'
down_revision: Union[str, Sequence[str], None] = '3134f34a6182'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column('projects', 'memory_pct')

    op.alter_column('projects', 'understood_pct', existing_type=sa.Integer(), nullable=True, server_default=None)
    op.execute(
        "UPDATE projects p SET understood_pct = CASE "
        "  WHEN p.files_count = 0 OR NOT EXISTS (SELECT 1 FROM code_index_runs r WHERE r.project_id = p.id) "
        "  THEN NULL "
        "  ELSE LEAST(100, round(100.0 * (SELECT count(*) FROM code_files f WHERE f.project_id = p.id) "
        "                        / p.files_count))::int END")

    for column in ('model', 'fallback_model', 'tasks_done', 'success_rate', 'avg_minutes'):
        op.drop_column('agents', column)

    op.alter_column('plans', 'confidence', existing_type=sa.Integer(), nullable=True, server_default=None)
    op.execute("UPDATE plans SET confidence = NULL WHERE compiler->>'provider' = 'rules'")

    op.execute("DELETE FROM settings WHERE key = 'workspace.seeded'")


def downgrade() -> None:
    """Downgrade schema. The numbers themselves are not restored: nothing measured them."""
    op.execute("UPDATE plans SET confidence = 0 WHERE confidence IS NULL")
    op.alter_column('plans', 'confidence', existing_type=sa.Integer(), nullable=False, server_default='0')

    op.add_column('agents', sa.Column('avg_minutes', sa.Integer(), server_default='0', nullable=False))
    op.add_column('agents', sa.Column('success_rate', sa.Integer(), server_default='0', nullable=False))
    op.add_column('agents', sa.Column('tasks_done', sa.Integer(), server_default='0', nullable=False))
    op.add_column('agents', sa.Column('fallback_model', sa.String(length=120), server_default='', nullable=False))
    op.add_column('agents', sa.Column('model', sa.String(length=120), server_default='', nullable=False))

    op.execute("UPDATE projects SET understood_pct = 0 WHERE understood_pct IS NULL")
    op.alter_column('projects', 'understood_pct', existing_type=sa.Integer(), nullable=False, server_default='0')
    op.add_column('projects', sa.Column('memory_pct', sa.Integer(), server_default='0', nullable=False))
