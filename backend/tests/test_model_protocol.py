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
        assert payload["reasoning_effort"] == "none" and payload["max_tokens"] == 4096
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
