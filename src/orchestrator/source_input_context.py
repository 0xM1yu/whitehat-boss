"""슬라이서가 고른 입력 디코더 함수의 본문을 Auditor `SOURCE` 맨 뒤에 싣는다
(`auditor_input_context`).

S20 Phase 13 · council 2026-10-14 + 사용자 최종 승인. 슬라이서(`tools/slicer_v2/input_context.py`)가
`slices[].analysis.input_context`에 **이름으로** 고른 함수(`functions[]`)를 하네스가 읽어 `SOURCE`에
붙인다. 소스를 읽는 것도, 무엇을 얼마나 붙일지의 상한도 하네스 쪽 기계(`runner._auditor_source`의
`cap`·`measure`·`seen`·`size`)가 이미 가진 것을 그대로 쓴다 - 새 수치 상한은 없다(ADR 0012 결정 5).

## 싣는 것 / 안 싣는 것

- 싣는다: `status == "selected"` ∧ `basis == "cpg"`인 선택의 `functions[]`(문법 검사 `grammar`와
  `decoder` 둘 다 - 종류는 안 가른다).
- 안 싣는다: `basis == "syntax"`(인덱스만으로 고른 약한 선택 - 호출 경로 증명이 없다) ·
  `dispatches` · `carried_decoders`. 셋은 이벤트에 수만 남는다.
- 함수 범위 **전체**가 이미 `SOURCE`에 **실린** 범위 안에 들면 건너뛴다(`_contained` - 러너의
  `shown`에 든 범위 하나, 또는 같은 파일 여러 범위의 합집합이 덮을 때). **일부만 겹치면**(호출자
  창·쓰기 지점 창 조각) 건너뛰지 않고 완전한 함수를 붙인다 - 창과 몇 줄이 겹쳐 중복되는 것은
  받아들이고 디코더를 잃는 것은 안 받아들인다.
- 슬라이서의 `identical_to_auditor_input`은 **믿지 않는다** - 그 값은 「선택이 슬라이스 자신의
  함수에 더하는 것이 없다」일 뿐 이 SOURCE에 이미 뭐가 실렸는지는 모른다(피호출·호출자 창·쓰기
  창은 슬라이서가 못 본다).

## 이벤트의 세 상태

`input_context` 키: `absent`(슬라이스에 키가 없다 - `--index`를 안 줬다. 수는 **안 적는다**, 0이
아니라 모름) · `none_selected`(훑었는데 더할 함수가 없었다 - `cap_hit`이 서 있으면 잘린 탐색일 수
있어 0이 아니라 모름이다) · `selected`. `cap_hit`·`cap_reasons`는 슬라이서가 쓴 그대로 든다.
건너뜀은 넷이다: `skipped_syntax`(`syntax` 근거) · `skipped_carried`(전체가 이미 든 범위) ·
`skipped_size`(상한) · `skipped_empty`(읽은 본문이 비었다 - 없는 파일은 빈 문자열이다).

⚠ **PoV·Critic도 이 본문을 받는다** - `runner._step_auditor`가 이 SOURCE 문자열을
`state.finding_sources`로 물려 주고 Critic과 8단계 PoV(`pov._WIDE_INPUT_VERSIONS` 판)가 그대로
프롬프트에 싣는다.

⚠ **한계**: `shown`은 실린 범위만 든다 - 상한에 걸려 못 실은 창·함수 안에 든 디코더는 건너뛰지 않고
직접 상한에 견준다(`skipped_size`). 줄여 실은 entry 함수·enum은 일부만 보이므로 `shown`에 안 든다
(디코더가 그 함수와 같으면 다시 붙는다).
"""

from collections.abc import Callable
from dataclasses import dataclass

from src.schemas.candidate import SliceRef
from src.schemas.common import Location


@dataclass(frozen=True)
class InputContextCarry:
    """`carry_input_context`의 결과 - 붙일 블록(순서대로)과 `auditor_source` 이벤트에 더할 필드."""

    blocks: tuple[str, ...]
    event: dict[str, int | str | bool]


def carry_input_context(
    ref: SliceRef,
    *,
    read_source: Callable[[Location], str],
    block: Callable[[Location, str], str],
    measure: Callable[[str], int],
    seen: list[Location],
    shown: list[Location],
    size: int,
    cap: int,
) -> InputContextCarry:
    """`ref.input_context`가 고른 함수 본문을 `cap` 안에서 붙인다. `seen`·`shown`에 범위를 더한다.

    「전체가 이미 실렸는가」는 `shown` - 실제로 `SOURCE`에 붙은 블록의 범위 - 로 본다(`_contained`).
    `seen`은 상한에 걸려 못 실은 범위도 들어서, 그것으로 보면 안 실린 것을 실렸다고 센다. `seen`에는
    러너의 다른 단계처럼 이 함수의 범위도 더한다. `size`는 지금까지 실린 `measure` 합이고(러너의
    `size`), 블록 하나는 `size + 1 + measure(block) > cap`이면 안 든다 - 러너의 다른 단계와 같은
    식이다. 상한에 걸려 뺀 함수는 `seen`에만 둔다.
    """
    context = ref.input_context
    if context is None:
        return InputContextCarry((), {"input_context": "absent"})
    blocks: list[str] = []
    listed = len(context.functions)
    skipped_syntax = skipped_carried = skipped_size = skipped_empty = 0
    if context.basis == "syntax":
        skipped_syntax = listed  # 호출 경로 증명이 없는 약한 선택 - 읽지도 않는다
    elif context.status == "selected":
        for function in context.functions:
            where = Location(
                file=function.file, symbol=function.symbol,
                line_start=function.line_start, line_end=function.line_end,
            )
            if _contained(where, shown):
                skipped_carried += 1
                continue
            seen.append(where)
            text = read_source(where)
            if not text:  # 없는 파일은 빈 문자열이다 - 배너만 싣지 않는다
                skipped_empty += 1
                continue
            piece = block(where, text)
            if size + 1 + measure(piece) > cap:
                skipped_size += 1
                continue
            blocks.append(piece)
            shown.append(where)
            size += 1 + measure(piece)
    event: dict[str, int | str | bool] = {
        "input_context": context.status,
        "input_context_basis": context.basis,
        "input_context_cap_hit": context.cap_hit,
        "input_context_cap_reasons": ",".join(context.cap_reasons),
        "input_context_listed": listed,
        "input_context_added": len(blocks),
        # 붙은 글자 수 - 블록마다 배너 줄과 앞에 붙는 개행 한 글자를 포함한다(= SOURCE가 늘어난
        # 길이).
        "input_context_chars": sum(1 + len(piece) for piece in blocks),
        "input_context_skipped_syntax": skipped_syntax,
        "input_context_skipped_carried": skipped_carried,
        "input_context_skipped_size": skipped_size,
        "input_context_skipped_empty": skipped_empty,
        # 이 판이 싣지 않는 것들 - 있었다는 사실만 남긴다.
        "input_context_carried_decoders": context.carried_decoders,
        "input_context_dispatches": context.dispatches,
    }
    return InputContextCarry(tuple(blocks), event)


def _contained(where: Location, shown: list[Location]) -> bool:
    """`where`의 줄 범위 **전체**가 `shown`의 한 범위 또는 같은 파일 범위들의 합집합 안에 드는가.
    범위 없는 위치(파일 전체)가 `shown`에 있으면 그 파일은 다 든 것이다. 일부만 겹치면 거짓이다."""
    start, end = where.line_start or 1, where.line_end or where.line_start or 1
    spans = []
    for other in shown:
        if other.file != where.file:
            continue
        if other.line_start is None:
            return True
        spans.append((other.line_start, other.line_end or other.line_start))
    reached = start  # 아직 덮이지 않은 첫 줄
    for first, last in sorted(spans):
        if first > reached:
            return False
        reached = max(reached, last + 1)
        if reached > end:
            return True
    return False
