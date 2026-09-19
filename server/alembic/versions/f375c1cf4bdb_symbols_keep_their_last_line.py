"""symbols keep their last line

- `code_symbols.end_line`: where a declaration ends, as tree-sitter or Python's ast read it; null where the
  parser does not know.

Revision ID: f375c1cf4bdb
Revises: e78fbd4aef84
Create Date: 2026-09-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f375c1cf4bdb'
down_revision: Union[str, Sequence[str], None] = 'e78fbd4aef84'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('code_symbols', sa.Column('end_line', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('code_symbols', 'end_line')
