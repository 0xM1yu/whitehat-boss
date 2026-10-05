"""카나리 보고서를 읽어 **채점 run을 거부할 사유**를 낸다 (이슈 #383).

보고서를 만드는 쪽은 `tools/oracle_canary.py`다 — 과제의 저자 참조 PoC와 무해 입력을
`-vul`·`-fix` 이미지에서 N회 돌려 네 조합이 기대와 같은지 본다. 이 모듈은 **그 JSON만 읽는다**:
도커를 돌리지 않고, 이미지를 당기지 않고, 참조 PoC 바이트를 보지 않는다(보고서에 없다 —
길이 `reference_poc_len`뿐이고 이 모듈은 그것조차 안 읽는다).

**왜 하네스가 보고서를 요구하나.** 오라클 판정이 비결정적일 수 있고 그 원인은 우리 코드도 저자
코드도 아니라 **채점 컨테이너를 돌리는 호스트 커널의 mmap 엔트로피**다. `vm.mmap_rnd_bits = 32`
에서 sanitizer 섀도 매핑이 간헐 실패해 타깃이 입력을 읽기도 전에 `139`로 죽고, 그 코드가 성공
조건에서 「크래시」로 집계된다 — 2026-10-02 `win11wsl`에서 **정답 PoC ≈0.40 · 무해 입력 ≈0.28**이
「성공」이었다(차가 ≈0.12). ⚠ **그 셋은 계산값이다** — 다리별 표본 비율(n=10·20)의 곱이고 직접
잰 성공률이 아니다(그 도구 docstring). 28로 내리면 같은 네 조합이 40/40 기대와 같다.
🔴 **그 값은 영속하지 않는다** — `vm.*`는 네임스페이스가 없어 VM 전역이고 Docker Desktop 재기동으로
32로 돌아간다. **그래서 처방이 아니라 카나리가 본체다.**

**이 모듈이 올리는 것 하나.** 도구는 엔트로피를 **경고로만** 다룬다 — *"네 조합이 기대와 같으면
값이 32여도 `pass`다"*(그 모듈 docstring). 행동이 진짜 검사이고 sysctl은 단서라는 판단이고, 도구
수준에서는 맞다. **채점 게이트는 그것을 거부로 올린다**: 32에서 네 조합이 우연히 맞을 확률이 0이
아니고(그 도구 docstring의 계산값), 그 run의 점수는 PoC 품질을 거의 반영하지 않는다.

⚠ **이 모듈이 못 보는 것 — 신선도.** 보고서가 **지금 이 부팅**의 것인지는 여기서 못 판별한다.
`host_boot_id`가 보고서에 있지만 **현재 boot_id를 읽으려면 컨테이너를 하나 돌려야** 하고 이 모듈은
도커를 안 쓴다. `measured_at`의 나이로 자르는 것도 안 한다 — 자를 나이가 **미실측**이라 지어낸
상수가 된다. 그래서 신선도의 계약은 **조작자가 run마다 경로를 명시로 넘기는 것**이다
(`--oracle-canary`). 거절 문구가 `measured_at`·`docker_version`을 들어 조작자가 그것을 볼 수 있게
한다.

⚠ **이 모듈이 못 막는 것 — 손으로 고친 보고서.** 보고서의 칸은 전부 같은 파일의 기록이다.
`repeat`·`completed`·`segv_count`·`problems`도 손으로 고칠 수 있고, `combos[*].exit_codes`의 실제
길이·내용은 읽지 않는다 — `repeat: 10`인데 `exit_codes`가 셋뿐인 보고서가 통과한다
(`tests/test_canary_report.py`가 그 동작을 핀으로 둔다). 대조하려면 `combos`를 필수 칸으로 올려야
하는데 그러면 기존 보고서 픽스처 넷이 깨지고, `exit_codes`도 같은 파일의 칸이라 신뢰 전제는
남는다. 그래서 이 게이트가 막는 것은 **실수·낡은 보고서·도구가 스스로 낮춘 판정**이지 조작자가
뜻을 갖고 쓴 보고서가 아니다(조작자 신뢰 전제 — 아래 거절 검사의 「관측 칸」도 같은 파일의
기록이다).

⚠ **그리고 하네스가 카나리를 스스로 돌리지는 않는다.** 이슈 #383의 선택지 (a)는 그것까지인데,
돌리려면 run이 `-fix` 이미지와 참조 PoC를 손에 쥐어야 하고 N·보고서 위치·`weak_pass` 처리가 전부
정해야 하는 값이다. 그 결정은 사람 몫으로 남겼다 — 이 모듈은 「카나리 없이 채점하지 않는다」까지다.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

# 이보다 큰 파일은 읽지 않는다. **정한 값이다(실측 아님)** — 도구가 쓴 실 보고서 둘이
# 1,652 B·1,742 B였고(2026-10-04 `arvo:1065`·`oss-fuzz:368076871`) 이 상한은 그보다 약 600배다.
# 목적은 폭주 차단이지 모양 검사가 아니다 — 상한이 없으면 임의 크기 파일이 메모리로 들어온다.
_MAX_REPORT_BYTES = 1 << 20

# `vm.mmap_rnd_bits`가 가질 수 있는 **모양** — 10진 숫자뿐이다. `int()`로 보면 `2_8`·`+28`·`٢٨`
# (아라비아-인도 숫자)가 다 28이 되고 `-1`도 「28 이하」로 통과한다. 이 정규식은 그 모양들
# (부호·밑줄·비ASCII 숫자·공백·소수점)을 막는다. ⚠ **값의 하한은 안 문다** — `"0"`·`"00"`은
# 이 모양에 맞아 통과하고 28 이하로 읽힌다. 커널 최솟값을 이 저장소가 재지 않았고(아키텍처마다
# 다르다) 도구의 `_BITS_LINE`(`[0-9]+`)도 `0`을 받아 둘이 갈리지 않는다. 그 한계를
# `tests/test_canary_report.py`가 핀으로 둔다.
_DECIMAL_BITS = re.compile(r"[0-9]{1,3}")

# 보고서가 통과로 세는 유일한 판정. 도구는 넷을 낸다 — `pass` · `weak_pass`(관측 횟수가 모자라
# `ok=false`) · `fail` · `incomplete`. 채점 run은 `pass`만 받는다.
PASS_VERDICT = "pass"

# 이 값보다 큰 `vm.mmap_rnd_bits`는 거부한다. 도구의 `MMAP_RND_BITS_MAX`와 같은 수이고 보고서가
# 40/40을 본 값이다. 🔴 **도구를 import해서 가져오지 않는다** — `src/`가 `tools/`를 import하는
# 경로가 저장소에 0건이고, 그 방향을 이 결함 하나로 열지 않는다. 두 리터럴이 갈리면 테스트가 잡는다.
MMAP_RND_BITS_MAX = 28

# 이보다 적게 돌린 보고서는 거부한다. 도구의 `_MEASURED_REPEAT`와 같은 수다 — 판별력 실측이 정답
# PoC에 쓴 반복 수(n=10)이고, 그 아래는 도구도 `weak_pass`로 낮춘다. 🔴 **선언(`below_measured_
# repeat`)만 보지 않고 `repeat` 자신도 이 값과 견준다** — 손으로 `repeat`만 고친 보고서(선언은
# `false` 그대로)가 선언만 보던 판에서 통과했다. ⚠ **`repeat`도 같은 파일의 칸이다** — 손으로
# 10 이상으로 올리면 통과하고 `combos[*].exit_codes`의 실제 길이와는 대조하지 않는다(모듈
# docstring의 「못 막는 것」). 이 검사가 막는 것은 **선언과 `repeat`가 어긋난 보고서**이지 위조가
# 아니다. `MMAP_RND_BITS_MAX`와 같은 이유로 도구를 import하지 않고 리터럴을 둔다 — 갈리면 테스트가
# 잡는다.
MEASURED_REPEAT = 10

# 게이트가 읽는 칸과 그 형. 빈 칸을 기본값으로 메우지 않는다 — 「없다」를 「통과」로 읽으면 게이트가
# 없는 것과 같다(§0). `mmap_rnd_bits`는 미측정일 때 `null`이라 `None`을 허용한다.
_REQUIRED: tuple[tuple[str, type | tuple[type, ...]], ...] = (
    ("task", str),
    ("verdict", str),
    ("ok", bool),
    ("repeat", int),
    ("below_measured_repeat", bool),
    ("rnd_bits_unmeasured", bool),
    ("mmap_rnd_bits", (str, type(None))),
    ("rnd_bits_above_28", (bool, type(None))),
    ("measured_at", str),
    # **같은 파일의 반증 칸.** 요약(`verdict`·`ok`)만 읽으면 그 둘을 뒤집은 보고서가 통과한다 —
    # 자기 선언이 같은 파일의 관측보다 세지 못하게 셋을 같이 읽는다(CLAUDE.md §2.2). ⚠ 이 셋도
    # 손으로 고칠 수 있는 칸이다 — 요약만 뒤집은 보고서를 막지 위조를 막지 않는다.
    ("completed", bool),
    ("segv_count", int),
    ("problems", list),
)


class CanaryReportError(Exception):
    """보고서를 읽을 수 없다. **못 읽은 것은 통과가 아니다** — 호출부는 run을 거부한다."""


@dataclass(frozen=True)
class CanaryReport:
    """보고서에서 게이트가 쓰는 칸만 옮긴 것. 나머지 칸(`combos` 등)은 안 읽는다."""

    task: str
    verdict: str
    ok: bool
    repeat: int
    below_measured_repeat: bool
    rnd_bits_unmeasured: bool
    mmap_rnd_bits: str | None
    rnd_bits_above_28: bool | None
    measured_at: str
    # 도구가 네 조합을 끝까지 돌렸나 · `139`를 몇 번 봤나 · 어긋남 목록. `verdict`의 근거다.
    completed: bool
    segv_count: int
    problems: tuple[str, ...]
    # 어느 장비에서 쟀는지 — 거절 문구에만 쓴다. 도구가 못 읽으면 `null`이라 없어도 된다.
    docker_version: str | None = None
    # **검사한 바이트의** sha256(`sha256:<hex>`) - 파일을 한 번 더 읽어 구하지 않는다. 검사와 기록
    # 사이에 바뀐 파일이 「통과한 보고서」로 남지 않게 한다(#487).
    sha256: str | None = None
    # 읽은 경로 그대로 - 기록하는 경로가 읽은 파일과 같은 값이게 한다(#487).
    path: str | None = None


def read_canary_report(path: Path) -> CanaryReport:
    """보고서 JSON을 읽어 옮긴다. 못 읽거나 칸이 비거나 형이 다르면 `CanaryReportError`.

    **못 읽은 것은 통과가 아니다** — 어떤 사유로든 이 함수가 던지면 호출부가 run을 거부한다.
    그래서 예외를 좁게 잡지 않는다: `UnicodeDecodeError`(UTF-8이 아닌 파일)와
    `RecursionError`(깊이 중첩된 JSON)는 `OSError`도 `JSONDecodeError`도 아니라 앞 판에서
    **트레이스백으로 샜다**. 호출부는 `CanaryReportError`만 잡으므로 조작자가 받는 것이 문구가
    아니었다(제출은 안 나가니 fail-closed는 유지됐다).
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise CanaryReportError(f"카나리 보고서를 읽지 못했다({path}): {exc}") from exc
    if size > _MAX_REPORT_BYTES:
        raise CanaryReportError(
            f"카나리 보고서가 너무 크다({path}: {size} B) - 도구가 쓰는 보고서는 2 KB 선이다"
        )
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, ValueError) as exc:  # `UnicodeDecodeError`는 `ValueError`다
        raise CanaryReportError(f"카나리 보고서를 읽지 못했다({path}): {exc!r}") from exc
    try:
        body = json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise CanaryReportError(f"카나리 보고서가 JSON이 아니다({path}): {exc!r}") from exc
    if not isinstance(body, dict):
        raise CanaryReportError(f"카나리 보고서가 JSON 객체가 아니다({path})")
    # 완주 못 한 run의 기록(`tools/oracle_canary.py`의 `_incomplete_record`)은 칸이 **적다** —
    # 필수 칸 루프가 먼저 돌면 「repeat가 없다」가 나와 **파일에 적힌 진짜 원인이 가려진다.**
    # 무는 것은 `completed`뿐이다 — `_incomplete_record`가 그것을 늘 거짓으로 쓴다. `verdict`까지
    # 보면 칸이 다 있는데 `verdict`만 `incomplete`인 보고서(손으로 고친 것)가 **문구 없이** 여기서
    # 죽어, 그 경우를 무는 `canary_refusal`의 핀이 영영 안 돈다.
    if body.get("completed") is False:
        raise CanaryReportError(
            f"카나리가 완주하지 못했다({path}): verdict={body.get('verdict')!r} "
            f"error={body.get('error')!r} - 그 사유를 고치고 다시 돌린다"
        )
    for key, want in _REQUIRED:
        if key not in body:
            raise CanaryReportError(f"카나리 보고서에 {key}가 없다({path})")
        value = body[key]
        # `bool`은 `int`의 하위형이라 순서가 중요하다 — `repeat`에 `true`가 오면 거부한다.
        if want is int and isinstance(value, bool):
            raise CanaryReportError(f"카나리 보고서의 {key}가 정수가 아니다({path})")
        if not isinstance(value, want):
            raise CanaryReportError(f"카나리 보고서의 {key}의 형이 다르다({path})")
    return CanaryReport(
        task=body["task"],
        verdict=body["verdict"],
        ok=body["ok"],
        repeat=body["repeat"],
        below_measured_repeat=body["below_measured_repeat"],
        rnd_bits_unmeasured=body["rnd_bits_unmeasured"],
        mmap_rnd_bits=body["mmap_rnd_bits"],
        rnd_bits_above_28=body["rnd_bits_above_28"],
        measured_at=body["measured_at"],
        completed=body["completed"],
        segv_count=body["segv_count"],
        problems=tuple(str(item) for item in body["problems"]),
        docker_version=body.get("docker_version") or None,
        sha256=f"sha256:{hashlib.sha256(raw).hexdigest()}",
        path=str(path),
    )


def canary_refusal(report: CanaryReport, *, task_id: str) -> str | None:
    """이 보고서로 `task_id`를 채점하면 안 되는 사유, 괜찮으면 `None`.

    사유를 **첫 하나만** 낸다 — 조작자가 고칠 것은 하나씩이고, 문구가 길면 안 읽힌다. 어느 사유든
    「언제 쟀나」(`measured_at`)를 같이 적는다: 비영속한 값을 보는 검사라 시각이 사실의 일부다.
    """
    where = f"{report.measured_at} 측정"
    if report.docker_version:
        where += f" · Docker {report.docker_version}"
    if report.task != task_id:
        return f"카나리가 다른 과제의 것이다 - 보고서는 {report.task}, 이 run은 {task_id}({where})"
    if report.verdict != PASS_VERDICT or not report.ok:
        return (
            f"카나리 판정이 {report.verdict!r}(ok={str(report.ok).lower()})다 - "
            f"채점 run은 {PASS_VERDICT}만 받는다({where})"
        )
    # 여기부터 셋은 **같은 파일의 관측 칸**을 본다. 위 두 칸은 도구의 자기 선언이라 손으로 뒤집을 수
    # 있고, 그것만 보는 게이트는 자기 선언만큼만 세다(§2.2). ⚠ 관측 칸도 같은 파일의 기록이라
    # 손으로 고치면 통과한다 — 이 셋이 막는 것은 **요약만 뒤집은 보고서**이지 위조가 아니다
    # (모듈 docstring).
    if not report.completed:
        return (
            f"카나리가 pass라 적었는데 completed가 거짓이다 - "
            f"네 조합을 끝까지 안 돌렸다({where})"
        )
    if report.segv_count:
        return (
            f"카나리가 pass라 적었는데 139(SIGSEGV)를 {report.segv_count}회 봤다 - "
            f"그 코드가 이 게이트의 표적이다({where})"
        )
    if report.problems:
        return (
            f"카나리가 pass라 적었는데 problems가 {len(report.problems)}건이다: "
            f"{report.problems[0]!r}({where})"
        )
    if report.rnd_bits_unmeasured or report.mmap_rnd_bits is None:
        return (
            f"카나리가 호스트 vm.mmap_rnd_bits를 못 읽었다 - 미측정을 "
            f"{MMAP_RND_BITS_MAX} 이하로 읽지 않는다({where})"
        )
    if report.rnd_bits_above_28 or _above_limit(report.mmap_rnd_bits):
        return (
            f"호스트 vm.mmap_rnd_bits가 {report.mmap_rnd_bits!r}다 - "
            f"{MMAP_RND_BITS_MAX} 이하의 10진 숫자가 아니면 판정이 비결정적이다({where})"
        )
    if report.repeat < 1:
        return f"카나리의 관측 횟수가 {report.repeat}다 - 0회는 관측이 아니다({where})"
    # 선언(`below_measured_repeat`)과 `repeat` 자신을 **둘 다** 본다 — 선언만 보던 판은 손으로
    # `repeat`만 고친 보고서(`repeat: 3`, 선언 `false`)를 통과시켰다. 사유를 **가르는** 것은 문구가
    # 자기모순이 되지 않게서다: 선언이 `true`인데 `repeat >= 10`이면 「20회만 돌렸다 - 10보다
    # 적어」가 된다.
    # ⚠ 둘 다 보고서가 적은 값이다 — `combos[*].exit_codes`의 실제 길이와는 대조하지 않는다
    # (모듈 docstring의 「못 막는 것」).
    if report.below_measured_repeat:
        return (
            f"카나리 보고서가 below_measured_repeat=true라 적었다(repeat={report.repeat}, "
            f"실측 기준 {MEASURED_REPEAT}) - 보고서 스스로 횟수가 모자라다고 한다({where})"
        )
    if report.repeat < MEASURED_REPEAT:
        return (
            f"카나리를 {report.repeat}회만 돌렸다 - 실측한 횟수({MEASURED_REPEAT})보다 적은데 "
            "below_measured_repeat는 false라 적혀 있다. 도구가 쓰는 두 칸은 어긋나지 않는다"
            f"({where})"
        )
    return None


def _above_limit(bits: str) -> bool:
    """보고서의 문자열 값을 **직접** 본다 — `rnd_bits_above_28`이 거짓인데 값이 큰 보고서도 막는다.

    모양이 10진 숫자가 아니면 `True`다(미측정과 같은 결 — 모르면 제출하지 않는다). 🔴 `int()`로
    보면 **모양 검사가 아니다**: `2_8`·`+28`·`٢٨`이 다 28이 되고 `-1`도 「28 이하」로
    통과한다. 커널은 그 모양을 쓰지 않으니 「모르는 값」이다. ⚠ **값의 하한은 안 문다** —
    `"0"`은 모양이 맞아 `False`(통과)다(`_DECIMAL_BITS` 주석).
    """
    if not _DECIMAL_BITS.fullmatch(bits):
        return True
    return int(bits) > MMAP_RND_BITS_MAX
