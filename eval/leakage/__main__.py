"""`python -m eval.leakage` — CI에서 도는 누출 검사 진입점.

**한 건이라도 걸리면 exit 1이고 그 run은 무효다**(evaluation.md §4). CI가 이 명령으로
막지 않으면 누출은 결과가 나온 뒤에야, 그것도 우연히 발견된다.

기본 검사 대상은 `prompts/`와 `{artifacts_root}/*/trace.jsonl`이다. 프롬프트가 기본에 들어
있는 것이 핵심이다 — 그 파일들은 계속 고쳐지고, 고치는 사람이 매번 CVE 금지 규칙을 기억할
이유는 없다.

**run 산출물 뿌리는 인자로 받는다**(`--artifacts-root`, R3). 저장소 안에 못박아 두면 ATOM
처럼 `--artifacts-root /srv/…`로 돈 run은 기본 호출에서 **한 건도 안 잡히고**, 대신 그
체크아웃에 남아 있던 옛 run이 잡힌다 — 같은 명령이 장비의 잔여물에 따라 다른 것을 검사한다.
그래서 출력이 **무엇을 훑었는지** 먼저 적는다.

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

# run 산출물 뿌리의 기본값. **`src.cli run --artifacts-root`와 같은 뜻이다** —
# `{artifacts_root}/{run_id}/…`(`cli._claim_run_dir`·`container_tools.scratch_dir()`).
# 같은 값을 그대로 이 명령에 넘길 수 있어야 "무엇을 검사했나"가 명령에서 읽힌다.
DEFAULT_ARTIFACTS_ROOT = REPO_ROOT / "artifacts"

# trace JSONL은 run마다 생기므로 glob으로 모은다. **뿌리 기준 상대 경로다** — 여기에
# `artifacts/`를 다시 붙이면 `--artifacts-root`가 가리키는 곳을 못 찾는다.
TRACE_GLOB = "*/trace.jsonl"

# **run 산출물 프롬프트**(open-questions D19). 2026-08-22까지 이 스캐너가 본 프롬프트는
# `prompts/` 아래 **템플릿**뿐이었다 — 즉 대상 코드에서 흘러들어온 문자열은 한 번도
# 검사된 적이 없다. `{{SOURCE}}`·`{{CAPSULE_SECTIONS}}`가 렌더된 뒤의 텍스트가 여기 있고,
# CVE 번호·문제명이 실제로 섞일 수 있는 곳은 템플릿이 아니라 이쪽이다.
#
# evaluation.md §4의 `Prompt Logging` 행이 요구하는 **"로그 존재 검사"는 아직 아니다** —
# 여기 있는 것은 "있으면 검사한다"이고, 없으면 그냥 대상이 0건이다(R3).
RUN_GLOBS = (TRACE_GLOB, "*/prompts/*.md")

# 문제명 목록. 없으면 문제명 검사만 비활성이다.
DEFAULT_PROBLEM_NAMES = REPO_ROOT / "eval" / "datasets" / "problem_names.txt"


@dataclass(frozen=True)
class Report:
    leaks: list[Leak]
    overlaps: list[Overlap]
    scanned: int
    intersection_checked: bool
    scan_roots: tuple[Path, ...]
    artifacts_root: Path
    run_artifacts: int

    @property
    def failed(self) -> bool:
        return bool(self.leaks or self.overlaps)

    def render(self) -> str:
        # **무엇을 훑었는지 먼저 적는다.** R3가 남긴 문장이 *"「검사했다」가 무엇을 검사한
        # 것인지 명령만 보고 알 수 없다"*이고, 뿌리를 인자로 뺀 것만으로는 그 문장이 안
        # 닫힌다 — 인자를 **안 준** 호출이 여전히 다수이기 때문이다. 출력이 답한다.
        roots = " · ".join(str(path) for path in self.scan_roots) or "(없음)"
        lines = [
            f"[대상] --scan: {roots}",
            f"[대상] --artifacts-root: {self.artifacts_root}"
            f" — run 산출물 {self.run_artifacts}건",
            f"[스캔] 경로 {self.scanned}개",
        ]
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
    artifacts_root: Path,
    kb: Path | None,
    evalset: Path | None,
    problem_names: Path,
) -> Report:
    """마스킹 스캔과 digest 교집합 검사를 한 번에 돌린다.

    ⚠ **`artifacts_root`를 검증하지 않는다.** 없는 뿌리는 예외가 아니라 0건이 된다
    (`Path.glob()`이 그렇다). "명시한 뿌리가 디렉터리여야 한다"는 규약은 `main()`에 있고
    여기에는 없다 — 다른 호출자를 붙이려면 그 검사를 같이 가져가야 한다.
    """
    names = load_problem_names(problem_names)
    found = sorted(path for pattern in RUN_GLOBS for path in artifacts_root.glob(pattern))
    targets = [*scan, *found]
    leaks = scan_paths(targets, problem_names=names)
    checked = kb is not None and evalset is not None and kb.exists() and evalset.exists()
    overlaps = intersecting(digests(kb), digests(evalset)) if checked else []
    return Report(
        leaks=leaks,
        overlaps=overlaps,
        scanned=sum(1 for path in targets if path.exists()),
        intersection_checked=checked,
        scan_roots=tuple(scan),
        artifacts_root=artifacts_root,
        run_artifacts=len(found),
    )


def main(argv: Sequence[str] | None = None) -> int:
    force_utf8(sys.stdout)
    force_utf8(sys.stderr)
    parser = argparse.ArgumentParser(prog="python -m eval.leakage")
    parser.add_argument(
        "--scan",
        type=Path,
        action="append",
        help="추가로 훑을 경로. 기본은 prompts/ 와 {artifacts_root}/*/trace.jsonl",
    )
    # ⚠ **`type=Path`를 쓰지 않는다.** argparse가 `""`를 `Path(".")`로 **조용히** 바꾸고
    # (실측 3.14), 그러면 아래 `is_dir()`이 참이라 통과해 **현재 디렉터리**를 run 뿌리로
    # 훑는다 — `--artifacts-root "$ATOM_ARTIFACTS"`에서 변수가 비면 나는 모양이고, 그것이
    # 정확히 이 모듈이 막겠다고 적은 "오타 하나가 통과를 만든다"다. 문자열로 받아야
    # 조작자가 준 것이 빈 값이었는지 `.`이었는지 구별된다.
    parser.add_argument(
        "--artifacts-root",
        help="run 산출물 뿌리. `src.cli run --artifacts-root`와 같은 값 (기본: ./artifacts)",
    )
    parser.add_argument("--kb", type=Path, help="KB 인덱스 디렉터리 또는 digest 매니페스트(.json)")
    parser.add_argument("--evalset", type=Path, help="평가셋 디렉터리 또는 digest 매니페스트")
    parser.add_argument("--problem-names", type=Path, default=DEFAULT_PROBLEM_NAMES)
    args = parser.parse_args(argv)

    # **명시한 뿌리가 디렉터리가 아니면 오류다.** 오타 하나가 "run 산출물 0건 · 통과"를
    # 만들면 이 스캐너를 고친 뜻이 사라진다 — "검사했고 깨끗함"과 "검사할 것이 없었음"을
    # 안 뭉개는 것이 이 모듈의 규약이다(모듈 docstring, digest 교집합의 `[생략]`).
    # **기본 뿌리가 없는 것은 오류가 아니다** — run을 한 번도 안 돌린 체크아웃이 그렇고,
    # CI가 정확히 그 상태로 이 명령을 돈다.
    #
    # ⚠ **여기서 막는 것은 「디렉터리가 아닌 값」까지다.** 존재하는 **엉뚱한** 디렉터리
    # (옆 뿌리를 가리킨 오타)는 못 가른다 — 그것을 답하는 것은 `render()`가 찍는 경로와
    # run 산출물 건수이지 이 분기가 아니다. 성질을 못 무는 자리는 적어 둔다(§2.5 끝절).
    artifacts_root = DEFAULT_ARTIFACTS_ROOT
    if args.artifacts_root is not None:
        artifacts_root = Path(args.artifacts_root)
        if not args.artifacts_root or not artifacts_root.is_dir():
            print(
                f"[ERROR] --artifacts-root 가 디렉터리가 아니다: {args.artifacts_root!r}",
                file=sys.stderr,
            )
            return 2

    report = check(
        scan=args.scan or list(DEFAULT_SCAN),
        artifacts_root=artifacts_root,
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
