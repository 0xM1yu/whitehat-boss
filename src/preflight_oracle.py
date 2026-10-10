"""`preflight` — 채점 쪽 점검: 채점기 · API 키 · 카나리 보고서와 현재 호스트 · 번들 (이슈 #534).

`run --oracle-submit`이 제출 전에 거부하는 판정을 **같은 함수로** 재사용한다(`cli`의
`_oracle_server_problem`·`_key_has_bad_chars`·`_origin`·`_inside_task_tree`, `canary_report`의
`canary_refusal`·`_above_limit`, `cybergym_oracle`의 `checksum`·`refused_family_task_id`) — 판정을
복제하면 갈린다. `--oracle-submit`의 전제는 `--task-dir`·`--oracle-server`·`--oracle-canary` 셋이라
채점 run을 점검하는 중(둘 중 하나라도 줬다)이면 빠진 것은 ERROR다(`run`이 exit 2로 거부한다).

⚠ **OK가 보증하지 않는 것.** ① 채점기 TCP는 그 서버가 CyberGym인지·키가 맞는지 모른다. ② 카나리
`boot_id`가 보고서와 같다는 것은 Docker VM 커널이 같은 값이라는 것뿐이다 — 재기동이 VM을
재부팅하는지는 실측 0건이고(`tools/oracle_canary.py` docstring) 값이 같다고 「재기동이 없었다」가
되지도 않는다. 그래서 `vm.mmap_rnd_bits`는 같은 `docker run`에서 **직접** 읽어 28 이하인지 본다 —
그 순간의 값이고 그 뒤의 변경은 못 본다. ③ 번들은 아카이브가 열리는지(첫 멤버)까지만 본다 — 끊긴
gz·크기 상한·안전 필터는 run의 해제가 비로소 본다. ④ 마스킹된 과제 id는 `--mask-map`이 있어야 쓰는데
`preflight`는 그것을 안 받아 WARN으로 센다.
"""

import argparse
import re
import tarfile
import urllib.parse
from collections.abc import Mapping
from pathlib import Path

from src.adapters.canary_report import (
    MMAP_RND_BITS_MAX,
    CanaryReport,
    CanaryReportError,
    _above_limit,
    canary_refusal,
    read_canary_report,
)
from src.adapters.container_tools import DEFAULT_IMAGE
from src.adapters.cybergym_oracle import checksum, refused_family_task_id, unsupported_task_id
from src.cli import (
    _CANARY_COMMAND,
    _ORACLE_KEY_ENV,
    Check,
    Severity,
    _inside_task_tree,
    _key_has_bad_chars,
    _oracle_server_problem,
    _origin,
)
from src.orchestrator.task_loader import (
    REPO_ARCHIVE_NAME,
    SUBMIT_SCRIPT_NAME,
    SubmitMetadata,
    UnexpectedTaskFilesError,
    check_task_dir,
    read_submit_metadata,
)
from src.preflight_probes import (
    DOCKER_TIMEOUT_S,
    Probes,
    docker_argv,
    execute,
    failure,
    guard,
    shown,
    skipped,
)

_BITS_FILE = "/proc/sys/vm/mmap_rnd_bits"
_BOOT_ID_FILE = "/proc/sys/kernel/random/boot_id"
_BITS = re.compile(r"[0-9]+")
_BOOT_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# 과제 id가 가질 만한 모양(`arvo:1065` · 마스킹 id `9c73e92e52b7`) — 이 모양일 때만 그대로 찍는다.
_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}")


# --- 채점기 서버 · 키 -----------------------------------------------------------------------


def check_oracle_server(server: str | None, probes: Probes, *, required: bool) -> Check:
    label = "채점기 서버:"
    if server is None:
        return skipped(label, "--oracle-server", required=required)
    problem = _oracle_server_problem(server)
    if problem is not None:
        return Check(Severity.ERROR, f"{label} --oracle-server {server!r}: {problem}")
    parts = urllib.parse.urlsplit(server)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    why = probes.tcp(parts.hostname or "", port)
    if why:
        return Check(Severity.ERROR, f"{label} {server}에 TCP로 못 닿는다({why}) — 서버를 띄운다")
    return Check(
        Severity.OK, f"{label} {server}에 TCP로 닿는다 — CyberGym 채점기인지·키가 맞는지는 모른다"
    )


def check_api_key(env: Mapping[str, str], oracle_run: bool) -> Check:
    label = f"{_ORACLE_KEY_ENV}:"
    if not oracle_run:
        return Check(
            Severity.WARN,
            f"{label} 건너뜀 — 채점 run이 아니면 필요 없다(--oracle-server·--oracle-canary가 없다)",
        )
    key = env.get(_ORACLE_KEY_ENV, "")
    if not key:
        return Check(
            Severity.ERROR,
            f"{label} 비어 있다 — 채점기의 private 경로(submit-fix·query-poc)에 필요하다",
        )
    if _key_has_bad_chars(key):
        return Check(
            Severity.ERROR,
            f"{label} 공백·비출력 문자가 있다({len(key)}자) — 값은 찍지 않는다. 파일에서 읽었다면 "
            "끝의 개행(CRLF면 CR까지)이 섞인 것이다",
        )
    return Check(Severity.OK, f"{label} 설정됨({len(key)}자) — 값은 찍지 않는다")


# --- 카나리 보고서 · 현재 호스트 ---------------------------------------------------------------


def expected_task(task_dir: Path | None) -> tuple[str | None, str]:
    """카나리 보고서와 견줄 실 과제 id, 못 알면 `(None, 사유)`."""
    if task_dir is None:
        return None, "--task-dir 없음"
    metadata = read_submit_metadata(task_dir)
    if metadata is None:
        return None, "번들에서 과제 id를 못 읽음"
    if unsupported_task_id(metadata.task_id) is not None:
        return None, "번들 id가 마스킹이라 실 id를 모름 — run은 --mask-map이 필요하다"
    return metadata.task_id, ""


def read_host(probes: Probes) -> tuple[str | None, str | None, str]:
    """같은 `docker run` 한 번으로 읽은 현재 Docker VM의 (`vm.mmap_rnd_bits`, `boot_id`, 실패 사유).

    줄 위치가 아니라 **모양**으로 가른다 — 파일 하나를 못 읽어 줄이 밀려도 틀린 칸에 값이 안
    들어간다. 종료 코드는 안 본다(카나리 도구와 같다): 모양에 맞는 줄만 받는다.
    """
    argv = docker_argv("docker", DEFAULT_IMAGE, "cat", _BITS_FILE, _BOOT_ID_FILE)
    done, why = execute(probes, argv, DOCKER_TIMEOUT_S)
    lines = [line.strip() for line in done.stdout.splitlines()] if done is not None else []
    bits = next((line for line in lines if _BITS.fullmatch(line)), None)
    boot_id = next((line for line in lines if _BOOT_ID.fullmatch(line)), None)
    ran = done is not None and done.returncode == 0
    return bits, boot_id, "출력이 모양에 안 맞는다" if ran else failure(done, why)


def _check_bits(bits: str | None, why: str) -> Check:
    label = "카나리 mmap_rnd_bits:"
    if bits is None:
        return Check(
            Severity.WARN,
            f"{label} 확인 못 함 — 지금 Docker VM의 vm.mmap_rnd_bits를 못 읽었다({why}): "
            f"{MMAP_RND_BITS_MAX} 이하인지 모른다",
        )
    if _above_limit(bits):
        return Check(
            Severity.ERROR,
            f"{label} 지금 Docker VM의 vm.mmap_rnd_bits가 {bits}다 — {MMAP_RND_BITS_MAX}보다 크면 "
            "sanitizer 섀도 매핑이 간헐 실패해 무해한 입력도 「크래시」로 집계된다. 보고서가 "
            "통과여도 이 값이면 그 판정은 지금 이 장비의 것이 아니다. 값을 내리고 카나리를 다시 "
            f"돌린다: python {_CANARY_COMMAND}",
        )
    return Check(
        Severity.OK,
        f"{label} 지금 Docker VM의 vm.mmap_rnd_bits가 {bits}다({MMAP_RND_BITS_MAX} 이하) — 이 "
        "순간의 값이다: 이 뒤의 재기동·sysctl 변경은 못 본다",
    )


def _check_boot(recorded: str | None, current: str | None, why: str) -> Check:
    label = "카나리 boot_id:"
    if recorded is None:
        return Check(
            Severity.WARN, f"{label} 확인 못 함 — 보고서에 host_boot_id가 없어 부팅을 못 견준다"
        )
    if current is None:
        return Check(
            Severity.WARN,
            f"{label} 확인 못 함 — 현재 boot_id를 못 읽었다({why}): 보고서가 지금 부팅의 것인지 "
            "모른다",
        )
    if current != recorded:  # 정확한 같음이다 — 앞 몇 자·대소문자·공백을 접지 않는다
        return Check(
            Severity.ERROR,
            f"{label} 지금 Docker VM의 boot_id({current})가 보고서의 것"
            f"({shown(recorded, _BOOT_ID)})과 다르다 — 보고서를 쓴 뒤 VM이 다시 부팅됐거나 다른 "
            "VM의 보고서다. 재부팅이면 vm.mmap_rnd_bits가 되돌아갔을 수 있다. 카나리를 다시 "
            f"돌린다: python {_CANARY_COMMAND}",
        )
    return Check(
        Severity.OK,
        f"{label} 지금 Docker VM의 boot_id가 보고서의 것과 같다({current}) — 같은 부팅이라는 "
        "근거일 뿐이다: 재기동이 VM을 재부팅하는지는 실측 0건이고 값이 같다고 「재기동이 "
        "없었다」가 되지도 않는다",
    )


def check_host_now(report: CanaryReport, probes: Probes) -> list[Check]:
    bits, boot_id, why = read_host(probes)
    return [_check_bits(bits, why), _check_boot(report.host_boot_id, boot_id, why)]


def check_canary(
    path: Path | None, task_dir: Path | None, probes: Probes, *, required: bool
) -> list[Check]:
    label = "카나리:"
    if path is None:
        note = " (--oracle-submit run은 보고서 없이 거부된다)"
        return [skipped(label, "--oracle-canary", note, required=required)]
    if task_dir is not None and _inside_task_tree(
        argparse.Namespace(task_dir=task_dir, task_work_dir=None), Path(path)
    ):  # run이 거부하는 판정 그대로다 — 번들은 신뢰 경계 밖이다
        return [
            Check(
                Severity.ERROR,
                f"{label} --oracle-canary가 과제 트리 안이다({shown(str(path))}) — 과제 번들은 "
                "신뢰 "
                "경계 밖이라 run은 게이트가 읽는 파일을 그 안에 두면 거부한다(exit 2). 번들 밖 "
                "경로로 준다",
            )
        ]
    try:
        report = read_canary_report(Path(path).resolve())
    except CanaryReportError as exc:
        return [Check(Severity.ERROR, f"{label} {exc}")]
    except (OSError, RuntimeError) as exc:  # 경로를 못 푼다(심볼릭 링크 순환 등)
        return [Check(Severity.ERROR, f"{label} 경로를 풀 수 없다({shown(str(path))}): {exc!r}")]
    task_id, why = expected_task(task_dir)
    problem = canary_refusal(report, task_id=task_id or report.task)
    if problem is not None:
        return [Check(Severity.ERROR, f"{label} {problem}")]
    note = "" if task_id else f" · 과제 대조 안 함({why})"
    line = Check(
        # 과제를 못 견준 점검은 OK가 아니다 — run은 번들의 실 id와 견준다
        Severity.OK if task_id else Severity.WARN,
        f"{label} 과제 {shown(report.task, _TASK_ID)} · 판정 {shown(report.verdict)} · "
        f"vm.mmap_rnd_bits {shown(report.mmap_rnd_bits or '')} · {report.repeat}회 · "
        f"{shown(report.measured_at)} 측정{note}",
    )
    return [line, *guard("카나리 호스트:", lambda: check_host_now(report, probes))]


# --- 과제 번들 ------------------------------------------------------------------------------


def _archive_problem(path: Path) -> str | None:
    """아카이브가 열리고 첫 멤버가 읽히나 — 아니면 그 사유. 끝까지 풀어 보지는 않는다."""
    try:
        with tarfile.open(path, "r:*") as tar:
            first = tar.next()
    except (tarfile.TarError, EOFError, OSError) as exc:
        hint = ""
        if isinstance(exc, tarfile.ReadError):  # 비-gzip — LFS 포인터를 그대로 받으면 이 모양이다
            hint = " — HuggingFace LFS 포인터를 `curl -L` 없이 받은 것일 수 있다"
        detail = shown(" ".join(str(exc).split())[:100])
        return f"{REPO_ARCHIVE_NAME}을 읽을 수 없다({type(exc).__name__}: {detail}){hint}"
    return f"{REPO_ARCHIVE_NAME}에 멤버가 없다" if first is None else None


def _identity_problems(metadata: SubmitMetadata, oracle_server: str | None) -> list[str]:
    """`run`이 제출 전에 거부하는 번들 신원의 어긋남 — 같은 함수로 판정한다."""
    problems = []
    refused = refused_family_task_id(metadata.task_id)
    if refused is not None:
        problems.append(refused)
    if checksum(metadata.task_id, metadata.agent_id) != metadata.checksum:
        problems.append(
            "번들의 checksum이 기본 salt로 계산한 값과 다르다 — 다른 salt로 만든 번들은 서버가 "
            "400으로 거부한다"
        )
    if oracle_server is not None and _oracle_server_problem(oracle_server) is None:
        if _origin(metadata.server) != _origin(oracle_server):
            problems.append(
                f"번들(submit.sh)의 서버 주소 {metadata.server!r}가 --oracle-server "
                f"{oracle_server!r}와 다르다 — `localhost`와 `127.0.0.1`도 다른 주소로 센다"
            )
    return problems


def check_bundle(
    task_dir: Path | None, oracle_server: str | None, *, required: bool
) -> list[Check]:
    label = "과제 번들:"
    if task_dir is None:
        return [skipped(label, "--task-dir", required=required)]
    problems: list[str] = []
    try:
        check_task_dir(task_dir)  # 정답 자료가 섞였는지 — `run`이 쓰는 허용 목록 가드 그대로다
    except UnexpectedTaskFilesError as exc:
        problems.append(str(exc))
    except OSError as exc:
        detail = f"{type(exc).__name__}: {exc}"
        return [Check(Severity.ERROR, f"{label} 과제 디렉터리를 읽을 수 없다: {detail}")]
    needed = (REPO_ARCHIVE_NAME, SUBMIT_SCRIPT_NAME)
    missing = [name for name in needed if not (Path(task_dir) / name).is_file()]
    if missing:
        problems.append(f"필수 파일이 없다: {', '.join(missing)}")
    if REPO_ARCHIVE_NAME not in missing and (
        archive := _archive_problem(Path(task_dir) / REPO_ARCHIVE_NAME)
    ):
        problems.append(archive)
    metadata = None
    if SUBMIT_SCRIPT_NAME not in missing:
        metadata = read_submit_metadata(task_dir)
        if metadata is None:
            problems.append(
                f"{SUBMIT_SCRIPT_NAME}에서 채점기 신원을 못 읽었다 — 서버 주소와 metadata"
                "(task_id·agent_id·checksum)가 필요하다"
            )
        else:
            problems += _identity_problems(metadata, oracle_server)
    if problems:
        return [Check(Severity.ERROR, f"{label} {problem}") for problem in problems]
    assert metadata is not None  # problems가 비었으면 신원을 읽었다
    task = shown(metadata.task_id, _TASK_ID)
    if unsupported_task_id(metadata.task_id) is not None:
        return [
            Check(
                Severity.WARN,
                f"{label} 과제 id {task}가 마스킹된 모양이다 — run은 --mask-map 없이 거부한다"
                "(preflight는 --mask-map을 안 받아 그 대조를 못 한다)",
            )
        ]
    return [
        Check(
            Severity.OK,
            f"{label} 허용 파일만 있고 {', '.join(needed)}가 있다(과제 id {task}) · "
            "아카이브가 열리고 "
            "첫 멤버를 읽었다(끝까지 풀지는 않았다) · 번들 checksum이 기본 salt와 맞다",
        )
    ]
