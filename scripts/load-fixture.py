"""Load the tests' workspace into a throwaway database, for the checks that look at a running app.

The product ships no sample work, so a screen with nothing in it is what a new workspace really
looks like. The render smoke and the layout lint want screens with something on them too — a task
board with cards, a memory list with facts, a plan with open questions — and the fixture the Python
tests already trust is that something. This is the one door it goes through into a database the app
runs on; it refuses the development and live databases by name, so a slip of the flag cannot write
test rows over real work.

    uv run --project server python scripts/load-fixture.py --database postgresql+asyncpg://…/neurocode_smoke
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent / "server"
sys.path.insert(0, str(SERVER))

from app.data.engine import Database  # noqa: E402
from tests.fixtures.workspace import load_workspace  # noqa: E402

#: Databases that hold someone's work. The checks make their own, named for the check.
PROTECTED = {"neurocode", "postgres", "template0", "template1"}


async def load(url: str) -> None:
    db = Database(url=url)
    try:
        async with db.session() as session:
            await load_workspace(session)
    finally:
        await db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", required=True, help="the async URL of a migrated, throwaway database")
    args = parser.parse_args()
    name = args.database.rsplit("/", 1)[-1].split("?", 1)[0]
    if name in PROTECTED:
        print(f"refusing to load test rows into {name!r}: that database holds real work", file=sys.stderr)
        return 2
    asyncio.run(load(args.database))
    print(f"loaded the test workspace into {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
