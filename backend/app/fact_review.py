"""Limited deterministic source-declaration review; no execution or LLM judge."""
import ast
import copy
import hashlib
import json
import re
import textwrap
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from .bm25_relations import claims as relation_claims, relation_checks

CHECKER = "source-facts-v2"
LEGACY_CHECKERS = {"source-numeric-v1", CHECKER}
MAX_CHECKS = 100
MAX_FACTS = 128
SCOPE = "仅核对可识别的 Python 数值声明、评分公式参数及有限的 BM25 数学关系句式，不验证其余自然语言解释、运行行为或全部源码。"
_PATTERN = ast.parse("idf * tf * MULT / (tf + K1 * (COMP + B * length / average))", mode="eval").body
_CLAIM = re.compile(r"(?<![A-Za-z0-9_])(?P<name>(?i:k1|k_1|b)|[A-Z][A-Z0-9_]{2,})(?![A-Za-z0-9_])\s*(?:=|为|是|等于|:|：)\s*(?P<value>[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)(?![A-Za-z0-9_.]|\s*[+*/-])")
_QUALIFIER = re.compile(r"(?:不是|不等于|并非|不为|不应|错误说法|错误表述|假设|如果|例如|示例|建议|计划|改为|设置为|修改为|should|suppose|example|not\s+)\s*[^。；;\n]{0,30}$", re.I)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def trusted_code(item):
    path, sha, source = item.get("path", ""), item.get("sha", ""), item.get("source", "")
    start, end = item.get("line_start"), item.get("line_end")
    if source not in ("local_project", "github") or not path.endswith(".py") or not re.fullmatch(r"[0-9a-f]{40}", sha):
        return False
    if type(start) is not int or type(end) is not int or not 1 <= start <= end or end - start >= 400:
        return False
    if not item.get("content") or len(item["content"].splitlines()) != end - start + 1:
        return False
    path_hash = hashlib.sha256(path.encode()).hexdigest()[:12]
    return item.get("id") == f"code:{source}:{sha}:{path_hash}:{start}:{end}"


def number(node, values=None, depth=0):
    if depth > 8:
        return None
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        try:
            value = Decimal(str(node.value))
            return value if value.is_finite() and value.copy_abs() < Decimal("1e100") else None
        except InvalidOperation:
            return None
    if isinstance(node, ast.Name):
        return (values or {}).get(node.id)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = number(node.operand, values, depth + 1)
        return value.copy_negate() if value is not None and isinstance(node.op, ast.USub) else value
    # Deliberately omit evaluation of calls, attributes, arithmetic or user code.
    return None


def display(value):
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def exact_sum(left, right):
    # Enough for bounded integers and Python floats down to the smallest subnormal.
    with localcontext() as context:
        context.prec = 600
        return left + right


def match_formula(template, actual, values, captures):
    if isinstance(template, ast.Name) and template.id in ("MULT", "K1", "COMP", "B"):
        value = number(actual, values)
        if value is None:
            return False
        captures[template.id] = value
        return True
    if type(template) is not type(actual):
        return False
    if isinstance(template, ast.Name):
        return template.id == actual.id
    if isinstance(template, ast.BinOp):
        return (type(template.op) is type(actual.op) and match_formula(template.left, actual.left, values, captures)
                and match_formula(template.right, actual.right, values, captures))
    return False


def scopes(tree):
    pending = [(tree, "module")]
    while pending:
        scope, name = pending.pop()
        statements, stack = [], list(getattr(scope, "body", []))
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                pending.append((node, node.name if name == "module" else name + "." + node.name))
                continue
            statements.append(node)
            stack.extend(ast.iter_child_nodes(node))
        yield scope, name, statements


def extract_source(evidence):
    facts, relations = [], []
    for item in evidence.values():
        if not trusted_code(item):
            continue
        try:
            tree = ast.parse(textwrap.dedent(item["content"]))
        except (SyntaxError, ValueError, RecursionError):
            continue
        for scope, name, nodes in scopes(tree):
            writes = {}
            for node in nodes:
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                    writes[node.id] = writes.get(node.id, 0) + 1
            values = {}
            direct = []
            for node in getattr(scope, "body", []):
                target = node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else node.target if isinstance(node, ast.AnnAssign) else None
                if not isinstance(target, ast.Name) or writes.get(target.id) != 1:
                    continue
                value = number(node.value)
                if value is not None:
                    values[target.id] = value
                    if name == "module" and re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", target.id):
                        direct.append((target.id, value, node.lineno, "literal_declaration"))
            # A partial function window cannot establish local binding uniqueness.
            if not item.get("partial_symbol"):
                for node in nodes:
                    if not isinstance(node, ast.AugAssign) or not isinstance(node.op, ast.Add):
                        continue
                    captures = {}
                    if not match_formula(_PATTERN, node.value, values, captures):
                        continue
                    k1, b = captures["K1"], captures["B"]
                    multiplier, complement = exact_sum(k1, 1), exact_sum(1, b.copy_negate())
                    if k1 > 0 and 0 <= b <= 1:
                        for role, value, expression, expected, actual in (
                            ("numerator", captures["MULT"], "k1+1", multiplier, node.value.left.right),
                            ("length_base", captures["COMP"], "1-b", complement, node.value.right.right.right.left),
                        ):
                            relations.append({"name": "分子乘数" if role == "numerator" else "长度常数项", "value": display(value),
                                "role": role, "expression": expression, "expression_value": display(expected), "holds": value == expected,
                                "bindings": {"k1": display(k1), "b": display(b)}, "kind": "bm25_relation",
                                "source_form": "bound_name" if isinstance(actual, ast.Name) else "literal", "source_expression": ast.unparse(actual),
                                "subject": item.get("symbol") or name, "evidence_id": item["id"], "path": item["path"], "sha": item["sha"],
                                "line": item["line_start"] + node.lineno - 1,
                                "statement": f"{'分子乘数' if role == 'numerator' else '长度常数项'} {display(value)} {'=' if value == expected else '≠'} {expression}（{display(expected)}）"})
                            if len(facts) + len(relations) >= MAX_FACTS:
                                return facts, relations, True
                    if k1 > 0 and 0 <= b <= 1 and exact_sum(captures["COMP"], b) == 1 and captures["MULT"] == multiplier:
                        direct.extend([(key, value, node.lineno, "bm25_formula") for key, value in (("k1", k1), ("b", b))])
            for key, value, row, kind in direct:
                facts.append({"name": key, "value": display(value), "subject": item.get("symbol") or name, "kind": kind,
                              "evidence_id": item["id"], "path": item["path"], "sha": item["sha"],
                              "line": item["line_start"] + row - 1})
                if len(facts) + len(relations) >= MAX_FACTS:
                    return facts, relations, True
    return facts, relations, False


def extract_facts(evidence):
    facts, _, limited = extract_source(evidence)
    return facts, limited


def fields(analysis):
    for key in ("title", "summary", "recommendation"):
        yield key, analysis.get(key, ""), None
    for index, finding in enumerate(analysis.get("findings", [])):
        for key in ("title", "detail"):
            yield f"findings.{index}.{key}", finding.get(key, ""), finding.get("evidence_ids", [])
    for key in ("next_steps", "gaps"):
        for index, text in enumerate(analysis.get(key, [])):
            yield f"{key}.{index}", text, None


def set_field(analysis, path, value):
    parts, node = path.split("."), analysis
    for part in parts[:-1]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    if isinstance(node, list):
        node[int(parts[-1])] = value
    else:
        node[parts[-1]] = value


def apply_review(answer, evidence):
    output = copy.deepcopy(answer)
    previous = output.pop("fact_review", None)
    if previous and previous.get("checker") in LEGACY_CHECKERS and previous.get("original_analysis"):
        output.update(copy.deepcopy(previous["original_analysis"]))
    analysis = {key: copy.deepcopy(output.get(key, [] if key in ("findings", "next_steps", "gaps") else ""))
                for key in ("title", "summary", "recommendation", "findings", "next_steps", "gaps")}
    facts, relations, facts_limited = extract_source(evidence)
    checks, limited = [], False
    for field, text, refs in fields(analysis):
        spans = relation_claims(text)
        for match in _CLAIM.finditer(text):
            if re.search(r"[A-Za-z0-9_)\]]\s*[+*/-]\s*$", text[max(0, match.start()-40):match.start()]):
                continue
            if any(match.start() >= c["start"] and match.end() <= c["end"] for c in spans):
                continue
            if len(checks) >= MAX_CHECKS:
                limited = True
                break
            key = match["name"]
            key = "k1" if key.lower() in ("k1", "k_1") else "b" if key.lower() == "b" else key
            candidates = [fact for fact in facts if fact["name"] == key and (refs is None or fact["evidence_id"] in refs)]
            subjects = {fact["subject"] for fact in candidates if fact["subject"] != "module" and fact["subject"] in text}
            if subjects:
                candidates = [fact for fact in candidates if fact["subject"] in subjects]
            expected = sorted({(fact["sha"], fact["path"], fact["subject"], fact["value"]) for fact in candidates})
            # A proposal/negation is not an assertion of the current declaration.
            qualified = bool(_QUALIFIER.search(text[max(0, match.start() - 40):match.start()]))
            status = "insufficient"
            reason = "此引用范围没有唯一可识别的数值来源。"
            if qualified:
                reason = "包含假设、建议或否定；当前检查器不判断此类表述。"
            elif len(expected) == 1:
                try:
                    observed = Decimal(match["value"]) if len(match["value"]) <= 100 else None
                    if observed is None or not observed.is_finite() or observed.copy_abs() >= Decimal("1e100"):
                        reason = "数值超出检查器范围，未验证。"
                    else:
                        status = "matched" if observed == Decimal(expected[0][3]) else "conflict"
                        reason = "与该引用的数值声明一致；运行值及其余解释未验证。" if status == "matched" else "数值表述与固定提交的源码不一致。"
                except InvalidOperation:
                    reason = "无法解析数值，未验证。"
            checks.append({"field": field, "claim": match.group(), "observed": match["value"], "name": key, "kind": "numeric",
                           "status": status, "reason": reason, "expected": [fact for fact in candidates],
                           "original_text": text})
    relational, relation_limited = relation_checks(list(fields(analysis)), relations, MAX_CHECKS - len(checks))
    checks.extend(relational)
    limited = limited or relation_limited
    workflow = output.get("workflow", {})
    inherited = copy.deepcopy(workflow.get("source_conflicts", []))[:MAX_CHECKS]
    limited = limited or workflow.get("source_conflicts_limited", False) or len(workflow.get("source_conflicts", [])) > MAX_CHECKS
    if not facts and not relations and not checks and not inherited and not any(trusted_code(e) for e in evidence.values()):
        return output
    checks.extend(inherited)
    conflicts = {check["field"] for check in checks if check["status"] == "conflict" and not check.get("task_id")}
    # A finding's title and detail form one conclusion; quarantine both together.
    for field in list(conflicts):
        if field.startswith("findings."):
            prefix = ".".join(field.split(".")[:2])
            conflicts.update((prefix + ".title", prefix + ".detail"))
    for field in sorted(conflicts):
        set_field(output, field, "此段包含与固定源码冲突的表述，原文已移至事实核对区，请依据对应提交核对。")
    has_conflicts = bool(conflicts or inherited)
    if has_conflicts:
        output["recommendation"] = "存在源码事实冲突，核对后再采用结论。"
    source_hash = digest([{key: e.get(key) for key in ("id", "content", "path", "sha", "line_start", "line_end", "symbol", "partial_symbol")}
                          for e in evidence.values()])
    revision = hashlib.sha256(b"".join((Path(__file__).parent / name).read_bytes() for name in ("fact_review.py", "bm25_relations.py"))).hexdigest()
    output["fact_review"] = {"checker": CHECKER, "checker_revision": revision,
        "input_hash": digest({"analysis": analysis, "sources": source_hash}), "source_hash": source_hash,
        "scope": SCOPE, "status": "conflict" if has_conflicts else "partial" if facts or relations or checks else "unavailable",
        "counts": {key: sum(check["status"] == key for check in checks) for key in ("matched", "conflict", "insufficient")},
        "categories": {kind: {key: sum(c["status"] == key and c.get("kind", "numeric") == kind for c in checks) for key in ("matched", "conflict", "insufficient")} for kind in ("numeric", "bm25_relation")},
        "checks": checks, "facts": facts, "relations": relations, "limited": limited or facts_limited,
        "original_analysis": analysis if has_conflicts else None}
    return output
