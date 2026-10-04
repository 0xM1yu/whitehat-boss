"""7단계 Independent Critic — 상위 Finding만 다른 계열 모델로 반박한다.

전이 표 F-5~F-9. 한 호출에서 두 사건이 순서대로 일어난다: 먼저 Critic 판정을 기록하고,
그다음 게이트가 E3이면 `validated`로 올린다. 그래서 F-5(무전이 `critic_upheld`)와
F-8(`validated` `critic_upheld`)이 같은 사유로 다른 상태에 간다.

**Auditor의 원문 추론을 전달하지 않는다**(agent-contracts.md §2.3). 프롬프트에 실리는
것은 주장·경로·근거(ID·kind·ok·source, content-aware 판은 본문·위치도)뿐이고 `rca_draft`는
넘기지 않는다 — 앞 모델의 설명을 보여 주면 반박이 아니라 재진술이 돌아온다.

**정규화는 여기 한 곳에서만 한다.** 근거 없는 `rebut`을 `uphold`로 바꾸는 판단이 두 곳에
있으면 "근거 없는 반박 비율"의 분자가 갈라진다. 노드가 `gate.normalize()`를 부르고,
`gate.was_normalized()`면 `critic_normalized`를 남긴 뒤 **정규화된 값**을 정책에 넘긴다.
"""

from collections.abc import Mapping

from src.orchestrator import gate
from src.orchestrator.context import RunContext
from src.orchestrator.policy import DecisionPoint, RoutingRequest
from src.orchestrator.prompt_loader import build_facts, evidence_summaries, load_prompt
from src.orchestrator.stages import Stage
from src.orchestrator.trace import EventKind
from src.schemas.common import Location, ProducedBy
from src.schemas.decision import Decision, DecisionDraft
from src.schemas.enums import Role
from src.schemas.evidence import Evidence
from src.schemas.finding import Finding, MissingEvidence

ROLE = Role.CRITIC
# 요청하는 구조화 출력 스키마 (`nodes/scout.py`의 같은 상수 참조).
# **`Decision`이 아니라 `DecisionDraft`다** — ID·`produced_by`는 하네스가 넣는다.
OUTPUT_SCHEMA = DecisionDraft

# 근거 본문을 FACTS·EVIDENCE에 싣는 것은 그 본문을 **읽도록 쓰인 프롬프트 버전**에만 한다.
# v3·v4·v5는 content-conditional(본문 있으면 rebut/uphold, 없으면 need_evidence)이라 여기 든다.
# v1은 그대로 metadata만 받게 두어 바이트 동일 → 재측정(evaluation §2.3)이 안 걸린다.
_CONTENT_AWARE_VERSIONS = frozenset({"v3", "v4", "v5"})

# 상한에 밀려 7단계에 못 올라간 사유. `runner._REASON_OVER_MAX`("over_candidate_max")와
# 같은 꼴로 읽히도록 맞춘다 — 절단 사유는 어느 상한이 걸렸는지를 이름으로 말해야 한다.
_REASON_OVER_TOP_N = "over_critic_top_n"


def select(ctx: RunContext, findings: list[Finding]) -> list[Finding]:
    """Critic에 올릴 상위 Finding. 개수 상한은 config에서 온다(`critic_top_n`).

    Critic은 가장 비싼 단계이므로 상한이 있어야 한다(architecture.md §3 "상위 Finding만").
    confidence 동점은 `finding_id`로 가른다 — 정렬이 흔들리면 재현 실행이 갈라진다.

    **자르는 자리와 남기는 자리가 한 곳이다** (#98 (b), `_order_and_cut`과 같은 구조).
    슬라이스만 하던 동안에는 잘린 건이 `Finding.state`에서도 trace에서도 안 읽혔다 —
    F-9(`repro_spec_missing`)가 `hypothesized → hypothesized`라 7단계에 **간** 건과 **못
    간** 건이 리포트의 같은 묶음에 나란히 찍히고, 44줄이 멀쩡히 있어서 전량을 본 것처럼
    읽혔다(실측 `run_20260825_0001`: 승격 44 · 선별 8 · **절단 36**).

    잘린 건만 남긴다. 올라간 건은 이미 `gate_eval`·`transition`(stage=7)에 있다 —
    `target_skipped`가 잘린 target만 남기는 것과 같다.
    """
    ordered = sorted(findings, key=lambda f: (-f.confidence, f.finding_id))
    limit = ctx.config.thresholds.critic_top_n
    for cut in ordered[limit:]:
        ctx.trace.emit(
            EventKind.CRITIC_SKIPPED,
            stage=Stage.CRITIC,
            subject_kind="finding",
            subject_id=cut.finding_id,
            reason=_REASON_OVER_TOP_N,
            # 선별 키와 8단계 진입 조건을 나란히 싣는다 — #98 (a)가 묻는 것이 이 둘의
            # 어긋남이고, 없으면 그 답을 보려고 `state.db`를 열어야 한다.
            confidence=cut.confidence,
            has_repro_spec=cut.repro_spec is not None,
        )
    return ordered[:limit]


def run(
    ctx: RunContext,
    *,
    finding: Finding,
    evidences: tuple[Evidence, ...] = (),
    missings: tuple[MissingEvidence, ...] = (),
) -> tuple[Finding, Decision]:
    """반박을 받아 정규화하고, 재판정한 등급까지 반영해 전이시킨다."""
    decision_id = ctx.ids.next("decision")
    own = gate.evidences_for(finding, evidences)
    with ctx.stage(Stage.CRITIC):
        decision = _ask_model(ctx, decision_id=decision_id, finding=finding, evidences=own)
        ctx.store.put(decision)
        if gate.was_normalized(decision):
            ctx.record_critic_normalized(decision, stage=Stage.CRITIC)
        verdict = gate.normalize(decision)

        result = ctx.evaluate_gate(finding, own, [decision], missings, stage=Stage.CRITIC)
        action = ctx.decide(
            RoutingRequest(
                point=DecisionPoint.CRITIC_VERDICT,
                stage=Stage.CRITIC,
                subject_kind="finding",
                subject_id=finding.finding_id,
                signals={
                    "verdict": verdict.value,
                    "grade": result.grade.value,
                    "has_repro_spec": finding.repro_spec is not None,
                },
            )
        )
        return ctx.apply_action(finding, action, stage=Stage.CRITIC), decision


def _ask_model(
    ctx: RunContext, *, decision_id: str, finding: Finding, evidences: list[Evidence]
) -> Decision:
    version = ctx.config.model_for(ROLE.value).prompt_template_version
    template = load_prompt(ROLE, version)
    # content-aware 버전(`_CONTENT_AWARE_VERSIONS`)일 때만 payload 본문을 읽고 **위치도** 싣는다.
    # 그 외 버전은 본문·위치 없이 종전과 동일(#237 HIGH-1 — v1은 바이트 동일). 위치는 payload가
    # 있느냐가 아니라 **판**이 정한다 — payload가 없어도(`evidence_summaries`가 그 Evidence를 맵에서
    # 뺀다) 같은 판이면 FACTS와 EVIDENCE 줄이 같은 값을 든다.
    aware = version in _CONTENT_AWARE_VERSIONS
    bodies = evidence_summaries(evidences, ctx.read_payload) if aware else {}
    prompt = template.render(
        facts=build_facts(
            run_id=ctx.run_id,
            ids={"finding_id": finding.finding_id, "decision_id": decision_id},
            evidences=evidences,
            evidence_bodies=bodies,
            evidence_locations=aware,
        ),
        claim=f"{finding.title} ({finding.cwe})",
        path=_describe_path(finding),
        evidence=_describe_evidence(evidences, bodies, with_location=aware),
    )
    # **verdict를 자유 문자열로 받는다** — `Decision`의 `verdict: Verdict`를 스키마에 `enum`으로
    # 강제하면 grammar 마스크가 모델의 맞는 추론을 틀린 토큰으로 덮는다(gpt-oss-20b 실측
    # 2026-09-12: enum 강제 2/5 vs enum 제거 5/5 — **원천: PR #182 본문, 원문 미커밋**.
    # `docs/experiments/harness/2026-09-12-critic-decisiondraft-budget-atom.md` §5).
    # `DecisionDraft`(모델이 정하는 필드만)로 받아
    # `to_decision()`이 `Verdict.from_text`로 표준값에 매핑한다. verdict를 못 읽으면
    # `need_evidence`(보류)이고 원문을 trace에 남긴다 — 미해석을 uphold로 소비하지 않는다.
    result = ctx.call_model(
        role=ROLE,
        prompt=prompt,
        schema=OUTPUT_SCHEMA,
        stage=Stage.CRITIC,
        subject_kind="finding",
        subject_id=finding.finding_id,
    )
    draft = result.value
    if not draft.verdict_interpreted():
        ctx.record_critic_verdict_unmapped(
            decision_id=decision_id,
            finding_id=finding.finding_id,
            raw_verdict=draft.verdict,
            stage=Stage.CRITIC,
        )
    # **provenance는 하네스가 넣는다** — 모델 출력의 `produced_by`는 오염돼 있다(실측:
    # `"ChatGPT"` 등). 실제 호출 모델(`result.model`)·역할·프롬프트 버전으로 결정적으로 만든다.
    produced_by = ProducedBy(role=ROLE, model=result.model, prompt_version=prompt.prompt_version)
    return draft.to_decision(
        decision_id=decision_id,
        run_id=ctx.run_id,
        finding_id=finding.finding_id,
        produced_by=produced_by,
    )


def _describe_path(finding: Finding) -> str:
    """주장의 경로만 전달한다. `rca_draft`는 의도적으로 빠져 있다."""
    if not finding.path:
        return "(경로 없음)"
    return "\n".join(f"- {step.step.value}: `{step.symbol}`" for step in finding.path)


def _location_text(location: Location) -> str:
    """`file:start-end` 한 토막. 줄이 없으면 파일만, 끝줄이 시작과 같으면 한 줄로 적는다.

    Evidence의 줄 필드는 선택이다(`schemas/common.Location` — "Evidence는 file·line_start만
    쓴다"). 없는 값을 `None`이라 적지 않는다(CLAUDE.md §0).
    """
    start, end = location.line_start, location.line_end
    if start is None:
        return location.file
    if end is None or end == start:
        return f"{location.file}:{start}"
    return f"{location.file}:{start}-{end}"


def _describe_evidence(
    evidences: list[Evidence],
    bodies: Mapping[str, Mapping[str, object]] | None = None,
    *,
    with_location: bool = False,
) -> str:
    """근거 한 줄씩. `with_location`은 **content-aware 판만** 켠다(`_ask_model`) — 꺼져 있으면
    main의 렌더와 바이트 동일이다(v1 · 적대적 리뷰 #480 HIGH-1)."""
    if not evidences:
        return "(근거 없음)"
    bodies = bodies or {}
    lines = []
    for ev in evidences:
        line = f"- {ev.evidence_id} · {ev.kind.value} · {'ok' if ev.ok else 'failed'} · {ev.source}"
        # **위치를 적는다 — Critic이 「이 근거가 그 주장과 같은 자리를 가리키나」를 볼 재료다.**
        # 게이트 등급은 이 값으로 안 정해진다: `p1 = static_ok or location_ok`이고 Evidence에 위치를
        # 싣는 곳이 semgrep(STATIC) 하나라 인용하면 `static_ok`가 이미 참이다(`gate.evaluate` ·
        # `container_tools.first_hit_location`). 그 위치는 semgrep `results[0]`(첫 히트) 하나라
        # 주장한 줄과 다를 수 있다. 이 노출이 인용·판정을 바꾸는지는 미실측이다(이슈 #475).
        # 위치가 없으면 아무것도 더하지 않는다 — 산문 한 줄이라 `null`을 적으면 노이즈다.
        if with_location and ev.location is not None:
            line += f" · {_location_text(ev.location)}"
        body = bodies.get(ev.evidence_id)
        if body and body.get("body"):
            snippet = str(body["body"]).replace("\n", "\n    ")
            mark = " [truncated]" if body.get("truncated") else ""
            line += f"\n    {snippet}{mark}"
        lines.append(line)
    return "\n".join(lines)
