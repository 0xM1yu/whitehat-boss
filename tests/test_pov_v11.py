"""PoV v11 - 디코더가 받는 JSON 스키마가 판별 합이고, prefix 예시가 그 키 순서다 (이슈 #704).

모든 요청이 `model_json_schema()`를 `response_format: json_schema, strict`로 보내고 서버(xgrammar)가
그것을 문법으로 집행한다 - **키 순서까지**. v5~v10 봉투(`PovActionNamed`)의 스키마는
`required: [action, rationale]`이고 키 순서가 action · spec · tool · rationale · poc_sha256인데,
v5~v10 prefix의 `gen` 예시는 `rationale`을 `spec` 앞에 적었다. 예시대로 `rationale`을 먼저 쓰면
그 뒤에 `spec` 자리가 없어 `{"action": "gen", "rationale": "…", "poc_sha256": null}`로 닫힌다 -
run_20261016_0001 8단계 3 hop · 6시도가 전부 이 모양이었고 pydantic이 거절했다.
Auditor v18(PR #699 · #602)과 같은 처방(검증은 부모 그대로, 디코더 스키마만 판별 합)에 예시 순서를
더한다. 묶는 것은 일곱이다.

1. **v2~v10 봉투의 스키마는 한 글자도 안 바뀌었다** - sha256 핀. v11 봉투도 핀이다.
2. **문법이 거절한다** - `spec` 없는/null인 `gen`(관측 모양) · `tool` 없는 `inspect` · 다른 액션의
   필드 · kind별 필수 필드가 빠진 세그먼트 · 16진이 아닌 `hex`.
3. **과하게 조이지 않는다** - 모든 액션·도구·세그먼트 kind의 pydantic-유효 표본을 스키마가 받는다.
   예외는 하나 - `submit` 밖의 `poc_sha256` 문자열(pydantic은 무시하고 받는다, 문법은 거절한다).
4. **모든 갈래에 닿는다** - 표본마다 정확히 한 갈래에 맞는다.
5. **`tool` enum 좁히기와 겹쳐 선다** - `signals.without_tools`가 만든 하위 봉투도 판별 합이다.
6. **배선** - v11 = v10 body 바이트 동일 + prefix 편집 둘 + 이 봉투 · 서빙 좌석 ·
   `_WIDE_INPUT_VERSIONS` · Fake가 v11 루프에서 낸 hop이 요청 스키마를 통과한다.
7. **prefix 예시가 스키마를 통과한다** - 자리표시자를 채운 예시가 pydantic·스키마를 통과한다.

⚠ 1~5·7은 `jsonschema`(Draft 2020-12)가 본다 - **키 순서는 안 본다.** 키 순서와 컴파일은
`tests/test_check_decoder_schemas.py`의 xgrammar 검사가 본다(설치돼 있을 때만). 이 봉투로 모델이
`spec`을 채우는지는 **미실측**이다.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from typing import Any

import jsonschema
import pytest
from pydantic import BaseModel, ValidationError

from src import cli
from src.adapters.fake_model import _BUILDERS
from src.config import load_config
from src.interfaces.model_client import ModelResult
from src.orchestrator import runner
from src.orchestrator.nodes import pov, signals
from src.orchestrator.prompt_loader import PROMPTS_DIR, load_prompt
from src.schemas.enums import Role
from src.schemas.pov import PovAction, PovActionNamed, PovDiscriminatedAction
from tests.test_nodes import _pov_state, ctx  # noqa: F401
from tests.test_pov_loop import _with_pov_version
from tests.test_pov_v7 import _render, _split_facts, _text

ENVELOPE = PovDiscriminatedAction
_TOOLS = ("asan", "build", "gdb", "poc")

# === 1. 스키마 핀 =================================================================================

# 이 변경 **전에** 잰 값이다(`json.dumps(sort_keys=True)`의 sha256). 이 둘이 v2~v4·v5~v10의 요청
# payload다(evaluation §2.3).
_FLAT_SCHEMA_SHA256 = {
    PovAction: "a9a83131d344632392aa3aeb1d7269920d9df843c2ddfe4f6bd3c49bc95b0a3e",
    PovActionNamed: "4610392e7937684d2ad38749d1ea8442aca8aaf872078e636aa28e6faa66ad4d",
}
# v11의 요청 payload - 봉투를 고치면 이 값이 바뀌고, 그때는 새 판을 세운다
# (agent-contracts §4 규칙 1).
_V11_SCHEMA_SHA256 = "251d298731bc55273b042fbfcc5ee1b6a25987adaea13e529150cecfc4ee16a4"


def _schema_sha(schema: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()


@pytest.mark.parametrize("envelope", list(_FLAT_SCHEMA_SHA256), ids=lambda e: e.__name__)
def test_the_older_envelope_schemas_are_byte_identical_to_before(envelope: type[BaseModel]) -> None:
    schema = envelope.model_json_schema()

    assert _schema_sha(schema) == _FLAT_SCHEMA_SHA256[envelope]
    assert "anyOf" not in schema and schema["required"] == ["action", "rationale"]


def test_the_v11_request_schema_is_pinned() -> None:
    assert _schema_sha(ENVELOPE.model_json_schema()) == _V11_SCHEMA_SHA256


# === 표본 =========================================================================================

_LITERAL = {"kind": "literal", "hex": "3c3f786d6c"}
_REPEAT = {"kind": "repeat", "byte": 65, "count": 64}
_INT = {"kind": "int", "value": 4096, "size": 4, "endian": "little"}
_SPEC = {"segments": [_LITERAL], "rationale": "xml prolog"}
_DIGEST = "sha256:" + "ab" * 32

# run_20261016_0001 `prompts/0071_pov_hop1_response.txt`의 모양 그대로(rationale만 줄였다).
_OBSERVED = {"action": "gen", "rationale": "Try an XML prolog.", "poc_sha256": None}


def _valid_samples() -> dict[str, dict[str, Any]]:
    samples: dict[str, dict[str, Any]] = {
        "gen": {"action": "gen", "spec": _SPEC, "rationale": "r"},
        "gen-every-kind": {
            "action": "gen",
            "spec": {"segments": [_LITERAL, _INT, _REPEAT], "rationale": "w"},
            "rationale": "r",
        },
        "gen-null-others": {
            "action": "gen", "spec": _SPEC, "tool": None, "rationale": "r", "poc_sha256": None,
        },
        "gen-segment-nulls": {
            "action": "gen",
            "spec": {"segments": [{**_LITERAL, "byte": None, "count": None}], "rationale": "w"},
            "rationale": "r",
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


def _gen(*segments: dict[str, Any]) -> dict[str, Any]:
    spec = {"segments": list(segments), "rationale": "w"}
    return {"action": "gen", "spec": spec, "rationale": "r"}


# === 2. 문법이 거절한다 ===========================================================================


def test_the_observed_gen_without_a_spec_is_not_a_legal_output() -> None:
    """#704의 6/6 - 옛 봉투의 스키마는 받았고 pydantic만 거절했다. 새 봉투는 스키마가 거절한다."""
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
        {"action": "gen", "spec": None, "rationale": "r"},
        {"action": "gen", "spec": _SPEC, "tool": "poc", "rationale": "r"},
        {"action": "inspect", "tool": "poc", "spec": _SPEC, "rationale": "r"},
        {"action": "submit", "spec": _SPEC, "rationale": "r"},
        {"action": "submit", "tool": "poc", "rationale": "r"},
        {"action": "gen", "spec": _SPEC, "rationale": "r", "extra": 1},
        {"action": "gen", "spec": _SPEC},
        _gen({"kind": "literal"}),
        _gen({"kind": "literal", "hex": ""}),
        _gen({"kind": "literal", "hex": "abc"}),
        _gen({"kind": "literal", "hex": "zz"}),
        _gen({"kind": "literal", "hex": "41", "byte": 1}),
        _gen({"kind": "repeat", "byte": 65}),
        _gen({"kind": "int", "value": 1, "size": 1}),
        _gen(),
    ],
    ids=[
        "gen-null-spec", "gen+tool", "inspect+spec", "submit+spec", "submit+tool", "extra-key",
        "no-rationale", "literal-no-hex", "literal-empty-hex", "literal-odd-hex",
        "literal-non-hex", "literal+byte", "repeat-no-count", "int-no-endian", "no-segments",
    ],
)
def test_the_schema_refuses_what_pydantic_refuses(sample: dict[str, Any]) -> None:
    assert not _validator(ENVELOPE).is_valid(sample)
    with pytest.raises(ValidationError):
        ENVELOPE.model_validate(sample)


@pytest.mark.parametrize(
    "sample",
    [
        {"action": "gen", "spec": _SPEC, "rationale": "r", "poc_sha256": _DIGEST},
        {"action": "inspect", "tool": "poc", "rationale": "r", "poc_sha256": _DIGEST},
    ],
    ids=["gen+digest", "inspect+digest"],
)
def test_a_digest_outside_submit_is_refused_by_the_schema_but_not_by_pydantic(
    sample: dict[str, Any],
) -> None:
    """의도한 차이 하나 - `poc_sha256`은 `submit`에서만 읽힌다(`runner._run_pov_hops`). 그 밖의
    갈래에서는 문법이 막는다."""
    assert ENVELOPE.model_validate(sample).poc_sha256 == _DIGEST
    assert not _validator(ENVELOPE).is_valid(sample)


# === 3·4. 과하게 조이지 않고 모든 갈래에 닿는다 ===================================================


@pytest.mark.parametrize("name", sorted(_valid_samples()))
def test_every_pydantic_valid_sample_is_accepted_by_the_decoder_schema(name: str) -> None:
    sample = _valid_samples()[name]

    ENVELOPE.model_validate(sample)
    _validator(ENVELOPE).validate(sample)


def _matching(schema: dict[str, Any], refs: list[str], sample: Any) -> list[str]:
    return [
        ref
        for ref in refs
        if jsonschema.Draft202012Validator({"$defs": schema["$defs"], "$ref": ref}).is_valid(sample)
    ]


def test_the_top_level_is_one_branch_per_action_and_every_sample_matches_exactly_one() -> None:
    schema = ENVELOPE.model_json_schema()
    branches = [ref["$ref"] for ref in schema["anyOf"]]

    assert branches == ["#/$defs/GenHop", "#/$defs/InspectHop", "#/$defs/SubmitHop"]
    assert schema["title"] == ENVELOPE.__name__
    assert schema["description"] == PovActionNamed.model_json_schema()["description"]
    for name, sample in _valid_samples().items():
        assert len(_matching(schema, branches, sample)) == 1, name


def test_segments_are_one_branch_per_kind_and_each_matches_exactly_one() -> None:
    schema = ENVELOPE.model_json_schema()
    items = schema["$defs"]["PovSpec"]["properties"]["segments"]["items"]
    refs = [ref["$ref"] for ref in items["anyOf"]]

    assert refs == [
        "#/$defs/LiteralSegment", "#/$defs/RepeatSegment", "#/$defs/IntSegment",
    ]  # fmt: skip
    for segment in (_LITERAL, _REPEAT, _INT):
        assert len(_matching(schema, refs, segment)) == 1, segment


def test_each_branch_requires_its_own_field() -> None:
    defs = ENVELOPE.model_json_schema()["$defs"]

    assert defs["GenHop"]["required"] == ["action", "spec", "rationale"]
    assert defs["InspectHop"]["required"] == ["action", "tool", "rationale"]
    assert defs["SubmitHop"]["required"] == ["action", "rationale"]
    assert defs["LiteralSegment"]["required"] == ["kind", "hex"]
    assert defs["RepeatSegment"]["required"] == ["kind", "byte", "count"]
    assert defs["IntSegment"]["required"] == ["kind", "value", "size", "endian"]
    for name in ("GenHop", "InspectHop", "SubmitHop", "LiteralSegment", "RepeatSegment"):
        assert defs[name]["additionalProperties"] is False, name


def test_the_key_order_is_the_parent_field_order() -> None:
    """xgrammar는 이 순서로만 키를 생성하게 한다 - prefix 예시가 이 순서여야 한다(§7)."""
    defs = ENVELOPE.model_json_schema()["$defs"]

    for name in ("GenHop", "InspectHop", "SubmitHop"):
        assert list(defs[name]["properties"]) == list(PovActionNamed.model_fields), name
    assert list(defs["PovSpec"]["properties"]) == ["segments", "rationale"]
    assert list(defs["IntSegment"]["properties"]) == [
        "kind", "hex", "byte", "count", "value", "size", "endian",
    ]  # fmt: skip


def test_the_decoder_schema_has_no_dangling_or_unused_definitions() -> None:
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
    assert not check.is_valid(_gen({"kind": "literal"}))


def test_a_narrowed_v10_envelope_keeps_the_flat_schema() -> None:
    schema = signals.without_tools(PovActionNamed, ("build",)).model_json_schema()

    assert "anyOf" not in schema and schema["required"] == ["action", "rationale"]


# === 6. 배선 ======================================================================================

_V10 = PROMPTS_DIR / "pov" / "v10"
_V11 = PROMPTS_DIR / "pov" / "v11"

_V11_SHA = {
    "prefix.md": "0981756dca5085a590ab5f93b2d0e314db7c3255895c2d0834b4a32541bd9e8f",
    "body.md": "962b7fe89e6fcbdd26d77ee96cb16571e9ec04d60bb467f15b4ed6cebaa22443",
}

# v10 → v11 prefix 편집 둘. 편집 밖은 바이트 동일이다.
_EDITS = [
    (
        "- Shapes (placeholders — a segment carries only its own kind's fields;",
        "- Shapes (placeholders — write the keys **in the order shown**; "
        "a segment carries only its own kind's fields;",
    ),
    (
        '`{"action":"gen","rationale":"<one sentence>","spec":{"rationale":"<why these bytes>",'
        '"segments":[{"kind":"literal","hex":"<even-length hex>"},{"kind":"int","value":4096,'
        '"size":4,"endian":"little"},{"kind":"repeat","byte":65,"count":64}]}}`',
        '`{"action":"gen","spec":{"segments":[{"kind":"literal","hex":"<even-length hex>"},'
        '{"kind":"int","value":4096,"size":4,"endian":"little"},{"kind":"repeat","byte":65,'
        '"count":64}],"rationale":"<why these bytes>"},"rationale":"<one sentence>"}`',
    ),
]


@pytest.mark.parametrize("name", sorted(_V11_SHA))
def test_v11_files_match_their_recorded_sha(name: str) -> None:
    assert hashlib.sha256((_V11 / name).read_bytes()).hexdigest() == _V11_SHA[name]
    assert f"sha256 `{_V11_SHA[name]}`" in _text(_V11 / "meta.yaml")


def test_v11_body_is_the_v10_body_byte_for_byte() -> None:
    assert (_V11 / "body.md").read_bytes() == (_V10 / "body.md").read_bytes()


def test_v11_prefix_is_the_v10_prefix_with_exactly_the_recorded_edits() -> None:
    expected = _text(_V10 / "prefix.md")
    for old, new in _EDITS:
        assert expected.count(old) == 1, old
        expected = expected.replace(old, new)
    assert expected == _text(_V11 / "prefix.md")
    assert b"\r" not in (_V11 / "prefix.md").read_bytes()


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


def test_v11_gets_the_same_body_as_v10(ctx) -> None:  # noqa: ANN001, F811
    """body(FACTS 포함)는 바이트 동일하고 prefix만 편집 둘만큼 다르다."""
    source = "/* ===== parser.c:3-6 ===== */\n6|     memcpy(buf, src, len);"
    v10, prepared = _render(ctx, "v10", source)
    v11, _ = _render(ctx, "v11", source, prepared)

    assert v11.body == v10.body
    assert _split_facts(v11.body)[1] == _split_facts(v10.body)[1]
    assert v11.prefix != v10.prefix and v11.prompt_version == "v11"


class _Recording:
    """`complete()`가 낸 값과 요청한 스키마를 기록하는 얇은 껍데기
    (`test_auditor_v18_envelope`와 같다)."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: list[tuple[type[BaseModel], BaseModel]] = []

    def complete(self, **kwargs: Any) -> ModelResult:
        result = self.inner.complete(**kwargs)
        self.calls.append((kwargs["schema"], result.value))
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def test_every_hop_the_fake_emits_on_v11_passes_the_decoder_schema(ctx) -> None:  # noqa: ANN001, F811
    """Fake가 v11 루프에서 낸 gen → submit이 **요청한 그 스키마**를 통과하고, 요청이 판별 합이다."""
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]
    recording = _Recording(ctx.models)
    armed = dataclasses.replace(_with_pov_version(ctx, "v11"), models=recording)

    runner._run_pov_hops(armed, state, finding, capsule)

    assert {schema.__name__ for schema, _ in recording.calls} == {ENVELOPE.__name__}
    assert [value.action for _, value in recording.calls] == ["gen", "submit"]  # type: ignore[attr-defined]
    for schema, value in recording.calls:
        assert issubclass(schema, ENVELOPE) and "anyOf" in schema.model_json_schema()
        _validator(schema).validate(json.loads(value.model_dump_json()))


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


# === 7. prefix 예시가 스키마를 통과한다 ===========================================================

_PLACEHOLDER = re.compile(r"<([^<>]+)>")


def prefix_examples() -> list[str]:
    """v11 prefix 「Output」의 Shapes 예시 - 자리표시자를 값으로 채운 JSON 문자열
    (키 순서 그대로)."""
    lines = _text(_V11 / "prefix.md").splitlines()
    shapes = lines.index(next(line for line in lines if line.startswith("- Shapes (")))
    examples = []
    for line in lines[shapes + 1 :]:
        if not line.startswith("  - `{"):
            break
        raw = line.removeprefix("  - `").removesuffix("`")
        examples.append(
            _PLACEHOLDER.sub(lambda m: "4142" if "hex" in m.group(1) else "x", raw)
        )
    return examples


def test_the_prefix_examples_are_legal_and_valid() -> None:
    examples = prefix_examples()

    assert [json.loads(e)["action"] for e in examples] == ["gen", "submit"]
    for example in examples:
        sample = json.loads(example)
        ENVELOPE.model_validate(sample)
        _validator(ENVELOPE).validate(sample)
