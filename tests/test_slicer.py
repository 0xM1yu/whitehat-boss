"""Offline slicer contract tests. Joern and its HTTP transport are always mocked.

**이 파일이 통과한다고 CPGQL 이 맞는 것은 아니다.** mock 이 HTTP 경계에 있으므로
`candidates_case_a/b`·`flow_count`·`truncated`·slice 의 라인 소속은 전부 **mock 이 박은 값을
되읽는다.** 질의 문자열에 대해 여기서 확인되는 것은 substring 과 위치뿐이고,
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
"""

import base64
import copy
import json
import re
import threading
from pathlib import Path
from urllib.error import URLError

import pytest

import slicer
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
        return self.Reply(payload)

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
        "case_a": response("case_a", [] if case_a is None else case_a),
        "case_b": response("case_b", [] if case_b is None else case_b),
        "flows": response(
            "flows",
            {
                "flow_count": flow_count,
                "truncated": truncated,
                "lines": [{"file": "main.c", "line": 5}] if flow_count else [],
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


def test_stack_overflow_stops_and_keeps_completed_slices(monkeypatch, repo, tmp_path):
    data = payloads(case_a=[candidate("a"), candidate("z")], flow_count=2)
    calls = []

    def flows(query):
        calls.append(query)
        if len(calls) == 2:
            return {"success": True, "stdout": "java.lang.StackOverflowError", "stderr": ""}
        return response(
            "flows", {"flow_count": 2, "truncated": False, "lines": [{"file": "main.c", "line": 5}]}
        )

    data["flows"] = flows
    code, result, transport = run_cli(monkeypatch, repo, tmp_path, data)
    assert code == 2 and result["status"] == "partial"
    assert len(result["slices"]) == 1
    assert result["counters"]["candidates_not_processed"] == 1
    assert result["counters"]["queries_aborted"] == 1
    assert transport.stages[-1] == "flows"


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
    flow = next(
        json.loads(req.data)["query"]
        for req, _ in transport.requests
        if "__SLICER_flows__" in json.loads(req.data)["query"]
    )
    assert ".take(4)" in flow and "observed.take(3)" in flow


def test_deterministic_slices_under_reordered_candidates(monkeypatch, repo, tmp_path):
    data = payloads(
        case_a=[candidate("z"), candidate("a")], case_b=[candidate("sprintf", 5)], flow_count=2
    )
    _, first, _ = run_cli(monkeypatch, repo, tmp_path, data)
    data["case_a"] = response("case_a", [candidate("a"), candidate("z")])
    _, second, _ = run_cli(monkeypatch, repo, tmp_path, data)
    assert (
        json.dumps(first["slices"], ensure_ascii=False).encode()
        == json.dumps(second["slices"], ensure_ascii=False).encode()
    )
    assert first["slices"][0]["id"] == "slice_0001"
    assert first["slices"][0]["absorbed"] == ["slice_0002"]
    assert first["slices"][1]["case"] == "B"


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


def test_invalid_utf8_source_is_counted(monkeypatch, repo, tmp_path):
    (repo / "bad.cpp").write_bytes(b"\xff\xfe\xfd")
    _, result, _ = run_cli(monkeypatch, repo, tmp_path, payloads())
    assert result["status"] == "partial"
    assert result["counters"]["files_seen"] == 2
    assert result["counters"]["files_read_failed"] == 1
    assert "bad.cpp" in result["error"]


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


def test_crossfile_flow_and_new_context_survive_assembly(monkeypatch, repo, tmp_path):
    (repo / "other.cpp").write_text("void helper() {\n    use();\n}\n", encoding="utf-8")
    data = payloads(case_a=[candidate()])
    inventory, _ = slicer.decode_response(data["inventory"], "inventory")
    inventory["files"].append("other.cpp")
    data["inventory"] = response("inventory", inventory)
    data["flows"] = response(
        "flows", {"flow_count": 1, "truncated": False, "lines": [{"file": "other.cpp", "line": 2}]}
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
        if any(f"__SLICER_{stage}__" in query for stage in ("case_b", "inventory", "flows")):
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
    assert ".take(" not in query
    context = queries.context_query([("main.c", 1)], "/work/repo")
    assert '.stripPrefix("/work/repo/").stripPrefix("./")' in context
    assert "replace('\\\\', '/')" in context


def test_query_strings_pin_the_defects_the_live_run_found():
    # 아래 assert 들이 주장하는 것은 **그 결함이 다시 들어오면 죽는다** 뿐이다 — 질의가
    # 의미상 옳다는 주장이 아니다. 파일 docstring 대로 mock 은 HTTP 경계에 있고 Scala 는
    # 여기서 실행되지 않으므로, 각 문자열이 실제 CPG 에서 무엇을 고르는지는 확인되지 않는다.
    # 다섯 번째(null method sink)는 test_orphan_flow_sinks_are_reported_as_partial 이 든다.
    inventory = queries.inventory_query()
    # `cpg.metaData.overlays.l` 은 이미 평평하다. `.flatten` 이 String 을 Char 로 쪼개
    # `ujson.Arr.from` 이 컴파일되지 않았다. 아래 줄은 위 줄에 가리지 않는다 — 같은 실수가
    # 옆 필드(`cpg.file.name.l`)에 앉으면 위 줄은 통과하고 아래 줄만 죽는다.
    assert "cpg.metaData.overlays.l.distinct.sorted" in inventory
    assert ".l.flatten" not in inventory

    context = queries.context_query([("main.c", 1)], "/work/repo")
    # 선언과 적용 지점을 따로 본다 — 한쪽만 남아도 합성 METHOD_REF/TYPE_DECL 이 다시
    # `<global>` 로 올라간다. 적용 형태를 문자로 박으므로 같은 뜻의 재작성에도 걸린다.
    assert "def sourceNode(n: nodes.AstNode): Boolean" in context
    assert ".filter(sourceNode)" in context

    flows = queries.flows_query([candidate()["scope"]], 3)
    # 괄호가 빠지면 Scala 가 `"truncated" -> observed.size > 3` 을 튜플 비교로 파싱한다.
    # 괄호 없는 판도 substring 검사는 통과하므로 여기서 재는 것은 괄호뿐이다.
    assert "(observed.size > 3)" in flows
    # sink 하나마다 엔진을 새로 짓지 않는다 — 배치 단위는 enclosing method 다.
    assert "sinks.groupBy(_.method)" in flows


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
    row = candidate()
    row["scope"]["line"] = '1); throw new RuntimeException("injected"); //'
    _, result, transport = run_cli(monkeypatch, repo, tmp_path, payloads(case_a=[row]))
    assert result["status"] == "partial"
    assert result["counters"]["queries_aborted"] == 1
    assert result["counters"]["candidates_case_a"] is None
    assert "case_b" not in transport.stages and "flows" not in transport.stages


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
