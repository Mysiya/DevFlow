import asyncio
import base64

import httpx
import pytest

from app.config import Settings
from app.github import GitHubClient, GitHubError


def test_etag_snapshot_readme_uses_commit_and_reuses_304():
    seen, sha = [], "a" * 40
    def handler(request):
        seen.append(request)
        if request.headers.get("if-none-match"):
            return httpx.Response(304)
        path = request.url.path
        if path.endswith("/branches/main"):
            data = {"commit": {"sha": sha}}
        elif path.endswith("/readme"):
            assert request.url.params["ref"] == sha
            data = {"path": "README.md", "content": base64.b64encode(b"# Guide\nsetup").decode()}
        elif path.endswith("/actions/runs"):
            data = {"workflow_runs": []}
        elif path.endswith("/issues") or path.endswith("/pulls"):
            data = []
        else:
            data = {"full_name": "owner/repo", "default_branch": "main", "size": 1}
        return httpx.Response(200, json=data, headers={"etag": '"test-etag"'})
    async def scenario():
        github = GitHubClient(Settings())
        await github.client.aclose()
        github.client = httpx.AsyncClient(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
        try:
            first = await github.snapshot("owner/repo")
            second = await github.snapshot("owner/repo")
            assert second["sync_info"]["etag_cache_hits"] == 6
            assert first["documents"] == second["documents"]
            doc = second["documents"][0]
            assert doc["revision"] == sha and doc["url"] == f"https://github.com/owner/repo/blob/{sha}/README.md"
            assert doc["content"] == "# Guide\nsetup"
            assert all(x.method == "GET" for x in seen)
        finally:
            await github.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('scopes,paths',[
    (['issues'],['','/issues']),(['issues','pulls'],['','/issues','/pulls']),(['runs'],['','/actions/runs']),
    (['documents','repository'],['','/branches/main','/readme']),
])
def test_event_refresh_only_reads_selected_sections_and_preserves_the_rest(scopes,paths):
    previous={'name':'owner/repo','source':'github','description':'old','branch':'main','head_sha':'a'*40,'github_repository_id':123,
              'issues':[{'number':1,'title':'old issue'}],'pulls':[{'number':2,'title':'old PR'}],'runs':[{'id':3}],'documents':[{'id':'doc:readme','content':'old guide'}]}
    seen=[]
    def handler(request):
        path=request.url.path.removeprefix('/repos/owner/repo');seen.append(path)
        if path=='':data={'id':123,'full_name':'owner/repo','default_branch':'main','size':1}
        elif path=='/branches/main':data={'commit':{'sha':'b'*40}}
        elif path=='/readme':
            assert request.url.params['ref']=='b'*40
            data={'path':'README.md','content':base64.b64encode(b'new guide').decode()}
        elif path=='/actions/runs':data={'workflow_runs':[]}
        else:data=[]
        return httpx.Response(200,json=data,headers={'etag':'"changed"'})
    async def check():
        github=GitHubClient(Settings());await github.client.aclose()
        github.client=httpx.AsyncClient(base_url='https://api.github.com',transport=httpx.MockTransport(handler))
        try:
            result=await github.snapshot('owner/repo',previous,scopes)
            assert sorted(seen)==sorted(paths) and result['sync_info']['strategy']=='event-scoped'
            for section in {'issues','pulls','runs','documents'}-set(scopes):assert result[section]==previous[section]
            if 'repository' not in scopes:assert result['head_sha']==previous['head_sha']
            else:assert result['head_sha']=='b'*40 and result['documents'][0]['revision']=='b'*40
            assert previous['issues'][0]['title']=='old issue' and previous['head_sha']=='a'*40
        finally:await github.close()
    asyncio.run(check())


@pytest.mark.parametrize('scopes',[['repository','documents'],['issues']])
def test_changed_remote_repository_id_does_not_replace_the_old_binding(scopes):
    async def check():
        github=GitHubClient(Settings());await github.client.aclose()
        github.client=httpx.AsyncClient(base_url='https://api.github.com',transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'id':456,'full_name':'owner/repo','default_branch':'main'})))
        try:
            with pytest.raises(GitHubError,match='ID 已变化'):await github.snapshot('owner/repo',{'github_repository_id':123},scopes)
        finally:await github.close()
    asyncio.run(check())


@pytest.mark.parametrize("size", [0, 10])
def test_empty_branch_is_distinct_from_failed_nonempty_sync(size):
    def handler(request):
        path = request.url.path
        if path.endswith("/branches/main"):
            return httpx.Response(404)
        if path.endswith("/actions/runs"):
            data = {"workflow_runs": []}
        elif path.endswith("/issues") or path.endswith("/pulls"):
            data = []
        else:
            data = {"full_name": "owner/repo", "default_branch": "main", "size": size}
        return httpx.Response(200, json=data)
    async def scenario():
        github = GitHubClient(Settings())
        await github.client.aclose()
        github.client = httpx.AsyncClient(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
        try:
            if size:
                with pytest.raises(GitHubError, match="停止同步"):
                    await github.snapshot("owner/repo")
            else:
                snapshot = await github.snapshot("owner/repo")
                assert snapshot["sync_info"]["empty_repository"] and snapshot["documents"] == []
        finally:
            await github.close()
    asyncio.run(scenario())
