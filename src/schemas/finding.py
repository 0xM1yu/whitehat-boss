"""Finding / MissingEvidence / ReproSpec 스키마 (agent-contracts.md §3)."""

from typing import Literal

from pydantic import Field, model_validator

from src.schemas.common import (
    Cwe,
    HarnessModel,
    Identifier,
    NonEmptyStr,
    ProducedBy,
)
from src.schemas.enums import FindingState, PathStepKind

# Finding이 가질 수 있는 상태 (architecture.md §4). Candidate 쪽 값(candidate/triaged)은
# Auditor가 Finding을 만드는 시점에 이미 지나갔으므로 제외한다.
FindingStateValue = Literal[
    FindingState.HYPOTHESIZED,
    FindingState.QUEUED,
    FindingState.VALIDATED,
    FindingState.PROVEN,
    FindingState.REPORTED,
]

# 근거 없이 도달할 수 없는 상태. Evidence Gate E3 이상이 필요한 구간이다.
_EVIDENCE_BACKED_STATES = frozenset(
    {FindingState.VALIDATED, FindingState.PROVEN, FindingState.REPORTED}
)


class PathStep(HarnessModel):
    """source→sink 경로의 한 단계."""

    step: PathStepKind
    symbol: NonEmptyStr
    note: str | None = None


class MissingEvidence(HarnessModel):
    """Auditor가 "코드에서 확인 불가"라고 판단해 요청한 근거 (agent-contracts.md §2.2).

    `blocking=True`면 이 근거 없이는 Evidence Gate를 통과시키지 않는다.
    """

    missing_id: Identifier
    run_id: Identifier
    finding_id: Identifier
    need: NonEmptyStr
    how_to_obtain: NonEmptyStr
    blocking: bool


class ReproSpec(HarnessModel):
    """재현 명세.

    제안: architecture.md §4의 E3 조건이 "Critic이 반박 실패 + **재현 명세 존재**"이고
    §3의 8단계 입력이 "`Finding` + 재현 명세"인데, 문서에 이 구조체가 없다.
    8단계 Tool Validation이 무엇을 실행하고 무엇을 성공으로 볼지가 없으면 E3→E4 판정이
    사람 판단이 되므로 신설했다.
    """

    # 샌드박스에서 순서대로 실행할 단계. 실행 자체는 ToolRunner가 한다.
    steps: list[NonEmptyStr] = Field(min_length=1)
    # 무엇이 관측되면 재현 성공인가. 예: "ASAN heap-buffer-overflow at parse_header".
    expected_signal: NonEmptyStr


class Finding(HarnessModel):
    """Auditor가 세운 취약점 가설과 그 이후의 상태.

    **전이 시 주의**: `model_copy(update=...)`는 아래 검증기를 우회한다. 상태 전이는
    `HarnessModel.transition()`으로 한다 — 그래야 `proven`으로 올릴 때 재현 명세와
    근거가 있는지 실제로 확인된다.

    **필드 선언 순서가 계약이다 (#102).** 이 순서가 그대로 `model_json_schema()`의
    property 순서가 되고, 그 스키마가 `response_format`으로 서버에 실린다
    (`adapters/openai_compat._request_payload`). 순서는 `prompts/auditor/v2`
    prefix 90-105행이 요구하는 것과 같다 — **범주값(`state`·`trigger_status`)을
    숫자값(`confidence`)과 산문(`rca_draft`)보다 먼저** 내게 해서 뒤 필드가 앞의
    판정을 거스르지 못하게 한다는 것이 근거다. 근거의 출처는 관측이다: 이전 실험에서
    `confidence` 0.2인데 `rca_draft`는 "취약하다"고 쓰는 모순이 있었고, 상태를 먼저
    확정하게 하니 줄었다.

    **그 효과가 실제로 나는지는 아직 안 쟀다.** 구조화 출력 백엔드(xgrammar)가
    property 순서대로 생성을 강제하는지에 달렸고, v2 프롬프트로 도는 첫 run이 그
    답을 준다. 순서를 맞춰 두는 것 자체는 어느 쪽이든 해롭지 않다.
    """

    finding_id: Identifier
    run_id: Identifier
    candidate_id: Identifier
    state: FindingStateValue
    path: list[PathStep] = Field(default_factory=list)
    # 트리거 가능성의 명시적 상태 (윤경 요청, 2026-08-28). 프롬프트가 `rca_draft`·`confidence`의
    # 모순(예: `not_vulnerable`인데 "취약"이라 서술)을 줄이려 이 상태를 **먼저** 확정하는데, 그
    # 값이 산출물에 안 남아 "모델이 실제로 그 상태로 판정했는지"를 사후에 못 봤다. Auditor가
    # 내는 값: `triggerable` · `guarded` · `no_reach` · `reachability_unknown` · `no_signal`.
    #
    # **optional·nullable이다.** 서버의 확정 프롬프트(2026-08-29 반영)에 위 다섯 값이 들어갔고,
    # 이 필드가 없으면 emit해도 파싱에서 버려진다 — 그래서 먼저 받을 자리를 낸다. 아직 안 내는
    # 프롬프트(기존 실험)에서는 `None`으로 남는다.
    #
    # 그래서 `None`은 두 뜻을 겹친다 — "프롬프트가 안 시킨 run"과 "시켰는데 모델이 안 낸 run".
    # **채움률은 `produced_by.prompt_version`별로 센다**: 하네스가 그 값을 stamp하므로
    # (`context.py` `call_model`) 어느 프롬프트로 돈 Finding인지가 건마다 남고, 두 `None`이
    # 사후에 갈린다. 전체를 한 분모로 세면 그 둘이 뭉개진다.
    # **타입을 일부러 `str`로 둔다.** 요청의 목적은 "값이 산출물에 안 남아 못 봤다"의 해소,
    # 곧 **관측**이다. `str`은 뭐가 나오든 다 기록하고 finding을 떨구지 않는다. 실제 emit
    # 분포를 본 뒤에야 `Literal`/enum으로 좁혀 강제한다 — 그 보류가 #102이다.
    trigger_status: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    rca_draft: str = ""
    cwe: Cwe
    title: NonEmptyStr
    evidence_refs: list[Identifier] = Field(default_factory=list)
    missing_evidence: list[Identifier] = Field(default_factory=list)
    repro_spec: ReproSpec | None = None
    # 마지막이다 — 모델이 채우는 값이 아니라 하네스가 stamp한다(`context.py` `call_model`).
    # v2 프롬프트도 "harness metadata, do not fabricate"로 그렇게 적는다.
    produced_by: ProducedBy

    @model_validator(mode="after")
    def _promoted_states_need_backing(self) -> "Finding":
        """validated 이상은 재현 명세·근거·경로를 동반해야 한다.

        architecture.md §4: E3(`validated`)의 조건은 "Critic 반박 실패 + 재현 명세 존재"이고,
        **경로(source→sink)는 그 앞 E2의 조건**이다 — §4 상태 흐름이 E2를 지난 뒤에야 E3로
        오므로 `validated`에 이른 Finding은 그 경로를 들고 있어야 한다(그래서 아래가 `path`도
        요구한다). E4(`proven`)는 재현 로그를 요구한다. 근거 0건짜리 `validated`는 이 시스템의
        전제(Evidence 중심 판정)를 정면으로 뒤집으므로 만들어질 수 없어야 한다.
        `hypothesized`/`queued` 초안 단계에는 요구하지 않는다.

        **게이트가 이 전제를 grading에서 강제한다** — `gate.py`의 `p3`가 `path_ok`를 포함해
        경로 없는 Finding은 E3를 못 받는다(이슈 #154). 그 조건이 없던 때, 경로 없이 E3를 받은
        Finding이 `validated`로 전이하려다 아래 `path` 검사에서 `ValidationError`로 run을 죽였다.
        """
        if self.state not in _EVIDENCE_BACKED_STATES:
            return self
        if self.repro_spec is None:
            raise ValueError(f"state={self.state}에는 repro_spec이 필요하다 (architecture §4 E3)")
        if not self.evidence_refs:
            raise ValueError(f"state={self.state}에는 evidence_refs가 최소 1건 필요하다")
        if not self.path:
            raise ValueError(f"state={self.state}에는 source→sink 경로가 필요하다")
        return self
