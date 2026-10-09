import asyncio
import json

import pytest
from pydantic import ValidationError

from app.agents import AgentService
from app.config import Settings
from app.demo import DEMO_SHA, demo_snapshot
from app.llm import ModelClient
from app.tools import Tools, search_documents


def read_events(response):
    return [json.loads(frame[6:]) for frame in response.text.split("\n\n") if frame.startswith("data: ")]


def start(client, task="workflow", target=None, **extra):
    repo = client.get("/api/repositories").json()[0]
    response = client.post("/api/chat/stream", json={"repository_id": repo["id"], "message": "检查当前版本是否可以发布", "task": task, "target": target, **extra})
    return repo, response


def test_workflow_preserves_evidence_and_progress(client):
    repo, response = start(client)
    assert response.status_code == 200
    events = read_events(response)
    assert [x["sequence"] for x in events] == list(range(1, len(events) + 1))
    assert sum(x["type"] == "observer" for x in events) == 1
    answer = events[-1]["data"]["result"]
    assert answer["recommendation"] == "暂缓发布"
    assert answer["provider"] == "demo-rules"
    refs = {x["id"]: x for x in answer["evidence"]}
    assert refs["pr:18"]["sha"] == refs["ci:301"]["sha"] == DEMO_SHA
    assert all(set(x["evidence_ids"]).issubset(refs) for x in answer["findings"])
    run_id = events[-1]["data"]["run_id"]
    stored = client.get(f"/api/repositories/{repo['id']}/runs/{run_id}").json()
    assert stored["status"] == "completed"
    assert len(stored["events"]) == len(events)


def test_selected_pr_is_used_and_unrelated_failure_is_not(client):
    _, response = start(client, "pr", 19)
    answer = read_events(response)[-1]["data"]["result"]
    assert answer["title"] == "PR #19 风险分析"
    assert not any(x["severity"] == "high" for x in answer["findings"])
    assert "pr:18" not in {x["id"] for x in answer["evidence"]}


def test_missing_target_becomes_failed_run(client):
    repo, response = start(client, "pr", 999)
    events = read_events(response)
    assert events[-1]["type"] == "run.failed"
    stored = client.get(f"/api/repositories/{repo['id']}/runs").json()
    assert stored[0]["status"] == "failed"
    assert stored[0]["result"] is None


def test_explicit_number_in_specialist_question_is_respected(client):
    _, response = start(client, "pr", message="请审查 PR #19 的风险")
    answer = read_events(response)[-1]["data"]["result"]
    assert answer["title"] == "PR #19 风险分析"


def test_drafts_are_idempotent_local_artifacts(client):
    repo, response = start(client, "pr", 18)
    run_id = read_events(response)[-1]["data"]["run_id"]
    url = f"/api/repositories/{repo['id']}/drafts"
    first = client.post(url, json={"run_id": run_id}).json()
    second = client.post(url, json={"run_id": run_id}).json()
    assert first["id"] == second["id"]
    assert first["status"] == "draft"
    assert len(client.get(url).json()) == 1
    assert client.get("/api/health").json()["external_writes"] is False


def test_scope_and_mode_are_enforced(client):
    assert client.post("/api/repositories", json={"full_name": "other/repository"}).status_code == 400
    assert client.post("/api/repositories", json={"full_name": "../../etc/passwd"}).status_code == 422
    repo = client.get("/api/repositories").json()[0]
    assert client.post("/api/chat/stream", json={"repository_id": repo["id"], "conversation_id": "unknown", "message": "hi"}).status_code == 404
    assert client.get(f"/api/repositories/{repo['id']}/runs/unknown").status_code == 404


def test_keyword_search_does_not_invent_results():
    assert search_documents(demo_snapshot()["documents"], "租户权限")
    assert search_documents(demo_snapshot()["documents"], "zzzxxyy") == []


def test_unknown_tool_and_invalid_number_rejected():
    async def scenario():
        async def emit(*args):
            pass
        tools = Tools(Settings(), demo_snapshot(), emit)
        with pytest.raises(ValueError, match="未注册"):
            await tools.call("delete_repository", {})
        with pytest.raises(ValidationError):
            await tools.call("inspect_pr", {"number": -1})
    asyncio.run(scenario())


def test_live_loop_stops_repeated_calls(monkeypatch):
    async def scenario():
        async def emit(*args):
            pass
        async def fake_chat(self, messages, tools=None, structured=False):
            return {"role": "assistant", "content": None, "tool_calls": [{"id": "call1", "type": "function", "function": {"name": "get_repo_health", "arguments": "{}"}}]}
        monkeypatch.setattr(ModelClient, "chat", fake_chat)
        settings = Settings(devflow_mode="live", llm_model="test", llm_api_key="test")
        tools = Tools(settings, demo_snapshot(), emit)
        try:
            with pytest.raises(ValueError, match="重复工具调用"):
                await AgentService(tools, "检查仓库").conversation()
        finally:
            await tools.close()
    asyncio.run(scenario())


def test_model_fabricated_citation_rejected(monkeypatch):
    async def scenario():
        async def fake_chat(self, *args, **kwargs):
            return {"content": json.dumps({"title": "检查", "summary": "总结", "recommendation": "核验", "findings": [{"severity": "high", "title": "问题", "detail": "说明", "evidence_ids": ["fake:123"]}]})}
        monkeypatch.setattr(ModelClient, "chat", fake_chat)
        with pytest.raises(ValueError, match="未提供的证据"):
            await ModelClient(Settings()).analyze("PR", "review", {})
    asyncio.run(scenario())
