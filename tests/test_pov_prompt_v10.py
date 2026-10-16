"""PoV v10 - v9의 「도구는 항상 넷」 서술 다섯 군데를 고친 판 (2026-10-16 · 이슈 #698).

2단계 `build`가 컴파일러 오류를 잰 타깃에서는 노드가 요청 스키마의 `tool` enum에서 `build`·`asan`·
`gdb`를 뺀다(`signals.withheld_tools`·`without_tools`). v9 prefix의 「`tool` is one of poc/asan/gdb/
build」·「The four tools」·「`inspect(build)`를 쓰라」 셋(다섯 군데)은 그때 거짓이거나 오도다.
v10은 그 다섯 군데만 「`tool` 필드가 받는 값」 기준으로 고쳤다. 묶는 것은 다섯이다.

1. **v9는 한 글자도 안 바뀌었다** - prefix·body sha256(`tests/test_pov_v9.py`의 값과 같다).
2. **v10 = v9 + 이 편집 다섯** - body는 v9와 바이트 동일, prefix는 편집 목록의 결과와 바이트 동일.
3. **봉투·홉 줄·중계 옵션 선언은 v9와 같다.**
4. **배선** - `_WIDE_INPUT_VERSIONS`. (서빙 좌석·디스크 최신 판 핀은 ⏵ 2026-10-17
`tests/test_pov_v11.py`로 옮겼다.)
5. **고친 문장이 코드와 맞는다** - 실제 `signals.withheld_tools`로 좁힌 봉투가 그 세 도구를 거절하고
   하네스 노트가 prefix가 인용하는 문구를 쓴다.

⚠ 1~4는 철자·구조 핀이고 5의 문구 핀은 **철자**를 문다(성질이 아니다). 이 고침이 모델의 도구 선택을
바꾸는지는 미측정이다.
"""

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.orchestrator.nodes import pov, signals
from src.orchestrator.prompt_loader import PROMPTS_DIR, load_prompt
from src.schemas.enums import Role
from src.schemas.pov import PovActionNamed
from tests.test_compile_note import _build_failure
from tests.test_nodes import ctx  # noqa: F401
from tests.test_pov_v7 import _render, _split_facts, _text

_V9 = PROMPTS_DIR / "pov" / "v9"
_V10 = PROMPTS_DIR / "pov" / "v10"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# === 1. v9는 한 글자도 안 바뀌었다 ================================================================

_V9_SHA = {
    "prefix.md": "1a56e83831e14f0ea1f4eb3cfd9e3969324f77a075bf94a4befee507cd0792f8",
    "body.md": "962b7fe89e6fcbdd26d77ee96cb16571e9ec04d60bb467f15b4ed6cebaa22443",
}


@pytest.mark.parametrize("name", sorted(_V9_SHA))
def test_v9_is_byte_identical_to_the_recorded_values(name: str) -> None:
    assert _sha(_V9 / name) == _V9_SHA[name]


# === 2. v10 = v9 + 이 편집 다섯 ===================================================================

_V10_SHA = {
    "prefix.md": "9c51df1d2bb33f219f859e42a9bb3b2c7abfadc5f3eae8064748c5ac4507dd6e",
    "body.md": "962b7fe89e6fcbdd26d77ee96cb16571e9ec04d60bb467f15b4ed6cebaa22443",
}
_EDITS = (
    (  # DEF-SUBMIT 마지막 항목
        'on the last hop emit another action such as `inspect(build)` ("Hop budget").\n',
        "on the last hop emit another action such as `inspect(build)` (or an `inspect` of another "
        'tool the `tool` field accepts) ("Hop budget").\n',
    ),
    (  # `inspect` 항목의 도구 목록
        "`tool` is one of `poc` / `asan` / `gdb` / `build`. Two harness rules:\n",
        "`tool` is one of `poc` / `asan` / `gdb` / `build` - the values the `tool` field accepts. "
        "When the harness note (stage 2) says the harness does not offer `build`, `asan` or `gdb` "
        "for this target, the field does not accept them and only `poc` remains. "
        "Two harness rules:\n",
    ),
    (  # 절 제목
        "### The four tools split into two groups\n",
        "### The tools split into two groups\n",
    ),
    (  # Hop budget `remaining=0`
        "if nothing may be submitted, emit another action such as `inspect(build)`.\n",
        "if nothing may be submitted, emit another action such as `inspect(build)` (or an "
        "`inspect` of another tool the `tool` field accepts).\n",
    ),
    (  # When stuck
        "If you are unsure the target builds at all, use `inspect(build)`.\n",
        "If you are unsure the target builds at all, use `inspect(build)` - unless the harness "
        "note says `build` is not offered for this target: stage 2 already reported compiler "
        "errors there.\n",
    ),
)


@pytest.mark.parametrize("name", sorted(_V10_SHA))
def test_v10_files_match_their_recorded_sha(name: str) -> None:
    assert _sha(_V10 / name) == _V10_SHA[name]


def test_the_v10_meta_records_the_same_sha_the_test_pins() -> None:
    meta = _text(_V10 / "meta.yaml")
    for digest in _V10_SHA.values():
        assert f"sha256 `{digest}`" in meta


def test_v10_body_is_the_v9_body_byte_for_byte() -> None:
    assert (_V10 / "body.md").read_bytes() == (_V9 / "body.md").read_bytes()


def test_v10_prefix_is_the_v9_prefix_with_exactly_the_recorded_edits() -> None:
    """줄 단위가 아니라 **바이트 등식**이다 - 편집 밖에 한 글자라도 다르면 죽는다."""
    expected = _text(_V9 / "prefix.md")
    for old, new in _EDITS:
        assert expected.count(old) == 1, old
        expected = expected.replace(old, new)
    assert expected == _text(_V10 / "prefix.md")
    assert b"\r" not in (_V10 / "prefix.md").read_bytes()


# === 3. 봉투·홉 줄·중계 옵션 선언은 v9와 같다 =====================================================


def test_the_v10_envelope_and_relayed_option_declaration_are_the_v9_ones() -> None:
    v9, v10 = load_prompt(Role.POV, "v9"), load_prompt(Role.POV, "v10")
    assert (v10.output_schema, v10.hop_header, v10.input) == (
        v9.output_schema, v9.hop_header, v9.input,
    )
    assert v10.source_options == v9.source_options, "선언이 같아야 중계 옵션 가드가 선다"


# === 4. 배선 =====================================================================================


def test_v10_is_in_the_wide_input_set() -> None:
    """빠지면 `oracle_vul` 줄·출력 꼬리·`last_gen_*`·진입점 사실·**하네스 노트**가 조용히 빠진다."""
    assert "v10" in pov._WIDE_INPUT_VERSIONS


def test_v10_gets_the_same_wide_inputs_as_v9(ctx) -> None:  # noqa: ANN001, F811
    """같은 Finding·SOURCE·진입점이면 body(FACTS 포함)가 바이트 동일하고 prefix만 다르다."""
    source = (
        "/* ===== parser.c:3-6 ===== */\n6|     memcpy(buf, src, len);\n"
        "/* ... +2 more write windows not shown ... */"
    )
    v9, prepared = _render(ctx, "v9", source)
    v10, _ = _render(ctx, "v10", source, prepared)

    assert v10.body == v9.body
    assert _split_facts(v10.body)[1] == _split_facts(v9.body)[1]
    assert v10.prefix != v9.prefix and v10.prompt_version == "v10"


# ⏵ 2026-10-17 — 「서빙 좌석이 v10을 고른다」·「디스크의 최신 판이 v10이다」 두 핀은 v11이 서빙 판이
# 되며
# `tests/test_pov_v11.py`로 옮겼다(이슈 #704).


# === 5. 고친 문장이 코드와 맞는다 =================================================================


def test_the_corrected_tool_sentence_matches_what_the_request_schema_does() -> None:
    """`build`가 컴파일러 오류를 잰 타깃의 요청 스키마는 `poc`만 받는다 - v10 prefix의 서술이다."""
    withheld = signals.withheld_tools([_build_failure()])
    assert withheld == ("build", "asan", "gdb")
    narrowed = signals.without_tools(PovActionNamed, withheld)

    assert narrowed.__name__ == PovActionNamed.__name__, "요청의 스키마 이름이 종전과 같다"
    for tool in withheld:
        with pytest.raises(ValidationError):
            narrowed.model_validate({"action": "inspect", "rationale": "r", "tool": tool})
        PovActionNamed.model_validate({"action": "inspect", "rationale": "r", "tool": tool})
    action = narrowed.model_validate({"action": "inspect", "rationale": "r", "tool": "poc"})
    assert action.tool == "poc"


def test_the_prefix_quotes_the_wording_the_harness_note_really_uses() -> None:
    """prefix가 인용하는 하네스 노트 문구를 실제 `compile_note`가 쓴다. 한계: 철자 핀이다."""
    note = signals.compile_note([_build_failure()], ".")
    assert note is not None
    assert "the harness does not offer build, asan or gdb" in note
    prefix = _text(_V10 / "prefix.md")
    assert "the harness does not offer `build`, `asan` or `gdb` for this target" in prefix
    assert "the field does not accept them and only `poc` remains" in prefix
    # 도구를 안 뺀 타깃의 서술(넷 모두 제시)은 남아 있다
    assert "`tool` is one of `poc` / `asan` / `gdb` / `build`" in prefix
    assert "four tools" not in prefix
