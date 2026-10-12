"""`preflight` — 서버 쪽 점검: 교대 · 터널 · 원격 커밋 · 가중치 캐시 · 교대 없는 포트 (#534).

두 갈래다. 교대를 선언한 프로필은 장비 파일의 교대 명령이 서버를 띄우므로 그 명령이 닿는
선결조건(환경변수·bash·터널·원격 커밋·가중치 캐시)을 본다. **교대를 선언하지 않은 실서빙 프로필은
run이 서버를 안 띄운다 — 사람이 미리 띄워 둔다.** 그 포트를 TCP로 안 두드리면 서버가 전부 죽어
있어도 점검이 통과한다(리뷰가 `harness_pool_hybrid`로 재현했다).

⚠ 포트·터널 OK는 TCP 연결뿐이다 — ssh 포워드는 원격 서버가 없어도 로컬 포트를 먼저 연다. 어느
모델이 떠 있는지는 모른다(`/v1/models`는 `cli bench`·교대가 본다). 경로로 적힌 교대 실행 파일은
**있다는 것까지**다 — 띄워 보지 않았고 Windows `os.access`는 실행 비트를 안 가른다.
"""

import os
import re
import shlex
import urllib.parse
from collections.abc import Mapping
from pathlib import Path

from src.cli import _SWAP_NEEDS_DEVICE, Check, Severity, _check_serving
from src.config import HarnessConfig, ServingConfig, load_serving
from src.interfaces.model_client import ModelSwapError
from src.preflight_probes import (
    GIT_TIMEOUT_S,
    SSH_CONNECT_TIMEOUT_S,
    SSH_TIMEOUT_S,
    Probes,
    execute,
    failure,
    guard,
    shown,
)

# 장비 파일의 `command`가 이 래퍼를 부를 때만 환경변수·터널·원격 커밋·가중치 캐시가 걸린다.
REMOTE_WRAPPER = "tools/serve/swap-remote.sh"
# 그 스크립트의 `need` 호출이 요구하는 변수 — 사람이 세우는 셋과 하네스가 run마다 주는 하나
# (`model_swap.swap_runner`가 env에 넣는다). 스크립트의 `need` 줄과 같은지 테스트가 잠근다.
SWAP_ENV_OPERATOR = ("SWAP_SSH_HOST", "SWAP_REMOTE_ROOT", "SWAP_REMOTE_PYTHON")
SWAP_ENV_HARNESS = ("HARNESS_RUN_ID",)

_MSYS_DRIVE = re.compile(r"[A-Za-z]:[\\/]")
# 원격 `.harness-commit`·로컬 `git rev-parse HEAD`가 가져야 할 모양 — 이 모양일 때만 그대로 찍는다.
_COMMIT = re.compile(r"[0-9a-f]{40}")
# registry `source`의 모양 — `tools/serve/prefetch_weights.py`의 `_HF`와 같다(셸 전에 거른다).
_HF_SOURCE = re.compile(r"hf://(?P<repo>[\w.-]+/[\w.-]+)@(?P<rev>[0-9a-f]{40})")


def _clean_absolute(root: str) -> bool:
    """`swap-remote.sh`의 `case … in /*[!/])` — 절대경로이고 `/`로 끝나지 않는다."""
    return root.startswith("/") and len(root) > 1 and not root.endswith("/")


def check_swap_env(env: Mapping[str, str]) -> list[Check]:
    label = "교대 환경변수:"
    missing = [name for name in SWAP_ENV_OPERATOR if not env.get(name)]
    checks = []
    if missing:
        checks.append(
            Check(
                Severity.ERROR,
                f"{label} {', '.join(missing)}이(가) 비었다 — swap-remote.sh가 ssh 전에 exit 2로 "
                "멈추고 하네스는 그것을 교대를 시도할 때(0~3단계 뒤) 받는다",
            )
        )
    root = env.get("SWAP_REMOTE_ROOT", "")
    if root and not _clean_absolute(root):
        hint = ""
        if _MSYS_DRIVE.match(root):
            hint = (
                " — Git Bash의 export가 `/`로 시작하는 값을 Windows 경로로 바꾼 것이다: "
                r"PowerShell `$env:`로 세우고 `Git\usr\bin`을 PATH 앞에 둔다"
                '(Git Bash면 `MSYS2_ENV_CONV_EXCL="SWAP_REMOTE_ROOT;SWAP_REMOTE_PYTHON"` — '
                "SWAP_REMOTE_PYTHON도 같이 바뀌는데 이 점검은 그것을 안 본다) — "
                "docs/portability.md 「Windows에서 원격 교대 run 절차」"
            )
        checks.append(
            Check(
                Severity.ERROR,
                f"{label} SWAP_REMOTE_ROOT가 절대경로가 아니거나 /로 끝난다({shown(root)}) — "
                f"/root/harness처럼 적는다{hint}",
            )
        )
    if not checks:
        checks.append(
            Check(
                Severity.OK,
                f"{label} {', '.join(SWAP_ENV_OPERATOR)} 설정됨 — "
                f"{', '.join(SWAP_ENV_HARNESS)}는 하네스가 run마다 준다(점검 대상이 아니다)",
            )
        )
    return checks


def _check_path_head(label: str, head: str, repo_root: Path) -> Check:
    """경로로 적힌 교대 실행 파일 — 그 파일이 **있나**.

    `_resolved`는 경로가 든 이름을 `which`에 안 넘기고 그대로 돌려주므로(그쪽 docstring), 경로라는
    것만 보고 OK를 내면 **없는 경로도 통과한다** — 교대는 그때야 `subprocess`의 `OSError`를
    `ModelSwapError("교대 명령을 실행하지 못했다")`로 바꿔 0~3단계를 태운 뒤 멈춘다(리뷰가 재현).

    상대경로는 **교대가 명령을 띄우는 작업 디렉터리** 기준이다 — `swap_runner`의 `cwd`(저장소
    루트)이고 preflight의 cwd가 아니다. ⚠ 실행 가능성은 `os.access`가 보는 만큼이다: Windows는
    그 함수가 실행 비트를 안 가르므로 있다는 것까지다(그래서 OK 문구가 「띄워 보지는 않았다」다).
    """
    target = repo_root / head  # head가 절대경로면 pathlib이 repo_root를 버린다
    # 장비 파일(추적 파일)의 값이라 `shown`으로 **안 자른다** — 고치려면 경로를 그대로 봐야 하고,
    # 60자 상한에 걸리면 `repr(앞 60자)`가 되어 고칠 수 없는 문구가 된다(그 꼴을 한 번 냈다).
    # 제어 문자는 출력 직전 `printable`이 막는다.
    where = str(target) if str(target) == head else f"{head} → {target}"
    try:
        found = target.is_file()
    except OSError as exc:  # NUL이 든 경로 · 너무 긴 경로
        return Check(Severity.ERROR, f"{label} 경로를 못 봤다({where}): {exc}")
    if not found:
        return Check(
            Severity.ERROR,
            f"{label} 교대 실행 파일이 없다(또는 파일이 아니다): {where} — 장비 파일 command[0]을 "
            "고친다(교대는 이것을 0~3단계를 태운 뒤 「교대 명령을 실행하지 못했다」로 받는다)",
        )
    if not os.access(target, os.X_OK):
        return Check(Severity.ERROR, f"{label} 실행 권한이 없다({where}) — chmod +x한다")
    return Check(
        Severity.OK,
        f"{label} 교대 명령이 경로로 적혀 있다({where}) — PATH로 안 푼다. "
        "그 파일이 있다(띄워 보지는 않았다)",
    )


def check_bash(command: tuple[str, ...], probes: Probes, repo_root: Path) -> Check:
    label, head = "bash 해소:", command[0]
    try:
        resolved = probes.resolve((head,))[0]
    except ModelSwapError as exc:  # System32의 WSL 런처 — 문구가 고치는 법까지 든다
        return Check(Severity.ERROR, f"{label} {exc}")
    if resolved != head:
        # `shutil.which`가 고른 것이다 — 그 함수가 실재·실행 가능까지 보고 고른다.
        return Check(Severity.OK, f"{label} {head} → {resolved}")
    if os.sep in head or (os.altsep and os.altsep in head):
        return _check_path_head(label, head, repo_root)
    return Check(
        Severity.WARN,
        f"{label} {head}를 PATH에서 못 찾았다 — 이름만으로는 못 막는다: Windows "
        "`CreateProcess`는 PATH에 System32가 없어도 먼저 뒤져 WSL 런처를 고를 수 있다",
    )


# --- 엔드포인트 ---------------------------------------------------------------------------


def _host_port(endpoint: str) -> tuple[str, int] | None:
    """`http(s)://호스트[:포트]/…` → (호스트, 포트). 포트가 없으면 스킴의 기본 포트다."""
    try:
        parts = urllib.parse.urlsplit(endpoint)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return parts.hostname, port or (443 if parts.scheme == "https" else 80)


def _endpoints(
    config: HarnessConfig, keys: tuple[str, ...]
) -> tuple[tuple[tuple[str, int], ...], tuple[str, ...]]:
    """registry 키들의 (호스트, 포트) 집합(포트 순)과, 주소를 못 읽은 키."""
    found: set[tuple[str, int]] = set()
    unreadable: list[str] = []
    for key in dict.fromkeys(keys):
        endpoint = _host_port(config.models[key].endpoint)
        if endpoint is None:
            unreadable.append(key)
        else:
            found.add(endpoint)
    return tuple(sorted(found, key=lambda e: (e[1], e[0]))), tuple(unreadable)


def tunnel_endpoints(
    config: HarnessConfig, serving: ServingConfig
) -> tuple[tuple[tuple[str, int], ...], tuple[str, ...]]:
    """장비 파일의 서버가 registry에서 받는 (호스트, 포트)와 **주소를 못 읽은 키**.

    `tools/serve/vast-tunnel.sh`의 `PORTS`가 같은 집합이어야 한다(테스트가 `PORTS=(…)` 한 줄의
    모양만 읽어 견준다 — 스크립트의 다른 변경은 못 본다).

    못 읽은 키를 **같이** 돌려주는 이유: 종전에는 `_endpoints`의 첫 값만 취해 그 키를 버렸고,
    그러면 주소가 깨진 모델의 포트 점검이 조용히 사라졌다(리뷰가 재현했다 — 교대 없는 갈래는
    같은 목록을 이미 ERROR로 세고 있었다).
    """
    return _endpoints(config, tuple(serving.servers))


def _closed(endpoints: tuple[tuple[str, int], ...], probes: Probes) -> dict[str, str]:
    return {f"{h}:{p}": why for h, p in endpoints if (why := probes.tcp(h, p))}


def _rejected(
    label: str, endpoints: tuple[tuple[str, int], ...], unreadable: tuple[str, ...]
) -> Check | None:
    """두드려 보기 **전에** 걸리는 것 둘 — 교대 갈래·교대 없는 갈래가 같이 지난다.

    둘 다 「빈 성공」이 되는 자리다: 주소를 못 읽은 키를 버리면 그 모델의 포트가 점검에서 사라지고,
    남은 집합이 비면 닫힌 포트가 없으니 OK가 난다. 하네스는 같은 주소로 모델을 부르므로 run은
    0~3단계를 태운 뒤 같은 이유로 실패한다.
    """
    if unreadable:
        return Check(
            Severity.ERROR,
            f"{label} http(s) 주소가 아니라 포트를 못 읽은 모델: {', '.join(unreadable)} — "
            "registry endpoint를 고친다(하네스도 같은 주소로 그 모델을 부른다)",
        )
    if not endpoints:
        return Check(
            Severity.ERROR,
            f"{label} 점검할 포트가 하나도 없다 — registry 주소를 주는 모델이 없다(빈 집합은 "
            "「전부 닿는다」가 아니다)",
        )
    return None


def check_tunnel(config: HarnessConfig, serving: ServingConfig, probes: Probes) -> Check:
    label = "터널 포트:"
    endpoints, unreadable = tunnel_endpoints(config, serving)
    if (bad := _rejected(label, endpoints, unreadable)) is not None:
        return bad
    closed = _closed(endpoints, probes)
    if closed:
        listed = ", ".join(f"{addr}({why})" for addr, why in closed.items())
        return Check(
            Severity.ERROR,
            f"{label} 닫혀 있다: {listed} — 별도 터미널에서 tools/serve/vast-tunnel.sh를 띄워 "
            "둔다(교대 뒤 준비 2차 확인과 모델 호출이 이 포트를 지난다)",
        )
    ports = "·".join(str(p) for _, p in endpoints)
    return Check(
        Severity.OK,
        f"{label} 127.0.0.1:{ports}에 TCP로 닿는다 — 터널이 떠 있다는 뜻일 뿐 원격 서버가 "
        "떴는지는 교대 뒤 `/v1/models`가 본다",
    )


def check_served(config: HarnessConfig, probes: Probes) -> Check:
    """교대 없는 실서빙 프로필 — 역할이 쓰는 서버 포트를 TCP로 두드린다.

    ERROR인 이유: 이 프로필은 서버를 띄우는 주체가 없다(교대 선언 없음) — 사람이 미리 띄워 두는
    것이 전제다. 안 띄웠으면 run은 0~3단계(실 Docker 도구)를 태운 뒤 첫 모델 호출(4단계)에서야
    그것을 안다. 사람이 띄우는 서버라 「아직 안 띄웠다」가 실제로 흔한 상태이고, 그 상태를 WARN으로
    두면 run을 시작하라는 신호로 읽힌다.
    """
    label = "서빙 엔드포인트:"
    keys = tuple(config.served_models().values())
    endpoints, unreadable = _endpoints(config, keys)
    if (bad := _rejected(label, endpoints, unreadable)) is not None:
        return bad
    closed = _closed(endpoints, probes)
    if closed:
        listed = ", ".join(f"{addr}({why})" for addr, why in closed.items())
        return Check(
            Severity.ERROR,
            f"{label} 닫혀 있다: {listed} — 이 프로필은 교대를 선언하지 않아 run이 서버를 안 띄운다"
            "(사람이 미리 띄운다). 안 띄운 채 run하면 첫 모델 호출(4단계)에서야 실패한다",
        )
    addrs = ", ".join(f"{h}:{p}" for h, p in endpoints)
    return Check(
        Severity.OK,
        f"{label} {len(endpoints)}개({addrs})에 TCP로 닿는다 — 포트가 열려 있다는 뜻일 뿐 어느 "
        "모델이 떠 있는지·registry 이름과 맞는지는 모른다(`cli bench`가 `/v1/models`를 본다)",
    )


# --- 원격 사본의 커밋 -----------------------------------------------------------------------


def _like_command_substitution(text: str) -> str:
    """셸 `$(…)`가 하는 대로 끝 개행만 뗀다 — `swap-remote.sh`의 `got=$(cat …)`와 같은 값이다."""
    return text.rstrip("\n")


def _ssh_options(env: Mapping[str, str]) -> list[str]:
    # `swap-remote.sh`와 같은 옵션이다 — 암호를 안 묻고(BatchMode) 호스트는 `--` 뒤라 옵션이 아니다.
    options = ["-n", "-T", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={SSH_CONNECT_TIMEOUT_S}"]
    if env.get("SWAP_SSH_PORT"):
        options += ["-p", env["SWAP_SSH_PORT"]]
    return options


def check_remote_commit(env: Mapping[str, str], repo_root: Path, probes: Probes) -> Check:
    label = "원격 커밋:"
    host, root = env.get("SWAP_SSH_HOST", ""), env.get("SWAP_REMOTE_ROOT", "")
    if not host or not _clean_absolute(root):
        return Check(
            Severity.WARN, f"{label} 건너뜀 — ssh로 갈 곳을 모른다(위 환경변수 점검이 실패했다)"
        )
    argv = ["git", "-C", str(repo_root), "rev-parse", "HEAD"]
    local, why = execute(probes, argv, GIT_TIMEOUT_S)
    if local is None or local.returncode != 0:
        return Check(Severity.ERROR, f"{label} 로컬 커밋을 못 읽었다({failure(local, why)})")
    want = _like_command_substitution(local.stdout)
    if not _COMMIT.fullmatch(want):
        return Check(
            Severity.ERROR,
            f"{label} 로컬 커밋이 40자 hex가 아니다({shown(want)}) — 비교하지 않는다",
        )
    options = _ssh_options(env)
    remote = f"cd {shlex.quote(root)} && cat .harness-commit"
    done, why = execute(probes, ["ssh", *options, "--", host, remote], SSH_TIMEOUT_S)
    if done is None or done.returncode != 0:
        return Check(
            Severity.ERROR,
            f"{label} ssh로 원격 .harness-commit을 못 읽었다({failure(done, why)}) — 호스트 접속·"
            "SWAP_REMOTE_ROOT·원격 사본(tools/serve/vast-bootstrap.sh)을 본다",
        )
    got = _like_command_substitution(done.stdout)
    # 정확한 같음이다 — 스크립트의 `[ "$got" = "$want" ]`가 공백 하나도 안 봐준다.
    if got != want:
        return Check(
            Severity.ERROR,
            f"{label} 원격 커밋({shown(got, _COMMIT) if got else '없음'})이 로컬({want})과 "
            "다르다 — "
            "tools/serve/vast-bootstrap.sh를 다시 돌린다",
        )
    return Check(
        Severity.OK, f"{label} 원격 사본이 로컬 HEAD({want[:12]})와 같다 — 미커밋 변경은 못 가른다"
    )


# --- 원격 가중치 캐시 -------------------------------------------------------------------------


def _hf_pairs(
    config: HarnessConfig, serving: ServingConfig
) -> tuple[dict[tuple[str, str], list[str]], list[str]]:
    """장비 파일이 서빙하는 모델의 `(repo, rev)` → model key들, 그리고 rev를 못 읽은 `hf://` 좌석.

    `hf://`인데 `@<40hex>`가 아닌 좌석을 버리면 그 좌석이 점검에서 조용히 사라진다.
    """
    pairs: dict[tuple[str, str], list[str]] = {}
    unpinned: list[str] = []
    for key in serving.servers:
        source = config.models[key].source or ""
        found = _HF_SOURCE.fullmatch(source)
        if found:
            pairs.setdefault((found["repo"], found["rev"]), []).append(key)
        elif source.startswith("hf://"):
            unpinned.append(key)
    return pairs, unpinned


def check_weights_cached(
    config: HarnessConfig, serving: ServingConfig, env: Mapping[str, str], probes: Probes
) -> Check:
    label = "가중치 캐시:"
    pairs, unpinned = _hf_pairs(config, serving)
    check = _check_pinned(pairs, env, probes) if pairs else None
    if not unpinned:
        return check or Check(Severity.OK, f"{label} 해당 없음 — 서빙 모델 중 hf:// 소스가 없다")
    note = (
        f"rev가 없어 캐시를 확인할 수 없다: {', '.join(unpinned)} — swap.sh가 기동 때 죽는 자리다"
        "(registry source를 hf://<repo>@<40자 rev>로 고친다)"
    )
    if check is None:
        return Check(Severity.WARN, f"{label} {note}")
    return Check(Severity.WARN, f"{check.message} / {note}")


def _check_pinned(
    pairs: dict[tuple[str, str], list[str]], env: Mapping[str, str], probes: Probes
) -> Check:
    """각 좌석의 고정 revision이 원격 HF 캐시에 있나 — **WARN만**, 원격에는 읽기뿐이다 (이슈 #571).

    `ready_timeout_s`는 가중치가 캐시돼 있다는 전제다(장비 파일 주석). 선취(`prefetch-weights.sh`)를
    사람이 빠뜨리면 콜드 기동이 상한에 걸려 run이 7단계에서 죽은 적이 있다. 경로는 `swap.sh`의
    `HF_HUB`와 같다. ⚠ `snapshots/<rev>` 디렉터리가 있고 `blobs/*.incomplete`가 없다는 것까지다 —
    파일 개수·크기는 안 본다.
    """
    label, prefetch = "가중치 캐시:", "bash tools/serve/prefetch-weights.sh"
    host = env.get("SWAP_SSH_HOST", "")
    if not host or not _clean_absolute(env.get("SWAP_REMOTE_ROOT", "")):
        return Check(
            Severity.WARN, f"{label} 확인 불가 — ssh로 갈 곳을 모른다(위 환경변수 점검이 실패했다)"
        )
    hub = "${HF_HUB_CACHE:-${HF_HOME:-$HOME/.cache/huggingface}/hub}"
    tests = [
        f'[ -d "$h/models--{repo.replace("/", "--")}/snapshots/{rev}" ] && '
        f'! ls "$h/models--{repo.replace("/", "--")}/blobs"/*.incomplete >/dev/null 2>&1 && '
        f'echo "have {repo}@{rev}"'
        for repo, rev in pairs  # repo·rev는 `_HF_SOURCE`가 거른 값이라 인용 없이 셸에 넣어도 된다
    ]
    remote = f"h={hub}; {'; '.join(tests)}; true"
    done, why = execute(probes, ["ssh", *_ssh_options(env), "--", host, remote], SSH_TIMEOUT_S)
    if done is None or done.returncode != 0:
        return Check(Severity.WARN, f"{label} 확인 불가 — ssh가 안 됐다({failure(done, why)})")
    have = set(done.stdout.splitlines())
    missing = [
        f"{'·'.join(keys)}({rev[:12]})"
        for (repo, rev), keys in pairs.items()
        if f"have {repo}@{rev}" not in have
    ]
    if missing:
        return Check(
            Severity.WARN,
            f"{label} 원격 HF 캐시에 없거나 미완이다: {', '.join(missing)} — 콜드 기동은 장비 "
            f"파일의 ready_timeout_s를 넘길 수 있다(run이 교대에서 죽는다). 먼저 선취한다: "
            f"SWAP_SSH_HOST·SWAP_SSH_PORT를 같게 두고 `{prefetch}`",
        )
    return Check(
        Severity.OK,
        f"{label} 서빙 모델 {len(pairs)}개의 revision이 원격 HF 캐시에 있다 — 디렉터리가 있고 "
        "미완 blob이 없다는 것까지다(크기·무결성은 안 봤다)",
    )


# --- 조립 ---------------------------------------------------------------------------------


def uses_remote_wrapper(command: tuple[str, ...], repo_root: Path) -> bool:
    """`command`의 인자 중에 `REMOTE_WRAPPER`와 **같은 파일**이 있나.

    철자가 아니라 파일로 견준다 — `./tools/serve/swap-remote.sh`·`tools/serve//swap-remote.sh`·
    대소문자만 다른 경로도 같은 파일이다. 상대경로는 하네스가 교대를 부르는 자리(저장소 루트)
    기준이다. ⚠ 래퍼를 다른 스크립트·`bash -c`가 감싸면 못 알아본다.
    """
    wrapper = repo_root / REMOTE_WRAPPER
    for arg in command:
        try:
            if os.path.samefile(repo_root / arg, wrapper):
                return True
        except (OSError, ValueError):  # 없는 경로·NUL이 든 인자 — 래퍼가 아니다
            continue
    return False


def swap_checks(
    config: HarnessConfig, device: str | None, probes: Probes, config_dir: Path, repo_root: Path
) -> list[Check]:
    label = "교대 점검:"
    if config.swap is None:
        if not config.served_models():
            return [Check(Severity.OK, f"{label} 해당 없음 — 실서빙 역할이 없다")]
        return [
            Check(
                Severity.OK,
                f"{label} 해당 없음 — 프로필이 교대를 선언하지 않았다(서버는 사람이 미리 띄운다)",
            ),
            *guard("서빙 엔드포인트:", lambda: check_served(config, probes)),
        ]
    if device is None:
        return [Check(Severity.ERROR, f"{label} {_SWAP_NEEDS_DEVICE}")]
    found = _check_serving(config, device, config_dir)
    if any(c.severity is Severity.ERROR for c in found):
        return [*found, Check(Severity.WARN, f"{label} 건너뜀 — 장비 파일을 못 믿는다(위 ERROR)")]
    serving, env = load_serving(device, config_dir=config_dir), probes.environ
    bash = guard("bash 해소:", lambda: check_bash(serving.command, probes, repo_root))
    if not uses_remote_wrapper(serving.command, repo_root):
        local_only = Check(
            Severity.OK,
            "교대 환경변수·터널·원격 커밋: 해당 없음 — 이 장비의 교대 명령 인자 중에 "
            f"{REMOTE_WRAPPER}와 같은 파일이 없다(서버는 교대 뒤에 뜬다. 다른 스크립트가 그것을 "
            "감싸면 이 판정은 못 알아본다)",
        )
        return [*found, local_only, *bash]
    return [
        *found,
        *guard("교대 환경변수:", lambda: check_swap_env(env)),
        *bash,
        *guard("터널 포트:", lambda: check_tunnel(config, serving, probes)),
        *guard("원격 커밋:", lambda: check_remote_commit(env, repo_root, probes)),
        *guard("가중치 캐시:", lambda: check_weights_cached(config, serving, env, probes)),
    ]
