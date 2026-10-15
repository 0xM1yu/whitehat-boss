"""#377 (b) `--description`: the task description names files; those files are sliced first.

Order only. The description is never trusted: one that matches no file leaves the run what it was
without the flag (a wrong one that still matches some file moves those files first).
`src/orchestrator/description_match.py` holds the same token rule for the runner's `candidate_max`
cut (`src` does not import the slicer, so it is written twice;
tests/test_description_priority.py pins that the two agree).
"""

import os
import re
from pathlib import Path

from slicer_source import SOURCE_SUFFIXES

IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
MIN_TOKEN = 4
# English function words and the verbs bug descriptions are phrased with. Not a tuned list.
STOPWORDS = frozenset(
    "this that these those with from when where which while then than there their them they "
    "into onto over also been being have does would could should will were what some such only "
    "after before because cause causes caused return returns returned initialize uses used "
    "using allows leads".split()
)


def description_tokens(text: str) -> frozenset[str]:
    """Lower-cased identifiers of at least MIN_TOKEN characters, minus STOPWORDS."""
    found = (match.lower() for match in IDENTIFIER.findall(text))
    return frozenset(t for t in found if len(t) >= MIN_TOKEN and t not in STOPWORDS)


def expand(tokens: frozenset[str]) -> frozenset[str]:
    """The tokens plus their `_`-separated parts of MIN_TOKEN+ characters (`file_regexec` ->
    `regexec`), so a description that spells the whole identifier still finds `regexec.c`."""
    parts = (p for t in tokens for p in t.split("_") if len(p) >= MIN_TOKEN and p not in STOPWORDS)
    return tokens | frozenset(parts)


def matching_files(tokens: frozenset[str], repo: Path) -> set[str]:
    """Repo-relative C/C++ files whose name without extension is a token or a part of one.

    Only names are read, never file contents, and symlinks are not followed (the same policy as
    Slicer.load_sources). Inaccessible directories are skipped: a file missed here is not boosted,
    which is the no-flag order.
    """
    root = repo.resolve()
    names: set[str] = set()
    if not tokens:
        return names
    tokens = expand(tokens)
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d != ".git")
        for name in files:
            stem, suffix = os.path.splitext(name)
            if suffix.lower() not in SOURCE_SUFFIXES or stem.lower() not in tokens:
                continue
            path = Path(directory) / name
            if not path.is_symlink():
                names.add(path.relative_to(root).as_posix())
    return names
