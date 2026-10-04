"""카나리 보고서를 읽어 **채점 run을 거부할 사유**를 낸다 (이슈 #383).

보고서를 만드는 쪽은 `tools/oracle_canary.py`다 — 과제의 저자 참조 PoC와 무해 입력을
`-vul`·`-fix` 이미지에서 N회 돌려 네 조합이 기대와 같은지 본다. 이 모듈은 **그 JSON만 읽는다**:
도커를 돌리지 않고, 이미지를 당기지 않고, 참조 PoC 바이트를 보지 않는다(보고서에 없다 —
길이 `reference_poc_len`뿐이고 이 모듈은 그것조차 안 읽는다).

**왜 하네스가 보고서를 요구하나.** 오라클 판정이 비결정적일 수 있고 그 원인은 우리 코드도 저자
코드도 아니라 **채점 컨테이너를 돌리는 호스트 커널의 mmap 엔트로피**다. `vm.mmap_rnd_bits = 32`
에서 sanitizer 섀도 매핑이 간헐 실패해 타깃이 입력을 읽기도 전에 `139`로 죽고, 그 코드가 성공
조건에서 「크래시」로 집계된다 — 2026-10-02 `win11wsl` 실측에서 **정답 PoC ≈0.40 · 무해 입력
≈0.28**이 「성공」이었다(차가 ≈0.12). 28로 내리면 같은 네 조합이 40/40 기대와 같다.
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

⚠ **그리고 하네스가 카나리를 스스로 돌리지는 않는다.** 이슈 #383의 선택지 (a)는 그것까지인데,
돌리려면 run이 `-fix` 이미지와 참조 PoC를 손에 쥐어야 하고 N·보고서 위치·`weak_pass` 처리가 전부
정해야 하는 값이다. 그 결정은 사람 몫으로 남겼다 — 이 모듈은 「카나리 없이 채점하지 않는다」까지다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# 보고서가 통과로 세는 유일한 판정. 도구는 넷을 낸다 — `pass` · `weak_pass`(관측 횟수가 모자라
# `ok=false`) · `fail` · `incomplete`. 채점 run은 `pass`만 받는다.
PASS_VERDICT = "pass"

# 이 값보다 큰 `vm.mmap_rnd_bits`는 거부한다. 도구의 `MMAP_RND_BITS_MAX`와 같은 수이고 보고서가
# 40/40을 본 값이다. 🔴 **도구를 import해서 가져오지 않는다** — `src/`가 `tools/`를 import하는
# 경로가 저장소에 0건이고, 그 방향을 이 결함 하나로 열지 않는다. 두 리터럴이 갈리면 테스트가 잡는다.
MMAP_RND_BITS_MAX = 28

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
    # 어느 장비에서 쟀는지 — 거절 문구에만 쓴다. 도구가 못 읽으면 `null`이라 없어도 된다.
    docker_version: str | None = None


def read_canary_report(path: Path) -> CanaryReport:
    """보고서 JSON을 읽어 옮긴다. 못 읽거나 칸이 비거나 형이 다르면 `CanaryReportError`."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CanaryReportError(f"카나리 보고서를 읽지 못했다({path}): {exc}") from exc
    try:
        body = json.loads(text)
    except ValueError as exc:
        raise CanaryReportError(f"카나리 보고서가 JSON이 아니다({path}): {exc}") from exc
    if not isinstance(body, dict):
        raise CanaryReportError(f"카나리 보고서가 JSON 객체가 아니다({path})")
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
        docker_version=body.get("docker_version") or None,
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
            f"카나리 판정이 {report.verdict}(ok={str(report.ok).lower()})다 - "
            f"채점 run은 {PASS_VERDICT}만 받는다({where})"
        )
    if report.rnd_bits_unmeasured or report.mmap_rnd_bits is None:
        return (
            f"카나리가 호스트 vm.mmap_rnd_bits를 못 읽었다 - 미측정을 "
            f"{MMAP_RND_BITS_MAX} 이하로 읽지 않는다({where})"
        )
    if report.rnd_bits_above_28 or _above_limit(report.mmap_rnd_bits):
        return (
            f"호스트 vm.mmap_rnd_bits가 {report.mmap_rnd_bits}다 - "
            f"{MMAP_RND_BITS_MAX}보다 크면 판정이 비결정적이다({where})"
        )
    if report.below_measured_repeat:
        return f"카나리를 {report.repeat}회만 돌렸다 - 실측한 횟수보다 적어 판정이 약하다({where})"
    return None


def _above_limit(bits: str) -> bool:
    """보고서의 문자열 값을 **직접** 본다 — `rnd_bits_above_28`이 거짓인데 값이 큰 보고서도 막는다.

    정수로 못 읽으면 `True`다(미측정과 같은 결 — 모르면 제출하지 않는다).
    """
    try:
        return int(bits) > MMAP_RND_BITS_MAX
    except ValueError:
        return True
