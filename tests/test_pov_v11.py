"""PoV v11 - 디코더가 받는 JSON 스키마가 판별 합이다 (이슈 #704).

모든 요청이 `model_json_schema()`를 `response_format: json_schema, strict`로 보내고 서버가 그것을
문법으로 집행한다. v5~v10 봉투(`PovActionNamed`)의 스키마는 `required: [action, rationale]`이라
`{"action": "gen", "rationale": "…", "poc_sha256": null}`을 합법으로 허용하고, pydantic이 뒤에서
「gen 액션은 spec가 필요하다」로 거절했다. run_20261016_0001 8단계는 3 hop · 6시도 전부 이
모양이었다.
Auditor v18(PR #699 · #602)과 같은 처방이다 - 검증은 부모 그대로, 디코더 스키마만 판별 합. 묶는 것은
여섯이다.

1. **v2~v10 봉투의 스키마는 한 글자도 안 바뀌었다** - sha256 핀.
2. **문법이 거절한다** - `spec` 없는 `gen`(관측 모양) · `tool` 없는 `inspect` · 다른 액션의 필드.
3. **과하게 조이지 않는다** - 모든 액션·모든 도구의 pydantic-유효 표본을 스키마가 받는다. 예외는
   하나 - `submit` 밖의 `poc_sha256` 문자열(pydantic은 무시하고 받는다, 문법은 거절한다).
4. **모든 갈래에 닿는다** - 표본마다 정확히 한 갈래에 맞는다.
5. **`tool` enum 좁히기와 겹쳐 선다** - `signals.without_tools`가 만든 하위 봉투도 판별 합이다.
6. **배선** - v11 = v10 prefix·body 바이트 동일 + 이 봉투 · 서빙 좌석 · `_WIDE_INPUT_VERSIONS` ·
Fake.

⚠ 1~5는 `jsonschema`(Draft 2020-12)가 본다. xgrammar 컴파일은
`tests/test_check_decoder_schemas.py`의
xgrammar 검사가 본다(설치돼 있을 때만). 이 봉투로 모델이 `spec`을 채우는지는 **미실측**이다.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import jsonschema
import pytest
from pydantic import BaseModel, ValidationError

from src import cli
from src.adapters.fake_model import _BUILDERS
from src.config import load_config
from src.orchestrator.nodes import pov, signals
from src.orchestrator.prompt_loader import PROMPTS_DIR, load_prompt
from src.schemas.enums import Role
from src.schemas.pov import PovAction, PovActionNamed, PovDiscriminatedAction
from tests.test_nodes import ctx  # noqa: F401
from tests.test_pov_v7 import _render, _split_facts

ENVELOPE = PovDiscriminatedAction
_TOOLS = ("asan", "build", "gdb", "poc")

# === 1. v2~v10 봉투의 스키마는 그대로다 ===========================================================

# 이 변경 **전에** 잰 값이다(`json.dumps(sort_keys=True)`의 sha256). 이 둘이 v2~v4·v5~v10의 요청
# payload다(evaluation §2.3).
_FLAT_SCHEMA_SHA256 = {
    PovAction: "a9a83131d344632392aa3aeb1d7269920d9df843c2ddfe4f6bd3c49bc95b0a3e",
    PovActionNamed: "4610392e7937684d2ad38749d1ea8442aca8aaf872078e636aa28e6faa66ad4d",
}


def _schema_sha(schema: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()


@pytest.mark.parametrize("envelope", list(_FLAT_SCHEMA_SHA256), ids=lambda e: e.__name__)
def test_the_older_envelope_schemas_are_byte_identical_to_before(envelope: type[BaseModel]) -> None:
    schema = envelope.model_json_schema()

    assert _schema_sha(schema) == _FLAT_SCHEMA_SHA256[envelope]
    assert "anyOf" not in schema and schema["required"] == ["action", "rationale"]


# === 표본 =========================================================================================

_SPEC = {"segments": [{"kind": "literal", "hex": "3c3f786d6c"}], "rationale": "xml prolog"}
_DIGEST = "sha256:" + "ab" * 32

# run_20261016_0001 `prompts/0071_pov_hop1_response.txt`의 모양 그대로(rationale만 줄였다).
_OBSERVED = {"action": "gen", "rationale": "Try an XML prolog.", "poc_sha256": None}


def _valid_samples() -> dict[str, dict[str, Any]]:
    samples: dict[str, dict[str, Any]] = {
        "gen": {"action": "gen", "spec": _SPEC, "rationale": "r"},
        "gen-null-others": {
            "action": "gen", "spec": _SPEC, "tool": None, "rationale": "r", "poc_sha256": None,
        },
        "submit-bare": {"action": "submit", "rationale": "r"},
        "submit-named": {"action": "submit", "rationale": "r", "poc_sha256": _DIGEST},
        "submit-null": {"action": "submit", "rationale": "r", "poc_sha256": None},
    }  # fmt: skip
    for tool in _TOOLS:
        samples[f"inspect-{tool}"] = {"action": "inspect", "tool": tool, "rationale": "r"}
    return samples


def _validator(schema: type[BaseModel]) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(schema.model_json_schema())


# === 2. 문법이 거절한다 ===========================================================================


def test_the_observed_gen_without_a_spec_is_not_a_legal_output() -> None:
    """#704의 6/6 - 옛 봉투의 문법은 받았고 pydantic만 거절했다. 새 봉투는 문법이 거절한다."""
    assert _validator(PovActionNamed).is_valid(_OBSERVED)
    with pytest.raises(ValidationError, match="spec"):
        PovActionNamed.model_validate(_OBSERVED)

    assert not _validator(ENVELOPE).is_valid(_OBSERVED)
    assert not _validator(ENVELOPE).is_valid({"action": "gen", "rationale": "r"})


def test_an_inspect_without_a_tool_is_not_a_legal_output() -> None:
    assert not _validator(ENVELOPE).is_valid({"action": "inspect", "rationale": "r"})
    assert not _validator(ENVELOPE).is_valid({"action": "inspect", "tool": None, "rationale": "r"})


@pytest.mark.parametrize(
    "sample",
    [
        {"action": "gen", "spec": _SPEC, "tool": "poc", "rationale": "r"},
        {"action": "inspect", "tool": "poc", "spec": _SPEC, "rationale": "r"},
        {"action": "submit", "spec": _SPEC, "rationale": "r"},
        {"action": "submit", "tool": "poc", "rationale": "r"},
        {"action": "gen", "spec": _SPEC, "rationale": "r", "extra": 1},
        {"action": "gen", "spec": _SPEC},
    ],
    ids=["gen+tool", "inspect+spec", "submit+spec", "submit+tool", "extra-key", "no-rationale"],
)
def test_the_schema_refuses_what_pydantic_refuses(sample: dict[str, Any]) -> None:
    assert not _validator(ENVELOPE).is_valid(sample)
    with pytest.raises(ValidationError):
        ENVELOPE.model_validate(sample)


def test_a_digest_outside_submit_is_refused_by_the_schema_but_not_by_pydantic() -> None:
    """의도한 차이 하나 - `poc_sha256`은 `submit`에서만 읽힌다. 문법에서 그 밖의 갈래에 열어 두면
    `gen`과 `submit`의 모양이 섞인다(#704의 6/6이 `gen`에 `poc_sha256: null`을 실었다)."""
    sample = {"action": "gen", "spec": _SPEC, "rationale": "r", "poc_sha256": _DIGEST}

    assert ENVELOPE.model_validate(sample).poc_sha256 == _DIGEST
    assert not _validator(ENVELOPE).is_valid(sample)


# === 3·4. 과하게 조이지 않고 모든 갈래에 닿는다 ===================================================


@pytest.mark.parametrize("name", sorted(_valid_samples()))
def test_every_pydantic_valid_sample_is_accepted_by_the_decoder_schema(name: str) -> None:
    sample = _valid_samples()[name]

    ENVELOPE.model_validate(sample)
    _validator(ENVELOPE).validate(sample)


def test_the_top_level_is_one_branch_per_action_and_every_sample_matches_exactly_one() -> None:
    schema = ENVELOPE.model_json_schema()
    branches = [ref["$ref"] for ref in schema["anyOf"]]

    assert branches == ["#/$defs/GenHop", "#/$defs/InspectHop", "#/$defs/SubmitHop"]
    assert schema["title"] == ENVELOPE.__name__
    assert schema["description"] == PovActionNamed.model_json_schema()["description"]
    for name, sample in _valid_samples().items():
        hits = [
            branch
            for branch in branches
            if jsonschema.Draft202012Validator(
                {"$defs": schema["$defs"], "$ref": branch}
            ).is_valid(sample)
        ]
        assert len(hits) == 1, (name, hits)


def test_each_branch_requires_its_own_field() -> None:
    defs = ENVELOPE.model_json_schema()["$defs"]

    assert defs["GenHop"]["required"] == ["action", "spec", "rationale"]
    assert defs["InspectHop"]["required"] == ["action", "tool", "rationale"]
    assert defs["SubmitHop"]["required"] == ["action", "rationale"]
    branches = ("GenHop", "InspectHop", "SubmitHop")
    assert all(defs[n]["additionalProperties"] is False for n in branches)


def test_the_decoder_schema_has_no_dangling_definitions() -> None:
    schema = ENVELOPE.model_json_schema()
    text = json.dumps(schema)
    refs = {part.split('"')[0] for part in text.split('"$ref": "#/$defs/')[1:]}

    assert refs == set(schema["$defs"])


# === 5. `tool` enum 좁히기와 겹쳐 선다 ===========================================================


def _tool_enum(schema: dict[str, Any]) -> list[str]:
    """값이 하나만 남으면 pydantic은 `enum` 대신 `const`를 낸다(`poc`만 남는 좁히기)."""
    tool = schema["$defs"]["InspectHop"]["properties"]["tool"]
    return tool["enum"] if "enum" in tool else [tool["const"]]


def test_the_narrowed_envelope_is_still_the_discriminated_union_without_the_withheld_tools() -> (
    None
):
    withheld = ("build", "asan", "gdb")
    narrowed = signals.without_tools(ENVELOPE, withheld)
    schema = narrowed.model_json_schema()

    assert narrowed.__name__ == ENVELOPE.__name__ and issubclass(narrowed, ENVELOPE)
    assert len(schema["anyOf"]) == 3
    assert _tool_enum(schema) == ["poc"]
    assert _tool_enum(ENVELOPE.model_json_schema()) == list(_TOOLS)
    check = _validator(narrowed)
    assert not check.is_valid({"action": "inspect", "tool": "build", "rationale": "r"})
    assert check.is_valid({"action": "inspect", "tool": "poc", "rationale": "r"})
    assert not check.is_valid(_OBSERVED)


def test_a_narrowed_v10_envelope_keeps_the_flat_schema() -> None:
    schema = signals.without_tools(PovActionNamed, ("build",)).model_json_schema()

    assert "anyOf" not in schema and schema["required"] == ["action", "rationale"]


# === 6. 배선 ======================================================================================

_V10 = PROMPTS_DIR / "pov" / "v10"
_V11 = PROMPTS_DIR / "pov" / "v11"


@pytest.mark.parametrize("name", ["prefix.md", "body.md"])
def test_v11_text_is_the_v10_text_byte_for_byte(name: str) -> None:
    """바뀐 것은 봉투뿐이다 - v10 prefix가 이미 「`poc_sha256` is optional and read only on
    `submit`」·「`gen` carries `spec`」를 말하므로 문장을 고칠 곳이 없다."""
    assert (_V11 / name).read_bytes() == (_V10 / name).read_bytes()


def test_v11_declares_the_discriminated_envelope_and_keeps_the_rest_of_v10() -> None:
    v10, v11 = load_prompt(Role.POV, "v10"), load_prompt(Role.POV, "v11")

    assert v10.output_schema == "PovActionNamed"
    assert v11.output_schema == ENVELOPE.__name__
    assert (v11.hop_header, v11.input, v11.source_options) == (
        v10.hop_header, v10.input, v10.source_options,
    )  # fmt: skip


def test_the_envelope_is_registered_where_an_output_schema_has_to_be() -> None:
    assert ENVELOPE in pov.ALLOWED_OUTPUT_SCHEMAS
    assert pov.resolve_output_schema(ENVELOPE.__name__) is ENVELOPE
    assert ENVELOPE in _BUILDERS
    # 러너의 `poc_sha256` 읽기가 그대로 선다
    assert issubclass(ENVELOPE, PovActionNamed)


def test_v11_is_in_the_wide_input_set() -> None:
    assert "v11" in pov._WIDE_INPUT_VERSIONS


def test_v11_gets_the_same_inputs_as_v10(ctx) -> None:  # noqa: ANN001, F811
    """prefix·body가 같으므로 렌더된 프롬프트도 바이트 동일하다 - 판 번호만 다르다."""
    source = "/* ===== parser.c:3-6 ===== */\n6|     memcpy(buf, src, len);"
    v10, prepared = _render(ctx, "v10", source)
    v11, _ = _render(ctx, "v11", source, prepared)

    assert (v11.prefix, v11.body) == (v10.prefix, v10.body)
    assert _split_facts(v11.body)[1] == _split_facts(v10.body)[1]
    assert v11.prompt_version == "v11"


@pytest.mark.parametrize("profile", ["harness_pool", "harness_pool_hybrid"])
def test_the_serving_pov_seat_selects_v11_and_the_doctor_has_nothing_to_say(profile: str) -> None:
    config = load_config(profile)
    version = config.model_for("pov").prompt_template_version

    assert version == "v11"
    assert cli._check_prompt_version(config, "pov", version) == []
    assert cli._undescribed_relayed_options(config) == []


def test_no_pov_version_newer_than_the_served_one_is_on_disk() -> None:
    numbers = sorted(
        int(p.name[1:]) for p in (PROMPTS_DIR / "pov").iterdir() if p.name[1:].isdecimal()
    )
    assert numbers[-1] == 11
