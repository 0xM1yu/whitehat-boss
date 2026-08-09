"""8단계 Tool Validation — 재현 명세를 샌드박스에서 실행한다.

전이 표 F-10(재현 성공) / F-11(실패) / F-12(tool_policy 위반).

**`expected_signal` 매칭은 도구 선택 시점에 한다.** 게이트는 `Evidence.ok`만 보므로
(계획 §2), "asan을 돌렸는데 기대 신호는 PoC 크래시였다" 같은 어긋남이 `ok=True`로 남으면
E4가 잘못 성립한다. 그래서 기대 신호에서 도구를 **역으로 고르고**, 어떤 허용 도구로도
매핑되지 않으면 실행하지 않고 `tool_policy_violation`으로 기록한다 — 실행할 수 없는
명세를 억지로 돌려 실패로 세는 것과 다른 사건이다.
"""

from src.interfaces.tool_runner import ToolArgs
from src.orchestrator.context import RunContext
from src.orchestrator.nodes.sandbox import DEFAULT_SANDBOX
from src.orchestrator.policy import DecisionPoint, RoutingRequest
from src.orchestrator.stages import Stage
from src.schemas.evidence import Evidence
from src.schemas.finding import Finding

BUILD_TOOL = "build"

# 기대 신호에 나타나는 낱말 → 그것을 만들어 낼 수 있는 도구. 이름은 ToolRunner의
# 화이트리스트 안에 있어야 한다. 위에서부터 처음 걸리는 것을 쓴다.
_SIGNAL_TOOLS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("sanitizer", "asan", "addresssanitizer", "ubsan", "msan"), "asan"),
    (("poc", "exploit", "crash", "segfault"), "poc"),
)


def tool_for(expected_signal: str) -> str | None:
    """기대 신호를 만들 수 있는 도구. 없으면 `None`(= tool_policy 밖 요구)."""
    lowered = expected_signal.lower()
    for needles, tool in _SIGNAL_TOOLS:
        if any(needle in lowered for needle in needles):
            return tool
    return None


def run(ctx: RunContext, *, finding: Finding, workdir: str = ".") -> tuple[Finding, list[Evidence]]:
    """빌드 → 재현 실행 순서로 돌리고 결과를 전이에 반영한다."""
    spec = finding.repro_spec
    if spec is None:
        raise ValueError(f"{finding.finding_id}에 repro_spec이 없다. validated 이상만 8단계에 온다")
    tool = tool_for(spec.expected_signal)
    with ctx.stage(Stage.VALIDATE):
        evidences: list[Evidence] = []
        if tool is not None:
            evidences.append(_call(ctx, BUILD_TOOL, [BUILD_TOOL, *spec.steps], workdir, finding))
            evidences.append(_call(ctx, tool, [tool, *spec.steps], workdir, finding))
        finding = _attach(ctx, finding, evidences)
        action = ctx.decide(
            RoutingRequest(
                point=DecisionPoint.REPRO_RESULT,
                stage=Stage.VALIDATE,
                subject_kind="finding",
                subject_id=finding.finding_id,
                signals={
                    "ok": bool(evidences) and all(ev.ok for ev in evidences),
                    "tool_allowed": tool is not None,
                },
            )
        )
        return ctx.apply_action(finding, action, stage=Stage.VALIDATE), evidences


def _call(ctx: RunContext, tool: str, argv: list[str], workdir: str, finding: Finding) -> Evidence:
    return ctx.call_tool(
        tool=tool,
        args=ToolArgs(argv=argv, workdir=workdir),
        sandbox=DEFAULT_SANDBOX,
        stage=Stage.VALIDATE,
        subject_id=finding.finding_id,
    )


def _attach(ctx: RunContext, finding: Finding, evidences: list[Evidence]) -> Finding:
    """실행 로그를 Finding의 근거로 붙인다.

    상태 변경이 아니라 데이터 보강이므로 `transition()`을 직접 쓴다(검증은 그대로 돈다).
    이걸 하지 않으면 게이트가 8단계 산출물을 못 보고 E4가 성립하지 않는다.
    """
    new_refs = [ev.evidence_id for ev in evidences if ev.evidence_id not in finding.evidence_refs]
    if not new_refs:
        return finding
    updated = finding.transition(evidence_refs=[*finding.evidence_refs, *new_refs])
    ctx.store.put(updated)
    return updated
