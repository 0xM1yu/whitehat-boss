"""`Oracle` 대역 — 채점기 배선 테스트들이 같이 쓴다 (#386).

서버를 흉내 내는 것이 아니라 **러너가 보는 경계**(`src/interfaces/oracle.py`)의 대역이다. 호출을
기록하고, 호출마다 **새 `poc_id`**를 낸다 — 실서버의 `poc_id`가 uuid4라 같은 입력에서도 다르다.
그래서 이 대역으로 돌린 두 run의 trace가 바이트 같다면 그것은 휘발 값이 trace에 안 들어갔다는 뜻이다
(`tests/test_pipeline_e2e.py`의 재현성 검사가 이 성질에 기댄다).

HTTP 계약을 흉내 내는 서버 대역은 `tests/test_cybergym_oracle.py`의 `_FakeOracle`이다 — 이름이
비슷하지만 층이 다르다.
"""

import hashlib
import json
from uuid import uuid4

from src.adapters.cybergym_oracle import checksum
from src.interfaces.oracle import OracleOutcome, VulSubmission

# 지원하는 모양의 과제 id(마스킹을 안 한 서버의 실제 id) — 왕복 보고서가 쓴 값이다.
TASK_ID = "arvo:1065"


def submit_script(
    *,
    server: str = "http://127.0.0.1:8666",
    task_id: str = TASK_ID,
    agent_id: str = "dcd41fef",
    **overrides: object,
) -> str:
    """과제 번들의 `submit.sh` — 저자 `submit.template`의
    모양(`docs/research/2026-09-29-cybergym-pov-design.md` 1.4절). 체크섬은 기본 salt로 계산한
    값이고 `overrides`가 metadata 필드를 덮는다."""
    fields = {
        "task_id": task_id,
        "agent_id": agent_id,
        "checksum": checksum(task_id, agent_id),
        "require_flag": False,
        **overrides,
    }
    return (
        "#!/bin/bash\n"
        f"curl -X POST {server}/submit-vul \\\n"
        f"  -F 'metadata={json.dumps(fields)}' \\\n"
        '  -F "file=@${POC_FILE}"\n'
    )


class FakeOracle:
    """호출을 기록하는 `Oracle`. 실패는 호출 번호(0부터)로 심는다."""

    def __init__(
        self,
        *,
        vul_errors: dict[int, Exception] | None = None,
        final_error: Exception | None = None,
        vul_exit_code: int | None = 77,
        fix_exit_code: int | None = 0,
        success: bool = True,
        vul_cached: bool | None = False,
        fix_cached: bool | None = False,
    ) -> None:
        self.vul_calls: list[bytes] = []
        # 성공한 `submit_vul` 호출이 돌려준 것 - 호출 순서대로(실패한 호출은 없다).
        self.vul_results: list[VulSubmission] = []
        self.final_calls: list[tuple[bytes, VulSubmission]] = []
        self.query_calls: list[bytes] = []
        self._vul_errors = vul_errors or {}
        self._final_error = final_error
        self._vul_exit_code = vul_exit_code
        self._fix_exit_code = fix_exit_code
        self._success = success
        self._vul_cached = vul_cached
        self._fix_cached = fix_cached

    def submit_vul(self, poc: bytes) -> VulSubmission:
        index = len(self.vul_calls)
        self.vul_calls.append(poc)
        if index in self._vul_errors:
            raise self._vul_errors[index]
        result = VulSubmission(
            task_id=TASK_ID,
            poc_id=uuid4().hex,
            response_exit_code=0 if self._vul_exit_code is None else self._vul_exit_code,
            cached=self._vul_cached,
        )
        self.vul_results.append(result)
        return result

    def query_vul(self, poc: bytes) -> int | None:
        self.query_calls.append(poc)
        return self._vul_exit_code

    def finalize(self, poc: bytes, vul: VulSubmission) -> OracleOutcome:
        self.final_calls.append((poc, vul))
        if self._final_error is not None:
            raise self._final_error
        return OracleOutcome(
            task_id=TASK_ID,
            poc_id=vul.poc_id,
            poc_hash=hashlib.sha256(poc).hexdigest(),
            poc_length=len(poc),
            vul_exit_code=self._vul_exit_code,
            fix_exit_code=self._fix_exit_code,
            success=self._success,
            fix_cached=self._fix_cached,
        )

