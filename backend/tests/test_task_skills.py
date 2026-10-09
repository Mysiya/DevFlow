import asyncio
import copy
import json

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app import main, task_skills
from app.agents import AgentService
from app.answer_comparisons import context_for
from app.config import Settings
from app.db import AgentRun, Conversation, ModelCall, PromptExperiment, RunCheckpoint, RunJob, SessionLocal, new_id
from app.demo import demo_snapshot
from app.llm import ModelClient
from app.planning import Plan, Planner
from app.prompts import BASELINE, VARIANTS, bound_settings, fingerprint, freeze_binding, schema, system_prompt
from app.run_queue import claim_job
from app.tools import Tools
from app.worker import execute_claim
from tests.test_governance import setup_auth, new_member

ANSWER = {'title':'源码范围', 'summary':'仅解释固定源码片段。', 'recommendation':'人工核验', 'findings':[], 'next_steps':[], 'gaps':[]}
EVIDENCE = {'code:one':{'id':'code:one','title':'固定源码','content':'def entry():\n    return process()','source':'local_project','path':'app/example.py','sha':'a'*40,'line_start':1,'line_end':2,'url':''}}


async def emit(*args):
    pass


def isolated_definitions(monkeypatch, tmp_path):
    for ident in task_skills.MANIFEST:
        (tmp_path / (ident + '.json')).write_text((task_skills.ROOT / (ident + '.json')).read_text(encoding='utf-8'), encoding='utf-8')
    monkeypatch.setattr(task_skills, 'ROOT', tmp_path)
    return tmp_path


def test_catalog_is_fixed_read_only_and_returns_fresh_definitions(client):
    repo = client.get('/api/repositories').json()[0]['id']
    response = client.get(f'/api/repositories/{repo}/task-skills')
    assert response.status_code == 200
    skills = response.json()['skills']
    assert [s['id'] for s in skills] == list(task_skills.MANIFEST)
    assert all(s['version'] == '1.0.0' and len(s['definition_hash']) == 64 for s in skills)
    skills[0]['checklist'].clear()
    assert task_skills.catalog()[0]['checklist']
    with pytest.raises(ValueError):
        task_skills.load_skill('../config')
    with SessionLocal() as db:
        assert not list(db.scalars(select(AgentRun))) and not list(db.scalars(select(ModelCall)))


@pytest.mark.parametrize('values', [
    {'skill_id':'../code-explain-v1','task':'code'},
    {'skill_id':'code-explain-v1','task':'chat'},
    {'skill_id':'pr-review-v1','task':'pr'},
    {'skill_id':'ci-debug-v1','task':'ci'},
    {'skill_id':'pr-review-v1','task':'pr','target':0},
    {'skill_id':'ci-debug-v1','task':'pr','target':301},
])
def test_invalid_selection_fails_before_any_persistence(client, values):
    repo = client.get('/api/repositories').json()[0]['id']
    response = client.post('/api/chat/runs', json={'repository_id':repo,'message':'检查 PR #18',**values})
    assert response.status_code == 422
    with SessionLocal() as db:
        assert not list(db.scalars(select(Conversation))) and not list(db.scalars(select(AgentRun))) and not list(db.scalars(select(RunJob)))


def test_legacy_prompt_shape_is_exact_and_unselected_jobs_ignore_global_skill():
    settings = Settings(_env_file=None)
    original = BASELINE.format(role='code', schema=json.dumps(schema(), ensure_ascii=False)) + VARIANTS['baseline-v1']['extra']
    assert system_prompt('baseline-v1','code') == original
    assert set(freeze_binding(settings)) == {'prompt_id','template_hash','model_config_hash'}
    configured = settings.model_copy(update={'analysis_skill_id':'code-explain-v1'})
    assert bound_settings(configured, None).analysis_skill_id is None
    assert bound_settings(configured, {'analysis_binding':freeze_binding(settings)}).analysis_skill_id is None
    assert bound_settings(settings, {'analysis_binding':freeze_binding(configured)}).analysis_skill_id == 'code-explain-v1'
    for bad in (None, [], {'id':'unknown'}, {'id':'code-explain-v1'}):
        with pytest.raises(ValueError):
            bound_settings(settings, {'analysis_binding':{**freeze_binding(settings),'skill':bad}})


@pytest.mark.parametrize('change', ['version','instructions','allowed_tools'])
def test_queued_definition_change_fails_before_model(client, monkeypatch, tmp_path, change):
    directory = isolated_definitions(monkeypatch,tmp_path)
    repo = client.get('/api/repositories').json()[0]['id']
    response = client.post('/api/chat/runs',json={'repository_id':repo,'message':'审查目标 PR','task':'pr','target':18,'skill_id':'pr-review-v1'})
    assert response.status_code == 202
    path = directory / 'pr-review-v1.json'; definition = json.loads(path.read_text(encoding='utf-8'))
    definition[change] = {'version':'1.0.1','instructions':definition['instructions']+' 新增检查要求。','allowed_tools':['inspect_pr']}[change]
    path.write_text(json.dumps(definition,ensure_ascii=False),encoding='utf-8')
    async def forbidden(*args,**kwargs):
        raise AssertionError('Changed queued Skill must stop before model')
    monkeypatch.setattr(ModelClient,'chat',forbidden)
    asyncio.run(execute_claim(claim_job(main.settings,'skill-worker'),main.settings))
    run = client.get(f'/api/repositories/{repo}/runs/{response.json()["run_id"]}').json()
    assert run['status'] == 'failed' and 'Skill' in run['events'][-1]['data']['message']
    with SessionLocal() as db:
        assert not list(db.scalars(select(ModelCall)))


def test_tool_subset_is_enforced_for_advertising_and_calling():
    settings = Settings(_env_file=None, analysis_skill_id='code-explain-v1')
    tools = Tools(settings,demo_snapshot(),emit)
    assert {t['function']['name'] for t in tools.definitions()} == {'read_code','search_code','search_memories'}
    with pytest.raises(ValueError,match='未注册工具'):
        asyncio.run(tools.call('inspect_issue',{'number':42}))
    assert not tools.evidence
    asyncio.run(tools.close())


def test_dequeued_definition_stays_fixed_during_tool_reads_and_model_calls(monkeypatch,tmp_path):
    directory=isolated_definitions(monkeypatch,tmp_path)
    settings=Settings(_env_file=None,analysis_skill_id='code-explain-v1',devflow_mode='live')
    frozen=freeze_binding(settings)
    running=bound_settings(settings,{'analysis_binding':frozen})
    path=directory/'code-explain-v1.json';value=json.loads(path.read_text(encoding='utf-8'))
    value.update(version='1.0.1',allowed_tools=['inspect_pr'],instructions=value['instructions']+' 新版指令。')
    path.write_text(json.dumps(value),encoding='utf-8')
    tools=Tools(running,demo_snapshot(),emit)
    assert set(tools.registry)=={'read_code','search_code','search_memories'}
    sent=[]
    async def chat(self,messages,**kwargs):
        sent.append(messages);return {'content':json.dumps(ANSWER)}
    monkeypatch.setattr(ModelClient,'chat',chat)
    answer=asyncio.run(ModelClient(running).analyze('code','解释 entry',EVIDENCE))
    assert answer['analysis_context']['skill']==frozen['skill']
    assert '新版指令' not in sent[0][0]['content']
    with pytest.raises(ValueError,match='已变化'):
        bound_settings(settings,{'analysis_binding':frozen})
    asyncio.run(tools.close())


def test_actual_http_path_sends_skill_and_records_server_owned_context(monkeypatch,tmp_path):
    directory = isolated_definitions(monkeypatch,tmp_path)
    captured=[]; before=task_skills.skill_binding('code-explain-v1')
    def handler(request):
        captured.append(json.loads(request.content))
        # Change the definition while the response is in flight: provenance
        # must describe the actual sent instructions, not this new definition.
        path=directory/'code-explain-v1.json'; value=json.loads(path.read_text(encoding='utf-8'))
        value['version']='1.0.1';path.write_text(json.dumps(value),encoding='utf-8')
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({**ANSWER,'analysis_context':{'skill':{'id':'forged'}}})}}]})
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    settings=Settings(_env_file=None,devflow_mode='live',llm_model='fixture',llm_api_key=SecretStr('fixture-only'),analysis_skill_id='code-explain-v1')
    answer=asyncio.run(ModelClient(settings).analyze('code','解释 entry',EVIDENCE))
    assert len(captured)==1 and before['definition_hash'] in captured[0]['messages'][0]['content']
    assert '检查项是分析要求' in captured[0]['messages'][0]['content']
    assert answer['analysis_context']['skill']==before
    assert answer['analysis_context']['system_prompt']==captured[0]['messages'][0]['content']
    assert answer['analysis_context']['answer_hash']==fingerprint({k:answer[k] for k in ('title','summary','recommendation','findings','next_steps')})
    assert 'forged' not in json.dumps(answer)


@pytest.mark.parametrize('skill,task,target', [('pr-review-v1','pr',18),('ci-debug-v1','ci',301)])
def test_demo_worker_saves_binding_result_and_event_without_model(client,monkeypatch,skill,task,target):
    repo=client.get('/api/repositories').json()[0]['id']
    async def forbidden(*args,**kwargs):
        raise AssertionError('Demo Skill must not call model')
    monkeypatch.setattr(ModelClient,'chat',forbidden)
    response=client.post('/api/chat/runs',json={'repository_id':repo,'message':'按流程审查','task':task,'target':target,'skill_id':skill})
    ident=response.json()['run_id']
    with SessionLocal() as db:
        assert db.get(RunJob,ident).payload['input']['analysis_binding']['skill']==task_skills.skill_binding(skill)
    asyncio.run(execute_claim(claim_job(main.settings,'demo-worker'),main.settings))
    run=client.get(f'/api/repositories/{repo}/runs/{ident}').json()
    assert run['status']=='completed' and run['result']['task_skill']['mode']=='demo-rules'
    assert run['result']['task_skill']['definition_hash']==task_skills.skill_binding(skill)['definition_hash']
    assert next(e for e in run['events'] if e['type']=='run.started')['data']['skill_id']==skill
    assert 'analysis_context' not in run['result']
    with SessionLocal() as db:
        assert not list(db.scalars(select(ModelCall)))


def test_planner_receives_release_skill_once_across_repair():
    captured=[]
    class FakeModel:
        async def chat(self,messages,**kwargs):
            captured.append(copy.deepcopy(messages))
            return {'content':'{}' if len(captured)==1 else json.dumps({'reason':'只读检查','steps':[{'id':'report','agent':'report','title':'检查快照'}]})}
    settings=Settings(_env_file=None,analysis_skill_id='release-check-v1')
    context={'issue_numbers':[],'pr_numbers':[],'ci_ids':[],'workspace':None,'document_count':0,'explicit_ci':None}
    plan=asyncio.run(Planner(FakeModel(),settings,emit).create('发布检查',context,3))
    assert plan.steps[0].agent=='report' and len(captured)==2
    assert captured[0][0]==captured[1][0] and captured[0][0]['content'].count('任务 Skill：')==1


def test_workflow_skill_survives_checkpoint_resume(client,monkeypatch):
    plan=Plan.model_validate({'reason':'Skill 恢复样例','steps':[{'id':'report','agent':'report','title':'快照'},{'id':'docs','agent':'knowledge','title':'文档','query':'租户','depends_on':['report']}]})
    monkeypatch.setattr(Planner,'fallback',lambda *args:plan)
    original=AgentService.specialist; calls=[]
    async def cancel_once(self,task,target=None):
        calls.append(task)
        if task=='knowledge' and calls.count(task)==1:
            raise asyncio.CancelledError()
        return await original(self,task,target)
    monkeypatch.setattr(AgentService,'specialist',cancel_once)
    repo=client.get('/api/repositories').json()[0]['id']
    response=client.post('/api/chat/stream',json={'repository_id':repo,'message':'发布协作检查','task':'workflow','skill_id':'release-check-v1'})
    frames=[json.loads(x[6:]) for x in response.text.split('\n\n') if x.startswith('data: ')]
    ident=frames[0]['data']['run_id'];root=f'/api/repositories/{repo}/runs/{ident}'
    assert client.get(root).json()['status']=='cancelled'
    with SessionLocal() as db:
        assert db.get(RunCheckpoint,ident).state['input']['analysis_binding']['skill']==task_skills.skill_binding('release-check-v1')
    resumed=client.post(root+'/resume')
    assert resumed.status_code==200
    run=client.get(root).json()
    assert run['status']=='completed' and run['result']['task_skill']['id']=='release-check-v1'
    assert calls.count('report')==1 and calls.count('knowledge')==2


def test_catalog_invalid_definition_and_credentials_never_leak(client,monkeypatch,tmp_path):
    directory=isolated_definitions(monkeypatch,tmp_path)
    repo=client.get('/api/repositories').json()[0]['id'];path=directory/'code-explain-v1.json'
    original=json.loads(path.read_text(encoding='utf-8'))
    for field,value in (('allowed_tools',['execute_shell']),('roles',['ChatAgent']),('arbitrary','command')):
        path.write_text(json.dumps({**original,field:value}),encoding='utf-8')
        response=client.get(f'/api/repositories/{repo}/task-skills')
        assert response.status_code==503 and 'execute_shell' not in response.text
    secret='fixture-skill-secret'
    path.write_text(json.dumps({**original,'instructions':original['instructions']+secret}),encoding='utf-8')
    monkeypatch.setattr(main,'settings',main.settings.model_copy(update={'llm_api_key':SecretStr(secret)}))
    response=client.get(f'/api/repositories/{repo}/task-skills')
    assert response.status_code==422 and secret not in response.text
    assert client.post('/api/chat/runs',json={'repository_id':repo,'message':'解释','task':'code','skill_id':'code-explain-v1'}).status_code==422
    with SessionLocal() as db:
        assert not list(db.scalars(select(Conversation)))


def test_skill_catalog_respects_membership_roles_mcp_and_origin(client,monkeypatch):
    repo,settings=setup_auth(client,monkeypatch);new_member(client,repo,'viewer')
    client.post('/api/auth/logout');client.post('/api/auth/login',json={'username':'member','password':'testing-member-1234'})
    assert client.get(f'/api/repositories/{repo}/task-skills').status_code==200
    assert client.get(f'/api/repositories/{new_id()}/task-skills').status_code==404
    assert client.post('/api/chat/runs',json={'repository_id':repo,'message':'检查','task':'pr','target':18,'skill_id':'pr-review-v1'}).status_code==403
    assert client.post('/api/chat/runs',headers={'Origin':'https://untrusted.example'},json={'repository_id':repo,'message':'检查'}).status_code==403
    token='fixture-mcp-skill-'+('a'*32)
    monkeypatch.setattr(main,'settings',settings.model_copy(update={'mcp_access_token':SecretStr(token),'mcp_repository_ids':repo}))
    assert client.get(f'/api/repositories/{repo}/task-skills',headers={'Authorization':'Bearer '+token}).status_code==403


def test_comparison_and_evaluation_sets_separate_skill_strata(client,monkeypatch):
    repo=client.get('/api/repositories').json()[0]['id']; root=f'/api/repositories/{repo}'
    async def chat(*args,**kwargs):
        return {'content':json.dumps(ANSWER)}
    monkeypatch.setattr(ModelClient,'chat',chat)
    experiments=[];all_ids=[]
    with SessionLocal() as db:
        conv=Conversation(repository_id=repo,title='Skill 参数隔离');db.add(conv);db.flush()
        for skill in (None,'code-explain-v1'):
            ids=[]
            for prompt in ('baseline-v1','evidence-first-v2'):
                settings=Settings(_env_file=None,devflow_mode='live',llm_model='fixture',analysis_prompt_id=prompt,analysis_skill_id=skill)
                result=asyncio.run(ModelClient(settings).analyze('code','解释 entry',EVIDENCE))
                result.update(evidence=list(EVIDENCE.values()),provider='llm',task='code')
                run=AgentRun(repository_id=repo,conversation_id=conv.id,question='解释 entry',task='code',mode='live',status='completed',result=result)
                db.add(run);db.flush();ids.append(run.id)
                assert context_for(run)[1] is None
            experiment=PromptExperiment(repository_id=repo,request_id=new_id(),request_hash=fingerprint(ids),question='解释 entry',task='code',left_run_id=ids[0],right_run_id=ids[1],frozen_hash=fingerprint(ids),sources={},parameters={},prompt_bindings=[],created_by='fixture')
            db.add(experiment);db.flush();experiments.append(experiment.id);all_ids.append(ids)
        db.commit()
    for ids in all_ids:
        assert client.get(root+f'/answer-comparisons?left={ids[0]}&right={ids[1]}').json()['controlled_pair']
    mixed=client.get(root+f'/answer-comparisons?left={all_ids[0][0]}&right={all_ids[1][1]}').json()
    assert not mixed['controlled_pair'] and any('Skill' in reason for reason in mixed['reasons'])
    response=client.post(root+'/answer-evaluation-sets',json={'request_id':new_id(),'name':'Skill 分层样例','experiment_ids':experiments})
    assert response.status_code==201,response.text
    detail=client.get(root+'/answer-evaluation-sets/'+response.json()['id']).json()
    groups=detail['coverage']['by_prompt']
    assert len(groups)==4 and sum('skill' in group for group in groups)==2
    assert detail['coverage']['eligible_pairs']==2 and detail['coverage']['reviewed']==0
