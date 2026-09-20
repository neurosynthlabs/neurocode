"""counters name the prefixes the code asks for

`ref_counters` was seeded in 1559832034bf from the refs each table already held, and three of its rows
did not match what the code asks the counter for:

- `SESSION-` was seeded from `chats`, but `ChatRepository.next_ref` asks for `CHAT-` — the prefix every
  session ref in the product is actually built from. The `SESSION-` row is therefore a number nobody
  will ever read, sitting beside the one that is missing.
- `EVAL-` (`EvalRunRepository.next_ref`) was not seeded at all.

Nothing is broken today: a prefix with no row seeds itself from its own table's highest ref the first
time one is asked for, which is why this is a correction and not a fix. But a counter is the sort of
row people read when they are working out where a number came from, and two of these would send them
somewhere untrue. They are made to say what the code says.

Data only — no table, column or constraint changes here.

Revision ID: a17f3c92d4be
Revises: 1559832034bf
Create Date: 2026-09-20

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a17f3c92d4be'
down_revision: Union[str, Sequence[str], None] = '1559832034bf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: The number already handed out for a prefix, read off the refs its own table holds — the same
#: expression the original seed used, and the same one `Repository._seed_ref_counter` falls back to.
HIGHEST = "max(coalesce(nullif(regexp_replace(ref, '\\D', '', 'g'), '')::int, 0))"


#: The prefix each table's refs are counted under. Written here rather than inline so the test can seed a
#: transaction of its own with the very same statements: the shared test database is truncated by another
#: test, so what this migration committed cannot be read back there — only its behaviour can be proved.
SEEDS = (("CHAT-", "chats"), ("EVAL-", "eval_runs"))
#: DO NOTHING on conflict: a workspace that has already asked for a `CHAT-` or an `EVAL-` ref since the last
#: migration holds a number that has been handed out, and seeding over it would hand the same ref out twice.
SEED = ("INSERT INTO ref_counters (prefix, next) "
        "SELECT '{prefix}', coalesce({highest}, 0) FROM {table} ON CONFLICT (prefix) DO NOTHING")
#: The row for a prefix no code asks for. Nothing reads it, so nothing loses anything.
UNUSED = "DELETE FROM ref_counters WHERE prefix = 'SESSION-'"


def statements() -> list[str]:
    """What `upgrade()` runs, in order — shared with the test that proves it."""
    return [SEED.format(prefix=prefix, table=table, highest=HIGHEST) for prefix, table in SEEDS] + [UNUSED]


def upgrade() -> None:
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    # The `SESSION-` row comes back, seeded as it was. The `CHAT-` and `EVAL-` rows deliberately stay:
    # by now they may hold numbers that have been handed out, and deleting them would let the refs be
    # handed out a second time. The older code never reads either one, so leaving them costs nothing.
    op.execute(f"""
        INSERT INTO ref_counters (prefix, next)
        SELECT 'SESSION-', coalesce({HIGHEST}, 0) FROM chats
        ON CONFLICT (prefix) DO NOTHING
    """)
