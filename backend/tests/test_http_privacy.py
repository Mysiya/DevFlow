import asyncio
import pytest

from app.http_privacy import PrivateAPIResponses


@pytest.mark.parametrize("path,status", [("/api/auth/session", 200), ("/api/repositories", 401),
                                       ("/api/chat/runs", 422), ("/api", 404)])
def test_private_success_and_error_responses_replace_public_cache_headers(path, status):
    received = []

    async def endpoint(scope, receive, send):
        await send({"type":"http.response.start", "status":status, "headers":[
            (b"Cache-Control",b"public, max-age=86400"), (b"pragma",b"public"),
            (b"expires",b"tomorrow"), (b"set-cookie",b"session=test; Secure; HttpOnly"),
            (b"access-control-allow-origin",b"https://devflow.example.test")]})
        await send({"type":"http.response.body", "body":b'private body'})

    async def record(message): received.append(message)
    asyncio.run(PrivateAPIResponses(endpoint)({"type":"http", "path":path}, None, record))
    start = received[0]
    assert start["status"] == status
    assert len([key for key,value in start["headers"] if key.lower()==b"cache-control"])==1
    headers = dict(start["headers"])
    assert headers[b"cache-control"]==b"private, no-store, max-age=0"
    assert headers[b"pragma"]==b"no-cache" and headers[b"expires"]==b"0"
    assert headers[b"set-cookie"]==b"session=test; Secure; HttpOnly"
    assert headers[b"access-control-allow-origin"]==b"https://devflow.example.test"
    assert received[1]["body"]==b'private body'


def test_sse_events_are_forwarded_immediately_without_body_changes():
    sent=[]
    events=[{"type":"http.response.body","body":b'id: 1\ndata: first\n\n',"more_body":True},
            {"type":"http.response.body","body":b'id: 2\ndata: complete\n\n',"more_body":False}]

    async def endpoint(scope, receive, send):
        await send({"type":"http.response.start","status":200,"headers":[(b"content-type",b"text/event-stream")]})
        for event in events:
            await send(event)
            assert sent[-1] is event  # Each event reaches the consumer before the next one exists.

    async def record(message): sent.append(message)
    asyncio.run(PrivateAPIResponses(endpoint)({"type":"http","path":"/api/repositories/repo/runs/run/events"},None,record))
    assert sent[1:]==events
    assert dict(sent[0]["headers"])[b"content-type"]==b"text/event-stream"


def test_non_api_public_responses_keep_their_cache_policy():
    messages=[{"type":"http.response.start","status":200,"headers":[(b"cache-control",b"public, max-age=3600")]},
              {"type":"http.response.body","body":b'public asset'}]
    received=[]
    async def endpoint(scope, receive, send):
        for message in messages: await send(message)
    async def record(message): received.append(message)
    asyncio.run(PrivateAPIResponses(endpoint)({"type":"http","path":"/apiculture/icon.png"},None,record))
    assert received==messages and all(a is b for a,b in zip(received,messages))


def test_real_api_and_cors_preflight_have_private_cache_policy(client):
    for response in [client.get('/api/auth/session'), client.get('/api/does-not-exist'),
                     client.post('/api/chat/runs',json={}),
                     client.options('/api/auth/session',headers={'Origin':'http://127.0.0.1:3000',
                              'Access-Control-Request-Method':'POST'})]:
        assert 'no-store' in response.headers['cache-control']
        assert 'private' in response.headers['cache-control']
