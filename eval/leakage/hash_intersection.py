"""Train/Test 분리 검사 — KB 인덱스와 평가셋의 artifact digest 교집합 (evaluation.md §4).

평가셋 파일이 KB에 색인되어 있으면 retrieval이 정답 근처를 직접 집어 온다. 그러면
측정되는 것은 발견 능력이 아니라 색인 적중이고, **그 run의 모든 비교 결과가 무효**가 된다.

**경로가 아니라 내용 digest로 본다.** 같은 파일을 다른 경로에 복사해 두는 것이 가장 흔한
누출 경로이고, 경로 비교는 그것을 놓친다.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from src.ids import sha256_digest

# 매니페스트로 읽을 확장자. 그 외 경로는 디렉터리로 보고 직접 해싱한다.
_MANIFEST_SUFFIXES = (".json",)


@dataclass(frozen=True)
class Overlap:
    """양쪽에 같은 내용이 있다는 사실 하나."""

    digest: str
    kb_key: str
    eval_key: str

    def render(self) -> str:
        return f"{self.digest[:19]}… : KB {self.kb_key!r} == 평가셋 {self.eval_key!r}"


def digests(path: Path) -> dict[str, str]:
    """식별자 → digest 맵.

    디렉터리면 그 아래 파일을 전부 해싱하고(키는 루트 기준 상대경로), `.json`이면
    `{식별자: digest}` 매니페스트로 읽는다. KB 인덱스가 아직 파일로 존재하지 않는
    단계에서도 매니페스트만 있으면 검사가 돌아간다.
    """
    if path.suffix in _MANIFEST_SUFFIXES:
        return _read_manifest(path)
    return {
        file.relative_to(path).as_posix(): sha256_digest(file.read_bytes())
        for file in sorted(p for p in path.rglob("*") if p.is_file())
    }


def intersecting(kb: Mapping[str, str], evalset: Mapping[str, str]) -> list[Overlap]:
    """양쪽에 모두 있는 digest. 비어 있어야 정상이다.

    키가 아니라 값(digest)으로 뒤집어 맞춘다. 같은 digest가 KB에 여러 경로로 있으면
    각각을 별도 사실로 남긴다 — 어느 사본을 지워야 하는지 알아야 고칠 수 있다.
    """
    by_digest: dict[str, list[str]] = {}
    for key, digest in kb.items():
        by_digest.setdefault(digest, []).append(key)
    return [
        Overlap(digest=digest, kb_key=kb_key, eval_key=eval_key)
        for eval_key, digest in sorted(evalset.items())
        for kb_key in sorted(by_digest.get(digest, ()))
    ]


def _read_manifest(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}의 최상위는 {{식별자: digest}} 매핑이어야 한다")
    bad = sorted(key for key, value in data.items() if not isinstance(value, str))
    if bad:
        raise ValueError(f"{path}의 digest 값이 문자열이 아니다: {bad}")
    return dict(data)
