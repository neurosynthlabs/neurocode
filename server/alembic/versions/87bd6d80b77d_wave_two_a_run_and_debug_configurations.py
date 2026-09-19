"""wave two a: run and debug configurations

`run_configs`: how a person runs or debugs a project from the Workbench — a command in a folder of the
checkout, or a program and its arguments for a debugger — kept per project and removed with it.

Revision ID: 87bd6d80b77d
Revises: 849ead068781
Create Date: 2026-09-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '87bd6d80b77d'
down_revision: Union[str, Sequence[str], None] = '849ead068781'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('run_configs',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('project_id', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('kind', sa.String(length=10), server_default='run', nullable=False),
    sa.Column('language', sa.String(length=10), server_default='shell', nullable=False),
    sa.Column('command', sa.Text(), nullable=False),
    sa.Column('args', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('cwd', sa.Text(), server_default='', nullable=False),
    sa.Column('env', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('created_by', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('run', 'debug')", name=op.f('ck_run_configs_kind')),
    sa.CheckConstraint("language IN ('python', 'node', 'shell')", name=op.f('ck_run_configs_language')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_run_configs_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_run_configs_project_id_projects'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_run_configs'))
    )
    op.create_index(op.f('ix_run_configs_project_id'), 'run_configs', ['project_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_run_configs_project_id'), table_name='run_configs')
    op.drop_table('run_configs')
