"""Finite BM25 relation grammar. Source bindings come from AST; no general NL judge."""
import re
from decimal import Decimal, InvalidOperation, localcontext

_NUM = r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
_EXPR = r"(?:k_?1\s*\+\s*1|1\s*-\s*b)"
_OP = r"(?:并不等于|不等于|并不是|不是|并非|不对应|等于|对应|是|==|!=|≠|=)"
_ROLE = r"(?:分子(?:(?:中|里)的)?(?:常量乘数|乘数|乘子|系数|因子)?|numerator(?:\s+multiplier)?|长度(?:归一化)?(?:项)?(?:(?:中|里)的)?(?:常数(?:项)?|常量(?:项)?)|分母常数项)"
_ROLE_CLAIM = re.compile(rf"(?P<role>{_ROLE})\s*(?P<value>{_NUM})?\s*(?:是(?:一个)?(?:独立的)?常量乘数[，,]\s*)?(?P<op>{_OP})\s*(?P<expression>{_EXPR})(?![A-Za-z0-9_+*/-])", re.I)
_EXPR_CLAIM = re.compile(rf"(?<![A-Za-z0-9_])(?P<expression>{_EXPR})\s*(?P<op>{_OP})\s*(?P<value>{_NUM})(?![A-Za-z0-9_.]|\s*[+*/-])", re.I)
_REVERSE_CLAIM = re.compile(rf"(?<![A-Za-z0-9_.+*/-])(?P<value>{_NUM})\s*(?P<op>{_OP})\s*(?P<expression>{_EXPR})(?![A-Za-z0-9_+*/-])", re.I)
_NONASSERTION = re.compile(r"(?:建议|计划|假设|如果|例如|示例|错误说法|错误表述|误称|有人称|有人声称|不能说|不应说|不要说|should|suppose|example|someone\s+claims)[^。；;\n]{0,40}$", re.I)
_SYNTAX = re.compile(r"字面量|直接写|写成|表达式形式|源码形式|代码形式|写法|语法|源码中(?:的)?表达式")
_QUOTES = (("“", "”"), ("「", "」"), ('"', '"'))


def claims(text):
    rows = []
    for pattern, math_only in ((_ROLE_CLAIM, False), (_EXPR_CLAIM, True), (_REVERSE_CLAIM, True)):
        for match in pattern.finditer(text):
            if pattern is _REVERSE_CLAIM and re.search(r"[+*/-]\s*$", text[max(0, match.start()-30):match.start()]):
                continue
            rows.append({"start": match.start(), "end": match.end(), "claim": match.group(), "value": match["value"],
                         "role": None if math_only else "numerator" if match["role"].lower().startswith(("分子", "numerator")) else "length_base",
                         "expression": re.sub(r"\s|_", "", match["expression"].lower()), "op": match["op"], "math_only": math_only})
    filtered = []
    for row in sorted(rows, key=lambda row: (row["start"], -row["end"])):
        if not any(row["start"] >= prior["start"] and row["end"] <= prior["end"] for prior in filtered):
            filtered.append(row)
    return filtered


def protected(text, claim):
    # Relation negation is a supported assertion, unlike a numeric suggestion.
    before = text[max(0, claim["start"] - 60):claim["start"]]
    left = max(text.rfind(mark, 0, claim["start"]) for mark in ("。", "；", ";", "\n")) + 1
    right = min((p for mark in ("。", "；", ";", "\n") if (p := text.find(mark, claim["end"])) >= 0), default=len(text))
    context = text[left:right]
    quoted = False
    for opening, closing in _QUOTES:
        prefix = text[left:claim["start"]]
        if opening == closing:
            inside = prefix.count(opening) % 2 == 1
        else:
            inside = prefix.rfind(opening) > prefix.rfind(closing)
        quoted = quoted or (inside and closing in text[claim["end"]:right])
    return bool(_NONASSERTION.search(before) or _SYNTAX.search(context) or quoted)


def expression_value(fact, expression):
    values = fact["bindings"]
    with localcontext() as context:
        context.prec = 600
        return Decimal(values["k1"]) + 1 if expression == "k1+1" else 1 - Decimal(values["b"])


def relation_checks(fields, relations, budget):
    checks = []
    for field, text, refs in fields:
        for claim in claims(text):
            if len(checks) >= budget:
                return checks, True
            candidates = [f for f in relations if (refs is None or f["evidence_id"] in refs)
                          and (f["expression"] == claim["expression"] if claim["math_only"] else f["role"] == claim["role"])]
            subjects = {f["subject"] for f in candidates if f["subject"] != "module" and f["subject"] in text}
            if subjects:
                candidates = [f for f in candidates if f["subject"] in subjects]
            unique = {(f["sha"], f["path"], f["subject"], f["value"], f["bindings"]["k1"], f["bindings"]["b"]) for f in candidates}
            status, reason = "insufficient", "此引用范围没有唯一可识别的评分公式及参数绑定。"
            expected = []
            for fact in candidates:
                rhs = expression_value(fact, claim["expression"])
                lhs = Decimal(fact["value"])
                statement = f"{fact['name']} {fact['value']} {'=' if lhs == rhs else '≠'} {claim['expression']}（{rhs}）"
                expected.append({**fact, "statement": statement})
            if protected(text, claim):
                reason = "包含建议、假设、引用或源码写法说明；当前关系检查器不判定此类表述。"
            elif len(unique) == 1:
                fact = candidates[0]
                try:
                    value = Decimal(claim["value"]) if claim["value"] and len(claim["value"]) <= 100 else None
                    rhs = expression_value(fact, claim["expression"])
                    negative = claim["op"] in ("并不等于", "不等于", "并不是", "不是", "并非", "不对应", "!=", "≠")
                    if claim["value"] and (value is None or not value.is_finite() or value.copy_abs() >= Decimal("1e100")):
                        reason = "数值超出检查器范围，未验证。"
                    else:
                        if claim["math_only"]:
                            consistent = (rhs != value) if negative else (rhs == value)
                        else:
                            source_value = Decimal(fact["value"])
                            consistent = (value is None or value == source_value) and ((source_value != rhs) if negative else (source_value == rhs))
                        status = "matched" if consistent else "conflict"
                        reason = "该数学关系与所引公式的参数一致；其他解释及运行行为未验证。" if consistent else "此数学关系与所引评分公式的数值绑定冲突；直接使用字面量不改变数值之间的关系。"
                except InvalidOperation:
                    reason = "数值无法解析，未验证。"
            checks.append({"field": field, "claim": claim["claim"], "observed": claim["value"] or claim["expression"],
                           "name": claim["expression"], "kind": "bm25_relation", "status": status, "reason": reason,
                           "expected": expected, "original_text": text})
    return checks, False
