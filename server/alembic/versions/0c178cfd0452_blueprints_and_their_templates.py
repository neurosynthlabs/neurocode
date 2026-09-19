"""blueprints and their templates

`blueprints`: systems designed in the Blueprint wizard — the answers, the architecture they became, and
the project scaffolded from them. `blueprint_templates`: a person's own architecture templates, beside the
catalogue's (which ship as files, like the permission catalogue).

Revision ID: 0c178cfd0452
Revises: 5b607e469316
Create Date: 2026-09-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0c178cfd0452'
down_revision: Union[str, Sequence[str], None] = '5b607e469316'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('blueprint_templates',
    sa.Column('id', sa.String(length=80), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('description', sa.Text(), server_default='', nullable=False),
    sa.Column('spec', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('created_by', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_blueprint_templates_created_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_blueprint_templates'))
    )
    op.create_table('blueprints',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('template', sa.String(length=80), server_default='', nullable=False),
    sa.Column('answers', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('spec', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('status', sa.String(length=12), server_default='draft', nullable=False),
    sa.Column('revision', sa.Integer(), server_default='1', nullable=False),
    sa.Column('project_id', sa.String(length=40), nullable=True),
    sa.Column('created_by', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('draft', 'final', 'scaffolded')", name=op.f('ck_blueprints_status')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_blueprints_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_blueprints_project_id_projects'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_blueprints'))
    )


def downgrade() -> None:
    op.drop_table('blueprints')
    op.drop_table('blueprint_templates')
