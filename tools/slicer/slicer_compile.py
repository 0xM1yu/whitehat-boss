"""Compile-aware CPG input.

Hand c2cpg the project's compile_commands so it builds the graph from the translation
units the build actually compiles, with their real flags. c2cpg reads the database itself
through its `--compilation-database` frontend option, so no external preprocessor runs.

The end-to-end effect -- uncompiled files and inactive `#ifdef` branches leaving the graph --
is a c2cpg behaviour. What has been measured *through this path* (c2cpg `--compilation-database`)
is one sample: sample_08 (WavPack), an ATOM slicer remeasure. There `get_word3`, whose whole
file sits under an unset `ENABLE_LEGACY`, is absent from the graph (0 occurrences) while the
live functions stay, `files_missing_from_cpg` is 0, and internal methods number 673.

⚠ The stronger figures once quoted here -- "unpack3.c: 10 phantom methods to 0" and 31
macro-expanded functions becoming real -- were NOT measured through this path. They come from a
separate gcc `-E` preprocessor-coverage study (11 samples), a different mechanism, and are not
evidence for what `--compilation-database` does. Do not read them as this path's results
(CLAUDE.md §0, §2.2).

LIMITATION -- inactive branches under target macros. c2cpg `--compilation-database` does not
define target/platform macros such as `__linux__` or `__x86_64__` unless the database's command
passes `-D` for them. A branch guarded by an undefined macro is therefore inactive, and this
path does not parse inactive branches, so code reachable only under such a macro can be dropped.
Demonstrating the macro behaviour needs a Joern run; here it is recorded as a limitation only
and verified by the main session's full slicing.

The database's `file` entries resolve against each entry's `directory`. A relative directory is
resolved against the repository's parent -- so a non-root relative directory (e.g. "repo/sub")
keeps its subpath -- and release samples ship "repo" (the repo directory's own name), which
resolves back to the repository root.

c2cpg does not turn every database entry into methods: it skips its DefaultIgnoredFolders and
parses only C/C++ source extensions. `_compiled_files` mirrors both so an entry c2cpg ignores is
not later read as a parse miss ("Source files missing from CPG" -> partial); the count it skips
is returned as a diagnostic so the exclusion is not silent.

The normalized copy is content-addressed and written atomically: two slicer runs that share an
output parent (parallel slicing writes every sample under one payload dir) would otherwise
overwrite one fixed filename and hand each other the wrong translation-unit set.
"""
import hashlib
import json
import os
import shlex
import uuid
from pathlib import Path, PurePosixPath

# c2cpg (Joern v4.0.605 and v4.0.646) skips these folder names when it walks a project, so a
# database entry under one of them -- or under a dot-directory -- never enters the CPG. Source:
# C2Cpg.scala DefaultIgnoredFolders. Verified at both tags by reading the source on 2026-10-03:
# C2Cpg.scala is byte-identical, and x2cpg SourceFiles.scala (which applies the list) differs only
# in whitespace. Repo-verifiable instance: the 2026-09-14 zlib live run (4.0.605) listed
# test/example.c, test/infcover.c, test/minigzip.c as the exact 3 "missing from CPG" files
# (docs/experiments/harness/2026-09-14-joern-slicer-live-atom.md §3.1). Mirroring the rule keeps
# those from reading as parse misses.
_IGNORED_DIR_NAMES = frozenset({"test", "tests", "CMakeFiles"})
# c2cpg's AstCreationPass builds C/C++ translation units only; a database `file` with any other
# extension is not turned into methods. This mirrors that extension set (the load-bearing filter
# above is the folder one -- a compiled `file` entry is almost always a .c/.cc/.cpp TU, so this
# only guards the odd non-source entry, and over-excluding here at worst leaves a genuine miss
# unflagged, never invents a false "missing").
_SOURCE_EXTENSIONS = frozenset({
    ".c", ".cc", ".cpp", ".cxx", ".c++", ".h", ".hpp", ".hh", ".hxx", ".h++",
    ".i", ".ii", ".tcc", ".m", ".mm",
})


def _ignored_by_c2cpg(rel: str) -> bool:
    """True if c2cpg would not parse this repo-relative path (ignored folder or non-source)."""
    path = PurePosixPath(rel)
    if any(part in _IGNORED_DIR_NAMES or part.startswith(".") for part in path.parts[:-1]):
        return True
    return path.suffix.lower() not in _SOURCE_EXTENSIONS


def _resolved_directory(directory: object, repo: Path) -> Path:
    """The absolute directory c2cpg should use to resolve an entry's relative `file`.

    An absolute directory that exists on the Joern host is authoritative. A relative directory
    is resolved against the repository's PARENT so a non-root relative directory keeps its
    subpath (substituting the repo root here silently dropped the subpath and let a repo-relative
    `file` resolve to a non-existent path). A missing or absolute-but-nonexistent directory is
    anchored at the repo root so a repo-relative `file` can still resolve.
    """
    if isinstance(directory, str) and directory:
        candidate = Path(directory)
        if candidate.is_absolute():
            return candidate if candidate.is_dir() else repo
        return (repo.parent / candidate).resolve()
    return repo


# Include flags whose path is a separate following token, and the attached forms (-Ipath).
# Of these, c2cpg's compilation-database parser (JSONCompilationDatabaseParser.scala) only ever
# extracts `-I`: `arguments.filter(_.startsWith("-I"))` for an `arguments` entry, and the regex
# `-I(\S+)` for a `command` string -- both only match the path when it is in the SAME token as
# the flag (a separated `-I path` yields an empty string in `arguments` form, and no match at all
# in `command` form). `-isystem`/`-iquote`/`-idirafter`/`-imacros`/`-include`/`-cxx-isystem` are
# never read by c2cpg's include resolution at all; they are normalized here only for consistency
# with `-I`, not because c2cpg consults them.
#
# c2cpg resolves whatever it does extract against the JVM working directory, not the entry's
# `directory`, so a relative include is never found on the Joern host. That silently drops any
# code behind an unfound header -- for a node addon, the nan.h that defines NAN_METHOD is under a
# relative `-I../build_context/generated_headers/nan`, so every method declared with that macro
# (WrappedRE2::Split, the GT sink) fails to parse and cannot become a candidate. Anchoring each
# relative include at the entry directory, and merging a separated flag+path into one attached
# token so c2cpg's `-I` extraction actually sees it, recovers them.
_INCLUDE_SEP_FLAGS = frozenset(
    {"-I", "-isystem", "-iquote", "-idirafter", "-imacros", "-include", "-cxx-isystem"}
)
_INCLUDE_ATTACHED = ("-I", "-iquote", "-isystem", "-idirafter")


def _abs_include(path: str, directory: str) -> str:
    return path if os.path.isabs(path) else os.path.normpath(os.path.join(directory, path))


def _generated_header_dirs(cc_path: Path) -> list[Path]:
    """The sibling generated_headers directory and its immediate subdirs, resolved, if present.

    Some samples ship generated config headers (pnglibconf.h, config.h) under
    build_context/generated_headers but leave the compile command's -I pointing only at the repo
    root, so c2cpg never finds them. Then every function behind a config macro (libpng's
    PNG_READ_SUPPORTED) is #ifdef'd out and the file parses to no methods at all. Adding these
    directories to the include path recovers them. Absent directory means an empty list.
    """
    gh = cc_path.parent / "generated_headers"
    if not gh.is_dir():
        return []
    return [gh.resolve()] + [d.resolve() for d in sorted(gh.iterdir()) if d.is_dir()]


def _generated_header_includes(cc_path: Path) -> list[str]:
    """Attached `-I<abspath>` flags for `_generated_header_dirs` -- the only include form c2cpg's
    database parser reads. Empty when the directory is absent."""
    return ["-I" + str(d) for d in _generated_header_dirs(cc_path)]


def generated_header_dirs(cc_path: Path) -> list[str]:
    """Relative posix paths of the generated_header dirs the include path gains, for the run
    record. Empty when the convention (`cc_path.parent/generated_headers`) is absent, so a run can
    tell compile-aware-with-generated-headers from without rather than the attachment being a
    silent no-op the result JSON cannot reveal (review M2)."""
    base = cc_path.parent.resolve()
    return [d.relative_to(base).as_posix() for d in _generated_header_dirs(cc_path)]


def _absolutize_includes(entry: dict, directory: str) -> list[str]:
    """Rewrite the compile args so every relative include path is absolute (anchored at the
    entry directory), returning the rewritten argument list. A separated flag+path pair
    (`-I path`) is merged into one attached token (`-I<abspath>`) -- see the module-level comment
    above `_INCLUDE_SEP_FLAGS` for why the separated form does not reach c2cpg otherwise. Raises
    ValueError via `shlex.split` if `entry["command"]` has unbalanced quoting; the caller decides
    what to do with one malformed entry."""
    if isinstance(entry.get("arguments"), list):
        tokens = [str(t) for t in entry["arguments"]]
    else:
        tokens = shlex.split(entry.get("command", "") or "")
    out: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in _INCLUDE_SEP_FLAGS and i + 1 < len(tokens):
            out.append(token + _abs_include(tokens[i + 1], directory))
            i += 2
            continue
        for prefix in _INCLUDE_ATTACHED:
            if token.startswith(prefix) and len(token) > len(prefix):
                out.append(prefix + _abs_include(token[len(prefix):], directory))
                break
        else:
            out.append(token)
        i += 1
    return out


def _compiled_files(entries: list[dict], repo: Path) -> tuple[set[str], int]:
    """Files a database compiles, relative to the repo, and how many c2cpg would ignore."""
    files: set[str] = set()
    ignored = 0
    for entry in entries:
        name = entry.get("file")
        if not isinstance(name, str) or not name:
            continue
        path = Path(name)
        if not path.is_absolute():
            path = Path(entry["directory"]) / name
        try:
            rel = path.resolve().relative_to(repo).as_posix()
        except (ValueError, OSError):
            # An entry that points outside the repository is not a repo source we score.
            continue
        if _ignored_by_c2cpg(rel):
            # c2cpg drops this by folder/extension; counting it as expected would flag a
            # phantom "missing from CPG". Record the skip so the exclusion is auditable.
            ignored += 1
            continue
        files.add(rel)
    return files, ignored


def resolve_compilation_database(
    cc_path: Path, repo: Path, out_dir: Path
) -> tuple[Path, set[str], int]:
    """Normalize a compile_commands database and report the files it compiles.

    Returns the path to a normalized copy (every entry's `directory` made absolute and every
    relative include path anchored at it -- see `_absolutize_includes`), the set of compiled
    files relative to the repository (with c2cpg's ignored folders and non-source extensions
    already removed), and the count of database entries c2cpg would ignore by those rules. Each
    entry keeps its original `arguments`-array-vs-`command`-string shape: c2cpg extracts `-D`
    macro values differently per shape (an `arguments` token splits cleanly on `=`; a `command`
    string only keeps a quoted value), so collapsing every entry into `command` strings would
    silently drop `-D` values for `arguments`-shaped entries. The copy is written under
    `out_dir`, the same shared location the file-transport payloads use, so a remote Joern can
    read it. Raises OSError/ValueError/json.JSONDecodeError on a missing or unusable database;
    the caller falls back to importing every source file. A single entry with unparsable
    `command` quoting does not raise: that entry is kept unmodified (matching pre-absolutize
    behaviour) so it alone doesn't take every other entry's compile-aware import down with it.
    """
    repo = repo.resolve()
    raw = json.loads(cc_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("compile_commands.json is empty or not a JSON array")
    gen_includes = _generated_header_includes(cc_path)
    entries: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
            continue
        directory = str(_resolved_directory(entry.get("directory"), repo))
        try:
            tokens = _absolutize_includes(entry, directory)
        except ValueError:
            # Unbalanced quoting: keep the entry untokenized so it alone doesn't take the whole
            # compile-aware import down. Still attach the generated-header dirs, or this one TU
            # loses its config macros while every well-formed entry gets them (review L1). The
            # appended tokens are well-formed regardless of the entry's own quoting.
            kept = {**entry, "directory": directory}
            if gen_includes:
                if isinstance(entry.get("arguments"), list):
                    kept["arguments"] = list(entry["arguments"]) + gen_includes
                elif isinstance(entry.get("command"), str):
                    kept["command"] = entry["command"] + " " + shlex.join(gen_includes)
            entries.append(kept)
            continue
        # Append the sibling generated-header dirs as the LOWEST-priority includes: they recover a
        # header the command references nowhere (libpng pnglibconf.h), which a trailing -I finds,
        # without shadowing a header the command already resolves with a same-named one under
        # generated_headers (review M1). c2cpg extracts -I regardless of token position; order
        # among -I flags is the include search priority, so last means only-if-not-found-earlier.
        if gen_includes:
            tokens = tokens + gen_includes
        if isinstance(entry.get("arguments"), list):
            entry = {**entry, "directory": directory, "arguments": tokens}
            entry.pop("command", None)
        else:
            entry = {**entry, "directory": directory, "command": shlex.join(tokens)}
        entries.append(entry)
    if not entries:
        raise ValueError("compile_commands.json has no usable entries")
    compiled, ignored = _compiled_files(entries, repo)
    if not compiled:
        raise ValueError("no compile_commands entry resolves inside the repository")
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(entries)
    # Content-addressed name so distinct databases never collide in a shared dir; identical
    # content maps to one file. Write to a unique temp then rename so a concurrent writer of the
    # same name can never observe a half-written file.
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    normalized = out_dir / f"compile_commands.resolved.{digest}.json"
    tmp = out_dir / f".compile_commands.{uuid.uuid4().hex}.tmp"
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, normalized)
    return normalized.resolve(), compiled, ignored
