import json
import asyncio
from time import perf_counter

import httpx

from .config import Settings
from .schemas import Analysis
from .telemetry import begin_call, end_call
from .fact_review import apply_review, extract_source
from .prompts import provenance, fingerprint, ANSWER_FIELDS


class ModelClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def chat(self, messages: list[dict], tools: list[dict] | None = None, structured=False):
        if not self.settings.llm_api_key.get_secret_value() or not self.settings.llm_model:
            raise ValueError("真实模式需要在 backend/.env 配置 LLM_API_KEY 和 LLM_MODEL。")
        payload = {"model": self.settings.llm_model, "messages": messages, "temperature": 0.2, "max_tokens": self.settings.llm_max_tokens}
        if self.settings.llm_reasoning_effort is not None:
            payload["reasoning_effort"] = self.settings.llm_reasoning_effort
        if tools:
            payload["tools"] = tools
        if structured:
            payload["response_format"] = {"type": "json_object"}
        call_id, started, usage, status = begin_call(self.settings), perf_counter(), None, "failed"
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                try:
                    response = await client.post(self.settings.llm_base_url.rstrip("/") + "/chat/completions", headers={"Authorization": f"Bearer {self.settings.llm_api_key.get_secret_value()}"}, json=payload)
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise RuntimeError(f"模型请求失败（HTTP {exc.response.status_code}），请核对模型名称、密钥和接口能力。") from exc
                except httpx.HTTPError as exc:
                    raise RuntimeError("模型网络连接失败或请求超时。") from exc
                data = response.json()
                usage = data.get("usage")
                if not data.get("choices"):
                    raise RuntimeError("模型没有返回有效 choices。")
                if data["choices"][0].get("finish_reason") == "length":
                    raise RuntimeError("模型输出达到长度上限，请缩小问题范围或调整 LLM_MAX_TOKENS。")
                status = "completed"
                return data["choices"][0]["message"]
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        finally:
            end_call(call_id, status, int((perf_counter()-started)*1000), usage)

    async def analyze(self, role: str, question: str, evidence: dict, gaps: list[str] | None = None):
        facts, relations, limited = extract_source(evidence)
        user_input = {"question": question, "evidence": evidence, "known_gaps": gaps or [], "source_numeric_facts": facts, "source_formula_relations": relations, "source_facts_limited": limited}
        context = provenance(self.settings, role, user_input, {})
        message = await self.chat([
            {"role": "system", "content": context["system_prompt"]},
            {"role": "user", "content": json.dumps(user_input, ensure_ascii=False)},
        ], structured=True)
        analysis = Analysis.model_validate_json(message.get("content") or "")
        allowed = set(evidence)
        for finding in analysis.findings:
            bad = set(finding.evidence_ids) - allowed
            if bad:
                raise ValueError("模型引用了未提供的证据，已停止输出。")
        analysis.gaps = list(dict.fromkeys(analysis.gaps + (gaps or [])))
        answer = analysis.model_dump()
        context["answer_hash"] = fingerprint({key: answer.get(key) for key in ANSWER_FIELDS})
        context["saved_answer_hash"] = fingerprint({key: answer.get(key) for key in (*ANSWER_FIELDS, "gaps")})
        answer["analysis_context"] = context
        return apply_review(answer, evidence)
