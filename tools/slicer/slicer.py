#!/usr/bin/env python3
"""Offline, model-free Joern C/C++ slicer (Python 3.11+, standard library only).

The JSON artifact is also the failure record. Unobserved measurements stay null;
an empty successful query must carry a valid payload, never an empty REPL response.
"""

import argparse
import base64
import codecs
import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import UTC, datetime
from http.client import HTTPException
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import slicer_queries as queries
from slicer_candidates import CASE_RANK, DEFAULT_CASE_ORDER
from slicer_checkpoint import Checkpoint, digest
from slicer_claims import ClaimQueue
from slicer_compile import generated_header_dirs, resolve_compilation_database
from slicer_errors import QueryError  # re-exported: callers and tests use slicer.QueryError
from slicer_export import export_sources
from slicer_pipeline import SlicingStages
from slicer_source import (
    SOURCE_SUFFIXES,
    relative_file,
)

# ANSI 이스케이프. **SGR(색)만 벗기면 모자란다** — `\[2J`(화면 지움)·`\[K`(줄 지움)·
# OSC(`\]0;제목\`)가 남으면 오류 스캔이 그것을 보고, payload 마커 한가운데 끼면
# 마커가 잘려 「응답이 비었다」로 읽힌다. CSI 는 종단 바이트가 `@`~`~` 전체다.
ANSI = re.compile(
    r"\x1b\[[0-9;?]*[\x40-\x7E]"  # CSI — SGR(`m`)은 이 집합의 한 종단 바이트다
    # OSC — BEL 또는 ST 로 닫는다. **페이로드에서 줄바꿈을 뺀 것이 핵심이다**: 닫히지 않은
    # 오프너와 뒤쪽의 무관한 단독 BEL 이 짝지으면 사이의 여러 줄이 통째로 사라져 진짜
    # 스택트레이스가 지워진다. 오탐은 시끄럽지만 이 미탐은 조용하다.
    r"|\x1b][^\x07\x1b\n]*(?:\x07|\x1b\\)"
    r"|\x1b[@-Z\\-_]"  # 두 글자 이스케이프
)
def strip_ansi(text: str) -> str:
    """ANSI 이스케이프를 **줄 단위로** 벗긴다 — 한 시퀀스가 줄을 넘지 못하게.

    닫는 BEL/ST 가 없는 OSC 오프너와 스트림 뒤쪽의 무관한 BEL 이 짝지으면 사이의 여러 줄이
    통째로 사라지고, 거기 있던 진짜 스택트레이스가 `ERROR` 스캔에서 안 보인다.
    `splitlines()` 로 먼저 쪼개면 그 일이 구조적으로 안 난다 — 「줄」의 정의가
    `scannable()`·`parse_report()` 가 쓰는 그 함수와 같아진다. `keepends=True` 라 이어
    붙이면 원문이 복원된다(이스케이프만 빠진 채로).

    ⚠ **대가가 있고, 그것을 지불하기로 한 것이다.** 줄을 넘는 삼킴을 막으면 **미종결 OSC의
    꼬리가 텍스트로 남는다** — 페이로드에 줄 경계문자(`\\r`·`\\v` 등)가 끼면 오프너가
    속한 줄 안에 종단자가 없어 OSC 매치가 실패하고, `\\x1b]` 두 바이트만 2글자 이스케이프
    분기에 먹힌 채 나머지가 살아남는다. 결과는 둘 중 하나다.

    - 그 꼬리에 `Exception` 이 들었으면 **오탐** — 성공한 응답이 실패로 raise 된다.
    - 꼬리가 리포트 행 앞에 붙으면 `parse_report` 가 그 행을 못 세고 `files_parsed` 등이
      **`null` + 사유**가 된다(가짜 `0` 이 아니고 `errors` 에도 남는다).

    **둘 다 시끄러운 실패다.** 반대편(줄을 넘게 두는 것)은 진짜 크래시가 흔적 없이 사라지는
    **조용한 미탐**이고, §0 이 *"측정이 틀리면 결론이 틀리고 그건 안 돌아온다"* 로 막는 쪽이
    그것이다. 그래서 이쪽을 고른다.

    ⚠ **OSC 정규식의 제외 집합을 `splitlines()` 집합에 맞추는 것은 여기서 no-op 이다** —
    쪼갠 뒤엔 경계문자가 줄 종단으로만 오므로 어느 쪽이든 매치가 실패한다(실측 확인).
    그러니 「제외 집합을 넓혀서 고쳤다」고 적지 않는다.
    """
    return "".join(ANSI.sub("", line) for line in text.splitlines(keepends=True))


ERROR = re.compile(
    r"StackOverflowError|OutOfMemoryError|\b[\w.]+Exception\b|\b(?:\w+\.)+\w+Error\b|"
    r"(?im:^\s*(?:--.*(?:Error|error)|error\s*:|error\b.*(?:not found|value)|"
    r"\d+ errors? found|.*\bCompilation (?:failed|error)|.*\[ERROR\]))"
)
# Joern frontend 파싱 리포트의 한 행 —
# `| 1 | src/net/HttpException.cpp | 42 | yes | yes | 12ms |`. 리포트를 **세는 쪽**과
# 오류 스캔에서 **빼는 쪽**이 같은 인식기를 써야 한다. 갈리면 한쪽이 리포트로 세는 줄을
# 다른 쪽이 오류 로그로 읽는다.
def report_row(line: str) -> list[str] | None:
    parts = [part.strip() for part in line.split("|")]
    if len(parts) == 8 and parts[1].isdigit() and parts[4] in ("yes", "no"):
        return parts if parts[5] in ("yes", "no") else None
    return None


# 개별 파일 실패가 경로를 인용한다. 그 파일 하나의 실패이지 배치의 실패가 아니다 —
# `parse_report` 가 세어 `errors` 에 남긴다.
QUOTED_PATH = re.compile(r"(Failed to parse|Failed to generate a CPG for:)\s*'[^']*'")


def scannable(text: str) -> str:
    r"""오류 스캔에서 **파일 이름이 사는 자리**를 뺀다.

    `ERROR` 의 `\b[\w.]+Exception\b` 는 `[\w.]+` 가 `.` 을 단어문자와 동급으로 먹어
    `HttpException.cpp` 의 `HttpException` 에서 경계 조건을 만족한다. 그래서 CPG가 멀쩡히
    만들어져도 **소스 파일 이름 하나로 import 가 전역 실패했다** — C/C++ 저장소에서
    클래스명=파일명은 흔한 컨벤션이라 HTTP·파서 라이브러리는 거의 반드시 하나쯤 있다.

    **빼는 것은 이름이 사는 두 자리뿐이다** — 리포트 표의 행과 실패 메시지의 인용 경로.
    나머지 줄은 그대로 훑는다. 정규식을 좁히는 쪽(예: throwable 뒤에 `:` 를 요구)이 더
    작은 수정이지만, 그쪽은 **진짜 오류를 놓치는 방향**으로 틀린다 — §0이 경계하는 쪽이다.
    """
    return "\n".join(
        QUOTED_PATH.sub(r"\1 '<path>'", line)
        for line in text.splitlines()
        if report_row(line) is None
    )


def _decode_bad_bytes_as_latin1(exc: UnicodeDecodeError) -> tuple[str, int]:
    """Map only the invalid bytes to their Latin-1 characters (U+0080..U+00FF).

    Valid UTF-8 elsewhere in the file is left intact (a `©` or a Korean comment stays
    what it is), unlike a blanket `raw.decode("latin-1")` that would reinterpret every
    byte. The result has no surrogates, so it still re-encodes to UTF-8 for the JSON
    output (`assemble`, `export_sources`) -- surrogateescape would crash there instead.
    """
    return bytes(exc.object[exc.start:exc.end]).decode("latin-1"), exc.end


codecs.register_error("slicer_latin1_bad_bytes", _decode_bad_bytes_as_latin1)


def _read_no_follow(path: Path) -> bytes:
    """심링크를 **open 시점에** 거부하고 읽는다.

    호출부가 바로 앞에서 `resolve().relative_to(repo)` 로 저장소 밖 타깃을 막지만, 그 검사와
    `read_text()` 는 **서로 다른 두 번의 조회**다. 그 사이에 링크가 바뀌면 검사한 것과 읽는
    것이 달라진다(TOCTOU). 이 도구는 **감사 대상이 쓴 저장소**를 읽으므로 그 창을 연 채로
    둘 이유가 없다.

    **바이트를 그대로 준다.** 호출부가 원본 해시를 뜬 뒤에 줄바꿈을 접는다 — 접기를
    빠뜨리면 CRLF 가 `sources` 에 남아 조립한 줄마다 CR 이 하나씩 붙고, 먼저 접으면
    CRLF 사본과 LF 사본이 같은 해시를 받아 재개가 다른 파일을 통과시킨다.

    `O_NOFOLLOW` 가 없는 플랫폼에서는 그냥 읽는다 — 앞선 `resolve()` 검사가 정적 심링크는
    이미 막고 있어, 없는 곳에서 기능을 끄는 것이 아니라 창만 남는다.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    try:
        handle = os.fdopen(descriptor, "rb")
    except BaseException:
        # fdopen 이 소유권을 가져가기 전에 실패하면 raw fd 가 남는다. 저장소의 파일마다
        # 한 번씩 도는 루프라 계통적 실패에서 누적된다.
        os.close(descriptor)
        raise
    with handle:
        return handle.read()


COUNTERS = (
    "files_seen",
    "files_parsed",
    "files_parse_failed",
    "candidates_case_a",
    "candidates_case_b",
    "slices_before_clustering",
    "slices_after_clustering",
    "flows_truncated",
    "queries_aborted",
    "cpg_seconds",
    "total_seconds",
    "files_read_failed",
    "files_in_cpg",
    "files_missing_from_cpg",
    "files_cpg_failed",
    "methods_internal",
    "calls",
    "calls_unresolved",
    "unknown_nodes",
    "locations_unmapped",
    "candidates_unmapped",
    "candidates_invalid",
    "candidates_not_processed",
    "candidates_failed",
    "candidates_degraded",
    "engine_flows_truncated",
    "directories_skipped",
    "files_symlink_skipped",
    "flow_sinks_without_method",
    "backward_truncated",
    "candidates_reused",
    "candidate_stages_reused",
)
LIMITATIONS = [
    "single-function stack overflow not covered",
    "free() aliasing not resolved",
    "cross-function double free not covered",
    "fuzzy parsing and missing call edges can inflate source-first candidates",
    "a CPG file node does not prove successful or complete parsing",
    "Case A sources are enclosing-method parameters; parameterless readers may yield zero flows",
    "Joern engine-internal limits are not measured by flows_truncated",
    "Case A candidates are capped (api and readers each) in slicer_queries; at the cap the "
    "kept subset is an unspecified first-N in CPG traversal order, so a capped run is not "
    "distinguishable from a repo with exactly that many candidates without the measurement note",
    "Case A silently drops methods with an empty name (seen when c2cpg parses a header without "
    "build info); they are not counted, so such a method is never a candidate",
    "Case A silently drops methods whose body block holds no statement (prototypes and "
    "definitions with an empty body); they are not counted, so such a method is never a candidate",
    "flow cap stops further method batches, but does not bound engine work within one batch",
    "structure recovery is lexical and does not guarantee compilable preprocessed C/C++",
    "an unterminated OSC escape leaves its payload as text: it can raise a false "
    "query error, or drop a frontend report row to null with a reason",
]
# Added to known_limitations only when compile-aware import is active (see build_cpg). Two
# consequences of analysing one build configuration through c2cpg --compilation-database:
COMPILE_AWARE_LIMITATIONS = [
    "compile-aware import analyzes ONE build configuration (the given compile_commands); code "
    "compiled only under a different configuration is not in the graph (multi-config union is "
    "not attempted -- Refs #271)",
    "c2cpg --compilation-database does not define target/platform macros (e.g. __linux__, "
    "__x86_64__) unless the command passes -D, so branches guarded by them are inactive and "
    "this path does not parse inactive branches; such code can be dropped (verified by full "
    "slicing, not by CI)",
]


def valid_location(value: object) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("file"), str)
        and type(value.get("line")) is int
    )


CANDIDATE_STAGES = ("case_a", "case_b")
# How many dropped rows a diagnostics entry quotes (the count next to it stays exact).
INVALID_ROW_SAMPLE = 3


def candidate_row_problem(stage: str, value: object) -> str | None:
    """The first field that makes one candidate row unusable, or None when the row is valid.

    #361: a candidate payload is checked row by row, so one bad row is dropped and counted
    (`Slicer.drop_invalid_candidates`) instead of voiding the whole discovery -- it used to raise
    `Invalid case_x payload schema` and end the run with 0 slices (#347). The checks are the ones
    the old all-rows test applied; only the unit they reject changed."""
    if not valid_location(value):
        return "location"
    if not isinstance(value.get("symbol"), str) or not value["symbol"]:
        return "symbol"
    scope = value.get("scope")
    if not valid_location(scope) or not isinstance(scope.get("full_name"), str):
        return "scope"
    if not isinstance(value.get("anchors"), list) or not all(
        valid_location(point) for point in value["anchors"]
    ):
        return "anchors"
    if stage == "case_b":
        selector = value.get("selector")
        if not (
            isinstance(selector, dict)
            and type(selector.get("line")) is int
            and type(selector.get("column")) is int
            and isinstance(selector.get("code"), str)
            and isinstance(selector.get("ast_path"), list)
            and all(type(n) is int and n >= 0 for n in selector["ast_path"])
        ):
            return "selector"
    return None


def valid_candidate(value: object) -> bool:
    return candidate_row_problem("case_a", value) is None


def validate_payload(stage: str, data: object, logs: str) -> None:
    """Validate before counters or Scala selectors consume untrusted HTTP fields.

    A candidate stage is validated here only as a whole (it must be a list); its rows are
    validated one by one right after, in `Slicer.query`, which drops the invalid ones and fails
    the stage when none is valid (#361)."""
    valid = False
    if stage in CANDIDATE_STAGES:
        valid = isinstance(data, list)
    elif stage == "context":
        valid = isinstance(data, list) and all(valid_location(row) for row in data)
    elif stage == "import":
        valid = isinstance(data, dict) and data.get("imported") is True
    elif stage == "save_cpg":
        valid = isinstance(data, dict) and data.get("saved") is True
    elif stage in ("overlay", "library"):
        valid = data is True
    elif stage == "backward" and isinstance(data, dict):
        valid = (
            data.get("flow_count") is None
            and type(data.get("truncated")) is bool
            and all(
                type(data.get(k)) is int and data[k] >= 0
                for k in ("node_count", "state_count", "dependency_count")
            )
            and isinstance(data.get("lines"), list)
            and all(valid_location(p) for p in data["lines"])
            and isinstance(data.get("boundaries"), list)
            and all(
                isinstance(b, dict)
                and isinstance(b.get("reason"), str)
                and valid_location(b.get("location"))
                for b in data["boundaries"]
            )
            and isinstance(data.get("truncation_reasons"), list)
            and all(isinstance(r, str) for r in data["truncation_reasons"])
        )
    elif stage == "flows" and isinstance(data, dict):
        valid = (
            type(data.get("flow_count")) is int
            and data["flow_count"] >= 0
            and type(data.get("truncated")) is bool
            and isinstance(data.get("lines"), list)
            and all(valid_location(row) for row in data["lines"])
            and (data["flow_count"] > 0 or not data["lines"])
            # `endpoints` is optional (#433 review): the query computes it inside a Try, so a
            # failure there must not turn the whole flows result into a QueryError. Present ->
            # well-formed; absent -> the slice records source/sink as unavailable. The query
            # emits exactly one of `endpoints` / `endpoints_error`, never both.
            and (
                "endpoints" not in data
                or (
                    isinstance(data["endpoints"], list)
                    and len(data["endpoints"]) <= data["flow_count"]
                    and all(
                        isinstance(pair, dict)
                        and valid_location(pair.get("source"))
                        and valid_location(pair.get("sink"))
                        for pair in data["endpoints"]
                    )
                )
            )
            and (
                "endpoints_error" not in data
                or (isinstance(data["endpoints_error"], str) and "endpoints" not in data)
            )
        )
    elif stage == "inventory" and isinstance(data, dict):
        engine = data.get("engine_config")
        valid = (
            all(
                isinstance(data.get(key), list) and all(isinstance(x, str) for x in data[key])
                for key in ("files", "overlays")
            )
            and all(
                type(data.get(key)) is int and data[key] >= 0
                for key in (
                    "methods_internal",
                    "calls",
                    "calls_unresolved",
                    "unknown_nodes",
                    "flow_sinks_without_method",
                )
            )
            and isinstance(engine, dict)
            and all(
                type(engine.get(key)) is int
                for key in ("maxCallDepth", "maxArgsToAllow", "maxOutputArgsExpansion")
            )
        )
    if not valid:
        raise QueryError(f"Invalid {stage} payload schema", logs)


def decode_response(
    response: dict, stage: str, payload_path: Path | None = None
) -> tuple[object, str]:
    if not isinstance(response, dict):
        raise QueryError("HTTP response is not an object")
    stdout, stderr = response.get("stdout", ""), response.get("stderr", "")
    if not isinstance(stdout, str) or not isinstance(stderr, str):
        raise QueryError("stdout/stderr must be strings")
    logs = strip_ansi(stdout + "\n" + stderr)
    match = re.search(rf"__SLICER_{stage}__([A-Za-z0-9+/=]+)__END_SLICER__", logs)
    # Inspect diagnostics outside the payload, so a source identifier named Error
    # cannot be mistaken for a failed interpreter request.
    diagnostics = logs if match is None else logs[: match.start()] + logs[match.end() :]
    scanned = scannable(diagnostics)
    failure = ERROR.search(scanned)
    success = response.get("success")
    if not (success is True or success == "true") or failure:
        raise QueryError(
            diagnostics.strip() or "Joern returned success=false",
            logs,
            poisoned="StackOverflowError" in scanned or "OutOfMemoryError" in scanned,
        )
    if match is None:
        raise QueryError(
            "Missing or incomplete query payload (empty stdout is not zero results)", logs
        )
    if payload_path is not None:
        # File transport: the marker carries the byte count Joern wrote, the payload is in
        # the file. Confirming the count catches a truncated/partial write instead of
        # feeding half a JSON document to json.loads (which would raise the same way).
        try:
            declared = int(match[1])
        except ValueError as exc:
            raise QueryError(f"Payload marker is not a byte count: {exc}", logs) from exc
        try:
            raw = payload_path.read_bytes()
        except OSError as exc:
            raise QueryError(f"Payload file unreadable: {exc}", logs) from exc
        if len(raw) != declared:
            raise QueryError(
                f"Payload file is {len(raw)} bytes, marker declared {declared}", logs
            )
        try:
            return json.loads(raw.decode("utf-8")), logs
        except (ValueError, UnicodeError) as exc:
            raise QueryError(f"Invalid query payload: {exc}", logs) from exc
    try:
        return json.loads(base64.b64decode(match[1], validate=True).decode("utf-8")), logs
    except (ValueError, UnicodeError) as exc:
        raise QueryError(f"Invalid query payload: {exc}", logs) from exc


class JoernClient:
    def __init__(
        self, endpoint: str, timeout: float | None, payload_dir: Path | None = None
    ):
        self.url = endpoint.rstrip("/") + "/query-sync"
        self.timeout = timeout
        # Where file-transport queries (FILE_TRANSPORT_STAGES) drop their payload. Joern (the JVM)
        # writes it and this process reads it, so the path must be on a filesystem both see
        # -- the same box today; a shared writable mount once Joern runs sandboxed.
        self.payload_dir = payload_dir

    def query(self, stage: str, query: str) -> tuple[object, str]:
        if self.timeout is None:
            return self._request(stage, query)
        outcome = []

        def request_once() -> None:
            try:
                outcome.append(self._request(stage, query))
            except Exception as exc:
                outcome.append(exc)

        # urllib's socket timeout resets on reads. A daemon worker also imposes a
        # deadline on the whole request, including DNS and a trickling response.
        worker = threading.Thread(target=request_once, daemon=True)
        worker.start()
        worker.join(self.timeout)
        if worker.is_alive():
            raise QueryError(
                f"Query deadline exceeded ({self.timeout} seconds); server execution may continue",
                poisoned=True,
            )
        if isinstance(outcome[0], Exception):
            raise outcome[0]
        return outcome[0]

    def _request(self, stage: str, query: str) -> tuple[object, str]:
        payload_path = None
        # Key file transport on the stage, not on the token appearing in the query text: a
        # repo file or symbol named like the token would otherwise trip a non-transport query.
        if stage in queries.FILE_TRANSPORT_STAGES:
            if self.payload_dir is None:
                raise QueryError(f"File-transport {stage} query has no payload_dir configured")
            # A fresh name per request. Two runs sharing an output parent (or a resume) must
            # not read a stale payload another wrote -- the marker carries only a byte count
            # (`decode_response`), so a same-length payload from elsewhere would pass.
            payload_path = self.payload_dir / f"{stage}.{uuid.uuid4().hex}.payload.json"
            # `as_posix()` keeps the JSON literal backslash-free so the Scala string is valid
            # on any host; `payload_dir` is resolved by the caller so this is absolute and both
            # Joern and this process open the same file.
            query = query.replace(queries.OUT_PATH_TOKEN, json.dumps(payload_path.as_posix()))
        try:
            request = Request(
                self.url,
                json.dumps({"query": query}).encode("utf-8"),
                {"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=self.timeout) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except (OSError, URLError, ValueError, HTTPException) as exc:
            # HTTP timeout does not cancel Scala execution. Do not reuse this REPL.
            raise QueryError(f"HTTP request failed: {exc}", poisoned=True) from exc
        try:
            return decode_response(decoded, stage, payload_path)
        finally:
            # Drop the payload so a work dir does not accumulate them. A cleanup error must
            # not mask a successful decode, so swallow it.
            if payload_path is not None:
                try:
                    payload_path.unlink(missing_ok=True)
                except OSError:
                    pass


def initial_result(repo: Path, threshold: float) -> dict:
    return {
        "slice_version": "slice-v2",
        "repo": repo.name,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "joern_version": None,
        "threshold": threshold,
        "status": "failed",
        "slices": [],
        "candidate_manifest": {},
        "counters": dict.fromkeys(COUNTERS),
        "measurement_notes": {key: "Stage not reached" for key in COUNTERS},
        "known_limitations": LIMITATIONS.copy(),
        "diagnostics": [],
    }


class Slicer(SlicingStages):
    def __init__(
        self,
        repo: Path,
        threshold: float,
        max_flows: int | None,
        client: JoernClient,
        engine_limits: dict[str, int] | None = None,
    ):
        self.repo = repo.absolute()
        self.result = initial_result(self.repo, threshold)
        self.client = client
        self.max_flows = max_flows
        self.engine_limits = engine_limits or {}
        self.result["joern_engine_config"] = None
        self.result["measurement_notes"]["joern_engine_config"] = "Inventory not reached"
        self.result["measurement_notes"]["engine_flows_truncated"] = (
            "Joern does not expose a count of paths discarded by its internal engine limits"
        )
        self.sources: dict[str, list[str]] = {}
        self.errors: list[str] = []
        self.cpg_ready = False
        self.completed: list[dict] = []
        self.candidates: list[dict] = []
        self.processed = 0
        self.checkpoint = None
        self.export_only = False
        # #368: `--prepare-only` builds the CPG and discovers candidates once, then saves the graph
        # to `cpg_out`; a shard given `cpg_file` (and a copy of that checkpoint) loads the graph
        # instead of parsing the repo and reuses the discovery instead of re-running it.
        self.prepare_only = False
        # #369: shared claim queue (slicer_claims.ClaimQueue) instead of the fixed p % N split.
        self.claims = None
        self.cpg_out: Path | None = None
        self.cpg_file: Path | None = None
        # The checkpoint's `prepared` record a `cpg_file` was verified against (check_prepared).
        self.prepared: dict | None = None
        self.shard: tuple[int, int] | None = None
        # #377 (b): repo-relative files whose candidates are sliced first (--priority-files) --
        # inside each case under the default `ba`, ahead of the whole rest under `ab`.
        self.priority_files: set[str] | None = None
        # --skip-candidates: candidate keys this process does not query (not part of the contract).
        self.skip_keys: frozenset[str] = frozenset()
        # --priority-case-a: that list also steers WHICH Case A methods exist (candidates_a_query),
        # not only the order. Needs priority_files; main() refuses the flag without a list.
        self.priority_case_a = False
        # --import-include-root: build_cpg gives c2cpg the repo root as a header include path.
        self.import_include_root = False
        # Compile-aware import (set from the CLI). compile_commands is the input database;
        # compile_db_dir is where the normalized copy is written (a shared location a remote
        # Joern can read); compiled_files is the set the database compiles, filled in by
        # build_cpg and used by inventory to tell a genuine parse miss from an uncompiled file.
        self.compile_commands: Path | None = None
        self.compile_db_dir: Path | None = None
        self.compiled_files: set[str] | None = None
        self.settings = {}
        self.code_hashes = {}
        self.source_hashes = {}
        self.active_candidate = None
        self.candidate_started = None
        self.progress = None
        self.progress_lock = threading.Lock()
        self.heartbeat_seconds = 30.0
        for key in (
            "queries_aborted",
            "flows_truncated",
            "locations_unmapped",
            "candidates_unmapped",
            "candidates_failed",
            "candidates_degraded",
            "files_read_failed",
            "directories_skipped",
            "files_symlink_skipped",
            "candidates_reused",
            "candidate_stages_reused",
        ):
            self.measure(key, 0)

    def measure(self, key: str, value: int | float) -> None:
        self.result["counters"][key] = value
        self.result["measurement_notes"].pop(key, None)

    def increment(self, key: str, value: int = 1) -> None:
        self.measure(key, self.result["counters"][key] + value)

    def event(self, event: str, **fields) -> None:
        if self.progress:
            with self.progress_lock:
                self.progress.write(
                    json.dumps(
                        {
                            "event": event,
                            "time": time.time(),
                            "candidate_key": self.active_candidate,
                            **fields,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                self.progress.flush()

    def query(self, stage: str, query: str) -> tuple[object, str]:
        started = time.perf_counter()
        ended = threading.Event()
        original_timeout = self.client.timeout

        def heartbeat():
            while not ended.wait(self.heartbeat_seconds):
                self.event("heartbeat", stage=stage, elapsed_seconds=time.perf_counter() - started)

        worker = threading.Thread(target=heartbeat, daemon=True)
        self.event("query_start", stage=stage)
        worker.start()
        timings = {}
        try:
            budget = self.settings.get("candidate_timeout_seconds")
            if budget is not None and self.candidate_started is not None:
                left = budget - (time.perf_counter() - self.candidate_started)
                if left <= 0:
                    raise QueryError("Candidate deadline exceeded")
                self.client.timeout = min(left, original_timeout) if original_timeout else left
            data, logs = self.client.query(stage, query)
            # A timed envelope (queries.TIMING) wraps the payload. Timings go to the progress
            # log only: they change every run, so the checkpoint and `slices` never see them.
            if isinstance(data, dict) and data.keys() == queries.TIMED_KEYS:
                timings = {"timings": {k: data[k] for k in ("body_ms", "timings_ms", "counts")}}
                data = data["value"]
            validate_payload(stage, data, logs)
            if stage in CANDIDATE_STAGES:
                data = self.drop_invalid_candidates(stage, data)
            return data, logs
        except QueryError as exc:
            self.increment("queries_aborted")
            self.result["diagnostics"].append(
                {
                    "stage": stage,
                    "error": str(exc),
                    "logs": exc.logs,
                    "stop": True,
                    "candidate_key": self.active_candidate,
                }
            )
            if exc.poisoned:
                self.errors.append(
                    "Joern state is uncertain; stop and restart the dedicated server"
                )
            if "StackOverflowError" in str(exc):
                self.result["status"] = "partial"
            self.event("query_error", stage=stage, error=str(exc))
            raise
        finally:
            ended.set()
            worker.join()
            self.client.timeout = original_timeout
            self.event(
                "query_end", stage=stage, elapsed_seconds=time.perf_counter() - started, **timings
            )

    def drop_invalid_candidates(self, stage: str, rows: list) -> list:
        """Keep the valid rows of a candidate payload and account for the rest (#361).

        One malformed row used to fail the payload check and void the run (0 slices, #347). Now
        it is dropped, never silently: `counters.candidates_invalid` (both stages) counts it,
        `measurement_notes.candidates_invalid` names the stage and the failing field, a
        `diagnostics` entry quotes the first rows as ASCII JSON text, and the run is `partial`.
        A payload whose rows are ALL invalid is still a stage failure. The counter is measured (0
        included) the first time a candidate payload passes, so a run that never reached
        discovery keeps it null with "Stage not reached"."""
        kept, dropped, problems = [], [], {}
        for row in rows:
            problem = candidate_row_problem(stage, row)
            if problem is None:
                kept.append(row)
            else:
                problems[problem] = problems.get(problem, 0) + 1
                dropped.append(row)
        if rows and not kept:
            raise QueryError(f"Invalid {stage} payload schema: all {len(rows)} rows invalid")
        counters, notes = self.result["counters"], self.result["measurement_notes"]
        counted = counters["candidates_invalid"]
        if counted is None:
            self.measure("candidates_invalid", 0)  # first call only: measure() clears the note
        counters["candidates_invalid"] = (counted or 0) + len(dropped)
        if dropped:
            reasons = ", ".join(f"{field}: {count}" for field, count in sorted(problems.items()))
            note = f"{stage}: {len(dropped)} rows dropped ({reasons})"
            sample = dropped[:INVALID_ROW_SAMPLE]
            previous = notes.get("candidates_invalid")
            notes["candidates_invalid"] = f"{previous}; {note}" if previous else note
            self.result["diagnostics"].append(
                {
                    "stage": stage,
                    "note": f"dropped {len(dropped)} invalid candidate rows ({reasons}); "
                    "the valid rows were kept",
                    # ASCII JSON text: a raw row can hold NaN or a lone surrogate, which
                    # write_result cannot encode -- the echo must not lose the run.
                    "rows": [json.dumps(row, ensure_ascii=True) for row in sample],
                }
            )
            self.errors.append(f"Dropped {len(dropped)} invalid {stage} candidate rows")
        return kept

    def load_sources(self) -> None:
        self.repo = self.repo.resolve()
        if not self.repo.is_dir():
            raise ValueError(f"Repository is not a directory: {self.repo}")
        paths = []

        # os.walk's default silently ignores inaccessible directories.
        def walk_error(exc: OSError) -> None:
            raise exc

        for directory, dirs, files in os.walk(self.repo, onerror=walk_error, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d != ".git")
            for name in dirs.copy():
                path = Path(directory) / name
                if path.is_symlink():
                    dirs.remove(name)
                    self.increment("directories_skipped")
                    self.errors.append(
                        f"Directory symlink not traversed: {path.relative_to(self.repo).as_posix()}"
                    )
            for name in sorted(files):
                path = Path(directory) / name
                if path.suffix.lower() not in SOURCE_SUFFIXES:
                    continue
                # A file symlink is refused at read time (O_NOFOLLOW, below), and one refused read
                # used to void the whole run through prepare_checkpoint's count check (#362 --
                # libmspack, accel-ppp). Skip and count it like a directory symlink instead: the
                # policy (never follow links in the audited repo) is unchanged, the run survives.
                if path.is_symlink():
                    self.increment("files_symlink_skipped")
                    self.errors.append(
                        f"File symlink not read: {path.relative_to(self.repo).as_posix()}"
                    )
                    continue
                paths.append(path)
        self.measure("files_seen", len(paths))
        for path in sorted(paths):
            name = path.relative_to(self.repo).as_posix()
            try:
                path.resolve().relative_to(self.repo)
                # Reject the symlink at open() time, not before it: resolve() and a later
                # open() are two lookups, and this tool reads repositories written by
                # whoever we are auditing. O_NOFOLLOW closes that window.
                raw = _read_no_follow(path)
                # Decode UTF-8; on invalid bytes, map ONLY those bytes to Latin-1 rather
                # than raising. A single stray Latin-1 byte (`°`, an accented author name
                # -- routine in real C/C++ comments and UI strings) used to make one file
                # unreadable and abort the whole run through `prepare_checkpoint`'s count
                # check (#254). Valid UTF-8 elsewhere is preserved (see the error handler),
                # and the text never carries surrogates so it re-encodes cleanly.
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    text = raw.decode("utf-8", errors="slicer_latin1_bad_bytes")
                    self.result["diagnostics"].append(
                        {"stage": "load_sources", "file": name,
                         "note": "invalid UTF-8 bytes decoded as Latin-1"}
                    )
                text = text.replace("\r\n", "\n").replace("\r", "\n")
                # Hash the bytes as they sit on disk, and only after the read has
                # succeeded: an OSError below leaves no `sources` entry and no hash, so
                # `prepare_checkpoint`'s count check still refuses a genuinely short read.
                # Folding newlines first would let a CRLF and an LF copy share a signature.
                self.source_hashes[name] = hashlib.sha256(raw).hexdigest()
                # splitlines also splits form feeds inside C source and shifts lineNumber.
                self.sources[name] = text.removesuffix("\n").split("\n") if text else []
            except (OSError, ValueError) as exc:
                self.increment("files_read_failed")
                self.errors.append(f"Source read failed: {name}: {exc}")

    def parse_report(self, logs: str) -> None:
        rows = {}
        for line in strip_ansi(logs).splitlines():
            parts = report_row(line)
            if parts is not None:
                name = relative_file(parts[2], self.repo)
                if name is not None:
                    rows[name] = (parts[4] == "yes", parts[5] == "yes")
        observed_failures = sorted(set(re.findall(r"Failed to parse '([^']+)'", logs)))
        cpg_failures = sorted(set(re.findall(r"Failed to generate a CPG for: '([^']+)'", logs)))
        self.result["diagnostics"].append(
            {
                "stage": "parse_report",
                "files": rows,
                "parse_failures_observed": observed_failures,
                "cpg_failures_observed": cpg_failures,
            }
        )
        # Require a report footer: partial log capture cannot establish a total count.
        footer = re.search(r"\|\s*Total\s*\|[^\n]*?\|\s*(\d+)/(\d+)\s*\|\s*(\d+)/(\d+)\s*\|", logs)
        totals = (
            sum(parsed for parsed, _ in rows.values()),
            len(rows),
            sum(cpg for _, cpg in rows.values()),
            len(rows),
        )
        complete = footer is not None and tuple(map(int, footer.groups())) == totals
        if complete:
            self.measure("files_parsed", sum(parsed for parsed, _ in rows.values()))
            self.measure("files_parse_failed", sum(not parsed for parsed, _ in rows.values()))
            self.measure("files_cpg_failed", sum(not cpg for _, cpg in rows.values()))
        else:
            if footer:
                self.errors.append("Incomplete or inconsistent frontend report")
            for key in ("files_parsed", "files_parse_failed", "files_cpg_failed"):
                self.result["measurement_notes"][key] = (
                    "Complete frontend report unavailable in HTTP output; "
                    "CPG file count is not parsing success"
                )
        if observed_failures or cpg_failures or any(not all(row) for row in rows.values()):
            self.errors.append("Frontend reported parsing or CPG generation failures")

    def build_cpg(self) -> None:
        started = time.perf_counter()
        logs = ""
        compilation_database = None
        if self.compile_commands is not None:
            try:
                normalized, compiled, ignored = resolve_compilation_database(
                    self.compile_commands, self.repo, self.compile_db_dir
                )
                compilation_database = normalized.as_posix()
                self.compiled_files = compiled
                self.result["diagnostics"].append(
                    {
                        "stage": "build_cpg",
                        "compile_aware": True,
                        "compile_commands": self.compile_commands.as_posix(),
                        "compiled_units": len(compiled),
                        # Entries dropped from the expected set because c2cpg ignores their
                        # folder (test/, CMakeFiles/, dotdirs) or non-source extension. Recorded
                        # so the exclusion is not silent (it must not read as a parse miss).
                        "ignored_units": ignored,
                        # Sibling generated_headers dirs added to the include path (relative), or
                        # [] when the convention did not match -- so this attachment is not a
                        # silent no-op the result JSON cannot show (review M2).
                        "generated_header_dirs": generated_header_dirs(self.compile_commands),
                    }
                )
                # These hold only under compile-aware import, so they are added here rather
                # than to the unconditional LIMITATIONS list.
                self.result["known_limitations"].extend(COMPILE_AWARE_LIMITATIONS)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                # A missing or malformed database must not lose the run: fall back to
                # importing every source file, which is the pre-compile-aware behaviour.
                self.compiled_files = None
                self.errors.append(
                    f"Compile-aware import unavailable, importing all sources: {exc}"
                )
                self.result["diagnostics"].append(
                    {"stage": "build_cpg", "compile_aware": False, "reason": str(exc)}
                )
        try:
            # A unique project prevents reuse of a stale graph from an earlier batch.
            project = "slicer_" + uuid.uuid4().hex
            payload, logs = self.query(
                "import",
                queries.import_cpg_query(self.cpg_file.as_posix(), project)
                if self.cpg_file is not None
                else queries.import_query(
                    self.repo.as_posix(), project, compilation_database, self.import_include_root
                ),
            )
            if not isinstance(payload, dict) or payload.get("imported") is not True:
                raise ValueError("CPG import did not confirm completion")
            self.cpg_ready = True
        except QueryError as exc:
            logs = exc.logs
            raise
        finally:
            self.measure("cpg_seconds", time.perf_counter() - started)
            self.parse_report(logs)

    def inventory(self) -> None:
        data, _ = self.query("inventory", queries.inventory_query(self.engine_limits))
        if not isinstance(data, dict) or not isinstance(data.get("files"), list):
            raise ValueError("Invalid inventory payload")
        files = {relative_file(file, self.repo) for file in data["files"]}
        found = set(self.sources) & files
        self.measure("files_in_cpg", len(found))
        # Under compile-aware import the graph holds only the compiled units, so an uncompiled
        # source is absent by design and must not read as a parse miss. Expect exactly the
        # files the database compiles; without a database, expect every source as before.
        expected = self.compiled_files if self.compiled_files is not None else set(self.sources)
        missing = sorted(expected - files)
        self.measure("files_missing_from_cpg", len(missing))
        if missing:
            self.errors.append("Source files missing from CPG: " + ", ".join(missing))
        for key in (
            "methods_internal",
            "calls",
            "calls_unresolved",
            "unknown_nodes",
            "flow_sinks_without_method",
        ):
            if type(data.get(key)) is not int or data[key] < 0:
                raise ValueError(f"Invalid inventory counter: {key}")
            self.measure(key, data[key])
        if data["flow_sinks_without_method"]:
            self.errors.append(
                f"Skipped {data['flow_sinks_without_method']} flow sinks "
                "without an enclosing method"
            )
        engine = data.get("engine_config")
        if not isinstance(engine, dict) or any(
            type(engine.get(key)) is not int
            for key in ("maxCallDepth", "maxArgsToAllow", "maxOutputArgsExpansion")
        ):
            raise ValueError("Invalid engine configuration payload")
        self.result["joern_engine_config"] = engine
        self.result["server"] = data.get("server")
        self.result["measurement_notes"].pop("joern_engine_config")
        if not {"ossdataflow", "dataflowOss"}.intersection(data.get("overlays", [])):
            self.query("overlay", queries.envelope("overlay", "run.ossdataflow; ujson.Bool(true)"))
        overlays = sorted(set(data["overlays"]) | {"dataflowOss"})
        if self.checkpoint:
            self.checkpoint.bind("overlays", overlays)


def positive_int(value: str) -> int:
    number = int(value)
    if not 0 < number < 2**31 - 1:
        # Scala take() accepts Int; reserve one value for the truncation probe.
        raise argparse.ArgumentTypeError("must be between 1 and 2147483646 (Scala Int probe limit)")
    return number


def positive_seconds(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be finite and positive")
    return number


def shard_value(value: str) -> tuple[int, int]:
    """`I/N` (0 <= I < N, N >= 2): slice the 0-based positions p with p % N == I, or, with
    --claim-db, be owner I/N of the shared queue."""
    match = re.fullmatch(r"([0-9]+)/([0-9]+)", value)
    if not match or not 0 <= int(match[1]) < int(match[2]) or int(match[2]) < 2:
        raise argparse.ArgumentTypeError("must be I/N with 0 <= I < N and N >= 2")
    return int(match[1]), int(match[2])


def threshold_value(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return number


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--repo", required=True, type=Path)
    result.add_argument("--out", required=True, type=Path)
    # Optional compile_commands.json. When given, c2cpg builds a compile-aware graph from the
    # compiled units only; when absent, every source file is imported (unchanged behaviour).
    result.add_argument("--compile-commands", type=Path)
    # c2cpg `--include <--repo>`: project headers reached through `<...>` or from another directory
    # resolve instead of being skipped. It changes the graph (more METHODs), so it is part of the
    # run contract (settings.import_include_root). Not with --compile-commands (not measured).
    result.add_argument(
        "--import-include-root", action="store_true", help="give c2cpg --repo itself as a header "
        "include path (`--include <repo>`) so code behind header-config guards and header-only "
        "C++ libraries enter the CPG. Changes the CPG and so the candidates; cannot be combined "
        "with --compile-commands",
    )
    result.add_argument("--joern-endpoint", default="http://localhost:16240")
    result.add_argument("--checkpoint", type=Path)
    result.add_argument("--resume", action="store_true")
    result.add_argument("--export-only", action="store_true")
    # #368: build the CPG and discover candidates once, save the graph, slice nothing. The
    # checkpoint it leaves is the seed every shard starts from (copy it, then --resume --cpg).
    result.add_argument("--prepare-only", action="store_true")
    result.add_argument("--cpg-out", type=Path, help="with --prepare-only: where to save the CPG")
    result.add_argument(
        "--cpg", type=Path, help="load this saved CPG instead of parsing --repo (needs --resume "
        "on a copy of the --prepare-only checkpoint)",
    )
    # #377 (b): order only (unless --priority-case-a, below). The same candidates are discovered,
    # and a candidate that gets a full slice gets the same one. Which candidates end up degraded
    # is order-dependent: those not reached when the run is cut short (tools/slice_sharded.py
    # --wall-seconds, a kill), and those after a poison streak (POISON_STOP_STREAK in
    # slicer_pipeline).
    result.add_argument(
        "--priority-files", type=Path, help="file of paths (repo-relative or absolute under "
        "--repo), one per line; blank lines and lines starting with # are skipped, no inline "
        "comments: candidates whose entry or an anchor is in one are sliced first -- within "
        "their case under the default --case-order ba (a listed Case A still runs after every "
        "Case B), ahead of all unlisted candidates under ab",
    )
    # Unlike --priority-files alone this is not order only: it changes which Case A candidates
    # exist, so it is part of the run contract (settings.priority_case_a).
    result.add_argument(
        "--priority-case-a", action="store_true", help="with --priority-files: the list also "
        "steers Case A discovery -- a method defined in a listed file is ranked ahead of the "
        "rest before CASE_A_CANDIDATE_CAP and skips CASE_A_CALL_LIMIT (it still needs a body and "
        "a parameter). Changes WHICH Case A candidates exist, not only their order",
    )
    # Which case is sliced first; the default is `ba`, Case B first. The candidate set and every
    # candidate_key stay the same, but ids are numbered after the sort: ids, slices[] order,
    # `absorbed` and the export file names move with the order. What a cut-short run
    # (tools/slice_sharded.py --wall-seconds, a kill) or a poison streak leaves degraded depends on
    # it too. With --priority-files, `ba` splits the cases first and `ab` the tiers first
    # (slicer_candidates.prioritize). Bound into the run contract (settings);
    # docs/slicer-README.md has the rest, including clustering with 10,000 or more candidates.
    result.add_argument(
        "--case-order", choices=sorted(CASE_RANK), default=DEFAULT_CASE_ORDER,
        help="ba (default): Case B candidates are sliced before Case A; ab: Case A before Case B, "
        "the order before ba became the default. With --priority-files, ba is case-first "
        "(listed-file B, the rest's B, listed-file A, the rest's A), so no Case A runs before a "
        "Case B, and ab is tier-first (listed-file A, listed-file B, then the rest's A, B). A run "
        "cut short (tools/slice_sharded.py --wall-seconds, a kill) or stopped by a poison streak "
        "leaves the candidates it did not reach degraded, so this decides which case that is. "
        "Ids are numbered after the sort, so they differ per order. Shards and --resume must "
        "pass the same value",
    )
    result.add_argument("--export-dir", type=Path)
    result.add_argument("--progress", type=Path)
    result.add_argument("--heartbeat-seconds", type=positive_seconds, default=30)
    result.add_argument("--candidate-timeout-seconds", type=positive_seconds)
    result.add_argument("--backward-depth", type=positive_int)
    result.add_argument("--backward-nodes", type=positive_int)
    result.add_argument("--threshold", type=threshold_value, default=0.95)
    result.add_argument("--max-flows-per-candidate", type=positive_int, default=None)
    result.add_argument("--query-timeout-seconds", type=positive_seconds, default=None)
    # Per-candidate queries only read the CPG, so N processes on N Joern servers can split one
    # repo's candidates. Each shard builds its own CPG -- unless it loads one saved graph with
    # --cpg (#368) -- so a shard's result equals an unsharded run's only as far as Joern itself
    # is deterministic (docs/slicer-README.md). Each shard
    # checkpoints its share; tools/merge_slicer_shards.py joins the checkpoints (refusing shards
    # of a different run) and --export-only clusters.
    result.add_argument(
        "--shard",
        type=shard_value,
        help="I/N: slice candidates at positions p %% N == I (with --claim-db: whatever the "
        "shared queue hands out); needs --checkpoint",
    )
    # #369: N shards pull from one queue instead of a fixed share. Fresh file per run, on a
    # host-local filesystem; a shard that died is recovered by --resume with the same I/N.
    result.add_argument(
        "--claim-db", type=Path, help="with --shard and --checkpoint: take candidates from this "
        "shared queue (SQLite, one fresh file per run for all shards, host-local filesystem) "
        "instead of the fixed p %% N split; a died shard's claims come back only by --resume "
        "with the same I/N",
    )
    # tools/slice_sharded.py --restart-on-deadline: a shard restarted after a query hit its deadline
    # must not re-attempt that candidate -- its claim comes back with --resume and the checkpoint
    # holds nothing for it -- so the restarted process is told to leave it. Which candidates run,
    # not how: not part of the run contract (same as --shard).
    result.add_argument(
        "--skip-candidates", type=Path, help="file of candidate keys, one per line (blank lines "
        "and lines starting with # are skipped): this process does not slice them -- one already "
        "complete in the checkpoint is reused, any other gets the anchor-only degraded slice and "
        "stays unsaved, so a later --resume slices it",
    )
    for option in ("--max-call-depth", "--max-args-to-allow", "--max-output-args-expansion"):
        result.add_argument(
            option,
            type=positive_int,
            default=None,
            help="Override Joern EngineConfig; omitted uses its measured default",
        )
    result.add_argument(
        "--joern-version-command",
        type=Path,
        help="Optional executable from the server installation; invokes --version",
    )
    return result


def read_version(command: Path | None, timeout: float | None) -> tuple[str | None, str]:
    version = None
    note = "HTTP API does not expose joern --version; no --joern-version-command supplied"
    if command:
        try:
            process = subprocess.run(
                [str(command), "--version"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True,
                timeout=timeout,
            )
            output = (process.stdout + process.stderr).strip()
            # Some Joern launchers reject --version but print a version banner before
            # exiting on stdin EOF. Keep timestamped terminal warnings out of the identity.
            matches = re.findall(
                r"(?im)^\s*(?:Version:\s*|joern\s+)?(\d+\.\d+\.\d+(?:[-+][\w.-]+)?)\s*$",
                ANSI.sub("", output),
            )
            versions = set(matches)
            version = versions.pop() if len(versions) == 1 else None
            note = output or "joern --version returned empty output"
            if version is None:
                note = "No unambiguous Joern version in command output: " + note
        except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
            note = f"joern --version unavailable: {exc}"
    return version, note


def read_priority_files(path: Path, repo: Path) -> set[str]:
    """The --priority-files list, spelled as collect() spells candidate files (#377 (b)).

    A line is one path; blank lines and lines starting with `#` are skipped. There are no inline
    comments: `#` is legal in a file name, so `a.c  # note` is the path `a.c  # note`. Each path
    goes through relative_file() as collect() sends entry/anchor files, so an absolute path under
    --repo, `./a.c`, `x//y.c` and backslashes match. utf-8-sig: a BOM must not stick to line 1.
    """
    # collect() maps against the resolved repo (load_sources); the unresolved spelling is tried
    # too so a path written under a symlinked --repo still maps. The fallback is collect()'s.
    roots = (repo.resolve(), repo.absolute())
    names = set()
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        value = line.strip()
        if value and not value.startswith("#"):
            names.add(relative_file(value, roots[0]) or relative_file(value, roots[1]) or value)
    return names


def read_skip_keys(path: Path) -> frozenset[str]:
    """The --skip-candidates list: a candidate key per line, blank lines and `#` lines skipped
    (utf-8-sig: a BOM must not stick to line 1)."""
    lines = (line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines())
    return frozenset(line for line in lines if line and not line.startswith("#"))


def write_result(path: Path, result: dict) -> None:
    """Publish only a complete artifact; failed writes must not destroy an earlier run."""
    encoded = (json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".slices-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    cli = parser()
    args = cli.parse_args(argv)
    if (args.resume or args.export_only) and not args.checkpoint:
        cli.error("--resume/--export-only requires --checkpoint")
    if args.shard and (args.export_only or not args.checkpoint):
        # A shard's own slices.json is a fraction of the run; only the merged checkpoint's
        # --export-only is the result, so a shard without a checkpoint would be lost work.
        cli.error("--shard requires --checkpoint and cannot be combined with --export-only")
    if args.prepare_only and (
        not args.checkpoint or not args.cpg_out or args.resume or args.export_only or args.shard
        or args.cpg
    ):
        cli.error("--prepare-only needs --checkpoint and --cpg-out, and no --resume, "
                  "--export-only, --shard or --cpg")
    if args.claim_db and (not args.shard or not args.checkpoint):
        cli.error("--claim-db needs --shard and --checkpoint")
    if args.cpg_out and not args.prepare_only:
        cli.error("--cpg-out only goes with --prepare-only")
    if args.priority_files and args.export_only:
        # Export reads the order the checkpoint was bound with; a list here would change nothing.
        cli.error("--priority-files has no effect with --export-only")
    if args.skip_candidates and (args.export_only or args.prepare_only):
        cli.error("--skip-candidates has no effect with --export-only or --prepare-only")
    skip = frozenset()
    if args.skip_candidates:
        try:
            skip = read_skip_keys(args.skip_candidates)
        except (OSError, UnicodeError) as exc:
            cli.error(f"--skip-candidates unreadable: {exc}")
    if args.priority_case_a and not args.priority_files:
        cli.error("--priority-case-a needs --priority-files")
    if args.import_include_root and args.compile_commands:
        # The database carries each file's real include paths, and the pair was not measured.
        cli.error("--import-include-root cannot be combined with --compile-commands")
    priority = None
    if args.priority_files:
        try:
            priority = read_priority_files(args.priority_files, args.repo)
        except (OSError, UnicodeError) as exc:
            cli.error(f"--priority-files unreadable: {exc}")
        if args.priority_case_a and not priority:
            # A list of notes and blank lines: discovery would be the default one while the result
            # says it was steered (settings.priority_case_a is true).
            cli.error("--priority-case-a needs a --priority-files that names a file")
    if args.cpg and (not args.checkpoint or not args.resume or args.export_only):
        # The shard must start from the prepared checkpoint: its discovery is what makes every
        # shard slice the same candidate list, and the run contract checks it is the same repo.
        cli.error("--cpg needs --checkpoint (a copy of the --prepare-only checkpoint) and --resume")
    if args.prepare_only and args.checkpoint.exists():
        # A failed prepare is redone from scratch, not resumed (--resume is refused above).
        cli.error("--prepare-only needs a new --checkpoint; it exists")
    if args.cpg_out and args.cpg_out.exists():
        # Running shards may be reading it; a re-prepare must not replace it under them.
        cli.error("--cpg-out exists; give a new path (shards may be reading the old CPG)")
    if args.cpg and not args.cpg.is_file():
        cli.error(f"--cpg is not a file: {args.cpg}")
    outputs = [
        p.resolve()
        for p in (args.out, args.checkpoint, args.progress, args.cpg_out, args.cpg, args.claim_db)
        if p
    ]
    if len(outputs) != len(set(outputs)):
        cli.error("Output, checkpoint, progress, CPG and claim-db paths must be distinct")
    if args.export_dir:
        # export_sources() os.replace()s everything under this directory. Run state left
        # there is replaced by an export artifact -- an open checkpoint becomes JSON and
        # the resume state is gone, silently. Reserving the subtree rather than a list of
        # basenames keeps the guard true when the exporter gains a file.
        reserved = args.export_dir.resolve()
        if any(path == reserved or reserved in path.parents for path in outputs):
            cli.error("Output, checkpoint, progress and CPG paths must stay out of --export-dir")
        if args.export_dir.exists() and not args.export_dir.is_dir():
            # export_sources() only mkdirs on its first write, so a regular file here
            # surfaces after the run: --out already holds a correct result and main()
            # still returns 1. Fail on the typo before paying for Joern.
            cli.error("--export-dir exists and is not a directory")
    # File-transport payloads (FILE_TRANSPORT_STAGES) land here: a sibling of the run outputs (--out
    # is required), so it shares their writable filesystem and stays out of --export-dir's
    # replaced subtree. Resolve so the path handed to Joern is absolute -- Joern's JVM resolves
    # a relative path against its own working directory, not this process's.
    payload_dir = (args.out.parent / ".slicer-payloads").resolve()
    client = JoernClient(
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
    job = Slicer(args.repo, args.threshold, args.max_flows_per_candidate, client, limits)
    # Hash the database content, not just its path: it steers the CPG the same way sources do,
    # so a same-path rebuild with different build flags must change the run signature (the path
    # string alone would collide). None when absent or unreadable; build_cpg then reports the
    # unreadable case and falls back.
    compile_commands_sha256 = None
    if args.compile_commands:
        try:
            compile_commands_sha256 = hashlib.sha256(
                args.compile_commands.read_bytes()
            ).hexdigest()
        except OSError:
            compile_commands_sha256 = None
    job.settings = {
        "threshold": args.threshold,
        "max_flows_per_candidate": args.max_flows_per_candidate,
        "query_timeout_seconds": args.query_timeout_seconds,
        "engine_overrides": limits,
        "backward_depth": args.backward_depth,
        "backward_nodes": args.backward_nodes,
        "candidate_timeout_seconds": args.candidate_timeout_seconds,
        "compile_commands": args.compile_commands.as_posix() if args.compile_commands else None,
        "compile_commands_sha256": compile_commands_sha256,
        # The generated_headers dirs folded into the include set are an implicit input that steers
        # the CPG (their presence changes which config-macro'd methods parse) yet leaves
        # compile_commands_sha256 unchanged, so record them here to keep the run signature honest
        # (review M2). Hashing the external headers' content stays out of scope, as it was for the
        # command's own out-of-repo -I paths before this (#291).
        "generated_header_dirs": (
            generated_header_dirs(args.compile_commands) if args.compile_commands else None
        ),
        # The order is bound into the checkpoint, so it is part of the contract: shards or a
        # resume with another list are refused instead of slicing a differently ordered run.
        "priority_files": (
            {"count": len(priority), "sha256": digest(sorted(priority))}
            if priority is not None
            else None
        ),
        # Same reason: collect() numbers and orders the candidates by it, and that order is bound
        # into the checkpoint, so a shard or a resume with another order is refused.
        "case_order": args.case_order,
        # Also contract: with a list it changes which Case A candidates exist (ranked-first and
        # exempt from the call limit), so a shard or a resume without it is refused.
        "priority_case_a": args.priority_case_a,
        # Steers the CPG itself (c2cpg's include paths), like compile_commands above, so it is
        # contract: a shard or a resume built from a differently imported graph is refused.
        "import_include_root": args.import_include_root,
    }
    job.priority_files = priority
    job.skip_keys = skip
    job.priority_case_a = args.priority_case_a
    job.import_include_root = args.import_include_root
    job.result["settings"] = job.settings
    # The normalized database lands in payload_dir, which is already on the filesystem Joern
    # shares for file-transport payloads.
    job.compile_commands = args.compile_commands.resolve() if args.compile_commands else None
    job.compile_db_dir = payload_dir
    job.code_hashes = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path(__file__).parent.glob("slicer*.py"))
    }
    job.export_only = args.export_only
    # Not in settings: the shard picks WHICH candidates run, not how, so every shard shares the
    # run contract and their checkpoints can be merged.
    job.shard = args.shard
    job.prepare_only = args.prepare_only
    job.cpg_out = args.cpg_out.resolve() if args.cpg_out else None
    job.cpg_file = args.cpg.resolve() if args.cpg else None
    job.heartbeat_seconds = args.heartbeat_seconds
    attempt = None
    try:
        # Inside the try so a non-writable output parent surfaces as a clean result error,
        # not a bare traceback (same reasoning as the progress dir below).
        payload_dir.mkdir(parents=True, exist_ok=True)
        if args.progress:
            args.progress.parent.mkdir(parents=True, exist_ok=True)
            job.progress = args.progress.open("a", encoding="utf-8", newline="\n")
        if args.checkpoint:
            job.checkpoint = Checkpoint(args.checkpoint, resume=args.resume or args.export_only)
        if args.claim_db:
            # Inside the try: a missing parent is created, and a stale/foreign file or a mount
            # without WAL becomes a clean result error instead of a traceback.
            job.claims = ClaimQueue(args.claim_db, *args.shard)
        if args.export_only:
            contract = job.checkpoint.get("run_contract")
            version = contract["joern_version"] if contract else None
            note = "Export-only: recorded Joern version"
            if contract:
                job.settings = contract["settings"]
                job.result["settings"] = job.settings
        else:
            version, note = read_version(args.joern_version_command, args.query_timeout_seconds)
        if job.checkpoint and not args.export_only and version is None:
            raise ValueError("Checkpoints require a measured --joern-version-command")
        job.result["joern_version"] = version
        if args.joern_version_command and not args.export_only:
            job.result["diagnostics"].append({"stage": "version_command", "output": note})
        if version is None:
            job.result["measurement_notes"]["joern_version"] = note
        if job.checkpoint:
            attempt = job.checkpoint.begin(
                {
                    "settings": job.settings,
                    "client_pid": os.getpid(),
                    "endpoint": args.joern_endpoint,
                }
            )
        result = job.run()
        if job.checkpoint:
            job.checkpoint.end(
                attempt,
                {
                    "status": result["status"],
                    "error": result.get("error"),
                    "counters": result["counters"],
                    "diagnostics": result["diagnostics"],
                    "server": result.get("server"),
                },
            )
            result["attempts"] = job.checkpoint.attempts()
        write_result(args.out, result)
        if args.export_dir:
            export_sources(args.export_dir, result, job.sources)
        return {"ok": 0, "partial": 2, "failed": 1}[result["status"]]
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Cannot write or prepare slicer result: {exc}", file=sys.stderr)
        if not job.cpg_ready:
            job.result["error"] = str(exc)
            try:
                write_result(args.out, job.result)
            except (OSError, ValueError):
                pass  # Destination errors have already been printed above.
        return 1
    finally:
        if job.checkpoint:
            job.checkpoint.close()
        if job.claims is not None:
            job.claims.close()
        if job.progress:
            job.progress.close()


if __name__ == "__main__":
    raise SystemExit(main())
