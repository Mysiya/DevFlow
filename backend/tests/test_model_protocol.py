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
