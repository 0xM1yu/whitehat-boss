"""9단계 Dedupe & Report — 규칙 기반 중복 제거와 템플릿 리포트.

architecture.md §3의 9단계는 "규칙 기반 중복 제거, 템플릿 리포트 생성"이다. 모델을
부르지 않는다 — 리포트 문구가 모델마다 달라지면 조건 간 비교에서 사람이 읽는 산출물이
흔들린다.

**`proven`을 `reported`로 자동 전이시키지 않는다.** 전이 표 F-15는 사람 2인 검증으로만
일어난다(disclosure-policy.md). 이 노드는 상태를 바꾸지 않고 읽기만 한다.
"""

from dataclasses import dataclass

from src.orchestrator.context import RunContext
from src.orchestrator.stages import Stage
from src.schemas.enums import FindingState
from src.schemas.finding import Finding

# 리포트에 싣는 상태 순서. 위에 있을수록 먼저 나온다.
_STATE_ORDER = (
    FindingState.PROVEN,
    FindingState.VALIDATED,
    FindingState.HYPOTHESIZED,
    FindingState.QUEUED,
)


@dataclass(frozen=True)
class Report:
    """리포트 한 건. 렌더링은 `render()`가 하고 지표 집계는 trace에서 따로 한다."""

    run_id: str
    findings: tuple[Finding, ...]
    duplicates: int

    def render(self) -> str:
        lines = [f"# 취약점 분석 리포트 — {self.run_id}", ""]
        if self.duplicates:
            lines += [f"중복 제거: {self.duplicates}건", ""]
        for state in _STATE_ORDER:
            group = [f for f in self.findings if f.state is state]
            if not group:
                continue
            lines += [f"## {state.value} ({len(group)}건)", ""]
            lines += [_entry(finding) for finding in group]
            lines.append("")
        if not self.findings:
            lines += ["보고할 Finding이 없다.", ""]
        return "\n".join(lines)


def dedupe(findings: list[Finding]) -> tuple[list[Finding], int]:
    """같은 CWE·같은 sink 경로는 한 건으로 본다.

    `finding_id` 순서로 처음 것을 남긴다 — 어느 것이 남는지가 실행마다 달라지면
    리포트를 실행 간에 대조할 수 없다.
    """
    kept: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for finding in sorted(findings, key=lambda f: f.finding_id):
        key = (finding.cwe, _sink(finding))
        if key in seen:
            continue
        seen.add(key)
        kept.append(finding)
    return kept, len(findings) - len(kept)


def run(ctx: RunContext, *, findings: list[Finding]) -> Report:
    """중복을 제거하고 리포트 객체를 만든다. 상태는 바꾸지 않는다."""
    with ctx.stage(Stage.REPORT):
        kept, duplicates = dedupe(findings)
    return Report(run_id=ctx.run_id, findings=tuple(kept), duplicates=duplicates)


def _sink(finding: Finding) -> str:
    """경로의 마지막 심볼. 경로가 없으면 제목으로 대신한다."""
    return finding.path[-1].symbol if finding.path else finding.title


def _entry(finding: Finding) -> str:
    return (
        f"- **{finding.title}** ({finding.cwe}, confidence {finding.confidence:.2f})\n"
        f"  - id: `{finding.finding_id}` · 근거 {len(finding.evidence_refs)}건"
    )
