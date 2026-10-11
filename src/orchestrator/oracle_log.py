"""채점기(oracle) 왕복의 run 산출물 — 장부와 사이드카 `{run_dir}/oracle/verdict.json` (#386).

`runner`가 중간 gen과 모델 submit마다 `submit_vul`을, run당 한 번 `finalize`를 부르고
(`src/interfaces/oracle.py`)
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

`candidates`(vul 제출 한 건 — 중간 gen은 `designatable: false`와 원시 종료 코드 조회를
`observation`에 싣고, 모델 submit은 그 필드가 없어 최종 지정 후보임을 뜻한다. 성공이면
`poc_id`·`response_exit_code`·`cached`, 실패면 `error`·`error_status`(HTTP 상태. HTTP 오류가
아니면 `null`)·`error_detail`, 0바이트라 제출하지 않았으면 `status: skipped`·`error: empty_poc`) ·
`final`. `final`은 run이 최종 단계에 못 닿았으면(8단계가 끝까지 가지 못했다 — 단 예산·서버 쪽
게이트웨이 오류로 끊겨도 이미 낸 최종 후보가 있거나 `pov_attempt_top_n`의 추가 대상에서 끊긴 run은
최종 지정까지 하고 끝난다) `null`이고 아니면
`status`(`scored` | `failed` | `none`)·`designation`(지정 근거 — 지정 못 했으면 `null`)과 그에
따른 값이다. 후보가 둘 이상이어서 지정이 등급을 읽었으면 `candidate_grades`(`finding_id` → 지정이
읽은 내부 근거 등급)도 든다 — 키가 없으면 등급을 읽지 않았다(후보 0·1건).

⚠ **`null`은 「안 돌았다」·「못 가렸다」다 — 0도 거짓도 아니다**(CLAUDE.md §0). `fix_exit_code`가
`null`인 것은 `vul`에서 크래시가 안 나 채점기가 `fix`를 안 돌린 경우다. `final.status`가 `none`인 것
(지정된 후보가 없었다)과 `failed`인 것(지정했는데 판정을 못 읽었다)은 다른 사건이다 —
「최종 PoC가 없다」가 「최종 PoC가 실패했다」로 읽히면 안 된다. `none`의 `reason`은 **넷**이다:
`no_poc`·`all_vul_submissions_failed`·`rate_limited`(지정할 후보가 **없다** — 낼 PoC가 없던 것,
낸 것이 다 실패한 것, 한도(HTTP 429)가 전부 삼킨 것은 서로 다른 사건이다)와
`grade_unknown`(후보 중에 내부 근거 등급·`confidence`·Critic 판정 계단 중 하나라도 모르는 것이
있어 **규칙이 거부했다** — `runner._designate_final`. 이름은 등급 미상에서 굳었지만 그 검사는
셋을 같이 본다 · ⏵ 2026-10-11 판정 계단이 셋째로 들었다).
현 러너로는 닿지 않는 방어 분기다). 어느 쪽이든 `fix` 다리와 판정은 안 돈다.

⏵ 2026-10-04 — **`grade_tie`는 없어졌다**(그래서 다섯이 아니라 넷이다). 후보가 둘 이상일 때
지정이 등급 **동점에서 거부하던** 것을 사슬로 바꿨다(`runner._designate_final`): 같은
바이트끼리 접은 뒤 ①등급 ②`confidence` ③작은 바이트 ④PoC 해시다. ④가 **전순서**라 서로 다른
PoC 사이의 동점이 남지 않는다 — 거부할 자리가 없어져 사유도 상수도 지웠다. 바꾼 이유: 실측
run에서 후보 8건이 **같은 바이트 1개**였고(제출 8 = 고유 PoC 1 = DB 행 1) 동점 거부가 그
run의 점수를 0으로 만들었다.
⏵ 2026-10-11 — 사슬 맨 앞에 **Critic 판정**이 붙어 다섯이다(`critic_verdict`: 보류 PoC는
미경유 PoC 뒤, 미경유 PoC는 uphold PoC 뒤다 — ⏵ 2026-10-12 미경유를 uphold에서 떼었다).

⚠ **이 파일은 하네스 내부 등급이 아니다.** Evidence가 아니고 게이트가 읽지 않는다(#232).

## 쓰기

vul 제출·최종 판정마다 **파일 전체를 다시 쓴다**(작다) — 8단계가 중간에 끊겨도(`BudgetExhausted`
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

# `status == none`의 사유 **넷**. 둘로 갈린다: **지정할 후보가 없어서**인 셋(`no_poc` ·
# `all_vul_submissions_failed` · 아래 `rate_limited`)과, 후보가 **둘 이상**인데 **규칙이 돌았는데
# 하나를 못 가려서**인 하나(`grade_unknown`, `runner._designate_final`). 마지막은 「규칙이
# 없다」가 아니다 — 규칙이 거부한 것이다. ⚠ 세는 말로 적지 않는다 — `rate_limited`가 아래에
# 따로 있어서 「앞 셋·뒤 하나」로 쓰면 이 자리에 보이는 상수 셋과 어긋난다.
#
# ⏵ 2026-10-04 — `grade_tie`를 지웠다(그래서 다섯이 아니라 넷이다). 등급 동점은 더 이상 거부
# 사유가 아니고 사슬의 다음 단계로 넘어간다(`runner._designate_final`). 사슬의 끝이 PoC 해시라
# **전순서**이고, 서로 다른 PoC가 끝까지 동점일 수 없다 — 못 가리는 분기가 없어졌으니 사유도
# 없앴다. 지우지 않고 두면 어느 run도 못 내는 사유가 문서와 경고에 남는다(닿지 않는 코드다).
REASON_NO_POC = "no_poc"
# 제출 전에 거부한 0바이트 PoC의 사유 — 제출이 **없었으므로** 실패로 세지 않는다(이슈 #423).
ERROR_EMPTY_POC = "empty_poc"
# 제출은 성공했는데 서버에 그 바이트의 판정 행이 없거나 코드가 비었다 — 조회가 비었다는 사실을
# 「안 돌았다」와 가르는 표지다(`observation_error`).
OBSERVATION_NO_VERDICT = "no_verdict"

REASON_ALL_VUL_FAILED = "all_vul_submissions_failed"
# 후보 중에 내부 근거 등급·`confidence`·Critic 판정 계단 중 하나라도 모르는 것이 있어 규칙이
# 거부했다. 이름은 등급 미상에서 굳었지만 `runner._designate_final`의 검사가 셋을 같이 본다
# (⏵ 2026-10-11 판정 계단이 셋째로 들었다). 모르는 값을 0으로 깔면
# 없는 근거로 고르게 되기 때문이다(CLAUDE.md §0). 현 러너로는 닿지 않는 방어 분기다.
REASON_GRADE_UNKNOWN = "grade_unknown"

# 제출 실패가 **전부** 채점기 레이트리미트여서 후보가 안 남은 경우(이슈 #422). 그렇게 생긴
# 「점수 없음」은 「냈는데 거부당했다」와 다른 사건이다 — 한도는 60초 슬라이딩 윈도라
# **사라진 판정은 원래 나왔을 수 있는 것**이고, 재시도가 없어 복구되지 않는다.
# 한도 밖 실패가 한 건이라도 섞이면 더 넓은 `all_vul_submissions_failed`로 남긴다 —
# 「한도 때문」을 단언할 수 없다.
REASON_RATE_LIMITED = "rate_limited"

# 채점기가 한도를 넘긴 제출에 돌려주는 HTTP 상태(어댑터 모듈 docstring의 2026-10-02
# 실측: 20번째가 통과하고 21번째가 429). **어댑터를 import하지 않고 상태만 본다** —
# 오케스트레이터는 어댑터 리터럴을 소스에 두지 않는다
# (`test_orchestrator_source_has_no_adapter_import`).
_HTTP_TOO_MANY_REQUESTS = 429

# 「크래시가 안 났다」로 세는 `vul` 코드 — 어댑터 `_NO_CRASH_EXIT_CODES`
# (`src/adapters/cybergym_oracle.py`)의 사본이다(오케스트레이터는 어댑터를 못 읽는다).
# 같다는 것은 `test_the_excluded_code_tuple_matches_the_adapter`가 문다.
# **로그 전용이다** — `runner._terminal_fields`만 읽고 지정은 이 값을 안 읽는다.
NO_CRASH_VUL_EXIT_CODES = (0, 71, 300)

# `designation` 값 — 어떤 근거로 최종 PoC를 지정했는지. **위치(처음·마지막)로도 채점기 점수로도
# 고르지 않는다** - 이 저장소가 2026-10-03에 정한 규칙이고 저자의 금지 문장이 아니다(조사
# 문서 1.3절은 최종 하나를 에이전트가 지정한다는 채점 방식만 적는다).
#
# ⏵ 2026-10-04 — 둘에서 여섯으로 늘었다. 동점 거부를 사슬로 바꾸면서 **무엇이 실제로 갈랐는지**를
# 싣기로 했다(§0 — 지정 근거를 적는다). 앞 둘은 고를 것이 없던 경우고, 뒤 넷은 사슬의 **어느
# 단계가** 둘을 갈랐는지다. `poc_digest`가 찍힌 run은 등급·`confidence`·바이트 수가 전부 같아
# **내용 해시로** 갈린 것이다 — 약한 근거이고, 그렇게 적혀 있어야 읽는 쪽이 안다.
# ⏵ 2026-10-11 — 사슬 맨 앞에 `critic_verdict`가 붙어 일곱이고 사슬은 다섯이다.
DESIGNATION_SOLE_CANDIDATE = "sole_candidate"  # 후보가 하나뿐 — 선택이 없었다
DESIGNATION_SOLE_POC = "sole_poc"  # 후보는 여럿인데 **바이트가 전부 같다** — 선택이 없었다
DESIGNATION_CRITIC_VERDICT = "critic_verdict"  # Critic 판정 — uphold < 미경유 < 보류 순으로 앞선다
DESIGNATION_TOP_GRADE = "top_grade"  # 내부 근거 등급이 갈랐다
DESIGNATION_TOP_CONFIDENCE = "top_confidence"  # 등급 동점 — `confidence`가 갈랐다
DESIGNATION_SMALLEST_POC = "smallest_poc"  # 등급·confidence 동점 — 바이트가 작은 쪽
DESIGNATION_POC_DIGEST = "poc_digest"  # 위 전부 동점 — PoC 해시 사전순(전순서를 만드는 자리)


def clip_error(exc: BaseException) -> str:
    """예외 문구를 상한까지 자른다 — `error_detail`은 사이드카에만 간다(이유는 모듈 docstring)."""
    return str(exc)[:ERROR_DETAIL_CHARS]


@dataclass(frozen=True)
class VulAttempt:
    """PoC 하나의 `vul` 제출 시도. `submission`이 없으면 실패다(`error`가 예외 클래스 이름)."""

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
    # 서버가 돌려준 HTTP 상태 — HTTP 오류가 아니면(연결 실패·타임아웃·계약 어긋남)
    # `None`이다. 이것이 없으면 429(레이트리미트)와 그 밖의 실패를 **사유 본문을
    # 파싱해서만** 가른다(이슈 #422).
    error_status: int | None = None
    # 모델의 `submit`만 최종 지정 후보가 된다. 중간 gen 판정은 모델 입력으로만 쓰며 정렬 키에는
    # 닿지 않는다. `candidates`가 이 필드로 먼저 거른다.
    designatable: bool = field(kw_only=True)
    observation_exit_code: int | None = None
    observation_error: str | None = None
    observation_error_detail: str = ""

    @property
    def ok(self) -> bool:
        return self.submission is not None

    @property
    def skipped(self) -> bool:
        """제출 전에 거부했다(0바이트) — 제출 시도도 실패도 아니다."""
        return self.error == ERROR_EMPTY_POC


def vul_ok(
    finding_id: str,
    poc: bytes,
    submission: VulSubmission,
    wall_ms: int,
    *,
    designatable: bool,
    observation_exit_code: int | None = None,
    observation_error: str | None = None,
    observation_error_detail: str = "",
) -> VulAttempt:
    return VulAttempt(
        finding_id=finding_id,
        poc_sha256=hashlib.sha256(poc).hexdigest(),
        poc_bytes=len(poc),
        wall_ms=wall_ms,
        poc=poc,
        submission=submission,
        designatable=designatable,
        observation_exit_code=observation_exit_code,
        observation_error=observation_error,
        observation_error_detail=observation_error_detail,
    )


def vul_failed(
    finding_id: str,
    poc: bytes,
    exc: Exception,
    wall_ms: int,
    *,
    designatable: bool,
) -> VulAttempt:
    return VulAttempt(
        finding_id=finding_id,
        poc_sha256=hashlib.sha256(poc).hexdigest(),
        poc_bytes=len(poc),
        wall_ms=wall_ms,
        error=type(exc).__name__,
        error_detail=clip_error(exc),
        # `CyberGymOracleError.status`를 덕 타이핑으로 읽는다 — 어댑터 import 금지(아래 상수 주석).
        error_status=getattr(exc, "status", None),
        designatable=designatable,
    )


def vul_empty(finding_id: str, poc: bytes, *, designatable: bool) -> VulAttempt:
    """0바이트 PoC — 제출하지 않았다. 실패가 아니라 **건너뜀**이다(`VulAttempt.skipped`)."""
    return VulAttempt(
        finding_id=finding_id,
        poc_sha256=hashlib.sha256(poc).hexdigest(),
        poc_bytes=0,
        wall_ms=0,
        error=ERROR_EMPTY_POC,
        error_detail="PoC가 0바이트라 vul 다리에 제출하지 않았다",
        designatable=designatable,
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
    """한 run의 채점기 장부 — 모든 vul 시도와 최종 지정 단계. `runner._Accumulator`가 든다."""

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
        """모델이 submit했고 `vul` 제출도 성공한 최종 후보(`runner._designate_final`)."""
        return [attempt for attempt in self.attempts if attempt.ok and attempt.designatable]

    @property
    def submitted(self) -> int:
        """성공한 vul 제출 **전부** — 중간 gen 포함이다. 최종 후보 수는 `candidates`다."""
        return sum(1 for attempt in self.attempts if attempt.ok)

    @property
    def failed(self) -> int:
        """제출을 시도했다가 실패한 수 — 0바이트라 안 낸 것(`skipped`)은 세지 않는다."""
        return sum(1 for attempt in self.attempts if not attempt.ok and not attempt.skipped)

    @property
    def skipped_empty(self) -> int:
        return sum(1 for attempt in self.attempts if attempt.skipped)

    @property
    def intermediate(self) -> int:
        """중간 gen 시도 수(성공·실패·건너뜀 합) — 되먹임을 썼는지의 표지다."""
        return sum(1 for attempt in self.attempts if not attempt.designatable)

    @property
    def observation_failed(self) -> int:
        """제출은 성공했는데 판정 조회가 실패했거나 비었던 수 — 모델은 `unavailable`을 받았다."""
        return sum(1 for attempt in self.attempts if attempt.observation_error is not None)

    @property
    def record_ref(self) -> str | None:
        """사이드카의 run_dir 상대 경로. 아직 안 썼거나 마지막 쓰기가 실패했으면 `None`."""
        return REF if self._written and self.write_error is None else None

    @property
    def rate_limited(self) -> int:
        """레이트리미트(429)로 실패한 시도 수(이슈 #422)."""
        return sum(1 for a in self.attempts if a.error_status == _HTTP_TOO_MANY_REQUESTS)

    def none_reason(self) -> str:
        """지정할 후보가 없을 때의 사유 — 시도가 없었나, 전부 한도에 걸렸나, 전부 실패했나.

        셋을 가르는 이유는 같은 「점수 없음」으로 읽힐 만큼 **다른 사건**이기 때문이다(§0).
        """
        # 최종 지정 사유이므로 모델 submit 시도만 본다. 중간 gen의 제출 실패가 최종 답 부재를
        # `all_vul_submissions_failed`나 `rate_limited`로 바꾸면 모델이 submit하지 않은 사실을
        # 잃는다.
        failures = [a for a in self.attempts if a.designatable and not a.ok and not a.skipped]
        if not failures:
            return REASON_NO_POC
        if all(a.error_status == _HTTP_TOO_MANY_REQUESTS for a in failures):
            return REASON_RATE_LIMITED
        return REASON_ALL_VUL_FAILED

    def summary(self) -> dict[str, str | int | None] | None:
        """`RunRecord.oracle`이 될 요약. 채점기가 안 배선된 run은 `None`이다."""
        if not self.wired:
            return None
        return {
            "vul_submitted": self.submitted,
            "vul_failed": self.failed,
            "final": STATUS_NOT_REACHED if self.final is None else self.final.status,
            # `none`의 사유 — 후보가 여럿인데 등급·`confidence`·Critic 판정을 몰라 못 가른 run
            # (`grade_unknown`)과 후보가 없던 run을 매니페스트에서도 가른다.
            "final_reason": None if self.final is None else self.final.reason,
            "record": self.record_ref,
            # 아래 넷은 **0이 아닐 때만** 있다 - 이 필드 이전의 manifest와 키 집합이 같아야 해서다.
            # `vul_submitted`는 중간 gen 제출을 **포함**하고(`submitted`) `vul_intermediate`는 중간
            # gen **시도** 전부(성공·실패·건너뜀)라 둘의 차이는 후보 수가 아니다 - 후보 수는
            # `vul_candidates`다. `vul_intermediate`가 있는 run에는 **0이어도** 늘 있다 - 중간
            # gen이 없던 run(키 없음)에서만 `vul_submitted`가 곧 후보 수다.
            **{
                key: count
                for key, count in (
                    ("vul_intermediate", self.intermediate),
                    ("vul_observation_failed", self.observation_failed),
                    ("vul_empty_poc", self.skipped_empty),
                )
                if count
            },
            **({"vul_candidates": len(self.candidates)} if self.intermediate else {}),
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
        # 조회가 예외 없이 비어 돌아온 경우(`no_verdict`)는 trace 이벤트가 없다 - 사이드카뿐이다.
        observed = f"{sidecar}의 observation" if sidecar else "trace의 oracle_failed(빈 답은 없다)"
        if self.failed:
            out.append(f"채점기 vul 제출 {self.failed}건이 예외로 실패했다 - 사유는 {where}에 있다")
        # 후보가 남은 run에서도 말한다 — 점수가 조용히 낮아진 사유가 된다(이슈 #422). 「그중」이
        # 가리키는 줄(vul 제출 실패)의 **바로 뒤**에 둔다.
        if self.rate_limited:
            out.append(
                f"그중 {self.rate_limited}건은 채점기 레이트리미트(HTTP 429)다 - 재시도가 없어 "
                "그 판정은 복구되지 않는다"
            )
        if self.observation_failed:
            out.append(
                f"중간 gen의 채점기 판정 조회 {self.observation_failed}건이 실패하거나 비어 모델이 "
                f"`unavailable`을 받았다 - 사유는 {observed}에 있다"
            )
        if self.skipped_empty:
            out.append(f"0바이트 PoC {self.skipped_empty}건은 제출하지 않았다")
        final = self.final
        if final is None:
            out.append(
                "채점기가 배선됐지만 8단계가 끝까지 돌지 않아 최종 PoC를 지정하지 못했다"
                f"(vul 제출 {self.submitted}건, 그중 최종 후보 {len(self.candidates)}건) - "
                "이 run은 채점 점수가 없다"
            )
        elif final.status == STATUS_FAILED:
            reason = f"사유는 {sidecar}" if sidecar else "사유는 trace의 oracle_final"
            out.append(
                f"채점기 최종 판정이 예외({final.error})로 실패했다 - 이 run은 채점 점수가 없다"
                f"({reason})"
            )
        elif final.status == STATUS_NONE:
            out.append(_none_warning(final, len(self.candidates)))
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


def _none_warning(final: FinalAttempt, candidates: int) -> str:
    """`status == none`의 경고 한 문장 - 사유마다 다른 말이다(같은 문장이면 사유가 안 가려진다).

    콘솔에 찍히므로 cp949로 인코딩할 수 있는 글자만 쓴다.
    """
    tail = "이 run은 채점 점수가 없다"
    if final.reason == REASON_GRADE_UNKNOWN:
        return (
            f"vul 제출이 성공한 최종 후보가 {candidates}건인데 그중 내부 근거 등급·"
            "confidence·Critic 판정 중 모르는 것이 있어 최종 PoC를 지정하지 않았다 - "
            "모르는 값으로 고르면 "
            "없는 근거로 "
            f"고르는 것이다. fix 다리와 판정 조회를 돌리지 않았다 - {tail}"
        )
    if final.reason == REASON_RATE_LIMITED:
        # 사유를 가르는 자리가 사이드카·매니페스트와 **이 한 줄** 둘이다. 앞 판은 이 줄에 분기가
        # 없어 한도로 잃은 run이 「낼 PoC가 없었다」로 찍혔다 - 가르려던 그 오독이다(이슈 #422).
        return (
            "채점기에 낼 최종 PoC를 지정하지 못했다(vul 제출이 전부 채점기 "
            f"레이트리미트(HTTP 429)로 실패해) - 낼 PoC는 있었고 한도가 판정을 삼켰다. {tail}"
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
    if not attempt.designatable:
        row["designatable"] = False
    if attempt.skipped:
        return {**row, "status": "skipped", "error": attempt.error,
                "error_detail": attempt.error_detail}
    if attempt.submission is None:
        return {
            **row,
            "status": "failed",
            "error": attempt.error,
            "error_status": attempt.error_status,
            "error_detail": attempt.error_detail,
        }
    result = {
        **row,
        "status": "submitted",
        "poc_id": attempt.submission.poc_id,
        "response_exit_code": attempt.submission.response_exit_code,
        "cached": attempt.submission.cached,
    }
    if not attempt.designatable:
        result["observation"] = {
            "exit_code": attempt.observation_exit_code,
            "error": attempt.observation_error,
            "error_detail": attempt.observation_error_detail,
        }
    return result


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
