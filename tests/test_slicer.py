"""Offline slicer contract tests. Joern and its HTTP transport are always mocked.

**이 파일이 통과한다고 CPGQL 이 맞는 것은 아니다.** mock 이 HTTP 경계에 있으므로
`candidates_case_a/b`·`flow_count`·`truncated`·slice 의 라인 소속은 전부 **mock 이 박은 값을
되읽는다.** 질의 문자열에 대해 확인되는 것은 substring · **경계**(사슬을 닫는 괄호 ·
줄 전체 · 첫 등장 자리 · 등장 횟수) · **술어의 퇴화 여부**(불리언 리터럴 주입)뿐이고,
`(observed.size > max_flows)` 의 경계·`observed.take(max_flows)` 의 절단 의미·`groupMap`·
`argumentIndex` 필터가 **실제 CPG 에서 의도한 것을 고르는지는 어떤 검사도 실행하지 않는다.**

그 공백은 가정이 아니라 **이 브랜치 자신의 이력이 보인 것**이다. `a0e5514` 에서 이 파일이
전부 통과한 뒤 실서버 run 이 질의 결함 둘을 냈고 `c4acdd2` 가 고쳤다 —
`truncated` 의 비교식이 **Scala 연산자 우선순위 때문에 튜플 비교로 파싱**됐고
(`f"observed.size > {max_flows}"` → `f"(observed.size > {max_flows})"`), null method 인
호출 인자에서 정렬 키 접근이 NPE 로 죽었다. **둘 다 substring 검사는 통과한다.**

질의 정합의 유일한 검증은 별도 ATOM run 이고 CI 가 아니다. 그러므로 이 파일이 초록인 것을
「질의가 맞다」로 읽지 않는다 — CLAUDE.md §5 의
*"테스트가 통과했으니 맞겠지 → 테스트는 주석 문장이 옳은지 검증하지 않는다"* 가 이 자리다.
⏵ 2026-10-08 — ATOM 은 2026-09-29 사용 종료(#340). 그 뒤 실 Joern 슬라이싱은 vast 인스턴스에서
돌았다(`docs/experiments/harness/2026-10-04-cybergym-operational-run-vastai-remote.md` §2).
CI 가 아니라는 점은 그대로다.
"""

import base64
import copy
import difflib
import json
import re
import threading
from pathlib import Path
from urllib.error import URLError

import pytest

import slicer
import slicer_pipeline
import slicer_queries as queries
from slicer_source import assemble, cluster, mask_non_code, normalize_points, recover_lines


def response(stage, payload, *, logs="", success=True):
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    return {
        "success": success,
        "stdout": logs
        + (f'\x1b[32mval res4: String = "__SLICER_{stage}__{encoded}__END_SLICER__"\x1b[0m'),
        "stderr": "",
    }


def report(rows):
    header = "| # | File | LOC | Parsed | Got a CPG | Duration |\n"
    body = "".join(
        f"| {i} | {file} | 10 | {parsed} | {cpg} | 1ms |\n"
        for i, (file, parsed, cpg) in enumerate(rows, 1)
    )
    parsed = sum(parsed == "yes" for _, parsed, _ in rows)
    cpg = sum(cpg == "yes" for _, _, cpg in rows)
    return header + body + f"| Total | | 10 | {parsed}/{len(rows)} | {cpg}/{len(rows)} | |\n"


class MockTransport:
    def __init__(self, payloads):
        self.payloads = payloads
        self.stages = []
        self.requests = []

    def __call__(self, request, timeout=None):
        query = json.loads(request.data)["query"]
        stage = re.search(r'"__SLICER_(\w+)__"', query)[1]
        self.stages.append(stage)
        self.requests.append((request, timeout))
        payload = self.payloads[stage]
        if callable(payload):
            payload = payload(query)
        if isinstance(payload, Exception):
            raise payload
        return self.Reply(self._materialize(stage, query, payload))

    @staticmethod
    def _materialize(stage, query, payload):
        """Emulate Joern's file transport: a query that writes to a path (case_a/case_b)
        drops the payload in that file and returns only the byte count, mirroring
        `slicer_queries.file_envelope`. Inline-marker stages are left untouched."""
        target = re.search(r'Paths\.get\("([^"]*)"\)', query)
        if target is None or not isinstance(payload, dict):
            return payload
        stdout = payload.get("stdout", "")
        marker = re.search(rf"__SLICER_{stage}__([A-Za-z0-9+/=]+)__END_SLICER__", stdout)
        if marker is None:
            return payload
        raw = base64.b64decode(marker[1])
        Path(target[1]).write_bytes(raw)
        return {
            **payload,
            "stdout": stdout.replace(marker[0], f"__SLICER_{stage}__{len(raw)}__END_SLICER__"),
        }

    class Reply:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return json.dumps(self.payload).encode()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.c").write_text(
        "int entry(char *p)\n{\n    char buf[32];\n    if (p) {\n"
        "        sprintf(buf, p);\n    }\n    return 0;\n}\n",
        encoding="utf-8",
        newline="\n",
    )
    return root


def candidate(symbol="entry", line=1):
    return {
        "symbol": symbol,
        "file": "main.c",
        "line": line,
        "scope": {"file": "main.c", "line": 1, "symbol": "entry", "full_name": "entry"},
        "anchors": [{"file": "main.c", "line": line}],
        "selector": {"line": line, "column": 5, "code": symbol + "(buf)", "ast_path": [1, 2]},
    }


def payloads(*, case_a=None, case_b=None, flow_count=0, truncated=False):
    return {
        "import": response("import", {"imported": True}, logs=report([("main.c", "yes", "yes")])),
        "inventory": response(
            "inventory",
            {
                "files": ["main.c"],
                "methods_internal": 1,
                "calls": 1,
                "calls_unresolved": 0,
                "unknown_nodes": 0,
                "flow_sinks_without_method": 0,
                "overlays": ["ossdataflow"],
                "engine_config": {
                    "maxCallDepth": 4,
                    "maxArgsToAllow": 1000,
                    "maxOutputArgsExpansion": 1000,
                },
            },
        ),
        "library": response("library", True),
        "case_a": response("case_a", [] if case_a is None else case_a),
        "case_b": response("case_b", [] if case_b is None else case_b),
        "flows": response(
            "flows",
            {
                "flow_count": flow_count,
                "truncated": truncated,
                "lines": [{"file": "main.c", "line": 5}] if flow_count else [],
                "endpoints": (
                    [
                        {
                            "source": {"file": "main.c", "line": 1},
                            "sink": {"file": "main.c", "line": 5},
                        }
                    ]
                    if flow_count
                    else []
                ),
            },
        ),
        "backward": response(
            "backward",
            {
                "flow_count": None,
                "truncated": False,
                "lines": [{"file": "main.c", "line": 5}],
                "node_count": 2,
                "state_count": 2,
                "dependency_count": 1,
                "boundaries": [],
                "truncation_reasons": [],
            },
        ),
        "context": response("context", [{"file": "main.c", "line": n} for n in (1, 3, 4, 7)]),
    }


def run_cli(monkeypatch, repo, tmp_path, data, *options):
    transport = MockTransport(data)
    monkeypatch.setattr(slicer, "urlopen", transport)
    output = tmp_path / "slices.json"
    code = slicer.main(["--repo", str(repo), "--out", str(output), *options])
    return code, json.loads(output.read_text(encoding="utf-8")), transport


def make_slice(id, line_numbers, *, file="a.c", flow_count=1, truncated=False):
    return {
        "id": id,
        "case": "A",
        "entry": {"file": file, "line": 1, "symbol": "f"},
        "lines": [{"file": file, "line": n} for n in line_numbers],
        "code": "",
        "absorbed": [],
        "flow_count": flow_count,
        "truncated": truncated,
    }


def test_clustering_preserves_every_absorbed_line_and_id():
    sources = {"a.c": [str(n) for n in range(1, 102)]}
    original = [
        make_slice("slice_0001", range(1, 101)),
        make_slice("slice_0002", [1, 2, 3, 4, 101], truncated=True),
        make_slice("slice_0003", [2, 3]),
    ]
    saved = copy.deepcopy(original)
    result = cluster(original, 0.8, sources)
    assert original == saved
    assert len(result) == 1
    assert result[0]["id"] == "slice_0001"
    assert result[0]["absorbed"] == ["slice_0002", "slice_0003"]
    assert result[0]["flow_count"] == 3
    assert result[0]["truncated"] is True
    points = {(p["file"], p["line"]) for p in result[0]["lines"]}
    for item in original:
        assert {(p["file"], p["line"]) for p in item["lines"]} <= points
    assert "101" in result[0]["code"]


@pytest.mark.parametrize(("threshold", "count"), [(0.949999, 1), (0.95, 1), (0.950001, 2)])
def test_containment_threshold_boundary(threshold, count):
    a = make_slice("slice_0001", range(1, 101))
    b = make_slice("slice_0002", [*range(1, 96), *range(101, 106)])
    assert len(cluster([a, b], threshold, {"a.c": ["x"] * 105})) == count


def test_clustering_compares_file_and_line_and_transfers_transitive_ids():
    a, b = make_slice("slice_0001", [1]), make_slice("slice_0003", [1], file="b.c")
    b["absorbed"] = ["slice_0002"]
    source = {"a.c": ["a"], "b.c": ["b"]}
    assert len(cluster([a, b], 0.95, source)) == 2
    result = cluster([b, a], 0, source)
    assert result[0]["absorbed"] == ["slice_0002", "slice_0003"]
    assert result[0]["code"] == "/* ===== a.c ===== */\na\n/* ===== b.c ===== */\nb"


def test_valid_empty_result_and_dead_joern_are_different(monkeypatch, repo, tmp_path):
    code, empty, _ = run_cli(monkeypatch, repo, tmp_path, payloads())
    assert code == 0 and empty["status"] == "ok" and empty["slices"] == []
    assert "error" not in empty
    assert empty["counters"]["candidates_case_a"] == 0
    code, dead, _ = run_cli(monkeypatch, repo, tmp_path, {"import": URLError("connection refused")})
    assert code == 1 and dead["status"] == "failed" and dead["slices"] == []
    assert "connection refused" in dead["error"]
    assert dead["counters"]["queries_aborted"] == 1
    assert dead["counters"]["candidates_case_a"] is None
    assert set(slicer.COUNTERS) <= dead["counters"].keys()
    assert dead["counters"]["cpg_seconds"] >= 0
    assert dead["counters"]["total_seconds"] >= dead["counters"]["cpg_seconds"]
    assert all(
        key in dead["measurement_notes"] for key, value in dead["counters"].items() if value is None
    )


def test_empty_stdout_is_not_an_empty_candidate_array(monkeypatch, repo, tmp_path):
    data = payloads()
    data["case_a"] = {"success": True, "stdout": "", "stderr": ""}
    code, result, transport = run_cli(monkeypatch, repo, tmp_path, data)
    assert code == 2 and result["status"] == "partial"
    assert result["counters"]["queries_aborted"] == 1
    assert "case_b" not in transport.stages


@pytest.mark.parametrize(
    "message",
    [
        "java.lang.StackOverflowError",
        "java.lang.OutOfMemoryError: heap",
        "-- [E008] Not Found Error: value x is not a member of cpg",
        "error: not found: value srcs",
        "Compilation failed",
        "1 error found",
    ],
)
def test_success_true_does_not_hide_errors(message):
    with pytest.raises(slicer.QueryError):
        slicer.decode_response(response("case_a", [], logs=message + "\n"), "case_a")


@pytest.mark.parametrize(
    "name",
    ["HttpException.cpp", "src/net/ParseError.cpp", "include/ValidationError.h"],
)
def test_a_source_file_named_after_a_throwable_does_not_fail_the_import(name):
    r"""**성공한** 파싱 리포트에 예외 이름을 품은 **파일 이름**이 있어도 실패가 아니다.

    `ERROR` 정규식의 `\b[\w.]+Exception\b` 는 `[\w.]+` 가 `.` 을 단어문자와 동급으로 먹어
    `HttpException.cpp` 의 `HttpException` 에서 경계 조건을 그대로 만족한다. 그런데
    `decode_response` 는 payload 마커 구간만 빼고 **나머지 stdout 전체**를 훑고, 그 나머지에
    `build_cpg` 가 `parse_report` 에 넘기는 **프론트엔드 파싱 리포트 표**가 들어 있다.

    그래서 CPG가 멀쩡히 만들어져도 파일 이름 하나로 import가 **전역 실패**했다. C/C++
    저장소에서 클래스명=파일명 컨벤션은 흔하다 — HTTP·파서·네트워킹 라이브러리는 거의
    반드시 하나쯤 있다. §0이 말하는 「결론이 틀리고 그건 안 돌아온다」 부류다.
    """
    logs = report([(name, "yes", "yes")])
    assert slicer.decode_response(response("import", {"ok": True}, logs=logs), "import")[0] == {
        "ok": True
    }


def test_a_per_file_parse_failure_names_a_throwable_without_failing_the_batch():
    """개별 파일 실패 메시지의 **인용된 경로**도 같은 오탐 경로다.

    `Failed to parse '<path>'` 는 그 파일 하나가 안 된 것이고 `parse_report` 가
    `parse_failures_observed` 로 세어 `errors` 에 남긴다 — **배치를 죽이는 신호가 아니다.**
    그런데 경로에 `HttpException.cpp` 가 들어가면 `ERROR` 가 물어 전체가 죽는다.
    """
    logs = "Failed to parse 'src/net/HttpException.cpp'" + "\n"
    assert slicer.decode_response(response("import", {"ok": True}, logs=logs), "import")[0] == {
        "ok": True
    }


@pytest.mark.parametrize(
    "message",
    [
        "java.lang.StackOverflowError",
        "com.example.BarException: boom",
        "Exception in thread \"main\" java.lang.RuntimeException: x",
        "| 1 | ok.c | 10 | yes | yes | 1ms |" + "\n" + "java.lang.OutOfMemoryError: heap",
    ],
)
def test_real_throwables_still_fail_even_next_to_a_report(message):
    """위 둘을 고치면서 **진짜 오류를 놓치면** 훨씬 나쁘다 — 그쪽이 §0이 경계하는 방향이다.

    마지막 경우가 그 자리를 지킨다: 리포트 행을 빼더라도 **같은 로그의 다른 줄**에 있는
    진짜 throwable 은 그대로 잡혀야 한다.
    """
    with pytest.raises(slicer.QueryError):
        slicer.decode_response(response("import", {"ok": True}, logs=message + "\n"), "import")


@pytest.mark.parametrize(
    "separator",
    ["\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"],
)
def test_no_line_separator_lets_an_osc_opener_swallow_a_real_crash(separator):
    """줄 구분자 **전부**에 대해 성립해야 한다 — `\n` 하나만 막는 것으로는 모자란다.

    이 검사가 `\n` 만 봤을 때 **같은 버그가 `\r` 로 그대로 재현됐다.** 앞 수정이
    「`splitlines()` 와 같은 줄 규율을 건다」고 적어 놓고 실제로는 그 집합의 부분집합
    하나만 옮겼기 때문이다(§2.2 — 요약이 원문보다 셌다).

    `str.splitlines()` 가 줄로 세는 것은 `\n` 만이 아니다. 여기 목록이 그것이고,
    `scannable()`·`parse_report()` 가 「줄」이라고 부르는 것이 정확히 그 함수의 정의다.
    """
    logs = (
        "\x1b]0;joern"
        + separator
        + "java.lang.StackOverflowError"
        + separator
        + "\x07"
    )
    with pytest.raises(slicer.QueryError):
        slicer.decode_response(response("import", {"ok": True}, logs=logs), "import")


def test_an_unterminated_osc_leaves_its_tail_as_text_rather_than_swallowing_lines():
    """미종결 OSC의 꼬리가 **텍스트로 남는 것은 결함이 아니라 고른 대가**다.

    줄을 넘는 삼킴을 막으면 이 꼬리를 피할 수 없다. 페이로드에 줄 경계문자가 끼면 오프너가
    속한 줄 안에 종단자가 없어 OSC 매치가 실패하고 `\\x1b]` 두 바이트만 먹힌다.

    **이 검사는 그 대가를 박제한다.** 여기가 깨지면 누군가 OSC를 다시 줄 너머로 매치하게
    만든 것이고, 그 순간 진짜 크래시가 조용히 사라지는 쪽으로 되돌아간다 — 아래 형제
    검사가 막는 바로 그것이다. 시끄러운 실패를 조용한 실패와 바꾸지 않는다(§0).
    """
    tail = slicer.strip_ansi("\x1b]0;build\rBuildException monitor\x07rest")
    assert "BuildException monitor" in tail  # 삼켜지지 않았다
    assert "rest" in tail


def test_a_dropped_report_row_degrades_to_null_with_a_reason_not_to_zero(repo):
    """그 꼬리가 리포트 행을 가리면 **`null` + 사유**로 내려간다 — 가짜 `0` 이 아니다.

    §0 이 *"안 잰 값은 0으로 두고 사유를 남긴다"* 로 요구하는 자리다. 대가를 지불하되
    **장부는 정직하게** 남는지를 여기서 고정한다.
    """
    state = slicer.Slicer.__new__(slicer.Slicer)
    state.repo, state.errors = repo, []
    state.result = {
        "counters": dict.fromkeys(slicer.COUNTERS),
        "measurement_notes": {},
        "diagnostics": [],
    }
    state.measure = lambda key, value: state.result["counters"].__setitem__(key, value)
    state.parse_report(
        "\x1b]0;progress\r50% | done\x07"
        "| 1 | main.c | 4 | yes | yes | 1ms |\n| Total | | 4 | 1/1 | 1/1 | |\n"
    )
    assert state.result["counters"]["files_parsed"] is None
    assert state.result["measurement_notes"]["files_parsed"]
    assert state.errors == ["Incomplete or inconsistent frontend report"]


def test_an_unterminated_osc_opener_does_not_swallow_a_real_crash():
    """ANSI 제거가 **줄을 넘어 삼키면** 진짜 크래시가 흔적 없이 사라진다.

    OSC(`\x1b]0;제목\x07`)는 BEL이나 ST로 닫힌다. 그런데 Joern 로그는 잘려서 오거나 섞여서
    오므로 **닫히지 않은 오프너**가 있을 수 있고, 스트림 뒤쪽 어딘가에 무관한 단독 BEL이
    하나만 있으면 정규식이 그 둘을 짝지어 **사이의 여러 줄을 통째로** 지운다. 그 사이에
    스택트레이스가 있으면 `ERROR` 가 볼 것이 안 남는다.

    **오탐보다 이쪽이 나쁘다.** 파일 이름 오탐은 시끄럽게 틀려서 사람이 본다. 이건
    조용히 성공으로 보고한다 — §0의 *"측정이 틀리면 결론이 틀리고 그건 안 돌아온다"* 다.

    같은 커밋의 `report_row`·`QUOTED_PATH` 가 `splitlines()` 로 **줄 단위**로만 도는 것과
    같은 규율을 OSC에도 건다 — 페이로드에서 줄바꿈을 뺀다.
    """
    # payload 마커는 **정상으로 둔다** — 마커가 없으면 "payload 없음"으로 raise돼
    # 검사가 틀린 이유로 통과한다. 여기서 유일하게 raise를 만들 수 있는 것은 오류 탐지다.
    logs = (
        "\x1b]0;joern"  # 닫는 BEL/ST가 없는 오프너
        "\njava.lang.StackOverflowError\n"
        "\x07"  # 뒤쪽의 무관한 단독 BEL — 정규식이 이 둘을 짝지으면 사이가 사라진다
    )
    with pytest.raises(slicer.QueryError):
        slicer.decode_response(response("import", {"ok": True}, logs=logs), "import")


def test_a_terminated_osc_on_one_line_is_still_stripped():
    """줄 안에서 제대로 닫힌 OSC는 그대로 벗긴다 — 위 수정이 L1을 되돌리면 안 된다."""
    assert slicer.ANSI.sub("", "\x1b]0;title\x07back") == "back"


def test_payload_delimiters_and_error_words_are_data():
    payload = [{"symbol": 'ErrorException|,:"\\\n한글', "line": 1}]
    assert slicer.decode_response(response("case_a", payload), "case_a")[0] == payload


def test_case_a_query_carries_the_payload_through_a_file_not_the_repl():
    """#219/#241 follow-up: the enumeration payload must not ride the REPL return.

    Returned inline, a few-thousand-row case_a payload overruns Joern's REPL and comes
    back empty (markers present, base64 gone) -> 0 slices. `file_envelope` writes it to a
    path and returns only the byte count, so the return stays tiny at any candidate count.
    """
    query = queries.candidates_a_query()

    assert queries.OUT_PATH_TOKEN in query  # the client substitutes a real, writable path
    assert "Files.write" in query
    assert "Base64" not in query  # the payload no longer travels as a REPL-returned string
    assert '"__SLICER_case_a__"' in query and '"__END_SLICER__"' in query


def _scala_code(segment: str) -> str:
    """The segment without `//` comment lines -- a guard quoted only in a comment must not pin."""
    return "\n".join(
        line for line in segment.splitlines() if not line.lstrip().startswith("//")
    )


def test_case_a_drops_empty_named_methods_from_both_lists():
    """#347: one empty-named method row voided the run (`valid_candidate` rejected it, the payload
    check failed, 0 slices). Since #361 such a row is dropped and counted (`candidates_invalid`)
    instead; this filter keeps it from reaching that check at all. The guards are pinned on the
    `val` lines themselves, in code (not comments), and the payload is pinned to be exactly
    `(api ++ readers).distinct` so nothing re-adds unfiltered methods. ⚠ Spelling pin (CLAUDE.md
    §2.5): it fails when this spelling is reverted or moved off the `val` line, it does not prove
    the filter runs -- the Scala is not executed here, and the semantically-equal rewrites fail it
    falsely."""
    code = _scala_code(queries.candidates_a_query())
    lines = [line.strip() for line in code.splitlines()]
    assert (
        'val api = cpg.method.internal.filterNot(m => m.name.isEmpty || m.name.startsWith("<"))'
        in lines
    )
    assert "val readers = cpg.method.internal.filter(_.name.nonEmpty)" in lines
    readers = lines.index("val readers = cpg.method.internal.filter(_.name.nonEmpty)")
    assert lines[readers + 1] == '.filter(_.call.name("({READERS})").nonEmpty)'.replace(
        "{READERS}", queries.READERS
    )
    assert code.count("ujson.Arr.from(") == 1
    assert "ujson.Arr.from((api ++ readers).distinct.map(m => ujson.Obj(" in code


CASE_A_BODY_FILTER = ".filter(_.astChildren.isBlock.astChildren.nonEmpty)"


def test_case_a_takes_only_methods_with_a_body():
    """A prototype (header declaration, forward declaration) and an empty definition `{}` are
    internal METHODs whose body block holds no statement; none can yield a slice, and in the
    capped repos they spent cap slots. The 10-repo C2 measurement (`slicer_c2/C2-report.md` 6b)
    found 18% of Case A candidates body-less (3,906 of 21,478) and counted the body block's
    children with `m.astChildren.isBlock.astChildren.size`; this is that predicate as a filter.

    ⚠ Spelling pin (CLAUDE.md §2.5): it fails when the filter is dropped or moved off the lines
    pinned here, it does not prove the Scala keeps only methods with a body -- the Scala is not
    executed here. The live counts are in the PR, not in CI."""
    code = _scala_code(queries.candidates_a_query())
    lines = [line.strip() for line in code.splitlines()]
    # api: straight after the name guard, so the cheap AST test runs before the per-method
    # `cpg.call.nameExact` scan it saves (a body-less method costs no call search)
    api = lines.index(
        'val api = cpg.method.internal.filterNot(m => m.name.isEmpty || m.name.startsWith("<"))'
    )
    assert lines[api + 1] == CASE_A_BODY_FILTER
    # readers: a method that calls a reader has a body, so this is a no-op by construction -- the
    # line is there so "Case A never takes a body-less method" holds on both lists, not only api
    readers = lines.index("val readers = cpg.method.internal.filter(_.name.nonEmpty)")
    assert lines[readers + 2] == CASE_A_BODY_FILTER
    assert code.count(CASE_A_BODY_FILTER) == 2


def test_case_a_query_without_a_priority_list_is_the_default_text():
    """`--priority-case-a` off must leave discovery untouched, byte for byte: every way of saying
    "no list" yields the very text `candidates_a_query()` always produced, and none of the
    priority spellings appear in it."""
    default = queries.candidates_a_query()
    for absent in (None, [], (), set()):
        assert queries.candidates_a_query(absent) == default
        assert queries.candidates_a_query(absent, "/work/repo") == default
    assert "isPrio" not in default and "prioFiles" not in default
    assert ".sortBy(m => if (hotIds.contains(m.id)) 0 else 1)" in default


def test_priority_case_a_changes_exactly_two_lines_and_adds_the_list():
    """A line diff against the default text: of the default, only the call-limit line and the
    sort key may change; everything else of the default stays where it was."""
    default = queries.candidates_a_query()
    on = queries.candidates_a_query(["b.c", "a.c"], "/work/repo")
    changed = [x for x in difflib.ndiff(default.splitlines(), on.splitlines()) if x[0] in "+-"]
    limit = queries.CASE_A_CALL_LIMIT
    assert [x[2:].strip() for x in changed if x[0] == "-"] == [
        f"cpg.call.nameExact(m.name).take({limit} + 1).size <= {limit})",
        ".sortBy(m => if (hotIds.contains(m.id)) 0 else 1)",
    ]
    assert len([x for x in changed if x[0] == "+"]) > 2  # the list and `isPrio` come with them


def test_priority_case_a_ranks_the_listed_files_first_and_exempts_them_from_the_call_limit():
    """F1+F2 of the C2 report: a method defined in a listed file sorts ahead of the "hot" layer
    before `CASE_A_CANDIDATE_CAP`, and skips `CASE_A_CALL_LIMIT`. It still needs a parameter (and,
    from F0, a body). ⚠ Spelling pins (CLAUDE.md §2.5) -- the Scala is not executed here."""
    cap, limit = queries.CASE_A_CANDIDATE_CAP, queries.CASE_A_CALL_LIMIT
    code = _scala_code(queries.candidates_a_query(["b.c", "a.c"], "/work/repo"))
    lines = [line.strip() for line in code.splitlines()]
    # the list: sorted (so the text does not depend on set order), rebuilt at run time, and an
    # exact set -- `prioritize()` matches the normalized file string exactly, not a suffix
    assert 'val prioFiles = List("a.c\\nb.c").mkString.split("\\n").toSet' in lines
    # c2cpg may name a file absolute or `./`-led: strip the repo prefix the way context_query does.
    # The two lines are pinned as ONE definition -- `prioFiles.contains(<the normalized name>)` and
    # nothing else -- so a looser test (suffix, basename) next to the exact one is not a survivor
    # (a mutant that added `|| prioFiles.exists(p => m.filename.endsWith(p))` passed the first pin).
    define = lines.index("def isPrio(m: nodes.Method): Boolean = prioFiles.contains(")
    assert lines[define + 1] == (
        "m.filename.replace('\\\\', '/').stripPrefix(\"/work/repo/\").stripPrefix(\"./\"))"
    )
    assert lines[define + 2].startswith("val api = ")  # nothing continues the definition
    # admission: the priority test comes first so a listed method never pays the call search
    first = lines.index(".filter(m =>")
    assert lines[first + 1] == (
        f"isPrio(m) || cpg.call.nameExact(m.name).take({limit} + 1).size <= {limit})"
    )
    assert lines[first - 1] == CASE_A_BODY_FILTER  # F0 stays in front of it
    # rank: listed, then hot, then the rest -- before the cap, which still bounds both lists
    api = code[code.index("val api =") : code.index(f".take({cap})", code.index("val api ="))]
    assert ".filter(_.parameter.size > 0).l" in api
    assert ".sortBy(m => if (isPrio(m)) 0 else if (hotIds.contains(m.id)) 1 else 2)" in api
    assert code.count(f".take({cap})") == 2
    # readers do not change: the list steers the api layer only
    readers = code[code.index("val readers =") :]
    assert "isPrio" not in readers.split("ujson.Arr.from(")[0]


def test_priority_case_a_needs_the_repo_to_normalize_file_names():
    with pytest.raises(ValueError, match="repo"):
        queries.candidates_a_query(["a.c"])


def test_case_b_query_carries_the_payload_through_a_file_not_the_repl():
    """case_b has the same overrun risk as case_a and must use the same file transport.

    Reverting `file_envelope("case_b")` to `envelope("case_b")` should turn this red -- a
    metadata-only substring check on the other stages would not.
    """
    query = queries.candidates_b_query()

    assert queries.OUT_PATH_TOKEN in query
    assert "Files.write" in query
    assert "Base64" not in query
    assert '"__SLICER_case_b__"' in query and '"__END_SLICER__"' in query
    assert "case_b" in queries.FILE_TRANSPORT_STAGES  # the client keys transport on this


def test_inventory_uses_file_transport():
    """#404: inventory carries every CPG file name; inline, a large repo's list overran the REPL
    and came back empty (2026-10-02 vast, wazuh and hdf5 prepares failed in inventory).
    Reverting `file_envelope("inventory")` to `envelope("inventory")` turns this red."""
    query = queries.inventory_query()

    assert queries.OUT_PATH_TOKEN in query
    assert "Files.write" in query
    assert "Base64" not in query
    assert '"__SLICER_inventory__"' in query and '"__END_SLICER__"' in query
    assert "inventory" in queries.FILE_TRANSPORT_STAGES


def test_decode_response_reads_the_payload_from_the_file(tmp_path):
    payload = [candidate("entry", 1)]
    raw = json.dumps(payload).encode("utf-8")
    path = tmp_path / "case_a.payload.json"
    path.write_bytes(raw)
    reply = {
        "success": True,
        "stdout": f'val res: String = "__SLICER_case_a__{len(raw)}__END_SLICER__"',
        "stderr": "",
    }

    assert slicer.decode_response(reply, "case_a", path)[0] == payload


def test_decode_response_rejects_a_short_payload_file(tmp_path):
    """A truncated write must fail loudly, not hand half a JSON document downstream."""
    path = tmp_path / "case_a.payload.json"
    path.write_bytes(b'[{"partial"')  # 11 bytes on disk, marker claims more
    reply = {
        "success": True,
        "stdout": 'val res: String = "__SLICER_case_a__999__END_SLICER__"',
        "stderr": "",
    }

    with pytest.raises(slicer.QueryError, match="declared 999"):
        slicer.decode_response(reply, "case_a", path)


def test_a_poisoned_candidate_is_isolated_not_cascaded(monkeypatch, repo, tmp_path):
    # A single poisoned candidate (here a StackOverflow mid-run) used to propagate to the
    # outer handler and discard every remaining candidate (#219). Now it is recorded as
    # failed and the candidates *after* it are still sliced — the middle one poisons, the
    # first and third both come back.
    data = payloads(case_a=[candidate("a", 1), candidate("m", 2), candidate("z", 3)], flow_count=2)
    calls = []

    def flows(query):
        calls.append(query)
        if len(calls) == 2:
            return {"success": True, "stdout": "java.lang.StackOverflowError", "stderr": ""}
        return response(
            "flows",
            {
                "flow_count": 2,
                "truncated": False,
                "lines": [{"file": "main.c", "line": 5}],
                "endpoints": [],
            },
        )

    data["flows"] = flows
    code, result, _ = run_cli(monkeypatch, repo, tmp_path, data)
    assert result["status"] == "partial"
    assert result["counters"]["candidates_failed"] == 1
    # All three candidates were reached — the run did not cascade to a stop, so nothing is
    # left unprocessed (a stop after the middle candidate would leave the third behind).
    assert result["counters"]["candidates_not_processed"] == 0
    assert result["slices"]  # at least the surviving candidates produced a slice
    assert result["counters"]["queries_aborted"] == 1
    # The poisoned candidate is not DROPPED: it survives as an anchor-only degraded slice, so its
    # capture (the recall unit) still reaches the output (the degraded slice may then be absorbed
    # into an overlapping full slice by clustering, so the counter -- not a marker on the
    # representative -- is the binding).
    assert result["counters"]["candidates_degraded"] == 1


def test_a_streak_of_poisoned_candidates_switches_to_degraded_not_stop(monkeypatch, repo, tmp_path):
    # Past a short streak the server is likely unusable, but the remaining candidates were
    # captured (that capture is the recall unit). Instead of ABORTING the run -- which dropped
    # every remaining candidate's slice, leaving them unprocessed -- the run switches to
    # anchor-only degraded slices without querying the server. No captured candidate is lost:
    # every one is processed and appears in slices.json marked `degraded`, and each candidate's
    # checkpoint is left at its last saved phase (pending for a query-stage failure) so --resume
    # re-attempts from there after the server is restarted.
    cands = [candidate("c" + str(i), i + 1) for i in range(6)]
    data = payloads(case_a=cands, flow_count=2)

    def flows(query):
        return {"success": True, "stdout": "java.lang.StackOverflowError", "stderr": ""}

    data["flows"] = flows
    code, result, _ = run_cli(monkeypatch, repo, tmp_path, data)
    streak = slicer_pipeline.POISON_STOP_STREAK
    # The first `streak` candidates each fail their flow query (then degrade); after the streak
    # the rest skip the server and degrade directly. Every candidate is processed, none dropped.
    assert result["counters"]["candidates_failed"] == streak
    assert result["counters"]["candidates_not_processed"] == 0
    assert result["counters"]["candidates_degraded"] == len(cands)
    assert result["slices"] and all(s.get("degraded") for s in result["slices"])
    # §2.5 (#307 review): a degraded slice must still CARRY the candidate's capture coords --
    # a degraded_slice that returned empty lines/code would silently lose the capture this PR
    # exists to keep, yet the counter/marker asserts above would still pass. Bind the coords:
    # every candidate's entry line must appear in some slice, and no slice is empty.
    all_lines = {(p["file"], p["line"]) for s in result["slices"] for p in s["lines"]}
    for i in range(len(cands)):
        assert ("main.c", i + 1) in all_lines
    assert all(s["lines"] and s["code"] for s in result["slices"])


@pytest.mark.parametrize("truncated", [False, True])
def test_flow_cap_records_observed_truncation(monkeypatch, repo, tmp_path, truncated):
    data = payloads(case_a=[candidate("sprintf", 5)], flow_count=3, truncated=truncated)
    _, result, transport = run_cli(
        monkeypatch,
        repo,
        tmp_path,
        data,
        "--max-flows-per-candidate",
        "3",
        "--query-timeout-seconds",
        "7",
    )
    assert result["slices"][0]["truncated"] is truncated
    assert result["slices"][0]["flow_count"] == 3
    assert result["slices"][0]["entry"]["symbol"] == "sprintf"
    assert result["slices"][0]["entry"]["line"] == 5
    assert result["counters"]["flows_truncated"] == int(truncated)
    assert result["status"] == ("partial" if truncated else "ok")
    assert all(timeout == 7 for _, timeout in transport.requests)
    # The flow cap is a run setting, so it is compiled into the session library (slicer_library).
    sent = [json.loads(req.data)["query"] for req, _ in transport.requests]
    library = next(q for q in sent if "__SLICER_library__" in q)
    assert ".take(4)" in library and "observed.take(3)" in library


@pytest.mark.parametrize(("order", "cases", "absorbed"), [
    ("ab", ["A", "B"], ["slice_0002"]),  # the order before `ba` became the default
    ("ba", ["B", "A"], ["slice_0003"]),  # the default: B is numbered first, A's pair follows it
])
def test_deterministic_slices_under_reordered_candidates(
    monkeypatch, repo, tmp_path, order, cases, absorbed
):
    data = payloads(
        case_a=[candidate("z"), candidate("a")], case_b=[candidate("sprintf", 5)], flow_count=2
    )
    _, first, _ = run_cli(monkeypatch, repo, tmp_path, data, "--case-order", order)
    data["case_a"] = response("case_a", [candidate("a"), candidate("z")])
    _, second, _ = run_cli(monkeypatch, repo, tmp_path, data, "--case-order", order)
    assert (
        json.dumps(first["slices"], ensure_ascii=False).encode()
        == json.dumps(second["slices"], ensure_ascii=False).encode()
    )
    assert [item["case"] for item in first["slices"]] == cases
    assert first["slices"][0]["id"] == "slice_0001"
    assert first["slices"][cases.index("A")]["absorbed"] == absorbed


def test_original_line_numbers_and_multifile_assembly(repo):
    sources = {"main.c": ["first", "middle", "last"], "other.h": ["header"]}
    points, invalid = normalize_points(
        [
            {"file": str(repo / "main.c"), "line": 1},
            {"file": "main.c", "line": 3},
            {"file": "./other.h", "line": 1},
            {"file": "main.c", "line": 0},
            {"file": "main.c", "line": 4},
            {"file": "../escape.c", "line": 1},
            {"file": "main.c", "line": True},
        ],
        sources,
        repo,
    )
    assert invalid == 4
    assert points == {("main.c", 1), ("main.c", 3), ("other.h", 1)}
    assert assemble(points, sources) == (
        "/* ===== main.c ===== */\nfirst\nlast\n/* ===== other.h ===== */\nheader"
    )


def test_structure_recovers_multiline_signature_calls_and_braces():
    lines = [
        "int f(",
        "    char *p)",
        "{",
        "    if (p &&",
        "        p[0])",
        "    {",
        "        emit(",
        "            p);",
        "    }",
        "}",
    ]
    assert recover_lines(lines, {8}) == set(range(1, 11))


@pytest.mark.parametrize("declaration", ["char buf[] = {", "int buf[2][2] = {", "Buffer buf{"])
def test_structure_keeps_initializer_contents_and_size(declaration):
    lines = [
        "void f() {",
        "    " + declaration,
        "        {1, 2},",
        "        {3, 4}",
        "    }",
        "    ;",
        "    use(buf);",
        "}",
    ]
    assert recover_lines(lines, {2, 7}) == set(range(1, 9))


def test_structure_does_not_expand_an_unrelated_function():
    lines = [
        "void f() {",
        "    if (ready) {",
        "        use();",
        "    }",
        "}",
        "void other() {",
        "    unrelated();",
        "}",
    ]
    assert recover_lines(lines, {3}) == {1, 2, 3, 4, 5}


def test_context_query_stops_before_leaving_a_method():
    query = queries.context_query([("main.c", 1)], "/work/repo")
    # This is a query-contract check, not a claim that mock tests execute Scala.
    boundary = query.index("if (n.isInstanceOf[nodes.Method]) return Nil")
    assert boundary < query.index("var parent = n.astParentOption")
    assert "case c: nodes.Call => c.ast.isIdentifier.l" in query
    assert "case n if" not in query


def test_context_query_guards_target_against_argumentless_assignments():
    """#219 후속: `cpg.assignment.filter(a => a.target …)`에서 인자 없는 assignment 노드가
    오면 `a.target`(= argument(1))이 빈 iterator next로 죽는다(sample_40 context 단계 실측).
    `.target` 앞에 argumentIndex(1)이 실재하는지 가드가 있어야 한다.
    """
    query = queries.context_query([("main.c", 1)], "/work/repo")

    guard = query.index("a.argument.argumentIndex(1).nonEmpty")
    assert guard < query.index("a.target.ast.isIdentifier.refsTo")


def test_case_b_query_adds_the_writer_sink_set_bounded():
    # Query-contract check (not a claim that mock tests execute Scala): the case_b query
    # gains a bounded WRITERS sink set that tags dangerous_write, on top of the existing
    # local-array-argument and double-free patterns (issue #219 (b)).
    query = queries.candidates_b_query()
    assert "memcpy" in query and "strcpy" in query and "memset" in query
    # Bind the wiring: writers are collected then per-method budgeted (CASE_B_PER_METHOD) under
    # the writer cap ceiling. A bare `.take(` check passed even after the flat cap was replaced.
    assert "def budget(" in query  # per-method budgeting present
    assert (f'val writers = budget(cpg.call.name("({queries.WRITERS})")'
            f'.take({queries.CASE_B_MATERIALIZE_CAP}).l, '
            f'{queries.CASE_B_PER_METHOD}, {queries.CASE_B_WRITER_CAP})') in query
    # §2.5 (#311 review): pin the round-robin BODY, not just the call site. The starvation this
    # PR fixes comes back with a depth-first rewrite (`groups.flatMap(_._2)`) that leaves the
    # call sites and `def budget(` green -- these three fragments turn that regression red.
    assert "groupBy(_.method.id)" in query  # grouped per enclosing method
    assert "(0 until k).flatMap(i =>" in query  # round-robin over rounds (breadth-first)
    assert "g.lift(i)" in query  # the i-th anchor of every method before any method's next
    # #311 review robustness: budget guards a null enclosing method (else `.method.id` NPEs and
    # drops the whole case_b query), and each anchor traversal is capped BEFORE .l so a huge CPG
    # cannot OOM before any candidate is produced. Pin both so a regression that drops them reddens.
    assert "Option(c.method).isDefined" in query  # null-method guard
    assert f".take({queries.CASE_B_MATERIALIZE_CAP}).l" in query  # materialization bound
    assert '"dangerous_write"' in query
    # The trigger includes every argument of a writer call so backtracking reaches dest,
    # source and size — the union is unbounded strings, so the cap must be present.
    assert "__WRITERS__" not in query  # the placeholder was substituted


def test_case_b_query_adds_the_injection_sink_set_bounded():
    # Query-contract check (2026-09-26 injection candidate-capture measurement): the injection
    # eval set's GT sinks are command/SQL execution, which the memory-write WRITERS/operator sets
    # miss (sample_13 `make_tunnel` -> `apr_proc_create` matched nothing). case_b gains a bounded
    # exec/query sink set that tags `injection_sink`, mirroring WRITERS.
    query = queries.candidates_b_query()
    # Bind the wiring, not the spelling: a substring check on "2000"/"injection_sink" passed even
    # with `++ execs` or the cap replace removed (review MEDIUM). Pin the collected+capped set,
    # its membership in the union, the tag, both placeholder substitutions, and bare `system`
    # (a `system` substring alone is satisfied by `wsystem`).
    exec_set = (f'budget(cpg.call.name("({queries.EXEC_SINKS})")'
                f'.take({queries.CASE_B_MATERIALIZE_CAP}).l, '
                f'{queries.CASE_B_PER_METHOD}, {queries.CASE_B_EXEC_CAP})')
    assert f"val execs = {exec_set}" in query
    assert "(arrays ++ repeatedCalls ++ writers ++ execs ++ operators)" in query
    assert 'Option.when(execs.contains(c))("injection_sink")' in query
    assert "__EXECS__" not in query and "__ECAP__" not in query
    assert "system" in queries.EXEC_SINKS.split("|")
    assert "apr_proc_create" in queries.EXEC_SINKS and "sqlite3_exec" in queries.EXEC_SINKS


def test_flows_query_keeps_injection_sinks_in_the_global_sink_set():
    # flows_query runs only for Case A candidates (slicer_pipeline: case == "A"); a Case B
    # candidate anchored on an injection sink backtracks that call's OWN args (backward_b_query),
    # not this global set -- so the effect of adding execSinks here is that a flow FROM a Case A
    # source can REACH an exec sink's argument. Bind the wiring (union membership + collected set),
    # not the spelling: removing `execSinks ++` from the union or the cap left the suite green.
    query = queries.flows_query([{"file": "x.c", "full_name": "foo", "line": 1}], None)
    assert "(writerSinks ++ execSinks ++ indexWrites ++ derefWrites ++ ptrArith)" in query
    assert f'cpg.call.name("({queries.EXEC_SINKS})").take({queries.CASE_B_EXEC_CAP}).l' in query


def test_case_b_query_adds_operator_anchors_narrowed_before_the_cap():
    # Query-contract check (issue #219 follow-up): the release GT sinks are dominated by
    # index/pointer operators, so case_b anchors on them. Raw operators explode (measured on
    # WavPack: 3042 index accesses, 3309 add/sub), so each anchor is NARROWED before .take —
    # array index to WRITE targets, pointer arithmetic to a pointer-typed operand — and the
    # cap is the final bound. Assert the narrowing precedes the cap, not just that a cap exists.
    query = queries.candidates_b_query()
    assert "<operator>.indirectIndexAccess" in query
    assert "<operator>.(addition|subtraction)" in query
    # index anchor is restricted to assignment targets (writes), not every a[i]:
    assert '<operator>.assignment' in query and 'argumentIndex(1)' in query
    # pointer-arithmetic anchor keeps a pointer-ish operand: a raw-pointer type OR a memory
    # access (the latter catches typedef'd pointers whose evalType lacks "*", e.g. PCRE2_SPTR):
    assert 'evalType.exists(_.contains("*"))' in query
    assert "indirectFieldAccess" in query and "addressOf" in query
    assert '"index_write"' in query and '"pointer_arithmetic"' in query
    assert str(queries.CASE_B_OPERATOR_CAP) in query
    assert "__OPCAP__" not in query  # the placeholder was substituted
    # The narrowing filter must sit before the anchor list is handed to budget(), so the
    # per-method budget + cap ceiling bound a real (narrowed) subset, not the raw 3000+
    # operator population. budget's args close with `.l, {CASE_B_PER_METHOD},`.
    for anchor in ("<operator>.indirectIndexAccess", "<operator>.(addition|subtraction)"):
        start = query.index(anchor)
        seg = query[start : query.index(f".l, {queries.CASE_B_PER_METHOD},", start)]
        assert ".filter(" in seg


def test_case_b_query_anchors_deref_field_writes_in_write_context():
    # 2026-09-26: deref/field is the biggest GT sink bucket but raw explodes; anchor only the
    # WRITE context (`p->x = ...` / `*p = ...` / `s.f = ...`) with the same assignment-LHS
    # narrowing as indexWrites, bounded by the same cap. Bind the wiring, not the spelling.
    query = queries.candidates_b_query()
    assert "val derefWrites =" in query
    assert 'cpg.call.name("<operator>.(indirectFieldAccess|fieldAccess|indirection)")' in query
    assert "(indexWrites ++ derefWrites ++ ptrArith).distinct" in query  # in the operator union
    assert '"deref_write"' in query                                       # tagged reason
    # The narrowing filter must sit before .take(cap) so the cap bounds a real subset,
    # not the raw 3000+ operator population.
    for anchor in ("<operator>.indirectIndexAccess", "<operator>.(addition|subtraction)"):
        start = query.index(anchor)
        seg = query[start : query.index(".take(", start) + 6]
        assert ".filter(" in seg and seg.index(".filter(") < seg.index(".take(")
    # derefWrites shares its operator name with ptrArith's filter, so `.index(name)` would hit
    # the wrong spot -- anchor on its `val` line (#308 review MED). Its narrowing filter
    # (assignment-LHS, write context) must also precede .take(cap); otherwise raw field/deref
    # writes -- the 3万+ population this anchor is bounded against -- flow back uncaught.
    dstart = query.index("val derefWrites =")
    dseg = query[dstart : query.index(".take(", dstart) + 6]
    assert ".filter(" in dseg and dseg.index(".filter(") < dseg.index(".take(")


def test_noncode_delimiters_do_not_change_structure():
    text = '#define OPEN {\nvoid f() {\n// }\nputs("}");\n/* } */\n}\n'
    masked = mask_non_code(text)
    assert masked.count("{") == masked.count("}") == 1
    raw = 'const char *s = R"tag({ " // })tag";'
    assert "{" not in mask_non_code(raw)
    assert len(mask_non_code(raw)) == len(raw)
    assert "{" not in mask_non_code("// continued \\\n{ still a comment\n")


def test_stack_overflow_during_import_is_recorded_as_partial(monkeypatch, repo, tmp_path):
    data = {"import": {"success": True, "stdout": "java.lang.StackOverflowError", "stderr": ""}}
    code, result, transport = run_cli(monkeypatch, repo, tmp_path, data)
    assert code == 2 and result["status"] == "partial"
    assert result["slices"] == [] and result["counters"]["queries_aborted"] == 1
    assert transport.stages == ["import"]
    # `partial` + `[]` 는 두 가지를 담는다 — CPG는 섰는데 슬라이스가 0건인 경우와, CPG 자체가
    # 없는 이 경우다. `status` 문자열만으로는 안 갈린다. **가르는 것이 이 필드**이고,
    # `docs/slicer-README.md` 의 계약 표가 그렇게 적으므로 여기서 고정한다 — 안 그러면
    # 문서가 검증 안 된 문장을 든다(§2.1).
    assert result["joern_engine_config"] is None
    assert result["counters"]["candidates_case_a"] is None


def test_missing_parser_report_is_unknown_not_success(monkeypatch, repo, tmp_path):
    data = payloads()
    data["import"] = response("import", {"imported": True})
    _, result, _ = run_cli(monkeypatch, repo, tmp_path, data)
    assert result["counters"]["files_in_cpg"] == 1
    assert result["counters"]["files_parsed"] is None
    assert result["counters"]["files_parse_failed"] is None
    assert "report" in result["measurement_notes"]["files_parsed"]
    assert result["joern_version"] is None
    assert "joern_version" in result["measurement_notes"]


def test_parse_failure_and_cpg_failure_are_separate(monkeypatch, repo, tmp_path):
    data = payloads()
    data["import"] = response(
        "import",
        {"imported": True},
        logs=report([("main.c", "yes", "no"), ("broken.c", "no", "no")]),
    )
    code, result, _ = run_cli(monkeypatch, repo, tmp_path, data)
    assert code == 2
    assert result["counters"]["files_parsed"] == 1
    assert result["counters"]["files_parse_failed"] == 1
    assert result["counters"]["files_cpg_failed"] == 2


def test_non_utf8_source_falls_back_to_latin1_not_a_read_failure(monkeypatch, repo, tmp_path):
    # D85/#254: invalid UTF-8 used to count as a read failure (and, under --checkpoint,
    # abort the whole run through prepare_checkpoint). It is now decoded latin-1, so the
    # file is read, not dropped -- one stray Latin-1 byte no longer kills the run.
    (repo / "bad.cpp").write_bytes(b"\xff\xfe\xfd")
    _, result, _ = run_cli(monkeypatch, repo, tmp_path, payloads())
    assert result["counters"]["files_seen"] == 2
    assert result["counters"]["files_read_failed"] == 0
    assert result["status"] != "failed"
    # 더는 읽기 실패가 아니다. (CPG inventory mock이 bad.cpp를 안 담아 "missing from CPG"로
    # 뜨는 것은 별개 -- 실제 run에서는 CPG에 들어간다.)
    assert "Source read failed" not in (result.get("error") or "")
    # 폴백은 조용하지 않다 -- diagnostics에 남는다.
    notes = [d for d in result["diagnostics"] if d.get("stage") == "load_sources"]
    assert [n["file"] for n in notes] == ["bad.cpp"]


def test_invalid_repo_still_writes_failure_json(monkeypatch, repo, tmp_path):
    _, result, transport = run_cli(monkeypatch, repo / "absent", tmp_path, {})
    assert result["status"] == "failed" and not transport.stages
    assert result["counters"]["files_seen"] is None


def test_missing_candidate_location_consumes_id_without_reuse(monkeypatch, repo, tmp_path):
    invalid, valid = candidate("a", -1), candidate("z", 1)
    _, result, _ = run_cli(monkeypatch, repo, tmp_path, payloads(case_a=[valid, invalid]))
    assert result["status"] == "partial"
    assert result["counters"]["candidates_case_a"] == 2
    assert result["counters"]["candidates_unmapped"] == 1
    # 같은 후보가 두 카운터를 올린다 — 이중 계상이 아니라 **단위가 다른 두 지표**다
    # (위치 건수 vs 후보 건수). 합산·비교하는 코드가 없다는 것이 그 근거이고,
    # 관계를 여기서 고정해 둬야 한쪽만 움직이는 변경이 조용히 지나가지 않는다.
    assert result["counters"]["locations_unmapped"] == 1
    assert [item["id"] for item in result["slices"]] == ["slice_0002"]


def _empty_symbol(row):
    # #361 (#352 review mock): the empty-name shape #347 saw from real Joern in Case A
    # (libexpat xmlrole.h:119), carried into Case B. Whether real Joern emits it there is unknown.
    row["symbol"] = ""
    return row


def _negative_ast_path(row):
    # #361 (#352 review mock): `selector.ast_path` with a -1 element.
    row["selector"]["ast_path"] = [1, -1]
    return row


@pytest.mark.parametrize(
    ("spoil", "field"), [(_empty_symbol, "symbol"), (_negative_ast_path, "selector")]
)
def test_one_invalid_case_b_row_is_dropped_not_the_whole_run(
    monkeypatch, repo, tmp_path, spoil, field
):
    """#361: before, one bad Case B row failed the payload check -> `exit=2 partial slices=0`
    (#352 review mock). Now the valid row is sliced and the bad one is dropped -- and counted,
    named, quoted and made `partial`, so the drop is not silent."""
    bad = spoil(candidate("memcpy", 5))
    code, result, transport = run_cli(
        monkeypatch, repo, tmp_path, payloads(case_b=[candidate("sprintf", 5), bad])
    )
    assert code == 2 and result["status"] == "partial"
    assert result["counters"]["queries_aborted"] == 0
    assert result["counters"]["candidates_invalid"] == 1
    assert result["counters"]["candidates_case_b"] == 1
    assert [item["case"] for item in result["slices"]] == ["B"]
    assert "backward" in transport.stages
    assert result["measurement_notes"]["candidates_invalid"] == (
        f"case_b: 1 rows dropped ({field}: 1)"
    )
    [entry] = [d for d in result["diagnostics"] if d.get("stage") == "case_b"]
    assert [json.loads(row) for row in entry["rows"]] == [bad]
    assert "Dropped 1 invalid case_b candidate rows" in result["error"]


def test_invalid_rows_of_both_cases_are_counted_together(monkeypatch, repo, tmp_path):
    """Case A's line -1 row is NOT invalid (valid_location checks the type only; it is unmapped
    later, test above). A row without anchors is invalid in either case."""
    no_anchors = candidate("b")
    del no_anchors["anchors"]
    no_file = candidate("c")
    no_file["file"] = None
    data = payloads(
        case_a=[candidate(), no_anchors, no_file, candidate("a", -1)],
        case_b=[candidate("sprintf", 5), _empty_symbol(candidate("x", 5))],
        flow_count=1,
    )
    _, result, _ = run_cli(monkeypatch, repo, tmp_path, data)
    assert result["counters"]["candidates_invalid"] == 3
    assert result["counters"]["candidates_case_a"] == 2
    assert result["counters"]["candidates_unmapped"] == 1
    assert result["measurement_notes"]["candidates_invalid"] == (
        "case_a: 2 rows dropped (anchors: 1, location: 1); case_b: 1 rows dropped (symbol: 1)"
    )
    assert sorted(item["case"] for item in result["slices"]) == ["A", "B"]


def test_a_fully_valid_candidate_payload_is_unchanged(monkeypatch, repo, tmp_path):
    """No invalid row -> the counter is measured as 0, no note, no diagnostic, status ok."""
    data = payloads(case_a=[candidate()], case_b=[candidate("sprintf", 5)], flow_count=1)
    code, result, _ = run_cli(monkeypatch, repo, tmp_path, data)
    assert code == 0 and result["status"] == "ok"
    assert result["counters"]["candidates_invalid"] == 0
    assert "candidates_invalid" not in result["measurement_notes"]
    assert not [d for d in result["diagnostics"] if str(d.get("stage")).startswith("case_")]


@pytest.mark.parametrize(
    ("a_bad", "b_bad", "note"),
    [
        (False, False, None),
        (True, False, "case_a: 1 rows dropped (anchors: 1)"),
        (False, True, "case_b: 1 rows dropped (symbol: 1)"),
        (True, True, "case_a: 1 rows dropped (anchors: 1); case_b: 1 rows dropped (symbol: 1)"),
    ],
)
def test_the_invalid_note_survives_whichever_case_has_the_bad_row(
    monkeypatch, repo, tmp_path, a_bad, b_bad, note
):
    """#361 review HIGH: a clean Case B measured the counter again and erased Case A's note."""
    no_anchors = candidate("b")
    del no_anchors["anchors"]
    case_a = [candidate(), *([no_anchors] if a_bad else [])]
    case_b = [candidate("sprintf", 5), *([_empty_symbol(candidate("x", 5))] if b_bad else [])]
    _, result, _ = run_cli(
        monkeypatch, repo, tmp_path, payloads(case_a=case_a, case_b=case_b, flow_count=1)
    )
    assert result["counters"]["candidates_invalid"] == a_bad + b_bad
    assert result["measurement_notes"].get("candidates_invalid") == note


@pytest.mark.parametrize("count", [1, 2])
@pytest.mark.parametrize("stage", ["case_a", "case_b"])
def test_a_candidate_payload_with_only_invalid_rows_still_fails_the_stage(
    monkeypatch, repo, tmp_path, stage, count
):
    """#361 review N-A: a schema change that spoils EVERY row is a stage failure, not a clean
    run with 0 candidates -- one spoiled row included. The guard throws before the counter moves:
    Case A all invalid leaves it unmeasured; Case B all invalid keeps Case A's one dropped row."""
    no_anchors = candidate("a")
    del no_anchors["anchors"]
    rows = [_empty_symbol(candidate(f"x{n}")) for n in range(count)]
    data = payloads(**{stage: rows})
    if stage == "case_b":
        data = payloads(case_a=[candidate(), no_anchors], case_b=rows)
    _, result, _ = run_cli(monkeypatch, repo, tmp_path, data)
    assert result["counters"]["queries_aborted"] == 1
    assert f"Invalid {stage} payload schema" in result["error"]
    notes = result["measurement_notes"]
    if stage == "case_a":
        assert result["counters"]["candidates_invalid"] is None
        assert notes["candidates_invalid"] == "Stage not reached"
        assert result["slices"] == []
    else:
        assert result["counters"]["candidates_invalid"] == 1
        assert notes["candidates_invalid"] == "case_a: 1 rows dropped (anchors: 1)"


@pytest.mark.parametrize("checkpoint", [False, True])
def test_echoing_a_dropped_row_cannot_lose_the_run(monkeypatch, repo, tmp_path, checkpoint):
    """#361 review MEDIUM: the verbatim echo of a dropped row carried a lone surrogate and
    NaN/Infinity from fields nobody validates into `write_result`, which then wrote no file."""
    bad = _empty_symbol(candidate("b"))
    bad["reasons"] = [float("nan"), float("inf"), "x\ud800y"]
    options = ["--checkpoint", str(tmp_path / "state.db")] if checkpoint else []
    monkeypatch.setattr(slicer, "read_version", lambda *args: ("test-joern", "test fixture"))
    code, result, _ = run_cli(
        monkeypatch, repo, tmp_path, payloads(case_a=[candidate(), bad], flow_count=1), *options
    )
    assert code == 2 and [item["case"] for item in result["slices"]] == ["A"]
    [entry] = [d for d in result["diagnostics"] if d.get("stage") == "case_a"]
    [echo] = entry["rows"]
    assert isinstance(echo, str) and echo.isascii()
    assert json.loads(echo)["reasons"][2] == "x\ud800y"


def test_a_candidate_payload_that_is_not_a_list_still_fails_the_stage(monkeypatch, repo, tmp_path):
    """Row isolation needs rows: a non-list payload is still rejected whole, and the counter stays
    unmeasured when neither candidate payload passed."""
    data = payloads()
    data["case_a"] = response("case_a", {"symbol": "entry"})
    _, result, _ = run_cli(monkeypatch, repo, tmp_path, data)
    assert result["counters"]["queries_aborted"] == 1
    assert result["slices"] == []
    assert "Invalid case_a payload schema" in result["error"]
    assert result["counters"]["candidates_invalid"] is None
    assert result["measurement_notes"]["candidates_invalid"] == "Stage not reached"


def test_crossfile_flow_and_new_context_survive_assembly(monkeypatch, repo, tmp_path):
    (repo / "other.cpp").write_text("void helper() {\n    use();\n}\n", encoding="utf-8")
    data = payloads(case_a=[candidate()])
    inventory, _ = slicer.decode_response(data["inventory"], "inventory")
    inventory["files"].append("other.cpp")
    data["inventory"] = response("inventory", inventory)
    data["flows"] = response(
        "flows",
        {
            "flow_count": 1,
            "truncated": False,
            "lines": [{"file": "other.cpp", "line": 2}],
            "endpoints": [],
        },
    )
    contexts = []

    def context(query):
        contexts.append(query)
        lines = [1, 7] if len(contexts) == 1 else [1, 3, 4, 5, 7]
        return response("context", [{"file": "main.c", "line": n} for n in lines])

    data["context"] = context
    _, result, _ = run_cli(monkeypatch, repo, tmp_path, data)
    assert len(contexts) >= 3
    item = result["slices"][0]
    assert "/* ===== other.cpp ===== */\nvoid helper() {\n    use();\n}" in item["code"]
    assert {"file": "main.c", "line": 3} in item["lines"]
    assert "char buf[32]" in item["code"]


def _import_query(transport):
    for request, _ in transport.requests:
        query = json.loads(request.data)["query"]
        if "__SLICER_import__" in query:
            return query
    raise AssertionError("no import query was sent")


def _write_compile_commands(path, entries):
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def test_compile_aware_import_excludes_uncompiled_sources(monkeypatch, repo, tmp_path):
    # Compile-aware import expects the compiled units, not every source file. An uncompiled
    # source in the repo must therefore NOT read as "missing from CPG" (which is a partial).
    (repo / "extra.c").write_text(
        "int extra(void) { return 0; }\n", encoding="utf-8", newline="\n"
    )
    cc = _write_compile_commands(
        tmp_path / "compile_commands.json",
        [{"directory": "repo", "file": "main.c", "command": "cc -c main.c"}],
    )
    # Without the database every source is expected, so extra.c is reported missing (partial).
    _, plain, _ = run_cli(monkeypatch, repo, tmp_path, payloads())
    assert plain["status"] == "partial"
    assert plain["counters"]["files_missing_from_cpg"] == 1
    # With it, extra.c is not a compiled unit, so nothing is missing and the flag is passed.
    _, aware, transport = run_cli(
        monkeypatch, repo, tmp_path, payloads(), "--compile-commands", str(cc)
    )
    assert aware["counters"]["files_missing_from_cpg"] == 0
    assert "Source files missing from CPG" not in (aware.get("error") or "")
    assert "--compilation-database" in _import_query(transport)


def test_compile_aware_import_excludes_c2cpg_ignored_folders(monkeypatch, repo, tmp_path):
    # A database entry under a c2cpg-ignored folder (test/) is not expected, so it is not
    # reported missing; the skip is recorded as a diagnostic (ignored_units), not silent.
    (repo / "test").mkdir()
    (repo / "test" / "foo.c").write_text(
        "int foo(void) { return 0; }\n", encoding="utf-8", newline="\n"
    )
    cc = _write_compile_commands(
        tmp_path / "compile_commands.json",
        [
            {"directory": "repo", "file": "main.c", "command": "cc -c main.c"},
            {"directory": "repo", "file": "test/foo.c", "command": "cc -c test/foo.c"},
        ],
    )
    _, result, _ = run_cli(
        monkeypatch, repo, tmp_path, payloads(), "--compile-commands", str(cc)
    )
    assert result["counters"]["files_missing_from_cpg"] == 0
    diags = [
        d
        for d in result["diagnostics"]
        if d.get("stage") == "build_cpg" and d.get("compile_aware")
    ]
    assert diags and diags[0]["compiled_units"] == 1 and diags[0]["ignored_units"] == 1


def test_broken_compile_commands_falls_back_to_importing_all_sources(
    monkeypatch, repo, tmp_path
):
    # A malformed database must not lose the run: build_cpg falls back to importing every
    # source, so the import query carries no --compilation-database and the fallback is recorded.
    cc = tmp_path / "compile_commands.json"
    cc.write_text("{ this is not valid json", encoding="utf-8")
    _, result, transport = run_cli(
        monkeypatch, repo, tmp_path, payloads(), "--compile-commands", str(cc)
    )
    assert "--compilation-database" not in _import_query(transport)
    diags = [d for d in result["diagnostics"] if d.get("stage") == "build_cpg"]
    assert diags and diags[0]["compile_aware"] is False
    # The repo's lone source still imports, so the run completes rather than failing.
    assert result["counters"]["files_missing_from_cpg"] == 0


def test_import_include_root_passes_the_repo_to_the_import(monkeypatch, repo, tmp_path):
    _, plain, transport = run_cli(monkeypatch, repo, tmp_path, payloads())
    query = _import_query(transport)
    assert "--include" not in query and "args=List" not in query
    assert plain["settings"]["import_include_root"] is False
    _, rooted, transport = run_cli(
        monkeypatch, repo, tmp_path, payloads(), "--import-include-root"
    )
    # the repo as this run resolved it, absolute -- c2cpg turns each include path into
    # `Paths.get(_).toAbsolutePath`, so a relative one would resolve against ITS working directory
    absolute = json.dumps(repo.resolve().as_posix())
    assert f'args=List("--include", {absolute}))' in _import_query(transport)
    assert rooted["settings"]["import_include_root"] is True


def test_import_include_root_is_refused_with_compile_commands(repo, tmp_path, capsys):
    """The database already carries the include paths and the pair was not measured."""
    cc = _write_compile_commands(tmp_path / "compile_commands.json", [])
    with pytest.raises(SystemExit):
        slicer.main(["--repo", str(repo), "--out", str(tmp_path / "s.json"),
                     "--import-include-root", "--compile-commands", str(cc)])
    assert "--import-include-root cannot be combined with --compile-commands" in (
        capsys.readouterr().err
    )


def test_query_deadline_does_not_wait_for_blocked_transport(monkeypatch, repo, tmp_path):
    released = threading.Event()

    def blocked_request(*args, **kwargs):
        released.wait()
        raise URLError("finished after client deadline")

    monkeypatch.setattr(slicer, "urlopen", blocked_request)
    output = tmp_path / "timeout.json"
    try:
        code = slicer.main(
            ["--repo", str(repo), "--out", str(output), "--query-timeout-seconds", "0.01"]
        )
        result = json.loads(output.read_text(encoding="utf-8"))
        assert code == 1 and result["counters"]["queries_aborted"] == 1
        assert "deadline exceeded" in result["error"]
    finally:
        released.set()


def test_source_form_feed_does_not_shift_joern_line_numbers(repo):
    (repo / "main.c").write_bytes(b"int x;\f int y;\nint z;\n")
    job = slicer.Slicer(repo, 0.95, None, slicer.JoernClient("http://unused", None))
    job.load_sources()
    assert job.sources["main.c"] == ["int x;\f int y;", "int z;"]


def test_non_utf8_source_keeps_prepare_checkpoint_whole(repo):
    # D85/#254: one Latin-1 byte in an unrelated UI string used to leave source_hashes
    # short of files_seen, which prepare_checkpoint refuses -- aborting the run under
    # --checkpoint. The byte is now decoded latin-1 (0xb0 -> U+00B0), byte-exact, so the
    # completeness condition holds and the ° is preserved for line-accurate extraction.
    (repo / "ui.cpp").write_bytes(b'gtk_label_new("Track n\xb0:");\n')
    job = slicer.Slicer(repo, 0.95, None, slicer.JoernClient("http://unused", None))
    job.load_sources()
    assert job.result["counters"]["files_read_failed"] == 0
    assert len(job.source_hashes) == job.result["counters"]["files_seen"]
    assert job.sources["ui.cpp"] == ['gtk_label_new("Track n°:");']


def test_valid_utf8_non_ascii_is_read_as_utf8_without_fallback(repo):
    # D85/#254 review MEDIUM: only INVALID bytes fall back to Latin-1. A valid non-ASCII
    # UTF-8 source (©, a Korean comment) must be read as UTF-8 verbatim, with no fallback
    # diagnostic -- a blanket `raw.decode("latin-1")` would mojibake these and this pins
    # against that regression.
    (repo / "u.c").write_bytes("int x; /* © 저작권 2024 */\n".encode())
    job = slicer.Slicer(repo, 0.95, None, slicer.JoernClient("http://unused", None))
    job.load_sources()
    assert job.sources["u.c"] == ["int x; /* © 저작권 2024 */"]
    assert [d for d in job.result["diagnostics"] if d.get("stage") == "load_sources"] == []


def test_version_comes_only_from_the_explicit_command(monkeypatch, repo, tmp_path):
    commands = []

    def version(command, **kwargs):
        commands.append(command)
        assert kwargs["stdin"] == slicer.subprocess.DEVNULL
        return slicer.subprocess.CompletedProcess(command, 0, "Version: 4.0.605\n", "")

    monkeypatch.setattr(slicer.subprocess, "run", version)
    _, result, _ = run_cli(
        monkeypatch, repo, tmp_path, payloads(), "--joern-version-command", "/opt/joern/joern"
    )
    assert result["joern_version"] == "4.0.605"
    assert commands[0][-1] == "--version"
    assert "joern_version" not in result["measurement_notes"]


def test_existing_joern_dataflow_overlay_is_not_reapplied(monkeypatch, repo, tmp_path):
    data = payloads()
    inventory, _ = slicer.decode_response(data["inventory"], "inventory")
    inventory["overlays"] = ["base", "controlflow", "dataflowOss"]
    data["inventory"] = response("inventory", inventory)
    code, _, transport = run_cli(monkeypatch, repo, tmp_path, data)
    assert code == 0
    assert "overlay" not in transport.stages


def test_orphan_flow_sinks_are_reported_as_partial(monkeypatch, repo, tmp_path):
    data = payloads(case_a=[candidate()])
    inventory, _ = slicer.decode_response(data["inventory"], "inventory")
    inventory["flow_sinks_without_method"] = 6
    data["inventory"] = response("inventory", inventory)
    code, result, _ = run_cli(monkeypatch, repo, tmp_path, data)
    assert code == 2 and result["status"] == "partial"
    assert result["slices"]
    assert result["counters"]["flow_sinks_without_method"] == 6
    assert "without an enclosing method" in result["error"]


def test_engine_overrides_reach_every_flow_query(monkeypatch, repo, tmp_path):
    _, result, transport = run_cli(
        monkeypatch,
        repo,
        tmp_path,
        payloads(case_a=[candidate()]),
        "--max-call-depth",
        "8",
        "--max-args-to-allow",
        "200",
        "--max-output-args-expansion",
        "300",
    )
    assert result["settings"]["engine_overrides"] == {
        "maxCallDepth": 8,
        "maxArgsToAllow": 200,
        "maxOutputArgsExpansion": 300,
    }
    for request, _ in transport.requests:
        query = json.loads(request.data)["query"]
        # flows reach the overrides through the session library they call (slicer_library).
        if any(f"__SLICER_{stage}__" in query for stage in ("case_b", "inventory", "library")):
            assert "maxCallDepth = 8" in query
            assert "maxArgsToAllow = 200" in query
            assert "maxOutputArgsExpansion = 300" in query
    assert result["counters"]["engine_flows_truncated"] is None


def test_same_named_methods_in_different_files_keep_separate_selectors():
    scopes = [candidate()["scope"], {**candidate()["scope"], "file": "other.cpp"}]
    query = queries.flows_query(scopes, None)
    assert 'm.filename == "main.c"' in query
    assert 'm.filename == "other.cpp"' in query
    assert "m.lineNumber.getOrElse(-1) == 1" in query
    # max_flows=None means the FLOW enumeration is not truncated. The sink set is capped
    # (writers/operators/FLOWS_SINK_CAP, #219) regardless, so a bare `.take(` no longer
    # distinguishes the two -- pin the flow-truncation site itself.
    assert "observed.take" not in query
    context = queries.context_query([("main.c", 1)], "/work/repo")
    assert '.stripPrefix("/work/repo/").stripPrefix("./")' in context
    assert "replace('\\\\', '/')" in context


# ## 실서버 run(#202)이 낸 질의 결함 다섯 — **결함 하나에 테스트 하나**
#
# 한 함수에 묶으면 pytest 가 첫 실패에서 멈춰, 질의를 갈아엎는 리팩터가 둘 이상을 되돌려도
# **한 번에 하나만 보인다**(2라운드 리뷰 실측: 결함 1·5 를 같이 되돌렸더니 `1 failed` 에
# 첫 줄만 나왔다). 쪼개면 전량이 한 run 에서 드러난다.
#
# **공통 한계 — 이 문장을 세게 쓰지 않는다.**
#
# 아래가 주장하는 것은 **「이 철자로 되돌리면 죽는다」**이지 **「그 결함이 다시 들어오면
# 죽는다」가 아니다.** 4라운드 리뷰가 그 차이를 실측으로 보였다 — 핀 문자열을 전부 남긴 채
# 결함을 되살리는 한 줄짜리 변이가 결함 1·2·4·5에 각각 있었다(`.sorted.flatten` 추가 ·
# `case` 목록 절단 · 필터를 `sortBy` 뒤로 · 배치를 단원소로). 그 넷은 이번에 막았지만
# **다섯째가 없다는 보장은 문자 핀으로 만들 수 없다.**
#
# 파일 docstring 대로 mock 은 HTTP 경계에 있고 Scala 는 여기서 실행되지 않는다 —
# **질의 정합의 유일한 검증은 별도 ATOM run 이고 CI 가 아니다.** 이 파일은 그 경계를
# 뒤집지 않는다.
# ⏵ 2026-10-08 — ATOM 은 사용 종료다(파일 docstring 의 같은 ⏵). 실 run 은 이제 vast 다.
#
# ⚠ **「전부 문자 pin 이라 뜻이 같은 재작성에도 걸린다」로 뭉치지 않는다.** 그것은 **양성**
# 단언에만 맞다 — 음성 단언(`".l.flatten" not in …`)은 없어야 할 문자열을 막는 것이라
# 재작성으로 트립되지 않고, 대가가 반대 방향이다(T1 주석). 2라운드가 지역 주석에서 지운
# 그 문장을 3차 커밋이 **머리말로 올려 되살렸다** — 각 테스트가 자기 한계를 들고, 여기서는
# 뭉치지 않는다.


def test_inventory_query_keeps_the_overlays_list_flat():
    """결함 1 — `cpg.metaData.overlays.l` 은 이미 평평하다.

    `.flatten` 이 String 을 Char 로 쪼개 `ujson.Arr.from` 이 컴파일되지 않았다.
    """
    inventory = queries.inventory_query()
    # ⚠ **닫는 괄호까지 문다.** 부분문자열 pin 은 **덧붙이기**를 통과시킨다 —
    # `.sorted` **뒤에** `.flatMap(_.toSeq)` 를 붙이면 `augmentString` 이 걸려 똑같이 Char 로
    # 쪼개는데 철자 `flatten` 이 없어 아래 음성도, 위 양성도 초록이었다(5라운드 실측).
    # 사슬이 여기서 **끝난다**는 것이 이 결함의 성질이고, 그것을 무는 것이 `)` 다.
    assert "cpg.metaData.overlays.l.distinct.sorted)" in inventory
    # 음성 단언은 이 질의 **전체**에서 `flatten` 을 금지한다. ⚠ **`.l.flatten` 한 철자만
    # 막던 판은 구멍이었다** — `.distinct.sorted` **뒤에** 붙이거나 옆 필드에 앉히면
    # `List[String]` 에 `augmentString` 이 걸려 똑같이 Char 로 쪼개는데 둘 다 초록이었다
    # (4라운드 실측). 지금 이 질의에 `flatten` 은 0회라 전면 금지가 성립한다.
    # ⚠ **그런데 전면 금지로도 모자랐다** — `flatMap(_.toSeq)` 는 같은 결함인데 그 철자가
    # 없어 통과했다(5라운드 실측). 낱말을 금지하는 축으로는 이 부류가 안 닫힌다.
    # 위 양성의 `)` 가 그것을 닫는다.
    # ⚠ **대가는 반대 방향이다**: 장래에 **옳은** flatten 이 필요해지면 죽는다 —
    # 그때는 필드 한정으로 좁힌다. (양성과 달리 이쪽은 없어야 할 문자열이라 재작성에 안 걸린다.)
    # ⚠ **양성 쪽의 대가는 브리틀함이다** — `.sorted.distinct` 로 순서를 바꾸거나 사이에
    # 공백을 넣기만 해도 깨진다. 뜻은 같지만 **의도한 것**이다: 이 사슬이 움직이면 사람이
    # 한 번 본다. scalafmt 를 돌리는 사람이 이유를 못 찾지 않게 여기 적어 둔다.
    assert "flatten" not in inventory


def test_case_a_query_bounds_are_pinned():
    # #241 review MEDIUM-2: candidates_a_query had no regression pin, and the mock slicer tests
    # never execute this Scala (tests/test_slicer.py header: candidates_case_a is a mock value).
    # Pin the three properties a harmless-looking rewrite silently breaks.
    q = queries.candidates_a_query()
    lim, cap = queries.CASE_A_CALL_LIMIT, queries.CASE_A_CANDIDATE_CAP
    # MEDIUM-1: the K=1 filter short-circuits at LIMIT+1 calls instead of enumerating the whole
    # call set of a common name. Semantically == `.size <= LIMIT`; this pins the early-exit form.
    assert f".take({lim} + 1).size <= {lim}" in q
    # Property 1: the candidate cap .take sits AFTER the param filter (moving it before would
    # take first-N raw methods then filter, shrinking toward 0) AND after the D90 relevance sort
    # (moving it before the sort re-introduces traversal-order starvation, #318). Pin the
    # ordering by segment, not adjacency, since the sort now sits between the filter and the cap.
    astart = q.index("val api =")
    aseg = q[astart : q.index(f".take({cap})", astart)]
    assert ".filter(_.parameter.size > 0).l" in aseg
    # D90 (#318): the WHOLE sort key, not just its location (#324 review MEDIUM-1: `1 else 0`
    # -- hot last --, `0 else 0` -- no-op sort -- and an emptied hotIds all passed the old
    # location-only pin). ⚠ These are still spelling pins (CLAUDE.md §2.5): they fail when the
    # key is re-spelled, they do not prove the ordering property. The mock tests never run Scala.
    assert ".sortBy(m => if (hotIds.contains(m.id)) 0 else 1)" in aseg  # hot first, before cap
    # "hot" = writes memory (WRITER/EXEC call OR assignment into index/field/deref). Pin both
    # halves and their method-null guards, and that hotIds is not post-filtered away.
    hseg = _scala_code(q[q.index("val hotIds =") : q.index("val api =")])
    assert f'cpg.call.name("({queries.WRITERS}|{queries.EXEC_SINKS})")' in hseg
    assert 'nameExact("<operator>.assignment").filter(_.argument.argumentIndex(1).isCall' in hseg
    assert hseg.count(".filter(c => Option(c.method).isDefined).method.id.l") == 2
    assert hseg.rstrip().endswith(".method.id.l).toSet")
    # Property 2 / MEDIUM-3: readers is ALSO capped (an uncapped readers set can overrun the
    # same stdout the api cap guards). So the cap .take appears exactly twice: api and readers.
    assert q.count(f".take({cap})") == 2


def test_context_query_excludes_synthetic_nodes_at_every_site():
    """결함 2 — 합성 METHOD_REF/TYPE_DECL 이 다시 `<global>` 로 올라간다.

    **자리가 넷이다** — 선언·본문·등반·선택. 한쪽만 남아도 새어 나간다.
    좌표 대신 심볼로 가리킨다(이 PR 은 `slicer_queries.py` 를 안 건드리므로 줄번호는 낡는다):
    `def sourceNode` 선언과 그 **본문의 `case` 절** · `def ancestors` 의 등반 `while` ·
    `context_query` 의 `val all` 선택.

    ⚠ **한계** — 아래에서 **자리**(첫 `case` 절)와 **줄 전체**(`val all`)는 성질을 물지만
    나머지는 문자 pin 이다. 뜻이 같은 재작성(`case x: nodes.MethodRef =>` 등)에 걸리고,
    반대로 `sourceNode` 가 **무엇을 반환하는지**까지는 `=> false` 철자로만 붙든다.
    `=> true` 로 뒤집는 것은 항진식 검사(결함 4)가 아니라 그 철자 pin 이 막는다.
    """
    context = queries.context_query([("main.c", 1)], "/work/repo")
    assert "def sourceNode(n: nodes.AstNode): Boolean" in context  # 선언
    # ⚠ **본문도 문다.** 선언과 호출 셋을 그대로 두고 `case` 절만 지우면 가드가 항상 거짓을
    # 안 내고 **결함이 통째로 돌아오는데** 위·아래 assert 는 전부 초록이었다(3라운드 실측).
    assert "case _: nodes.MethodRef | _: nodes.TypeDecl" in context
    # ⚠ **그리고 접두만 물면 모자란다.** 바로 위 소스 주석이 `MethodRef`/`TYPE_DECL` **둘만**
    # 정당화하므로 주석에 맞춰 목록을 줄이는 사람이 지우는 것이 **나머지 셋**이고, #202 가 보고한
    # *"형제 함수와 파일 첫 주석을 끌어왔다"* 는 정확히 그 셋이 통과할 때 나온다. 결과(`=> false`)도
    # 같이 문다 — `=> true` 로 뒤집어도 접두는 남는다(4라운드 실측, 둘 다 초록이었다).
    assert "_: nodes.NamespaceBlock |" in context
    assert "_: nodes.Namespace | _: nodes.File => false" in context
    # ⚠ **배제 절이 첫 절이어야 한다.** 앞에 `case _: nodes.MethodRef => true` 를 하나
    # 끼우면 첫 매치가 그것이라 배제가 죽는데, 위 pin 들은 **그대로 남아 초록**이었다
    # (5라운드 실측). 존재가 아니라 **자리**를 문다.
    body = context.split("def sourceNode", 1)[1]
    assert body.index("case _: nodes.MethodRef | _: nodes.TypeDecl") == body.index("case ")
    # ⚠ **`val all` 은 줄 전체를 문다.** `.filter(sourceNode)` 부분문자열만 물면
    # `(… .filter(sourceNode) ++ …필터안한것).distinct` 로 합집합을 만들어도 초록이다
    # (5라운드 실측). 필터가 **있다**가 아니라 그것이 **전부**라는 것이 성질이다.
    assert (
        "val all = relevantMethods.flatMap(_.ast.l).filter(sourceNode).distinct" in context
    )
    # 등반 가드. 빠지면 `context_query` 의 고정점 루프가 `cpg.assignment` 로 끌어온 노드
    # (`relevantMethods` 로 제한되지 않은 전역 traversal)가 다음 패스의 등반 기점이 되어
    # TypeDecl/NamespaceBlock/File 로 샌다(#202: *"형제 함수와 파일 첫 주석을 끌어왔다"*).
    assert "sourceNode(parent.get)" in context


def test_context_relevant_methods_indexes_points_by_file():
    """relevant_methods가 이 철자로 쓰였다 — 파일별 정렬 배열 + 이분 탐색.

    철자 pin이다. 저장소 관례(`val all …` 줄 전체)대로 세 덩어리를 통째로 문다 — 탐색 결과를
    무시하거나(`first = 0`) 삽입점을 틀리게 바꾸거나 전수 스캔을 끼워 넣으면 빨갛다. 동치
    재작성도 빨갛다. 뽑히는 메서드 집합이 옛 전수 비교와 같다는 성질은 Scala 실행이 필요해
    2026-09-30 vast 1회 실측(4샘플 · 안 잘린 후보 차이 0, #345 본문)으로만 확인했고, 그것을
    되풀이할 도구는 저장소에 없다.
    """
    context = queries.context_query([("a.c", 3)], "/repo")
    head = context.split('mark("relevant_methods")', 1)[0]
    assert "points.exists" not in head
    assert (
        "val pointLines: Map[String, Array[Int]] = points.groupBy(_._1).map { case (f, ps) =>\n"
        "        f -> ps.toArray.map(_._2).sorted }"
    ) in head
    assert (
        "val relevantMethods = cpg.method.internal.filter(m => m.lineNumber.exists { lineStart =>\n"
        "        val start: Int = lineStart\n"
        "        pointLines.get(filename(m)).exists { lines =>\n"
        "          val found = java.util.Arrays.binarySearch(lines, start)\n"
        "          val first = if (found >= 0) found else -found - 1\n"
        "          first < lines.length && m.lineNumberEnd.forall(_ >= lines(first))\n"
        "        }\n"
        "      }).l"
    ) in head


def test_flows_query_parenthesizes_the_truncated_comparison():
    """결함 3 — 괄호가 빠지면 Scala 가 `"truncated" -> observed.size > 3` 을 튜플 비교로 파싱한다.

    ⚠ **여기서 재는 것은 괄호가 아니라 식 전체의 철자다.** `(3 < observed.size)` 처럼 괄호가
    있고 뜻도 옳은 재작성에도 걸린다 — 괄호만 잰다고 적으면 다음 사람이 이 assert 를 과소평가한다.
    """
    flows = queries.flows_query([candidate()["scope"]], 3)
    assert "(observed.size > 3)" in flows


def test_queries_guard_against_sinks_whose_method_is_null():
    """결함 4 — 호출 인자 6개의 소속 method 가 null 이라 sink 정렬에서 NPE 가 났다(#202).

    **#225 표 4행이 든 자리는 셋이고 그중 둘이 질의에 있다.** 셋째(`slicer.py:628-632` 의
    `errors.append`)는 `test_orphan_flow_sinks_are_reported_as_partial` 이 든다 —
    **그쪽은 mock 응답에 값을 박고 보고 경로만 읽으므로 질의를 한 줄도 열지 않는다.**
    그래서 질의 쪽 둘은 여기서 문다.

    ⚠ **「`slicer.py` 의 보고 경로」로 넓혀 읽지 마라.** `c4acdd2` 가 그 파일에서
    **`flow_sinks_without_method` 때문에** 고친 자리는 넷이고(`COUNTERS` 등록 ·
    `validate_payload` 키 · 적재 루프 키 · `errors.append`) 그중 앞의 둘은 **되돌려도 전량
    초록이다**(3라운드 실측). 둘 다 #225 표 밖이라 이 PR 의 범위는 아니지만, 덮였다고 적으면
    그것이 다음 결함이다([CLAUDE.md](../CLAUDE.md) §2.2).
    ⚠ **그 커밋이 `slicer.py` 에서 고친 자리 **전체**는 일곱이다** — 위 넷은 이 결함 한정이다.

    ⚠ 이 PR 의 1차 판은 *"`test_orphan_…` 이 넷째를 든다"* 고 적고 질의 쪽 둘을 안 물었는데,
    되돌려도 전량 초록이었다(1·2라운드 리뷰가 실측으로 재현).
    """
    inventory = queries.inventory_query()
    assert '"flow_sinks_without_method" -> cpg.call.argument.dedup' in inventory
    # ⚠ **술어를 문다 — 단 「대체」까지다.** 위 한 줄만으로는 `.count(n => false)` 로 갈아도
    # 초록이었다(3라운드 실측). 카운터의 **존재**가 아니라 그것이 무엇을 세는지가 실질이다.
    # ⚠ **그리고 이 pin 은 「덧붙이기」를 못 막는다** — `isEmpty && false` 는 철자를 다 남긴다
    # (5라운드 실측). 그쪽은 아래 항진식 검사가 든다.
    assert "Option(n.method).isEmpty" in inventory
    flows = queries.flows_query([candidate()["scope"]], 3)
    # ⚠ **술어를 항진식으로 죽이는 길을 막는다.** 위 양성 pin 은 **대체**를 막지
    # **덧붙이기**를 못 막는다 — `Option(n.method).isEmpty && false` 는 두 pin 을 다 남긴 채
    # 카운터를 영원히 0으로 만든다(5라운드 실측). 불리언 리터럴이 논리 연산자에 붙으면
    # 그 술어는 판별을 안 한다. 등반 가드의 `|| true` 도 같은 형이라 세 질의를 다 본다.
    context_for_tautology = queries.context_query([("main.c", 1)], "/work/repo")
    degenerate = re.compile(r"(&&|\|\|)\s*(true|false)\b|\b(true|false)\s*(&&|\|\|)")
    assert degenerate.search("Option(n.method).isEmpty && false)"), "항진식 검출기가 죽었다"
    assert not degenerate.search('m.filename == "a.c" && m.lineNumber'), "정상 술어를 잡는다"
    for name, text in (("inventory", inventory), ("flows", flows),
                       ("context", context_for_tautology)):
        assert not degenerate.search(text), f"{name} 질의의 술어에 불리언 리터럴이 붙었다"
    # ⚠ **위치까지 문다.** #202 의 NPE 는 **정렬**에서 났으므로(`sortBy(… n.method.fullName)`)
    # 필터를 `sortBy` **뒤로** 옮기면 결함이 그대로 돌아온다. 철자가 아니라 **순서**를 문다
    # (#252 리뷰 §2.5): method-null 가드가 `n.method.fullName`을 처음 참조하는 `sortBy`보다
    # 앞에 있어야 한다.
    assert "Option(n.method).nonEmpty" in flows
    assert flows.index("Option(n.method).nonEmpty") < flows.index("n.method.fullName"), (
        "method-null 가드가 sortBy(n.method.fullName)보다 뒤로 가면 #202 NPE가 돌아온다"
    )


def test_flows_query_uses_hybrid_local_and_bounded_global_sinks():
    """#219 + #252 review HIGH-2/3: hybrid sinks. Inside the candidate methods EVERY call
    argument is a sink (recall -- keeps deref/field & index-READ GT types and the live-probe
    fixtures); globally the memory-write and command/SQL-exec sink calls, capped, so the
    per-candidate dataflow stays under the deadline on large CPGs. `cpg.call.argument` over the
    whole repo is what blew the
    deadline, so it must not be the global set.
    """
    flows = queries.flows_query([candidate()["scope"]], 3)

    assert "cpg.call.argument.filter" not in flows  # global set is NOT every repo call arg
    assert "methods.flatMap(m => m.ast.isCall.argument.l)" in flows  # local: all candidate args
    assert 'cpg.call.name("(' in flows  # global writer sink calls
    assert "<operator>.indirectIndexAccess" in flows  # global index-write operators
    assert f".take({queries.FLOWS_SINK_CAP})" in flows  # global sink universe is bounded
    assert '"sinks_capped"' in flows and '"sinks_kept"' in flows  # cap is recorded, not silent


def test_flows_and_case_b_share_the_same_operator_sink_definition():
    """#252 review MEDIUM: the writer/index-write/ptr-arith sink defs are copied between
    flows_query and candidates_b_query. Pin the shared operator filters in BOTH so a change
    to one that isn't mirrored fails here instead of silently diverging.
    """
    flows = queries.flows_query([candidate()["scope"]], 3)
    case_b = queries.candidates_b_query()
    for fragment in (
        'cpg.call.nameExact("<operator>.indirectIndexAccess")',
        'c.inCall.nameExact("<operator>.assignment").argument',
        'cpg.call.name("<operator>.(addition|subtraction)")',
        'c.argument.evalType.exists(_.contains("*"))',
        'indirection|indirectIndexAccess|addressOf)',
        # deref/field WRITE anchor is copied between the two queries (2026-09-26) -- pin it too.
        'cpg.call.name("<operator>.(indirectFieldAccess|fieldAccess|indirection)")',
        # exec sink PATTERN is copied between the two queries (#299) -- pin it here too. The
        # collection wrappers now differ intentionally (flows: flat cap; case_b: per-method
        # budget), so pin the shared name set, not the take/budget form.
        f'cpg.call.name("({queries.EXEC_SINKS})")',
    ):
        assert fragment in flows and fragment in case_b, fragment


def test_case_b_dealloc_scan_filters_to_methods_that_call_free():
    """#219: the free-wrapper scan filters to methods that actually call free before the
    per-parameter dataflow. Scanning every method's AST and building a dataflow engine per
    parameter for the rest is what pushes case_b past the query deadline on a large C++ CPG
    (sample_40). Exact filter -- a method with no free() call cannot reach one.
    """
    query = queries.candidates_b_query()

    assert 'cpg.method.internal.filter(_.ast.isCall.nameExact("free").nonEmpty)' in query


def test_flows_query_batches_by_enclosing_method():
    """결함 5 — sink 하나마다 엔진을 새로 짓지 않는다. 배치 단위는 enclosing method 다.

    **세 자리를 따로 문다** — 묶기 · 배치로 부르기 · **묶인 것의 크기**. 셋이 각각 다른
    한 줄짜리 변이로 결함을 되살릴 수 있었다(3·4라운드 실측).

    ⚠ **한계** — 세 양성은 문자 pin 이라 `groupBy(n => n.method)` 처럼 뜻이 같은 재작성에
    걸린다. 그리고 셋 다 「그 줄이 있는가」라 **줄과 줄 사이**는 안 봤다 — 실제로 뚫린 축이
    그쪽이다(5라운드 실측). 아래 바인딩 수와 소비부 인접이 그 사이를 막는다.
    """
    flows = queries.flows_query([candidate()["scope"]], 3)
    assert "sinks.groupBy(_.method)" in flows
    # ⚠ **소비부까지 문다.** 묶기만 하고 `Iterator.single(sink).reachableByFlows(...)` 로
    # 되돌리면 **결함이 그대로 돌아오는데** 위 줄은 초록이었다 — 배치를 만드는 것과
    # 배치로 **부르는** 것이 다른 자리다(3라운드 실측).
    assert "batch.iterator.reachableByFlows" in flows
    # ⚠ **크기까지 문다.** 위 둘을 남긴 채 `.map(_._2)` 를 `.flatMap(_._2.map(List(_)))` 로
    # 바꾸면 배치가 전부 단원소가 되어 **sink 마다 엔진을 다시 짓는다** — 위 둘은 초록이었다
    # (4라운드 실측).
    assert "}.map(_._2)" in flows
    # ⚠ **핀이 걸린 줄 사이에 끼워 넣는 길을 막는다.** 위 셋은 전부 「그 줄이 있는가」라
    # 줄과 줄 **사이**는 안 본다 — `val batches` 를 그림자로 한 번 더 묶거나, 소비 시작점에
    # `.flatMap(_.map(List(_)))` 를 끼우면 배치가 전부 단원소가 되는데 셋 다 초록이었다
    # (5라운드 실측). 바인딩은 하나이고, 묶은 것이 **곧바로** 소비돼야 한다.
    assert flows.count("val batches") == 1
    assert "else batches.iterator).flatMap(batch =>" in flows


def test_unwritable_output_reports_error_without_traceback(monkeypatch, repo, tmp_path, capsys):
    monkeypatch.setattr(slicer, "urlopen", MockTransport(payloads()))
    code = slicer.main(["--repo", str(repo), "--out", str(tmp_path)])
    assert code == 1
    assert "Cannot write" in capsys.readouterr().err


def test_output_and_sources_have_no_bom_or_crlf(monkeypatch, repo, tmp_path):
    run_cli(monkeypatch, repo, tmp_path, payloads())
    paths = [
        tmp_path / "slices.json",
        Path(slicer.__file__),
        Path(queries.__file__),
        Path(slicer.__file__).with_name("slicer_source.py"),
        Path(__file__),
    ]
    for path in paths:
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), path
        assert b"\r\n" not in raw, path


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--threshold", "nan"),
        ("--threshold", "1.1"),
        ("--max-flows-per-candidate", "0"),
        ("--query-timeout-seconds", "inf"),
        ("--query-timeout-seconds", "-1"),
        ("--max-flows-per-candidate", "2147483647"),
    ],
)
def test_cli_rejects_invalid_limits(option, value):
    with pytest.raises(SystemExit) as error:
        slicer.parser().parse_args(["--repo", ".", "--out", "slices.json", option, value])
    assert error.value.code == 2


def test_malformed_scope_is_rejected_before_query_interpolation(monkeypatch, repo, tmp_path):
    """The malformed row never reaches a query. Before #361 the whole case_a payload was rejected
    (`queries_aborted` 1, no case_b); now only that row is dropped and the valid one is sliced --
    the injected text still appears in no request."""
    row = candidate("a")
    row["scope"]["line"] = '1); throw new RuntimeException("injected"); //'
    _, result, transport = run_cli(
        monkeypatch, repo, tmp_path, payloads(case_a=[row, candidate()], flow_count=1)
    )
    assert result["status"] == "partial"
    assert result["counters"]["queries_aborted"] == 0
    assert result["counters"]["candidates_invalid"] == 1
    assert result["counters"]["candidates_case_a"] == 1
    assert "case_b" in transport.stages and transport.stages.count("flows") == 1
    assert result["measurement_notes"]["candidates_invalid"] == "case_a: 1 rows dropped (scope: 1)"
    sent = [json.loads(req.data)["query"] for req, _ in transport.requests]
    assert not any("injected" in query for query in sent)


def test_failed_atomic_replace_preserves_previous_artifact(monkeypatch, tmp_path):
    output = tmp_path / "slices.json"
    output.write_bytes(b"previous complete result")

    def fail_replace(*args):
        raise OSError("disk failure")

    monkeypatch.setattr(slicer.os, "replace", fail_replace)
    with pytest.raises(OSError, match="disk failure"):
        slicer.write_result(output, {"status": "ok"})
    assert output.read_bytes() == b"previous complete result"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["slices.json"]


def test_inconsistent_report_counts_are_not_reported_as_measurements(monkeypatch, repo, tmp_path):
    data = payloads()
    logs = report([("main.c", "yes", "yes")]).replace("1/1", "0/1")
    data["import"] = response("import", {"imported": True}, logs=logs)
    _, result, _ = run_cli(monkeypatch, repo, tmp_path, data)
    assert result["status"] == "partial"
    assert result["counters"]["files_parsed"] is None
    assert "inconsistent frontend report" in result["error"]


def test_skipped_directory_links_are_counted(monkeypatch, repo, tmp_path):
    linked = repo / "linked"
    linked.mkdir()
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == linked or original(path))
    _, result, _ = run_cli(monkeypatch, repo, tmp_path, payloads())
    assert result["status"] == "partial"
    assert result["counters"]["directories_skipped"] == 1
    assert "Directory symlink not traversed" in result["error"]


def test_query_timings_reach_the_progress_log_and_nothing_else(monkeypatch, repo, tmp_path):
    """Timed stages wrap their payload (`queries.TIMING`). The client must unwrap it so the
    slices are byte-identical to an untimed run, and the timings must land only in the
    progress log -- they change every run, so in `slices` they would break reproducibility.
    """
    def timed(stage, value):
        return response(stage, {"value": value, "timings_ms": {"dataflow": 7.0},
                                "counts": {"batches": 2}, "body_ms": 9.0})

    def payload(stage, data):
        return json.loads(base64.b64decode(
            re.search(r"__SLICER_\w+__([A-Za-z0-9+/=]+)__END", data[stage]["stdout"])[1]))

    plain = payloads(case_a=[candidate()], flow_count=1)
    wrapped = {**plain, **{s: timed(s, payload(s, plain)) for s in ("flows", "context")}}
    _, expected, _ = run_cli(monkeypatch, repo, tmp_path / "plain", plain)
    progress = tmp_path / "progress.jsonl"
    _, result, _ = run_cli(monkeypatch, repo, tmp_path / "timed", wrapped,
                           "--progress", str(progress))

    assert result["slices"] == expected["slices"] and result["slices"]
    assert "timings" not in json.dumps(result)
    ends = [json.loads(line) for line in progress.read_text(encoding="utf-8").splitlines()]
    timed_ends = [e for e in ends if e["event"] == "query_end" and "timings" in e]
    assert {e["stage"] for e in timed_ends} == {"flows", "context"}
    assert timed_ends[0]["timings"]["timings_ms"] == {"dataflow": 7.0}


def test_only_per_candidate_stages_are_timed():
    """The per-candidate stages carry the timing prelude; one-shot and file-transport stages
    do not (`file_envelope` has its own result shape)."""
    from slicer_backward import backward_b_query

    back = backward_b_query({**candidate(), "entry": candidate(), "scopes": [candidate()["scope"]]},
                            max_depth=None, max_nodes=None, max_call_depth=4)
    for text in (queries.flows_query([candidate()["scope"]], 3),
                 queries.context_query([("main.c", 1)], "/work/repo"), back):
        assert "def mark(k: String)" in text and '"timings_ms"' in text
    for text in (queries.candidates_a_query(), queries.candidates_b_query(),
                 queries.inventory_query(), queries.import_query("/r", "p")):
        assert "def mark(" not in text


def test_session_library_replaces_every_placeholder_and_calls_carry_the_data():
    """slicer_library generates functions from the self-contained builders by swapping
    placeholders for parameters. A leftover placeholder would silently query a fake
    candidate; a builder edit that duplicates or drops one must fail construction."""
    import slicer_library

    lib = slicer_library.Library("/work/repo", 3, {"maxCallDepth": 8}, 30, 5000, 4)
    assert re.search(r"__LIB_|99900\d", lib.definition) is None
    for name in ("flows", "context", "backward"):
        assert f"def {name}(" in lib.definition
    assert lib.definition.startswith(f"object {lib.name} {{")
    assert "maxCallDepth = 8" in lib.definition and "observed.take(3)" in lib.definition
    call = lib.backward({**candidate("sprintf", 5), "entry": candidate("sprintf", 5),
                         "scopes": [candidate()["scope"]]})
    assert f"{lib.name}.backward(" in call and '"sprintf(buf)"' in call
    assert '__SLICER_context__' in lib.context([("main.c", 1)])
    # Different settings -> different object name: a session never reuses a stale library.
    assert slicer_library.Library("/work/repo", 4, {}, 30, 5000, 4).name != lib.name
    with pytest.raises(ValueError, match="occurs 0 times"):
        slicer_library.as_function("flows", "{ x }", "f", "", [("absent", "y")])
    with pytest.raises(ValueError, match="not a timed envelope"):
        slicer_library.as_function("flows", "{ x }", "f", "", [])


def test_session_library_scope_filter_follows_method_selector(monkeypatch):
    """#322 review MEDIUM-1: the library kept its own copy of the `method_selector`
    predicate, so editing `method_selector` changed the inline builders and left the session
    library on the old predicate -- with every test green. Edit the predicate the way a real
    change would; the library must follow it."""
    import slicer_backward
    import slicer_library

    real = queries.method_selector

    def edited(scopes):
        return real(scopes).replace("m.fullName ==", "m.name ==")

    monkeypatch.setattr(queries, "method_selector", edited)
    monkeypatch.setattr(slicer_backward, "method_selector", edited)
    definition = slicer_library.Library("/work/repo", 3, {}, 30, 5000, 4).definition
    assert "m.fullName ==" not in definition
    assert definition.count("m.name == fn") == 2  # flows and backward


def test_session_library_cuts_the_body_at_the_envelopes_own_seams(monkeypatch):
    """#322 review MEDIUM-2: `as_function` cut the Scala body out of the envelope by
    hard-coded text (a leading `{ ` and the marker), so reframing the envelope produced a
    library that only fails to compile on a live server -- with every test green. The
    generated definition must not depend on how the envelope frames its body."""
    import slicer_backward
    import slicer_library

    expected = slicer_library.Library("/work/repo", 3, {}, 30, 5000, 4).definition
    real = queries.envelope

    def reframed(stage, body, *, timed=False):
        return "locally " + real(stage, body, timed=timed)

    monkeypatch.setattr(queries, "envelope", reframed)
    monkeypatch.setattr(slicer_backward, "envelope", reframed)
    assert slicer_library.Library("/work/repo", 3, {}, 30, 5000, 4).definition == expected


def test_a_script_run_isolates_a_poisoned_candidate(monkeypatch, repo, tmp_path):
    """#363: the #219 isolation, run the way the docs run the slicer -- `python slicer.py`
    (here `runpy` with run_name `__main__`, the same module execution). The script's QueryError
    must be the class slicer_pipeline catches. Before the fix the pipeline caught the QueryError
    of a second `slicer` module, so the poisoned middle candidate ended the whole run:
    candidates_failed 0 and the third candidate never sliced."""
    import runpy
    import sys
    import urllib.request

    data = payloads(case_a=[candidate("a", 1), candidate("m", 2), candidate("z", 3)], flow_count=2)
    calls = []

    def flows(query):
        calls.append(query)
        if len(calls) == 2:
            return {"success": True, "stdout": "java.lang.StackOverflowError", "stderr": ""}
        return response(
            "flows",
            {
                "flow_count": 2,
                "truncated": False,
                "lines": [{"file": "main.c", "line": 5}],
                "endpoints": [],
            },
        )

    data["flows"] = flows
    # the script binds `urlopen` at exec time (`from urllib.request import urlopen`)
    monkeypatch.setattr(urllib.request, "urlopen", MockTransport(data))
    output = tmp_path / "slices.json"
    monkeypatch.setattr(sys, "argv", ["slicer.py", "--repo", str(repo), "--out", str(output)])
    with pytest.raises(SystemExit):
        runpy.run_path(slicer.__file__, run_name="__main__")
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["counters"]["candidates_failed"] == 1
    assert result["counters"]["candidates_not_processed"] == 0
    assert result["counters"]["candidates_degraded"] == 1
    assert len(calls) == 3
