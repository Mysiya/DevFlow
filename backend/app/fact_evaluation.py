"""Fixed local assertions about the bounded numeric checker, never model accuracy."""
import copy
import hashlib
import json
from pathlib import Path
from .fact_review import apply_review, digest

DATASET = Path(__file__).resolve().parents[1] / "evals/facts-v2.json"


def load_fact_dataset():
    return json.loads(DATASET.read_text(encoding="utf-8"))


def materialize(data, case):
    evidence = {}
    for source in case["sources"]:
        content = data["sources"][source["fixture"]]
        path, sha, origin = source.get("path", "fixture.py"), source.get("sha", "a" * 40), source.get("source", "local_project")
        start, end = 10, 10 + len(content.splitlines()) - 1
        ident = f"code:{origin}:{sha}:{hashlib.sha256(path.encode()).hexdigest()[:12]}:{start}:{end}"
        evidence[ident] = {"id": ident, "source": origin, "path": path, "sha": sha, "content": content,
                           "line_start": start, "line_end": end, "partial_symbol": source.get("partial_symbol", False),
                           "symbol": source.get("symbol", "ranked_search" if source["fixture"] in ("formula", "named", "reassigned", "changed_formula") else "")}
    answer = {"title": "固定数值样例", "summary": "有限核对", "recommendation": "人工确认", "findings": [], "next_steps": [], "gaps": []}
    if case.get("field") == "finding":
        answer["findings"] = [{"title": "结论", "detail": case["text"], "evidence_ids": case.get("refs", list(evidence)), "severity": "low"}]
    elif case.get("field") == "gaps":
        answer["gaps"] = [case["text"]]
    else:
        answer["summary"] = case["text"]
    return answer, evidence


def score_case(data, case):
    answer, evidence = materialize(data, case)
    before = copy.deepcopy((answer, evidence))
    reviewed = apply_review(answer, evidence)
    checks = reviewed["fact_review"]["checks"]
    status = "conflict" if any(c["status"] == "conflict" for c in checks) else "matched" if checks and all(c["status"] == "matched" for c in checks) else "insufficient"
    validation = {"expected_status": status == case["status"], "bounded_claims": len(checks) == case.get("count", 1),
                  "input_preserved": (answer, evidence) == before}
    if case["status"] == "conflict":
        validation["original_preserved"] = reviewed["fact_review"]["original_analysis"] == answer
        validation["conflict_quarantined"] = case["text"] not in json.dumps({key: reviewed[key] for key in answer}, ensure_ascii=False)
    return {"id": case["id"], "label": case["label"], "expected": case["status"], "observed": status, "checks": validation, "passed": all(validation.values())}


def evaluate_facts():
    data = load_fact_dataset()
    rows = [score_case(data, case) for case in data["cases"]]
    return {"dataset": data["dataset"], "fingerprint": digest(data), "description": data["description"],
            "rows": rows, "passed": sum(row["passed"] for row in rows), "total": len(rows)}
