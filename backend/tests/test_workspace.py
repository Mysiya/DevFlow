import asyncio
from pathlib import Path

import pytest

from app.config import Settings
from app.workspace import WorkspaceError, WorkspaceManager, readable_path, redact, safe_path
from app.db import SessionLocal, Repository
from app.tools import Tools
from app.demo import DEMO_SHA, demo_snapshot


@pytest.fixture
def code_fixture(client, tmp_path):
    repo_id = client.get("/api/repositories").json()[0]["id"]
    project = tmp_path / "project"
    (project / "app").mkdir(parents=True)
    (project / "app" / "main.py").write_text("async def stream():\n    yield 'first'\n", encoding="utf-8")
    (project / "app" / "empty.py").write_text("", encoding="utf-8")
    (project / ".env").write_text("PRIVATE=never-read", encoding="utf-8")
    (project / "credentials.json").write_text('{"token":"never-read"}', encoding="utf-8")
    settings = Settings(workspace_root=str(tmp_path / "stores"), llm_api_key="sk-" + "A" * 24)
    manager = WorkspaceManager(settings); manager.project = project
    asyncio.run(manager.sync(repo_id, "demo/devflow-shop", "local_project", {}))
    return repo_id, project, manager, settings


def test_real_git_snapshot_pins_version_and_is_idempotent(code_fixture):
    repo_id, project, manager, _ = code_fixture
    old = manager.ref(repo_id)
    assert manager.files(old)["files"]
    asyncio.run(manager.sync(repo_id, "demo/devflow-shop", "local_project", {}))
    assert manager.ref(repo_id) == old
    (project / "app" / "main.py").write_text("async def stream():\n    yield 'second'\n", encoding="utf-8")
    asyncio.run(manager.sync(repo_id, "demo/devflow-shop", "local_project", {}))
    current = manager.ref(repo_id)
    assert current["sha"] != old["sha"]
    assert "first" in manager.read(old, "app/main.py")["content"]
    assert "second" in manager.read(current, "app/main.py")["content"]
    assert manager.read(current, "app/empty.py")["total_lines"] == 0


def test_code_search_citations_redaction_and_secret_file_exclusion(code_fixture):
    repo_id, project, manager, settings = code_fixture
    (project / "app" / "config.py").write_text('TOKEN="' + settings.llm_api_key.get_secret_value() + '"\n', encoding="utf-8")
    asyncio.run(manager.sync(repo_id, "demo/devflow-shop", "local_project", {}))
    ref = manager.ref(repo_id)
    assert {x["path"] for x in manager.files(ref)["files"]} == {"app/main.py", "app/empty.py", "app/config.py"}
    result = manager.search(ref, "yield", "app")
    assert len(result["results"]) == 1
    assert result["results"][0]["citation"] == "app/main.py L1-L2"
    assert result["results"][0]["sha"] == ref["sha"]
    assert manager.search(ref, "zzznomatch")["results"] == []
    content = manager.read(ref, "app/config.py")["content"]
    assert "[REDACTED]" in content and settings.llm_api_key.get_secret_value() not in content
    with pytest.raises(WorkspaceError): manager.read(ref, ".env")


def test_bootstrap_admin_password_is_redacted_from_source():
    settings=Settings(bootstrap_admin_password='fixture-admin-password-123')
    text,changed=redact('PASSWORD="fixture-admin-password-123"',settings)
    assert changed and text=='PASSWORD="[REDACTED]"'


def test_android_tool_caches_never_enter_source_snapshots_or_citations(code_fixture):
    repo_id,project,manager,_=code_fixture
    java=project/'mobile/android/app/src/main/java/MainActivity.java'
    java.parent.mkdir(parents=True);java.write_text('class MainActivity {}',encoding='utf-8')
    hidden=['mobile/android/.local/sdk/NOTICE','mobile/android/.local/sdk-consent.json',
            'mobile/android/.gradle/cache/build-info.json']
    for name in hidden:
        path=project/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('TOOL_CACHE_MUST_NOT_BE_IMPORTED',encoding='utf-8')
    asyncio.run(manager.sync(repo_id,'demo/devflow-shop','local_project',{}))
    ref=manager.ref(repo_id)
    paths={item['path'] for item in manager.files(ref)['files']}
    assert java.relative_to(project).as_posix() in paths
    assert not any(name in paths for name in hidden)
    assert manager.search(ref,'TOOL_CACHE_MUST_NOT_BE_IMPORTED')['results']==[]
    for name in hidden:
        assert not readable_path(name)
        with pytest.raises(WorkspaceError):manager.read(ref,name)


@pytest.mark.parametrize("path", ["../outside.py", "/etc/passwd", "app/../main.py", "C:/file.py", "app\\main.py", "app/main.py\0", "app//main.py"])
def test_code_paths_cannot_escape(path):
    with pytest.raises(WorkspaceError): safe_path(path)


def test_workspace_scope_limits_and_excluded_paths(code_fixture):
    repo_id, project, manager, _ = code_fixture
    ref = manager.ref(repo_id)
    with pytest.raises(WorkspaceError): manager.read({**ref, "repository_id": "b" * 32}, "app/main.py")
    with pytest.raises(WorkspaceError): manager.read(ref, "app/main.py", 1, 401)
    with pytest.raises(WorkspaceError): manager.read(ref, "app/main.py", 9, 10)
    assert not readable_path("node_modules/pkg/index.js")
    assert not readable_path("backend/data/anything.py")
    assert readable_path("backend/.env.example")
    assert not readable_path("keys/private.pem")


def test_file_analysis_reads_requested_path_without_keyword_hits(code_fixture):
    from app.agents import AgentService
    repo_id, _, manager, settings = code_fixture
    async def scenario():
        async def emit(*args): pass
        tools = Tools(settings, demo_snapshot(), emit, repo_id)
        answer = await AgentService(tools, "分析 app/main.py", retrieval_query="app/main.py").specialist("code")
        assert answer["recommendation"] != "证据不足"
        assert any(x.get("path") == "app/main.py" and "yield 'first'" in x["content"] for x in tools.evidence.values())
        await tools.close()
    asyncio.run(scenario())


def test_tool_run_keeps_initial_workspace_ref(code_fixture):
    repo_id, project, manager, settings = code_fixture
    async def emit(*args): pass
    tools = Tools(settings, demo_snapshot(), emit, repo_id)
    initial_sha = tools.workspace_ref["sha"]
    (project / "app" / "main.py").write_text("yield 'later'\n", encoding="utf-8")
    asyncio.run(manager.sync(repo_id, "demo/devflow-shop", "local_project", {}))
    hit = asyncio.run(tools.call("read_code", {"path": "app/main.py"}))
    assert hit["sha"] == initial_sha and "first" in hit["content"]
    pinned_none = Tools(settings, demo_snapshot(), emit, repo_id, workspace_ref=None)
    with pytest.raises(WorkspaceError): asyncio.run(pinned_none.call("search_code", {"query": "yield"}))


def test_api_scope_paths_and_source_metadata(client, code_fixture, monkeypatch):
    repo_id, _, manager, settings = code_fixture
    from app import main
    monkeypatch.setattr(main, "settings", settings)
    root = f"/api/repositories/{repo_id}/code"
    status = client.get(root + "/status").json()
    assert status["source"] == "local_project" and status["sha"]
    assert client.get(root + "/files").json()["source"] == "local_project"
    hit = client.get(root + "/file", params={"path": "app/main.py", "revision": status["sha"]}).json()
    assert hit["source"] == "local_project" and not hit["url"]
    assert client.get(root + "/file", params={"path": "../outside"}).status_code == 409
    assert client.get(root + "/file", params={"path": "app/main.py", "revision": "--help"}).status_code == 422
    assert client.post(root + "/search", json={"query": "  "}).status_code == 409
    assert client.get("/api/repositories/unknown/code/status").status_code == 404


def test_ci_sha_mismatch_is_rejected_before_citation():
    async def emit(*args): pass
    tools = Tools(Settings(), demo_snapshot(), emit, expected_ci_sha=DEMO_SHA)
    with pytest.raises(RuntimeError, match="不匹配"):
        asyncio.run(tools.call("inspect_ci", {"run_id": 303}))
    assert "ci:303" not in tools.evidence
