"""Candidate-stage transitions, reusable checkpoints and result accounting."""

import copy
import hashlib
import sys
import time
from pathlib import Path

import slicer_queries as queries
from slicer_candidates import collect, prioritize
from slicer_checkpoint import digest
from slicer_errors import QueryError
from slicer_library import Library
from slicer_source import (
    assemble,
    cluster,
    normalize_points,
    recover_points,
    source_sink,
)

STAGE_COUNTERS = ("locations_unmapped", "candidates_unmapped")

# A poisoned failure (deadline/HTTP/OOM) may leave the Joern server running the runaway
# query, so the candidates right after it tend to time out too. Isolate each candidate,
# but stop the run after this many *consecutive* poisoned failures.
# NOTE on --resume: a failed candidate is NOT persisted as failed (the failure is before
# save_stage, so no checkpoint row -> restore_stage returns "pending"). So --resume RE-RUNS
# it, it does not skip it. Restarting the dedicated server first lets resume progress when
# the poison was *server state*. When the poison is *candidate-intrinsic* (a query that
# always exceeds the 600s deadline), resume re-burns the timeout on it every time, and a
# streak of >= POISON_STOP_STREAK such candidates never advances past them (documented limit).
POISON_STOP_STREAK = 3

# --priority-files audit (review M-3/M-6): the matched/unmatched names are recorded in the
# result so a reader can tell, after the fact, exactly what the list resolved to -- not just a
# count. Capped so a huge list cannot bloat the result; the counts next to them stay exact.
PRIORITY_AUDIT_CAP = 50


def file_identity(path: Path) -> dict | None:
    """Size and SHA-256 of a file as this client reads it; None when it cannot read it."""
    try:
        sha = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1 << 20), b""):
                sha.update(block)
        return {"bytes": path.stat().st_size, "sha256": sha.hexdigest()}
    except OSError:
        return None


class SlicingStages:
    def selected_cases(self) -> str:
        """--cases: which candidate discovery queries run. Checkpoints written before the flag
        carry no `cases` setting and ran both."""
        return self.settings.get("cases", "AB")

    def collect_candidates(self) -> None:
        rows = {}
        selected = self.selected_cases()
        for case, query in (
            ("A", queries.candidates_a_query),
            ("B", lambda: queries.candidates_b_query(self.engine_limits)),
        ):
            if case not in selected:
                # Not 0: the query never ran, so the count is unknown, not empty.
                self.result["measurement_notes"]["candidates_case_" + case.lower()] = (
                    f"Skipped by --cases {selected}"
                )
                continue
            rows[case], _ = self.query("case_" + case.lower(), query())
            self.measure(
                "candidates_case_" + case.lower(), len(collect({case: rows[case]}, self.repo))
            )
        # HIGH-1 (#241 review): the .take cap in candidates_a_query is silent. When the count
        # reaches the cap, api and/or readers may be truncated, so record a per-run note (measure
        # popped any prior note). Below the cap it cannot have been truncated, so no note.
        case_a = self.result["counters"]["candidates_case_a"]
        if case_a is not None and case_a >= queries.CASE_A_CANDIDATE_CAP:
            self.result["measurement_notes"]["candidates_case_a"] = (
                f"reached the cap ({queries.CASE_A_CANDIDATE_CAP}); api and/or readers may be "
                "truncated to an unspecified first-N in CPG traversal order (api: write-context "
                "methods first, then traversal order within each layer; slicer_queries)"
            )
        order = self.settings.get("case_order", "ab")
        self.candidates = collect(rows, self.repo, order)
        if self.priority_files is not None:
            # Before bind(): the checkpoint (and so --prepare-only's shards and the claim queue)
            # keeps this order, and resuming with a different list is a contract mismatch.
            self.candidates, first, matched = prioritize(
                self.candidates, self.priority_files, order
            )
            unmatched_names = sorted(self.priority_files - matched)
            self.result["priority"] = {
                "files": len(self.priority_files),
                "files_with_candidates": len(matched),
                # The size of the priority tier: how many candidates have their entry or an anchor
                # in a listed file. Under `ab` they are the first `first` candidates; under `ba`
                # the listed Case B lead and the listed Case A follow the rest of Case B, so this
                # is a count, not the length of a leading run.
                "first_tier_candidates": first,
                "candidates": len(self.candidates),
                # Audit trail (review M-3/M-6): the normalized names this list resolved to, so a
                # reader can tell a real match from a silent typo. Counts above stay exact.
                "matched_files": sorted(matched)[:PRIORITY_AUDIT_CAP],
                "unmatched_files": unmatched_names[:PRIORITY_AUDIT_CAP],
            }
            # A list that matches nothing is not an error (the run is still complete), but a
            # misspelled list must not read as applied -- warn on stderr and note it.
            unmatched = len(unmatched_names)
            if not matched:
                print(
                    f"--priority-files: the list ({len(self.priority_files)} files) matched no "
                    "candidate's entry or anchor file; candidates keep the default order",
                    file=sys.stderr,
                )
                self.result["measurement_notes"]["priority_files"] = (
                    f"the list ({len(self.priority_files)} files) matched no candidate's entry or "
                    "anchor file; candidates keep the default order"
                )
            elif unmatched:
                self.result["measurement_notes"]["priority_files"] = (
                    f"{unmatched} of {len(self.priority_files)} listed files matched no "
                    "candidate's entry or anchor file"
                )
        self.result["candidate_manifest"] = {c["candidate_key"]: c for c in self.candidates}
        if self.checkpoint:
            self.checkpoint.bind("candidates", self.candidates)

    def define_library(self) -> None:
        """Compile the per-candidate query logic once for this Joern session (slicer_library)."""
        self.library = Library(
            self.repo.as_posix(),
            self.max_flows,
            self.engine_limits,
            self.settings.get("backward_depth"),
            self.settings.get("backward_nodes"),
            self.result["joern_engine_config"]["maxCallDepth"],
        )
        self.query("library", self.library.definition)

    def points(self, raw: list[dict]) -> set[tuple[str, int]]:
        points, invalid = normalize_points(raw, self.sources, self.repo)
        self.increment("locations_unmapped", invalid)
        if invalid:
            self.errors.append(f"Could not restore {invalid} reported source locations")
        return points

    def save_stage(self, candidate: dict, state: dict, phase: str) -> None:
        state["phase"] = phase
        state["errors"] = self.errors[self.candidate_error_start :]
        state["counters"] = {
            k: self.result["counters"][k] - self.candidate_counter_start[k] for k in STAGE_COUNTERS
        }
        if self.checkpoint:
            self.checkpoint.save(candidate["candidate_key"], state)
        self.event("stage_saved", candidate_key=candidate["candidate_key"], phase=phase)

    def restore_stage(self, candidate: dict) -> dict:
        self.candidate_error_start = len(self.errors)
        self.candidate_counter_start = {k: self.result["counters"][k] for k in STAGE_COUNTERS}
        state = self.checkpoint.load(candidate["candidate_key"]) if self.checkpoint else None
        if state:
            self.errors.extend(state["errors"])
            for key, count in state["counters"].items():
                self.increment(key, count)
            self.increment("candidate_stages_reused")
        return state or {"phase": "pending"}

    def slice_candidate(self, candidate: dict) -> dict | None:
        self.active_candidate = candidate["candidate_key"]
        self.candidate_started = time.perf_counter()
        state = self.restore_stage(candidate)
        if state["phase"] == "complete":
            self.increment("candidates_reused")
            return state["item"]
        entry = candidate["entry"]
        if state["phase"] == "pending":
            if not self.points([entry]):
                self.increment("candidates_unmapped")
                state["item"] = None
                self.save_stage(candidate, state, "complete")
                return None
            points = self.points(candidate["anchors"]) | {(entry["file"], entry["line"])}
            if candidate["case"] == "A":
                query = self.library.flows(candidate["scopes"])
                payload, _ = self.query("flows", query)
                if payload.get("sinks_capped"):
                    # HIGH-1 (#252 review): the global memory-write/command-SQL-exec sink set
                    # hit FLOWS_SINK_CAP,
                    # so the kept subset is an unspecified first-N by source order. Candidate-method
                    # sinks are always kept, so this bounds only cross-function reach, not the
                    # candidate's own function. Record it -- a silent cap would look complete.
                    self.result["diagnostics"].append({
                        "stage": "flows",
                        "candidate": candidate["candidate_key"],
                        "note": f"global memory-write/exec sinks exceeded "
                        f"{queries.FLOWS_SINK_CAP}; kept first-N by source order "
                        "(candidate-method sinks always kept)",
                    })
            else:
                query = self.library.backward(candidate)
                payload, _ = self.query("backward", query)
            dependency_points = self.points(payload["lines"])
            state.update(
                payload=payload,
                points=sorted(points | dependency_points),
                anchor_points=sorted(points),
                dependency_points=sorted(dependency_points),
            )
            self.save_stage(candidate, state, "flow_saved")
        if state["phase"] == "flow_saved":
            self.expand_context(candidate, state)
        points = {tuple(p) for p in state["points"]}
        payload = state["payload"]
        state["item"] = {
            "id": candidate["id"],
            "candidate_key": candidate["candidate_key"],
            "case": candidate["case"],
            "entry": entry,
            "members": [candidate["candidate_key"]],
            "absorbed": [],
            "lines": [{"file": f, "line": n} for f, n in sorted(points)],
            "code": assemble(points, self.sources),
            "flow_count": payload["flow_count"],
            "truncated": payload["truncated"],
            "analysis_kind": "parameter_paths" if candidate["case"] == "A" else "backward_ddg",
            "analysis": payload,
            "measurement_notes": (
                {}
                if candidate["case"] == "A"
                else {"flow_count": "Backward dependency graph; paths were not enumerated"}
            ),
            "source_sink": source_sink(
                candidate["case"], entry, payload, self.sources, self.repo
            ),
            "line_provenance": {
                "data_dependency": state["dependency_points"],
                "candidate_anchor": state["anchor_points"],
                "context": sorted(
                    points - {tuple(p) for p in state["dependency_points"] + state["anchor_points"]}
                ),
            },
        }
        self.save_stage(candidate, state, "complete")
        return state["item"]

    def expand_context(self, candidate: dict, state: dict) -> None:
        points = {tuple(p) for p in state["points"]}
        while True:
            old = points.copy()
            data, _ = self.query(
                "context", self.library.context(sorted(points))
            )
            points |= self.points(data)
            points = recover_points(points, self.sources)
            state["points"] = sorted(points)
            self.save_stage(candidate, state, "context_saved" if old == points else "flow_saved")
            if old == points:
                return

    def degraded_slice(self, candidate: dict) -> dict:
        """Server-free fallback for a candidate whose flow/backward query failed or was skipped
        after a poison streak. The candidate was already captured (that capture is the recall
        unit -- source/sink is labelled), so keep it in the output with its own coordinates and
        the source at them instead of DROPPING it. Builds nothing from the CPG, so it works even
        when the server is unresponsive. `degraded` marks that the dataflow context is absent:
        a consumer sees the anchor, not a traced slice, and can `--resume` for the full slice.
        """
        entry = candidate["entry"]
        # Normalize WITHOUT counting (#307 review LOW): in the except-QueryError path
        # slice_candidate already ran self.points on these anchors, folding their unmapped count
        # into locations_unmapped -- re-counting here via self.points would double-count that
        # diagnostic. normalize_points is the count-free path.
        mapped, _ = normalize_points(candidate["anchors"], self.sources, self.repo)
        anchors = set(mapped) | {(entry["file"], entry["line"])}
        points = sorted(anchors)
        return {
            "id": candidate["id"],
            "candidate_key": candidate["candidate_key"],
            "case": candidate["case"],
            "entry": entry,
            "members": [candidate["candidate_key"]],
            "absorbed": [],
            "lines": [{"file": f, "line": n} for f, n in points],
            "code": assemble(anchors, self.sources),
            "flow_count": 0,
            "truncated": False,
            "degraded": True,
            "analysis_kind": "degraded_anchor_only",
            "analysis": {"lines": [], "flow_count": 0, "truncated": False, "degraded": True},
            "measurement_notes": {
                "degraded": "flow/backward query failed or was skipped (poison streak); "
                "anchor-only slice, no dataflow context -- --resume re-attempts a full slice"
            },
            "source_sink": source_sink(candidate["case"], entry, None, self.sources, self.repo),
            "line_provenance": {"data_dependency": [], "candidate_anchor": points, "context": []},
        }

    def prepare_checkpoint(self) -> None:
        contract = {
            "source_hashes": self.source_hashes,
            "repo_path": self.repo.as_posix(),
            "settings": self.settings,
            "code_hashes": self.code_hashes,
            "joern_version": self.result["joern_version"],
            "slice_version": "slice-v2",
        }
        self.result["run_signature"] = digest(contract)
        if self.checkpoint:
            if len(self.source_hashes) != self.result["counters"]["files_seen"]:
                raise ValueError("Cannot checkpoint incomplete source reads")
            self.checkpoint.bind("run_contract", contract)

    def restore_export(self) -> None:
        baseline = self.checkpoint.get("baseline")
        if baseline is None:
            raise ValueError("Checkpoint has no completed candidate discovery to export")
        self.result.update(baseline["result"])
        self.errors.extend(baseline["errors"])
        self.candidates = self.checkpoint.get("candidates")
        self.cpg_ready = True
        self.measure("cpg_seconds", 0)
        self.result["measurement_notes"]["cpg_seconds"] = "Export-only: no CPG request executed"
        unfinished = 0
        for candidate in self.candidates:
            state = self.restore_stage(candidate)
            if state["phase"] == "complete":
                self.processed += 1
                if state["item"]:
                    self.completed.append(state["item"])
                self.increment("candidates_reused")
                continue
            # Not complete: a shard that hit a poison streak or was stopped, or a merge with a
            # shard missing. An unsharded run gives such a candidate the anchor-only degraded
            # slice (#307) -- it never reaches the checkpoint, so export used to DROP it and a
            # sharded run lost candidates an unsharded one keeps (#364). Emit the same slice here;
            # it needs only the candidate and the sources, no server. --resume still re-attempts
            # (nothing is saved).
            unfinished += 1
            if self.points([candidate["entry"]]):
                self.completed.append(self.degraded_slice(candidate))
                self.increment("candidates_degraded")
            else:
                self.increment("candidates_unmapped")
            # Count it as processed, as the unsharded degrade_only branch does: it is in the
            # output now, so candidates_not_processed (the completion metric) must not list it.
            self.processed += 1
        if unfinished:
            self.errors.append(
                f"Export-only: checkpoint contains unfinished candidates ({unfinished}); "
                "exported as degraded anchor-only slices"
            )

    def check_prepared(self) -> None:
        """Before any Joern request: `--cpg` must be the graph the prepare run saved (#368).

        The prepare run writes `prepared` only after `save_cpg` succeeded, so a seed from a
        prepare that died while saving has none. The size+SHA-256 bind the file to that run: a
        different or rewritten CPG under the same path is refused instead of sliced."""
        prepared = self.checkpoint.get("prepared") if self.checkpoint else None
        if prepared is None:
            raise ValueError(
                "--cpg needs a copy of a checkpoint whose --prepare-only run saved the CPG; this "
                "one has no 'prepared' record (that run failed before or while saving). Rerun "
                "--prepare-only with a new --checkpoint and --cpg-out"
            )
        if prepared["sha256"] is None:
            raise ValueError(
                "The prepare run could not read the CPG it saved, so it has no SHA-256 to verify "
                f"--cpg against ({prepared['path']}); rerun --prepare-only where the client can "
                "read --cpg-out"
            )
        found = file_identity(self.cpg_file)
        expected = {"bytes": prepared["bytes"], "sha256": prepared["sha256"]}
        if found != expected:
            raise ValueError(
                f"--cpg {self.cpg_file.as_posix()} is not the CPG the prepare run saved: "
                f"expected {expected}, found {found}"
            )
        self.prepared = prepared

    def restore_discovery(self) -> None:
        """Take the candidate list and discovery result a `--prepare-only` run left (#368)."""
        baseline = self.checkpoint.get("baseline") if self.checkpoint else None
        candidates = self.checkpoint.get("candidates") if self.checkpoint else None
        if baseline is None or candidates is None:
            raise ValueError("--cpg needs a checkpoint copied from a --prepare-only run")
        # The discovery counters, manifest and parse report are the prepare run's. The server and
        # cpg_seconds are this process's own measurements (here: the importCpg time), so they are
        # kept; the prepare's CPG time stays under prepared_cpg.cpg_seconds.
        server = self.result.get("server")
        cpg_seconds = self.result["counters"]["cpg_seconds"]
        self.result.update(baseline["result"])
        self.result["server"] = server
        self.measure("cpg_seconds", cpg_seconds)
        self.result["prepared_cpg"] = self.prepared
        self.errors.extend(e for e in baseline["errors"] if e not in self.errors)
        self.candidates = candidates

    def save_cpg(self) -> None:
        """Save the graph for the shards and slice nothing (#368 `--prepare-only`).

        `prepared` goes into the checkpoint last, after the copy succeeded and the client hashed
        it: a shard (`check_prepared`) accepts only a seed that has it."""
        data, _ = self.query("save_cpg", queries.save_cpg_query(self.cpg_out.as_posix()))
        identity = file_identity(self.cpg_out)
        server_bytes = data.get("bytes")
        if isinstance(server_bytes, str) and server_bytes.isascii() and server_bytes.isdigit():
            # #400: Files.size is a Scala Long and ujson writes a Long as a JSON string
            # ("4646430"), so the int read back never equalled it and every real prepare failed.
            server_bytes = int(server_bytes)
        prepared = {
            "path": self.cpg_out.as_posix(),
            "server_bytes": server_bytes,
            "bytes": identity["bytes"] if identity else None,
            "sha256": identity["sha256"] if identity else None,
            "cpg_seconds": self.result["counters"]["cpg_seconds"],
        }
        self.result["prepared_cpg"] = prepared
        if identity is None:
            self.result["measurement_notes"]["prepared_cpg"] = (
                "the saved CPG is not readable from this client, so bytes/sha256 are unmeasured "
                "and --cpg will refuse this checkpoint"
            )
            self.errors.append(
                f"Prepare-only: {self.cpg_out.as_posix()} is not readable from this client; "
                "shards cannot verify it and --cpg will refuse this checkpoint"
            )
        elif identity["bytes"] != server_bytes:
            raise ValueError(
                f"Joern saved {data.get('bytes')!r} bytes but this client reads "
                f"{identity['bytes']} at {self.cpg_out.as_posix()}; it is not the same file"
            )
        if self.checkpoint:
            self.checkpoint.bind("prepared", prepared)
        self.errors.append(
            "Prepare-only: CPG saved and candidates discovered, none sliced; copy the "
            "checkpoint per shard and run each with --resume --cpg <saved CPG> --shard I/N"
        )

    def run(self) -> dict:
        started = time.perf_counter()
        try:
            self.load_sources()
            self.prepare_checkpoint()
            if self.cpg_file is not None:
                self.check_prepared()
            if self.export_only:
                self.restore_export()
            else:
                self.build_cpg()
                self.inventory()
                self.define_library()
                if self.checkpoint:
                    self.checkpoint.bind("engine_config", self.result["joern_engine_config"])
                if self.cpg_file is not None:
                    # A shard on a prepared CPG reuses the prepared discovery (#368): same
                    # candidate list for every shard, and no case_a/case_b queries per shard.
                    self.restore_discovery()
                else:
                    self.collect_candidates()
                    if self.checkpoint:
                        self.checkpoint.set(
                            "baseline",
                            {"result": copy.deepcopy(self.result), "errors": self.errors.copy()},
                        )
                if self.prepare_only:
                    self.save_cpg()
                # QueryError comes from slicer_errors, not `from slicer import` -- under
                # `python slicer.py` that import loaded a second slicer module whose QueryError
                # never matched what slicer.py raised, so this isolation never ran (#363).
                consecutive_poison = 0
                # After a poison streak the server is likely unusable, but the remaining
                # candidates were already captured (that capture is the recall unit). Rather than
                # abort the run -- which DROPPED every remaining candidate's slice (#219 left them
                # unprocessed) -- switch to emitting anchor-only degraded slices without querying
                # the server, so no captured candidate is lost from slices.json.
                degrade_only = False
                if self.claims is not None and not self.prepare_only:
                    # A leftover claim file from another run would make every shard find all
                    # candidates taken and slice nothing; refuse it instead (#372 review).
                    self.claims.bind(self.result["run_signature"], self.candidates)
                for position, candidate in enumerate(
                    [] if self.prepare_only else self.candidates
                ):
                    if self.claims is not None:
                        # Shared queue (#369): a shard whose server is poisoned stops taking work
                        # so the healthy shards slice the rest, instead of degrading all of it.
                        if degrade_only:
                            break
                        if not self.claims.claim(candidate["candidate_key"]):
                            continue
                    elif self.shard and position % self.shard[1] != self.shard[0]:
                        continue
                    if degrade_only:
                        # A prior run may have already completed this candidate's full slice (a
                        # "complete" checkpoint). That reuse needs NO server, so restore it rather
                        # than degrading a slice we already have -- degrading it would drop the full
                        # dataflow that `--resume` promises to keep (#307 review HIGH).
                        state = self.restore_stage(candidate)
                        if state["phase"] == "complete":
                            self.increment("candidates_reused")
                            if state["item"] is not None:
                                self.completed.append(state["item"])
                            self.processed += 1
                            continue
                        if not self.points([candidate["entry"]]):
                            self.increment("candidates_unmapped")
                            self.processed += 1
                            continue
                        self.completed.append(self.degraded_slice(candidate))
                        self.increment("candidates_degraded")
                        self.processed += 1
                        continue
                    try:
                        item = self.slice_candidate(candidate)
                    except QueryError as exc:
                        # Isolate a per-candidate failure. A single flows/backward timeout or
                        # abort used to propagate to the outer handler and discard every remaining
                        # candidate (#219). Record it, and instead of DROPPING this candidate's
                        # slice, emit an anchor-only degraded slice so its capture survives into
                        # the output. The checkpoint is left at its last saved phase -- "pending"
                        # for a flows/backward-query failure (nothing saved yet), or "flow_saved"
                        # for a context (expand_context) failure -- so --resume re-attempts from
                        # there (a context failure resumes the saved flow, not from scratch).
                        self.increment("candidates_failed")
                        self.event(
                            "candidate_failed",
                            candidate_key=candidate["candidate_key"],
                            error=str(exc),
                        )
                        self.processed += 1
                        self.completed.append(self.degraded_slice(candidate))
                        self.increment("candidates_degraded")
                        if exc.poisoned:
                            consecutive_poison += 1
                            if consecutive_poison >= POISON_STOP_STREAK:
                                remaining = (
                                    # Claim mode: the loop breaks, nothing more is claimed.
                                    "this shard stops claiming candidates; the rest stay in the "
                                    "shared queue for the other shards"
                                    if self.claims is not None
                                    else "remaining candidates get degraded (anchor-only) "
                                    "slices without querying the server"
                                )
                                self.errors.append(
                                    f"{consecutive_poison} consecutive poisoned candidates; "
                                    f"{remaining}. Restart the dedicated server and --resume "
                                    "to re-attempt full slices"
                                )
                                degrade_only = True
                        else:
                            consecutive_poison = 0
                        continue
                    consecutive_poison = 0
                    self.processed += 1
                    if item is not None:
                        self.completed.append(item)
        except Exception as exc:
            self.errors.append(f"{type(exc).__name__}: {exc}")
        except KeyboardInterrupt:
            self.errors.append("Interrupted by user; server execution may continue")
        finally:
            self.active_candidate = None
            self.candidate_started = None
            self.finish(started)
        return self.result

    def finish(self, started: float) -> None:
        if self.prepare_only:
            # After the checkpoint baseline, so neither a shard nor a merged export inherits it.
            # The loader (src/adapters/slices.py) refuses this file: it holds no slices.
            self.result["prepare_only"] = True
        if self.shard:
            # After the checkpoint baseline, so a merged --export-only does not inherit it.
            index, count = self.shard
            self.result["shard"] = {"index": index, "count": count}
            taken = (
                "only the candidates it claimed from the shared queue"
                if self.claims is not None
                else f"only candidates at positions p % {count} == {index}"
            )
            self.errors.append(
                f"Shard {index}/{count}: {taken} were sliced; merge the shard checkpoints "
                "(tools/merge_slicer_shards.py) and run --export-only for the full result"
            )
        if self.cpg_ready:
            truncated = [c for c in self.completed if c["truncated"]]
            if truncated:
                self.errors.append(f"Analysis truncated for {len(truncated)} completed candidates")
            self.measure(
                "flows_truncated", sum(c["truncated"] for c in self.completed if c["case"] == "A")
            )
            self.measure(
                "backward_truncated",
                sum(c["truncated"] for c in self.completed if c["case"] == "B"),
            )
            self.result["status"] = "partial" if self.errors else "ok"
            self.measure("slices_before_clustering", len(self.completed))
            self.result["slices"] = cluster(self.completed, self.result["threshold"], self.sources)
            self.measure("slices_after_clustering", len(self.result["slices"]))
            # Discovery finished: every selected case was counted (--cases may skip one).
            if all(
                self.result["counters"]["candidates_case_" + case.lower()] is not None
                for case in self.selected_cases()
            ):
                self.measure("candidates_not_processed", len(self.candidates) - self.processed)
        if self.errors:
            self.result["error"] = "; ".join(dict.fromkeys(self.errors))
        self.measure("total_seconds", time.perf_counter() - started)
