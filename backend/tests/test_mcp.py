import asyncio
import os
from pathlib import Path
import socket
import subprocess
import sys
import httpx
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app import main
from app.db import Base, ProjectMemory, Repository, SessionLocal
from app.demo import demo_snapshot

TOKEN='fixture-mcp-access-token-'+'a'*32


def test_mcp_bearer_scope_and_read_whitelist_cannot_bypass_local_mode(client,monkeypatch):
    ident=client.get('/api/repositories').json()[0]['id']
    monkeypatch.setattr(main,'settings',main.settings.model_copy(update={'mcp_access_token':SecretStr(TOKEN),'mcp_repository_ids':ident}))
    headers={'Authorization':'Bearer '+TOKEN}
    assert client.get('/api/repositories',headers=headers).json()[0]['id']==ident
    assert client.get(f'/api/repositories/{ident}/snapshot',headers=headers).status_code==200
    assert client.post(f'/api/repositories/{ident}/knowledge/search',json={'query':'tenant'},headers=headers).status_code==200
    assert client.post('/api/chat/runs',json={'repository_id':ident,'message':'do not run'},headers=headers).status_code==403
    assert client.get(f'/api/repositories/{ident}/drafts',headers=headers).status_code==403
    assert client.get(f'/api/repositories/{ident}/memories',headers=headers).status_code==403
    assert client.get('/api/admin/users',headers=headers).status_code==403
    assert client.get('/api/repositories/'+'f'*32+'/snapshot',headers=headers).status_code==404
    assert client.get('/api/repositories',headers={'Authorization':'Bearer wrong'}).status_code==401


def test_actual_stdio_mcp_handshake_search_and_scope_against_isolated_api(client,tmp_path):
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    ident='1'*32
    database='sqlite:///'+(tmp_path/'mcp.db').as_posix()
    isolated=create_engine(database)
    Base.metadata.create_all(isolated)
    with Session(isolated) as db:
        db.add(Repository(id=ident,full_name='demo/devflow-shop',snapshot=demo_snapshot()))
        db.add(ProjectMemory(id='2'*32,repository_id=ident,title='approved',content='fixtureapproved knowledge',status='approved',version=2,author='fixture',approved_by='fixture'))
        db.add(ProjectMemory(id='3'*32,repository_id=ident,title='candidate',content='fixturecandidate secret',status='candidate',author='fixture'))
        db.commit()
    isolated.dispose()
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env={**os.environ,'DATABASE_URL':database,'DEVFLOW_MODE':'demo','AUTH_ENABLED':'true',
         'BOOTSTRAP_ADMIN_PASSWORD':'fixture-password-123','MCP_ACCESS_TOKEN':TOKEN,'MCP_REPOSITORY_IDS':ident,
         'DEVFLOW_MCP_API_PORT':str(port),'LLM_API_KEY':'','GITHUB_TOKEN':'','RERANK_ENABLED':'false','PYTHONUTF8':'1'}
    backend=str(Path(__file__).resolve().parents[1])
    api=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(port),'--no-access-log'],cwd=backend,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    async def scenario():
        async with httpx.AsyncClient(trust_env=False) as http:
            for _ in range(60):
                if api.poll() is not None:raise AssertionError('isolated API exited')
                try:
                    if (await http.get(f'http://127.0.0.1:{port}/api/health',timeout=.2)).is_success:break
                except httpx.HTTPError:pass
                await asyncio.sleep(.1)
            else:raise AssertionError('isolated API did not start')
        server=StdioServerParameters(command=sys.executable,args=['-m','app.mcp_server'],cwd=backend,env=env)
        async with stdio_client(server) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                tools=await session.list_tools()
                assert len(tools.tools)==6 and all(t.annotations.readOnlyHint for t in tools.tools)
                result=await session.call_tool('repository_snapshot',{'repository_id':ident})
                assert not result.isError and result.structuredContent['source']=='demo'
                memory=await session.call_tool('search_approved_memories',{'repository_id':ident,'query':'fixtureapproved'})
                assert not memory.isError and memory.structuredContent['results'][0]['id']=='memory:'+'2'*32+':v2'
                candidate=await session.call_tool('search_approved_memories',{'repository_id':ident,'query':'fixturecandidate'})
                assert candidate.structuredContent['results']==[]
                rejected=await session.call_tool('repository_snapshot',{'repository_id':'f'*32})
                assert rejected.isError
    try:asyncio.run(scenario())
    finally:
        api.terminate()
        try:api.wait(timeout=10)
        except subprocess.TimeoutExpired:api.kill();api.wait(timeout=5)
