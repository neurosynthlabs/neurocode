"""wave two: grants, questions, forks, uploads, plan comments, taste, routines, the inbox

- `runs.grants` and `chats.grants`: what a person allowed beyond the tool rules ("Allow once / for this
  run / for this session"), kept where the allowance applies.
- `run_steps.commit_sha`, `question`, `answer`: the worktree commit a step left (so a run can be reverted
  to it), and a question an agent asked mid-step with the person's answer.
- `chats.parent_id`, `forked_at`; `chat_messages.attachments`, `superseded_by`; table `chat_files`: forks,
  mentions and uploads in a session, and edited or regenerated turns that keep the old one.
- `plans.step_gate`, `revision`; table `plan_comments`: a plan shaped before dispatch.
- tables `taste_signals`, `taste_rules`: how a person likes the work done, learned from what they did and
  active only once adopted.
- tables `schedules`, `schedule_fires`: routines; `users.last_seen_at`: "done since your last visit".

Revision ID: 849ead068781
Revises: f3671f0b6574
Create Date: 2026-09-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '849ead068781'
down_revision: Union[str, Sequence[str], None] = 'f3671f0b6574'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('taste_rules',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('project_id', sa.String(length=40), nullable=True),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=10), server_default='proposed', nullable=False),
    sa.Column('support', sa.Integer(), server_default='0', nullable=False),
    sa.Column('contradict', sa.Integer(), server_default='0', nullable=False),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('proposed_by', sa.String(length=160), server_default='', nullable=False),
    sa.Column('adopted_by', sa.String(length=40), nullable=True),
    sa.Column('adopted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('proposed', 'active', 'retired')", name=op.f('ck_taste_rules_status')),
    sa.ForeignKeyConstraint(['adopted_by'], ['users.id'], name=op.f('fk_taste_rules_adopted_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_taste_rules_project_id_projects'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_taste_rules'))
    )
    op.create_index(op.f('ix_taste_rules_project_id'), 'taste_rules', ['project_id'], unique=False)
    op.create_table('chat_files',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('chat_id', sa.String(length=40), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('mime', sa.String(length=120), nullable=False),
    sa.Column('bytes', sa.Integer(), nullable=False),
    sa.Column('sha1', sa.String(length=40), nullable=False),
    sa.Column('data', sa.LargeBinary(), nullable=False),
    sa.Column('by', sa.String(length=120), server_default='', nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['chat_id'], ['chats.id'], name=op.f('fk_chat_files_chat_id_chats'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_chat_files'))
    )
    op.create_index(op.f('ix_chat_files_chat_id'), 'chat_files', ['chat_id'], unique=False)
    op.create_table('schedules',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('project_id', sa.String(length=40), nullable=False),
    sa.Column('workflow_id', sa.String(length=40), nullable=True),
    sa.Column('requirement', sa.Text(), server_default='', nullable=False),
    sa.Column('cadence', sa.String(length=120), server_default='', nullable=False),
    sa.Column('enabled', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('next_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_fired_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('token_hash', sa.String(length=128), nullable=True),
    sa.Column('created_by', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("workflow_id IS NOT NULL OR requirement <> ''", name=op.f('ck_schedules_what')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_schedules_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_schedules_project_id_projects'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workflow_id'], ['workflow_definitions.id'], name=op.f('fk_schedules_workflow_id_workflow_definitions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_schedules'))
    )
    op.create_index(op.f('ix_schedules_next_at'), 'schedules', ['next_at'], unique=False)
    op.create_index(op.f('ix_schedules_project_id'), 'schedules', ['project_id'], unique=False)
    op.create_table('schedule_fires',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('schedule_id', sa.String(length=40), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('trigger', sa.String(length=20), nullable=False),
    sa.Column('plan_ref', sa.String(length=40), nullable=True),
    sa.Column('run_ref', sa.String(length=40), nullable=True),
    sa.Column('outcome', sa.String(length=20), nullable=False),
    sa.Column('detail', sa.Text(), server_default='', nullable=False),
    sa.Column('payload_excerpt', sa.Text(), server_default='', nullable=False),
    sa.CheckConstraint("trigger IN ('schedule', 'manual', 'webhook')", name=op.f('ck_schedule_fires_trigger')),
    sa.ForeignKeyConstraint(['schedule_id'], ['schedules.id'], name=op.f('fk_schedule_fires_schedule_id_schedules'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_schedule_fires'))
    )
    op.create_index(op.f('ix_schedule_fires_schedule_id'), 'schedule_fires', ['schedule_id'], unique=False)
    op.create_table('plan_comments',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('plan_id', sa.String(length=40), nullable=False),
    sa.Column('step_id', sa.String(length=40), nullable=True),
    sa.Column('kind', sa.String(length=10), server_default='comment', nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('revision', sa.Integer(), server_default='1', nullable=False),
    sa.Column('by_user_id', sa.String(length=40), nullable=True),
    sa.Column('resolved', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('comment', 'split', 'remove', 'why', 'risky')", name=op.f('ck_plan_comments_kind')),
    sa.ForeignKeyConstraint(['by_user_id'], ['users.id'], name=op.f('fk_plan_comments_by_user_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['plan_id'], ['plans.id'], name=op.f('fk_plan_comments_plan_id_plans'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['step_id'], ['plan_steps.id'], name=op.f('fk_plan_comments_step_id_plan_steps'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_plan_comments'))
    )
    op.create_index(op.f('ix_plan_comments_plan_id'), 'plan_comments', ['plan_id'], unique=False)
    op.create_table('taste_signals',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('project_id', sa.String(length=40), nullable=True),
    sa.Column('run_id', sa.String(length=40), nullable=True),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('distilled', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('by_user_id', sa.String(length=40), nullable=True),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('accept', 'refuse', 'rework_note', 'edit_delta', 'plan_edit')", name=op.f('ck_taste_signals_kind')),
    sa.ForeignKeyConstraint(['by_user_id'], ['users.id'], name=op.f('fk_taste_signals_by_user_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_taste_signals_project_id_projects'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], name=op.f('fk_taste_signals_run_id_runs'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_taste_signals'))
    )
    op.create_index(op.f('ix_taste_signals_project_id'), 'taste_signals', ['project_id'], unique=False)
    op.add_column('chat_messages', sa.Column('attachments', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('chat_messages', sa.Column('superseded_by', sa.BigInteger(), nullable=True))
    op.create_foreign_key(op.f('fk_chat_messages_superseded_by_chat_messages'), 'chat_messages', 'chat_messages', ['superseded_by'], ['id'], ondelete='SET NULL')
    op.add_column('chats', sa.Column('parent_id', sa.String(length=40), nullable=True))
    op.add_column('chats', sa.Column('forked_at', sa.BigInteger(), nullable=True))
    op.add_column('chats', sa.Column('grants', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.create_foreign_key(op.f('fk_chats_parent_id_chats'), 'chats', 'chats', ['parent_id'], ['id'], ondelete='SET NULL')
    op.add_column('plans', sa.Column('step_gate', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('plans', sa.Column('revision', sa.Integer(), server_default='1', nullable=False))
    op.add_column('run_steps', sa.Column('commit_sha', sa.String(length=64), server_default='', nullable=False))
    op.add_column('run_steps', sa.Column('question', sa.Text(), server_default='', nullable=False))
    op.add_column('run_steps', sa.Column('answer', sa.Text(), server_default='', nullable=False))
    op.add_column('runs', sa.Column('grants', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('users', sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'last_seen_at')
    op.drop_column('runs', 'grants')
    op.drop_column('run_steps', 'answer')
    op.drop_column('run_steps', 'question')
    op.drop_column('run_steps', 'commit_sha')
    op.drop_column('plans', 'revision')
    op.drop_column('plans', 'step_gate')
    op.drop_constraint(op.f('fk_chats_parent_id_chats'), 'chats', type_='foreignkey')
    op.drop_column('chats', 'grants')
    op.drop_column('chats', 'forked_at')
    op.drop_column('chats', 'parent_id')
    op.drop_constraint(op.f('fk_chat_messages_superseded_by_chat_messages'), 'chat_messages', type_='foreignkey')
    op.drop_column('chat_messages', 'superseded_by')
    op.drop_column('chat_messages', 'attachments')
    op.drop_index(op.f('ix_taste_signals_project_id'), table_name='taste_signals')
    op.drop_table('taste_signals')
    op.drop_index(op.f('ix_plan_comments_plan_id'), table_name='plan_comments')
    op.drop_table('plan_comments')
    op.drop_index(op.f('ix_schedule_fires_schedule_id'), table_name='schedule_fires')
    op.drop_table('schedule_fires')
    op.drop_index(op.f('ix_schedules_project_id'), table_name='schedules')
    op.drop_index(op.f('ix_schedules_next_at'), table_name='schedules')
    op.drop_table('schedules')
    op.drop_index(op.f('ix_chat_files_chat_id'), table_name='chat_files')
    op.drop_table('chat_files')
    op.drop_index(op.f('ix_taste_rules_project_id'), table_name='taste_rules')
    op.drop_table('taste_rules')
