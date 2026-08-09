"""콘솔 출력 인코딩.

Windows 콘솔의 기본 인코딩(cp949)에서는 `—`·`∩` 같은 문자가 `UnicodeEncodeError`를 내고
**출력 도중에 죽는다.** 실제로 두 번 물렸다: `cli doctor`가 점검 6줄을 찍고 죽었고,
`python -m eval.leakage`가 결과 첫 줄에서 죽었다. 둘 다 pytest가 출력을 UTF-8로 잡아
테스트에는 보이지 않았고, 명령을 직접 돌려서야 드러났다.

진단·검증 도구가 진단 대상보다 먼저 깨지면 안 되므로 진입점마다 이 함수를 부른다.
우리가 쓰는 파일(trace·리포트·프롬프트)이 전부 UTF-8이므로 콘솔도 같은 인코딩으로 맞춘다.

`src/`에 있지만 파이프라인에 의존하지 않는다 — `eval/`도 import 하므로 무거운 것을
끌고 오면 안 된다.
"""

from typing import TextIO


def force_utf8(stream: TextIO) -> None:
    """출력 스트림을 UTF-8로 바꾼다.

    바꿀 수 없는 스트림(파이프로 감싼 테스트 캡처 등)은 그대로 둔다. 여기서 실패해도
    명령 자체는 계속되어야 하므로 예외를 좁게 잡아 넘긴다 — 인코딩을 못 바꾼 것이
    명령을 실패시킬 이유는 아니다.
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8")
    except (ValueError, OSError, AttributeError):
        return
