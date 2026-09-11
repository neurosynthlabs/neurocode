import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

SEED = json.loads((Path(__file__).resolve().parent.parent / "seed" / "seed.json").read_text())


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(str(tmp_path / "test.db"))) as c:
        yield c


def test_health_counts_match_the_seed(client):
    counts = client.get("/health").json()["counts"]
    for key in ("projects", "agents", "tasks", "approvals", "memory", "activity"):
        assert counts[key] == len(SEED[key]), key


def test_approving_is_final_and_logged(client):
    pending = client.get("/approvals", params={"status": "pending"}).json()
    ref = pending[0]["ref"]
    r = client.post(f"/approvals/{ref}/approve")
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["decidedAt"]
    assert client.post(f"/approvals/{ref}/deny").status_code == 409
    assert len(client.get("/approvals", params={"status": "pending"}).json()) == len(pending) - 1
    top = client.get("/activity", params={"limit": 1}).json()[0]
    assert top["action"] == "Approved" and ref in top["detail"] and top["actorKind"] == "human"


def test_unknown_approval_and_bad_decision(client):
    assert client.post("/approvals/APPR-999/approve").status_code == 404
    assert client.post("/approvals/APPR-118/maybe").status_code == 422


def test_decision_is_pushed_to_the_stream(client):
    q = client.app.state.bus.subscribe()
    client.post("/approvals/APPR-120/deny")
    event = q.get_nowait()
    assert event["action"] == "Denied" and "APPR-120" in event["detail"]


def test_memory_full_text_search(client):
    refs = [f["ref"] for f in client.get("/memory", params={"q": "TRANS"}).json()]
    assert "MEM-142" in refs
    # punctuation and FTS operators in user input must never reach FTS5 as syntax
    assert client.get("/memory", params={"q": 'TRANS_* AND "(' }).status_code == 200


def test_pin_and_archive(client):
    fact = client.get("/memory").json()[-1]
    ref = fact["ref"]
    assert client.post(f"/memory/{ref}/pin", json={"pinned": not fact["pinned"]}).json()["pinned"] is (not fact["pinned"])
    client.post(f"/memory/{ref}/archive")
    assert ref not in [f["ref"] for f in client.get("/memory").json()]
    assert ref in [f["ref"] for f in client.get("/memory", params={"include_archived": True}).json()]


def test_checklist_toggle_persists(client):
    task = client.get("/tasks/TASK-492").json()
    item = next(c for c in task["checklist"] if not c["done"])
    client.post(f"/tasks/TASK-492/checklist/{item['id']}", json={"done": True})
    again = client.get("/tasks/TASK-492").json()
    assert next(c for c in again["checklist"] if c["id"] == item["id"])["done"] is True


def test_task_status_is_validated(client):
    assert client.patch("/tasks/TASK-492", json={"status": "shipped"}).status_code == 422
    assert client.patch("/tasks/TASK-000", json={"status": "done"}).status_code == 404
    assert client.patch("/tasks/TASK-492", json={"status": "review"}).json()["status"] == "review"


def test_reset_needs_explicit_confirmation(client):
    assert client.post("/admin/reset").status_code == 400
    client.post("/approvals/APPR-118/approve")
    assert client.post("/admin/reset", headers={"X-Confirm": "reset"}).status_code == 200
    assert client.get("/approvals", params={"status": "pending"}).json()[0]["status"] == "pending"
