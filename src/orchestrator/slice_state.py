"""슬라이스의 정적 상태를 Auditor FACTS의 opt-in 키 `slice_state`로 낸다
(`thresholds.auditor_slice_state`).

추적: #438 선택지 (b). 선택지 (a)(`source_gate`)는 같은 신호를 `auditor_source` 이벤트의
사유 코드로만 남겼고 모델 입력은 안 바꿨다. 이쪽은 **슬라이서가 `slices.json`에 쓴 정적
사실을 모델에 보인다** - LLM이 만든 주장은 없다. `src/adapters`를 import하지 않는 순수
함수다(`SliceRef`만 읽는다).

## 내는 것

- `analysis_kind` - 슬라이서가 쓴 그대로. 값 집합은 닫혀 있지 않다: `parameter_paths` ·
  `backward_ddg` · `degraded_anchor_only`(`tools/slicer`)에 더해 대상 지정 모드
  (`tools/slicer_v2`)가 `forward_ddg`·`degraded_lexical`을 쓴다.
- `degraded` - 데이터 흐름 문맥 없는 대체 슬라이스(#307)인가.
- `truncated` · `truncation_reasons` - 슬라이서가 분석을 상한에서 잘랐나와 그 사유(Case B
  역추적이 적는 `backward_depth`·`backward_states`·`call_depth` · 대상 지정 모드는
  `forward_*`도. Case A는 사유 목록을 안 낸다).
- `boundary_reasons` - 의존 역추적의 갈래가 끝난 자리의 사유(중복 없이 정렬).
  `entry_parameter`는 CPG에 호출 간선이 없는 함수의 파라미터에 닿았다는 뜻이다 - 호출자가
  **없다**는 뜻이 아니다. 슬라이서가 쓰는 다른 사유(`external_or_unresolved_call` ·
  `dependency_root` 등)도 그대로 나간다.
- `sinks_capped` - Case A flows 질의가 전역 싱크 집합을 `FLOWS_SINK_CAP`에서 잘랐나(그 질의만
  적는다). `cross_method_flow_truncated` - 대상 지정 모드가 교차 메서드 간선을
  `CROSS_EDGE_LIMIT`에서 잘랐나(그 모드만 적는다). 둘 다 `truncated`와 별개의 상한이다 -
  `truncated: false`는 흐름 수 상한에 안 닿았다는 뜻일 뿐 슬라이스가 완전하다는 뜻이 아니다.

## 「모름」은 `false`가 아니다 (CLAUDE.md §0)

로더가 빈 값으로 채운 것(`truncated=False` · 빈 사유·경계 튜플)은 기본값이지 측정이 아니다 -
그대로 내면 다 본 깨끗한 슬라이스와 같은 문자열이 된다. 그래서 **`False`·`[]`는 슬라이서가 그
키를 실제로 적었을 때만 낸다.** 안 적었으면 `None`(JSON `null`)이다. 서 있는 신호(`True`·사유가
있는 목록)는 적었는지와 무관하게 지우지 않는다 - 모름은 「없다」는 단언만 막는다.

적었는지는 **키마다 로더가 센다**(`SliceRef.truncated_recorded` ·
`truncation_reasons_recorded` · `boundaries_recorded` · `sinks_capped_recorded` ·
`cross_method_flow_truncated_recorded`) - 종류 목록을 여기 박지 않는다. 실제로
Case A(`parameter_paths`)와 `degraded_anchor_only`는 사유·경계를 둘 다 안 적고
(`slicer_pipeline.py`) 상한 둘(`sinks_capped` · `cross_method_flow_truncated`)도 해당 슬라이서
종류만 적는다 - 그 자리는 `[]`(없다)가 아니라 `null`(안 적었다)이다.

`degraded`만 예외다: 슬라이서는 `degraded`를 **대체 슬라이스에만** 적고 정상 슬라이스에는 키가
없다. 그래서 키 부재가 「아니다」이려면 슬라이서가 분석 사실을 적는 판이어야 하고(`analysis_kind`가
비지 않음), 아니면 모름이다.

⚠ **빈 `truncation_reasons`·`boundary_reasons`(`[]`)는 「적었는데 사유가 없다」이지 「슬라이스가
완전하다」가 아니다.**
"""

from collections.abc import Mapping

from src.schemas.candidate import SliceRef

# `auditor_source` 이벤트 필드 접두어 - `input_context_*`와 같은 결이다.
_EVENT_PREFIX = "slice_state_"


def facts(ref: SliceRef) -> dict[str, object]:
    """`ref`가 든 정적 사실을 FACTS `slice_state` 값으로 낸다. 키는 늘 일곱이다(`None`은 모름)."""
    boundary_reasons = sorted({reason for reason, _file, _line in ref.boundaries})
    return {
        "analysis_kind": ref.analysis_kind or None,
        # `degraded` 키는 대체 슬라이스에만 적힌다 - 부재가 「아니다」이려면 분석 사실을 적는
        # 판이어야 한다.
        "degraded": _flag(ref.degraded, bool(ref.analysis_kind)),
        "truncated": _flag(ref.truncated, ref.truncated_recorded),
        "truncation_reasons": _reasons(
            list(ref.truncation_reasons), ref.truncation_reasons_recorded
        ),
        "boundary_reasons": _reasons(boundary_reasons, ref.boundaries_recorded),
        # 상한 둘 - `truncated`와 별개다. 그 키를 적는 슬라이서 종류만 `true`·`false`를 낸다.
        "sinks_capped": _flag(ref.sinks_capped, ref.sinks_capped_recorded),
        "cross_method_flow_truncated": _flag(
            ref.cross_method_flow_truncated, ref.cross_method_flow_truncated_recorded
        ),
    }


def event_fields(state: Mapping[str, object]) -> dict[str, object]:
    """`facts`가 낸 값을 `auditor_source` 이벤트 필드로 옮긴다 - `TraceValue`는 스칼라라 사유
    목록은 쉼표로 이은 문자열이다(사유에 쉼표가 없다). 모름은 `None`, 사유 없음은 빈 문자열이다."""
    return {
        _EVENT_PREFIX + key: ",".join(value) if isinstance(value, list) else value
        for key, value in state.items()
    }


def _flag(value: bool, known: bool) -> bool | None:
    """서 있으면 `True`. 꺼져 있으면 슬라이서가 적었을 때만 `False`, 아니면 모름."""
    if value:
        return True
    return False if known else None


def _reasons(values: list[str], known: bool) -> list[str] | None:
    """사유가 있으면 그 목록. 없으면 슬라이서가 그 키를 적었을 때만 `[]`, 아니면 모름."""
    if values:
        return values
    return [] if known else None
