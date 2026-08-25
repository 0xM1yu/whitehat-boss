"""실 토크나이저를 토큰 카운터로 만든다 (D21 (b)).

모델의 `tokenizer.json`을 읽어 `src.tokens.set_counter()`에 넘길 카운터
(`Callable[[str], int]`)를 돌려준다. 러너가 역할 캡슐 조립 직전에 그것을 set 한다(후속 단계).

**`tokenizers`는 하드 의존이 아니다.** 실서빙 프로필에만 필요하고 그쪽엔 vLLM과 함께 깔려
있다(ATOM). 프로젝트 기본 의존은 pydantic·pyyaml뿐이고 CI·dev는 `tokenizers` 없이 돈다 —
그래서 import를 **지연**하고, 없으면 `None`을 돌려 호출자가 기본 카운터(글자수 근사)를
유지하게 한다.

**구별: "파일 없음/라이브러리 없음"은 None(폴백), "파일이 있는데 깨짐"은 예외.** 실서빙
run에서 토크나이저가 깨졌는데 조용히 글자수 근사로 돌아가면 D21이 고치려던 바로 그
"예산이 실제와 다르다"를 다시 만든다 — 그러니 그건 소리내어 실패한다.
"""

from collections.abc import Callable
from pathlib import Path

# HF 저장소·스냅샷 디렉터리에 들어 있는 fast tokenizer 파일 이름.
TOKENIZER_FILE = "tokenizer.json"


def counter_from_tokenizer(source: str | Path) -> Callable[[str], int] | None:
    """`source`에서 실 토큰 카운터를 만든다.

    `source`가 디렉터리면 그 안의 `tokenizer.json`을, 파일이면 그 파일을 읽는다. 파일이
    없거나 `tokenizers`가 안 깔려 있으면 `None`(호출자는 기본 카운터 유지). 파일이 있는데
    읽지 못하면 예외를 그대로 올린다(위 docstring의 구별).

    돌려주는 카운터는 **원시 토큰 수**만 센다 — 바닥 1은 `src.tokens.estimate_tokens`가
    강제하므로 여기서 다시 씌우지 않는다.
    """
    path = Path(source)
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
