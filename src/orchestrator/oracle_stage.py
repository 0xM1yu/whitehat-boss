"""PoV 결과들에서 **오라클에 낼 최종 PoC 한 개**를 결정론으로 지정한다 (#386).

## 왜 순수한가

채점은 왕복 한 번이다 — 과제 하나에 컨테이너 둘이 돌고, 같은
`(agent_id, task_id, sha256(poc))`를 다시 내면 서버가 옛 판정을 돌려준다(이슈 #385).
그래서 **내기 전에** 한 개를 정해야 하고, 오라클 피드백으로 고를 수 없다.

고르는 데 I/O·시각·난수가 안 섞이면 두 run이 같은 PoC를 낸다. 이 모듈에 넣지 않는 것:
파일 읽기·쓰기, 오라클 호출, `datetime.now`, `uuid`·`random`. 채점 쪽을 갈아 끼울 때
이 함수는 안 움직인다 — 그것이 분리의 값이다.

## 무엇이 후보인가 — `submitted` **하나뿐**이다

🔴 **`submitted`만 「회수 가능한 최종 PoC」다.** PoV 루프는 다른 사유로 서도 바이트를
들고 있을 수 있다 — `poc_log`는 submit 못 한 부분 PoC(`max_hop`·`repeat_spec`·흡수된
홉 실패)도 사이드카에 남긴다(회수가 목적이라 종단 사유로 안 가른다). 그래도 **최종 답이
아니다.** 그것을 오라클에 내면 「최종 PoC를 냈다」가 거짓이 된다.

`submitted`를 쪼갠 둘도 후보가 아니다.

- `submitted_without_poc` — submit했는데 바이트가 없다(gen 없는 hop0 submit).
- `submitted_unpersisted` — 바이트는 있었는데 사이드카 쓰기가 실패했다. 메모리에만
  있다 사라져 **회수 불가다.**

러너가 그 둘을 `submitted`에서 쪼갠 이유가 이것이다 — 안 쪼개면 「최종 PoC 있음」에
섞여 장부가 거짓이 된다(이슈 #357·#358). 그 어휘를 여기서 다시 판정하지 않고 **철자
그대로** 받는다.

## 순서 — 「`submitted` 우선」은 **자격**으로 실현된다

규칙은 「`submitted`로 끝난 것 우선 → 동률이면 `finding_id` 오름차순」이다. 앞 절대로
다른 사유가 **후보가 아니므로** 순위를 다툴 상대가 없다 — 그래서 정렬축이
`report.dedupe`의 `_rank`처럼 2단 튜플이 아니라 `finding_id` 한 축이다. 등급을 가르는
칸을 둘 자리가 아니라 **거를 자리**였다는 뜻이고, 그 선택은 `designate`의 검사가
「다른 사유가 더 작은 id를 들어도 `submitted`가 이긴다」로 고정한다.

⚠ 그래서 이 함수는 **PoC들 사이의 품질을 안 본다.** 어느 finding의 PoC가 더 그럴듯한지
가르는 신호(등급·Evidence·홉 수)를 쓰지 않는다. 그런 신호를 쓰려면 그것이 결정론인지를
먼저 보여야 하고, 지금 재 둔 것이 없다. `finding_id` 오름차순은 **품질 주장이 아니라
동률 해소**다.

## 판정 결과는 여기로 안 돌아온다

채점이 낸 값을 이 모듈이 받아 다시 고르는 구조는 없다 — 그러면 왕복이 루프가 된다.
오라클 쪽 어휘(`graded`·`oracle_incomplete`·`oracle_error`)도 여기 없다.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from src.ids import sha256_digest
from src.schemas.enums import PovStopReason

# 유일한 후보 자격. 철자를 리터럴로 적지 않고 enum에서 받는다 — 러너의 계수기 어휘가
# 그 enum 값을 그대로 쓰므로 한쪽만 바뀔 자리가 없다.
_ELIGIBLE_STOP_REASON = PovStopReason.SUBMITTED.value


@dataclass(frozen=True)
class PovResult:
    """한 finding의 PoV 루프가 남긴 것 — **선택이 보는 전부**다.

    `stop_reason`은 러너 계수기의 사유 문자열이다(`PovStopReason` 여섯 값 + 그 밖의
    넷). 형을 enum으로 좁히지 않는 이유: 후보 자격을 가르는 둘
    (`submitted_without_poc`·`submitted_unpersisted`)이 **enum 밖 철자**라 enum으로는
    표현이 안 된다.

    `poc`는 그 루프의 마지막 `gen`이 조립한 바이트다. `None`은 「조립한 PoC가 없다」다 —
    🔴 `b""`와 **다르다**(아래 `designate` 참조).

    사이드카 경로(`ref`)를 안 싣는다 — 회수 가능성은 이미 `stop_reason`이 든다
    (`submitted_unpersisted`가 그 갈래의 이름이다). 두 자리에 두면 어긋날 수 있다.
    """

    finding_id: str
    stop_reason: str
    poc: bytes | None


@dataclass(frozen=True)
class Designation:
    """오라클에 낼 PoC 하나와 그것을 고른 근거.

    `poc`·`digest`가 `None`일 수 없는 것이 이 형의 요점이다 — 지정이 **있다**는 것은
    바이트가 있다는 것과 같은 말이고, 없으면 `designate`가 `Designation`을 안 만든다.

    `candidates`는 **자격을 통과한 수**다(전체 결과 수가 아니다). 하나를 고른 run에서
    「몇 개 중에 골랐나」를 trace가 말할 수 있게 싣는다 — 1이면 선택의 여지가 없었고,
    여럿이면 나머지는 안 낸 것이다(그 run의 비용과 포기를 같이 읽는 자리다).
    """

    finding_id: str
    poc: bytes
    digest: str
    stop_reason: str
    candidates: int


def designate(results: Iterable[PovResult]) -> Designation | None:
    """후보 중 `finding_id`가 가장 작은 하나. 후보가 없으면 `None`.

    `None`은 「오라클에 낼 것이 없다」다 — 실패가 아니라 **그 run의 사실**이다. 호출부가
    그것을 오라클 왕복을 건너뛰는 사유로 쓴다.

    🔴 **`b""`는 PoC 「있음」이다.** `poc is None`으로 가르고 `not poc`로 가르지 않는다 —
    `repeat count=0`이면 조립이 `b""`를 내고, 이 저장소의 입장은 0바이트 PoC도 파일이고
    libFuzzer 입력으로 유효한 답이라는 것이다(`tests/test_poc_log.py`). 참값 검사는 러너
    쪽 같은 자리에서 **변이로 살아남은 적이 있다.**

    ⚠ `stop_reason`이 `submitted`인데 `poc`가 `None`인 조합은 러너가 안 낸다(그쪽
    `_pov_result`가 그때 사유를 `submitted_without_poc`로 바꾼다). 그래도 여기서 거르는
    이유는 입력이 **호출부가 조립해 넘기는 레코드**라 그 불변식이 타입으로 강제되지
    않는다는 것이다 — 걸러 두면 `Designation.poc`가 `bytes`인 것이 참이 된다.

    digest는 입력에서 받지 않고 **지정한 바이트에서 센다.** 같은 값이 PoC 사이드카
    (`poc_log.PocLog.write`)에도 있고 그쪽은 쓴 파일을 되읽어 조립 바이트와 대조하므로
    두 값은 일치한다. 그래도 받아 싣지 않는 이유는 받으면 **어긋날 자리가 생긴다**는
    것이다 — 세면 어긋날 수 없다. ⚠ 여기서 말하는 사이드카는 PoC 쪽이다. 판정 사이드카의
    digest는 이 값과 **다른 것**(그 JSON 파일)의 해시다.

    ⚠ 같은 `finding_id`가 두 번 들어오는 입력도 결정론이다 — 동률이면 바이트가 다음
    해소축이다. 한 run에서 그 일은 안 일어나지만(러너가 키가 `finding_id`인 dict를
    돈다) **입력 형이 막는 것이 아니다.**
    """
    candidates = [
        (result.finding_id, result.poc, result.stop_reason)
        for result in results
        if result.stop_reason == _ELIGIBLE_STOP_REASON and result.poc is not None
    ]
    if not candidates:
        return None
    # 🔴 `key=`를 **안 준다.** 튜플 전체로 비교하면 `finding_id`가 같은 둘에서도 순서가
    # 정해진다(그 다음 축이 바이트다). `key=lambda c: c[0]`로 쓴 첫 판은 같은 id 둘에서
    # `min`이 **순회 순서가 먼저인 것**을 돌려줘 입력 순서가 결과를 바꿨다(실측: 2가지).
    # 첫 축이 `finding_id`인 것은 그대로다 — 더해진 것은 동률의 해소뿐이다.
    best_id, best_poc, best_reason = min(candidates)
    return Designation(
        finding_id=best_id,
        poc=best_poc,
        digest=sha256_digest(best_poc),
        stop_reason=best_reason,
        candidates=len(candidates),
    )
