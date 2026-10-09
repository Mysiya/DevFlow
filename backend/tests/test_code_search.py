import asyncio

import pytest

from app.code_evaluation import evaluate_code, score_code
from app.code_search import MAX_CHARS, MAX_LINES, chunks, code_tokens, ranked_search
from app.demo import demo_snapshot
from app.tools import Tools
from app.workspace import WorkspaceError
from test_workspace import code_fixture


def test_identifier_parts_preserve_exact_spelling_and_acronyms():
    terms = set(code_tokens("StreamingResponse HTTPRequest renew_lease 后台租约"))
    assert {"streamingresponse", "streaming", "response", "httprequest", "http", "request", "renew_lease", "renew", "lease", "后台", "租约"} <= terms
    assert "后台租约" not in terms


def test_decorated_nested_definitions_have_correct_qualified_names_and_ranges():
    text = "class Worker:\n    @guard\n    async def renew_lease(self):\n        def token():\n            return 'ok'\n        return token()\n\nx = 1\n"
    pieces, omitted, failed = chunks("worker.py", text)
    assert not failed and not omitted
    inner = next(c for c in pieces if c["symbol"] == "Worker.renew_lease.token")
    assert (inner["line_start"], inner["line_end"]) == (4, 5)
    method = next(c for c in pieces if c["line_start"] == 2)
    assert method["symbol"] == "Worker.renew_lease" and method["symbol_start"] == 2 and method["symbol_end"] == 6
    lines = text.splitlines()
    covered = set()
    for piece in pieces:
        assert piece["content"] == "\n".join(lines[piece["line_start"] - 1:piece["line_end"]])
        covered.update(range(piece["line_start"], piece["line_end"] + 1))
    assert covered == set(range(1, len(lines) + 1))


def test_source_parsing_never_runs_top_level_or_decorators(tmp_path):
    marker = tmp_path / "must-not-exist"
    text = f"open({str(marker)!r}, 'w').write('executed')\n@dangerous()\ndef verify_signature():\n    return True\n"
    found = ranked_search([{"path": "unsafe.py", "text": text}], "verify_signature")["results"]
    assert found[0]["symbol"] == "verify_signature" and not marker.exists()


@pytest.mark.parametrize("text", ["def broken(:\n    await lease\n", "class Broken\n    lease = 1\n"])
def test_invalid_python_remains_searchable_with_explicit_fallback(text):
    found = ranked_search([{"path": "broken.py", "text": text}], "lease")
    assert found["parse_fallback_files"] == 1 and found["results"]
    assert found["results"][0]["chunk_kind"] == "line_window" and found["results"][0]["symbol"] is None


def test_parser_unsupported_grammar_uses_same_fallback(monkeypatch):
    from app import code_search
    def unsupported(*args): raise SyntaxError("unsupported grammar")
    monkeypatch.setattr(code_search.ast, "parse", unsupported)
    found = ranked_search([{"path": "future.py", "text": "def future[T](lease: T):\n    return lease\n"}], "lease")
    assert found["parse_fallback_files"] == 1 and found["results"][0]["chunk_kind"] == "line_window"


def test_typescript_uses_line_windows_without_claiming_ast_symbols():
    found = ranked_search([{"path": "view.tsx", "text": "export function approveDraft() { return revision; }"}], "approve draft")
    assert found["results"][0]["chunk_kind"] == "line_window"
    assert found["results"][0]["symbol"] is None


def test_long_function_is_bounded_covers_all_lines_and_keeps_symbol_range():
    text = "def renew_lease():\n" + "".join(f"    counter_{i} = {i}\n" for i in range(210)) + "    return counter_209\n"
    pieces, omitted, failed = chunks("worker.py", text)
    assert not omitted and not failed
    assert len(pieces) > 1
    covered = set()
    for piece in pieces:
        assert piece["partial_symbol"] and piece["symbol"] == "renew_lease"
        assert piece["symbol_start"] == 1 and piece["symbol_end"] == 212
        assert piece["line_end"] - piece["line_start"] + 1 <= MAX_LINES
        assert len(piece["content"]) <= MAX_CHARS
        covered.update(range(piece["line_start"], piece["line_end"] + 1))
    assert covered == set(range(1, 213))
    hits = ranked_search([{"path": "worker.py", "text": text}], "counter_209")["results"]
    assert "return counter_209" in hits[0]["content"]
    for i, left in enumerate(hits):
        for right in hits[i + 1:]:
            overlap = max(0, min(left["line_end"], right["line_end"]) - max(left["line_start"], right["line_start"]) + 1)
            assert overlap / min(left["line_end"] - left["line_start"] + 1, right["line_end"] - right["line_start"] + 1) <= .5


def test_character_bound_keeps_exact_lines_and_reports_omitted_long_line():
    text = "before = 1\n" + "x" * (MAX_CHARS + 1) + "\nafter = 2\n"
    pieces, omitted, _ = chunks("huge.txt", text)
    assert omitted == 1 and len(pieces) == 2
    assert [(p["line_start"], p["line_end"]) for p in pieces] == [(1, 1), (3, 3)]
    found = ranked_search([{"path": "huge.txt", "text": text}], "after")
    assert found["omitted_lines"] == 1 and found["results"][0]["line_start"] == 3


def test_definition_outranks_mentions_and_path_can_match_without_body_terms():
    files = [{"path": "a-notes.md", "text": "verify_signature\n"},
             {"path": "security.py", "text": "def verify_signature(raw):\n    return digest(raw)\n"}]
    assert ranked_search(files, "verify_signature")["results"][0]["path"] == "security.py"
    assert ranked_search(files, "security.py")["results"][0]["path"] == "security.py"
    assert ranked_search(files, "zzq_fixture_none")["results"] == []


def test_all_identifier_parts_outrank_single_generic_method_name():
    files = [{"path": "a.py", "text": "def search():\n    ranked = []\n    return ranked\n"},
             {"path": "ranker.py", "text": "def ranked_search(files):\n    counts = Counter(files)\n    frequencies = calculate(counts)\n    return score(frequencies)\n"}]
    assert ranked_search(files, "ranked search")["results"][0]["symbol"] == "ranked_search"


def test_live_workspace_search_pins_sha_redacts_and_preserves_tool_metadata(code_fixture):
    repo_id, project, manager, settings = code_fixture
    (project / "app" / "main.py").write_text("async def renewLease():\n    token = '" + settings.llm_api_key.get_secret_value() + "'\n    return token\n", encoding="utf-8")
    asyncio.run(manager.sync(repo_id, "demo/devflow-shop", "local_project", {}))
    original = manager.ref(repo_id)
    async def emit(*args): pass
    tools = Tools(settings, demo_snapshot(), emit, repo_id)
    (project / "app" / "main.py").write_text("def different():\n    return 'new version'\n", encoding="utf-8")
    asyncio.run(manager.sync(repo_id, "demo/devflow-shop", "local_project", {}))
    found = asyncio.run(tools.call("search_code", {"query": "renew lease", "path_prefix": "app"}))
    hit = found["results"][0]
    assert found["sha"] == original["sha"] and hit["symbol"] == "renewLease"
    assert hit["redacted"] and "[REDACTED]" in hit["content"] and settings.llm_api_key.get_secret_value() not in hit["content"]
    evidence = tools.evidence[hit["id"]]
    assert evidence["symbol"] == "renewLease" and evidence["retrieval_method"] == "code-bm25-v1"
    assert manager.search(original, "renew lease", "application")["results"] == []
    with pytest.raises(WorkspaceError): manager.search(original, "lease", limit=21)


def test_fixed_code_regression_has_reproducible_meaningful_comparison():
    one, two = evaluate_code(), evaluate_code()
    assert one == two and one["positive_cases"] == 8
    current, baseline = one["strategies"][one["current_strategy"]], one["strategies"]["line_keyword_v09"]
    assert current["passed"] == current["total"] == 10
    assert current["mean"]["recall_at_5"] > baseline["mean"]["recall_at_5"]


def test_code_scorer_rejects_wrong_path_partial_lines_and_rank_six():
    target = {"path": "worker.py", "line_start": 2, "line_end": 20}
    assert not score_code([{**target, "path": "other.py"}], [target])["passed"]
    assert not score_code([{**target, "line_end": 19}], [target])["passed"]
    assert not score_code([{"path": "noise.py", "line_start": 1, "line_end": 30}] * 5 + [target], [target])["passed"]
    assert not score_code([target], [])["passed"]
    assert score_code([target], [target])["mrr_at_5"] == 1


def test_search_tool_reports_partial_coverage_to_agent(code_fixture):
    repo_id, project, manager, settings = code_fixture
    (project / "app" / "broken.py").write_text("def broken(:\n    lease = 1\n", encoding="utf-8")
    (project / "app" / "huge.py").write_text("VALUE = '" + "z" * (MAX_CHARS + 1) + "'\n", encoding="utf-8")
    asyncio.run(manager.sync(repo_id, "demo/devflow-shop", "local_project", {}))
    events = []
    async def emit(kind, data): events.append((kind, data))
    tools = Tools(settings, demo_snapshot(), emit, repo_id)
    found = asyncio.run(tools.call("search_code", {"query": "lease", "path_prefix": "app"}))
    assert found["parse_fallback_files"] == found["omitted_lines"] == 1
    assert tools.retrieval_notices and any(kind == "retrieval.warning" for kind, _ in events)
    assert found["results"][0]["path"] == "app/broken.py"


def test_search_api_exposes_cited_definition_and_scope_limits(client, code_fixture, monkeypatch):
    from app import main
    repo_id, _, manager, settings = code_fixture
    monkeypatch.setattr(main, "settings", settings)
    response = client.post(f"/api/repositories/{repo_id}/code/search", json={"query": "stream", "path_prefix": "app", "top_k": 1})
    assert response.status_code == 200
    found = response.json()
    assert found["retrieval_method"] == "code-bm25-v1" and found["searched_chunks"] > 0
    hit = found["results"][0]
    assert hit["symbol"] == "stream" and hit["sha"] == manager.ref(repo_id)["sha"]
    assert hit["content"] == manager.read(manager.ref(repo_id), hit["path"], hit["line_start"], hit["line_end"])["content"]
    assert client.post(f"/api/repositories/{repo_id}/code/search", json={"query": "stream", "path_prefix": "../other"}).status_code == 409
