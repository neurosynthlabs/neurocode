"""recalls are recorded, and MCP servers are checked

Two sets of numbers that looked measured and were not.

- A fact carried `strength` (80 on the day it was written, and never again anything else) and `hits`
  (which nothing incremented). They go. In their place `memory_hits` keeps a row each time a feature
  cites a fact or hands it to a model, so how often a fact is used is counted from what happened.
  `archived_at` records when a fact was retired; facts archived before now have no such moment on
  record, so theirs stays null rather than borrowing another timestamp.
- An MCP server carried `calls_24h`, `error_rate` and a latency that only the sample ever wrote. The
  counters go; latency, resources and prompts become nullable and are cleared, and `checked_at` and
  `last_error` arrive, because from now on they are written by a real check. The tools a server was
  listed with were never listed by the server either, so they are cleared too, and every server reads
  `disconnected` until it is checked.
- A conflict names two different facts, and a pair has at most one open conflict.

Revision ID: 586508bc5ed0
Revises: 70aaad7321c6
Create Date: 2026-09-16 20:19:51.917992

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy          # autogenerate renders pgvector types without importing them


# revision identifiers, used by Alembic.
revision: str = '586508bc5ed0'
down_revision: Union[str, Sequence[str], None] = '70aaad7321c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('memory_hits',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('fact_id', sa.String(length=40), nullable=False),
    sa.Column('feature', sa.String(length=20), nullable=False),
    sa.Column('ref', sa.String(length=40), nullable=True),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("feature IN ('ask', 'chat', 'retrieval', 'research', 'compile')", name=op.f('ck_memory_hits_known_feature')),
    sa.ForeignKeyConstraint(['fact_id'], ['memory_facts.id'], name=op.f('fk_memory_hits_fact_id_memory_facts'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_memory_hits'))
    )
    op.create_index(op.f('ix_memory_hits_at'), 'memory_hits', ['at'], unique=False)
    op.create_index('ix_memory_hits_fact_id_at', 'memory_hits', ['fact_id', 'at'], unique=False)
    op.add_column('memory_facts', sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True))
    op.drop_column('memory_facts', 'hits')
    op.drop_column('memory_facts', 'strength')

    op.execute("DELETE FROM memory_conflicts WHERE a = b")          # a fact cannot contradict itself
    op.create_check_constraint(op.f('ck_memory_conflicts_two_different_facts'), 'memory_conflicts', 'a <> b')
    op.execute(
        "UPDATE memory_conflicts c SET status = 'resolved' FROM memory_conflicts older "
        "WHERE c.status = 'open' AND older.status = 'open' AND c.id > older.id "
        "AND least(c.a, c.b) = least(older.a, older.b) AND greatest(c.a, c.b) = greatest(older.a, older.b)")
    op.create_index('uq_memory_conflicts_open_pair', 'memory_conflicts',
                    [sa.text('least(a, b)'), sa.text('greatest(a, b)')], unique=True,
                    postgresql_where=sa.text("status = 'open'"))

    op.add_column('mcp_servers', sa.Column('checked_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('mcp_servers', sa.Column('last_error', sa.Text(), server_default='', nullable=False))
    for column in ('resources', 'prompts', 'latency_ms'):
        op.alter_column('mcp_servers', column, existing_type=sa.INTEGER(), server_default=None, nullable=True)
    op.drop_column('mcp_servers', 'calls_24h')
    op.drop_column('mcp_servers', 'error_rate')
    op.execute("UPDATE mcp_servers SET resources = NULL, prompts = NULL, latency_ms = NULL, "
               "status = 'disconnected'")
    op.execute("DELETE FROM mcp_tools")


def downgrade() -> None:
    """Downgrade schema. The counters come back empty: nothing ever measured them, and the hits are dropped."""
    op.add_column('mcp_servers', sa.Column('error_rate', sa.NUMERIC(precision=5, scale=2), server_default=sa.text("'0'::numeric"), autoincrement=False, nullable=False))
    op.add_column('mcp_servers', sa.Column('calls_24h', sa.INTEGER(), server_default=sa.text('0'), autoincrement=False, nullable=False))
    for column in ('latency_ms', 'prompts', 'resources'):
        op.execute(f"UPDATE mcp_servers SET {column} = 0 WHERE {column} IS NULL")
        op.alter_column('mcp_servers', column, existing_type=sa.INTEGER(), server_default=sa.text('0'), nullable=False)
    op.drop_column('mcp_servers', 'last_error')
    op.drop_column('mcp_servers', 'checked_at')

    op.drop_index('uq_memory_conflicts_open_pair', table_name='memory_conflicts')
    op.drop_constraint(op.f('ck_memory_conflicts_two_different_facts'), 'memory_conflicts', type_='check')

    op.add_column('memory_facts', sa.Column('strength', sa.INTEGER(), server_default=sa.text('0'), autoincrement=False, nullable=False))
    op.add_column('memory_facts', sa.Column('hits', sa.INTEGER(), server_default=sa.text('0'), autoincrement=False, nullable=False))
    op.execute("UPDATE memory_facts f SET hits = (SELECT count(*) FROM memory_hits h WHERE h.fact_id = f.id)")
    op.drop_column('memory_facts', 'archived_at')
    op.drop_index('ix_memory_hits_fact_id_at', table_name='memory_hits')
    op.drop_index(op.f('ix_memory_hits_at'), table_name='memory_hits')
    op.drop_table('memory_hits')
