"""`preflight`이 바깥 세계에 닿는 통로와 점검 공용 도우미 (이슈 #534).

접속 도구(ssh·TCP·`docker run`)는 전부 `Probes`로 주입한다 — 기본값이 실제 구현이고 테스트가
가짜를 넣는다. subprocess는 argv 리스트·`shell=False`·timeout이다.

🔴 **`CYBERGYM_API_KEY`는 자식 프로세스에 안 간다.** 자식이 환경을 통째로 물려받으면 사용자의
ssh 설정(`SendEnv`)이 키를 원격까지 실어 보낼 수 있다. 그리고 `Probes`의 `repr`에도 환경을 안
싣는다 — 로그·예외 문구에 환경 전체가 새지 않게. ⚠ 막는 것은 이 모듈이 띄우는 자식과 `repr`
둘이다: 부모 프로세스 자신의 `os.environ`·사용자가 직접 연 터미널은 이 모듈 밖이다.
"""

import os
import re
import socket
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from src.adapters.model_swap import _resolved
from src.cli import _ORACLE_KEY_ENV, Check, Severity

# 정한 값이다(실측 아님) — 응답 없는 대상에 매달리지 않게 하는 상한. docker는 카나리 도구의
# 호스트 읽기(`_HOST_TIMEOUT_S`)와 같다.
TCP_TIMEOUT_S = 3.0
GIT_TIMEOUT_S = 10.0
SSH_TIMEOUT_S = 30.0
SSH_CONNECT_TIMEOUT_S = 10
DOCKER_TIMEOUT_S = 120.0

# 외부에서 온 문자열을 출력에 그대로 싣는 상한 — 넘으면 `repr`로 자른다.
SHOWN_MAX = 60

Runner = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]
TcpProbe = Callable[[str, int], str | None]  # 닿으면 None, 아니면 사유
Resolver = Callable[[tuple[str, ...]], tuple[str, ...]]
Done = subprocess.CompletedProcess[str] | None


def _child_env() -> dict[str, str]:
    """자식에게 줄 환경 — 채점기 API 키만 뺀다. Windows는 이름의 대소문자를 안 가리므로 접는다."""
    return {k: v for k, v in os.environ.items() if k.upper() != _ORACLE_KEY_ENV}


def _run(argv: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    # 바이트로 받아 직접 디코드한다 — `text=True`는 `\r`·`\r\n`을 `\n`으로 접어, 원격 파일 끝의 CR을
    # 지운다(교대 스크립트의 `[ "$got" = "$want" ]`는 CR을 안 지운다 — 점검과 교대가 갈린다).
    done = subprocess.run(
        list(argv),
        capture_output=True,
        stdin=subprocess.DEVNULL,
        env=_child_env(),
        timeout=timeout,
        check=False,
    )
    return subprocess.CompletedProcess(
        done.args,
        done.returncode,
        done.stdout.decode("utf-8", "replace"),
        done.stderr.decode("utf-8", "replace"),
    )


def _tcp(host: str, port: int) -> str | None:
    try:
        with socket.create_connection((host, port), timeout=TCP_TIMEOUT_S):
            return None
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"


@dataclass(frozen=True)
class Probes:
    """점검이 바깥 세계에 닿는 통로. 기본값이 실제 구현이고 테스트가 가짜를 넣는다."""

    run: Runner = _run
    tcp: TcpProbe = _tcp
    resolve: Resolver = _resolved  # 교대 때 `model_swap`이 쓰는 bash 해소 그대로다
    # 키가 든 환경이다 — `repr`에 싣지 않는다(모듈 docstring).
    environ: Mapping[str, str] = field(default_factory=lambda: os.environ, repr=False)


def docker_argv(runtime: str, image: str, *command: str) -> list[str]:
    """컨테이너를 한 번 돌려 읽기만 하는 argv — 네트워크 없음 · 이미지를 안 당김 · 마운트 없음.

    `preflight`의 모든 컨테이너 호출이 이 하나를 지난다(안전 인자를 호출마다 따로 적지 않는다).
    """
    return [runtime, "run", "--rm", "--network=none", "--pull", "never", image, *command]


def execute(probes: Probes, argv: Sequence[str], timeout: float) -> tuple[Done, str]:
    """`(결과, "")` 또는 `(None, 사유)` — 못 띄웠거나 시간이 넘은 것도 사유로 돌려 이어 돈다."""
    try:
        return probes.run(argv, timeout), ""
    except subprocess.TimeoutExpired:
        return None, f"{timeout:g}초 안에 안 끝났다"
    except OSError as exc:
        return None, f"{argv[0]} 실행 실패: {exc}"


def failure(done: Done, why: str) -> str:
    """못 읽은 사유 한 줄 — 못 띄웠으면 그 사유, 아니면 exit 코드와 stderr 끝 두 줄.

    끝을 보는 것은 ssh의 경고(`Permanently added …`)가 앞에 오고 진짜 오류가 뒤에 오기 때문이다.
    """
    if done is None:
        return why
    tail = [line.strip() for line in done.stderr.splitlines() if line.strip()][-2:]
    return (
        f"exit {done.returncode}: {' / '.join(tail)[:200]}" if tail else f"exit {done.returncode}"
    )


def skipped(label: str, flag: str, note: str = "", *, required: bool = False) -> Check:
    """인자가 없어 안 본 점검. 채점 run을 점검하는 중이면(`required`) `run`이 거부하는 조합이다."""
    if required:
        message = f"{label} {flag}가 없다 — 채점 run(--oracle-submit)은 이것 없이 거부된다(exit 2)"
        return Check(Severity.ERROR, message)
    # 인자가 없어 안 본 것은 OK가 아니다 — WARN이라 보고에 남고 `--strict`가 비-0으로 센다.
    return Check(Severity.WARN, f"{label} 건너뜀 — {flag}가 없다{note}")


def guard(label: str, fn: Callable[[], Check | list[Check]]) -> list[Check]:
    """점검 하나가 예외로 죽어도 나머지가 돈다 — 그 죽음은 삼키지 않고 ERROR로 남긴다."""
    try:
        out = fn()
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        message = f"{label} 점검이 예외로 끝났다({detail}) — preflight의 결함이다"
        return [Check(Severity.ERROR, message)]
    return out if isinstance(out, list) else [out]


def shown(value: str, shape: re.Pattern[str] | None = None) -> str:
    """외부에서 온 문자열(원격 파일·번들·보고서)을 출력에 싣는 모양.

    `shape`가 있으면 그것에 fullmatch할 때만 그대로, 없으면 출력 가능한 짧은 문자열일 때만
    그대로다. 아니면 `repr(앞 60자)` — ESC 시퀀스·긴 줄로 터미널을 조작하지 못한다.
    """
    ok = shape.fullmatch(value) if shape else value.isprintable() and len(value) <= SHOWN_MAX
    return value if ok else repr(value[:SHOWN_MAX])


def printable(line: str) -> str:
    """콘솔로 가는 줄의 제어 문자(ESC·개행·CR 등)를 `\\x1b`처럼 눈에 보이는 글자로 바꾼다.

    `shown`이 못 거른 출처(ssh stderr 꼬리·카나리 보고서의 문구)까지 한 곳에서 막는 마지막 문이다.
    """
    return "".join(ch if ch.isprintable() else ascii(ch)[1:-1] for ch in line)
