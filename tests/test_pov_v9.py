"""PoV v9 - v8에서 과제 고유 사실 한 줄을 일반화한 판 (2026-10-15).

v8 DEF-EXIT의 `77` 줄은 *"the one scored PoC on record in the 2026-10-08 run (arvo:1065, MSan)"*처럼
**우리 지난 run이 같은 과제에서 본 결과**를 모델에 줬다 - CyberGym Level 1 입력(취약 저장소 +
description.txt) 밖의 정보라 그 과제를 다시 돌리면 점수가 오염된다.
v9는 그 줄만 일반화했다. 묶는 것은 넷이다.

1. **v8은 한 글자도 안 바뀌었다** - prefix·body sha256(`tests/test_pov_v8.py`의 값과 같다).
2. **v9 = v8 + 이 편집 하나** - body는 v8과 바이트 동일, prefix는 그 한 줄만 다르다.
3. **v9 prefix에 과제 ID·run 날짜가 없다** - 판정 규칙(`77`은 제외 코드가 아니다)은 남는다.
4. **배선** - `_WIDE_INPUT_VERSIONS` · 봉투 · 중계 옵션 선언은 v8과 같다. ⏵ 2026-10-16 서빙 좌석·
   doctor·「디스크의 최신 판」 핀은 v10으로 옮겼다(`tests/test_pov_prompt_v10.py`).

⚠ 철자·구조 핀이다 - 그 줄을 빼는 것이 모델이 `77`을 읽는 방식을 바꾸는지는 미측정이다.
"""

import hashlib
import re
from pathlib import Path

import pytest

from src.orchestrator.nodes import pov
from src.orchestrator.prompt_loader import PROMPTS_DIR, load_prompt
from src.schemas.enums import Role
from tests.test_nodes import ctx  # noqa: F401
from tests.test_pov_v7 import _render, _split_facts, _text

_V8 = PROMPTS_DIR / "pov" / "v8"
_V9 = PROMPTS_DIR / "pov" / "v9"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# === 1. v8은 한 글자도 안 바뀌었다 ================================================================

_V8_SHA = {
    "prefix.md": "192dc978fa02097a5e5d76854e6dae267f11f7ff42db2c629b465f2faa145395",
    "body.md": "962b7fe89e6fcbdd26d77ee96cb16571e9ec04d60bb467f15b4ed6cebaa22443",
}


@pytest.mark.parametrize("name", sorted(_V8_SHA))
def test_v8_is_byte_identical_to_the_recorded_values(name: str) -> None:
    assert _sha(_V8 / name) == _V8_SHA[name]


# === 2. v9 = v8 + 이 편집 하나 ====================================================================

_V9_SHA = {
    "prefix.md": "1a56e83831e14f0ea1f4eb3cfd9e3969324f77a075bf94a4befee507cd0792f8",
    "body.md": "962b7fe89e6fcbdd26d77ee96cb16571e9ec04d60bb467f15b4ed6cebaa22443",
}
_LINE_OLD = (
    "- `77` is not an exclusion code. It was the `vul` code of the one scored PoC on record in the "
    "2026-10-08 run (arvo:1065, MSan) and may be libFuzzer's error exit; one other sanitizer build "
    "ended in `1` (oss-fuzz:368076871).\n"
)
_LINE_NEW = (
    "- `77` is not an exclusion code; it may be libFuzzer's error exit, "
    "and other sanitizer builds can end in `1`.\n"
)


@pytest.mark.parametrize("name", sorted(_V9_SHA))
def test_v9_files_match_their_recorded_sha(name: str) -> None:
    assert _sha(_V9 / name) == _V9_SHA[name]


def test_the_v9_meta_records_the_same_sha_the_test_pins() -> None:
    meta = _text(_V9 / "meta.yaml")
    for digest in _V9_SHA.values():
        assert f"sha256 `{digest}`" in meta


def test_v9_body_is_the_v8_body_byte_for_byte() -> None:
    assert (_V9 / "body.md").read_bytes() == (_V8 / "body.md").read_bytes()


def test_v9_prefix_is_the_v8_prefix_with_exactly_the_recorded_edit() -> None:
    """줄 단위가 아니라 **바이트 등식**이다 - 편집 밖에 한 글자라도 다르면 죽는다."""
    v8 = _text(_V8 / "prefix.md")
    assert v8.count(_LINE_OLD) == 1
    assert v8.replace(_LINE_OLD, _LINE_NEW) == _text(_V9 / "prefix.md")
    assert b"\r" not in (_V9 / "prefix.md").read_bytes()


# === 3. 과제 ID·run 날짜가 없다 ===================================================================

# 양성 출처: `prompts/pov/v8/prefix.md:15`의 `arvo:1065`·`oss-fuzz:368076871`·`2026-10-08`.
_TASK_FACT = re.compile(r"\b(?:arvo|oss-fuzz):\d+|\b20\d\d-\d\d-\d\d\b")


def test_the_task_fact_pattern_catches_the_v8_line() -> None:
    """양성 핀 - v8의 실제 줄을 잡는다. 이것이 초록이어야 아래 음성 단언이 뜻을 가진다."""
    assert len(_TASK_FACT.findall(_text(_V8 / "prefix.md"))) == 3


def test_v9_prefix_carries_no_task_id_or_run_date_but_keeps_the_77_rule() -> None:
    prefix = _text(_V9 / "prefix.md")
    assert _TASK_FACT.findall(prefix) == []
    assert "`77` is not an exclusion code" in prefix
    assert "The exclusion codes are `0`, `71`, and `300`." in prefix  # 판정 규칙은 그대로다


# === 4. 배선 =====================================================================================


def test_v9_is_in_the_wide_input_set() -> None:
    """빠지면 `oracle_vul` 줄·출력 꼬리·`last_gen_*`·진입점 사실이 **조용히** 사라진다."""
    assert "v9" in pov._WIDE_INPUT_VERSIONS


def test_the_v9_envelope_and_relayed_option_declaration_are_the_v8_ones() -> None:
    v8, v9 = load_prompt(Role.POV, "v8"), load_prompt(Role.POV, "v9")
    assert (v9.output_schema, v9.hop_header, v9.input) == (
        v8.output_schema, v8.hop_header, v8.input,
    )
    assert v9.source_options == v8.source_options, "선언이 같아야 중계 옵션 가드가 선다"


def test_v9_gets_the_same_wide_inputs_as_v8(ctx) -> None:  # noqa: ANN001, F811
    """같은 Finding·SOURCE·진입점이면 body(FACTS 포함)가 바이트 동일하고 prefix만 다르다."""
    source = (
        "/* ===== parser.c:3-6 ===== */\n6|     memcpy(buf, src, len);\n"
        "/* ... +2 more write windows not shown ... */"
    )
    v8, prepared = _render(ctx, "v8", source)
    v9, _ = _render(ctx, "v9", source, prepared)

    assert v9.body == v8.body
    assert _split_facts(v9.body)[1] == _split_facts(v8.body)[1]
    assert v9.prefix != v8.prefix and v9.prompt_version == "v9"


# ⏵ 2026-10-16 - 서빙 좌석이 v10으로 올라갔다(이슈 #698). 「서빙 좌석이 v9를 고른다」·
# 「v9보다 새 판이 디스크에 없다」 핀은 `tests/test_pov_prompt_v10.py`로 옮겼다
# (v9 파일은 그대로다 - 위 sha 핀).
