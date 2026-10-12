"""8단계 Tool Validation — 재현 명세를 샌드박스에서 실행한다.

전이 표 F-10(재현 성공, E4) / F-10b(재현 성공, E4 미만) / F-11(실패) / F-12(tool_policy 위반).

## ⏵ 2026-10-13 (이슈 #326) — 이 경로는 서빙 점수 경로 밖이다 (선택지 (c))

여기서 도구를 고르는 것은 `build`와 `asan`뿐이고, 둘 다 **우리 clang이 대상을 직접 컴파일한다**
(`container_tools.plan`이 `repro_spec.steps`를 무시하고 소스를 자체 링크한다). ADR 0014가 대상
빌드를 우리가 짓지 않기로 했고 CyberGym 번들은 우리 clang으로 컴파일되지 않는다(#507) - 그래서
**그 번들에서는 이 경로가 E4를 못 낸다.** 서빙 점수 경로는 PoV 루프의 `poc`(과제 이미지에 PoC를
먹인다)이고, 이 노드는 `poc`를 **돌리지 않는다**(입력 바이트를 실을 길이 없어 항상 거절당했다).

**측정하지 못한 것을 재현 실패로 세지 않는다.** 도구 층이 측정을 못 낸 건(`build`·`asan`이
`ok=False`이고 종료 코드가 있다 - 컴파일·링크가 안 됐거나 대상이 안 떴다)과 `poc` 신호는 F-11로
내리지 않고 `validated`에 그대로 둔다(`repro_not_measured` 이벤트 + Evidence는 `ok=False`로 남는다).
F-11을 쓰면 「도구 검증 제거율」(`trace.py`)의 분자가 하네스 한계로 부풀고 보고서에는 Critic을
통과한 Finding이 재현에 실패한 것으로 찍힌다. **PoV 풀에는 영향이 없다** - 러너가 8단계 진입 시점의
`validated` 목록으로 PoV 대상을 잡는다(`runner._analyze`).

**`expected_signal` 매칭은 도구 선택 시점에 한다.** 게이트는 `expected_signal`을 안 보고 E4를
`Evidence.ok`·`kind`·`exit_code`로만 가르므로(계획 §2), "asan을 돌렸는데 기대 신호는
PoC 크래시였다" 같은 어긋남이 `ok=True`·비-0 종료 코드로 남으면 E4가 잘못 성립한다. 그래서
기대 신호에서 도구를 **역으로 고르고**, 어떤 허용 도구로도 매핑되지 않으면 실행하지 않고
`tool_policy_violation`으로 기록한다 — 실행할 수 없는 명세를 억지로 돌려 실패로 세는 것과
다른 사건이다.

## 여기가 Evidence Gate의 세 번째 호출 지점이다

`stages.GATE_STAGES`가 `(GATE, CRITIC, VALIDATE)`이고 `gate.py`가 "세 단계에서 각각
호출된다"고 단정하는데도, **이 단계에는 호출이 없었다.** 그래서 E4(`SANITIZER`/`CRASH`/
`POC_RUN` Evidence — 지금은 종료 코드가 0도 `None`도 아닌 것, #232)는 그것을 만드는 유일한
단계에서 판정되지 못했고 `gate_eval` trace에 **한 번도 나타나지 않았다** — 도구 검증 제거율의
분자가 구조적으로 0이었다.

`attach_observed()`가 실행 로그를 Finding에 붙여 두는데 그 뒤에 판정하는 사람이 없던 상태다
(그 함수 docstring이 "이걸 하지 않으면 E4가 성립하지 않는다"고 적어 뒀다). 그 함수는
**6단계가 같은 일을 하게 되면서** `nodes/gate_node.py`로 옮겼다(이슈 #552).

## 모델이 쓴 `steps`가 예약 자리표와 같으면 실행하지 않고 거절한다

`_call`은 `[tool, *spec.steps]`를 argv로 넘기는데 `steps`는 모델이 쓴 자유 문자열이다. 원소가
`INPUT_PLACEHOLDER`와 **정확히 같으면** `ToolArgs`가 그 요청을 못 만든다 — `ValidationError`는
최후 방어선을 지나 run을 멈춘다(독립 검토가 `stopped_after=8`로 실증했다). 그래서 도구에 가기 전에
`_refuse`가 `ok=False` Evidence로 남긴다(`ToolRunner` 계약: 도구 실패도 Evidence로 기록).
"""

from datetime import UTC, datetime

from src.ids import sha256_text
from src.interfaces.tool_runner import INPUT_PLACEHOLDER, ToolArgs
from src.orchestrator import gate
from src.orchestrator.context import RunContext
from src.orchestrator.nodes.gate_node import attach_observed
from src.orchestrator.nodes.sandbox import DEFAULT_SANDBOX
from src.orchestrator.policy import DecisionPoint, RoutingRequest
from src.orchestrator.stages import Stage
from src.orchestrator.trace import EventKind
from src.schemas.decision import Decision
from src.schemas.enums import EvidenceKind
from src.schemas.evidence import Evidence
from src.schemas.finding import Finding, MissingEvidence

BUILD_TOOL = "build"
# 기대 신호가 가리키는 도구이지만 이 노드는 **돌리지 않는다**(모듈 docstring) - PoV 루프의 몫이다.
POC_TOOL = "poc"

# 거절한 요청의 Evidence에 찍는 `kind` — `adapters.fake_tools.TOOL_KINDS`의 사본이다(노드는
# 어댑터를 import하지 못한다). 이 노드가 **부르는** 도구만 든다(`POC_TOOL`은 안 부른다) —
# `tests/test_nodes.py`가 화이트리스트와 대조한다.
REFUSED_KINDS: dict[str, EvidenceKind] = {
    BUILD_TOOL: EvidenceKind.BUILD,
    "asan": EvidenceKind.SANITIZER,
}
# 거절한 Evidence의 관측 시각 — 어댑터가 모든 Evidence에 찍는 고정 시각과 같다(`fake_tools`·
# `container_tools`의 기본값). 아무것도 관측하지 않았고 재현 실행에서 같은 값이 나와야 한다.
_REFUSED_AT = datetime(2026, 9, 1, tzinfo=UTC)

# 기대 신호에 나타나는 낱말 → 그것을 만들어 낼 수 있는 도구. 이름은 ToolRunner의
# 화이트리스트 안에 있어야 한다. 위에서부터 처음 걸리는 것을 쓴다.
_SIGNAL_TOOLS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("sanitizer", "asan", "addresssanitizer", "ubsan", "msan"), "asan"),
    (("poc", "exploit", "crash", "segfault"), POC_TOOL),
)


def tool_for(expected_signal: str) -> str | None:
    """기대 신호를 만들 수 있는 도구. 없으면 `None`(= tool_policy 밖 요구). `POC_TOOL`은 분류일 뿐
    `run`이 돌리지 않는다."""
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
        if tool is not None and tool != POC_TOOL:
            produced.append(_call(ctx, BUILD_TOOL, [BUILD_TOOL, *spec.steps], workdir, finding))
            produced.append(_call(ctx, tool, [tool, *spec.steps], workdir, finding))
        finding = attach_observed(ctx, finding, produced)
        # `attach_observed()`가 방금 만든 근거를 `evidence_refs`에 넣었으므로, 앞 단계 것과 합쳐
        # 넘기면 `evidences_for()`가 이 Finding의 것만 골라낸다. E4는 여기서만 성립한다.
        #
        # 반환값을 쓴다(1-A 전에는 버렸다). E4의 조건인 `SANITIZER`/`CRASH`/`POC_RUN`
        # Evidence(종료 코드가 0도 `None`도 아닌 것 — #232)는 바로 위 `_call()`이 만들고,
        # `attach_observed()` 이후가 `p4`를 참으로 만들 수 있는 **유일한 시점**이다. 여기서
        # 부르지 않으면
        # 그 등급은 `gate_eval` trace에 영영 나타나지 않고 도구 검증 제거율의 분자가 구조적으로
        # 0이 된다(W3이 고친 것이 그것이다).
        #
        # **아래 정책도 이제 이 등급을 본다**(F-10b, 이슈 #68). `policy._repro_result()`가
        # `ok=True`일 때만 `grade`를 읽어 E4 미만이면 `proven`으로 올리지 않는다 — 상태
        # 축과 등급 축이 어긋난 채로 남아 있던 문제(2026-08-21 ATOM run 실측)가 여기서
        # 닫힌다.
        result = ctx.evaluate_gate(
            finding,
            gate.evidences_for(finding, (*evidences, *produced)),
            decisions,
            missings,
            stage=Stage.VALIDATE,
        )
        unmeasured = _not_measured(tool, produced)
        if unmeasured is not None:
            reason, culprit = unmeasured
            # 상태를 안 바꾼다 - 전이도 `policy_decision`도 없다. 「제거율 — Tool」의 분자(F-11)에도
            # 분모(F-10·10b·11)에도 안 든다: 재현을 **시도하지 못한** 건이다(모듈 docstring).
            ctx.trace.emit(
                EventKind.REPRO_NOT_MEASURED,
                stage=Stage.VALIDATE,
                subject_kind="finding",
                subject_id=finding.finding_id,
                tool=tool,
                reason=reason,
                exit_code=culprit.exit_code if culprit is not None else None,
                error=culprit.error if culprit is not None else None,
            )
            return finding, produced
        action = ctx.decide(
            RoutingRequest(
                point=DecisionPoint.REPRO_RESULT,
                stage=Stage.VALIDATE,
                subject_kind="finding",
                subject_id=finding.finding_id,
                signals={
                    "ok": bool(produced) and all(ev.ok for ev in produced),
                    "tool_allowed": tool is not None,
                    "grade": result.grade.value,
                },
            )
        )
        return ctx.apply_action(finding, action, stage=Stage.VALIDATE), produced


# `repro_not_measured`의 `reason`. trace를 읽는 쪽이 가르는 값이다.
NOT_MEASURED_POC = "poc_runs_in_the_pov_loop"
NOT_MEASURED_TOOL = "tool_produced_no_measurement"


def _not_measured(tool: str | None, produced: list[Evidence]) -> tuple[str, Evidence | None] | None:
    """이 건이 **재현을 시도하지 못했으면** `(사유, 원인 Evidence)`, 아니면 `None`.

    - `poc` 신호: 이 노드는 `poc`를 안 돌린다(입력 바이트를 실을 길이 없다 - PoV 루프의 몫).
    - 도구 층이 측정을 못 낸 Evidence: `ok=False`이고 `exit_code`가 있다.
      `container_tools.judge()`의
      계약이 그것이다 - `build`·`asan`의 `ok=False`는 컴파일·링크가 안 됐거나 대상이 안 떴다는
      뜻이고, 대상이 떠서 비-0으로 끝난 것은 `ok=True`다(크래시는 신호). **종료 코드가 `None`인
      것은 안 센다** - 모델이 쓴 `steps`가 예약 자리표와 같아 우리가 요청을 안 보낸 `_refuse`이고,
      그것은 명세의 실패라 기존대로 F-11이다.
    """
    if tool == POC_TOOL:
        return NOT_MEASURED_POC, None
    for evidence in produced:
        if not evidence.ok and evidence.exit_code is not None:
            return NOT_MEASURED_TOOL, evidence
    return None


def _call(ctx: RunContext, tool: str, argv: list[str], workdir: str, finding: Finding) -> Evidence:
    if INPUT_PLACEHOLDER in argv:
        return _refuse(ctx, tool, argv, workdir, finding)
    return ctx.call_tool(
        tool=tool,
        args=ToolArgs(argv=argv, workdir=workdir),
        sandbox=DEFAULT_SANDBOX,
        stage=Stage.VALIDATE,
        subject_id=finding.finding_id,
    )


def _refuse(
    ctx: RunContext, tool: str, argv: list[str], workdir: str, finding: Finding
) -> Evidence:
    """예약 자리표와 같은 단계가 있는 요청을 도구에 보내지 않고 `ok=False`로 남긴다. 도구 실패와
    같은 모양이다 — Evidence를 저장하고 `tool_call`을 내며(키 집합도 같다), 실행한 적이 없으므로
    `exit_code`는 `None`(미측정)이다."""
    evidence_id = ctx.ids.next("evidence")
    error = (
        f"repro_spec.steps에 예약된 자리표 {INPUT_PLACEHOLDER!r}와 같은 단계가 있다 — "
        f"실행하지 않았다({tool})"
    )
    evidence = Evidence(
        evidence_id=evidence_id,
        run_id=ctx.run_id,
        kind=REFUSED_KINDS.get(tool, EvidenceKind.BUILD),
        source=tool,
        artifact_id=workdir,
        payload_ref=f"artifacts/{ctx.run_id}/{tool}/{evidence_id}.log",
        digest=sha256_text(f"{tool} {' '.join(argv)} refused"),
        observed_at=_REFUSED_AT,
        ok=False,
        exit_code=None,
        error=error,
    )
    ctx.store.put(evidence)
    ctx.trace.emit(
        EventKind.TOOL_CALL,
        stage=Stage.VALIDATE,
        subject_kind="evidence",
        subject_id=evidence_id,
        wall_ms=0,
        tool=tool,
        argv=" ".join(argv),
        ok=False,
        exit_code=None,
        error=error,
        finding_id=finding.finding_id,
    )
    return evidence
