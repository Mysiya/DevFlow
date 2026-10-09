import asyncio
import json
from datetime import datetime, timezone
import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from app import main
from app.db import AgentRun, EvaluationBatch, ModelCall, RunAttempt, SessionLocal
from app.llm import ModelClient
from app.telemetry import attempt_context, begin_call, end_attempt, end_call, estimate, provider_usage, run_metrics, start_attempt
from app.config import Settings
from app.evaluation import evaluate, retrieval_scores


def repo(client):return client.get('/api/repositories').json()[0]['id']


def completed(client):
    ident=repo(client)
    assert client.post('/api/chat/stream',json={'repository_id':ident,'task':'pr','target':18,'message':'检查 PR #18'}).status_code==200
    return client.get(f'/api/repositories/{ident}/runs').json()[0]['id']


def test_new_demo_run_records_elapsed_without_fake_model_usage(client):
    ident=repo(client);run=completed(client)
    metrics=client.get(f'/api/repositories/{ident}/metrics').json()['runs'][0]
    assert metrics['run_id']==run and metrics['measured'] and metrics['complete']
    assert metrics['elapsed_ms']>=0 and metrics['model_calls']==0 and metrics['estimated_cost']=='0'


def test_legacy_and_partially_recorded_resumes_do_not_claim_zero_cost(client):
    ident=repo(client);run=completed(client)
    with SessionLocal() as db:
        for attempt in db.scalars(select(RunAttempt).where(RunAttempt.run_id==run)):db.delete(attempt)
        db.commit()
        assert run_metrics(db,db.get(AgentRun,run))['estimated_cost'] is None
    attempt=start_attempt(run);end_attempt(attempt,'completed',12)
    with SessionLocal() as db:
        result=run_metrics(db,db.get(AgentRun,run))
        assert result['historical_prefix'] and result['estimated_cost'] is None and not result['usage_complete']


@pytest.mark.parametrize('raw',[None,{}, {'prompt_tokens':True,'completion_tokens':-1}, {'prompt_tokens':'12','completion_tokens':2}])
def test_missing_or_invalid_provider_usage_is_unknown(raw):
    assert estimate(provider_usage(raw),{'input':2,'output':4,'cached_input':1}) is None


def test_cached_usage_estimate_requires_correct_prices_and_never_invents_usage():
    usage=provider_usage({'prompt_tokens':1000,'completion_tokens':100,'prompt_cache_hit_tokens':600})
    assert estimate(usage,{'input':2,'output':4,'cached_input':.5})=='0.00150000'
    assert estimate(usage,{'input':2,'output':4}) is None
    assert estimate(provider_usage({'prompt_tokens':2,'completion_tokens':1,'prompt_cache_hit_tokens':3}),{'input':2,'output':4,'cached_input':.5}) is None


def test_price_configuration_keeps_empty_unknown_and_rejects_nonfinite_values():
    assert Settings(llm_input_price_per_million='').llm_input_price_per_million is None
    from pydantic import ValidationError
    for value in (float('inf'),float('nan'),-1):
        with pytest.raises(ValidationError):Settings(llm_input_price_per_million=value)


def test_model_request_records_usage_and_failure_without_prompts_or_credentials(client,monkeypatch):
    run=completed(client);attempt=start_attempt(run);token=attempt_context.set(attempt)
    original=httpx.AsyncClient
    settings=Settings(llm_model='fixture-model',llm_api_key='fixture-private-token',llm_input_price_per_million=2,llm_output_price_per_million=4)
    def success(request):return httpx.Response(200,json={'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}],'usage':{'prompt_tokens':100,'completion_tokens':20}})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(success),**kw))
    try:
        assert asyncio.run(ModelClient(settings).chat([{'role':'user','content':'private fixture prompt'}]))['content']=='{}'
        def timeout(request):raise httpx.ReadTimeout('unknown response')
        monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(timeout),**kw))
        with pytest.raises(RuntimeError):asyncio.run(ModelClient(settings).chat([]))
    finally:attempt_context.reset(token);end_attempt(attempt,'failed',50)
    with SessionLocal() as db:
        calls=list(db.scalars(select(ModelCall).where(ModelCall.attempt_id==attempt)))
        assert {c.status for c in calls}=={'completed','failed'}
        assert any(c.estimated_cost=='0.00028000' for c in calls)
        stored=json.dumps([{'model':c.model,'usage':c.usage,'prices':c.prices} for c in calls])
        assert 'private fixture prompt' not in stored and 'fixture-private-token' not in stored
        result=run_metrics(db,db.get(AgentRun,run));assert result['unknown_usage_calls']==1 and result['estimated_cost'] is None


def test_fixed_benchmark_is_offline_reproducible_and_has_useful_baseline(client):
    one=asyncio.run(evaluate(main.settings));two=asyncio.run(evaluate(main.settings))
    assert one[:2]==two[:2]
    result=one[2]
    assert result['total']==60 and result['passed']==60 and one[0]=='core-v4'
    assert result['code_retrieval']['strategies']['code-bm25-v1']['passed']==10
    assert result['retrieval']['bm25']['mean']['recall']>result['retrieval']['title_keyword']['mean']['recall']
    assert result['analysis']==two[2]['analysis'] and result['runner_revision']==two[2]['runner_revision']
    assert not retrieval_scores([{'id':'wrong'}],['wanted'])['passed']
    assert not retrieval_scores([{'id':'wrong'}],[])['passed']


def test_parallel_model_requests_keep_usage_in_their_own_run(client,monkeypatch):
    runs=[completed(client),completed(client)]
    attempts=[start_attempt(run) for run in runs]
    original=httpx.AsyncClient
    async def response(request):
        count=int(json.loads(request.content)['messages'][0]['content'])
        await asyncio.sleep(.01)
        return httpx.Response(200,json={'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}],'usage':{'prompt_tokens':count,'completion_tokens':3}})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(response),**kw))
    async def task(index):
        token=attempt_context.set(attempts[index])
        try:await ModelClient(Settings(llm_model='fixture',llm_api_key='fixture-key')).chat([{'role':'user','content':str(100+index)}])
        finally:attempt_context.reset(token);end_attempt(attempts[index],'completed',20)
    async def both():await asyncio.gather(task(0),task(1))
    asyncio.run(both())
    with SessionLocal() as db:
        for index,run in enumerate(runs):
            metrics=run_metrics(db,db.get(AgentRun,run))
            assert metrics['model_calls']==1 and metrics['known_input_tokens']==100+index


def test_evaluation_records_export_and_actual_ragas_metrics(client):
    ident=repo(client);root=f'/api/repositories/{ident}/evaluations'
    response=client.post(root);assert response.status_code==201
    batch=response.json();assert batch['results']['passed']==60
    exported=client.get(root+'/'+batch['id']+'/export').json()
    assert len(exported['samples'])==6 and exported['fingerprint']==batch['fingerprint']
    from app.delivery import evaluator_python
    if evaluator_python():
        result=client.post(root+'/'+batch['id']+'/ragas')
        assert result.status_code==200,result.text
        scores=result.json()['results']['ragas']
        assert scores['provider']=='ragas-nonllm' and len(scores['rows'])==6
        assert all(0<=r['context_recall']<=1 for r in scores['rows'])
        assert client.post(root+'/'+batch['id']+'/ragas').json()['results']['ragas']==scores


def test_weekly_report_date_bounds_idempotence_and_reviewed_knowledge_ingestion(client):
    ident=repo(client);run=completed(client);root=f'/api/repositories/{ident}/reports'
    with SessionLocal() as db:db.get(AgentRun,run).created_at=datetime(2026,10,4,16,0,tzinfo=timezone.utc);db.commit()
    assert client.post(root,json={'week_start':'2026-10-06'}).status_code==422
    report=client.post(root,json={'week_start':'2026-10-05'}).json()
    assert report['source_run_ids']==[run] and not report['document_id']
    assert '不是 GitHub 全量活动周报' in report['body']
    assert '2026-10-05T00:00:00+08:00' in report['body'] and '证据版本：' in report['body'] and '可能已经过时' in report['body']
    assert client.post(root,json={'week_start':'2026-10-05'}).json()['id']==report['id']
    assert client.post(root,json={'week_start':'2026-10-12'}).json()['source_run_ids']==[]
    saved=client.post(root+'/'+report['id']+'/publish-knowledge').json()
    assert saved['document_id'] and client.post(root+'/'+report['id']+'/publish-knowledge').json()['document_id']==saved['document_id']
    documents=client.get(f'/api/repositories/{ident}/knowledge/documents').json()
    assert any(d['id']==saved['document_id'] and run in d['content'] for d in documents)
    hits=client.post(f'/api/repositories/{ident}/knowledge/search',json={'query':'已保存分析周报'}).json()['results']
    assert any(h['document_id']==saved['document_id'] and h['source_scope']=='historical_analysis' and '可能已经过时' in h['source_notice'] for h in hits)


def test_delivery_scope_and_review_roles(client,monkeypatch):
    from tests.test_governance import setup_auth,new_member
    ident,_=setup_auth(client,monkeypatch)
    batch=client.post(f'/api/repositories/{ident}/evaluations').json()
    report=client.post(f'/api/repositories/{ident}/reports',json={'week_start':'2026-10-05'}).json()
    new_member(client,ident,'viewer')
    client.post('/api/auth/login',json={'username':'member','password':'testing-member-1234'})
    root=f'/api/repositories/{ident}'
    assert client.get(root+'/metrics').status_code==200
    assert client.post(root+'/evaluations').status_code==403
    assert client.post(root+'/reports').status_code==403
    assert client.post(root+'/reports/'+report['id']+'/publish-knowledge').status_code==403
    assert client.get('/api/repositories/'+'f'*32+'/evaluations/'+batch['id']+'/export').status_code==404
