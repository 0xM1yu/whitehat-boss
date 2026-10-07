"""Auditor v10의 출력 계약 — `AuditProcedure` · `AuditBlock` · 형제 봉투 `AuditorProcedureAction`.

원천은 `prompts/auditor/v10/prefix.md`의 「Exact output contract」 절이다. **값 집합과 필드 순서는
그 산문을 직접 읽어서 스키마와 대조한다** — 옮겨 적은 값끼리만 맞추면 산문이 바뀌어도 초록이다
(CLAUDE.md §2.1). 산문을 못 읽는 상태(검출기가 아무것도 안 잡는 상태)는 읽은 값을 손으로 적은
기대값과도 비교해 막는다.

`AuditorAction`의 요청 바이트가 안 바뀌는지는 이 파일이 **속성 순서와 `$defs`**로 문다 — 그 스키마가
v8·v9 루프의 `response_format`으로 그대로 나가기 때문이다(evaluation §2.3).
"""

import copy

import pytest
from pydantic import ValidationError

from src.orchestrator.prompt_loader import PROMPTS_DIR
from src.schemas import (
    AuditBlock,
    AuditDefense,
    AuditorAction,
    AuditOrigin,
    AuditorProcedureAction,
    AuditPoint,
    AuditProcedure,
    AuditRefutation,
    Finding,
)

PREFIX = PROMPTS_DIR / "auditor" / "v10" / "prefix.md"

# 계약이 적은 닫힌 값 집합 일곱. 출처는 모두 `prompts/auditor/v10/prefix.md`의
# 「Exact output contract」 절이다 — 계약 JSON과 그 아래 「Category keys:」 줄.
# ⚠ **줄 번호로 적지 않는다** — 산문이 움직이면 어긋난다. 절 이름과 키로 적고,
# 대조는 `test_the_closed_value_sets_equal_the_v10_prose`가 **내용으로** 한다.
#   category = 「Category keys:」 줄 · 나머지 여섯(trigger_status · origin.kind ·
#   defense.status · defense.gap(`or null`은 집합 밖의 null) · refutation.result ·
#   path[].step) = 계약 JSON의 해당 줄.
# (표시 이름, 값이 사는 `$defs` 정의 — 루트면 None, 필드, 유효 payload 안의 위치, 값 집합)
CLOSED_SETS = [
    (
        "category",
        "AuditBlock",
        "category",
        ("audit", "category"),
        {
            "spatial_bounds",
            "lifetime_null",
            "initialization",
            "resource",
            "integer",
            "type",
            "control_flow",
            "input_validation",
            "format_string",
            "injection",
            "path_traversal",
            "info_exposure",
            "concurrency",
            "authorization",
            "crypto_config",
        },
    ),
    (
        "trigger_status",
        None,
        "trigger_status",
        ("trigger_status",),
        {"triggerable", "guarded", "no_reach", "reachability_unknown", "no_signal"},
    ),
    (
        "origin.kind",
        "AuditOrigin",
        "kind",
        ("audit", "origin", "kind"),
        {"input", "param", "local", "const"},
    ),
    (
        "defense.status",
        "AuditDefense",
        "status",
        ("audit", "defense", "status"),
        {"absent", "fails", "covers"},
    ),
    (
        "defense.gap",
        "AuditDefense",
        "gap",
        ("audit", "defense", "gap"),
        {"width", "operands", "both_bounds", "off_by_one", "order", "other"},
    ),
    (
        "refutation.result",
        "AuditRefutation",
        "result",
        ("audit", "refutation", "result"),
        {"defeated", "holds", "path_not_executable", "decisive_code_missing"},
    ),
    (
        "path[].step",
        "PathStep",
        "step",
        ("audit", "path", 0, "step"),
        {"source", "propagate", "sink"},
    ),
]

OUTER_FIELDS = (
    "audit",
    "trigger_status",
    "confidence",
    "cwe",
    "title",
    "rca_draft",
    "evidence_refs",
    "repro_spec",
)


# --- 픽스처 payload ---------------------------------------------------------


def _block() -> dict:
    """`triggerable`에 맞는 `audit` 블록.

    모양은 계약 예시(`prefix.md`의 §Exact output contract)를 따른다.
    """
    return {
        "category": "spatial_bounds",
        "point": {"symbol": "parse_header", "expression": "memcpy(buf, src, len)"},
        "origin": {"kind": "input", "fact": "len은 헤더에서 읽은 값이다"},
        "path": [
            {"step": "source", "symbol": "read_header", "note": "len을 헤더에서 읽는다"},
            {"step": "propagate", "symbol": "parse_header", "note": "len이 그대로 넘어간다"},
            {"step": "sink", "symbol": "memcpy", "note": "buf는 64바이트다"},
        ],
        "defense": {
            "expression": "none on visible path",
            "status": "absent",
            "gap": None,
            "fact": "len을 거르는 검사가 없다",
        },
        "refutation": {"result": "defeated", "witness": "len=65", "missing_information": []},
    }


def _procedure(**override: object) -> dict:
    """`AuditProcedure`의 최소 유효 payload — 여덟 필드 전부 든다(전부 required다)."""
    base = {
        "audit": _block(),
        "trigger_status": "triggerable",
        "confidence": 0.8,
        "cwe": "CWE-787",
        "title": "out-of-bounds write",
        "rca_draft": "len에 상한이 없어 memcpy 길이로 그대로 간다",
        "evidence_refs": [],
        "repro_spec": None,
    }
    return {**base, **override}


def _set(payload: dict, path: tuple, value: object) -> dict:
    """`payload` 복사본의 `path` 자리를 `value`로 바꾼다."""
    out = copy.deepcopy(payload)
    node = out
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return out


def _request() -> dict:
    return {"kind": "macro", "target": "BUF_SIZE", "purpose": "버퍼 크기", "request_code": "L1"}


def _spec() -> dict:
    return {"segments": [{"kind": "repeat", "byte": 65, "count": 8}], "rationale": "64바이트 패딩"}


def _finding_payload(**extra: object) -> dict:
    """`Finding`의 최소 유효 payload — `tests/test_auditor_loop.py`의 같은 이름 함수와 같은 값."""
    return {
        "finding_id": "finding_0001",
        "run_id": "run_20260101_0001",
        "candidate_id": "candidate_0001",
        "state": "hypothesized",
        "path": [{"step": "sink", "symbol": "memcpy", "note": "길이가 상수가 아니다"}],
        "confidence": 0.5,
        "cwe": "CWE-787",
        "title": "out-of-bounds write",
        "evidence_refs": [],
        "produced_by": {"role": "auditor", "model": "fake", "prompt_version": "v7"},
        **extra,
    }


# --- 산문 읽기 ---------------------------------------------------------------


def _contract_text() -> str:
    """`## Exact output contract` 절 본문."""
    return PREFIX.read_text(encoding="utf-8").split("## Exact output contract", 1)[1]


def _keys_at_indent(indent: int) -> tuple[str, ...]:
    """계약 JSON 예시에서 들여쓰기 `indent`칸에 `"key": ...`로 시작하는 줄들의 키(순서 그대로)."""
    marker = " " * indent + '"'
    return tuple(
        line.split('"')[1] for line in _contract_text().splitlines() if line.startswith(marker)
    )


def _alternatives(field: str) -> set[str]:
    """계약 예시의 `"field": "a|b|c"` 줄이 든 대안 집합. 꼬리의 ` or null`은 뗀다."""
    marker = f'"{field}": "'
    for line in _contract_text().splitlines():
        if marker in line:
            raw = line.split(marker, 1)[1].split('"', 1)[0]
            return set(raw.removesuffix(" or null").split("|"))
    raise AssertionError(f"계약 예시에 `{marker}` 줄이 없다")


def _category_keys() -> set[str]:
    """`Category keys:` 줄의 백틱 안 값들."""
    line = next(ln for ln in _contract_text().splitlines() if ln.startswith("Category keys:"))
    return set(line.split("`")[1::2])


# --- 스키마 읽기 -------------------------------------------------------------


def _enum_of(schema: dict, def_name: str | None, field: str) -> set[str]:
    """`field`의 enum 값 집합. `$ref`를 따라가고 `| None`이면 `anyOf` 가지에서 찾는다."""
    root = schema if def_name is None else schema["$defs"][def_name]
    node = root["properties"][field]
    for candidate in (node, *node.get("anyOf", [])):
        if "$ref" in candidate:
            candidate = schema["$defs"][candidate["$ref"].rsplit("/", 1)[1]]
        if "enum" in candidate:
            return set(candidate["enum"])
    raise AssertionError(f"{def_name}.{field}에 enum이 없다 — 닫히지 않았다: {node}")


# --- 필드 집합 · 순서 ---------------------------------------------------------


def test_the_procedure_has_the_eight_outer_fields_of_the_contract_in_order() -> None:
    """`AuditProcedure`의 필수 필드 집합과 property 순서가 계약 예시의 바깥 키와 같다.

    출처: `prompts/auditor/v10/prefix.md` §Exact output contract의
    「one JSON object with these fields in this order」와 그 절 계약 JSON의 바깥 키 여덟.
    **순서가 계약이다** — 그 순서가 `response_format`의 property
    순서가 되어 `audit`(범주·근거)를 `trigger_status`·`confidence`·`rca_draft`보다 먼저 내게 한다.
    산문에서 읽은 값을 손으로 적은 여덟과도 비교한다 — 읽기가 깨져 `()`를 읽으면 아래 대조가
    공허해진다.
    """
    prose = _keys_at_indent(6)
    schema = AuditProcedure.model_json_schema()

    assert prose == OUTER_FIELDS, "계약 예시에서 바깥 키를 못 읽었다"
    assert set(schema["required"]) == set(OUTER_FIELDS), "`Every field shown is required`가 깨졌다"
    assert tuple(schema["properties"]) == OUTER_FIELDS


def test_the_audit_block_and_its_parts_follow_the_contract_order_and_are_all_required() -> None:
    """`AuditBlock`과 그 하위 넷의 property 순서가 계약 예시와 같고 **전부 required**다.

    출처: `prefix.md` §Exact output contract의 계약 JSON(블록 여섯 키는 들여쓰기 8칸,
    하위 필드는 10칸)과 같은 절 「Every
    field shown in `procedure` is required」. nullable인 `defense.gap`도 required다 — 기본값이
    있으면 모델이 생략하고 그 생략이 `null`과 구분되지 않는다. ⚠ `path[]`가 재사용하는
    `PathStep.note`는 optional이고 **산문이 그 사실을 적는다**(「the schema does not require
    `path[].note`; fill it anyway」) — 스키마의 사실과 산문의 요구가 갈려 있으므로 여기서는
    단언하지 않는다.
    """
    block_keys = ("category", "point", "origin", "path", "defense", "refutation")
    parts = (AuditPoint, AuditOrigin, AuditDefense, AuditRefutation)
    nested = tuple(name for cls in parts for name in cls.model_json_schema()["properties"])

    assert _keys_at_indent(8) == block_keys, "계약 예시에서 블록 키를 못 읽었다"
    assert tuple(AuditBlock.model_json_schema()["properties"]) == block_keys
    assert _keys_at_indent(10) == nested, "하위 필드의 순서가 계약 예시와 다르다"
    for cls in (AuditBlock, *parts):
        schema = cls.model_json_schema()
        assert set(schema["required"]) == set(schema["properties"]), cls.__name__


@pytest.mark.parametrize("missing", ["audit", "repro_spec"])
def test_a_nullable_outer_field_is_still_required(missing: str) -> None:
    """`audit`·`repro_spec`은 `| None`이어도 **생략할 수 없다** — 계약이 「Every field shown is
    required」(`prefix.md` §Exact output contract)다. 기본값을 주면 모델이 생략해도
    통과하고 `null`과 구분이
    안 된다."""
    payload = _procedure()
    del payload[missing]

    with pytest.raises(ValidationError):
        AuditProcedure(**payload)


def test_the_audit_path_reuses_pathstep_and_may_be_empty() -> None:
    """`audit.path`는 기존 `PathStep`을 재사용하고 **`minItems`가 없다**.

    출처: `prefix.md` §Audit procedure 「4. Keep fields consistent」의
    「`audit.path` may be `[]` for reachability_unknown and is non-empty
    for triggerable/guarded/no_reach」. 그 규칙은 형제 필드 `trigger_status`에 달려 블록 혼자는
    못 가른다 — 어느 쪽으로 고정해도 합법 출력 하나를 거절하므로 산문 몫으로 남긴다
    (`Finding.path`의 `min_items` 주석과 같은 논법).
    """
    path = AuditProcedure.model_json_schema()["$defs"]["AuditBlock"]["properties"]["path"]
    empty = _procedure(
        audit={**_block(), "path": []}, trigger_status="reachability_unknown", confidence=0.5
    )

    assert path["items"]["$ref"].endswith("/PathStep")
    assert "minItems" not in path
    assert AuditProcedure(**empty).audit.path == []


# --- 닫힌 값 집합 -------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "def_name", "field", "where", "expected"),
    CLOSED_SETS,
    ids=[row[0] for row in CLOSED_SETS],
)
def test_each_closed_value_set_is_closed_in_the_schema(
    name: str, def_name: str | None, field: str, where: tuple, expected: set[str]
) -> None:
    """계약의 닫힌 값 집합 일곱이 **JSON 스키마의 enum**으로 닫혀 있고 집합 밖 값은 거절된다.

    스키마가 열려 있으면 grammar가 그 자리를 마스크하지 못해 모델이 집합 밖 문자열을 내고, 하네스는
    그것을 파싱 실패로 받아 재시도를 쓴다. 집합은 **등치**로 건다 — 부분집합만 보면 값 하나를
    지워도 초록이다. 출처는 위 `CLOSED_SETS` 주석의 줄이다.
    """
    schema = AuditProcedure.model_json_schema()

    assert _enum_of(schema, def_name, field) == expected, name
    with pytest.raises(ValidationError):
        AuditProcedure(**_set(_procedure(), where, "not_in_the_set"))


def test_the_closed_value_sets_equal_the_v10_prose() -> None:
    """위 `CLOSED_SETS`가 **산문이 지금 적은 값과 같다** — 손으로 옮긴 표가 낡지 않는다.

    산문에서 읽은 집합을 스키마가 아니라 **표**와 비교한다(표 ↔ 스키마는 위 검사가 문다). 둘이
    어긋나면 산문과 스키마 중 어느 쪽이 맞는지는 이 검사가 가르지 못하고, 사람이 `prefix.md`를
    연다.
    """
    prose = {
        "category": _category_keys(),
        "trigger_status": _alternatives("trigger_status"),
        "origin.kind": _alternatives("kind"),
        "defense.status": _alternatives("status"),
        "defense.gap": _alternatives("gap"),
        "refutation.result": _alternatives("result"),
        "path[].step": {"source", "propagate", "sink"},
    }

    for name, _, _, _, expected in CLOSED_SETS:
        assert prose[name] == expected, name
    assert "one of source, propagate, or sink" in _contract_text(), "path[].step 문장이 바뀌었다"
    assert len(prose["category"]) == 15


def test_defense_gap_is_nullable() -> None:
    """`defense.gap`은 값 여섯 **또는 null**이다.

    출처: `prefix.md` §Exact output contract의 `... or null`.
    """
    schema = AuditProcedure.model_json_schema()
    branches = schema["$defs"]["AuditDefense"]["properties"]["gap"]["anyOf"]

    assert {"type": "null"} in branches
    width = AuditProcedure(**_set(_procedure(), ("audit", "defense", "gap"), "width"))
    assert AuditProcedure(**_procedure()).audit.defense.gap is None
    assert width.audit.defense.gap == "width"


# --- cwe ----------------------------------------------------------------------


def test_cwe_enforces_the_pattern_and_refuses_null_even_for_no_signal() -> None:
    """`cwe`는 `^CWE-\\d+$`를 강제하고 **null을 거절한다 — no_signal에서도**.

    출처: `prefix.md` §Exact output contract의 `"cwe": "CWE-<number>"`와
    같은 절 「`cwe` always matches `^CWE-\\d+$`;
    it is never null, whatever the trigger_status」. `Finding.cwe`가 nullable이 아니고 그 값을
    읽는 자리가 여섯이라(SARIF `ruleId` 포함) 이 스키마는 넓히지 않는다 — 산문이 한 번 null을
    적었던 자리라서 no_signal까지 일부러 못 박는다.
    """
    schema = AuditProcedure.model_json_schema()
    no_signal = {"audit": None, "trigger_status": "no_signal", "confidence": 0.05}

    assert schema["properties"]["cwe"]["pattern"] == r"^CWE-\d+$"
    assert "cwe" in schema["required"]
    assert AuditProcedure(**_procedure(cwe="CWE-190")).cwe == "CWE-190"
    for bad in (None, "CWE-", "cwe-787", "787", "CWE-7a"):
        with pytest.raises(ValidationError):
            AuditProcedure(**_procedure(cwe=bad))
    with pytest.raises(ValidationError):
        AuditProcedure(**_procedure(cwe=None, **no_signal))


# --- audit null <-> no_signal ----------------------------------------------------


@pytest.mark.parametrize("status", ["triggerable", "guarded", "no_reach", "reachability_unknown"])
def test_a_null_audit_with_any_status_but_no_signal_is_refused(status: str) -> None:
    """`audit`가 null인데 no_signal이 아니면 거절한다 — 동치의 한쪽 방향.

    출처: `prefix.md` §Audit procedure 「4. Keep fields consistent」의
    「`audit` is null exactly when no_signal」. 거절 사유가 **검증기**임을
    에러 종류로 가른다(`value_error` 하나뿐) — Literal 위반 같은 다른 사유로 우연히 빨개진 것이
    아니다.
    """
    with pytest.raises(ValidationError) as caught:
        AuditProcedure(**_procedure(audit=None, trigger_status=status))

    assert [e["type"] for e in caught.value.errors()] == ["value_error"]


def test_no_signal_with_an_audit_block_is_refused() -> None:
    """no_signal인데 `audit` 블록이 있으면 거절한다 — 동치의 다른 쪽 방향(위 검사와 한 쌍)."""
    with pytest.raises(ValidationError) as caught:
        AuditProcedure(**_procedure(trigger_status="no_signal"))

    assert [e["type"] for e in caught.value.errors()] == ["value_error"]


def test_the_consistent_pairs_are_accepted() -> None:
    """양성 쌍 — no_signal ↔ null, 나머지 넷 ↔ 블록 있음. 위 거절 둘이 **전부 거절**하는
    검증기가 아님을 보인다(그런 검증기는 위 두 검사를 통과한다)."""
    quiet = AuditProcedure(**_procedure(audit=None, trigger_status="no_signal", confidence=0.05))

    assert quiet.audit is None
    for status in ("triggerable", "guarded", "no_reach", "reachability_unknown"):
        assert AuditProcedure(**_procedure(trigger_status=status)).audit is not None


# --- 봉투 ----------------------------------------------------------------------


def test_auditor_action_keeps_the_property_order_its_request_bytes_depend_on() -> None:
    """`AuditorAction`의 property 순서가 `action·requests·tool·spec·finding·rationale` **그대로**다.

    이 클래스의 JSON 스키마가 v8·v9 루프의 `response_format`으로 그대로 나간다 — 베이스 클래스로
    공통 필드를 빼는 리팩터가 순서를 바꾸면 프롬프트가 안 바뀐 판들의 요청 바이트가 바뀐다
    (evaluation §2.3). pydantic은 베이스 필드를 **먼저** 놓으므로 베이스가 `rationale`을 선언하면
    `finding`이 그 뒤로 밀린다 — 이 검사가 그 사고를 문다. 기대 순서의 출처는 리팩터 이전
    `src/schemas/auditor.py`의 선언 순서(`action`→`requests`→`tool`→`spec`→`finding`→`rationale`)다.
    형제 봉투는 `finding` 자리에 `procedure`가 온다.
    """
    action = AuditorAction.model_json_schema()
    sibling = AuditorProcedureAction.model_json_schema()

    assert list(action["properties"]) == [
        "action",
        "requests",
        "tool",
        "spec",
        "finding",
        "rationale",
    ]
    assert action["required"] == ["action", "rationale"]
    assert list(sibling["properties"]) == [
        "action",
        "requests",
        "tool",
        "spec",
        "procedure",
        "rationale",
    ]
    assert sibling["required"] == ["action", "rationale"]


def test_finding_stores_audit_but_does_not_request_it() -> None:
    """`Finding.audit`는 저장·검증·직렬화에는 서지만 **요청 JSON 스키마에는 없다**.

    스키마에 두면 `Finding`을 요청하는 한 방 판과, `$defs`에 `Finding`을 싣는 `AuditorAction`
    (v8·v9)의 요청 바이트가 프롬프트 변경 없이 바뀐다 — `AuditorAction`을 안 고치려고 봉투를 따로
    둔 이유와 같은 불변식이다. 이 필드를 요청에 싣기로 하면 이 검사를 먼저 뒤집고 그 판들을
    재측정한다. 자리는 `path` 바로 뒤, `trigger_status` 앞이다(범주·근거가 판정보다 먼저 —
    `Finding` 주석).
    """
    fields = list(Finding.model_fields)
    defs = AuditorAction.model_json_schema()["$defs"]

    assert fields.index("path") < fields.index("audit") < fields.index("trigger_status")
    assert "audit" not in Finding.model_json_schema()["properties"]
    assert "audit" not in defs["Finding"]["properties"]
    assert not [name for name in defs if name.startswith("Audit")]


_OWN_FIELDS = {"lookup": {"requests"}, "probe": {"tool", "spec"}, "answer": {"procedure"}}
_FOREIGN_CELLS = [
    (action, field)
    for action, own in _OWN_FIELDS.items()
    for field in ("requests", "tool", "spec", "procedure")
    if field not in own
]
_VALUES = {
    "requests": [_request()],
    "tool": "semgrep",
    "spec": _spec(),
    "procedure": _procedure(),
}


def _action(action: str, **extra: object) -> AuditorProcedureAction:
    """`action`이 **자기 필드만으로** 서는 최소 payload에 `extra`를 얹어 만든다."""
    own = {name: _VALUES[name] for name in _OWN_FIELDS[action] if name != "spec"}
    return AuditorProcedureAction(action=action, rationale="검사", **{**own, **extra})


@pytest.mark.parametrize(("action", "foreign"), _FOREIGN_CELLS)
def test_the_procedure_envelope_rejects_every_other_actions_field(
    action: str, foreign: str
) -> None:
    """액션이 쓰지 않는 필드가 실리면 어느 칸이든 거절한다 — 3 액션 × 4 필드 중 8칸.

    `AuditorAction._check_action`과 **같은 규율**이다(`tests/test_auditor_loop.py`의
    `test_an_action_rejects_every_other_actions_field`가 그쪽을 8칸으로 문다). `extra="forbid"`는
    없는 이름만 막고, 선언된 필드를 엉뚱한 액션에 채운 것은 이 검증기만 잡는다.
    """
    with pytest.raises(ValidationError):
        _action(action, **{foreign: _VALUES[foreign]})


@pytest.mark.parametrize("action", ["lookup", "probe", "answer"])
def test_the_procedure_envelope_accepts_each_actions_own_field(action: str) -> None:
    """세 액션이 제 필드만으로 선다 — 위 거절 검사의 양성 쌍. `probe`의 `spec`은 선택이다."""
    assert _action(action).action == action
    assert _action("probe", spec=_VALUES["spec"]).spec is not None


def test_an_answer_needs_a_procedure_and_the_other_actions_need_theirs() -> None:
    """`answer`는 `procedure` 없이, `lookup`은 빈 `requests`로, `probe`는 `tool` 없이 거절된다.

    `AuditorAction`의 `test_answer_needs_a_finding` 등과 같은 규율이다 — 끝났다면서 결과가 없는 홉,
    resolver에 넘길 것이 없는 홉은 아예 안 나게 봉투에서 막는다.
    """
    with pytest.raises(ValidationError):
        AuditorProcedureAction(action="answer", rationale="끝")
    with pytest.raises(ValidationError):
        AuditorProcedureAction(action="lookup", requests=[], rationale="더 본다")
    with pytest.raises(ValidationError):
        AuditorProcedureAction(action="probe", rationale="돌린다")


def test_each_envelope_carries_only_its_own_answer_field() -> None:
    """`AuditorAction`은 `procedure`를, 형제 봉투는 `finding`을 **받지 않는다**(`extra="forbid"`).

    두 봉투가 `answer`의 payload 필드 하나만 달리 든다는 구분이다 — 베이스가 둘을 합쳐 선언하면
    한 봉투로 다른 봉투의 답이 새어 들어간다.
    """
    with pytest.raises(ValidationError):
        AuditorAction(action="answer", procedure=_procedure(), rationale="x")
    with pytest.raises(ValidationError):
        AuditorProcedureAction(action="answer", finding=_finding_payload(), rationale="x")


def test_the_procedure_envelope_tells_the_model_the_truth_about_spec() -> None:
    """형제 봉투의 설명도 `probe`의 `spec`을 입력 바이트 공급 수단으로 서술하지 않는다.

    지금 도구는 `spec`을 입력으로 안 받는다 — 설명이 그렇게 말하지 않으면 모델이 「내가 구성한
    입력으로 돌렸더니」라는 거짓 문장을 궤적에 쓴다. 클래스 설명과 `spec` 필드 설명이 둘 다
    「받지 않는다」를 말해야 한다. `tests/test_auditor_loop.py`의
    `test_the_request_schema_tells_the_model_the_truth_about_spec`이 `AuditorAction`에 대해 같은
    것을 문다.
    """
    schema = AuditorProcedureAction.model_json_schema()

    assert "받지 않는다" in schema["description"]
    assert "받지 않" in schema["properties"]["spec"]["description"]
    assert "조립 명세" not in schema["description"]


def test_the_new_classes_add_no_repo_internal_reference_to_the_request() -> None:
    """새 클래스의 설명이 요청 payload에 저장소 내부 참조를 **새로** 싣지 않는다.

    내부 참조는 SHA·이슈 번호·파일 경로다. `tests/test_auditor_loop.py`의 같은 검출기를 쓴다 —
    그쪽이 `AuditorAction`에 거는 집합 포함(「이 봉투의 누출 ⊆ 이미 나가는 누출」)을 형제 봉투에
    건다. 재사용한 정의들(`PathStepKind`·`ReproSpec`·`ReproOrigin`·`RequestedContext`)의 누출은
    한 방 `Finding`과 v8·v9 봉투가 이미 보내는 것이라 이 변경이
    만든 것이 아니다. 검출기는 늦게 import한다 — 그쪽 이름이 바뀌어도 이 파일의 나머지 검사는 산다.
    """
    from src.schemas import PovAction, ScoutResponse
    from tests.test_auditor_loop import _leaking_definitions

    own = _leaking_definitions(AuditorProcedureAction.model_json_schema(), "AuditorProcedureAction")
    already_sent = (
        _leaking_definitions(Finding.model_json_schema(), "Finding")
        | _leaking_definitions(ScoutResponse.model_json_schema(), "ScoutResponse")
        | _leaking_definitions(PovAction.model_json_schema(), "PovAction")
    )
    mine = tuple(
        f"{cls.__name__}:"
        for cls in (
            AuditorProcedureAction,
            AuditProcedure,
            AuditBlock,
            AuditPoint,
            AuditOrigin,
            AuditDefense,
            AuditRefutation,
        )
    )

    assert not {key for key in own if key.startswith(mine)}, sorted(own)
    assert own <= already_sent, f"새 누출: {sorted(own - already_sent)}"


# --- 회귀 ------------------------------------------------------------------------


def test_a_finding_without_audit_is_built_exactly_as_before() -> None:
    """`audit`는 optional이라 **기존 `Finding` 생성이 그대로 선다**(회귀 pin).

    v1~v9가 내는 Finding에는 `audit`가 없다 — 기본값 `None`이 아니면 그 판들이 전부 파싱 실패한다.
    """
    finding = Finding(**_finding_payload())

    assert finding.audit is None
    assert finding.trigger_status is None
    assert "audit" not in Finding.model_json_schema()["required"]


def test_an_audit_block_survives_validation_transition_and_a_json_round_trip() -> None:
    """`Finding`이 든 `AuditBlock`이 `transition()`(dump→재검증)과 JSON 왕복에서 안 사라진다.

    `audit`를 요청 스키마에서만 뺀 것이지 필드에서 뺀 것이 아님을 보인다 — 하네스가 v10 응답의
    `audit`를 옮겨 실은 Finding이 Critic·Gate·보고서까지 그 블록을 들고 가야 한다.
    """
    finding = Finding(**_finding_payload(audit=_block(), trigger_status="triggerable"))

    assert isinstance(finding.audit, AuditBlock)
    assert finding.transition(confidence=0.4).audit == finding.audit
    assert Finding.model_validate_json(finding.model_dump_json()).audit == finding.audit


# v8·v9 루프 요청에 **그대로 실리는** 바이트의 길이. 🔴 **이 PR의 헤드라인 주장이 그것이다** —
# `AuditorAction`을 고치지 않았고 `Finding.audit`을 `SkipJsonSchema`로 숨겨서 v1~v9의 요청
# payload가 안 바뀐다. 적대적 검토 다섯이 각자 `origin/main`을 풀어 재서 확인했지만 **그 확인은
# 스냅샷이고 상시 핀이 아니었다** — 설명 한 글자를 바꾸는 변이 다섯이 전량 초록으로 살아남았다.
_AUDITOR_ACTION_DESC_CHARS = 483
_AUDITOR_ACTION_SPEC_DESC_CHARS = 49
_FINDING_DESC_CHARS = 731


def test_the_v8_v9_request_payload_keeps_the_descriptions_it_already_sent() -> None:
    """v8·v9 요청에 실리는 **설명 문자열**이 안 바뀐다.

    🔴 **왜 길이로 묶는가.** 이 자리에서 지켜야 하는 것은 「무슨 뜻인가」가 아니라 **「바이트가
    같은가」**다 — 모델 요청 payload가 달라지면 그 판으로 돈 이전 run과 비교할 수 없다
    (`docs/evaluation.md` §2.3). 철자를 통째로 적어 두면 그 자체가 또 하나의 복제본이 되고,
    `model_json_schema()`를 골든 해시로 묶으면 pydantic 판올림에 깨진다(하한이 2.7이다).
    길이는 그 둘 사이다 — **한 글자만 바뀌어도 빨개지고** 라이브러리 내부에는 안 묶인다.

    ⚠ **길이가 같은 치환은 못 잡는다.** 철자를 단언하지 성질을 단언하지 않는 핀이고, 같은
    길이로 뜻을 뒤집는 편집은 지나간다. 그런 편집을 막는 것은 이 검사가 아니라 리뷰다.

    묶는 셋: `AuditorAction` 자신의 설명(클래스 docstring) · `spec` 필드 설명(도구가 그 값을
    안 받는다는 **모델에게 하는 정직한 고지**) · `$defs`로 실리는 `Finding`의 설명.
    """
    action = AuditorAction.model_json_schema()
    finding = Finding.model_json_schema()

    assert len(action["description"]) == _AUDITOR_ACTION_DESC_CHARS
    assert len(action["properties"]["spec"]["description"]) == _AUDITOR_ACTION_SPEC_DESC_CHARS
    assert len(finding["description"]) == _FINDING_DESC_CHARS
    assert [k for k, v in finding["properties"].items() if v.get("description")] == [], (
        "`Finding`의 필드에 설명이 생기면 v1~v9의 요청 바이트가 늘어난다"
    )
    assert action["$defs"]["Finding"] == {
        key: value for key, value in finding.items() if key != "$defs"
    }, "봉투가 품는 `Finding` 정의가 한 방 판이 보내는 것과 달라졌다"

