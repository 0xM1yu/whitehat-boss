"""디코더 스키마 컴파일 게이트(`tools/check_decoder_schemas.py`, #698)와 `doctor` 배선.

열거는 xgrammar 없이 돈다. 실제 변환은 `importorskip` - xgrammar가 없는 CI에서는 건너뛴다.
"""

import json
import sys

import pytest

from src import cli
from src import decoder_schemas as gate
from src.config import PROMPTED_ROLES, load_config

PROFILE = "harness_pool"


@pytest.fixture(scope="module")
def pool_items():
    return gate.enumerate_schemas(load_config(PROFILE))


def test_every_seat_of_the_profile_is_enumerated(pool_items):
    config = load_config(PROFILE)
    expected = {role for role in PROMPTED_ROLES if role in config.profile.roles}
    assert expected  # 빈 집합 == 빈 집합이 초록이 되지 않게
    assert {item.role for item in pool_items} == expected
    for item in pool_items:  # 판은 프로필이 정한 것이다
        assert item.version == config.model_for(item.role).prompt_template_version


def test_auditor_and_pov_carry_every_withheld_set(pool_items):
    for role in ("auditor", "pov"):
        mine = [i for i in pool_items if i.role == role]
        # 서빙 판은 `tool` enum이 있는 봉투다 - 없으면 축소가 없고 열거가 한 변형이다.
        assert {i.withheld for i in mine} == set(gate.WITHHELD_SETS), role
        # 빼는 도구가 다르면 요청 스키마가 달라야 한다 - 같으면 축소가 안 걸린 것이다.
        # (PoV의 enum에는 `clang-tidy`가 없어 그 집합은 `()`와 같은 모양이다.)
        shape = {i.withheld: json.dumps(i.schema.model_json_schema(), sort_keys=True) for i in mine}
        assert shape[()] != shape[("build", "asan", "gdb")], role


def test_unsupported_feature_filter_matches_vllm_rules():
    assert gate.has_unsupported_features({"type": "object", "patternProperties": {}})
    assert gate.has_unsupported_features({"a": [{"type": "number", "multipleOf": 2}]})
    assert gate.has_unsupported_features({"type": "string", "format": "regex"})
    assert not gate.has_unsupported_features({"type": "string", "format": "date-time"})
    assert not gate.has_unsupported_features({"anyOf": [{"const": "a"}, {"type": "null"}]})


def test_every_enumerated_schema_converts():
    xgr = pytest.importorskip("xgrammar")
    compiler = gate.make_compiler(xgr)
    items = gate.enumerate_schemas(load_config(PROFILE))
    bad = {
        gate.format_line(i, e) for i in items if (e := gate.compile_error(i.schema, xgr, compiler))
    }
    assert not bad


def test_gate_rejects_a_schema_xgrammar_cannot_take():
    xgr = pytest.importorskip("xgrammar")
    from pydantic import BaseModel, Field

    class Bad(BaseModel):
        n: float = Field(json_schema_extra={"multipleOf": 2})

    assert gate.compile_error(Bad, xgr, gate.make_compiler(xgr)) is not None


def test_doctor_check_warns_when_xgrammar_is_absent(monkeypatch):
    monkeypatch.setitem(sys.modules, "xgrammar", None)  # `import xgrammar` -> ImportError
    checks = cli._check_decoder_schemas(load_config(PROFILE))
    assert [c.severity for c in checks] == [cli.Severity.WARN]
    assert "돌리지 않았다" in checks[0].message


def test_doctor_check_errors_on_a_failed_conversion(monkeypatch):
    first = gate.enumerate_schemas(load_config(PROFILE))[0]
    item = gate.DecoderSchema(first.role, first.version, first.schema, ())
    monkeypatch.setattr(gate, "check_profile", lambda config: [(item, "boom")])
    checks = cli._check_decoder_schemas(load_config(PROFILE))
    assert [c.severity for c in checks] == [cli.Severity.ERROR]
    assert "boom" in checks[0].message


def test_the_compiled_grammar_enforces_what_the_v18_and_narrowed_schemas_promise() -> None:
    """「문법이 막는다」의 근거 — 같은 xgrammar로 컴파일한 문법이 관측된 실패 모양(#602)과 빠진
    도구(#698)를 거절하고, pydantic이 받는 정상 표본은 받는다. 서버의 실제 집행은 아니다(합성 어휘 ·
    같은 문법)."""
    import json

    xgr = pytest.importorskip("xgrammar")
    from src.orchestrator.nodes import signals
    from src.schemas.auditor import AuditorProcedureProbeInputDiscriminatedAction as Envelope
    from tests.test_auditor_v18_envelope import _hop, _valid_samples, _with_audit

    compiler = gate.make_compiler(xgr)

    def accepts(schema, sample) -> bool:  # noqa: ANN001
        return gate.grammar_accepts(schema, json.dumps(sample), xgr, compiler)

    for name, sample in _valid_samples().items():
        assert accepts(Envelope, sample), name
    assert not accepts(Envelope, {"action": "answer", "rationale": "x"})
    cand_0001 = _hop("answer", procedure=_with_audit("no_signal", defense__status="absent"))
    assert not accepts(Envelope, cand_0001)
    narrowed = signals.without_tools(Envelope, signals.COMPILE_NOTE_TOOLS)
    probe = next(s for s in _valid_samples().values() if s.get("action") == "probe")
    assert accepts(narrowed, {**probe, "tool": "poc"})
    assert not accepts(narrowed, {**probe, "tool": "build"})


def test_the_compiled_grammar_enforces_what_the_pov_v11_schema_promises() -> None:
    """#704의 근거 — 같은 xgrammar로 컴파일한 v11 문법이 관측된 실패 모양(`spec` 없는 `gen`)과 필수
    필드가 빠진 세그먼트를 거절하고, pydantic이 받는 표본은 받는다. **키 순서도 본다**: v10 prefix의
    `gen` 예시 순서(`rationale` → `spec`)는 옛 문법·새 문법 모두 거절하고(그래서 6/6이 `spec` 없이
    닫혔다), v11 prefix의 예시는 받는다. 서버의 실제 집행은 아니다(합성 어휘 · 같은 문법)."""
    import json

    xgr = pytest.importorskip("xgrammar")
    from src.orchestrator.nodes import signals
    from src.schemas.pov import PovActionNamed
    from src.schemas.pov import PovDiscriminatedAction as Envelope
    from tests.test_pov_v11 import _OBSERVED, _gen, _valid_samples, prefix_examples

    compiler = gate.make_compiler(xgr)

    def accepts(schema, sample) -> bool:  # noqa: ANN001
        text = sample if isinstance(sample, str) else json.dumps(sample)
        return gate.grammar_accepts(schema, text, xgr, compiler)

    for name, sample in _valid_samples().items():
        assert accepts(Envelope, sample), name
    for example in prefix_examples():
        assert accepts(Envelope, example), example
    v10_order = (
        '{"action":"gen","rationale":"r","spec":{"rationale":"w",'
        '"segments":[{"kind":"literal","hex":"4142"}]}}'
    )
    assert not accepts(PovActionNamed, v10_order)
    assert not accepts(Envelope, v10_order)
    assert accepts(PovActionNamed, _OBSERVED)
    assert not accepts(Envelope, _OBSERVED)
    for refused in (
        {"action": "gen", "spec": None, "rationale": "r"},
        {"action": "inspect", "rationale": "r"},
        {"action": "inspect", "tool": "poc", "rationale": "r", "poc_sha256": "abcdef12"},
        _gen({"kind": "literal"}),
        _gen({"kind": "literal", "hex": "abc"}),
        _gen({"kind": "repeat", "byte": 65}),
    ):
        assert not accepts(Envelope, refused), refused
    narrowed = signals.without_tools(Envelope, signals.COMPILE_NOTE_TOOLS)
    assert accepts(narrowed, {"action": "inspect", "tool": "poc", "rationale": "r"})
    assert not accepts(narrowed, {"action": "inspect", "tool": "build", "rationale": "r"})
