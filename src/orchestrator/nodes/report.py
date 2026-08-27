"""9단계 Dedupe & Report — 규칙 기반 중복 제거와 템플릿 리포트.

architecture.md §3의 9단계는 "규칙 기반 중복 제거, 템플릿 리포트 생성"이다. 모델을
부르지 않는다 — 리포트 문구가 모델마다 달라지면 조건 간 비교에서 사람이 읽는 산출물이
흔들린다.

**`proven`을 `reported`로 자동 전이시키지 않는다.** 전이 표 F-15는 사람 2인 검증으로만
일어난다(disclosure-policy.md). 이 노드는 상태를 바꾸지 않고 읽기만 한다.
"""

from collections import Counter
from dataclasses import dataclass, field

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

# 등급 대표 우선순위 (D35). 값이 작을수록 높은 등급이라 dedupe에서 대표로 남는다.
# 문자열 비교("E10" > "E3" 같은 함정)를 피하려 명시 순서로 둔다.
_GRADE_ORDER = {"E4": 0, "E3": 1, "E2": 2, "E1": 3, "E0": 4}
# 등급을 못 받은 Finding의 기본값. `evidence_refs`가 없으면 게이트도 E0을 준다.
_DEFAULT_GRADE = "E0"
# 요약 줄에 등급을 높은 것부터 적는다 (D36).
_GRADE_DISPLAY_ORDER = ("E4", "E3", "E2", "E1", "E0")


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
    # finding_id → 등급 값("E3" 등) (D35·D36). **러너가 `gate.evaluate`로 계산해 넘긴다.**
    # Finding에 등급 필드가 없어 등급은 게이트 판정이라 trace에만 있었고, 그래서 이 노드가
    # 대표를 고를 때 등급을 못 봐 E3가 사라졌고(D35) 최종 산출물이 등급을 못 실었다(D36).
    # 노드가 다시 매기지 않고 러너 값을 받는 것은 `deferred`·`critic_deferred`와 같은 이유다.
    grades: dict[str, str] = field(default_factory=dict)

    def _grade_of(self, finding: Finding) -> str:
        return self.grades.get(finding.finding_id, _DEFAULT_GRADE)

    def render(self) -> str:
        lines = [f"# 취약점 분석 리포트 — {self.run_id}", ""]
        # **등급 분포를 맨 위에 싣는다 (D36).** 이 run의 성과(예: 8단계 E3 8건)가 여기
        # 없으면 리포트만 읽는 사람은 trace를 파야 그것을 본다 — F-9가 상태를 안 바꿔
        # E3가 `queued`로 찍히기 때문이다.
        if self.findings:
            dist = Counter(self._grade_of(f) for f in self.findings)
            parts = [f"{g} {dist[g]}건" for g in _GRADE_DISPLAY_ORDER if dist[g]]
            if parts:
                lines += [f"등급 분포: {' · '.join(parts)}", ""]
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
            lines += [_entry(finding, self._grade_of(finding)) for finding in group]
            lines.append("")
        if not self.findings:
            lines += ["보고할 Finding이 없다.", ""]
        return "\n".join(lines)


def dedupe(
    findings: list[Finding], grades: dict[str, str] | None = None
) -> tuple[list[Finding], int]:
    """같은 CWE·같은 source→sink는 한 건으로 본다.

    **대표는 등급이 높은 것을 남긴다 (D35).** 예전에는 `finding_id` 오름차순으로 처음
    것을 남겨 등급을 안 봤고, 그래서 E3가 E0/E2 대표에 밀려 리포트에서 사라졌다(실측
    6건). 등급 우선으로 바꿔도 동급은 `finding_id`로 가르므로 **재현성은 그대로다** —
    어느 것이 남는지가 실행마다 흔들리지 않는다.

    **키에 source 심볼도 넣는다 (D35).** sink만 보면 저장소 전역에서 `memcpy`를 쓰는
    서로 다른 함수가 같은 CWE로 한 건이 됐다 — source→sink 쌍이라야 다른 함수가 안 뭉친다.
    """
    grades = grades or {}
    kept: list[Finding] = []
    seen: set[tuple[str, str, str]] = set()
    ordered = sorted(
        findings,
        key=lambda f: (_GRADE_ORDER.get(grades.get(f.finding_id, _DEFAULT_GRADE), 4), f.finding_id),
    )
    for finding in ordered:
        key = _dedupe_key(finding)
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
    grades: dict[str, str] | None = None,
) -> Report:
    """중복을 제거하고 리포트 객체를 만든다. 상태는 바꾸지 않는다.

    `deferred`·`dropped`·`critic_deferred`는 러너가 센 값을 그대로 받는다 — 노드가 다시
    세면 경고와 리포트가 다른 수를 말할 수 있다. `critic_top_n`도 같은 이유로 받는다:
    노드가 `ctx.config`에서 다시 읽으면 러너의 경고와 갈릴 여지가 생긴다. `grades`도 같다 —
    등급은 게이트 판정이라 러너가 `gate.evaluate`로 매긴 값을 받고 여기서 다시 매기지 않는다.
    """
    grades = grades or {}
    with ctx.stage(Stage.REPORT):
        kept, duplicates = dedupe(findings, grades)
    return Report(
        run_id=ctx.run_id,
        findings=tuple(kept),
        duplicates=duplicates,
        deferred=deferred,
        dropped=dropped,
        critic_deferred=critic_deferred,
        critic_top_n=critic_top_n,
        grades=dict(grades),
    )


def _dedupe_key(finding: Finding) -> tuple[str, str, str]:
    """(CWE, source 심볼, sink 심볼). 경로가 없으면 제목으로 양쪽을 대신한다 (D35).

    sink만이 아니라 source도 넣는 것이 요점이다 — 같은 sink·같은 CWE라도 그 경로가 시작된
    함수가 다르면 다른 Finding이다.
    """
    if finding.path:
        return (finding.cwe, finding.path[0].symbol, finding.path[-1].symbol)
    return (finding.cwe, finding.title, finding.title)


def _entry(finding: Finding, grade: str) -> str:
    # **앞머리에 등급을 박는다 (D36).** state(`queued` 등)만 찍으면 E3에 도달한 Finding도
    # `queued`로 보인다 — F-9가 상태를 안 바꾸기 때문이다. 등급이 이 run의 성과다.
    return (
        f"- **[{grade}] {finding.title}** ({finding.cwe}, confidence {finding.confidence:.2f})\n"
        f"  - id: `{finding.finding_id}` · 근거 {len(finding.evidence_refs)}건"
    )
