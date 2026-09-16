"""the screens get real back ends

The tables behind the screens that were sample data: what a test run found (its failures, its coverage,
and a person's standing word about a test), workflows a person wrote, eval suites and their runs, and
research with its angles and citations. Plus three columns on tables that already existed: which run a
model call was for, which workflow a plan came from, and the totals a run's tests reported.

Three things here are written by hand, because autogenerate cannot see them:

- `run_role` gains `check`. Alembic never diffs the values inside an enum type.
- The new enum types are created up front, once, and dropped after the tables that use them. Left to
  `create_table`, a type is created again by every column that names it — `eval_results` names
  `eval_status` twice — and an existing type such as `run_status` is created a second time and refused.
- So every enum column below names its type with `create_type=False`, including the array of
  `chunk_kind`, which reuses the type retrieval already has.

Revision ID: 3134f34a6182
Revises: d226f409c8fa
Create Date: 2026-09-16 15:13:54.540834

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy          # autogenerate renders pgvector types without importing them
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '3134f34a6182'
down_revision: Union[str, Sequence[str], None] = 'd226f409c8fa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The types this revision makes.
eval_kind = postgresql.ENUM('regression', 'capability', 'safety', 'cost', name='eval_kind', create_type=False)
eval_target = postgresql.ENUM('compile', 'ask', 'retrieval', 'review', 'prompt', name='eval_target',
                              create_type=False)
eval_status = postgresql.ENUM('pass', 'fail', 'partial', 'error', name='eval_status', create_type=False)
test_expectation = postgresql.ENUM('legacy', 'quarantine', name='test_expectation', create_type=False)
NEW_TYPES = (eval_kind, eval_target, eval_status, test_expectation)

# The types it reuses, which exist already.
chunk_kind = postgresql.ENUM('code', 'doc', 'memory', name='chunk_kind', create_type=False)
run_status = postgresql.ENUM('queued', 'running', 'waiting', 'done', 'failed', 'cancelled', name='run_status',
                             create_type=False)
run_step_status = postgresql.ENUM('todo', 'running', 'waiting', 'done', 'failed', 'skipped',
                                  name='run_step_status', create_type=False)


def upgrade() -> None:
    """Upgrade schema."""
    # A value added inside a transaction cannot be used until that transaction commits, and Postgres
    # before 12 refuses the ALTER inside one at all. Its own autocommit block settles both.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE run_role ADD VALUE IF NOT EXISTS 'check'")

    bind = op.get_bind()
    for kind in NEW_TYPES:
        kind.create(bind, checkfirst=True)

    op.create_table('eval_suites',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('name', postgresql.CITEXT(), nullable=False),
    sa.Column('kind', eval_kind, server_default='capability', nullable=False),
    sa.Column('target_kind', eval_target, nullable=False),
    sa.Column('project_id', sa.String(length=40), nullable=True),
    sa.Column('lane', sa.String(length=40), nullable=True),
    sa.Column('system_prompt', sa.Text(), server_default='', nullable=False),
    sa.Column('threshold', sa.Integer(), server_default='90', nullable=False),
    sa.Column('allow_offline', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('description', sa.Text(), server_default='', nullable=False),
    sa.Column('created_by', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("target_kind NOT IN ('compile', 'ask', 'retrieval') OR project_id IS NOT NULL", name=op.f('ck_eval_suites_project_when_target_reads_one')),
    sa.CheckConstraint('threshold BETWEEN 0 AND 100', name=op.f('ck_eval_suites_threshold_is_a_percentage')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_eval_suites_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_eval_suites_project_id_projects'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_eval_suites')),
    sa.UniqueConstraint('name', name=op.f('uq_eval_suites_name'))
    )
    op.create_index(op.f('ix_eval_suites_kind'), 'eval_suites', ['kind'], unique=False)
    op.create_table('research_reports',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('ref', sa.String(length=40), nullable=False),
    sa.Column('project_id', sa.String(length=40), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('kinds', postgresql.ARRAY(chunk_kind), server_default='{code,doc,memory}', nullable=False),
    sa.Column('status', run_status, server_default='queued', nullable=False),
    sa.Column('requested_by', sa.String(length=120), server_default='', nullable=False),
    sa.Column('user_id', sa.String(length=40), nullable=True),
    sa.Column('summary', sa.Text(), server_default='', nullable=False),
    sa.Column('recommendation', sa.Text(), server_default='', nullable=False),
    sa.Column('architecture', sa.Text(), server_default='', nullable=False),
    sa.Column('risks', postgresql.ARRAY(sa.Text()), server_default='{}', nullable=False),
    sa.Column('gaps', postgresql.ARRAY(sa.Text()), server_default='{}', nullable=False),
    sa.Column('alternatives', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('lane', sa.String(length=40), nullable=True),
    sa.Column('model', sa.String(length=120), nullable=True),
    sa.Column('fallback', sa.Text(), server_default='', nullable=False),
    sa.Column('note', sa.Text(), server_default='', nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('cardinality(kinds) >= 1', name=op.f('ck_research_reports_reads_something')),
    sa.CheckConstraint('char_length(question) BETWEEN 3 AND 2000', name=op.f('ck_research_reports_question_length')),
    sa.CheckConstraint('finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at', name=op.f('ck_research_reports_finishes_after_it_starts')),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_research_reports_project_id_projects'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_research_reports_user_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_research_reports')),
    sa.UniqueConstraint('ref', name=op.f('uq_research_reports_ref'))
    )
    op.create_index('ix_research_reports_project_id_created_at', 'research_reports', ['project_id', 'created_at'], unique=False)
    op.create_index('ix_research_reports_status_created_at', 'research_reports', ['status', 'created_at'], unique=False)
    op.create_table('test_expectations',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('project_id', sa.String(length=40), nullable=False),
    sa.Column('test_name', sa.Text(), nullable=False),
    sa.Column('kind', test_expectation, nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('by_user_id', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['by_user_id'], ['users.id'], name=op.f('fk_test_expectations_by_user_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_test_expectations_project_id_projects'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_test_expectations')),
    sa.UniqueConstraint('project_id', 'test_name', name=op.f('uq_test_expectations_project_id_test_name'))
    )
    op.create_table('workflow_definitions',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('name', postgresql.CITEXT(), nullable=False),
    sa.Column('description', sa.Text(), server_default='', nullable=False),
    sa.Column('project_id', sa.String(length=40), nullable=True),
    sa.Column('requirement_template', sa.Text(), nullable=False),
    sa.Column('created_by', sa.String(length=40), nullable=True),
    sa.Column('archived', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("strpos(requirement_template, '{input}') > 0", name=op.f('ck_workflow_definitions_template_takes_input')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_workflow_definitions_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_workflow_definitions_project_id_projects'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_workflow_definitions')),
    sa.UniqueConstraint('name', name=op.f('uq_workflow_definitions_name'))
    )
    op.create_index(op.f('ix_workflow_definitions_project_id'), 'workflow_definitions', ['project_id'], unique=False)
    op.create_table('eval_runs',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('ref', sa.String(length=40), nullable=False),
    sa.Column('suite_id', sa.String(length=40), nullable=False),
    sa.Column('status', run_status, server_default='queued', nullable=False),
    sa.Column('lane', sa.String(length=40), nullable=True),
    sa.Column('score', sa.Integer(), nullable=True),
    sa.Column('passed', sa.Integer(), server_default='0', nullable=False),
    sa.Column('failed', sa.Integer(), server_default='0', nullable=False),
    sa.Column('partial', sa.Integer(), server_default='0', nullable=False),
    sa.Column('errored', sa.Integer(), server_default='0', nullable=False),
    sa.Column('requested_by', sa.String(length=40), nullable=True),
    sa.Column('note', sa.Text(), server_default='', nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('score BETWEEN 0 AND 100', name=op.f('ck_eval_runs_score_is_a_percentage')),
    sa.ForeignKeyConstraint(['requested_by'], ['users.id'], name=op.f('fk_eval_runs_requested_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['suite_id'], ['eval_suites.id'], name=op.f('fk_eval_runs_suite_id_eval_suites'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_eval_runs')),
    sa.UniqueConstraint('ref', name=op.f('uq_eval_runs_ref'))
    )
    op.create_index(op.f('ix_eval_runs_status'), 'eval_runs', ['status'], unique=False)
    op.create_index('ix_eval_runs_suite_id_finished_at', 'eval_runs', ['suite_id', 'finished_at'], unique=False, postgresql_where=sa.text("status = 'done'"))
    op.create_table('research_angles',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('report_id', sa.String(length=40), nullable=False),
    sa.Column('n', sa.SmallInteger(), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('status', run_step_status, server_default='todo', nullable=False),
    sa.Column('hits', sa.Integer(), server_default='0', nullable=False),
    sa.Column('hits_lexical', sa.Integer(), server_default='0', nullable=False),
    sa.Column('hits_semantic', sa.Integer(), server_default='0', nullable=False),
    sa.Column('finding', sa.Text(), server_default='', nullable=False),
    sa.Column('lane', sa.String(length=40), nullable=True),
    sa.Column('model', sa.String(length=120), nullable=True),
    sa.Column('ms', sa.Integer(), nullable=True),
    sa.Column('error', sa.Text(), server_default='', nullable=False),
    sa.CheckConstraint('hits >= 0', name=op.f('ck_research_angles_hits_not_negative')),
    sa.CheckConstraint('n BETWEEN 1 AND 8', name=op.f('ck_research_angles_n_in_range')),
    sa.ForeignKeyConstraint(['report_id'], ['research_reports.id'], name=op.f('fk_research_angles_report_id_research_reports'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_research_angles')),
    sa.UniqueConstraint('report_id', 'n', name=op.f('uq_research_angles_report_id_n'))
    )
    op.create_table('workflow_steps',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('workflow_id', sa.String(length=40), nullable=False),
    sa.Column('n', sa.Integer(), nullable=False),
    sa.Column('label', sa.Text(), nullable=False),
    sa.Column('agent', sa.String(length=120), nullable=False),
    sa.Column('detail', sa.Text(), server_default='', nullable=False),
    sa.CheckConstraint('n >= 1', name=op.f('ck_workflow_steps_n_positive')),
    sa.ForeignKeyConstraint(['workflow_id'], ['workflow_definitions.id'], name=op.f('fk_workflow_steps_workflow_id_workflow_definitions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_workflow_steps')),
    sa.UniqueConstraint('workflow_id', 'n', name=op.f('uq_workflow_steps_workflow_id_n'))
    )
    op.create_table('eval_cases',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('suite_id', sa.String(length=40), nullable=False),
    sa.Column('n', sa.Integer(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('input', sa.Text(), nullable=False),
    sa.Column('checks', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('weight', sa.Integer(), server_default='1', nullable=False),
    sa.Column('source_plan_id', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("jsonb_typeof(checks) = 'array' AND jsonb_array_length(checks) >= 1", name=op.f('ck_eval_cases_at_least_one_check')),
    sa.CheckConstraint('weight >= 1', name=op.f('ck_eval_cases_weight_positive')),
    sa.ForeignKeyConstraint(['source_plan_id'], ['plans.id'], name=op.f('fk_eval_cases_source_plan_id_plans'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['suite_id'], ['eval_suites.id'], name=op.f('fk_eval_cases_suite_id_eval_suites'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_eval_cases')),
    sa.UniqueConstraint('suite_id', 'n', name=op.f('uq_eval_cases_suite_id_n'))
    )
    op.create_table('research_citations',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('report_id', sa.String(length=40), nullable=False),
    sa.Column('angle_id', sa.BigInteger(), nullable=False),
    sa.Column('n', sa.SmallInteger(), nullable=False),
    sa.Column('kind', chunk_kind, nullable=False),
    sa.Column('ref', sa.Text(), nullable=False),
    sa.Column('path', sa.Text(), server_default='', nullable=False),
    sa.Column('line', sa.Integer(), server_default='0', nullable=False),
    sa.Column('title', sa.Text(), server_default='', nullable=False),
    sa.Column('excerpt', sa.Text(), server_default='', nullable=False),
    sa.ForeignKeyConstraint(['angle_id'], ['research_angles.id'], name=op.f('fk_research_citations_angle_id_research_angles'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['report_id'], ['research_reports.id'], name=op.f('fk_research_citations_report_id_research_reports'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_research_citations')),
    sa.UniqueConstraint('angle_id', 'kind', 'ref', name=op.f('uq_research_citations_angle_id_kind_ref'))
    )
    op.create_index('ix_research_citations_report_id_n', 'research_citations', ['report_id', 'n'], unique=False)
    op.create_table('eval_results',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.String(length=40), nullable=False),
    sa.Column('case_id', sa.String(length=40), nullable=False),
    sa.Column('status', eval_status, nullable=False),
    sa.Column('score', sa.Numeric(precision=4, scale=3), nullable=False),
    sa.Column('output', sa.Text(), server_default='', nullable=False),
    sa.Column('checks', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('lane', sa.String(length=40), server_default='', nullable=False),
    sa.Column('model', sa.String(length=120), server_default='', nullable=False),
    sa.Column('ms', sa.Integer(), nullable=True),
    sa.Column('offline', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('error', sa.Text(), server_default='', nullable=False),
    sa.Column('judge_model', sa.String(length=120), nullable=True),
    sa.Column('judge_reason', sa.Text(), server_default='', nullable=False),
    sa.Column('override_status', eval_status, nullable=True),
    sa.Column('override_note', sa.Text(), server_default='', nullable=False),
    sa.Column('override_by', sa.String(length=40), nullable=True),
    sa.Column('overridden_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('score BETWEEN 0 AND 1', name=op.f('ck_eval_results_score_is_a_fraction')),
    sa.ForeignKeyConstraint(['case_id'], ['eval_cases.id'], name=op.f('fk_eval_results_case_id_eval_cases'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['override_by'], ['users.id'], name=op.f('fk_eval_results_override_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['run_id'], ['eval_runs.id'], name=op.f('fk_eval_results_run_id_eval_runs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_eval_results')),
    sa.UniqueConstraint('run_id', 'case_id', name=op.f('uq_eval_results_run_id_case_id'))
    )
    op.create_index('ix_eval_results_case_id_at', 'eval_results', ['case_id', 'at'], unique=False)
    op.create_table('test_coverage',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.String(length=40), nullable=False),
    sa.Column('path', sa.Text(), nullable=False),
    sa.Column('covered', sa.Integer(), nullable=False),
    sa.Column('total', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.CheckConstraint('covered >= 0', name=op.f('ck_test_coverage_covered_not_negative')),
    sa.CheckConstraint('total >= covered', name=op.f('ck_test_coverage_total_covers_covered')),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], name=op.f('fk_test_coverage_run_id_runs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_test_coverage')),
    sa.UniqueConstraint('run_id', 'path', name=op.f('uq_test_coverage_run_id_path'))
    )
    op.create_table('test_failures',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.String(length=40), nullable=False),
    sa.Column('step_n', sa.Integer(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('file', sa.Text(), server_default='', nullable=False),
    sa.Column('line', sa.Integer(), nullable=True),
    sa.Column('message', sa.Text(), server_default='', nullable=False),
    sa.Column('excerpt', sa.Text(), server_default='', nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], name=op.f('fk_test_failures_run_id_runs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_test_failures')),
    sa.UniqueConstraint('run_id', 'file', 'name', name=op.f('uq_test_failures_run_id_file_name'))
    )
    op.create_index(op.f('ix_test_failures_name'), 'test_failures', ['name'], unique=False)
    op.add_column('ai_calls', sa.Column('run_id', sa.String(length=40), nullable=True))
    op.create_index('ix_ai_calls_run_id', 'ai_calls', ['run_id'], unique=False, postgresql_where=sa.text('run_id IS NOT NULL'))
    op.create_foreign_key(op.f('fk_ai_calls_run_id_runs'), 'ai_calls', 'runs', ['run_id'], ['id'], ondelete='SET NULL')
    op.add_column('plans', sa.Column('workflow_id', sa.String(length=40), nullable=True))
    op.create_index('ix_plans_workflow_id_created_at', 'plans', ['workflow_id', 'created_at'], unique=False)
    op.create_foreign_key(op.f('fk_plans_workflow_id_workflow_definitions'), 'plans', 'workflow_definitions', ['workflow_id'], ['id'], ondelete='SET NULL')
    op.add_column('runs', sa.Column('tests_passed', sa.Integer(), nullable=True))
    op.add_column('runs', sa.Column('tests_failed', sa.Integer(), nullable=True))
    op.add_column('runs', sa.Column('tests_skipped', sa.Integer(), nullable=True))
    op.add_column('runs', sa.Column('tests_total', sa.Integer(), nullable=True))
    op.add_column('runs', sa.Column('tests_sha', sa.String(length=64), server_default='', nullable=False))
    op.add_column('runs', sa.Column('tests_runner', sa.String(length=20), server_default='', nullable=False))
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('runs', 'tests_runner')
    op.drop_column('runs', 'tests_sha')
    op.drop_column('runs', 'tests_total')
    op.drop_column('runs', 'tests_skipped')
    op.drop_column('runs', 'tests_failed')
    op.drop_column('runs', 'tests_passed')
    op.drop_constraint(op.f('fk_plans_workflow_id_workflow_definitions'), 'plans', type_='foreignkey')
    op.drop_index('ix_plans_workflow_id_created_at', table_name='plans')
    op.drop_column('plans', 'workflow_id')
    op.drop_constraint(op.f('fk_ai_calls_run_id_runs'), 'ai_calls', type_='foreignkey')
    op.drop_index('ix_ai_calls_run_id', table_name='ai_calls', postgresql_where=sa.text('run_id IS NOT NULL'))
    op.drop_column('ai_calls', 'run_id')
    op.drop_index(op.f('ix_test_failures_name'), table_name='test_failures')
    op.drop_table('test_failures')
    op.drop_table('test_coverage')
    op.drop_index('ix_eval_results_case_id_at', table_name='eval_results')
    op.drop_table('eval_results')
    op.drop_index('ix_research_citations_report_id_n', table_name='research_citations')
    op.drop_table('research_citations')
    op.drop_table('eval_cases')
    op.drop_table('workflow_steps')
    op.drop_table('research_angles')
    op.drop_index('ix_eval_runs_suite_id_finished_at', table_name='eval_runs', postgresql_where=sa.text("status = 'done'"))
    op.drop_index(op.f('ix_eval_runs_status'), table_name='eval_runs')
    op.drop_table('eval_runs')
    op.drop_index(op.f('ix_workflow_definitions_project_id'), table_name='workflow_definitions')
    op.drop_table('workflow_definitions')
    op.drop_table('test_expectations')
    op.drop_index('ix_research_reports_status_created_at', table_name='research_reports')
    op.drop_index('ix_research_reports_project_id_created_at', table_name='research_reports')
    op.drop_table('research_reports')
    op.drop_index(op.f('ix_eval_suites_kind'), table_name='eval_suites')
    op.drop_table('eval_suites')
    # ### end Alembic commands ###

    bind = op.get_bind()
    for kind in NEW_TYPES:
        kind.drop(bind, checkfirst=True)
    # `check` stays in run_role: Postgres cannot drop a value from an enum type without rebuilding the
    # type and every column that uses it, and a value nothing uses costs nothing.
