"""Compile-aware import: database normalization and the import query flag.

The end-to-end effect (dead #ifdef branches and uncompiled files leaving the graph) is a
c2cpg behaviour verified on the Joern server; these tests fix the Python contract around it.
"""
import json
import os
from pathlib import Path

import pytest

import slicer_queries as queries
from slicer_compile import generated_header_dirs, resolve_compilation_database


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


def test_relative_include_paths_are_absolutized(tmp_path):
    # c2cpg resolves -I against its own working directory, not the entry directory, so a relative
    # include (a node addon's generated nan headers) is never found and NAN_METHOD-defined methods
    # never parse. The normalized command must carry absolute include paths anchored at the entry
    # directory. Only the attached form (-Ipath) is what c2cpg's `-I(\S+)` command-string regex
    # actually captures (JSONCompilationDatabaseParser.scala); a separated `-I path` is merged
    # into one attached token here so it reaches c2cpg at all -- see test below for that merge
    # asserted at the token level, not by substring.
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.c").write_text("int a;\n")
    db = _write_db(
        tmp_path / "cc.json",
        [{"directory": "repo", "file": "src/a.c",
          "command": "gcc -I../gen/nan -Iinclude -c src/a.c"}],
    )

    normalized, _, _ = resolve_compilation_database(db, repo, tmp_path / "w")
    command = json.loads(normalized.read_text())[0]["command"]

    assert os.path.normpath(str(repo.parent / "gen" / "nan")) in command
    assert os.path.normpath(str(repo / "include")) in command
    # No relative include token survives.
    assert "-I../" not in command and "../gen" not in command


def test_separated_include_flag_is_merged_into_one_attached_token(tmp_path):
    # c2cpg's `-I(\S+)` command regex and its `arguments.filter(_.startsWith("-I"))` rule both
    # only match a path that shares a token with the flag. A separated `["-I", "path"]` pair
    # yields an empty include for c2cpg (the "-I" token alone strips to ""), so the fix must
    # merge the pair into one token rather than absolutizing the path token in place.
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.c").write_text("int a;\n")
    db = _write_db(
        tmp_path / "cc.json",
        [{"directory": str(repo), "file": "src/a.c",
          "arguments": ["gcc", "-I", "../gen/nan", "-c", "src/a.c"]}],
    )

    normalized, _, _ = resolve_compilation_database(db, repo, tmp_path / "w")
    arguments = json.loads(normalized.read_text())[0]["arguments"]

    expected = "-I" + os.path.normpath(str(repo.parent / "gen" / "nan"))
    assert expected in arguments
    assert "-I" not in arguments  # the bare flag token must not survive on its own
    assert "../gen/nan" not in arguments


def test_absolute_include_paths_are_left_unchanged(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.c").write_text("int a;\n")
    abs_inc = str(tmp_path / "external" / "inc")
    # arguments form avoids a shell string, so an absolute path with backslashes on Windows is
    # not re-escaped by the tokenizer.
    db = _write_db(
        tmp_path / "cc.json",
        [{"directory": str(repo), "file": "src/a.c",
          "arguments": ["gcc", "-I" + abs_inc, "-c", "src/a.c"]}],
    )

    normalized, _, _ = resolve_compilation_database(db, repo, tmp_path / "w")
    arguments = json.loads(normalized.read_text())[0]["arguments"]

    assert abs_inc in " ".join(arguments)


def test_arguments_form_entries_keep_arguments_shape_and_define_values(tmp_path):
    # H1: c2cpg extracts a -D value differently per entry shape -- an `arguments` token splits
    # cleanly on '=', but its `command`-string regex only keeps a value that is quoted. Collapsing
    # every entry into a `command` string (the pre-fix behaviour) silently dropped every -D value
    # for `arguments`-shaped entries. The fix must keep the entry's original shape.
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.c").write_text("int a;\n")
    db = _write_db(
        tmp_path / "cc.json",
        [{"directory": str(repo), "file": "src/a.c",
          "arguments": ["gcc", "-DHAVE_CONFIG_H=1", "-D_FILE_OFFSET_BITS=64", "-c", "src/a.c"]}],
    )

    normalized, _, _ = resolve_compilation_database(db, repo, tmp_path / "w")
    out_entry = json.loads(normalized.read_text())[0]

    assert "command" not in out_entry
    assert "-DHAVE_CONFIG_H=1" in out_entry["arguments"]
    assert "-D_FILE_OFFSET_BITS=64" in out_entry["arguments"]


def test_one_malformed_command_does_not_fail_the_whole_database(tmp_path):
    # L1: shlex.split raises ValueError on unbalanced quoting. That must not propagate out of
    # resolve_compilation_database and take every other (well-formed) entry down with it via the
    # caller's fallback to full-source import (slicer.py build_cpg's except ValueError clause).
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.c").write_text("int a;\n")
    (repo / "src" / "b.c").write_text("int b;\n")
    db = _write_db(
        tmp_path / "cc.json",
        [
            {"directory": str(repo), "file": "src/a.c", "command": "gcc -I../gen -c src/a.c"},
            {"directory": str(repo), "file": "src/b.c", "command": 'gcc -DMSG="x -c src/b.c'},
        ],
    )

    normalized, compiled, _ = resolve_compilation_database(db, repo, tmp_path / "w")
    entries = json.loads(normalized.read_text())

    assert len(compiled) == 2
    assert "gen" in entries[0]["command"] and ".." not in entries[0]["command"]
    # The malformed entry is left exactly as it came in.
    assert entries[1]["command"] == 'gcc -DMSG="x -c src/b.c'


def test_generated_headers_dir_is_added_to_includes(tmp_path):
    # Some samples ship generated config headers under build_context/generated_headers but the
    # compile command's -I points only at the repo root, so c2cpg never finds them and the file
    # parses to no methods (libpng: PNG_READ_SUPPORTED undefined -> everything #ifdef'd out). The
    # normalization adds that directory and its subdirs to the include path.
    bc = tmp_path / "build_context"
    (bc / "generated_headers").mkdir(parents=True)
    (bc / "generated_headers" / "pnglibconf.h").write_text("#define PNG_READ_SUPPORTED\n")
    (bc / "generated_headers" / "nan").mkdir()
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.c").write_text("int a;\n")
    db = _write_db(
        bc / "compile_commands.json",
        [{"directory": str(repo), "file": "src/a.c", "command": "gcc -I. -c src/a.c"}],
    )

    normalized, _, _ = resolve_compilation_database(db, repo, tmp_path / "w")
    command = json.loads(normalized.read_text())[0]["command"]

    assert str((bc / "generated_headers").resolve()) in command
    assert str((bc / "generated_headers" / "nan").resolve()) in command
    assert command.split()[0] == "gcc"


def test_no_sibling_gen_dir_adds_no_include(tmp_path):
    # With no sibling generated_headers directory next to compile_commands.json, nothing is added:
    # only the absolutized -Isrc from the original command survives. (Detection is an is_dir()
    # check for that exact sibling, so the tmp_path spelling never matters.)
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.c").write_text("int a;\n")
    db = _write_db(
        tmp_path / "compile_commands.json",
        [{"directory": str(repo), "file": "src/a.c", "command": "gcc -Isrc -c src/a.c"}],
    )

    normalized, _, _ = resolve_compilation_database(db, repo, tmp_path / "w")
    command = json.loads(normalized.read_text())[0]["command"]

    # Exactly one include flag survives (the absolutized -Isrc), none added.
    assert command.count("-I") == 1


def test_generated_headers_are_appended_after_the_command_includes(tmp_path):
    # M1: generated_headers recover a header the command references nowhere, so they must be the
    # lowest-priority include -- a header the command already resolves must not be shadowed by a
    # same-named one under generated_headers. c2cpg searches -I in token order, so every original
    # include must precede every generated_headers include in the normalized entry. arguments form
    # keeps this at the token level (no shell string to re-split).
    bc = tmp_path / "build_context"
    (bc / "generated_headers" / "nan").mkdir(parents=True)
    (bc / "generated_headers" / "config.h").write_text("#define X 1\n")
    repo = tmp_path / "repo"
    (repo / "include").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "src" / "a.c").write_text("int a;\n")
    db = _write_db(
        bc / "compile_commands.json",
        [{"directory": str(repo), "file": "src/a.c",
          "arguments": ["gcc", "-Iinclude", "-c", "src/a.c"]}],
    )

    normalized, _, _ = resolve_compilation_database(db, repo, tmp_path / "w")
    arguments = json.loads(normalized.read_text())[0]["arguments"]

    # Exact tokens, not a path substring: pytest puts this test's name (which contains
    # "generated_headers") into tmp_path, so every include path would match a substring check.
    gen = {"-I" + str((bc / "generated_headers").resolve()),
           "-I" + str((bc / "generated_headers" / "nan").resolve())}
    gen_idx = [i for i, t in enumerate(arguments) if t in gen]
    orig_idx = [i for i, t in enumerate(arguments) if t.startswith("-I") and t not in gen]
    assert orig_idx and gen_idx
    assert max(orig_idx) < min(gen_idx)


def test_generated_header_dirs_lists_relative_paths(tmp_path):
    # review M2: the run record must name the dirs the include path gained (relative), so a run
    # with generated_headers is distinguishable from one without.
    bc = tmp_path / "build_context"
    (bc / "generated_headers" / "nan").mkdir(parents=True)
    db = _write_db(bc / "compile_commands.json", [])
    assert generated_header_dirs(db) == ["generated_headers", "generated_headers/nan"]


def test_generated_header_dirs_empty_when_absent(tmp_path):
    db = _write_db(tmp_path / "compile_commands.json", [])
    assert generated_header_dirs(db) == []


def test_import_query_passes_the_compilation_database_flag():
    query = queries.import_query("/r", "proj", "/work/compile_commands.resolved.json")
    assert "--compilation-database" in query
    assert "/work/compile_commands.resolved.json" in query


def test_import_query_without_a_database_passes_no_frontend_args():
    query = queries.import_query("/r", "proj")
    assert "--compilation-database" not in query
    assert "args=List" not in query


def test_import_query_with_the_include_root_passes_the_repo_as_a_header_include_path():
    """c2cpg 4.0.605 (`Main.scala`): `--include <path>` is `opt[String]("include").unbounded()`,
    "header include paths", and `ParserConfig` takes each as `Paths.get(_).toAbsolutePath` -- so
    the path is the (already absolute) repo itself, not a relative one."""
    query = queries.import_query("/r/repo", "proj", include_root=True)
    assert (
        'importCode.c(inputPath="/r/repo", projectName="proj", '
        'args=List("--include", "/r/repo"))' in query
    )
    # the path goes through the same literal helper as the other arguments (quotes, backslashes)
    assert 'args=List("--include", "C:\\\\w\\\\r\\"x")' in queries.import_query(
        'C:\\w\\r"x', "proj", include_root=True
    )
    # never `--define`: one dummy define cut the internal methods 21.5-62.8% on 3 repos (E3 probe)
    assert "--define" not in query


def test_import_query_without_the_include_root_is_the_query_it_always_was():
    """Flag off: every way of saying so gives the same bytes, with or without a database."""
    plain = queries.import_query("/r/repo", "proj")
    assert queries.import_query("/r/repo", "proj", None, False) == plain
    assert "--include" not in plain
    database = queries.import_query("/r/repo", "proj", "/work/cc.json")
    assert queries.import_query("/r/repo", "proj", "/work/cc.json", False) == database
    assert 'args=List("--compilation-database", "/work/cc.json"))' in database
    assert "--include" not in database


def test_import_query_composes_both_options_in_one_arg_list():
    """The builder composes them; the CLI refuses the pair (not measured), see test_slicer.py."""
    query = queries.import_query("/r/repo", "proj", "/work/cc.json", True)
    assert 'args=List("--compilation-database", "/work/cc.json", "--include", "/r/repo"))' in query
