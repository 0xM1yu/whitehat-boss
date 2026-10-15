"""Auditor `SOURCE`가 어디까지 믿을 입력인지를 사유 코드로 낸다 (`thresholds.auditor_source_gate`).

추적: #438. `runner._auditor_source`가 이미 아는 값만 받는 **순수 함수**다 — 파일도 인덱스도 안
읽고(`src/adapters`를 import하지 않는다) 모르는 것을 사유로 지어내지 않는다. **읽기 전용 관측**이다:
사유는 `auditor_source` 이벤트의 `gate_reasons`로만 나가고 Auditor 프롬프트·FACTS·`SOURCE`는 안
바꾼다. ⏵ 2026-10-14 — 같은 `SliceRef` 필드를 모델에 보이는 둘째 소비자가 `slice_state`다
(`auditor_slice_state` · FACTS `slice_state` · 이슈 #438 (b)) - 이쪽은 읽기 전용 관측이고 저쪽은
모델 입력이다. 「적었나」를 재는 `*_recorded`는 저쪽만 읽는다.

⚠ **빈 목록은 「아래 검사가 하나도 안 걸렸다」이지 「SOURCE가 완전하다」가 아니다.** 아래 「못 내는
것」은 검사하지 않고, 켜지 않은 단계(정의·피호출·호출자 창)는 그 검사가 안 돈다.

사유(이 순서로 나온다):

- `ANALYSIS_UNKNOWN` — 슬라이스에 분석 사실이 없다(`analysis_kind`가 비었다 — 옛 `slices.json`처럼
  슬라이서가 안 적었다). 이때 절단·싱크 cap·경계 사유는 **안 걸린 것이 아니라 모르는 것**이다 —
  이 사유 없이 비워 두면 다 본 깨끗한 슬라이스와 같은 문자열이 된다(없음과 모름을 가른다).
- `SLICE_DEGRADED` — anchor-only 대체 슬라이스(#307): flow/backward 질의가 실패·건너뛰어 dataflow
  문맥이 없다.
- `SLICE_TRUNCATED[:사유+사유]` — 슬라이서가 분석을 상한에서 잘랐다. 사유는 Case B 역추적이 적는
  `backward_depth`·`backward_states`·`call_depth`다. Case A는 사유 목록을 안 내서 낱말 없는
  `SLICE_TRUNCATED`다.
- `FLOW_SINKS_CAPPED` — Case A flows 질의가 전역 싱크 집합을 `FLOWS_SINK_CAP`에서 잘랐다
  (`analysis.sinks_capped`). 후보 메서드 자신의 싱크는 늘 남고 cap 밖 싱크로 가는 흐름이
  슬라이스에서 빠진다. `truncated`와 별개이고, 후보 범위를 안 보는 CPG 전역 집합이라 같은 CPG의
  Case A 슬라이스는 같은 값이다.
- `WINDOW_FALLBACK` — entry를 품는 함수를 못 찾아 앞뒤 창을 읽었다(`runner._auditor_target`). 창은
  `symbol`이 없는 위치다 — 인덱스가 없어도, 있어도 entry가 어느 정의에도 안 들면 그렇다.
- `SLICE_LINES_UNCOVERED` — 슬라이스 줄이 어느 인덱스 함수에도 안 들어 `SOURCE`가 못 읽었다(구조체
  필드·전역·프로토타입·매크로 — 이벤트의 `uncovered_lines`).
- `SOURCE_DROPPED` — 슬라이스가 걸친 함수가 `SOURCE` 총 상한에 걸려 통째로 빠졌다(이벤트의
  `dropped`).
- `CALLEE_DROPPED` — 피호출 함수 본문이 상한에 걸려 빠졌다(`auditor_callee_hops`가 켜졌을 때).
- `CALLER_DROPPED` — 호출 지점은 찾았는데 그 호출자 창이 상한에 걸려 빠졌다(`auditor_caller_lines`가
  켜졌을 때 — 이벤트의 `callers_dropped`). 호출자가 **있는데 입력에 없다**.
- `NO_CALLER_IN_CPG` — Case B 역추적의 한 갈래가 닿은 함수 파라미터에 CPG의 호출 간선(`callIn`)이
  없다(`entry_parameter` 경계 — `slicer_backward.py`). 호출자가 **없다**는 뜻이 아니라 CPG가
  호출자를 **못 이었다**는 뜻이다(동적 호출에 대한 완전한 추적은 보장하지 않는다 —
  `docs/slicer-README.md`).
  다른 갈래는 잘렸을 수 있다(`SLICE_TRUNCATED`가 같이 나온다). 「검증이 없다」는 뜻도 아니다 —
  슬라이서에는 제어 의존이 없다.
- `DEFINITION_DROPPED` — 정의(매크로·타입·파일 변수·enum 상수)가 상한에 걸려 빠졌다
  (`auditor_definitions`가 켜졌을 때).

**못 내는 것** — 이 지점에 신호가 없다. `DECL_ONLY`(entry가 선언뿐)는 인덱스가 선언을 안 줘서
`WINDOW_FALLBACK`과 안 갈린다. 피호출의 `NO_REPO_BODY`·`INDIRECT_UNRESOLVED`는 `callees_of`가 풀린
것만 돌려줘서 못 푼 호출이 안 보인다 — 새 조회가 있어야 한다. 호출 간선 부재의 깊이는 슬라이스
항목에 없고 run 전체 설정(`slices.json` 최상위 `settings.backward_depth`)에 있어 사유에 싣지 않았다.
계획의 `sink_arguments`(`candidate_manifest`의 싱크 호출 인자)는 로더가 아직 안 싣는다 — 미뤘다.
"""

from src.schemas.candidate import SliceRef
from src.schemas.common import Location

# Case B 역추적이 CPG에 호출 간선이 없는 함수의 파라미터에 적는 경계 사유
# (`tools/slicer/slicer_backward.py`). `src/`는 슬라이서를 import하지 않으므로 문자열을 다시 적는다.
_ENTRY_PARAMETER = "entry_parameter"


def reasons(
    ref: SliceRef,
    anchor: Location,
    *,
    uncovered_lines: int = 0,
    functions_dropped: int = 0,
    callees_dropped: int = 0,
    callers_dropped: int = 0,
    definitions_dropped: int = 0,
) -> list[str]:
    """`ref`(슬라이서의 분석 사실)와 `_auditor_source`가 본 것(`anchor` · 못 읽은 줄 수 · 상한이 뺀
    함수·피호출·호출자 창·정의 수)에서 사유를 낸다.

    `anchor`는 `_auditor_target`이 세운 캡슐 대상이다 — 감싸는 함수를 찾았으면 인덱스 심볼이라
    `symbol`이 있고, 못 찾아 앞뒤 창을 세웠으면 없다.
    """
    out: list[str] = []
    if not ref.analysis_kind:
        out.append("ANALYSIS_UNKNOWN")
    if ref.degraded:
        out.append("SLICE_DEGRADED")
    if ref.truncated:
        why = "+".join(ref.truncation_reasons)
        out.append(f"SLICE_TRUNCATED:{why}" if why else "SLICE_TRUNCATED")
    if ref.sinks_capped:
        out.append("FLOW_SINKS_CAPPED")
    if anchor.symbol is None:
        out.append("WINDOW_FALLBACK")
    if uncovered_lines:
        out.append("SLICE_LINES_UNCOVERED")
    if functions_dropped:
        out.append("SOURCE_DROPPED")
    if callees_dropped:
        out.append("CALLEE_DROPPED")
    if callers_dropped:
        out.append("CALLER_DROPPED")
    # 호출자로 올라가는 분석은 Case B(`sink` 끝)뿐이다 — 경계도 거기서만 적힌다.
    if ref.given_end == "sink" and any(kind == _ENTRY_PARAMETER for kind, _, _ in ref.boundaries):
        out.append("NO_CALLER_IN_CPG")
    if definitions_dropped:
        out.append("DEFINITION_DROPPED")
    return out
