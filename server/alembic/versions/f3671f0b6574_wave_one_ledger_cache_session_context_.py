"""wave one: the ledger's cache and reasoning tokens, a session's measured context, goals, tool rules

- `ai_calls.tokens_cached` and `tokens_reasoning`: what the provider reported of the prompt served from
  its cache and of the answer spent reasoning. A cache hit is priced lower, and a reasoning token is paid
  for without being shown, so both change what a call cost. Older calls reported neither: 0.
- `chats.context_tokens`: the prompt size the provider counted on the session's last call, which the
  context meter shows against the lane's window. `chat_messages.reasoning` keeps what a model thought
  before answering; `compacted` marks turns a summary has replaced in what the model is sent, and the
  `summary` role is that summary.
- `plans.acceptance_criteria`, `runs.attempt` and `runs.goal_budget`: a goal run's criteria, which try it
  is, and how many the person allowed.
- `tool_rules`: allow, ask or deny, per tool and pattern, for the workspace or one project.

Revision ID: f3671f0b6574
Revises: 6cabeae8e435
Create Date: 2026-09-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f3671f0b6574'
down_revision: Union[str, Sequence[str], None] = '6cabeae8e435'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A value added inside a transaction cannot be used until that transaction commits: its own block.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE chat_role ADD VALUE IF NOT EXISTS 'summary'")

    op.create_table('tool_rules',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('project_id', sa.String(length=40), nullable=True),
    sa.Column('tool', sa.String(length=20), nullable=False),
    sa.Column('pattern', sa.Text(), nullable=False),
    sa.Column('action', sa.String(length=10), nullable=False),
    sa.Column('note', sa.Text(), server_default='', nullable=False),
    sa.Column('created_by', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("action IN ('allow', 'ask', 'deny')", name=op.f('ck_tool_rules_action')),
    sa.CheckConstraint("tool IN ('edit', 'command', 'read', 'web_fetch', 'web_search', 'mcp')", name=op.f('ck_tool_rules_tool')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_tool_rules_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_tool_rules_project_id_projects'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tool_rules'))
    )
    op.create_index(op.f('ix_tool_rules_project_id'), 'tool_rules', ['project_id'], unique=False)
    op.add_column('ai_calls', sa.Column('tokens_cached', sa.Integer(), server_default='0', nullable=False))
    op.add_column('ai_calls', sa.Column('tokens_reasoning', sa.Integer(), server_default='0', nullable=False))
    op.add_column('chat_messages', sa.Column('reasoning', sa.Text(), server_default='', nullable=False))
    op.add_column('chat_messages', sa.Column('compacted', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('chats', sa.Column('context_tokens', sa.Integer(), nullable=True))
    op.add_column('plans', sa.Column('acceptance_criteria', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('runs', sa.Column('attempt', sa.Integer(), server_default='1', nullable=False))
    op.add_column('runs', sa.Column('goal_budget', sa.Integer(), nullable=True))


def downgrade() -> None:
    # The 'summary' value stays in chat_role: Postgres cannot drop an enum value without rebuilding the
    # type, and an unused value costs nothing.
    op.drop_column('runs', 'goal_budget')
    op.drop_column('runs', 'attempt')
    op.drop_column('plans', 'acceptance_criteria')
    op.drop_column('chats', 'context_tokens')
    op.drop_column('chat_messages', 'compacted')
    op.drop_column('chat_messages', 'reasoning')
    op.drop_column('ai_calls', 'tokens_reasoning')
    op.drop_column('ai_calls', 'tokens_cached')
    op.drop_index(op.f('ix_tool_rules_project_id'), table_name='tool_rules')
    op.drop_table('tool_rules')
