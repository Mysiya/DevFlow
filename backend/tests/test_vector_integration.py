"""Opt-in actual CPU model + Milvus tests. SQL fixture and collection are isolated."""
import asyncio
import os
from pathlib import Path
from uuid import uuid4
from dotenv import dotenv_values
import pytest
from app import retrieval
from app.config import Settings
from app.db import Repository,SessionLocal
from app.knowledge import digest,load_corpus,upsert_document
from app.vector import EmbeddingClient,MilvusStore,RetrievalError

pytestmark=pytest.mark.skipif(os.environ.get('DEVFLOW_VECTOR_INTEGRATION')!='1',reason='需要本机 Embedding 和 Milvus Lite；普通回归不连接真实服务。')


def test_actual_model_milvus_scope_invalidation_cache_and_rebuild(client,monkeypatch):
    prefix='devflow_test_'+uuid4().hex[:16]
    local=dotenv_values(Path(__file__).resolve().parents[1]/'.env')
    settings=Settings(_env_file=None,devflow_mode='demo',milvus_collection=prefix,
                      embedding_local=True,embedding_base_url=local['EMBEDDING_BASE_URL'],embedding_model=local['EMBEDDING_MODEL'],
                      embedding_api_key=local['EMBEDDING_API_KEY'],embedding_dimensions=512,milvus_deployment='lite',milvus_uri='http://127.0.0.1:19530')
    assert settings.embedding_local and settings.milvus_deployment=='lite' and settings.milvus_uri=='http://127.0.0.1:19530'
    data=[('background','后台执行','关闭页面只断开订阅，不取消后台任务。独立 Worker 继续分析。显式点击停止按钮才会取消。','退出网页后人工智能任务会终止吗'),
          ('memory','记忆审批','人工录入的约定先成为候选记忆。审核者批准后才能被检索。修改正文会撤销审批，必须再次批准。','随手记下的经验能直接用来回答吗'),
          ('code','源码版本','历史分析读取固定 Git SHA 的源码，更新新版本不会改变旧引用，缓存保留后旧提交仍可读取。','代码升级后以前引用还能访问吗'),
          ('price','用量费用','模型单价没有配置时费用未知。不能把缺失价格算成零元。输入输出 Token 由模型供应商返回。','没有收费标准时怎么算花费')]
    with SessionLocal() as db:
        record=Repository(full_name='demo/vector-validation',snapshot={'source':'demo','documents':[]});db.add(record);db.flush();repo=record.id
        for ident,title,content,_ in data:upsert_document(db,repo,{'id':ident+'.md','title':title,'path':ident+'.md','content':'# '+title+'\n'+content,'revision':digest(content)},settings)
        db.commit();generation,_=retrieval.begin_index(db,repo,settings)
    original=EmbeddingClient.embed;calls=[]
    async def measured(self,texts,expected_dimension=None):
        calls.append(len(texts));return await original(self,texts,expected_dimension)
    monkeypatch.setattr(EmbeddingClient,'embed',measured)
    store=MilvusStore(settings,512)
    try:
        asyncio.run(retrieval.build_index(repo,generation,settings))
        with SessionLocal() as db:
            assert retrieval.status_for(db,repo,settings)['vector_ready'];corpus=load_corpus(db,repo)
        assert calls==[4]
        for ident,_,_,query in data:
            answer=asyncio.run(retrieval.search(repo,query,settings,'hybrid',top_k=1))
            assert answer['results'][0]['path']==ident+'.md',{'query':query,'path':answer['results'][0]['path']}
            assert 'vector' in answer['results'][0]['methods'] and not answer['warnings']
        vector=asyncio.run(EmbeddingClient(settings).embed([data[0][3]]))[0]
        connection=store._client()
        try:connection.release_collection(collection_name=store.collection)
        finally:connection.close()
        # Server restart releases collections; searching must reload persisted vectors.
        assert len(asyncio.run(store.search(repo,generation,vector,20)))==4
        foreign={'chunk_id':digest('foreign scoped fixture')}
        asyncio.run(store.write('f'*32,generation,[foreign],[vector]))
        asyncio.run(store.write(repo,'e'*32,[{'chunk_id':digest('different generation fixture')}],[vector]))
        hits=asyncio.run(store.search(repo,generation,vector,20))
        assert len(hits)==4 and all(h['chunk_id'] in {c['chunk_id'] for c in corpus} for h in hits)
        with SessionLocal() as db:
            upsert_document(db,repo,{'id':'background.md','title':'后台执行','path':'background.md','content':'# 后台执行\n'+data[0][2]+'\n新增：API 重启也不会取消有有效租约的 Worker。'},settings)
            db.commit();assert retrieval.status_for(db,repo,settings)['index']['status']=='stale'
        with pytest.raises(RetrievalError,match='过期'):asyncio.run(retrieval.search(repo,data[0][3],settings,'hybrid'))
        with SessionLocal() as db:new_generation,_=retrieval.begin_index(db,repo,settings)
        before=len(calls);asyncio.run(retrieval.build_index(repo,new_generation,settings))
        assert len(calls)==before+1 and calls[-1]==1
        with SessionLocal() as db:assert retrieval.status_for(db,repo,settings)['vector_ready']
        assert asyncio.run(retrieval.search(repo,data[0][3],settings,'hybrid',top_k=1))['results'][0]['path']=='background.md'
    finally:
        connection=store._client()
        try:
            assert store.collection.startswith(prefix+'_')
            if connection.has_collection(store.collection):connection.drop_collection(store.collection)
        finally:connection.close()
