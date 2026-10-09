import asyncio
import copy
import json
from datetime import datetime, timezone
import pytest
from app.db import AgentRun, SessionLocal
from app.fact_evaluation import evaluate_facts, load_fact_dataset, materialize
from app.fact_review import apply_review, extract_facts
from app.llm import ModelClient
from app.config import Settings

DATA = load_fact_dataset()


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c:c["id"])
def test_fixed_numeric_claims(case):
    from app.fact_evaluation import score_case
    row = score_case(DATA, case)
    assert row["passed"], row


def formula():
    return materialize(DATA, DATA["cases"][0])


def test_relation_parser_revision_is_part_of_review_provenance(monkeypatch):
    from pathlib import Path
    answer, evidence = formula()
    before = apply_review(answer, evidence)["fact_review"]
    read_bytes = Path.read_bytes
    def changed_parser(path):
        data = read_bytes(path)
        return data + b"\n# grammar revision\n" if path.name == "bm25_relations.py" else data
    monkeypatch.setattr(Path, "read_bytes", changed_parser)
    after = apply_review(answer, evidence)["fact_review"]
    assert before["checker_revision"] != after["checker_revision"]
    assert before["counts"] == after["counts"] and before["source_hash"] == after["source_hash"]


def test_review_is_idempotent_and_retains_originals_with_absolute_source_lines():
    answer,evidence=formula()
    answer["summary"]="k1=2.2。"
    original=copy.deepcopy(answer)
    reviewed=apply_review(answer,evidence)
    assert reviewed==apply_review(reviewed,evidence)
    assert answer==original and reviewed["fact_review"]["original_analysis"]==original
    assert "冲突" in reviewed["recommendation"] and "k1=2.2" not in reviewed["summary"]
    assert {(f["name"],f["value"],f["line"]) for f in reviewed["fact_review"]["facts"]}=={("k1","1.2",12),("b","0.75",12)}


def test_matching_title_cannot_wash_away_child_conflicts_without_code_sources():
    answer,_=formula()
    answer["summary"]="子任务已完成。"
    answer["workflow"]={"source_conflicts":[{"field":"task:source.gaps.0","claim":"k1=2.2","observed":"2.2","name":"k1","status":"conflict","reason":"来源冲突","expected":[],"original_text":"k1=2.2","task_id":"source","task_title":"源码分析"}]}
    reviewed=apply_review(answer,{})
    assert reviewed["fact_review"]["counts"]["conflict"]==1
    assert reviewed["fact_review"]["status"]=="conflict" and "冲突" in reviewed["recommendation"]
    assert reviewed["summary"]==answer["summary"]
    assert apply_review(reviewed,{})==reviewed
    answer['workflow']['source_conflicts_limited']=True
    assert apply_review(answer,{})['fact_review']['limited'] is True


def test_invalid_evidence_identity_does_not_establish_source_fact():
    answer,evidence=formula()
    item=next(iter(evidence.values()))
    item["line_start"]+=1
    review=apply_review(answer,evidence)["fact_review"]
    assert not review["facts"] and review["counts"]["insufficient"]==2


def test_untrusted_python_is_parsed_without_execution(tmp_path):
    answer,evidence=formula()
    item=next(iter(evidence.values()))
    target=tmp_path/"executed.txt"
    item["content"]=f"open({str(target)!r}, 'w').write('bad')\nMAX_LINES = 80\nMAX_LINES = 90"
    # Keep the canonical identity and line count, but ambiguous writes cannot establish a fact.
    answer["summary"]="MAX_LINES=80。"
    assert not extract_facts(evidence)[0] and not target.exists()
    assert apply_review(answer,evidence)["fact_review"]["counts"]["insufficient"]==1


def test_numeric_limit_and_claim_limit_are_explicit():
    answer,evidence=formula()
    answer["summary"]="k1=1e101。"*110
    review=apply_review(answer,evidence)["fact_review"]
    assert len(review["checks"])==100 and review["limited"]
    assert review["counts"]["insufficient"]==100


def test_model_receives_derived_facts_and_cannot_supply_its_own_review(monkeypatch):
    answer,evidence=formula()
    answer["summary"]="k1=2.2。"
    answer["fact_review"]={"checker":"source-numeric-v1","status":"partial","checks":[],"original_analysis":{"summary":"k1=1.2。"}}
    async def chat(self,messages,**kwargs):
        payload=json.loads(messages[1]["content"])
        assert {(f["name"],f["value"]) for f in payload["source_numeric_facts"]}=={("k1","1.2"),("b","0.75")}
        assert {(f['expression'],f['expression_value'],f['holds']) for f in payload['source_formula_relations']}=={('k1+1','2.2',True),('1-b','0.25',True)}
        return {"content":json.dumps(answer,ensure_ascii=False)}
    monkeypatch.setattr(ModelClient,"chat",chat)
    reviewed=asyncio.run(ModelClient(Settings()).analyze("source","问题",evidence))
    assert reviewed["fact_review"]["status"]=="conflict"
    assert reviewed["fact_review"]["original_analysis"]["summary"]=="k1=2.2。"


def saved_conflict(client):
    repo=client.get('/api/repositories').json()[0]['id']
    response=client.post('/api/chat/stream',json={'repository_id':repo,'task':'issue','target':42,'message':'检查 Issue #42'})
    assert response.status_code==200
    ident=client.get(f'/api/repositories/{repo}/runs').json()[0]['id']
    answer,evidence=materialize(DATA,DATA["cases"][1])
    answer.update(evidence=list(evidence.values()),provider="fixture",task="code")
    with SessionLocal() as db:
        run=db.get(AgentRun,ident)
        run.result=answer;run.created_at=datetime(2026,10,5,tzinfo=timezone.utc);db.commit()
    return repo,ident,answer


def test_historical_preview_is_read_only_and_draft_generation_is_blocked(client):
    repo,ident,original=saved_conflict(client)
    root=f'/api/repositories/{repo}'
    before=client.get(root+f'/runs/{ident}').json()
    preview=client.get(root+f'/runs/{ident}/fact-review')
    assert preview.status_code==200
    assert preview.json()['persisted'] is False
    assert preview.json()['reviewed_analysis']['fact_review']['status']=='conflict'
    assert preview.json()['reviewed_analysis']['fact_review']['preview']
    assert client.get(root+f'/runs/{ident}').json()==before
    assert client.post(root+'/drafts',json={'run_id':ident}).status_code==409
    assert client.get(root+'/drafts').json()==[]
    assert client.get(root+f'/runs/{ident}').json()['result']==original
    report=client.post(root+'/reports',json={'week_start':'2026-10-05'}).json()
    assert "k1=2.2" not in report['body'] and "存在源码事实冲突" in report['body']
    assert client.get(root+f'/runs/{ident}').json()==before
    assert client.get(root+'/runs/missing/fact-review').status_code==404
    with SessionLocal() as db:
        db.get(AgentRun,ident).status='interrupted';db.commit()
    assert client.get(root+f'/runs/{ident}/fact-review').status_code==409


def test_fixed_fact_scores_are_repeatable_and_not_model_metrics():
    one=evaluate_facts()
    assert one==evaluate_facts() and one['total']==one['passed']==42


def test_final_run_review_retains_scope_notices(monkeypatch):
    from types import SimpleNamespace
    from app.agents import AgentService
    answer,evidence=formula()
    answer['summary']='k1=2.2。'
    tools=SimpleNamespace(settings=Settings(),snapshot={'coverage':'固定快照范围'},evidence=evidence,retrieval_notices=['工具范围'])
    async def specialist(self,*args):return apply_review(answer,evidence)
    monkeypatch.setattr(AgentService,'specialist',specialist)
    result=asyncio.run(AgentService(tools,'固定问题').run('code',None))
    assert result['fact_review']['status']=='conflict'
    assert '固定快照范围' in result['gaps'] and '工具范围' in result['gaps']


@pytest.mark.parametrize('field',['title','summary','recommendation','finding','next_steps','gaps'])
def test_conflicts_in_every_answer_field_are_quarantined(field):
    answer,evidence=formula()
    text='k1=2.2。'
    if field=='finding':answer['findings']=[{'title':text,'detail':text,'severity':'low','evidence_ids':list(evidence)}]
    elif field in ('next_steps','gaps'):answer[field]=[text]
    else:answer[field]=text
    result=apply_review(answer,evidence)
    assert result['fact_review']['status']=='conflict'
    assert text not in json.dumps({key:result[key] for key in answer},ensure_ascii=False)


def test_preview_is_repository_scoped_and_mcp_cannot_use_unlisted_endpoint(client,monkeypatch):
    from app import main
    from app.db import Repository
    from app.demo import demo_snapshot
    from pydantic import SecretStr
    repo,ident,_=saved_conflict(client)
    with SessionLocal() as db:
        other=Repository(full_name='demo/other',snapshot=demo_snapshot())
        db.add(other);db.commit();other_id=other.id
    assert client.get(f'/api/repositories/{other_id}/runs/{ident}/fact-review').status_code==404
    settings=main.settings.model_copy(update={'mcp_access_token':SecretStr('independent-fixture-mcp-token-123456'),'mcp_repository_ids':repo})
    monkeypatch.setattr(main,'settings',settings)
    response=client.get(f'/api/repositories/{repo}/runs/{ident}/fact-review',headers={'Authorization':'Bearer independent-fixture-mcp-token-123456'})
    assert response.status_code==403


def test_relation_conflict_quarantines_whole_finding_and_restores_legacy_review():
    answer,evidence=formula()
    answer['findings']=[{'title':'分子中的2.2不是k1+1','detail':'这段解释应随冲突标题一起移出正文。','severity':'low','evidence_ids':list(evidence)}]
    old=copy.deepcopy(answer)
    old['fact_review']={'checker':'source-numeric-v1','original_analysis':copy.deepcopy(answer)}
    old['recommendation']='存在源码事实冲突，核对后再采用结论。'
    reviewed=apply_review(old,evidence)
    assert reviewed['fact_review']['original_analysis']==answer
    assert reviewed['fact_review']['categories']['bm25_relation']['conflict']==1
    assert reviewed['findings'][0]['title']==reviewed['findings'][0]['detail']
    assert reviewed==apply_review(reviewed,evidence)
    assert old['findings']==answer['findings']


def test_expression_claim_is_not_misread_as_direct_b_declaration():
    answer,evidence=formula();answer['summary']='1 - b = 0.25。'
    review=apply_review(answer,evidence)['fact_review']
    assert review['categories']['numeric']=={'matched':0,'conflict':0,'insufficient':0}
    assert review['categories']['bm25_relation']=={'matched':1,'conflict':0,'insufficient':0}


def test_tiny_source_parameters_do_not_round_away_formula_relationships():
    answer,evidence=formula()
    source=next(iter(evidence.values()))
    source['content']='def ranked_search():\n    score = 0\n    score += idf * tf * 1 / (tf + 1e-99 * (.25 + .75 * length / average))'
    answer['summary']='分子乘数等于k1+1。'
    review=apply_review(answer,evidence)['fact_review']
    assert review['categories']['bm25_relation']['conflict']==1
    assert review['relations'][0]['holds'] is False


def test_relation_limits_and_unsupported_sources_are_visible():
    answer,evidence=formula();answer['summary']='分子乘数等于k1+1。'*105
    review=apply_review(answer,evidence)['fact_review']
    assert review['limited'] and len(review['checks'])==100
    assert review['categories']['bm25_relation']['matched']==100
    next(iter(evidence.values()))['source']='issue'
    assert apply_review(answer,evidence)['fact_review']['categories']['bm25_relation']['insufficient']==100


def test_readonly_relation_preview_blocks_drafts_and_report_uses_quarantined_summary(client):
    repo,ident,_=saved_conflict(client)
    answer,evidence=formula()
    answer.update(summary='分子中的2.2是常量乘数，不是k1+1。',evidence=list(evidence.values()),provider='fixture',task='code')
    with SessionLocal() as db:
        db.get(AgentRun,ident).result=answer;db.commit()
    root=f'/api/repositories/{repo}'
    original=client.get(root+f'/runs/{ident}').json()
    preview=client.get(root+f'/runs/{ident}/fact-review').json()['reviewed_analysis']
    assert preview['fact_review']['categories']['bm25_relation']['conflict']==1
    assert preview['fact_review']['preview']
    assert client.post(root+'/drafts',json={'run_id':ident}).status_code==409
    report=client.post(root+'/reports',json={'week_start':'2026-10-05'}).json()
    assert '不是k1+1' not in report['body'] and '存在源码事实冲突' in report['body']
    assert client.get(root+f'/runs/{ident}').json()==original


def test_correct_live_style_chains_survive_recheck_without_a_new_model_call(client):
    from app.fact_review import CHECKER
    repo,ident,_=saved_conflict(client)
    answer,evidence=formula()
    for source in evidence.values():source['title']=source['path']
    answer.update(summary='k1=1.2，b=0.75。数学上，2.2 = k1+1，0.25 = 1-b。',evidence=list(evidence.values()),provider='fixture',task='code')
    answer['findings']=[{'title':'关系与源码写法','detail':'源码直接使用字面量。数学上，2.2 = k1+1 = 1.2+1，0.25 = 1-b = 1-0.75。源码写法为字面量。','severity':'info','evidence_ids':list(evidence)}]
    original={key:copy.deepcopy(answer[key]) for key in ('title','summary','recommendation','findings','next_steps','gaps')}
    # Rechecking a previously quarantined record restores its raw answer first.
    answer['fact_review']={'checker':CHECKER,'original_analysis':original}
    answer['summary']='旧检查器误报后移出了此摘要。'
    with SessionLocal() as db:db.get(AgentRun,ident).result=answer;db.commit()
    root=f'/api/repositories/{repo}'
    before=client.get(root+f'/runs/{ident}').json()
    review=client.get(root+f'/runs/{ident}/fact-review').json()['reviewed_analysis']
    assert review['fact_review']['counts']['conflict']==0
    assert review['fact_review']['categories']['bm25_relation']['matched']==4
    assert review['findings']==original['findings'] and review['summary']==original['summary']
    assert client.get(root+f'/runs/{ident}').json()==before
    assert client.post(root+'/drafts',json={'run_id':ident}).status_code==200
