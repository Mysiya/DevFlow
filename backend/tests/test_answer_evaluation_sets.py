import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app import answer_evaluation_sets, main
from app.db import AgentRun, AnswerEvaluationSet, AnswerReview, AuditRecord, Conversation, ModelCall, PromptExperiment, Repository, RunJob, SessionLocal, new_id
from app.llm import ModelClient
from app.prompts import fingerprint
from tests.test_answer_comparisons import pair


def fixture_group(client, monkeypatch):
    root, ids, _ = pair(client, monkeypatch)
    repo = root.rsplit('/', 1)[1]
    with SessionLocal() as db:
        run = db.get(AgentRun, ids[0])
        experiment = PromptExperiment(repository_id=repo, request_id=new_id(), request_hash=fingerprint(run.question),
                                      question=run.question, task=run.task, left_run_id=ids[0], right_run_id=ids[1],
                                      frozen_hash=fingerprint({'fixture': ids}), sources={},
                                      parameters=run.result['analysis_context']['model_parameters'], prompt_bindings=[], created_by='fixture')
        db.add(experiment); db.commit()
        ident = experiment.id
    return root, ident, ids


def body(*ids):
    return {'request_id': new_id(), 'name': ' 固定租约样本 ', 'note': ' 单一场景，尚无代表性。 ', 'experiment_ids': list(ids)}


def create(client, root, payload):
    response = client.post(root + '/answer-evaluation-sets', json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_freezes_answers_idempotent_creation_and_no_new_execution(client, monkeypatch):
    root, ident, ids = fixture_group(client, monkeypatch)
    async def forbidden(*args, **kwargs):
        raise AssertionError('Dataset actions must never call a model')
    monkeypatch.setattr(ModelClient, 'chat', forbidden)
    with SessionLocal() as db:
        original = {i: copy.deepcopy({'result': db.get(AgentRun, i).result, 'events': db.get(AgentRun, i).events}) for i in ids}
    payload = body(ident)
    saved = create(client, root, payload)
    assert saved['name'] == '固定租约样本' and saved['note'] == payload['note'].strip()
    assert create(client, root, payload) == saved
    assert client.post(root + '/answer-evaluation-sets', json={**payload, 'name': '另一个集合'}).status_code == 409
    path = root + '/answer-evaluation-sets/' + saved['id']
    detail = client.get(path).json()
    assert detail['coverage']['eligible_pairs'] == 1 and detail['coverage']['reviewed'] == 0
    assert detail['coverage']['fields'] == detail['coverage']['unreviewed'] == 8
    assert detail['model_accuracy'] is detail['winner'] is None and not detail['human_labels_generated']
    exported = client.get(path + '/export')
    assert exported.json() == detail and 'attachment' in exported.headers['content-disposition']
    assert client.get(root + '/answer-evaluation-sets').json()['sets'] == [saved]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AnswerEvaluationSet)) == 1
        assert db.scalar(select(func.count()).select_from(AuditRecord).where(AuditRecord.action == 'answer_evaluation_set.create')) == 1
        assert not list(db.scalars(select(AnswerReview))) and not list(db.scalars(select(RunJob))) and not list(db.scalars(select(ModelCall)))
        assert {i: {'result': db.get(AgentRun, i).result, 'events': db.get(AgentRun, i).events} for i in ids} == original


def test_new_reviews_update_coverage_without_changing_frozen_samples(client, monkeypatch):
    root, ident, ids = fixture_group(client, monkeypatch)
    saved = create(client, root, body(ident))
    path = root + '/answer-evaluation-sets/' + saved['id']
    before = client.get(path).json()
    review_path = root + f'/runs/{ids[0]}/answer-reviews'
    snapshot = client.get(review_path).json()
    result = client.post(review_path, json={'snapshot_hash': snapshot['snapshot_hash'], 'expected_version': 0,
        'annotations': [{'item_id': 'summary', 'decision': 'supported', 'evidence_ids': ['doc:one'], 'note': '隔离测试：来源直接要求保留租约。'}]})
    assert result.status_code == 201
    after = client.get(path).json()
    assert after['fingerprint'] == before['fingerprint'] and after['pairs'][0]['comparison'] == before['pairs'][0]['comparison']
    assert after['pairs'][0]['current_reviews']['left']['version'] == 1 and after['coverage']['reviewed'] == 1
    assert after['coverage']['fully_reviewed_pairs'] == 0
    assert sum(g['counts']['supported'] for g in after['coverage']['by_prompt']) == 1
    for i in ids:
        target = root + f'/runs/{i}/answer-reviews'; snapshot = client.get(target).json()
        response = client.post(target, json={'snapshot_hash': snapshot['snapshot_hash'], 'expected_version': snapshot['latest_version'],
            'annotations': [{'item_id': item['id'], 'decision': 'not_applicable', 'evidence_ids': [], 'note': '隔离测试标签，不表示事实正确。'} for item in snapshot['snapshot']['items']]})
        assert response.status_code == 201
    finished = client.get(path).json()
    assert finished['coverage']['fully_reviewed_pairs'] == 1 and finished['coverage']['unreviewed'] == 0
    assert all(g['counts']['supported'] == 0 for g in finished['coverage']['by_prompt'])
    assert finished['model_accuracy'] is finished['winner'] is None


def test_stale_review_is_excluded_without_invalidating_unchanged_pair(client, monkeypatch):
    root, ident, ids = fixture_group(client, monkeypatch)
    saved = create(client, root, body(ident))
    target = root + f'/runs/{ids[0]}/answer-reviews'; snapshot = client.get(target).json()
    result = client.post(target, json={'snapshot_hash': snapshot['snapshot_hash'], 'expected_version': 0,
        'annotations': [{'item_id': 'summary', 'decision': 'supported', 'evidence_ids': ['doc:one'], 'note': '隔离测试标签。'}]})
    assert result.status_code == 201
    with SessionLocal() as db:
        db.get(AnswerReview, result.json()['id']).snapshot_hash = '0' * 64; db.commit()
    detail = client.get(root + '/answer-evaluation-sets/' + saved['id']).json()
    assert detail['coverage']['eligible_pairs'] == 1 and detail['coverage']['reviewed'] == 0
    assert detail['pairs'][0]['coverage']['left']['stale_review'] and detail['pairs'][0]['current_reviews']['left'] is None


def test_model_parameter_groups_are_not_combined(client, monkeypatch):
    root, first, _ = fixture_group(client, monkeypatch)
    _, second, ids = fixture_group(client, monkeypatch)
    with SessionLocal() as db:
        for ident in ids:
            run = db.get(AgentRun, ident); value = copy.deepcopy(run.result)
            parameters = value['analysis_context']['model_parameters']
            parameters['model'] = 'different-fixture-model'
            value['analysis_context']['model_config_hash'] = fingerprint(parameters)
            run.result = value
        db.commit()
    saved = create(client, root, body(first, second))
    groups = client.get(root + '/answer-evaluation-sets/' + saved['id']).json()['coverage']['by_prompt']
    assert len(groups) == 4 and all(g['answers'] == 1 for g in groups)
    assert {g['model_parameters']['model'] for g in groups} == {'fixture-model', 'different-fixture-model'}


@pytest.mark.parametrize('mutation', ['source', 'answer', 'missing'])
def test_changed_source_answer_or_missing_run_excludes_labels_keeps_frozen_text(client, monkeypatch, mutation):
    root, ident, ids = fixture_group(client, monkeypatch)
    saved = create(client, root, body(ident)); path = root + '/answer-evaluation-sets/' + saved['id']
    before = client.get(path).json()
    with SessionLocal() as db:
        run = db.get(AgentRun, ids[0])
        if mutation == 'missing':
            run.repository_id = 'another-repository'
        else:
            value = copy.deepcopy(run.result)
            if mutation == 'source': value['evidence'][0]['content'] = '来源已经变化'
            else: value['summary'] = '新回答'
            run.result = value
        db.commit()
    data = client.get(path).json()
    assert data['pairs'][0]['comparison'] == before['pairs'][0]['comparison'] and data['pairs'][0]['reasons']
    assert not data['pairs'][0]['eligible'] and data['pairs'][0]['current_reviews'] == {'left': None, 'right': None}
    assert data['coverage']['eligible_pairs'] == 0 and data['coverage']['excluded_pairs'] == 1
    assert data['coverage']['fields'] == data['coverage']['reviewed'] == 0 and data['coverage']['excluded_fields'] == 8
    assert data['coverage']['by_prompt'] == [] and data['winner'] is None


def test_old_group_survives_recent_windows_and_duplicate_input_is_visible(client, monkeypatch):
    root, ident, ids = fixture_group(client, monkeypatch)
    _, second, _ = fixture_group(client, monkeypatch)
    saved = create(client, root, body(ident, second))
    assert saved['pair_count'] == 2 and saved['unique_inputs'] == 1
    with SessionLocal() as db:
        original = db.get(AgentRun, ids[0])
        for _ in range(35):
            db.add(AgentRun(repository_id=original.repository_id, conversation_id=original.conversation_id,
                           question='newer fixture', task='knowledge', status='completed', mode='demo',
                           result={'title': 'fixture', 'summary': 'fixture', 'recommendation': 'fixture', 'evidence': []}))
        db.commit()
    assert not set(ids) & {r['id'] for r in client.get(root + '/answer-reviews/status').json()['runs']}
    detail = client.get(root + '/answer-evaluation-sets/' + saved['id']).json()
    assert detail['coverage']['eligible_pairs'] == 2
    assert client.get(root + f'/runs/{ids[0]}/answer-reviews').status_code == 200
    assert sorted(g['answers'] for g in detail['coverage']['by_prompt']) == [2, 2]


@pytest.mark.parametrize('same', [True, False])
def test_concurrent_creation_keeps_one_dataset(client, monkeypatch, same):
    root, ident, _ = fixture_group(client, monkeypatch)
    original = answer_evaluation_sets.existing; barrier = Barrier(2)
    def synchronized(*args):
        value = original(*args)
        if value is None: barrier.wait(timeout=10)
        return value
    monkeypatch.setattr(answer_evaluation_sets, 'existing', synchronized)
    payload = body(ident); other = {**payload, 'name': payload['name'] if same else '另一组'}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(client.post, root + '/answer-evaluation-sets', json=value) for value in (payload, other)]
        responses = [f.result(timeout=15) for f in futures]
    assert sorted(r.status_code for r in responses) == ([201, 201] if same else [201, 409])
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AnswerEvaluationSet)) == 1
        assert db.scalar(select(func.count()).select_from(AuditRecord).where(AuditRecord.action == 'answer_evaluation_set.create')) == 1


def test_invalid_uncontrolled_cross_repo_size_and_secrets_are_atomic(client, monkeypatch):
    root, ident, ids = fixture_group(client, monkeypatch)
    path = root + '/answer-evaluation-sets'; payload = body(ident)
    for changes in ({'name': '  '}, {'request_id': 'invalid'}, {'experiment_ids': []}, {'experiment_ids': [ident, ident]},
                    {'experiment_ids': [ident] * 21}, {'experiment_ids': ['bad']}, {'winner': 'baseline-v1'}):
        assert client.post(path, json={**payload, **changes}).status_code == 422
    monkeypatch.setattr(main.settings, 'llm_api_key', SecretStr('private-dataset-fixture-key'))
    assert client.post(path, json={**payload, 'note': 'private-dataset-fixture-key'}).status_code == 422
    assert client.post(path, json={**payload, 'experiment_ids': [new_id()]}).status_code == 404
    monkeypatch.setattr(answer_evaluation_sets, 'MAX_PAYLOAD', 50)
    assert client.post(path, json=payload).status_code == 422
    monkeypatch.setattr(answer_evaluation_sets, 'MAX_PAYLOAD', 12_000_000)
    with SessionLocal() as db:
        db.get(AgentRun, ids[1]).status = 'failed'; db.commit()
    assert client.post(path, json=payload).status_code == 409
    with SessionLocal() as db:
        run = db.get(AgentRun, ids[1]); run.status = 'completed'
        value = copy.deepcopy(run.result); value.pop('analysis_context'); run.result = value; db.commit()
    assert client.post(path, json=payload).status_code == 409
    with SessionLocal() as db:
        db.get(PromptExperiment, ident).repository_id = 'another-repository'; db.commit()
    assert client.post(path, json=payload).status_code == 404
    with SessionLocal() as db:
        assert not list(db.scalars(select(AnswerEvaluationSet)))
        assert not list(db.scalars(select(AuditRecord).where(AuditRecord.action == 'answer_evaluation_set.create')))


def test_frozen_payload_integrity_and_export_secret_guard(client, monkeypatch):
    root, ident, ids = fixture_group(client, monkeypatch)
    saved = create(client, root, body(ident)); path = root + '/answer-evaluation-sets/' + saved['id']
    with SessionLocal() as db:
        record = db.get(AnswerEvaluationSet, saved['id']); value = copy.deepcopy(record.payload)
        value['pairs'][0]['comparison']['left']['snapshot']['items'][0]['text'] = 'tampered'; record.payload = value; db.commit()
    assert client.get(path).status_code == client.get(path + '/export').status_code == 409
    with SessionLocal() as db:
        record = db.get(AnswerEvaluationSet, saved['id']); value = copy.deepcopy(record.payload)
        value['pairs'][0]['comparison']['left']['snapshot']['items'][0]['text'] = 'private-export-fixture-key'
        record.payload = value; record.fingerprint = fingerprint(value); db.commit()
    monkeypatch.setattr(main.settings, 'llm_api_key', SecretStr('private-export-fixture-key'))
    assert client.get(path).status_code == client.get(path + '/export').status_code == 422


def test_viewer_editor_origin_actor_scope_and_mcp_permissions(client, monkeypatch):
    from tests.test_governance import setup_auth, new_member
    root, ident, ids = fixture_group(client, monkeypatch)
    saved = create(client, root, body(ident)); path = root + '/answer-evaluation-sets/' + saved['id']
    repo, _ = setup_auth(client, monkeypatch)
    member = new_member(client, repo, 'viewer')
    client.post('/api/auth/login', json={'username': 'member', 'password': 'testing-member-1234'})
    assert client.get(path).status_code == client.get(path + '/export').status_code == 200
    assert client.post(root + '/answer-evaluation-sets', json=body(ident)).status_code == 403
    assert client.post('/api/auth/login', json={'username': 'admin', 'password': 'testing-owner-1234'}).status_code == 200
    assert client.post(f'/api/admin/repositories/{repo}/members', json={'user_id': member['id'], 'role': 'editor'}).status_code == 200
    client.post('/api/auth/login', json={'username': 'member', 'password': 'testing-member-1234'})
    submitted = body(ident); create(client, root, submitted)
    assert client.post(root + '/answer-evaluation-sets', json=body(ident), headers={'Origin': 'https://foreign.invalid'}).status_code == 403
    assert client.post(root + f'/runs/{ids[0]}/answer-reviews', json={}).status_code == 403
    assert client.post('/api/auth/login', json={'username': 'admin', 'password': 'testing-owner-1234'}).status_code == 200
    assert client.post(root + '/answer-evaluation-sets', json=submitted).status_code == 409
    with SessionLocal() as db:
        other = Repository(full_name='other/dataset', snapshot={'source': 'demo'}); db.add(other); db.commit(); other_id = other.id
    assert client.get(f'/api/repositories/{other_id}/answer-evaluation-sets/{saved["id"]}').status_code == 404
    token = 'dataset-fixture-token-at-least-32-chars'
    monkeypatch.setattr(main, 'settings', main.settings.model_copy(update={'mcp_access_token': SecretStr(token), 'mcp_repository_ids': repo}))
    for url in (root + '/answer-evaluation-sets', path, path + '/export'):
        assert client.get(url, headers={'Authorization': 'Bearer ' + token}).status_code == 403
