"""tools a person defines, rules that govern them, and an identity provider

- `custom_tools`: a tool a person wrote for their agents — a command on this machine, or an HTTP call, with a
  JSON Schema for its arguments. It is not a way around the rules; it is another thing the rules govern.
- `tool_rules.tool` accepts two more kinds: `tool` (one of those custom tools) and `hook` (a repository's own
  lifecycle hook, which this product reads and shows but has never run). Both start denied by absence: with
  no rule saying otherwise, a custom tool and a hook are refused, exactly as a command is.
- `users.idp` and `.idp_subject`: who vouches for a person when they signed in through an identity provider.


Revision ID: 05e6004426af
Revises: 993cc199c8b2
Create Date: 2026-09-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '05e6004426af'
down_revision: Union[str, Sequence[str], None] = '993cc199c8b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('custom_tools',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('project_id', sa.String(length=40), nullable=True),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('description', sa.Text(), server_default='', nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('spec', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('enabled', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_by', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('command', 'http')", name=op.f('ck_custom_tools_kind')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_custom_tools_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_custom_tools_project_id_projects'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_custom_tools')),
    sa.UniqueConstraint('project_id', 'name', name=op.f('uq_custom_tools_project_id_name'))
    )
    op.create_index(op.f('ix_custom_tools_project_id'), 'custom_tools', ['project_id'], unique=False)
    # A check constraint is not diffed by autogenerate, so the wider vocabulary is written by hand.
    op.drop_constraint("tool", "tool_rules", type_="check")
    op.create_check_constraint(
        "tool", "tool_rules",
        "tool IN ('edit', 'command', 'read', 'web_fetch', 'web_search', 'mcp', 'tool', 'hook')")
    op.add_column('users', sa.Column('idp', sa.String(length=40), server_default='', nullable=False))
    op.add_column('users', sa.Column('idp_subject', sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'idp_subject')
    op.drop_constraint("tool", "tool_rules", type_="check")
    op.create_check_constraint(
        "tool", "tool_rules", "tool IN ('edit', 'command', 'read', 'web_fetch', 'web_search', 'mcp')")
    op.drop_column('users', 'idp')
    op.drop_index(op.f('ix_custom_tools_project_id'), table_name='custom_tools')
    op.drop_table('custom_tools')
