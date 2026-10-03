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

from src.config import load_config
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


def test_finding_example_carries_trigger_status() -> None:
    """이 필드가 빠져 있던 것이 이 단언을 만든 계기다 (PR #53 리뷰).

    **원문 JSON을 본다.** 파싱된 모델을 보면 안 된다 — `trigger_status`는 기본값이 `None`인
    optional 필드라, 예시에서 빠져도 `finding.trigger_status is None`이 그대로 참이다.
    `model_validate` 한 줄로 끝나는 위 파라미터 테스트가 이 누락을 못 잡는 이유도 같다.
    """
    raw = json.loads(_body("agent-contracts.md", "Finding", 0))

    assert "trigger_status" in raw


def test_run_record_example_carries_the_profile() -> None:
    """`profile`이 `| None = None`이라 예시에서 빠져도 파싱은 통과한다 (이슈 #76).

    바로 위 `trigger_status`와 **같은 기제다** — 기본값이 있는 optional 필드는
    `model_validate` 한 줄짜리 파라미터 테스트가 누락을 못 잡는다. 그래서 원문 JSON을 본다.

    이 필드는 evaluation.md §2.3 규칙 2가 정한 비교의 기준축이라, 예시에서 사라지면
    계약 문서가 그 축을 빠뜨린 채 읽힌다.
    """
    raw = json.loads(_body("agent-contracts.md", "RunRecord (재현성)", 0))

    assert "profile" in raw


def test_run_record_example_uses_full_length_digests() -> None:
    """`sha256:0000…` 같은 축약은 사람에게만 읽히고 스키마는 통과하지 못한다."""
    record = RunRecord.model_validate(
        json.loads(_body("agent-contracts.md", "RunRecord (재현성)", 0))
    )

    assert all(len(digest) == len("sha256:") + 64 for digest in record.model_checksums.values())


def test_run_record_example_prompt_versions_match_the_profile_it_names() -> None:
    """예시의 `prompt_versions`가 예시가 **스스로 적은 `profile`**의 실제 registry 값과 같다.

    위 검사들은 스키마와 자리만 본다 — 값이 낡는 것은 못 잡는다. 이 예시는 계약 문서가 드는
    유일한 완성형 `RunRecord`라 읽는 쪽이 「이 자리에 무엇이 들어가나」를 여기서 배운다.

    **사고 —** 2026-09-29 Auditor가 v3→v4가 됐을 때는 이 줄이 따라왔는데, 2026-10-03 v4→v6
    (#418·#421)에는 **안 따라왔다.** 손으로 고치는 값이라 CI가 영영 초록이었다. 여기서
    묶으면 다음 판 올림에서 빨개진다 — 고치는 쪽이 예시도 같이 고치게 된다.
    """
    raw = json.loads(_body("agent-contracts.md", "RunRecord (재현성)", 0))
    config = load_config(raw["profile"])
    declared = raw["prompt_versions"]

    # 거르지 않는다 — `if role in profile.roles`로 걸러 두면 프로필에서 사라진 역할 이름이
    # 예시에 남아도 **조용히 건너뛰어** 초록이 된다. 없는 역할이면 여기서 터져야 한다.
    missing = sorted(set(declared) - set(config.profile.roles))
    assert not missing, f"예시가 `{raw['profile']}`에 없는 역할을 든다: {missing}"

    actual = {role: config.model_for(role).prompt_template_version for role in declared}
    assert declared == actual


def test_finding_example_produced_by_matches_the_registry() -> None:
    """Finding 예시의 `produced_by`(모델 키·프롬프트 판)도 registry와 같다.

    위 `RunRecord` 핀과 같은 이유다 — 이 값도 손으로 고치는 값이라 v4→v6(#418·#421)에서
    따라오지 않았다. `produced_by`는 하네스가 파싱 뒤 **덮어쓰는** 자리라(`context.py`의
    `ProducedBy`) 예시가 틀리면 읽는 쪽이 「무엇으로 덮이는가」를 틀리게 배운다.

    `RunRecord` 예시가 `harness_pool`을 들므로 그것을 기준으로 본다 — 두 예시가 같은 프로필을
    서술한다는 것이 이 문서의 전제다(§2 Auditor 절이 그 registry 엔트리를 든다).
    """
    finding = json.loads(_body("agent-contracts.md", "Finding", 0))
    profile = json.loads(_body("agent-contracts.md", "RunRecord (재현성)", 0))["profile"]
    config = load_config(profile)
    produced_by = finding["produced_by"]
    role = produced_by["role"]

    # 둘 다 묻는다 — `prompt_version`만 묻던 첫 판은 예시의 `"model"`을 다른 키로 바꿔도 초록이었다.
    assert produced_by["model"] == config.model_key_for(role)
    assert produced_by["prompt_version"] == config.model_for(role).prompt_template_version
