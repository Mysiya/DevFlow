import asyncio
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app import main, prompt_experiments
from app.db import AgentRun, AnswerReview, AuditRecord, Conversation, PromptExperiment, Repository, RunJob, SessionLocal, new_id
from app.prompts import fingerprint
from app.run_queue import claim_job
from app.worker import execute_claim


def live_repo(client, monkeypatch):
    repo = client.get('/api/repositories').json()[0]['id']
    imported = client.post(f'/api/repositories/{repo}/knowledge/documents', json={'path':'docs/pair.md','title':'后台任务租约','content':'# 后台任务租约\n后台任务租约过期后旧 Worker 停止。'})
    assert imported.status_code == 200
    with SessionLocal() as db:
        record = db.get(Repository, repo)
        record.snapshot = {**record.snapshot, 'source':'github'}
        db.commit()
    settings = main.settings.model_copy(update={'devflow_mode':'live','llm_model':'paired-fixture','llm_api_key':SecretStr('fixture-paired-secret')})
    monkeypatch.setattr(main, 'settings', settings)
    return repo, f'/api/repositories/{repo}/prompt-experiments'


def body():
    return {'question':'后台任务租约过期后如何处理？','task':'knowledge','request_id':new_id()}


def test_atomic_frozen_sources_idempotent_retry_and_conflicting_reuse(client, monkeypatch):
    repo, root = live_repo(client, monkeypatch)
    original = prompt_experiments.WorkspaceManager.ref
    calls = []
    def once(self, ident):
        calls.append(ident)
        return original(self, ident)
    monkeypatch.setattr(prompt_experiments.WorkspaceManager, 'ref', once)
    data = body()
    created = client.post(root, json=data)
    assert created.status_code == 202, created.text
    record = created.json()
    assert record['active'] and not record['answers_available']
    assert [record[k]['status'] for k in ('left','right')] == ['queued','queued']
    assert client.post(root, json=data).json()['id'] == record['id']
    assert calls == [repo]
    for field, value in (('question','另一个问题'), ('task','code')):
        assert client.post(root,json={**data,field:value}).status_code == 409
    with SessionLocal() as db:
        jobs = [db.get(RunJob,record[k]['id']) for k in ('left','right')]
        inputs = [copy.deepcopy(j.payload['input']) for j in jobs]
        assert [i.pop('analysis_binding')['prompt_id'] for i in inputs] == ['baseline-v1','evidence-first-v2']
        assert inputs[0] == inputs[1] and fingerprint(inputs[0]) == record['frozen_hash']
        assert inputs[0]['history'] == [] and inputs[0]['target'] is None
        assert len(list(db.scalars(select(AgentRun)))) == 2
        assert len(list(db.scalars(select(PromptExperiment)))) == 1
        assert len(list(db.scalars(select(AuditRecord).where(AuditRecord.action=='prompt_experiment.submit')))) == 1
        assert not list(db.scalars(select(AnswerReview)))


def test_capacity_on_second_job_rolls_back_the_entire_pair(client, monkeypatch):
    repo, root = live_repo(client,monkeypatch)
    with SessionLocal() as db:
        conv = Conversation(repository_id=repo,title='queue fixtures')
        db.add(conv);db.flush()
        for _ in range(99):
            run = AgentRun(repository_id=repo,conversation_id=conv.id,question='capacity fixture',task='knowledge',mode='live',status='queued')
            db.add(run);db.flush();db.add(RunJob(run_id=run.id,payload={}))
        db.commit()
    response = client.post(root,json=body())
    assert response.status_code == 409 and '队列' in response.json()['detail']
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RunJob)) == 99
        assert db.scalar(select(func.count()).select_from(AgentRun)) == 99
        assert db.scalar(select(func.count()).select_from(Conversation)) == 1
        assert not list(db.scalars(select(PromptExperiment)))
        assert not list(db.scalars(select(AuditRecord).where(AuditRecord.action=='prompt_experiment.submit')))


@pytest.mark.parametrize('same',[True,False])
def test_concurrent_retries_create_only_one_pair(client,monkeypatch,same):
    _,root = live_repo(client,monkeypatch)
    barrier = Barrier(2)
    original = prompt_experiments.existing
    def synchronized(*args):
        value=original(*args)
        if value is None:barrier.wait(timeout=10)
        return value
    monkeypatch.setattr(prompt_experiments,'existing',synchronized)
    data=body();other={**data,'question':data['question'] if same else '不同的问题'}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(client.post,root,json=value) for value in (data,other)]
        responses=[f.result(timeout=15) for f in futures]
    assert sorted(r.status_code for r in responses)==([202,202] if same else [202,409])
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RunJob)) == 2
        assert db.scalar(select(func.count()).select_from(AgentRun)) == 2
        assert db.scalar(select(func.count()).select_from(PromptExperiment)) == 1


@pytest.mark.parametrize('fail',[False,True])
def test_real_execution_path_records_identical_input_and_never_retries_failed_model(client,monkeypatch,fail):
    repo,root=live_repo(client,monkeypatch)
    captured=[]
    def handler(request):
        captured.append(json.loads(request.content))
        if fail and len(captured)==2:return httpx.Response(503,json={'error':'fixture failure'})
        inputs=json.loads(captured[-1]['messages'][1]['content'])
        answer={'title':'后台租约','summary':'来源要求旧 Worker 在租约失效时停止。','recommendation':'人工核验','findings':[{'severity':'info','title':'来源范围','detail':'只说明该来源约定。','evidence_ids':list(inputs['evidence'])[:1]}],'next_steps':[],'gaps':[]}
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(answer,ensure_ascii=False)}}],'usage':{'prompt_tokens':100,'completion_tokens':20}})
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    data=body();record=client.post(root,json=data).json()
    for _ in range(2):asyncio.run(execute_claim(claim_job(main.settings,'fixture-worker'),main.settings))
    detail=client.get(root+'/'+record['id']).json()
    assert not detail['active'] and detail['answers_available'] is not fail
    assert captured[0]['messages'][1]==captured[1]['messages'][1] and captured[0]['messages'][0]!=captured[1]['messages'][0]
    assert client.post(root,json=data).json()['id']==record['id'] and len(captured)==2
    assert claim_job(main.settings,'fixture-worker') is None
    if fail:
        assert detail['right']['status']=='failed' and detail['right']['message']
    else:
        pair=client.get(f'/api/repositories/{repo}/answer-comparisons?left={record["left"]["id"]}&right={record["right"]["id"]}').json()
        assert pair['controlled_pair'] and pair['left']['metrics']['model_calls']==pair['right']['metrics']['model_calls']==1


def test_stop_two_queued_jobs_is_idempotent_and_keeps_pair(client,monkeypatch):
    _,root=live_repo(client,monkeypatch)
    record=client.post(root,json=body()).json()
    stopped=client.post(root+'/'+record['id']+'/stop').json()
    assert not stopped['active'] and not stopped['answers_available']
    assert [stopped[k]['status'] for k in ('left','right')]==['cancelled','cancelled']
    assert client.post(root+'/'+record['id']+'/stop').json()['left']['status']=='cancelled'
    assert claim_job(main.settings,'fixture-worker') is None
    assert client.get(root).json()['records'][0]['id']==record['id']
    with SessionLocal() as db:
        assert all(len(db.get(AgentRun,record[k]['id']).events)==2 for k in ('left','right'))


@pytest.mark.parametrize('mutation',[
    {'question':'  '},{'task':'workflow'},{'request_id':'invalid'},{'prompt_id':'arbitrary'},{'question':'fixture-paired-secret'}])
def test_invalid_or_secret_input_does_not_create_jobs(client,monkeypatch,mutation):
    _,root=live_repo(client,monkeypatch)
    assert client.post(root,json={**body(),**mutation}).status_code==422
    with SessionLocal() as db:assert not list(db.scalars(select(RunJob)))


def test_demo_missing_workspace_and_changed_worker_parameters_stop_before_model(client,monkeypatch):
    repo=client.get('/api/repositories').json()[0]['id'];root=f'/api/repositories/{repo}/prompt-experiments'
    assert client.post(root,json=body()).status_code==409
    assert not client.get(root).json()['enabled']
    _,root=live_repo(client,monkeypatch)
    assert client.post(root,json={**body(),'task':'code'}).status_code==409
    record=client.post(root,json=body()).json()
    changed=main.settings.model_copy(update={'llm_max_tokens':2048})
    for _ in range(2):asyncio.run(execute_claim(claim_job(changed,'fixture-worker'),changed))
    detail=client.get(root+'/'+record['id']).json()
    assert all(detail[k]['status']=='failed' and '参数' in detail[k]['message'] for k in ('left','right'))


def test_roles_origin_actor_idempotency_and_mcp_do_not_expand_access(client,monkeypatch):
    from tests.test_governance import setup_auth,new_member
    repo,root=live_repo(client,monkeypatch)
    data=body();record=client.post(root,json=data).json()
    setup_auth(client,monkeypatch)
    with SessionLocal() as db:
        other=Repository(full_name='fixture/other',snapshot={'source':'github','documents':[]});db.add(other);db.commit();other_id=other.id
    assert client.get(f'/api/repositories/{other_id}/prompt-experiments/{record["id"]}').status_code==404
    assert client.post(root,json=body(),headers={'Origin':'https://untrusted.example'}).status_code==403
    member=new_member(client,repo,'viewer')
    client.post('/api/auth/login',json={'username':'member','password':'testing-member-1234'})
    assert client.get(root).status_code==200 and client.get(root+'/'+record['id']).status_code==200
    assert client.post(root,json=body()).status_code==403
    assert client.post(root+'/'+record['id']+'/stop').status_code==403
    from app.db import RepositoryMember
    with SessionLocal() as db:db.get(RepositoryMember,(repo,member['id'])).role='editor';db.commit()
    assert client.post(root,json=data).status_code==409
    assert client.post(root,json=body()).status_code==202
    token='paired-test-token-at-least-32-characters'
    monkeypatch.setattr(main,'settings',main.settings.model_copy(update={'mcp_access_token':SecretStr(token),'mcp_repository_ids':repo}))
    assert client.get(root,headers={'Authorization':'Bearer '+token}).status_code==403
    assert client.post(root,json=body(),headers={'Authorization':'Bearer '+token}).status_code==403
