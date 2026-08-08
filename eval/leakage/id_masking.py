"""CVE ID·문제명·flag 마스킹 검사 (evaluation.md §4).

프롬프트나 캡슐에 CVE 번호가 남아 있으면 모델은 코드를 읽는 대신 그 번호를 기억해 낸다.
그 run에서 나온 발견율은 검색 능력의 측정치이지 분석 능력의 측정치가 아니다.

**검사 대상에 `prompts/`를 반드시 포함한다.** agent-contracts.md §4는 "프롬프트에 평가
정답 단서(CVE ID, 문제명, patch diff)를 넣지 않는다"고 정했고 C4에서 그 약속을 지켰다.
약속을 사람이 아니라 스크립트가 지키게 하는 것이 이 검사의 목적이다 — 프롬프트는 앞으로
계속 고쳐질 파일이고, 고치는 사람이 매번 이 규칙을 기억할 이유는 없다.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

# 검사 대상 파일 확장자. 바이너리를 훑으면 오탐만 늘고 느려진다.
TEXT_SUFFIXES = (".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".py", ".c", ".h")

# 이름 → 정규식. 이름이 그대로 보고서에 찍히므로 무엇에 걸렸는지 바로 읽힌다.
PATTERNS: dict[str, re.Pattern[str]] = {
    "cve_id": re.compile(r"CVE-\d{4}-\d{4,7}"),
    # flag{...}, FLAG{...}, ctf_flag{...} 등 대회 제출 형식.
    "flag": re.compile(r"[A-Za-z0-9_]{0,16}flag\s*\{[^}\n]{0,120}\}", re.IGNORECASE),
    # patch diff 헤더. 정답 패치가 캡슐이나 프롬프트에 섞여 들어온 경우다.
    "patch_diff": re.compile(r"^(\+\+\+ b/|--- a/|@@ -\d)", re.MULTILINE),
}


@dataclass(frozen=True)
class Leak:
    """누출 한 건. 어느 파일 몇 번째 줄에서 무엇이 걸렸는가."""

    path: Path
    line: int
    pattern: str
    matched: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: [{self.pattern}] {self.matched}"


def scan_text(text: str, *, path: Path, problem_names: Sequence[str] = ()) -> list[Leak]:
    """한 문서에서 누출을 찾는다. 줄 번호를 함께 돌려준다 — 고치려면 위치가 필요하다."""
    leaks = [
        Leak(path=path, line=_line_of(text, match.start()), pattern=name, matched=match.group(0))
        for name, pattern in PATTERNS.items()
        for match in pattern.finditer(text)
    ]
    lowered = text.lower()
    leaks.extend(
        Leak(
            path=path,
            line=_line_of(text, lowered.index(name.lower())),
            pattern="problem_name",
            matched=name,
        )
        for name in problem_names
        if name and name.lower() in lowered
    )
    return sorted(leaks, key=lambda leak: (leak.line, leak.pattern))


def scan_paths(roots: Iterable[Path], *, problem_names: Sequence[str] = ()) -> list[Leak]:
    """디렉터리·파일 목록을 훑는다. 없는 경로는 결과에 나타나지 않는다."""
    leaks: list[Leak] = []
    for path in roots:
        for file in _text_files(path):
            leaks.extend(
                scan_text(
                    file.read_text(encoding="utf-8", errors="replace"),
                    path=file,
                    problem_names=problem_names,
                )
            )
    return leaks


def load_problem_names(path: Path) -> list[str]:
    """한 줄에 하나씩 적힌 문제명 목록. `#`로 시작하는 줄은 주석이다.

    목록을 코드에 박지 않는 것은 대회·데이터셋마다 다르기 때문이다. 파일이 없으면
    빈 목록이고, 그때는 문제명 검사만 비활성이며 CVE·flag 검사는 그대로 돈다.
    """
    if not path.is_file():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _text_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        return []
    return sorted(p for p in path.rglob("*") if p.is_file() and p.suffix in TEXT_SUFFIXES)


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1
