"""tools/slicer_v2 targeted mode and the `--cases` / `defines` changes in tools/slicer. Joern and
its HTTP transport are mocked (as in tests/test_slicer.py), so nothing here shows that the Scala
queries select the right nodes in a real CPG -- only the Python around them: spec handling, the
payload contracts, the JSON the mode writes, and that the harness loader reads it."""

import hashlib
import json
from pathlib import Path
from urllib.error import URLError

import pytest
from test_slicer import MockTransport, payloads, response, run_cli

import slicer
import slicer_queries as queries
import slicer_target as target
from slicer_errors import QueryError
from src.adapters import index_graph
from src.adapters.slices import load_slices

FIXTURES = Path(__file__).parent / "fixtures" / "input_context"
SOURCE = (
    "int entry(char *p)\n{\n    char buf[32];\n    if (p) {\n"
    "        sprintf(buf, p);\n    }\n    return 0;\n}\n"
)


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.c").write_text(SOURCE, encoding="utf-8", newline="\n")
    return root


# --- tools/slicer: the new options change nothing while they are off -----------------------


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_default_queries_are_byte_identical_to_before_the_options():
    # Hashes of the texts at 3cdc653 (before `defines` and the context_query options).
    assert sha(queries.context_query([("a.c", 3), ("b.c", 9)], "/r")) == (
        "87eab3d2232c3bffecac5062f6cfc7bf3ed0d65b67de8dd6071ea77b5525f837"
    )
    assert sha(queries.import_query("/r", "p")) == (
        "baa3e3666a34b5e67d6e5194c33ed36b3bfb62577b2c1d9fd5366db30cd9a282"
    )
    assert sha(queries.import_query("/r", "p", "db.json", True)) == (
        "2509ac6000c892e1472711f0c75e4216c0d6eafffae2a2ef76a18855e326ed7b"
    )


def test_defines_become_c2cpg_define_arguments():
    text = queries.import_query("/r", "p", None, False, ["QUICK", "NGIF_FILE"])
    assert '"--define", "QUICK", "--define", "NGIF_FILE"' in text
    assert "--define" not in queries.import_query("/r", "p", None, False, [])


def test_context_query_options_change_only_the_text_they_name():
    points = [("a.c", 3)]
    plain = queries.context_query(points, "/r")
    assert "pointLimit" not in plain and "nodes.JumpTarget" in plain
    guarded = queries.context_query(points, "/r", guard_returns_only=True)
    assert "pointLimit" in guarded and "points.toList" in guarded
    assert "ujson.read" in queries.context_query(
        points, "/r", guard_returns_only=True, guard_points=[("a.c", 1)]
    )
    assert 'startsWith("case")' in queries.context_query(points, "/r", case_labels=False)
    assert "cpg.assignment" not in queries.context_query(points, "/r", follow_assignments=False)


def test_cases_default_keeps_the_settings_and_the_queries(monkeypatch, tree, tmp_path):
    code, result, transport = run_cli(monkeypatch, tree, tmp_path, payloads())
    assert code == 0
    assert "cases" not in result["settings"]
    assert "case_a" in transport.stages and "case_b" in transport.stages


@pytest.mark.parametrize(
    ("cases", "ran", "skipped"), [("A", "case_a", "case_b"), ("B", "case_b", "case_a")]
)
def test_cases_skips_the_other_discovery_query(monkeypatch, tree, tmp_path, cases, ran, skipped):
    code, result, transport = run_cli(monkeypatch, tree, tmp_path, payloads(), "--cases", cases)
    assert code == 0
    assert result["settings"]["cases"] == cases
    assert ran in transport.stages and skipped not in transport.stages
    counter = "candidates_" + skipped
    assert result["counters"][counter] is None  # unknown, not 0
    assert result["measurement_notes"][counter] == f"Skipped by --cases {cases}"
    # The completion metric is gated on the *last selected* case being counted
    # (tools/slicer/slicer_pipeline.py:636 `selected_cases()[-1]`), not on case B. Gated on B it
    # would be None here for `--cases A` -- a run that finished would read as unknown.
    assert result["counters"]["candidates_not_processed"] == 0


# --- spec / index handling ------------------------------------------------------------------


def test_the_spec_needs_a_sink_with_a_file_and_a_line():
    assert target.validate_spec({"sink": {"file": "a.c", "line": 3}}) is None
    for bad in (
        None,
        {},
        {"sink": {"file": "a.c"}},
        {"sink": {"file": "a.c", "line": 0}},
        {"sink": {"file": "", "line": 3}},
        {"sink": {"file": "a.c", "line": 3, "function_span": [5, 2]}},
        {"sink": {"file": "a.c", "line": 3}, "source": {"file": "a.c"}},
    ):
        assert target.validate_spec(bad) is not None


def test_the_run_signature_hashes_the_input_modules_as_well_as_the_slicer_ones():
    """`code_hashes` is part of the run contract. `input_*.py` writes `analysis.input_evidence` and
    `analysis.input_context`, so those modules belong in it -- a `slicer*.py`-only glob would let
    their output change under the same signature. Positive pins are the files on disk; the negative
    is this directory's own non-module files, which must stay out."""
    here = Path(target.__file__).parent
    names = {p.name for p in target.code_files(here, Path(slicer.__file__).parent)}
    assert {"input_context.py", "input_evidence.py", "input_paths.py", "input_text.py"} <= names
    assert {"slicer_target.py", "slicer.py", "slicer_queries.py"} <= names
    assert "README.md" not in names and not any(n.startswith("test_") for n in names)
    assert names == {p.name for p in here.glob("*.py")} | {
        p.name for p in Path(slicer.__file__).parent.glob("slicer*.py")
    }


def test_the_spec_carries_no_ground_truth_field():
    # Nothing in the spec or the module reads a ground-truth path or a CWE.
    code = (
        Path(target.__file__).read_text(encoding="utf-8").split('"""', 2)[2]
    )  # past the docstring
    for word in ("ground_truth", "private_gt", "cwe"):
        assert word not in code.lower()


def test_index_versions_and_tables_are_checked(tmp_path):
    good = {
        "version": "index-v4",
        "files": ["main.c"],
        "functions": {},
        "calls": {},
        "macros": {},
        "file_vars": {},
    }
    path = tmp_path / "index.json"
    path.write_text(json.dumps(good), encoding="utf-8")
    index, identity, problem = target.load_index(path)
    assert problem is None and identity["version"] == "index-v4"
    assert identity["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    for broken in (good | {"version": "index-v1"}, {k: v for k, v in good.items() if k != "calls"}):
        path.write_text(json.dumps(broken), encoding="utf-8")
        assert target.load_index(path)[2] is not None
    path.write_text("not json", encoding="utf-8")
    assert target.load_index(path)[2] is not None


def test_the_nonproduct_list_is_the_harness_list():
    assert target.NONPRODUCT_DIRS == index_graph._NONPRODUCT_DIRS
    assert target.FUZZ_PREFIXES == index_graph._FUZZ_PREFIXES


def test_activating_defines_on_a_real_conditional():
    lines = (FIXTURES / "apprentice_read_fragment.c").read_text(encoding="utf-8").split("\n")
    mmap = next(n for n, text in enumerate(lines, 1) if "MAP_TYPE_MMAP" in text)
    read = next(n for n, text in enumerate(lines, 1) if "read(fd" in text)
    assert target.activating_defines(lines, mmap) == ["QUICK"]  # inside `#ifdef QUICK`
    assert target.activating_defines(lines, read) == []  # in its `#else`


def test_choose_prefers_the_specs_function_over_the_narrower_one_on_the_same_line():
    """One line can sit in two methods (a C++ lambda inside its enclosing function). The spec's
    function wins; the narrowest is only the tie-break. Pinned because the two orders disagree
    exactly when the spec's function is the wider one -- and then picking the narrower also writes
    `function_mismatch` into the note, so the slice would claim the spec named another function."""
    outer = {"file": "main.c", "line": 1, "line_end": 40, "full_name": "outer", "symbol": "outer"}
    inner = {"file": "main.c", "line": 9, "line_end": 11, "full_name": "outer.lam", "symbol": "lam"}
    rows = [
        {**row("IDENTIFIER", "n", "n", 10, 5), "method": outer},
        {**row("IDENTIFIER", "n", "n", 10, 5), "method": inner},
    ]
    picked, note = target.choose(rows, {"file": "main.c", "line": 10, "function": "outer"})
    assert note["method"] == "outer" and "function_mismatch" not in note
    assert [r["method"] for r in picked] == [outer]
    # Negative: with no `function` in the spec nothing is preferred by name, so the narrowest wins.
    _, plain = target.choose(rows, {"file": "main.c", "line": 10})
    assert plain["method"] == "outer.lam"


def test_pick_line_prefers_the_symbol_line_and_falls_back():
    sources = {"main.c": SOURCE.split("\n")}
    spec = {"file": "main.c", "line": 4, "symbol": "sprintf(buf, p)"}
    assert target.pick_line(sources, spec) == (5, True)
    assert target.pick_line(sources, {**spec, "symbol": "nothing_like_it"}) == (4, False)


# --- the mode end to end, Joern mocked --------------------------------------------------------


NODE = {
    "method": {"file": "main.c", "line": 1, "line_end": 8, "full_name": "entry", "symbol": "entry"}
}


def row(label, code, name, line, column):
    return {
        "label": label,
        "code": code,
        "name": name,
        "line": line,
        "column": column,
        "ast_path": [1, line],
        **NODE,
    }


def traversal(direction):
    return {
        "flow_count": None,
        "truncated": False,
        "lines": [{"file": "main.c", "line": 5}],
        "node_count": 2,
        "state_count": 2,
        "dependency_count": 1,
        "boundaries": [
            {
                "reason": "source_reached" if direction == "backward" else "sink_reached",
                "location": {"file": "main.c", "line": 1 if direction == "backward" else 5},
            }
        ],
        "truncation_reasons": [],
        "cross_method_flow": [],
        "cross_method_flow_truncated": False,
        "after_target_lines": [],
        "target_reached": True,
        "reached": [],
        "stop_requested": True,
        "pruned_to_target": direction == "forward",
        "pruned_to_start_method": False,
        "kept_after_target": 0,
        "nodes_before_prune": 2,
    }


EVIDENCE = {
    "version": 1,
    "status": "ok",
    "root_count": 1,
    "method_count": 1,
    "truncated": False,
    "truncation_reasons": [],
    "methods": [
        {
            "id": "1",
            "file": "main.c",
            "name": "entry",
            "full_name": "entry",
            "line_start": 1,
            "line_end": 8,
            "depth": 0,
            "parameters": [{"index": 1, "name": "p"}],
            "ddg_lines": [5],
            "reads": [],
            "reads_truncated": False,
            "guards": [],
            "guards_truncated": False,
        }
    ],
    "calls": [],
}


def mock_data(**override):
    data = payloads()
    data.update(
        locate=lambda query: response(
            "locate",
            [row("CALL", "sprintf(buf, p)", "sprintf", 5, 9)]
            if "= 5" in query
            else [row("METHOD_PARAMETER_IN", "char *p", "p", 1, 12)],
        ),
        backward=response("backward", traversal("backward")),
        forward=response("forward", traversal("forward")),
        input_evidence=response("input_evidence", EVIDENCE),
        cleanup=response("cleanup", True),
    )
    data.update(override)
    return data


def run_target(monkeypatch, tree, tmp_path, data, *options, spec=None):
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(
        json.dumps(
            spec
            or {
                "sink": {
                    "file": "main.c",
                    "line": 5,
                    "function": "entry",
                    "symbol": "sprintf(buf, p)",
                },
                "source": {"file": "main.c", "line": 1, "function": "entry", "symbol": "p"},
            }
        ),
        encoding="utf-8",
    )
    transport = MockTransport(data)
    monkeypatch.setattr(slicer, "urlopen", transport)
    out = tmp_path / "slices.json"
    code = target.main(["--repo", str(tree), "--spec", str(spec_path), "--out", str(out), *options])
    return code, json.loads(out.read_text(encoding="utf-8")), out, transport


def index_file(tmp_path):
    path = tmp_path / "index.json"
    path.write_text(
        json.dumps(
            {
                "version": "index-v4",
                "files": ["main.c"],
                "calls": {},
                "macros": {},
                "file_vars": {},
                "functions": {
                    "entry": [{"file": "main.c", "line_start": 1, "line_end": 8, "params": []}]
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_both_directions_are_written_and_the_harness_loader_reads_them(monkeypatch, tree, tmp_path):
    code, result, out, transport = run_target(monkeypatch, tree, tmp_path, mock_data())
    assert code == 0, result.get("error")
    assert [(s["id"], s["case"], s["direction"]) for s in result["slices"]] == [
        ("backward", "B", "backward"),
        ("forward", "F", "forward"),
    ]
    assert "cleanup" in transport.stages
    # No --index: the evidence is there, the selection is not.
    for item in result["slices"]:
        assert item["analysis"]["input_evidence"]["status"] == "ok"
        assert "input_context" not in item["analysis"]
    loaded = load_slices(out)
    assert {s.id: s.given_end for s in loaded.slices} == {"backward": "sink", "forward": "source"}


def test_input_context_is_added_with_an_index_and_loads(monkeypatch, tree, tmp_path):
    options = ("--index", str(index_file(tmp_path)))
    code, result, out, _ = run_target(monkeypatch, tree, tmp_path, mock_data(), *options)
    assert code == 0, result.get("error")
    assert result["settings"]["index"]["version"] == "index-v4"
    for item in result["slices"]:
        context = item["analysis"]["input_context"]
        assert context["version"] == 1 and context["basis"] == "cpg"
        assert context["cap_hit"] is False and context["cap_reasons"] == []
        assert context["identical_to_auditor_input"] is True  # `entry` calls nothing
        assert context["caps_measured"] is False
    assert len(load_slices(out).slices) == 2  # unknown analysis keys do not stop the loader


def test_the_output_is_deterministic(monkeypatch, tree, tmp_path):
    options = ("--index", str(index_file(tmp_path)))
    first = run_target(monkeypatch, tree, tmp_path, mock_data(), *options)[1]["slices"]
    second = run_target(monkeypatch, tree, tmp_path, mock_data(), *options)[1]["slices"]
    assert json.dumps(first) == json.dumps(second)


def test_without_a_source_there_is_no_forward_slice(monkeypatch, tree, tmp_path):
    spec = {"sink": {"file": "main.c", "line": 5, "symbol": "sprintf(buf, p)"}}
    code, result, _, _ = run_target(monkeypatch, tree, tmp_path, mock_data(), spec=spec)
    assert code == 0
    assert [s["id"] for s in result["slices"]] == ["backward"]


def test_a_sink_with_no_node_gives_a_degraded_slice_the_loader_marks(monkeypatch, tree, tmp_path):
    data = mock_data(locate=response("locate", []))
    code, result, out, _ = run_target(monkeypatch, tree, tmp_path, data)
    assert code == 2  # partial: the nodes were not located
    assert all(s["degraded"] for s in result["slices"])
    assert all(s.degraded for s in load_slices(out).slices)


def test_an_evidence_query_failure_is_recorded_not_fatal(monkeypatch, tree, tmp_path):
    data = mock_data(input_evidence=URLError("boom"))
    code, result, _, _ = run_target(monkeypatch, tree, tmp_path, data)
    assert code in (0, 2)
    first = result["slices"][0]["analysis"]["input_evidence"]
    assert first["status"] == "query_failed"


def test_a_malformed_locate_payload_is_refused_before_use():
    with pytest.raises(QueryError):
        slicer.validate_payload("locate", [{"label": 1}], "")
    with pytest.raises(QueryError):
        slicer.validate_payload("input_evidence", {"version": 1, "status": "ok"}, "")
    slicer.validate_payload("cleanup", True, "")


def test_a_cut_slice_makes_the_run_partial(monkeypatch, tree, tmp_path):
    cut = traversal("backward") | {"truncated": True, "truncation_reasons": ["backward_depth"]}
    data = mock_data(backward=response("backward", cut))
    code, result, out, _ = run_target(monkeypatch, tree, tmp_path, data)
    assert code == 2 and result["status"] == "partial"
    assert "truncated" in result["error"]
    assert load_slices(out).slices[0].truncation_reasons == ("backward_depth",)


def test_the_loaded_location_symbol_is_the_enclosing_function(monkeypatch, tree, tmp_path):
    """The loader takes the function name from `candidate_manifest[key].scopes[0].symbol`; without
    it a B/F slice reaches the Auditor as a nameless window and the guard scan skips it."""
    from src.adapters.slices import slice_refs

    code, result, out, _ = run_target(monkeypatch, tree, tmp_path, mock_data())
    assert code == 0
    assert result["candidate_manifest"]["backward"]["scopes"] == [
        {"file": "main.c", "line": 1, "symbol": "entry", "full_name": "entry"}
    ]
    refs = slice_refs(load_slices(out))
    assert [(r.slice_id, r.location.symbol) for r in refs] == [
        ("backward", "entry"),
        ("forward", "entry"),
    ]


def test_a_degraded_slice_takes_the_function_name_from_the_spec(monkeypatch, tree, tmp_path):
    from src.adapters.slices import slice_refs

    data = mock_data(locate=response("locate", []))
    _, _, out, _ = run_target(monkeypatch, tree, tmp_path, data)
    assert [r.location.symbol for r in slice_refs(load_slices(out))] == ["entry", "entry"]


def test_the_guard_scan_skips_the_f_slice_with_a_reason_that_is_not_a_case_name(
    monkeypatch, tree, tmp_path
):
    """The F slice of this mode gives the `source` end, so the harness does not know its sink and
    the guard scan skips it -- with the reason `given_source`, not `case_a`. `SliceRef` carries
    `given_end` and not `case` (src/adapters/slices.py `_GIVEN_END`), so a case-named reason would
    be a false one here: an F slice is not a Case A slice.

    Both refs come from this mode's own output through the harness loader, not from a hand-written
    SliceRef (CLAUDE.md 2.5 -- pin samples come from the repo).
    """
    from src.adapters.slices import slice_refs
    from src.orchestrator.source_guards import scan_guards
    from src.schemas.common import Location

    code, _, out, _ = run_target(monkeypatch, tree, tmp_path, mock_data())
    assert code == 0
    refs = {r.slice_id: r for r in slice_refs(load_slices(out))}
    anchor = Location(file="main.c", symbol="entry", line_start=1, line_end=8)
    scans = {
        key: scan_guards(ref, anchor, SOURCE, lambda _loc: SOURCE) for key, ref in refs.items()
    }
    assert scans["forward"].skipped == "given_source"
    assert scans["forward"].event_fields() == {"guards_skipped": "given_source"}
    # Negative pin: the same run's B slice gives the sink end and is scanned, so the reason is not
    # written for every slice of this mode.
    assert scans["backward"].skipped is None
