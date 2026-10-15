"""Mock HTTP, real SQLite/files, and public C fixture regression coverage."""

import hashlib
import json
import random
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_slicer import MockTransport, candidate, make_slice, payloads, report, response, run_cli

import slicer
import slicer_pipeline
import slicer_queries as queries
from slicer_candidates import collect
from slicer_checkpoint import Checkpoint, digest, merge_shards
from slicer_export import export_sources
from slicer_source import cluster, recover_lines, source_sink

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
sys.path.insert(0, sys.argv[2])
from slicer_checkpoint import Checkpoint
store = Checkpoint(Path(sys.argv[1]), resume=True)
store.conn.execute('DELETE FROM stages')
os._exit(19)
"""
    slicer_dir = str(Path(slicer.__file__).parent)
    child = subprocess.run([sys.executable, "-c", program, str(path), slicer_dir], check=False)
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


def test_case_a_priority_list_survives_the_jvm_string_constant_limit():
    """The priority list is interpolated into the Scala source like the context points are, and a
    long list (every file of a big repo) must not become one oversized string constant."""
    files = [f"examples/some/rather/long/path/source_{n}.c" for n in range(1, 6001)]
    query = queries.candidates_a_query(files, "/work/repo")
    literals = SCALA_STRING.findall(query)
    assert literals, "no string literals found; the extraction regex is wrong"
    assert max(len(s.encode("utf-8")) for s in literals) <= 65535
    # the list itself is longer than one constant could hold: it really is split
    assert len(re.search(r"val prioFiles = (.*?)\.mkString", query, re.DOTALL)[1]) > 65535


def test_case_a_priority_list_reassembles_the_same_files_at_runtime():
    """Chunking and escaping must not change the value: the joined literals re-read as the sorted
    list, quotes, backslashes and non-ASCII names included."""
    files = ['a"b.c', "back\\slash.c", "한글/파일.c", "\U0001f600/x.c"]
    files += [f"d/f{n}.c" for n in range(3000)]
    query = queries.candidates_a_query(files, "/work/repo")
    chunks = re.search(r"val prioFiles = (.*?)\.mkString\.split", query, re.DOTALL)
    assert chunks, "the list is no longer rebuilt with mkString.split(...)"
    rebuilt = "".join(json.loads(s) for s in SCALA_STRING.findall(chunks.group(1)))
    assert rebuilt.split("\n") == sorted(files)


def run_shards(monkeypatch, repo, tmp_path, data, count):
    databases = []
    for index in range(count):
        folder = tmp_path / f"shard{index}"
        folder.mkdir(parents=True)
        databases.append(folder / "state.db")
        code, result, _ = run_cli(
            monkeypatch, repo, folder, data, "--checkpoint", str(databases[-1]),
            "--shard", f"{index}/{count}",
        )
        # A shard's own output is a fraction of the run and must not read as complete.
        assert code == 2 and result["status"] == "partial"
        assert result["shard"] == {"index": index, "count": count}
    return databases


def test_merged_shards_export_the_same_slices_as_one_run(monkeypatch, repo, tmp_path):
    def data():
        return payloads(
            case_a=[candidate(), candidate("other", 3)], case_b=[candidate("sprintf", 5)],
            flow_count=1,
        )

    whole = tmp_path / "whole"
    whole.mkdir()
    _, single, _ = run_cli(monkeypatch, repo, whole, data())
    databases = run_shards(monkeypatch, repo, tmp_path, data(), 2)
    merged = tmp_path / "merged.db"
    summary = merge_shards(merged, databases)
    assert summary == {"shards": 2, "candidates": 3, "complete": 3}
    export = tmp_path / "export"
    export.mkdir()
    _, exported, transport = run_cli(
        monkeypatch, repo, export, {}, "--checkpoint", str(merged), "--export-only"
    )
    assert transport.stages == []
    assert exported["status"] == "ok" and "shard" not in exported
    assert exported["slices"] == single["slices"]


def test_shards_of_different_runs_are_not_merged(monkeypatch, repo, tmp_path):
    first = run_shards(monkeypatch, repo, tmp_path / "a", payloads(case_a=[candidate()]), 2)
    other = run_shards(
        monkeypatch, repo, tmp_path / "b", payloads(case_a=[candidate("different")]), 2
    )
    with pytest.raises(ValueError, match="Shard mismatch on candidates"):
        merge_shards(tmp_path / "merged.db", [first[0], other[1]])
    assert not (tmp_path / "merged.db").exists()


def test_a_shard_without_a_checkpoint_is_refused(repo, tmp_path):
    with pytest.raises(SystemExit):
        slicer.main(["--repo", str(repo), "--out", str(tmp_path / "s.json"), "--shard", "0/2"])
    with pytest.raises(SystemExit):
        slicer.main(["--repo", str(repo), "--out", str(tmp_path / "s.json"), "--shard", "2/2",
                     "--checkpoint", str(tmp_path / "state.db")])
    for bad in ("0/1", "٠/٤", "-1/2"):
        with pytest.raises(SystemExit):
            slicer.main(["--repo", str(repo), "--out", str(tmp_path / "s.json"), "--shard", bad,
                         "--checkpoint", str(tmp_path / "state.db")])


def three_candidates():
    return payloads(
        case_a=[candidate(), candidate("other", 3)], case_b=[candidate("sprintf", 5)],
        flow_count=1,
    )


@pytest.mark.parametrize("count", [2, 3])
def test_each_shard_slices_exactly_its_positions(monkeypatch, repo, tmp_path, count):
    """분할 자체를 문다 — `%`의 비교를 뒤집거나 건너뛰기를 끄면 샤드가 남의 몫을 자른다."""
    databases = run_shards(monkeypatch, repo, tmp_path, three_candidates(), count)
    for index, database in enumerate(databases):
        with Checkpoint(database, resume=True) as store:
            keys = [c["candidate_key"] for c in store.get("candidates")]
            saved = {row[0] for row in store.conn.execute("SELECT candidate_key FROM stages")}
        assert saved == {key for position, key in enumerate(keys) if position % count == index}


def test_a_missing_shard_exports_as_unfinished(monkeypatch, repo, tmp_path):
    """병합 export의 완결성 그물 — 샤드 하나가 빠지면 완성처럼 보이면 안 된다."""
    databases = run_shards(monkeypatch, repo, tmp_path, three_candidates(), 3)
    merged = tmp_path / "merged.db"
    summary = merge_shards(merged, databases[:2])
    assert summary["complete"] < summary["candidates"]
    export = tmp_path / "export"
    export.mkdir()
    code, exported, _ = run_cli(
        monkeypatch, repo, export, {}, "--checkpoint", str(merged), "--export-only"
    )
    assert code == 2 and exported["status"] == "partial"
    assert "unfinished candidates" in exported["error"]


def test_the_merge_tool_exits_nonzero_when_candidates_are_missing(monkeypatch, repo, tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "merge_tool", Path(slicer.__file__).parent.parent / "merge_slicer_shards.py"
    )
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    databases = run_shards(monkeypatch, repo, tmp_path, three_candidates(), 3)
    assert tool.main(["x", str(tmp_path / "part.db"), *map(str, databases[:2])]) == 2
    assert tool.main(["x", str(tmp_path / "full.db"), *map(str, databases)]) == 0


@pytest.mark.parametrize("key", ["run_contract", "engine_config"])
def test_shards_that_disagree_on_the_run_are_not_merged(monkeypatch, repo, tmp_path, key):
    databases = run_shards(monkeypatch, repo, tmp_path, three_candidates(), 2)
    with Checkpoint(databases[1], resume=True) as store:
        value = store.get(key)
        value["tampered"] = True
        store.set(key, value)
    with pytest.raises(ValueError, match=f"Shard mismatch on {key}"):
        merge_shards(tmp_path / "merged.db", databases)
    assert not (tmp_path / "merged.db").exists()
    assert not (tmp_path / "merged.db.lock").exists()


def test_the_merged_errors_do_not_depend_on_shard_order(monkeypatch, repo, tmp_path):
    """baseline은 첫 샤드 것이지만 오류는 합집합 — 순서로 ok/partial이 갈리면 안 된다."""
    databases = run_shards(monkeypatch, repo, tmp_path, three_candidates(), 2)
    with Checkpoint(databases[1], resume=True) as store:
        baseline = store.get("baseline")
        baseline["errors"].append("Skipped 2 flow sinks without an enclosing method")
        store.set("baseline", baseline)
    for name, order in (("forward", databases), ("reverse", databases[::-1])):
        merged = tmp_path / f"{name}.db"
        merge_shards(merged, order)
        export = tmp_path / name
        export.mkdir()
        code, exported, _ = run_cli(
            monkeypatch, repo, export, {}, "--checkpoint", str(merged), "--export-only"
        )
        assert code == 2 and "without an enclosing method" in exported["error"], name


def test_the_same_shard_twice_is_refused(monkeypatch, repo, tmp_path):
    databases = run_shards(monkeypatch, repo, tmp_path, three_candidates(), 2)
    with pytest.raises(ValueError, match="twice"):
        merge_shards(tmp_path / "merged.db", [databases[0], databases[0]])


def test_the_harness_refuses_a_shard_fragment(monkeypatch, repo, tmp_path):
    from src.adapters.slices import SliceInputError, load_slices

    run_shards(monkeypatch, repo, tmp_path, three_candidates(), 2)
    with pytest.raises(SliceInputError, match="샤드 조각"):
        load_slices(tmp_path / "shard0" / "slices.json")


def test_a_file_symlink_is_skipped_not_fatal(monkeypatch, repo, tmp_path):
    """#362: a file symlink is refused at read time (O_NOFOLLOW -> ELOOP). It used to count as a
    failed read and `Cannot checkpoint incomplete source reads` voided the run (libmspack,
    accel-ppp). Now it is skipped and counted like a directory symlink and the run goes on."""
    linked = repo / "linked.c"
    linked.write_text("int x;\n", encoding="utf-8")
    is_link = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == linked or is_link(path))
    real_read = slicer._read_no_follow

    def refuse_links(path):  # what O_NOFOLLOW does to a real symlink
        if path == linked:
            raise OSError(40, "Too many levels of symbolic links")
        return real_read(path)

    monkeypatch.setattr(slicer, "_read_no_follow", refuse_links)
    data = payloads(case_a=[candidate()], flow_count=2)
    _, result, _ = run_cli(monkeypatch, repo, tmp_path, data, *checkpoint_options(tmp_path))
    assert result["counters"]["files_symlink_skipped"] == 1
    assert result["counters"]["files_read_failed"] == 0
    assert "incomplete source reads" not in (result.get("error") or "")
    assert "File symlink not read: linked.c" in result["error"]
    assert result["slices"]


def test_export_gives_unfinished_candidates_the_degraded_slice(monkeypatch, repo, tmp_path):
    """#364: a merged export with a shard missing keeps every candidate. The missing shard's
    candidate comes out as the anchor-only degraded slice an unsharded run would give it after a
    poison streak, instead of vanishing. Still partial, still reported as unfinished."""
    databases = run_shards(monkeypatch, repo, tmp_path, three_candidates(), 3)
    merged = tmp_path / "merged.db"
    merge_shards(merged, databases[:2])
    with Checkpoint(merged, resume=True) as store:
        candidates = store.get("candidates")
    missing = candidates[2]
    export = tmp_path / "export"
    export.mkdir()
    code, exported, _ = run_cli(
        monkeypatch, repo, export, {}, "--checkpoint", str(merged), "--export-only"
    )
    assert code == 2 and exported["status"] == "partial"
    assert "unfinished candidates (1)" in exported["error"]
    assert exported["counters"]["candidates_degraded"] == 1
    # exported = processed: the completion metric must match an unsharded degraded run (#365)
    assert exported["counters"]["candidates_not_processed"] == 0
    keys = {k for s in exported["slices"] for k in s["members"]}
    assert keys == {c["candidate_key"] for c in candidates}
    entry = missing["entry"]
    assert any(
        {"file": entry["file"], "line": entry["line"]} in s["lines"]
        for s in exported["slices"]
        if missing["candidate_key"] in s["members"]
    )


def _saving(cpg: Path, content: bytes = b"saved-graph", *, size=None):  # noqa: ANN202
    """Stand-in for Joern's `save` + copy: writes the graph the query names, reports its size.

    Real Joern reports the size as a JSON *string*: `Files.size` is a Scala Long and ujson writes
    a Long as a string -- 2026-10-02 vast, Joern 4.0.605, ReadStat prepare: `"server_bytes":
    "4646430"` (#400). The default here is that real form; `size` overrides it."""
    def save(query):  # noqa: ANN001, ANN202
        assert cpg.resolve().as_posix() in query
        cpg.write_bytes(content)
        reported = str(len(content)) if size is None else size
        return response("save_cpg", {"saved": True, "bytes": reported})
    return save


def _prepare(monkeypatch, repo, tmp_path, data, *, save=None, extra=()):  # noqa: ANN001, ANN202
    """#368: one `--prepare-only` run -> (checkpoint, saved CPG path, code, result, transport)."""
    folder = tmp_path / "prep"
    folder.mkdir(parents=True)
    cpg = folder / "cpg.bin"
    data["save_cpg"] = _saving(cpg) if save is None else save
    code, result, transport = run_cli(
        monkeypatch, repo, folder, data, "--checkpoint", str(folder / "state.db"),
        "--prepare-only", "--cpg-out", str(cpg), *extra,
    )
    return folder / "state.db", cpg, code, result, transport


def _seed(state: Path, folder: Path) -> Path:
    """Copy the prepared checkpoint as one shard's starting state."""
    import shutil

    folder.mkdir(parents=True)
    shutil.copy(state, folder / "state.db")
    return folder / "state.db"


def _shard_on(monkeypatch, repo, folder, cpg, *extra):  # noqa: ANN001, ANN202
    """A shard on the prepared graph; case_a/case_b are absent, so a rediscovery would fail."""
    data = three_candidates()
    del data["case_a"], data["case_b"]
    return run_cli(
        monkeypatch, repo, folder, data, "--checkpoint", str(folder / "state.db"),
        "--resume", "--cpg", str(cpg), *extra,
    )


def test_prepare_only_discovers_once_and_saves_the_graph(monkeypatch, repo, tmp_path):
    """#368: the prepare run parses and discovers, saves the CPG, and slices nothing. The
    `prepared` record (size+SHA-256 the client read back) is written only after the save."""
    state, cpg, code, result, transport = _prepare(monkeypatch, repo, tmp_path, three_candidates())
    assert code == 2 and result["status"] == "partial"
    assert result["prepare_only"] is True
    assert "Prepare-only" in result["error"]
    assert {"import", "case_a", "case_b", "save_cpg"} <= set(transport.stages)
    assert not {"flows", "backward", "context"} & set(transport.stages)
    content = cpg.read_bytes()
    expected = {
        "path": cpg.resolve().as_posix(),
        "server_bytes": len(content),
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "cpg_seconds": result["counters"]["cpg_seconds"],
    }
    assert result["prepared_cpg"] == expected
    with Checkpoint(state, resume=True) as store:
        assert len(store.get("candidates")) == 3
        assert store.get("baseline") is not None
        assert "prepare_only" not in store.get("baseline")["result"]
        assert store.get("prepared") == expected
        assert not list(store.conn.execute("SELECT candidate_key FROM stages"))


def test_the_harness_refuses_a_prepare_only_result(monkeypatch, repo, tmp_path):
    """#371 review: the prepare output is `partial` + `slices: []` + no `shard` -- without the
    marker the loader read it as a finished run with zero slices."""
    from src.adapters.slices import SliceInputError, load_slices

    _prepare(monkeypatch, repo, tmp_path, three_candidates())
    with pytest.raises(SliceInputError, match="--prepare-only"):
        load_slices(tmp_path / "prep" / "slices.json")


@pytest.mark.parametrize("name", ['quote"d', "back\\slash", "unié"])
def test_cpg_paths_reach_scala_as_one_exact_literal(name):
    """A path goes through `literal`, not pasted between quotes: a `"` or `\\` in it must not
    end the Scala string early or change the path Joern writes/reads."""
    path = f"/work/prep/{name}/cpg.bin"
    for query in (queries.save_cpg_query(path), queries.import_cpg_query(path, "p")):
        assert path in [json.loads(s) for s in SCALA_STRING.findall(query)]


def test_shards_on_a_prepared_graph_skip_parsing_and_discovery(monkeypatch, repo, tmp_path):
    """#368: a shard on a copy of the prepared checkpoint loads the saved CPG (importCpg, not
    importCode) and never re-runs discovery -- case_a/case_b are absent from its mock, so a
    rerun would fail. Merged and exported, the shards give exactly the one-run slices."""
    whole = tmp_path / "whole"
    whole.mkdir()
    _, single, _ = run_cli(monkeypatch, repo, whole, three_candidates())
    state, cpg, _, _, _ = _prepare(monkeypatch, repo, tmp_path, three_candidates())
    databases = []
    for index in range(2):
        databases.append(_seed(state, tmp_path / f"seeded{index}"))
        code, result, transport = _shard_on(
            monkeypatch, repo, databases[-1].parent, cpg, "--shard", f"{index}/2"
        )
        assert code == 2 and result["shard"] == {"index": index, "count": 2}
        assert "prepare_only" not in result
        assert not {"case_a", "case_b"} & set(transport.stages)
        imports = [q for q in (json.loads(r.data)["query"] for r, _ in transport.requests)
                   if '"__SLICER_import__"' in q]
        assert len(imports) == 1 and "importCpg(" in imports[0] and "importCode" not in imports[0]
    merged = tmp_path / "merged.db"
    assert merge_shards(merged, databases) == {"shards": 2, "candidates": 3, "complete": 3}
    with Checkpoint(merged, resume=True) as store:
        # #371 review: every seed carries the prepare's attempt -- it is one attempt, not two.
        assert len(store.attempts()) == 3
    export = tmp_path / "export"
    export.mkdir()
    _, exported, _ = run_cli(
        monkeypatch, repo, export, {}, "--checkpoint", str(merged), "--export-only"
    )
    assert exported["status"] == "ok"
    assert "prepare_only" not in exported
    assert exported["slices"] == single["slices"]


def test_a_shard_keeps_its_own_cpg_time_and_server(monkeypatch, repo, tmp_path):
    """#371 review: the discovery (counters, errors) is the prepare's, but `server` and
    `cpg_seconds` are this shard's own measurements -- the prepare's parse time must not be
    reported as the shard's. Sentinels in the seed tell the two apart."""
    state, cpg, _, _, _ = _prepare(monkeypatch, repo, tmp_path, three_candidates())
    seeded = _seed(state, tmp_path / "seeded")
    with Checkpoint(seeded, resume=True) as store:
        baseline = store.get("baseline")
        baseline["result"]["counters"]["cpg_seconds"] = 12345.0
        baseline["result"]["counters"]["candidates_case_a"] = 777
        baseline["result"]["server"] = {"sentinel": "prepare"}
        baseline["errors"].append("sentinel prepare warning")
        store.set("baseline", baseline)
    _, result, _ = _shard_on(monkeypatch, repo, seeded.parent, cpg)
    assert result["counters"]["candidates_case_a"] == 777
    assert "sentinel prepare warning" in result["error"]
    assert result["server"] != {"sentinel": "prepare"}
    assert result["counters"]["cpg_seconds"] != 12345.0
    assert result["prepared_cpg"]["sha256"] == hashlib.sha256(cpg.read_bytes()).hexdigest()


@pytest.mark.parametrize(("extra", "message"), [
    (["--prepare-only", "{ck}"], "--prepare-only needs"),                     # no --cpg-out
    (["--prepare-only", "--cpg-out", "{out}"], "--prepare-only needs"),       # no --checkpoint
    (["--prepare-only", "--cpg-out", "{out}", "{ck}", "--shard", "0/2"], "--prepare-only needs"),
    (["--prepare-only", "--cpg-out", "{out}", "{ck}", "--resume"], "--prepare-only needs"),
    (["--prepare-only", "--cpg-out", "{out}", "{ck}", "--export-only"], "--prepare-only needs"),
    (["--prepare-only", "--cpg-out", "{out}", "{ck}", "--cpg", "{cpg}"], "--prepare-only needs"),
    (["--cpg-out", "{out}", "{ck}"], "--cpg-out only goes"),
    (["--cpg", "{cpg}", "{ck}"], "--cpg needs"),                              # without --resume
    (["--cpg", "{cpg}", "{ck}", "--resume", "--export-only"], "--cpg needs"),
    (["--cpg", "{out}", "{ck}", "--resume"], "--cpg is not a file"),
    (["--prepare-only", "--cpg-out", "{out}", "--checkpoint", "{cpg}"], "needs a new --checkpoint"),
    (["--prepare-only", "--cpg-out", "{cpg}", "{ck}"], "--cpg-out exists"),
    (["--prepare-only", "--cpg-out", "{state}", "{ck}"], "must be distinct"),
    (["--cpg", "{cpg}", "--checkpoint", "{cpg}", "--resume"], "must be distinct"),
])
def test_prepared_graph_options_are_refused_when_inconsistent(
    monkeypatch, repo, tmp_path, capsys, extra, message
):
    """Each combination is refused by its own check (the message), before any Joern request."""
    cpg = tmp_path / "existing.bin"
    cpg.write_bytes(b"graph")
    state = tmp_path / "state.db"
    names = {"{out}": str(tmp_path / "new.bin"), "{cpg}": str(cpg), "{state}": str(state)}
    argv = []
    for item in extra:
        argv += ["--checkpoint", str(state)] if item == "{ck}" else [names.get(item, item)]
    transport = MockTransport({})
    monkeypatch.setattr(slicer, "urlopen", transport)
    with pytest.raises(SystemExit):
        slicer.main(["--repo", str(repo), "--out", str(tmp_path / "s.json"), *argv])
    assert message in capsys.readouterr().err
    assert transport.stages == [] and not state.exists()


def test_a_shard_on_a_checkpoint_without_discovery_fails(monkeypatch, repo, tmp_path):
    """--cpg on a checkpoint whose prepare run died before discovery (case_a failed) has no
    candidate list to reuse -> the shard says so and slices nothing, it does not run its own
    discovery behind the prepared graph's back."""
    data = three_candidates()
    data["case_a"] = response("case_a", [], success=False)
    state, cpg, _, prepared, _ = _prepare(monkeypatch, repo, tmp_path, data)
    assert "Prepare-only" not in (prepared.get("error") or "")
    assert not cpg.exists()
    cpg.write_bytes(b"some graph")
    seeded = _seed(state, tmp_path / "seeded")
    code, result, transport = _shard_on(monkeypatch, repo, seeded.parent, cpg)
    assert code != 0
    assert "--prepare-only" in result["error"]
    assert transport.stages == []


def test_a_seed_whose_save_failed_is_refused(monkeypatch, repo, tmp_path):
    """#371 review: discovery is committed before `save_cpg`, so a prepare whose save failed
    leaves baseline+candidates. Without the `prepared` record a shard accepted that seed."""
    state, cpg, code, prepared, _ = _prepare(
        monkeypatch, repo, tmp_path, three_candidates(),
        save=response("save_cpg", {}, success=False),
    )
    assert code != 0 and "Prepare-only" not in prepared["error"]
    with Checkpoint(state, resume=True) as store:
        assert store.get("candidates") is not None and store.get("prepared") is None
    cpg.write_bytes(b"a graph from somewhere else")
    seeded = _seed(state, tmp_path / "seeded")
    code, result, transport = _shard_on(monkeypatch, repo, seeded.parent, cpg)
    assert code != 0 and "'prepared' record" in result["error"]
    assert transport.stages == []


def test_a_cpg_that_is_not_the_prepared_one_is_refused(monkeypatch, repo, tmp_path):
    """#371 review: same path, other bytes (rewritten, or another prepare's) -> refused before
    any Joern request instead of slicing a graph the candidate list was not discovered on."""
    state, cpg, _, _, _ = _prepare(monkeypatch, repo, tmp_path, three_candidates())
    cpg.write_bytes(cpg.read_bytes() + b"!")
    seeded = _seed(state, tmp_path / "seeded")
    code, result, transport = _shard_on(monkeypatch, repo, seeded.parent, cpg)
    assert code != 0 and "is not the CPG the prepare run saved" in result["error"]
    assert transport.stages == []


def test_an_unhashed_prepared_cpg_is_said_and_refused(monkeypatch, repo, tmp_path):
    """The client could not read what Joern saved (another host): the prepare says so and leaves
    bytes/sha256 null (unmeasured, not guessed), and a shard refuses what it cannot verify."""
    def remote(query):  # noqa: ANN001, ANN202
        return response("save_cpg", {"saved": True, "bytes": 99})

    state, cpg, _, prepared, _ = _prepare(
        monkeypatch, repo, tmp_path, three_candidates(), save=remote
    )
    assert prepared["prepared_cpg"]["sha256"] is None
    assert prepared["prepared_cpg"]["bytes"] is None
    assert prepared["prepared_cpg"]["server_bytes"] == 99
    assert "prepared_cpg" in prepared["measurement_notes"]
    assert "not readable from this client" in prepared["error"]
    cpg.write_bytes(b"x" * 99)
    seeded = _seed(state, tmp_path / "seeded")
    code, result, transport = _shard_on(monkeypatch, repo, seeded.parent, cpg)
    assert code != 0 and "no SHA-256" in result["error"]
    assert transport.stages == []


def test_shards_of_two_different_prepares_do_not_merge(monkeypatch, repo, tmp_path):
    """Same repo, same candidate list, but two prepared graphs: their stages were sliced on
    different CPGs, so merging them is refused like any other cross-run merge."""
    databases = []
    for index in range(2):
        state, cpg, _, _, _ = _prepare(
            monkeypatch, repo, tmp_path / f"run{index}", three_candidates()
        )
        databases.append(_seed(state, tmp_path / f"seeded{index}"))
        _shard_on(monkeypatch, repo, databases[-1].parent, cpg, "--shard", f"{index}/2")
    with pytest.raises(ValueError, match="Shard mismatch on prepared"):
        merge_shards(tmp_path / "merged.db", databases)


def _claim_shard(monkeypatch, repo, tmp_path, data, index, count, claims):  # noqa: ANN001, ANN202
    folder = tmp_path / f"claim{index}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "state.db", run_cli(
        monkeypatch, repo, folder, data, "--checkpoint", str(folder / "state.db"),
        "--shard", f"{index}/{count}", "--claim-db", str(claims),
    )


def test_a_claim_queue_lets_an_idle_shard_take_every_open_candidate(monkeypatch, repo, tmp_path):
    """#369: with a shared queue the work is not split by position. Run one after the other,
    the first shard takes all three candidates and the second finds none left -- under p % N
    each would have sliced its own share. Merged, the result equals one unsharded run."""
    whole = tmp_path / "whole"
    whole.mkdir()
    _, single, _ = run_cli(monkeypatch, repo, whole, three_candidates())
    claims = tmp_path / "claims.db"
    first, (_, r0, t0) = _claim_shard(monkeypatch, repo, tmp_path, three_candidates(), 0, 2, claims)
    second, (_, r1, t1) = _claim_shard(monkeypatch, repo, tmp_path, three_candidates(), 1, 2,
                                       claims)
    with Checkpoint(first, resume=True) as store:
        assert len(list(store.conn.execute("SELECT candidate_key FROM stages"))) == 3
    with Checkpoint(second, resume=True) as store:
        assert list(store.conn.execute("SELECT candidate_key FROM stages")) == []
    assert "flows" not in t1.stages and "backward" not in t1.stages
    assert "claimed from the shared queue" in r0["error"]
    merged = tmp_path / "merged.db"
    assert merge_shards(merged, [first, second]) == {"shards": 2, "candidates": 3, "complete": 3}
    export = tmp_path / "export"
    export.mkdir()
    _, exported, _ = run_cli(
        monkeypatch, repo, export, {}, "--checkpoint", str(merged), "--export-only"
    )
    assert exported["slices"] == single["slices"]


def _failing(data):  # noqa: ANN001, ANN202
    """Every flows/backward query fails without poisoning: claimed, degraded, never saved."""
    failed = {"success": False, "stdout": "", "stderr": "boom"}
    return {**data, "flows": failed, "backward": failed}


def test_a_shard_resumes_its_own_claims(monkeypatch, repo, tmp_path):
    """A shard whose candidates all failed keeps them claimed: another shard (other I/N) takes
    none of them, and `--resume` of the same I/N slices them all."""
    claims = tmp_path / "claims.db"
    mine, (_, failed, _) = _claim_shard(
        monkeypatch, repo, tmp_path, _failing(three_candidates()), 0, 2, claims
    )
    assert failed["counters"]["candidates_failed"] == 3
    other, (_, _, t1) = _claim_shard(monkeypatch, repo, tmp_path, three_candidates(), 1, 2,
                                     claims)
    assert "flows" not in t1.stages and "backward" not in t1.stages
    code, resumed, _ = run_cli(
        monkeypatch, repo, mine.parent, three_candidates(), "--checkpoint", str(mine), "--resume",
        "--shard", "0/2", "--claim-db", str(claims),
    )
    assert code == 2 and resumed["counters"]["candidates_failed"] == 0
    assert merge_shards(tmp_path / "merged.db", [mine, other])["complete"] == 3


def test_a_poisoned_shard_stops_claiming_and_leaves_the_rest(monkeypatch, repo, tmp_path):
    """After a poison streak a queue shard stops taking candidates (its server is unusable)
    instead of claiming and degrading all of them; a healthy shard then slices the rest."""
    cands = [candidate("c" + str(i), i + 1) for i in range(6)]
    claims = tmp_path / "claims.db"
    bad = payloads(case_a=cands, flow_count=2)
    bad["flows"] = lambda query: {"success": True, "stdout": "java.lang.StackOverflowError",
                                  "stderr": ""}
    _, (_, poisoned, _) = _claim_shard(monkeypatch, repo, tmp_path, bad, 0, 2, claims)
    streak = slicer_pipeline.POISON_STOP_STREAK
    assert poisoned["counters"]["candidates_failed"] == streak
    healthy_db, (_, healthy, _) = _claim_shard(
        monkeypatch, repo, tmp_path, payloads(case_a=cands, flow_count=2), 1, 2, claims
    )
    with Checkpoint(healthy_db, resume=True) as store:
        done = len(list(store.conn.execute("SELECT candidate_key FROM stages")))
    assert done == len(cands) - streak


def test_a_claim_queue_needs_a_shard(repo, tmp_path):
    with pytest.raises(SystemExit):
        slicer.main(["--repo", str(repo), "--out", str(tmp_path / "s.json"),
                     "--checkpoint", str(tmp_path / "state.db"),
                     "--claim-db", str(tmp_path / "claims.db")])


def test_a_poison_streak_message_says_what_happens_to_the_rest(monkeypatch, repo, tmp_path):
    """Claim mode stops claiming (the rest stay queued); without it the rest get degraded."""
    cands = [candidate("c" + str(i), i + 1) for i in range(6)]
    bad = payloads(case_a=cands, flow_count=2)
    bad["flows"] = lambda query: {"success": True, "stdout": "java.lang.StackOverflowError",
                                  "stderr": ""}
    _, (_, queued, _) = _claim_shard(monkeypatch, repo, tmp_path, bad, 0, 2,
                                     tmp_path / "claims.db")
    assert "this shard stops claiming candidates" in queued["error"]
    assert "remaining candidates get degraded" not in queued["error"]
    plain = tmp_path / "plain"
    plain.mkdir()
    _, fixed, _ = run_cli(monkeypatch, repo, plain, bad, "--checkpoint", str(plain / "s.db"),
                          "--shard", "0/2")
    assert "remaining candidates get degraded" in fixed["error"]
    assert "stops claiming" not in fixed["error"]


def _flows_by_outcome(outcomes):  # noqa: ANN001, ANN202
    """flows reply per call: P poisoned, F failed (not poisoned), S success."""
    calls = []

    def flows(query):  # noqa: ANN001, ANN202
        calls.append(query)
        kind = outcomes[len(calls) - 1]
        if kind == "P":
            return {"success": True, "stdout": "java.lang.StackOverflowError", "stderr": ""}
        if kind == "F":
            return {"success": False, "stdout": "", "stderr": "boom"}
        return response(
            "flows",
            {
                "flow_count": 2,
                "truncated": False,
                "lines": [{"file": "main.c", "line": 5}],
                "endpoints": [],
            },
        )

    return flows


@pytest.mark.parametrize(
    ("outcomes", "failed"), [("PPSPPS", 4), ("PPFPPS", 5)], ids=["success", "plain-failure"]
)
def test_a_poison_streak_restarts_after_a_non_poisoned_candidate(
    monkeypatch, repo, tmp_path, outcomes, failed
):
    """Only CONSECUTIVE poison stops: a success or an unpoisoned failure resets the count, so
    P P S P P (or P P F P P) never reaches the streak of 3 and every candidate hits the server."""
    assert slicer_pipeline.POISON_STOP_STREAK == 3
    cands = [candidate("c" + str(i), i + 1) for i in range(len(outcomes))]
    data = payloads(case_a=cands, flow_count=2)
    data["flows"] = _flows_by_outcome(outcomes)
    _, result, transport = run_cli(monkeypatch, repo, tmp_path, data)
    assert transport.stages.count("flows") == len(outcomes)
    assert result["counters"]["candidates_failed"] == failed
    assert "consecutive poisoned candidates" not in result["error"]


def test_claims_are_keyed_by_the_candidate_key(monkeypatch, repo, tmp_path):
    from slicer_claims import read_claims

    claims = tmp_path / "claims.db"
    state, _ = _claim_shard(monkeypatch, repo, tmp_path, three_candidates(), 1, 2, claims)
    with Checkpoint(state, resume=True) as store:
        keys = {c["candidate_key"] for c in store.get("candidates")}
    assert set(read_claims(claims)) == keys
    assert {owner for owner, _ in read_claims(claims).values()} == {"1/2"}


def test_the_claim_db_runs_in_wal_mode(tmp_path):
    from slicer_claims import ClaimQueue

    queue = ClaimQueue(tmp_path / "claims.db", 0, 2)
    try:
        assert queue.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        queue.close()


def test_a_filesystem_without_wal_is_refused_loudly(monkeypatch, tmp_path):
    """SQLite returns the old mode instead of raising; that must not pass as a working queue."""
    from slicer_claims import ClaimQueue

    monkeypatch.setattr(ClaimQueue, "_bootstrap", staticmethod(lambda conn, count: "delete"))
    with pytest.raises(ValueError, match="host-local filesystem"):
        ClaimQueue(tmp_path / "claims.db", 0, 2, timeout=0.05)


def _hold_write_lock(path):  # noqa: ANN001, ANN202
    other = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    other.execute("BEGIN IMMEDIATE")
    return other


def test_a_claim_waits_out_another_shards_write_lock(tmp_path):
    """The busy timeout: a claim during another shard's brief INSERT waits, it does not fail."""
    import threading

    from slicer_claims import ClaimQueue

    queue = ClaimQueue(tmp_path / "claims.db", 0, 2)
    other = _hold_write_lock(tmp_path / "claims.db")
    release = threading.Timer(0.3, lambda: other.execute("COMMIT"))
    release.start()
    try:
        assert queue.claim("k1")
    finally:
        release.join()
        other.close()
        queue.close()


def test_a_lock_past_the_timeout_raises_instead_of_skipping_the_candidate(tmp_path):
    """Only a taken key means "not mine"; a lock error read as one would drop the candidate."""
    from slicer_claims import ClaimQueue

    queue = ClaimQueue(tmp_path / "claims.db", 0, 2, timeout=0.1)
    other = _hold_write_lock(tmp_path / "claims.db")
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            queue.claim("k1")
    finally:
        other.execute("ROLLBACK")
        other.close()
    assert queue.claim("k1")
    queue.close()


CLAIM_WORKER = """
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from slicer_claims import ClaimQueue
db, index, count, go = Path(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
Path(go + ".ready" + str(index)).touch()
while not os.path.exists(go):
    time.sleep(0.001)
queue = ClaimQueue(db, index, count)
print(json.dumps([k for k in ("k" + str(n) for n in range(40)) if queue.claim(k)]))
queue.close()
"""


def _open_at_once(folder, count):  # noqa: ANN001, ANN202
    """`count` processes open one fresh claim db together and claim k0..k39; their outputs."""
    root = str(Path(slicer.__file__).parent)
    folder.mkdir()
    go = str(folder / "go")
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", CLAIM_WORKER, root, str(folder / "claims.db"), str(i),
             str(count), go],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for i in range(count)
    ]
    try:
        for i in range(count):
            ready = Path(go + ".ready" + str(i))
            for _ in range(3000):
                if ready.exists():
                    break
                time.sleep(0.01)
        Path(go).touch()
        outputs = [proc.communicate(timeout=60) for proc in procs]
        return [(proc.returncode, *out) for proc, out in zip(procs, outputs, strict=True)]
    finally:
        for proc in procs:
            proc.kill()


def test_many_shards_open_a_fresh_claim_db_at_once(tmp_path):
    """#372 review: N processes creating the file together got `database is locked` from the
    WAL switch (no busy timeout there). All must open, and every key goes to exactly one.
    With the retry removed, 5 of 10 rounds of 12 processes lost at least one process (2026-10-02,
    local Windows, ad-hoc script outside the repo), so five rounds make the race show."""
    for round_ in range(5):
        outputs = _open_at_once(tmp_path / str(round_), 12)
        assert [err for code, _, err in outputs if code] == []
        taken = [key for _, out, _ in outputs for key in json.loads(out)]
        assert sorted(taken) == sorted("k" + str(n) for n in range(40))


def test_a_claim_db_from_another_run_is_refused(monkeypatch, repo, tmp_path):
    """#372 review: a leftover claim file made every shard of the next run find all candidates
    taken -- merge 0/N complete, no word about the file. A different shard count fails on open,
    a different candidate list before any candidate is sliced."""
    claims = tmp_path / "claims.db"
    _claim_shard(monkeypatch, repo, tmp_path / "old", three_candidates(), 0, 2, claims)
    code, other_count, transport = _claim_shard(
        monkeypatch, repo, tmp_path / "count", three_candidates(), 0, 3, claims
    )[1]
    assert code == 1 and "2 shards, not 3" in other_count["error"]
    assert transport.stages == []
    changed = payloads(case_a=[candidate("fresh", 7)], flow_count=1)
    _, (_, other_run, transport) = _claim_shard(
        monkeypatch, repo, tmp_path / "run", changed, 0, 2, claims
    )
    assert "started by another run" in other_run["error"]
    assert "flows" not in transport.stages


def test_a_claim_db_gets_its_parent_and_a_bad_one_is_a_result_error(monkeypatch, repo, tmp_path):
    nested = tmp_path / "a" / "b" / "claims.db"
    code, _, _ = _claim_shard(monkeypatch, repo, tmp_path / "ok", three_candidates(), 0, 2,
                              nested)[1]
    assert code == 2 and nested.is_file()
    folder = tmp_path / "dir"
    code, result, _ = _claim_shard(monkeypatch, repo, folder, three_candidates(), 0, 2,
                                   tmp_path)[1]
    assert code == 1 and result["error"]


def test_the_claim_db_is_not_another_output(repo, tmp_path):
    with pytest.raises(SystemExit):
        slicer.main(["--repo", str(repo), "--out", str(tmp_path / "s.json"),
                     "--checkpoint", str(tmp_path / "state.db"), "--shard", "0/2",
                     "--claim-db", str(tmp_path / "state.db")])


def test_the_merge_tool_names_the_claim_owner_of_each_incomplete_candidate(
    monkeypatch, repo, tmp_path, capsys
):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "merge_tool", Path(slicer.__file__).parent.parent / "merge_slicer_shards.py"
    )
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    claims = tmp_path / "claims.db"
    mine, _ = _claim_shard(monkeypatch, repo, tmp_path, _failing(three_candidates()), 0, 2,
                           claims)
    other, _ = _claim_shard(monkeypatch, repo, tmp_path, three_candidates(), 1, 2, claims)
    with Checkpoint(mine, resume=True) as store:
        keys = [c["candidate_key"] for c in store.get("candidates")]
    capsys.readouterr()
    assert tool.main(["x", "--claim-db", str(claims), str(tmp_path / "m.db"), str(mine),
                      str(other)]) == 2
    err = capsys.readouterr().err
    assert "incomplete: 3" in err
    for key in keys:
        assert f"{key}  claimed by 0/2" in err
    assert tool.main(["x", str(tmp_path / "plain.db"), str(mine), str(other)]) == 2
    err = capsys.readouterr().err
    assert all(f"  {key}\n" in err for key in keys)


@pytest.mark.parametrize("size", [None, "int"])
def test_prepare_accepts_the_size_as_joern_writes_it(monkeypatch, repo, tmp_path, size):
    """#400: a string size (what real Joern sends) and an int size both pass and are recorded as
    an int; before the fix the string form failed every real prepare."""
    folder = tmp_path / "x"
    folder.mkdir()
    cpg_holder = folder / "prep" / "cpg.bin"
    save = _saving(cpg_holder, size=None if size is None else len(b"saved-graph"))
    state, cpg, code, result, _ = _prepare(monkeypatch, repo, folder, three_candidates(), save=save)
    assert code == 2 and result["prepared_cpg"]["server_bytes"] == len(b"saved-graph")
    with Checkpoint(state, resume=True) as store:
        assert store.get("prepared")["server_bytes"] == len(b"saved-graph")


@pytest.mark.parametrize("size", ["999", "12abc", "", "-11"])
def test_prepare_still_refuses_a_size_that_is_not_the_file(monkeypatch, repo, tmp_path, size):
    """Only a plain digit string is read as a number; anything else stays a mismatch."""
    folder = tmp_path / "x"
    folder.mkdir()
    save = _saving(folder / "prep" / "cpg.bin", size=size)
    state, _, code, result, _ = _prepare(monkeypatch, repo, folder, three_candidates(), save=save)
    assert code != 0 and "it is not the same file" in str(result.get("error"))
    with Checkpoint(state, resume=True) as store:
        assert store.get("prepared") is None


# #377 (b) -- --priority-files: order only, bound into the checkpoint.


def in_file(item, name):
    """candidate() moved to another file, same line/symbol."""
    moved = json.loads(json.dumps(item))
    moved["file"] = name
    moved["scope"]["file"] = name
    moved["anchors"] = [{**a, "file": name} for a in moved["anchors"]]
    return moved


@pytest.fixture(name="two_file_repo")
def two_file_repo_fixture(repo):
    (repo / "a.c").write_text((repo / "main.c").read_text(encoding="utf-8"), encoding="utf-8")
    return repo


def two_file_payloads():
    # a.c sorts before main.c, so without a list a.c's candidate comes first.
    data = payloads(case_a=[in_file(candidate(), "a.c"), candidate("other", 3)], flow_count=1)
    data["import"] = response(
        "import", {"imported": True}, logs=report([("a.c", "yes", "yes"), ("main.c", "yes", "yes")])
    )
    data["inventory"] = response("inventory", {
        "files": ["a.c", "main.c"], "methods_internal": 2, "calls": 2, "calls_unresolved": 0,
        "unknown_nodes": 0, "flow_sinks_without_method": 0, "overlays": ["ossdataflow"],
        "engine_config": {
            "maxCallDepth": 4, "maxArgsToAllow": 1000, "maxOutputArgsExpansion": 1000,
        },
    })
    return data


def test_prioritize_moves_listed_files_first_and_keeps_order_and_ids(repo):
    from slicer_candidates import prioritize

    rows = [in_file(candidate("x", 1), "a.c"), in_file(candidate("y", 2), "b.c"),
            candidate("z", 3), in_file(candidate("w", 4), "b.c")]
    ordered = collect({"A": rows}, repo)
    assert [c["entry"]["file"] for c in ordered] == ["a.c", "b.c", "b.c", "main.c"]
    moved, first, matched = prioritize(ordered, {"main.c", "b.c", "gone.c"})
    assert first == 3 and matched == {"main.c", "b.c"}
    # each tier keeps collect()'s relative order; ids are collect()'s, not the new positions
    assert [c["id"] for c in moved] == [ordered[i]["id"] for i in (1, 2, 3, 0)]
    assert prioritize(ordered, set()) == (ordered, 0, set())


def test_an_anchor_in_a_listed_file_is_enough(repo):
    from slicer_candidates import prioritize

    row = in_file(candidate(), "a.c")
    row["anchors"].append({"file": "main.c", "line": 5})
    # q (a.c:5) sorts after row (a.c:1); only row has an anchor in main.c
    ordered = collect({"A": [in_file(candidate("q", 5), "a.c"), row]}, repo)
    assert [c["entry"]["symbol"] for c in ordered] == ["entry", "q"]
    moved, first, matched = prioritize(ordered, {"main.c"})
    assert first == 1 and moved == ordered and matched == {"main.c"}
    moved, first, _ = prioritize(ordered[::-1], {"main.c"})
    assert first == 1 and [c["entry"]["symbol"] for c in moved] == ["entry", "q"]


def test_priority_files_reorder_the_bound_candidates(monkeypatch, two_file_repo, tmp_path):
    listing = tmp_path / "priority.txt"
    listing.write_text("# hint\n\n./main.c\n", encoding="utf-8")
    database = tmp_path / "state.db"
    code, result, _ = run_cli(
        monkeypatch, two_file_repo, tmp_path, two_file_payloads(),
        "--checkpoint", str(database), "--priority-files", str(listing),
    )
    assert code == 0, result.get("error")
    assert result["priority"] == {
        "files": 1, "files_with_candidates": 1, "first_tier_candidates": 1, "candidates": 2,
        "matched_files": ["main.c"], "unmatched_files": [],
    }
    assert result["settings"]["priority_files"] == {"count": 1, "sha256": digest(["main.c"])}
    assert "priority_files" not in result["measurement_notes"]
    with Checkpoint(database, resume=True) as store:
        files = [c["entry"]["file"] for c in store.get("candidates")]
    assert files == ["main.c", "a.c"]


def test_without_priority_files_nothing_changes(monkeypatch, two_file_repo, tmp_path):
    database = tmp_path / "state.db"
    _, result, _ = run_cli(
        monkeypatch, two_file_repo, tmp_path, two_file_payloads(), "--checkpoint", str(database)
    )
    assert "priority" not in result and result["settings"]["priority_files"] is None
    with Checkpoint(database, resume=True) as store:
        files = [c["entry"]["file"] for c in store.get("candidates")]
    assert files == ["a.c", "main.c"]


def test_the_slices_do_not_depend_on_the_list(monkeypatch, two_file_repo, tmp_path):
    """A complete run with no failed candidate: the same slices with or without a list.

    Only that case. A run cut short, or one where a poison streak degrades the rest
    (POISON_STOP_STREAK), can degrade different candidates depending on the order.
    """
    listing = tmp_path / "priority.txt"
    listing.write_text("main.c\n", encoding="utf-8")
    outputs = []
    for name, extra in (("plain", ()), ("listed", ("--priority-files", str(listing)))):
        folder = tmp_path / name
        folder.mkdir()
        code, result, _ = run_cli(
            monkeypatch, two_file_repo, folder, two_file_payloads(), *extra
        )
        assert code == 0, result.get("error")
        assert result["counters"]["candidates_degraded"] == 0
        outputs.append(result["slices"])
    assert outputs[0] and outputs[0] == outputs[1]


def test_shards_with_different_lists_are_not_merged(monkeypatch, two_file_repo, tmp_path):
    databases = []
    for index, listed in enumerate(("main.c\n", "a.c\n")):
        folder = tmp_path / f"shard{index}"
        folder.mkdir()
        listing = folder / "priority.txt"
        listing.write_text(listed, encoding="utf-8")
        databases.append(folder / "state.db")
        code, result, _ = run_cli(
            monkeypatch, two_file_repo, folder, two_file_payloads(),
            "--checkpoint", str(databases[-1]), "--shard", f"{index}/2",
            "--priority-files", str(listing),
        )
        # as run_shards: a shard's own output is a fraction of the run
        assert code == 2 and result["status"] == "partial", result.get("error")
    contracts = []
    for database in databases:
        with Checkpoint(database, resume=True) as store:
            contracts.append(store.get("run_contract")["settings"]["priority_files"])
    assert contracts == [
        {"count": 1, "sha256": digest(["main.c"])}, {"count": 1, "sha256": digest(["a.c"])},
    ]
    # run_contract is compared before candidates (SHARD_SHARED_KEYS), so the settings entry is
    # what refuses this merge, not the reordered candidate list.
    with pytest.raises(ValueError, match="Shard mismatch on run_contract"):
        merge_shards(tmp_path / "merged.db", databases)


def test_resuming_with_another_list_is_a_contract_mismatch(monkeypatch, two_file_repo, tmp_path):
    database = tmp_path / "state.db"
    listing = tmp_path / "priority.txt"
    listing.write_text("main.c\n", encoding="utf-8")
    code, _, _ = run_cli(
        monkeypatch, two_file_repo, tmp_path, two_file_payloads(),
        "--checkpoint", str(database), "--priority-files", str(listing),
    )
    assert code == 0
    listing.write_text("a.c\n", encoding="utf-8")
    code, result, _ = run_cli(
        monkeypatch, two_file_repo, tmp_path, two_file_payloads(),
        "--checkpoint", str(database), "--resume", "--priority-files", str(listing),
    )
    assert code != 0 and "Checkpoint mismatch: run_contract" in result["error"]


def test_the_list_is_read_as_collect_spells_files(two_file_repo, tmp_path):
    listing = tmp_path / "priority.txt"
    absolute = (two_file_repo / "main.c").resolve()
    # BOM first: plain utf-8 kept it on line 1, so that path never matched
    listing.write_bytes(
        "\ufeffa.c\n".encode()
        + f"{absolute}\n  ./sub//x.c  \nsub\\y.c\n# skipped\nz.c # kept\n".encode()
    )
    assert slicer.read_priority_files(listing, two_file_repo) == {
        "a.c", "main.c", "sub/x.c", "sub/y.c", "z.c # kept",
    }


def test_a_bom_and_an_absolute_path_still_reorder(monkeypatch, two_file_repo, tmp_path):
    listing = tmp_path / "priority.txt"
    absolute = (two_file_repo / "main.c").resolve()
    listing.write_bytes(f"\ufeff{absolute}\n".encode())
    database = tmp_path / "state.db"
    code, result, _ = run_cli(
        monkeypatch, two_file_repo, tmp_path, two_file_payloads(),
        "--checkpoint", str(database), "--priority-files", str(listing),
    )
    assert code == 0, result.get("error")
    assert result["priority"]["first_tier_candidates"] == 1
    with Checkpoint(database, resume=True) as store:
        assert [c["entry"]["file"] for c in store.get("candidates")] == ["main.c", "a.c"]


def test_a_list_that_matches_nothing_is_noted_not_failed(monkeypatch, two_file_repo, tmp_path):
    listing = tmp_path / "priority.txt"
    listing.write_text("nope.c\n", encoding="utf-8")
    code, result, _ = run_cli(
        monkeypatch, two_file_repo, tmp_path, two_file_payloads(),
        "--priority-files", str(listing),
    )
    assert code == 0, result.get("error")
    assert result["priority"]["files_with_candidates"] == 0
    note = result["measurement_notes"]["priority_files"]
    assert "matched no candidate" in note and "default order" in note


def test_listed_files_without_candidates_are_counted(monkeypatch, two_file_repo, tmp_path):
    listing = tmp_path / "priority.txt"
    listing.write_text("main.c\nnope.c\ngone.c\n", encoding="utf-8")
    code, result, _ = run_cli(
        monkeypatch, two_file_repo, tmp_path, two_file_payloads(),
        "--priority-files", str(listing),
    )
    assert code == 0, result.get("error")
    assert result["priority"]["files_with_candidates"] == 1
    assert result["measurement_notes"]["priority_files"].startswith("2 of 3 listed files")


def test_priority_files_are_refused_with_export_only_or_unreadable(repo, tmp_path):
    listing = tmp_path / "priority.txt"
    listing.write_text("main.c\n", encoding="utf-8")
    base = ["--repo", str(repo), "--out", str(tmp_path / "s.json")]
    with pytest.raises(SystemExit):
        slicer.main([*base, "--checkpoint", str(tmp_path / "state.db"), "--export-only",
                     "--priority-files", str(listing)])
    with pytest.raises(SystemExit):
        slicer.main([*base, "--priority-files", str(tmp_path / "missing.txt")])


def case_a_query(transport):
    """The case_a discovery query as it was sent, with the client's payload path put back to the
    token `candidates_a_query` carries, so it compares to the builder's own text."""
    for request, _ in transport.requests:
        query = json.loads(request.data)["query"]
        if '"__SLICER_case_a__"' in query:
            return re.sub(r'Paths\.get\("[^"]*"\)', f"Paths.get({queries.OUT_PATH_TOKEN})", query)
    raise AssertionError("no case_a query was sent")


def test_priority_case_a_sends_the_list_to_the_case_a_query(monkeypatch, two_file_repo, tmp_path):
    listing = tmp_path / "priority.txt"
    listing.write_text("# hint\n\n./main.c\n", encoding="utf-8")
    code, result, transport = run_cli(
        monkeypatch, two_file_repo, tmp_path, two_file_payloads(),
        "--priority-files", str(listing), "--priority-case-a",
    )
    assert code == 0, result.get("error")
    # the list as the run normalized it (`./main.c` -> `main.c`) and this run's repo prefix
    expected = queries.candidates_a_query(["main.c"], two_file_repo.resolve().as_posix())
    assert case_a_query(transport) == expected
    assert 'val prioFiles = List("main.c").mkString.split("\\n").toSet' in expected
    assert result["settings"]["priority_case_a"] is True
    assert result["settings"]["priority_files"] == {"count": 1, "sha256": digest(["main.c"])}


def test_a_priority_list_without_the_flag_leaves_discovery_alone(
    monkeypatch, two_file_repo, tmp_path
):
    """`--priority-files` alone orders only: the case_a query is the default one, to the byte."""
    listing = tmp_path / "priority.txt"
    listing.write_text("main.c\n", encoding="utf-8")
    code, result, transport = run_cli(
        monkeypatch, two_file_repo, tmp_path, two_file_payloads(), "--priority-files", str(listing)
    )
    assert code == 0, result.get("error")
    assert case_a_query(transport) == queries.candidates_a_query()
    assert result["settings"]["priority_case_a"] is False


def test_priority_case_a_is_refused_without_a_list(repo, tmp_path, capsys):
    with pytest.raises(SystemExit):
        slicer.main(["--repo", str(repo), "--out", str(tmp_path / "s.json"), "--priority-case-a"])
    assert "--priority-case-a needs --priority-files" in capsys.readouterr().err


def test_priority_case_a_is_refused_with_a_list_that_names_no_file(repo, tmp_path, capsys):
    """A list of comments and blank lines is a list, but it names nothing: the discovery would be
    the default one while `settings.priority_case_a` says it was steered. Refused, like no list."""
    listing = tmp_path / "priority.txt"
    listing.write_text("# only a note\n\n   \n", encoding="utf-8")
    out = tmp_path / "s.json"
    with pytest.raises(SystemExit):
        slicer.main([
            "--repo", str(repo), "--out", str(out),
            "--priority-files", str(listing), "--priority-case-a",
        ])
    assert "--priority-case-a needs a --priority-files that names a file" in capsys.readouterr().err
    assert not out.exists()  # refused before any run state was written


# #377 (b) / D97 -- --description: the task description names files for the same priority set.


def description_file(tmp_path, text="A crash in main when parsing."):
    path = tmp_path / "description.txt"
    path.write_text(text, encoding="utf-8")
    return path


def test_description_prioritizes_the_file_it_names(monkeypatch, two_file_repo, tmp_path):
    text = "A crash in main when parsing."
    database = tmp_path / "state.db"
    code, result, transport = run_cli(
        monkeypatch, two_file_repo, tmp_path, two_file_payloads(),
        "--checkpoint", str(database), "--description", str(description_file(tmp_path, text)),
    )
    assert code == 0, result.get("error")
    assert result["priority"]["matched_files"] == ["main.c"]
    assert result["settings"]["priority_files"] == {"count": 1, "sha256": digest(["main.c"])}
    assert result["settings"]["description_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    # order only: --priority-case-a is not turned on by the description
    assert result["settings"]["priority_case_a"] is False
    assert case_a_query(transport) == queries.candidates_a_query()
    with Checkpoint(database, resume=True) as store:
        files = [c["entry"]["file"] for c in store.get("candidates")]
    assert files == ["main.c", "a.c"]


def test_description_is_united_with_priority_files(monkeypatch, two_file_repo, tmp_path):
    listing = tmp_path / "priority.txt"
    listing.write_text("a.c\n", encoding="utf-8")
    code, result, _ = run_cli(
        monkeypatch, two_file_repo, tmp_path, two_file_payloads(),
        "--priority-files", str(listing), "--description", str(description_file(tmp_path)),
    )
    assert code == 0, result.get("error")
    assert result["settings"]["priority_files"] == {"count": 2, "sha256": digest(["a.c", "main.c"])}


def test_a_description_that_names_no_file_leaves_the_order_alone(
    monkeypatch, two_file_repo, tmp_path, capsys
):
    database = tmp_path / "state.db"
    code, result, _ = run_cli(
        monkeypatch, two_file_repo, tmp_path, two_file_payloads(), "--checkpoint", str(database),
        "--description", str(description_file(tmp_path, "Something unrelated overflows.")),
    )
    assert code == 0, result.get("error")
    assert result["priority"]["matched_files"] == [] and result["priority"]["files"] == 0
    assert "matched no candidate" in result["measurement_notes"]["priority_files"]
    assert "matched no candidate" in capsys.readouterr().err
    with Checkpoint(database, resume=True) as store:
        files = [c["entry"]["file"] for c in store.get("candidates")]
    assert files == ["a.c", "main.c"]


def test_without_a_description_the_settings_have_no_description_key(
    monkeypatch, two_file_repo, tmp_path
):
    _, result, _ = run_cli(monkeypatch, two_file_repo, tmp_path, two_file_payloads())
    assert "description_sha256" not in result["settings"]


def test_description_must_be_one_readable_file(repo, tmp_path, capsys):
    base = ["--repo", str(repo), "--out", str(tmp_path / "s.json")]
    with pytest.raises(SystemExit):
        slicer.main([*base, "--description", str(tmp_path)])
    assert "--description is not a file" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        slicer.main([*base, "--checkpoint", str(tmp_path / "state.db"), "--export-only",
                     "--description", str(description_file(tmp_path))])


def test_priority_case_a_accepts_a_description_that_matches_a_file(
    monkeypatch, two_file_repo, tmp_path, capsys
):
    code, result, transport = run_cli(
        monkeypatch, two_file_repo, tmp_path, two_file_payloads(),
        "--description", str(description_file(tmp_path)), "--priority-case-a",
    )
    assert code == 0, result.get("error")
    assert case_a_query(transport) == queries.candidates_a_query(
        ["main.c"], two_file_repo.resolve().as_posix()
    )
    assert result["settings"]["priority_case_a"] is True
    # a description that matches nothing steers nothing, so the flag is refused like an empty list
    with pytest.raises(SystemExit):
        slicer.main([
            "--repo", str(two_file_repo), "--out", str(tmp_path / "s2.json"),
            "--description", str(description_file(tmp_path, "Nothing named here.")),
            "--priority-case-a",
        ])
    assert "or a --description that matches one" in capsys.readouterr().err


def test_shards_with_and_without_a_description_are_not_merged(
    monkeypatch, two_file_repo, tmp_path
):
    databases = []
    for index, extra in enumerate(([], ["--description"])):
        folder = tmp_path / f"shard{index}"
        folder.mkdir()
        databases.append(folder / "state.db")
        if extra:
            extra.append(str(description_file(folder)))
        code, result, _ = run_cli(
            monkeypatch, two_file_repo, folder, two_file_payloads(),
            "--checkpoint", str(databases[-1]), "--shard", f"{index}/2", *extra,
        )
        assert code == 2, result.get("error")
    with pytest.raises(ValueError, match="Shard mismatch on run_contract"):
        merge_shards(tmp_path / "merged.db", databases)


def test_resuming_without_the_priority_case_a_flag_is_a_contract_mismatch(
    monkeypatch, two_file_repo, tmp_path
):
    """The flag changes which candidates exist, so it is part of the run contract: a shard or a
    resume that forgets it is refused and the message names the setting."""
    database = tmp_path / "state.db"
    listing = tmp_path / "priority.txt"
    listing.write_text("main.c\n", encoding="utf-8")
    first = ("--checkpoint", str(database), "--priority-files", str(listing))
    code, _, _ = run_cli(
        monkeypatch, two_file_repo, tmp_path, two_file_payloads(), *first, "--priority-case-a"
    )
    assert code == 0
    code, result, _ = run_cli(
        monkeypatch, two_file_repo, tmp_path, two_file_payloads(), *first, "--resume"
    )
    assert code != 0 and "Checkpoint mismatch: run_contract" in result["error"]
    assert "settings.priority_case_a" in result["error"]


def test_resuming_without_the_import_include_root_flag_is_a_contract_mismatch(
    monkeypatch, repo, tmp_path
):
    """The include root changes the graph c2cpg builds, so the run contract carries it: a resume
    (or a shard) that forgets it is refused, and the message names the setting."""
    options = checkpoint_options(tmp_path)
    data = payloads(case_a=[candidate()])
    code, _, _ = run_cli(monkeypatch, repo, tmp_path, data, *options, "--import-include-root")
    assert code == 0
    code, result, _ = run_cli(monkeypatch, repo, tmp_path, data, *options, "--resume")
    assert code != 0 and "Checkpoint mismatch: run_contract" in result["error"]
    assert "settings.import_include_root" in result["error"]


def test_the_case_a_cap_note_follows_the_order_the_run_used(monkeypatch, two_file_repo, tmp_path):
    """The note says how the cut was ordered: with the flag on the listed files came first, and
    with it off the note keeps the words it always had."""
    monkeypatch.setattr(queries, "CASE_A_CANDIDATE_CAP", 1)
    listing = tmp_path / "priority.txt"
    listing.write_text("main.c\n", encoding="utf-8")
    on = ("--priority-files", str(listing), "--priority-case-a")
    notes = []
    for name, extra in (("off", ()), ("on", on)):
        folder = tmp_path / name
        folder.mkdir()
        _, result, _ = run_cli(monkeypatch, two_file_repo, folder, two_file_payloads(), *extra)
        notes.append(result["measurement_notes"]["candidates_case_a"])
    assert "api: write-context methods first, then traversal order" in notes[0]
    assert "priority" not in notes[0]
    assert "api: priority-file methods first, then write-context methods" in notes[1]


def flows_file_order(transport):
    """The repo file of each flows query, in the order they were sent to the server.

    The flows call carries the candidate's scope list -- `List(("a.c", "entry", 1))` -- so the
    first tuple element is the candidate's file. This reads the actual EXECUTION order, not the
    order the checkpoint was bound in (HIGH-1: a mutation that reorders the loop but keeps the
    binding leaves the checkpoint looking applied while the queries run in a different order)."""
    order = []
    for request, _ in transport.requests:
        query = json.loads(request.data)["query"]
        if '"__SLICER_flows__"' in query:
            order.append(re.search(r'List\(\("([^"]*)"', query)[1])
    return order


def test_priority_files_change_the_query_execution_order(monkeypatch, two_file_repo, tmp_path):
    """HIGH-1: the list must change the order queries are actually SENT, not just the binding.

    Kills the mutation that reorders the loop by candidate id (`sorted(self.candidates, key=id)`):
    that leaves the checkpoint in priority order (so the bound-order test still passes) while the
    server sees id order -- here main.c would still be queried after a.c."""
    listing = tmp_path / "priority.txt"
    listing.write_text("main.c\n", encoding="utf-8")
    plain = tmp_path / "plain"
    plain.mkdir()
    _, _, without = run_cli(monkeypatch, two_file_repo, plain, two_file_payloads())
    listed = tmp_path / "listed"
    listed.mkdir()
    _, _, with_list = run_cli(
        monkeypatch, two_file_repo, listed, two_file_payloads(), "--priority-files", str(listing)
    )
    assert flows_file_order(without) == ["a.c", "main.c"]
    assert flows_file_order(with_list) == ["main.c", "a.c"]


def six_candidates_two_files():
    """3 candidates in a.c, 3 in main.c; a.c's flows poison, main.c's succeed."""
    data = two_file_payloads()
    a = [in_file(candidate("a" + str(i), i + 1), "a.c") for i in range(3)]
    m = [candidate("m" + str(i), i + 1) for i in range(3)]
    data["case_a"] = response("case_a", a + m)

    def flows(query):
        if '"a.c"' in query:  # the scope list names a.c -> this candidate is in a.c
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
    return data


def test_a_poison_streak_degrades_a_different_set_with_a_list(monkeypatch, two_file_repo, tmp_path):
    """HIGH-1: WHICH candidates end up full vs degraded changes with the list, under a cap.

    a.c's three flows poison; after POISON_STOP_STREAK the rest are degraded without a query.
    Without a list a.c is processed first, so the streak degrades everything (no full slice). With
    `--priority-files main.c`, main.c is sliced first (full) and only a.c degrades. The FULL set
    differs by list presence -- the mutation that loops in id order (always a.c first) leaves both
    runs fully degraded, so this fails under it."""
    plain = tmp_path / "plain"
    plain.mkdir()
    _, without, _ = run_cli(monkeypatch, two_file_repo, plain, six_candidates_two_files())
    assert without["slices"] and all(s.get("degraded") for s in without["slices"])
    assert without["counters"]["candidates_degraded"] == 6

    listing = tmp_path / "priority.txt"
    listing.write_text("main.c\n", encoding="utf-8")
    listed = tmp_path / "listed"
    listed.mkdir()
    _, with_list, _ = run_cli(
        monkeypatch, two_file_repo, listed, six_candidates_two_files(),
        "--priority-files", str(listing),
    )
    full = {s["entry"]["file"] for s in with_list["slices"] if not s.get("degraded")}
    degraded = {s["entry"]["file"] for s in with_list["slices"] if s.get("degraded")}
    assert full == {"main.c"} and degraded == {"a.c"}
    assert with_list["counters"]["candidates_degraded"] == 3


def three_candidates_two_files():
    """a.c's candidate plus two in main.c: the priority, id and candidate_key orders all differ.

    two_file_payloads()' two keys happen to sort in the priority order, so a key-sorted manifest
    read as a kept one there -- and once the list reorders two candidates, their key order must be
    the priority or the id order. `third` is at line 4 for its key (at line 5 the keys sorted in the
    priority order again)."""
    data = two_file_payloads()
    data["case_a"] = response(
        "case_a", [in_file(candidate(), "a.c"), candidate("other", 3), candidate("third", 4)]
    )
    return data


def test_priority_round_trips_through_shard_merge_and_export(monkeypatch, two_file_repo, tmp_path):
    """HIGH-1: the list survives a shard -> merge -> export round trip -- but not in the manifest.

    Two p%%N shards with the SAME list, merged and exported, give exactly the single prioritized
    run's slices, and the merged checkpoint's `candidates` keep the priority order. The exported
    candidate_manifest does not (#434): export reads it back from the checkpoint baseline, which
    Checkpoint.set stores key-sorted (canonical, sort_keys=True), so it is in candidate_key order.
    A candidate_manifest is in the priority order only when its run discovered the candidates
    itself, as the single run did (with a checkpoint), and --export-dir writes it in that order."""
    listing = tmp_path / "priority.txt"
    listing.write_text("main.c\n", encoding="utf-8")
    whole = tmp_path / "whole"
    whole.mkdir()
    _, single, _ = run_cli(
        monkeypatch, two_file_repo, whole, three_candidates_two_files(),
        "--checkpoint", str(whole / "state.db"), "--export-dir", str(whole / "exported"),
        "--priority-files", str(listing),
    )
    databases = []
    for index in range(2):
        folder = tmp_path / f"shard{index}"
        folder.mkdir()
        databases.append(folder / "state.db")
        code, result, _ = run_cli(
            monkeypatch, two_file_repo, folder, three_candidates_two_files(),
            "--checkpoint", str(databases[-1]), "--shard", f"{index}/2",
            "--priority-files", str(listing),
        )
        assert code == 2 and result["status"] == "partial", result.get("error")
    merged = tmp_path / "merged.db"
    merge_shards(merged, databases)
    export = tmp_path / "export"
    export.mkdir()
    _, exported, transport = run_cli(
        monkeypatch, two_file_repo, export, {}, "--checkpoint", str(merged), "--export-only",
        "--export-dir", str(export / "exported"),
    )
    assert transport.stages == []
    assert exported["slices"] == single["slices"]
    with Checkpoint(merged, resume=True) as store:
        bound = store.get("candidates")
    # listed file first, collect()'s order in each tier -- not read back from the code under test
    entries = [(c["entry"]["file"], c["entry"]["line"]) for c in bound]
    assert entries == [("main.c", 3), ("main.c", 4), ("a.c", 1)]
    priority = [c["candidate_key"] for c in bound]
    by_id = [c["candidate_key"] for c in sorted(bound, key=lambda c: c["id"])]
    # The fixture must keep the three orders apart, or a key-sorted manifest passes as a kept one.
    assert len({tuple(priority), tuple(by_id), tuple(sorted(priority))}) == 3, "re-pick `third`"
    assert list(single["candidate_manifest"]) == priority
    written = (whole / "exported" / "candidate-manifest.json").read_text(encoding="utf-8")
    assert list(json.loads(written)) == priority
    assert list(exported["candidate_manifest"]) == sorted(priority)
    written = (export / "exported" / "candidate-manifest.json").read_text(encoding="utf-8")
    assert list(json.loads(written)) == sorted(priority)


@pytest.mark.parametrize(
    ("extra", "expected"), [((), 0), (("--shard", "0/2"), 2)], ids=["whole", "shard"]
)
def test_a_cpg_run_reads_the_manifest_back_in_key_order(
    monkeypatch, two_file_repo, tmp_path, extra, expected
):
    """#434: a --cpg run takes the prepare run's discovery (restore_discovery), so its own
    candidate_manifest comes back key-sorted as an export's does. The gate is --cpg alone, with or
    without --shard (`slice_sharded.py --shards N>1` passes `--shard I/N`, `--shards 1` does not);
    a shard's exit code is 2 (partial). `candidates` keep the priority."""
    listing = tmp_path / "priority.txt"
    listing.write_text("main.c\n", encoding="utf-8")
    prep = tmp_path / "prep"
    prep.mkdir()
    cpg = prep / "cpg.bin"
    data = three_candidates_two_files()
    data["save_cpg"] = _saving(cpg)
    code, result, _ = run_cli(
        monkeypatch, two_file_repo, prep, data, "--checkpoint", str(prep / "state.db"),
        "--prepare-only", "--cpg-out", str(cpg), "--priority-files", str(listing),
    )
    assert code == 2 and result["prepare_only"] is True, result.get("error")
    seeded = _seed(prep / "state.db", tmp_path / "run")
    data = three_candidates_two_files()
    del data["case_a"], data["case_b"]  # a rediscovery would fail
    code, result, _ = run_cli(
        monkeypatch, two_file_repo, seeded.parent, data, "--checkpoint", str(seeded),
        "--resume", "--cpg", str(cpg), "--priority-files", str(listing), *extra,
    )
    assert code == expected, result.get("error")
    with Checkpoint(seeded, resume=True) as store:
        bound = store.get("candidates")
    assert [c["entry"]["file"] for c in bound] == ["main.c", "main.c", "a.c"]
    priority = [c["candidate_key"] for c in bound]
    assert priority != sorted(priority)
    assert list(result["candidate_manifest"]) == sorted(priority)


def test_matching_is_exact_not_substring_or_basename(repo):
    """M-2: exact match only. Kills the substring, reverse-substring and basename mutations.

    The fixtures elsewhere have no name-colliding pairs, so a loose match never shows. Here `a.c`
    must NOT match `data.c` (substring) or `sub/a.c` (basename), and `data.c` must NOT match a
    candidate whose file is `a.c` (reverse substring)."""
    from slicer_candidates import prioritize

    rows = [in_file(candidate("x", 1), "data.c"), in_file(candidate("y", 2), "sub/a.c")]
    ordered = collect({"A": rows}, repo)
    assert {c["entry"]["file"] for c in ordered} == {"data.c", "sub/a.c"}
    # a.c is a substring of data.c and the basename of sub/a.c -- neither may match
    assert prioritize(ordered, {"a.c"}) == (ordered, 0, set())
    # reverse substring: a.c (candidate) is a substring of the listed data.c
    only_a = collect({"A": [in_file(candidate("z", 1), "a.c")]}, repo)
    assert prioritize(only_a, {"data.c"}) == (only_a, 0, set())
    # the exact spellings still match
    moved, first, matched = prioritize(ordered, {"data.c", "sub/a.c"})
    assert first == 2 and matched == {"data.c", "sub/a.c"} and len(moved) == 2


def test_the_priority_digest_ignores_list_order(monkeypatch, two_file_repo, tmp_path):
    """M-8: the sha256 is of the SORTED list, so line order cannot change it.

    The contract tests used 1-file lists, where `list(set)` and `sorted(set)` coincide, so a
    mutation hashing the unsorted list survived and `settings.priority_files.sha256` differed
    across PYTHONHASHSEED -- shards then rejected merge/resume nondeterministically. A 3+-file
    unsorted list pins it to `digest(sorted(...))`, and two line orders must agree."""
    names = ["z.c", "a.c", "main.c", "m.c"]
    first = tmp_path / "first"
    first.mkdir()
    listing = first / "priority.txt"
    listing.write_text("\n".join(names) + "\n", encoding="utf-8")
    _, result, _ = run_cli(
        monkeypatch, two_file_repo, first, two_file_payloads(), "--priority-files", str(listing)
    )
    assert result["settings"]["priority_files"] == {"count": 4, "sha256": digest(sorted(names))}
    second = tmp_path / "second"
    second.mkdir()
    reordered = second / "priority.txt"
    reordered.write_text("\n".join(reversed(names)) + "\n", encoding="utf-8")
    _, other, _ = run_cli(
        monkeypatch, two_file_repo, second, two_file_payloads(), "--priority-files", str(reordered)
    )
    assert other["settings"]["priority_files"] == result["settings"]["priority_files"]


def test_a_zero_match_list_warns_on_stderr_and_records_the_names(
    monkeypatch, two_file_repo, tmp_path, capsys
):
    """M-3: a list that matches nothing must not read as applied -- it warns and keeps the names."""
    listing = tmp_path / "priority.txt"
    listing.write_text("nope.c\nalso-missing.c\n", encoding="utf-8")
    code, result, _ = run_cli(
        monkeypatch, two_file_repo, tmp_path, two_file_payloads(), "--priority-files", str(listing)
    )
    assert code == 0, result.get("error")
    err = capsys.readouterr().err
    assert "--priority-files" in err and "matched no candidate" in err
    assert result["priority"]["files_with_candidates"] == 0
    assert result["priority"]["matched_files"] == []
    assert result["priority"]["unmatched_files"] == ["also-missing.c", "nope.c"]


def test_a_partial_match_records_the_matched_and_unmatched_names(
    monkeypatch, two_file_repo, tmp_path
):
    """M-3/M-6: the audit trail names what matched and what did not, not just counts."""
    listing = tmp_path / "priority.txt"
    listing.write_text("main.c\nnope.c\ngone.c\n", encoding="utf-8")
    code, result, _ = run_cli(
        monkeypatch, two_file_repo, tmp_path, two_file_payloads(), "--priority-files", str(listing)
    )
    assert code == 0, result.get("error")
    assert result["priority"]["matched_files"] == ["main.c"]
    assert result["priority"]["unmatched_files"] == ["gone.c", "nope.c"]


def test_a_dropped_priority_list_on_resume_names_the_field(monkeypatch, two_file_repo, tmp_path):
    """L-11: forgetting --priority-files on a manual shard resume mismatches on run_contract; the
    message must name settings.priority_files so the fix (re-pass the list) is clear, not 'start a
    new checkpoint'."""
    database = tmp_path / "state.db"
    listing = tmp_path / "priority.txt"
    listing.write_text("main.c\n", encoding="utf-8")
    code, _, _ = run_cli(
        monkeypatch, two_file_repo, tmp_path, two_file_payloads(),
        "--checkpoint", str(database), "--priority-files", str(listing),
    )
    assert code == 0
    _, result, _ = run_cli(
        monkeypatch, two_file_repo, tmp_path, two_file_payloads(),
        "--checkpoint", str(database), "--resume",
    )
    assert "Checkpoint mismatch: run_contract" in result["error"]
    assert "settings.priority_files" in result["error"]


# -- --skip-candidates: keys this process does not slice (slice_sharded --restart-on-deadline) -----


def _candidate_keys(monkeypatch, repo, tmp_path):  # noqa: ANN001, ANN202
    """(the three candidate keys of `three_candidates()` in candidate order, the case-B one)."""
    folder = tmp_path / "learn"
    folder.mkdir()
    _, result, _ = run_cli(monkeypatch, repo, folder, three_candidates())
    manifest = result["candidate_manifest"]
    return list(manifest), next(k for k, c in manifest.items() if c["case"] == "B")


def _skipping(tmp_path, *keys):  # noqa: ANN002, ANN202
    listing = tmp_path / "skip.txt"
    listing.write_text("\n".join(keys) + "\n", encoding="utf-8")
    return str(listing)


def test_a_skipped_candidate_is_degraded_without_a_server_query(monkeypatch, repo, tmp_path):
    """The restarted shard must not re-attempt the candidate whose query hit the deadline: it gets
    the anchor-only slice a poison streak gives, nothing is sent to Joern for it, and the result
    says it was not sliced (a degraded slice in an `ok` result would read as complete)."""
    _, b_key = _candidate_keys(monkeypatch, repo, tmp_path)
    folder = tmp_path / "skip"
    folder.mkdir()
    code, result, transport = run_cli(
        monkeypatch, repo, folder, three_candidates(), "--checkpoint", str(folder / "state.db"),
        "--skip-candidates", _skipping(tmp_path, b_key),
        "--progress", str(folder / "progress.jsonl"),
    )
    assert transport.stages.count("flows") == 2 and "backward" not in transport.stages
    events = [json.loads(line) for line in (folder / "progress.jsonl").read_text().splitlines()]
    skipped = [e["candidate_key"] for e in events if e["event"] == "candidate_skipped"]
    assert skipped == [b_key]                       # the progress log names what was left out
    assert code == 2 and result["status"] == "partial"
    assert "--skip-candidates: 1 candidate not sliced" in result["error"]
    degraded = [s for s in result["slices"] if s.get("degraded")]
    assert [s["candidate_key"] for s in degraded] == [b_key]
    assert "--skip-candidates" in degraded[0]["measurement_notes"]["degraded"]
    assert result["counters"]["candidates_degraded"] == 1
    assert result["counters"]["candidates_failed"] == 0


def test_without_the_list_the_degraded_note_still_names_the_poison_streak(
    monkeypatch, repo, tmp_path
):
    """The flag off changes nothing: what a poison streak leaves unsliced is noted as that, never
    as a --skip-candidates skip, and the progress log has no candidate_skipped event."""
    cands = [candidate("c" + str(i), i + 1) for i in range(6)]
    bad = payloads(case_a=cands, flow_count=2)
    bad["flows"] = lambda query: {"success": True, "stdout": "java.lang.StackOverflowError",
                                  "stderr": ""}
    folder = tmp_path / "streak"
    folder.mkdir()
    _, result, _ = run_cli(monkeypatch, repo, folder, bad,
                           "--progress", str(folder / "progress.jsonl"))
    notes = [s["measurement_notes"]["degraded"] for s in result["slices"]]
    assert notes and all("(poison streak)" in note for note in notes)
    assert not any("--skip-candidates" in note for note in notes)
    assert "--skip-candidates" not in result["error"]
    events = [json.loads(line) for line in (folder / "progress.jsonl").read_text().splitlines()]
    assert "candidate_skipped" not in {e["event"] for e in events}


def test_a_skipped_candidate_stays_unsaved_so_resume_slices_it(monkeypatch, repo, tmp_path):
    keys, b_key = _candidate_keys(monkeypatch, repo, tmp_path)
    folder = tmp_path / "skip"
    folder.mkdir()
    state = folder / "state.db"
    run_cli(monkeypatch, repo, folder, three_candidates(), "--checkpoint", str(state),
            "--skip-candidates", _skipping(tmp_path, b_key))
    with Checkpoint(state, resume=True) as store:
        saved = {row[0] for row in store.conn.execute("SELECT candidate_key FROM stages")}
    assert saved == set(keys) - {b_key}
    code, result, transport = run_cli(monkeypatch, repo, folder, three_candidates(),
                                      "--checkpoint", str(state), "--resume")
    assert code == 0, result.get("error")
    assert transport.stages.count("backward") == 1 and "flows" not in transport.stages
    counters = result["counters"]
    assert counters["candidates_reused"] == 2 and counters["candidates_degraded"] == 0


def test_a_skipped_candidate_that_is_already_complete_is_reused_not_degraded(
    monkeypatch, repo, tmp_path
):
    _, b_key = _candidate_keys(monkeypatch, repo, tmp_path)
    folder = tmp_path / "skip"
    folder.mkdir()
    state = folder / "state.db"
    run_cli(monkeypatch, repo, folder, three_candidates(), "--checkpoint", str(state))
    code, result, transport = run_cli(
        monkeypatch, repo, folder, three_candidates(), "--checkpoint", str(state), "--resume",
        "--skip-candidates", _skipping(tmp_path, b_key),
    )
    assert code == 0 and "flows" not in transport.stages and "backward" not in transport.stages
    assert result["counters"]["candidates_degraded"] == 0 and "error" not in result


def test_a_claim_shard_skips_a_candidate_it_owns_without_giving_it_away(
    monkeypatch, repo, tmp_path
):
    """The skipped candidate stays claimed by this shard: no other shard takes it (the doc'd
    no-lease rule), and a later plain `--resume` of the same I/N slices it."""
    from slicer_claims import read_claims

    _, b_key = _candidate_keys(monkeypatch, repo, tmp_path)
    claims = tmp_path / "claims.db"
    folder = tmp_path / "claim0"
    folder.mkdir()
    code, result, transport = run_cli(
        monkeypatch, repo, folder, three_candidates(), "--checkpoint", str(folder / "state.db"),
        "--shard", "0/2", "--claim-db", str(claims),
        "--skip-candidates", _skipping(tmp_path, b_key),
    )
    assert "backward" not in transport.stages and transport.stages.count("flows") == 2
    assert read_claims(claims)[b_key][0] == "0/2"
    assert result["counters"]["candidates_degraded"] == 1


@pytest.mark.parametrize("mode", ["export", "prepare"])
def test_skip_candidates_with_export_or_prepare_is_refused(repo, tmp_path, capsys, mode):
    """Neither slices a candidate, so the list would change nothing -- said, not ignored."""
    flags = ["--export-only"] if mode == "export" else [
        "--prepare-only", "--cpg-out", str(tmp_path / "c.bin")]
    with pytest.raises(SystemExit):
        slicer.main(["--repo", str(repo), "--out", str(tmp_path / "s.json"), *flags,
                     "--checkpoint", str(tmp_path / "state.db"),
                     "--skip-candidates", _skipping(tmp_path, "k")])
    assert "--skip-candidates has no effect" in capsys.readouterr().err


def test_a_skip_list_reads_like_a_priority_list(monkeypatch, repo, tmp_path):
    """One key per line; blank lines and `#` lines are skipped; a BOM does not stick to line 1."""
    keys, _ = _candidate_keys(monkeypatch, repo, tmp_path)
    listing = tmp_path / "skip.txt"
    bom = chr(0xFEFF)
    listing.write_text(
        bom + "# deadline hits\n\n" + f"{keys[0]}\n  {keys[1]}  \n", encoding="utf-8"
    )
    assert slicer.read_skip_keys(listing) == frozenset(keys[:2])


# --case-order: which case is sliced first. The default is `ba`; `ab` is the order before `ba`
# became the default and stays selectable. Bound into the checkpoint.


def test_collect_orders_the_cases_by_case_order_and_defaults_to_ba(repo):
    """The case is the first sort key: `ba` (the default) is every B then every A, `ab` -- the
    order before `ba` became the default -- the reverse, with the old numbering.

    The files are laid out so a sort by file alone (a.c, b.c, y.c, z.c) would be a third order, and
    inside a case nothing moves. ids are numbered after the sort, so `ba` gives B the low numbers;
    the candidate set and every candidate_key stay the same."""
    rows = {
        "A": [in_file(candidate("a1", 1), "a.c"), in_file(candidate("a2", 1), "z.c")],
        "B": [in_file(candidate("b1", 1), "b.c"), in_file(candidate("b2", 1), "y.c")],
    }
    default = collect(rows, repo)
    assert default == collect(rows, repo, "ba")
    assert [(c["case"], c["entry"]["file"]) for c in default] == [
        ("B", "b.c"), ("B", "y.c"), ("A", "a.c"), ("A", "z.c"),
    ]
    assert {c["entry"]["symbol"]: c["id"] for c in default} == {
        "b1": "slice_0001", "b2": "slice_0002", "a1": "slice_0003", "a2": "slice_0004",
    }
    first_a = collect(rows, repo, "ab")
    assert [(c["case"], c["entry"]["file"]) for c in first_a] == [
        ("A", "a.c"), ("A", "z.c"), ("B", "b.c"), ("B", "y.c"),
    ]
    assert {c["entry"]["symbol"]: c["id"] for c in first_a} == {
        "a1": "slice_0001", "a2": "slice_0002", "b1": "slice_0003", "b2": "slice_0004",
    }
    keys = {c["entry"]["symbol"]: c["candidate_key"] for c in default}
    assert {c["entry"]["symbol"]: c["candidate_key"] for c in first_a} == keys


def test_the_default_case_order_is_ba_in_the_cli_and_the_library():
    import inspect

    from slicer_candidates import DEFAULT_CASE_ORDER, prioritize

    assert DEFAULT_CASE_ORDER == "ba"
    assert slicer.parser().parse_args(["--repo", "r", "--out", "o"]).case_order == "ba"
    for function in (collect, prioritize):
        assert inspect.signature(function).parameters["case_order"].default == "ba"


def test_a_settings_dict_without_case_order_is_read_as_ab(repo):
    """A contract written before --case-order existed (#441) has no `case_order` key, and that run
    sliced Case A first. The pipeline reads the missing key as `ab`, not as today's default `ba`.
    The CLI always fills the key, so this only shows when a settings dict reaches the pipeline some
    other way (a contract's settings, a hand-built job)."""
    rows = {"case_a": [candidate()], "case_b": [candidate("sprintf", 5)]}
    job = slicer.Slicer(repo, 0.95, None, slicer.JoernClient("unused", None))
    job.query = lambda stage, _query: (rows[stage], "")
    for settings, expected in (
        ({}, ["A", "B"]),
        ({"case_order": "ab"}, ["A", "B"]),
        ({"case_order": "ba"}, ["B", "A"]),
    ):
        job.settings = settings
        job.collect_candidates()
        assert [c["case"] for c in job.candidates] == expected, settings


def test_prioritize_is_tier_first_under_ab_and_case_first_under_ba(repo):
    """--priority-files splits a list stably, and what it splits depends on the case order. `ab`
    splits the whole list into listed files / the rest (tier-first, as #377 (b) shipped it):
    priority-A, priority-B, rest-A, rest-B. `ba` splits each case group (case-first): priority-B,
    rest-B, priority-A, rest-A -- no Case A, listed or not, runs before a Case B. Inside a tier
    both keep collect()'s order, and `first` counts the listed-file candidates either way."""
    from slicer_candidates import prioritize

    def group(case):
        return [
            in_file(candidate(f"{case}{i}_{where}", i), f"{where}.c")
            for where in ("hot", "cold")
            for i in (1, 2)
        ]

    rows = {"A": group("a"), "B": group("b")}
    for order, expected in (
        ("ab", "a1_hot a2_hot b1_hot b2_hot a1_cold a2_cold b1_cold b2_cold"),
        ("ba", "b1_hot b2_hot b1_cold b2_cold a1_hot a2_hot a1_cold a2_cold"),
    ):
        moved, first, matched = prioritize(collect(rows, repo, order), {"hot.c"}, order)
        assert first == 4 and matched == {"hot.c"}, order
        assert [c["entry"]["symbol"] for c in moved] == expected.split(), order


def legacy_prioritize(candidates, files):
    """prioritize() exactly as #377 (b) shipped it -- what `ab` must keep returning."""
    head, tail, matched = [], [], set()
    for c in candidates:
        hits = files & ({c["entry"]["file"]} | {a["file"] for a in c["anchors"]})
        matched |= hits
        (head if hits else tail).append(c)
    return head + tail, len(head), matched


def random_rows(seed):
    """Both cases over a few files, some candidates anchored in a second file, and a file list."""
    rng = random.Random(seed)
    names = ["a.c", "b.c", "c.c", "d.c"]
    rows = {}
    for case in "AB":
        rows[case] = []
        for i in range(rng.randint(0, 8)):
            row = in_file(candidate(f"{case.lower()}{i}", rng.randint(1, 8)), rng.choice(names))
            row["anchors"].append({"file": rng.choice(names), "line": 1})
            rows[case].append(row)
    return rows, set(rng.sample(names, rng.randint(0, 3)))


def test_prioritize_under_ab_is_what_377_shipped(repo):
    """`ab` keeps the tier-first order byte for byte -- it was the default when #377 (b) shipped it
    and is still selectable: against the old body, on random inputs."""
    from slicer_candidates import prioritize

    for seed in range(60):
        rows, files = random_rows(seed)
        ordered = collect(rows, repo, "ab")
        assert prioritize(ordered, files, "ab") == legacy_prioritize(ordered, files), seed


def test_prioritize_under_ba_is_case_first_whatever_the_input(repo):
    """The same rule written the other way round (a model, not the code's own loop): the B group
    then the A group, listed files first inside each, the input's order kept inside a group, ids
    untouched. The input is not always what collect(..., "ba") gave -- the A-first list (`ab`) and
    a shuffled one go in too: the case order is `ba`'s, not whichever case the input starts with."""
    from slicer_candidates import prioritize

    def touched(c):
        return {c["entry"]["file"]} | {a["file"] for a in c["anchors"]}

    for seed in range(60):
        rows, files = random_rows(seed)
        shuffled = collect(rows, repo, "ba")
        random.Random(seed).shuffle(shuffled)
        for ordered in (collect(rows, repo, "ba"), collect(rows, repo, "ab"), shuffled):
            expected = []
            for case in "BA":
                members = [c for c in ordered if c["case"] == case]
                expected += [c for c in members if files & touched(c)]
                expected += [c for c in members if not files & touched(c)]
            moved, first, matched = prioritize(ordered, files, "ba")
            assert moved == expected, seed
            assert first == sum(bool(files & touched(c)) for c in ordered), seed
            assert matched == files & set().union(*[touched(c) for c in ordered]), seed


def two_file_both_cases_payloads():
    """a.c holds the A candidate entry@1 and the B candidate sprintf@5, main.c holds A other@3 and
    B memcpy@6."""
    data = two_file_payloads()
    data["case_b"] = response(
        "case_b", [in_file(candidate("sprintf", 5), "a.c"), candidate("memcpy", 6)]
    )
    return data


def executed_candidates(transport):
    """What each per-candidate query was about, in the order it was SENT: a flows query names the
    candidate's file, a backward query the call's code."""
    sent = []
    for stage, (request, _) in zip(transport.stages, transport.requests, strict=True):
        query = json.loads(request.data)["query"]
        if stage == "flows":
            sent.append((stage, re.search(r'List\(\("([^"]*)"', query)[1]))
        elif stage == "backward":
            sent.append((stage, re.search(r'"(\w+)\(buf\)"', query)[1]))
    return sent


@pytest.mark.parametrize(("order", "queries", "ids"), [
    ("ab", [("flows", "main.c"), ("backward", "memcpy"), ("flows", "a.c"), ("backward", "sprintf")],
     ["slice_0002", "slice_0004", "slice_0001", "slice_0003"]),
    ("ba", [("backward", "memcpy"), ("backward", "sprintf"), ("flows", "main.c"), ("flows", "a.c")],
     ["slice_0002", "slice_0001", "slice_0004", "slice_0003"]),
])
def test_priority_files_follow_the_case_order_in_the_pipeline(
    monkeypatch, two_file_repo, tmp_path, order, queries, ids
):
    """With --priority-files main.c: `ab` runs listed A, listed B, rest A, rest B and `ba` runs
    listed B, rest B, listed A, rest A -- in the order the checkpoint is bound AND the queries are
    sent. ids stay as collect() numbered them (B first under `ba`), and first_tier_candidates is
    the number of listed-file candidates under both orders."""
    listing = tmp_path / "priority.txt"
    listing.write_text("main.c\n", encoding="utf-8")
    database = tmp_path / "state.db"
    code, result, transport = run_cli(
        monkeypatch, two_file_repo, tmp_path, two_file_both_cases_payloads(),
        "--checkpoint", str(database), "--priority-files", str(listing), "--case-order", order,
    )
    assert code == 0, result.get("error")
    assert result["priority"]["first_tier_candidates"] == 2
    assert result["priority"]["candidates"] == 4
    with Checkpoint(database, resume=True) as store:
        assert [c["id"] for c in store.get("candidates")] == ids
    assert executed_candidates(transport) == queries


@pytest.mark.parametrize(("extra", "recorded", "ids"), [
    ((), "ba", {"B": "slice_0001", "A": "slice_0002"}),  # the default
    (("--case-order", "ba"), "ba", {"B": "slice_0001", "A": "slice_0002"}),
    (("--case-order", "ab"), "ab", {"A": "slice_0001", "B": "slice_0002"}),  # the order before `ba`
])
def test_case_order_defaults_to_ba_and_ab_keeps_the_previous_numbering(
    monkeypatch, repo, tmp_path, extra, recorded, ids
):
    """Without the flag a run is sliced B first and numbered that way (the old default was A first);
    `--case-order ab` still gives the old order and the old ids, and the setting is recorded."""
    database = tmp_path / "state.db"
    data = payloads(case_a=[candidate()], case_b=[candidate("sprintf", 5)], flow_count=1)
    code, result, _ = run_cli(
        monkeypatch, repo, tmp_path, data, "--checkpoint", str(database), *extra
    )
    assert code == 0, result.get("error")
    assert result["settings"]["case_order"] == recorded
    with Checkpoint(database, resume=True) as store:
        candidates = store.get("candidates")
    assert [c["case"] for c in candidates] == list(ids)  # the bound (= processing) order
    assert {c["case"]: c["id"] for c in candidates} == ids


def test_case_order_changes_the_query_execution_order(monkeypatch, repo, tmp_path):
    """The flag must change the order queries are SENT, not only the bound list (the trap
    --priority-files already had): the first per-candidate query is backward under the default
    `ba`, flows under `ab`."""
    def per_candidate(transport):
        return [stage for stage in transport.stages if stage in ("flows", "backward")]

    sent = {}
    for name, extra in (("default", ()), ("ab", ("--case-order", "ab"))):
        folder = tmp_path / name
        folder.mkdir()
        data = payloads(case_a=[candidate()], case_b=[candidate("sprintf", 5)], flow_count=1)
        _, _, sent[name] = run_cli(monkeypatch, repo, folder, data, *extra)
    assert per_candidate(sent["default"]) == ["backward", "flows"]
    assert per_candidate(sent["ab"]) == ["flows", "backward"]


def test_case_order_is_part_of_the_run_contract(monkeypatch, repo, tmp_path):
    """The order is bound into the checkpoint's candidates, so it is part of the contract: the
    same order resumes, another one -- or leaving the flag out, which means the default `ba` -- is
    refused by name. `ab`, the non-default value, is the one that has to be given again."""
    options = ["--checkpoint", str(tmp_path / "state.db")]

    def data():
        return payloads(case_a=[candidate()], case_b=[candidate("sprintf", 5)], flow_count=1)

    code, result, _ = run_cli(monkeypatch, repo, tmp_path, data(), *options, "--case-order", "ab")
    assert code == 0, result.get("error")
    assert result["settings"]["case_order"] == "ab"
    assert [s["case"] for s in result["slices"]] == ["A", "B"]  # slices[] is in id order
    with Checkpoint(tmp_path / "state.db", resume=True) as store:
        assert store.get("run_contract")["settings"]["case_order"] == "ab"
        assert [c["case"] for c in store.get("candidates")] == ["A", "B"]
    code, resumed, _ = run_cli(
        monkeypatch, repo, tmp_path, data(), *options, "--resume", "--case-order", "ab"
    )
    assert code == 0, resumed.get("error")
    for another in ((), ("--case-order", "ba")):  # no flag = the default `ba`
        code, refused, _ = run_cli(
            monkeypatch, repo, tmp_path, data(), *options, "--resume", *another
        )
        assert code != 0 and "Checkpoint mismatch: run_contract" in refused["error"], another
        assert "settings.case_order" in refused["error"], another


def test_shards_with_different_case_orders_are_not_merged(monkeypatch, repo, tmp_path):
    databases = []
    for index, extra in enumerate((("--case-order", "ab"), ())):  # the second is the default `ba`
        folder = tmp_path / f"shard{index}"
        folder.mkdir()
        databases.append(folder / "state.db")
        code, result, _ = run_cli(
            monkeypatch, repo, folder, three_candidates(), "--checkpoint", str(databases[-1]),
            "--shard", f"{index}/2", *extra,
        )
        assert code == 2 and result["status"] == "partial", result.get("error")
    with pytest.raises(ValueError, match="Shard mismatch on run_contract"):
        merge_shards(tmp_path / "merged.db", databases)


def test_case_order_reaches_the_prepare_run_every_shard_and_the_export(monkeypatch, repo, tmp_path):
    """tools/slice_sharded.py hands the same arguments to the prepare run and to every shard and
    none to the export, which reads the run contract. `ab` is the non-default value, so the flag has
    to reach every stage for the contracts to match: two shards that carried it, merged and
    exported WITHOUT it, give exactly the single `ab` run's slices and candidates, ids included."""
    whole = tmp_path / "whole"
    whole.mkdir()
    _, single, _ = run_cli(monkeypatch, repo, whole, three_candidates(), "--case-order", "ab")
    state, cpg, code, _, _ = _prepare(
        monkeypatch, repo, tmp_path, three_candidates(), extra=("--case-order", "ab")
    )
    assert code == 2
    databases = []
    for index in range(2):
        databases.append(_seed(state, tmp_path / f"seeded{index}"))
        code, result, _ = _shard_on(
            monkeypatch, repo, databases[-1].parent, cpg, "--shard", f"{index}/2",
            "--case-order", "ab",
        )
        assert code == 2 and result["shard"] == {"index": index, "count": 2}, result.get("error")
    merged = tmp_path / "merged.db"
    merge_shards(merged, databases)
    export = tmp_path / "export"
    export.mkdir()
    _, exported, _ = run_cli(
        monkeypatch, repo, export, {}, "--checkpoint", str(merged), "--export-only"
    )
    assert exported["settings"]["case_order"] == "ab"
    assert exported["slices"] == single["slices"]
    # The manifest dict comes back from the checkpoint key-sorted (canonical JSON), so only its
    # content is compared; the order a run sliced in is the checkpoint's `candidates` list.
    assert exported["candidate_manifest"] == single["candidate_manifest"]
    with Checkpoint(merged, resume=True) as store:
        assert [c["case"] for c in store.get("candidates")] == ["A", "A", "B"]


def test_a_shard_that_forgot_the_case_order_is_refused(monkeypatch, repo, tmp_path):
    """A shard without the flag runs with the default `ba`, so it is refused when the prepare run
    was `ab`; the other way round (both default) is what every other shard test already runs."""
    state, cpg, _, _, _ = _prepare(
        monkeypatch, repo, tmp_path, three_candidates(), extra=("--case-order", "ab")
    )
    seeded = _seed(state, tmp_path / "seeded")
    code, result, transport = _shard_on(monkeypatch, repo, seeded.parent, cpg)
    assert code != 0 and "Checkpoint mismatch: run_contract" in result["error"]
    assert "settings.case_order" in result["error"]
    assert transport.stages == []  # refused before any Joern request


# #429: each slice records where the slicer put source and sink, for comparison with a GT.


def test_case_a_records_each_kept_flows_parameter_and_sink(monkeypatch, repo, tmp_path):
    """The flows payload's endpoint pair (fixture: main.c:1 -> main.c:5) lands as-is."""
    _, result, _ = run_cli(
        monkeypatch, repo, tmp_path, payloads(case_a=[candidate()], flow_count=1)
    )
    assert result["slices"][0]["source_sink"] == {
        "basis": "flow_endpoints",
        "sources": [{"file": "main.c", "line": 1, "kind": "parameter"}],
        "sinks": [{"file": "main.c", "line": 5, "kind": "flow_end"}],
        "pairs": [
            {
                "source": {"file": "main.c", "line": 1, "kind": "parameter"},
                "sink": {"file": "main.c", "line": 5, "kind": "flow_end"},
            }
        ],
        "unmapped": 0,
    }


def test_case_b_sink_is_the_candidate_call_and_sources_are_its_boundaries(
    monkeypatch, repo, tmp_path
):
    data = payloads(case_b=[candidate("sprintf", 5)])
    backward, _ = slicer.decode_response(data["backward"], "backward")
    backward["boundaries"] = [
        {"reason": "entry_parameter", "location": {"file": "main.c", "line": 1}},
        {"reason": "dependency_root", "location": {"file": "main.c", "line": 3}},
    ]
    data["backward"] = response("backward", backward)
    _, result, _ = run_cli(monkeypatch, repo, tmp_path, data)
    assert result["slices"][0]["source_sink"] == {
        "basis": "backward_boundaries",
        "sources": [
            {"file": "main.c", "line": 1, "kind": "entry_parameter"},
            {"file": "main.c", "line": 3, "kind": "dependency_root"},
        ],
        "sinks": [{"file": "main.c", "line": 5, "kind": "candidate_call"}],
        "pairs": [],
        "unmapped": 0,
    }


def test_without_an_analysis_case_b_keeps_its_sink_and_case_a_claims_nothing(repo):
    sources = {"main.c": (repo / "main.c").read_text(encoding="utf-8").splitlines()}
    entry = {"file": "main.c", "line": 5, "symbol": "sprintf"}
    b = source_sink("B", entry, None, sources, repo)
    assert b["basis"] == "candidate_only" and b["sources"] == []
    assert b["sinks"] == [{"file": "main.c", "line": 5, "kind": "candidate_call"}]
    a = source_sink("A", entry, None, sources, repo)
    assert (a["basis"], a["sources"], a["sinks"], a["pairs"]) == ("none", [], [], [])


def test_an_endpoint_outside_the_sources_is_dropped_and_counted(repo):
    sources = {"main.c": (repo / "main.c").read_text(encoding="utf-8").splitlines()}
    payload = {
        "endpoints": [
            {"source": {"file": "main.c", "line": 1}, "sink": {"file": "gone.c", "line": 9}}
        ]
    }
    found = source_sink("A", {"file": "main.c", "line": 1}, payload, sources, repo)
    assert found["sources"] == [{"file": "main.c", "line": 1, "kind": "parameter"}]
    assert found["sinks"] == [] and found["pairs"] == [] and found["unmapped"] == 1


def test_a_cluster_keeps_every_members_sources_and_sinks():
    def with_ends(id, source, sink):
        item = make_slice(id, [1, 2, 3])
        item.update(
            candidate_key=id,
            members=[id],
            analysis={},
            line_provenance={"data_dependency": [], "candidate_anchor": [], "context": []},
            source_sink={
                "basis": "flow_endpoints",
                "sources": [{"file": "a.c", "line": source, "kind": "parameter"}],
                "sinks": [{"file": "a.c", "line": sink, "kind": "flow_end"}],
                "pairs": [],
                "unmapped": 0,
            },
        )
        return item

    merged = cluster(
        [with_ends("slice_0001", 1, 3), with_ends("slice_0002", 2, 3)],
        0.95,
        {"a.c": ["x", "y", "z"]},
    )
    assert len(merged) == 1
    ends = merged[0]["source_sink"]
    assert [p["line"] for p in ends["sources"]] == [1, 2]
    assert [p["line"] for p in ends["sinks"]] == [3]
    assert ends["basis"] == "flow_endpoints"


_PAIR = {"source": {"file": "main.c", "line": 1}, "sink": {"file": "main.c", "line": 5}}


@pytest.mark.parametrize(
    "extra",
    [
        {"endpoints": None},
        {"endpoints": [{"source": {"file": "main.c", "line": 1}}]},
        {"endpoints": [_PAIR] * 2},
        {"endpoints_error": 7},
        {"endpoints": [_PAIR], "endpoints_error": "boom"},
    ],
    ids=["null", "no-sink", "more-than-flows", "error-not-str", "both"],
)
def test_a_flows_payload_with_bad_endpoints_is_refused(extra):
    data = {"flow_count": 1, "truncated": False, "lines": [{"file": "main.c", "line": 5}]}
    data.update(extra)
    with pytest.raises(slicer.QueryError):
        slicer.validate_payload("flows", data, "")


@pytest.mark.parametrize(
    "extra",
    [{}, {"endpoints_error": "java.util.NoSuchElementException: head of empty list"}],
    ids=["absent", "errored"],
)
def test_a_flows_payload_without_endpoints_is_accepted(extra):
    """#433 review: `endpoints` is optional, so its failure cannot fail the flows result."""
    data = {"flow_count": 1, "truncated": False, "lines": [{"file": "main.c", "line": 5}]}
    data.update(extra)
    slicer.validate_payload("flows", data, "")


@pytest.mark.parametrize(
    "extra",
    [{}, {"endpoints_error": "java.util.NoSuchElementException: head of empty list"}],
    ids=["absent", "errored"],
)
def test_without_endpoints_case_a_is_a_full_slice_with_unavailable_source_sink(
    monkeypatch, repo, tmp_path, extra
):
    """No endpoints -> not recorded (`endpoints_unavailable`), not zero pairs; not degraded."""
    data = payloads(case_a=[candidate()], flow_count=1)
    flows, _ = slicer.decode_response(data["flows"], "flows")
    del flows["endpoints"]
    flows.update(extra)
    data["flows"] = response("flows", flows)
    _, result, _ = run_cli(monkeypatch, repo, tmp_path, data)
    item = result["slices"][0]
    assert "degraded" not in item["measurement_notes"]
    assert item["flow_count"] == 1 and item["lines"]
    assert item["source_sink"] == {
        "basis": "endpoints_unavailable",
        "sources": [],
        "sinks": [],
        "pairs": [],
        "unmapped": 0,
    }
    for key, value in extra.items():
        assert item["analysis"][key] == value


def test_recorded_zero_endpoints_is_not_unavailable(repo):
    sources = {"main.c": (repo / "main.c").read_text(encoding="utf-8").splitlines()}
    entry = {"file": "main.c", "line": 1}
    assert source_sink("A", entry, {"endpoints": []}, sources, repo)["basis"] == "flow_endpoints"
    assert source_sink("A", entry, {}, sources, repo)["basis"] == "endpoints_unavailable"


def test_a_backward_boundary_without_a_location_is_refused():
    data = {
        "flow_count": None,
        "truncated": False,
        "lines": [],
        "node_count": 0,
        "state_count": 0,
        "dependency_count": 0,
        "boundaries": [{"reason": "dependency_root"}],
        "truncation_reasons": [],
    }
    with pytest.raises(slicer.QueryError):
        slicer.validate_payload("backward", data, "")


def test_the_flows_query_emits_the_head_and_last_of_each_kept_flow():
    """Pins the Scala SPELLING only -- no Joern here, so neither compilation nor runtime
    behaviour of `keptEndpoints` is tested. What it does check: the pair is head/last, it is
    computed inside a Try, and the success/failure branches write the two keys the validator
    knows, with the block's value being the result object (not the match's Unit)."""
    query = queries.flows_query([{"file": "main.c", "full_name": "entry", "line": 1}], 10)
    assert "(p.elements.head, p.elements.last)" in query
    body = query[query.index("val keptEndpoints") :]
    assert body.startswith("val keptEndpoints = scala.util.Try {")
    assert 'case scala.util.Success(pairs) => flowsResult.value("endpoints") =' in body
    assert 'flowsResult.value("endpoints_error") = ujson.Str(' in body
    # the endpoints are no longer a field of the literal result object
    assert '"endpoints" ->' not in body
    # last statement of the result block is the object itself
    assert re.search(r"\}\s*flowsResult\s*\}; ", body)
    block = body[: body.rindex("flowsResult")]
    assert block.count("{") == block.count("}")
