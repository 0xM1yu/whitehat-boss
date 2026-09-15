"""Offline slicer contract tests. Joern and its HTTP transport are always mocked."""

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


def test_payload_delimiters_and_error_words_are_data():
    payload = [{"symbol": 'ErrorException|,:"\\\n한글', "line": 1}]
    assert slicer.decode_response(response("case_a", payload), "case_a")[0] == payload


def test_stack_overflow_stops_and_keeps_completed_slices(monkeypatch, repo, tmp_path):
    data = payloads(case_a=[candidate()], case_b=[candidate("sprintf", 5)], flow_count=2)
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
    data = payloads(case_b=[candidate("sprintf", 5)], flow_count=3, truncated=truncated)
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
    assert first["slices"][0]["absorbed"] == ["slice_0002", "slice_0003"]


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
        return slicer.subprocess.CompletedProcess(command, 0, "fixture-version\n", "")

    monkeypatch.setattr(slicer.subprocess, "run", version)
    _, result, _ = run_cli(
        monkeypatch, repo, tmp_path, payloads(), "--joern-version-command", "/opt/joern/joern"
    )
    assert result["joern_version"] == "fixture-version"
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
