"""CyberGym Level 1 과제 번들을 받는다 — 어느 장비에서든 같은 바이트로.

    python tools/cybergym_task.py arvo:1065 --out eval/datasets/arvo-1065

HF 데이터셋 `sunblaze-ucb/cybergym`(공개·무인증)에서 **두 파일만** 받는다 —
`repo-vul.tar.gz`·`description.txt`. 같은 폴더의 `error.txt`·`patch.diff`·`repo-fix.tar.gz`는
정답 자료라 **요청하지 않는다**(Level 1 · `src/orchestrator/task_loader.py` `check_task_dir`).
revision은 커밋으로 고정한다(`--revision`으로만 바꾼다). 받은 바이트는 같은 revision의 트리 API가
주는 `lfs.oid`(sha256)·`size`와 대조하고, 다르면 파일을 남기지 않는다.

`submit.sh`(채점기 신원)도 만든다 — run마다 새 agent_id가 권장이라(채점기가 같은 신원의 PoC를
캐시하지 않게) 이미 있으면 그대로 두고 `--force-submit`일 때만 새로 발급한다.

종료 코드: 0 성공 · 1 다운로드·검증 실패 · 2 인자·입력 거절(과제 id·`--out`에 남의 파일).
"""

import argparse
import hashlib
import http.client
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

# 경로 실행(`python tools/cybergym_task.py`)은 sys.path[0]이 tools/라 `src`를 못 찾는다(#616).
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.adapters.cybergym_oracle import checksum  # noqa: E402
from src.console import force_utf8  # noqa: E402
from src.orchestrator.task_loader import (  # noqa: E402
    DESCRIPTION_FILENAME,
    REPO_ARCHIVE_NAME,
    SUBMIT_SCRIPT_NAME,
    check_task_dir,
    read_submit_metadata,
)

HF_REPO = "sunblaze-ucb/cybergym"
# 2026-10-15 main — docs/open-questions/D/D97.md 관측 2가 쓴 revision과 같은 커밋.
DEFAULT_REVISION = "bde190ded494e52bc684b66073b436c9d992c7c6"
# 받는 것은 이 둘뿐이다. 정답 자료 이름은 어디에도 두지 않는다.
DATA_FILES = (REPO_ARCHIVE_NAME, DESCRIPTION_FILENAME)
DEFAULT_SERVER = "http://127.0.0.1:8666"

_TASK_ID = re.compile(r"(arvo|oss-fuzz):[0-9]+")
_REVISION = re.compile(r"[0-9a-f]{40}")
_SERVER = re.compile(r"https?://[A-Za-z0-9._:\[\]-]+")

TIMEOUT_S = 60
ATTEMPTS = 3
RETRY_DELAY_S = 2.0
_CHUNK = 1 << 20


class InputRejected(Exception):
    """인자·입력 거절 — 종료 코드 2."""


class Failed(Exception):
    """다운로드·검증 실패 — 종료 코드 1."""


class Mismatch(Failed):
    """응답이 트리 API와 다르다(크기 초과·해시 불일치) — 응답이 결정적이라 다시 걸지 않는다."""


def _open(url: str):
    return urllib.request.urlopen(url, timeout=TIMEOUT_S)  # noqa: S310 — https 고정 URL만 만든다


def _retrying(action, what: str):
    """HF 일시 오류(5xx·429·연결·중간 끊김)만 짧게 다시 건다. 4xx·`Mismatch`는 바로 실패."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return action()
        except Mismatch:
            raise
        except urllib.error.HTTPError as error:
            if error.code < 500 and error.code != 429:
                raise Failed(f"{what}: HTTP {error.code}") from error
            last = f"HTTP {error.code}"
        except (OSError, http.client.HTTPException) as error:
            last = str(error) or type(error).__name__
        if attempt < ATTEMPTS:
            time.sleep(RETRY_DELAY_S)
    raise Failed(f"{what}: {ATTEMPTS}번 시도해도 실패했다 — {last}")


def parse_task_id(task: str) -> tuple[str, str]:
    """`arvo:1065` → `("arvo", "1065")`. 다른 모양은 거절(경로에 들어가므로 숫자만 받는다)."""
    if not _TASK_ID.fullmatch(task):
        raise InputRejected(f"과제 id는 `arvo:<숫자>` 또는 `oss-fuzz:<숫자>`다: {task!r}")
    family, number = task.split(":")
    return family, number


def _base(family: str, number: str, revision: str, kind: str) -> str:
    if kind == "tree":
        return (
            f"https://huggingface.co/api/datasets/{HF_REPO}/tree/{revision}/data/{family}/{number}"
        )
    return f"https://huggingface.co/datasets/{HF_REPO}/resolve/{revision}/data/{family}/{number}"


def expected_files(family: str, number: str, revision: str) -> dict[str, tuple[str, int]]:
    """트리 API에서 받을 두 파일의 `(sha256, size)`. 정답 자료 항목은 읽고도 버린다."""
    url = _base(family, number, revision, "tree")

    def read() -> bytes:
        with _open(url) as response:
            return response.read()

    try:
        entries = json.loads(_retrying(read, "트리 API"))
        by_path = {entry["path"].rsplit("/", 1)[-1]: entry for entry in entries}
        found = {}
        for name in DATA_FILES:
            lfs = by_path[name].get("lfs")
            if not lfs:
                # 검증값을 약하게 바꾸지 않는다 — sha256이 없으면 받지 않는다.
                raise Failed(
                    f"{name}은 HF에서 LFS가 아니라 sha256 검증값이 없다 — 받지 않는다: {url}"
                )
            found[name] = (lfs["oid"], int(lfs["size"]))
        return found
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise Failed(
            f"트리 API 응답에서 {DATA_FILES}의 lfs.oid·size를 못 읽었다"
            f"({type(error).__name__}) — 과제가 그 revision에 없거나 응답 모양이 다르다: {url}"
        ) from error


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, dest: Path, sha256: str, size: int) -> None:
    def once() -> None:
        digest, total = hashlib.sha256(), 0
        with _open(url) as response, dest.open("wb") as handle:
            while chunk := response.read(_CHUNK):
                total += len(chunk)
                if total > size:  # 끝없는 응답이 디스크를 채우지 않게 읽는 도중에 끊는다
                    raise Mismatch(f"받은 바이트가 트리 API의 {size} B를 넘었다")
                handle.write(chunk)
                digest.update(chunk)
        if total < size:  # 중간에 끊긴 것 — 일시 실패다
            raise OSError(f"{size} B 중 {total} B만 받고 끊겼다")
        if digest.hexdigest() != sha256:
            raise Mismatch(f"받은 바이트의 sha256이 트리 API와 다르다({digest.hexdigest()})")

    _retrying(once, f"다운로드 {dest.name}")


def prepare_out(out: Path, expected: dict[str, tuple[str, int]]) -> list[str]:
    """`--out`을 만들거나 검사하고 **아직 없는** 데이터 파일 이름을 돌려준다.

    이 도구가 만든 번들(두 파일 해시 일치 + `submit.sh`)이 아닌 것이 있으면 거절한다.
    """
    if out.exists() and not out.is_dir():
        raise InputRejected(f"--out이 디렉터리가 아니다: {str(out)!r}")
    try:
        out.mkdir(parents=True, exist_ok=True)
        allowed = {*DATA_FILES, SUBMIT_SCRIPT_NAME}
        extras = sorted(entry.name for entry in out.iterdir() if entry.name not in allowed)
    except OSError as error:
        raise InputRejected(f"--out을 만들거나 읽을 수 없다({error}): {str(out)!r}") from error
    if extras:
        hint = (
            " — `.part-*`는 강제 종료로 남은 임시 폴더라 지워도 된다"
            if any(name.startswith(".part-") for name in extras)
            else ""
        )
        raise InputRejected(
            f"--out에 이 도구가 만든 것이 아닌 항목이 있다: {', '.join(map(repr, extras))}{hint} — "
            "빈 디렉터리를 쓴다(정답 자료가 섞이지 않게)"
        )
    missing = []
    for name in DATA_FILES:
        path = out / name
        if not path.exists():
            missing.append(name)
        elif not path.is_file() or _sha256_of(path) != expected[name][0]:
            raise InputRejected(
                f"--out의 {name}이 이 revision의 해시와 다르다 — 빈 디렉터리를 쓴다"
            )
    return missing


def existing_submit(out: Path, task: str, server: str, *, force: bool):
    """그대로 둘 기존 `submit.sh`의 메타데이터. 없거나 `force`면 `None`, 안 맞으면 거절한다.

    파일만 읽으므로 네트워크 전에 부른다 — 서버·과제가 안 맞는 번들에 헛다운로드를 하지 않는다.
    """
    if force or not (out / SUBMIT_SCRIPT_NAME).exists():
        return None
    existing = read_submit_metadata(out)
    if existing is None or existing.task_id != task:
        raise InputRejected(
            f"기존 {SUBMIT_SCRIPT_NAME}을 못 읽거나 다른 과제의 것이다 "
            "— --force-submit으로 다시 만든다"
        )
    server = server.rstrip("/")
    if existing.server != server:
        raise InputRejected(
            f"기존 {SUBMIT_SCRIPT_NAME}의 서버({existing.server})가 --server({server})와 "
            "다르다 — --force-submit으로 새 agent_id와 함께 다시 만든다"
        )
    return existing


def write_submit_sh(out: Path, task: str, server: str, *, force: bool) -> str:
    """`submit.sh`의 agent_id를 돌려준다. 있으면 그대로 두고 `force`면 새로 발급한다."""
    server = server.rstrip("/")
    existing = existing_submit(out, task, server, force=force)
    if existing is not None:
        return existing.agent_id
    agent_id = uuid.uuid4().hex
    metadata = {
        "task_id": task,
        "agent_id": agent_id,
        "checksum": checksum(task, agent_id),
        "require_flag": False,
    }
    script = (
        "#!/bin/bash\n"
        f"curl -X POST {server}/submit-vul \\\n"
        f"  -F 'metadata={json.dumps(metadata)}' \\\n"
        '  -F "file=@${POC_FILE}"\n'
    )
    (out / SUBMIT_SCRIPT_NAME).write_bytes(script.encode("utf-8"))  # LF 그대로
    parsed = read_submit_metadata(out)  # 하네스가 읽는 그대로 다시 읽어 본다
    if parsed is None or (parsed.server, parsed.task_id, parsed.agent_id) != (
        server,
        task,
        agent_id,
    ):
        raise Failed(f"만든 {SUBMIT_SCRIPT_NAME}을 하네스 파서가 같은 값으로 못 읽는다: {parsed}")
    return agent_id


def run(args: argparse.Namespace) -> int:
    family, number = parse_task_id(args.task)
    if not _REVISION.fullmatch(args.revision):
        raise InputRejected(f"--revision은 40자 커밋 해시다: {args.revision!r}")
    if not _SERVER.fullmatch(args.server.rstrip("/")):
        raise InputRejected(f"--server는 `http(s)://호스트[:포트]`다: {args.server!r}")
    out = Path(args.out)
    existing_submit(out, args.task, args.server, force=args.force_submit)  # 네트워크 전에

    expected = expected_files(family, number, args.revision)
    missing = prepare_out(out, expected)
    # 임시 폴더는 `--out` 안에 둔다 — 같은 파일시스템이라 검증 뒤 이름 바꾸기가 원자적이고,
    # 실패하면 폴더째 지워져 검증 안 된 파일이 남지 않는다.
    with tempfile.TemporaryDirectory(prefix=".part-", dir=out) as tmp:
        for name in missing:  # 전부 받아 검증한 **뒤에** 옮긴다 — 둘째가 실패해도 첫째가 안 남는다
            url = f"{_base(family, number, args.revision, 'resolve')}/{name}"
            _download(url, Path(tmp) / name, *expected[name])
        for name in missing:
            os.replace(Path(tmp) / name, out / name)
    agent_id = write_submit_sh(out, args.task, args.server, force=args.force_submit)
    check_task_dir(out)  # 하네스 가드를 그대로 통과하는지

    print(f"OK {args.task} → {out}  (revision {args.revision[:7]})")
    for name in DATA_FILES:
        print(f"  {name}  sha256={expected[name][0]}  {expected[name][1]} B")
    print(f"  {SUBMIT_SCRIPT_NAME}  agent_id={agent_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    force_utf8(sys.stdout)
    force_utf8(sys.stderr)  # Windows 콘솔(cp949)에서 한글이 깨지지 않게
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("task", help="과제 id — arvo:<숫자> 또는 oss-fuzz:<숫자>")
    parser.add_argument("--out", required=True, type=Path, help="번들을 둘 디렉터리(없으면 만든다)")
    parser.add_argument(
        "--server", default=DEFAULT_SERVER, help="채점 서버(--oracle-server와 같은 값)"
    )
    parser.add_argument("--revision", default=DEFAULT_REVISION, help="HF 데이터셋 커밋(40자)")
    parser.add_argument(
        "--force-submit",
        action="store_true",
        help="submit.sh를 새 agent_id로 다시 만든다 — run마다 새 agent_id가 권장이다",
    )
    try:
        return run(parser.parse_args(argv))
    except InputRejected as error:
        print(f"거절: {error}", file=sys.stderr)
        return 2
    except (Failed, OSError) as error:  # OSError — 디스크·권한(입력이 아니라 환경 문제)
        print(f"실패: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
