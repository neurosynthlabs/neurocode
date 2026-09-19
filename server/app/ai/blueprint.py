"""The Blueprint wizard's review: a model reads the answers and the architecture and proposes changes.

It only proposes. Each change names a place in the architecture (`layers.backend.choice`,
`services[api].talksTo`, `services[+]` to add one), the value it would put there and why. The service
checks every change against the blueprint's shape and hands the survivors to a person, who accepts or
rejects each one; nothing here writes anything. With no model there is no review — a template of generic
advice presented as a critique of this design would be invented — so the call raises, and the service
turns that into the words that say how to add a model.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .gateway import Gateway, Result, extract_json


class Change(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    path: str = Field(min_length=1, max_length=200)
    to: Any = None
    why: str = ""
    #: What the model believes is there now. Kept for its reasoning only: the screen is shown the value
    #: really at the path, read by the service.
    was: Any = Field(default=None, alias="from")


class ReviewOut(BaseModel):
    summary: str = ""
    changes: list[Change] = Field(default_factory=list)


SYSTEM = """You are a principal software architect reviewing a system design inside NeuroCode. You are given what
the team is building (their answers to a short questionnaire), the architecture as JSON, and the technology
catalogue (id, name, category). Critique the architecture against the answers: a choice that fights the team's
skills, budget, scale, compliance or timeline; a missing layer the answers call for (a queue for background
jobs, search, auth, observability); a service boundary that is too fine or too coarse; security gaps. Do not
propose changes for taste alone, and do not repeat what is already there.

Propose at most 8 concrete changes. Each change is {"path", "to", "why"}:
- "path" names one place in the architecture: "layers.<layer>.choice" (a technology id from the catalogue),
  "layers.<layer>.alternatives", "layers.<layer>" (a whole layer {"choice","alternatives","why"}, or null to
  drop the layer), "services[<name>].tech", "services[<name>].talksTo", "services[+]" (add a service
  {"name","kind","tech","responsibilities","talksTo"}), "services[<name>]" with "to": null (remove it),
  "dataStores[+]", "environments[+]", "ci.stages", "security.notes", "conventions", "adrs[+]"
  ({"title","decision","why","alternatives"}), "summary".
  Layers are: frontend, mobile, desktop, backend, database, cache, queue, search, auth, ai-ml, data, infra,
  ci-cd, observability, testing, hosting. Service kinds are: web, api, worker, gateway, function, job, cli,
  library, service, pipeline, desktop, mobile, static, realtime. Names are lower-case with dashes.
- "to" is the complete new value for that place, in the same shape as the value there now.
- "why" is one or two sentences tied to a specific answer or risk.
Prefer technology ids from the catalogue; use a plain name only when nothing in it fits.
Reply with one JSON object: {"summary": "two or three sentences on the design overall", "changes": [...]}"""


def _compact(arch: dict[str, Any]) -> str:
    """The architecture as the model reads it: whole, but the scaffold's file notes cut to their paths —
    the review is about the design, and the notes are the longest part of it."""
    slim = dict(arch)
    repos = (arch.get("scaffold") or {}).get("repos") or []
    slim["scaffold"] = {"repos": [{"label": r["label"], "files": [f["path"] for f in r.get("files") or []]}
                                  for r in repos]}
    return json.dumps(slim, ensure_ascii=False, separators=(",", ":"))


def review(gw: Gateway, name: str, answers: dict[str, Any], arch: dict[str, Any],
           catalogue: list[tuple[str, str, str]], *, actor: str | None = None) -> Result[ReviewOut]:
    """A model's review of one blueprint. Raises `NoModel` when no lane can answer and `ProviderError`
    when every lane that tried failed. Blocking, like the gateway."""
    asked = "\n".join(f"- {k}: {json.dumps(v, ensure_ascii=False)}" for k, v in answers.items()) or "(none given)"
    techs = "\n".join(f"{tid} ({tname}, {cat})" for tid, tname, cat in catalogue)
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Technology catalogue:\n{techs}\n\nBlueprint: {name}\n\n"
                                        f"Answers:\n{asked}\n\nArchitecture:\n{_compact(arch)}"}]

    def parse(raw: str) -> ReviewOut:
        out = ReviewOut.model_validate(extract_json(raw))
        out.changes = [c for c in out.changes if c.path.strip()]
        return out

    return gw.ask(msgs, parse, feature="blueprint", actor=actor)
