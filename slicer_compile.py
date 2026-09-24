"""Compile-aware CPG input.

Hand c2cpg the project's compile_commands so it builds the graph from the translation
units the build actually compiles, with their real flags. c2cpg reads the database itself
through its `--compilation-database` frontend option, so no external preprocessor runs.

Two things follow from doing this, both measured on the release eval:

- Uncompiled files and dead `#ifdef` branches leave the graph. WavPack's `get_word3`, whose
  whole file sits under an unset `ENABLE_LEGACY`, disappears while the live functions stay,
  and the method count drops from the raw import's noise (unpack3.c: 10 phantom methods to 0)
  without pulling in system headers.
- Macro-expanded and included-template functions that the raw parse missed become real.

The database's `file` entries resolve against each entry's `directory`. Release samples ship a
relative directory ("repo") that does not resolve on the Joern host, so every directory is
rewritten to the absolute repository path before the copy is handed to c2cpg.

The normalized copy is content-addressed and written atomically: two slicer runs that share an
output parent (parallel slicing writes every sample under one payload dir) would otherwise
overwrite one fixed filename and hand each other the wrong translation-unit set.
"""
import hashlib
import json
import os
import uuid
from pathlib import Path


def _compiled_files(entries: list[dict], repo: Path) -> set[str]:
    files: set[str] = set()
    for entry in entries:
        name = entry.get("file")
        if not isinstance(name, str) or not name:
            continue
        path = Path(name)
        if not path.is_absolute():
            path = Path(entry["directory"]) / name
        try:
            files.add(path.resolve().relative_to(repo).as_posix())
        except (ValueError, OSError):
            # An entry that points outside the repository is not a repo source we score.
            continue
    return files


def resolve_compilation_database(
    cc_path: Path, repo: Path, out_dir: Path
) -> tuple[Path, set[str]]:
    """Normalize a compile_commands database and report the files it compiles.

    Returns the path to a normalized copy (every entry's `directory` made absolute) and the
    set of compiled files relative to the repository. The copy is written under `out_dir`,
    the same shared location the file-transport payloads use, so a remote Joern can read it.
    Raises OSError/ValueError/json.JSONDecodeError on a missing or unusable database; the
    caller falls back to importing every source file.
    """
    repo = repo.resolve()
    raw = json.loads(cc_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("compile_commands.json is empty or not a JSON array")
    entries: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
            continue
        directory = entry.get("directory")
        if not (
            isinstance(directory, str)
            and Path(directory).is_absolute()
            and Path(directory).is_dir()
        ):
            # A relative or missing directory cannot resolve on the Joern host; anchor it at
            # the repo so a repo-relative `file` resolves.
            entry = {**entry, "directory": str(repo)}
        entries.append(entry)
    if not entries:
        raise ValueError("compile_commands.json has no usable entries")
    compiled = _compiled_files(entries, repo)
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
    return normalized.resolve(), compiled
