"""Compile-aware import: database normalization and the import query flag.

The end-to-end effect (dead #ifdef branches and uncompiled files leaving the graph) is a
c2cpg behaviour verified on the Joern server; these tests fix the Python contract around it.
"""
import json
from pathlib import Path

import pytest

import slicer_queries as queries
from slicer_compile import resolve_compilation_database


def _write_db(path: Path, entries: list[dict]) -> Path:
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def test_relative_directory_is_rewritten_to_the_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.c").write_text("int a;\n")
    db = _write_db(
        tmp_path / "compile_commands.json",
        [{"directory": "repo", "file": "src/a.c", "command": "cc -c src/a.c"}],
    )
    out = tmp_path / "work"

    normalized, compiled = resolve_compilation_database(db, repo, out)

    assert compiled == {"src/a.c"}
    written = json.loads(normalized.read_text(encoding="utf-8"))
    # The relative "repo" directory could not resolve on the Joern host; it is anchored.
    assert written[0]["directory"] == str(repo.resolve())
    assert normalized.parent == out.resolve()


def test_absolute_directory_that_exists_is_left_alone(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.c").write_text("int a;\n")
    db = _write_db(
        tmp_path / "cc.json",
        [{"directory": str(repo), "file": "src/a.c", "command": "cc -c src/a.c"}],
    )

    normalized, compiled = resolve_compilation_database(db, repo, tmp_path / "w")

    assert compiled == {"src/a.c"}
    assert json.loads(normalized.read_text())[0]["directory"] == str(repo)


def test_absolute_file_path_inside_repo_is_recorded_relative(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    src = repo / "src" / "b.c"
    src.write_text("int b;\n")
    db = _write_db(
        tmp_path / "cc.json",
        [{"directory": str(repo), "file": str(src), "command": "cc -c b.c"}],
    )

    _, compiled = resolve_compilation_database(db, repo, tmp_path / "w")

    assert compiled == {"src/b.c"}


def test_entries_outside_the_repo_are_dropped(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.c").write_text("int a;\n")
    outside = tmp_path / "other" / "z.c"
    outside.parent.mkdir()
    outside.write_text("int z;\n")
    db = _write_db(
        tmp_path / "cc.json",
        [
            {"directory": str(repo), "file": "src/a.c", "command": "cc a"},
            {"directory": str(outside.parent), "file": str(outside), "command": "cc z"},
        ],
    )

    _, compiled = resolve_compilation_database(db, repo, tmp_path / "w")

    assert compiled == {"src/a.c"}


def test_no_entry_inside_the_repo_raises(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "other"
    outside.mkdir()
    db = _write_db(
        tmp_path / "cc.json",
        [{"directory": str(outside), "file": str(outside / "z.c"), "command": "cc z"}],
    )

    with pytest.raises(ValueError):
        resolve_compilation_database(db, repo, tmp_path / "w")


def test_empty_or_non_array_database_raises(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    empty = _write_db(tmp_path / "empty.json", [])
    with pytest.raises(ValueError):
        resolve_compilation_database(empty, repo, tmp_path / "w")

    not_array = tmp_path / "obj.json"
    not_array.write_text('{"file": "a.c"}', encoding="utf-8")
    with pytest.raises(ValueError):
        resolve_compilation_database(not_array, repo, tmp_path / "w")


def test_missing_database_raises_oserror(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(OSError):
        resolve_compilation_database(tmp_path / "nope.json", repo, tmp_path / "w")


def test_distinct_databases_get_distinct_normalized_files(tmp_path):
    # Parallel slicing writes every sample's normalized database under one shared dir; a fixed
    # name would let two runs overwrite each other and import the wrong translation units.
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.c").write_text("int a;\n")
    (repo / "src" / "b.c").write_text("int b;\n")
    out = tmp_path / "shared"
    ea = [{"directory": str(repo), "file": "src/a.c", "command": "cc a"}]
    eb = [{"directory": str(repo), "file": "src/b.c", "command": "cc b"}]
    db_a = _write_db(tmp_path / "a.json", ea)
    db_b = _write_db(tmp_path / "b.json", eb)

    norm_a, _ = resolve_compilation_database(db_a, repo, out)
    norm_b, _ = resolve_compilation_database(db_b, repo, out)

    assert norm_a != norm_b
    assert norm_a.exists() and norm_b.exists()
    # Each keeps its own translation unit; neither was overwritten.
    assert "src/a.c" in norm_a.read_text()
    assert "src/b.c" in norm_b.read_text()


def test_identical_database_content_maps_to_one_file(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.c").write_text("int a;\n")
    out = tmp_path / "shared"
    entry = [{"directory": str(repo), "file": "src/a.c", "command": "cc a"}]
    db = _write_db(tmp_path / "cc.json", entry)

    first, _ = resolve_compilation_database(db, repo, out)
    second, _ = resolve_compilation_database(db, repo, out)

    assert first == second


def test_import_query_passes_the_compilation_database_flag():
    query = queries.import_query("/r", "proj", "/work/compile_commands.resolved.json")
    assert "--compilation-database" in query
    assert "/work/compile_commands.resolved.json" in query


def test_import_query_without_a_database_passes_no_frontend_args():
    query = queries.import_query("/r", "proj")
    assert "--compilation-database" not in query
    assert "args=List" not in query
