import asyncio
from time import perf_counter
import httpx
from sqlalchemy import select

from .db import EmbeddingCache, KnowledgeDocument, KnowledgeIndex, SessionLocal, new_id, utcnow
from .knowledge import bm25, corpus_hash, digest, load_corpus, reciprocal_rank_fusion
from .vector import EmbeddingClient, MilvusStore, RerankClient, RetrievalError, embedding_key, index_key, vector_configured, milvus_client


def probe_milvus(settings,dimension):
    client=None
    try:
        client=milvus_client(settings,timeout=3)
        client.list_collections(timeout=3)
        present=client.has_collection(MilvusStore(settings,dimension).collection,timeout=3) if dimension else None
        return {"status":"ready","index_present":present,"message":"本机 Milvus Lite 已连接。" if settings.milvus_deployment=="lite" else "Milvus 已连接。"}
    except Exception:return {"status":"unreachable","index_present":None,"message":"Milvus 无法连接，请启动服务并检查配置。"}
    finally:
        if client:client.close()


async def service_status(settings,dimension=None):
    if not vector_configured(settings):return {"embedding":{"status":"not_configured","message":"尚未配置 Embedding。"},"milvus":{"status":"not_configured","message":"请先配置向量连接。"}}
    started=perf_counter()
    async def embedding():
        if not settings.embedding_local:return {"status":"not_probed","message":"远程 Embedding 不发送计费探测，需用实际索引请求验证。"}
        try:
            async with httpx.AsyncClient(timeout=3,trust_env=False,follow_redirects=False) as client:
                response=await client.get(settings.embedding_base_url.rstrip('/').removesuffix('/v1')+'/health');response.raise_for_status()
            data=response.json()
            if data.get('model')!=settings.embedding_model or data.get('offline') is not True or (settings.embedding_dimensions and data.get('dimension')!=settings.embedding_dimensions):
                return {"status":"mismatch","message":"本地模型版本、维度或离线状态不符，请检查配置并重建索引。"}
            return {"status":"ready","message":"本机中文模型已加载，离线 CPU 计算。"}
        except Exception:return {"status":"unreachable","message":"本地 Embedding 无法连接，请启动服务。"}
    embedding_result,milvus_result=await asyncio.gather(embedding(),asyncio.to_thread(probe_milvus,settings,dimension))
    return {"embedding":embedding_result,"milvus":milvus_result,"elapsed_ms":int((perf_counter()-started)*1000)}


def status_for(db, repo_id, settings):
    chunks = load_corpus(db, repo_id)
    docs = list(db.scalars(select(KnowledgeDocument).where(KnowledgeDocument.repository_id == repo_id, KnowledgeDocument.is_deleted.is_(False))))
    state = db.get(KnowledgeIndex, repo_id)
    fresh = bool(state and state.status == "ready" and state.corpus_hash == corpus_hash(chunks) and state.index_key == index_key(settings))
    return {"document_count": len(docs), "chunk_count": len(chunks), "default_mode": "hybrid" if settings.retrieval_backend == "milvus" else "keyword", "vector_configured": vector_configured(settings), "vector_ready": fresh, "embedding_model": settings.embedding_model or None, "embedding_execution":"local-cpu" if settings.embedding_local else "remote", "vector_store":"milvus-lite" if settings.milvus_deployment=="lite" else "milvus", "rerank_enabled": settings.rerank_enabled, "index": {"status": "stale" if state and state.status == "ready" and not fresh else state.status if state else "idle", "indexed_chunks": state.indexed_chunks if state else 0, "dimension": state.dimension if state else None, "message": "文档或连接配置已变化，请重新建立索引。" if state and state.status == "ready" and not fresh else state.message if state else "尚未建立向量索引", "updated_at": state.updated_at.isoformat() if state else None}}


async def search(repo_id, query, settings, mode="keyword", top_k=5, use_rerank=True, pinned_chunks=None):
    warnings = []
    with SessionLocal() as db:
        current = load_corpus(db, repo_id)
        chunks = current if pinned_chunks is None else pinned_chunks
        if pinned_chunks is not None and corpus_hash(current) != corpus_hash(chunks):
            warnings.append("文档库已更新，本次继续使用原运行保存的文档版本，以 BM25 检索该快照。")
            mode, use_rerank = "keyword", False
        state = db.get(KnowledgeIndex, repo_id)
        if mode == "hybrid" and not status_for(db, repo_id, settings)["vector_ready"]:
            raise RetrievalError("当前仓库的向量索引尚未就绪或已过期，请先在项目知识页建立索引；BM25 仍可单独使用。")
        generation, dimension = (state.generation, state.dimension) if state else (None, None)
    lexical = bm25(chunks, query, max(20, top_k))
    if mode == "hybrid":
        vectors = await EmbeddingClient(settings).embed([query], expected_dimension=dimension)
        hits = await MilvusStore(settings, dimension).search(repo_id, generation, vectors[0], max(20, top_k))
        allowed = {x["chunk_id"]: x for x in chunks}
        vector_hits = [{**allowed[x["chunk_id"]], "score": x["vector_score"], "vector_score": x["vector_score"], "methods": ["vector"]} for x in hits if x["chunk_id"] in allowed]
        if len(vector_hits) != len(hits):
            warnings.append("向量库返回了不在当前文档集中的片段，已排除这些结果。")
        candidates = reciprocal_rank_fusion([lexical, vector_hits], max(20, top_k))
    else:
        candidates = lexical
    # Reject answers from a corpus replaced while remote query calls were in flight.
    with SessionLocal() as db:
        if pinned_chunks is None and corpus_hash(load_corpus(db, repo_id)) != corpus_hash(chunks):
            raise RetrievalError("检索期间文档发生变化，请重新查询。")
    if settings.rerank_enabled and use_rerank:
        candidates = await RerankClient(settings).rerank(query, candidates, top_k)
    with SessionLocal() as db:
        if pinned_chunks is None and corpus_hash(load_corpus(db, repo_id)) != corpus_hash(chunks):
            raise RetrievalError("检索期间文档发生变化，请重新查询。")
    results = candidates[:top_k]
    for item in results:
        item["citation"] = f"{item['path']} L{item['line_start']}-L{item['line_end']}"
        item["retrieval_mode"] = mode
    return {"query": query, "mode": mode, "reranked": settings.rerank_enabled and use_rerank, "results": results, "warnings": warnings, "candidate_count": len(candidates)}


def begin_index(db, repo_id, settings):
    if not vector_configured(settings):
        raise RetrievalError("请先配置 Embedding 模型、密钥和 Milvus 连接，再建立向量索引。")
    chunks = load_corpus(db, repo_id)
    if not chunks:
        raise RetrievalError("当前仓库没有可索引的文档片段。")
    state = db.get(KnowledgeIndex, repo_id)
    if state and state.status == "indexing":
        return state.generation, False
    if not state:
        state = KnowledgeIndex(repository_id=repo_id)
        db.add(state)
    state.status, state.generation = "indexing", new_id()
    state.message, state.updated_at = "正在生成向量并写入 Milvus", utcnow()
    db.commit()
    return state.generation, True


async def build_index(repo_id, generation, settings):
    key = embedding_key(settings)
    try:
        with SessionLocal() as db:
            chunks = load_corpus(db, repo_id)
            initial_hash = corpus_hash(chunks)
            cache = {x.chunk_id: x.vector for x in db.scalars(select(EmbeddingCache).where(EmbeddingCache.model_key == key, EmbeddingCache.chunk_id.in_([x["chunk_id"] for x in chunks])))}
        missing = [x for x in chunks if x["chunk_id"] not in cache]
        dimension = len(next(iter(cache.values()))) if cache else settings.embedding_dimensions
        for offset in range(0, len(missing), 32):
            batch = missing[offset:offset + 32]
            embedded = await EmbeddingClient(settings).embed([x["title"] + "\n" + x["heading"] + "\n" + x["content"] for x in batch], expected_dimension=dimension)
            if embedded:
                dimension = len(embedded[0])
            with SessionLocal() as db:
                for chunk, vector in zip(batch, embedded):
                    ident = digest(chunk["chunk_id"] + key)
                    if not db.get(EmbeddingCache, ident):
                        db.add(EmbeddingCache(id=ident, chunk_id=chunk["chunk_id"], model_key=key, vector=vector))
                    cache[chunk["chunk_id"]] = vector
                db.commit()
        vectors = [cache[x["chunk_id"]] for x in chunks]
        if not dimension:
            raise RetrievalError("没有可用的向量。")
        await MilvusStore(settings, dimension).write(repo_id, generation, chunks, vectors)
        with SessionLocal() as db:
            if corpus_hash(load_corpus(db, repo_id)) != initial_hash:
                raise RetrievalError("索引建立期间文档发生变化，当前向量版本未激活，请重建。")
            state = db.get(KnowledgeIndex, repo_id)
            if not state or state.generation != generation:
                return
            state.status, state.corpus_hash, state.model_key, state.index_key = "ready", initial_hash, key, index_key(settings)
            state.dimension, state.indexed_chunks = dimension, len(chunks)
            state.message, state.updated_at = "向量索引已就绪", utcnow()
            db.commit()
    except Exception as exc:
        with SessionLocal() as db:
            state = db.get(KnowledgeIndex, repo_id)
            if state and state.generation == generation:
                state.status = "failed"
                state.message = str(exc) if isinstance(exc, RetrievalError) else "索引建立失败，请检查模型与 Milvus 配置。"
                state.updated_at = utcnow()
                db.commit()
