"""채점기(oracle) 왕복의 run 산출물 — 장부와 사이드카 `{run_dir}/oracle/verdict.json` (#386).

`runner`가 후보마다 `submit_vul`을, run당 한 번 `finalize`를 부르고(`src/interfaces/oracle.py`)
그 결과를 이 장부에 쌓는다. 장부는 사이드카 파일·run 매니페스트의 `oracle` 요약·run 경고의 원천이다.

## 왜 값이 trace가 아니라 사이드카에 있나

trace는 재현성 기준이 있다 — 두 실행에서 `ts`·`wall_ms`만 빼면 바이트가 같아야 한다(`trace.py`의
「재현성 기준」). PR #408의 첫 판은 채점 서버가 돌려준 값을 trace `data`에 실었고, 같은 번들로 두
번 돌린 trace에서 95줄 중 1줄이 갈렸다(`poc_id` — 서버가 만드는 uuid4. 같은 서버에 다시 돌렸더니
`cached`가 또 한 줄을 갈랐다). 채점기를 안 켠 run은 0줄이 갈렸다. 셋 다 그 PR의 적대적 리뷰가 실제
CLI run으로 잰 것이다. 그래서 필드를 하나씩 갈랐다:

- **trace — 하네스가 정하는 값(결정론).** `task_id`·`poc_sha256`·`poc_bytes`·`finding_id`·
  `designation`(지정 근거 — 지정 못 한 run은 `null`)·`status`·`reason`. 예외 클래스 이름
  (`error`)도 여기다 — 하네스가 무엇을 못 했는지(제어 흐름)이다.
- **사이드카 — 휘발하는 값.** `poc_id`(서버가 만드는 uuid4)·`cached`·`fix_cached`(서버 장부의 상태에
  달렸다).
- **사이드카 — 장비가 정하는 값.** `vul_exit_code`·`fix_exit_code`·`success`·
  `response_exit_code`. 같은 입력에서 `fix`가 0 5회·139 5회였다(`vm.mmap_rnd_bits` 32,
  docs/experiments/README.md) — 하네스가 정하는 값이 아니라 장비가 재는 값이다.
- **사이드카 — 문구.** `error_detail`은 URL과 OS 로케일의 errno 문구를 싣는다.
- **`wall_ms`.** 시계라서 trace 최상위 필드(재현성 비교에서 빠진다)와 사이드카에 싣는다.

trace는 사이드카를 **고정된 경로**(`record_ref` = `oracle/verdict.json`)로 가리킨다 — 내용의
digest가 아니다 (휘발 값의 digest는 휘발이다).

## 사이드카의 모양

`candidates`(후보마다 vul 제출 한 건 — 성공이면 `poc_id`·`response_exit_code`·`cached`, 실패면
`error`·`error_detail`) · `final`. `final`은 run이 8단계 끝까지 가지 못했으면 `null`이고 아니면
`status`(`scored` | `failed` | `none`)·`designation`(지정 근거 — 지정 못 했으면 `null`)과 그에
따른 값이다. 후보가 둘 이상이어서 지정이 등급을 읽었으면 `candidate_grades`(`finding_id` → 지정이
읽은 내부 근거 등급)도 든다 — 키가 없으면 등급을 읽지 않았다(후보 0·1건).

⚠ **`null`은 「안 돌았다」·「못 가렸다」다 — 0도 거짓도 아니다**(CLAUDE.md §0). `fix_exit_code`가
`null`인 것은 `vul`에서 크래시가 안 나 채점기가 `fix`를 안 돌린 경우다. `final.status`가 `none`인 것
(지정된 후보가 없었다)과 `failed`인 것(지정했는데 판정을 못 읽었다)은 다른 사건이다 —
「최종 PoC가 없다」가 「최종 PoC가 실패했다」로 읽히면 안 된다. `none`의 `reason`은 넷이다:
`no_poc`·`all_vul_submissions_failed`(지정할 후보가 없다) · `grade_tie`(후보가 **둘 이상**인데 내부
근거 등급의 최댓값이 **동점이라** 안 가려졌다) · `grade_unknown`(후보 중에 등급을 모르는 것이 있다).
뒤 둘은 **규칙이 없어서가 아니라 규칙이 거부한 것이다**(`runner._designate_final`). 어느 쪽이든
`fix` 다리와 판정은 안 돈다.

⚠ **이 파일은 하네스 내부 등급이 아니다.** Evidence가 아니고 게이트가 읽지 않는다(#232).

## 쓰기

후보 제출·최종 판정마다 **파일 전체를 다시 쓴다**(작다) — 8단계가 중간에 끊겨도(`BudgetExhausted`
등) 그때까지 나간 vul 제출은 남는다. 원자적으로 쓴다(임시 파일 + `os.replace` — `poc_log`와 같은
관용구, 같은 전제: run_dir은 신뢰 경계 안이다). 쓰기 실패(`OSError`)는 장부가 흡수하고
`write_error`와 run 경고로 남긴다 — 채점기는 추가형이라 사이드카가 run을 죽이면 안 된다.
"""

import contextlib
import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from src.interfaces.oracle import OracleOutcome, VulSubmission

# run_dir 아래 디렉터리·파일 이름. **`pov_poc/`·`prompts/`·도구 슬롯과 겹치지 않는다.**
DIR_NAME = "oracle"
FILE_NAME = "verdict.json"
# trace가 가리키는 고정 경로(run_dir 상대). `run_id`를 넣지 않는다 — 넣으면 비교 단위가 run마다
# 달라진다.
REF = f"{DIR_NAME}/{FILE_NAME}"

# 오류 문구를 사이드카에 싣는 길이 상한. 서버 본문이 길어도 항목 하나가 파일을 삼키지 않게 자른다.
# 잰 값이 아니라 정한 값이다.
ERROR_DETAIL_CHARS = 500

# `final.status` 값. `NOT_REACHED`는 매니페스트 요약에만 있다(`final`이 `null`인 run).
STATUS_SCORED = "scored"
STATUS_FAILED = "failed"
STATUS_NONE = "none"
STATUS_NOT_REACHED = "not_reached"

# `status == none`의 사유. 앞 둘은 지정할 후보가 **없어서**고, 뒤 둘은 후보가 **둘 이상**인데
# **규칙이 돌았는데 하나를 못 가려서**다(`runner._designate_final`). 「규칙이 없다」가 아니다 —
# 규칙은 2026-10-03에 정해졌고(내부 근거 등급 최댓값 · 동점이면 거부) 그 규칙이 거부한 것이다.
REASON_NO_POC = "no_poc"
REASON_ALL_VUL_FAILED = "all_vul_submissions_failed"
REASON_GRADE_TIE = "grade_tie"
REASON_GRADE_UNKNOWN = "grade_unknown"

# `designation` 값 — 어떤 근거로 최종 PoC를 지정했는지. **위치(처음·마지막)로도 채점기 점수로도
# 고르지 않는다** - 이 저장소가 2026-10-03에 정한 규칙이고 저자의 금지 문장이 아니다(조사
# 문서 1.3절은 최종 하나를 에이전트가 지정한다는 채점 방식만 적는다). 둘뿐이다: 후보가
# 하나뿐이라 고를 것이 없었거나(`sole_candidate`), 내부 근거 등급의 최댓값을 든 후보가
# **하나뿐이라** 그것을 골랐다(`top_grade`).
DESIGNATION_SOLE_CANDIDATE = "sole_candidate"
DESIGNATION_TOP_GRADE = "top_grade"


def clip_error(exc: BaseException) -> str:
    """예외 문구를 상한까지 자른다 — `error_detail`은 사이드카에만 간다(이유는 모듈 docstring)."""
    return str(exc)[:ERROR_DETAIL_CHARS]


@dataclass(frozen=True)
class VulAttempt:
    """후보 하나의 `vul` 제출 시도. `submission`이 없으면 실패다(`error`가 예외 클래스 이름)."""

    finding_id: str
    poc_sha256: str
    poc_bytes: int
    wall_ms: int
    # 바이트는 성공한 시도만 든다 — 최종 지정이 이것을 다시 내보내야 해서다. 실패한 시도는 지정
    # 대상이 아니라 해시와 길이만 남긴다.
    poc: bytes | None = field(default=None, repr=False)
    submission: VulSubmission | None = None
    error: str | None = None
    error_detail: str = ""

    @property
    def ok(self) -> bool:
        return self.submission is not None


def vul_ok(finding_id: str, poc: bytes, submission: VulSubmission, wall_ms: int) -> VulAttempt:
    return VulAttempt(
        finding_id=finding_id,
        poc_sha256=hashlib.sha256(poc).hexdigest(),
        poc_bytes=len(poc),
        wall_ms=wall_ms,
        poc=poc,
        submission=submission,
    )


def vul_failed(finding_id: str, poc: bytes, exc: Exception, wall_ms: int) -> VulAttempt:
    return VulAttempt(
        finding_id=finding_id,
        poc_sha256=hashlib.sha256(poc).hexdigest(),
        poc_bytes=len(poc),
        wall_ms=wall_ms,
        error=type(exc).__name__,
        error_detail=clip_error(exc),
    )


@dataclass(frozen=True)
class FinalAttempt:
    """run당 한 번의 최종 단계 결과 — `status`가 셋 중 무엇인지가 첫째 사실이다.

    - `scored`: 지정한 PoC에 `finalize`가 판정을 돌려줬다(`outcome`).
    - `failed`: 지정했는데 `finalize`가 예외로 끝났다(`error`).
    - `none`: 지정된 후보가 없었다(`reason`) — 실패가 아니다.

    `designation`은 최종 PoC를 어떤 근거로 지정했는지다 — 지정한 run만 값이 있다(`None`이면
    지정하지 않았다).
    """

    status: str
    wall_ms: int | None = None
    designation: str | None = None
    reason: str | None = None
    attempt: VulAttempt | None = None
    outcome: OracleOutcome | None = None
    error: str | None = None
    error_detail: str = ""


class OracleLog:
    """`{run_dir}/oracle/verdict.json`을 쓴다 — `PocLog`와 같은 모양의 주입 객체다."""

    def __init__(self, run_dir: Path) -> None:
        self._dir = Path(run_dir) / DIR_NAME

    def write(self, record: Mapping[str, object]) -> str:
        """파일 전체를 원자적으로 쓰고 run_dir 상대 경로(`REF`)를 돌려준다. 실패하면 `OSError`다."""
        self._dir.mkdir(parents=True, exist_ok=True)
        final = self._dir / FILE_NAME
        tmp = final.with_name(FILE_NAME + ".tmp")
        # 바이트로 쓴다 — 텍스트 모드는 Windows에서 개행을 번역한다(`poc_log` 「쓰기」와 같은 이유).
        payload = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
        try:
            tmp.write_bytes(payload.encode("utf-8", errors="replace"))
            os.replace(tmp, final)
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            raise
        return REF


class OracleLedger:
    """한 run의 채점기 장부 — 후보별 vul 시도와 최종 단계. `runner._Accumulator`가 든다."""

    def __init__(self, *, wired: bool = False, log: OracleLog | None = None) -> None:
        # 채점기가 배선된 run인가 — 안 배선된 run은 요약·경고·파일이 **없다**(옛 동작 그대로).
        self.wired = wired
        self._log = log
        self.attempts: list[VulAttempt] = []
        self.final: FinalAttempt | None = None
        # **지정이 읽은** 후보별 내부 근거 등급(`finding_id` -> `E0`~`E4`) - 재계산한 값이 아니라
        # `runner._designate_final`에 넘어간 그 사전이다. 지정은 후보가 둘 이상일 때만 등급을
        # 읽으므로 그때만 값이 있다(`None`은 **안 읽었다**). 이 기록이 없으면 지정 등급과 8단계
        # `gate_eval`이 어긋나도(#460 리뷰) 다시 계산해야만 보였다.
        self.grades: Mapping[str, str] | None = None
        self.write_error: str | None = None
        self._written = False

    # --- 쌓기 ---

    def add_attempt(self, attempt: VulAttempt) -> None:
        self.attempts.append(attempt)
        self._flush()

    def set_final(self, final: FinalAttempt, grades: Mapping[str, str] | None = None) -> None:
        self.final = final
        self.grades = grades
        self._flush()

    # --- 읽기 ---

    @property
    def candidates(self) -> list[VulAttempt]:
        """`vul` 제출이 **성공한** 시도 - 최종 PoC의 후보다(`runner._designate_final`)."""
        return [attempt for attempt in self.attempts if attempt.ok]

    @property
    def submitted(self) -> int:
        return len(self.candidates)

    @property
    def failed(self) -> int:
        return sum(1 for attempt in self.attempts if not attempt.ok)

    @property
    def record_ref(self) -> str | None:
        """사이드카의 run_dir 상대 경로. 아직 안 썼거나 마지막 쓰기가 실패했으면 `None`."""
        return REF if self._written and self.write_error is None else None

    def none_reason(self) -> str:
        """지정할 후보가 없을 때의 사유 — 시도 자체가 없었나, 시도가 전부 실패했나."""
        return REASON_ALL_VUL_FAILED if self.failed else REASON_NO_POC

    def summary(self) -> dict[str, str | int | None] | None:
        """`RunRecord.oracle`이 될 요약. 채점기가 안 배선된 run은 `None`이다."""
        if not self.wired:
            return None
        return {
            "vul_submitted": self.submitted,
            "vul_failed": self.failed,
            "final": STATUS_NOT_REACHED if self.final is None else self.final.status,
            # `none`의 사유 — 후보가 여럿인데 등급이 못 가른 run(`grade_tie`·`grade_unknown`)과
            # 후보가 없던 run을 매니페스트에서도 가른다.
            "final_reason": None if self.final is None else self.final.reason,
            "record": self.record_ref,
        }

    def record(self) -> dict[str, object]:
        """사이드카의 내용. 값의 뜻은 모듈 docstring이다."""
        return {
            "candidates": [_attempt_record(attempt) for attempt in self.attempts],
            "final": None
            if self.final is None
            else _final_record(self.final, self.attempts, self.grades),
        }

    def warnings(self) -> list[str]:
        """run 경고 — 실패와 「점수가 없다」를 `trace.jsonl` 밖에서도 보이게 한다.

        콘솔에 찍히는 문장이라 cp949로 인코딩할 수 있는 글자만 쓴다(문장부호는 ASCII).
        """
        if not self.wired:
            return []
        out: list[str] = []
        sidecar = self.record_ref
        where = f"{sidecar}와 trace의 oracle_failed" if sidecar else "trace의 oracle_failed"
        if self.failed:
            out.append(f"채점기 vul 제출 {self.failed}건이 예외로 실패했다 - 사유는 {where}에 있다")
        final = self.final
        if final is None:
            out.append(
                "채점기가 배선됐지만 8단계가 끝까지 돌지 않아 최종 PoC를 지정하지 못했다"
                f"(vul 제출 {self.submitted}건) - 이 run은 채점 점수가 없다"
            )
        elif final.status == STATUS_FAILED:
            reason = f"사유는 {sidecar}" if sidecar else "사유는 trace의 oracle_final"
            out.append(
                f"채점기 최종 판정이 예외({final.error})로 실패했다 - 이 run은 채점 점수가 없다"
                f"({reason})"
            )
        elif final.status == STATUS_NONE:
            out.append(_none_warning(final, self.submitted))
        else:
            legs = _cached_legs(final, self.attempts)
            if legs:
                joined = " 와 ".join(legs)
                out.append(
                    f"채점기 판정의 {joined} 다리는 이전 판정의 캐시다(이슈 #385) - 이번 환경에서 "
                    "이 바이트를 다시 돌린 값이 아니다. 같은 과제 번들로 run을 반복하면 서버 "
                    "캐시를 공유한다"
                )
        if self.write_error is not None:
            out.append(f"채점기 사이드카({REF})를 쓰지 못했다({self.write_error})")
        return out

    # --- 내부 ---

    def _flush(self) -> None:
        if self._log is None:
            return
        try:
            self._log.write(self.record())
        except OSError as exc:
            self.write_error = type(exc).__name__
        else:
            self._written = True
            self.write_error = None


def _none_warning(final: FinalAttempt, submitted: int) -> str:
    """`status == none`의 경고 한 문장 - 사유마다 다른 말이다(같은 문장이면 사유가 안 가려진다).

    콘솔에 찍히므로 cp949로 인코딩할 수 있는 글자만 쓴다.
    """
    tail = "이 run은 채점 점수가 없다"
    if final.reason == REASON_GRADE_TIE:
        return (
            f"vul 제출이 성공한 후보가 {submitted}건인데 내부 근거 등급의 최댓값이 동점이라 최종 "
            "PoC를 지정하지 않았다 - 등급으로 하나를 못 가렸고 위치(처음·마지막)나 채점기 점수로 "
            "고르지 않는 것이 이 저장소의 규칙이다(2026-10-03). fix 다리와 판정 조회를 돌리지 "
            f"않았다 - {tail}"
        )
    if final.reason == REASON_GRADE_UNKNOWN:
        return (
            f"vul 제출이 성공한 후보가 {submitted}건인데 그중 내부 근거 등급을 모르는 것이 있어 "
            "최종 PoC를 지정하지 않았다 - 등급 없이 고르면 없는 근거로 고르는 것이다. fix 다리와 "
            f"판정 조회를 돌리지 않았다 - {tail}"
        )
    cause = (
        "vul 제출이 전부 실패해"
        if final.reason == REASON_ALL_VUL_FAILED
        else "PoV가 submit한 PoC가 없어"
    )
    return f"채점기에 낼 최종 PoC를 지정하지 못했다({cause}) - {tail}"


def vul_preexisted(attempts: Sequence[VulAttempt], attempt: VulAttempt) -> bool | None:
    """이 후보의 `vul` 코드가 **이 run 전에** 서버에 있었나. 못 가렸으면 `None`.

    후보 자신의 `cached`가 아니라 **같은 바이트를 이 run이 처음 낸 제출**의 `cached`다 — 같은
    바이트를 이 run이 앞서 냈으면(finding 둘이 같은 PoC를 만든다. `dev_fake`는 늘 그렇다) 그 뒤의
    적중은 이 run이 만든 행이 돌려준 것이라 옛 판정이 아니다. 앞의 제출이 캐시였으면 그 행은 이 run
    전의 것이다.
    """
    first = next((a for a in attempts if a.ok and a.poc_sha256 == attempt.poc_sha256), attempt)
    return None if first.submission is None else first.submission.cached


def _cached_legs(final: FinalAttempt, attempts: Sequence[VulAttempt]) -> list[str]:
    """옛 판정이 돌아온 다리 이름. `vul`은 `vul_preexisted`가, `fix`는 최종 단계가 가른다."""
    legs = []
    if final.attempt is not None and vul_preexisted(attempts, final.attempt):
        legs.append("vul")
    if final.outcome is not None and final.outcome.fix_cached:
        legs.append("fix")
    return legs


def _attempt_record(attempt: VulAttempt) -> dict[str, object]:
    row: dict[str, object] = {
        "finding_id": attempt.finding_id,
        "poc_sha256": attempt.poc_sha256,
        "poc_bytes": attempt.poc_bytes,
        "leg": "vul",
        "wall_ms": attempt.wall_ms,
    }
    if attempt.submission is None:
        return {
            **row,
            "status": "failed",
            "error": attempt.error,
            "error_detail": attempt.error_detail,
        }
    return {
        **row,
        "status": "submitted",
        "poc_id": attempt.submission.poc_id,
        "response_exit_code": attempt.submission.response_exit_code,
        "cached": attempt.submission.cached,
    }


def _final_record(
    final: FinalAttempt, attempts: Sequence[VulAttempt], grades: Mapping[str, str] | None
) -> dict[str, object]:
    row: dict[str, object] = {
        "status": final.status,
        "designation": final.designation,
        "wall_ms": final.wall_ms,
    }
    if grades is not None:
        row["candidate_grades"] = dict(grades)
    if final.status == STATUS_NONE:
        return {**row, "reason": final.reason}
    attempt = final.attempt
    assert attempt is not None  # scored·failed는 지정한 후보가 있다
    row |= {
        "finding_id": attempt.finding_id,
        "poc_sha256": attempt.poc_sha256,
        "poc_bytes": attempt.poc_bytes,
    }
    if final.status == STATUS_FAILED:
        return {**row, "error": final.error, "error_detail": final.error_detail}
    outcome = final.outcome
    assert outcome is not None  # scored는 판정이 있다
    return {
        **row,
        "task_id": outcome.task_id,
        "poc_id": outcome.poc_id,
        "vul_exit_code": outcome.vul_exit_code,
        "fix_exit_code": outcome.fix_exit_code,
        "success": outcome.success,
        # 다리별로 「이 run 전에 이미 있던 코드였나」다 - `vul`은 `vul_preexisted`가 가른다.
        "cached": {"vul": vul_preexisted(attempts, attempt), "fix": outcome.fix_cached},
    }
