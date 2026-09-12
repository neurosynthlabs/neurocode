"""log level tool

A run logs its own output — the command it ran, each file it wrote, the model answering — at level
`tool`. That value was in the old schema and in the screens, and was missed when the enum was written
here, so every one of those lines failed to insert and took the step down with it.

Written by hand: Alembic's autogenerate compares tables and columns, never the values inside an enum
type, so a missing value like this one will never show up as a diff.

Revision ID: c3f1a9d24b70
Revises: ba71ca7751e2
Create Date: 2026-09-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy          # autogenerate renders pgvector types without importing them

# revision identifiers, used by Alembic.
revision: str = 'c3f1a9d24b70'
down_revision: Union[str, Sequence[str], None] = 'ba71ca7751e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Postgres 12 and later allow this inside a transaction, which is how Alembic runs it."""
    op.execute("ALTER TYPE level ADD VALUE IF NOT EXISTS 'tool'")


def downgrade() -> None:
    """Postgres cannot drop a value from an enum type without rebuilding it, and an unused value costs
    nothing — so this deliberately does not undo itself."""
