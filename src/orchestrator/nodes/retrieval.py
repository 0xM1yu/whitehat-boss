"""3단계 Retrieval — 대상 하나에 대응하는 Context Capsule을 만든다.

⚠ **「후보 하나당」이 아니다.** Scout 풀에서는 대상 하나를 멤버 셋이 각각 보므로 후보가
셋인데 **캡슐은 하나이고 셋이 공유한다**(ADR 0010 §3, `runner._step_retrieval`의
`state.capsules` 캐시). 후보 수로 캡슐 토큰을 곱하면 풀 run에서 셋 배로 계상된다.

architecture.md §3의 통과 조건은 "capsule 토큰 상한 이내"이고 그 상한은 config에서
온다(`thresholds.capsule_budget_tokens`). 노드가 숫자를 박지 않는다.

Progressive Context Expansion(knowledge-base.md §5)의 **횟수 상한 판정은 정책이 한다.**
노드는 `DecisionPoint.CAPSULE_EXPANSION`에 현재 확장 횟수를 넘기고 돌아온 `Action`을
그대로 적용한다 — 상한을 넘기면 후보를 폐기하지 않고 `queued`로 둔다(전이 표 C-5).
"""

from src.config import SCOUT_POOL_ROLES
from src.interfaces.retriever import Query
from src.orchestrator.context import RunContext
from src.orchestrator.policy import DecisionPoint, Route, RoutingRequest
from src.orchestrator.stages import Stage
from src.orchestrator.trace import EventKind
from src.schemas.candidate import Candidate
from src.schemas.capsule import CapsuleSection, ContextCapsule
from src.schemas.common import Location
from src.schemas.enums import CapsuleLevel
from src.schemas.finding import MissingEvidence
from src.tokens import estimate_tokens

# 풀 공유 캡슐 예산 마진 (#194). 캡슐은 첫 멤버(qwen) 토크나이저로 예산이 정해지는데 계열이 다른
# 멤버는 같은 텍스트를 최대 ~11% 더 센다(2026-09-13 ATOM 실측 — 그때 로스터의 mistral
# `scout_boundary`: 캡슐 합 +4%, 조각 최대 +10.6%). 첫 멤버 기준을 이 factor로 줄여 담으면 그 최대
# 갈림(1/0.90 ≈ 1.111)까지 세 자리 전부 상한 안에 든다 — strict per-member drop 없이 예산이
# 강제된다. 단일 프로필은 자리가 하나라 적용하지 않는다. ⏵ 2026-09-21 로스터 교체 뒤 Scout 세 자리가
# 모두 qwen 계열이라 그 실측의 조건(계열이 다른 자리)이 지금은 없다 — 마진은 계열이 다른 모델이 풀에
# 다시 들어오는 날을 위해 그대로 둔다.
_POOL_CAPSULE_MARGIN = 0.90

# CyberGym 과제 설명을 담는 캡슐 섹션의 kind (PoV 배선). `known_facts`를 재쓰지 않는
# 이유는 그 섹션이 두 Retriever에서 「고정 64토큰 자리표시자」로 하드코딩돼 있어(§0) 가변
# 길이 설명을 넣으면 회계가 조용히 틀리기 때문이다(council 판정). 이 섹션의 토큰은 body에서
# 계산한다(켜진 카운터 기준 — 기본은 `len//4` 근사이고, 실 토크나이저 카운터가 켜진 프로필에서만
# 실측이다).
VULN_DESCRIPTION_KIND = "vuln_description"


def _vuln_description_section(text: str, budget: int) -> CapsuleSection | None:
    """과제 설명을 담는 캡슐 섹션. 남은 예산 안에 드는 줄까지만 담고 `tokens`를 본문에서
    계산한다(켜진 카운터 기준 — 기본은 `len//4` 근사이고, 실 토크나이저 카운터가 켜진
    프로필에서만 실측이다). 고정 자리표시자가 아니라 본문에서 센 값이라 매니페스트가 내용과 안
    갈라진다. 한 줄도 못 담으면 `None`.

    빈 줄은 버린다 — `body`는 `NonEmptyStr` 목록이고, 빈 줄로 자리를 채우면 매니페스트가
    본문을 과장한다.
    """
    kept: list[str] = []
    used = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        cost = estimate_tokens(line)
        if used + cost > budget:
            break
        kept.append(line)
        used += cost
    if not kept:
        return None
    return CapsuleSection(kind=VULN_DESCRIPTION_KIND, tokens=used, items=len(kept), body=kept)


def run(
    ctx: RunContext,
    *,
    target: Location,
    level: CapsuleLevel,
    keywords: tuple[str, ...] = (),
    slice_id: str | None = None,
) -> ContextCapsule:
    """레벨에 맞는 캡슐을 만들어 저장한다.

    캐시 적중 여부는 Retriever의 훅이 `cache` 이벤트로 남긴다 — 노드가 캐시를 아는
    구조였다면 구현마다 적중률 정의가 갈라졌을 것이다.

    `slice_id`는 러너가 슬라이스 타깃의 LIGHT 캡슐에만 준다(`runner._step_retrieval`) —
    `target_function`이 슬라이스 렌더를 센다. ⚠ 그것이 Scout가 받는 것과 같다고 읽으면 안 된다 —
    `input: source` 선언(서빙 v4)의 Scout는 entry를 품는 함수 본문을 받으므로 이 토큰 수는 그
    프롬프트와 다른 것을 센다(#529). Auditor의 EXPANDED는 entry를 감싸는 함수가
    대상이라 주지 않는다(`runner._auditor_target`) — 슬라이스가 걸친 다른 함수는 캡슐이 아니라
    `SOURCE`에만 붙는다(`runner._auditor_source`).
    """
    with ctx.stage(Stage.RETRIEVAL):
        budget = ctx.config.thresholds.budget_for(level)
        # #194 — 풀은 공유 캡슐을 세 계열이 나눠 보므로 첫 멤버 기준 예산을 마진만큼 줄여
        # 담는다(위 `_POOL_CAPSULE_MARGIN`). 그래야 계열이 다른 자리도 상한 안에 든다(지금
        # 로스터에는 없다 — 위 주석).
        if all(member in ctx.config.profile.roles for member in SCOUT_POOL_ROLES):
            budget = int(budget * _POOL_CAPSULE_MARGIN)
        # 과제 설명을 담을 자리를 **미리** 뗀다(PoV 배선). Retriever가 예산을 다 쓰기 전에
        # 자리를 받아야 큰 타깃에서도 설명이 확실히 실린다 — 뒤에 붙이면 남은 예산이 0이라
        # 조용히 빠진다. 코드 자리를 굶기지 않게 최대 절반까지만 뗀다(계획 상한). 뗀 양은 켜진
        # 카운터로 본문에서 계산한 토큰이다(기본은 `len//4` 근사 — 실측이 아니다).
        desc_section = (
            _vuln_description_section(ctx.vuln_description, budget // 2)
            if ctx.vuln_description
            else None
        )
        reserve = desc_section.tokens if desc_section is not None else 0
        query = Query(
            run_id=ctx.run_id,
            target=target,
            keywords=list(keywords),
            budget_tokens=budget - reserve,
            slice_id=slice_id,
        )
        capsule = ctx.retriever.search(query, level)
        _record_cuts(ctx, capsule)
        # 설명은 Retriever를 안 건드리고 노드에서 붙인다 — 구현(fake·hybrid)에 무관하게 한
        # 자리에서 하고, 설명은 run 단위 사실이라 target별 Retriever가 알 필요가 없다.
        if ctx.vuln_description:
            capsule = _attach_vuln_description(
                ctx, capsule, desc_section, budget, ctx.vuln_description
            )
        ctx.store.put(capsule)
    return capsule


def _record_cuts(ctx: RunContext, capsule: ContextCapsule) -> None:
    """예산 절단이 내용을 갈랐으면 `capsule_cut`을 남긴다(#663).

    안 잘렸거나 기록이 없으면(`None`) 안 낸다.
    """
    cuts = capsule.cuts
    if cuts is None or not cuts.any_cut:
        return
    ctx.trace.emit(
        EventKind.CAPSULE_CUT,
        stage=Stage.RETRIEVAL,
        subject_kind="capsule",
        subject_id=capsule.capsule_id,
        level=capsule.level.value,
        dropped_sections=",".join(cuts.dropped_sections),
        graph_stopped=cuts.graph_stopped,
        graph_skipped=cuts.graph_skipped,
        related_skipped=cuts.related_skipped,
    )


def _attach_vuln_description(
    ctx: RunContext,
    capsule: ContextCapsule,
    section: CapsuleSection | None,
    budget: int,
    text: str,
) -> ContextCapsule:
    """뗀 자리에 설명 섹션을 붙이고 예산을 원래대로 되돌린다. 잘림은 조용히 두지 않는다.

    설명이 예산 절반 상한을 넘어 잘렸거나(kept < total) 한 줄도 못 들어가면(section None)
    `vuln_description_truncated`를 남긴다 — 조용히 빠지는 것을 §0이 금한다.
    """
    total = sum(1 for line in text.splitlines() if line.strip())
    kept = section.items or 0 if section is not None else 0
    if kept < total:
        ctx.trace.emit(
            EventKind.VULN_DESCRIPTION_TRUNCATED,
            stage=Stage.RETRIEVAL,
            subject_kind="capsule",
            subject_id=capsule.capsule_id,
            kept=kept,
            total=total,
        )
    if section is None:
        return capsule
    return capsule.transition(
        sections=[*(s.model_dump() for s in capsule.sections), section.model_dump()],
        budget_tokens=budget,
    )


def expand(
    ctx: RunContext,
    *,
    capsule: ContextCapsule,
    need: MissingEvidence,
    candidate: Candidate,
    stage: Stage = Stage.RETRIEVAL,
) -> tuple[ContextCapsule, Candidate]:
    """확장 상한을 정책에 묻고, 허용되면 캡슐을 넓힌다.

    상한 초과면 후보가 `queued`가 되고 캡슐은 그대로다. "상한 초과"와 "더 붙일 것이
    없음"은 다른 사건이라 섞지 않는다 — 후자는 Retriever가 같은 캡슐을 돌려준다.
    5단계에서 부르면 `stage`를 넘겨 전이가 그 단계로 기록되게 한다(표 C-5는 3·5단계).
    """
    action = ctx.decide(
        RoutingRequest(
            point=DecisionPoint.CAPSULE_EXPANSION,
            stage=stage,
            subject_kind="candidate",
            subject_id=candidate.candidate_id,
            signals={"expansion_count": capsule.expansion_count},
        )
    )
    if action.route is not Route.EXPAND:
        return capsule, ctx.apply_action(candidate, action, stage=stage)
    expanded = ctx.retriever.expand(capsule, need)
    ctx.store.put(expanded)
    return expanded, candidate
