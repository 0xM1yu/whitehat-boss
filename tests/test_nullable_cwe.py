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

⚠ **이 sha는 pydantic의 직렬화에 달려 있다.** `requirements.txt`·`pyproject.toml`은 `pydantic>=2.7`
부동이고 CI는 Python 3.11이다(`.github/workflows/ci.yml`). 같은 sha를 확인한 조합은 둘이다 —
pydantic 2.12.4 · Python 3.11.0(2026-10-10 이 저장소 로컬)과 pydantic 2.13.4 · Python 3.14.5(적대적
리뷰 환경). 다른 마이너에서 같은 값이 나오는지는 미측정이다 — pydantic이 스키마 생성(키 순서·
`title`)을 바꾸면 요청 바이트가 실제로 바뀌므로 여기서 빨개지는 것이 맞고, 그때는 v1~v11 전부의
요청이 바뀐 것이다(이 PR의 회귀가 아니다). 전체 스키마 sha 핀은 이 파일이 처음이다(앞 검사들은
`["pattern"]` 같은 속성 단위였다).
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
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
    # 이 줄은 통째로 바뀌었다 — 조각이 아니라 줄 전체다.
    'For no_signal, output `"audit": null` and keep the other fields; `cwe` stays a `CWE-<number>` '
    "string, because the harness requires a non-null one for every status (step 5).",
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
        # ⏵ 적대적 리뷰 H1·H4 — 예전 판은 `was in old and now in new`만 봐서 **줄의 나머지**가
        # 무핀이었다(201번 줄 1,174자의 `repro_spec` 약속 · 282번 줄 845자의 `origin` 문장).
        # 그 줄 끝에 v11의 목적과 반대되는 문장(「no_signal에도 CWE 문자열을 써라」)을 덧붙여도
        # 초록이었다. 이제 **v10 줄에서 그 조각만 바꾼 것이 v11 줄 전체와 같다** — v10은 sha로
        # 동결돼 있으므로(위 1절) v11의 네 줄이 바이트로 정해진다. 덧붙이기·지우기·뒤집기가 다
        # 죽는다.
        assert old.count(was) == 1, was
        assert old.replace(was, now) == new, now
    assert load_prompt(Role.AUDITOR, "v11").body == load_prompt(Role.AUDITOR, "v10").body
    assert "v11" in auditor._CONTENT_AWARE_VERSIONS


def test_no_v11_line_keeps_the_v10_spellings_that_said_cwe_is_never_null() -> None:
    """반대쪽 — v10이 적던 「non-null for every status」 **철자** 다섯이 v11에 하나도 안 남았다(v10
    실물에서 골랐다: 그 넷 + 계약 예시의 문자열 전용 값).

    ⚠ **철자 핀이다, 성질 핀이 아니다**(CLAUDE.md §2.5). 같은 뜻을 다른 낱말로 덧붙이면 여기서는
    못 잡는다 — 그 성질(「v11은 v10에서 네 조각만 바꿨다」)은 위
    `test_v11_prompt_differs_from_v10_only_in_the_four_cwe_lines`가 줄 전체 등식으로 문다.
    ⏵ 2026-10-10 이름을 바꿨다(전에는 `..._still_says_cwe_is_never_null_for_no_signal` — 성질을
    주장하는 이름이었다, 적대적 리뷰 H4)."""
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
#
# ⏵ 2026-10-10 (적대적 리뷰 M2) — 픽스처가 `cwe=None`이면 `no_signal`, 아니면 `triggerable`로 묶여
# 있어서 리더의 분기를 `cwe is None` → `trigger_status == "no_signal"`로 바꾼 변이 넷(SARIF·
# critic·pov·report)이 전부 살았다. 그 둘이 갈리는 모집단 — **`no_signal` + CWE 문자열**(v10 이하의
# 옛 행, 그리고 v11이 산문을 어긴 답) — 을 셋째 픽스처로 넣는다. 분기는 `cwe`를 봐야 한다.


def _finding(cwe: str | None, status: str | None = None) -> Finding:
    """읽는 자리에 넘길 `Finding` — 경로·상태가 있는 꼴. `status`를 안 주면 `cwe`가 `None`일 때
    `no_signal`, 아니면 `triggerable`이다."""
    if status is None:
        status = "no_signal" if cwe is None else "triggerable"
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
        trigger_status=status,
        confidence=0.6,
        rca_draft="r",
        cwe=cwe,
        title="parse_header에서 길이 검증 없는 memcpy",
        evidence_refs=["ev_0001"],
        produced_by=_STAMPED,
    )


def _no_signal_with_cwe() -> Finding:
    """`no_signal` + CWE 문자열 — 위 M2의 모집단."""
    return _finding("CWE-787", status="no_signal")


def test_sarif_gives_a_null_cwe_a_non_cwe_rule_id_and_keeps_the_old_result_otherwise() -> None:
    """`ruleId`는 JSON 스키마상 선택이지만 **규범 본문은 조건부 SHALL이다** — OASIS SARIF 2.1.0
    §3.27.5 *"If `rule.id` is absent, `ruleId` SHALL be present"*. `_sarif_result`는 `rule`을 안
    싣으므로 **모든** result가 `ruleId`를 든다(빼면 그 SHALL을 어긴다 — 이 파일의 첫 판이 그랬다,
    적대적 리뷰 M1). null `cwe`의 `ruleId`는 CWE 꼴이 아닌 센티넬이다 — `"None"`도 지어낸 CWE도
    아니다. CWE가 있으면 종전 dict와 **키 순서까지** 같다."""
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
    assert "rule" not in result, "전제 — `rule.id`가 없으니 `ruleId`가 SHALL이다"
    assert result["ruleId"] == "no-cwe"
    assert not re.fullmatch(r"CWE-\d+", result["ruleId"])
    assert "None" not in json.dumps(result)
    assert list(result) == list(old), "키 순서도 같다 — `ruleId` 자리만 값이 다르다"

    # M2 — 분기는 `cwe`를 본다. `no_signal`이어도 CWE 문자열이면 그 값이 `ruleId`다.
    assert _sarif_result(_no_signal_with_cwe())["ruleId"] == "CWE-787"


def test_report_entry_says_no_cwe_instead_of_none() -> None:
    line = report._entry(_finding(None), "E1")

    assert "(CWE 없음, confidence 0.60)" in line
    assert "None" not in line
    assert "(CWE-787, confidence 0.60)" in report._entry(_finding("CWE-787"), "E1")
    assert "(CWE-787, confidence 0.60)" in report._entry(_no_signal_with_cwe(), "E1"), "M2"


def test_critic_claim_says_no_cwe_and_keeps_the_old_bytes_otherwise() -> None:
    with_cwe = _finding("CWE-787")

    assert critic._describe_claim(with_cwe) == f"{with_cwe.title} ({with_cwe.cwe})"
    assert critic._describe_claim(_finding(None)) == f"{with_cwe.title} (CWE 없음)"
    assert critic._describe_claim(_no_signal_with_cwe()) == f"{with_cwe.title} (CWE-787)", "M2"


@pytest.mark.parametrize("wide", [True, False], ids=["wide", "v2-narrow"])
def test_pov_finding_line_says_none_marker_and_keeps_the_old_bytes_otherwise(wide: bool) -> None:
    """PoV 서술의 첫 줄 — CWE가 있으면 종전 f-string과 같은 바이트, null이면 `CWE: (없음)`."""
    with_cwe = _finding("CWE-787")
    old_head = f"{with_cwe.finding_id} · CWE: {with_cwe.cwe} · {with_cwe.title}"

    assert pov._describe_finding(with_cwe, wide=wide).startswith(old_head + "\n")
    assert pov._describe_finding(_no_signal_with_cwe(), wide=wide).startswith(old_head + "\n")
    text = pov._describe_finding(_finding(None), wide=wide)
    assert text.startswith(f"{with_cwe.finding_id} · CWE: (없음) · {with_cwe.title}\n")
    assert "None" not in text


def test_probe_reconstructs_the_critic_claim_with_the_node_function() -> None:
    """`tools/probe/critic_output_budget.py`의 `_reconstruct`가 Critic `CLAIM`을 노드와 같은
    함수(`critic._describe_claim`)로 되만든다 — 자기 f-string으로 되돌리면 null `cwe`에서
    `(None)`이 찍힌다(적대적 리뷰 L3: 그 변이가 살았다). 프로브는 엔드포인트를 띄워야 돌므로
    `_reconstruct`를 직접 부르지 않고 **그 함수가 부르는 이름**과 그 이름의 정체를 본다.
    ⚠ 철자 핀이다 — 같은 조립을 다른 이름으로 감싸면 못 잡는다."""
    import importlib.util  # noqa: PLC0415
    import sys  # noqa: PLC0415

    path = PROMPTS_DIR.parent / "tools" / "probe" / "critic_output_budget.py"
    spec = importlib.util.spec_from_file_location("_critic_output_budget_probe", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        assert module._describe_claim is critic._describe_claim
        source = inspect.getsource(module._reconstruct)
    finally:
        sys.modules.pop(spec.name, None)
    assert "claim=_describe_claim(finding)" in source
    assert "finding.cwe" not in source, "CWE를 자기 손으로 찍지 않는다"
    assert critic._describe_claim(_finding(None)).endswith("(CWE 없음)")


# --- 보고서 중복 제거 — 실제 `no_signal` 모양(`path=[]`) ----------------------------------------


def _real_no_signal(candidate_id: str, finding_id: str) -> Finding:
    """노드가 v11 `no_signal` 답을 옮긴 **실제 모양** — `audit`가 null이라 `path=[]`다
    (`_finding_from_procedure`). 위 `_finding`은 PROPAGATE 경로를 들어 이 모양을 안 탄다."""
    procedure = AuditProcedureNullableCwe.model_validate(_payload("no_signal", cwe=None))
    ids = {**_IDS, "candidate_id": candidate_id, "finding_id": finding_id}
    return auditor._finding_from_procedure(
        procedure, ids=ids, run_id="run_0001", produced_by=_STAMPED
    )


def test_report_does_not_merge_distinct_candidates_whose_null_cwe_findings_share_a_title() -> None:
    """적대적 리뷰 H5 — `no_signal`은 `path=[]`라 `_target_symbol`이 **모델이 쓴 제목**으로
    떨어진다. 키가 `(None, 파일, 제목)`이면 서로 다른 후보의 부정 결론이 제목만 같아도 한 건으로
    합쳐졌다. null `cwe`의 키는 후보 ID를 든다 — 다른 후보는 안 묶이고 같은 후보 둘만 묶인다.
    CWE 있는 Finding과도 안 묶인다."""
    a = _real_no_signal("cand_0001", "find_0001")
    b = _real_no_signal("cand_0002", "find_0002")
    a_again = _real_no_signal("cand_0001", "find_0003")
    assert a.path == [] and a.title == b.title, "전제 — 실제 모양이고 제목이 같다"
    with_cwe = _finding("CWE-787").model_copy(update={"finding_id": "find_0009"})
    files = {f.finding_id: "parser.c" for f in (a, b, a_again, with_cwe)}

    kept, duplicates = report.dedupe([a, b, a_again, with_cwe], files=files)

    assert duplicates == 1
    assert {f.finding_id for f in kept} == {"find_0001", "find_0002", "find_0009"}


def test_report_keeps_the_old_key_for_a_finding_with_a_cwe() -> None:
    """CWE가 든 Finding의 키는 종전 그대로 `(CWE, 파일, 대상 함수)`다 — `path=[]`이면 제목."""
    old_row = _moved("no_signal")  # v10 이하의 꼴 — no_signal + CWE 문자열, path=[]
    assert old_row.path == []
    assert report._dedupe_key(old_row, "parser.c") == ("CWE-787", "parser.c", old_row.title)
    assert report._dedupe_key(_finding("CWE-787"), None) == ("CWE-787", "", "parse_header")


# --- 관측 — v11 `no_signal`이 CWE를 냈는가 -------------------------------------------------------


def test_the_answer_hop_counts_a_v11_no_signal_that_still_carries_a_cwe() -> None:
    """적대적 리뷰 H2 — v11 산문은 `no_signal`에 null을 시키지만 스키마는 CWE 문자열을 받는다.
    그 위반은 `answer` 홉 관측의 `cwe_present_on_no_signal`로 센다. `no_signal`은 `audit is None`
    이라 그 조기 반환 **뒤**에 두면 영영 안 생긴다 — 이 검사가 그 자리를 문다.
    v8~v10의 답(`Finding` · `AuditProcedure`)은 계약이 non-null이라 같은 모양이어도 키가 없다."""
    violating = AuditProcedureNullableCwe.model_validate(_payload("no_signal"))
    assert auditor._answer_observed(violating, cited=0) == {
        "cited_probe_evidence": 0,
        "trigger_status": "no_signal",
        "cwe_present_on_no_signal": True,
    }

    obeying = AuditProcedureNullableCwe.model_validate(_payload("no_signal", cwe=None))
    assert "cwe_present_on_no_signal" not in auditor._answer_observed(obeying, cited=0)
    triggerable = AuditProcedureNullableCwe.model_validate(_payload("triggerable"))
    assert "cwe_present_on_no_signal" not in auditor._answer_observed(triggerable, cited=0)

    v10 = AuditProcedure.model_validate(_payload("no_signal"))
    assert "cwe_present_on_no_signal" not in auditor._answer_observed(v10, cited=0)
    v9 = _moved("no_signal")  # `Finding` 답 — v8·v9 봉투의 최종 답 꼴
    assert "cwe_present_on_no_signal" not in auditor._answer_observed(v9, cited=0)


# --- v11 prefix 글자 수를 베낀 자리 -------------------------------------------------------------

# v11 prefix의 글자 수(`len(load_prompt(Role.AUDITOR, "v11").prefix)` — 2026-10-09 실측).
# 🔴 v10의 56,906은 `tests/test_auditor_prompt_v10.py`의 `_PREFIX_COPY_SITES`가 문다 — 그 핀의
# 이유가 「이 수는 한 세션에서 세 번 낡았다」다. v11 수도 세 자리에 베껴 적었으므로 같은 장치를
# 둔다(적대적 리뷰 M4: 그 셋이 무핀이었다).
_V11_PREFIX_CHARS = 56_872
_V11_PREFIX_COPY_SITES = (
    "config/budgets.yaml",
    "src/config.py",
    "src/orchestrator/nodes/auditor.py",
)


def test_every_place_that_copies_the_v11_prefix_length_carries_the_measured_number() -> None:
    """v11 prefix 길이가 상수와 같고, 그것을 베낀 세 자리가 **같은 꼴**(`56,872`)로 든다.
    `config/budgets.yaml`은 v10 대비 차(`34자 짧다`)도 든다 — 길이만 고치고 차를 안 고치는 것이
    v10에서 실제로 났던 일이다."""
    prefix_chars = len(load_prompt(Role.AUDITOR, "v11").prefix)
    v10_chars = len(load_prompt(Role.AUDITOR, "v10").prefix)
    repo = PROMPTS_DIR.parent
    texts = {name: (repo / name).read_text(encoding="utf-8") for name in _V11_PREFIX_COPY_SITES}

    assert prefix_chars == _V11_PREFIX_CHARS, (
        "v11 산문의 길이가 바뀌었다 — 아래 세 자리와 v10 대비 차를 다시 재서 고친다"
    )
    assert [name for name, text in texts.items() if f"{prefix_chars:,}" not in text] == []
    assert f"v10보다 {v10_chars - prefix_chars}자 짧다" in texts["config/budgets.yaml"]
