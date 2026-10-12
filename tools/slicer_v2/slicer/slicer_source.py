"""Original-source recovery and lossless clustering for the offline slicer."""

import copy
import re
from bisect import bisect_right
from pathlib import Path, PurePosixPath

SOURCE_SUFFIXES = {
    ".c",
    ".h",
    ".cc",
    ".cpp",
    ".cxx",
    ".cp",
    ".ccm",
    ".cxxm",
    ".c++m",
    ".c++",
    ".hh",
    ".hpp",
    ".hxx",
    ".hp",
    ".h++",
    ".ipp",
    ".tpp",
    ".inl",
    ".inc",
    ".tcc",
    ".i",
    ".ii",
    ".ixx",
    ".cppm",
}


def relative_file(value: str, repo: Path) -> str | None:
    value = value.replace("\\", "/")
    root = repo.as_posix().rstrip("/")
    if value.startswith(root + "/"):
        value = value[len(root) + 1 :]
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or re.match(r"^[A-Za-z]:", value):
        return None
    return path.as_posix()


def normalize_points(points: list[dict], sources: dict, repo: Path) -> tuple[set, int]:
    result = set()
    invalid = 0
    for point in points:
        file = relative_file(point["file"], repo)
        line = point["line"]
        if type(line) is not int or file not in sources or not 1 <= line <= len(sources[file]):
            invalid += 1
        else:
            result.add((file, line))
    return result, invalid


def _point(raw: object, sources: dict, repo: Path) -> dict | None:
    mapped, _ = normalize_points([raw], sources, repo)
    if not mapped:
        return None
    file, line = mapped.pop()
    return {"file": file, "line": line}


def _order(p: dict) -> tuple:
    if "source" in p:
        return (_order(p["source"]), _order(p["sink"]))
    return (p["file"], p["line"], p["kind"])


def _sorted(points: list[dict]) -> list[dict]:
    """Deduplicated, in source order (file, line, kind)."""
    return sorted({_order(p): p for p in points}.values(), key=_order)


def source_sink(case: str, entry: dict, payload: dict | None, sources: dict, repo: Path) -> dict:
    """Where this slice's analysis started and ended, for comparison with a GT source/sink.

    Case A traces from the candidate method's parameters: each kept flow's head is a source
    (`parameter`) and its last element the sink argument it reached (`flow_end`); `pairs` keeps
    which source reached which sink. Case B starts AT the sink -- the candidate call (`entry`)
    -- and walks back; the boundaries where that walk stopped are its sources, kind = the
    boundary reason (`entry_parameter`, `external_or_unresolved_call`, `dependency_root`).
    These are what the slicer treated as source/sink, not a judgement that they are the
    vulnerable pair. `payload` None = no analysis (degraded slice): Case B still knows its
    sink, Case A knows neither. A Case A payload WITHOUT `endpoints` (the query's Try failed
    -> `endpoints_error`, kept in `analysis`; or a payload from before the field existed) is
    `endpoints_unavailable`: not recorded, which is not the same as `flow_endpoints` with zero
    pairs. Points outside the restored sources are dropped and counted in `unmapped`, as
    `lines` drops them.
    """
    found_sources, sinks, pairs, unmapped = [], [], [], 0

    def keep(raw: object, kind: str) -> dict | None:
        nonlocal unmapped
        point = _point(raw, sources, repo)
        if point is None:
            unmapped += 1
            return None
        return {**point, "kind": kind}

    if case == "A":
        if payload is None:
            basis = "none"
        elif "endpoints" in payload:
            basis = "flow_endpoints"
        else:
            basis = "endpoints_unavailable"
        for pair in (payload or {}).get("endpoints", []):
            source, sink = keep(pair["source"], "parameter"), keep(pair["sink"], "flow_end")
            found_sources += [source] if source else []
            sinks += [sink] if sink else []
            if source and sink:
                pairs.append({"source": source, "sink": sink})
    else:
        basis = "candidate_only" if payload is None else "backward_boundaries"
        sink = keep(entry, "candidate_call")
        sinks += [sink] if sink else []
        for boundary in (payload or {}).get("boundaries", []):
            source = keep(boundary["location"], boundary["reason"])
            found_sources += [source] if source else []
    return {
        "basis": basis,
        "sources": _sorted(found_sources),
        "sinks": _sorted(sinks),
        "pairs": _sorted(pairs),
        "unmapped": unmapped,
    }


def merge_source_sink(a: dict, b: dict) -> dict:
    """Union for a cluster; the basis stays one word only when both members share it."""
    return {
        "basis": a["basis"] if a["basis"] == b["basis"] else "mixed",
        **{k: _sorted(a[k] + b[k]) for k in ("sources", "sinks", "pairs")},
        "unmapped": a["unmapped"] + b["unmapped"],
    }


def mask_non_code(text: str) -> str:
    """Keep offsets/newlines while hiding delimiters inside comments and C/C++ strings."""
    pattern = re.compile(
        r'R"(?P<delimiter>[^\s()\\]{0,16})\(.*?\)(?P=delimiter)"'
        r'|/\*.*?\*/|//(?:[^\n]*\\\n)*[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
        re.DOTALL,
    )
    masked = pattern.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), text)
    # A macro's unmatched braces must not become enclosing function braces.
    return re.sub(
        r"^[ \t]*\#(?:[^\n]*\\\n)*[^\n]*",
        lambda m: re.sub(r"[^\n]", " ", m.group()),
        masked,
        flags=re.MULTILINE,
    )


def is_initializer(header: str) -> bool:
    """Keep data braces whole; omitting values can change an inferred array size."""
    header = header.strip()
    if re.match(r"(?:if|for|while|switch|catch|else|do|try|namespace)\b", header):
        return False
    if re.search(r"(?<![=!<>])=(?!=)", header):
        return True
    if re.match(r"(?:class|struct|union|enum)\s+\w+\s*(?::[^;]*)?$", header):
        return False
    return bool(header) and not re.search(
        r"\)\s*(?:(?:const|noexcept|override|final)\s*)*$", header
    )


def syntax_regions(masked: str) -> tuple[list, list, dict, set]:
    stack: list[tuple[str, int]] = []
    pairs: list[tuple[int, int, str]] = []
    statement_start = 0
    statements = []
    headers = {}
    initializers = set()
    for offset, char in enumerate(masked):
        if char in "({[":
            if char == "{":
                headers[offset] = statement_start
                if any(pos in initializers for _, pos in stack) or is_initializer(
                    masked[statement_start:offset]
                ):
                    initializers.add(offset)
                statement_start = offset + 1
            stack.append((char, offset))
        elif char in ")}]" and stack and stack[-1][0] == {"}": "{", ")": "(", "]": "["}[char]:
            opening, begin = stack.pop()
            pairs.append((begin, offset, opening))
            if char == "}":
                statement_start = offset + 1
        elif char == ";" and not any(c in "([" for c, _ in stack):
            statements.append((statement_start, offset))
            statement_start = offset + 1
    return pairs, statements, headers, initializers


def recover_lines(source: list[str], selected: set[int]) -> set[int]:
    """Restore original syntax; data initializers stay whole, control bodies may be sliced.

    Joern adds ancestors/signatures/returns first. Parentheses protect multiline
    calls and for-loop semicolons. This pass does not guarantee compilability.
    """
    masked = mask_non_code("\n".join(source))
    starts = [0] + [m.end() for m in re.finditer("\n", masked)]
    line_at = lambda offset: bisect_right(starts, offset)  # noqa: E731
    pairs, statements, headers, initializers = syntax_regions(masked)

    def span(begin: int, end: int) -> set[int]:
        while begin < end and masked[begin].isspace():
            begin += 1
        return set(range(line_at(begin), line_at(end) + 1))

    groups = [span(a, b) for a, b in statements]
    groups += [span(a, b) for a, b, kind in pairs if kind != "{"]
    for begin, end, _ in pairs:
        if begin in initializers:
            terminator = re.match(r"\s*;", masked[end + 1 :])
            last = end + (terminator.end() if terminator else 0)
            groups.append(span(headers[begin], last))
    braces = [(line_at(a), line_at(b), span(headers[a], a)) for a, b, kind in pairs if kind == "{"]
    skeletons = []
    for begin, end, kind in pairs:
        if kind != "{" or begin in initializers:
            continue
        header = masked[headers[begin] : begin].strip()
        body = masked[begin : end + 1]
        control = re.search(r"\b(?:switch|for|while|do)\b", header)
        function = not re.search(r"\b(?:if|else|switch|for|while|catch)\b", header) and bool(
            re.search(r"\)\s*(?:const\s*)?$", header)
        )
        if control or (function and re.search(r"\bgoto\b", body)):
            structural = set()
            for match in re.finditer(
                r"\b(?:break|continue)\s*;|\bgoto\s+\w+\s*;|"
                r"\bcase\s+[^;{}]+?:|\bdefault\s*:|(?m:^[ \t]*\w+\s*:(?!:))",
                body,
            ):
                structural |= span(begin + match.start(), begin + match.end() - 1)
            skeletons.append((line_at(begin), line_at(end), structural))
    result = set(selected)
    changed = True
    while changed:
        before = len(result)
        for group in groups:
            if result & group:
                result |= group
        for begin, end, header in braces:
            if any(begin <= line <= end for line in result) or result & header:
                result |= header | {begin, end}
        for begin, end, structural in skeletons:
            if any(begin <= line <= end for line in result):
                result |= structural
        changed = len(result) != before
    return result


def recover_points(points: set[tuple[str, int]], sources: dict) -> set[tuple[str, int]]:
    return {
        (file, line)
        for file in sorted({file for file, _ in points})
        for line in recover_lines(sources[file], {n for f, n in points if f == file})
    }


def assemble(points: set[tuple[str, int]], sources: dict) -> str:
    chunks = []
    for file in sorted({file for file, _ in points}):
        chunks.append(f"/* ===== {file} ===== */")
        chunks.extend(sources[file][line - 1] for f, line in sorted(points) if f == file)
    return "\n".join(chunks)


def cluster(slices: list[dict], threshold: float, sources: dict) -> list[dict]:
    """Choose large representatives first, then repeat until no absorption is possible.

    A absorbs B only when the overlap covers at least `threshold` of BOTH: B's lines and A's
    current lines. Measuring only B made a small slice merge into any larger one that contained
    it, and since A grows by union, a representative snowballed into hundreds of unrelated
    candidates (ffmpeg: 453 Case B candidates in one 2,330-line slice). Union can enable another
    absorption; fixed-point passes avoid an order-dependent missed merge. IDs are allocated
    before this function and are never recycled.
    """
    remaining = {item["id"]: copy.deepcopy(item) for item in slices}
    points = {
        key: {(p["file"], p["line"]) for p in value["lines"]} for key, value in remaining.items()
    }
    changed = True
    while changed:
        changed = False
        order = sorted(remaining, key=lambda key: (-len(points[key]), key))
        for aid in order:
            if aid not in remaining:
                continue
            for bid in order:
                if bid == aid or bid not in remaining or not points[bid] or not points[aid]:
                    continue
                if remaining[aid]["case"] != remaining[bid]["case"]:
                    continue
                shared = len(points[bid] & points[aid])
                if shared / len(points[bid]) < threshold or shared / len(points[aid]) < threshold:
                    continue
                a, b = remaining[aid], remaining.pop(bid)
                points[aid] |= points[bid]
                a["absorbed"] = sorted(a["absorbed"] + [bid] + b["absorbed"])
                if "members" in a:
                    a["members"] = sorted(set(a["members"]) | set(b["members"]))
                    # Detailed analysis belongs to a member, not to the representative.
                    a.setdefault("member_analysis", {a["candidate_key"]: a["analysis"]})
                    a["member_analysis"].update(
                        b.get("member_analysis", {b["candidate_key"]: b["analysis"]})
                    )
                    if "source_sink" in a and "source_sink" in b:
                        a["source_sink"] = merge_source_sink(a["source_sink"], b["source_sink"])
                    for reason, locations in b["line_provenance"].items():
                        a["line_provenance"][reason] = sorted(
                            {tuple(p) for p in a["line_provenance"][reason]}
                            | {tuple(p) for p in locations}
                        )
                counts = (a["flow_count"], b["flow_count"])
                a["flow_count"] = None if None in counts else sum(counts)
                a["truncated"] |= b["truncated"]
                changed = True
    for key, item in remaining.items():
        item["lines"] = [{"file": f, "line": n} for f, n in sorted(points[key])]
        item["code"] = assemble(points[key], sources)
    return [remaining[key] for key in sorted(remaining)]
