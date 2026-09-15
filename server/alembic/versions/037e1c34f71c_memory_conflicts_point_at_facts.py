"""memory conflicts point at facts

Revision ID: 037e1c34f71c
Revises: c3f1a9d24b70
Create Date: 2026-09-15 22:10:19.699036

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy          # autogenerate renders pgvector types without importing them
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '037e1c34f71c'
down_revision: Union[str, Sequence[str], None] = 'c3f1a9d24b70'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # `a::text` on a JSONB string gives `"m324"` — quotes and all — so the value is taken out with
    # `#>> '{}'`, which returns the text a JSON string holds rather than the JSON that spells it.
    op.alter_column('memory_conflicts', 'a',
               existing_type=postgresql.JSONB(astext_type=sa.Text()),
               server_default=None,
               type_=sa.String(length=40),
               existing_nullable=False,
               postgresql_using="a #>> '{}'")
    op.alter_column('memory_conflicts', 'b',
               existing_type=postgresql.JSONB(astext_type=sa.Text()),
               server_default=None,
               type_=sa.String(length=40),
               existing_nullable=False,
               postgresql_using="b #>> '{}'")
    op.create_foreign_key(op.f('fk_memory_conflicts_b_memory_facts'), 'memory_conflicts', 'memory_facts', ['b'], ['id'], ondelete='CASCADE')
    op.create_foreign_key(op.f('fk_memory_conflicts_a_memory_facts'), 'memory_conflicts', 'memory_facts', ['a'], ['id'], ondelete='CASCADE')
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('fk_memory_conflicts_a_memory_facts'), 'memory_conflicts', type_='foreignkey')
    op.drop_constraint(op.f('fk_memory_conflicts_b_memory_facts'), 'memory_conflicts', type_='foreignkey')
    op.alter_column('memory_conflicts', 'b',
               existing_type=sa.String(length=40),
               server_default=sa.text("'{}'::jsonb"),
               type_=postgresql.JSONB(astext_type=sa.Text()),
               existing_nullable=False,
               postgresql_using="to_jsonb(b)")
    op.alter_column('memory_conflicts', 'a',
               existing_type=sa.String(length=40),
               server_default=sa.text("'{}'::jsonb"),
               type_=postgresql.JSONB(astext_type=sa.Text()),
               existing_nullable=False)
    # ### end Alembic commands ###
