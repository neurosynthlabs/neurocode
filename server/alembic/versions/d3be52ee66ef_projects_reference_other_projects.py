"""projects reference other projects

`project_references`: another project this one reads from — its code, documents and memory searched and
handed to models as context here, and never written to from here. A project cannot reference itself, and
a pair is referenced once.

Revision ID: d3be52ee66ef
Revises: 21e18d25de2e
Create Date: 2026-09-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3be52ee66ef'
down_revision: Union[str, Sequence[str], None] = '21e18d25de2e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('project_references',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('project_id', sa.String(length=40), nullable=False),
    sa.Column('referenced_id', sa.String(length=40), nullable=False),
    sa.Column('note', sa.Text(), server_default='', nullable=False),
    sa.Column('created_by', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('project_id <> referenced_id', name=op.f('ck_project_references_not_itself')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_project_references_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_project_references_project_id_projects'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['referenced_id'], ['projects.id'], name=op.f('fk_project_references_referenced_id_projects'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_project_references')),
    sa.UniqueConstraint('project_id', 'referenced_id', name=op.f('uq_project_references_project_id_referenced_id'))
    )
    op.create_index(op.f('ix_project_references_project_id'), 'project_references', ['project_id'], unique=False)
    op.create_index(op.f('ix_project_references_referenced_id'), 'project_references', ['referenced_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_project_references_referenced_id'), table_name='project_references')
    op.drop_index(op.f('ix_project_references_project_id'), table_name='project_references')
    op.drop_table('project_references')
