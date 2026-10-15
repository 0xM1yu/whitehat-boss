"""PoV v8 - v7에 prefix 한 줄을 더한 판 (S20 Phase 15 · ADR 0013 2026-10-15 개정).

v7은 「submit 없이 끝나면 최종 후보가 없다」고만 적었다. 개정 뒤에는 하네스가 그런 Finding의 크래시
계단 중간 gen을 (같은 응답 코드 계단 안의) 제출된 후보보다 아래 순위로 구제할 수 있다
(`oracle_log.OracleLedger.rescued`) - v8은
「Hop budget」의 그 한 줄에 그 사실과 「기대지 말라」를 더했다. 묶는 것은 다섯이다.

1. **v7은 한 글자도 안 바뀌었다** - prefix·body sha256(`tests/test_pov_v7.py`의 값과 같다).
2. **v8 = v7 + 이 편집 하나** - body는 v7과 바이트 동일, prefix는 편집 한 곳만 다르다.
3. **문장이 코드와 맞는다** - 「구제된 gen은 제출된 후보 아래」는 `_designation_key`의 둘째 성분이고
   「자기 vul 응답이 크래시」는 `vul_code_tier`의 크래시 계단이다.
4. **배선** - `_WIDE_INPUT_VERSIONS` · 서빙 좌석 · doctor · 중계 옵션 선언(`source_options`)은
   v7과 같다.
5. **v8보다 새 판이 디스크에 없다.**

⚠ 철자·구조 핀이다 - 추가 문장이 모델의 submit 시점을 늦추는지는 미측정이다.
"""

import hashlib
from pathlib import Path

import pytest

from src.orchestrator import oracle_log, runner
from src.orchestrator.nodes import pov
from src.orchestrator.prompt_loader import PROMPTS_DIR, load_prompt
from src.schemas.enums import Role
from tests.test_nodes import ctx  # noqa: F401
from tests.test_pov_v7 import _render, _split_facts, _text

_V7 = PROMPTS_DIR / "pov" / "v7"
_V8 = PROMPTS_DIR / "pov" / "v8"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# === 1. v7은 한 글자도 안 바뀌었다 ================================================================

_V7_SHA = {
    "prefix.md": "38be10c45410e26513817a807961faacd22c32959e9cb25b5c82403b8e6a72ce",
    "body.md": "962b7fe89e6fcbdd26d77ee96cb16571e9ec04d60bb467f15b4ed6cebaa22443",
}


@pytest.mark.parametrize("name", sorted(_V7_SHA))
def test_v7_is_byte_identical_to_the_recorded_values(name: str) -> None:
    assert _sha(_V7 / name) == _V7_SHA[name]


# === 2. v8 = v7 + 이 편집 하나 ====================================================================

_V8_SHA = {
    "prefix.md": "192dc978fa02097a5e5d76854e6dae267f11f7ff42db2c629b465f2faa145395",
    "body.md": "962b7fe89e6fcbdd26d77ee96cb16571e9ec04d60bb467f15b4ed6cebaa22443",
}
_HOP_OLD = "- A loop that exhausts all hops without a `submit` produces no final candidate.\n"
_HOP_NEW = (
    "- A loop that exhausts all hops without a `submit` produces no final candidate. "
    "Submit is how you choose. If a Finding ends with no `submit`, the harness may fall back to "
    "an intermediate `gen` whose own vul response was a crash (not `0`, `71`, `300`, a timeout, "
    "or an environment-failure code); within the same scorer-code group it ranks below "
    "submitted candidates — do not rely on it.\n"
)


@pytest.mark.parametrize("name", sorted(_V8_SHA))
def test_v8_files_match_their_recorded_sha(name: str) -> None:
    assert _sha(_V8 / name) == _V8_SHA[name]


def test_the_v8_meta_records_the_same_sha_the_test_pins() -> None:
    meta = _text(_V8 / "meta.yaml")
    for digest in _V8_SHA.values():
        assert f"sha256 `{digest}`" in meta


def test_v8_body_is_the_v7_body_byte_for_byte() -> None:
    assert (_V8 / "body.md").read_bytes() == (_V7 / "body.md").read_bytes()


def test_v8_prefix_is_the_v7_prefix_with_exactly_the_recorded_edit() -> None:
    """줄 단위가 아니라 **바이트 등식**이다 - 편집 밖에 한 글자라도 다르면 죽는다."""
    v7 = _text(_V7 / "prefix.md")
    assert v7.count(_HOP_OLD) == 1
    assert v7.replace(_HOP_OLD, _HOP_NEW) == _text(_V8 / "prefix.md")
    assert b"\r" not in (_V8 / "prefix.md").read_bytes()


def test_the_v8_envelope_and_relayed_option_declaration_are_the_v7_ones() -> None:
    v7, v8 = load_prompt(Role.POV, "v7"), load_prompt(Role.POV, "v8")
    assert (v8.output_schema, v8.hop_header, v8.input) == (
        v7.output_schema, v7.hop_header, v7.input,
    )
    assert v8.source_options == v7.source_options == (
        "auditor_write_window_marker", "auditor_input_context",
    ), "v8이 SOURCE 블록을 서술하는 머리 문단을 그대로 물려받았다 - 선언도 같아야 가드가 선다"


# === 3. 문장이 코드와 맞는다 ======================================================================


def test_the_v8_sentence_matches_the_rescue_rule_in_the_runner() -> None:
    """① 구제 대상은 `vul_code_tier`의 크래시 계단이다(환경 값·0·71은 아니다) ② 구제된 gen은 같은
    계단에서 제출된 후보 **아래**다 - `_designation_key`의 둘째 성분(0 제출 < 1 구제)이고 근거
    이름이 사슬 둘째 자리에 있다. ⚠ 철자 핀 + 규칙 핀이다 - 규칙의 행동은
    `tests/test_oracle_rescue.py`다."""
    tier = oracle_log.vul_code_tier
    assert tier(139) == oracle_log.VUL_TIER_CRASH
    assert tier(0) == tier(71) == oracle_log.VUL_TIER_NO_CRASH
    assert tier(137) == tier(None) == oracle_log.VUL_TIER_UNKNOWN
    assert runner._DESIGNATION_CHAIN[1] == oracle_log.DESIGNATION_SUBMITTED_OVER_RESCUED

    flat = " ".join(_text(_V8 / "prefix.md").split())
    assert "Submit is how you choose." in flat
    assert "whose own vul response was a crash (not `0`, `71`, `300`, a timeout" in flat
    assert "within the same scorer-code group it ranks below submitted candidates" in flat
    assert "ranked below any submitted" not in flat  # 계단이 먼저라 그 서술은 거짓이다
    assert "do not rely on it" in flat
    assert "may fall back" not in " ".join(_text(_V7 / "prefix.md").split())


def test_the_crash_then_submit_instruction_is_still_there() -> None:
    """추가 문장이 「크래시 뒤엔 바로 submit」 지시를 지우지 않았다."""
    flat = " ".join(_text(_V8 / "prefix.md").split())
    assert "**`submit` on the very next turn**" in flat


# === 4. 배선 =====================================================================================


def test_v8_is_in_the_wide_input_set() -> None:
    """빠지면 `oracle_vul` 줄·출력 꼬리·`last_gen_*`·진입점 사실이 **조용히** 사라진다."""
    assert "v8" in pov._WIDE_INPUT_VERSIONS


def test_v8_gets_the_same_wide_inputs_as_v7(ctx) -> None:  # noqa: ANN001, F811
    """같은 Finding·SOURCE·진입점이면 body(FACTS 포함)가 바이트 동일하고 prefix만 다르다."""
    source = (
        "/* ===== parser.c:3-6 ===== */\n6|     memcpy(buf, src, len);\n"
        "/* ... +2 more write windows not shown ... */"
    )
    v7, prepared = _render(ctx, "v7", source)
    v8, _ = _render(ctx, "v8", source, prepared)

    assert v8.body == v7.body
    assert _split_facts(v8.body)[1] == _split_facts(v7.body)[1]
    assert "fuzz_entrypoint" in _split_facts(v8.body)[1]
    assert v8.prefix != v7.prefix and v8.prompt_version == "v8"


# ⏵ 2026-10-15 - 서빙 좌석이 v9로 올라갔다. 「서빙 좌석이 v8을 고른다」·
# 「v8보다 새 판이 디스크에 없다」 핀은 `tests/test_pov_v9.py`로 옮겼다
# (v8 파일은 그대로다 - 위 sha 핀).
