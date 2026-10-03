"""Auditor `SOURCE`가 어디까지 믿을 입력인지를 사유 코드로 낸다 (`thresholds.auditor_source_gate`).

`runner._auditor_source`가 이미 아는 값만 받는 **순수 함수**다 — 파일도 인덱스도 안 읽고
(`src/adapters`를 import하지 않는다) 모르는 것을 사유로 지어내지 않는다. **읽기 전용 관측**이다:
사유는 `auditor_source` 이벤트의 `gate_reasons`로만 나가고 Auditor 프롬프트·FACTS·`SOURCE`는 안
바꾼다.

⚠ **빈 목록은 「알려진 결함 없음」이지 「완전하다」가 아니다.** 켜지 않은 단계(정의·피호출 — 그
카운터는 0이다)와 이 지점에서 볼 수 없는 것은 말하지 못한다.

사유(이 순서로 나온다):

- `SLICE_DEGRADED` — anchor-only 대체 슬라이스(#307): flow/backward 질의가 실패·건너뛰어 dataflow
  문맥이 없다.
- `SLICE_TRUNCATED[:사유+사유]` — 슬라이서가 분석을 상한에서 잘랐다. 사유는 Case B 역추적이 적는
  `backward_depth`·`backward_states`·`call_depth`다. Case A는 사유 목록을 안 내서 낱말 없는
  `SLICE_TRUNCATED`다.
- `WINDOW_FALLBACK` — entry를 품는 함수를 못 찾아 앞뒤 창을 읽었다(`runner._auditor_target`). 창은
  `symbol`이 없는 위치다 — 인덱스가 없어도, 있어도 entry가 어느 정의에도 안 들면 그렇다.
- `CALLEE_DROPPED` — 피호출 함수 본문이 상한에 걸려 빠졌다(`auditor_callee_hops`가 켜졌을 때).
- `NONE_FOUND_TO_DEPTH` — Case B 역추적의 한 갈래가 호출자 없는 파라미터(`entry_parameter`)에서
  끝났다: CPG에서 그 위의 호출자를 못 찾았다. 다른 갈래는 잘렸을 수 있다(`SLICE_TRUNCATED`가 같이
  나온다). 「검증이 없다」는 뜻이 아니다 — 슬라이서에는 제어 의존이 없다.
- `DEFINITION_DROPPED` — 정의(매크로·타입·파일 변수)가 상한에 걸려 빠졌다(`auditor_definitions`가
  켜졌을 때).

**못 내는 것** — 이 지점에 신호가 없다. `DECL_ONLY`(entry가 선언뿐)는 인덱스가 선언을 안 줘서
`WINDOW_FALLBACK`과 안 갈린다. 피호출의 `NO_REPO_BODY`·`INDIRECT_UNRESOLVED`는 `callees_of`가 풀린
것만 돌려줘서 못 푼 호출이 안 보인다 — 새 조회가 있어야 한다. 호출자 부재의 깊이는 슬라이스 항목에
없고 run 전체 설정(`slices.json` 최상위 `settings.backward_depth`)에 있어 사유에 싣지 않았다.
"""

from src.schemas.candidate import SliceRef
from src.schemas.common import Location

# Case B 역추적이 호출자 없는 함수의 파라미터에 적는 경계 사유(`tools/slicer/slicer_backward.py`).
# `src/`는 슬라이서를 import하지 않으므로 문자열을 다시 적는다.
_ENTRY_PARAMETER = "entry_parameter"


def reasons(
    ref: SliceRef,
    anchor: Location,
    *,
    definitions_dropped: int = 0,
    callees_dropped: int = 0,
) -> list[str]:
    """`ref`(슬라이서의 분석 사실)와 `_auditor_source`가 본 것(`anchor`·상한이 뺀 수)에서 사유를
    낸다.

    `anchor`는 `_auditor_target`이 세운 캡슐 대상이다 — 감싸는 함수를 찾았으면 인덱스 심볼이라
    `symbol`이 있고, 못 찾아 앞뒤 창을 세웠으면 없다.
    """
    out: list[str] = []
    if ref.degraded:
        out.append("SLICE_DEGRADED")
    if ref.truncated:
        why = "+".join(ref.truncation_reasons)
        out.append(f"SLICE_TRUNCATED:{why}" if why else "SLICE_TRUNCATED")
    if anchor.symbol is None:
        out.append("WINDOW_FALLBACK")
    if callees_dropped:
        out.append("CALLEE_DROPPED")
    # 호출자로 올라가는 분석은 Case B(`sink` 끝)뿐이다 — 경계도 거기서만 적힌다.
    if ref.given_end == "sink" and any(kind == _ENTRY_PARAMETER for kind, _, _ in ref.boundaries):
        out.append("NONE_FOUND_TO_DEPTH")
    if definitions_dropped:
        out.append("DEFINITION_DROPPED")
    return out
