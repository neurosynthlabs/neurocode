"""a workspace chunk is unique too

Revision ID: 0d132a2d78a9
Revises: b95c70ab289a
Create Date: 2026-09-15 22:18:37.104553

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy          # autogenerate renders pgvector types without importing them


# revision identifiers, used by Alembic.
revision: str = '0d132a2d78a9'
down_revision: Union[str, Sequence[str], None] = 'b95c70ab289a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """A null project_id means the workspace itself, so it has to compare equal to another null.

    By default Postgres treats every null as distinct, which made the (project_id, kind, ref) rule
    silently inapplicable to exactly the rows it most needed to cover: rebuilding the workspace scope
    while a previous build was still inserting could leave two copies of the same chunk, and a
    retrieval would then cite it twice.
    """
    op.execute("DELETE FROM chunks a USING chunks b WHERE a.id > b.id AND a.project_id IS NULL "
               "AND b.project_id IS NULL AND a.kind = b.kind AND a.ref = b.ref")
    op.drop_constraint("uq_chunks_project_id_kind_ref", "chunks", type_="unique")
    op.execute("ALTER TABLE chunks ADD CONSTRAINT uq_chunks_project_id_kind_ref "
               "UNIQUE NULLS NOT DISTINCT (project_id, kind, ref)")


def downgrade() -> None:
    op.drop_constraint("uq_chunks_project_id_kind_ref", "chunks", type_="unique")
    op.create_unique_constraint("uq_chunks_project_id_kind_ref", "chunks",
                                ["project_id", "kind", "ref"])
