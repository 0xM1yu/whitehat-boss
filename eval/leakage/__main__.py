"""`python -m eval.leakage` — CI에서 도는 누출 검사 진입점.

**한 건이라도 걸리면 exit 1이고 그 run은 무효다**(evaluation.md §4). CI가 이 명령으로
막지 않으면 누출은 결과가 나온 뒤에야, 그것도 우연히 발견된다.

기본 검사 대상은 `prompts/`와 `artifacts/*/trace.jsonl`이다. 프롬프트가 기본에 들어 있는
것이 핵심이다 — 그 파일들은 계속 고쳐지고, 고치는 사람이 매번 CVE 금지 규칙을 기억할
이유는 없다.

digest 교집합 검사는 매니페스트가 있을 때만 돈다. 없으면 **SKIP으로 표시하고 통과시킨다** —
아직 KB도 평가셋도 없기 때문이다. "검사했고 깨끗함"과 "검사할 것이 없었음"을 같은
초록불로 뭉개지 않으려고 출력에서 구분한다.
"""

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from eval.leakage.hash_intersection import Overlap, digests, intersecting
from eval.leakage.id_masking import Leak, load_problem_names, scan_paths
from src.console import force_utf8

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# 기본 스캔 대상. 프롬프트는 반드시 포함한다(모듈 docstring 참조).
DEFAULT_SCAN = (REPO_ROOT / "prompts",)

# trace JSONL은 run마다 생기므로 glob으로 모은다.
TRACE_GLOB = "artifacts/*/trace.jsonl"

# **run 산출물 프롬프트**(open-questions D19). 2026-08-22까지 이 스캐너가 본 프롬프트는
# `prompts/` 아래 **템플릿**뿐이었다 — 즉 대상 코드에서 흘러들어온 문자열은 한 번도
# 검사된 적이 없다. `{{SOURCE}}`·`{{CAPSULE_SECTIONS}}`가 렌더된 뒤의 텍스트가 여기 있고,
# CVE 번호·문제명이 실제로 섞일 수 있는 곳은 템플릿이 아니라 이쪽이다.
#
# evaluation.md §4의 `Prompt Logging` 행이 요구하는 **"로그 존재 검사"는 아직 아니다** —
# 여기 있는 것은 "있으면 검사한다"이고, 없으면 그냥 대상이 0건이다(R3).
RUN_GLOBS = (TRACE_GLOB, "artifacts/*/prompts/*.md")

# 문제명 목록. 없으면 문제명 검사만 비활성이다.
DEFAULT_PROBLEM_NAMES = REPO_ROOT / "eval" / "datasets" / "problem_names.txt"


@dataclass(frozen=True)
class Report:
    leaks: list[Leak]
    overlaps: list[Overlap]
    scanned: int
    intersection_checked: bool

    @property
    def failed(self) -> bool:
        return bool(self.leaks or self.overlaps)

    def render(self) -> str:
        lines = [f"[스캔] 경로 {self.scanned}개"]
        if self.leaks:
            lines.append(f"[실패] 마스킹 위반 {len(self.leaks)}건")
            lines.extend(f"  {leak.render()}" for leak in self.leaks)
        else:
            lines.append("[통과] CVE·flag·문제명·patch diff 없음")
        if not self.intersection_checked:
            lines.append("[생략] digest 교집합 — 매니페스트가 없다 (KB·평가셋 미구축)")
        elif self.overlaps:
            lines.append(f"[실패] KB ∩ 평가셋 {len(self.overlaps)}건")
            lines.extend(f"  {overlap.render()}" for overlap in self.overlaps)
        else:
            lines.append("[통과] KB와 평가셋의 digest 교집합 없음")
        return "\n".join(lines)


def check(
    *,
    scan: Sequence[Path],
    kb: Path | None,
    evalset: Path | None,
    problem_names: Path,
) -> Report:
    """마스킹 스캔과 digest 교집합 검사를 한 번에 돌린다."""
    names = load_problem_names(problem_names)
    found = [path for pattern in RUN_GLOBS for path in REPO_ROOT.glob(pattern)]
    targets = [*scan, *sorted(found)]
    leaks = scan_paths(targets, problem_names=names)
    checked = kb is not None and evalset is not None and kb.exists() and evalset.exists()
    overlaps = intersecting(digests(kb), digests(evalset)) if checked else []
    return Report(
        leaks=leaks,
        overlaps=overlaps,
        scanned=sum(1 for path in targets if path.exists()),
        intersection_checked=checked,
    )


def main(argv: Sequence[str] | None = None) -> int:
    force_utf8(sys.stdout)
    force_utf8(sys.stderr)
    parser = argparse.ArgumentParser(prog="python -m eval.leakage")
    parser.add_argument(
        "--scan",
        type=Path,
        action="append",
        help="추가로 훑을 경로. 기본은 prompts/ 와 artifacts/*/trace.jsonl",
    )
    parser.add_argument("--kb", type=Path, help="KB 인덱스 디렉터리 또는 digest 매니페스트(.json)")
    parser.add_argument("--evalset", type=Path, help="평가셋 디렉터리 또는 digest 매니페스트")
    parser.add_argument("--problem-names", type=Path, default=DEFAULT_PROBLEM_NAMES)
    args = parser.parse_args(argv)

    report = check(
        scan=args.scan or list(DEFAULT_SCAN),
        kb=args.kb,
        evalset=args.evalset,
        problem_names=args.problem_names,
    )
    print(report.render())
    if report.failed:
        print("\n누출이 있는 run의 비교 결과는 무효다 (evaluation.md §4).", file=sys.stderr)
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
