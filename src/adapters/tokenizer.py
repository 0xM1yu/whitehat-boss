"""실 토크나이저를 토큰 카운터로 만든다 (#96 (b)).

`models.yaml`의 `source`(예: `hf://Qwen/Qwen3-4B-...@<rev>`)에서 그 모델의 `tokenizer.json`을
찾아 토큰 카운터(`Callable[[str], int]`)를 돌려준다. `factory`가 역할별로 이 카운터를 만들어
`Deps.counter_for_role`로 주입하고, 러너가 역할 캡슐 조립 직전에
`using_counter(ctx.counter_for_role(role))`로 켠다(3b, `runner._step_retrieval`).

**`tokenizers`는 하네스의 런타임 의존이 아니다** — `pyproject.toml`의 `dependencies`는
pydantic·pyyaml 둘뿐이고 실제로 이 라이브러리를 부르는 것은 실서빙 프로필뿐이다(그쪽엔
vLLM과 함께 깔려 있다 — ATOM).
⏵ 2026-10-08 — ATOM은 2026-09-29 사용 종료(#340). 하네스가 로컬이고 서빙만 vast인 배치
(`--device vastai-remote`)에서 하네스 쪽에 `tokenizers`가 깔려 있는지는 이 줄이 확인하지 않았다.

**그러나 `requirements.txt`에는 있다.** 그 파일은 "이 저장소를 검사하려면 무엇이 필요한가"의
목록이고, `tests/test_tokenizer_adapter.py`의 실카운팅 검증을 **CI에서 실제로 돌리기 위해**
tree-sitter가 이미 같은 자리에 있다. 따라서 **"CI는 `tokenizers` 없이 돈다"는 더 이상 사실이
아니다** — 없으면 그 테스트가 skip이 아니라 collection error를 낸다.

**그런데도 import는 여전히 지연이고 없으면 `None`이다.** 노리는 것이 CI가 아니라 **런타임의
부분 설치 환경**이기 때문이다: `requirements.txt`를 안 쓰고 최소 설치로 띄운 곳, 또는
`pip install`이 부분 실패한 곳. 거기서 import 실패로 파이프라인을 죽이는 것보다 기본
카운터(글자수 근사)로 도는 편이 낫다 — 다만 **그 폴백은 이제 조용하지 않다**:
러너가 run 시작 시 `token_counter` 이벤트로 역할별 `real`/`approx`와 그 사유를 남긴다
(`orchestrator/trace.py`의 `EventKind.TOKEN_COUNTER`).

**구별: "파일 없음/라이브러리 없음"은 None(폴백), "파일이 있는데 깨짐"은 예외.** 실서빙
run에서 토크나이저가 깨졌는데 조용히 글자수 근사로 돌아가면 #96이 고치려던 바로 그
"예산이 실제와 다르다"를 다시 만든다 — 그러니 그건 소리내어 실패한다.
"""

import os
from collections.abc import Callable
from functools import cache
from pathlib import Path

# HF 저장소·스냅샷 디렉터리에 들어 있는 fast tokenizer 파일 이름.
TOKENIZER_FILE = "tokenizer.json"

_HF_SCHEME = "hf://"
_FILE_SCHEME = "file://"

# 기본 카운터로 되돌아간 **사유**. `token_counter` 이벤트의 `{role}_reason`이 되고,
# 그 값이 곧 지표의 열 값이므로 함부로 바꾸지 않는다.
#
# **넷을 가르는 것이 요점이다** — `graph_index`가 `missing`과 `unsupported_version`을 가른
# 것과 같은 이유로 대응이 전부 다르다. `no_source`는 설정이 그렇게 생긴 것(fake 등)이라
# 정상이고, `library_absent`는 설치 문제이고, `file_absent`는 캐시가 안 받아진 것이고,
# `unresolved_source`는 registry가 리비전 없는 `source`를 적은 것이다.
REASON_NO_SOURCE = "no_source"
REASON_UNRESOLVED_SOURCE = "unresolved_source"
REASON_FILE_ABSENT = "file_absent"
REASON_LIBRARY_ABSENT = "library_absent"


def counter_from_tokenizer(location: str | Path) -> Callable[[str], int] | None:
    """`location`에서 실 토큰 카운터를 만든다.

    `location`이 디렉터리면 그 안의 `tokenizer.json`을, 파일이면 그 파일을 읽는다. 파일이
    없거나 `tokenizers`가 안 깔려 있으면 `None`(호출자는 기본 카운터 유지). 파일이 있는데
    읽지 못하면 예외를 그대로 올린다(위 docstring의 구별).

    돌려주는 카운터는 **원시 토큰 수**만 센다 — 바닥 1은 `src.tokens.estimate_tokens`가
    강제하므로 여기서 다시 씌우지 않는다.

    **사유까지 필요하면 `_load_counter`를 쓴다.** 이 함수는 그것의 얇은 껍데기다 —
    판정이 두 곳에 살면 카운터와 기록이 갈리고, 갈린 기록은 없는 것보다 나쁘다.
    """
    return _load_counter(location)[0]


def _load_counter(location: str | Path) -> tuple[Callable[[str], int] | None, str | None]:
    """`(카운터, 폴백 사유)`. 성공하면 `(counter, None)`, 실패하면 `(None, 사유)`.

    **`file_absent`와 `library_absent`를 가른다.** 둘 다 결과는 글자수 근사로 같지만 대응이
    다르다 — 앞은 HF 캐시를 받아야 하고 뒤는 `pip install`이다. 이 구별이 없으면 ATOM에서
    venv가 `tokenizers` 없이 떴을 때와 스냅샷이 안 받아졌을 때가 기록에서 같아 보인다.
    """
    path = Path(location)
    if path.is_dir():
        path = path / TOKENIZER_FILE
    if not path.is_file():
        return None, REASON_FILE_ABSENT

    try:
        from tokenizers import Tokenizer
    except ImportError:
        return None, REASON_LIBRARY_ABSENT

    tokenizer = Tokenizer.from_file(str(path))

    def count(text: str) -> int:
        return len(tokenizer.encode(text).ids)

    return count, None


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


@cache
def _resolve_counter(
    source: str, hub_cache: Path | None = None
) -> tuple[Callable[[str], int] | None, str | None]:
    """`source` → `(카운터, 폴백 사유)`. **카운터와 기록이 같은 판정에서 나오는 자리다.**

    `counter_for_source`와 `counter_reason_for_source`가 둘 다 여기를 부른다. 판정을 두 벌
    두면 러너가 켠 카운터와 trace에 적힌 종류가 갈릴 수 있고, **갈린 기록은 없는 것보다
    나쁘다**(`factory`가 `tool_images`를 "구현에게 묻는" 것과 같은 논법).

    **결과를 캐시한다**(`source`별). 러너가 후보마다 부르는데 토크나이저 로딩(수 MB 파싱)이
    무거워, 같은 모델은 run 내내 한 번만 로드한다. 토크나이저 파일은 run 중 바뀌지 않으므로
    (조립 시작 시점에 존재) 캐시가 안전하다.
    """
    directory = tokenizer_dir_from_source(source, hub_cache)
    if directory is None:
        return None, REASON_UNRESOLVED_SOURCE
    return _load_counter(directory)


def counter_for_source(source: str, hub_cache: Path | None = None) -> Callable[[str], int] | None:
    """`models.yaml`의 `source` 하나로 실 카운터까지 — 경로 해석 + 로더를 합친다.

    러너가 역할 캡슐 조립 직전에 `using_counter(counter_for_source(...))`로 쓴다(3b). 경로를
    못 만들거나(스킴 모름·리비전 없음) 그 경로에 `tokenizer.json`이 없거나 `tokenizers`가
    없으면 `None` — 어느 경우든 호출자는 기본 카운터를 유지한다.

    **왜 `None`인지는 `counter_reason_for_source`가 답한다.** 그 값이 run 산출물에 남는다
    (`token_counter` 이벤트) — 이 폴백이 조용하던 동안에는 "토크나이저로 쟀다"는 기록을
    run 전에 사람이 손으로 확인해야 했다(2026-08-26 재측정 §근거 로그).
    """
    return _resolve_counter(source, hub_cache)[0]


def clear_counter_cache() -> None:
    """`_resolve_counter`의 캐시를 비운다. **테스트가 쓰라고 있는 손잡이다.**

    캐시에 상한이 없고 프로세스 수명 내내 `Tokenizer`를 붙잡는다 — run당 1회 로드라는
    목적에는 맞지만, 비우는 길이 없으면 "파일이 없다가 생겼다"를 검사할 방법이 없다.
    지금 테스트가 안전한 것은 `tmp_path`가 매번 달라 키가 겹치지 않기 때문이고 **그것은
    우연에 가깝다.**

    덧붙임: `_resolve_counter(src)`와 `_resolve_counter(src, None)`은 `functools.cache`에서
    **다른 키**다. 결과가 같으니 최악이 중복 로드 한 번이고 버그가 아니다 — 정규화 래퍼를
    하나 더 두는 값이 얻는 것보다 크다고 보아 두지 않는다.
    """
    _resolve_counter.cache_clear()


def counter_reason_for_source(source: str, hub_cache: Path | None = None) -> str | None:
    """실 카운터를 못 쓴 사유. 쓸 수 있으면 `None`.

    `counter_for_source`와 **같은 판정**(`_resolve_counter`)에서 나온다.
    """
    return _resolve_counter(source, hub_cache)[1]
