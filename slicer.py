#!/usr/bin/env python3
"""Offline, model-free Joern C/C++ slicer (Python 3.11+, standard library only).

The JSON artifact is also the failure record. Unobserved measurements stay null;
an empty successful query must carry a valid payload, never an empty REPL response.
"""

import argparse
import base64
import json
import math
import os
import re
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
from slicer_source import (
    SOURCE_SUFFIXES,
    assemble,
    cluster,
    normalize_points,
    recover_points,
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


def _read_no_follow(path: Path) -> str:
    """심링크를 **open 시점에** 거부하고 읽는다.

    호출부가 바로 앞에서 `resolve().relative_to(repo)` 로 저장소 밖 타깃을 막지만, 그 검사와
    `read_text()` 는 **서로 다른 두 번의 조회**다. 그 사이에 링크가 바뀌면 검사한 것과 읽는
    것이 달라진다(TOCTOU). 이 도구는 **감사 대상이 쓴 저장소**를 읽으므로 그 창을 연 채로
    둘 이유가 없다.

    `O_NOFOLLOW` 가 없는 플랫폼에서는 그냥 읽는다 — 앞선 `resolve()` 검사가 정적 심링크는
    이미 막고 있어, 없는 곳에서 기능을 끄는 것이 아니라 창만 남는다.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    try:
        handle = os.fdopen(descriptor, "r", encoding="utf-8", newline=None)
    except BaseException:
        # fdopen 이 소유권을 가져가기 전에 실패하면 raw fd 가 남는다. 저장소의 파일마다
        # 한 번씩 도는 루프라 계통적 실패에서 누적된다.
        os.close(descriptor)
        raise
    # `newline=None` (universal newlines) is not a detail — it is what `read_text` did, and
    # dropping it left CRLF in `sources`, which shifts every assembled line by a stray CR.
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
    "candidates_not_processed",
    "engine_flows_truncated",
    "directories_skipped",
    "flow_sinks_without_method",
)
LIMITATIONS = [
    "single-function stack overflow not covered",
    "free() aliasing not resolved",
    "cross-function double free not covered",
    "fuzzy parsing and missing call edges can inflate source-first candidates",
    "a CPG file node does not prove successful or complete parsing",
    "flow sources are enclosing-method parameters; parameterless readers may yield zero flows",
    "Joern engine-internal limits are not measured by flows_truncated",
    "flow cap stops further method batches, but does not bound engine work within one batch",
    "structure recovery is lexical and does not guarantee compilable preprocessed C/C++",
]


class QueryError(Exception):
    def __init__(self, message: str, logs: str = "", *, poisoned: bool = False):
        super().__init__(message)
        self.logs = logs
        self.poisoned = poisoned


def valid_location(value: object) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("file"), str)
        and type(value.get("line")) is int
    )


def valid_candidate(value: object) -> bool:
    if not valid_location(value) or not isinstance(value.get("symbol"), str) or not value["symbol"]:
        return False
    scope = value.get("scope")
    return (
        valid_location(scope)
        and isinstance(scope.get("full_name"), str)
        and isinstance(value.get("anchors"), list)
        and all(valid_location(point) for point in value["anchors"])
    )


def validate_payload(stage: str, data: object, logs: str) -> None:
    """Validate before counters or Scala selectors consume untrusted HTTP fields."""
    valid = False
    if stage in ("case_a", "case_b"):
        valid = isinstance(data, list) and all(valid_candidate(row) for row in data)
    elif stage == "context":
        valid = isinstance(data, list) and all(valid_location(row) for row in data)
    elif stage == "import":
        valid = isinstance(data, dict) and data.get("imported") is True
    elif stage == "overlay":
        valid = data is True
    elif stage == "flows" and isinstance(data, dict):
        valid = (
            type(data.get("flow_count")) is int
            and data["flow_count"] >= 0
            and type(data.get("truncated")) is bool
            and isinstance(data.get("lines"), list)
            and all(valid_location(row) for row in data["lines"])
            and (data["flow_count"] > 0 or not data["lines"])
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
                    "methods_internal", "calls", "calls_unresolved", "unknown_nodes",
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


def decode_response(response: dict, stage: str) -> tuple[object, str]:
    if not isinstance(response, dict):
        raise QueryError("HTTP response is not an object")
    stdout, stderr = response.get("stdout", ""), response.get("stderr", "")
    if not isinstance(stdout, str) or not isinstance(stderr, str):
        raise QueryError("stdout/stderr must be strings")
    logs = ANSI.sub("", stdout + "\n" + stderr)
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
    try:
        return json.loads(base64.b64decode(match[1], validate=True).decode("utf-8")), logs
    except (ValueError, UnicodeError) as exc:
        raise QueryError(f"Invalid query payload: {exc}", logs) from exc


class JoernClient:
    def __init__(self, endpoint: str, timeout: float | None):
        self.url = endpoint.rstrip("/") + "/query-sync"
        self.timeout = timeout

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
        return decode_response(decoded, stage)


def initial_result(repo: Path, threshold: float) -> dict:
    return {
        "slice_version": "slice-v1",
        "repo": repo.name,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "joern_version": None,
        "threshold": threshold,
        "status": "failed",
        "slices": [],
        "counters": dict.fromkeys(COUNTERS),
        "measurement_notes": {key: "Stage not reached" for key in COUNTERS},
        "known_limitations": LIMITATIONS.copy(),
        "diagnostics": [],
    }


class Slicer:
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
        for key in (
            "queries_aborted",
            "flows_truncated",
            "locations_unmapped",
            "candidates_unmapped",
            "files_read_failed",
            "directories_skipped",
        ):
            self.measure(key, 0)

    def measure(self, key: str, value: int | float) -> None:
        self.result["counters"][key] = value
        self.result["measurement_notes"].pop(key, None)

    def increment(self, key: str, value: int = 1) -> None:
        self.measure(key, self.result["counters"][key] + value)

    def query(self, stage: str, query: str) -> tuple[object, str]:
        try:
            data, logs = self.client.query(stage, query)
            validate_payload(stage, data, logs)
            return data, logs
        except QueryError as exc:
            self.increment("queries_aborted")
            self.result["diagnostics"].append(
                {"stage": stage, "error": str(exc), "logs": exc.logs, "stop": True}
            )
            if exc.poisoned:
                self.errors.append(
                    "Joern state is uncertain; stop and restart the dedicated server"
                )
            if "StackOverflowError" in str(exc):
                self.result["status"] = "partial"
            raise

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
                        f"Directory symlink not traversed: {path.relative_to(self.repo)}"
                    )
            for name in sorted(files):
                path = Path(directory) / name
                if path.suffix.lower() in SOURCE_SUFFIXES:
                    paths.append(path)
        self.measure("files_seen", len(paths))
        for path in sorted(paths):
            name = path.relative_to(self.repo).as_posix()
            try:
                path.resolve().relative_to(self.repo)
                # Reject the symlink at open() time, not before it: resolve() and a later
                # open() are two lookups, and this tool reads repositories written by
                # whoever we are auditing. O_NOFOLLOW closes that window.
                # Decode strictly: replacement characters would silently change evidence.
                text = _read_no_follow(path)
                # splitlines also splits form feeds inside C source and shifts lineNumber.
                self.sources[name] = text.removesuffix("\n").split("\n") if text else []
            except (OSError, UnicodeError, ValueError) as exc:
                self.increment("files_read_failed")
                self.errors.append(f"Source read failed: {name}: {exc}")

    def parse_report(self, logs: str) -> None:
        rows = {}
        for line in ANSI.sub("", logs).splitlines():
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
        try:
            # A unique project prevents reuse of a stale graph from an earlier batch.
            payload, logs = self.query(
                "import", queries.import_query(self.repo.as_posix(), "slicer_" + uuid.uuid4().hex)
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
        missing = sorted(set(self.sources) - files)
        self.measure("files_missing_from_cpg", len(missing))
        if missing:
            self.errors.append("Source files missing from CPG: " + ", ".join(missing))
        for key in (
            "methods_internal", "calls", "calls_unresolved", "unknown_nodes",
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
        self.result["measurement_notes"].pop("joern_engine_config")
        if not {"ossdataflow", "dataflowOss"}.intersection(data.get("overlays", [])):
            self.query("overlay", queries.envelope("overlay", "run.ossdataflow; ujson.Bool(true)"))

    def collect_candidates(self) -> None:
        combined = {}
        for case, make_query in (
            ("A", queries.candidates_a_query),
            ("B", lambda: queries.candidates_b_query(self.engine_limits)),
        ):
            data, _ = self.query("case_" + case.lower(), make_query())
            if not isinstance(data, list):
                raise ValueError(f"Case {case} result is not an array")
            keys = set()
            for row in data:
                file = relative_file(row["file"], self.repo)
                line, symbol = row["line"], row["symbol"]
                if type(line) is not int or not isinstance(symbol, str) or not symbol:
                    raise ValueError("Invalid candidate entry")
                key = (case, file or row["file"], line, symbol)
                keys.add(key)
                candidate = combined.setdefault(
                    key,
                    {
                        "case": case,
                        "scopes": [],
                        "anchors": [],
                        "entry": {"symbol": symbol, "file": key[1], "line": line},
                    },
                )
                if row["scope"] not in candidate["scopes"]:
                    candidate["scopes"].append(row["scope"])
                candidate["anchors"].extend(row["anchors"])
            self.measure("candidates_case_" + case.lower(), len(keys))
        self.candidates = [combined[key] for key in sorted(combined)]
        for index, candidate in enumerate(self.candidates, 1):
            candidate["id"] = f"slice_{index:04d}"
            candidate["scopes"].sort(key=lambda s: (s["file"], s["line"], s["full_name"]))

    def points(self, raw: list[dict]) -> set[tuple[str, int]]:
        points, invalid = normalize_points(raw, self.sources, self.repo)
        self.increment("locations_unmapped", invalid)
        if invalid:
            self.errors.append(f"Could not restore {invalid} reported source locations")
        return points

    def slice_candidate(self, candidate: dict) -> dict | None:
        entry = candidate["entry"]
        if not self.points([entry]):
            self.increment("candidates_unmapped")
            return None
        points = self.points(candidate["anchors"]) | {(entry["file"], entry["line"])}
        payload, _ = self.query(
            "flows", queries.flows_query(candidate["scopes"], self.max_flows, self.engine_limits)
        )
        if (
            not isinstance(payload, dict)
            or type(payload.get("flow_count")) is not int
            or payload["flow_count"] < 0
            or type(payload.get("truncated")) is not bool
            or not isinstance(payload.get("lines"), list)
        ):
            raise ValueError("Invalid flow result")
        if payload["truncated"]:
            self.increment("flows_truncated")
            self.errors.append(f"Flow cap reached: {candidate['id']}")
        points |= self.points(payload["lines"])
        # Structural expansion introduces identifiers in conditions/returns; repeat
        # supplementation to a fixed point so those declarations are not omitted.
        while True:
            old = points.copy()
            data, _ = self.query(
                "context", queries.context_query(sorted(points), self.repo.as_posix())
            )
            if not isinstance(data, list):
                raise ValueError("Context result is not an array")
            points |= self.points(data)
            points = recover_points(points, self.sources)
            if old == points:
                break
        return {
            "id": candidate["id"],
            "case": candidate["case"],
            "entry": entry,
            "lines": [{"file": f, "line": n} for f, n in sorted(points)],
            "code": assemble(points, self.sources),
            "absorbed": [],
            "flow_count": payload["flow_count"],
            "truncated": payload["truncated"],
        }

    def run(self) -> dict:
        started = time.perf_counter()
        try:
            self.load_sources()
            self.build_cpg()
            self.inventory()
            self.collect_candidates()
            for candidate in self.candidates:
                item = self.slice_candidate(candidate)
                self.processed += 1
                if item is not None:
                    self.completed.append(item)
        except Exception as exc:
            self.errors.append(f"{type(exc).__name__}: {exc}")
        except KeyboardInterrupt:
            self.errors.append("Interrupted by user; server execution may continue")
        finally:
            self.finish(started)
        return self.result

    def finish(self, started: float) -> None:
        if self.cpg_ready:
            self.result["status"] = "partial" if self.errors else "ok"
            self.measure("slices_before_clustering", len(self.completed))
            self.result["slices"] = cluster(self.completed, self.result["threshold"], self.sources)
            self.measure("slices_after_clustering", len(self.result["slices"]))
            if self.result["counters"]["candidates_case_b"] is not None:
                self.measure("candidates_not_processed", len(self.candidates) - self.processed)
        if self.errors:
            self.result["error"] = "; ".join(dict.fromkeys(self.errors))
        self.measure("total_seconds", time.perf_counter() - started)


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


def threshold_value(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return number


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--repo", required=True, type=Path)
    result.add_argument("--out", required=True, type=Path)
    result.add_argument("--joern-endpoint", default="http://localhost:16240")
    result.add_argument("--threshold", type=threshold_value, default=0.95)
    result.add_argument("--max-flows-per-candidate", type=positive_int, default=None)
    result.add_argument("--query-timeout-seconds", type=positive_seconds, default=None)
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
            version = (process.stdout + process.stderr).strip() or None
            note = "joern --version returned empty output"
        except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
            note = f"joern --version unavailable: {exc}"
    return version, note


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
    args = parser().parse_args(argv)
    started = time.perf_counter()
    client = JoernClient(args.joern_endpoint, args.query_timeout_seconds)
    limits = {
        key: value
        for key, value in (
            ("maxCallDepth", args.max_call_depth),
            ("maxArgsToAllow", args.max_args_to_allow),
            ("maxOutputArgsExpansion", args.max_output_args_expansion),
        )
        if value is not None
    }
    job = Slicer(args.repo, args.threshold, args.max_flows_per_candidate, client, limits)
    version, note = read_version(args.joern_version_command, args.query_timeout_seconds)
    result = job.run()
    result["joern_version"] = version
    if version is None:
        result["measurement_notes"]["joern_version"] = note
    result["settings"] = {
        "max_flows_per_candidate": args.max_flows_per_candidate,
        "query_timeout_seconds": args.query_timeout_seconds,
        "engine_overrides": limits,
    }
    job.measure("total_seconds", time.perf_counter() - started)
    try:
        write_result(args.out, result)
    except (OSError, ValueError) as exc:
        # If the destination itself is unwritable, stderr is the only remaining channel.
        print(f"Cannot write {args.out}: {exc}", file=sys.stderr)
        return 1
    return {"ok": 0, "partial": 2, "failed": 1}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
