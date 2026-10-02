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
from src.schemas.enums import FindingState, PathStepKind, ReproOrigin

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

    제안: architecture.md §4의 E3 조건이 "E2의 조건 전부 + Critic이 반박 실패 +
    **재현 명세 존재**"이고 §3의 8단계 입력이 "`Finding` + 재현 명세"인데, 문서에 이 구조체가 없다.
    8단계 Tool Validation이 무엇을 실행하고 무엇을 성공으로 볼지가 없으면 E3→E4 판정이
    사람 판단이 되므로 신설했다.
    """

    # 샌드박스에서 순서대로 실행할 단계. 실행 자체는 ToolRunner가 한다.
    steps: list[NonEmptyStr] = Field(min_length=1)
    # 무엇이 관측되면 재현 성공인가. 예: "ASAN heap-buffer-overflow at parse_header".
    expected_signal: NonEmptyStr
    # 이 명세를 누가 만들었나 (#S7). 기본은 `model` — 모델이 낸 명세의 값이다. 스키마에 드러나
    # 모델이 `harness`를 쓸 수도 있으나 하네스가 저장 전에 덮는다(`auditor._ensure_repro_origin`,
    # 발급값 도장 #279와 같은 결). `harness`는 하네스가 합성한 명세이고, 뜻은 `ReproOrigin` 주석이
    # 든다. **등급 판정(`gate.evaluate`)은 이 값으로 갈리지 않는다** — E3은 명세 존재만,
    # E4는 실행 로그를 본다. 게이트는 이 값을 `gate_eval` trace에만 싣는다.
    origin: ReproOrigin = ReproOrigin.MODEL


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
    # **기본값을 주지 않는다 — 스키마의 `required`가 모델의 생략을 가른다.** 2026-09-11 ATOM
    # 실측(이슈 #172). 아래 수는 그 보고서 **§3.1 표에서 재계산한 것**이고, 사실 블록에 없는
    # 근거 ID는 **전 조건 0**이었다 — 강제해도 환각하지 않는다.
    #
    #   `stock`     유효 20/20 · 채움 **0/20**   ← 네 조건 전부에서 비웠다
    #   `required`  유효 10/20 · 채움 **10/10**  ← 성공한 호출은 전부 채웠다
    #
    # ⚠ **그 보고서 §1 요약은 「13/13」·「5/20」이라 적는데 §3.1 표에서 재구성되지 않는다**
    #   (표로는 10/10, 그리고 아래 5/15다). 어느 쪽이 맞는지는 원천 JSON이 ATOM에 있어 여기서
    #   못 가른다 — **재구성되는 쪽만 적는다**(CLAUDE.md §0·§2.1). 불일치는 이슈 #172가 든다.
    #
    # ⚠ **대가가 있고 그것은 서버 쪽이다** — `required`는 공백 폭주를 부른다. 유효 JSON이
    #   `disable_any_whitespace=False`에서 **5/15**(조건 A·B·D), `true`에서 **5/5**다.
    #   그 플래그는 하네스가 보내는 값이 아니라 `vllm serve`의 인자이고, 정본 자리는
    #   `config/models.yaml`의 해당 모델 「기동 조건」 주석이다.
    #   **그 조건 없이 이 required를 켜면 채움을 얻고 유효율을 잃는다.**
    #
    # ⚠ **이 수들을 비율로 읽지 마라.** 보고서 자신이 그것을 금지한다 — 같은 인자의 조건 A와 B가
    #   2/5와 0/5를 냈고 **n=5**다. 읽을 것은 *"조건 C만 전 팔 5/5"*다. 그리고 **한 프롬프트·
    #   한 모델·한 역할**의 결과이며 그 run은 `run_id`가 없어 비교표 밖이다.
    # ⚠ `min_items`는 **넣지 않는다.** 채움률로 고른 것이 아니다 — 그 팔의 채움률은 정의상
    #   100%라 비교 대상이 못 된다(프로브 docstring). 빈 리스트가 **근거 없는 `hypothesized`
    #   초안의 정당한 출력**이라서다.
    path: list[PathStep]
    # 트리거 가능성의 명시적 상태 (@0xM1yu 요청, 2026-08-28). 프롬프트가 `rca_draft`·`confidence`의
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
    # `path`와 같은 이유로 기본값이 없다 (위 주석).
    evidence_refs: list[Identifier]
    # ⚠ **`missing_evidence`는 대상이 아니다.** 형태만 같다 — 이쪽은 모델이 「무엇이 없는가」를
    # 알릴 때만 채우는 선택 필드라 required로 올리면 빈 배열을 강제로 받게 된다.
    missing_evidence: list[Identifier] = Field(default_factory=list)
    repro_spec: ReproSpec | None = None
    # 마지막이다 — 모델이 채우는 값이 아니라 하네스가 stamp한다(`context.py` `call_model`).
    # v2 프롬프트도 "harness metadata, do not fabricate"로 그렇게 적는다.
    produced_by: ProducedBy

    @model_validator(mode="after")
    def _promoted_states_need_backing(self) -> "Finding":
        """validated 이상은 재현 명세·근거·경로를 동반해야 한다.

        architecture.md §4: E3(`validated`)의 조건은 "E2의 조건 전부 + Critic 반박 실패 + 재현
        명세 존재"이고, **경로(source→sink)는 그 E2의 조건**이다 — §4 상태 흐름이 E2를 지난
        뒤에야 E3로 오므로 `validated`에 이른 Finding은 그 경로를 들고 있어야 한다(그래서 아래가
        `path`도 요구한다). E4(`proven`)는 재현 로그를 요구한다(종료 코드가 0도 미측정도 아닌
        런타임 Evidence — #232). 근거 0건짜리 `validated`는 이 시스템의 전제(Evidence 중심 판정)를
        정면으로 뒤집으므로 만들어질 수 없어야 한다.
        `hypothesized`/`queued` 초안 단계에는 요구하지 않는다.

        **게이트가 이 전제를 강제한다** — `gate.py`의 `p3`가 `p2`(경로 양 끝·그래프 근거·
        blocking 없음)를 통째로 요구해 E3가 E2를 함의한다(이슈 #154 — 2026-09-09에 경로,
        2026-09-23에 나머지 둘). 경로 조건이 없던 때, 경로 없이 E3를 받은 Finding이 `validated`로
        전이하려다 아래 `path` 검사에서 `ValidationError`로 run을 죽였다. 남은 것 둘(이슈 #154의
        남은 항목): E4는 이 포함 밖이고(`p4`가 경로를 안 본다), 7단계 진입이 등급이 아니라
        상태라 E1도 Critic까지 올라온다 — 다만 거기서 E3는 못 된다.
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
