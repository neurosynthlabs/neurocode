"""a source can be a reference

`project_sources.role`: `code` is worked on — agents may change it, in worktrees; `reference` is only read —
documents, a design system, another team's repository — indexed for search and grounding and never written
to. Every existing source is code.

Revision ID: 21e18d25de2e
Revises: 0c178cfd0452
Create Date: 2026-09-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '21e18d25de2e'
down_revision: Union[str, Sequence[str], None] = '0c178cfd0452'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('project_sources', sa.Column('role', sa.String(length=10), server_default='code', nullable=False))
    op.create_check_constraint(op.f('ck_project_sources_role'), 'project_sources', "role IN ('code', 'reference')")


def downgrade() -> None:
    op.drop_constraint(op.f('ck_project_sources_role'), 'project_sources', type_='check')
    op.drop_column('project_sources', 'role')
