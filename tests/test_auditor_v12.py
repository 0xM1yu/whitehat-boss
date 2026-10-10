"""Auditor v12 — `audit`를 모든 상태에서 채운다(이슈 #578 · 팀 결정 2026-10-10).

FT 데이터 Teacher v4가 `no_signal`에도 `audit`를 채운다. v10·v11은 `no_signal`이면 `audit`가 정확히
null이라 그 데이터로 학습한 Auditor를 서빙하면 구조화 출력이 학습한 출력을 막는다.

묶는 것은 다섯이다.

1. **요청 바이트가 안 바뀐 판** — 한 방 `Finding` · v8·v9 `AuditorAction` · v10 · v11의 봉투와
   최종 답 · 감사 블록 셋의 `model_json_schema()`와 v10·v11 prefix·body가 v12를 더하기 전과 바이트
   동일하다(sha256).
2. **v12 스키마** — `audit` non-null · 값 셋(`none` · `not_applicable` · `no_candidate`)은
   `no_signal`에서만, `no_signal`이면 셋 다 · `cwe` 규칙은 v11 그대로.
3. **배선** — meta 선언 → 봉투 · 허용집합 · `_CONTENT_AWARE_VERSIONS` · Fake 빌더 · 서빙 좌석은
   v12를 안 고른다.
4. **노드** — `_answer_observed`가 `no_candidate`에서 안 죽고, `_finding_from_procedure`가
   `no_signal`의 경로를 `Finding.path`로 안 옮기며, 저장 왕복이 같은 Finding을 낸다.
5. **산문** — v11 대비 바뀐 줄이 `audit` 서술뿐이고, 산문의 값 집합 · 짝 표 · 경로 규칙이 스키마와
   노드의 것과 같다.

⚠ 1의 sha256은 **v12를 더하기 전 브랜치 머리(77422ed — PR #575)에서 잰 값이다**(직렬화는
`json.dumps(schema, ensure_ascii=False)` — `tests/test_nullable_cwe.py`와 같다). 이 클래스들이나
v10·v11 산문을 고치면 핀이 빨개지고, 그것이 목적이다(evaluation §2.3).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from typing import get_args

import pytest
from pydantic import ValidationError

from src.adapters.fake_model import _BUILDERS, FakeContext
from src.config import load_config
from src.orchestrator.nodes import auditor
from src.orchestrator.prompt_loader import PROMPTS_DIR, PromptError, load_prompt
from src.schemas import ProducedBy, Role
from src.schemas.auditor import (
    AuditorAction,
    AuditorProcedureAction,
    AuditorProcedureAuditAlwaysAction,
    AuditorProcedureNullableCweAction,
    AuditProcedure,
    AuditProcedureAuditAlways,
    AuditProcedureNullableCwe,
)
from src.schemas.finding import (
    NO_SIGNAL_ONLY_AUDIT_VALUES,
    AuditBlock,
    AuditBlockWithNoSignal,
    AuditDefense,
    AuditDefenseWithNoSignal,
    AuditRefutation,
    AuditRefutationWithNoSignal,
    Finding,
)

# === 1. 요청 바이트가 안 바뀐 판 ===============================================================

# 77422ed(PR #575 머리 — v12 전)에서 잰 값. 앞 넷은 `tests/test_nullable_cwe.py`의 핀과 같은 값이다.
_SCHEMA_SHA256_BEFORE_V12 = {
    Finding: "3d80b9d8f58bcb42917110f13308f3783e90a8af170c3f52635b548b3c3bf900",
    AuditorAction: "bc57826011500c0441e9a62b61e487342b19a0f9fe97f32bd21e0b87fc37af0f",
    AuditorProcedureAction: "de80108c2234784a2be53d83dcc6c85493dce86c93cdefb51f70557a6db1c2a5",
    AuditProcedure: "be52bc8ac3f1a18bbeda41d22d8ea7eed47d051132f2744cf96db7e7862b1b9b",
    AuditorProcedureNullableCweAction: (
        "b0e5b7c9f9402c5ebfc2a10ca92586413adc230826d6c1d6e472ebe648e88777"
    ),
    AuditProcedureNullableCwe: "ebd79c41b42a1682a83eb504e91f406dddc29042f005c4337182328130ed400d",
    AuditBlock: "fb3a43abc9665d636a726722b27c99ce4d8bf21e6d44fd99f47deacfbe2454a3",
    AuditDefense: "92267a9ffa863c9141a7547a1dc3156fb21f9da17e21c58dd3434658f2c6506d",
    AuditRefutation: "ba24834b1d469c574b2e9aa7f1cd098b0b72a54a2ffd05ed99785180da501206",
}
_PROMPT_SHA256_BEFORE_V12 = {
    "v10": (
        "18c9a320cac383f04d630ee52583de07f99cf8ffeea0d6f0d95909956d5acdb5",
        "bac0cb11af57bb460c4c36bc723b828e66e6200cc64cab15358ec80677e6c1d0",
    ),
    "v11": (
        "faaa5134d1f6f1fb88df0d1b1f7938511712423b9d20fbd6a974917279d8feb5",
        "bac0cb11af57bb460c4c36bc723b828e66e6200cc64cab15358ec80677e6c1d0",
    ),
}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("schema", list(_SCHEMA_SHA256_BEFORE_V12), ids=lambda s: s.__name__)
def test_the_request_schemas_before_v12_are_byte_identical(schema: type) -> None:
    """v12의 형제 클래스를 더하고 `Finding.audit`를 넓혀도 v1~v11이 보내는 스키마는 그대로다.

    `Finding.audit`는 `SkipJsonSchema`라 합집합째로 요청 스키마에서 빠진다 — `Finding`(한 방 판)과
    `AuditorAction`(v8·v9, `$defs`에 `Finding`)의 핀이 그것을 문다. 감사 블록 셋은 v10·v11 요청의
    `$defs`다."""
    assert _sha256(json.dumps(schema.model_json_schema(), ensure_ascii=False)) == (
        _SCHEMA_SHA256_BEFORE_V12[schema]
    )


@pytest.mark.parametrize("version", ["v10", "v11"])
def test_the_v10_and_v11_prompt_text_is_byte_identical(version: str) -> None:
    template = load_prompt(Role.AUDITOR, version)

    assert (_sha256(template.prefix), _sha256(template.body)) == (
        _PROMPT_SHA256_BEFORE_V12[version]
    )


# === 2. v12 스키마 ============================================================================

_STATUSES = get_args(AuditProcedure.model_fields["trigger_status"].annotation)
_NOT_NO_SIGNAL = tuple(s for s in _STATUSES if s != "no_signal")
_RESULT_BY_STATUS = {
    "triggerable": "defeated",
    "guarded": "holds",
    "no_reach": "path_not_executable",
    "reachability_unknown": "decisive_code_missing",
    "no_signal": "no_candidate",
}


def _audit(status: str) -> dict[str, object]:
    """그 상태에 맞는 최소 `audit` — `no_signal`이면 세 값을 쓴다."""
    no_signal = status == "no_signal"
    return {
        "category": "none" if no_signal else "spatial_bounds",
        "point": {"symbol": "f", "expression": "memcpy(d, s, n)"},
        "origin": {"kind": "param", "fact": "n is a parameter"},
        "path": [
            {"step": "source", "symbol": "f", "note": "n enters"},
            {"step": "sink", "symbol": "f", "note": "copies n bytes"},
        ],
        "defense": {
            "expression": "none on visible path",
            "status": "not_applicable" if no_signal else "absent",
            "gap": None,
            "fact": "-",
        },
        "refutation": {
            "result": _RESULT_BY_STATUS[status],
            "witness": "w",
            "missing_information": [],
        },
    }


def _payload(status: str, **override: object) -> dict[str, object]:
    base: dict[str, object] = {
        "audit": _audit(status),
        "trigger_status": status,
        "confidence": 0.5,
        "cwe": None if status == "no_signal" else "CWE-787",
        "title": "t",
        "rca_draft": "r",
        "evidence_refs": [],
        "repro_spec": None,
    }
    return {**base, **override}


def _with(audit: dict[str, object], key: str, value: str) -> dict[str, object]:
    """`audit`의 점 경로(`defense.status` 등) 하나를 바꾼 사본."""
    copy = json.loads(json.dumps(audit))
    head, _, tail = key.partition(".")
    if tail:
        copy[head][tail] = value
    else:
        copy[head] = value
    return copy


def test_v12_accepts_every_status_with_a_filled_audit() -> None:
    for status in _STATUSES:
        AuditProcedureAuditAlways.model_validate(_payload(status))


@pytest.mark.parametrize("status", _STATUSES)
def test_v12_refuses_a_null_audit_for_every_status(status: str) -> None:
    """`audit`는 어느 상태에서도 null이 아니다 — v10·v11의 「no_signal이면 null」이 남지 않았다."""
    with pytest.raises(ValidationError):
        AuditProcedureAuditAlways.model_validate(_payload(status, audit=None))


def test_v10_and_v11_still_refuse_a_filled_audit_for_no_signal() -> None:
    """반대쪽 — v10·v11의 계약은 안 바뀌었다. v12의 `no_signal` 답을 그 둘은 거절한다."""
    for schema in (AuditProcedure, AuditProcedureNullableCwe):
        with pytest.raises(ValidationError):
            schema.model_validate(_payload("no_signal", cwe="CWE-787"))


@pytest.mark.parametrize("key", list(NO_SIGNAL_ONLY_AUDIT_VALUES))
def test_no_signal_needs_all_three_no_signal_values(key: str) -> None:
    """`no_signal`이면 세 값 셋 다다 — 하나라도 다른 값이면 거절한다(Teacher v4
    `validate_teacher_output`과 같은 쪽)."""
    replacement = {"category": "spatial_bounds", "defense.status": "absent"}.get(key, "defeated")
    audit = _with(_audit("no_signal"), key, replacement)

    with pytest.raises(ValidationError, match=key):
        AuditProcedureAuditAlways.model_validate(_payload("no_signal", audit=audit))


@pytest.mark.parametrize("status", _NOT_NO_SIGNAL)
@pytest.mark.parametrize("key", list(NO_SIGNAL_ONLY_AUDIT_VALUES))
def test_the_no_signal_values_are_refused_for_other_statuses(status: str, key: str) -> None:
    audit = _with(_audit(status), key, NO_SIGNAL_ONLY_AUDIT_VALUES[key])

    with pytest.raises(ValidationError, match="no_signal일 때에만"):
        AuditProcedureAuditAlways.model_validate(_payload(status, audit=audit))


def test_v12_keeps_the_v11_cwe_rule() -> None:
    """`cwe` null은 `no_signal`에서만 — 다섯 상태를 다 돌려 집합으로 단언한다. `no_signal`에 CWE
    문자열을 내는 것은 v11처럼 스키마가 안 막는다(산문만 null을 시킨다)."""
    accepted = set()
    for status in _STATUSES:
        try:
            AuditProcedureAuditAlways.model_validate(_payload(status, cwe=None))
        except ValidationError:
            continue
        accepted.add(status)

    assert accepted == {"no_signal"}
    AuditProcedureAuditAlways.model_validate(_payload("no_signal", cwe="CWE-787"))


def test_v12_does_not_reject_a_status_result_mismatch_among_the_other_four() -> None:
    """나머지 네 쌍의 어긋남은 v10·v11처럼 **거절하지 않는다**(노드가 센다 — 아래 4). 빈 경로도
    상태와 무관하게 받는다."""
    audit = _with(_audit("triggerable"), "refutation.result", "holds")
    AuditProcedureAuditAlways.model_validate(_payload("triggerable", audit=audit))
    for status in ("no_signal", "reachability_unknown", "triggerable"):
        audit = {**_audit(status), "path": []}
        AuditProcedureAuditAlways.model_validate(_payload(status, audit=audit))


@pytest.mark.parametrize(
    ("old", "new", "field", "added"),
    [
        (AuditBlock, AuditBlockWithNoSignal, "category", "none"),
        (AuditDefense, AuditDefenseWithNoSignal, "status", "not_applicable"),
        (AuditRefutation, AuditRefutationWithNoSignal, "result", "no_candidate"),
    ],
)
def test_each_v12_block_adds_exactly_one_value_and_nothing_else(
    old: type, new: type, field: str, added: str
) -> None:
    """v12 블록은 v10 블록의 값 집합 + 하나다. 순서도 v10의 것 뒤에 붙인다. 그 밖의 필드 스키마는
    같다 — 다른 데가 넓어지면 v10의 「같은 값은 같은 판정」이 깨진다."""
    old_values = get_args(old.model_fields[field].annotation)
    new_values = get_args(new.model_fields[field].annotation)

    assert new_values == (*old_values, added)
    old_schema = old.model_json_schema()
    new_schema = new.model_json_schema()
    assert list(new_schema["properties"]) == list(old_schema["properties"])
    assert new_schema["required"] == old_schema["required"]
    changed = {
        k
        for k in old_schema["properties"]
        if old_schema["properties"][k] != new_schema["properties"][k]
        and not k.endswith(("defense", "refutation"))
    }
    assert changed == {field}


def test_v12_envelope_differs_from_v11_only_in_the_audit_field() -> None:
    """v12 봉투의 JSON 스키마는 v11 것에서 `procedure.audit`(non-null · v12 블록)와 이름·설명만
    다르다 — 필드 순서(#102의 생성 순서 근거) · required · 액션 서술이 같다."""
    v11 = AuditorProcedureNullableCweAction.model_json_schema()
    v12 = AuditorProcedureAuditAlwaysAction.model_json_schema()
    proc11 = v11["$defs"].pop("AuditProcedureNullableCwe")
    proc12 = v12["$defs"].pop("AuditProcedureAuditAlways")

    assert v12["description"] == v11["description"]
    assert {k: v for k, v in v12["properties"].items() if k != "procedure"} == {
        k: v for k, v in v11["properties"].items() if k != "procedure"
    }
    assert list(proc12["properties"]) == list(proc11["properties"])
    assert proc12["required"] == proc11["required"]
    changed = {
        k for k in proc11["properties"] if proc11["properties"][k] != proc12["properties"][k]
    }
    assert changed == {"audit"}
    assert proc12["properties"]["audit"] == {"$ref": "#/$defs/AuditBlockWithNoSignal"}
    for name in ("AuditBlock", "AuditDefense", "AuditRefutation"):
        assert name in v11["$defs"] and name not in v12["$defs"]


def test_a_teacher_v4_shaped_no_signal_answer_is_accepted() -> None:
    """Teacher v4 출력 계약의 `no_signal` 꼴 — `audit` 여섯 필드 · `cwe` null ·
    `evidence_refs` `[]` · `repro_spec` null — 이 v12 최종 답으로 그대로 선다(`Teacher_v4.md`
    「Output contract」)."""
    teacher = {
        "audit": {
            "category": "none",
            "point": {"symbol": "parse", "expression": "buf[i] = c"},
            "origin": {"kind": "local", "fact": "i counts from 0 below sizeof buf"},
            "path": [
                {"step": "source", "symbol": "parse", "note": "i starts at 0"},
                {"step": "propagate", "symbol": "parse", "note": "loop bound i < sizeof buf"},
                {"step": "sink", "symbol": "parse", "note": "store into buf"},
            ],
            "defense": {
                "expression": "i < sizeof buf",
                "status": "not_applicable",
                "gap": None,
                "fact": "index never leaves the array",
            },
            "refutation": {
                "result": "no_candidate",
                "witness": "the loop bound equals the array size, so no category applies",
                "missing_information": [],
            },
        },
        "trigger_status": "no_signal",
        "confidence": 0.05,
        "cwe": None,
        "title": "no weakness in parse",
        "rca_draft": "no dangerous operation is established",
        "evidence_refs": [],
        "repro_spec": None,
    }
    action = AuditorProcedureAuditAlwaysAction.model_validate(
        {"action": "answer", "procedure": teacher, "rationale": "done"}
    )

    assert isinstance(action.procedure, AuditProcedureAuditAlways)


# === 3. 배선 ==================================================================================


def test_v12_declares_the_audit_always_envelope_and_is_a_loop_prompt() -> None:
    template = load_prompt(Role.AUDITOR, "v12")

    assert template.output_schema == "AuditorProcedureAuditAlwaysAction"
    assert auditor.resolve_output_schema(template.output_schema) is (
        AuditorProcedureAuditAlwaysAction
    )
    assert auditor._loop_envelope(template) is AuditorProcedureAuditAlwaysAction
    assert AuditorProcedureAuditAlwaysAction in _BUILDERS
    assert "v12" in auditor._CONTENT_AWARE_VERSIONS
    with pytest.raises(PromptError, match="이 역할이 낼 수 없다"):
        auditor.resolve_output_schema("AuditProcedureAuditAlways")


def test_v12_meta_matches_v11_except_the_envelope() -> None:
    """SOURCE 옵션 · 도구가 v11과 같다 — 옵션 서술을 v11 그대로 둔 판이라서다
    (`cli._undescribed_source_options`가 meta 선언을 읽는다)."""
    v11 = load_prompt(Role.AUDITOR, "v11")
    v12 = load_prompt(Role.AUDITOR, "v12")

    assert v12.source_options == v11.source_options
    assert v12.tools == v11.tools
    assert v12.body == v11.body


@pytest.mark.parametrize("profile", ["harness_pool", "harness_pool_hybrid"])
def test_no_serving_auditor_selects_v12(profile: str) -> None:
    """v12는 FT Auditor를 서빙할 때 고를 판이다 — 그 전환은 이 PR의 결정이 아니다(이슈 #578).
    서빙 판 이름을 박지 않는다 — 다른 PR이 서빙 판을 올려도(`!= "v12"`) 이 핀은 안 깨진다."""
    assert load_config(profile).model_for("auditor").prompt_template_version != "v12"


def test_the_fake_builder_answers_with_the_v12_procedure() -> None:
    """Fake가 v12 봉투를 낸다 — 빌더가 빠지면 v12를 고르는 Fake run이 `FakeModelError`로 죽는다.
    fake 판정(`triggerable`·`reachability_unknown`)은 `audit`를 채우므로 v12 검증기를 지난다."""
    context = FakeContext(run_id="run_0001", source="memcpy(d, s, n);")
    action = _BUILDERS[AuditorProcedureAuditAlwaysAction](
        context, ProducedBy(role=Role.AUDITOR, model="fake", prompt_version="v12"), ()
    )

    assert isinstance(action, AuditorProcedureAuditAlwaysAction)
    if action.action == "answer":
        assert isinstance(action.procedure, AuditProcedureAuditAlways)


# === 4. 노드 ==================================================================================

_STAMPED = ProducedBy(role=Role.AUDITOR, model="m", prompt_version="v12")
_IDS = {"finding_id": "find_0001", "candidate_id": "cand_0001", "missing_id": "mev_0001"}


def _moved(status: str, **override: object) -> Finding:
    procedure = AuditProcedureAuditAlways.model_validate(_payload(status, **override))
    return auditor._finding_from_procedure(
        procedure, ids=_IDS, run_id="run_0001", produced_by=_STAMPED
    )


def test_answer_observed_reads_no_candidate_without_a_key_error() -> None:
    """`_STATUS_BY_RESULT`에 `no_candidate`가 없으면 v12의 `no_signal` 답마다 `KeyError`로 홉이
    죽는다 — 모델 출력이 아니라 하네스 코드의 예외다. 맞는 짝이라 어긋남 키도 안 생긴다."""
    procedure = AuditProcedureAuditAlways.model_validate(_payload("no_signal"))

    observed = auditor._answer_observed(procedure, cited=0)

    assert observed == {"cited_probe_evidence": 0, "trigger_status": "no_signal"}


def test_answer_observed_still_counts_a_mismatch_among_the_other_four() -> None:
    audit = _with(_audit("triggerable"), "refutation.result", "holds")
    procedure = AuditProcedureAuditAlways.model_validate(_payload("triggerable", audit=audit))

    observed = auditor._answer_observed(procedure, cited=0)

    assert observed["status_result_mismatch"] == "triggerable/holds"


def test_a_v12_no_signal_finding_keeps_the_audit_but_not_the_path() -> None:
    """`no_signal`의 `audit.path`는 걸러낸 후보를 따라간 기록이라 `Finding.path`로 안 옮긴다 —
    옮기면 `trigger_status`를 안 읽는 게이트 `p2`가 그 양 끝을 센다. 블록은 그대로 남는다."""
    finding = _moved("no_signal")

    assert finding.path == []
    assert isinstance(finding.audit, AuditBlockWithNoSignal)
    assert finding.audit.category == "none"
    assert [step.step for step in finding.audit.path] == ["source", "sink"]
    assert finding.missing_evidence == []
    assert finding.cwe is None


@pytest.mark.parametrize("status", _STATUSES)
def test_a_v12_finding_survives_the_store_round_trip_and_a_transition(status: str) -> None:
    """저장 왕복(`model_dump(mode="json")` → `model_validate`)과 `transition`(재검증)이 같은
    Finding을 낸다 — 블록 클래스를 값이 정하므로(`finding._canonical_audit`) 전이 앞뒤로 클래스가
    안 바뀐다.
    `no_signal` 밖의 상태는 `AuditBlock`으로 선다(v10 블록으로 서는 값이다)."""
    finding = _moved(status)

    assert Finding.model_validate(finding.model_dump(mode="json")) == finding
    assert finding.transition(confidence=0.4).audit == finding.audit
    expected = AuditBlockWithNoSignal if status == "no_signal" else AuditBlock
    assert type(finding.audit) is expected


def test_the_v12_path_and_missing_evidence_match_v10_for_the_other_statuses() -> None:
    """`no_signal` 밖에서는 v10과 같은 길이다 — 경로를 옮기고 `reachability_unknown`이면 발급
    `missing_id`를 붙인다."""
    assert [s.step for s in _moved("triggerable").path] == ["source", "sink"]
    assert _moved("reachability_unknown").missing_evidence == [_IDS["missing_id"]]
    assert _moved("guarded").missing_evidence == []


# === 5. 산문 ==================================================================================


def _lines(version: str) -> list[str]:
    path = PROMPTS_DIR / "auditor" / version / "prefix.md"
    return path.read_text(encoding="utf-8").splitlines()


# v11에서 바뀐 줄이 건드리는 것 — 줄마다 이 중 하나를 든다.
_AUDIT_TOKENS = ("audit", "no_signal", "no_candidate", "not_applicable", "`none`")


def test_v12_changes_only_audit_lines_of_v11() -> None:
    """v11 대비 바뀐 줄은 전부 `audit`·`no_signal` 서술이다 — 그 밖(도구 · FACTS · SOURCE 옵션
    서술)은 바이트 그대로다. 바뀐 줄 수를 핀으로 든다 — 늘면 서술이 어디로 샜는지 본다."""
    v11, v12 = _lines("v11"), _lines("v12")
    matcher = difflib.SequenceMatcher(a=v11, b=v12, autojunk=False)
    changed_old: list[str] = []
    changed_new: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            changed_old += v11[i1:i2]
            changed_new += v12[j1:j2]

    assert (len(changed_old), len(changed_new)) == (19, 20)
    for line in changed_new:
        assert any(token in line for token in _AUDIT_TOKENS), line


def test_no_v12_line_still_says_audit_is_null() -> None:
    """반대쪽 — v11이 적던 「audit는 null」 문형이 v12에 하나도 안 남았다(v11 실물에서 고른 넷)."""
    v12 = load_prompt(Role.AUDITOR, "v12").prefix

    for phrase in (
        "`audit` is null exactly when no_signal",
        'output `"audit": null`',
        "(when `audit` is not null)",
        "then `audit` is null",
    ):
        assert phrase in load_prompt(Role.AUDITOR, "v11").prefix, f"전제 — v11 실물: {phrase}"
        assert phrase not in v12, phrase


def test_the_content_aware_reasons_are_unchanged_in_v12() -> None:
    """`_CONTENT_AWARE_VERSIONS`가 v12를 드는 근거 — `## Evidence body completeness` 절의 머리와
    `FACTS.evidence[*].evidence_id` 인용 줄이 v11과 같은 바이트로 v12에 있다."""
    v12 = _lines("v12")
    for line in _lines("v11"):
        if "FACTS.evidence[*].evidence_id" in line or line.startswith(
            ("## Evidence body completeness", "A `FACTS.evidence[].body`, or the log")
        ):
            assert line in v12, line


def test_the_v12_prose_pairs_match_the_node_map() -> None:
    (line,) = [x for x in _lines("v12") if "↔ trigger_status:" in x]
    pairs = dict(re.findall(r"(\w+)→(\w+)", line))

    assert pairs == auditor._STATUS_BY_RESULT


def test_the_v12_prose_lists_exactly_the_schema_values() -> None:
    """출력 계약 예시의 `category` 열쇠 · `defense.status` · `refutation.result` 값이 v12 블록의
    Literal과 같다."""
    lines = _lines("v12")
    (keys,) = [x for x in lines if x.startswith("Category keys:")]
    (status,) = [x for x in lines if x.strip().startswith('"status": "absent')]
    (result,) = [x for x in lines if x.strip().startswith('"result": "defeated')]

    category = get_args(AuditBlockWithNoSignal.model_fields["category"].annotation)
    assert tuple(re.findall(r"`(\w+)`", keys)) == category
    assert tuple(status.split('"')[3].split("|")) == get_args(
        AuditDefenseWithNoSignal.model_fields["status"].annotation
    )
    assert tuple(result.split('"')[3].split("|")) == get_args(
        AuditRefutationWithNoSignal.model_fields["result"].annotation
    )


def test_the_v12_prose_names_the_statuses_whose_path_must_not_be_empty() -> None:
    """「`audit.path` may be `[]` for … and is non-empty for A/B/C」의 A/B/C가 노드의
    `_PATH_REQUIRED_STATUSES`와 같다 — 노드가 `path_without_endpoints`로 세는 상태다."""
    (line,) = [x for x in _lines("v12") if "`audit` is never null, for any trigger_status" in x]
    required = re.search(r"is non-empty for ([\w/]+)", line)

    assert required is not None
    assert tuple(required.group(1).split("/")) == auditor._PATH_REQUIRED_STATUSES
    # 반대쪽 — 「may be `[]` for X and for Y」의 빈 경로 허용 상태가 나머지 전부다.
    empty = re.search(r"may be `\[\]` for ([\w ]+?), and is non-empty", line)
    assert empty is not None
    assert set(empty.group(1).split(" and for ")) == set(_STATUSES) - set(
        auditor._PATH_REQUIRED_STATUSES
    )


def test_the_v12_prose_names_the_no_signal_only_values_the_validator_enforces() -> None:
    """`no_signal` 전용 값 셋의 **이름**이 스키마(`NO_SIGNAL_ONLY_AUDIT_VALUES`)와 같다 — 값 집합
    핀은 예시 JSON의 세 줄만 읽어 이 문장을 안 본다(적대적 리뷰 #1 · M2·M2b). ⚠ 「no_signal 전용 ·
    셋 다」라는 양화는 구문으로 읽을 길이 없어 철자로 묶는다."""
    (line,) = [x for x in _lines("v12") if "`audit` records the candidate that came closest" in x]
    named = re.findall(r"`(category|defense\.status|refutation\.result)` `(\w+)`", line)

    assert dict(named) == NO_SIGNAL_ONLY_AUDIT_VALUES and len(named) == 3
    assert "go with no_signal only" in line and "always uses all three" in line


def test_the_v12_example_tail_fills_audit_with_the_no_signal_only_values() -> None:
    """출력 계약 끝의 「For no_signal, …」 문장: `audit`를 채우라 하고(생략·null 지시 없음), 세 값이
    스키마와 같고, `cwe`만 null이다(적대적 리뷰 #1 · M0·M1)."""
    (line,) = [x for x in _lines("v12") if x.startswith("For no_signal,")]
    values = dict(re.findall(r'"(category|status|result)": "(\w+)"', line))

    assert values == {k.split(".")[-1]: v for k, v in NO_SIGNAL_ONLY_AUDIT_VALUES.items()}
    assert '"cwe": null' in line
    assert not re.search(r"omit|null", line.replace('"cwe": null', ""))
