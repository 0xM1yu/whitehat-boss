"""실 토크나이저를 토큰 카운터로 만든다 (D21 (b)).

`models.yaml`의 `source`(예: `hf://Qwen/Qwen3-4B-...@<rev>`)에서 그 모델의 `tokenizer.json`을
찾아 `src.tokens.set_counter()`에 넘길 카운터(`Callable[[str], int]`)를 돌려준다. 러너가
역할 캡슐 조립 직전에 `counter_for_source(...)`로 그것을 set 한다(후속 단계 3b).

**`tokenizers`는 하드 의존이 아니다.** 실서빙 프로필에만 필요하고 그쪽엔 vLLM과 함께 깔려
있다(ATOM). 프로젝트 기본 의존은 pydantic·pyyaml뿐이고 CI·dev는 `tokenizers` 없이 돈다 —
그래서 import를 **지연**하고, 없으면 `None`을 돌려 호출자가 기본 카운터(글자수 근사)를
유지하게 한다.

**구별: "파일 없음/라이브러리 없음"은 None(폴백), "파일이 있는데 깨짐"은 예외.** 실서빙
run에서 토크나이저가 깨졌는데 조용히 글자수 근사로 돌아가면 D21이 고치려던 바로 그
"예산이 실제와 다르다"를 다시 만든다 — 그러니 그건 소리내어 실패한다.
"""

import os
from collections.abc import Callable
from pathlib import Path

# HF 저장소·스냅샷 디렉터리에 들어 있는 fast tokenizer 파일 이름.
TOKENIZER_FILE = "tokenizer.json"

_HF_SCHEME = "hf://"
_FILE_SCHEME = "file://"


def counter_from_tokenizer(location: str | Path) -> Callable[[str], int] | None:
    """`location`에서 실 토큰 카운터를 만든다.

    `location`이 디렉터리면 그 안의 `tokenizer.json`을, 파일이면 그 파일을 읽는다. 파일이
    없거나 `tokenizers`가 안 깔려 있으면 `None`(호출자는 기본 카운터 유지). 파일이 있는데
    읽지 못하면 예외를 그대로 올린다(위 docstring의 구별).

    돌려주는 카운터는 **원시 토큰 수**만 센다 — 바닥 1은 `src.tokens.estimate_tokens`가
    강제하므로 여기서 다시 씌우지 않는다.
    """
    path = Path(location)
    if path.is_dir():
        path = path / TOKENIZER_FILE
    if not path.is_file():
        return None

    try:
        from tokenizers import Tokenizer
    except ImportError:
        return None

    tokenizer = Tokenizer.from_file(str(path))

    def count(text: str) -> int:
        return len(tokenizer.encode(text).ids)

    return count


def _default_hub_cache() -> Path:
    """HF 캐시 hub 디렉터리 — HF 환경변수 관례를 따른다."""
    hub = os.environ.get("HF_HUB_CACHE")
    if hub:
        return Path(hub)
    home = os.environ.get("HF_HOME")
    if home:
        return Path(home) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def tokenizer_dir_from_source(source: str, hub_cache: Path | None = None) -> Path | None:
    """`models.yaml`의 `source`에서 `tokenizer.json`이 있을 디렉터리를 구성한다.

    - `hf://<repo>@<rev>` → `{hub}/models--<repo의 /를 -->/snapshots/<rev>` (HF 캐시 관례)
    - `file://<path>`     → 그 경로 (모델 디렉터리 자체)
    - 그 외(빈 값·`@rev` 없음·다른 스킴 예: `sha256:...`) → `None` (호출자는 기본 카운터 유지)

    **존재 확인은 하지 않는다** — 경로만 구성하고 파일 유무는 `counter_from_tokenizer`가 본다.
    `source` 형식은 `ModelEntry`가 패턴으로 강제하지 않으므로(config.py 주석) 여기서도 아는
    두 스킴만 풀고 나머지는 조용히 None이다. `hub_cache`가 없으면 HF 환경변수로 정한다.
    """
    if source.startswith(_FILE_SCHEME):
        return Path(source[len(_FILE_SCHEME) :])
    if source.startswith(_HF_SCHEME):
        repo, at, rev = source[len(_HF_SCHEME) :].rpartition("@")
        if not at:  # 리비전이 없으면 어느 스냅샷인지 특정하지 못한다
            return None
        hub = hub_cache if hub_cache is not None else _default_hub_cache()
        return hub / f"models--{repo.replace('/', '--')}" / "snapshots" / rev
    return None


def counter_for_source(source: str, hub_cache: Path | None = None) -> Callable[[str], int] | None:
    """`models.yaml`의 `source` 하나로 실 카운터까지 — 경로 해석 + 로더를 합친다.

    러너가 역할 캡슐 조립 직전에 `set_counter(counter_for_source(...) or 기본)`으로 쓴다(3b).
    경로를 못 만들거나(스킴 모름·리비전 없음) 그 경로에 `tokenizer.json`이 없거나 `tokenizers`가
    없으면 `None` — 어느 경우든 호출자는 기본 카운터를 유지한다.
    """
    directory = tokenizer_dir_from_source(source, hub_cache)
    if directory is None:
        return None
    return counter_from_tokenizer(directory)
