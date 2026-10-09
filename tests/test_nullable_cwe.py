"""`cwe`가 `no_signal`에서만 null일 수 있다 — 이슈 #525 선택지 1 · D107.

묶는 것은 넷이다.

1. **요청 바이트가 안 바뀐 판** — `Finding`(한 방 판의 요청 스키마) · `AuditorAction`(v8·v9) ·
   `AuditorProcedureAction`(v10)의 `model_json_schema()`와 v10 prefix·body가 넓히기 전과 바이트
   동일하다.
   요청 payload는 system(prefix) · user(body 렌더) · `response_format`(그 스키마)로 조립되므로
   (`adapters/openai_compat`) 셋이 같으면 같은 입력에서 v10 요청이 같다.
2. **v11** — 형제 봉투가 `cwe` null을 `no_signal`에서만 받고, 산문 차이가 `cwe` 서술 네 줄뿐이다.
3. **`Finding`** — 저장 모양이 같은 조건으로 null을 받고 옛 행(CWE 문자열)이 그대로 읽힌다.
4. **읽는 자리** — `None`이 `"None"`이라는 글자로 새지 않고, CWE가 있으면 렌더가 종전과 같다.

⚠ 1의 sha256은 **넓히기 전 `origin/main`(a78b09f)에서 잰 값이다** — 같은 함수로 그 트리에서
직렬화했다
(`json.dumps(schema, ensure_ascii=False)`). 이 네 클래스나 v10 산문을 고치면 이 핀이 빨개지고,
그것이
목적이다(evaluation §2.3 — 프롬프트가 안 바뀌었는데 요청이 바뀌면 재측정이 걸린다).
"""

from __future__ import annotations

import hashlib
import json
from typing import get_args

import pytest
from pydantic import ValidationError

from src.cli import _sarif_result
from src.orchestrator.nodes import auditor, critic, pov, report
from src.orchestrator.prompt_loader import PROMPTS_DIR, load_prompt
from src.schemas import FindingState, ProducedBy, Role
from src.schemas.auditor import (
    AuditorAction,
    AuditorProcedureAction,
    AuditorProcedureNullableCweAction,
    AuditProcedure,
    AuditProcedureNullableCwe,
)
from src.schemas.finding import Finding, PathStep

# === 1. 요청 바이트가 안 바뀐 판 ===============================================================

# `origin/main` a78b09f(넓히기 전)에서 잰 값 — 모듈 docstring의 ⚠.
_SCHEMA_SHA256_BEFORE = {
    Finding: "3d80b9d8f58bcb42917110f13308f3783e90a8af170c3f52635b548b3c3bf900",
    AuditorAction: "bc57826011500c0441e9a62b61e487342b19a0f9fe97f32bd21e0b87fc37af0f",
    AuditorProcedureAction: "de80108c2234784a2be53d83dcc6c85493dce86c93cdefb51f70557a6db1c2a5",
    AuditProcedure: "be52bc8ac3f1a18bbeda41d22d8ea7eed47d051132f2744cf96db7e7862b1b9b",
}
_V10_PREFIX_SHA256 = "18c9a320cac383f04d630ee52583de07f99cf8ffeea0d6f0d95909956d5acdb5"
_V10_BODY_SHA256 = "bac0cb11af57bb460c4c36bc723b828e66e6200cc64cab15358ec80677e6c1d0"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("schema", list(_SCHEMA_SHA256_BEFORE), ids=lambda s: s.__name__)
def test_the_request_schemas_of_the_versions_before_v11_are_byte_identical(schema: type) -> None:
    """`Finding.cwe`를 넓혀도 v1~v10이 보내는 스키마는 넓히기 전 바이트 그대로다.

    `Finding`은 한 방 판이 직접, `AuditorAction`(v8·v9)이 `$defs`로 싣는다 — `cwe`를 `anyOf`로
    넓히면
    프롬프트가 안 바뀐 그 판들의 요청이 바뀐다. `WithJsonSchema`가 그것을 막는다(`finding.py`의
    `_FINDING_CWE_JSON_SCHEMA`). `AuditorProcedureAction`·`AuditProcedure`(v10)는 아예 안 고쳤다."""
    assert (
        _sha256(json.dumps(schema.model_json_schema(), ensure_ascii=False))
        == (_SCHEMA_SHA256_BEFORE[schema])
    )


def test_the_v10_prompt_text_is_byte_identical() -> None:
    """v10 prefix·body는 안 고쳤다 — v11이 새 판으로 섰다(agent-contracts §4 규칙 1)."""
    template = load_prompt(Role.AUDITOR, "v10")

    assert _sha256(template.prefix) == _V10_PREFIX_SHA256
    assert _sha256(template.body) == _V10_BODY_SHA256
    assert template.output_schema == "AuditorProcedureAction"


def test_the_finding_request_schema_still_says_cwe_is_a_required_string() -> None:
    """위 sha256의 뜻을 성질로 한 번 더 — 요청 스키마의 `cwe`에 `null`이 없고 required다.

    sha256 핀은 바이트가 바뀌면 빨개지지만 **무엇이** 바뀌었는지는 안 말한다. 이 검사는 넓힌 방향
    (null 허용이 요청에 새는 것)을 이름으로 문다."""
    schema = Finding.model_json_schema()

    assert schema["properties"]["cwe"] == {
        "pattern": r"^CWE-\d+$",
        "title": "Cwe",
        "type": "string",
    }
    assert "cwe" in schema["required"]


# === 2. v11 ===================================================================================

_STATUSES = get_args(AuditProcedure.model_fields["trigger_status"].annotation)

# v10 산문(`prompts/auditor/v10/prefix.md`)의 실물 줄 — 이 넷이 v11에서 바뀐 자리다.
_V10_CWE_LINES = (
    "no_signal → keep the candidate-relevant CWE when one is supplied, because the harness "
    "requires a non-null `CWE-<number>` string for every status",
    '      "cwe": "CWE-<number>",',
    "`cwe` stays a `CWE-<number>` string, because the harness requires a non-null one for every "
    "status (step 5).",
    "`cwe` always matches `^CWE-\\d+$`; it is never null, whatever the trigger_status.",
)
# 같은 네 자리의 v11 줄(`prompts/auditor/v11/prefix.md`).
_V11_CWE_LINES = (
    'no_signal → `"cwe": null`: no root cause was established, so no CWE applies — do not carry '
    "over the Scout's suspected CWE",
    '      "cwe": "CWE-<number>, or null for no_signal only",',
    'For no_signal, output `"audit": null` and `"cwe": null`, and keep the other fields (step 5).',
    "`cwe` is null when trigger_status is no_signal, and otherwise matches `^CWE-\\d+$`; it is "
    "never null for any other status.",
)


def _payload(status: str, **override: object) -> dict[str, object]:
    """`tests/test_auditor_prompt_v10.py`의 `_procedure_payload`와 같은 최소 payload."""
    audit = {
        "category": "spatial_bounds",
        "point": {"symbol": "f", "expression": "memcpy(d, s, n)"},
        "origin": {"kind": "param", "fact": "n is a parameter"},
        "path": [{"step": "sink", "symbol": "f", "note": "copies n bytes"}],
        "defense": {
            "expression": "none on visible path",
            "status": "absent",
            "gap": None,
            "fact": "-",
        },
        "refutation": {"result": "defeated", "witness": "n=65", "missing_information": []},
    }
    base: dict[str, object] = {
        "audit": None if status == "no_signal" else audit,
        "trigger_status": status,
        "confidence": 0.5,
        "cwe": "CWE-787",
        "title": "t",
        "rca_draft": "r",
        "evidence_refs": [],
        "repro_spec": None,
    }
    return {**base, **override}


def _accepted(schema: type[AuditProcedure], **override: object) -> set[str]:
    accepted = set()
    for status in _STATUSES:
        try:
            schema.model_validate(_payload(status, **override))
        except ValidationError:
            continue
        accepted.add(status)
    return accepted


def test_v11_accepts_a_null_cwe_exactly_for_no_signal() -> None:
    """v11은 null `cwe`를 `no_signal`에서만 받는다. 다섯 상태를 다 돌려 **집합**으로 단언한다 —
    `no_signal` 하나만 보면 검증기를 지워도 초록이다."""
    assert _accepted(AuditProcedureNullableCwe, cwe=None) == {"no_signal"}
    assert _accepted(AuditProcedureNullableCwe) == set(_STATUSES), "CWE 문자열은 다 받는다"


def test_v10_still_refuses_a_null_cwe_for_every_status() -> None:
    """v10의 `AuditProcedure`는 안 넓혔다 — null `cwe`를 어느 상태에서도 거절한다."""
    assert _accepted(AuditProcedure, cwe=None) == set()


def test_v11_schema_differs_from_v10_only_in_cwe() -> None:
    """v11 봉투의 JSON 스키마는 v10 것에서 `procedure`의 `cwe`(null 허용)와 이름·설명만 다르다 —
    필드 순서(#102의 생성 순서 근거)와 액션 서술이 같다."""
    v10 = AuditorProcedureAction.model_json_schema()
    v11 = AuditorProcedureNullableCweAction.model_json_schema()
    proc10 = v10["$defs"].pop("AuditProcedure")
    proc11 = v11["$defs"].pop("AuditProcedureNullableCwe")

    assert v11["description"] == v10["description"]
    assert list(v11["properties"]) == list(v10["properties"])
    assert {k: v for k, v in v11["properties"].items() if k != "procedure"} == {
        k: v for k, v in v10["properties"].items() if k != "procedure"
    }
    assert v11["$defs"] == v10["$defs"], "AuditBlock·PovSpec 등 나머지 정의는 같다"
    assert list(proc11["properties"]) == list(proc10["properties"])
    assert proc11["required"] == proc10["required"], "`cwe`는 nullable이되 여전히 required다"
    changed = {
        k for k in proc10["properties"] if proc10["properties"][k] != proc11["properties"][k]
    }
    assert changed == {"cwe"}
    assert {"type": "null"} in proc11["properties"]["cwe"]["anyOf"]


def test_v11_prompt_differs_from_v10_only_in_the_four_cwe_lines() -> None:
    """v11 prefix는 v10에서 `cwe` 서술 네 줄만 바꿨고 body는 같다. 바뀐 줄은 v10의 실물 넷 →
    v11의 넷이다 — `_CONTENT_AWARE_VERSIONS`가 v11을 드는 근거(본문 서술이 v10과 같다)가
    이것이다."""
    v10 = (PROMPTS_DIR / "auditor" / "v10" / "prefix.md").read_text(encoding="utf-8").splitlines()
    v11 = (PROMPTS_DIR / "auditor" / "v11" / "prefix.md").read_text(encoding="utf-8").splitlines()

    assert len(v10) == len(v11)
    changed = [(old, new) for old, new in zip(v10, v11, strict=True) if old != new]
    assert len(changed) == 4
    for (old, new), was, now in zip(changed, _V10_CWE_LINES, _V11_CWE_LINES, strict=True):
        assert was in old and now in new
    assert load_prompt(Role.AUDITOR, "v11").body == load_prompt(Role.AUDITOR, "v10").body
    assert "v11" in auditor._CONTENT_AWARE_VERSIONS


def test_no_v11_line_still_says_cwe_is_never_null_for_no_signal() -> None:
    """반대쪽 — v10이 적던 「non-null for every status」 문형이 v11에 하나도 안 남았다(다섯 문형을
    v10 실물에서 골랐다: 그 넷 + 계약 예시의 문자열 전용 값)."""
    v11 = load_prompt(Role.AUDITOR, "v11").prefix

    for phrase in (
        "requires a non-null",
        "never null, whatever the trigger_status",
        "stays a `CWE-<number>` string",
        "keep the candidate-relevant CWE",
        '"cwe": "CWE-<number>",',
    ):
        assert phrase not in v11, phrase


def test_v11_selects_the_nullable_cwe_envelope() -> None:
    template = load_prompt(Role.AUDITOR, "v11")

    assert (
        auditor.resolve_output_schema(template.output_schema) is AuditorProcedureNullableCweAction
    )
    assert auditor._loop_envelope(template) is AuditorProcedureNullableCweAction


# === 3. Finding ===============================================================================

_STAMPED = ProducedBy(role=Role.AUDITOR, model="m", prompt_version="v11")
_IDS = {"finding_id": "find_0001", "candidate_id": "cand_0004", "missing_id": "mev_0001"}


def _moved(status: str, **override: object) -> Finding:
    """v11 응답을 노드가 `Finding`으로 옮긴 결과 — 실경로(`_finding_from_procedure`)를 탄다."""
    procedure = AuditProcedureNullableCwe.model_validate(_payload(status, **override))
    return auditor._finding_from_procedure(
        procedure, ids=_IDS, run_id="run_0001", produced_by=_STAMPED
    )


def test_a_v11_no_signal_answer_is_stored_with_a_null_cwe() -> None:
    """2026-10-08 실서빙 run의 `cand_0004` 꼴(`no_signal` · `evidence_refs` 비움)이 v11이면 CWE 없이
    저장된다 — 하네스가 지어 채우지 않는다. 저장 형태(`model_dump(mode="json")`)로 왕복한다."""
    finding = _moved("no_signal", cwe=None)

    assert finding.cwe is None
    stored = finding.model_dump(mode="json")
    assert stored["cwe"] is None
    assert Finding.model_validate(stored) == finding


@pytest.mark.parametrize("status", [None, *(s for s in _STATUSES if s != "no_signal")])
def test_a_finding_refuses_a_null_cwe_unless_no_signal(status: str | None) -> None:
    """`Finding`도 같은 조건을 다시 본다 — v1~v9 Finding(`trigger_status`가 `None`일 수 있다)과
    `no_signal` 밖의 상태는 null `cwe`를 못 싣는다."""
    stored = _moved("no_signal", cwe=None).model_dump(mode="json")

    with pytest.raises(ValidationError, match="no_signal일 때에만 null"):
        Finding.model_validate({**stored, "trigger_status": status})


def test_an_old_stored_finding_with_a_cwe_string_still_loads() -> None:
    """옛 행 — v10 이하가 `no_signal`에도 CWE 문자열을 들고 저장한 꼴 — 이 그대로 읽힌다. 반대
    방향(`no_signal`이면 null이어야 한다)을 걸면 이 행이 죽는다."""
    stored = _moved("no_signal").model_dump(mode="json")
    stored["produced_by"]["prompt_version"] = "v10"

    assert stored["cwe"] == "CWE-787"
    assert Finding.model_validate(stored).cwe == "CWE-787"


# === 4. 읽는 자리 =============================================================================


def _finding(cwe: str | None) -> Finding:
    """읽는 자리에 넘길 `Finding` — 경로·상태가 있는 꼴. `cwe`가 `None`이면 `no_signal`이다."""
    return Finding(
        finding_id="find_0007",
        run_id="run_0001",
        candidate_id="cand_0001",
        state=FindingState.HYPOTHESIZED,
        path=[
            PathStep(step="source", symbol="recv_request", note="len"),
            PathStep(step="propagate", symbol="parse_header"),
            PathStep(step="sink", symbol="memcpy"),
        ],
        trigger_status="no_signal" if cwe is None else "triggerable",
        confidence=0.6,
        rca_draft="r",
        cwe=cwe,
        title="parse_header에서 길이 검증 없는 memcpy",
        evidence_refs=["ev_0001"],
        produced_by=_STAMPED,
    )


def test_sarif_omits_rule_id_for_a_null_cwe_and_keeps_the_old_result_otherwise() -> None:
    """SARIF 2.1.0에서 `result.ruleId`는 선택 속성이다 — null이면 키를 뺀다(`"None"`을 규칙 ID로
    내지 않는다). CWE가 있으면 종전 dict와 **키 순서까지** 같다."""
    with_cwe = _finding("CWE-787")
    old = {
        "ruleId": with_cwe.cwe,
        "level": "warning",
        "message": {"text": f"{with_cwe.title} (state={with_cwe.state.value})"},
        "properties": {
            "findingId": with_cwe.finding_id,
            "confidence": with_cwe.confidence,
            "evidenceRefs": list(with_cwe.evidence_refs),
        },
    }
    assert json.dumps(_sarif_result(with_cwe)) == json.dumps(old)

    result = _sarif_result(_finding(None))
    assert "ruleId" not in result
    assert "None" not in json.dumps(result)


def test_report_dedupes_mixed_null_and_real_cwes_without_raising() -> None:
    """`None`과 문자열이 섞인 키로 묶음이 돈다. 같은 파일·대상 함수의 `no_signal` 둘은 한 건이고,
    같은 자리의 CWE 있는 Finding과는 안 묶인다."""
    a = _finding(None)
    b = _finding(None).model_copy(update={"finding_id": "find_0008"})
    c = _finding("CWE-787").model_copy(update={"finding_id": "find_0009"})
    files = {f.finding_id: "parser.c" for f in (a, b, c)}

    kept, duplicates = report.dedupe([c, b, a], files=files)

    assert duplicates == 1
    assert {f.finding_id for f in kept} == {"find_0007", "find_0009"}


def test_report_entry_says_no_cwe_instead_of_none() -> None:
    line = report._entry(_finding(None), "E1")

    assert "(CWE 없음, confidence 0.60)" in line
    assert "None" not in line
    assert "(CWE-787, confidence 0.60)" in report._entry(_finding("CWE-787"), "E1")


def test_critic_claim_says_no_cwe_and_keeps_the_old_bytes_otherwise() -> None:
    with_cwe = _finding("CWE-787")

    assert critic._describe_claim(with_cwe) == f"{with_cwe.title} ({with_cwe.cwe})"
    assert critic._describe_claim(_finding(None)) == f"{with_cwe.title} (CWE 없음)"


@pytest.mark.parametrize("wide", [True, False], ids=["wide", "v2-narrow"])
def test_pov_finding_line_says_none_marker_and_keeps_the_old_bytes_otherwise(wide: bool) -> None:
    """PoV 서술의 첫 줄 — CWE가 있으면 종전 f-string과 같은 바이트, null이면 `CWE: (없음)`."""
    with_cwe = _finding("CWE-787")
    old_head = f"{with_cwe.finding_id} · CWE: {with_cwe.cwe} · {with_cwe.title}"

    assert pov._describe_finding(with_cwe, wide=wide).startswith(old_head + "\n")
    text = pov._describe_finding(_finding(None), wide=wide)
    assert text.startswith(f"{with_cwe.finding_id} · CWE: (없음) · {with_cwe.title}\n")
    assert "None" not in text
