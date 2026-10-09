import asyncio
import json

import httpx
import pytest
from sqlalchemy import select

from app import retrieval
from app.config import Settings
from app.db import EmbeddingCache, KnowledgeChunk, KnowledgeDocument, KnowledgeIndex, Repository, SessionLocal
from app.knowledge import bm25, chunk_markdown, corpus_hash, digest, load_corpus, reciprocal_rank_fusion, upsert_document
from app.tools import Tools
from app.vector import EmbeddingClient, MilvusStore, RerankClient, RetrievalError, index_key


def first_repo(client):
    return client.get("/api/repositories").json()[0]["id"]


def configured(**kwargs):
    return Settings(embedding_model="test-embed", embedding_api_key="test", embedding_dimensions=3, **kwargs)


def test_markdown_headings_fences_and_line_citations():
    text = "# Intro\nsetup\n## Code\n````python\n# inside\n```\n# still inside\n````\n## Release\nship"
    chunks = chunk_markdown(text, "Guide", size=200, overlap=20)
    assert [(x["heading"], x["line_start"], x["line_end"]) for x in chunks] == [("Intro", 1, 2), ("Intro / Code", 3, 8), ("Intro / Release", 9, 10)]
    assert "# still inside" in chunks[1]["content"]


def test_import_updates_chunks_and_invalidates_old_cache(client):
    repo_id = first_repo(client)
    root = f"/api/repositories/{repo_id}/knowledge"
    payload = {"title": "Manual", "path": "docs/manual.md", "content": "# Database\npostgres tenant isolation"}
    first = client.post(root + "/documents", json=payload).json()
    assert first["changed"]
    second = client.post(root + "/documents", json=payload).json()
    assert first["id"] == second["id"] and not second["changed"]
    with SessionLocal() as db:
        chunk = db.scalar(select(KnowledgeChunk).where(KnowledgeChunk.document_id == first["id"]))
        old_id = chunk.id
        db.add(EmbeddingCache(id="cached", chunk_id=old_id, model_key="test", vector=[1, 0, 0]))
        db.commit()
    payload["content"] = "# Database\nnew migrations policy"
    client.post(root + "/documents", json=payload).raise_for_status()
    with SessionLocal() as db:
        assert db.get(KnowledgeChunk, old_id) is None
        assert db.get(EmbeddingCache, "cached") is None
    hit = client.post(root + "/search", json={"query": "migrations"}).json()["results"][0]
    assert hit["citation"] == "docs/manual.md L1-L2"
    assert hit["source"] == "manual" and hit["methods"] == ["bm25"]
    assert hit["revision"] == digest(payload["content"])


def test_long_identical_lines_do_not_collide(client):
    repo_id = first_repo(client)
    with SessionLocal() as db:
        doc, _ = upsert_document(db, repo_id, {"id": "long.md", "title": "Long", "content": "a" * 2200}, Settings(chunk_size=200))
        db.commit()
        chunks = list(db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.document_id == doc.id)))
        assert len(chunks) == 11 and len({x.id for x in chunks}) == 11


def test_repository_isolation_archive_and_validation(client):
    one = first_repo(client)
    with SessionLocal() as db:
        other = Repository(full_name="demo/other", snapshot={"source": "demo"})
        db.add(other); db.flush()
        other_id = other.id
        upsert_document(db, other.id, {"id": "secret.md", "title": "Other", "content": "uniquetermtenant"}, Settings())
        db.commit()
    root = f"/api/repositories/{one}/knowledge"
    assert client.post(root + "/search", json={"query": "uniquetermtenant"}).json()["results"] == []
    assert client.post(root + "/documents", json={"title": "x", "path": "../outside", "content": "x"}).status_code == 422
    assert client.post(root + "/search", json={"query": "  "}).status_code == 422
    doc = client.post(root + "/documents", json={"title": "Manual", "path": "mine.md", "content": "uniquetermmanual"}).json()
    assert client.post(f"/api/repositories/{other_id}/knowledge/documents/{doc['id']}/archive").status_code == 404
    assert client.post(root + f"/documents/{doc['id']}/archive").status_code == 200
    assert client.post(root + "/search", json={"query": "uniquetermmanual"}).json()["results"] == []
    assert client.post(root + "/index").status_code == 409
    assert client.post(root + "/search", json={"query": "tenant", "mode": "hybrid"}).status_code == 409


def test_bm25_rrf_and_citation_metadata_hash():
    chunks = [{"chunk_id": "a", "title": "Policy", "heading": "tenants", "content": "tenant isolation"}, {"chunk_id": "b", "title": "Build", "heading": "CI", "content": "pytest workflow"}]
    assert [x["chunk_id"] for x in bm25(chunks, "tenant")] == ["a"]
    assert bm25(chunks, "absentzzz") == []
    lexical = bm25(chunks, "tenant")
    vector = [{**chunks[1], "score": .9, "vector_score": .9, "methods": ["vector"]}, {**chunks[0], "score": .8, "vector_score": .8, "methods": ["vector"]}]
    merged = reciprocal_rank_fusion([lexical, vector])
    assert merged[0]["chunk_id"] == "a" and merged[0]["methods"] == ["bm25", "vector"]
    assert reciprocal_rank_fusion([lexical + lexical])[0]["score"] == 1 / 61
    item = {**chunks[0], "revision": None, "url": "", "path": "a.md", "line_start": 1, "line_end": 2}
    assert corpus_hash([item]) != corpus_hash([{**item, "line_start": 2}])


def test_index_activation_cache_and_stale_corpus(client, monkeypatch):
    repo_id = first_repo(client)
    settings = configured()
    calls, writes = [], []
    async def embed(self, texts, expected_dimension=None):
        calls.append(len(texts)); return [[1., 0., 0.] for _ in texts]
    async def write(self, repo, generation, chunks, vectors):
        writes.append((repo, generation, len(chunks)))
    monkeypatch.setattr(EmbeddingClient, "embed", embed)
    monkeypatch.setattr(MilvusStore, "write", write)
    with SessionLocal() as db:
        generation, started = retrieval.begin_index(db, repo_id, settings)
        duplicate, again = retrieval.begin_index(db, repo_id, settings)
        assert started and not again and duplicate == generation
    asyncio.run(retrieval.build_index(repo_id, generation, settings))
    with SessionLocal() as db:
        assert retrieval.status_for(db, repo_id, settings)["vector_ready"]
        generation2, _ = retrieval.begin_index(db, repo_id, settings)
    asyncio.run(retrieval.build_index(repo_id, generation2, settings))
    assert len(calls) == 1 and len(writes) == 2
    with SessionLocal() as db:
        doc = db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.repository_id == repo_id))
        doc.content += "\nnew text"
        upsert_document(db, repo_id, {"id": doc.external_id, "title": doc.title, "content": doc.content}, settings, doc.source)
        db.commit()
        assert retrieval.status_for(db, repo_id, settings)["index"]["status"] == "stale"
    with pytest.raises(RetrievalError, match="过期"):
        asyncio.run(retrieval.search(repo_id, "tenant", settings, "hybrid"))


def test_corpus_changed_during_index_is_never_activated(client, monkeypatch):
    repo_id = first_repo(client)
    settings = configured()
    async def embed(self, texts, expected_dimension=None):
        return [[1., 0., 0.] for _ in texts]
    async def write(self, *args):
        with SessionLocal() as db:
            doc = db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.repository_id == repo_id))
            doc.is_deleted = True; db.commit()
    monkeypatch.setattr(EmbeddingClient, "embed", embed)
    monkeypatch.setattr(MilvusStore, "write", write)
    with SessionLocal() as db:
        generation, _ = retrieval.begin_index(db, repo_id, settings)
    asyncio.run(retrieval.build_index(repo_id, generation, settings))
    with SessionLocal() as db:
        state = retrieval.status_for(db, repo_id, settings)
        assert not state["vector_ready"] and state["index"]["status"] == "failed"
        assert "文档发生变化" in state["index"]["message"]


def test_hybrid_excludes_foreign_chunks_and_filters_generation(client, monkeypatch):
    repo_id = first_repo(client)
    settings = configured()
    with SessionLocal() as db:
        chunks = load_corpus(db, repo_id)
        db.add(KnowledgeIndex(repository_id=repo_id, status="ready", generation="current", corpus_hash=corpus_hash(chunks), index_key=index_key(settings), dimension=3)); db.commit()
    async def embed(self, *args, **kwargs):
        return [[1., 0., 0.]]
    async def search(self, repo, generation, vector, limit):
        assert repo == repo_id and generation == "current"
        return [{"chunk_id": "foreign", "vector_score": 1.}, {"chunk_id": chunks[0]["chunk_id"], "vector_score": .8}]
    monkeypatch.setattr(EmbeddingClient, "embed", embed)
    monkeypatch.setattr(MilvusStore, "search", search)
    result = asyncio.run(retrieval.search(repo_id, "absentzzz", settings, "hybrid"))
    assert len(result["results"]) == 1 and result["warnings"]
    assert result["results"][0]["methods"] == ["vector"]
    class FakeClient:
        def has_collection(self, name): return True
        def load_collection(self, **kwargs): pass
        def search(self, **kwargs):
            assert kwargs["filter"] == f'repository_id == {json.dumps(repo_id)} and generation == "current"'
            return []
        def close(self): pass
    monkeypatch.setattr(MilvusStore, "_client", lambda self: FakeClient())
    assert MilvusStore(settings, 3)._search(repo_id, "current", [1., 0., 0.], 5) == []


@pytest.mark.parametrize("data", [[], [{"index": 0, "embedding": [0, 0, 0]}], [{"index": 0, "embedding": [1, 2]}], [{"index": 1, "embedding": [1, 0, 0]}], [{"index": 0, "embedding": [True, 0, 0]}]])
def test_embedding_invalid_data_is_safe_error(data, monkeypatch):
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"data": data}))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))
    with pytest.raises(RetrievalError, match="无效"):
        asyncio.run(EmbeddingClient(configured()).embed(["text"]))


def test_agent_vector_failure_reports_explicit_bm25_fallback(client):
    repo_id = first_repo(client)
    with SessionLocal() as db:
        snapshot = db.get(Repository, repo_id).snapshot
    events = []
    async def emit(kind, data): events.append((kind, data))
    tools = Tools(Settings(retrieval_backend="milvus"), snapshot, emit, repository_id=repo_id)
    docs = asyncio.run(tools.call("search_knowledge", {"query": "租户权限"}))
    assert docs and docs[0]["methods"] == ["bm25"]
    assert any(x[0] == "retrieval.warning" for x in events) and tools.retrieval_notices
    assert all(x.get("citation") for x in tools.evidence.values())


def test_rerank_rejects_duplicate_indexes(monkeypatch):
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"results": [{"index": 0, "relevance_score": .8}, {"index": 0, "relevance_score": .7}]}))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))
    settings = Settings(rerank_api_key="test", rerank_model="test")
    with pytest.raises(RetrievalError, match="无效"):
        asyncio.run(RerankClient(settings).rerank("query", [{"title": "a", "content": "x", "methods": ["bm25"]}, {"title": "b", "content": "y", "methods": ["bm25"]}], 2))
