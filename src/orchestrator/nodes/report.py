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
    # 4단계에 못 올라간 target 수 (W2). **"무엇을 찾았는가"만 적고 "무엇을 안 봤는가"를
    # 빼면, 예산에 잘린 run의 리포트가 전량을 본 run의 리포트와 같은 얼굴을 한다** —
    # `index_graph`가 "없다 vs 0건"에 대해, `retrieval_truncated`가 "이벤트 부재는
    # 안 잘림"에 대해 거부한 것과 같은 모양이다.
    #
    # 목록이 아니라 수만 싣는다. 목록은 trace의 `target_skipped`에 있고, 리포트는
    # 사람이 읽는 요약이라 수백 줄을 여기 옮기면 Finding이 묻힌다.
    deferred: int = 0
    dropped: int = 0
    # 7단계에 못 올라간 Finding 수 (D26 (b)). **위 둘과 같은 줄에 넣지 않는다** — 그쪽 단위는
    # target이고 이쪽은 Finding이다. 사람이 읽는 산출물에서 분모를 섞는 것이 trace에서
    # 섞는 것보다 나쁘다.
    #
    # 이 줄이 없으면 절단된 Finding이 **안 보이는 게 아니라 잘못 보인다.** F-9가
    # `hypothesized → hypothesized`라 7단계에 간 건과 못 간 건이 아래 같은 묶음에 나란히
    # 찍힌다 — 실측 `run_20260825_0001`에서 `hypothesized 44건` 중 **36건이 7단계에 못
    # 갔는데** 리포트에 그 사실이 없었다.
    critic_deferred: int = 0
    # 위 수를 만든 상한. **값을 함께 싣는다** — 러너의 run 경고가 `critic_top_n(8)`처럼
    # 값을 찍으므로 이름만 적으면 두 산출물이 같은 사실을 다르게 말한다. 그리고 report.md는
    # config 없이 단독으로 읽히는 문서라 여기 없으면 "36이 상한 8의 결과인지 80의 결과인지"를
    # 이 파일만 보고는 알 수 없다.
    critic_top_n: int = 0

    def render(self) -> str:
        lines = [f"# 취약점 분석 리포트 — {self.run_id}", ""]
        if self.duplicates:
            lines += [f"중복 제거: {self.duplicates}건", ""]
        if self.deferred or self.dropped:
            lines += [
                f"분석하지 못한 대상: 예산 초과 {self.deferred}건 · 호출 실패 "
                f"{self.dropped}건 (사유는 trace의 `target_skipped`)",
                "",
            ]
        if self.critic_deferred:
            # **앞머리 명사구에 잘린 사유를 넣는다.** "반박받지 못한 Finding"으로 적으면
            # `insufficient_evidence`로 6단계에서 떨어진 건(실측 11건)도 같은 이름에 들어가
            # 이 수가 47이어야 맞는 문장이 된다. 사유가 다른 둘을 한 이름으로 묶으면 D26이
            # 지적하는 구별이 리포트에서 다시 합쳐진다 — `target_skipped`가 `reason`을 필드로
            # 두는 것과 같은 이유다.
            lines += [
                f"`critic_top_n`으로 잘린 Finding: {self.critic_deferred}건 "
                f"(상한 {self.critic_top_n} · 목록은 trace의 `critic_skipped`)",
                "",
            ]
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


def run(
    ctx: RunContext,
    *,
    findings: list[Finding],
    deferred: int = 0,
    dropped: int = 0,
    critic_deferred: int = 0,
    critic_top_n: int = 0,
) -> Report:
    """중복을 제거하고 리포트 객체를 만든다. 상태는 바꾸지 않는다.

    `deferred`·`dropped`·`critic_deferred`는 러너가 센 값을 그대로 받는다 — 노드가 다시
    세면 경고와 리포트가 다른 수를 말할 수 있다. `critic_top_n`도 같은 이유로 받는다:
    노드가 `ctx.config`에서 다시 읽으면 러너의 경고와 갈릴 여지가 생긴다.
    """
    with ctx.stage(Stage.REPORT):
        kept, duplicates = dedupe(findings)
    return Report(
        run_id=ctx.run_id,
        findings=tuple(kept),
        duplicates=duplicates,
        deferred=deferred,
        dropped=dropped,
        critic_deferred=critic_deferred,
        critic_top_n=critic_top_n,
    )


def _sink(finding: Finding) -> str:
    """경로의 마지막 심볼. 경로가 없으면 제목으로 대신한다."""
    return finding.path[-1].symbol if finding.path else finding.title


def _entry(finding: Finding) -> str:
    return (
        f"- **{finding.title}** ({finding.cwe}, confidence {finding.confidence:.2f})\n"
        f"  - id: `{finding.finding_id}` · 근거 {len(finding.evidence_refs)}건"
    )
