"""Bounded source retrieval; Python AST parsing never imports or executes source."""
import ast
import math
import re
from collections import Counter

from .knowledge import tokenize

VERSION = "code-bm25-v1"
MAX_LINES = 80
MAX_CHARS = 6000
OVERLAP = 12


def code_tokens(text):
    terms = []
    for word in re.findall(r"[A-Za-z_][A-Za-z_0-9]*|[0-9]+|[\u4e00-\u9fff]+", text):
        if re.fullmatch(r"[\u4e00-\u9fff]+", word):
            terms.extend(tokenize(word))
            continue
        terms.append(word.lower())
        split = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", word)
        split = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", split)
        parts = [part.lower() for part in split.split("_") if part]
        if len(parts) > 1:
            terms.extend(parts)
    return terms


def declarations(text):
    tree = ast.parse(text)
    found, pending = [], [(tree, "")]
    while pending:
        node, parent = pending.pop()
        scope = parent
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope = parent + "." + node.name if parent else node.name
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            found.append({"symbol": scope, "symbol_start": start,
                          "symbol_end": node.end_lineno, "definition_line": node.lineno})
        pending.extend((child, scope) for child in ast.iter_child_nodes(node))
    return sorted(found, key=lambda d: (d["symbol_start"], -d["symbol_end"]))


def chunks(path, text):
    lines, symbols, failed = text.splitlines(), [], False
    if path.lower().endswith(".py"):
        try:
            symbols = declarations(text)
        except (SyntaxError, ValueError, RecursionError):
            failed = True  # Unsupported grammar or malformed source remains searchable.
    starts = {}
    for symbol in symbols:
        starts.setdefault(symbol["symbol_start"], []).append(symbol)
    regions, active, region_start, previous = [], [], 1, None
    for number in range(1, len(lines) + 1):
        while active and active[-1]["symbol_end"] < number:
            active.pop()
        active.extend(starts.get(number, []))
        owner = active[-1] if active else None
        if owner != previous:
            if region_start < number:
                regions.append((region_start, number - 1, previous))
            region_start, previous = number, owner
    if lines:
        regions.append((region_start, len(lines), previous))
    output, omitted = [], 0
    for first, last, symbol in regions:
        start = first
        while start <= last:
            end, size = start - 1, 0
            while end < last and end - start + 1 < MAX_LINES:
                length = len(lines[end]) + (1 if end >= start else 0)
                if size + length > MAX_CHARS:
                    break
                size += length
                end += 1
            if end < start:  # Keep exact line citations; never silently truncate a long line.
                omitted += 1
                start += 1
                continue
            output.append({"path": path, "content": "\n".join(lines[start - 1:end]),
                           "line_start": start, "line_end": end, "total_lines": len(lines),
                           "chunk_kind": "python_ast" if symbol else "line_window",
                           "symbol": symbol["symbol"] if symbol else None,
                           "symbol_start": symbol["symbol_start"] if symbol else None,
                           "symbol_end": symbol["symbol_end"] if symbol else None,
                           "definition_line": symbol["definition_line"] if symbol else None,
                           "partial_symbol": bool(symbol and (start > symbol["symbol_start"] or end < symbol["symbol_end"]))})
            if end == last:
                break
            start = end - min(OVERLAP, (end - start + 1) // 3) + 1
    return output, omitted, failed


def ranked_search(files, query, limit=8):
    """files contain already-redacted UTF-8 text, scoped to one immutable Git tree."""
    corpus, omitted_lines, parse_failures = [], 0, 0
    for file in files:
        pieces, omitted, failed = chunks(file["path"], file["text"])
        corpus.extend({**piece, "redacted": file.get("redacted", False)} for piece in pieces)
        omitted_lines += omitted
        parse_failures += int(failed)
    terms = set(code_tokens(query))
    if not terms or not corpus:
        return {"results": [], "searched_chunks": len(corpus), "omitted_lines": omitted_lines, "parse_fallback_files": parse_failures}
    counts = [Counter(code_tokens(c["content"]) + code_tokens(c["path"]) * 2 + code_tokens(c["symbol"] or "") * 3) for c in corpus]
    frequency = Counter(term for count in counts for term in terms if term in count)
    average = sum(sum(count.values()) for count in counts) / len(counts) or 1
    identifiers = {word.lower() for word in re.findall(r"[A-Za-z_][A-Za-z_0-9]*", query)}
    ranked = []
    for chunk, count in zip(corpus, counts):
        matched = terms.intersection(count)
        if not matched:
            continue
        length, score = sum(count.values()), 0.0
        for term in sorted(matched):
            tf = count[term]
            idf = math.log(1 + (len(corpus) - frequency[term] + .5) / (frequency[term] + .5))
            score += idf * tf * 2.2 / (tf + 1.2 * (.25 + .75 * length / average))
        symbol = chunk["symbol"] or ""
        exact_definition = identifiers.intersection(part.lower() for part in symbol.split("."))
        # Definition bonus only belongs to the window containing its declaration.
        if symbol and chunk["line_start"] <= chunk["definition_line"] <= chunk["line_end"]:
            symbol_matches = terms.intersection(code_tokens(symbol))
            score += 6 * len(symbol_matches) / len(terms)
            score += 4 * len(exact_definition) / max(1, len(identifiers))
        if chunk["path"].lower() in query.lower():
            score += 5
        ranked.append({**chunk, "score": round(score, 6), "matched_terms": sorted(matched)[:20], "retrieval_method": VERSION})
    ranked.sort(key=lambda c: (-c["score"], c["path"], c["line_start"]))
    selected = []
    for candidate in ranked:
        duplicate = False
        for hit in selected:
            if candidate["path"] != hit["path"]:
                continue
            overlap = max(0, min(candidate["line_end"], hit["line_end"]) - max(candidate["line_start"], hit["line_start"]) + 1)
            if overlap / min(candidate["line_end"] - candidate["line_start"] + 1, hit["line_end"] - hit["line_start"] + 1) > .5:
                duplicate = True
                break
        if not duplicate:
            selected.append(candidate)
        if len(selected) == limit:
            break
    return {"results": selected, "searched_chunks": len(corpus), "omitted_lines": omitted_lines, "parse_fallback_files": parse_failures}


def legacy_search(files, query, limit=5):
    """Frozen v0.9 line-keyword baseline used only by the fixed benchmark."""
    terms, hits = set(tokenize(query)), []
    for file in files:
        lines, used = file["text"].splitlines(), set()
        for number, line in enumerate(lines, 1):
            matches = terms.intersection(tokenize(line))
            if not matches or number in used:
                continue
            start, end = max(1, number - 5), min(len(lines), number + 8)
            used.update(range(start, end + 1))
            content = "\n".join(lines[start - 1:end])
            if len(content) <= 16000:
                hits.append({"path": file["path"], "content": content, "line_start": start, "line_end": end,
                             "score": len(matches) + len(terms.intersection(tokenize(file["path"]))) * .5})
    return sorted(hits, key=lambda c: (-c["score"], c["path"], c["line_start"]))[:limit]
