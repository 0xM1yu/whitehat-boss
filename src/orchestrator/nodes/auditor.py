"""5단계 Primary Auditor — 가설과 근거 경로를 세운다.

전이 표 F-1(Finding 생성) + C-6/C-7(후보 쪽 실패 전이). 후보는 이 시점에 `triaged`이므로
파싱 실패·예산 소진이 그대로 표의 행이 된다.

**MissingEvidence 객체는 노드가 만든다.** 구조화 출력 스키마가 `Finding` 하나여서
모델은 미리 발급된 `missing_id`만 참조하고, 실제 객체는 여기서 템플릿 문구로 만든다
(S1 계약, C4에서 확정). 모델이 `missing_evidence`를 비우지 않았으면 `run()`의 발급값 도장이
어떤 ID를 썼든 발급 `missing_id` 하나로 정규화한다(이슈 #279) — 발급 ID와의 교집합만 남기면
발급 안 된 ID만 쓴 「확인 불가」 요청이 사라져 E2가 열린다. `blocking=True`인 이유: 모델이
`missing_evidence`에 무엇이든 적었다는 것은 "코드에서 확인 불가"라고 판단했다는 뜻이고, 그것이
곧 Evidence Gate E2가 막아야 할 상태다
(계획 D-h: E2의 "guard 부재"를 blocking MissingEvidence 부재로 환원).

## `callers` 사실은 캡슐에서 파생한다

예전에는 `run(callers=...)` 인자였는데 러너가 **그것을 넘기지 않았다** — 항상 빈 튜플이라
`FakeModelClient`의 `context.callers[0]` 분기가 죽은 코드였고, 실제 모델도 호출자 목록을
받지 못했다(W4).

인자를 되살리는 대신 없앴다. 캡슐이 이미 `callers` 섹션을 들고 이 함수에 들어오므로
(#63, `index_graph`), 호출자에게 같은 정보를 다시 달라고 하면 **두 번째 데이터원**이
생기고 그 둘이 갈릴 자리가 만들어진다. 여기서 파생시키면 갈릴 것이 없다.
"""

from collections.abc import Callable

from src.graph_entry import name_of
from src.orchestrator.budget import BudgetExhausted
from src.orchestrator.context import RunContext
from src.orchestrator.nodes.scout import describe_sections, describe_target
from src.orchestrator.prompt_loader import build_facts, evidence_summaries, load_prompt
from src.orchestrator.stages import Stage
from src.orchestrator.trace import EventKind
from src.schemas.candidate import Candidate
from src.schemas.capsule import ContextCapsule
from src.schemas.enums import FindingState, ReasonCode, ReproOrigin, Role
from src.schemas.evidence import Evidence
from src.schemas.finding import Finding, MissingEvidence, ReproSpec

ROLE = Role.AUDITOR
# 요청하는 구조화 출력 스키마 (`nodes/scout.py`의 같은 상수 참조).
OUTPUT_SCHEMA = Finding

# 근거 본문을 FACTS에 싣는 것은 그 본문을 **전제로 쓰인 프롬프트 버전**에만 한다
# (`critic._CONTENT_AWARE_VERSIONS`와 같은 패턴 — **자기 상수다, critic 것을 import하지 않는다**:
# 본문을 전제로 쓰였는지는 역할별 프롬프트의 성질이라 두 역할이 같은 집합을 공유할 이유가 없다).
# v3가 드는 것은 `prompts/auditor/v3/meta.yaml`이 스스로 「evidence 본문이 닿는 변경 전까지 v3는
# 올리지 않는다」를 조건으로 걸었기 때문이다. ⚠ v3 prefix는 critic v3와 달리 `body` 필드를 이름으로
# 부르지 않는다(「supplied source or facts」만) — 모델이 그 본문을 실제로 쓰는지는 미측정이다.
# v4가 드는 것은 v4 prefix가 「등록된 evidence의 content가 주장을 뒷받침할 때만」을 전제로 쓰였기
# 때문이다(#328 리뷰 MEDIUM-1 — 빠져 있으면 v3→v4 전환이 툴 로그 본문을 조용히 떨군다).
# v4_t1은 v4의 입력 서술만 바꾼 판이라(#349) v4와 같이 본문을 싣는다.
# v5는 body·truncated를 명시하고 절단된 근거의 완전성을 판단하므로 본문 배선을 유지한다.
# v6가 드는 것은 **v5에서 출발해 개선만 얹은 판**이라 v5의 본문 전제를 그대로 들고 있기
# 때문이다 — `prompts/auditor/v6/prefix.md`의 `## Evidence body completeness` 절
# (`FACTS.evidence[].body`가 생성 요약이 아니라 툴 로그의 유계 발췌라고 적는다)과
# `evidence_refs: only FACTS.evidence[*].evidence_id` 줄이 그것이다.
# ⚠ **빠져 있으면 v4→v6 전환이 툴 로그 본문을
# 조용히 떨군다**(#328 리뷰 MEDIUM-1과 같은 결) — `test_config.py`의
# `test_the_canonical_auditor_version_receives_evidence_bodies`가 그것을 문다.
# v1은 그대로 metadata만 받게 두어 바이트 동일 → 재측정(evaluation §2.3)이 안 걸린다.
_CONTENT_AWARE_VERSIONS = frozenset({"v3", "v4", "v4_t1", "v5", "v6"})

# 노드가 만드는 MissingEvidence의 문구. 모델이 자유 텍스트로 요청하게 두면 같은 요청이
# 매번 다르게 쓰여 집계가 안 된다 — 무엇이 없어서 막혔는지는 세어야 하는 값이다.
_NEED = "대상 경로를 코드만으로 확정할 수 없다: 정적분석 신호 또는 호출 그래프 근거"
_HOW = "2단계 신호 수집을 대상 파일로 다시 실행하거나 index 도구로 호출자를 확인한다"

# `callers` 사실을 뽑아 올 섹션. `index_graph.GRAPH_SECTION_KINDS`의 값과 같은 문자열인데
# 그쪽은 어댑터라 import 할 수 없다(engineering-guide.md §3). 갈리면 사실 블록이 조용히
# 비므로 `tests/test_nodes.py`가 두 값을 대조한다.
CALLERS_SECTION = "callers"


def run(
    ctx: RunContext,
    *,
    candidate: Candidate,
    capsule: ContextCapsule,
    evidences: tuple[Evidence, ...] = (),
    source: Callable[[], str] | None = None,
) -> tuple[Finding, list[MissingEvidence]]:
    """가설 Finding과 그것이 요청한 MissingEvidence를 만들어 저장한다.

    `source`를 주면 그것을 불러 `SOURCE`를 얻는다 — 슬라이스 타깃이면 러너가 슬라이스가 걸친
    함수들을 이어 붙이는 콜러블을 준다(`runner._auditor_source`, 결정 1-b). 안 주면 캡슐 대상의
    원문이다. **값이 아니라 콜러블인 것은 그 읽기를 5단계 구간 안에서 하려는 것이다** — 러너가 미리
    읽으면 파일 읽기가 5단계 `stage_end.wall_ms`에서 빠지고 `auditor_source` 이벤트가 5단계
    `stage_start`보다 먼저 난다.
    """
    ids = {
        "candidate_id": candidate.candidate_id,
        "finding_id": ctx.ids.next("finding"),
        "missing_id": ctx.ids.next("missing_evidence"),
    }
    with ctx.stage(Stage.AUDITOR):
        finding = _ask_model(
            ctx,
            ids=ids,
            candidate=candidate,
            capsule=capsule,
            evidences=evidences,
            source=source,
        )
        # **재현 명세의 출처를 하네스가 정한다** (#S7). 모델이 낸 명세는 `origin=model`로 덮고
        # (모델이 `harness`를 참칭 못 함), 비워 냈고 `synthesize_repro_spec`이 켜졌으면 후보 위치로
        # 합성한다. 발급값 도장(#279)과 같은 결이고, `stamp_issued`가 이 아래에서 전체를 재검증한다.
        finding = _ensure_repro_origin(ctx, finding, candidate, finding_id=ids["finding_id"])
        # **발급값은 하네스가 도장 찍는다**(이슈 #279) — 논거·방식은 `RunContext.stamp_issued`.
        # 초기 `state`가 F-1(`hypothesized`)이 아니면 `create()`가 `IllegalTransition`을 냈고,
        # 남의 `finding_id`면 그 행을 예외 없이 덮었다. `missing_evidence`는 **비어 있지 않으면 발급
        # `missing_id` 하나로 정규화한다** — 모델이 쓴 ID마다 해제 경로 없는 blocking이 생겼고
        # (`_materialize_missing`), 발급 ID와의 교집합만 남기면 발급 안 된 ID만 쓴 「확인 불가」
        # 요청이 MissingEvidence 0건이 되어 E2 blocking 축이 열린다. 비어 있으면 그대로 비어 있다
        # (누락 없음).
        finding = ctx.stamp_issued(
            finding,
            {
                "finding_id": ids["finding_id"],
                "run_id": ctx.run_id,
                "candidate_id": ids["candidate_id"],
                "state": FindingState.HYPOTHESIZED,
                "missing_evidence": [ids["missing_id"]] if finding.missing_evidence else [],
            },
            stage=Stage.AUDITOR,
            subject_kind="finding",
            subject_id=ids["finding_id"],
        )
        ctx.create(finding, reason=ReasonCode.AUDITOR_HYPOTHESIZED, stage=Stage.AUDITOR)
        missings = _materialize_missing(ctx, finding)
        for missing in missings:
            ctx.store.put(missing)
    return finding, missings


def _ask_model(
    ctx: RunContext,
    *,
    ids: dict[str, str],
    candidate: Candidate,
    capsule: ContextCapsule,
    evidences: tuple[Evidence, ...],
    source: Callable[[], str] | None,
) -> Finding:
    version = ctx.config.model_for(ROLE.value).prompt_template_version
    template = load_prompt(ROLE, version)
    text = ctx.read_source(capsule.target) if source is None else source()
    # content-aware 버전(`_CONTENT_AWARE_VERSIONS`)일 때만 payload 본문을 읽어 싣는다.
    # 그 외 버전은 본문 없이 종전과 동일(`critic._ask_model`과 같은 패턴).
    bodies = (
        evidence_summaries(evidences, ctx.read_payload)
        if version in _CONTENT_AWARE_VERSIONS
        else {}
    )
    prompt = template.render(
        facts=build_facts(
            run_id=ctx.run_id,
            ids=ids,
            artifact_id=candidate.artifact_id,
            target=capsule.target,
            capsule=capsule,
            source=text,
            evidences=evidences,
            evidence_bodies=bodies,
            callers=caller_names(capsule),
        ),
        candidate=_describe_candidate(candidate),
        target=describe_target(capsule.target),
        capsule_sections=describe_sections(capsule),
        source=text,
    )
    try:
        result = ctx.call_model(
            role=ROLE,
            prompt=prompt,
            schema=OUTPUT_SCHEMA,
            stage=Stage.AUDITOR,
            subject_kind="finding",
            subject_id=ids["finding_id"],
        )
    except BudgetExhausted:
        _queue_candidate(ctx, candidate, ReasonCode.BUDGET_EXHAUSTED)
        raise
    except Exception:
        _queue_candidate(ctx, candidate, ReasonCode.SCHEMA_PARSE_FAILED)
        raise
    return result.value


def _ensure_repro_origin(
    ctx: RunContext, finding: Finding, candidate: Candidate, *, finding_id: str
) -> Finding:
    """재현 명세의 `origin`을 하네스가 확정한다 (#S7).

    - 모델이 명세를 냈으면 `origin=model`로 덮는다 — 스키마가 `origin`을 노출하므로 모델이
      `harness`를 쓸 수 있는데, 그 출처는 하네스만 찍는다(발급값 도장 #279와 같은 결).
    - 명세를 비워 냈고 `synthesize_repro_spec`이 켜졌으면 후보 위치로 합성한다(`origin=harness`).
      Level 1처럼 과제가 재현법을 안 주면 비어 F-9로 멈추던 Finding이 E3까지 올라가 8단계가 돈다.
    - 꺼졌으면 비운 채로 둔다 — 종전(F-9) 그대로다.

    반환은 `repro_spec`만 바꾼 새 Finding이다. `model_copy`가 `Finding`의 검증기를 우회하지만
    (그 docstring), 바로 아래 `stamp_issued`가 `model_validate`로 전체를 재검증하므로 최종값은
    검증된다. 지금 state는 `hypothesized`라 `repro_spec` 존재가 검증기에 걸리지 않는다.
    """
    spec = finding.repro_spec
    if spec is not None:
        if spec.origin is ReproOrigin.MODEL:
            return finding
        return finding.model_copy(
            update={"repro_spec": spec.model_copy(update={"origin": ReproOrigin.MODEL})}
        )
    if not ctx.config.thresholds.synthesize_repro_spec:
        return finding
    synthesized = _synthesize_repro_spec(candidate)
    ctx.trace.emit(
        EventKind.REPRO_SPEC_SYNTHESIZED,
        stage=Stage.AUDITOR,
        subject_kind="finding",
        subject_id=finding_id,
        expected_signal=synthesized.expected_signal,
        steps=len(synthesized.steps),
    )
    return finding.model_copy(update={"repro_spec": synthesized})


def _synthesize_repro_spec(candidate: Candidate) -> ReproSpec:
    """후보 위치로 합성한 재현 명세 (#S7). `expected_signal`에 「sanitizer crash」를 넣는 것은
    8단계가 그 문구로 도구를 역선택하기 때문이다(`validate.tool_for` — 「sanitizer」가 asan에
    매핑된다). 명세가 가리키는 위치는 후보의 sink다. `steps`는 비어 있을 수 없어(스키마) 한 줄을
    둔다 — 실제 재현은 8단계 PoV가 PoC를 만들고 oracle이 판정하는 후속이다.

    ⚠ **이 도구 run(asan)이 재는 것은 점수가 아니다** (#393 리뷰 MEDIUM-2) — `validate.run`의
    asan은 추출한 `repo-vul.tar.gz`를 ASan으로 **새로 빌드**한 것을 돌린다. 채점되는 ARVO/oss-fuzz
    이미지(MSan 등)와 다른 바이너리다. 점수는 그 내부 Evidence가 아니라 oracle의 `vul`·`fix`가
    정한다(S6b). 그래서 여기서 나오는 E4를 「채점 결과」로 읽지 않는다.
    ⚠ **`steps`는 `validate.run`에서 argv가 된다**(`_call(ctx, tool, [tool, *spec.steps], …)`) —
    이 한 줄이 그대로 도구 인자·Evidence 로그에 실린다. 지금 경로는 리스트로 넘겨 셸을 안 타지만
    값이 argv에 남는다는 사실은 적어 둔다 (#393 리뷰 LOW-1)."""
    where = candidate.location.symbol or candidate.location.file
    return ReproSpec(
        steps=[f"build the target and run it on the generated PoC for {where}"],
        expected_signal=f"sanitizer crash at {where}",
        origin=ReproOrigin.HARNESS,
    )


def caller_names(capsule: ContextCapsule) -> tuple[str, ...]:
    """캡슐의 `callers` 섹션에서 **심볼 이름만** 뽑는다. 없으면 빈 튜플.

    사실 블록의 `callers`는 `FakeContext.callers` 주석대로 "source→sink 경로 구성용"이라
    맨 이름이 필요하고, 섹션 본문은 호출 **지점**(`main main.c:12`)이라 알갱이가 다르다.
    같은 데이터원에서 나온 두 표현이므로 갈릴 수 없다 — 두 번째 데이터원을 만들지 않은
    것이 요점이다.

    같은 호출자가 여러 줄에서 나오면 한 번만 센다. `dict.fromkeys`는 순서를 지키므로
    캡슐이 결정론적이면 이 값도 결정론적이다(engineering-guide.md §5).
    """
    for section in capsule.sections:
        if section.kind == CALLERS_SECTION:
            return tuple(dict.fromkeys(name_of(entry) for entry in section.body))
    return ()


def _queue_candidate(ctx: RunContext, candidate: Candidate, reason: ReasonCode) -> None:
    """표 C-6·C-7. 후보를 폐기하지 않고 사유와 함께 queued로 둔다."""
    if candidate.state is not FindingState.TRIAGED:
        return
    ctx.apply(candidate, to=FindingState.QUEUED, reason=reason, stage=Stage.AUDITOR)


def _materialize_missing(ctx: RunContext, finding: Finding) -> list[MissingEvidence]:
    return [
        MissingEvidence(
            missing_id=missing_id,
            run_id=ctx.run_id,
            finding_id=finding.finding_id,
            need=_NEED,
            how_to_obtain=_HOW,
            blocking=True,
        )
        for missing_id in finding.missing_evidence
    ]


def _describe_candidate(candidate: Candidate) -> str:
    cwes = ", ".join(candidate.suspected_cwe) or "(없음)"
    reasons = ", ".join(candidate.reason_codes) or "(없음)"
    return f"{candidate.candidate_id} · 추정 CWE: {cwes} · 트리아지 신호: {reasons}"
