import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.agents import AgentService
from app.checkpoints import CheckpointError, CheckpointStore, claim_resume
from app.db import AgentRun, Repository, RunCheckpoint, SessionLocal, new_id
from app.planning import Plan, Planner
from app.config import Settings
from app.knowledge import load_corpus
from app.retrieval import search


def frames(response):
    return [json.loads(frame[6:]) for frame in response.text.split("\n\n") if frame.startswith("data: ")]


@pytest.fixture
def interrupted(client, monkeypatch):
    plan = Plan.model_validate({"reason":"resume fixture", "steps":[
        {"id":"report","agent":"report","title":"Repository"},
        {"id":"docs","agent":"knowledge","title":"Documents","query":"租户","depends_on":["report"]},
    ]})
    monkeypatch.setattr(Planner, "fallback", lambda *args: plan)
    original = AgentService.specialist
    calls = []
    async def cancel_once(self, task, target=None):
        calls.append(task)
        if task == "knowledge" and calls.count("knowledge") == 1:
            raise asyncio.CancelledError()
        return await original(self, task, target)
    monkeypatch.setattr(AgentService, "specialist", cancel_once)
    repo = client.get("/api/repositories").json()[0]["id"]
    imported = client.post(f"/api/repositories/{repo}/knowledge/documents", json={"path":"docs/recovery.md","title":"租户恢复指南","content":"# 租户规则\n原版本要求按 tenant_id 隔离。"})
    assert imported.status_code == 200
    response = client.post("/api/chat/stream", json={"repository_id":repo, "message":"检查协作恢复", "task":"workflow"})
    events = frames(response)
    assert events[-1]["type"] == "run.cancelled", events[-1]
    ident = events[0]["data"]["run_id"]
    root = f"/api/repositories/{repo}/runs/{ident}"
    detail = client.get(root).json()
    assert detail["status"] == "cancelled" and detail["recovery"]["settled_tasks"] == 1
    return repo, ident, root, calls


def test_resume_reuses_completed_task_and_appends_events(client, interrupted):
    _, ident, root, calls = interrupted
    old = client.get(root).json()
    response = client.post(root + "/resume")
    assert response.status_code == 200, response.text
    events = frames(response)
    assert events[0]["type"] == "run.resumed"
    assert events[0]["sequence"] == old["events"][-1]["sequence"] + 1
    answer = events[-1]["data"]["result"]
    assert calls == ["report", "knowledge", "knowledge"]
    assert any(x["type"] == "task.reused" and x["data"]["id"] == "report" for x in events)
    assert answer["workflow"]["resume_count"] == 1
    assert "repo:health" in answer["workflow"]["outcomes"][1]["evidence_ids"]
    detail = client.get(root).json()
    assert detail["status"] == "completed" and not detail["recovery"]["available"]
    assert [x["sequence"] for x in detail["events"]] == list(range(1, len(detail["events"]) + 1))
    assert client.post(root + "/resume").status_code == 409


def test_restore_in_new_process_after_startup_marks_interrupted(client, interrupted):
    _, ident, root, _ = interrupted
    with SessionLocal() as db:
        db.get(AgentRun, ident).status = "running"  # Persisted state left by an abruptly exited worker.
        db.commit()
    script = f'''
import json
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app) as client:
    before = client.get({root!r}).json()
    assert before["status"] == "interrupted"
    assert before["recovery"]["settled_tasks"] == 1
    response = client.post({(root + "/resume")!r})
    assert response.status_code == 200, response.text
    after = client.get({root!r}).json()
    assert after["status"] == "completed"
    assert after["result"]["workflow"]["resume_count"] == 1
    assert sum(x["type"] == "task.started" and x["data"]["id"] == "report" for x in after["events"]) == 1
    print(json.dumps({{"status":after["status"],"retained_report":True}}))
'''
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1])}
    proc = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).parents[2], env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["retained_report"]


def test_document_snapshot_survives_updates_and_archive(client, interrupted):
    repo, ident, root, _ = interrupted
    with SessionLocal() as db:
        pinned = db.get(RunCheckpoint, ident).state["input"]["corpus"]
    original = {x["id"]: x["revision"] for x in pinned}
    docs = client.get(f"/api/repositories/{repo}/knowledge/documents").json()
    for doc in docs:
        if doc["source"] != "manual": continue
        imported = client.post(f"/api/repositories/{repo}/knowledge/documents", json={"path":doc["path"],"title":"替换的文档","content":"# 新文档\n不再包含原租户规则。"})
        assert imported.status_code == 200, imported.text
        assert client.post(f"/api/repositories/{repo}/knowledge/documents/{doc['id']}/archive").status_code == 200
    response = client.post(root + "/resume")
    answer = frames(response)[-1]["data"]["result"]
    old_hits = [x for x in answer["evidence"] if x["id"] in original]
    assert old_hits and all(x["sha"] == original[x["id"]] for x in old_hits)
    assert any("原运行保存的文档版本" in x for x in answer["gaps"]), answer["gaps"]
    retrieved = asyncio.run(search(repo, "租户", Settings(retrieval_backend="milvus"), "hybrid", pinned_chunks=pinned))
    assert retrieved["mode"] == "keyword" and retrieved["warnings"]


def test_atomic_claim_rejects_duplicate_owner(client, interrupted):
    _, ident, _, _ = interrupted
    with SessionLocal() as first, SessionLocal() as second:
        a, b = first.get(AgentRun, ident), second.get(AgentRun, ident)
        cp_a, cp_b = first.get(RunCheckpoint, ident), second.get(RunCheckpoint, ident)
        old_token = cp_a.lease_token
        stale_store = CheckpointStore(ident, old_token)
        claim_resume(first, a, cp_a, Settings())
        with pytest.raises(CheckpointError, match="重复"):
            claim_resume(second, b, cp_b, Settings())
        with pytest.raises(CheckpointError, match="执行权"):
            asyncio.run(stale_store.update(phase="planning"))
    with SessionLocal() as db:
        assert db.get(RunCheckpoint, ident).resume_count == 1


@pytest.mark.parametrize("change", ["version", "scope", "limit", "model"])
def test_resume_rejects_incompatible_checkpoint(client, interrupted, change):
    _, ident, root, _ = interrupted
    with SessionLocal() as db:
        cp = db.get(RunCheckpoint, ident)
        state = json.loads(json.dumps(cp.state))
        if change == "version": cp.schema_version = 99
        elif change == "scope": state["input"]["repository_id"] = "f" * 32
        elif change == "limit": cp.resume_count = 3
        else: state["input"]["model_key"] = "changed"
        cp.state = state; db.commit()
    response = client.post(root + "/resume")
    assert response.status_code == 409
    assert client.get(root).json()["status"] == "cancelled"


def test_resume_scope_and_legacy_runs_are_rejected(client, interrupted):
    repo, ident, root, _ = interrupted
    with SessionLocal() as db:
        other = Repository(full_name="demo/other", snapshot={"source":"demo"})
        db.add(other); db.commit()
        other_id = other.id
    assert client.post(f"/api/repositories/{other_id}/runs/{ident}/resume").status_code == 404
    with SessionLocal() as db:
        db.delete(db.get(RunCheckpoint, ident)); db.commit()
    assert client.post(root + "/resume").status_code == 409


def test_already_saved_answer_does_not_call_model_or_agents(client, interrupted, monkeypatch):
    _, ident, root, _ = interrupted
    response = client.post(root + "/resume")
    answer = frames(response)[-1]["data"]["result"]
    with SessionLocal() as db:
        cp = db.get(RunCheckpoint, ident)
        cp.state = {**cp.state, "phase":"ready", "answer":answer}
        run = db.get(AgentRun, ident); run.status, run.result = "cancelled", None
        db.commit()
    async def forbidden(*args, **kwargs): raise AssertionError("Completed output should be reused")
    monkeypatch.setattr(AgentService, "run", forbidden)
    events = frames(client.post(root + "/resume"))
    assert events[-1]["type"] == "run.completed"
    assert any(x["type"] == "answer.reused" for x in events)
    assert events[-1]["data"]["result"]["workflow"]["resume_count"] == 2


def test_changed_pr_head_blocks_resume_before_claim(client, interrupted, monkeypatch):
    from app import main
    from app.checkpoints import model_key
    from app.github import GitHubClient
    repo, ident, root, _ = interrupted
    settings = Settings(devflow_mode="live", llm_model="test", llm_api_key="test")
    monkeypatch.setattr(main, "settings", settings)
    async def changed(*args, **kwargs): return {"head":{"sha":"c"*40}}
    monkeypatch.setattr(GitHubClient, "get", changed)
    with SessionLocal() as db:
        run = db.get(AgentRun, ident); run.mode = "live"
        repository = db.get(Repository, repo)
        repository.snapshot = {**repository.snapshot,"source":"github"}
        cp = db.get(RunCheckpoint, ident)
        state = json.loads(json.dumps(cp.state))
        from app.prompts import freeze_binding
        state["input"].update(mode="live", model_key=model_key(settings), analysis_binding=freeze_binding(settings))
        state["input"]["snapshot"]["source"] = "github"
        state["plans"][0]["steps"][0].update(agent="pr", target=18)
        state["outcomes"]["report"].update(agent="pr",target=18,pr_context={"head_sha":"b"*40,"checks":[]})
        cp.state = state; db.commit()
    response = client.post(root + "/resume")
    assert response.status_code == 409 and "head SHA" in response.json()["detail"]
    with SessionLocal() as db:
        assert db.get(AgentRun, ident).status == "cancelled"
        assert db.get(RunCheckpoint, ident).resume_count == 0


def test_explicit_stop_persists_checkpoint_and_rejects_running_resume(client, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from app.execution import ACTIVE_RUNS
    waiting = threading.Event()
    plan = Plan.model_validate({"reason":"stop fixture", "steps":[
        {"id":"report","agent":"report","title":"Repository"},
        {"id":"docs","agent":"knowledge","title":"Documents","query":"租户","depends_on":["report"]},
    ]})
    monkeypatch.setattr(Planner, "fallback", lambda *args: plan)
    original = AgentService.specialist
    async def slow(self, task, target=None):
        if task == "knowledge":
            waiting.set()
            await asyncio.Event().wait()
        return await original(self, task, target)
    monkeypatch.setattr(AgentService, "specialist", slow)
    repo = client.get("/api/repositories").json()[0]["id"]
    with ThreadPoolExecutor(max_workers=1) as pool:
        response = pool.submit(client.post, "/api/chat/stream", json={"repository_id":repo,"message":"验证停止","task":"workflow"})
        assert waiting.wait(5)
        run = client.get(f"/api/repositories/{repo}/runs").json()[0]
        root = f"/api/repositories/{repo}/runs/{run['id']}"
        assert client.post(root + "/resume").status_code == 409
        stopped = client.post(root + "/stop")
        assert stopped.status_code == 200
        assert stopped.json()["status"] == "cancelled"
        assert stopped.json()["recovery"]["settled_tasks"] == 1
        assert frames(response.result(5))[-1]["type"] == "run.cancelled"
        assert client.post(root + "/stop").status_code == 200
    assert run["id"] not in ACTIVE_RUNS
