"""Auditor v18 봉투 - 디코더가 받는 JSON 스키마가 판별 합이다 (이슈 #602).

모든 요청이 `model_json_schema()`를 `response_format: json_schema, strict`로 보내고 서버가 그것을
문법으로 집행한다. v15~v17 봉투의 스키마는 `{"action": "answer", "rationale": "x"}`와 `no_signal`
인데 `defense.status`가 `absent`인 `procedure`를 합법으로 허용하고 pydantic이 뒤에서 거절해 후보가
버려졌다. 묶는 것은 다섯이다.

1. **v17 봉투의 스키마는 한 글자도 안 바뀌었다** - sha256 핀.
2. **문법이 거절한다** - `procedure` 없는 `answer` · `no_signal`의 `absent`/비-`none` 값 ·
   다른 상태의 `no_signal` 전용 값 · 다른 액션의 필드.
3. **과하게 조이지 않는다** - 모든 액션과 모든 `trigger_status`의 pydantic-유효 표본(손으로 만든
   것 + Fake 빌더가 낸 것)을 스키마가 받는다.
4. **모든 갈래에 닿는다** - 갈래마다 그 갈래만 받는 표본이 있다.
5. **`tool` enum 좁히기와 겹쳐 선다** - `signals.without_tools`가 만든 하위 봉투도 판별 합이고 빠진
   도구가 없다.

검증은 `jsonschema`(Draft 2020-12)가 한다. ⚠ 이 검사는 **스키마가 의미상 맞는지**를 본다 - vLLM의
xgrammar가 이 스키마를 컴파일하는지는 안 본다(서빙 전 스키마 컴파일 게이트의 몫). 쓰는 키워드가
허용 목록 안인지만 `test_the_schema_uses_only_the_listed_keywords`가 묶는다.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
from typing import Any

import jsonschema
import pytest
from pydantic import BaseModel, ValidationError

from src.adapters.fake_model import _BUILDERS
from src.interfaces.model_client import ModelResult
from src.orchestrator.context import RunContext
from src.orchestrator.nodes import auditor, signals
from src.schemas.auditor import (
    AUDITOR_TOOLS,
    AuditorProcedureProbeInputAction,
    AuditorProcedureProbeInputDiscriminatedAction,
)
from tests import test_nodes
from tests.test_auditor_v12 import _STATUSES, _audit, _payload
from tests.test_auditor_v17 import _SOURCE
from tests.test_nodes import VULN_TARGET, _triage

ctx = test_nodes.ctx

ENVELOPE = AuditorProcedureProbeInputDiscriminatedAction
_NO_SIGNAL_VALUES = {
    "category": "none",
    "defense.status": "not_applicable",
    "refutation.result": "no_candidate",
}

# === 1. v15~v17 봉투의 스키마는 그대로다 ==========================================================

# `AuditorProcedureProbeInputAction.model_json_schema()`를 이 변경 **전에** 잰 값이다
# (`json.dumps(sort_keys=True)`의 sha256).
_V15_SCHEMA_SHA256 = "181bf0c3fdda8c73a4ca6652c6892f0a096ab8fafe3efcb41e6ecc3bee7f6c00"


def _schema_sha(schema: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()


def test_the_v17_envelope_schema_is_byte_identical_to_before() -> None:
    """v15~v17이 선언하는 봉투 - 이 스키마가 그 판들의 요청 payload다(evaluation §2.3)."""
    schema = AuditorProcedureProbeInputAction.model_json_schema()

    assert _schema_sha(schema) == _V15_SCHEMA_SHA256
    assert "anyOf" not in schema and schema["required"] == ["action", "rationale"]


# === 표본 =========================================================================================


def _validator(schema: type[BaseModel]) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(schema.model_json_schema())


def _hop(action: str, **fields: Any) -> dict[str, Any]:
    return {"action": action, **fields, "rationale": "r"}


_REQUEST = {"kind": "function", "target": "f", "purpose": "see body", "request_code": "body"}
_SPEC = {"segments": [{"kind": "literal", "hex": "41"}], "rationale": "one byte"}


def _valid_samples() -> dict[str, dict[str, Any]]:
    """갈래마다 모델이 낼 법한 모양 - 쓰지 않는 필드를 **생략한** 것과 **null로 채운** 것 둘 다."""
    samples: dict[str, dict[str, Any]] = {
        "lookup": _hop("lookup", requests=[_REQUEST]),
        "lookup, padded": _hop("lookup", requests=[_REQUEST], tool=None, spec=None, procedure=None),
        "probe, no spec": _hop("probe", tool="asan"),
        "probe, with spec": _hop("probe", tool="poc", spec=_SPEC),
        "probe, padded": _hop("probe", requests=[], tool="gdb", spec=None, procedure=None),
    }
    samples |= {f"probe {tool}": _hop("probe", tool=tool) for tool in AUDITOR_TOOLS}
    for status in _STATUSES:
        samples[f"answer {status}"] = _hop("answer", procedure=_payload(status))
        samples[f"answer {status}, padded"] = _hop(
            "answer", requests=[], tool=None, spec=None, procedure=_payload(status)
        )
    # 경로가 빈 `reachability_unknown`과 `no_signal`은 합법이다(`AuditBlock.path` 주석).
    for status in ("reachability_unknown", "no_signal"):
        empty_path = {**_audit(status), "path": []}
        samples[f"answer {status}, empty path"] = _hop(
            "answer", procedure=_payload(status, audit=empty_path)
        )
    return samples


@pytest.mark.parametrize("name", sorted(_valid_samples()))
def test_every_pydantic_valid_sample_is_accepted_by_the_decoder_schema(name: str) -> None:
    """과하게 조이지 않는다 - 검증기가 받는 모양을 문법이 막으면 그 답이 통째로 사라진다."""
    sample = _valid_samples()[name]

    ENVELOPE.model_validate(sample)  # 표본 자체가 pydantic-유효하다
    _validator(ENVELOPE).validate(sample)
    # 봉투가 직렬화한 모양도 받는다(필드 전부, 쓰지 않는 것은 null/[]).
    _validator(ENVELOPE).validate(json.loads(ENVELOPE.model_validate(sample).model_dump_json()))


# === 2. 문법이 거절한다 ===========================================================================


def _rejected(sample: dict[str, Any], schema: type[BaseModel] = ENVELOPE) -> bool:
    return not _validator(schema).is_valid(sample)


def _pydantic_rejects(sample: dict[str, Any]) -> bool:
    try:
        ENVELOPE.model_validate(sample)
    except ValidationError:
        return True
    return False


def test_an_answer_without_a_procedure_is_not_a_legal_output() -> None:
    """run_20261016_0001에서 두 번 본 모양 - 문법이 허용하고 pydantic이 거절했다."""
    bare = {"action": "answer", "rationale": "x"}
    explicit_null = _hop("answer", procedure=None)

    for sample in (bare, explicit_null, _hop("answer", procedure=None, tool=None)):
        assert _rejected(sample)
        assert _pydantic_rejects(sample)  # 백스톱은 그대로다
    # v17 봉투의 스키마는 그것을 합법으로 두었다 - 이 변경이 고친 것이 이것이다.
    assert not _rejected(bare, AuditorProcedureProbeInputAction)


def _with_audit(status: str, **dotted: str) -> dict[str, Any]:
    procedure = copy.deepcopy(_payload(status))
    for key, value in dotted.items():
        head, _, tail = key.replace("__", ".").partition(".")
        procedure["audit"][head][tail] = value
    return procedure


def test_a_no_signal_with_an_absent_defense_is_not_a_legal_output() -> None:
    """cand_0001의 모양 - `no_signal`인데 `defense.status`가 `absent`다."""
    sample = _hop("answer", procedure=_with_audit("no_signal", defense__status="absent"))

    assert _rejected(sample)
    assert _pydantic_rejects(sample)
    assert not _rejected(sample, AuditorProcedureProbeInputAction)


@pytest.mark.parametrize("key", sorted(_NO_SIGNAL_VALUES))
def test_a_no_signal_needs_each_of_the_three_values(key: str) -> None:
    replacement = {"category": "spatial_bounds", "defense.status": "absent"}.get(key, "defeated")
    head, _, tail = key.partition(".")
    procedure = copy.deepcopy(_payload("no_signal"))
    if tail:
        procedure["audit"][head][tail] = replacement
    else:
        procedure["audit"][head] = replacement

    assert _rejected(_hop("answer", procedure=procedure))


@pytest.mark.parametrize("status", [s for s in _STATUSES if s != "no_signal"])
@pytest.mark.parametrize("key", sorted(_NO_SIGNAL_VALUES))
def test_the_other_statuses_cannot_use_the_no_signal_only_values(status: str, key: str) -> None:
    procedure = copy.deepcopy(_payload(status))
    head, _, tail = key.partition(".")
    if tail:
        procedure["audit"][head][tail] = _NO_SIGNAL_VALUES[key]
    else:
        procedure["audit"][head] = _NO_SIGNAL_VALUES[key]

    assert _rejected(_hop("answer", procedure=procedure))
    assert _pydantic_rejects(_hop("answer", procedure=procedure))


@pytest.mark.parametrize("status", [s for s in _STATUSES if s != "no_signal"])
def test_the_other_statuses_need_a_cwe(status: str) -> None:
    sample = _hop("answer", procedure=_payload(status, cwe=None))

    assert _rejected(sample) and _pydantic_rejects(sample)


def test_a_no_signal_with_a_cwe_is_refused_by_the_schema_but_not_by_pydantic() -> None:
    """**의도한 비대칭이다.** 산문이 `no_signal`은 `"cwe": null`이라고 적는다(prefix). pydantic은
    `no_signal`의 CWE를 막지 않는다(`tests/test_auditor_v12.py`가 문다) - 문법만 조인다."""
    sample = _hop("answer", procedure=_payload("no_signal", cwe="CWE-787"))

    ENVELOPE.model_validate(sample)
    assert _rejected(sample)


def test_the_other_actions_still_refuse_each_others_fields() -> None:
    procedure = _payload("triggerable")
    bad = [
        _hop("lookup", requests=[]),  # 빈 조회
        _hop("lookup", requests=[_REQUEST], tool="asan"),
        _hop("lookup", requests=[_REQUEST], spec=_SPEC),
        _hop("lookup", requests=[_REQUEST], procedure=procedure),
        _hop("probe"),  # tool 없음
        _hop("probe", tool="asan", requests=[_REQUEST]),
        _hop("probe", tool="asan", procedure=procedure),
        _hop("probe", tool="index"),  # 열지 않은 도구
        _hop("answer", procedure=procedure, tool="asan"),
        _hop("answer", procedure=procedure, requests=[_REQUEST]),
        _hop("answer", procedure=procedure, spec=_SPEC),
        {"action": "answer", "procedure": procedure},  # rationale 없음
    ]
    for sample in bad:
        assert _rejected(sample), sample
        assert _pydantic_rejects(sample), sample


# === 4. 모든 갈래에 닿는다 ========================================================================


def _branch_validator(defs_name: str) -> jsonschema.Draft202012Validator:
    schema = ENVELOPE.model_json_schema()
    return jsonschema.Draft202012Validator(
        {"$ref": f"#/$defs/{defs_name}", "$defs": schema["$defs"]}
    )


def test_the_top_level_is_one_branch_per_action_and_every_sample_matches_exactly_one() -> None:
    schema = ENVELOPE.model_json_schema()
    names = [ref["$ref"].rsplit("/", 1)[1] for ref in schema["anyOf"]]
    assert names == ["LookupHop", "ProbeHop", "AnswerHop"]
    assert schema["type"] == "object" and "properties" not in schema

    owners: dict[str, set[str]] = {name: set() for name in names}
    for sample_name, sample in _valid_samples().items():
        matches = [n for n in names if _branch_validator(n).is_valid(sample)]
        assert len(matches) == 1, (sample_name, matches)
        owners[matches[0]].add(sample_name)
    assert all(owners.values()), owners  # 닿지 않는 갈래가 없다


def test_the_procedure_has_one_branch_for_no_signal_and_one_for_the_rest() -> None:
    schema = ENVELOPE.model_json_schema()
    procedure = schema["$defs"]["AnswerHop"]["properties"]["procedure"]
    names = [ref["$ref"].rsplit("/", 1)[1] for ref in procedure["anyOf"]]
    assert [n.removeprefix("AuditProcedureAuditAlways") for n in names] == [
        "NoSignalCase",
        "SignalCase",
    ]

    for status in _STATUSES:
        matches = [n for n in names if _branch_validator(n).is_valid(_payload(status))]
        expected = names[0] if status == "no_signal" else names[1]
        assert matches == [expected], (status, matches)


def test_the_decoder_schema_has_no_unused_or_dangling_definitions() -> None:
    schema = ENVELOPE.model_json_schema()
    defs = schema["$defs"]
    refs: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "$ref" in node:
                refs.add(node["$ref"].rsplit("/", 1)[1])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk({key: value for key, value in schema.items() if key != "$defs"})
    walk(defs)
    assert refs == set(defs), (sorted(refs - set(defs)), sorted(set(defs) - refs))


# xgrammar의 JSON 스키마 변환기가 안다고 우리가 적어 둔 키워드 - 이 밖의 것(`oneOf` · `if` · `not` ·
# `dependentRequired` · `patternProperties` 등)이 스키마에 생기면 이 검사가 빨개진다. ⚠ 이 목록은
# **우리가 쓰기로 한 것**이지 변환기 문서의 검증이 아니다 - 컴파일되는지는 서빙 전 게이트가 본다.
_KEYWORDS = {
    "anyOf", "const", "enum", "required", "properties", "additionalProperties", "$defs", "$ref",
    "type", "items", "minItems", "maxItems", "minLength", "maxLength", "pattern", "minimum",
    "maximum",
    "title", "description", "default",
}  # fmt: skip


def test_the_schema_uses_only_the_listed_keywords() -> None:
    schema = ENVELOPE.model_json_schema()
    used: set[str] = set()

    def walk(node: Any, in_properties: bool = False) -> None:
        if isinstance(node, dict):
            if not in_properties:
                used.update(node)
            for key, value in node.items():
                walk(value, in_properties=key in ("properties", "$defs"))
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    assert used <= _KEYWORDS, sorted(used - _KEYWORDS)


# === 5. `tool` enum 좁히기와 겹쳐 선다 ===========================================================


def _tool_enum(schema: dict[str, Any]) -> list[str]:
    return schema["$defs"]["ProbeHop"]["properties"]["tool"]["enum"]


def test_the_narrowed_envelope_is_still_the_discriminated_union_without_the_withheld_tools() -> (
    None
):
    withheld = ("build", "asan", "gdb")
    narrowed = signals.without_tools(ENVELOPE, withheld)

    assert narrowed is not ENVELOPE and issubclass(narrowed, ENVELOPE)
    assert narrowed.__name__ == ENVELOPE.__name__  # 요청의 스키마 이름이 종전과 같다
    schema = narrowed.model_json_schema()
    assert [ref["$ref"] for ref in schema["anyOf"]] == [
        "#/$defs/LookupHop", "#/$defs/ProbeHop", "#/$defs/AnswerHop",
    ]  # fmt: skip
    assert _tool_enum(schema) == [t for t in AUDITOR_TOOLS if t not in withheld]
    assert _tool_enum(ENVELOPE.model_json_schema()) == list(AUDITOR_TOOLS)  # 원본은 안 바뀐다
    # 판별 합의 나머지는 좁히기와 무관하다.
    wide = ENVELOPE.model_json_schema()
    for name in set(wide["$defs"]) - {"ProbeHop"}:
        assert schema["$defs"][name] == wide["$defs"][name], name


def test_the_narrowed_schema_refuses_the_withheld_tool_and_keeps_every_other_rule() -> None:
    narrowed = signals.without_tools(ENVELOPE, ("build", "asan", "gdb"))
    check = _validator(narrowed)

    assert not check.is_valid(_hop("probe", tool="build"))
    assert check.is_valid(_hop("probe", tool="poc", spec=_SPEC))
    assert not check.is_valid({"action": "answer", "rationale": "x"})
    assert not check.is_valid(
        _hop("answer", procedure=_with_audit("no_signal", defense__status="absent"))
    )
    assert check.is_valid(_hop("answer", procedure=_payload("no_signal")))
    assert all(
        narrowed.model_validate(s)
        for s in (_hop("probe", tool="poc"), _hop("lookup", requests=[_REQUEST]))
    )


def test_a_narrowed_v17_envelope_keeps_the_flat_schema() -> None:
    """좁히기는 v17 봉투에서도 그대로 - 판별 합은 새 봉투의 것이다."""
    schema = signals.without_tools(AuditorProcedureProbeInputAction, ("build",)).model_json_schema()

    assert "anyOf" not in schema and schema["required"] == ["action", "rationale"]


# === 배선 / Fake ==================================================================================


def test_the_envelope_is_registered_where_an_output_schema_has_to_be() -> None:
    assert ENVELOPE in auditor.ALLOWED_OUTPUT_SCHEMAS and ENVELOPE in auditor._LOOP_ENVELOPES
    assert auditor.resolve_output_schema(ENVELOPE.__name__) is ENVELOPE
    assert ENVELOPE in _BUILDERS
    assert issubclass(ENVELOPE, AuditorProcedureProbeInputAction)  # 노드의 isinstance가 그대로 선다


class _Recording:
    """`complete()`가 낸 값과 요청한 스키마를 기록하는 얇은 껍데기."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: list[tuple[type[BaseModel], BaseModel]] = []

    def complete(self, **kwargs: Any) -> ModelResult:
        result = self.inner.complete(**kwargs)
        self.calls.append((kwargs["schema"], result.value))
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def test_every_hop_the_fake_emits_on_v18_passes_the_decoder_schema(ctx: RunContext) -> None:
    """Fake 빌더가 낸 lookup → probe → answer가 **요청한 그 스키마**를 통과한다."""
    entry = ctx.config.models["fake"].transition(prompt_template_version="v18")
    config = ctx.config.transition(
        models={**ctx.config.models, "fake_v18": entry},
        profile=ctx.config.profile.transition(
            roles={**ctx.config.profile.roles, "auditor": "fake_v18"}
        ),
        thresholds=ctx.config.thresholds.transition(auditor_tool_hops=4),
    )
    candidate, capsule, evidences = _triage(dataclasses.replace(ctx, config=config), VULN_TARGET)
    recording = _Recording(ctx.models)
    armed = dataclasses.replace(ctx, config=config, models=recording)

    auditor.run(
        armed, candidate=candidate, capsule=capsule, evidences=evidences, source=lambda: _SOURCE
    )

    loop = [(s, v) for s, v in recording.calls if s.__name__ == ENVELOPE.__name__]
    assert [v.action for _, v in loop] == ["lookup", "probe", "answer"]  # type: ignore[attr-defined]
    for schema, value in loop:
        _validator(schema).validate(json.loads(value.model_dump_json()))
