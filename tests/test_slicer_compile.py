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

    normalized, compiled, ignored = resolve_compilation_database(db, repo, out)

    assert compiled == {"src/a.c"}
    assert ignored == 0
    written = json.loads(normalized.read_text(encoding="utf-8"))
    # The relative "repo" directory (the repo dir's own name) resolves back to the repo root.
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

    normalized, compiled, _ = resolve_compilation_database(db, repo, tmp_path / "w")

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

    _, compiled, _ = resolve_compilation_database(db, repo, tmp_path / "w")

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

    _, compiled, _ = resolve_compilation_database(db, repo, tmp_path / "w")

    assert compiled == {"src/a.c"}


def test_c2cpg_ignored_folders_are_not_expected_and_are_counted(tmp_path):
    # c2cpg skips its DefaultIgnoredFolders (test/, tests/, CMakeFiles/, dotdirs), so those
    # database entries never enter the CPG. Keeping them in the expected set makes them read as
    # "Source files missing from CPG" -> partial. Measured instance: the 2026-09-14 zlib live
    # run's 3 missing files were test/example.c, test/infcover.c, test/minigzip.c.
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.c").write_text("int a;\n")
    db = _write_db(
        tmp_path / "cc.json",
        [
            {"directory": str(repo), "file": "src/a.c", "command": "cc a"},
            {"directory": str(repo), "file": "test/example.c", "command": "cc t"},
            {"directory": str(repo), "file": "tests/infcover.c", "command": "cc t"},
            {"directory": str(repo), "file": "CMakeFiles/x.c", "command": "cc x"},
            {"directory": str(repo), "file": ".hidden/y.c", "command": "cc y"},
        ],
    )

    _, compiled, ignored = resolve_compilation_database(db, repo, tmp_path / "w")

    assert compiled == {"src/a.c"}
    assert ignored == 4


def test_non_source_extension_is_not_expected(tmp_path):
    # c2cpg's AstCreationPass builds C/C++ TUs only; a non-source `file` is not parsed, so it
    # must not be expected in the CPG either.
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.c").write_text("int a;\n")
    db = _write_db(
        tmp_path / "cc.json",
        [
            {"directory": str(repo), "file": "src/a.c", "command": "cc a"},
            {"directory": str(repo), "file": "docs/readme.md", "command": "noop"},
        ],
    )

    _, compiled, ignored = resolve_compilation_database(db, repo, tmp_path / "w")

    assert compiled == {"src/a.c"}
    assert ignored == 1


def test_non_root_relative_directory_keeps_its_subpath(tmp_path):
    # A relative directory is resolved against the repo's parent, so "repo/sub" keeps its
    # subpath instead of collapsing to the repo root (which invented a non-existent file path
    # and reported compiled={"x.c"}).
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    (repo / "sub" / "x.c").write_text("int x;\n")
    db = _write_db(
        tmp_path / "compile_commands.json",
        [{"directory": "repo/sub", "file": "x.c", "command": "cc -c x.c"}],
    )

    normalized, compiled, _ = resolve_compilation_database(db, repo, tmp_path / "w")

    assert compiled == {"sub/x.c"}
    written = json.loads(normalized.read_text(encoding="utf-8"))
    assert written[0]["directory"] == str((repo / "sub").resolve())


def test_relative_directory_outside_the_repo_is_dropped_not_anchored(tmp_path):
    # A relative directory that resolves outside the repo must NOT be silently anchored at the
    # repo root: its `file` would then resolve to a path the repo does not contain. The entry is
    # dropped instead.
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.c").write_text("int a;\n")
    db = _write_db(
        tmp_path / "cc.json",
        [
            {"directory": str(repo), "file": "src/a.c", "command": "cc a"},
            {"directory": "../evil", "file": "x.c", "command": "cc x"},
        ],
    )

    _, compiled, _ = resolve_compilation_database(db, repo, tmp_path / "w")

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

    norm_a, _, _ = resolve_compilation_database(db_a, repo, out)
    norm_b, _, _ = resolve_compilation_database(db_b, repo, out)

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

    first, _, _ = resolve_compilation_database(db, repo, out)
    second, _, _ = resolve_compilation_database(db, repo, out)

    assert first == second


def test_import_query_passes_the_compilation_database_flag():
    query = queries.import_query("/r", "proj", "/work/compile_commands.resolved.json")
    assert "--compilation-database" in query
    assert "/work/compile_commands.resolved.json" in query


def test_import_query_without_a_database_passes_no_frontend_args():
    query = queries.import_query("/r", "proj")
    assert "--compilation-database" not in query
    assert "args=List" not in query
