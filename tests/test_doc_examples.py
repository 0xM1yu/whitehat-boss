"""문서의 JSON 예시를 실제 스키마로 파싱한다.

**불일치가 생긴 근본 원인은 "문서 예시를 아무도 대조하지 않았다"이다.** PR 1이
`ContextCapsule`에 `run_id`·`expansion_count`를 추가했는데 문서 예시는 그대로였고,
아무 테스트도 깨지지 않아 그대로 남았다. 사람이 기억으로 맞추는 방식은 같은 실패를
반복한다 — 이것이 유일한 기계적 재발 방지책이다.

여기서 고정하는 것은 "문서 예시가 코드로 파싱된다"뿐이다. 예시의 **값**은 단언하지 않는다
(합성 예시이고 바뀔 수 있다).

새 예시 블록을 문서에 추가하면 `EXPECTED`에도 등록해야 한다 — 등록되지 않은 블록이 있으면
`test_every_json_block_is_covered`가 실패한다. 검사 대상에서 조용히 빠지는 것을 막는다.
"""

import json
import re
from pathlib import Path

import pytest
from pydantic import BaseModel

from src.schemas import (
    Artifact,
    Candidate,
    ContextCapsule,
    Decision,
    Evidence,
    Finding,
    MissingEvidence,
    ReproSpec,
    RunRecord,
)

DOCS = Path(__file__).resolve().parent.parent / "docs"

# ```json 블록과 그 직전 제목. 제목만으로는 모자라는 경우가 있어(한 제목 아래 두 블록)
# 제목 안에서의 순번까지 키로 쓴다.
_FENCE = re.compile(r"^```json\s*$(?P<body>.*?)^```\s*$", re.DOTALL | re.MULTILINE)
_HEADING = re.compile(r"^#{1,6}\s+(?P<title>.+?)\s*$", re.MULTILINE)

# (문서, 제목, 제목 안 순번) → 이 블록이 따라야 하는 모델.
EXPECTED: dict[tuple[str, str, int], type[BaseModel]] = {
    ("agent-contracts.md", "Artifact", 0): Artifact,
    ("agent-contracts.md", "Candidate", 0): Candidate,
    ("agent-contracts.md", "Evidence", 0): Evidence,
    ("agent-contracts.md", "Finding", 0): Finding,
    # `repro_spec`이 채워진 형태를 보여 주는 두 번째 블록.
    ("agent-contracts.md", "Finding", 1): ReproSpec,
    ("agent-contracts.md", "MissingEvidence", 0): MissingEvidence,
    ("agent-contracts.md", "Decision", 0): Decision,
    ("agent-contracts.md", "RunRecord (재현성)", 0): RunRecord,
    ("knowledge-base.md", "4. Context Capsule", 0): ContextCapsule,
}

COVERED_DOCS = sorted({doc for doc, _, _ in EXPECTED})


def _blocks(doc: str) -> list[tuple[str, int, str]]:
    """(제목, 제목 안 순번, JSON 본문) 목록."""
    text = (DOCS / doc).read_text(encoding="utf-8")
    headings = [(match.start(), match.group("title")) for match in _HEADING.finditer(text)]
    seen: dict[str, int] = {}
    found = []
    for match in _FENCE.finditer(text):
        title = next(
            (title for start, title in reversed(headings) if start < match.start()),
            "(제목 없음)",
        )
        ordinal = seen.get(title, 0)
        seen[title] = ordinal + 1
        found.append((title, ordinal, match.group("body")))
    return found


def _body(doc: str, title: str, ordinal: int) -> str:
    return next(
        text
        for found_title, found_ordinal, text in _blocks(doc)
        if (found_title, found_ordinal) == (title, ordinal)
    )


@pytest.mark.parametrize("doc", COVERED_DOCS)
def test_every_json_block_is_covered(doc: str) -> None:
    """검사되지 않는 예시 블록이 생기면 알려 준다.

    문서에 예시를 늘리는 것 자체는 좋은 일이지만, 등록하지 않으면 그 블록만 조용히
    낡는다 — 지금 고치려는 실패 유형이 정확히 그것이다.
    """
    unregistered = [
        (title, ordinal)
        for title, ordinal, _ in _blocks(doc)
        if (doc, title, ordinal) not in EXPECTED
    ]

    assert unregistered == [], f"{doc}: EXPECTED에 등록되지 않은 JSON 블록"


@pytest.mark.parametrize(("key", "model"), sorted(EXPECTED.items(), key=lambda kv: str(kv[0])))
def test_doc_example_parses_with_its_schema(
    key: tuple[str, str, int], model: type[BaseModel]
) -> None:
    doc, title, ordinal = key

    model.model_validate(json.loads(_body(doc, title, ordinal)))


def test_capsule_example_carries_the_fields_pr1_added() -> None:
    """이 두 필드가 빠져 있던 것이 이 테스트를 만든 계기다."""
    capsule = ContextCapsule.model_validate(
        json.loads(_body("knowledge-base.md", "4. Context Capsule", 0))
    )

    assert capsule.run_id
    assert capsule.expansion_count >= 0


def test_run_record_example_uses_full_length_digests() -> None:
    """`sha256:0000…` 같은 축약은 사람에게만 읽히고 스키마는 통과하지 못한다."""
    record = RunRecord.model_validate(
        json.loads(_body("agent-contracts.md", "RunRecord (재현성)", 0))
    )

    assert all(len(digest) == len("sha256:") + 64 for digest in record.model_checksums.values())
