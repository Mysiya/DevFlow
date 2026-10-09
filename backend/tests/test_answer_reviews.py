import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app import main
from app.db import AgentRun, AnswerReview, AuditRecord, Conversation, Repository, SessionLocal
from app.demo import demo_snapshot
from app.fact_evaluation import load_fact_dataset, materialize
from app.fact_review import apply_review


def saved_run(client):
    repo = client.get('/api/repositories').json()[0]['id']
    data = load_fact_dataset()
    answer, evidence = materialize(data, data['cases'][0])
    answer['summary'] = 'k1=2.2。'
    answer.update(evidence=list(evidence.values()), provider='llm', task='code')
    result = apply_review(answer, evidence)
    with SessionLocal() as db:
        conversation = Conversation(repository_id=repo, title='评审样例')
        db.add(conversation)
        db.flush()
        run = AgentRun(repository_id=repo, conversation_id=conversation.id, question='仅检查已给定源码。', task='code', mode='demo', status='completed', result=result, events=[{'type':'run.completed','sequence':1}])
        db.add(run)
        db.commit()
        return repo, run.id, copy.deepcopy(result)


def paths(client):
    repo, run, original = saved_run(client)
    root = f'/api/repositories/{repo}'
    path = root + f'/runs/{run}/answer-reviews'
    detail = client.get(path).json()
    body = {'snapshot_hash':detail['snapshot_hash'], 'expected_version':0, 'annotations':[{'item_id':'summary','decision':'conflict','evidence_ids':[detail['snapshot']['evidence'][0]['id']], 'note':'所引源码 k1=1.2，与原摘要的 2.2 冲突。'}]}
    return root, path, run, original, detail, body


def test_original_answer_annotation_partial_coverage_export_and_no_run_mutation(client):
    root, path, run, original, detail, body = paths(client)
    assert detail['snapshot']['items'][1]['text'] == 'k1=2.2。'
    assert detail['automatic_review']['status'] == 'conflict' and detail['current_review'] is None
    before = client.get(root + '/answer-reviews/status').json()
    assert before['counts']['unreviewed'] == len(detail['snapshot']['items']) and before['model_accuracy'] is None
    response = client.post(path, json=body)
    assert response.status_code == 201, response.text
    record = response.json()
    assert record['version'] == 1 and record['created_by'] == '本地管理员'
    assert client.post(path, json=body).json()['id'] == record['id']
    after = client.get(root + '/answer-reviews/status').json()
    assert after['counts']['conflict'] == 1 and after['counts']['unreviewed'] == before['counts']['unreviewed'] - 1
    exported = client.get(root + '/answer-reviews/export')
    assert 'attachment' in exported.headers['content-disposition']
    sample = exported.json()['samples'][0]
    assert sample['snapshot'] == detail['snapshot'] == sample['reviewed_snapshot']
    assert sample['review_current'] and sample['review']['annotations'] == body['annotations']
    assert exported.json()['model_accuracy'] is None and not exported.json()['unreviewed_is_correct']
    with SessionLocal() as db:
        assert db.get(AgentRun, run).result == original
        assert db.get(AgentRun, run).events == [{'type':'run.completed','sequence':1}]
        assert len(list(db.scalars(select(AnswerReview)))) == 1
        assert len(list(db.scalars(select(AuditRecord).where(AuditRecord.action == 'answer_review.save')))) == 1


def test_revisions_append_and_stale_writes_do_not_overwrite_history(client):
    root, path, run, _, _, body = paths(client)
    first = client.post(path, json=body).json()
    changed = copy.deepcopy(body)
    changed['annotations'][0].update(decision='insufficient', evidence_ids=[], note='需要核对更多上下文。')
    assert client.post(path, json=changed).status_code == 409
    changed['expected_version'] = 1
    second = client.post(path, json=changed).json()
    assert second['version'] == 2 and second['id'] != first['id']
    assert client.get(path + '/' + first['id']).json()['annotations'] == first['annotations']
    assert [r['version'] for r in client.get(path).json()['records']] == [2,1]
    assert client.post(path, json=body).status_code == 409
    assert client.get(root + '/answer-reviews/status').json()['counts']['conflict'] == 0


def test_changed_answer_and_evidence_expire_labels_but_keep_frozen_history(client):
    root, path, run, _, detail, body = paths(client)
    record = client.post(path, json=body).json()
    with SessionLocal() as db:
        result = copy.deepcopy(db.get(AgentRun, run).result)
        result['evidence'][0]['content'] += '\n# changed source'
        db.get(AgentRun, run).result = result
        db.commit()
    assert client.post(path, json=body).status_code == 409
    current = client.get(path).json()
    assert current['snapshot_hash'] != detail['snapshot_hash'] and current['current_review'] is None
    assert current['latest_version'] == 1
    status = client.get(root + '/answer-reviews/status').json()
    assert status['counts']['conflict'] == 0 and status['stale_reviews'] == 1
    history = client.get(path + '/' + record['id']).json()
    assert history['snapshot'] == detail['snapshot']
    sample = client.get(root + '/answer-reviews/export').json()['samples'][0]
    assert not sample['review_current'] and sample['reviewed_snapshot'] == detail['snapshot']


def test_checker_metadata_changes_do_not_invalidate_answer_labels(client):
    _, path, run, _, detail, body = paths(client)
    client.post(path, json=body)
    with SessionLocal() as db:
        result = copy.deepcopy(db.get(AgentRun, run).result)
        result['fact_review']['checker_revision'] = 'changed-checker'
        db.get(AgentRun, run).result = result
        db.commit()
    updated = client.get(path).json()
    assert updated['snapshot_hash'] == detail['snapshot_hash'] and updated['current_review']['version'] == 1


def test_answer_without_python_sources_remains_reviewable_without_auto_counts(client):
    root, path, run, _, _, _ = paths(client)
    with SessionLocal() as db:
        db.get(AgentRun, run).result = {'summary':'应先查看文档。', 'provider':'llm', 'evidence':[]}
        db.commit()
    detail = client.get(path).json()
    assert detail['automatic_review'] == {} and detail['snapshot']['items'][0]['id'] == 'summary'
    body = {'snapshot_hash':detail['snapshot_hash'], 'expected_version':0,
            'annotations':[{'item_id':'summary','decision':'insufficient','evidence_ids':[],'note':'未提供对应文档证据。'}]}
    assert client.post(path, json=body).status_code == 201
    assert client.get(root+'/answer-reviews/status').json()['counts']['insufficient'] == 1


@pytest.mark.parametrize('change', ['unknown_field','unknown_evidence','duplicate_field','no_evidence','blank_note','empty','extra'])
def test_invalid_annotations_are_rejected_without_persistence(client, change):
    _, path, _, _, _, body = paths(client)
    row = body['annotations'][0]
    if change == 'unknown_field': row['item_id'] = 'findings.999'
    elif change == 'unknown_evidence': row['evidence_ids'] = ['unrelated-source']
    elif change == 'duplicate_field': body['annotations'].append(copy.deepcopy(row))
    elif change == 'no_evidence': row['evidence_ids'] = []
    elif change == 'blank_note': row['note'] = '   '
    elif change == 'empty': body['annotations'] = []
    elif change == 'extra': row['approved'] = True
    assert client.post(path, json=body).status_code == 422
    with SessionLocal() as db:
        assert not list(db.scalars(select(AnswerReview)))


def test_sensitive_notes_and_unfinished_or_oversize_runs_are_rejected(client, monkeypatch):
    _, path, run, _, _, body = paths(client)
    monkeypatch.setattr(main, 'settings', main.settings.model_copy(update={'llm_api_key':SecretStr('fixture-do-not-store-review-key')}))
    body['annotations'][0]['note'] = 'fixture-do-not-store-review-key'
    assert client.post(path, json=body).status_code == 422
    with SessionLocal() as db:
        db.get(AgentRun, run).status = 'running'
        db.commit()
    assert client.get(path).status_code == client.post(path, json=body).status_code == 409
    with SessionLocal() as db:
        row = db.get(AgentRun, run)
        row.status = 'completed'
        row.result = {'summary':'oversize', 'next_steps':['x'] * 101, 'evidence':[]}
        db.commit()
    assert client.get(path).status_code == 409


def test_scope_roles_cross_origin_and_mcp_do_not_expand_access(client, monkeypatch):
    from tests.test_governance import setup_auth, new_member
    root, path, run, _, _, body = paths(client)
    record = client.post(path, json=body).json()
    ident, _ = setup_auth(client, monkeypatch)
    with SessionLocal() as db:
        other = Repository(full_name='demo/other-review', snapshot=demo_snapshot())
        db.add(other); db.commit(); other_id = other.id
    wrong = f'/api/repositories/{other_id}/runs/{run}/answer-reviews'
    assert client.get(wrong).status_code == 404
    assert client.get(wrong + '/' + record['id']).status_code == 404
    assert client.post(path, json=body, headers={'Origin':'https://untrusted.example'}).status_code == 403
    new_member(client, ident, 'editor')
    client.post('/api/auth/login', json={'username':'member','password':'testing-member-1234'})
    assert client.get(path).status_code == 200 and client.get(root + '/answer-reviews/export').status_code == 200
    assert client.post(path, json=body).status_code == 403
    settings = main.settings.model_copy(update={'mcp_access_token':SecretStr('review-fixture-mcp-token-at-least-32'), 'mcp_repository_ids':ident})
    monkeypatch.setattr(main, 'settings', settings)
    assert client.get(root + '/answer-reviews/export', headers={'Authorization':'Bearer review-fixture-mcp-token-at-least-32'}).status_code == 403


@pytest.mark.parametrize('same', [False, True])
def test_concurrent_submissions_append_once_or_report_a_conflict(client, monkeypatch, same):
    from app import answer_reviews
    _, path, _, _, _, body = paths(client)
    barrier = Barrier(2)
    original = answer_reviews.latest
    def synchronized(*args):
        value = original(*args)
        if value is None:
            barrier.wait(timeout=10)
        return value
    monkeypatch.setattr(answer_reviews, 'latest', synchronized)
    changed = copy.deepcopy(body)
    if not same: changed['annotations'][0]['note'] += ' 第二位评审者的不同说明。'
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(client.post, path, json=value) for value in (body, changed)]
        results = [future.result(timeout=15) for future in futures]
    assert sorted(r.status_code for r in results) == ([201,201] if same else [201,409])
    with SessionLocal() as db:
        assert len(list(db.scalars(select(AnswerReview)))) == 1
