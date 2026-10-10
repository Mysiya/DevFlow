import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.llm import ModelClient


@pytest.mark.parametrize("finish", ["stop", "length"])
def test_deepseek_payload_is_bounded_and_truncation_is_rejected(monkeypatch, finish):
    def handler(request):
        payload = json.loads(request.content)
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert payload["model"] == "deepseek-v4-pro"
        assert payload["thinking"] == {"type": "disabled"} and payload["max_tokens"] == 4096
        assert "reasoning_effort" not in payload
        assert payload["response_format"] == {"type": "json_object"}
        return httpx.Response(200, json={"choices": [{"finish_reason": finish, "message": {"role": "assistant", "content": "{}"}}]})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    settings = Settings(llm_base_url="https://api.deepseek.com", llm_api_key="test", llm_model="deepseek-v4-pro", llm_reasoning_effort="none")
    if finish == "length":
        with pytest.raises(RuntimeError, match="长度上限"):
            asyncio.run(ModelClient(settings).chat([{"role": "user", "content": "JSON please"}], structured=True))
    else:
        assert asyncio.run(ModelClient(settings).chat([{"role": "user", "content": "JSON please"}], structured=True))["content"] == "{}"


@pytest.mark.parametrize("base,effort,thinking,forwarded", [
    ("https://api.deepseek.com/v1", "low", "enabled", "low"),
    ("https://api.deepseek.com", "high", "enabled", "high"),
    ("https://api.deepseek.com", "max", "enabled", "max"),
    ("https://api.deepseek.com", None, None, None),
    ("https://api.other-provider.example/v1", "none", None, "none"),
    ("https://api.deepseek.com.other-provider.example/v1", "none", None, "none"),
])
def test_provider_specific_thinking_controls(monkeypatch, base, effort, thinking, forwarded):
    def handler(request):
        payload=json.loads(request.content)
        if thinking is None: assert "thinking" not in payload
        else: assert payload["thinking"] == {"type": thinking}
        if forwarded is None: assert "reasoning_effort" not in payload
        else: assert payload["reasoning_effort"] == forwarded
        return httpx.Response(200,json={"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"ok"}}]})
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    settings=Settings(llm_base_url=base,llm_api_key="test",llm_model="deepseek-v4-pro",llm_reasoning_effort=effort)
    assert asyncio.run(ModelClient(settings).chat([{"role":"user","content":"test"}]))["content"]=="ok"


@pytest.mark.parametrize("returned,valid", [("E1",True),("source:long-identity",True),("E999",False),("file.py:1:2",False)])
def test_citations_map_only_to_supplied_sources(monkeypatch, returned, valid):
    evidence={"source:long-identity":{"id":"source:long-identity","content":"A verified source excerpt","path":"file.py"}}
    async def chat(self,messages,**kwargs):
        wire=json.loads(messages[1]["content"])
        assert wire["allowed_evidence_ids"]==["E1"]
        assert wire["evidence"]["E1"]["id"]=="E1"
        assert wire["evidence"]["E1"]["source_id"]=="source:long-identity"
        return {"content":json.dumps({"title":"Result","summary":"Source scope","recommendation":"Review",
          "findings":[{"severity":"info","title":"Source","detail":"Excerpt","evidence_ids":[returned]}],"next_steps":[],"gaps":[]})}
    monkeypatch.setattr(ModelClient,"chat",chat)
    client=ModelClient(Settings(_env_file=None))
    if not valid:
        with pytest.raises(ValueError,match="未提供的证据"):asyncio.run(client.analyze("code","Explain",evidence))
        return
    answer=asyncio.run(client.analyze("code","Explain",evidence))
    assert answer["findings"][0]["evidence_ids"]==["source:long-identity"]
    assert answer["analysis_context"]["evidence_ids"]==["source:long-identity"]
    assert answer["analysis_context"]["evidence_aliases"]=={"E1":"source:long-identity"}
    assert evidence["source:long-identity"]["id"]=="source:long-identity"
