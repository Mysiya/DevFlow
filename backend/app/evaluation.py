"""Bounded offline benchmark with immutable inputs and explicit scoring criteria."""
import hashlib
import json
from pathlib import Path
from time import perf_counter
from .agents import AgentService
from .demo import demo_snapshot
from .knowledge import bm25, tokenize
from .tools import Tools
from .code_evaluation import evaluate_code, load_code_dataset
from .fact_evaluation import evaluate_facts, load_fact_dataset

DATASET = Path(__file__).resolve().parents[1] / "evals/core-v1.json"


def load_dataset():
    data=json.loads(DATASET.read_text(encoding="utf-8"))
    encoded=json.dumps({"data":data,"code":load_code_dataset(),"facts":load_fact_dataset(),"snapshot":demo_snapshot(),"scorer":"core-v4.0"},sort_keys=True,ensure_ascii=False).encode()
    return data,hashlib.sha256(encoded).hexdigest()


def retrieval_scores(found, expected):
    ids=[c["id"] for c in found]
    relevant=set(expected)
    if not relevant:
        return {"precision":None,"recall":None,"mrr":None,"passed":not ids}
    hits=len(set(ids)&relevant)
    rank=next((i+1 for i,x in enumerate(ids) if x in relevant),None)
    return {"precision":hits/len(ids) if ids else 0,"recall":hits/len(relevant),"mrr":1/rank if rank else 0,"passed":hits==len(relevant)}


async def evaluate(settings):
    data,fingerprint=load_dataset();started=perf_counter()
    corpus=[{**c,"chunk_id":c["id"]} for c in data["corpus"]]
    retrieval={};exports=[]
    for name in ("title_keyword","bm25"):
        rows=[]
        for case in data["retrieval"]:
            terms=set(tokenize(case["query"]))
            found=bm25(corpus,case["query"],2) if name=="bm25" else [c for c in corpus if terms&set(tokenize(c["title"]))][:2]
            score=retrieval_scores(found,case["expected"])
            rows.append({"id":case["id"],"query":case["query"],"expected":case["expected"],"retrieved":[c["id"] for c in found],**score})
            if case["expected"]:
                exports.append({"variant":name,"case_id":case["id"],"user_input":case["query"],
                                "retrieved_contexts":[c["content"] for c in found],
                                "reference_contexts":[c["content"] for c in corpus if c["id"] in case["expected"]]})
        means={key:sum(r[key] for r in rows if r[key] is not None)/sum(r[key] is not None for r in rows) for key in ("precision","recall","mrr")}
        retrieval[name]={"rows":rows,"mean":means,"passed":sum(r["passed"] for r in rows),"total":len(rows)}
    analysis=[]
    demo_settings=settings.model_copy(update={"devflow_mode":"demo","retrieval_backend":"keyword","rerank_enabled":False,"analysis_skill_id":None})
    async def emit(*args):pass
    for case in data["analysis"]:
        tools=Tools(demo_settings,demo_snapshot(),emit)
        try:
            answer=await AgentService(tools,case["question"]).run(case["task"],case["target"])
            refs={e["id"] for e in answer["evidence"]}
            cited={ref for f in answer["findings"] for ref in f["evidence_ids"]}
            checks={"evidence_present":case["required_evidence"] in refs,"citations_valid":cited<=refs,"has_scope_limits":bool(answer["gaps"])}
            if "recommendation" in case:checks["recommendation"]=answer["recommendation"]==case["recommendation"]
            if "high_risk" in case:checks["risk"]=any(f["severity"]=="high" for f in answer["findings"])==case["high_risk"]
            if "text" in case:checks["observed_error"]=case["text"] in json.dumps(answer,ensure_ascii=False)
            analysis.append({"id":case["id"],"task":case["task"],"checks":checks,"passed":all(checks.values()),"recommendation":answer["recommendation"]})
        finally:await tools.close()
    code = evaluate_code()
    code_result = code["strategies"][code["current_strategy"]]
    facts = evaluate_facts()
    from .task_skills import ROOT, MANIFEST
    source = b"".join((Path(__file__).parent/name).read_bytes() for name in ("evaluation.py","agents.py","tools.py","knowledge.py","code_search.py","code_evaluation.py","workspace.py","fact_review.py","fact_evaluation.py","llm.py","planning.py","bm25_relations.py","prompts.py","task_skills.py"))
    revision=hashlib.sha256(source + b"".join((ROOT / (name + ".json")).read_bytes() for name in MANIFEST)).hexdigest()
    return "core-v4",fingerprint,{"scope":"fixed-demo-code-numeric-and-relation-fixture","runner":"core-v4.0","runner_revision":revision,"description":data["description"],
        "elapsed_ms":int((perf_counter()-started)*1000),"retrieval":retrieval,"analysis":analysis,"ragas_samples":exports,
        "code_retrieval":code,
        "source_facts":facts,
        "passed":sum(r["passed"] for r in analysis)+retrieval["bm25"]["passed"]+code_result["passed"]+facts["passed"],"total":len(analysis)+len(data["retrieval"])+code_result["total"]+facts["total"]}
