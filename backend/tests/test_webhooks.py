import asyncio
import copy
import hashlib
import hmac
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import select,func

from app import main,synchronization as sync
from app.config import Settings
from app.db import Repository, RepositorySyncState, SessionLocal, WebhookDelivery, AppUser, AuthSession, RepositoryMember, utcnow
from app.github import GitHubError

SECRET='fixture-webhook-secret-for-isolated-tests'


def setup(client,monkeypatch):
    with SessionLocal() as db:
        snapshot={'name':'owner/repo','source':'github','github_repository_id':123,'branch':'main','head_sha':'a'*40,'description':'test','coverage':'bounded snapshot',
                  'issues':[{'number':1,'title':'previous'}],'pulls':[],'runs':[],'documents':[]}
        record=Repository(full_name='owner/repo',snapshot=snapshot);db.add(record);db.commit();repo=record.id
    settings=Settings(_env_file=None,devflow_mode='live',github_webhook_enabled=True,github_webhook_secret=SECRET,github_webhook_repository_ids=repo)
    monkeypatch.setattr(main,'settings',settings)
    return repo,settings,snapshot


def send(client,event='issues',action='edited',delivery=None,body=None,**headers):
    payload=body or {'repository':{'id':123,'full_name':'owner/repo'},'action':action,'issue':{'body':'private payload must not be stored'}}
    raw=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
    signed={'Content-Type':'application/json','X-GitHub-Event':event,'X-GitHub-Delivery':delivery or str(uuid4()),
            'X-Hub-Signature-256':'sha256='+hmac.new(SECRET.encode(),raw,hashlib.sha256).hexdigest(),**headers}
    return client.post('/api/webhooks/github',content=raw,headers=signed)


def test_webhook_is_disabled_by_default_and_settings_require_independent_binding(client):
    assert send(client).status_code==503
    with pytest.raises(ValidationError):Settings(_env_file=None,github_webhook_enabled=True)
    with pytest.raises(ValidationError):Settings(_env_file=None,devflow_mode='live',github_webhook_enabled=True,github_webhook_secret=SECRET,github_webhook_repository_ids='a'*32,llm_api_key=SECRET)


def test_signature_covers_original_utf8_bytes_and_cookie_login_is_not_required(client,monkeypatch):
    repo,settings,_=setup(client,monkeypatch)
    monkeypatch.setattr(main,'settings',settings.model_copy(update={'auth_enabled':True}))
    assert client.get(f'/api/repositories/{repo}/sync/status').status_code==401
    assert send(client,body={'repository':{'id':123,'full_name':'owner/repo'},'action':'edited','issue':{'body':'中文原始字节'}}).status_code==202
    assert send(client,**{'X-Hub-Signature-256':'sha256='+'0'*64}).status_code==403
    assert send(client,**{'X-Hub-Signature-256':''}).status_code==403
    assert send(client,Origin='http://127.0.0.1:3000').status_code==403
    raw=b'{}'
    signed='sha256='+hmac.new(SECRET.encode(),raw,hashlib.sha256).hexdigest()
    assert client.post('/api/webhooks/github',content=raw+b' ',headers={'Content-Type':'application/json','X-Hub-Signature-256':signed}).status_code==403
    assert client.get('/api/webhooks/github').status_code in (401,405)


def test_replays_are_deduplicated_and_headers_or_content_cannot_reuse_an_id(client,monkeypatch):
    repo,_,_=setup(client,monkeypatch);ident=str(uuid4())
    first=send(client,delivery=ident);assert first.status_code==202
    with ThreadPoolExecutor(max_workers=4) as pool:responses=list(pool.map(lambda _:send(client,delivery=ident),range(8)))
    assert all(r.status_code==202 and r.json()['status']=='duplicate' for r in responses)
    assert send(client,event='pull_request',delivery=ident).status_code==409
    assert send(client,action='closed',delivery=ident).status_code==409
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(WebhookDelivery))==1
        job=db.get(WebhookDelivery,ident)
        assert job.scopes==['issues'] and job.repository_id==repo and job.attempts==0
        assert 'private payload' not in json.dumps({key:value for key,value in job.__dict__.items() if key!='_sa_instance_state'},default=str)


@pytest.mark.parametrize('body',[
    {'repository':{'id':456,'full_name':'owner/repo'},'action':'edited'},
    {'repository':{'id':123,'full_name':'other/repo'},'action':'edited'},
])
def test_repository_name_and_numeric_id_both_match_whitelisted_binding(client,monkeypatch,body):
    setup(client,monkeypatch);assert send(client,body=body).status_code==403
    with SessionLocal() as db:assert db.scalar(select(func.count()).select_from(WebhookDelivery))==0


@pytest.mark.parametrize('event,body,scopes',[
    ('issues',{'action':'closed'},['issues']),('pull_request',{'action':'synchronize'},['issues','pulls']),
    ('workflow_run',{'action':'completed'},['runs']),('push',{'ref':'refs/heads/main'},['documents','repository']),
    ('push',{'ref':'refs/heads/feature'},[]),('ping',{},[]),('issues',{'action':'future_action'},[]),
])
def test_event_action_and_default_branch_choose_only_expected_sections(client,monkeypatch,event,body,scopes):
    setup(client,monkeypatch);response=send(client,event=event,body={'repository':{'id':123,'full_name':'owner/repo'},**body})
    assert response.status_code==202
    with SessionLocal() as db:
        job=db.get(WebhookDelivery,response.json()['delivery_id'])
        assert job.scopes==scopes and job.status==('queued' if scopes else 'ignored')


def test_body_limits_and_invalid_delivery_do_not_enqueue(client,monkeypatch):
    setup(client,monkeypatch)
    assert send(client,delivery='not-a-uuid').status_code==422
    assert send(client,**{'Content-Encoding':'gzip'}).status_code==415
    assert send(client,**{'Content-Length':'1048577'}).status_code==413
    assert send(client,body={'repository':{'id':123,'full_name':'owner/repo'},'action':'edited','body':'x'*1048576}).status_code==413


def test_full_queue_rejects_new_event_but_allows_duplicate_ack(client,monkeypatch):
    repo,_,_=setup(client,monkeypatch);ident=send(client).json()['delivery_id']
    with SessionLocal() as db:
        for _ in range(99):db.add(WebhookDelivery(id=str(uuid4()),repository_id=repo,event='issues',action='edited',body_hash='f'*64,github_repository_id='123',scopes=['issues']))
        db.commit()
    assert send(client).status_code==503
    assert send(client,delivery=ident).json()['status']=='duplicate'


def test_mcp_token_does_not_authorize_webhook_or_retry_endpoints(client,monkeypatch):
    repo,settings,_=setup(client,monkeypatch);token='fixture-mcp-service-token-independent'
    monkeypatch.setattr(main,'settings',settings.model_copy(update={'mcp_access_token':SecretStr(token),'mcp_repository_ids':repo}))
    headers={'Authorization':'Bearer '+token}
    assert client.get(f'/api/repositories/{repo}/sync/status',headers=headers).status_code==403
    assert client.post(f'/api/repositories/{repo}/sync/deliveries/{uuid4()}/retry',headers=headers).status_code==403
    assert client.post('/api/webhooks/github',json={'repository':{'id':123}},headers=headers).status_code==403


def test_manual_sync_is_atomic_and_records_all_section_times(client,monkeypatch):
    repo,settings,previous=setup(client,monkeypatch)
    async def failed(*args):raise GitHubError('failed')
    with pytest.raises(GitHubError):asyncio.run(sync.manual_refresh(settings,repo,failed))
    with SessionLocal() as db:assert db.get(Repository,repo).snapshot==previous and db.get(RepositorySyncState,repo).status=='failed'
    async def fresh(name,snapshot):return {**snapshot,'description':'new after complete fetch'}
    asyncio.run(sync.manual_refresh(settings,repo,fresh))
    state=client.get(f'/api/repositories/{repo}/sync/status').json()
    assert state['status']=='ready' and state['sequence']==1 and state['last_full_sync_at']
    assert set(state['section_times'])==sync.SCOPES
    assert client.get(f'/api/repositories/{repo}/snapshot').json()['description']=='new after complete fetch'


def test_manual_api_refuses_a_different_remote_id_with_the_same_name(client,monkeypatch):
    import httpx
    from app.github import GitHubClient
    repo,_,previous=setup(client,monkeypatch)
    original=GitHubClient.__init__
    def init(self,*args,**kwargs):
        original(self,*args,**kwargs)
        # Never start the original lazy transport; this endpoint reads a controlled response.
        self.client=httpx.AsyncClient(base_url='https://api.github.com',transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'id':456,'full_name':'owner/repo','default_branch':'main'})))
    monkeypatch.setattr(GitHubClient,'__init__',init)
    response=client.post(f'/api/repositories/{repo}/sync')
    assert response.status_code==502 and 'ID 已变化' in response.json()['detail']
    with SessionLocal() as db:assert db.get(Repository,repo).snapshot==previous


def test_parallel_claims_serialize_with_manual_refresh_and_stale_writer_is_fenced(client,monkeypatch):
    repo,settings,previous=setup(client,monkeypatch);ident=send(client).json()['delivery_id']
    first=sync.claim_delivery(settings);assert first['id']==ident and sync.claim_delivery(settings) is None
    async def fake(name,snapshot):return {**snapshot,'description':'must not overwrite'}
    with pytest.raises(sync.SyncError,match='已有同步'):asyncio.run(sync.manual_refresh(settings,repo,fake))
    with SessionLocal() as db:
        db.get(RepositorySyncState,repo).lease_until=utcnow()-timedelta(seconds=1)
        db.get(WebhookDelivery,ident).lease_until=utcnow()-timedelta(seconds=1);db.commit()
    second=sync.claim_delivery(settings);assert second and second['token']!=first['token']
    async def fetch(settings,name,snapshot,scopes):return {**snapshot,'description':'stale'}
    monkeypatch.setattr(sync,'fetch_current',fetch)
    asyncio.run(sync.execute_delivery(first,settings))
    with SessionLocal() as db:
        assert db.get(Repository,repo).snapshot==previous
        job=db.get(WebhookDelivery,ident);assert job.status=='running' and job.lease_token==second['token'] and job.attempts==2


def test_failed_fetch_keeps_snapshot_and_retry_requires_editor_and_is_bounded(client,monkeypatch):
    repo,settings,previous=setup(client,monkeypatch);ident=send(client).json()['delivery_id'];claim=sync.claim_delivery(settings)
    async def failed(*args):raise GitHubError('private upstream data must not leak')
    monkeypatch.setattr(sync,'fetch_current',failed);asyncio.run(sync.execute_delivery(claim,settings))
    with SessionLocal() as db:
        assert db.get(Repository,repo).snapshot==previous
        assert db.get(WebhookDelivery,ident).status=='failed'
        token='fixture-session-token';user=AppUser(username='viewer',password_hash='unused');db.add(user);db.flush()
        db.add(RepositoryMember(repository_id=repo,user_id=user.id,role='viewer'))
        db.add(AuthSession(token_hash=hashlib.sha256(token.encode()).hexdigest(),user_id=user.id,expires_at=utcnow()+timedelta(hours=1)));db.commit();user_id=user.id
    monkeypatch.setattr(main,'settings',settings.model_copy(update={'auth_enabled':True}));client.cookies.set('devflow_session',token)
    assert client.get(f'/api/repositories/{repo}/sync/status').status_code==200
    path=f'/api/repositories/{repo}/sync/deliveries/{ident}/retry'
    assert client.post(path).status_code==403
    with SessionLocal() as db:db.get(RepositoryMember,(repo,user_id)).role='editor';db.commit()
    assert client.post(path).status_code==200 and client.post(path).status_code==409
    with SessionLocal() as db:job=db.get(WebhookDelivery,ident);job.status='failed';job.attempts=3;db.commit()
    assert client.post(path).status_code==409
    assert 'private upstream' not in json.dumps(client.get(f'/api/repositories/{repo}/sync/status').json())


def test_cancelled_read_is_requeued_and_revoked_binding_never_fetches(client,monkeypatch):
    repo,settings,_=setup(client,monkeypatch);ident=send(client).json()['delivery_id'];claim=sync.claim_delivery(settings)
    async def check():
        started=asyncio.Event()
        async def wait(*args):started.set();await asyncio.Event().wait()
        monkeypatch.setattr(sync,'fetch_current',wait)
        task=asyncio.create_task(sync.execute_delivery(claim,settings));await started.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    asyncio.run(check())
    with SessionLocal() as db:assert db.get(WebhookDelivery,ident).status=='queued' and db.get(RepositorySyncState,repo).lease_token is None
    second=sync.claim_delivery(settings)
    async def forbidden(*args):raise AssertionError('must not fetch after revocation')
    monkeypatch.setattr(sync,'fetch_current',forbidden)
    asyncio.run(sync.execute_delivery(second,settings.model_copy(update={'github_webhook_repository_ids':''})))
    with SessionLocal() as db:assert db.get(WebhookDelivery,ident).status=='failed'


def test_real_worker_process_consumes_persisted_event_after_api_restart(client,monkeypatch):
    repo,settings,_=setup(client,monkeypatch);ident=send(client).json()['delivery_id']
    async def restart():
        async with main.lifespan(main.app):pass
    asyncio.run(restart())
    assert client.get(f'/api/repositories/{repo}/sync/status').json()['counts']['queued']==1
    script=f'''
import asyncio,copy
from app import synchronization as sync
from app.config import get_settings
from app.db import WebhookDelivery,SessionLocal
from app.worker import run_worker
async def fake(settings,name,previous,scopes):
    assert scopes==['issues']
    result=copy.deepcopy(previous);result['issues']=[];return result
sync.fetch_current=fake
async def main():
    stop=asyncio.Event();task=asyncio.create_task(run_worker(get_settings(),stop))
    try:
        for _ in range(100):
            await asyncio.sleep(.05)
            with SessionLocal() as db:
                if db.get(WebhookDelivery,{ident!r}).status=='succeeded':break
        else:raise AssertionError('worker did not complete')
    finally:stop.set();await task
asyncio.run(main())
'''
    env={**os.environ,'PYTHONPATH':str(Path(__file__).parents[1]),'DEVFLOW_MODE':'live','GITHUB_WEBHOOK_ENABLED':'true','GITHUB_WEBHOOK_SECRET':SECRET,'GITHUB_WEBHOOK_REPOSITORY_IDS':repo}
    proc=subprocess.run([sys.executable,'-c',script],env=env,cwd=Path(__file__).parents[2],capture_output=True,text=True,timeout=15)
    assert proc.returncode==0,proc.stderr
    status=client.get(f'/api/repositories/{repo}/sync/status').json()
    assert status['counts']=={'succeeded':1} and status['sequence']==1
    assert set(status['section_times'])=={'issues'} and status['last_full_sync_at'] is None
    assert client.get(f'/api/repositories/{repo}/snapshot').json()['issues']==[]
    assert send(client,delivery=ident).json()['processing_status']=='succeeded'
