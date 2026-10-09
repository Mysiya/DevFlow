"""Explicit remote Embedding/Rerank adapters and versioned Milvus generations."""
import asyncio
import json
import math

import httpx

from .knowledge import digest


class RetrievalError(RuntimeError):
    pass


def embedding_key(settings):
    return digest(f"{settings.embedding_local}:{settings.embedding_base_url.rstrip('/')}:{settings.embedding_model}:{settings.embedding_dimensions}")


def index_key(settings):
    return digest(f"{settings.milvus_deployment}:{settings.milvus_uri}:{settings.milvus_collection}:{embedding_key(settings)}")


def vector_configured(settings):
    return bool(settings.embedding_model and settings.embedding_api_key.get_secret_value() and settings.milvus_uri)


def milvus_client(settings, timeout=10):
    try:
        from pymilvus import MilvusClient
    except ImportError as exc:
        raise RetrievalError("缺少 pymilvus，请安装 backend/requirements.lock.txt。") from exc
    options = {}
    if settings.milvus_deployment == "lite":
        # PyMilvus pools channels. Lite uses gRPC's default server ping policy;
        # the SDK's 10-second idle pings otherwise trigger too_many_pings.
        options["grpc_options"] = {"grpc.keepalive_time_ms": 2147483647, "grpc.keepalive_permit_without_calls": False}
    return MilvusClient(uri=settings.milvus_uri, token=settings.milvus_token.get_secret_value(), timeout=timeout, **options)


class EmbeddingClient:
    def __init__(self, settings):
        self.settings = settings

    async def embed(self, texts: list[str], expected_dimension: int | None = None):
        if not vector_configured(self.settings):
            raise RetrievalError("向量检索需要配置 EMBEDDING_MODEL、EMBEDDING_API_KEY 和 MILVUS_URI。")
        if not texts:
            return []
        payload = {"model": self.settings.embedding_model, "input": texts}
        if self.settings.embedding_dimensions:
            payload["dimensions"] = self.settings.embedding_dimensions
        try:
            async with httpx.AsyncClient(timeout=60,trust_env=not self.settings.embedding_local,follow_redirects=False) as client:
                response = await client.post(self.settings.embedding_base_url.rstrip("/") + "/embeddings", headers={"Authorization": f"Bearer {self.settings.embedding_api_key.get_secret_value()}"}, json=payload)
                response.raise_for_status()
            data = response.json().get("data", [])
            if len(data) != len(texts) or {x.get("index") for x in data} != set(range(len(texts))):
                raise ValueError("invalid embedding indexes")
            vectors = [x["embedding"] for x in sorted(data, key=lambda x: x["index"])]
            dimension = expected_dimension or self.settings.embedding_dimensions or len(vectors[0])
            if dimension < 2 or dimension > 65536:
                raise ValueError("invalid embedding dimension")
            for vector in vectors:
                if len(vector) != dimension or any(not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(x) for x in vector) or not any(vector):
                    raise ValueError("invalid embedding vector")
            return vectors
        except httpx.HTTPStatusError as exc:
            raise RetrievalError(f"Embedding 请求失败（HTTP {exc.response.status_code}），请核对模型、密钥与接口。") from exc
        except httpx.HTTPError as exc:
            raise RetrievalError("Embedding 服务连接失败或超时。") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise RetrievalError("Embedding 返回的数据数量、维度或数值无效，请检查配置并重新建立索引。") from exc


class MilvusStore:
    def __init__(self, settings, dimension):
        self.settings, self.dimension = settings, dimension
        self.collection = f"{settings.milvus_collection}_{embedding_key(settings)[:12]}_d{dimension}"

    def _client(self):
        return milvus_client(self.settings)

    def _write(self, repository_id, generation, chunks, vectors):
        client = None
        try:
            from pymilvus import DataType
            client = self._client()
            if not client.has_collection(self.collection):
                schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
                schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=64)
                schema.add_field("repository_id", DataType.VARCHAR, max_length=32)
                schema.add_field("generation", DataType.VARCHAR, max_length=32)
                schema.add_field("chunk_id", DataType.VARCHAR, max_length=64)
                schema.add_field("vector", DataType.FLOAT_VECTOR, dim=self.dimension)
                indexes = client.prepare_index_params()
                indexes.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
                client.create_collection(collection_name=self.collection, schema=schema, index_params=indexes, consistency_level="Strong")
            for offset in range(0, len(chunks), 128):
                rows = [{"id": digest(f"{generation}:{chunk['chunk_id']}"), "repository_id": repository_id, "generation": generation, "chunk_id": chunk["chunk_id"], "vector": vector} for chunk, vector in zip(chunks[offset:offset + 128], vectors[offset:offset + 128])]
                client.upsert(collection_name=self.collection, data=rows)
            client.flush(collection_name=self.collection)
            client.load_collection(collection_name=self.collection)
        except RetrievalError:
            raise
        except Exception as exc:
            raise RetrievalError("Milvus 索引写入失败，请检查服务是否启动、SDK 版本和连接配置。") from exc
        finally:
            if client:
                client.close()

    async def write(self, repository_id, generation, chunks, vectors):
        if len(chunks) != len(vectors) or any(len(x) != self.dimension for x in vectors):
            raise RetrievalError("索引数据与向量数量或维度不一致。")
        await asyncio.to_thread(self._write, repository_id, generation, chunks, vectors)

    def _search(self, repository_id, generation, vector, limit):
        client = None
        try:
            client = self._client()
            if not client.has_collection(self.collection):
                raise RetrievalError("Milvus 集合不存在，请重新建立索引。")
            # Persisted data survives a server restart, but load state does not.
            client.load_collection(collection_name=self.collection)
            scope = f"repository_id == {json.dumps(repository_id)} and generation == {json.dumps(generation)}"
            rows = client.search(collection_name=self.collection, data=[vector], filter=scope, limit=limit, output_fields=["chunk_id"], search_params={"metric_type": "COSINE", "params": {}}, consistency_level="Strong")
            return [{"chunk_id": row["entity"]["chunk_id"], "vector_score": float(row["distance"])} for row in rows[0]] if rows else []
        except RetrievalError:
            raise
        except Exception as exc:
            raise RetrievalError("Milvus 检索失败，请检查服务和已建立的索引。") from exc
        finally:
            if client:
                client.close()

    async def search(self, repository_id, generation, vector, limit):
        return await asyncio.to_thread(self._search, repository_id, generation, vector, limit)


class RerankClient:
    def __init__(self, settings):
        self.settings = settings

    async def rerank(self, query, candidates, limit):
        if not self.settings.rerank_api_key.get_secret_value() or not self.settings.rerank_model:
            raise RetrievalError("开启重排后需要配置 RERANK_API_KEY 和 RERANK_MODEL。")
        if not candidates:
            return []
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(self.settings.rerank_base_url.rstrip("/") + "/rerank", headers={"Authorization": f"Bearer {self.settings.rerank_api_key.get_secret_value()}"}, json={"model": self.settings.rerank_model, "query": query, "documents": [x["title"] + "\n" + x["content"] for x in candidates], "top_n": min(limit, len(candidates))})
                response.raise_for_status()
            rows = response.json()["results"]
            seen, ranked = set(), []
            for row in rows:
                idx, score = row["index"], row["relevance_score"]
                if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0 or idx >= len(candidates) or idx in seen or not isinstance(score, (int, float)) or not math.isfinite(score):
                    raise ValueError("invalid rerank results")
                seen.add(idx)
                ranked.append({**candidates[idx], "rerank_score": score, "methods": candidates[idx]["methods"] + ["rerank"]})
            if len(ranked) != min(limit, len(candidates)):
                raise ValueError("incomplete rerank results")
            return sorted(ranked, key=lambda x: -x["rerank_score"])
        except httpx.HTTPStatusError as exc:
            raise RetrievalError(f"重排服务请求失败（HTTP {exc.response.status_code}）。") from exc
        except httpx.HTTPError as exc:
            raise RetrievalError("重排服务连接失败或超时。") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise RetrievalError("重排服务返回了无效的候选编号或得分。") from exc
