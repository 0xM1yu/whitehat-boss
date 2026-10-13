"""Evidence 스키마 (agent-contracts.md §3, 파이프라인 2·8단계 산출물)."""

from datetime import datetime

from pydantic import Field, model_validator

from src.schemas.common import (
    Identifier,
    Location,
    NonEmptyStr,
    Sha256Digest,
    UtcModel,
)
from src.schemas.enums import EvidenceKind


class Evidence(UtcModel):
    """관찰된 사실 하나. Evidence Store에 append-only로 쌓인다.

    `ok`/`exit_code`/`error`는 문서 예시에는 없다. engineering-guide.md §8.3이
    "에러는 삼키지 않는다. 도구 실패도 `Evidence`(실패 사유 포함)로 기록한다"를 요구하고,
    §6이 trace에 "도구 실행 명령·종료 코드·소요 시간"을 남기라고 하므로 신설했다.
    도구가 실패해도 그 사실 자체가 신호다(architecture.md §3 2단계 "실패해도 진행").
    """

    evidence_id: Identifier
    run_id: Identifier
    kind: EvidenceKind
    # 신호를 만든 도구. "asan", "semgrep", "clang-tidy" 등.
    source: NonEmptyStr
    artifact_id: Identifier
    # 정적분석 신호가 파일 단위로만 오는 경우가 있어 위치는 선택 필드다.
    location: Location | None = None
    # 원문 로그 경로(artifacts/ 기준 상대). 로그 본문은 스키마에 싣지 않는다.
    payload_ref: NonEmptyStr
    digest: Sha256Digest
    observed_at: datetime
    # 도구 실행 결과. 정적 신호처럼 실행 개념이 없는 Evidence는 기본값(성공)을 쓴다.
    ok: bool = True
    exit_code: int | None = None
    error: str | None = None
    # ⏵ 2026-10-13(#232) — 런타임 도구(asan·gdb·poc)가 낸 로그에서 **크래시 흔적을 관측했는가**.
    # `True` = 로그에 sanitizer 배너나 신호 정지가 있었다 · `False` = 로그를 읽었고 없었다 ·
    # `None` = **안 쟀다**(런타임 도구가 아니거나, 대상이 안 떴거나, 로그가 없다 — CLAUDE.md §0).
    # ⚠ **기록뿐이다 — 게이트·정책·최종 지정 어느 것도 읽지 않는다.** E4의 `p4`는 여전히
    # `exit_code`만 본다. 이 필드를 `p4`가 읽게 하는 일은 실제 크래시 녹음이 쌓인 뒤로 미뤘다(#232).
    # ⚠ **`True`는 「배너가 찍혔다」이지 「죽었다」·「오라클이 크래시라 할 것」이 아니다.**
    # LeakSanitizer(종료 시점 보고)는 `exit_code=0`과 함께 왔다 — oss-fuzz 재생 5/5가 그 배너에
    # exit 0이다(이슈 #232의 재생 표). UBSan `runtime error:`도 프로그램을 계속 돌리므로 같은
    # 모양일 수 있는데 **그쪽은 아직 한 건도 관측되지 않았다**(그 5/5는 LSan뿐이다 — §0).
    # 오라클은 exit 0을 크래시 없음으로 읽는다. 그래서 `banner`를 같이
    # 싣는다: 읽는 쪽이 배너로 가른다. 반대로 **배너 없이 신호로 죽은 대상**(exit 139, 셸의
    # `Segmentation fault` 한 줄뿐)은 철자 목록(`container_tools._CRASH_BANNER`)의 셸 신호
    # 묶음이 맞춘다.
    crashed: bool | None = None
    # `crashed`가 `True`일 때 처음 맞은 배너 줄. 제어 문자는 공백으로 바꾸고 200자로 묶는다 —
    # 대상이 찍은 날것이 trace·DB로 가므로 무한정 싣지 않는다(`compile_failure_reason`과 같은 관례).
    banner: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _error_matches_ok(self) -> "Evidence":
        """실패에는 사유가, 성공에는 사유가 없어야 한다.

        사유 없는 실패 기록은 "에러를 삼킨" 것과 구분되지 않고, 사유가 붙은 성공 기록은
        지표 집계에서 실패로 오분류된다.
        """
        if self.ok and self.error is not None:
            raise ValueError("ok=True인 Evidence에 error를 넣을 수 없다")
        if not self.ok and not self.error:
            raise ValueError("ok=False인 Evidence는 error가 필요하다 (engineering-guide §8.3)")
        return self

    @model_validator(mode="after")
    def _crash_observation_needs_a_run(self) -> "Evidence":
        """돌지 않은 도구는 크래시를 관측하지 못한다 — 안 잰 값(`None`)이어야 한다 (CLAUDE.md §0).

        배너는 `crashed=True`의 증거로만 실린다. `crashed=False`에 배너가 있으면 모순이다.
        """
        if not self.ok and self.crashed is not None:
            raise ValueError("ok=False인 Evidence에 crashed를 넣을 수 없다 — 대상이 안 돌았다")
        if self.banner is not None and self.crashed is not True:
            raise ValueError("banner는 crashed=True일 때만 싣는다")
        return self
