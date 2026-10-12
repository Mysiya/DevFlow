"""Version compatibility and actual new-prompt/citation binding; no model judge."""
import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.db import RunJob, SessionLocal
from app.llm import ModelClient
from app.prompts import bound_settings, freeze_binding, system_prompt, template_hash
from app.task_skills import skill_binding

LEGACY_HASHES = {
    'baseline-v1': '21a3eb84883add00d0452144e195de432f9e24e3cf8312cde7a5d9c098f606b7',
    'evidence-first-v2': '956e0d01876db7893add45b6aae6d6b877ab14e969e76bc70dbd715627a8bd8c',
}


@pytest.mark.parametrize('ident', LEGACY_HASHES)
def test_saved_old_bindings_remain_resolvable_after_new_prompt(ident):
    settings = Settings(_env_file=None, analysis_prompt_id='evidence-boundaries-v3')
    frozen = freeze_binding(settings.model_copy(update={'analysis_prompt_id': ident}))
    frozen['template_hash'] = LEGACY_HASHES[ident]
    assert template_hash(ident) == LEGACY_HASHES[ident]
    restored = bound_settings(settings, {'analysis_binding': frozen})
    assert restored.analysis_prompt_id == ident and freeze_binding(restored) == frozen


def test_queue_binds_new_prompt_and_original_skill_without_changing_legacy_api_default(client):
    repo = client.get('/api/repositories').json()[0]['id']
    requests = [
        {'prompt_id': 'evidence-boundaries-v3', 'skill_id': 'pr-review-v1', 'task': 'pr', 'target': 18},
        {'task': 'code'},
    ]
    bindings = []
    for body in requests:
        response = client.post('/api/chat/runs', json={'repository_id': repo, 'message': '只读核对证据边界', **body})
        assert response.status_code == 202
        with SessionLocal() as db:
            bindings.append(db.get(RunJob, response.json()['run_id']).payload['input']['analysis_binding'])
    assert bindings[0]['prompt_id'] == 'evidence-boundaries-v3'
    assert bindings[0]['skill'] == skill_binding('pr-review-v1')
    assert bindings[1]['prompt_id'] == 'baseline-v1'


def test_new_prompt_is_sent_and_recorded_with_same_evidence_protocol(monkeypatch):
    captured = []
    evidence = {'ci:42': {'id': 'ci:42', 'title': '固定 CI', 'content': 'Backend regression: success',
                          'sha': 'a' * 40, 'url': '', 'source': 'github'}}
    answer = {'title': 'CI 状态', 'summary': '已有 job 成功，异常分支仍待验证。',
              'recommendation': '建议核对失败样本', 'findings': [
                  {'severity': 'info', 'title': '已观察到检查成功', 'detail': '所给 job 状态为 success。', 'evidence_ids': ['E1']}],
              'next_steps': [], 'gaps': ['未提供逐项测试输出。']}

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(answer)}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    results = []
    for ident in ('baseline-v1', 'evidence-boundaries-v3'):
        settings = Settings(_env_file=None, devflow_mode='live', llm_model='fixture',
                            llm_api_key=SecretStr('fixture-only'), analysis_prompt_id=ident, analysis_skill_id='pr-review-v1')
        result = asyncio.run(ModelClient(settings).analyze('pr', '检查 CI 证据边界', evidence))
        context = result['analysis_context']
        assert context['system_prompt'] == captured[-1]['messages'][0]['content'] == system_prompt(ident, 'pr', 'pr-review-v1')
        assert context['skill'] == skill_binding('pr-review-v1')
        assert result['findings'][0]['evidence_ids'] == ['ci:42']
        results.append(context)
    assert captured[0]['messages'][1] == captured[1]['messages'][1]
    assert results[0]['input_hash'] == results[1]['input_hash']
    assert results[0]['protocol_hash'] == results[1]['protocol_hash']
    assert results[0]['template_hash'] != results[1]['template_hash']
    assert results[0]['system_hash'] != results[1]['system_hash']
