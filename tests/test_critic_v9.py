"""Critic v9 - v8에서 `rebut`의 정의 한 줄과 반박 축 문단 하나만 고친 판 (이슈 #706).

v8의 `rebut`은 이미 절대값이다(5절: 축마다 약점이 필요로 하는 조건 · `rebut`은 그 조건이 성립
못 한다). 그런데 Role 절의 「cannot occur as the Auditor decided」는 Auditor 결론에 대한 상대값으로
읽힐 수 있다 - Critic 입력에 `trigger_status`가 없는데도(`critic._describe_claim`).
묶는 것은 넷이다.

1. **v8은 한 글자도 안 바뀌었다** - prefix·body sha256(`prompts/critic/v8/meta.yaml`의 기록).
2. **v9 = v8 + 이 편집 둘** - body는 바이트 동일이고 prefix는 `_OLD_REBUT`·`_AFTER_AXES` 자리에
   편집을 넣은 결과와 바이트 동일이다(판정 절차·출력 규칙·축 어휘 불변).
3. **새 문단이 드는 축 이름은 어휘 안이다** - 예시가 어휘 밖 철자면 모델이 그것을 따라 적어
   `invalid_axis`로 정규화된다(ADR 0015).
4. **배선** - 세 집합 · 서빙 좌석 · doctor · v8과 같은 입력.

⚠ 철자·구조 핀이다 - 이 문장이 판정–근거 불일치(rationale은 「취약」인데 `rebut`)를 줄이는지는
미측정이다(v9 run 0건).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from src import cli
from src.config import load_config
from src.orchestrator.nodes import critic
from src.orchestrator.prompt_loader import PROMPTS_DIR, load_prompt
from src.schemas.decision import AXIS_ONLY_GROUNDING_VERSIONS
from src.schemas.enums import RebuttalAxis, Role
from tests import test_nodes
from tests.test_critic_v8 import _SOURCE_WITH_BOTH_BLOCKS, _render

ctx = test_nodes.ctx

_V8 = PROMPTS_DIR / "critic" / "v8"
_V9 = PROMPTS_DIR / "critic" / "v9"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _text(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


_V8_SHA = {
    "prefix.md": "e7cef14b7dca3072206c8853ccf09cbafbc6fe8d0bc8bb69c657d4558ab4c55f",
    "body.md": "44860ed579585c49b351edc2ab3cbe1cd56bf54c0b073d0810f5015edd72bda2",
}
_V9_SHA = {
    "prefix.md": "c9c53207a6da6710f897f95eeb33af899219bf51ea3431f91120aa5edc83deb6",
    "body.md": "44860ed579585c49b351edc2ab3cbe1cd56bf54c0b073d0810f5015edd72bda2",
}

_OLD_REBUT = (
    "- rebut: SOURCE shows that the weakness at the PATH sink step cannot occur as the Auditor "
    "decided (a decisive failure, step 5);\n"
)
_NEW_REBUT = (
    "- rebut: SOURCE shows that the weakness named in CLAIM cannot occur at the PATH sink step "
    "(a decisive failure, step 5);\n"
)
_AFTER_AXES = "- `sink`: the operation at the sink step is dangerous with that value (Check E).\n"
_AXES_PARAGRAPH = (
    "\n"
    "Each axis names a condition that the weakness needs. A rebuttal axis names a condition that "
    "SOURCE shows cannot hold, so rebut always means that the weakness does not occur. For "
    'example, rebuttal_axis ["guard"] states that a guard in SOURCE blocks every triggering '
    'value, and ["arithmetic"] states that the arithmetic cannot produce the dangerous result. '
    "A rationale that concludes a condition can hold, such as a guard that does not block a "
    "triggering value or a divisor that can be zero, supports uphold, not rebut.\n"
)


@pytest.mark.parametrize("name", sorted(_V8_SHA))
def test_v8_is_byte_identical_to_its_recorded_values(name: str) -> None:
    assert _sha(_V8 / name) == _V8_SHA[name]


@pytest.mark.parametrize("name", sorted(_V9_SHA))
def test_v9_files_match_their_recorded_sha(name: str) -> None:
    assert _sha(_V9 / name) == _V9_SHA[name]


def test_the_v9_meta_records_the_same_sha_the_test_pins() -> None:
    meta = _text(_V9 / "meta.yaml")
    for name, digest in _V9_SHA.items():
        assert f"sha256 `{digest}`" in meta, name


def test_v9_body_is_the_v8_body_bytes() -> None:
    assert (_V9 / "body.md").read_bytes() == (_V8 / "body.md").read_bytes()


def test_v9_prefix_is_the_v8_prefix_with_exactly_the_two_recorded_edits() -> None:
    """**바이트 등식**이다 - 편집 밖에 한 글자라도 다르면 죽는다."""
    v8 = _text(_V8 / "prefix.md")
    assert v8.count(_OLD_REBUT) == 1
    assert v8.count(_AFTER_AXES) == 1

    expected = v8.replace(_OLD_REBUT, _NEW_REBUT).replace(
        _AFTER_AXES, _AFTER_AXES + _AXES_PARAGRAPH
    )

    assert _text(_V9 / "prefix.md") == expected


def test_v9_no_longer_ties_rebut_to_the_auditor_decision() -> None:
    """Critic은 Auditor 결론을 못 본다 - 그것에 기대 `rebut`을 정의하는 문장이 남으면 안 된다."""
    assert "as the Auditor decided" not in _text(_V9 / "prefix.md")


def test_every_axis_the_new_paragraph_names_is_in_the_vocabulary() -> None:
    named = re.findall(r'\["([^"\]]+)"\]', _AXES_PARAGRAPH)

    assert named == ["guard", "arithmetic"]
    assert all(RebuttalAxis.from_text(axis) is not None for axis in named)


def test_v9_is_in_every_critic_version_set() -> None:
    """빠지면 근거 본문·위치가 조용히 사라지거나(content-aware) SOURCE 슬롯이 안 채워지거나
    ID만 든 `rebut`이 조용히 근거가 된다(축 근거 판)."""
    assert "v9" in critic._CONTENT_AWARE_VERSIONS
    assert "v9" in critic._SOURCE_INPUT_VERSIONS
    assert "v9" in AXIS_ONLY_GROUNDING_VERSIONS


def test_v9_gets_the_same_request_as_v8_apart_from_the_prefix(ctx) -> None:
    v8 = _render(ctx, "v8", _SOURCE_WITH_BOTH_BLOCKS)
    v9 = _render(ctx, "v9", _SOURCE_WITH_BOTH_BLOCKS)

    assert v9.body == v8.body
    assert v9.prefix == load_prompt(Role.CRITIC, "v9").prefix != v8.prefix
    assert v9.prompt_version == "v9"


@pytest.mark.parametrize("profile", ["harness_pool", "harness_pool_hybrid"])
def test_the_serving_critic_seat_selects_v9_and_the_doctor_has_nothing_to_say(profile: str) -> None:
    config = load_config(profile)
    version = config.model_for("critic").prompt_template_version

    assert version == "v9"
    assert cli._check_prompt_version(config, "critic", version) == [], (
        "내용 인식 ERROR도 디스크 새 판 WARN도 없다"
    )


def test_no_critic_version_newer_than_the_served_one_is_on_disk() -> None:
    numbers = sorted(
        int(p.name[1:])
        for p in (PROMPTS_DIR / "critic").iterdir()
        if p.is_dir() and p.name[1:].isdecimal()
    )
    assert numbers[-1] == 9
