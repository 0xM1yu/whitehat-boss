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
# 길이 설명을 넣으면 회계가 조용히 틀리기 때문이다(council 판정). 이 섹션은 body에서 실측한다.
VULN_DESCRIPTION_KIND = "vuln_description"


def _vuln_description_section(text: str, budget: int) -> CapsuleSection | None:
    """과제 설명을 담는 캡슐 섹션. 남은 예산 안에 드는 줄까지만 담고 `tokens`를 본문에서
    실측한다(고정 자리표시자가 아니라 실측이라 §0을 안 어긴다). 한 줄도 못 담으면 `None`.

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
    `target_function`이 Scout가 받는 렌더를 센다. Auditor의 EXPANDED는 entry를 감싸는 함수가
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
        query = Query(
            run_id=ctx.run_id,
            target=target,
            keywords=list(keywords),
            budget_tokens=budget,
            slice_id=slice_id,
        )
        capsule = ctx.retriever.search(query, level)
        # 과제 설명이 있으면 캡슐에 vuln_description 섹션으로 싣는다(PoV 배선). Retriever를
        # 안 건드리고 노드에서 붙여 구현(fake·hybrid)에 무관하게 한 자리에서 한다 — 설명은 run
        # 단위 사실이라 target별 Retriever가 알 필요가 없다. 남은 예산 안에서만 담는다.
        if ctx.vuln_description:
            section = _vuln_description_section(
                ctx.vuln_description, capsule.budget_tokens - capsule.used_tokens
            )
            if section is not None:
                capsule = capsule.transition(
                    sections=[*(s.model_dump() for s in capsule.sections), section.model_dump()]
                )
        ctx.store.put(capsule)
    return capsule


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
