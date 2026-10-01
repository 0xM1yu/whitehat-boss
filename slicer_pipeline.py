"""Candidate-stage transitions, reusable checkpoints and result accounting."""

import copy
import time

import slicer_queries as queries
from slicer_candidates import collect
from slicer_checkpoint import digest
from slicer_errors import QueryError
from slicer_library import Library
from slicer_source import assemble, cluster, normalize_points, recover_points

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


class SlicingStages:
    def collect_candidates(self) -> None:
        rows = {}
        for case, query in (
            ("A", queries.candidates_a_query()),
            ("B", queries.candidates_b_query(self.engine_limits)),
        ):
            rows[case], _ = self.query("case_" + case.lower(), query)
            self.measure(
                "candidates_case_" + case.lower(), len(collect({case: rows[case]}, self.repo))
            )
        # HIGH-1 (#241 review): the .take cap in candidates_a_query is silent. When the count
        # reaches the cap, api and/or readers may be truncated, so record a per-run note (measure
        # popped any prior note). Below the cap it cannot have been truncated, so no note.
        if self.result["counters"]["candidates_case_a"] >= queries.CASE_A_CANDIDATE_CAP:
            self.result["measurement_notes"]["candidates_case_a"] = (
                f"reached the cap ({queries.CASE_A_CANDIDATE_CAP}); api and/or readers may be "
                "truncated to an unspecified first-N in CPG traversal order (api: write-context "
                "methods first, then traversal order within each layer; slicer_queries)"
            )
        self.candidates = collect(rows, self.repo)
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

    def restore_discovery(self) -> None:
        """Take the candidate list and discovery result a `--prepare-only` run left (#368)."""
        baseline = self.checkpoint.get("baseline") if self.checkpoint else None
        candidates = self.checkpoint.get("candidates") if self.checkpoint else None
        if baseline is None or candidates is None:
            raise ValueError("--cpg needs a checkpoint copied from a --prepare-only run")
        # Keep this run's own CPG timing/server facts; the counters are the discovery's.
        server = self.result.get("server")
        self.result.update(baseline["result"])
        self.result["server"] = server
        self.errors.extend(e for e in baseline["errors"] if e not in self.errors)
        self.candidates = candidates

    def save_cpg(self) -> None:
        """Save the graph for the shards and slice nothing (#368 `--prepare-only`)."""
        data, _ = self.query("save_cpg", queries.save_cpg_query(self.cpg_out.as_posix()))
        self.result["prepared_cpg"] = {"path": self.cpg_out.as_posix(), "bytes": data.get("bytes")}
        self.errors.append(
            "Prepare-only: CPG saved and candidates discovered, none sliced; copy the "
            "checkpoint per shard and run each with --resume --cpg <saved CPG> --shard I/N"
        )

    def run(self) -> dict:
        started = time.perf_counter()
        try:
            self.load_sources()
            self.prepare_checkpoint()
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
                for position, candidate in enumerate(
                    [] if self.prepare_only else self.candidates
                ):
                    if self.shard and position % self.shard[1] != self.shard[0]:
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
                                self.errors.append(
                                    f"{consecutive_poison} consecutive poisoned candidates; "
                                    "remaining candidates get degraded (anchor-only) slices "
                                    "without querying the server. Restart the dedicated server "
                                    "and --resume to re-attempt full slices"
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
        if self.shard:
            # After the checkpoint baseline, so a merged --export-only does not inherit it.
            index, count = self.shard
            self.result["shard"] = {"index": index, "count": count}
            self.errors.append(
                f"Shard {index}/{count}: only candidates at positions p % {count} == {index} "
                "were sliced; merge the shard checkpoints (tools/merge_slicer_shards.py) and "
                "run --export-only for the full result"
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
            if self.result["counters"]["candidates_case_b"] is not None:
                self.measure("candidates_not_processed", len(self.candidates) - self.processed)
        if self.errors:
            self.result["error"] = "; ".join(dict.fromkeys(self.errors))
        self.measure("total_seconds", time.perf_counter() - started)
