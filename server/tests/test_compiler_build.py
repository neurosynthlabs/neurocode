"""A plan held to what the runtime can make of it. Found on the first real "make me a good website here": the
model split one site across an Architect, a Frontend Engineer and QA in three worktrees, and added steps that
could only be answered with a checklist — so the run wrote a README, a review checklist, a requirements
template and a 30-line page.
"""
from __future__ import annotations

from app.ai.compiler import Context, PlanOut, built_as_one
from app.data import roster

BASE = {"title": "Build a good website", "businessRequirement": "b", "technicalRequirement": "t",
        "risk": "LOW", "confidence": 80}


def plan(steps: list[tuple[str, str, str]]) -> PlanOut:
    return PlanOut.model_validate({**BASE, "steps": [{"label": label, "agent": agent, "detail": detail}
                                                     for label, agent, detail in steps]})


def test_a_new_project_is_one_build_by_one_owner_without_the_runtimes_own_steps():
    out = plan([("Initialize project scaffold", "Architect", "package.json and Vite."),
                ("Create content pages", "Frontend Engineer", "Home, about and contact with placeholder content."),
                ("Write UI tests", roster.TESTER, "Cypress tests for navigation."),
                ("Run build and test", roster.TESTER, "npm run build and npm test."),
                ("Perform manual UI/UX review", roster.REVIEWER, "Check every page by hand.")])
    built_as_one(out, Context(project={"name": "site", "files": 0, "stack": []}, facts=[]))   # README only
    assert [s.label for s in out.steps] == ["Initialize project scaffold", "Create content pages", "Write UI tests"]
    assert {s.agent for s in out.steps} == {"Architect"}
    assert "placeholder" not in out.steps[1].detail and "real content" in out.steps[1].detail


def test_an_existing_codebase_keeps_its_owners_and_an_approval_stays_the_persons():
    out = plan([("Fix the tax split", "Backend Engineer", "Place of supply."),
                ("Update the invoice view", "Frontend Engineer", "Show IGST."),
                ("Your approval", roster.COMMANDER, "Sign off.")])
    built_as_one(out, Context(project={"name": "erp", "files": 2400}, facts=[]))
    assert [s.agent for s in out.steps] == ["Backend Engineer", "Frontend Engineer", roster.COMMANDER]


def test_a_plan_that_is_only_the_runtimes_job_is_left_as_it_was_and_an_unmeasured_project_is_not_new():
    out = plan([("Run the tests", roster.TESTER, "npm test.")])
    built_as_one(out, Context(project={"name": "x", "files": 3}, facts=[]))
    assert [s.label for s in out.steps] == ["Run the tests"]
    unmeasured = plan([("Map it", "Architect", "a"), ("Review", roster.REVIEWER, "b")])
    built_as_one(unmeasured, Context(project={"name": "erp", "files": 0, "stack": ["React 18"]}, facts=[]))
    assert [(s.label, s.agent) for s in unmeasured.steps] == [("Map it", "Architect"), ("Review", roster.REVIEWER)]
