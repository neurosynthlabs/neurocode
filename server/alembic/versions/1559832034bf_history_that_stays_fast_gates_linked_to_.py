"""history that stays fast, gates linked to their runs, rights inside a project

What a year of use was going to cost, and does not now:

- Deleting anything that cascades to a session read every message in the workspace once per turn, because
  the two self-references (`chats.parent_id`, `chat_messages.superseded_by`) led no index. Both are indexed
  here, partially — they are null on almost every row — along with the other children of things that get
  deleted, and the (parent, time) pairs the history screens actually order by.
- Every new run, plan, gate and session picked its number with `max(regexp_replace(ref, …))` over the whole
  table, under a lock. `ref_counters` holds one row per prefix instead, seeded here from those same maxima.
- A gate pointed at its run by free text, so a deleted run left its gates behind and every reader joined on
  a string. `approvals.run_id` is a real foreign key, backfilled from the refs; `run_ref` stays as the label
  people read. `approvals.seq` keeps the number the ref was built from, so gates written in one transaction
  have an order without a regular expression in the sort.
- The usage ledger had no constraints, so one odd provider answer could price a call that never happened.
  Existing rows are clamped to the honest floor first, then the floor is written into the schema.

And the rights a project can carry: `projects.restricted` and `project_roles`, which narrow what someone may
do inside one project — never widen it.


Revision ID: 1559832034bf
Revises: f375c1cf4bdb
Create Date: 2026-09-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy          # autogenerate renders pgvector types without importing them


# revision identifiers, used by Alembic.
revision: str = '1559832034bf'
down_revision: Union[str, Sequence[str], None] = 'f375c1cf4bdb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('ref_counters',
    sa.Column('prefix', sa.String(length=20), nullable=False),
    sa.Column('next', sa.BigInteger(), server_default='0', nullable=False),
    sa.PrimaryKeyConstraint('prefix', name=op.f('pk_ref_counters'))
    )
    op.create_table('project_roles',
    sa.Column('project_id', sa.String(length=40), nullable=False),
    sa.Column('user_id', sa.String(length=40), nullable=False),
    sa.Column('role_id', sa.String(length=40), nullable=False),
    sa.Column('granted_by', sa.String(length=40), nullable=True),
    sa.Column('granted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['granted_by'], ['users.id'], name=op.f('fk_project_roles_granted_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_project_roles_project_id_projects'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['role_id'], ['roles.id'], name=op.f('fk_project_roles_role_id_roles'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_project_roles_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('project_id', 'user_id', 'role_id', name=op.f('pk_project_roles'))
    )
    op.create_index('ix_project_roles_user_project', 'project_roles', ['user_id', 'project_id'], unique=False)
    op.create_index('ix_activity_project_id_seq', 'activity', ['project_id', 'seq'], unique=False)
    op.create_index('ix_ai_calls_at_failed', 'ai_calls', ['at'], unique=False, postgresql_where=sa.text('NOT ok'))
    op.create_index('ix_ai_calls_project_id', 'ai_calls', ['project_id'], unique=False, postgresql_where=sa.text('project_id IS NOT NULL'))
    op.add_column('approvals', sa.Column('run_id', sa.String(length=40), nullable=True))
    op.add_column('approvals', sa.Column('seq', sa.Integer(), nullable=True))
    op.create_index('ix_approvals_project_id', 'approvals', ['project_id'], unique=False, postgresql_where=sa.text('project_id IS NOT NULL'))
    op.create_index('ix_approvals_run_id', 'approvals', ['run_id'], unique=False, postgresql_where=sa.text('run_id IS NOT NULL'))
    op.create_foreign_key(op.f('fk_approvals_run_id_runs'), 'approvals', 'runs', ['run_id'], ['id'], ondelete='CASCADE')
    op.create_index('ix_chat_messages_superseded_by', 'chat_messages', ['superseded_by'], unique=False, postgresql_where=sa.text('superseded_by IS NOT NULL'))
    op.create_index('ix_chats_parent_id', 'chats', ['parent_id'], unique=False, postgresql_where=sa.text('parent_id IS NOT NULL'))
    op.add_column('projects', sa.Column('restricted', sa.Boolean(), server_default='false', nullable=False))
    op.create_index('ix_run_logs_at_id', 'run_logs', ['at', 'id'], unique=False)
    op.create_index(op.f('ix_run_steps_child_run_id'), 'run_steps', ['child_run_id'], unique=False)
    op.create_index(op.f('ix_runs_plan_id'), 'runs', ['plan_id'], unique=False)
    op.create_index(op.f('ix_runs_task_id'), 'runs', ['task_id'], unique=False)
    op.create_index('ix_schedule_fires_schedule_id_at', 'schedule_fires', ['schedule_id', 'at', 'id'], unique=False)
    op.create_index(op.f('ix_taste_signals_run_id'), 'taste_signals', ['run_id'], unique=False)

    # The numbers already handed out, so the counter carries on rather than starting again. APPR- keeps its
    # floor of 100, which the gates' own refs have always had.
    op.execute("""
        INSERT INTO ref_counters (prefix, next)
        SELECT prefix, coalesce(max(n), 0) FROM (
            SELECT 'RUN-' AS prefix, max(coalesce(nullif(regexp_replace(ref, '\\D', '', 'g'), '')::int, 0)) AS n FROM runs
            UNION ALL SELECT 'PLAN-', max(coalesce(nullif(regexp_replace(ref, '\\D', '', 'g'), '')::int, 0)) FROM plans
            UNION ALL SELECT 'TASK-', max(coalesce(nullif(regexp_replace(ref, '\\D', '', 'g'), '')::int, 0)) FROM tasks
            UNION ALL SELECT 'APPR-', greatest(100, coalesce(max(nullif(regexp_replace(ref, '\\D', '', 'g'), '')::int), 0)) FROM approvals
            UNION ALL SELECT 'SESSION-', max(coalesce(nullif(regexp_replace(ref, '\\D', '', 'g'), '')::int, 0)) FROM chats
            UNION ALL SELECT 'MEM-', max(coalesce(nullif(regexp_replace(ref, '\\D', '', 'g'), '')::int, 0)) FROM memory_facts
            UNION ALL SELECT 'IDEA-', max(coalesce(nullif(regexp_replace(ref, '\\D', '', 'g'), '')::int, 0)) FROM brainstorms
            UNION ALL SELECT 'REV-', max(coalesce(nullif(regexp_replace(ref, '\\D', '', 'g'), '')::int, 0)) FROM code_reviews
            UNION ALL SELECT 'RSCH-', max(coalesce(nullif(regexp_replace(ref, '\\D', '', 'g'), '')::int, 0)) FROM research_reports
        ) AS counted GROUP BY prefix
        ON CONFLICT (prefix) DO NOTHING
    """)

    # The gates' link to their runs, and the number their ref was built from.
    op.execute("UPDATE approvals a SET run_id = r.id FROM runs r WHERE a.run_ref = r.ref AND a.run_id IS NULL")
    op.execute("UPDATE approvals SET seq = nullif(regexp_replace(ref, '\\D', '', 'g'), '')::int WHERE seq IS NULL")

    # A provider's oddity becomes the honest floor before the floor becomes a rule.
    op.execute("""
        UPDATE ai_calls SET
            ms = greatest(ms, 0),
            tokens_in = greatest(tokens_in, 0),
            tokens_out = greatest(tokens_out, 0),
            tokens_cached = least(greatest(tokens_cached, 0), greatest(tokens_in, 0)),
            tokens_reasoning = least(greatest(tokens_reasoning, 0), greatest(tokens_out, 0))
        WHERE ms < 0 OR tokens_in < 0 OR tokens_out < 0 OR tokens_cached < 0 OR tokens_reasoning < 0
           OR tokens_cached > tokens_in OR tokens_reasoning > tokens_out
    """)
    op.create_check_constraint("counts_not_negative", "ai_calls",
                               "ms >= 0 AND tokens_in >= 0 AND tokens_out >= 0 AND tokens_cached >= 0 "
                               "AND tokens_reasoning >= 0")
    op.create_check_constraint("parts_fit_their_whole", "ai_calls",
                               "tokens_cached <= tokens_in AND tokens_reasoning <= tokens_out")


def downgrade() -> None:
    op.drop_constraint("ck_ai_calls_parts_fit_their_whole", "ai_calls", type_="check")
    op.drop_constraint("ck_ai_calls_counts_not_negative", "ai_calls", type_="check")
    op.drop_index(op.f('ix_taste_signals_run_id'), table_name='taste_signals')
    op.drop_index('ix_schedule_fires_schedule_id_at', table_name='schedule_fires')
    op.drop_index(op.f('ix_runs_task_id'), table_name='runs')
    op.drop_index(op.f('ix_runs_plan_id'), table_name='runs')
    op.drop_index(op.f('ix_run_steps_child_run_id'), table_name='run_steps')
    op.drop_index('ix_run_logs_at_id', table_name='run_logs')
    op.drop_column('projects', 'restricted')
    op.drop_index('ix_chats_parent_id', table_name='chats', postgresql_where=sa.text('parent_id IS NOT NULL'))
    op.drop_index('ix_chat_messages_superseded_by', table_name='chat_messages', postgresql_where=sa.text('superseded_by IS NOT NULL'))
    op.drop_constraint(op.f('fk_approvals_run_id_runs'), 'approvals', type_='foreignkey')
    op.drop_index('ix_approvals_run_id', table_name='approvals', postgresql_where=sa.text('run_id IS NOT NULL'))
    op.drop_index('ix_approvals_project_id', table_name='approvals', postgresql_where=sa.text('project_id IS NOT NULL'))
    op.drop_column('approvals', 'seq')
    op.drop_column('approvals', 'run_id')
    op.drop_index('ix_ai_calls_project_id', table_name='ai_calls', postgresql_where=sa.text('project_id IS NOT NULL'))
    op.drop_index('ix_ai_calls_at_failed', table_name='ai_calls', postgresql_where=sa.text('NOT ok'))
    op.drop_index('ix_activity_project_id_seq', table_name='activity')
    op.drop_index('ix_project_roles_user_project', table_name='project_roles')
    op.drop_table('project_roles')
    op.drop_table('ref_counters')
