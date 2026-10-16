"""Finding / MissingEvidence / ReproSpec 스키마 (agent-contracts.md §3)."""

from typing import Annotated, Literal

from pydantic import Field, WithJsonSchema, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema

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
# ⚠ **이 불변식이 보증하는 것은 「근거가 있다」가 아니다** — 「Evidence 레코드가 하나라도
# 붙어 있다」다.
# 붙이는 쪽(`nodes/gate_node.attach_observed`)이 `ok`를 안 보므로 실패 기록(`ok=False`)으로도
# 충족된다 — 8단계는 처음부터 그랬고 6단계가 이슈 #552로 같아졌다. 등급 쪽은 `gate.evaluate`의
# `usable`이 `ok=True`만 세므로 실패 기록만으로는 E1에 못 간다. 두 규칙이 다른 층에 있다.
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
    # ⏵ 2026-10-13(#232) — **이 문자열은 도구 이름을 고르는 데만 쓰인다**(`validate.tool_for()`).
    # 산출물과 대조하는 코드는 없다 — 등급(E4)은 종료 코드로만 갈린다.
    expected_signal: NonEmptyStr
    # 이 명세를 누가 만들었나 (#S7). 기본은 `model` — 모델이 낸 명세의 값이다. 스키마에 드러나
    # 모델이 `harness`를 쓸 수도 있으나 하네스가 저장 전에 덮는다(`auditor._ensure_repro_origin`,
    # 발급값 도장 #279와 같은 결). `harness`는 하네스가 합성한 명세이고, 뜻은 `ReproOrigin` 주석이
    # 든다. **등급 판정(`gate.evaluate`)은 이 값으로 갈리지 않는다** — E3은 명세 존재만,
    # E4는 실행 로그를 본다. 게이트는 이 값을 `gate_eval` trace에만 싣는다.
    origin: ReproOrigin = ReproOrigin.MODEL


# === 감사 절차 블록 (Auditor 프롬프트 v10의 `audit`) ==========================================
#
# 필드·필수·값 집합은 `prompts/auditor/v10/prefix.md`의 「Exact output contract」가 적은 그대로다.
# **원천은 그 산문이고 아래는 그것을 옮긴 것이다** — 값 집합을 바꾸려면 산문을 먼저 고친다
# (`tests/test_auditor_procedure_schema.py`가 둘을 대조한다). 계약이 「Every field shown is
# required」라 **기본값이 있는 필드가 없다** — nullable(`gap`)도 required다.
#
# ⚠ **아래 클래스의 docstring은 `response_format`으로 모델 요청 payload에 그대로 실린다**
# (`AuditProcedure`의 `$defs`). 커밋 SHA·이슈 번호·소스 파일 경로 같은 저장소 내부 참조를
# docstring에 넣지 않는다 — 그런 이력은 이런 `#` 주석에 둔다.


class AuditPoint(HarnessModel):
    """위험 지점 — 그 지점을 담은 함수와, SOURCE에서 그대로 옮긴 결함 식."""

    symbol: NonEmptyStr
    expression: NonEmptyStr


class AuditOrigin(HarnessModel):
    """위험 지점이 쓰는 값의 출처 — 입력 · 인자 · 지역 · 상수."""

    kind: Literal["input", "param", "local", "const"]
    fact: NonEmptyStr


class AuditDefense(HarnessModel):
    """보이는 방어 — 검사 식, 그 상태, 검사가 실패한다면 어디가 틀렸는가."""

    expression: NonEmptyStr
    status: Literal["absent", "fails", "covers"]
    gap: Literal["width", "operands", "both_bounds", "off_by_one", "order", "other"] | None
    fact: NonEmptyStr


class AuditRefutation(HarnessModel):
    """반박 시도의 결과 — 무너뜨렸는가 · 막혔는가 · 경로가 못 도는가 · 결정적 코드가 없는가."""

    result: Literal["defeated", "holds", "path_not_executable", "decisive_code_missing"]
    witness: NonEmptyStr
    missing_information: list[NonEmptyStr]


class AuditBlock(HarnessModel):
    """취약점 감사 한 건 — 범주 · 위험 지점 · 값의 출처 · 경로 · 방어 · 반박."""

    category: Literal[
        "spatial_bounds",
        "lifetime_null",
        "initialization",
        "resource",
        "integer",
        "type",
        "control_flow",
        "input_validation",
        "format_string",
        "injection",
        "path_traversal",
        "info_exposure",
        "concurrency",
        "authorization",
        "crypto_config",
    ]
    point: AuditPoint
    origin: AuditOrigin
    # ⚠ `min_length`를 걸지 않는다. 산문의 규칙은 「reachability_unknown이면 `[]`일 수 있고
    # triggerable·guarded·no_reach이면 비지 않는다」(v10 prefix 「4. Keep fields consistent」 첫
    # 항목)인데 그것은 형제 필드 `trigger_status`에 달렸다 — 이 블록은 그 상태를 모르므로 어느
    # 쪽으로 고정해도 합법 출력 하나를 거절한다. `Finding.path`에 `min_items`를 안 넣은 것과 같은
    # 논법이다(빈 리스트가 정당한 출력인 자리가 있다). 그 규칙은 산문 몫이고 JSON 스키마는 싣지
    # 않는다(「Exact output contract」 마지막 문단).
    path: list[PathStep]
    defense: AuditDefense
    refutation: AuditRefutation


# === v12: `no_signal`에도 채우는 감사 블록 (이슈 #578) ===========================================
#
# ⏵ 2026-10-10 — FT 데이터(Teacher v4)가 `no_signal`에도 `audit`를 채운다(팀 결정 2026-10-10). 그때
# 블록은 「가장 가까웠던 후보를 다섯 점검으로 걸러낸 기록」이고 값 셋이 더해진다 — `category`의
# `none` · `defense.status`의 `not_applicable` · `refutation.result`의 `no_candidate`. 셋은
# `no_signal`에서만 쓰고 `no_signal`이면 셋 다 쓴다 — 그 조건은 블록이 상태를 모르므로 v12의 최종 답
# (`schemas/auditor.AuditProcedureAuditAlways`)이 건다.
#
# **위 세 클래스(`AuditBlock` · `AuditDefense` · `AuditRefutation`)는 고치지 않는다.** 그 JSON
# 스키마가 v10·v11 요청 payload의 `$defs`라 enum을 넓히면 그 판들의 프롬프트가 안 바뀌었는데 요청
# 바이트가 바뀐다(agent-contracts §4 규칙 1, evaluation §2.3). v11이 `AuditProcedure`에 한 선례대로
# 형제를 둔다. 값 집합은 위 클래스의 것 + 하나씩이다(`tests/test_auditor_v12.py`가 문다).
# `AuditPoint` · `AuditOrigin` · `PathStep`은 값이 안 바뀌어 그대로 쓴다.
#
# ⚠ docstring은 v12 요청 payload에 실린다 — 저장소 내부 참조를 넣지 않는다(위 ⚠와 같다).


class AuditDefenseWithNoSignal(HarnessModel):
    """보이는 방어 — 검사 식, 그 상태, 검사가 실패한다면 어디가 틀렸는가.

    `not_applicable`은 위험한 연산이 아니라서 방어가 필요 없던 후보에만 쓴다(`no_signal`).
    """

    expression: NonEmptyStr
    status: Literal["absent", "fails", "covers", "not_applicable"]
    gap: Literal["width", "operands", "both_bounds", "off_by_one", "order", "other"] | None
    fact: NonEmptyStr


class AuditRefutationWithNoSignal(HarnessModel):
    """반박 시도의 결과 — 무너뜨렸는가 · 막혔는가 · 경로가 못 도는가 · 결정적 코드가 없는가 ·
    위험한 연산으로 서는 후보가 없는가(`no_signal`)."""

    result: Literal[
        "defeated", "holds", "path_not_executable", "decisive_code_missing", "no_candidate"
    ]
    witness: NonEmptyStr
    missing_information: list[NonEmptyStr]


class AuditBlockWithNoSignal(HarnessModel):
    """취약점 감사 한 건 — 범주 · 위험 지점 · 값의 출처 · 경로 · 방어 · 반박.

    `no_signal`이면 가장 가까웠던 후보를 같은 다섯 점검으로 걸러낸 기록이고 `category`는 `none`이다.
    """

    category: Literal[
        "spatial_bounds",
        "lifetime_null",
        "initialization",
        "resource",
        "integer",
        "type",
        "control_flow",
        "input_validation",
        "format_string",
        "injection",
        "path_traversal",
        "info_exposure",
        "concurrency",
        "authorization",
        "crypto_config",
        "none",
    ]
    point: AuditPoint
    origin: AuditOrigin
    # `AuditBlock.path`와 같은 이유로 `min_length`를 안 건다 — `no_signal`도 빈 경로가 합법이다
    # (Teacher v4 검증기가 `no_signal`·`reachability_unknown`의 빈 경로를 받는다).
    path: list[PathStep]
    defense: AuditDefenseWithNoSignal
    refutation: AuditRefutationWithNoSignal


# `no_signal`에서만 쓰는 값 — v12 최종 답의 검증기와 노드가 같이 읽는다.
NO_SIGNAL_ONLY_AUDIT_VALUES = {
    "category": "none",
    "defense.status": "not_applicable",
    "refutation.result": "no_candidate",
}


def no_signal_only_values_in(block: AuditBlockWithNoSignal) -> dict[str, str]:
    """`block`이 쓴 `no_signal` 전용 값 — 위 표의 키 중 그 값을 든 것만."""
    actual = {
        "category": block.category,
        "defense.status": block.defense.status,
        "refutation.result": block.refutation.result,
    }
    return {
        key: value for key, value in actual.items() if NO_SIGNAL_ONLY_AUDIT_VALUES[key] == value
    }


def _canonical_audit(value: object) -> object:
    """`Finding.audit`에 들어오는 v12 블록을 dict로 풀어 아래 `left_to_right` 합집합이 다시 고르게
    한다 — `AuditBlock`으로 서는 값이면 `AuditBlock`, `no_signal` 전용 값을 들면 v12 블록이다.

    이유는 저장 왕복이다. `HarnessModel.transition`·저장소 재적재는 `model_dump()`한 dict를 다시
    검증하므로, 들어올 때의 클래스를 그대로 두면 v12 `triggerable` 블록이 전이 한 번에
    `AuditBlock`으로 바뀌어 같은 Finding이 전이 앞뒤로 같지 않게 된다. 값으로 정하면 앞뒤가 같다.
    """
    if isinstance(value, AuditBlockWithNoSignal):
        return value.model_dump()
    return value


# `Finding.cwe`의 타입 — 검증은 `Cwe | None`, **JSON 스키마는 종전 `Cwe` 필드 그대로**다
# (⏵ 2026-10-09, 이슈 #525 선택지 1 — 이유는 그 필드 주석). 아래 dict는 넓히기 전
# `Finding.model_json_schema()["properties"]["cwe"]`를 그대로 옮긴 것이고,
# `tests/test_nullable_cwe.py`가 v8·v9·v10 봉투와 한 방 `Finding`의 요청 스키마를 바이트로 문다.
_FINDING_CWE_JSON_SCHEMA = {"pattern": r"^CWE-\d+$", "title": "Cwe", "type": "string"}
_FindingCwe = Annotated[Cwe | None, WithJsonSchema(_FINDING_CWE_JSON_SCHEMA)]


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
    #   못 가른다 — **재구성되는 쪽만 적는다**(CLAUDE.md §0·§2.1). 불일치는 이슈 #172가 다뤘고
    #   2026-10-12 닫았다 — 원천 JSON은 ATOM에 있었고 그 장비 종료(#340)로 못 가른다.
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
    # v10 감사 절차 블록(`AuditBlock`) — v10 응답(`AuditProcedure`)의 `audit`를 하네스가 옮겨 싣는
    # 자리다.
    # ⚠ **`None`이 두 뜻을 겹친다** — 「이 판은 `audit`를 안 묻는다」(v1~v9)와 「v10이 `no_signal`로
    # 판정했다」다(그 판정의 계약이 `audit: null`이다). `Finding.trigger_status`가 같은 함정을 두고
    # 처방을 적는다(아래 그 필드 주석) — **채움률은 `produced_by.prompt_version`별로 센다.**
    # 응답 쪽 `AuditProcedure.audit`는 required·nullable이라 그쪽에서는 안 겹친다.
    #
    # 선언 자리는 `path` 바로 뒤, `trigger_status` 앞이다. ⚠ **이 자리가 생성 순서를 정하지는
    # 않는다** — 아래 `SkipJsonSchema` 때문에 이 필드는 요청 스키마에 없고, 따라서 문법이 강제하는
    # property 순서에도 없다. 위 #102의 근거(범주값을 먼저 내게 한다)가 걸리는 것은 **모델이 내는
    # `AuditProcedure`** 쪽이고, 여기서 자리가 바꾸는 것은 `model_dump` 키 순서뿐이다. 그 순서를
    # v10 계약과 같게 둔 것은 저장된 Finding을 읽는 사람이 같은 순서로 읽게 하려는 것이다 —
    # 이미 있는 필드의 상대 순서는 안 바뀐다.
    #
    # **JSON 스키마에서 뺀다(`SkipJsonSchema`).** 스키마에 두면 `Finding`을 요청하는 한 방 판과
    # `AuditorAction`(`$defs`에 `Finding`이 든다 — v8·v9 루프)이 **프롬프트가 안 바뀌었는데 요청
    # 바이트가 바뀐다**(evaluation §2.3). 모델이 채울 값이 아닌 필드를 그 판들에 보여 주는 일이기도
    # 하다. 검증·직렬화(`model_dump`)에는 그대로 선다.
    #
    # ⏵ 2026-10-10 (이슈 #578) — v12 블록(`AuditBlockWithNoSignal`)도 받는다. v12는 `no_signal`에도
    # 블록을 채우므로 **v12 Finding에서는 `None`이 안 난다** — 위 「두 뜻」은 v1~v11의 것이다.
    # 어느 클래스로 서는지는 **값이 정한다**(`_canonical_audit` · `left_to_right`): `AuditBlock`
    # 으로 서는 값이면 `AuditBlock`, `no_signal` 전용 값(`category` `none` 등)을 들면 v12 블록이다.
    # 그래서 블록 클래스로 판을 가르지 않는다 — 판은 `produced_by.prompt_version`이 든다.
    # 요청 스키마는 그대로다 — `SkipJsonSchema`가 합집합째로 뺀다(`tests/test_auditor_v12.py`가
    # `Finding` 스키마의 sha256을 문다).
    audit: SkipJsonSchema[AuditBlock | AuditBlockWithNoSignal | None] = Field(
        default=None, union_mode="left_to_right"
    )
    # 트리거 가능성의 명시적 상태 (@Yunse4l 요청, 2026-08-28). 프롬프트가 `rca_draft`·`confidence`의
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
    # 곧 **관측**이다. `str`은 뭐가 나오든 다 기록하고 finding을 떨구지 않는다.
    # ⏵ 2026-10-10 — 이 필드는 좁히지 않기로 정했다(#102). 다섯 값의 강제는 서빙 판(v10 이상)의
    # 응답 스키마 `AuditProcedure.trigger_status`(`schemas/auditor.py`)가 맡는다. 여기를 좁히면 이
    # `Finding`을 요청 스키마로 보내는 v1~v9의 요청 바이트가 판 라벨 그대로 바뀐다
    # (위 `audit`와 같은 이유).
    trigger_status: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    rca_draft: str = ""
    # ⏵ 2026-10-09 (이슈 #525 선택지 1) — **`no_signal`에서만 `None`일 수 있다**(아래
    # `_cwe_is_null_only_for_no_signal`). `no_signal`은 뿌리 원인을 못 세운 판정이라 참인 CWE가
    # 없다 — non-null을 강제하면 모델이 지어낸 값이 SARIF `ruleId`와 보고서 중복 제거 키로 갈 수
    # 있었다(그 값이 실제로 지어낸 것이었는지는 미측정 — 이슈 #525 「안 쟀다」).
    # ⚠ **JSON 스키마는 종전 그대로 둔다**(`_FINDING_CWE_JSON_SCHEMA`) — `Finding`을 요청하는 한 방
    # 판과 `AuditorAction`(v8·v9, `$defs`에 `Finding`)의 요청 바이트가 이 스키마라, `anyOf`로
    # 넓히면 프롬프트가 안 바뀌었는데 그 판들의 요청이 바뀐다(evaluation §2.3 — 위 `audit`의
    # `SkipJsonSchema`와 같은 규율). 그 판들의 계약은 「non-null」이고 그대로 기록이다. `None`을
    # 싣는 길은 하네스가 v11 응답(`AuditProcedureNullableCwe`)을 옮겨 담는 것 하나다.
    # ⏵ 2026-10-10 — v12 응답(`AuditProcedureAuditAlways` — 그 서브클래스, 이슈 #578)도 같은 길이다.
    cwe: _FindingCwe
    title: NonEmptyStr
    # `path`와 같은 이유로 기본값이 없다 (위 주석).
    evidence_refs: list[Identifier]
    # ⚠ **`missing_evidence`는 대상이 아니다.** 형태만 같다 — 이쪽은 모델이 「무엇이 없는가」를
    # 알릴 때만 채우는 선택 필드라 required로 올리면 빈 배열을 강제로 받게 된다.
    missing_evidence: list[Identifier] = Field(default_factory=list)
    repro_spec: ReproSpec | None = None
    # 마지막이다 — 모델이 채우는 값이 아니라 하네스가 stamp한다. 찍는 자리는 **둘**이다:
    # `context.py`의 `call_model`(응답 루트에 이 필드가 있을 때)과, 루프 봉투는 루트에 없으므로
    # `nodes/auditor._finding_from`(⏵ 2026-10-06 — 그 전 v8·v9 루프는 모델이 쓴 값이 남았다).
    # v2 프롬프트도 "harness metadata, do not fabricate"로 그렇게 적는다.
    produced_by: ProducedBy

    @field_validator("audit", mode="before")
    @classmethod
    def _audit_by_value(cls, value: object) -> object:
        return _canonical_audit(value)

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
        전이하려다 아래 `path` 검사에서 `ValidationError`로 run을 죽였다. 남은 것 둘(이슈 #154는
        2026-10-12 닫았다 — 남은 항목은 그 이슈가 미러였던 `docs/open-questions/`의 결함 본문
        ⏵ 절에 있다): E4는 이 포함 밖이고(`p4`가 경로를 안 본다), 7단계 진입이 등급이 아니라
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

    @model_validator(mode="after")
    def _cwe_is_null_only_for_no_signal(self) -> "Finding":
        """`cwe`가 `None`이면 `trigger_status`가 `no_signal`이어야 한다(이슈 #525 선택지 1).

        반대 방향(`no_signal`이면 `None`이어야 한다)은 **안 건다** — v1~v10 Finding은 `no_signal`
        에도 CWE 문자열을 들고 저장돼 있고(그 판들의 계약이 non-null이었다), 그 행이 다시 읽혀야
        한다. 그 값이 지어낸 것인지는 이 검증기가 못 가른다 — `produced_by.prompt_version`이 가른다.
        """
        if self.cwe is None and self.trigger_status != "no_signal":
            raise ValueError(
                "cwe는 trigger_status가 no_signal일 때에만 null일 수 있다 "
                f"(trigger_status={self.trigger_status})"
            )
        return self
