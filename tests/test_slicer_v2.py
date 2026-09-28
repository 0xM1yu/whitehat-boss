"""Mock HTTP, real SQLite/files, and public C fixture regression coverage."""

import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from test_slicer import candidate, make_slice, payloads, response, run_cli

import slicer
import slicer_queries as queries
from slicer_candidates import collect
from slicer_checkpoint import Checkpoint
from slicer_export import export_sources
from slicer_source import cluster, recover_lines

READ_VERSION = slicer.read_version

# One Scala string literal, escapes included.
SCALA_STRING = re.compile(r'"(?:\\.|[^"\\])*"')


@pytest.fixture(name="repo")
def source_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.c").write_text(
        "int entry(char *p)\n{\n    char buf[32];\n    if (p) {\n"
        "        sprintf(buf, p);\n    }\n    return 0;\n}\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture(autouse=True)
def measured_version(monkeypatch):
    monkeypatch.setattr(slicer, "read_version", lambda *args: ("test-joern", "test fixture"))


def checkpoint_options(tmp_path):
    return ["--checkpoint", str(tmp_path / "state.db")]


def test_sink_selector_keeps_two_calls_on_the_same_line(repo):
    a, b = candidate("strcpy", 5), candidate("strcpy", 5)
    b["selector"] = {**b["selector"], "column": 30, "ast_path": [1, 3]}
    first = collect({"B": [a, b]}, repo)
    assert len(first) == 2
    assert first == collect({"B": [b, a]}, repo)
    assert first[0]["candidate_key"] != first[1]["candidate_key"]


def test_b_reports_graph_measurements_instead_of_fake_flow_count(monkeypatch, repo, tmp_path):
    _, result, transport = run_cli(
        monkeypatch, repo, tmp_path, payloads(case_b=[candidate("sprintf", 5)])
    )
    item = result["slices"][0]
    assert result["slice_version"] == "slice-v2"
    assert item["flow_count"] is None
    assert item["analysis_kind"] == "backward_ddg"
    assert item["analysis"]["node_count"] == 2
    assert "flow_count" in item["measurement_notes"]
    assert "backward" in transport.stages and "flows" not in transport.stages
    # The selector logic is compiled once into the session library; each call carries the data.
    sent = [json.loads(req.data)["query"] for req, _ in transport.requests]
    library = next(q for q in sent if "__SLICER_library__" in q)
    call = next(q for q in sent if "__SLICER_backward__" in q and q is not library)
    assert "c.code == code" in library and "astPath(c) == path" in library
    assert "sink.argument.l" in library
    assert '"sprintf(buf)"' in call and "List(1,2)" in call
    assert call.count(library.split()[1] + ".backward(") == 1  # calls THIS session's object
    assert item["members"] == [item["candidate_key"]]


def test_b_cut_is_not_counted_as_a_path_cap(monkeypatch, repo, tmp_path):
    data = payloads(case_b=[candidate("sprintf", 5)])
    backward, _ = slicer.decode_response(data["backward"], "backward")
    backward.update(truncated=True, truncation_reasons=["backward_states"])
    data["backward"] = response("backward", backward)
    _, result, _ = run_cli(monkeypatch, repo, tmp_path, data)
    assert result["status"] == "partial"
    assert result["counters"]["backward_truncated"] == 1
    assert result["counters"]["flows_truncated"] == 0


def test_completed_flow_is_reused_after_context_failure(monkeypatch, repo, tmp_path):
    data = payloads(case_a=[candidate()], flow_count=2)
    data["context"] = {"success": False, "stderr": "java.lang.OutOfMemoryError"}
    options = checkpoint_options(tmp_path)
    _, interrupted, _ = run_cli(monkeypatch, repo, tmp_path, data, *options)
    assert interrupted["counters"]["queries_aborted"] == 1
    good = payloads(case_a=[candidate()], flow_count=2)
    _, resumed, transport = run_cli(monkeypatch, repo, tmp_path, good, *options, "--resume")
    assert resumed["status"] == "ok"
    assert "flows" not in transport.stages
    assert resumed["counters"]["candidate_stages_reused"] == 1
    assert len(resumed["attempts"]) == 2
    assert resumed["attempts"][0]["counters"]["queries_aborted"] == 1
    separate = tmp_path / "fresh"
    separate.mkdir()
    _, uninterrupted, _ = run_cli(monkeypatch, repo, separate, good)
    assert resumed["slices"] == uninterrupted["slices"]


def test_complete_candidate_survives_later_sink_error(monkeypatch, repo, tmp_path):
    data = payloads(case_a=[candidate()], case_b=[candidate("sprintf", 5)], flow_count=2)
    data["backward"] = {"success": False, "stderr": "java.lang.OutOfMemoryError"}
    options = checkpoint_options(tmp_path)
    _, first, _ = run_cli(monkeypatch, repo, tmp_path, data, *options)
    # The case_a candidate completes; the case_b one's backward query OOMs. It is no longer
    # DROPPED -- it degrades to an anchor-only slice (its capture survives) -- so there are two
    # slices before clustering (one full, one degraded), and its checkpoint stays pending so
    # --resume re-attempts a full slice below.
    assert first["counters"]["slices_before_clustering"] == 2
    assert first["counters"]["candidates_degraded"] == 1
    data = payloads(case_a=[candidate()], case_b=[candidate("sprintf", 5)], flow_count=2)
    _, second, transport = run_cli(monkeypatch, repo, tmp_path, data, *options, "--resume")
    assert second["counters"]["candidates_reused"] == 1
    assert "flows" not in transport.stages
    assert {s["case"] for s in second["slices"]} == {"A", "B"}


@pytest.mark.parametrize("change", ["source", "cap", "candidate"])
def test_resume_mismatch_cannot_mix_results(monkeypatch, repo, tmp_path, change):
    data = payloads(case_a=[candidate()])
    options = checkpoint_options(tmp_path)
    run_cli(monkeypatch, repo, tmp_path, data, *options)
    if change == "source":
        with (repo / "main.c").open("a", encoding="utf-8") as stream:
            stream.write("// change\n")
    elif change == "cap":
        options += ["--max-flows-per-candidate", "2"]
    else:
        data = payloads(case_a=[candidate("different")])
    _, result, transport = run_cli(monkeypatch, repo, tmp_path, data, *options, "--resume")
    assert "Checkpoint mismatch" in result["error"]
    assert not result["slices"]
    if change != "candidate":
        assert transport.stages == []


def test_export_only_uses_saved_results_without_joern(monkeypatch, repo, tmp_path):
    data = payloads(case_a=[candidate()], flow_count=1)
    options = checkpoint_options(tmp_path)
    _, first, _ = run_cli(monkeypatch, repo, tmp_path, data, *options)
    _, exported, transport = run_cli(monkeypatch, repo, tmp_path, {}, *options, "--export-only")
    assert transport.stages == []
    assert first["slices"] == exported["slices"]
    assert exported["counters"]["cpg_seconds"] == 0


def test_real_sqlite_commit_and_os_lock_survive_process_death(tmp_path):
    path = tmp_path / "state.db"
    with Checkpoint(path) as store:
        store.save("first", {"phase": "complete", "data": "committed"})
        with pytest.raises((OSError, sqlite3.OperationalError)):
            Checkpoint(path, resume=True)
    program = """
import os, sys
from pathlib import Path
from slicer_checkpoint import Checkpoint
store = Checkpoint(Path(sys.argv[1]), resume=True)
store.conn.execute('DELETE FROM stages')
os._exit(19)
"""
    child = subprocess.run([sys.executable, "-c", program, str(path)], check=False)
    assert child.returncode == 19
    with Checkpoint(path, resume=True) as store:
        assert store.load("first")["data"] == "committed"


def test_checksum_corruption_is_rejected(tmp_path):
    with Checkpoint(tmp_path / "state.db") as store:
        store.save("candidate", {"phase": "flow_saved"})
        with store.conn:
            store.conn.execute("UPDATE stages SET payload='{}'")
        with pytest.raises(ValueError, match="checksum"):
            store.load("candidate")


def test_checkpoint_requires_observed_server_version(monkeypatch, repo, tmp_path):
    monkeypatch.setattr(slicer, "read_version", lambda *args: (None, "unavailable"))
    code, result, transport = run_cli(
        monkeypatch, repo, tmp_path, payloads(), *checkpoint_options(tmp_path)
    )
    assert code == 1 and result["status"] == "failed"
    assert "measured" in result["error"]
    assert transport.stages == []


def test_export_only_restores_nondefault_analysis_settings(monkeypatch, repo, tmp_path):
    options = checkpoint_options(tmp_path)
    _, original, _ = run_cli(
        monkeypatch,
        repo,
        tmp_path,
        payloads(case_a=[candidate()]),
        *options,
        "--max-flows-per-candidate",
        "3",
    )
    _, exported, transport = run_cli(monkeypatch, repo, tmp_path, {}, *options, "--export-only")
    assert transport.stages == []
    assert original["slices"] == exported["slices"]
    assert exported["settings"]["max_flows_per_candidate"] == 3


def test_structural_fixture_preserves_labels_jumps_and_guards():
    source = (Path(__file__).parent / "fixtures/slicer_v2/main.c").read_text().splitlines()
    selected = {i for i, line in enumerate(source, 1) if "strcpy(dst" in line}
    restored = recover_lines(source, selected)
    for i, line in enumerate(source, 1):
        if any(
            word in line
            for word in ("case ", "default:", "break;", "continue;", "goto ", "cleanup:")
        ):
            assert i in restored, (i, line)
    assembled = "\n".join(source[i - 1] for i in sorted(restored))
    assert "if (choose()) break;" in assembled
    assert "if (i == 1) continue;" in assembled
    assert "if (op == 7) goto cleanup;" in assembled


def test_structural_recovery_does_not_invent_breaks():
    source = [
        "void f(int n) {",
        "switch(n) {",
        "case 1:",
        "use();",
        "case 2:",
        "other();",
        "break;",
        "default:",
        "break;",
        "}",
        "}",
    ]
    result = recover_lines(source, {4})
    assert {3, 5, 7, 8, 9} <= result
    assert sum("break;" in source[n - 1] for n in result) == 2


def test_export_manifest_hashes_and_numbered_lines(monkeypatch, repo, tmp_path):
    _, result, _ = run_cli(
        monkeypatch, repo, tmp_path, payloads(case_a=[candidate()], flow_count=1)
    )
    job = slicer.Slicer(repo, 0.95, None, slicer.JoernClient("unused", None))
    job.load_sources()
    output = tmp_path / "export"
    export_sources(output, result, job.sources)
    manifest = json.loads((output / "MANIFEST.json").read_text())
    for file, expected in manifest["files"].items():
        assert hashlib.sha256((output / file).read_bytes()).hexdigest() == expected
    numbered = (output / manifest["slices"][0]["numbered"]).read_text()
    assert "main.c:5 |         sprintf(buf, p);" in numbered
    assert (output / manifest["slices"][0]["raw"]).read_text() == result["slices"][0]["code"] + "\n"


@pytest.mark.parametrize(
    "option, name",
    [
        ("--checkpoint", "slices.json"),
        ("--checkpoint", "MANIFEST.json"),
        ("--checkpoint", "raw/A0001_entry_main_5_k.c"),
        ("--out", "MANIFEST.json"),
        ("--out", "candidate-manifest.json"),
        ("--progress", "SHA256SUMS"),
        ("--progress", "numbered/A0001_entry_main_5_k.txt"),
        # `export / ""` is the export directory itself -- the `path == reserved`
        # half of the guard, which every case above leaves untouched.
        ("--checkpoint", ""),
        ("--progress", ""),
    ],
)
def test_run_state_under_the_export_directory_is_refused(tmp_path, option, name):
    """`export_sources()` os.replace()s every path under `--export-dir`.

    An open SQLite checkpoint there is replaced by JSON on POSIX and the resume state is
    gone -- the run cannot be continued and nothing reports the loss. Pairwise distinctness
    of out/checkpoint/progress does not catch it: the three paths differ from each other.
    """
    export = tmp_path / "export"
    argv = ["--repo", str(tmp_path), "--export-dir", str(export)]
    if option != "--out":
        argv += ["--out", str(tmp_path / "result.json")]
    argv += [option, str(export / name)]
    with pytest.raises(SystemExit) as error:
        slicer.main(argv)
    assert error.value.code == 2


def test_the_reserved_namespace_is_the_whole_export_directory(monkeypatch, repo, tmp_path):
    """Whatever `export_sources()` writes must land inside what the guard reserves.

    **Search from wider than the thing under test.** The first version of this globbed
    `export/` itself, so `export in path.parents` held for everything it could find --
    a tautology that passed even with an exporter writing to `export/../`. Globbing the
    parent is what makes the assertion able to fail.
    """
    _, result, _ = run_cli(
        monkeypatch, repo, tmp_path, payloads(case_a=[candidate()], flow_count=1)
    )
    job = slicer.Slicer(repo, 0.95, None, slicer.JoernClient("unused", None))
    job.load_sources()
    root = tmp_path / "namespace"
    export = root / "export"
    before = {p.resolve() for p in root.rglob("*")}
    export_sources(export, result, job.sources)
    written = [p for p in root.rglob("*") if p.is_file() and p.resolve() not in before]
    assert written, "the exporter wrote nothing; this test proves nothing"
    escaped = [str(p) for p in written if export.resolve() not in p.resolve().parents]
    assert escaped == [], f"export_sources wrote outside --export-dir: {escaped}"


def test_a_valid_export_dir_run_reaches_the_export(monkeypatch, repo, tmp_path):
    """The guard must let a correct layout through -- and the export must actually happen.

    Rejecting everything also satisfies "no checkpoint was clobbered", so the reject-path
    test cannot notice an over-broad guard. Only running `main()` to completion can.
    """
    export = tmp_path / "export"
    code, _, _ = run_cli(
        monkeypatch,
        repo,
        tmp_path,
        payloads(case_a=[candidate()], flow_count=1),
        "--checkpoint",
        str(tmp_path / "state.db"),
        "--progress",
        str(tmp_path / "progress.jsonl"),
        "--export-dir",
        str(export),
    )
    assert code == 0
    assert (export / "MANIFEST.json").is_file(), "the guard rejected a legitimate layout"


def test_an_export_dir_that_is_a_file_is_refused_before_the_run(tmp_path):
    """Fail on the typo before paying for Joern, not after the analysis succeeded.

    `atomic_bytes()` mkdirs the parent, so a regular file at `--export-dir` only blows up
    at the very end. `main()` then returns 1 for a run whose `--out` holds a correct
    `"ok"` result -- the exit-code contract says 1 means failed.
    """
    occupied = tmp_path / "export"
    occupied.write_text("not a directory\n", encoding="utf-8")
    argv = [
        "--repo", str(tmp_path),
        "--out", str(tmp_path / "result.json"),
        "--export-dir", str(occupied),
    ]
    with pytest.raises(SystemExit) as error:
        slicer.main(argv)
    assert error.value.code == 2


def test_an_unreadable_source_blocks_the_checkpoint(monkeypatch, repo, tmp_path):
    """A file counted (files_seen) but not actually read must not let a run_contract bind.

    Invalid encoding no longer triggers this -- latin-1 fallback reads it (D85/#254). A
    genuine I/O failure still does: `source_hashes` stays short of `files_seen`, the exact
    state `Cannot checkpoint incomplete source reads` exists to refuse.
    """
    (repo / "bad.cpp").write_text("int x;\n", encoding="utf-8")
    real_read = slicer._read_no_follow

    def failing_read(path):
        if path.name == "bad.cpp":
            raise OSError("simulated read failure")
        return real_read(path)

    monkeypatch.setattr(slicer, "_read_no_follow", failing_read)
    _, result, _ = run_cli(
        monkeypatch, repo, tmp_path, payloads(), "--checkpoint", str(tmp_path / "state.db")
    )
    assert result["counters"]["files_read_failed"] == 1
    assert "incomplete source reads" in (result.get("error") or ""), result.get("error")


def test_selector_type_error_stops_before_interpolation(monkeypatch, repo, tmp_path):
    row = candidate("sprintf", 5)
    row["selector"]["ast_path"] = ['0); throw new RuntimeException("x")']
    _, result, transport = run_cli(monkeypatch, repo, tmp_path, payloads(case_b=[row]))
    assert result["status"] == "partial"
    assert "backward" not in transport.stages


def test_clustering_never_merges_case_a_into_case_b():
    """Identical lines must not merge across cases: A counts paths, B counts nodes."""
    a = make_slice("slice_0001", [1, 2, 3])
    b = make_slice("slice_0002", [1, 2, 3])
    b["case"], b["flow_count"] = "B", None
    sources = {"a.c": ["x", "y", "z"]}
    result = cluster([a, b], 0.95, sources)
    assert [item["case"] for item in result] == ["A", "B"]
    assert all(not item["absorbed"] for item in result)
    assert result[0]["flow_count"] == 1 and result[1]["flow_count"] is None


def test_two_version_like_fields_refuse_to_name_a_version(monkeypatch):
    """Picking either one would stamp a guess into `run_contract` and poison every resume.

    A launcher that prints its own JVM build next to Joern's gives two `x.y.z` lines. The
    set is ambiguous, so the identity is `None` and the reason is kept, not a coin flip.
    """
    banner = "Version: 4.0.605\nopenjdk 21.0.12\njoern 4.0.999\n"

    def version(command, **kwargs):
        return slicer.subprocess.CompletedProcess(command, 0, banner, "")

    monkeypatch.setattr(slicer.subprocess, "run", version)
    found, note = READ_VERSION(Path("joern"), None)
    assert found is None, f"two distinct versions must not resolve to {found!r}"
    assert note.startswith("No unambiguous Joern version")
    assert "4.0.605" in note and "4.0.999" in note, "the raw output must survive in the note"


def test_version_takes_the_semantic_field_and_keeps_the_raw_output(monkeypatch, repo, tmp_path):
    """A launcher banner carries a timestamp and warnings; only x.y.z is the identity."""
    noisy = (
        "\x1b[33mWARNING: terminal size unavailable\x1b[0m\n"
        "2026-09-15 00:31:02,118 [main] INFO  starting\n"
        "Version: 4.0.605\n"
    )

    def version(command, **kwargs):
        return slicer.subprocess.CompletedProcess(command, 0, noisy, "")

    monkeypatch.setattr(slicer.subprocess, "run", version)
    monkeypatch.setattr(slicer, "read_version", READ_VERSION)
    _, result, _ = run_cli(
        monkeypatch,
        repo,
        tmp_path,
        payloads(),
        "--joern-version-command",
        "/opt/joern/joern",
    )
    assert result["joern_version"] == "4.0.605"
    recorded = next(d for d in result["diagnostics"] if d["stage"] == "version_command")
    assert "2026-09-15 00:31:02,118" in recorded["output"]
    assert "terminal size unavailable" in recorded["output"]


@pytest.mark.parametrize("field", ["code_hashes", "joern_version"])
def test_resume_rejects_a_changed_toolchain(monkeypatch, repo, tmp_path, field):
    """slicer*.py and the server version are part of the contract, not just the inputs."""
    options = checkpoint_options(tmp_path)
    run_cli(monkeypatch, repo, tmp_path, payloads(case_a=[candidate()]), *options)
    database = tmp_path / "state.db"
    with Checkpoint(database, resume=True) as store:
        contract = store.get("run_contract")
        assert contract[field], field
        contract[field] = (
            "4.0.000" if field == "joern_version" else dict.fromkeys(contract[field], "0" * 64)
        )
        store.set("run_contract", contract)
    _, result, transport = run_cli(
        monkeypatch, repo, tmp_path, payloads(case_a=[candidate()]), *options, "--resume"
    )
    assert "Checkpoint mismatch: run_contract" in result["error"]
    assert not result["slices"]
    assert transport.stages == []


def test_context_query_survives_the_jvm_string_constant_limit():
    """A JVM class file caps one string constant at 65,535 UTF-8 bytes.

    ATOM sample_03 aborted on exactly this: `String constant is too long for the JVM`
    from scalac, after 208 of 436 candidates. The points list is interpolated into the
    Scala source, so a large enough slice makes the query uncompilable.
    """
    points = [(f"examples/some/rather/long/path/source_{n % 7}.c", n) for n in range(1, 4001)]
    query = queries.context_query(points, "/work/repo")
    literals = SCALA_STRING.findall(query)
    assert literals, "no string literals found; the extraction regex is wrong"
    longest = max(len(s.encode("utf-8")) for s in literals)
    assert longest <= 65535, f"a single Scala string constant is {longest} bytes"


def test_context_query_reassembles_the_same_points_at_runtime():
    """Chunking must not change the value: the joined literals must re-read as the input."""
    points = [(f"src/f{n % 3}.c", n) for n in range(1, 500)]
    query = queries.context_query(points, "/work/repo")
    chunks = re.search(r"ujson\.read\((.*?)\)\.arr", query, re.DOTALL)
    assert chunks, "points are no longer passed through ujson.read(...)"
    rebuilt = "".join(json.loads(s) for s in SCALA_STRING.findall(chunks.group(1)))
    assert json.loads(rebuilt) == [list(p) for p in points]
