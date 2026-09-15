"""the audit log refuses to be rewritten

Revision ID: b95c70ab289a
Revises: 037e1c34f71c
Create Date: 2026-09-15 22:12:51.349471

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy          # autogenerate renders pgvector types without importing them


# revision identifiers, used by Alembic.
revision: str = 'b95c70ab289a'
down_revision: Union[str, Sequence[str], None] = '037e1c34f71c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """The audit log is append-only. Until now that was a claim in a docstring.

    A comment asks people to remember; a trigger does not need them to. Anyone who can write to this
    database — the application, a script, a person at a psql prompt — is refused an UPDATE or a
    DELETE here, which is the only version of "append-only" worth relying on when the whole point of
    the table is to be trustworthy after something has gone wrong.

    Entries still survive the accounts they describe: `user_id` is ON DELETE SET NULL, and a foreign
    key's own SET NULL is not an UPDATE anyone issued, so it is allowed through deliberately below.
    """
    op.execute("""
        CREATE OR REPLACE FUNCTION audit_log_is_append_only() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'the audit log is append-only: entry % cannot be deleted', OLD.seq
                    USING ERRCODE = 'restrict_violation';
            END IF;
            -- Everything except the owning account is frozen. Losing the account must not lose the
            -- record of what they did, so that one column may be cleared and nothing else may move.
            IF ROW(NEW.seq, NEW.at, NEW.action, NEW.target, NEW.detail, NEW.ip)
               IS DISTINCT FROM ROW(OLD.seq, OLD.at, OLD.action, OLD.target, OLD.detail, OLD.ip)
               OR NEW.user_id IS NOT NULL THEN
                RAISE EXCEPTION 'the audit log is append-only: entry % cannot be changed', OLD.seq
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER audit_log_append_only
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION audit_log_is_append_only()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_append_only ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_is_append_only()")
