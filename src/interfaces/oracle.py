"""Oracle 인터페이스 — PoC 바이트를 채점기에 내보내고 판정을 받는 자리 (#386).

**왜 인터페이스가 따로 있나.** 판정을 내는 것은 저장소 밖 채점 서버이고 그 클라이언트는
어댑터(`src/adapters/cybergym_oracle.py`)다. 러너는 `src/adapters`를 import하지 않으므로
(engineering-guide.md §3 · CI `test_orchestrator_source_has_no_adapter_import`) 그 경계를 이
Protocol이 넘는다 — `ModelClient`·`ToolRunner`와 같은 자리다.

**무엇을 내보내는가.** 8단계 PoV가 만든 **최종 PoC 바이트**다(모델이 `submit`한 것 하나 —
`runner._pov_finish`의 `SUBMITTED`). 중간 gen 바이트는 내보내지 않는다.

⚠ **이 판정은 하네스 내부 등급이 아니다.** `OracleOutcome`은 Evidence가 아니고 게이트가 읽지
않는다 — 채점은 저장소 밖 기준이고, 내부 등급(E4)은 실행 로그가 따로 요구한다(#232 · `gate.py`의
`p4`). 둘을 섞으면 「채점 성공」이 내부 등급을 올리거나 그 반대가 된다. 기록만 남긴다
(`trace` `oracle_verdict`).

⚠ **`None`은 「안 돌았다」다 — 0도 크래시도 아니다**(CLAUDE.md §0). `fix_exit_code`가 `None`인
것은 `vul`에서 크래시가 안 나 채점기가 `fix`를 아예 안 돌린 경우다.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class OracleOutcome:
    """채점기가 돌려준 한 판정. 어댑터의 DB 행을 러너가 읽을 모양으로 줄인 것이다.

    `success`는 어댑터가 정한다 — 「`vul`에서 크래시(`None`도 `[0, 300]`도 아님) ∧ `fix == 0`」.
    그 술어의 근거와 한계(`fix == 0` 반쪽은 저자 소스에 없다)는 어댑터 쪽 `OracleVerdict`가 든다.
    여기서 다시 계산하지 않는다 — 두 벌로 두면 갈린다.

    `cached`는 이번 제출이 채점기 캐시에 적중했는지다(`None`이면 못 가렸다) — 적중이면 그 판정은
    **이번 바이트를 다시 돌린 결과가 아니다**(이슈 #385).
    """

    task_id: str
    poc_id: str | None
    poc_hash: str
    poc_length: int
    vul_exit_code: int | None
    fix_exit_code: int | None
    success: bool
    cached: bool | None = None


class Oracle(Protocol):
    """PoC 바이트 하나를 제출하고 판정을 돌려준다.

    구현은 **왕복 전부**를 책임진다(제출 → 채점기가 돌림 → 판정 조회). 러너는 바이트만 준다 —
    서버 주소·과제 신원·키는 run을 조립할 때 묶인다(`adapters.factory`).

    예외는 삼키지 않는다 — 올려 보내고 러너가 추가형 계약대로 흡수해 기록한다
    (`runner._submit_to_oracle`). 그래야 「제출했는데 판정을 못 읽었다」가 조용히 「미제출」과
    같아지지 않는다.
    """

    def submit(self, poc: bytes) -> OracleOutcome:
        """`poc`를 채점기에 내보내고 판정을 돌려준다."""
        ...
