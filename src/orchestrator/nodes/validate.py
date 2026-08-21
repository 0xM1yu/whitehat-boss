"""8단계 Tool Validation — 재현 명세를 샌드박스에서 실행한다.

전이 표 F-10(재현 성공) / F-11(실패) / F-12(tool_policy 위반).

**`expected_signal` 매칭은 도구 선택 시점에 한다.** 게이트는 `Evidence.ok`만 보므로
(계획 §2), "asan을 돌렸는데 기대 신호는 PoC 크래시였다" 같은 어긋남이 `ok=True`로 남으면
E4가 잘못 성립한다. 그래서 기대 신호에서 도구를 **역으로 고르고**, 어떤 허용 도구로도
매핑되지 않으면 실행하지 않고 `tool_policy_violation`으로 기록한다 — 실행할 수 없는
명세를 억지로 돌려 실패로 세는 것과 다른 사건이다.

## 여기가 Evidence Gate의 세 번째 호출 지점이다

`stages.GATE_STAGES`가 `(GATE, CRITIC, VALIDATE)`이고 `gate.py`가 "세 단계에서 각각
호출된다"고 단정하는데도, **이 단계에는 호출이 없었다.** 그래서 E4(`SANITIZER`/`CRASH`/
`POC_RUN` Evidence)는 그것을 만드는 유일한 단계에서 판정되지 못했고 `gate_eval` trace에
**한 번도 나타나지 않았다** — 도구 검증 제거율의 분자가 구조적으로 0이었다.

`_attach()`가 실행 로그를 Finding에 붙여 두는데 그 뒤에 판정하는 사람이 없던 상태다
(그 함수 docstring이 "이걸 하지 않으면 E4가 성립하지 않는다"고 적어 뒀다).
"""

from src.interfaces.tool_runner import ToolArgs
from src.orchestrator import gate
from src.orchestrator.context import RunContext
from src.orchestrator.nodes.sandbox import DEFAULT_SANDBOX
from src.orchestrator.policy import DecisionPoint, RoutingRequest
from src.orchestrator.stages import Stage
from src.schemas.decision import Decision
from src.schemas.evidence import Evidence
from src.schemas.finding import Finding, MissingEvidence

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


def run(
    ctx: RunContext,
    *,
    finding: Finding,
    workdir: str = ".",
    evidences: tuple[Evidence, ...] = (),
    missings: tuple[MissingEvidence, ...] = (),
    decisions: tuple[Decision, ...] = (),
) -> tuple[Finding, list[Evidence]]:
    """빌드 → 재현 실행 → **게이트 재판정** 순서로 돌리고 결과를 전이에 반영한다.

    `evidences`·`missings`·`decisions`는 **앞 단계가 이 Finding에 대해 모아 둔 것**이고,
    게이트 재판정에만 쓴다. `critic.run()`이 같은 이유로 같은 인자를 받는다.

    **왜 앞 단계 산출물이 필요한가**: 8단계가 만든 build·asan Evidence만 게이트에 주면
    2단계의 static·graph Evidence가 `usable`에서 빠져 `p1`이 거짓이 된다. 그러면 asan이
    실패한 run에서 7단계 E3 → 8단계 E1로 **등급이 떨어져 보인다.** 등급은 단조 하강이
    아니라 근거가 쌓이며 올라가는 값이므로, 재판정에 주는 근거 집합이 앞 단계보다 작으면
    그 자체가 조용히 틀린 값이다.
    """
    spec = finding.repro_spec
    if spec is None:
        raise ValueError(f"{finding.finding_id}에 repro_spec이 없다. validated 이상만 8단계에 온다")
    tool = tool_for(spec.expected_signal)
    with ctx.stage(Stage.VALIDATE):
        produced: list[Evidence] = []
        if tool is not None:
            produced.append(_call(ctx, BUILD_TOOL, [BUILD_TOOL, *spec.steps], workdir, finding))
            produced.append(_call(ctx, tool, [tool, *spec.steps], workdir, finding))
        finding = _attach(ctx, finding, produced)
        # `_attach()`가 방금 만든 근거를 `evidence_refs`에 넣었으므로, 앞 단계 것과 합쳐
        # 넘기면 `evidences_for()`가 이 Finding의 것만 골라낸다. E4는 여기서만 성립한다.
        #
        # ## 이 등급은 아무도 읽지 않는다. 지우지 마라
        #
        # 반환값을 버린다. **목적이 판정이 아니라 집계**이기 때문이다 — E4의 조건인
        # `SANITIZER`/`CRASH`/`POC_RUN` Evidence는 바로 위 `_call()`이 만들고, `_attach()`
        # 이후가 `p4`를 참으로 만들 수 있는 **유일한 시점**이다. 여기서 부르지 않으면 그
        # 등급은 `gate_eval` trace에 영영 나타나지 않고 도구 검증 제거율의 분자가
        # 구조적으로 0이 된다(W3이 고친 것이 그것이다).
        #
        # **아래 정책은 이 등급을 보지 않는다.** `policy._repro_result()`가 읽는 것은
        # `ok`·`tool_allowed` 둘뿐이고 `basis`에도 `grade`가 없다. 그래서 게이트가 E3인
        # Finding도 `ok=True`면 `proven`이 된다 — 상태 축과 등급 축이 어긋난 채로 남아
        # 있다(2026-08-21 ATOM run 실측). 그 둘을 잇는 것은 승격 로직 변경이고 이전 run과
        # 비교가 끊기므로 **별건이다**(open-questions D18).
        #
        # 설명 없는 미소비 계산은 다음 사람이 죽은 코드로 보고 지운다. `GATE_STAGES`가
        # 참조 0건이던 자리와 같은 종류라 사유를 여기 남긴다.
        ctx.evaluate_gate(
            finding,
            gate.evidences_for(finding, (*evidences, *produced)),
            decisions,
            missings,
            stage=Stage.VALIDATE,
        )
        action = ctx.decide(
            RoutingRequest(
                point=DecisionPoint.REPRO_RESULT,
                stage=Stage.VALIDATE,
                subject_kind="finding",
                subject_id=finding.finding_id,
                signals={
                    "ok": bool(produced) and all(ev.ok for ev in produced),
                    "tool_allowed": tool is not None,
                },
            )
        )
        return ctx.apply_action(finding, action, stage=Stage.VALIDATE), produced


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
