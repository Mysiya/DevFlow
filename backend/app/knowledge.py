"""Versioned source chunks and repository-scoped BM25 retrieval."""
import hashlib
import math
import re
from collections import Counter

from sqlalchemy import delete, select

from .db import EmbeddingCache, KnowledgeChunk, KnowledgeDocument, utcnow


def digest(value: str):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def tokenize(value: str):
    terms = []
    for part in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", value.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            terms.extend(part[i:i + 2] for i in range(len(part) - 1)) if len(part) > 1 else terms.append(part)
        else:
            terms.append(part)
    return terms


def chunk_markdown(content: str, title: str, size=1000, overlap=120):
    """Keep heading boundaries and source line ranges, including fenced code."""
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    sections, current, headings = [], [], []
    fence = None
    for number, line in enumerate(lines, 1):
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        heading = None if fence else re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            if current:
                sections.append((list(headings), current))
                current = []
            level = len(heading[1])
            headings = headings[:level - 1] + [heading[2]]
        current.append((number, line))
        if marker:
            symbol = marker[1][0]
            if fence is None:
                fence = (symbol, len(marker[1]))
            elif fence[0] == symbol and len(marker[1]) >= fence[1] and not line[marker.end():].strip():
                fence = None
    if current:
        sections.append((list(headings), current))
    chunks = []
    for headings, section in sections:
        # Long individual lines are split while retaining their original line number.
        pieces = [(number, line[i:i + size]) for number, line in section for i in range(0, max(len(line), 1), size)]
        start = 0
        while start < len(pieces):
            end, length = start, 0
            while end < len(pieces) and (end == start or length + len(pieces[end][1]) + 1 <= size):
                length += len(pieces[end][1]) + 1
                end += 1
            selected = pieces[start:end]
            text = "\n".join(x[1] for x in selected).strip()
            if text:
                chunks.append({"content": text, "heading": " / ".join(headings) or title, "line_start": selected[0][0], "line_end": selected[-1][0]})
            if end == len(pieces):
                break
            next_start, kept = end, 0
            while next_start > start + 1 and kept + len(pieces[next_start - 1][1]) + 1 <= overlap:
                next_start -= 1
                kept += len(pieces[next_start][1]) + 1
            start = next_start
    return chunks


def upsert_document(db, repo_id, data: dict, settings, source="manual"):
    ident = digest(repo_id + "\0" + source + "\0" + data["id"])
    content_hash = digest(data["content"])
    chunker_key = f"markdown-v1:{settings.chunk_size}:{settings.chunk_overlap}"
    doc = db.get(KnowledgeDocument, ident)
    rebuild = doc is None or doc.content_hash != content_hash or doc.chunker_key != chunker_key or doc.title != data["title"]
    old_revision = doc.revision if doc else None
    if doc is None:
        doc = KnowledgeDocument(id=ident, repository_id=repo_id, external_id=data["id"])
        db.add(doc)
    doc.title, doc.path = data["title"], data.get("path") or data["id"]
    doc.content, doc.url = data["content"], data.get("url", "")
    doc.source, doc.revision, doc.is_deleted = source, data.get("revision"), False
    if rebuild:
        doc.content_hash, doc.chunker_key, doc.updated_at = content_hash, chunker_key, utcnow()
        old_ids = select(KnowledgeChunk.id).where(KnowledgeChunk.document_id == ident)
        db.execute(delete(EmbeddingCache).where(EmbeddingCache.chunk_id.in_(old_ids)))
        db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == ident))
        db.flush()
        for ordinal, chunk in enumerate(chunk_markdown(data["content"], data["title"], settings.chunk_size, settings.chunk_overlap)):
            chunk_id = digest(ident + "\0" + str(ordinal) + "\0" + chunk["heading"] + "\0" + chunk["content"])
            db.add(KnowledgeChunk(id=chunk_id, repository_id=repo_id, document_id=ident, **chunk))
    elif old_revision != data.get("revision"):
        doc.updated_at = utcnow()
    db.flush()
    return doc, rebuild


def sync_snapshot_documents(db, repository, settings):
    source = repository.snapshot["source"]
    active = []
    changed = 0
    for data in repository.snapshot.get("documents", []):
        doc, rebuilt = upsert_document(db, repository.id, data, settings, source)
        active.append(doc.id)
        changed += rebuilt
    # Snapshot document ingestion currently enumerates demo docs or a single README.
    for doc in db.scalars(select(KnowledgeDocument).where(KnowledgeDocument.repository_id == repository.id, KnowledgeDocument.source == source)):
        if doc.id not in active:
            doc.is_deleted = True
    return changed


def load_corpus(db, repo_id):
    rows = db.execute(select(KnowledgeChunk, KnowledgeDocument).join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id).where(KnowledgeChunk.repository_id == repo_id, KnowledgeDocument.repository_id == repo_id, KnowledgeDocument.is_deleted.is_(False)))
    return [{"chunk_id": chunk.id, "id": f"doc:{doc.id[:16]}:chunk:{chunk.id}", "document_id": doc.id, "title": doc.title, "content": chunk.content, "heading": chunk.heading, "path": doc.path, "line_start": chunk.line_start, "line_end": chunk.line_end, "revision": doc.revision, "url": doc.url, "source": doc.source,
             "source_scope":"historical_analysis" if doc.external_id.startswith("weekly:") else "project_document",
             "source_notice":"历史分析摘要，可能已经过时；不能替代最新源码和已批准记忆。" if doc.external_id.startswith("weekly:") else ""} for chunk, doc in rows]


def corpus_hash(chunks):
    # Include source metadata because citations can change while text stays identical.
    return digest("\n".join(sorted(f"{x['chunk_id']}:{x['revision']}:{x['title']}:{x['url']}:{x['path']}:{x['line_start']}:{x['line_end']}:{x.get('source_scope','')}:{x.get('source_notice','')}" for x in chunks)))


def bm25(chunks: list[dict], query: str, limit=20):
    query_terms = set(tokenize(query))
    if not chunks or not query_terms:
        return []
    terms = [Counter(tokenize(x["title"] + " " + x["heading"] + " " + x["content"])) for x in chunks]
    lengths = [sum(x.values()) for x in terms]
    average = max(sum(lengths) / len(terms), 1)
    frequencies = Counter(t for counts in terms for t in counts)
    scored = []
    for chunk, counts, length in zip(chunks, terms, lengths):
        score = 0.0
        for term in query_terms:
            tf = counts[term]
            if not tf:
                continue
            df = frequencies[term]
            idf = math.log(1 + (len(chunks) - df + 0.5) / (df + 0.5))
            score += idf * tf * 2.5 / (tf + 1.5 * (0.25 + 0.75 * length / average))
        if score > 0:
            scored.append({**chunk, "score": score, "bm25_score": score, "methods": ["bm25"]})
    return sorted(scored, key=lambda x: (-x["score"], x["chunk_id"]))[:limit]


def reciprocal_rank_fusion(rankings: list[list[dict]], limit=20, k=60):
    merged = {}
    for ranking in rankings:
        seen = set()
        for rank, item in enumerate(ranking, 1):
            ident = item["chunk_id"]
            if ident in seen:
                continue
            seen.add(ident)
            previous = merged.setdefault(ident, {**item, "score": 0.0, "methods": []})
            previous["score"] += 1 / (k + rank)
            for key in ("bm25_score", "vector_score"):
                if key in item:
                    previous[key] = item[key]
            previous["methods"] = list(dict.fromkeys(previous["methods"] + item["methods"]))
    return sorted(merged.values(), key=lambda x: (-x["score"], x["chunk_id"]))[:limit]
