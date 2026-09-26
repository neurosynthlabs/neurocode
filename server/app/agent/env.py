"""The environment a project's own code runs with: its test command, its checkers, a repository's hooks,
a custom tool's command. The API's own secrets stay behind.

The API process holds the database URL and password, the setup token, every model key and its own
Python environment. A child process inherits all of that unless told otherwise, and a project's test
command is code from the repository — which an agent may just have written. So a child is started with
this environment, never with `os.environ` as it stands: the machine's PATH, HOME and locale, and nothing
that is the API's.
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping

#: The API's own: its settings and secrets, the Postgres it talks to, the Python and uv it runs in, and
#: anything named like a credential. A project that needs a key of its own sets it in its own config.
PRIVATE = re.compile(
    r"^(NEUROCODE_|POSTGRES_|PG[A-Z]*$|DATABASE_URL$|UV_|VIRTUAL_ENV$|PYTHONPATH$|PYTHONHOME$|"
    r".*(_API_KEY|_SECRET|_SECRET_KEY|_TOKEN|_PASSWORD|_PASSWD)$)",
    re.IGNORECASE,
)


def child_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """This process's environment without anything `PRIVATE`, plus `extra` (which wins)."""
    env = {k: v for k, v in os.environ.items() if not PRIVATE.match(k)}
    env.update(extra or {})
    return env
