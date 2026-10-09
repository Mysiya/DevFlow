"""Fixed code fixture comparison using the same ranker as WorkspaceManager."""
import hashlib
import json
from pathlib import Path

from .code_search import legacy_search, ranked_search, VERSION

DATASET = Path(__file__).resolve().parents[1] / "evals/code-v1.json"
SCORER = "path-and-complete-line-range-v1"


def load_code_dataset():
    return json.loads(DATASET.read_text(encoding="utf-8"))


def score_code(found, expected):
    found = found[:5]
    if not expected:
        return {"hit_at_1": None, "recall_at_5": None, "mrr_at_5": None, "passed": not found}
    ranks = [next((i + 1 for i, hit in enumerate(found)
                   if hit["path"] == target["path"] and hit["line_start"] <= target["line_start"]
                   and hit["line_end"] >= target["line_end"]), None) for target in expected]
    first = min((rank for rank in ranks if rank is not None), default=None)
    return {"hit_at_1": int(first == 1), "recall_at_5": sum(rank is not None for rank in ranks) / len(expected),
            "mrr_at_5": 1 / first if first else 0, "passed": all(rank is not None for rank in ranks)}


def evaluate_code(data=None):
    data = data or load_code_dataset()
    strategies = {}
    for name in ("line_keyword_v09", VERSION):
        rows = []
        for case in data["cases"]:
            prefix = case.get("path_prefix", "").rstrip("/")
            files = [f for f in data["files"] if not prefix or f["path"] == prefix or f["path"].startswith(prefix + "/")]
            found = legacy_search(files, case["query"], 5) if name == "line_keyword_v09" else ranked_search(files, case["query"], 5)["results"]
            score = score_code(found, case["expected"])
            rows.append({"id": case["id"], "query": case["query"], "path_prefix": prefix,
                         "expected": case["expected"], "retrieved": [{k: c[k] for k in ("path", "line_start", "line_end")} for c in found], **score})
        means = {key: sum(row[key] for row in rows if row[key] is not None) / max(1, sum(row[key] is not None for row in rows))
                 for key in ("hit_at_1", "recall_at_5", "mrr_at_5")}
        strategies[name] = {"rows": rows, "mean": means, "passed": sum(row["passed"] for row in rows), "total": len(rows)}
    encoded = json.dumps({"data": data, "scorer": SCORER}, sort_keys=True, ensure_ascii=False).encode()
    return {"dataset": data["id"], "fingerprint": hashlib.sha256(encoded).hexdigest(), "scorer": SCORER,
            "description": data["description"], "strategies": strategies, "current_strategy": VERSION,
            "positive_cases": sum(bool(case["expected"]) for case in data["cases"])}
