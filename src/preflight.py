"""`preflight` — 실모델·실도구 run의 선결조건을 **접속해서** 한 번에 점검한다 (이슈 #534).

`doctor`는 엔드포인트에 접속하지 않는 것이 계약이라(`src/cli.py` 모듈 docstring) 이 점검은
거기에 못 둔다 — 여기는 **일부러** ssh·TCP·`docker run`을 쓴다. 선결조건 하나가 비면 run이
0~3단계(실 Docker 도구)를 태운 뒤 4단계 진입에서야 멈추고(대여 비용이 버려진다) 종료 코드는
0이다. 그래서 run 전에 빠진 것을 **전부** 모아 보인다: 한 항목이 실패해도 나머지를 다 돌고
ERROR가 하나라도 있으면 종료 코드 2다.

🔴 **자동화는 `--strict`로 돌린다.** 건너뛰었거나 못 확인한 점검(WARN)은 기본으로는 종료 코드를
안 바꾼다(사람이 읽는 용도) — 그러면 인자를 덜 준 호출이 전부 WARN인 채 exit 0이고 그것이 이슈
#533·#534가 불평한 형태다. `--strict`는 WARN이 하나라도 있으면 `EXIT_UNVERIFIED`로 끝낸다.

구성: 점검 항목은 셋으로 나뉜다 — 서버 쪽(`preflight_remote`: 원격 교대·터널·원격 커밋·교대 없는
프로필의 서빙 포트), 채점 쪽(`preflight_oracle`: 채점기·키·카나리·번들), 그리고 이 파일의 도구
이미지. 바깥 세계는 `preflight_probes.Probes`로 주입한다(기본값이 실제 구현, 테스트는 가짜).

⚠ **OK가 보증하지 않는 것.** 각 점검의 한계는 그 줄이 말한다. 전체로는: ① 포트·터널 OK는 TCP
연결뿐이다. ② 원격 커밋이 같아도 로컬 미커밋 변경은 못 가른다. ③ 채점기 TCP는 CyberGym인지·키가
맞는지 모른다. ④ 카나리 `boot_id`·`vm.mmap_rnd_bits`는 그 순간의 값이다. ⑤ 도구 이미지는 인덱서
한 파일만 견준다(룰셋·gdb.batch·semgrep 버전은 안 본다). ⑥ **`run`의 거부를 전부 되풀이하지
않는다** — 통과해도 run이 거부할 수 있다(`doctor`도 돌린다). ⑦ 이 PC에서 실제 원격 ssh로 끝까지
돌려 본 적은 없다(테스트는 가짜뿐) — 미실측.
"""

import argparse
import hashlib
import re
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from src.adapters.container_tools import _INDEXER, DEFAULT_IMAGE
from src.cli import (
    _ORACLE_KEY_ENV,
    REPO_ROOT,
    Check,
    Severity,
    _check_checksums,
    load_profile,
)
from src.config import CONFIG_DIR, ConfigError, HarnessConfig
from src.preflight_oracle import (
    check_api_key,
    check_bundle,
    check_canary,
    check_oracle_server,
)
from src.preflight_probes import (
    DOCKER_TIMEOUT_S,
    Probes,
    docker_argv,
    execute,
    failure,
    guard,
    printable,
)
from src.preflight_remote import swap_checks

# 종료 코드. `run`·`bench`의 거부(2)와 같은 값이 ERROR다 — `doctor`의 ERROR(1)와 다르다: 파이썬이
# 잡히지 않은 예외로 죽을 때도 1이라 「어긋남을 찾았다」와 「점검이 죽었다」를 자동화가 가른다.
EXIT_ERRORS = 2
# `--strict`에서 ERROR 없이 WARN만 있을 때. 값을 고른 근거(2026-10-07 `git grep`): `src/cli.py`는
# 0·1(doctor ERROR)·2(거부)와 128+신호만 쓰고, 다른 브랜치에서 `run`이 3(장애 중단)을 쓰기로 했다.
# 그 셋과 겹치지 않는 4다 — 3을 쓰면 같은 값이 「run이 중단됨」과 「점검이 못 확인함」 둘을
# 뜻한다. (`tools/`의 스크립트들이 3을 각자의 뜻으로 쓰지만 서로 다른 명령이라 모집단이 다르다.)
EXIT_UNVERIFIED = 4

INDEXER_SOURCE = Path("tools/harness-tools/indexer.py")
_INDEX_VERSION = re.compile(r'^INDEX_VERSION\s*=\s*"([^"]+)"', re.M)
_IMAGE_SHA = re.compile(r"^sha256=([0-9a-f]{64})$", re.M)
# 이미지 안에서 한 번에 읽는다 — `INDEX_VERSION` 줄과 파일 전체의 sha256. 이미지에는 python3이
# 있다(인덱서가 파이썬이라 Dockerfile이 그것으로 연막 검사를 돌린다).
_IMAGE_READ = (
    "import hashlib, sys\n"
    "raw = open(sys.argv[1], 'rb').read()\n"
    "print('sha256=' + hashlib.sha256(raw).hexdigest())\n"
    "for line in raw.decode('utf-8', 'replace').splitlines():\n"
    "    if line.startswith('INDEX_VERSION'):\n"
    "        print(line)\n"
)
_CAVEAT = "run의 거부 전부를 되풀이하지 않는다 — `doctor`도 돌린다"


# --- 도구 이미지의 인덱서 -------------------------------------------------------------------


def _local_index_version(repo_root: Path) -> str | None:
    try:
        text = (repo_root / INDEXER_SOURCE).read_text(encoding="utf-8")
    except OSError:
        return None
    found = _INDEX_VERSION.search(text)
    return found.group(1) if found else None


def _local_indexer_sha256(repo_root: Path) -> str | None:
    try:
        return hashlib.sha256((repo_root / INDEXER_SOURCE).read_bytes()).hexdigest()
    except OSError:
        return None


def _read_image_indexer(
    runtime: str, image: str, probes: Probes
) -> tuple[str | None, str | None, str]:
    """이미지의 `/opt/indexer.py`에서 읽은 (INDEX_VERSION, sha256, 해시를 못 읽은 사유).

    해시까지 읽는 호출이 실패하면(이미지에 python3이 없는 경우 등) 예전처럼 라벨 줄만 `grep`으로
    다시 읽는다 — 점검 수단이 없는 것과 어긋난 것은 다르다. 둘 다 실패하면 `(None, None, 사유)`다.
    """
    done, why = execute(
        probes,
        docker_argv(runtime, image, "python3", "-c", _IMAGE_READ, _INDEXER),
        DOCKER_TIMEOUT_S,
    )
    if done is not None and done.returncode == 0:
        version, sha = _INDEX_VERSION.search(done.stdout), _IMAGE_SHA.search(done.stdout)
        return (
            version.group(1) if version else None,
            sha.group(1) if sha else None,
            "" if sha else "출력에 sha256 줄이 없다",
        )
    sha_why = failure(done, why)
    argv = docker_argv(runtime, image, "grep", "^INDEX_VERSION", _INDEXER)
    done, why = execute(probes, argv, DOCKER_TIMEOUT_S)
    if done is None or done.returncode != 0:
        return None, None, failure(done, why)
    version = _INDEX_VERSION.search(done.stdout)
    return (version.group(1) if version else None), None, sha_why


def _check_tool_image(config: HarnessConfig, repo_root: Path, probes: Probes) -> Check:
    label, image = "도구 이미지:", DEFAULT_IMAGE
    runtime = config.profile.adapters.tool_runner
    if runtime == "fake":
        return Check(Severity.OK, f"{label} 해당 없음 — tool_runner가 Fake라 {image}를 안 쓴다")
    if runtime == "singularity":
        return Check(Severity.WARN, f"{label} 건너뜀 — singularity의 sif는 안 본다")
    ours, ours_sha = _local_index_version(repo_root), _local_indexer_sha256(repo_root)
    if ours is None or ours_sha is None:
        return Check(
            Severity.ERROR, f"{label} {INDEXER_SOURCE.as_posix()}에서 INDEX_VERSION을 못 읽었다"
        )
    theirs, theirs_sha, why = _read_image_indexer(runtime, image, probes)
    if theirs is None:
        return Check(
            Severity.ERROR, f"{label} {image}의 {_INDEXER}에서 INDEX_VERSION을 못 읽었다({why})"
        )
    rebuild = f"{runtime} build -t {image} tools/harness-tools/"
    if theirs != ours:
        return Check(
            Severity.ERROR,
            f"{label} {image}의 인덱서는 {theirs}인데 저장소 코드는 {ours}다 — 낡은 "
            f"이미지는 새 표(writes·includes 등)를 경고 없이 안 붙인다. 재빌드한다: {rebuild}",
        )
    if theirs_sha is None:
        return Check(
            Severity.WARN,
            f"{label} {image}의 INDEX_VERSION은 {ours}로 같지만 라벨만 봤다 — sha256을 못 읽어"
            f"({why}) "
            "같은 라벨 안에서 코드가 바뀐 낡은 이미지는 못 가린다",
        )
    if theirs_sha != ours_sha:
        return Check(
            Severity.ERROR,
            f"{label} {image}의 인덱서가 저장소 코드와 다르다 — 라벨은 같은 {ours}인데 sha256이 "
            f"다르다(이미지 {theirs_sha[:12]}… · 저장소 {ours_sha[:12]}…). 같은 라벨 안에서 코드가 "
            f"바뀐 낡은 이미지다. 재빌드한다: {rebuild}",
        )
    return Check(
        Severity.OK,
        f"{label} {image}의 {_INDEXER}가 저장소 {INDEXER_SOURCE.as_posix()}와 바이트까지 "
        f"같다({ours} · sha256 {ours_sha[:12]}…) — 인덱서 한 파일만 견줬다: 룰셋·gdb.batch·"
        "semgrep 버전은 안 본다",
    )


# --- 조립 ---------------------------------------------------------------------------------


def _checksums(config: HarnessConfig) -> list[Check]:
    """`doctor`가 보는 model checksum 자리표시자 — 그 판정 그대로다(0인 채 돈 run은 비교표 제외)."""
    return [Check(c.severity, f"모델 checksum: {c.message}") for c in _check_checksums(config)]


def preflight(
    profile: str,
    *,
    device: str | None = None,
    task_dir: Path | None = None,
    oracle_server: str | None = None,
    oracle_canary: Path | None = None,
    probes: Probes | None = None,
    config_dir: Path = CONFIG_DIR,
    repo_root: Path = REPO_ROOT,
) -> list[Check]:
    """항목 순서대로 점검 결과를 모은다. 한 항목의 실패가 뒤 항목을 막지 않는다."""
    probes = Probes() if probes is None else probes
    try:
        config = load_profile(profile, config_dir=config_dir)
    except ConfigError as exc:
        return [Check(Severity.ERROR, f"설정 로드 실패: {exc}")]
    # 채점 run을 점검하는 중이다 — `--oracle-submit`은 task-dir·server·canary 셋을 다 요구한다.
    oracle_run = oracle_server is not None or oracle_canary is not None
    runtime = config.profile.adapters.tool_runner
    return [
        Check(
            Severity.OK,
            f"프로필 {profile!r}: 실서빙 역할 {len(config.served_models())}개 · "
            f"교대 {'선언' if config.swap else '없음'} · 도구 런타임 {runtime}",
        ),
        *guard("교대 점검:", lambda: swap_checks(config, device, probes, config_dir, repo_root)),
        *guard("모델 checksum:", lambda: _checksums(config)),
        *guard(
            "채점기 서버:",
            lambda: check_oracle_server(oracle_server, probes, required=oracle_run),
        ),
        *guard(f"{_ORACLE_KEY_ENV}:", lambda: check_api_key(probes.environ, oracle_run)),
        *guard(
            "카나리:",
            lambda: check_canary(oracle_canary, task_dir, probes, required=oracle_run),
        ),
        *guard("과제 번들:", lambda: check_bundle(task_dir, oracle_server, required=oracle_run)),
        *guard("도구 이미지:", lambda: _check_tool_image(config, repo_root, probes)),
    ]


def exit_code(checks: Sequence[Check], *, strict: bool = False) -> int:
    """ERROR가 있으면 `EXIT_ERRORS`. `strict`면 ERROR 없이 WARN만 있어도 `EXIT_UNVERIFIED`."""
    if any(c.severity is Severity.ERROR for c in checks):
        return EXIT_ERRORS
    if strict and any(c.severity is Severity.WARN for c in checks):
        return EXIT_UNVERIFIED
    return 0


def summary(checks: Sequence[Check], *, strict: bool = False) -> Check:
    """끝 줄. 건너뛰었거나 확인 못 한 것(WARN)이 있으면 OK로 끝내지 않는다."""
    n = Counter(c.severity for c in checks)
    counts = f"ERROR {n[Severity.ERROR]}건 · WARN {n[Severity.WARN]}건 · OK {n[Severity.OK]}건"
    if n[Severity.ERROR]:
        return Check(
            Severity.ERROR, f"preflight: {counts} — 위 ERROR를 고치고 다시 돌린다. {_CAVEAT}"
        )
    if n[Severity.WARN]:
        how = (
            f"--strict라 exit {EXIT_UNVERIFIED}로 끝난다"
            if strict
            else f"자동화는 --strict로 돌린다(WARN이 하나라도 있으면 exit {EXIT_UNVERIFIED})"
        )
        return Check(
            Severity.WARN,
            f"preflight: {counts} — WARN은 건너뛰었거나 못 확인한 것이다(OK가 아니다). {how}. "
            f"{_CAVEAT}",
        )
    return Check(Severity.OK, f"preflight: {counts} — {_CAVEAT}")


def run_preflight(args: argparse.Namespace) -> int:
    checks = preflight(
        args.profile,
        device=args.device,
        task_dir=args.task_dir,
        oracle_server=args.oracle_server,
        oracle_canary=args.oracle_canary,
    )
    # `doctor`와 같은 모양으로 stdout에 찍는다. 외부 문자열의 제어 문자는 `printable`이 막는다.
    for check in [*checks, summary(checks, strict=args.strict)]:
        print(printable(check.render()))
    return exit_code(checks, strict=args.strict)
