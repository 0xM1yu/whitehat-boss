#!/usr/bin/env python3
"""source·sink를 지정해 취약점 슬라이스 두 개를 만든다 (Python 3.11+, 표준 라이브러리만).

`slicer.py`는 저장소 전체에서 후보를 스스로 찾아 자른다. 이 모듈은 후보 발견을 건너뛰고
지정한 위치에서 시작한다.

- **backward** — sink 노드에서 데이터가 **오는 쪽**(DDG 역방향)으로 따라가다 source에 닿으면 그
  가지를 멈춘다. source가 아닌 다른 입력(길이 변수 등)으로 가는 가지는 남는다. source가 없으면
  멈출 곳 없이 역추적만 한다.
- **forward** — source 노드에서 데이터가 **쓰이는 쪽**(DDG 정방향)으로 따라가다 sink에 닿으면 그
  가지를 멈춘다. sink에 닿았으면 **sink로 가는 경로 위의 노드만 남긴다**(`pruned_to_target`) —
  sink로 안 가는 가지는 버린다. 단 sink 함수 안에서 sink 줄 이후에 방문한 노드는 남긴다
  (`kept_after_target` — sink 직후의 실제 과다 읽기·쓰기). 못 닿았으면(sink가 source에 제어 의존으로만 이어질 때 등) **source
  함수 안의 노드만** 남긴다(`pruned_to_start_method`). source가 없으면 만들지 않는다.

두 방향 다 함수 경계를 호출 지점 스택으로 넘는다 — backward는 upstream `slicer_backward`의
규칙 그대로(파라미터 → 호출자 인자, 호출 → 피호출 return), forward는 그 거울(인자 → 피호출
파라미터, return → 호출 지점). 그 뒤 문맥 보충·원문 복원은 upstream과 같은 함수를 쓴다
(`context_query` 고정점 · `recover_points` · `assemble`).

## 노드를 고르는 법 — 정답을 보고 슬라이스를 고치지 않는다

GT는 (파일, 줄, 함수, 심볼 문자열)을 준다. 고르는 것은 **시작·멈춤 노드뿐**이다.

1. 줄: GT 줄 ±2 안에서 심볼 문자열(공백 무시)이 든 줄 중 GT 줄에 가장 가까운 것.
   없으면 심볼의 식별자를 절반 이상·가장 많이 담은 코드 줄, 그것도 없으면 GT 줄 그대로
   (둘 다 `symbol_on_line: false`).
2. 노드: 그 줄의 노드(CALL · IDENTIFIER · LITERAL · METHOD_PARAMETER_IN · RETURN)를 Joern에서
   받아 심볼과 맞춘다 — 코드가 같음 > 이름이 같은 호출 > 이름이 같은 식별자·파라미터 >
   심볼을 품는 가장 작은 노드 > 심볼 안에 든 가장 큰 노드. 같은 점수가 여럿이면 다 쓴다.

root_cause 줄은 이 모듈에 안 넘어온다. 담겼는지는 `run_gt.py`가 결과를 보고 기록만 한다.

## 사용

```
python slicer_target.py --repo /work/repo --spec spec.json --out slices.json \\
  --joern-endpoint http://127.0.0.1:16242 --joern-version-command /opt/joern/joern-cli/joern \\
  --backward-depth 30 --backward-nodes 5000
```

spec.json: `{"sink": {"file","line","line_end","function","symbol"}, "source": {...} | null}`
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

import slicer as base
import slicer_queries as queries
from input_evidence import input_evidence_query
from slicer_errors import QueryError
from slicer_source import assemble, normalize_points, recover_points, relative_file

# 상승 갈래 순위의 비제품 경로 — attach `index_graph.is_nonproduct_path`와 같은 규칙(같은 목록에서 만든다)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "attach"))
from index_graph import _FUZZ_PREFIXES, _NONPRODUCT_DIRS  # noqa: E402

NONPRODUCT_PATTERN = "(?:^|/)(?:" + "|".join(
    [re.escape(d) for d in sorted(_NONPRODUCT_DIRS)] + [re.escape(p) + "[^/]*" for p in _FUZZ_PREFIXES]) + ")/"

LINE_TOLERANCE = 2
# 같은 점수로 묶인 노드를 시작점으로 쓰는 상한. 한 줄의 같은 식별자 반복(`a[i] = a[i] + 1`)은
# 몇 개면 충분하고, 그보다 많으면 매칭이 아니라 줄 전체를 고른 것이다.
MAX_TIED_NODES = 8
LOCATE_LABELS = ("CALL", "IDENTIFIER", "LITERAL", "METHOD_PARAMETER_IN", "RETURN")
IDENT = re.compile(r"[A-Za-z_]\w*")

# 이 모듈의 `slicer_target` 계약 — upstream slice-v2 계약 위에 더한 것.
TARGET_VERSION = "target-v1"
TARGET_LIMITATIONS = [
    "targeted mode: start/stop nodes are matched from a GT symbol string on a line; a symbol "
    "that matches nothing falls back to the outermost nodes of that line",
    "forward: data entering a call through an argument continues into the call result and, "
    "for an assignment, its target; an internal callee's parameter is followed, but output "
    "arguments of external calls (memcpy dst from src) are not modelled",
    "forward: a call chosen as the source seeds the call and all its arguments, so later uses "
    "of non-tainted arguments are included (over-approximation)",
    "a branch stops where it reaches the other end (source for backward, sink for forward); "
    "other branches run until the dependency ends or the depth/state/call-depth limits",
    "forward: when the sink is reached, only the states that lead to it are kept "
    "(pruned_to_target), plus the visited states in the sink's own method at or after the sink "
    "line (kept_after_target); what the tainted data does in other functions after the sink is "
    "not in the forward slice",
    "the stop node's line is recorded as line_provenance.target_anchor even when the walk does "
    "not reach it (it is not added to the slice lines)",
    "forward: when the sink is not reached (e.g. it depends on the source only through a "
    "control dependence), only the source function's own nodes are kept (pruned_to_start_method)",
    "targeted context keeps only returns before the last slice line of their function, does "
    "not add repo-wide assignments to referenced variables, and adds method-wide goto labels "
    "but not case/default labels of switches the slice does not touch",
    "build configuration: macros that must be defined for the start/stop lines to be compiled "
    "(#ifdef X / #if X / #if defined(X) true branch, #ifndef X #else branch; not names starting "
    "with '_') are passed to c2cpg as --define (target.defines); every other macro stays "
    "undefined, so code under other configurations is still absent",
]


CONDITIONAL = re.compile(r"\s*#\s*(ifdef|ifndef|if|elif|else|endif)\b(.*)")
# `#if NAME` · `#if defined(NAME)` · `#if defined NAME` — 이름 하나짜리 조건만 푼다
SINGLE_NAME = re.compile(r"\s*(?:defined\s*\(\s*([A-Za-z_]\w*)\s*\)|defined\s+([A-Za-z_]\w*)|([A-Za-z_]\w*))\s*$")


def activating_defines(lines: list[str], target: int) -> list[str]:
    """`target` 줄을 감싼 전처리 조건 중, 매크로를 **정의해야** 그 줄이 살아나는 것의 이름.

    c2cpg는 정의 안 된 매크로를 미정의로 보고 그 분기를 버린다. 분석할 줄이 `#ifdef X` 안에
    있으면 노드가 하나도 안 생겼다(jq `USE_DECNUM` 안의 `jv_number_tostring`, ngiflib
    `NGIFLIB_NO_FILE` 안의 메모리 입력 분기). `#ifdef X`·`#if X`·`#if defined(X)`의 참 분기와
    `#ifndef X`의 `#else` 분기만 본다 — 정의를 빼야 사는 분기(`#ifndef X` 참, `#ifdef X` 거짓)는
    기본값(미정의) 그대로다. 밑줄로 시작하는 이름(`_WIN32`·`__APPLE__` 등 컴파일러·플랫폼
    매크로)은 켜지 않는다 — 다른 플랫폼의 코드가 전부 살아난다."""
    stack: list[list] = []  # [이름 | None, 정의해야 사는가]
    joined = ""
    for number, raw in enumerate(lines[: max(0, target - 1)], 1):
        joined = joined + raw
        if raw.rstrip().endswith("\\"):
            joined = joined.rstrip()[:-1]
            continue
        line, joined = joined, ""
        match = CONDITIONAL.match(line)
        if match is None:
            continue
        kind, rest = match.group(1), re.sub(r"/\*.*?\*/|//.*", "", match.group(2))
        if kind in ("ifdef", "ifndef"):
            name = (IDENT.match(rest.strip()) or [None])[0]
            stack.append([name, kind == "ifdef"])
        elif kind == "if":
            single = SINGLE_NAME.match(rest)
            name = next((g for g in single.groups() if g), None) if single else None
            stack.append([name, True])
        elif kind == "elif":
            if stack:
                stack[-1] = [None, True]
        elif kind == "else":
            if stack and stack[-1][0] is not None:
                stack[-1][1] = not stack[-1][1]
        elif kind == "endif" and stack:
            stack.pop()
    return [name for name, wanted in stack if name and wanted and not name.startswith("_")]


WRITE_THROUGH = re.compile(
    r"\*\s*\(?\s*([A-Za-z_]\w*)\s*(?:\+\+|--)?\s*\)?\s*=(?!=)|\b([A-Za-z_]\w*)\s*\[[^\]]*\]\s*=(?!=)"
)


def pointer_steps(lines: list[str], sink_lines: list[int], low: int, high: int) -> set[int]:
    """sink 줄이 **포인터를 통해 쓰는** 변수(`*fn = …`·`p[i] = …`)를 같은 함수의 `low..high` 줄에서
    움직이거나(`fn++`·`fn += n`·`--fn`) 그것을 통해 쓰는 줄(`*fn++ = '.'`·`*fn = '\\0'`).

    Joern DDG는 `fn++`(postIncrement)를 `fn`의 정의로 잇지 않아, 쓰기 위치를 정하는 포인터 이동이
    슬라이스에서 빠졌다(libmspack kwajd: `*fn++ = '.'`·`*fn = '\\0'` — 13바이트 버퍼를 넘는 쓰기의
    offset이 이 줄들로 정해진다). 포인터 선언 `char *fn = alloc(13)`도 같은 꼴이라 함께 잡힌다 —
    버퍼 크기를 정하는 할당이다. 어휘 보충이고 `line_provenance.pointer_steps`로 따로 적는다."""
    def code(number: int) -> str:
        return re.sub(r"/\*.*?\*/|//.*|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'", "''",
                      lines[number - 1]) if 1 <= number <= len(lines) else ""

    names = {a or b for n in sink_lines for a, b in WRITE_THROUGH.findall(code(n))}
    found: set[int] = set()
    for name in names:
        v = re.escape(name)
        step = re.compile(
            rf"\b{v}\s*(?:\+\+|--|[+\-]=(?!=))|(?:\+\+|--)\s*{v}\b"
            rf"|\*\s*\(?\s*{v}\s*(?:\+\+|--)?\s*\)?\s*=(?!=)|\b{v}\s*\[[^\]]*\]\s*=(?!=)"
        )
        found |= {n for n in range(max(1, low), min(len(lines), high) + 1) if step.search(code(n))}
    return found


def field_writes(lines: list[str], sink_lines: list[int], low: int, high: int) -> set[int]:
    """sink 줄의 호출에 **통째로 넘기는** 변수(`LoadGif(gif)`의 gif)의 필드에 쓰는 줄 — `low..high`(sink
    함수, sink 줄 앞) 안. Joern DDG는 `gif->input.bytes = buffer`(필드 쓰기)를 `gif`의 정의로 잇지 않아
    backward가 그 객체에 무엇이 들어갔는지를 놓쳤다(ngiflib: 취약 입력 분기 `input.bytes = buffer`).
    어휘 보충이고 `line_provenance.field_writes`로 따로 적는다."""
    def code(number: int) -> str:
        return re.sub(r"/\*.*?\*/|//.*|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'", "''",
                      lines[number - 1]) if 1 <= number <= len(lines) else ""

    names: set[str] = set()
    for n in sink_lines:
        for call in re.finditer(r"\b[A-Za-z_]\w*\s*\(([^()]*)\)", code(n)):
            for arg in call.group(1).split(","):
                whole = re.fullmatch(r"\s*&?\s*([A-Za-z_]\w*)\s*", arg)
                if whole:
                    names.add(whole.group(1))
    found: set[int] = set()
    for name in names:
        write = re.compile(rf"\b{re.escape(name)}\s*(?:->|\.)\s*[A-Za-z_][\w.\->\[\]\s]*?=(?!=)")
        found |= {n for n in range(max(1, low), min(len(lines), high) + 1)
                  if n not in sink_lines and write.search(code(n))}
    return found


def squash(text: str) -> str:
    return re.sub(r"\s+", "", text or "").rstrip(";")


def pick_line(sources: dict, spec: dict) -> tuple[int, bool]:
    """심볼이 든 줄 — GT 줄 ±2 안에서 GT 범위에 가장 가까운 것. (줄, 찾았나)."""
    lines = sources.get(spec["file"])
    first, last = spec["line"], spec.get("line_end") or spec["line"]
    symbol = squash(spec.get("symbol") or "")
    if not lines or not symbol:
        return first, False
    low, high = max(1, first - LINE_TOLERANCE), min(len(lines), last + LINE_TOLERANCE)
    span = spec.get("function_span")
    if span:
        # GT 함수 범위(MAP-C1) — 매핑 줄이 함수 밖이면 범위 전체에서, 아니면 ±2를 범위로 자른다
        if not span[0] <= first <= span[1]:
            low, high = max(1, span[0]), min(len(lines), span[1])
        else:
            low, high = max(low, span[0]), min(high, span[1])
    found = []
    for number in range(low, high + 1):
        if symbol in squash(lines[number - 1]):
            distance = 0 if first <= number <= last else min(abs(number - first), abs(number - last))
            found.append((distance, number))
    if not found:
        # 문자열이 그대로인 줄이 없으면 심볼의 식별자를 가장 많이 담은 코드 줄(절반 이상) — patch GT는
        # 줄이 근사 매핑(`mapped: approx`)이고 코드가 바뀐다(libmspack patch: GT 줄 222는 주석, 심볼
        # `*fn = buf[i]`는 수정 후 `*fn++ = buf[i]`). 주석·빈 줄을 고르면 노드가 없어 degraded가 됐다.
        wanted = set(IDENT.findall(spec.get("symbol") or ""))
        scored = []
        for number in range(low, high + 1):
            code = re.sub(r"/\*.*?\*/|//.*", "", lines[number - 1])
            overlap = len(wanted & set(IDENT.findall(code)))
            if wanted and overlap * 2 >= len(wanted):
                distance = 0 if first <= number <= last else min(abs(number - first), abs(number - last))
                scored.append((-overlap, distance, number))
        if scored:
            return min(scored)[2], False
        return first, False
    return min(found)[1], True


def score(node: dict, symbol: str) -> int:
    """심볼과 노드의 맞음 정도. 0이면 후보가 아니다."""
    code, name, label = squash(node["code"]), node["name"], node["label"]
    if not symbol:
        return 0
    if code == symbol:
        return 1000
    if IDENT.fullmatch(symbol):
        if label == "CALL" and name == symbol:
            return 900
        if label in ("IDENTIFIER", "METHOD_PARAMETER_IN") and name == symbol:
            return 800
    if symbol in code:
        return 500 - min(len(code), 400)
    if code in symbol and len(code) >= 2 and label != "LITERAL":
        return 100 + min(len(code), 300)
    return 0


def choose(rows: list[dict], spec: dict) -> tuple[list[dict], dict]:
    """locate 결과에서 시작/멈춤 노드를 고른다. (고른 행들, 기록)."""
    note = {"candidates_on_line": len(rows)}
    if not rows:
        return [], {**note, "match": "no_node_on_line"}
    # 같은 줄을 품는 메서드가 여럿이면(C++ 람다 등) GT 함수 이름 → 가장 좁은 메서드 순.
    def method_rank(row: dict) -> tuple:
        m = row["method"]
        return (m["symbol"] != spec.get("function"), m["line_end"] - m["line"])

    best_method = min(rows, key=method_rank)["method"]
    rows = [r for r in rows if r["method"] == best_method]
    symbol = squash(spec.get("symbol") or "")
    scored = [(score(r, symbol), r) for r in rows]
    top = max(s for s, _ in scored)
    if top > 0:
        picked = [r for s, r in scored if s == top]
        match = "symbol"
    else:
        # 심볼로 못 고르면 그 줄의 가장 바깥 노드들(문장 단위) — 줄 전체를 시작점으로 쓴다.
        depth = min(len(r["ast_path"]) for r in rows)
        picked = [r for r in rows if len(r["ast_path"]) == depth]
        match = "line_outermost"
    picked = sorted(picked, key=lambda r: (r["column"], r["ast_path"]))
    note.update(
        match=match,
        score=top,
        tied=len(picked),
        method=best_method["full_name"],
    )
    # 시작 노드의 함수가 GT 함수와 다르면 기록한다(P4) — 그 줄에 GT 함수의 노드가 없다는 뜻이다
    if spec.get("function") and best_method["symbol"] != spec["function"]:
        note["function_mismatch"] = {"gt": spec["function"], "picked": best_method["symbol"]}
    if len(picked) > MAX_TIED_NODES:
        note["tied_truncated"] = len(picked) - MAX_TIED_NODES
        picked = picked[:MAX_TIED_NODES]
    return picked, note


def in_span(method: dict, span: list[int]) -> bool:
    """노드의 메서드가 GT 함수 범위와 겹치나 — 머리 줄 차이(반환형 줄) 3줄까지 허용."""
    return span[0] - 3 <= method["line"] <= span[1] and method["line_end"] > span[0]


def relocation_lines(lines: list[str], spec: dict, span: list[int], skip: int, limit: int = 3) -> list[int]:
    """GT 함수 범위 안에서 다시 시도할 줄 — 심볼이 든 줄을 연산 자리 먼저(조건식 안에만 나오는 줄은 뒤로:
    수정으로 추가된 검사 `if (len > max) return -1;`가 시작점이 되지 않게), 그다음 GT 줄에 가까운 순.
    심볼 줄이 없으면 심볼 식별자를 절반 이상 담은 줄 상위 2개. 수정 정보는 쓰지 않는다."""
    symbol = squash(spec.get("symbol") or "")
    first = spec["line"]
    low, high = max(1, span[0]), min(len(lines), span[1])
    ranked = []
    for number in range(low, high + 1):
        if number == skip:
            continue
        code = re.sub(r"/\*.*?\*/|//.*", "", lines[number - 1])
        if symbol and symbol in squash(code):
            cond_only = bool(re.match(r"\s*(?:\}\s*)?(?:else\s+)?(?:if|while)\s*\(", code))
            ranked.append((cond_only, abs(number - first), number))
    if ranked:
        return [n for _c, _d, n in sorted(ranked)[:limit]]
    wanted = set(IDENT.findall(spec.get("symbol") or ""))
    scored = []
    for number in range(low, high + 1):
        if number == skip:
            continue
        overlap = len(wanted & set(IDENT.findall(re.sub(r"/\*.*?\*/|//.*", "", lines[number - 1]))))
        if wanted and overlap * 2 >= len(wanted):
            scored.append((-overlap, abs(number - first), number))
    return [n for _o, _d, n in sorted(scored)[:2]]


def selector(row: dict) -> dict:
    return {
        "file": row["method"]["file"],
        "method_full_name": row["method"]["full_name"],
        "method_line": row["method"]["line"],
        "label": row["label"],
        "line": row["line"],
        "column": row["column"],
        "code": row["code"],
        "ast_path": row["ast_path"],
    }


PREFIX = r"""
      val repoPrefix = __REPO__
      def fname(n: nodes.AstNode): String = n.location.filename.replace('\\', '/')
        .stripPrefix(repoPrefix).stripPrefix("./")
"""


def locate_query(repo: str, file: str, line: int) -> str:
    labels = ", ".join(queries.literal(label) for label in LOCATE_LABELS)
    body = (
        queries.LOCATION
        + queries.CALL_IDENTITY
        + PREFIX.replace("__REPO__", queries.literal(repo.rstrip("/") + "/"))
        + r"""
      val wanted = __FILE__
      val line = __LINE__
      val labels = Set(__LABELS__)
      val ms = cpg.method.internal.filter(m => fname(m) == wanted &&
        m.lineNumber.exists(_ <= line) && m.lineNumberEnd.exists(_ >= line)).l
      ujson.Arr.from(ms.flatMap(m => m.ast.l.filter(n =>
        n.lineNumber.getOrElse(-1) == line && labels.contains(n.label)).map(n => ujson.Obj(
          "label" -> n.label, "code" -> n.code,
          "name" -> (n match {
            case c: nodes.Call => c.name
            case i: nodes.Identifier => i.name
            case p: nodes.MethodParameterIn => p.name
            case _ => ""
          }),
          "line" -> n.lineNumber.getOrElse(-1), "column" -> n.columnNumber.getOrElse(-1),
          "ast_path" -> ujson.Arr.from(astPath(n)),
          "method" -> ujson.Obj("file" -> fname(m), "line" -> m.lineNumber.getOrElse(-1),
            "line_end" -> m.lineNumberEnd.getOrElse(-1), "full_name" -> m.fullName,
            "symbol" -> m.name)))))
    """.replace("__FILE__", queries.literal(file))
        .replace("__LINE__", str(int(line)))
        .replace("__LABELS__", labels)
    )
    return queries.envelope("locate", body)


# 시작·멈춤 노드를 selector로 다시 찾는다 — upstream backward_b_query의 sink 선택과 같은 축
# (메서드 · 줄 · 열 · code · AST 경로)에 label을 더했다. 하나가 아니면 질의가 죽는다.
FIND_NODES = r"""
      def findNodes(raw: String): List[nodes.CfgNode] = ujson.read(raw).arr.toList.flatMap { s =>
        val ms = cpg.method.internal.filter(m => m.fullName == s("method_full_name").str &&
          m.lineNumber.getOrElse(-1) == s("method_line").num.toInt &&
          fname(m) == s("file").str).l
        val path = s("ast_path").arr.map(_.num.toInt).toList
        val found = ms.flatMap(_.ast.l).filter(n =>
          n.label == s("label").str && n.lineNumber.getOrElse(-1) == s("line").num.toInt &&
          n.columnNumber.getOrElse(-1) == s("column").num.toInt && n.code == s("code").str &&
          astPath(n) == path).distinct.collect { case c: nodes.CfgNode => c }
        require(found.size == 1, "Target selector is missing or ambiguous: " + ujson.write(s))
        found
      }.distinct
"""

TRAVERSAL = r"""
      val starts = findNodes(__STARTS__)
      require(starts.nonEmpty, "No start node")
      val stops = findNodes(__STOPS__)
      mark("select")
      val stopIds: Set[Long] = stops.flatMap(n => n match {
        case a: nodes.AstNode => a.ast.l.map(_.id)
        case _ => Nil
      }).toSet
      case class State(node: nodes.CfgNode, stack: List[nodes.Call], depth: Int, up: Int = 0)
      def key(n: nodes.CfgNode): String = within("key_inclusive") {
        count("key_calls")
        ujson.write(ujson.Arr(
          n.location.filename, n.lineNumber.getOrElse(-1), n.columnNumber.getOrElse(-1),
          n.label, n.code, Option(n.method).map(_.fullName).getOrElse(""),
          ujson.Arr.from(n match { case a: nodes.AstNode => astPath(a); case _ => Nil })))
      }
      def stateKey(s: State): String = key(s.node) + s.stack.map(key).mkString("[", ",", "]")
      // Ascent fan-out ranking: callers in the parameter's own file first, then product paths
      // (index_graph.is_nonproduct_path, injected), then key order.
      val nonProductRe = __NONPRODUCT__.r
      def callerRank(c: nodes.Call, home: String): (Int, Int, String) = {
        val file = Option(c.method).map(_.filename).getOrElse("")
        (if (file == home) 0 else 1, if (nonProductRe.findFirstIn(file.toLowerCase).isDefined) 1 else 0, key(c))
      }
      // Methods active on this state's call stack (the current one and every caller pushed) --
      // descending into one of them again is recursion.
      def activeMethodIds(s: State, n: nodes.CfgNode): Set[Long] =
        (Option(n.method).toList ++ s.stack.flatMap(c => Option(c.method))).map(_.id).toSet
      val queue = scala.collection.mutable.Queue.empty[State]
      val seen = scala.collection.mutable.Set.empty[String]
      val nodesSeen = scala.collection.mutable.Map.empty[String, nodes.CfgNode]
      val edges = scala.collection.mutable.Set.empty[(String, String)]
      val cuts = scala.collection.mutable.Set.empty[String]
      val boundaries = scala.collection.mutable.Map.empty[String, ujson.Value]
      val reached = scala.collection.mutable.Map.empty[String, ujson.Value]
      // For pruning: which node each visited state is, which states hit a stop node, and which
      // node each boundary belongs to.
      val stateNode = scala.collection.mutable.Map.empty[String, nodes.CfgNode]
      val reachedStates = scala.collection.mutable.Set.empty[String]
      val boundaryNode = scala.collection.mutable.Map.empty[String, String]
      def boundary(n: nodes.CfgNode, reason: String): Unit = {
        val value = ujson.Obj("reason" -> reason, "location" -> loc(n))
        boundaries.update(ujson.write(value), value)
        boundaryNode.update(ujson.write(value), key(n))
      }
      def offer(s: State): Unit = {
        val k = stateKey(s)
        if (!seen.contains(k)) {
          if (s.depth > __MAX_DEPTH__)
            cuts += "__DIRECTION___depth"
          else if (seen.size >= __MAX_NODES__)
            cuts += "__DIRECTION___states"
          else { seen += k; queue.enqueue(s) }
        }
      }
      __SEED__
      while (queue.nonEmpty) {
        val current = queue.dequeue()
        val n = current.node
        nodesSeen.update(key(n), n)
        stateNode.update(stateKey(current), n)
        if (stopIds.contains(n.id)) {
          reached.update(key(n), loc(n))
          reachedStates += stateKey(current)
          boundary(n, "__REACHED__")
        } else {
          val next = scala.collection.mutable.ListBuffer.empty[State]
          __STEP__
          if (next.isEmpty) boundary(n, "__LEAF__")
          next.toList.sortBy(stateKey).foreach(s => {
            offer(s)
            if (seen.contains(stateKey(s))) edges += ((stateKey(current), stateKey(s)))
          })
        }
      }
      mark("traversal")
      // __PRUNE__: once the other end was reached, keep only the states that lead to it (reverse
      // reachability over the traversal edges) -- a branch that never reaches the target is not on
      // a source->sink path. Not reached -> nothing to prune against, keep everything.
      // The walk back stops at a start node: a path that comes round to the start again (recursion
      // -- cJSON parse_object -> parse_string -> the source assignment) is the direct start->target
      // path already kept, and without the stop every branch of the cycle would count as leading
      // to the target.
      val startIds: Set[Long] = starts.flatMap(n => n match {
        case c: nodes.Call => c.id :: c.argument.l.map(_.id)
        case _ => List(n.id)
      }).toSet
      val pruned = __PRUNE__ && reachedStates.nonEmpty
      // Target not reached (the link is a control dependence -- libexpat copyString: the loop
      // `while (s[charsRequired] != 0)` decides charsRequired; a DDG walk never gets there): keep
      // only the start method's own nodes. Without a target to prune against, the walk otherwise
      // follows the value into every caller and their callers.
      val startMethods: Set[String] = starts.flatMap(n => Option(n.method)).map(_.fullName).toSet
      val local = __PRUNE__ && reachedStates.isEmpty
      val keep: Set[String] = if (local) stateNode.toList.collect {
        case (k, n) if Option(n.method).exists(m => startMethods.contains(m.fullName)) => k
      }.toSet else if (!pruned) stateNode.keySet.toSet else {
        val preds = edges.toList.groupMap(_._2)(_._1)
        val out = scala.collection.mutable.Set.empty[String] ++ reachedStates
        val work = scala.collection.mutable.Queue.empty[String] ++ reachedStates
        while (work.nonEmpty) {
          val s = work.dequeue()
          if (!stateNode.get(s).exists(n => startIds.contains(n.id)))
            preds.getOrElse(s, Nil).foreach(p => if (out.add(p)) work.enqueue(p))
        }
        out.toSet
      }
      // After the target: states the walk already visited in the target's own method at or after
      // the target line. Pruning to source->target paths dropped what the tainted value does right
      // after the sink -- the actual over-read/-write (WavPack `bytecnt -= 2` and the second read,
      // netconsd `msg->ncfrag_off = nf_off`, libmspack `*fn = '\0'`).
      val reachedNodes = reachedStates.toList.flatMap(stateNode.get)
      val afterTarget: Set[String] = if (!pruned) Set.empty[String] else stateNode.toList.collect {
        case (k, n) if reachedNodes.exists(r =>
          Option(r.method).map(_.fullName) == Option(n.method).map(_.fullName) &&
          n.lineNumber.getOrElse(-1) >= r.lineNumber.getOrElse(Int.MaxValue)) => k
      }.toSet
      val kept = (keep ++ afterTarget).toList.flatMap(stateNode.get).map(n => key(n) -> n).toMap
      // Keep the interprocedural edges while the CPG is still open.  The attach stage runs
      // after this project/server has been closed, so it cannot reconstruct these links from
      // the line-only slice.  Restrict the payload to retained states and a bounded number of
      // cross-method edges; intra-method line membership is already in `lines`.
      def flowPoint(n: nodes.CfgNode): ujson.Value = ujson.Obj(
        "file" -> n.location.filename, "line" -> n.lineNumber.getOrElse(-1),
        "method" -> Option(n.method).map(_.fullName).getOrElse(""),
        "label" -> n.label, "code" -> n.code)
      val crossEdges = edges.toList.filter { case (a, b) => keep.contains(a) && keep.contains(b) }
        .flatMap { case (a, b) => (stateNode.get(a), stateNode.get(b)) match {
          case (Some(x), Some(y))
            if x.isInstanceOf[nodes.AstNode] && y.isInstanceOf[nodes.AstNode] &&
              Option(x.method).map(_.id) != Option(y.method).map(_.id) =>
              Some(ujson.Obj("from" -> flowPoint(x), "to" -> flowPoint(y)))
          case _ => None
        }}.groupBy(v => ujson.write(v)).toList.sortBy(_._1).map(_._2.head)
      val crossEdgeLimit = 256
      // Lines that hold only after-target nodes (no node on the source->target path).
      val keepLines = keep.toList.flatMap(stateNode.get)
        .map(n => (n.location.filename, n.lineNumber.getOrElse(-1))).toSet
      val afterOnly = (afterTarget -- keep).toList.flatMap(stateNode.get)
        .filterNot(n => keepLines.contains((n.location.filename, n.lineNumber.getOrElse(-1))))
        .collect { case a: nodes.AstNode if a.lineNumber.exists(_ > 0) => loc(a) }
      val locations = kept.toList.sortBy(_._1).flatMap { case (_, n) => n match {
        case a: nodes.AstNode if a.lineNumber.exists(_ > 0) => List(loc(a))
        case _ => Nil
      } }
      mark("locations")
      ujson.Obj("flow_count" -> ujson.Null, "truncated" -> cuts.nonEmpty,
        "lines" -> ujson.Arr.from(locations), "node_count" -> kept.size,
        "state_count" -> seen.size, "dependency_count" -> edges.size,
        "pruned_to_target" -> pruned, "pruned_to_start_method" -> local,
        "kept_after_target" -> (afterTarget -- keep).size,
        "after_target_lines" -> ujson.Arr.from(afterOnly),
        "nodes_before_prune" -> nodesSeen.size,
        "cross_method_flow" -> ujson.Arr.from(crossEdges.take(crossEdgeLimit)),
        "cross_method_flow_truncated" -> (crossEdges.size > crossEdgeLimit),
        "boundaries" -> ujson.Arr.from(boundaries.toList.sortBy(_._1)
          .filter { case (k, _) => boundaryNode.get(k).exists(kept.contains) }.map(_._2)),
        "truncation_reasons" -> ujson.Arr.from(cuts.toList.sorted),
        "stop_requested" -> stops.nonEmpty,
        "target_reached" -> reached.nonEmpty,
        "reached" -> ujson.Arr.from(reached.toList.sortBy(_._1).map(_._2)))
"""

# backward: upstream slicer_backward의 확장 규칙 그대로.
BACKWARD_SEED = r"""
      starts.sortBy(key).foreach(n => n match {
        case c: nodes.Call if c.argument.nonEmpty =>
          c.argument.l.sortBy(key).foreach(a => offer(State(a, Nil, 0)))
        case _ => offer(State(n, Nil, 0))
      })
"""
BACKWARD_STEP = r"""
          n.start.ddgIn.l.sortBy(key).foreach(p =>
            next += State(p, current.stack, current.depth + 1, current.up))
          n match {
            case p: nodes.MethodParameterIn =>
              // An ascent with no stack goes to callers; with many of them (a logging helper called
              // from everywhere -- libarchive `safe_fprintf`) every caller's argument chain came in.
              // Keep __ASCENT_FANOUT__ (0 = all), ranked by callerRank.
              val callers = if (current.stack.nonEmpty) current.stack.take(1)
                            else {
                              val ranked = p.method.callIn.l.sortBy(c => callerRank(c, p.method.filename))
                              if (__ASCENT_FANOUT__ > 0 && ranked.size > __ASCENT_FANOUT__) {
                                cuts += "__DIRECTION___ascent_fanout"
                                ranked.take(__ASCENT_FANOUT__)
                              } else ranked
                            }
              // An ascent (empty stack: any caller) counts against __MAX_ASCENT__; returning from a
              // call the walk descended into does not.
              val up = current.up + (if (current.stack.isEmpty) 1 else 0)
              if (callers.isEmpty) boundary(p, "entry_parameter")
              else if (up > __MAX_ASCENT__) boundary(p, "ascent_limit")
              else callers.foreach(c => c.argument.l.filter(_.argumentIndex == p.index).foreach(a =>
                next += State(a, current.stack.drop(1), current.depth + 1, up)))
            // An operator call that is not an assignment (`*dstptr2`, `a[i]`, `p->f`, `x + y`): go
            // into its operands -- the operator node itself often has no incoming DDG edge, so the
            // walk stopped at the sink (jasper `*dstptr2 = *srcptr2`: 2 nodes).
            case c: nodes.Call if c.name.startsWith("<operator>") && !c.name.contains("ssignment") =>
              c.argument.l.sortBy(key).foreach(a =>
                next += State(a, current.stack, current.depth + 1, current.up))
            case c: nodes.Call if !c.name.startsWith("<operator>") =>
              val callees = c.callee.filterNot(_.isExternal).l
              val active = activeMethodIds(current, n)
              if (callees.isEmpty) boundary(c, "external_or_unresolved_call")
              else if (current.stack.size >= __MAX_CALL_DEPTH__) cuts += "call_depth"
              else callees.foreach(m =>
                // recursion (review plan S1-C2): the same function again only repeats its own lines
                if (active.contains(m.id)) boundary(c, "recursive_call")
                else m.ast.isReturn.l.foreach(r =>
                  next += State(r, c :: current.stack, current.depth + 1, current.up)))
            case _ => ()
          }
"""

# forward: backward의 거울. DDG 정방향 + 인자 → 그 호출(결과) · 대입이면 그 대상 · 내부 피호출의
# 파라미터, return → 호출 지점(스택이 비면 모든 호출자).
FORWARD_SEED = r"""
      starts.sortBy(key).foreach(n => {
        offer(State(n, Nil, 0))
        n match {
          case c: nodes.Call => c.argument.l.sortBy(key).foreach(a => offer(State(a, Nil, 0)))
          case _ => ()
        }
      })
"""
FORWARD_STEP = r"""
          // Forward DDG = outgoing REACHING_DEF edges (schema accessor; ddgIn has no forward twin).
          n._reachingDefOut.collect { case c: nodes.CfgNode => c }.toList.sortBy(key).foreach(p =>
            next += State(p, current.stack, current.depth + 1, current.up))
          // The callee expression of a function-pointer call (`intf->sendrecv` in
          // `rsp = intf->sendrecv(intf, &req)` -- ipmitool) is linked to its call by a RECEIVER edge,
          // not as an argument/AST child the step below sees: the call's value is the walk's next node.
          n._receiverIn.collect { case c: nodes.Call => c }.toList.sortBy(key).foreach(c =>
            next += State(c, current.stack, current.depth + 1, current.up))
          n match {
            case r: nodes.Return =>
              val sites = if (current.stack.nonEmpty) current.stack.take(1)
                          else r.method.callIn.l.sortBy(key)
              if (sites.isEmpty) boundary(r, "exit_return")
              sites.foreach(c => next += State(c, current.stack.drop(1), current.depth + 1, current.up))
            // argumentIndex 0 = the receiver/callee expression of a call (`intf->sendrecv` in
            // `rsp = intf->sendrecv(intf, &req)` -- ipmitool): its value is the call's too.
            case e: nodes.Expression if e.argumentIndex >= 0 =>
              e.astParentOption match {
                case Some(c: nodes.Call) =>
                  next += State(c, current.stack, current.depth + 1, current.up)
                  if (c.name.contains("assignment") && e.argumentIndex != 1)
                    c.argument.l.filter(_.argumentIndex == 1).foreach(t =>
                      next += State(t, current.stack, current.depth + 1, current.up))
                  if (!c.name.startsWith("<operator")) {
                    val callees = c.callee.filterNot(_.isExternal).l
                    if (callees.isEmpty) boundary(c, "external_or_unresolved_call")
                    else if (current.stack.size >= __MAX_CALL_DEPTH__) cuts += "call_depth"
                    else {
                      val active = activeMethodIds(current, n)
                      callees.foreach(m =>
                        if (active.contains(m.id)) boundary(c, "recursive_call")  // S1-C2
                        else m.parameter.l.filter(_.index == e.argumentIndex)
                          .foreach(p => next += State(p, c :: current.stack, current.depth + 1, current.up)))
                    }
                  }
                case _ => ()
              }
            case _ => ()
          }
"""


def traversal_query(
    direction: str,
    repo: str,
    starts: list[dict],
    stops: list[dict],
    *,
    max_depth: int | None,
    max_nodes: int | None,
    max_call_depth: int,
    max_ascent: int | None = None,
    ascent_fanout: int = 0,
) -> str:
    # Forward prunes to source->sink paths. Backward keeps its other branches: an input that is not
    # the GT source (a length, an index) still decides whether the sink overflows.
    seed, step, reached, leaf, prune = (
        (BACKWARD_SEED, BACKWARD_STEP, "source_reached", "dependency_root", "false")
        if direction == "backward"
        else (FORWARD_SEED, FORWARD_STEP, "sink_reached", "dependency_leaf", "true")
    )
    body = (
        queries.LOCATION
        + queries.CALL_IDENTITY
        + PREFIX.replace("__REPO__", queries.literal(repo.rstrip("/") + "/"))
        + FIND_NODES
        + TRAVERSAL.replace("__SEED__", seed).replace("__STEP__", step)
    )
    body = (
        body.replace("__STARTS__", queries.joined_literal(json.dumps(starts, ensure_ascii=True)))
        .replace("__STOPS__", queries.joined_literal(json.dumps(stops, ensure_ascii=True)))
        .replace("__MAX_DEPTH__", str(max_depth if max_depth is not None else 2147483646))
        .replace("__MAX_NODES__", str(max_nodes if max_nodes is not None else 2147483646))
        .replace("__MAX_CALL_DEPTH__", str(int(max_call_depth)))
        .replace("__MAX_ASCENT__", str(max_ascent if max_ascent is not None else 2147483646))
        .replace("__ASCENT_FANOUT__", str(int(ascent_fanout or 0)))
        .replace("__NONPRODUCT__", queries.literal(NONPRODUCT_PATTERN))
        .replace("__DIRECTION__", direction)
        .replace("__REACHED__", reached)
        .replace("__LEAF__", leaf)
        .replace("__PRUNE__", prune)
    )
    return queries.envelope(direction, body, timed=True)


class TargetSlicer(base.Slicer):
    """upstream `Slicer`의 소스 적재·CPG·inventory·질의 계측을 그대로 쓰고, 후보 발견과 후보
    루프 대신 지정한 source·sink에서 두 방향을 자른다."""

    def __init__(self, repo: Path, client: base.JoernClient, spec: dict, **kwargs):
        super().__init__(repo, threshold=1.0, max_flows=None, client=client, **kwargs)
        self.spec = spec
        self.close_project = True
        self.result["known_limitations"] += TARGET_LIMITATIONS
        self.result["target"] = {"version": TARGET_VERSION, "spec": spec}

    def locate(self, role: str) -> tuple[list[dict], dict]:
        spec = self.spec[role]
        line, on_line = pick_line(self.sources, spec)
        note = {"role": role, "gt_line": spec["line"], "line": line, "symbol_on_line": on_line}
        if spec["file"] not in self.sources:
            return [], {**note, "match": "file_not_in_repo"}
        rows, _ = self.query("locate", locate_query(self.repo.as_posix(), spec["file"], line))
        picked, chosen = choose(rows, spec)
        span = spec.get("function_span")
        if span and (not picked or not in_span(picked[0]["method"], span)):
            # GT 함수 밖에서 고르거나 그 줄에 노드가 없으면 범위 안 심볼 줄에서 다시(MAP-C1) — 실패하면 원래 것
            for retry in relocation_lines(self.sources.get(spec["file"]) or [], spec, span, skip=line):
                rows2, _ = self.query("locate", locate_query(self.repo.as_posix(), spec["file"], retry))
                picked2, chosen2 = choose(rows2, spec)
                if picked2 and in_span(picked2[0]["method"], span):
                    chosen2["start_relocated"] = {"from": line, "to": retry,
                                                  "reason": chosen.get("match") if not picked else "outside_gt_function"}
                    picked, chosen, line = picked2, chosen2, retry
                    note["line"] = retry
                    break
        if span:
            chosen["in_gt_function"] = bool(picked) and in_span(picked[0]["method"], span)
        note.update(chosen)
        note["nodes"] = [
            {k: r[k] for k in ("label", "code", "name", "line", "column")} for r in picked
        ]
        return picked, note

    def direction(
        self, direction: str, starts: list[dict], stops: list[dict], start_role: str
    ) -> dict:
        query = traversal_query(
            direction,
            self.repo.as_posix(),
            [selector(r) for r in starts],
            [selector(r) for r in stops],
            max_depth=self.settings.get("backward_depth"),
            max_nodes=self.settings.get("backward_nodes"),
            max_call_depth=self.result["joern_engine_config"]["maxCallDepth"],
            max_ascent=self.settings.get("max_ascent"),
            ascent_fanout=self.settings.get("ascent_fanout") or 0,
        )
        self.active_candidate = direction
        self.candidate_started = time.perf_counter()
        payload, _ = self.query(direction, query)
        # The Joern project may be built from a materialized subset.  Persist
        # repository-relative paths so attach can use the full repository index.
        normalized_edges = []
        for edge in payload.get("cross_method_flow", []):
            sides = []
            for side in ("from", "to"):
                point = dict(edge.get(side) or {})
                point["file"] = relative_file(point.get("file", ""), self.repo)
                if point["file"] not in self.sources or type(point.get("line")) is not int:
                    break
                sides.append(point)
            if len(sides) == 2:
                normalized_edges.append({"from": sides[0], "to": sides[1]})
        payload["cross_method_flow"] = normalized_edges
        anchors = {(self.spec[start_role]["file"], r["line"]) for r in starts}
        anchors = {p for p in anchors if p[0] in self.sources}
        # 반대쪽 끝(멈춤 노드) 줄 — 순회가 닿지 못해도(필드를 거치는 흐름: ngiflib `buffer` →
        # `gif->input.bytes` → `LoadGif(gif)`) 위치는 처음부터 안다. 슬라이스 줄로는 넣지 않고 기록만 —
        # 붙이기(attach)가 끝 줄로 써서 그 줄의 호출 본문을 싣는다.
        stop_role = "source" if start_role == "sink" else "sink"
        target_anchor = sorted(
            (self.spec[stop_role]["file"], r["line"])
            for r in stops
            if self.spec.get(stop_role) and self.spec[stop_role]["file"] in self.sources
        )
        dependency = self.points(payload["lines"])
        # The sink DDG cannot see sibling grammar checks (a void end-of-input
        # validator, for example).  Export the bounded CPG call/control facts
        # while the graph is open; the attach stage selects source text later.
        if dependency:
            try:
                evidence, _ = self.query("input_evidence", input_evidence_query(
                    self.repo.as_posix(), sorted(dependency)))
                payload["input_evidence"] = evidence
            except QueryError as exc:
                payload["input_evidence"] = {"version": 1, "status": "query_failed",
                                              "error": str(exc)[:300]}
        # 포인터 이동·그 포인터를 통한 쓰기(pointer_steps) — sink 함수 안. backward는 sink 줄까지,
        # forward는 source 줄 뒤(sink 함수가 source 함수와 같을 때)·아니면 sink 함수 전체.
        sink_rows = starts if direction == "backward" else stops
        sink_spec = self.spec["sink"]
        steps: set[tuple[str, int]] = set()
        if sink_rows and sink_spec["file"] in self.sources:
            method = sink_rows[0].get("method") or {}
            low, high = method.get("line", -1), method.get("line_end", -1)
            sink_lines = sorted({r["line"] for r in sink_rows})
            if direction == "backward":
                high = max(sink_lines)
            elif any(low <= r["line"] <= high for r in starts):
                low = min(r["line"] for r in starts)
            if low > 0 and high >= low:
                steps = {(sink_spec["file"], n) for n in pointer_steps(
                    self.sources[sink_spec["file"]], sink_lines, low, high)}
        dependency |= steps
        # backward: sink 호출에 통째로 넘기는 객체의 필드 쓰기(field_writes) — sink 함수, sink 줄 앞
        fields: set[tuple[str, int]] = set()
        if direction == "backward" and sink_rows and sink_spec["file"] in self.sources:
            method = sink_rows[0].get("method") or {}
            low = method.get("line", -1)
            sink_lines = sorted({r["line"] for r in sink_rows})
            if low > 0:
                fields = {(sink_spec["file"], n) for n in field_writes(
                    self.sources[sink_spec["file"]], sink_lines, low, max(sink_lines) - 1)}
        dependency |= fields
        points = anchors | dependency
        state = {"points": sorted(points)}
        self.expand(state)
        points = {tuple(p) for p in state["points"]}
        entry_row = starts[0]
        entry = {
            "file": self.spec[start_role]["file"],
            "line": entry_row["line"],
            "symbol": entry_row["name"] or entry_row["code"],
        }
        return {
            "id": direction,
            "candidate_key": direction,
            "case": "B" if direction == "backward" else "F",
            "direction": direction,
            "entry": entry,
            "members": [direction],
            "absorbed": [],
            "lines": [{"file": f, "line": n} for f, n in sorted(points)],
            "code": assemble(points, self.sources),
            "flow_count": None,
            "truncated": payload["truncated"],
            "analysis_kind": f"{direction}_ddg",
            "analysis": payload,
            "measurement_notes": {"flow_count": "Dependency graph; paths were not enumerated"},
            "source_sink": self.endpoints(direction, entry, payload),
            "line_provenance": {
                "data_dependency": sorted(dependency),
                "candidate_anchor": sorted(anchors),
                "context": sorted(points - dependency - anchors),
                "target_anchor": target_anchor,
                "pointer_steps": sorted(steps),
                "field_writes": sorted(fields),
                # sink 뒤(같은 함수)에서만 남은 줄 — 보이되 정의 클로저의 타입·필드 경로는 펼치지 않는다
                "after_target": sorted(self.points(payload.get("after_target_lines") or [])),
            },
        }

    def expand(self, state: dict) -> None:
        """upstream `expand_context`와 같은 고정점 — context 질의 + 원문 복원.

        다른 점 둘(노이즈): return은 그 함수의 마지막 슬라이스 줄 **앞**의 것(그 줄에 닿기 전에 빠져나갈
        수 있는 가드)만 붙이고, 참조 변수에 대입하는 저장소 전체의 대입은 따라가지 않는다 — 데이터
        의존은 이미 DDG 추적이 가져왔다. upstream 규칙으로는 cJSON `parse_string`의 데이터 줄 2개가
        함수 60줄 중 51줄로 불었다(return마다 그 조건·선언·대입이 딸려 옴)."""
        points = {tuple(p) for p in state["points"]}
        guard_points = sorted(points)  # 시작점 + 데이터 의존 줄 — 가드 return의 경계
        while True:
            old = points.copy()
            data, _ = self.query(
                "context",
                queries.context_query(
                    sorted(points),
                    self.repo.as_posix(),
                    guard_returns_only=True,
                    guard_points=guard_points,
                    follow_assignments=False,
                    case_labels=False,
                ),
            )
            points |= self.points(data)
            points = recover_points(points, self.sources)
            state["points"] = sorted(points)
            if old == points:
                return

    def degraded(
        self, direction: str, role: str, note: dict, other: dict | None
    ) -> dict | None:
        """Joern이 그 줄에서 노드를 못 찾았을 때(`no_node_on_line` 등) — 그래프 없이 만드는 슬라이스.

        c2cpg가 함수 본문을 못 읽으면(libtiff `NeXTDecode`: 95줄 함수의 AST가 노드 12개) 시작·멈춤
        노드가 없어 슬라이스가 **하나도** 안 나왔다. 그래도 GT가 준 두 줄(시작 줄, 반대쪽 끝 줄)과 그것을
        감싼 문법(원문 복원)은 원본에서 바로 알 수 있고, 2단계 붙이기(attach — 인덱스 기반)는 Joern 없이
        진입 함수 전체·정의·호출자를 붙인다. upstream `degraded_slice`(앵커만)와 같은 성격이고
        `degraded: true`로 표시한다 — 데이터 의존은 없다."""
        spec = self.spec[role]
        if spec["file"] not in self.sources:
            return None
        anchors = {(spec["file"], note.get("line") or spec["line"])}
        if other is not None and self.spec.get("source" if role == "sink" else "sink"):
            far = self.spec["source" if role == "sink" else "sink"]
            if far["file"] in self.sources:
                anchors.add((far["file"], other.get("line") or far["line"]))
        anchors = {p for p in anchors if 1 <= p[1] <= len(self.sources[p[0]])}
        if not anchors:
            return None
        points = recover_points(anchors, self.sources)
        line = note.get("line") or spec["line"]
        entry = {"file": spec["file"], "line": line, "symbol": spec.get("symbol") or ""}
        self.increment("candidates_degraded")
        return {
            "id": direction,
            "candidate_key": direction,
            "case": "B" if direction == "backward" else "F",
            "direction": direction,
            "entry": entry,
            "members": [direction],
            "absorbed": [],
            "lines": [{"file": f, "line": n} for f, n in sorted(points)],
            "code": assemble(points, self.sources),
            "flow_count": None,
            "truncated": False,
            "degraded": True,
            "analysis_kind": "degraded_lexical",
            "analysis": {"lines": [], "flow_count": None, "truncated": False, "degraded": True,
                         "boundaries": [], "target_reached": False,
                         "reason": note.get("match")},
            "measurement_notes": {
                "degraded": f"Joern located no node ({note.get('match')}); GT lines and their "
                "lexical structure only -- no data dependency"
            },
            "source_sink": {"basis": f"degraded_{direction}", "sources": [], "sinks": [],
                            "pairs": [], "target_reached": False},
            "line_provenance": {
                "data_dependency": [],
                "candidate_anchor": sorted(anchors),
                "context": sorted(points - anchors),
            },
        }

    def endpoints(self, direction: str, entry: dict, payload: dict) -> dict:
        def keep(raw: dict, kind: str) -> dict | None:
            mapped, _ = normalize_points([raw], self.sources, self.repo)
            if not mapped:
                return None
            file, line = mapped.pop()
            return {"file": file, "line": line, "kind": kind}

        start = keep(entry, "target_" + ("sink" if direction == "backward" else "source"))
        ends = [
            point
            for b in payload["boundaries"]
            if (point := keep(b["location"], b["reason"])) is not None
        ]
        starts = [start] if start else []
        ordered = sorted(ends, key=lambda p: (p["file"], p["line"], p["kind"]))
        return {
            "basis": f"targeted_{direction}",
            "sources": ordered if direction == "backward" else starts,
            "sinks": starts if direction == "backward" else ordered,
            "pairs": [],
            "target_reached": payload["target_reached"],
        }

    def run(self) -> dict:
        started = time.perf_counter()
        try:
            self.load_sources()
            self.prepare_checkpoint()
            # 시작·멈춤 줄을 살리는 매크로를 c2cpg에 정의로 넘긴다(activating_defines)
            self.defines = sorted({
                name
                for role in ("sink", "source")
                if (spec := self.spec.get(role)) and spec["file"] in self.sources
                for name in activating_defines(self.sources[spec["file"]], spec["line"])
            })
            self.result["target"]["defines"] = self.defines
            self.build_cpg()
            self.inventory()
            sinks, sink_note = self.locate("sink")
            sources, source_note = ([], None)
            if self.spec.get("source"):
                sources, source_note = self.locate("source")
            self.result["target"]["located"] = {"sink": sink_note, "source": source_note}
            # 후보 매니페스트 자리에 시작·멈춤 노드를 남긴다 — slice-v2를 읽는 쪽이 같은 키로 찾게.
            self.result["candidate_manifest"] = {
                "backward": {"start": "sink", "stop": "source", "nodes": sink_note},
                **(
                    {"forward": {"start": "source", "stop": "sink", "nodes": source_note}}
                    if source_note
                    else {}
                ),
            }
            if not sinks:
                self.errors.append(f"Sink node not located: {sink_note.get('match')}")
                self.completed.append(self.degraded("backward", "sink", sink_note, source_note))
            else:
                self.completed.append(self.safely("backward", sinks, sources, "sink"))
            if self.spec.get("source"):
                if not sources:
                    self.errors.append(f"Source node not located: {source_note.get('match')}")
                    self.completed.append(
                        self.degraded("forward", "source", source_note, sink_note)
                    )
                else:
                    self.completed.append(self.safely("forward", sources, sinks, "source"))
            self.completed = [item for item in self.completed if item is not None]
        except Exception as exc:
            self.errors.append(f"{type(exc).__name__}: {exc}")
        except KeyboardInterrupt:
            self.errors.append("Interrupted by user; server execution may continue")
        finally:
            self.active_candidate = None
            self.candidate_started = None
            if self.cpg_ready and self.close_project:
                self.cleanup()
            self.finish_target(started)
        return self.result

    def cleanup(self) -> None:
        """이 run이 띄운 프로젝트를 닫고 지운다 — 서버 하나로 표본 여럿을 돌 때 그래프가 쌓이지
        않게. 실패해도 결과는 그대로다(진단에만 남는다). ⚠ `close`·`delete`는 Joern 콘솔 명령이고
        이 저장소에서 실 Joern으로 확인한 적이 없다."""
        query = queries.envelope(
            "cleanup",
            "val name = workspace.getActiveProject.map(_.name).getOrElse(\"\"); "
            "scala.util.Try(close(name)); scala.util.Try(delete(name)); ujson.Bool(true)",
        )
        try:
            self.query("cleanup", query)
        except QueryError as exc:
            self.result["diagnostics"].append({"stage": "cleanup", "error": str(exc)})

    def safely(self, direction: str, starts: list, stops: list, role: str) -> dict | None:
        try:
            return self.direction(direction, starts, stops, role)
        except QueryError as exc:
            self.increment("candidates_failed")
            self.errors.append(f"{direction} slice failed: {exc}")
            return None

    def finish_target(self, started: float) -> None:
        """upstream `finish`에서 클러스터링을 뺀 것 — 두 슬라이스는 방향이 달라 합치지 않는다."""
        if self.cpg_ready:
            self.measure(
                "backward_truncated",
                sum(c["truncated"] for c in self.completed if c["direction"] == "backward"),
            )
            self.measure("slices_before_clustering", len(self.completed))
            self.measure("slices_after_clustering", len(self.completed))
            self.result["slices"] = self.completed
            for key in ("candidates_case_a", "candidates_case_b", "flows_truncated"):
                self.result["measurement_notes"][key] = "Targeted mode: no candidate discovery"
            self.result["status"] = "partial" if self.errors else "ok"
        if self.errors:
            self.result["error"] = "; ".join(dict.fromkeys(self.errors))
        self.measure("total_seconds", time.perf_counter() - started)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    result.add_argument("--repo", required=True, type=Path)
    result.add_argument("--spec", required=True, type=Path, help="source·sink JSON")
    result.add_argument("--out", required=True, type=Path)
    result.add_argument("--compile-commands", type=Path)
    result.add_argument("--joern-endpoint", default="http://localhost:16240")
    result.add_argument("--joern-version-command", type=Path)
    result.add_argument("--progress", type=Path)
    result.add_argument("--heartbeat-seconds", type=base.positive_seconds, default=30)
    result.add_argument("--query-timeout-seconds", type=base.positive_seconds, default=None)
    result.add_argument("--candidate-timeout-seconds", type=base.positive_seconds)
    result.add_argument("--backward-depth", type=base.positive_int, help="both directions")
    result.add_argument("--backward-nodes", type=base.positive_int, help="both directions")
    result.add_argument("--max-ascent", type=base.positive_int,
                        help="backward: caller levels climbed from the sink with no call stack")
    result.add_argument("--ascent-fanout", type=int, default=0,
                        help="backward: callers kept per no-stack ascent (0 = all); ranked same file, "
                             "product path, key")
    for option in ("--max-call-depth", "--max-args-to-allow", "--max-output-args-expansion"):
        result.add_argument(option, type=base.positive_int, default=None)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    if not isinstance(spec.get("sink"), dict):
        print("spec needs a sink", file=sys.stderr)
        return 1
    payload_dir = (args.out.parent / ".slicer-payloads").resolve()
    client = base.JoernClient(
        args.joern_endpoint, args.query_timeout_seconds, payload_dir=payload_dir
    )
    limits = {
        k: v
        for k, v in (
            ("maxCallDepth", args.max_call_depth),
            ("maxArgsToAllow", args.max_args_to_allow),
            ("maxOutputArgsExpansion", args.max_output_args_expansion),
        )
        if v is not None
    }
    job = TargetSlicer(args.repo, client, spec, engine_limits=limits)
    job.settings = {
        "mode": "targeted",
        "query_timeout_seconds": args.query_timeout_seconds,
        "engine_overrides": limits,
        "backward_depth": args.backward_depth,
        "backward_nodes": args.backward_nodes,
        "max_ascent": args.max_ascent,
        "ascent_fanout": args.ascent_fanout,
        "candidate_timeout_seconds": args.candidate_timeout_seconds,
        "compile_commands": args.compile_commands.as_posix() if args.compile_commands else None,
    }
    job.result["settings"] = job.settings
    job.compile_commands = args.compile_commands.resolve() if args.compile_commands else None
    job.compile_db_dir = payload_dir
    job.code_hashes = {
        p.name: base.hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path(__file__).parent.glob("slicer*.py"))
    }
    job.heartbeat_seconds = args.heartbeat_seconds
    try:
        payload_dir.mkdir(parents=True, exist_ok=True)
        if args.progress:
            args.progress.parent.mkdir(parents=True, exist_ok=True)
            job.progress = args.progress.open("a", encoding="utf-8", newline="\n")
        version, note = base.read_version(args.joern_version_command, args.query_timeout_seconds)
        job.result["joern_version"] = version
        if version is None:
            job.result["measurement_notes"]["joern_version"] = note
        result = job.run()
        base.write_result(args.out, result)
        return {"ok": 0, "partial": 2, "failed": 1}[result["status"]]
    except (OSError, ValueError) as exc:
        print(f"Cannot write or prepare slicer result: {exc}", file=sys.stderr)
        job.result["error"] = str(exc)
        try:
            base.write_result(args.out, job.result)
        except (OSError, ValueError):
            pass
        return 1
    finally:
        if job.progress:
            job.progress.close()


if __name__ == "__main__":
    raise SystemExit(main())
