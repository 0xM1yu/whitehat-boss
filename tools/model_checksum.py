"""모델 스냅샷의 checksum을 산출한다 (model-serving.md §5).

`config/models.yaml`의 `checksum`에 넣을 값과, 그 값이 무엇으로부터 나왔는지를 적은
manifest를 함께 낸다.

## 왜 스크립트인가

**손으로 맞추면 조용히 다른 값이 나온다.** 정렬 기준·개행·제외 규칙 셋 중 하나만
어긋나도 같은 모델에서 다른 checksum이 나오고, 그 사실은 값을 비교하기 전까지 드러나지
않는다. `tools/probe/README.md`가 fixture 이름을 두고 같은 판단을 했다("사람이 손으로
적으면 틀린 이름의 fixture가 CI를 통과한다").

## 무엇을 해시하는가

**로컬 스냅샷 전 파일의 sha256을 모은 manifest의 sha256이다.**

HF Hub 메타데이터(`siblings[].lfs.sha256`)를 정본으로 쓰지 않는 이유는 그것이 **LFS 추적
파일만** 덮기 때문이다. `config.json`·`tokenizer_config.json` 같은 작은 파일은 일반 git
blob이라 `blobId`(git SHA-1)만 오는데, 하필 그쪽이 양자화 설정과 tokenizer 설정 —
registry 주석이 말하는 "model·tokenizer·quantization 묶음"의 절반이다. 그래서 받은 뒤
로컬에서 잰 값이 정본이고, HF 조회는 받기 전 예비 확인으로만 쓴다.

## 포함 규칙은 denylist다

allowlist가 아니다. **비대칭 때문이다**: allowlist가 새로 생긴 가중치·설정 파일을 놓치면
모델이 바뀌었는데 checksum이 그대로이고(조용한 거짓), denylist가 문서 파일을 놓치면
checksum이 헛되이 바뀔 뿐이다(시끄러운 참). `RunRecord.repo_dirty`가 미추적 파일까지
dirty로 세는 것과 같은 판단이다 — 잘못된 clean이 훨씬 비싸다.

## 심볼릭 링크

**파일 링크는 따라간다.** HF 캐시의 스냅샷 디렉터리는 `../../blobs/<sha256>`을 가리키는
링크 모음이라, 따라가지 않으면 해시할 내용 자체가 없다. **디렉터리 링크는 따라가지 않고
목록에 남긴다** — 순환하거나 스냅샷 밖 트리를 통째로 끌어들일 수 있고, 그렇게 들어온
파일은 이 모델의 일부가 아니다.

## 쓰는 법

    python tools/model_checksum.py <스냅샷 경로> --key qwen3-14b \\
        --source hf://Qwen/Qwen3-14B@<revision>

**첫 줄의 `[스냅샷]` 출력을 보고 넘어간다.** 엉뚱한 디렉터리를 해시해도 결과는 형식이
완벽한 checksum이라 값만 봐서는 모른다 (이슈 #134). **현재 디렉터리로 귀착하는 입력은
이제 거부한다** — 빈 문자열·`.`·`./`가 전부 `Path.parts == ()`로 같다. 남는 위험은
**엉뚱한 디렉터리를 명시적으로 준 경우**이고, 그건 막지 못하므로 이 줄로 보이게만 한다.

manifest는 `config/checksums/<model key>.txt`에 쓴다(`--manifest-out`으로 바꿀 수 있다).
**checksum이 `config/models.yaml`에 있으므로 그 근거도 `config/` 안에 있어야 한다** —
값과 근거가 다른 수명의 디렉터리에 흩어지면 갈라진다. 형식은 `sha256sum` 호환이라
스냅샷 디렉터리에서 `sha256sum -c <manifest>`로 바로 검증할 수 있다.

registry에는 **사람이 붙여넣는다.** `bench --apply`와 달리 자동으로 쓰지 않는다 — 값을
채우는 것은 다운로드가 끝난 뒤의 판단이고, 그 판단까지 자동화할 이유가 없다.

`src/`를 import 하지 않고 stdlib만 쓴다(`tools/probe/`와 같은 제약). 모델을 받은 장비에서
저장소를 클론하지 않고도 돌아야 하기 때문이다.
"""

import argparse
import fnmatch
import hashlib
import sys
from pathlib import Path

# 해시에서 빼는 것. **모델 로딩에 쓰이지 않는 파일만** 넣는다 — 여기에 뭔가를 더할 때는
# "이 파일이 바뀌어도 모델의 동작이 같은가"를 먼저 답해야 한다.
DENYLIST = (
    "*.md",
    "LICENSE*",
    "NOTICE*",
    ".gitattributes",
    ".gitignore",
    "*.lock",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.svg",
)

# 디렉터리 이름 기준 제외. HF 캐시의 `.cache/`에는 다운로드 중간 상태가 남는다.
DENY_DIRS = (".git", ".cache", "__pycache__")

_CHUNK = 1024 * 1024


class ChecksumError(RuntimeError):
    """계속하면 틀린 값이 나오는 상태."""


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def excluded(relative: Path) -> bool:
    if any(part in DENY_DIRS for part in relative.parts[:-1]):
        return True
    return any(fnmatch.fnmatch(relative.name, pattern) for pattern in DENYLIST)


def collect(root: Path) -> tuple[list[Path], list[str]]:
    """(해시 대상 상대경로, 건너뛴 것의 사유). 경로는 정렬돼 있다.

    정렬 기준은 **POSIX 상대경로의 코드포인트 순**이다. 구분자를 `/`로 고정하므로
    Windows에서 만들어도 같은 순서가 나온다.
    """
    files: list[Path] = []
    skipped: list[str] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if path.is_dir():
            if path.is_symlink():
                # 따라가지 않는다. 순환하거나 스냅샷 밖 트리를 끌어들인다.
                skipped.append(f"{relative.as_posix()}/ (디렉터리 심볼릭 링크)")
            continue
        if not path.is_file():
            # 끊어진 링크·소켓 등. 조용히 넘기지 않는다 — 해시 대상이 빠진 것이다.
            skipped.append(f"{relative.as_posix()} (일반 파일이 아니다)")
            continue
        if excluded(relative):
            skipped.append(f"{relative.as_posix()} (제외 규칙)")
            continue
        files.append(relative)
    return sorted(files, key=lambda item: item.as_posix()), sorted(skipped)


def manifest_text(root: Path, files: list[Path]) -> str:
    """`sha256sum` 호환 텍스트. 줄바꿈은 LF로 고정한다."""
    return "".join(f"{file_digest(root / rel)}  {rel.as_posix()}\n" for rel in files)


def aggregate(manifest: str) -> str:
    """manifest 바이트의 sha256. registry의 `checksum` 형식 그대로 돌려준다."""
    return "sha256:" + hashlib.sha256(manifest.encode("utf-8")).hexdigest()


def compute(root: Path) -> tuple[str, str, list[str]]:
    """(checksum, manifest, 건너뛴 사유)."""
    if not root.is_dir():
        raise ChecksumError(f"스냅샷 디렉터리가 아니다: {root}")
    files, skipped = collect(root)
    if not files:
        # 빈 디렉터리에서도 sha256은 나온다. 그 값을 registry에 넣으면 **모든 빈 스냅샷이
        # 같은 checksum**을 갖게 되므로 여기서 끊는다.
        raise ChecksumError(f"해시할 파일이 하나도 없다: {root}")
    manifest = manifest_text(root, files)
    return aggregate(manifest), manifest, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="모델 스냅샷의 checksum과 manifest를 낸다")
    parser.add_argument("snapshot", type=Path, help="모델 스냅샷 디렉터리")
    parser.add_argument("--key", help="model key. manifest 기본 경로에 쓰인다")
    parser.add_argument(
        "--source",
        help="registry의 source에 넣을 값 (예: hf://org/repo@rev). 없으면 스냅샷 절대경로",
    )
    parser.add_argument(
        "--manifest-out",
        type=Path,
        help="manifest를 쓸 경로. 기본은 config/checksums/<key>.txt",
    )
    args = parser.parse_args(argv)

    # **빈 경로를 거부한다** (이슈 #134). `Path("")`는 `.resolve()`에서 **현재
    # 디렉터리**가 되고, 아래 `compute()`의 유일한 가드(`if not root.is_dir()`)를
    # 디렉터리라서 통과한다. 그러면 이 스크립트가 **저장소를 해시한다.**
    #
    # 가정이 아니라 실제로 났다 — 2026-09-03 ATOM에서 셸이 `SNAP=` 대입에 실패해
    # 빈 문자열이 넘어갔고, 도구는 저장소 파일 165개(기대 10개)를 해시해 형식이
    # 완벽한 checksum을 낸 뒤 `config/checksums/<key>.txt`를 덮어썼다.
    #
    # **`argparse`의 `required`로는 못 막는다** — 위치 인자는 주어졌고 값이 빈
    # 문자열일 뿐이다.
    #
    # **막히는 것은 빈 문자열만이 아니다.** `Path("")`·`Path(".")`·`Path("./")`는
    # `parts`가 전부 `()`이고 서로 같다(`Path("") == Path(".")`). 그래서 이 가드는
    # **현재 디렉터리로 귀착하는 입력 전부**를 막는다 — 사유 문장도 그 넷 모두에
    # 참이어야 한다. 예전 문장("경로가 비어 있다")은 스냅샷 디렉터리 안에서
    # `model_checksum.py .`을 정당하게 돌린 사람에게 **거짓이었다.**
    #
    # **`.`을 통과시키지 않는 것은 의도다.** 명시적이든 아니든 cwd를 통째로 해시하는
    # 결과는 같고, 그것이 #134가 막으려던 사고다. 구별하려면 `type=Path`를 버리고
    # 원문자열을 봐야 하는데, 그렇게 얻는 것이 "cwd를 해시할 자유"뿐이라 하지 않았다.
    # 더 넓게 막는 것(모델 파일 시그니처 요구)은 정상적인 별난 스냅샷을 거부할 수
    # 있어 별도 판단으로 둔다.
    if not args.snapshot.parts:
        print(
            "[ERROR] 스냅샷 경로가 현재 디렉터리로 귀착한다 (빈 문자열 · `.` · `./`) — "
            "그러면 스냅샷이 아니라 지금 있는 디렉터리를 통째로 해시한다. "
            "셸 변수가 비지 않았는지 확인하고 스냅샷 디렉터리를 명시적으로 준다",
            file=sys.stderr,
        )
        return 2

    root = args.snapshot.resolve()
    try:
        checksum, manifest, skipped = compute(root)
    except ChecksumError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    out = args.manifest_out
    if out is None and args.key:
        out = Path(__file__).resolve().parents[1] / "config" / "checksums" / f"{args.key}.txt"
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        # 개행을 번역하지 않는다. 번역하면 Windows에서 만든 manifest의 내용이 aggregate의
        # 입력과 어긋나 `sha256sum -c`도, 재산출도 맞지 않는다.
        out.write_text(manifest, encoding="utf-8", newline="")

    # **무엇을 해시했는지 항상 찍는다** (이슈 #134). 아래 `source:` 줄이 루트를 보여
    # 주긴 하는데 `args.source`가 있으면 **그쪽으로 대체된다** — 그리고 `--source`를
    # 주는 것이 이 파일 머리의 「쓰는 법」이 규정한 정규 용법이다. 즉 정상적으로 쓰면
    # 잘못된 루트를 볼 창구가 닫혀 있었다. 위 가드가 못 막는 경우(**엉뚱한 디렉터리를
    # 준다** — 이웃 스냅샷, 상위 디렉터리, 오타 난 경로)를 이 한 줄이 눈에 보이게 한다.
    # `.`은 그 예가 아니다 — 위 가드가 `parts == ()`로 이미 막는다.
    print(f"[스냅샷] {root}")
    print(f'checksum: "{checksum}"')
    print(f'source: "{args.source or root.as_uri()}"')
    print(f"[파일] {len(manifest.splitlines())}개")
    for reason in skipped:
        print(f"[제외] {reason}")
    if out is not None:
        print(f"[manifest] {out}")
    print("registry에는 사람이 붙여넣는다 — 이 스크립트는 config/models.yaml을 쓰지 않는다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
