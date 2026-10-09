import importlib.util
import asyncio
import json
import sys
from pathlib import Path
import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.config import Settings

SCRIPTS=Path(__file__).resolve().parents[2]/'scripts'
sys.path.insert(0,str(SCRIPTS))
spec=importlib.util.spec_from_file_location('local_embedding_service',SCRIPTS/'local-embedding.py')
service=importlib.util.module_from_spec(spec);spec.loader.exec_module(service)
from vector_model import windows


@pytest.mark.parametrize('kwargs',[
    {'embedding_local':True,'embedding_base_url':'https://external.example/v1'},
    {'embedding_local':True,'embedding_base_url':'http://user:secret@127.0.0.1:8001/v1'},
    {'milvus_deployment':'lite','milvus_uri':'http://external.example:19530'},
])
def test_local_profiles_reject_external_or_credential_urls(kwargs):
    with pytest.raises(ValidationError):Settings(_env_file=None,**kwargs)


def test_local_input_windows_include_the_end_without_silent_tail_loss():
    text='a'*1000+'尾部关键约定'
    parts=windows(text)
    assert all(len(part)<=350 for part in parts) and parts[-1].endswith('尾部关键约定')
    assert windows('a'*350)==['a'*350]
    class Encoder:
        def embed(self,texts,**kw):
            self.texts=texts
            return iter([np.ones(512) for _ in texts])
    encoder=Encoder();vectors=service.embed(encoder,[text,'short'])
    assert len(vectors)==2 and all(abs(np.linalg.norm(v)-1)<1e-6 for v in vectors)
    assert encoder.texts[-2].endswith('尾部关键约定')


def test_embedding_api_requires_own_token_model_version_and_bounded_inputs():
    class Encoder:
        def embed(self,texts,**kw):return iter([np.ones(512) for _ in texts])
    metadata={'api_model':'fixture-model','fingerprint':'f'*64,'dimension':512}
    app=service.create_app(lambda:(metadata,Encoder()),lambda:'fixture-local-token-that-is-independent')
    headers={'Authorization':'Bearer fixture-local-token-that-is-independent'}
    with TestClient(app) as client:
        assert client.get('/health').json()['offline']
        assert client.get('/health',headers={'Origin':'http://external.example'}).status_code==403
        body={'model':'fixture-model','input':['资料只留在本机']}
        assert client.post('/v1/embeddings',json=body).status_code==401
        response=client.post('/v1/embeddings',json=body,headers=headers)
        assert response.status_code==200 and len(response.json()['data'][0]['embedding'])==512
        assert '资料只留在本机' not in json.dumps(response.json(),ensure_ascii=False)
        assert client.post('/v1/embeddings',json={**body,'model':'old-model'},headers=headers).status_code==400
        assert client.post('/v1/embeddings',json={**body,'dimensions':3},headers=headers).status_code==400
        assert client.post('/v1/embeddings',json={**body,'input':['x']*33},headers=headers).status_code==422
        assert client.post('/v1/embeddings',json={**body,'input':['x'*6001]},headers=headers).status_code==422
        assert client.post('/v1/embeddings',json={**body,'input':['  ']},headers=headers).status_code==422


def test_embedding_rejects_nonfinite_model_vectors_without_exposing_input():
    class Encoder:
        def embed(self,texts,**kw):return iter([np.full(512,np.nan) for _ in texts])
    app=service.create_app(lambda:({'api_model':'fixture-model'},Encoder()),lambda:'local-fixture-token')
    with TestClient(app) as client:
        response=client.post('/v1/embeddings',json={'model':'fixture-model','input':['private local text']},headers={'Authorization':'Bearer local-fixture-token'})
        assert response.status_code==503 and 'private local text' not in response.text


def test_connection_probe_is_read_only_and_reports_model_mismatch(monkeypatch):
    import httpx
    from app import retrieval
    settings=Settings(_env_file=None,embedding_local=True,embedding_base_url='http://127.0.0.1:8001/v1',embedding_model='expected',embedding_api_key='local-fixture',embedding_dimensions=512,milvus_deployment='lite')
    original=httpx.AsyncClient;calls=[]
    def response(request):
        calls.append(request)
        return httpx.Response(200,json={'model':'different','offline':True,'dimension':512})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(response),**kw))
    monkeypatch.setattr(retrieval,'probe_milvus',lambda *args:{'status':'ready','index_present':True,'message':'fixture'})
    result=asyncio.run(retrieval.service_status(settings,512))
    assert result['embedding']['status']=='mismatch' and result['milvus']['status']=='ready'
    assert len(calls)==1 and calls[0].method=='GET' and not calls[0].content and 'Authorization' not in calls[0].headers
    result=asyncio.run(retrieval.service_status(settings.model_copy(update={'embedding_local':False}),512))
    assert result['embedding']['status']=='not_probed' and len(calls)==1
