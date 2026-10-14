"""PoV v7 - v6에서 셋을 고친 판 (S20 Phase 14 · 이슈 #649 선택지 (a) · #669).

① body의 「The JSON below is the complete set of facts given as input.」는 거짓이었다 -
Finding의 CWE·제목·경로 단계·촉발 상태는 FACTS 밖 산문 블록(`{{FINDING}}`)에만 있고
FACTS에는 `ids.finding_id`뿐이다.
② Auditor `SOURCE`가 PoV에도 가므로(`runner.finding_sources`) 새 블록 둘(입력 디코더 함수 ·
#650 끝 줄 표지)을 prefix가 하네스 생성 텍스트·보조 문맥으로 서술한다.
③ DEF-SUBMIT의 최종 지정 문장 - #637 뒤 v6 문장이 거짓이라 다시 썼다. 묶는 것은 다섯이다.

1. **v6은 한 글자도 안 바뀌었다** - prefix·body sha256(`tests/test_pov_prompt_v6.py` 헤더의
   `7df25b5c…`와 같다).
2. **v7 = v6 + 이 편집 셋** - prefix·body 모두 `_PREFIX_*`·`_BODY_*` 편집을 v6에 적용한 결과와
   바이트 동일이다.
3. **문장이 코드와 맞는다** - 「Finding 정보는 Vulnerability 절에만 있고 JSON은 Finding ID만
   든다」를 실제 렌더로 문다(산문에 제목·경로가 있고 FACTS JSON에는 없다). 표지 줄 철자는
   러너 상수와 같다.
4. **배선** - `_WIDE_INPUT_VERSIONS` · 서빙 좌석 · doctor · v6과 같은 넓은 입력(FACTS 키 ·
   SOURCE).

⚠ 철자·구조 핀이다 - PoV가 Finding 산문을 더 읽는지·디코더 블록을 어떻게 쓰는지는 미측정이다.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from src import cli
from src.config import load_config
from src.orchestrator import runner
from src.orchestrator.nodes import pov
from src.orchestrator.prompt_loader import PROMPTS_DIR, load_prompt
from src.schemas.enums import Role
from tests import test_nodes
from tests.test_nodes import _pov_state

ctx = test_nodes.ctx

_V6 = PROMPTS_DIR / "pov" / "v6"
_V7 = PROMPTS_DIR / "pov" / "v7"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _text(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


# === 1. v6은 한 글자도 안 바뀌었다 ================================================================

_V6_SHA = {
    "prefix.md": "7df25b5c667fda203e211d26beb543503ac87c029dd43627f64d6c1eff972c31",
    "body.md": "3b6a2a447b52af84eaddf0743795a86918bf3b6453acd8227fdf9ab5a8487eac",
}


@pytest.mark.parametrize("name", sorted(_V6_SHA))
def test_v6_is_byte_identical_to_the_recorded_values(name: str) -> None:
    assert _sha(_V6 / name) == _V6_SHA[name]


# === 2. v7 = v6 + 이 편집 셋 ======================================================================

_V7_SHA = {
    "prefix.md": "38be10c45410e26513817a807961faacd22c32959e9cb25b5c82403b8e6a72ce",
    "body.md": "962b7fe89e6fcbdd26d77ee96cb16571e9ec04d60bb467f15b4ed6cebaa22443",
}
_BODY_OLD = (
    "The JSON below is the complete set of facts given as input. "
    "Do not invent anything that is not here.\n"
)
_BODY_NEW = (
    "Input includes the Vulnerability, Target, and Source sections above and the JSON "
    "below. The JSON holds structured facts; its only Finding data is `finding_id`, and "
    "the Finding's CWE, title, path steps, and trigger status appear in the Vulnerability "
    "section alone. Do not invent anything that is in none of the input.\n"
)
_PREFIX_OLD = "## How to reach the vulnerable path\n1. **Start small.**"
_PREFIX_NEW = (
    "## How to reach the vulnerable path\n"
    "SOURCE can end with functions the slicer picked as likely readers of the program's input; "
    "check that such a function is called on the path before relying on it. Banners "
    "(`/* ===== file:start-end ===== */`), omitted-region markers, and a final "
    "`/* ... +N more write windows not shown ... */` line are harness-generated text, not "
    "code: that last line only says N more write-site windows existed than SOURCE shows.\n"
    "\n"
    "1. **Start small.**"
)

_SLOT_OLD = (
    "- Why a non-crash is not submitted: the final candidate competes with other Findings' "
    "PoCs (which may be scorer-confirmed) for the run's single slot, and the harness does "
    "not choose that slot by scorer code — so an unconfirmed PoC can beat a confirmed one."
)
_SLOT_NEW = (
    "- Why a non-crash is not submitted: the run scores only one final PoC. The harness "
    "ranks the candidates first by the scorer code each one's own submission received — a "
    "crash, then an unavailable or environment-failure code, then `0` or `71` — and only "
    "candidates in the same group fall back to its other ranking. So a PoC whose code is "
    "among the three excluded codes (DEF-EXIT) can still become final when no other "
    "candidate has a better code, and such a code does not count as a reproduction."
)
_PREFIX_EDITS = ((_PREFIX_OLD, _PREFIX_NEW), (_SLOT_OLD, _SLOT_NEW))


@pytest.mark.parametrize("name", sorted(_V7_SHA))
def test_v7_files_match_their_recorded_sha(name: str) -> None:
    assert _sha(_V7 / name) == _V7_SHA[name]


def test_the_v7_meta_records_the_same_sha_the_test_pins() -> None:
    meta = _text(_V7 / "meta.yaml")
    for digest in _V7_SHA.values():
        assert f"sha256 `{digest}`" in meta


def test_v7_body_is_the_v6_body_with_exactly_the_recorded_edit() -> None:
    """줄 단위가 아니라 **바이트 등식**이다 - 편집 밖에 한 글자라도 다르면 죽는다."""
    v6 = _text(_V6 / "body.md")
    assert v6.count(_BODY_OLD) == 1
    assert v6.replace(_BODY_OLD, _BODY_NEW) == _text(_V7 / "body.md")


def test_v7_prefix_is_the_v6_prefix_with_exactly_the_recorded_edits() -> None:
    v6 = _text(_V6 / "prefix.md")
    for old, new in _PREFIX_EDITS:
        assert v6.count(old) == 1, old[:50]
        v6 = v6.replace(old, new)
    assert v6 == _text(_V7 / "prefix.md")
    assert b"\r" not in (_V7 / "prefix.md").read_bytes()


def test_the_v7_envelope_is_the_v6_envelope() -> None:
    v6, v7 = load_prompt(Role.POV, "v6"), load_prompt(Role.POV, "v7")
    assert (v7.output_schema, v7.hop_header, v7.input) == (
        v6.output_schema, v6.hop_header, v6.input,
    )


# === 3. 문장이 코드와 맞는다 ======================================================================


def _pov_at(context, version: str) -> None:
    key = context.config.model_key_for("pov")
    entry = context.config.models[key].transition(prompt_template_version=version)
    context.config = context.config.transition(
        models={**context.config.models, "fake_pov_probe": entry},
        profile=context.config.profile.transition(
            roles={**context.config.profile.roles, "pov": "fake_pov_probe"}
        ),
    )


def _render(context, version: str, source: str, prepared=None):
    """`prepared`(= 앞 호출이 낸 (finding, capsule))를 주면 같은 Finding으로 다시 렌더한다."""
    if prepared is None:
        state, finding = _pov_state(context)
        prepared = (finding, state.finding_capsules[finding.finding_id])
    finding, capsule = prepared
    _pov_at(context, version)
    prompt = pov._render_prompt(
        context, finding=finding, capsule=capsule, source=lambda: source,
        fuzz_entrypoint={"found": False, "source": "", "reason": "test"},
    )
    return prompt, prepared


def _split_facts(body: str) -> tuple[str, dict]:
    """(마지막 json 펜스 앞의 산문, FACTS)."""
    match = list(re.finditer(r"```json\s*(\{.*?\})\s*```", body, re.DOTALL))[-1]
    return body[: match.start()], json.loads(match.group(1))


def test_the_finding_prose_is_outside_facts_and_facts_carries_only_the_finding_id(ctx) -> None:
    """v7 body가 적은 두 사실을 실제 렌더로 문다: Finding의 제목·경로 심볼은 FACTS 밖 산문에만 있고
    FACTS의 Finding 식별은 `ids.finding_id` 하나다."""
    prompt, (finding, _) = _render(ctx, "v7", "int f(void) { return 0; }")
    prose, facts = _split_facts(prompt.body)

    assert finding.title in prose
    # v7 body는 「CWE는 Vulnerability 절에만 있다」고 적는다 - 머리줄에서 CWE를 빼는 변이를
    # 문다(리뷰 #676 LOW).
    assert f"CWE: {pov._cwe_label(finding)}" in prose
    assert "cwe" not in facts and "cwe" not in json.dumps(facts, ensure_ascii=False).lower()
    assert finding.path, "경로가 없는 Finding으론 이 핀이 의미가 없다"
    assert all(step.symbol in prose for step in finding.path)
    assert facts["ids"] == {"finding_id": finding.finding_id}
    facts_json = json.dumps(facts, ensure_ascii=False)
    assert finding.title not in facts_json
    assert "trigger_status" not in facts and "path" not in facts and "title" not in facts


def test_v6_makes_the_false_claim_and_v7_does_not(ctx) -> None:
    v6, prepared = _render(ctx, "v6", "int f(void) { return 0; }")
    v7, _ = _render(ctx, "v7", "int f(void) { return 0; }", prepared)

    assert "is the complete set of facts given as input" in v6.body
    assert "complete set of facts" not in v7.body
    assert "its only Finding data is `finding_id`" in v7.body


def test_the_marker_line_v7_names_is_the_one_the_runner_appends() -> None:
    marker = runner._WRITE_WINDOWS_MORE.format("N")
    assert marker == "/* ... +N more write windows not shown ... */"
    assert f"`{marker}`" in _text(_V7 / "prefix.md")
    assert f"`{marker}`" not in _text(_V6 / "prefix.md")


# === 4. 배선 =====================================================================================


def test_v7_is_in_the_wide_input_set() -> None:
    """빠지면 `oracle_vul` 줄·출력 꼬리·`last_gen_*`·진입점 사실이 **조용히** 사라진다."""
    assert "v7" in pov._WIDE_INPUT_VERSIONS


def test_v7_gets_the_same_wide_inputs_as_v6(ctx) -> None:
    """같은 Finding·SOURCE·진입점이면 FACTS(JSON)가 바이트 동일하고 body는 편집 한 곳만 다르다."""
    source = (
        "/* ===== parser.c:3-6 ===== */\n6|     memcpy(buf, src, len);\n"
        "/* ... +2 more write windows not shown ... */"
    )
    v6, prepared = _render(ctx, "v6", source)
    v7, _ = _render(ctx, "v7", source, prepared)

    assert _split_facts(v7.body)[1] == _split_facts(v6.body)[1]
    assert "fuzz_entrypoint" in _split_facts(v7.body)[1]
    assert source in v7.body
    assert v7.body == v6.body.replace(_BODY_OLD.rstrip("\n"), _BODY_NEW.rstrip("\n"))
    assert v7.prefix != v6.prefix and v7.prompt_version == "v7"


@pytest.mark.parametrize("profile", ["harness_pool", "harness_pool_hybrid"])
def test_the_serving_pov_seat_selects_v7_and_the_doctor_has_nothing_to_say(profile: str) -> None:
    config = load_config(profile)
    version = config.model_for("pov").prompt_template_version

    assert version == "v7"
    assert cli._check_prompt_version(config, "pov", version) == [], (
        "내용 인식 ERROR도 디스크 새 판 WARN도 없다"
    )


def test_no_pov_version_newer_than_the_served_one_is_on_disk() -> None:
    numbers = sorted(
        int(p.name[1:]) for p in (PROMPTS_DIR / "pov").iterdir() if p.name[1:].isdecimal()
    )
    assert numbers[-1] == 7


# === 5. 최종 지정 서술이 #637 코드와 맞는다 ======================================================


def test_the_v7_designation_sentence_matches_the_scorer_code_tiers() -> None:
    """v7 prefix가 말한 순서 - 크래시, 그다음 모름(없음·환경 실패), 그다음 `0`·`71` - 가
    `oracle_log.vul_code_tier`와 같다. 코드 1도 크래시로 센다(「크래시가 늘 이긴다」고 안 쓴
    이유의 한 갈래).
    ⚠ 철자 핀 + 계단 핀이다 - 같은 계단 안의 사슬은 `runner._designation_key` 몫이다."""
    from src.orchestrator import oracle_log

    tier = oracle_log.vul_code_tier
    assert tier(139) == tier(1) == oracle_log.VUL_TIER_CRASH
    assert tier(None) == tier(125) == oracle_log.VUL_TIER_UNKNOWN
    assert tier(0) == tier(71) == oracle_log.VUL_TIER_NO_CRASH
    assert oracle_log.VUL_TIER_CRASH < oracle_log.VUL_TIER_UNKNOWN < oracle_log.VUL_TIER_NO_CRASH

    flat = " ".join(_text(_V7 / "prefix.md").split())
    assert "a crash, then an unavailable or environment-failure code, then `0` or `71`" in flat
    assert "does not choose that slot by scorer code" not in flat
    assert "does not choose that slot by scorer code" in " ".join(_text(_V6 / "prefix.md").split())
