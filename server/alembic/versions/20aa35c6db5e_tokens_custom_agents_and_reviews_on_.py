"""tokens, custom agents and reviews on demand

- `api_tokens`: personal tokens for scripts and the terminal client, stored as hashes, scoped, expiring,
  revocable.
- `custom_agents`: agents a person defines for the workspace or a project, capped by the tool rules.
- `code_reviews`: reviews of any diff a person asks for, tied by fingerprint to the exact patch read.

Revision ID: 20aa35c6db5e
Revises: d3be52ee66ef
Create Date: 2026-09-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '20aa35c6db5e'
down_revision: Union[str, Sequence[str], None] = 'd3be52ee66ef'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('api_tokens',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('user_id', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('prefix', sa.String(length=16), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('scopes', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_api_tokens_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_api_tokens')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_api_tokens_token_hash'))
    )
    op.create_index(op.f('ix_api_tokens_user_id'), 'api_tokens', ['user_id'], unique=False)
    op.create_table('code_reviews',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('ref', sa.String(length=40), nullable=False),
    sa.Column('project_id', sa.String(length=40), nullable=False),
    sa.Column('source', sa.String(length=60), server_default='', nullable=False),
    sa.Column('target', sa.String(length=16), nullable=False),
    sa.Column('base', sa.Text(), server_default='', nullable=False),
    sa.Column('head', sa.Text(), server_default='', nullable=False),
    sa.Column('fingerprint', sa.String(length=64), server_default='', nullable=False),
    sa.Column('stats', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('findings', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('verdict', sa.Text(), server_default='', nullable=False),
    sa.Column('status', sa.String(length=10), server_default='running', nullable=False),
    sa.Column('lane', sa.String(length=40), nullable=True),
    sa.Column('model', sa.String(length=120), nullable=True),
    sa.Column('requested_by', sa.String(length=40), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('running', 'done', 'failed')", name=op.f('ck_code_reviews_status')),
    sa.CheckConstraint("target IN ('branch', 'working-tree', 'commit-range')", name=op.f('ck_code_reviews_target')),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_code_reviews_project_id_projects'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['requested_by'], ['users.id'], name=op.f('fk_code_reviews_requested_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_code_reviews')),
    sa.UniqueConstraint('ref', name=op.f('uq_code_reviews_ref'))
    )
    op.create_index(op.f('ix_code_reviews_project_id'), 'code_reviews', ['project_id'], unique=False)
    op.create_table('custom_agents',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('project_id', sa.String(length=40), nullable=True),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('role', sa.String(length=160), server_default='', nullable=False),
    sa.Column('prompt', sa.Text(), server_default='', nullable=False),
    sa.Column('lane', sa.String(length=40), nullable=True),
    sa.Column('tools', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('max_steps', sa.Integer(), server_default='8', nullable=False),
    sa.Column('mode', sa.String(length=10), server_default='subagent', nullable=False),
    sa.Column('created_by', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("mode IN ('primary', 'subagent')", name=op.f('ck_custom_agents_mode')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_custom_agents_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_custom_agents_project_id_projects'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_custom_agents')),
    sa.UniqueConstraint('project_id', 'name', name=op.f('uq_custom_agents_project_id_name'))
    )
    op.create_index(op.f('ix_custom_agents_project_id'), 'custom_agents', ['project_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_custom_agents_project_id'), table_name='custom_agents')
    op.drop_table('custom_agents')
    op.drop_index(op.f('ix_code_reviews_project_id'), table_name='code_reviews')
    op.drop_table('code_reviews')
    op.drop_index(op.f('ix_api_tokens_user_id'), table_name='api_tokens')
    op.drop_table('api_tokens')
