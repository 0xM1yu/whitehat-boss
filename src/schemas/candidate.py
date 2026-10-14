"""Candidate 스키마 (agent-contracts.md §3, 파이프라인 4단계 Scout 출력)."""

import re
from typing import Annotated, Literal

from pydantic import Field

from src.schemas.common import Cwe, HarnessModel, Identifier, Location, NonEmptyStr
from src.schemas.enums import FindingState

# Candidate가 가질 수 있는 상태. 상태 머신은 하나지만(enums.FindingState) 각 객체가
# 취할 수 있는 값은 다르다 — architecture.md §4 기준 Candidate는 여기까지다.
# `queued`는 4단계에서 임계값 미달로 탈락한 후보가 머무는 상태다(즉시 폐기하지 않는다).
CandidateState = Literal[
    FindingState.CANDIDATE,
    FindingState.TRIAGED,
    FindingState.QUEUED,
]


# Scout 풀 멤버 역할 키의 형태 (ADR 0010 §3 —
# docs/decisions/0010-harness-structure-and-model-roster.md).
# 풀 멤버는 `Role`에 더해지는 `scout_memory`·`scout_boundary`·`scout_injection`이고, 풀이
# 없는 프로필의 역할은 접두사 없는 `scout` 하나다 — **그 하나를 걸러 내는 것**이 이 패턴이
# 하는 일이다.
#
# **멤버 이름 셋을 여기 옮겨 적지 않는다.** 원천은 `Role`이고 여기가 아는 것은 그 이름
# 규칙뿐이다. 셋을 복제하면 열거형이 넷째 멤버를 얻는 날 이 파일만 뒤처지는데, 그때
# 증상은 "새 멤버에 태그가 안 붙는다"가 아니라 **stamp가 `None`을 남기는 것**이라
# 기록만 보고는 풀이 없던 run과 구별되지 않는다.
SCOUT_MEMBER_PATTERN = r"^scout_[a-z0-9]+(?:_[a-z0-9]+)*$"

# 풀 멤버 역할 **키**(`Role.value`)를 담는 문자열. 열거형 자체가 아닌 이유는
# `RunRecord.model_checksums`·`prompt_versions`·`config.model_key_for`·`counter_for_role`이
# 전부 역할 키 **문자열**을 받기 때문이다 — 행렬의 한 칸에서 그 run의 모델·checksum·프롬프트
# 버전으로 바로 이어진다. 여기서 말하는 것은 **키로 조회된다**는 것이지 그 역할이 이미
# 배선돼 있다는 뜻이 아니다: `counter_for_role`은 프로필이 선언한 역할이면 무엇이든 카운터를
# 준다. ⚠ **2026-09-11부터 부르는 쪽도 멤버 역할을 넘긴다**(PR #178 — `_step_retrieval`이
# `ctx.counter_for_role(item.role.value)`). **다만 켜지는 것은 그 target의 첫 멤버
# 하나다** — 캡슐이 공유돼 나머지 멤버는 그 자리에 도달하지 않는다. 남은 것은
# 기록 쪽이다 — `counter_report`는
# 여전히 `src.tokens.COUNTED_ROLES` 둘만 싣는다(그 상수의 ⚠ 문단).
#
# `Role`은 StrEnum이라 직렬화 결과도 같으므로, 나중에 열거형으로 좁혀도 매니페스트는 안 바뀐다.
ScoutMemberRole = Annotated[str, Field(pattern=SCOUT_MEMBER_PATTERN, max_length=64)]


def scout_member_of(role_value: str) -> str | None:
    """호출 역할 키가 Scout 풀 멤버면 그 키를, 아니면 `None`을 돌려준다.

    stamp 하는 쪽 둘(v1·v2 `Candidate`는 `orchestrator/context.py`의 `call_model`, v3
    `ScoutResponse`는 `orchestrator/nodes/scout.py`의 `_call_hop`)이 이것만 부른다. 규칙과 그
    규칙이 채우는 필드를 한 파일에 두는 것이 요점이다 — 둘이 갈라지면 패턴은 거부하는데
    stamp는 만들어 내는 값이 생기고, stamp가 쓰는 `model_copy(update=)`는 검증을 우회하므로
    그 값이 그대로 매니페스트에 남는다.
    """
    return role_value if re.fullmatch(SCOUT_MEMBER_PATTERN, role_value) else None


class Candidate(HarnessModel):
    """Scout/Triage가 만든 후보.

    `reason_codes`는 `enums.ReasonCode`(상태 전이 사유)와 **다른 축**이다. 문서 예시의
    `memcpy_unbounded`·`changed_in_diff`는 전이 사유가 아니라 트리아지 신호이고,
    agent-contracts.md §2.4가 열거형 고정을 요구하는 대상은 Evidence Gate의 전이 사유다.
    Scout 휴리스틱은 계속 늘어나므로 지금 열거형으로 닫지 않는다.
    """

    candidate_id: Identifier
    run_id: Identifier
    artifact_id: Identifier
    location: Location
    suspected_cwe: list[Cwe] = Field(default_factory=list)
    # Scout 점수. 승격 임계값(scout_threshold)은 config에서 온다(engineering-guide.md §8.5).
    score: float = Field(ge=0.0, le=1.0)
    reason_codes: list[NonEmptyStr] = Field(default_factory=list)
    evidence_refs: list[Identifier] = Field(default_factory=list)
    state: CandidateState
    # **어느 Scout 풀 멤버가 낸 후보인가 — 하네스가 stamp한다** (ADR 0010 §3).
    #
    # 모델이 채우는 값이 아니다(`produced_by`와 같은 논법). 모델은 자기가 풀의 어느 자리에서
    # 불렸는지 모르고, 이 필드는 그 자리를 증언하는 **증거**다 — 모델이 쓴 값이 살아남으면
    # 증언이 아니라 자기소개가 된다. 덮는 자리는 둘이다 — v1·v2(`Candidate`)는
    # `RunContext.call_model`, v3(`ScoutResponse`)는 `nodes/scout._call_hop`이다(래퍼에는 이
    # 필드가 없어 `call_model`이 건너뛴다). 발급 ID·`run_id`·`artifact_id`·초기 `state`는 같은
    # `_call_hop`에서 `RunContext.stamp_issued`가 덮는다.
    #
    # **`None`은 미실측 표시가 아니다**(CLAUDE.md §0). 풀이 없는 프로필 — `roles:`에 `scout`
    # 한 역할 — 로 돈 후보라는 관측이고, 풀 프로필의 후보에는 stamp가 반드시 값을 채우므로
    # 두 뜻이 한 값에 겹치지 않는다.
    #
    # 이 태그가 첫 run의 **공동오답 행렬**(누가 무엇을 함께 놓쳤나)을 만들고, 그 행렬이
    # 모델을 Scout 자리 사이에서 옮기는 유일한 근거다(ADR 0010 §2 "재배치의 유일한 근거" ·
    # docs/research/2026-09-08-roster.md §7).
    #
    # **`Finding`에는 넘기지 않는다.** ADR 0010 §3이 신설을 정한 것은 `Candidate`뿐이고,
    # 9단계 dedupe(`orchestrator/nodes/report.py`)가 어느 필드를 봐야 하는지는 그 문서가
    # 정하지 않았다 — 지금 넘기면 그 미결을 코드가 임의로 답한 것이 된다.
    scout_member: ScoutMemberRole | None = None
    # 이 후보가 나온 **슬라이스**(`slices.json`의 `slices[].id` — ADR 0012 결정 1의 #220 ① 풀이).
    # `location`은 슬라이스의 대표 위치(entry)라 불연속 줄 집합을 못 들고, entry가 같은 두
    # 슬라이스를 가르지도 못한다 — 그것을 이 필드가 든다. `scout_member`와 같은 논법으로
    # **하네스가 덮는다**(`nodes/scout.finish`). `None`은 `--slices` 없이 돈 run의 후보다.
    # ⏵ 2026-10-13 #529 — 「location은 entry」는 프롬프트가 `input: slice`일 때다. `input: source`
    # (서빙 v4)는 모델이 `FACTS.target`을 베끼면 entry를 품는 함수 범위다 — **미실측이다**:
    # 베끼는지 관측된 것은 `file`·`line_start`뿐이고 `symbol`은 2026-10-12 run(Scout v4)에서
    # 후보 12건 12/12 null이었다(이슈 #278 닫히는 조건 ⑥의 실측)
    # (그래서 #633이 도장을 넣었다). 슬라이스 run에서
    # 후보를 가르는 키는 그래서 `slice_id`다.
    slice_id: NonEmptyStr | None = None


class InputContextFunction(HarnessModel):
    """`analysis.input_context.functions[]` 한 항목 중 **읽는 네 키** — 위치만 든다.
    종류(`kind`)·점수·증명 (`cpg`)은 안 읽는다. 본문은 소비하는
    쪽(`orchestrator/source_input_context`)이 `read_source`로 읽는다."""

    file: NonEmptyStr
    symbol: str | None
    line_start: int
    line_end: int


class InputContext(HarnessModel):
    """슬라이서 `slices[].analysis.input_context`(`tools/slicer_v2/input_context.py`) 중 하네스가
    읽는 키.

    `SliceRef.input_context`가 `None`이면 **키가 없던 슬라이스**(`--index`를 안 줬다)이고, 이 객체의
    `status == "none_selected"`는 「인덱스로 훑었는데 더할 함수가 없었다」다 — 둘은 다른 사실이라
    합치지 않는다(CLAUDE.md §0). `cap_hit`이 서 있는 `none_selected`는 0이 아니라 **모름**이다 (잘린
    탐색일 수 있다 — 슬라이서 README). `carried_decoders`·`dispatches`는 이 판이 싣지 않아 수만
    든다(이벤트 보고용)."""

    version: int
    status: Literal["selected", "none_selected"]
    # 선택의 근거 - `cpg`는 호출 경로 증명이 있고 `syntax`는 인덱스만으로 고른 약한 선택이다.
    basis: Literal["cpg", "syntax"]
    cap_hit: bool
    cap_reasons: tuple[str, ...]
    functions: tuple[InputContextFunction, ...]
    carried_decoders: int
    dispatches: int


class SliceRef(HarnessModel):
    """러너가 슬라이스 하나를 타깃으로 세우는 데 필요한 것 — 어댑터가 `slices.json`에서 만든다.

    러너는 `src/adapters/`를 import하지 않으므로(계층 규칙) 슬라이서 형식이 아니라 이 스키마가
    경계를 넘는다 — `symbols_for`가 `Location`을 나르는 것과 같은 자리다.
    """

    slice_id: NonEmptyStr
    # entry의 file·line + 감싸는 함수 이름(후보의 `scopes`) — `input: slice`에서는
    # `Candidate.location`이 된다. ⏵ 2026-10-13 #529 — `input: source`(서빙 v4)에서는
    # 그 사이에 `entry_scope.enclosing_scope`가 끼어 FACTS `target`이 품는 함수 범위가 되므로
    # 이 등식이 깨진다(위 `slice_id` ⏵).
    location: Location
    # v3 `FACTS.slice_criterion.given_end` — Case A와 F(slicer_v2 정방향)는 `source`, B는 `sink`.
    given_end: Literal["source", "sink"]
    # 슬라이서가 준 entry **그대로**(file·line·symbol) — `FACTS.slice_criterion.given`이 된다.
    # `location`과 따로 드는 이유: Case B의 entry `symbol`은 감싸는 함수가 아니라 sink 호출
    # 이름이라(ADR 0012 결정 1의 ⚠) 한 필드로 합치면 둘 중 하나가 거짓이 된다.
    entry: Location
    # 슬라이스가 걸친 (file, line) 전부 — 슬라이서의 `lines` 그대로. Auditor가 슬라이스가 걸친
    # 함수들을 찾는 데 쓴다(`runner._auditor_source` — 결정 1-b = 넓은 (i)).
    lines: tuple[tuple[str, int], ...]
    # anchor-only 대체 슬라이스(#307)면 True — flow/backward 질의가 실패·건너뛴 후보라 dataflow
    # 문맥이 없다. FACTS와 RunRecord slicer summary가 이걸 실어야 모델·사후 기록이 절단 슬라이스를
    # full로 오인하지 않는다(@danielcho02 #307 리뷰). 기본 False = 정상 full 슬라이스.
    degraded: bool = False
    # 줄별 출처 — slice-v2 `line_provenance`(`data_dependency`·`candidate_anchor`·`context` → 각
    # (file, line) 목록). 슬라이서가 내는데 예전엔 하네스가 **안 읽고 버렸다**(#375 HIGH-2).
    # Scout 트림(`scout._fit_slice`)의 버리는 순서를 정한다 — anchor > dependency > context.
    # 기본 `{}`는 provenance 없는 입력(옛 슬라이스·degraded 빈 범주)이고, 그럴 땐 거리순만 남는다.
    provenance: dict[str, tuple[tuple[str, int], ...]] = Field(default_factory=dict)
    # 슬라이서의 분석 사실 — slice-v2 `analysis_kind`·`truncated`·`analysis.truncation_reasons`·
    # `analysis.sinks_capped`·`analysis.boundaries`. 슬라이서가 내는데 예전엔 하네스가 **안 읽고
    # 버렸다** — 그러면 하류가 「이 슬라이스는 잘렸다·역추적이 호출 간선 없는 파라미터에 닿았다」를
    # 못 안다(`orchestrator/source_gate.py`가 읽는다). 기본값은 옛 슬라이스(키가 없다)의 것이라
    # `truncated=False`만으로는 「절단 아님」인지 「안 적었다」인지 안 갈린다 — `analysis_kind`가 빈
    # 문자열이면 슬라이서가 분석 사실을 안 적은 슬라이스다.
    analysis_kind: str = ""
    truncated: bool = False
    # Case A(`parameter_paths`)는 사유 목록을 안 내므로 `truncated=True`여도 비어 있다.
    truncation_reasons: tuple[str, ...] = ()
    # Case A flows 질의가 전역 싱크 집합(메모리 쓰기·명령/SQL 실행 호출 인자)을 `FLOWS_SINK_CAP`
    # (`tools/slicer/slicer_queries.py`)에서 잘랐나 — 후보 메서드 자신의 싱크는 늘 남고 cap 밖
    # 싱크로 가는 흐름이 빠진다. `truncated`(경로 수 상한)와 별개이고, 후보 범위를 안 보는 CPG 전역
    # 집합이라 같은 CPG의 Case A 슬라이스는 같은 값이다.
    sinks_capped: bool = False
    # (reason, file, line) — Case B 역추적의 갈래가 끝난 자리. `entry_parameter`는 CPG에 호출
    # 간선(`callIn`)이 없는 함수의 파라미터이고 `dependency_root`·`external_or_unresolved_call`은
    # 의존의 끝·본문 없는 호출이다.
    # ⚠ `file`은 저장소 상대 경로지만 Joern이 보고한 그대로라 `lines`·`entry`와 달리 정리되지
    # 않는다 — 인클루드 디렉터리가 겹쳐 붙은 꼴이 나온다:
    # `libavdevice/libavformat/libavformat/avio.h`. `line`은 -1일 수 있다(Joern이 줄을 못 준 노드).
    boundaries: tuple[tuple[str, str, int], ...] = ()
    # 슬라이서의 `analysis.input_context` - 입력 디코더로 보이는 함수의 **이름**(소스는 안 싣는다).
    # `None`은 키가 없다(`--index` 없이 돈 슬라이스·옛 슬라이스)이고 `none_selected`와 다르다. 읽는
    # 곳은 `auditor_input_context`가 켜진 run의 `runner._auditor_source` 하나다.
    input_context: InputContext | None = None
