"""projects hold several sources

`project_sources`: further folders or repositories that belong to one project — the API beside the web
app, a shared library — each named by a label that is also the folder its files appear under inside the
project. The project's own source columns stay its first source, so every existing project is unchanged.

Revision ID: 5b607e469316
Revises: 87bd6d80b77d
Create Date: 2026-09-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5b607e469316'
down_revision: Union[str, Sequence[str], None] = '87bd6d80b77d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('project_sources',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('project_id', sa.String(length=40), nullable=False),
    sa.Column('label', sa.String(length=60), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('repo', sa.Text(), nullable=False),
    sa.Column('branch', sa.Text(), server_default='', nullable=False),
    sa.Column('position', sa.Integer(), server_default='0', nullable=False),
    sa.Column('status', sa.String(length=20), server_default='onboarding', nullable=False),
    sa.Column('note', sa.Text(), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('local', 'git')", name=op.f('ck_project_sources_kind')),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_project_sources_project_id_projects'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_project_sources')),
    sa.UniqueConstraint('project_id', 'label', name=op.f('uq_project_sources_project_id_label'))
    )
    op.create_index(op.f('ix_project_sources_project_id'), 'project_sources', ['project_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_project_sources_project_id'), table_name='project_sources')
    op.drop_table('project_sources')
