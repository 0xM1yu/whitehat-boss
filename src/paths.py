"""분석 대상 경로의 신뢰 경계.

`Location.file`과 `ToolArgs.workdir`는 **모델 출력이나 인덱스에서 온 값**이고 스키마는
비어 있지 않은 문자열이라는 것만 강제한다. 이 값을 그대로 `root / value`로 이으면
pathlib이 오른쪽이 절대경로일 때 `root`를 통째로 버리므로 대상 밖 파일이 읽힌다.
`..`도 마찬가지다.

지금은 Fake 어댑터만 쓰지만 "root 기준 상대경로"라는 계약을 실제 Retriever·ToolRunner가
그대로 물려받는다. 호출부가 전역에 퍼진 뒤에 경계를 다시 세우는 것보다 여기서 한 번
막는 편이 싸다. disclosure-policy.md의 "직접 구축한 로컬 환경으로 한정"과 같은 방향이다.
"""

from pathlib import Path


class PathEscape(ValueError):
    """대상 루트 밖을 가리키는 경로. 조용히 무시하지 않고 실패시킨다."""


def safe_join(root: Path, relative: str) -> Path:
    """`root` 아래로만 해석되는 경로. 벗어나면 `PathEscape`.

    존재 여부는 보지 않는다 — 없는 파일은 호출부가 빈 내용으로 처리할 사안이고,
    루트 탈출은 그와 다른 사건이다.
    """
    base = root.resolve()
    joined = (base / relative).resolve()
    if joined != base and base not in joined.parents:
        # 호스트 루트 경로를 안 싣는다 — `Evidence.error`로 모델 관측에 가는 자리가 있다
        # (`ContainerToolRunner.run`의 workdir 검사). 거절된 상대 경로만 든다.
        raise PathEscape(f"대상 루트 밖을 가리킨다: {relative!r}")
    return joined
