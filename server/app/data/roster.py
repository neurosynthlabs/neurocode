"""The agent roster, and the names the runtime works under.

The roster is read from the catalogue, and every place that needs an agent's name asks here: the
compiler's list of owners, the router screen's agents that are never called, the workflow check on
who may be handed a writing step, and the steps a run writes for itself. Each of those used to keep
its own copy, and a rename in one would have quietly stopped matching the others.

Three names the runtime uses are not agents on the roster, and are said so here rather than invented
as rows: the person who signs every run, the Orchestrator that merges branches with git, and the
owner a step gets when a plan named nobody.
"""
from __future__ import annotations

from .catalogue import AGENTS, ROLE_BY_ID, RosterAgent

BY_ID: dict[str, RosterAgent] = {a.id: a for a in AGENTS}
#: Name → id, in the catalogue's order. The compiler offers a model exactly these owners.
IDS_BY_NAME: dict[str, str] = {a.name: a.id for a in AGENTS}
NAMES: tuple[str, ...] = tuple(IDS_BY_NAME)

#: The agent that hands the work out. A plan's steps for it are never run.
COMMANDER = BY_ID["commander"].name
#: The agent that reads a repository while it is onboarded or re-indexed.
ARCHITECT = BY_ID["architect"].name
#: The agent that reads a run's diff.
REVIEWER = BY_ID["reviewer"].name
#: The agent a test step is filed under. It runs the project's own command; no model is asked.
TESTER = BY_ID["qa"].name
#: The person at the gate — the built-in role that signs, not an agent.
APPROVER = ROLE_BY_ID["approver"].name
#: Who a run's last step belongs to, as the run screen says it.
YOU = "You"
#: The runtime merging the agents' branches. It is git doing the work, so it is not on the roster.
ORCHESTRATOR = "Orchestrator"
#: The owner of a plan step that named nobody.
UNNAMED = "Engineer"
#: Owners a writing step can never have: the commander only plans, and the approver is you.
NOT_WRITERS: tuple[str, ...] = (COMMANDER, APPROVER)
