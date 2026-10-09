import asyncio
import copy
import json

import httpx
import pytest
from pydantic import SecretStr

from app import main
from app.answer_comparisons import context_for
from app.checkpoints import model_key
from app.config import Settings
from app.db import AgentRun, Conversation, Repository, RunCheckpoint, RunJob, SessionLocal, new_id
from app.llm import ModelClient
from app.prompts import bound_settings, fingerprint, freeze_binding, provenance, system_prompt
from app.run_queue import claim_job
from app.worker import execute_claim

ANSWER = {"title":"来源回答", "summary":"该来源要求保留任务租约。", "recommendation":"人工核验", "findings":[], "next_steps":["核对来源版本。"], "gaps":[]}
EVIDENCE = {"doc:one":{"id":"doc:one", "title":"租约", "content":"后台任务须保留任务租约。", "source":"document", "url":"", "sha":"revision-one"}}


def pair(client, monkeypatch):
    repo = client.get('/api/repositories').json()[0]['id']
    captured = []
    async def chat(self, messages, **kwargs):
        captured.append(messages)
        return {"content":json.dumps({**ANSWER,"analysis_context":{"input_hash":"forged"}},ensure_ascii=False)}
    monkeypatch.setattr(ModelClient,'chat',chat)
    ids = []
    with SessionLocal() as db:
        conversation = Conversation(repository_id=repo,title='隔离 Prompt 样例')
        db.add(conversation);db.flush()
        for ident in ('baseline-v1','evidence-first-v2'):
            settings = Settings(devflow_mode='live',llm_model='fixture-model',analysis_prompt_id=ident)
            result = asyncio.run(ModelClient(settings).analyze('knowledge','任务租约是什么？',EVIDENCE))
            result.update(evidence=list(EVIDENCE.values()),provider='llm',task='knowledge')
            run = AgentRun(repository_id=repo,conversation_id=conversation.id,question='任务租约是什么？',task='knowledge',mode='live',status='completed',result=result,events=[{'type':'fixture'}])
            db.add(run);db.flush();ids.append(run.id)
        db.commit()
    return f'/api/repositories/{repo}',ids,captured


def get_pair(client, root, ids):
    response=client.get(root+f'/answer-comparisons?left={ids[0]}&right={ids[1]}')
    assert response.status_code==200,response.text
    return response.json()


def test_two_prompts_same_input_compare_export_and_no_model_spoof(client,monkeypatch):
    root,ids,captured=pair(client,monkeypatch)
    assert captured[0][1]==captured[1][1] and captured[0][0]!=captured[1][0]
    assert captured[0][0]['content']==system_prompt('baseline-v1','knowledge')
    before=[]
    with SessionLocal() as db:
        for ident in ids:before.append(copy.deepcopy(db.get(AgentRun,ident).result))
    data=get_pair(client,root,ids)
    assert data['controlled_pair'] and not data['reasons'] and data['winner'] is None and data['model_accuracy'] is None
    assert data['left']['context']['input_hash']!='forged'
    assert data['left']['context']['input_hash']==data['right']['context']['input_hash']
    assert data['left']['review'] is None and data['left']['metrics']['elapsed_ms'] is None
    exported=client.get(root+f'/answer-comparisons/export?left={ids[0]}&right={ids[1]}')
    assert 'attachment' in exported.headers['content-disposition'] and exported.json()==data
    prompts=client.get(root+'/analysis-prompts').json()['prompts']
    assert [p['id'] for p in prompts]==['baseline-v1','evidence-first-v2']
    assert len(client.get(root+'/answer-comparisons/runs').json()['runs'])==2
    with SessionLocal() as db:
        assert [db.get(AgentRun,i).result for i in ids]==before
        assert all(db.get(AgentRun,i).events==[{'type':'fixture'}] for i in ids)


@pytest.mark.parametrize('change,reason',[
    ('legacy','未记录'),('question','问题或分析任务'),('evidence','证据'),('answer','无法核对'),('model','模型服务或参数'),
    ('gaps','完整模型输入'),('same-prompt','同一 Prompt'),('demo','真实模型'),('workflow','多 Agent'),('malformed','不完整')])
def test_conservative_comparison_rejects_unknown_mismatches_and_mutation(client,monkeypatch,change,reason):
    root,ids,_=pair(client,monkeypatch)
    with SessionLocal() as db:
        run=db.get(AgentRun,ids[1]);value=copy.deepcopy(run.result)
        if change=='legacy':value.pop('analysis_context')
        elif change=='question':run.question='另一个问题'
        elif change=='evidence':value['evidence'][0]['content']='已经变化'
        elif change=='answer':value['summary']='已被改写'
        elif change=='demo':run.mode='demo';value['provider']='demo-rules'
        elif change=='workflow':run.task='workflow'
        elif change=='malformed':value['analysis_context']={'schema':'analysis-context-v1','prompt_id':[]}
        else:
            settings=Settings(devflow_mode='live',llm_model='another-model' if change=='model' else 'fixture-model',analysis_prompt_id='baseline-v1' if change=='same-prompt' else 'evidence-first-v2')
            user={'question':run.question,'evidence':EVIDENCE,'known_gaps':['另一个缺口'] if change=='gaps' else [],'source_numeric_facts':[],'source_formula_relations':[],'source_facts_limited':False}
            value['analysis_context']=provenance(settings,'knowledge',user,ANSWER)
        run.result=value;db.commit()
    data=get_pair(client,root,ids)
    assert not data['controlled_pair'] and any(reason in r for r in data['reasons'])


def test_current_partial_review_and_stale_annotation_are_not_quality_scores(client,monkeypatch):
    root,ids,_=pair(client,monkeypatch)
    path=root+f'/runs/{ids[0]}/answer-reviews'
    detail=client.get(path).json()
    response=client.post(path,json={'snapshot_hash':detail['snapshot_hash'],'expected_version':0,'annotations':[{'item_id':'summary','decision':'supported','evidence_ids':['doc:one'],'note':'与此来源文字相符。'}]})
    assert response.status_code==201,response.text
    assert len(get_pair(client,root,ids)['left']['review']['annotations'])==1
    with SessionLocal() as db:
        run=db.get(AgentRun,ids[0]);value=copy.deepcopy(run.result);value['gaps']=['新缺口'];run.result=value;db.commit()
    data=get_pair(client,root,ids)
    assert data['left']['stale_review'] and data['left']['review'] is None and data['winner'] is None


def test_scope_incomplete_same_id_and_credentials(client,monkeypatch):
    root,ids,_=pair(client,monkeypatch)
    assert client.get(root+f'/answer-comparisons?left={ids[0]}&right={ids[0]}').status_code==422
    assert client.get(root+f'/answer-comparisons?left={ids[0]}&right=invalid').status_code==422
    assert client.get(root+f'/answer-comparisons?left={ids[0]}&right={new_id()}').status_code==404
    with SessionLocal() as db:
        other=Repository(full_name='example/other',snapshot={'source':'demo'});db.add(other);db.flush()
        db.get(AgentRun,ids[1]).repository_id=other.id;db.commit()
    assert client.get(root+f'/answer-comparisons?left={ids[0]}&right={ids[1]}').status_code==404
    with SessionLocal() as db:
        a,b=[db.get(AgentRun,i) for i in ids];b.repository_id=a.repository_id;b.status='failed';db.commit()
    assert client.get(root+f'/answer-comparisons?left={ids[0]}&right={ids[1]}').status_code==409
    monkeypatch.setattr(main.settings,'llm_api_key',SecretStr('private-test-key'))
    with SessionLocal() as db:
        run=db.get(AgentRun,ids[1]);run.status='completed';v=copy.deepcopy(run.result);v['summary']='private-test-key';run.result=v;db.commit()
    assert client.get(root+f'/answer-comparisons?left={ids[0]}&right={ids[1]}').status_code==422


def test_queued_choice_applies_in_worker_and_changed_binding_stops_before_execution(client,monkeypatch):
    from app.agents import AgentService
    seen=[]
    async def run(self,*args):
        seen.append(self.tools.settings.analysis_prompt_id)
        return {**ANSWER,'evidence':[],'provider':'demo-rules','task':'knowledge'}
    monkeypatch.setattr(AgentService,'run',run)
    repo=client.get('/api/repositories').json()[0]['id']
    for prompt in ('evidence-first-v2','baseline-v1'):
        result=client.post('/api/chat/runs',json={'repository_id':repo,'message':'租约','task':'knowledge','prompt_id':prompt})
        assert result.status_code==202
        claim=claim_job(main.settings,'fixture-worker')
        assert claim['payload']['input']['analysis_binding']['prompt_id']==prompt
        asyncio.run(execute_claim(claim,main.settings))
        with SessionLocal() as db:assert db.get(AgentRun,result.json()['run_id']).status=='completed'
    assert seen==['evidence-first-v2','baseline-v1']
    result=client.post('/api/chat/runs',json={'repository_id':repo,'message':'租约','task':'knowledge','prompt_id':'evidence-first-v2'})
    with SessionLocal() as db:
        job=db.get(RunJob,result.json()['run_id']);payload=copy.deepcopy(job.payload);payload['input']['analysis_binding']['template_hash']='changed';job.payload=payload;db.commit()
    asyncio.run(execute_claim(claim_job(main.settings,'fixture-worker'),main.settings))
    with SessionLocal() as db:assert db.get(AgentRun,result.json()['run_id']).status=='failed'
    assert seen==['evidence-first-v2','baseline-v1']
    assert client.post('/api/chat/runs',json={'repository_id':repo,'message':'x','prompt_id':'arbitrary'}).status_code==422


def test_binding_parameter_checks_legacy_compatibility_and_resume(client):
    settings=Settings(analysis_prompt_id='evidence-first-v2')
    frozen={'analysis_binding':freeze_binding(settings)}
    assert bound_settings(settings,frozen).analysis_prompt_id=='evidence-first-v2'
    assert bound_settings(settings,{}).analysis_prompt_id=='baseline-v1'
    assert model_key(settings)==model_key(settings.model_copy(update={'analysis_prompt_id':'baseline-v1'}))
    with pytest.raises(ValueError,match='模型参数'):
        bound_settings(settings.model_copy(update={'llm_max_tokens':2048}),frozen)
    repo=client.get('/api/repositories').json()[0]['id']
    result=client.post('/api/chat/runs',json={'repository_id':repo,'message':'恢复','task':'workflow','prompt_id':'evidence-first-v2'})
    ident=result.json()['run_id'];root=f'/api/repositories/{repo}/runs/{ident}'
    client.post(root+'/stop')
    assert client.get(root).json()['recovery']['available']
    with SessionLocal() as db:
        cp=db.get(RunCheckpoint,ident);value=copy.deepcopy(cp.state);value['input']['analysis_binding']['template_hash']='old';cp.state=value;db.commit()
    assert not client.get(root).json()['recovery']['available']
    assert client.post(root+'/resume-background').status_code==409


def test_actual_queue_agent_model_protocol_preserves_two_version_records(client,monkeypatch):
    """Real execution path, isolated DB and HTTP fixture; no external model request."""
    repo=client.get('/api/repositories').json()[0]['id']
    imported=client.post(f'/api/repositories/{repo}/knowledge/documents',json={'path':'docs/lease-fixture.md','title':'任务租约指南','content':'# 后台任务租约\n后台任务须保留任务租约；任务租约过期后停止旧 Worker。'})
    assert imported.status_code==200
    with SessionLocal() as db:
        record=db.get(Repository,repo);record.snapshot={**record.snapshot,'source':'github'};db.commit()
    chosen=main.settings.model_copy(update={'devflow_mode':'live','llm_model':'fixture-model','llm_api_key':SecretStr('fixture-key')})
    monkeypatch.setattr(main,'settings',chosen)
    captured=[]
    def response(request):
        assert str(request.url).endswith('/chat/completions')
        payload=json.loads(request.content);captured.append(payload)
        answer=copy.deepcopy(ANSWER)
        answer['findings']=[{'severity':'info','title':'来源范围','detail':'只依据所给材料。','evidence_ids':list(json.loads(payload['messages'][1]['content'])['evidence'])[:1]}]
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(answer,ensure_ascii=False)}}],'usage':{'prompt_tokens':100,'completion_tokens':20}})
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(response),**kwargs))
    ids=[]
    for prompt in ('baseline-v1','evidence-first-v2'):
        submitted=client.post('/api/chat/runs',json={'repository_id':repo,'message':'如何保留后台任务租约？','task':'knowledge','prompt_id':prompt})
        assert submitted.status_code==202,submitted.text
        ident=submitted.json()['run_id'];ids.append(ident)
        asyncio.run(execute_claim(claim_job(chosen,'fixture-worker'),chosen))
        with SessionLocal() as db:
            run=db.get(AgentRun,ident)
            assert run.status=='completed',run.events
            assert context_for(run)[1] is None
            assert run.result['analysis_context']['prompt_id']==prompt
    data=get_pair(client,f'/api/repositories/{repo}',ids)
    assert data['controlled_pair'],data['reasons']
    assert data['left']['metrics']['model_calls']==1 and data['left']['metrics']['known_input_tokens']==100
    assert captured[0]['messages'][1]==captured[1]['messages'][1]
    assert len(captured)==2 and data['left']['snapshot']['evidence']


def test_viewer_reads_but_mcp_white_list_is_unchanged(client,monkeypatch):
    from tests.test_governance import setup_auth,new_member
    root,ids,_=pair(client,monkeypatch)
    repo,_=setup_auth(client,monkeypatch)
    new_member(client,repo,'viewer')
    client.post('/api/auth/login',json={'username':'member','password':'testing-member-1234'})
    assert get_pair(client,root,ids)['controlled_pair']
    assert client.get(root+'/analysis-prompts').status_code==200
    assert client.get(root+'/answer-comparisons/runs').status_code==200
    assert client.get(root+f'/answer-comparisons/export?left={ids[0]}&right={ids[1]}').status_code==200
    token='compare-fixture-token-at-least-32-chars'
    monkeypatch.setattr(main,'settings',main.settings.model_copy(update={'mcp_access_token':SecretStr(token),'mcp_repository_ids':repo}))
    for path in ('/analysis-prompts','/answer-comparisons/runs',f'/answer-comparisons?left={ids[0]}&right={ids[1]}',f'/answer-comparisons/export?left={ids[0]}&right={ids[1]}'):
        assert client.get(root+path,headers={'Authorization':'Bearer '+token}).status_code==403
