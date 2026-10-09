import asyncio
import json
import os
import subprocess
import sys
import threading
from datetime import timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import main
from app.agents import AgentService
from app.checkpoints import CheckpointError, CheckpointStore
from app.db import AgentRun, RunCheckpoint, RunJob, SessionLocal, utcnow, new_id
from app.planning import Plan, Planner
from app.run_queue import claim_job, expire_jobs, renew_job
from app.worker import execute_claim, finish_claim, run_worker


def submit(client, task="workflow", **kwargs):
    repo = client.get("/api/repositories").json()[0]["id"]
    response = client.post("/api/chat/runs", json={"repository_id": repo, "task": task, "message": "检查当前发布", **kwargs})
    assert response.status_code == 202, response.text
    ident = response.json()["run_id"]
    return repo, ident, f"/api/repositories/{repo}/runs/{ident}"


def frames(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_submission_is_durable_without_worker_and_queued_stop_calls_no_model(client, monkeypatch):
    async def forbidden(*args, **kwargs): raise AssertionError("No worker should execute")
    monkeypatch.setattr(AgentService, "run", forbidden)
    repo, ident, root = submit(client)
    run = client.get(root).json()
    assert run["status"] == "queued" and run["background"]
    assert [x["type"] for x in run["events"]] == ["run.queued"]
    assert client.get("/api/queue/status").json()["queued"] == 1
    assert client.get("/api/queue/status").json()["workers_online"] == 0
    assert client.post(root + "/resume-background").status_code == 409
    assert client.post(root + "/stop").json()["status"] == "cancelled"
    assert client.post(root + "/stop").json()["status"] == "cancelled"
    assert claim_job(main.settings, new_id()) is None
    assert frames(client.get(root + "/events"))[-1]["type"] == "run.cancelled"


def test_dedicated_python_process_executes_saved_job_and_replays_cursor(client):
    _, ident, root = submit(client, "pr", target=18)
    script = f'''
import asyncio, json
from app.config import get_settings
from app.db import AgentRun, SessionLocal
from app.run_queue import claim_job
from app.worker import execute_claim
settings = get_settings()
claim = claim_job(settings, "child")
assert claim["run_id"] == {ident!r}
asyncio.run(execute_claim(claim, settings))
with SessionLocal() as db:
    run = db.get(AgentRun, {ident!r})
    assert run.status == "completed"
    assert run.result["title"] == "PR #18 风险分析", ascii(run.result["title"])
    print(json.dumps({{"status":run.status}}))
'''
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1])}
    proc = subprocess.run([sys.executable, "-c", script], env=env, cwd=Path(__file__).parents[2], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    detail = client.get(root).json()
    events = frames(client.get(root + "/events?after=2"))
    assert events == detail["events"][2:]
    assert [x["sequence"] for x in detail["events"]] == list(range(1, len(detail["events"]) + 1))
    assert client.get(root + f"/events?after={len(detail['events'])}").text == ""
    assert client.get(root + "/events?after=999999").status_code == 422


def test_claim_expires_and_fences_stale_worker_and_checkpoint(client):
    _, ident, root = submit(client)
    first = claim_job(main.settings, "worker-a")
    assert first and claim_job(main.settings, "worker-b") is None
    stale = CheckpointStore(ident, first["checkpoint_token"], job_lease=first["token"])
    with SessionLocal() as db:
        db.get(RunJob, ident).lease_until = utcnow() - timedelta(seconds=1)
        db.commit()
        expire_jobs(db)
    assert client.get(root).json()["status"] == "interrupted"
    assert renew_job(ident, first["token"], main.settings) == "lost"
    with pytest.raises(CheckpointError): asyncio.run(stale.update(phase="executing"))
    finish_claim(first, "failed", "stale")
    assert client.get(root).json()["status"] == "interrupted"
    resumed = client.post(root + "/resume-background")
    assert resumed.status_code == 202, resumed.text
    assert client.post(root + "/resume-background").status_code == 409
    assert client.post(root + "/resume").status_code == 409
    second = claim_job(main.settings, "worker-b")
    assert second["token"] != first["token"]
    asyncio.run(execute_claim(second, main.settings))
    assert client.get(root).json()["status"] == "completed"
    assert client.get(root).json()["recovery"]["resume_count"] == 1


def test_api_startup_preserves_other_process_live_lease(client):
    _, ident, root = submit(client)
    claim = claim_job(main.settings, "independent-worker")
    async def restart():
        async with main.lifespan(main.app):
            pass
    asyncio.run(restart())
    assert client.get(root).json()["status"] == "running"
    asyncio.run(execute_claim(claim, main.settings))
    assert client.get(root).json()["status"] == "completed"


def test_worker_config_mismatch_fails_before_calling_agents(client, monkeypatch):
    _, ident, root = submit(client)
    claim = claim_job(main.settings, "worker")
    async def forbidden(*args, **kwargs): raise AssertionError("must not execute")
    monkeypatch.setattr(AgentService, "run", forbidden)
    settings = main.settings.model_copy(update={"llm_model":"different"})
    asyncio.run(execute_claim(claim, settings))
    assert client.get(root).json()["status"] == "failed"
    assert "未调用模型" in client.get(root).json()["events"][-1]["data"]["message"]


def test_disconnect_subscriber_does_not_own_or_cancel_execution(client, monkeypatch):
    from app.run_queue import event_stream
    _, ident, root = submit(client, "report")
    async def scenario():
        waiting, release = asyncio.Event(), asyncio.Event()
        original = AgentService.run
        async def slow(self, *args, **kwargs):
            waiting.set()
            await release.wait()
            return await original(self, *args, **kwargs)
        monkeypatch.setattr(AgentService, "run", slow)
        claim = claim_job(main.settings, "worker")
        task = asyncio.create_task(execute_claim(claim, main.settings))
        await waiting.wait()
        subscription = event_stream(ident, 0).body_iterator
        assert "run.queued" in await anext(subscription)
        await subscription.aclose()
        assert not task.done()
        release.set()
        await task
    asyncio.run(scenario())
    assert client.get(root).json()["status"] == "completed"


def test_worker_stop_acknowledges_and_retains_completed_task(client, monkeypatch):
    waiting = threading.Event()
    plan = Plan.model_validate({"reason":"stop fixture", "steps":[
        {"id":"report", "agent":"report", "title":"Repository"},
        {"id":"docs", "agent":"knowledge", "title":"Documents", "query":"租户", "depends_on":["report"]},
    ]})
    monkeypatch.setattr(Planner, "fallback", lambda *args: plan)
    original = AgentService.specialist
    async def slow(self, task, target=None):
        if task == "knowledge":
            waiting.set()
            await asyncio.Event().wait()
        return await original(self, task, target)
    monkeypatch.setattr(AgentService, "specialist", slow)
    _, ident, root = submit(client)
    settings = main.settings.model_copy(update={"queue_poll_seconds":0.1})
    async def scenario():
        stop = asyncio.Event()
        worker = asyncio.create_task(run_worker(settings, stop))
        async with asyncio.timeout(5):
            while not waiting.is_set(): await asyncio.sleep(0.01)
        # Stop request crosses independent HTTP and worker loops through SQL.
        await asyncio.to_thread(client.post, root + "/stop")
        for _ in range(100):
            with SessionLocal() as db: status = db.get(AgentRun, ident).status
            if status == "cancelled": break
            await asyncio.sleep(0.02)
        assert status == "cancelled"
        stop.set(); await worker
    asyncio.run(scenario())
    detail = client.get(root).json()
    assert detail["recovery"]["settled_tasks"] == 1
    assert detail["stop_requested"]
    assert client.get("/api/queue/status").json()["workers_online"] == 0


def test_worker_shutdown_preserves_checkpoint_for_manual_resume(client, monkeypatch):
    waiting = asyncio.Event()
    original = AgentService.run
    async def blocked(self, *args, **kwargs):
        waiting.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(AgentService, "run", blocked)
    _, ident, root = submit(client)
    async def scenario():
        stop = asyncio.Event()
        worker = asyncio.create_task(run_worker(main.settings.model_copy(update={"queue_poll_seconds":0.1}), stop))
        async with asyncio.timeout(5): await waiting.wait()
        stop.set()
        await worker
    asyncio.run(scenario())
    detail = client.get(root).json()
    assert detail["status"] == "interrupted"
    assert detail["recovery"]["available"]
    assert detail["events"][-1]["type"] == "run.interrupted"
    assert not detail["stop_requested"]
    assert claim_job(main.settings, "replacement") is None
    monkeypatch.setattr(AgentService, "run", original)
    assert client.post(root + "/resume-background").status_code == 202
    asyncio.run(execute_claim(claim_job(main.settings, "replacement"), main.settings))
    assert client.get(root).json()["status"] == "completed"


def test_nonworkflow_job_pins_document_version_at_submission(client):
    repo = client.get("/api/repositories").json()[0]["id"]
    doc = {"path":"docs/local.md", "title":"本地约定", "content":"# 隔离\n租户必须隔离。"}
    assert client.post(f"/api/repositories/{repo}/knowledge/documents", json=doc).status_code == 200
    _, ident, root = submit(client, "knowledge", message="租户")
    doc["content"] = "# 新版\n新约定不含相关内容。"
    client.post(f"/api/repositories/{repo}/knowledge/documents", json=doc)
    claim = claim_job(main.settings, "worker")
    asyncio.run(execute_claim(claim, main.settings))
    detail = client.get(root).json()
    assert detail["status"] == "completed"
    assert any("租户必须隔离" in x["content"] for x in detail["result"]["evidence"])


def test_concurrent_workers_only_one_can_claim_same_job(client):
    _, ident, _ = submit(client, "report")
    barrier = threading.Barrier(2)
    def claim(number):
        barrier.wait(timeout=5)
        return claim_job(main.settings, str(number))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, (1, 2)))
    assert sum(bool(x) for x in results) == 1
    assert next(x for x in results if x)["run_id"] == ident


def test_worker_honors_run_concurrency_and_processes_waiting_jobs(client, monkeypatch):
    ids = [submit(client, "report")[1] for _ in range(3)]
    async def scenario():
        entered, release, stop = asyncio.Event(), asyncio.Event(), asyncio.Event()
        active, maximum = 0, 0
        original = AgentService.run
        async def gated(self, *args, **kwargs):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            if active == 2: entered.set()
            try:
                await release.wait()
                return await original(self, *args, **kwargs)
            finally: active -= 1
        monkeypatch.setattr(AgentService, "run", gated)
        worker = asyncio.create_task(run_worker(main.settings.model_copy(update={"queue_poll_seconds":0.1, "worker_concurrency":2}), stop))
        try:
            async with asyncio.timeout(5): await entered.wait()
            with SessionLocal() as db:
                states = [db.get(AgentRun, x).status for x in ids]
            assert states.count("running") == 2 and states.count("queued") == 1
            release.set()
            async with asyncio.timeout(5):
                while True:
                    with SessionLocal() as db: completed = all(db.get(AgentRun, x).status == "completed" for x in ids)
                    if completed: break
                    await asyncio.sleep(0.02)
            assert maximum == 2
        finally:
            release.set(); stop.set(); await worker
    asyncio.run(scenario())


def test_queued_resume_rechecks_pr_head_before_any_model_call(client, monkeypatch):
    from app.checkpoints import model_key
    from app.github import GitHubClient
    repo, ident, root = submit(client)
    asyncio.run(execute_claim(claim_job(main.settings, "initial"), main.settings))
    settings = main.settings.model_copy(update={"devflow_mode":"live", "llm_model":"test"})
    monkeypatch.setattr(main, "settings", settings)
    with SessionLocal() as db:
        run, job, cp = db.get(AgentRun, ident), db.get(RunJob, ident), db.get(RunCheckpoint, ident)
        from app.db import Repository
        repository = db.get(Repository, repo)
        repository.snapshot = {**repository.snapshot,"source":"github"}
        run.mode, run.status, job.status = "live", "interrupted", "interrupted"
        state = json.loads(json.dumps(cp.state))
        from app.prompts import freeze_binding
        state["input"].update(mode="live", model_key=model_key(settings), analysis_binding=freeze_binding(settings))
        cp.state = state
        db.commit()
        pr = next(x for x in state["outcomes"].values() if x["agent"] == "pr")
        head = pr["pr_context"]["head_sha"]
    calls = []
    async def current(*args, **kwargs):
        calls.append(True)
        return {"head":{"sha":head if len(calls) == 1 else "c"*40}}
    async def forbidden(*args, **kwargs): raise AssertionError("must stop before model call")
    monkeypatch.setattr(GitHubClient, "get", current)
    monkeypatch.setattr(AgentService, "run", forbidden)
    assert client.post(root + "/resume-background").status_code == 202
    asyncio.run(execute_claim(claim_job(settings, "resuming"), settings))
    detail = client.get(root).json()
    assert detail["status"] == "failed" and "head SHA" in detail["events"][-1]["data"]["message"]
    assert len(calls) == 2
