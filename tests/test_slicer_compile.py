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


def test_import_query_passes_the_compilation_database_flag():
    query = queries.import_query("/r", "proj", "/work/compile_commands.resolved.json")
    assert "--compilation-database" in query
    assert "/work/compile_commands.resolved.json" in query


def test_import_query_without_a_database_passes_no_frontend_args():
    query = queries.import_query("/r", "proj")
    assert "--compilation-database" not in query
    assert "args=List" not in query
