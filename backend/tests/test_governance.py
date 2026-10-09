import asyncio
import hashlib
import json
from datetime import timedelta

import httpx
from pydantic import SecretStr
from sqlalchemy import select

from app import main
from app.db import ActionDraft, AppUser, AuditRecord, AuthSession, DraftReview, ProjectMemory, PublishAttempt, Repository, SessionLocal, utcnow
from app.governance import draft_digest
from app.github import GitHubClient
from app.publishing import CommentClient, recover_publications
from app.run_queue import claim_job
from app.worker import execute_claim


def repo(client):return client.get('/api/repositories').json()[0]['id']


def run(client, task='pr', message='检查 PR #18'):
    response=client.post('/api/chat/stream',json={'repository_id':repo(client),'task':task,'message':message,'target':18 if task=='pr' else None})
    assert response.status_code==200
    return client.get(f'/api/repositories/{repo(client)}/runs').json()[0]


def draft(client):
    result=run(client)
    return client.post(f'/api/repositories/{repo(client)}/drafts',json={'run_id':result['id']}).json()['id']


def setup_auth(client,monkeypatch):
    ident=repo(client)
    settings=main.settings.model_copy(update={'auth_enabled':True,'bootstrap_admin_password':SecretStr('testing-owner-1234')})
    monkeypatch.setattr(main,'settings',settings)
    async def boot():
        async with main.lifespan(main.app):pass
    asyncio.run(boot())
    assert client.post('/api/auth/login',json={'username':'admin','password':'testing-owner-1234'}).status_code==200
    return ident,settings


def new_member(client,ident,role='viewer'):
    user=client.post('/api/admin/users',json={'username':'member','password':'testing-member-1234'}).json()
    assert client.post(f'/api/admin/repositories/{ident}/members',json={'user_id':user['id'],'role':role}).status_code==200
    return user


def test_auth_cookie_is_hashed_expiring_and_logout_revokes(client,monkeypatch):
    ident,_=setup_auth(client,monkeypatch)
    token=client.cookies.get('devflow_session')
    with SessionLocal() as db:
        session=db.get(AuthSession,hashlib.sha256(token.encode()).hexdigest())
        assert session and session.token_hash!=token
        user=db.get(AppUser,session.user_id)
        assert 'testing-owner-1234' not in user.password_hash
    assert client.post('/api/auth/logout').status_code==200
    assert client.get(f'/api/repositories/{ident}/snapshot').status_code==401
    assert client.get('/api/auth/session').json()['user'] is None


def test_failed_login_is_rate_limited_and_cross_origin_write_is_rejected(client,monkeypatch):
    setup_auth(client,monkeypatch)
    for _ in range(8):assert client.post('/api/auth/login',json={'username':'nobody','password':'wrong'}).status_code==401
    assert client.post('/api/auth/login',json={'username':'nobody','password':'wrong'}).status_code==429
    assert client.post('/api/auth/logout',headers={'Origin':'https://untrusted.example'}).status_code==403


def test_viewer_can_read_search_but_cannot_analyze_review_or_manage(client,monkeypatch):
    ident,_=setup_auth(client,monkeypatch);new_member(client,ident)
    client.post('/api/auth/logout')
    assert client.post('/api/auth/login',json={'username':'member','password':'testing-member-1234'}).status_code==200
    assert client.get(f'/api/repositories/{ident}/snapshot').status_code==200
    assert client.post(f'/api/repositories/{ident}/knowledge/search',json={'query':'租户'}).status_code==200
    assert client.post('/api/chat/runs',json={'repository_id':ident,'task':'report','message':'hello'}).status_code==403
    assert client.post(f'/api/repositories/{ident}/memories',json={'title':'x','content':'x'}).status_code==403
    assert client.get(f'/api/repositories/{ident}/audit').status_code==403
    assert client.get('/api/admin/users').status_code==403


def test_repository_membership_hides_lists_queue_and_foreign_rows(client,monkeypatch):
    ident,_=setup_auth(client,monkeypatch);new_member(client,ident,'editor')
    with SessionLocal() as db:
        original=db.get(Repository,ident)
        other=Repository(full_name='demo/other',snapshot=original.snapshot);db.add(other);db.commit();other_id=other.id
    client.post('/api/chat/runs',json={'repository_id':other_id,'task':'report','message':'foreign job'})
    client.post('/api/auth/logout');client.post('/api/auth/login',json={'username':'member','password':'testing-member-1234'})
    assert [r['id'] for r in client.get('/api/repositories').json()]==[ident]
    assert client.get('/api/queue/status').json()['queued']==0
    assert client.get(f'/api/repositories/{other_id}/snapshot').status_code==404
    assert client.post('/api/chat/runs',json={'repository_id':other_id,'message':'foreign'}).status_code==404


def test_disabling_user_revokes_existing_session(client,monkeypatch):
    ident,_=setup_auth(client,monkeypatch);user=new_member(client,ident)
    owner_cookie=client.cookies.get('devflow_session')
    client.post('/api/auth/login',json={'username':'member','password':'testing-member-1234'})
    member_cookie=client.cookies.get('devflow_session')
    client.cookies.set('devflow_session',owner_cookie)
    assert client.post(f"/api/admin/users/{user['id']}/disable").status_code==200
    client.cookies.set('devflow_session',member_cookie)
    assert client.get('/api/repositories').status_code==401


def test_memory_requires_approval_and_pins_version_at_submission(client):
    ident=repo(client);root=f'/api/repositories/{ident}/memories'
    memory=client.post(root,json={'title':'项目约定','content':'代码词 zyxfixture 必须人工审核。'}).json()
    pending=client.post('/api/chat/runs',json={'repository_id':ident,'task':'knowledge','message':'zyxfixture'}).json()
    asyncio.run(execute_claim(claim_job(main.settings,'first'),main.settings))
    first=client.get(f"/api/repositories/{ident}/runs/{pending['run_id']}").json()
    assert not any(e['source']=='approved_memory' for e in first['result']['evidence'])
    approved=client.post(f"{root}/{memory['id']}/review",json={'version':1,'decision':'approve'}).json()
    second=client.post('/api/chat/runs',json={'repository_id':ident,'task':'knowledge','message':'zyxfixture'}).json()
    assert client.post(f"{root}/{memory['id']}/archive",json={'version':approved['version']}).status_code==200
    asyncio.run(execute_claim(claim_job(main.settings,'second'),main.settings))
    detail=client.get(f"/api/repositories/{ident}/runs/{second['run_id']}").json()
    evidence=next(e for e in detail['result']['evidence'] if e['source']=='approved_memory')
    assert evidence['id']==f"memory:{memory['id']}:v2" and 'zyxfixture' in evidence['content']
    third=run(client,'knowledge','zyxfixture')
    assert not any(e['source']=='approved_memory' for e in third['result']['evidence'])


def test_memory_edit_invalidates_approval_stale_versions_and_invalid_evidence(client):
    ident=repo(client);root=f'/api/repositories/{ident}/memories'
    assert client.post(root,json={'title':'fake','content':'x','evidence_ids':['invented']}).status_code==422
    memory=client.post(root,json={'title':'rule','content':'memory'}).json()
    client.post(f"{root}/{memory['id']}/review",json={'version':1,'decision':'approve'})
    assert client.post(f"{root}/{memory['id']}/edit",json={'version':1,'title':'old','content':'old'}).status_code==409
    edited=client.post(f"{root}/{memory['id']}/edit",json={'version':2,'title':'new','content':'new'}).json()
    assert edited['status']=='candidate' and edited['approved_by'] is None


def test_editor_cannot_approve_memory_and_cannot_forge_a_role_header(client,monkeypatch):
    ident,_=setup_auth(client,monkeypatch);new_member(client,ident,'editor')
    client.post('/api/auth/login',json={'username':'member','password':'testing-member-1234'})
    memory=client.post(f'/api/repositories/{ident}/memories',json={'title':'m','content':'m'}).json()
    assert client.post(f"/api/repositories/{ident}/memories/{memory['id']}/review",json={'version':1,'decision':'approve'},headers={'X-Role':'admin'}).status_code==403


def test_draft_versions_approval_edit_and_audit(client):
    ident=repo(client);did=draft(client);root=f'/api/repositories/{ident}/drafts/{did}'
    pending=client.post(root+'/submit',json={'version':1}).json()
    assert pending['status']=='pending' and pending['version']==2
    approved=client.post(root+'/review',json={'version':2,'decision':'approve','note':'已核对'}).json()
    assert approved['status']=='approved' and approved['version']==3
    assert client.post(root+'/review',json={'version':2,'decision':'approve'}).status_code==409
    edited=client.post(root+'/edit',json={'version':3,'body':'updated'}).json()
    assert edited['status']=='draft' and edited['approved_by'] is None
    assert client.post(root+'/publish',json={'version':4}).status_code==409
    actions=[a['action'] for a in client.get(f'/api/repositories/{ident}/audit').json()]
    assert all(x in actions for x in ('draft.create','draft.submit','draft.approve','draft.edit'))


def test_wrong_target_and_secret_content_are_rejected(client,monkeypatch):
    ident=repo(client);did=draft(client);root=f'/api/repositories/{ident}/drafts/{did}'
    client.post(root+'/edit',json={'version':1,'body':'body','target_kind':'pr','target_number':19})
    assert client.post(root+'/submit',json={'version':2}).status_code==409
    monkeypatch.setattr(main,'settings',main.settings.model_copy(update={'llm_api_key':SecretStr('fixture-do-not-store')}))
    assert client.post(root+'/edit',json={'version':2,'body':'fixture-do-not-store'}).status_code==422
    assert client.post(f'/api/repositories/{ident}/memories',json={'title':'x','content':'fixture-do-not-store'}).status_code==422


def live_approved(client,monkeypatch):
    ident=repo(client);did=draft(client)
    with SessionLocal() as db:
        d=db.get(ActionDraft,did);r=db.get(Repository,ident);r.snapshot={**r.snapshot,'source':'github'}
        from app.db import AgentRun
        db.get(AgentRun,d.run_id).mode='live'
        sha=next(e['sha'] for e in db.get(AgentRun,d.run_id).result['evidence'] if e['id']=='pr:18')
        review=DraftReview(draft_id=did,version=3,target_kind='pr',target_number=18,expected_sha=sha,approved_by='admin')
        db.add(review);db.flush();d.status='approved';review.approved_digest=draft_digest(d,review);db.commit()
    settings=main.settings.model_copy(update={'devflow_mode':'live','auth_enabled':True,'github_write_enabled':True,'github_token':SecretStr('fixture-token')})
    monkeypatch.setattr(main,'settings',settings)
    # Use a real authenticated session, not a caller-supplied role override.
    from app.security import password_hash
    with SessionLocal() as db:
        db.add(AppUser(username='owner',password_hash=password_hash('fixture-owner-123'),is_admin=True));db.commit()
    assert client.post('/api/auth/login',json={'username':'owner','password':'fixture-owner-123'}).status_code==200
    async def current(*args,**kwargs):return {'state':'open','head':{'sha':sha}}
    monkeypatch.setattr(GitHubClient,'get',current)
    return ident,did,sha


def test_publish_only_once_and_retry_returns_saved_comment(client,monkeypatch):
    ident,did,_=live_approved(client,monkeypatch);root=f'/api/repositories/{ident}/drafts/{did}'
    calls=[]
    async def find(*args):return None
    async def post(self,repo,number,text):
        calls.append(text);return {'id':901,'html_url':f'https://github.com/{repo}/pull/{number}#issuecomment-901'}
    monkeypatch.setattr(CommentClient,'find',find);monkeypatch.setattr(CommentClient,'post',post)
    result=client.post(root+'/publish',json={'version':3})
    assert result.status_code==200 and result.json()['status']=='published'
    assert '<!-- devflow:' in calls[0]
    assert client.post(root+'/publish',json={'version':3}).json()['status']=='published'
    assert len(calls)==1
    assert client.post(root+'/edit',json={'version':4,'body':'changed'}).status_code==409


def test_timeout_becomes_uncertain_and_reconcile_never_posts_again(client,monkeypatch):
    ident,did,_=live_approved(client,monkeypatch);root=f'/api/repositories/{ident}/drafts/{did}'
    comments=[]
    async def find(*args):return None
    async def post(self,repo,number,text):
        comments.append({'id':902,'body':text,'html_url':f'https://github.com/{repo}/pull/{number}#issuecomment-902'})
        raise httpx.ReadTimeout('lost response')
    monkeypatch.setattr(CommentClient,'find',find);monkeypatch.setattr(CommentClient,'post',post)
    assert client.post(root+'/publish',json={'version':3}).json()['status']=='uncertain'
    assert client.post(root+'/publish',json={'version':4}).status_code==409
    async def found(*args):return comments[0]
    monkeypatch.setattr(CommentClient,'find',found)
    assert client.post(root+'/reconcile').json()['status']=='published'
    assert len(comments)==1


def test_changed_pr_head_prevents_publish_before_post(client,monkeypatch):
    ident,did,_=live_approved(client,monkeypatch)
    async def changed(*args,**kwargs):return {'state':'open','head':{'sha':'c'*40}}
    async def forbidden(*args):raise AssertionError('must not publish')
    monkeypatch.setattr(GitHubClient,'get',changed);monkeypatch.setattr(CommentClient,'post',forbidden)
    assert client.post(f'/api/repositories/{ident}/drafts/{did}/publish',json={'version':3}).status_code==409


def test_concurrent_publish_claims_send_one_comment(client,monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    ident,did,_=live_approved(client,monkeypatch);calls=[];barrier=threading.Barrier(2)
    async def find(*args):await asyncio.sleep(0.05);return None
    async def post(self,repo,number,text):
        calls.append(text);return {'id':903,'html_url':f'https://github.com/{repo}/pull/{number}#issuecomment-903'}
    monkeypatch.setattr(CommentClient,'find',find);monkeypatch.setattr(CommentClient,'post',post)
    def send(_):
        barrier.wait(timeout=5)
        return client.post(f'/api/repositories/{ident}/drafts/{did}/publish',json={'version':3}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(send,(1,2)))
    assert all(status in (200,409) for status in results) and 200 in results
    assert len(calls)==1


def test_tampered_approved_body_is_rejected(client,monkeypatch):
    ident,did,_=live_approved(client,monkeypatch)
    with SessionLocal() as db:db.get(ActionDraft,did).body='unapproved body';db.commit()
    assert client.post(f'/api/repositories/{ident}/drafts/{did}/publish',json={'version':3}).status_code==409


def test_nested_memory_and_draft_ids_cannot_escape_repository(client):
    ident=repo(client);did=draft(client)
    memory=client.post(f'/api/repositories/{ident}/memories',json={'title':'m','content':'m'}).json()
    with SessionLocal() as db:
        other=Repository(full_name='demo/other',snapshot=db.get(Repository,ident).snapshot);db.add(other);db.commit();oid=other.id
    assert client.post(f"/api/repositories/{oid}/memories/{memory['id']}/review",json={'version':1,'decision':'approve'}).status_code==404
    assert client.post(f'/api/repositories/{oid}/drafts/{did}/edit',json={'version':1,'body':'other'}).status_code==404


def test_comment_adapter_paginates_and_rejects_ambiguous_or_modified_markers():
    async def scenario():
        from app.config import Settings
        client=CommentClient(Settings(github_token='fixture'))
        pages=[]
        def respond(request):
            pages.append(request.url.params['page'])
            return httpx.Response(200,json=[{'id':n,'body':'unrelated'} for n in range(100)] if len(pages)==1 else [{'id':101,'body':'approved marker'}])
        await client.client.aclose()
        client.client=httpx.AsyncClient(base_url='https://api.github.com',transport=httpx.MockTransport(respond))
        assert (await client.find('a/b',1,'marker','approved marker'))['id']==101
        assert pages==['1','2']
        def altered(request):return httpx.Response(200,json=[{'id':1,'body':'modified marker'}])
        await client.client.aclose();client.client=httpx.AsyncClient(base_url='https://api.github.com',transport=httpx.MockTransport(altered))
        import pytest
        with pytest.raises(ValueError):await client.find('a/b',1,'marker','approved marker')
        await client.close()
    asyncio.run(scenario())


def test_memory_search_excludes_unapproved_edited_and_archived_versions(client):
    root=f'/api/repositories/{repo(client)}/memories'
    memory=client.post(root,json={'title':'索引约定','content':'uniquefixture 使用独立 Worker'}).json()
    def search():return client.post(root+'/search',json={'query':'uniquefixture'}).json()['results']
    assert search()==[]
    client.post(f"{root}/{memory['id']}/review",json={'version':1,'decision':'approve'})
    assert search()[0]['id']==f"memory:{memory['id']}:v2"
    client.post(f"{root}/{memory['id']}/edit",json={'version':2,'title':'索引约定','content':'uniquefixture 新约定'})
    assert search()==[]
    client.post(f"{root}/{memory['id']}/review",json={'version':3,'decision':'approve'})
    assert search()[0]['revision']=='4'
    client.post(f"{root}/{memory['id']}/archive",json={'version':4})
    assert search()==[]


def test_expired_publish_claim_becomes_uncertain_with_audit(client,monkeypatch):
    ident,did,_=live_approved(client,monkeypatch)
    with SessionLocal() as db:
        db.get(ActionDraft,did).status='publishing'
        db.add(PublishAttempt(draft_id=did,token='a'*32,digest='b'*64,lease_until=utcnow()-timedelta(seconds=1)))
        db.commit();recover_publications(db)
        assert db.get(ActionDraft,did).status=='uncertain'
        assert db.get(PublishAttempt,did).lease_until is None
        assert db.scalar(select(AuditRecord).where(AuditRecord.object_id==did,AuditRecord.action=='draft.uncertain')).actor=='系统恢复'
        recover_publications(db)
        assert len(list(db.scalars(select(AuditRecord).where(AuditRecord.object_id==did,AuditRecord.action=='draft.uncertain'))))==1


def test_reconcile_rejects_foreign_comment_url_without_reposting(client,monkeypatch):
    ident,did,_=live_approved(client,monkeypatch);root=f'/api/repositories/{ident}/drafts/{did}'
    async def missing(*args):return None
    async def timeout(*args):raise httpx.ReadTimeout('lost response')
    monkeypatch.setattr(CommentClient,'find',missing);monkeypatch.setattr(CommentClient,'post',timeout)
    assert client.post(root+'/publish',json={'version':3}).json()['status']=='uncertain'
    async def invalid(*args):return {'id':902,'html_url':'https://untrusted.example/comment'}
    async def forbidden(*args):raise AssertionError('reconcile must not POST')
    monkeypatch.setattr(CommentClient,'find',invalid);monkeypatch.setattr(CommentClient,'post',forbidden)
    assert client.post(root+'/reconcile').status_code==502
    with SessionLocal() as db:assert db.get(ActionDraft,did).status=='uncertain'
