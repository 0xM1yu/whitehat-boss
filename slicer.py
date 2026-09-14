#!/usr/bin/env python3
"""Offline, model-free Joern C/C++ slicer (Python 3.11+, standard library only).

The JSON artifact is also the failure record. Unobserved measurements stay null;
an empty successful query must carry a valid payload, never an empty REPL response.
"""

import argparse
import base64
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
from slicer_checkpoint import Checkpoint
from slicer_export import export_sources
from slicer_pipeline import SlicingStages
from slicer_source import (
    SOURCE_SUFFIXES,
    relative_file,
)

ANSI = re.compile(r"\x1b\[[0-9;]*m")
ERROR = re.compile(
    r"StackOverflowError|OutOfMemoryError|\b[\w.]+Exception\b|\b(?:\w+\.)+\w+Error\b|"
    r"(?im:^\s*(?:--.*(?:Error|error)|error\s*:|error\b.*(?:not found|value)|"
    r"\d+ errors? found|.*\bCompilation (?:failed|error)|.*\[ERROR\]))"
)
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
        if valid and stage == "case_b":
            valid = all(
                isinstance(row.get("selector"), dict)
                and type(row["selector"].get("line")) is int
                and type(row["selector"].get("column")) is int
                and isinstance(row["selector"].get("code"), str)
                and isinstance(row["selector"].get("ast_path"), list)
                and all(type(n) is int and n >= 0 for n in row["selector"]["ast_path"])
                for row in data
            )
    elif stage == "context":
        valid = isinstance(data, list) and all(valid_location(row) for row in data)
    elif stage == "import":
        valid = isinstance(data, dict) and data.get("imported") is True
    elif stage == "overlay":
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
    failure = ERROR.search(diagnostics)
    success = response.get("success")
    if not (success is True or success == "true") or failure:
        raise QueryError(
            diagnostics.strip() or "Joern returned success=false",
            logs,
            poisoned="StackOverflowError" in diagnostics or "OutOfMemoryError" in diagnostics,
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
            "files_read_failed",
            "directories_skipped",
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
        try:
            budget = self.settings.get("candidate_timeout_seconds")
            if budget is not None and self.candidate_started is not None:
                left = budget - (time.perf_counter() - self.candidate_started)
                if left <= 0:
                    raise QueryError("Candidate deadline exceeded")
                self.client.timeout = min(left, original_timeout) if original_timeout else left
            data, logs = self.client.query(stage, query)
            validate_payload(stage, data, logs)
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
            self.event("query_end", stage=stage, elapsed_seconds=time.perf_counter() - started)

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
                # Decode strictly: replacement characters would silently change evidence.
                raw = path.read_bytes()
                self.source_hashes[name] = hashlib.sha256(raw).hexdigest()
                text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
                # splitlines also splits form feeds inside C source and shifts lineNumber.
                self.sources[name] = text.removesuffix("\n").split("\n") if text else []
            except (OSError, UnicodeError, ValueError) as exc:
                self.increment("files_read_failed")
                self.errors.append(f"Source read failed: {name}: {exc}")

    def parse_report(self, logs: str) -> None:
        rows = {}
        for line in ANSI.sub("", logs).splitlines():
            parts = [part.strip() for part in line.split("|")]
            if len(parts) == 8 and parts[1].isdigit() and parts[4] in ("yes", "no"):
                name = relative_file(parts[2], self.repo)
                if parts[5] in ("yes", "no") and name is not None:
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
    result.add_argument("--checkpoint", type=Path)
    result.add_argument("--resume", action="store_true")
    result.add_argument("--export-only", action="store_true")
    result.add_argument("--export-dir", type=Path)
    result.add_argument("--progress", type=Path)
    result.add_argument("--heartbeat-seconds", type=positive_seconds, default=30)
    result.add_argument("--candidate-timeout-seconds", type=positive_seconds)
    result.add_argument("--backward-depth", type=positive_int)
    result.add_argument("--backward-nodes", type=positive_int)
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
    outputs = [p.resolve() for p in (args.out, args.checkpoint, args.progress) if p]
    if len(outputs) != len(set(outputs)):
        cli.error("Output, checkpoint and progress paths must be distinct")
    client = JoernClient(args.joern_endpoint, args.query_timeout_seconds)
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
    job.settings = {
        "threshold": args.threshold,
        "max_flows_per_candidate": args.max_flows_per_candidate,
        "query_timeout_seconds": args.query_timeout_seconds,
        "engine_overrides": limits,
        "backward_depth": args.backward_depth,
        "backward_nodes": args.backward_nodes,
        "candidate_timeout_seconds": args.candidate_timeout_seconds,
    }
    job.result["settings"] = job.settings
    job.code_hashes = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path(__file__).parent.glob("slicer*.py"))
    }
    job.export_only = args.export_only
    job.heartbeat_seconds = args.heartbeat_seconds
    attempt = None
    try:
        if args.progress:
            args.progress.parent.mkdir(parents=True, exist_ok=True)
            job.progress = args.progress.open("a", encoding="utf-8", newline="\n")
        if args.checkpoint:
            job.checkpoint = Checkpoint(args.checkpoint, resume=args.resume or args.export_only)
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
        if job.progress:
            job.progress.close()


if __name__ == "__main__":
    raise SystemExit(main())
