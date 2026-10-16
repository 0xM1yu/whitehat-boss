"""Auditor 도구 루프의 계약 — 봉투(`AuditorAction` · `AuditorProcedureAction`)와 홉 루프.

`tests/test_pov_loop.py`의 Auditor 판이다. 그쪽이 PoV 봉투에 대해 보는 것(액션별 필드 ·
`tool` Literal ⊆ `TOOL_KINDS` · docstring이 요청 payload에 실린다)을 같은 규율로 본다.

**이 파일이 `tests/test_nodes.py`·`tests/test_config.py`를 건드리지 않는 이유**: Auditor
프롬프트 판과 그 meta는 다른 작업 레인이고, 그 레인이 같은 두 파일을 고친다. 허용집합
단언도 여기 둔다.
"""

import hashlib
import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import get_args

import pytest
import yaml
from pydantic import ValidationError

from src import bench_report, cli
from src.adapters import fake_model, symbol_resolver
from src.adapters.factory import build_deps
from src.adapters.fake_retriever import read_symbol_source
from src.adapters.fake_tools import TOOL_KINDS
from src.adapters.symbol_render import render_followup
from src.bench import ROLE_SPEC
from src.config import CONFIG_DIR, load_config
from src.interfaces.model_client import (
    ModelGatewayError,
    ModelResult,
    SchemaParseError,
    Usage,
)
from src.interfaces.tool_runner import INPUT_PLACEHOLDER
from src.orchestrator import gate
from src.orchestrator.budget import BudgetExhausted, BudgetLedger
from src.orchestrator.context import RunContext
from src.orchestrator.nodes import auditor, index, preprocess, retrieval, scout, signals
from src.orchestrator.poc_assembly import assemble_poc
from src.orchestrator.policy import FixedThresholdPolicy
from src.orchestrator.prompt_loader import (
    PROMPT_TOOL_NAMES,
    PROMPTS_DIR,
    PromptError,
    PromptTemplate,
    load_prompt,
)
from src.orchestrator.runner import RunSpec, _auditor_no_finding_warning, _new_record
from src.orchestrator.trace import EventKind, TraceWriter
from src.schemas import CapsuleLevel, FindingState, Location, ProducedBy, Role
from src.schemas import auditor as auditor_schemas
from src.schemas.auditor import (
    AUDITOR_TOOLS,
    AuditorAction,
    AuditorProcedureAction,
    AuditorProcedureAuditAlwaysAction,
    AuditorProcedureNullableCweAction,
    AuditorProcedureProbeInputAction,
    AuditorProcedureProbeInputDiscriminatedAction,
    AuditProcedure,
    AuditProcedureAuditAlways,
    AuditProcedureNullableCwe,
)
from src.schemas.enums import AuditorStopReason, EvidenceKind, UnfilledReason
from src.schemas.finding import (
    AuditBlock,
    AuditRefutation,
    AuditRefutationWithNoSignal,
    Finding,
)
from src.schemas.need_evidence import Resolution, RetrievedBlock, Unfilled
from src.schemas.pov import PovAction, PovSegment, PovSpec
from src.schemas.scout_response import RequestedContext, ScoutResponse
from tests.test_pipeline_e2e import (
    _context_overflow,
    _failing_model,
    _per_stage,
    _run,
    _with_index,
)


def _finding_payload(evidence_refs: tuple[str, ...] = ()) -> dict:
    """`answer` 액션이 싣는 Finding의 최소 유효 payload."""
    return {
        "finding_id": "finding_0001",
        "run_id": "run_20260101_0001",
        "candidate_id": "candidate_0001",
        "state": "hypothesized",
        "path": [{"step": "sink", "symbol": "memcpy", "note": "길이가 상수가 아니다"}],
        "confidence": 0.5,
        "cwe": "CWE-787",
        "title": "out-of-bounds write",
        "evidence_refs": list(evidence_refs),
        "produced_by": {"role": "auditor", "model": "fake", "prompt_version": LOOP_VERSION},
    }


def _lookup_request() -> dict:
    """`lookup` 액션이 싣는 요청 하나 — `RequestedContext`와 같은 네 칸."""
    return {
        "kind": "macro",
        "target": "BUF_SIZE",
        "purpose": "버퍼 크기를 알아야 오버플로를 판정할 수 있다",
        "request_code": "L1",
    }


# --- 액션별 필드 ------------------------------------------------------------


def test_probe_needs_a_tool() -> None:
    """`probe`인데 `tool`이 없으면 거절한다 — 하네스가 무엇을 돌릴지 알 수 없다."""
    with pytest.raises(ValidationError):
        AuditorAction(action="probe", rationale="정적 신호를 먼저 본다")


def test_answer_needs_a_finding() -> None:
    """`answer`인데 `finding`이 없으면 거절한다 — 끝났다면서 결과가 없다."""
    with pytest.raises(ValidationError):
        AuditorAction(action="answer", rationale="끝")


def test_lookup_needs_at_least_one_request() -> None:
    """`lookup`인데 요청이 비면 거절한다 — resolver에 넘길 것이 없다.

    Scout NE에서 **빈 요청**이 정지 사유 2번이다. 봉투에서 막으면 그 홉이 아예 안 난다.
    """
    with pytest.raises(ValidationError):
        AuditorAction(action="lookup", requests=[], rationale="더 봐야 한다")


# 액션 → 그 액션이 쓰는 필드. 아래 매트릭스가 **이 표에 없는** (액션, 필드) 칸을 전부 거절로 건다.
_OWN_FIELDS = {"lookup": {"requests"}, "probe": {"tool", "spec"}, "answer": {"finding"}}
_FOREIGN_CELLS = [
    (action, field)
    for action, own in _OWN_FIELDS.items()
    for field in ("requests", "tool", "spec", "finding")
    if field not in own
]


def _action_with(action: str, **extra: object) -> AuditorAction:
    """`action`이 **자기 필드만으로** 서는 최소 payload에 `extra`를 얹어 만든다."""
    base = {
        "lookup": {"requests": [_lookup_request()]},
        "probe": {"tool": "semgrep"},
        "answer": {"finding": _finding_payload()},
    }[action]
    return AuditorAction(action=action, rationale="검사", **{**base, **extra})


@pytest.mark.parametrize(("action", "foreign"), _FOREIGN_CELLS)
def test_an_action_rejects_every_other_actions_field(action: str, foreign: str) -> None:
    """액션이 쓰지 않는 필드가 실리면 **어느 칸이든** 거절한다 — 3 액션 × 4 필드 중 8칸이다.

    `extra="forbid"`는 **없는 이름**만 막는다 — 선언된 필드를 엉뚱한 액션에 채운 것은
    못 잡는다. `PovSegment._check_kind`·`PovAction._check_action`과 같은 규율이다. 처음 판은
    (`probe`, `finding`) 한 칸만 걸어서, 검증기의 허용표(`_FIELDS_BY_ACTION`)를 한 액션에서 넓혀도
    초록이었다."""
    foreign_value = {
        "requests": [_lookup_request()],
        "tool": "semgrep",
        "spec": _spec(),
        "finding": _finding_payload(),
    }[foreign]

    with pytest.raises(ValidationError):
        _action_with(action, **{foreign: foreign_value})


@pytest.mark.parametrize("action", ["lookup", "probe", "answer"])
def test_each_action_accepts_its_own_field(action: str) -> None:
    """세 액션이 제 필드만으로 선다 — 위 거절 검사의 양성 쌍이다. `probe`의 `spec`은 선택이다."""
    assert _action_with(action).action == action
    assert _action_with("probe", spec=_spec()).spec is not None


# --- 화이트리스트 · 요청 payload --------------------------------------------


def test_tool_literal_is_the_whitelist_minus_index() -> None:
    """`AuditorAction.tool` Literal은 ToolRunner 화이트리스트에서 **`index`만 뺀** 집합이다.

    밖의 이름을 쓰면 `tool_policy` 위반 Evidence로 떨어지고 모델은 그것을 「도구가 없다」로 읽는다 —
    스키마에서 막는 것이 그 자리다. **부분집합만 보면 모자란다**: 도구를 Literal에서 지워도
    (`poc` 삭제) 부분집합이라 초록이다. 그래서 등치로 건다 — Literal(스키마를 뒤져 읽은 값) ==
    `AUDITOR_TOOLS`(별칭에서 읽은 값) == `TOOL_KINDS` − `index` == 이 파일이 meta로 광고하는
    여섯."""
    tool_values = _literal_values(AuditorAction.model_json_schema(), "tool")

    assert tool_values, "tool 필드의 Literal 값을 스키마에서 못 찾았다"
    assert tool_values == set(AUDITOR_TOOLS) == set(TOOL_KINDS) - {"index"} == set(LOOP_TOOLS)


def test_every_advertisable_tool_is_a_prompt_tool_name() -> None:
    """Auditor가 받는 여섯은 로더가 아는 이름(`PROMPT_TOOL_NAMES`)의 부분집합이다 — 로더가 모르는
    이름이면 meta로 광고할 길이 없다. 반대(`index`)는 로더가 통과시키고 `loop_problem`이 막는다."""
    assert set(AUDITOR_TOOLS) <= set(PROMPT_TOOL_NAMES)
    assert set(PROMPT_TOOL_NAMES) - set(AUDITOR_TOOLS) == {"index"}


# 저장소 **내부** 참조의 형(形) — 이슈·PR 번호(`#419`) · D 번호 · ADR · 커밋 SHA 모양 · 소스/문서
# 경로. 문자열 여섯을 막던 처음 판은 철자를 막았다(새 SHA·이슈 번호는 초록으로 통과했다) — 이것은
# **형**을 막는다. 경계는 **ASCII 기준**이다 — `\b`는 한글도 단어 문자로 봐서
# 「D99와」·「auditor.py가」를 놓친다.
_ASCII_WORD_BEFORE = r"(?<![A-Za-z0-9_])"
_ASCII_WORD_AFTER = r"(?![A-Za-z0-9_])"
_INTERNAL_REF = re.compile(
    "|".join(
        [
            r"#\d{2,}",
            rf"{_ASCII_WORD_BEFORE}D\d+{_ASCII_WORD_AFTER}",
            rf"{_ASCII_WORD_BEFORE}ADR(?![A-Za-z])",
            rf"{_ASCII_WORD_BEFORE}[0-9a-f]{{7,40}}{_ASCII_WORD_AFTER}",
            rf"\.(?:py|md|yaml){_ASCII_WORD_AFTER}",
            rf"{_ASCII_WORD_BEFORE}(?:src|docs|prompts|tests|config)/",
        ]
    )
)


def _leaking_definitions(schema: dict, owner: str) -> set[str]:
    """JSON 스키마에서 내부 참조를 든 `description`의 위치 집합. 키는 `<정의>:<그 안의 경로>`다.

    루트는 `owner`(클래스 이름)이고 `$defs`의 각 정의는 자기 이름이 소유자다 — 같은 정의가 다른
    스키마(한 방 `Finding`·Scout·PoV)의 `$defs`로도 실리므로 이름으로 맞춘다."""
    found: set[str] = set()

    def walk(node: object, name: str, path: str) -> None:
        if isinstance(node, dict):
            description = node.get("description")
            if isinstance(description, str) and _INTERNAL_REF.search(description):
                found.add(f"{name}:{path}")
            for key, value in node.items():
                if key == "$defs":
                    for def_name, definition in value.items():
                        walk(definition, def_name, "")
                else:
                    walk(value, name, f"{path}/{key}")
        elif isinstance(node, list):
            for position, value in enumerate(node):
                walk(value, name, f"{path}[{position}]")

    walk(schema, owner, "")
    return found


def test_the_internal_reference_detector_catches_the_repo_shapes_and_spares_prose() -> None:
    """검출기 자체의 핀 — 양성은 저장소의 실제 형태, 음성은 **나올 수 있는** 설명 문장이다.

    양성 출처: 한 방 `Finding` 설명의 `#102`·`prompts/`(`src/schemas/finding.py`), `ReproSpec`·
    `ProducedBy`의 `architecture.md §4` 꼴 문서 경로, `RequestedContext`의 `ADR 0012`. 음성은 이
    클래스가 실제로 쓰는 종류의 문장(액션·도구 이름 · 홉 번호 · 한국어 산문)이다. 검출기가 아무것도
    안 잡는 상태는 아래 본 검사가 영영 초록이라 이 핀 없이는 안 보인다."""
    for leak in (
        "한 방 Finding(#102) — prompts/ 아래 판이 쓴다",
        "architecture.md §4의 형식",
        "ADR 0012 결정 2",
        "D99와 같은 자리",
        "커밋 1fa2182 이후",
        "nodes/auditor.py가 센다",
    ):
        assert _INTERNAL_REF.search(leak) is not None, leak
    for prose in (
        "도구는 입력 바이트를 받지 않는다 — `spec`을 실어도 관측이 달라지지 않는다.",
        "홉 상한에 닿으면 마지막 홉은 `answer`여야 한다 (hop 3, 2회).",
        "Output an `answer` action with your final Finding.",
    ):
        assert _INTERNAL_REF.search(prose) is None, prose


def test_the_request_schema_adds_no_repo_internal_reference() -> None:
    """`AuditorAction`의 설명은 `response_format.json_schema.schema`로 **v8·v9 Auditor 루프 요청에
    실린다**(`openai_compat`) — v10은 형제 봉투 `AuditorProcedureAction`을 보내고, 그쪽은
    `tests/test_auditor_procedure_schema.py`가 이 파일의 `_leaking_definitions`로 **같은 명제**를
    건다(그 검사가 이미 있어 여기서 파라미터화하지 않는다). 커밋 SHA·PR·이슈 번호·소스 파일 경로
    같은 내부 참조가 이 봉투 때문에 **새로** 모델 입력에 들어가면 안 된다 — 그런 이력은 클래스
    **밖** `#` 주석에 둔다.

    명제는 「이 클래스(와 필드)의 설명에 내부 참조가 없다」이고, 하나 더 — 「한 방
    `Finding`·Scout·PoV가 **이미 보내는** 정의의 누출은 이 변경이 만든 것이 아니다」. `$defs`의
    `Finding`·`ReproSpec`· `RequestedContext` 등은 그 경로들이 먼저 보내는 설명이라, 고치면 그
    역할들의 요청 바이트가 바뀐다(evaluation §2.3 재측정) — 그 정리는 해당 스키마를 쥔 쪽의 몫이다.
    그래서 **집합 포함**으로 건다: 이 봉투의 누출 ⊆ 이미 나가는 누출. 새 클래스 설명·새 필드 설명·새
    정의가 내부 참조를 달면 빨개진다(처음 판은 철자 여섯만 봤고 `$defs`는 안 열었다)."""
    own = _leaking_definitions(AuditorAction.model_json_schema(), "AuditorAction")
    already_sent = (
        _leaking_definitions(Finding.model_json_schema(), "Finding")
        | _leaking_definitions(ScoutResponse.model_json_schema(), "ScoutResponse")
        | _leaking_definitions(PovAction.model_json_schema(), "PovAction")
    )

    assert not {key for key in own if key.startswith("AuditorAction:")}, (
        f"이 봉투 자신의 설명이 내부 참조를 든다: {sorted(own)}"
    )
    assert own <= already_sent, f"새 누출: {sorted(own - already_sent)}"


def test_the_request_schema_tells_the_model_the_truth_about_spec() -> None:
    """모델이 읽는 설명이 `probe`의 `spec`을 입력 바이트 공급 수단으로 서술하지 않는다.

    **지금 도구는 `spec`을 입력으로 안 받는다**(노드가 sha256과 길이만 기록한다). 설명이 「입력
    바이트가 필요하면 `spec`에 명세를 싣는다」고 하면 모델이 「내가 구성한 입력으로 돌렸더니」라는
    거짓 문장을 궤적에 쓴다 — `docs/agent-contracts.md` §2.2가 막으라고 한 바로 그 문장이다. 클래스
    설명과 `spec` 필드 설명이 둘 다 그 사실을 말해야 한다.

    ⏵ **2026-10-09 (ADR 0014) — 무는 철자를 옮겼다.** 전에는 「받지 않는다」를 물었는데 컨테이너
    `poc`는 이제 입력을 **받을 수 있다**(과제 `-vul` 이미지의 `/tmp/poc`). 참인 문장은 **「이
    경로가 안 싣는다」**이고 그 선을 넘지 않는다 — 「도구가 못 받는다」로 쓰면 다시 거짓이다.
    싣게 하는 것은 이슈 #419다.

    ⏵ **2026-10-13(#419) — 노드는 이제 싣는다. 이 검사는 그대로 선다 — 동결된 판(v8·v9)의
    요청 바이트를 문다.** 그 설명은 이제 **낡았고**(모델이 `spec`을 안 보내게 만든다 — 이슈 #617)
    고침은 다음 프롬프트 판(auditor v15)이 새 설명으로 간다. 여기서 「안 싣는다」를 지우면 v8·v9
    요청 payload가 바뀐다."""
    schema = AuditorAction.model_json_schema()

    assert "안 싣는다" in schema["description"]
    assert "안 싣" in schema["properties"]["spec"]["description"]
    assert "받지 않" not in schema["properties"]["spec"]["description"], (
        "`poc`는 이제 받는다 — 「도구가 못 받는다」는 모델에게 가는 거짓 문장이다"
    )
    assert "조립 명세" not in schema["description"]


def _literal_values(schema: dict, field: str) -> set[str]:
    """`model_json_schema()`에서 `field`의 Literal 값 집합. `| None`이면 `anyOf`에 든다."""
    node = schema["properties"][field]
    if "enum" in node:
        return set(node["enum"])
    for branch in node.get("anyOf", []):
        if "enum" in branch:
            return set(branch["enum"])
    return set()


# === 홉 루프 (`nodes/auditor.py`의 `_run_hops`) ===============================
#
# 위는 봉투만 본다. 아래는 그 봉투를 받는 루프가 **돌고 · 서고 · 도구를 부르는 방식**을 본다.
# 루프는 `thresholds.auditor_tool_hops`가 양수이고 프롬프트 meta가 `output_schema`로 루프 봉투
# (`AuditorAction` · `AuditorProcedureAction`)를 선언해야 켜진다 — 이 파일은 **그 모양의 프롬프트를
# 임시 디렉터리에 만들고** 노드가 부르는 `load_prompt`를 그리로 돌린다(실제 루프 판은 프롬프트
# 레인이 쓴다). 임시 판의 이름은 번호가 아니다(`LOOP_VERSION`) — 실제 판 번호와 겹치면 어느 쪽이
# 읽혔는지 이름으로는 모른다. 오염은 `src` 모듈 속성(`auditor.load_prompt`·`cli.load_prompt`)에 건다
# — 이 파일의 복사본에 걸면 노드가 안 먹고 초록이 난다. 안 먹었으면 `PromptError`로 빨개진다(그
# 이름의 판이 저장소에 없으므로).
#
# `ctx` 픽스처와 `_triage`는 `tests/test_nodes.py`의 것을 **복제**했다 — 그 파일은 다른 레인이
# 쥐고 있어서 import도 수정도 하지 않는다.

RUN_ID = "run_20260901_0001"
NOW = datetime(2026, 9, 1, tzinfo=UTC)
TARGET = Path(__file__).resolve().parent / "fixtures" / "target"

VULNERABLE_C = """\
#include <string.h>

void parse_header(const char *src, int len) {
    char buf[64];
    memcpy(buf, src, len);
}
"""

BENIGN_C = """\
#include <string.h>

void safe_copy(char *dst, const char *src) {
    strncpy(dst, src, 63);
    dst[63] = '\\0';
}
"""

VULN_TARGET = Location(file="parser.c", symbol="parse_header", line_start=3, line_end=6)

LOOP_VERSION = "v_loop_test"
# 스키마 Literal(`AuditorAction.tool`)과 같은 여섯 — meta가 광고하는 도구다.
LOOP_TOOLS = ("semgrep", "clang-tidy", "build", "asan", "gdb", "poc")

_LOOP_PREFIX = "You are the Primary Auditor. Each hop, output one AuditorAction JSON object.\n"
# `{{CANDIDATE}}`·`{{TARGET}}`·`{{CAPSULE_SECTIONS}}`·`{{SOURCE}}`·`{{FACTS}}` — 노드의 hop0 렌더가
# 채우는 다섯이 전부 있어야 한다(`PromptTemplate.render`가 빠짐·남음을 거절한다). fake 모델은 마지막
# ```json 펜스에서 사실 블록을 읽는다.
_LOOP_BODY = (
    "## Candidate\n\n{{CANDIDATE}}\n\n## Target\n\n{{TARGET}}\n\n"
    "## Capsule sections\n\n{{CAPSULE_SECTIONS}}\n\n## Source\n\n```c\n{{SOURCE}}\n```\n\n"
    "## Facts\n\n```json\n{{FACTS}}\n```\n"
)


class RecordingModelClient:
    """실제 클라이언트에 위임하면서 프롬프트를 남긴다 (`tests/test_nodes.py`의 복제)."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.prompts: list[tuple[Role, object]] = []

    def complete(self, *, role, prompt, schema, history=()):  # noqa: ANN001, ANN201
        self.prompts.append((role, prompt))
        return self._inner.complete(role=role, prompt=prompt, schema=schema, history=history)


class _ScriptedModel:
    """홉마다 정해진 루프 액션을 낸다 — 두 봉투(`AuditorAction` · v10의 `AuditorProcedureAction`)
    중 어느 쪽이든 목록에 든 대로다. 목록이 다하면 마지막 것을 반복한다.

    fake가 못 내는 패턴(조사만 계속 · 같은 요청 두 번 · 모델이 쓴 문자열이 실린 `probe`)을
    **테스트 안에서** 주입한다 — `fake_model`의 빌더는 건드리지 않는다. `prompts`는 노드가 홉마다
    보낸 프롬프트이고 `schemas`는 요청한 구조화 출력이다. `fail_at`이 `(홉 번호, 예외)`이면 그
    호출에서 그 예외를 올린다 — 모델 호출 실패의 사유 전이를 거는 데 쓴다."""

    def __init__(
        self,
        actions: list[AuditorAction | AuditorProcedureAction],
        *,
        fail_at: tuple[int, Exception] | None = None,
    ) -> None:
        self._actions = list(actions)
        self._fail_at = fail_at
        self._next = 0
        self.prompts: list[tuple[Role, object]] = []
        self.schemas: list[type] = []

    def complete(self, *, role, prompt, schema, history=()):  # noqa: ANN001, ANN201
        self.prompts.append((role, prompt))
        self.schemas.append(schema)
        call = self._next
        self._next += 1
        if self._fail_at is not None and self._fail_at[0] == call:
            raise self._fail_at[1]
        action = self._actions[min(call, len(self._actions) - 1)]
        return ModelResult(
            value=action,
            usage=Usage(input_tokens=1, output_tokens=1, cached_tokens=0),
            model="scripted",
            raw_text=action.model_dump_json(),
        )


class _CitingModel:
    """`probe` 한 번 뒤 **그 관측이 준 `evidence_id`를 인용하는** `answer`를 낸다."""

    def __init__(self) -> None:
        self.prompts: list[tuple[Role, object]] = []

    def complete(self, *, role, prompt, schema, history=()):  # noqa: ANN001, ANN201
        self.prompts.append((role, prompt))
        if not history:
            action = _probe_action("semgrep")
        else:
            found = re.search(r"evidence_id=(\S+)", prompt.body)
            assert found is not None, "관측 본문에 evidence_id가 없다"
            action = _answer_action(refs=(found.group(1), "evidence_nobody_made_this"))
        return ModelResult(
            value=action,
            usage=Usage(input_tokens=1, output_tokens=1, cached_tokens=0),
            model="scripted",
            raw_text=action.model_dump_json(),
        )


class _SpyTools:
    """도구 러너를 감싸 `run()`이 받은 `(tool, args, sandbox)`를 남긴다. 나머지는 위임한다."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.calls: list[tuple[str, object, object]] = []

    def run(self, tool, args, sandbox):  # noqa: ANN001, ANN201
        self.calls.append((tool, args, sandbox))
        return self._inner.run(tool, args, sandbox)

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


class _StubResolver:
    """`ctx.resolve_requests` 대역 — 받은 질의를 남기고 `resolution`을 주거나 `raises`를 올린다."""

    def __init__(
        self, resolution: Resolution | None = None, *, raises: Exception | None = None
    ) -> None:
        self._resolution = resolution
        self._raises = raises
        self.queries: list = []

    def __call__(self, query):  # noqa: ANN001, ANN204
        self.queries.append(query)
        if self._raises is not None:
            raise self._raises
        return self._resolution


@pytest.fixture
def ctx(tmp_path: Path) -> RunContext:
    """dev_fake 프로필로 배선된 실행 문맥. 대상 트리는 임시 디렉터리에 만든다.

    `tests/test_nodes.py`의 같은 이름 픽스처를 복제했다(위 머리말)."""
    root = tmp_path / "target"
    root.mkdir()
    (root / "parser.c").write_text(VULNERABLE_C, encoding="utf-8")
    (root / "util.c").write_text(BENIGN_C, encoding="utf-8")

    config = load_config("dev_fake")
    trace = TraceWriter(RUN_ID, clock=lambda: NOW)
    holder: list[RunContext] = []
    deps = build_deps(
        config,
        run_id=RUN_ID,
        source_root=root,
        artifacts_root=tmp_path / "artifacts",
        on_cache=lambda key, hit: holder[0].record_cache(key, hit),
    )
    context = RunContext(
        run_id=RUN_ID,
        config=config,
        models=RecordingModelClient(deps.models),
        retriever=deps.retriever,
        tools=deps.tools,
        store=deps.store,
        ids=deps.ids,
        trace=trace,
        ledger=BudgetLedger(config.budget, trace),
        policy=FixedThresholdPolicy(config.thresholds),
        read_source=partial(read_symbol_source, root),
        monotonic_ms=lambda: 0,
    )
    holder.append(context)
    return context


def _triage(ctx: RunContext, target: Location):  # noqa: ANN202
    """0~4단계를 대상 하나에 대해 돌려 5단계 입력을 만든다 (`tests/test_nodes.py`의 복제).

    **후보가 `triaged`로 나와야 한다** — 루프가 Finding 없이 서면 `queued`로 내리는데
    (`_queue_candidate`) 이미 다른 상태면 그 전이가 조용히 건너뛰어진다."""
    artifacts = preprocess.run(ctx, source_root=Path(ctx.read_source.args[0]))
    artifact = next(a for a in artifacts if a.path == target.file)
    graph = index.run(ctx, workdir=target.file)
    found = signals.with_signal(signals.run(ctx, workdir=target.file))
    capsule = retrieval.run(ctx, target=target, level=CapsuleLevel.LIGHT)
    candidate = scout.run(ctx, artifact=artifact, capsule=capsule, evidences=(graph, *found))
    assert candidate.state is FindingState.TRIAGED, "픽스처 전제 — 5단계에 들어갈 후보여야 한다"
    return candidate, capsule, (graph, *found)


def _write_loop_prompt(
    root: Path, *, tools: tuple[str, ...] | None, output_schema: str = "AuditorAction"
) -> None:
    """`root/auditor/<LOOP_VERSION>/`에 도구 루프 판 프롬프트를 쓴다. `tools=None`이면 meta에 키를
    안 쓴다. `output_schema`는 그 판이 선언하는 봉투 이름이다(`AuditorAction` · v10의
    `AuditorProcedureAction`)."""
    directory = root / "auditor" / LOOP_VERSION
    directory.mkdir(parents=True)
    meta = [
        "role: auditor",
        f"version: {LOOP_VERSION}",
        f"output_schema: {output_schema}",
        "description: 테스트가 만든 도구 루프 판",
    ]
    if tools is not None:
        meta.append(f"tools: [{', '.join(tools)}]")
    for name, text in (
        ("prefix.md", _LOOP_PREFIX),
        ("body.md", _LOOP_BODY),
        ("meta.yaml", "\n".join(meta) + "\n"),
    ):
        (directory / name).write_text(text, encoding="utf-8", newline="\n")


def _loop_config(config, *, hops: int):  # noqa: ANN001, ANN202
    """Auditor 좌석을 루프 판(`LOOP_VERSION`)으로 돌리고 조사 홉 상한을 `hops`로 둔 config."""
    entry = config.models["fake"].transition(prompt_template_version=LOOP_VERSION)
    return config.transition(
        models={**config.models, "fake_loop": entry},
        profile=config.profile.transition(roles={**config.profile.roles, "auditor": "fake_loop"}),
        thresholds=config.thresholds.transition(auditor_tool_hops=hops),
    )


# 한 방 판으로 돌릴 **실서빙** 좌석의 모델 키. critic이 `fake`(계열 `fake`)라
# `_check_family_split`이 계열 분리를 요구하므로 `qwen` 계열을 쓴다.
_SERVED_AUDITOR_KEY = "qwen3.8-27b"
# 그 좌석이 고르는 한 방 판 — `output_schema: Finding`인 판이면 아무것이나 같다.
_ONE_SHOT_VERSION = "v7"


def _served_one_shot_config(config, *, hops: int):  # noqa: ANN001, ANN202
    """Auditor 좌석을 **실서빙**(Fake 아님) 한 방 판으로 돌리고 상한을 `hops`로 둔 config.

    `loop_problem`의 둘째 규칙(한 방 판 + 양수 상한)은 **Fake 좌석에서 면제된다** — `dev_fake`가
    루프 판을 고를 길이 없어서다(그 함수 docstring의 셋). 그러니 그 규칙이 실제로 서는 자리를
    태우려면 좌석이 Fake가 아니어야 한다. `dev_fake`로 돌려도 좌석만 갈면 게이트까지는 간다 —
    게이트가 `cli.execute` **앞**이라 엔드포인트에 닿지 않는다.
    """
    entry = config.models[_SERVED_AUDITOR_KEY].transition(
        prompt_template_version=_ONE_SHOT_VERSION
    )
    return config.transition(
        models={**config.models, _SERVED_AUDITOR_KEY: entry},
        profile=config.profile.transition(
            roles={**config.profile.roles, "auditor": _SERVED_AUDITOR_KEY}
        ),
        # v7은 `auditor_guards`를 선언하지 않는다 - 켜진 채면 SOURCE 옵션 게이트가 먼저 서서 이
        # 검사가 묻는 한 방 판 규칙에 닿지 못한다(2026-10-06 v9가 그 옵션을 켰다).
        # `auditor_write_window_marker`·`auditor_input_context`도 같다(v16부터 서술 - 2026-10-14).
        # ⏵ 2026-10-17 - `auditor_desc_first`·`auditor_omitted_functions`(v19부터 서술 - #702)도
        # 같다.
        thresholds=config.thresholds.transition(
            auditor_tool_hops=hops, auditor_guards=0, auditor_write_window_marker=0,
            auditor_input_context=0, auditor_desc_first=0, auditor_omitted_functions=0,
        ),
    )


def _patch_prompts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    tools: tuple[str, ...] | None = LOOP_TOOLS,
    output_schema: str = "AuditorAction",
) -> None:
    """노드(`auditor.load_prompt`)와 cli 게이트(`cli.load_prompt`)가 같은 임시 디렉터리를 읽게 갈아
    끼운다. `LOOP_VERSION` 밖의 버전은 원래 디렉터리로 보낸다."""
    root = tmp_path / "loop_prompts"
    _write_loop_prompt(root, tools=tools, output_schema=output_schema)

    def load(role, version):  # noqa: ANN001, ANN202
        return load_prompt(role, version, root=root if version == LOOP_VERSION else PROMPTS_DIR)

    monkeypatch.setattr(auditor, "load_prompt", load)
    monkeypatch.setattr(cli, "load_prompt", load)


def _arm(
    ctx: RunContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    hops: int,
    tools: tuple[str, ...] | None = LOOP_TOOLS,
    output_schema: str = "AuditorAction",
) -> None:
    """Auditor 좌석을 도구 루프 판으로 돌리고 조사 홉 상한을 `hops`로 둔다."""
    _patch_prompts(monkeypatch, tmp_path, tools=tools, output_schema=output_schema)
    ctx.config = _loop_config(ctx.config, hops=hops)


def _events(ctx: RunContext, kind: EventKind) -> list:
    return [e for e in ctx.trace.events if e.kind is kind]


def _hops(ctx: RunContext) -> list:
    return _events(ctx, EventKind.AUDITOR_HOP)


def _auditor_calls(ctx: RunContext) -> list:
    return [e for e in _events(ctx, EventKind.MODEL_CALL) if e.data["role"] == "auditor"]


def _lookup_action(target: str, *, kind: str = "function", rationale: str = "더 봐야 한다"):
    return AuditorAction(
        action="lookup",
        requests=[
            RequestedContext(
                kind=kind, target=target, purpose="경로를 읽는다", request_code=f"L_{target}"
            )
        ],
        rationale=rationale,
    )


def _lookup_many(names: list[str]) -> AuditorAction:
    return AuditorAction(
        action="lookup",
        requests=[
            RequestedContext(
                kind="function", target=name, purpose="경로를 읽는다", request_code=f"L_{name}"
            )
            for name in names
        ],
        rationale="여럿을 한 번에 본다",
    )


def _spec(rationale: str = "64바이트 패딩") -> PovSpec:
    return PovSpec(segments=[PovSegment(kind="repeat", byte=0x41, count=8)], rationale=rationale)


def _probe_action(tool: str, *, spec: PovSpec | None = None, rationale: str = "도구로 본다"):
    return AuditorAction(action="probe", tool=tool, spec=spec, rationale=rationale)


def _answer_action(refs: tuple[str, ...] = ()) -> AuditorAction:
    return AuditorAction(
        action="answer", finding=_finding_payload(refs), rationale="관측으로 판정한다"
    )


def _queued(ctx: RunContext, candidate) -> bool:  # noqa: ANN001
    return ctx.store.get("candidate", candidate.candidate_id).state is FindingState.QUEUED


def _queue_transitions(ctx: RunContext, candidate) -> list[tuple[str, str]]:  # noqa: ANN001
    """후보가 `queued`로 내려간 전이의 (규칙, 사유) 목록 — **몇 번, 어떤 사유로**를 본다."""
    return [
        (e.data["rule"], e.data["reason"])
        for e in _events(ctx, EventKind.TRANSITION)
        if e.subject_kind == "candidate"
        and e.subject_id == candidate.candidate_id
        and e.data["to_state"] == "queued"
    ]


def _finding_transitions(ctx: RunContext) -> list:
    return [e for e in _events(ctx, EventKind.TRANSITION) if e.subject_kind == "finding"]


# --- 스키마 Literal 말고 프롬프트 쪽 이름표도 화이트리스트와 같아야 한다 ------------------


def test_prompt_tool_names_mirror_the_tool_whitelist() -> None:
    """`PROMPT_TOOL_NAMES`(meta `tools`가 고르는 이름)는 `TOOL_KINDS` 키와 같은 집합이다.

    로더는 어댑터를 import할 수 없어(`engineering-guide.md §3`) 이름을 복제해 든다. 저쪽에만
    있는 도구는 프롬프트가 광고할 길이 없고(PoV v2 ↔ gdb), 이쪽에만 있는 이름은 로더를 통과한 뒤
    실행 시점에 `tool_policy 밖 도구`로 떨어진다. 로더 주석이 이 대조를 이 파일에 맡겼다.
    """
    assert set(PROMPT_TOOL_NAMES) == set(TOOL_KINDS)


# --- 선언 ↔ 임계값 — 노드와 cli가 같은 함수로 본다 --------------------------------


def _template(output_schema: str, tools: tuple[str, ...] = ()) -> PromptTemplate:
    return PromptTemplate(
        role=Role.AUDITOR,
        version="vx",
        prefix="p",
        body="b {{FACTS}}",
        output_schema=output_schema,
        input="source",
        tools=tools,
    )


@pytest.mark.parametrize(
    ("output_schema", "hops", "tools", "needle"),
    [
        pytest.param("Finding", 0, (), None, id="one-shot-off"),
        pytest.param("Finding", 4, (), "auditor_tool_hops가 4이다", id="one-shot-on"),
        pytest.param("Finding", 4, LOOP_TOOLS, "auditor_tool_hops가 4이다", id="one-shot-on-tools"),
        pytest.param("AuditorAction", 0, LOOP_TOOLS, "auditor_tool_hops가 0이다", id="loop-off"),
        pytest.param("AuditorAction", 0, (), "auditor_tool_hops가 0이다", id="loop-off-no-tools"),
        pytest.param("AuditorAction", 4, (), "도구를 선언하지 않는다", id="loop-no-tools"),
        pytest.param("AuditorAction", 4, ("index",), "받지 않는다", id="loop-tool-index"),
        pytest.param(
            "AuditorAction", 4, (*LOOP_TOOLS, "index"), "받지 않는다", id="loop-plus-index"
        ),
        pytest.param("AuditorAction", 4, LOOP_TOOLS, None, id="loop-on"),
        pytest.param("AuditorAction", 4, ("semgrep", "asan"), None, id="loop-on-subset"),
        # v10의 봉투(`AuditorProcedureAction`)도 같은 표로 건다 — 사유 문장이 **그 판이 선언한
        # 이름**을 말해야 한다(처음 판은 `AuditorAction`을 문장에 박아 두었다).
        pytest.param(
            "AuditorProcedureAction",
            0,
            LOOP_TOOLS,
            "AuditorProcedureAction(도구 루프)을 선언하는데 thresholds.auditor_tool_hops가 0이다",
            id="procedure-loop-off",
        ),
        pytest.param(
            "AuditorProcedureAction", 4, (), "도구를 선언하지 않는다", id="procedure-no-tools"
        ),
        pytest.param(
            "AuditorProcedureAction",
            4,
            (*LOOP_TOOLS, "index"),
            "AuditorProcedureAction.tool이 받지 않는다",
            id="procedure-plus-index",
        ),
        pytest.param("AuditorProcedureAction", 4, LOOP_TOOLS, None, id="procedure-on"),
    ],
)
def test_loop_problem_reads_the_declaration_against_the_threshold(
    output_schema: str, hops: int, tools: tuple[str, ...], needle: str | None
) -> None:
    """`loop_problem`이 막는 것 넷과 통과하는 것 둘을 **한 표**로 건다.

    노드(`_ask_model`)와 cli(run·doctor)가 이 함수 하나를 부른다 — 처음 판은 cli가 `tools`만, 노드가
    `output_schema`만 봐서 한쪽만 통과하는 설정이 셋(루프 판+상한 0 · 한 방 판+상한 양수+`tools`
    선언 · `tools: [index]`)이나 `run`·`doctor`를 지나 첫 Auditor 호출에서야 섰다."""
    problem = auditor.loop_problem(_template(output_schema, tools), hops, is_fake=False)

    if needle is None:
        assert problem is None
    else:
        assert problem is not None and needle in problem and "auditor/vx" in problem


@pytest.mark.parametrize(
    ("output_schema", "hops", "tools", "needle"),
    [
        pytest.param("Finding", 4, (), None, id="fake-one-shot-on-is-exempt"),
        pytest.param("Finding", 4, LOOP_TOOLS, None, id="fake-one-shot-on-tools-is-exempt"),
        pytest.param(
            "AuditorAction",
            0,
            LOOP_TOOLS,
            "auditor_tool_hops가 0이다",
            id="fake-loop-off-still-held",
        ),
        pytest.param(
            "AuditorAction", 4, (), "도구를 선언하지 않는다", id="fake-no-tools-still-held"
        ),
        pytest.param(
            "AuditorAction", 4, ("index",), "받지 않는다", id="fake-tool-index-still-held"
        ),
        pytest.param(
            "AuditorProcedureAction",
            0,
            LOOP_TOOLS,
            "AuditorProcedureAction(도구 루프)",
            id="fake-procedure-loop-off-still-held",
        ),
    ],
)
def test_a_fake_seat_is_exempt_from_exactly_one_of_the_four_rules(
    output_schema: str, hops: int, tools: tuple[str, ...], needle: str | None
) -> None:
    """Fake Auditor 좌석은 **둘째 규칙만** 면제된다 — 나머지 셋은 그대로 묻는다.

    왜 면제가 있나. `thresholds`는 프로필 공통이라 상한을 켜면 Fake 좌석도 같이 켜지는데
    `dev_fake`가 루프 판을 고를 길이 **없다**(그 셋은 `loop_problem` docstring이 든다: 한 모델 키에
    세 역할 · `_check_family_split` · `prompts/critic/`에 그 판 번호가 없음). 만족시킬 설정이 없는
    규칙은 막는 것이 아니라 거짓 경보다.

    **면제를 넓히면 여기서 빨개진다.** `is_fake`를 함수 머리에서 `return None`으로 올리면 뒤 세
    행이 전부 초록이 되고, 그러면 Fake로 도는 루프 검사들(이 파일의 B1~B8)이 어긋난 선언을 안고
    조용히 돈다."""
    problem = auditor.loop_problem(_template(output_schema, tools), hops, is_fake=True)

    if needle is None:
        assert problem is None
    else:
        assert problem is not None and needle in problem and "auditor/vx" in problem


def test_bench_requests_the_schema_the_auditor_prompt_declares() -> None:
    """bench가 Auditor 판의 meta 선언을 **풀어서** 요청한다 — `ROLE_SPEC`의 이름이 아니다.

    `bench_report`는 처음에 scout 자리만 버전별로 풀었다. auditor가 루프 판으로 올라가면 그대로는
    bench가 `Finding` grammar를 재서 **운영이 안 쓰는 문법의 수**를 낸다(`ROLE_SPEC`의 auditor
    스키마는 그때부터 캡슐 레벨 전용이다). 세 판을 다 건다 — 한 방 판(v7)과 봉투가 서로 다른 루프
    판 둘(v8의 `AuditorAction` · v10의 `AuditorProcedureAction`)."""
    assert auditor.resolve_output_schema(load_prompt(Role.AUDITOR, "v7").output_schema) is Finding
    assert (
        auditor.resolve_output_schema(load_prompt(Role.AUDITOR, "v8").output_schema)
        is AuditorAction
    )
    assert (
        auditor.resolve_output_schema(load_prompt(Role.AUDITOR, "v10").output_schema)
        is AuditorProcedureAction
    )
    assert (
        auditor.resolve_output_schema(load_prompt(Role.AUDITOR, "v11").output_schema)
        is AuditorProcedureNullableCweAction
    )
    assert (
        auditor.resolve_output_schema(load_prompt(Role.AUDITOR, "v12").output_schema)
        is AuditorProcedureAuditAlwaysAction
    )
    # `ROLE_SPEC`이 auditor 자리에 든 이름(캡슐 레벨 전용이 된 쪽)을 `declared`로 주고, bench가
    # 그것을 **무시하고 판 선언을 푸는지** 본다. 문자열 핀으로 걸었을 때는 분기를 `elif False:`로
    # 막아도 초록이었다(CLAUDE.md 2.5).
    declared, _ = ROLE_SPEC[Role.AUDITOR]
    assert declared is Finding, "전제 — ROLE_SPEC의 auditor는 한 방 판 이름이다"
    for version, expected in (
        ("v7", Finding),
        ("v8", AuditorAction),
        ("v10", AuditorProcedureAction),
        ("v11", AuditorProcedureNullableCweAction),
        ("v12", AuditorProcedureAuditAlwaysAction),
    ):
        resolved = bench_report.schema_for(
            Role.AUDITOR, load_prompt(Role.AUDITOR, version), declared
        )
        assert resolved is expected, (
            f"bench가 auditor/{version}의 선언을 안 푼다 — `ROLE_SPEC`의 이름을 그대로 쓰면 "
            "루프 판 run의 grammar와 bench가 재는 grammar가 갈린다"
        )
    # scout 자리는 전부터 풀었고, 그 밖 자리는 `declared`를 그대로 쓴다.
    assert bench_report.schema_for(Role.CRITIC, load_prompt(Role.CRITIC, "v1"), declared) is Finding


def test_the_loader_rejects_a_meta_tool_name_it_does_not_know(tmp_path: Path) -> None:
    """meta `tools`의 모르는 이름(오타 `semgerp`)은 로더가 거절한다 — 오타 하나가 광고 목록을 조용히
    비우는 길을 막는다. 로더 docstring이 「모르는 이름은 거절한다」고 단언하는데 그 거절을 문 검사가
    없었다(검증 줄을 지워도 초록이었다)."""
    root = tmp_path / "typo"
    _write_loop_prompt(root, tools=("semgerp",))

    with pytest.raises(PromptError, match="모르는 값"):
        load_prompt(Role.AUDITOR, LOOP_VERSION, root=root)


def test_loop_problem_rejects_an_output_schema_outside_the_allowed_set() -> None:
    """허용집합 밖 `output_schema`는 조용히 안 넘어간다 — `PromptError`다."""
    with pytest.raises(PromptError, match="이 역할이 낼 수 없다"):
        auditor.loop_problem(_template("ScoutResponse", LOOP_TOOLS), 4, is_fake=False)


# --- B1~B3: 켜진 루프가 lookup → probe → answer로 끝난다 --------------------------


def test_the_loop_runs_lookup_then_probe_then_answer_and_returns_the_finding(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """dev_fake 모델이 낸 `lookup → probe → answer` 세 홉이 도는 끝에 Finding이 나온다.

    fake는 홉을 `history` 길이로 가르므로(`_build_auditor_action`) **이 시퀀스가 서려면 루프가
    두 턴씩 history를 쌓아야 한다** — 안 쌓으면 매 홉이 hop0(`lookup`)이라 `REPEAT_REQUEST`로
    선다. 관측이 다음 홉 user 턴으로 되먹임되는지도 본다: 첫 후속 턴은 조회 결과, 둘째는
    도구 결과다.
    prefix가 이 파일이 쓴 문장 그대로인 것은 **임시 판이 실제로 읽혔다**는 표시다 — 갈아 끼움이 안
    먹었는데 같은 이름의 판이 저장소에 생겨 있으면 이름만으로는 구별이 안 된다."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    _arm(ctx, monkeypatch, tmp_path, hops=4)

    finding, _ = auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    assert isinstance(finding, Finding)
    assert finding.candidate_id == candidate.candidate_id
    assert finding.state is FindingState.HYPOTHESIZED
    assert ctx.store.get("finding", finding.finding_id) is not None
    assert [h.data["action"] for h in _hops(ctx)] == ["lookup", "probe", "answer"]
    prompts = [p for role, p in ctx.models.prompts if role is Role.AUDITOR]
    assert [p.prompt_version for p in prompts] == [LOOP_VERSION] * 3
    assert {p.prefix for p in prompts} == {_LOOP_PREFIX}
    assert prompts[1].body.startswith("## Retrieved context (hop 1)")
    assert prompts[2].body.startswith("## Tool observation (hop 2)")
    assert {c.data["schema"] for c in _auditor_calls(ctx)} == {"AuditorAction"}


def test_every_hop_emits_one_auditor_hop_event_hop0_included(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """모델 호출 한 번에 `AUDITOR_HOP`이 정확히 하나 — hop0도 센다.

    상한(`auditor_tool_hops`)의 실측 근거(홉당 usage)가 이 이벤트에서 나온다(`EventKind.AUDITOR_HOP`
    주석). hop0을 빼면 「조사 몇 번에 끝났나」가 하나씩 적게 나오고, `model_call`과 홉 이벤트를
    `(subject_id, hop)`으로 조인할 때 짝 없는 호출이 생긴다. `subject_id`는 후보다(조사 홉에는
    Finding이 아직 없다)."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    _arm(ctx, monkeypatch, tmp_path, hops=4)

    auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    hops = _hops(ctx)
    calls = _auditor_calls(ctx)
    assert [h.data["hop"] for h in hops] == [0, 1, 2]
    assert [h.data["hop"] for h in hops] == [c.data["hop"] for c in calls]
    assert {h.subject_id for h in hops} == {candidate.candidate_id}
    assert all(h.data["input_tokens"] > 0 and h.data["output_tokens"] > 0 for h in hops)


def test_a_lookup_hop_without_a_resolver_reports_no_resolved_count(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """조회 훅이 없는 run의 `lookup` 홉에는 `resolved` 키가 **없다** — 0이 아니다.

    훅이 없으면 조회를 돌리지 않은 것이다. 0을 적으면 「돌렸는데 하나도 못 찾았다」로 읽혀
    `resolved=0` 집계가 오염된다(CLAUDE.md §0 — 안 잰 값은 0으로 채우지 않는다).
    픽스처의 `resolve_requests`는 `None`이다."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    _arm(ctx, monkeypatch, tmp_path, hops=4)
    assert ctx.resolve_requests is None, "픽스처 전제 — 조회 훅이 없다"

    auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    lookup = _hops(ctx)[0]
    assert lookup.data["action"] == "lookup" and lookup.data["requested"] == 1
    unrun = {"resolved", "unfilled", "truncated", "already", "index_available"}
    assert not unrun & set(lookup.data)


def test_only_the_answering_hop_carries_a_stop_reason(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """종단 이벤트의 `stop_reason`은 `answered`이고, 조사 홉에는 그 키가 없다.

    종단 사유는 마지막 홉 이벤트의 `stop_reason`이 든다(`EventKind.AUDITOR_HOP` 주석). 조사 홉에도
    사유가 실리면 선 홉과 안 선 홉이 이벤트만으로 구별되지 않는다."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    _arm(ctx, monkeypatch, tmp_path, hops=4)

    auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    hops = _hops(ctx)
    assert hops[-1].data["stop_reason"] == AuditorStopReason.ANSWERED.value == "answered"
    assert all("stop_reason" not in h.data for h in hops[:-1])
    assert hops[-1].data["cited_probe_evidence"] == 0


# --- 조회 — resolver가 있는 run ---------------------------------------------------


def _block(name: str = "BUF_SIZE") -> RetrievedBlock:
    return RetrievedBlock(name=name, label="macro", file="parser.c", lines=(1, 2))


def _found(text: str = "#define BUF_SIZE 64") -> Resolution:
    """블록 하나를 채운 resolution — 후속 턴 텍스트는 **실제 렌더러**가 만든다."""
    followup = render_followup(
        hop=1, fragments=[text], unfilled_lines=[], include_assignment_note=False
    )
    return Resolution(
        followup_text=followup, blocks=(_block(),), unfilled=(), already=(), truncated=()
    )


def _lookup_run(ctx, monkeypatch, tmp_path, resolver: _StubResolver, actions=None):  # noqa: ANN001, ANN202
    """resolver 대역을 꽂고 `lookup → answer`를 돌린다. (후보를 돌려준다.)"""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    _arm(ctx, monkeypatch, tmp_path, hops=4)
    ctx.resolve_requests = resolver
    ctx.models = _ScriptedModel(actions or [_lookup_action("BUF_SIZE"), _answer_action()])
    auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)
    return candidate


def test_a_found_lookup_feeds_the_blocks_back_without_the_scout_tail(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """resolver가 채우면 조각이 다음 홉 user 턴으로 가고, **Scout 전용 꼬리는 잘려 있다**.

    꼬리(*"output one complete ScoutResponse JSON"*)가 Auditor 관측에 들어가면 모델이 다른 역할의
    지시를 이 좌석의 것으로 읽는다. 홉 이벤트는 SCOUT_HOP과 같은 이름으로 채움·못 채움을 싣는다."""
    resolver = _StubResolver(_found())

    _lookup_run(ctx, monkeypatch, tmp_path, resolver)

    body = ctx.models.prompts[1][1].body
    assert "#define BUF_SIZE 64" in body
    assert "lookup requested=1 resolved=1 unfilled=0 truncated=0" in body
    assert "ScoutResponse" not in body and auditor._SCOUT_FOLLOWUP_TAIL not in body
    data = _hops(ctx)[0].data
    assert (data["resolved"], data["already"], data["fixable_count"]) == (1, 0, 0)
    assert (data["unfilled"], data["truncated"]) == ("", "")
    assert data["index_available"] is True
    # resolver가 받은 질의 — 훅이 무시되거나 상한이 0으로 바뀌면 조각이 안 온다.
    (query,) = resolver.queries
    assert query.hop == 1 and query.target_file == "parser.c" and query.visible == {}
    assert query.max_chars == auditor._LOOKUP_MAX_CHARS > 0
    assert [(r.kind, r.target) for r in query.requests] == [("function", "BUF_SIZE")]


def test_the_scout_tail_sentence_still_matches_the_followup_renderer() -> None:
    """노드가 잘라 내는 문장이 어댑터가 **실제로 렌더하는** 후속 턴에 들어 있다.

    둘이 갈리면 실서빙 첫 `lookup`에서야 `PromptError`로 선다(그 앞 단계에 쓴 GPU 시간 뒤) — 노드는
    어댑터를 import할 수 없어 문장을 복제해 든다(`_SCOUT_FOLLOWUP_TAIL`)."""
    text = render_followup(
        hop=1,
        fragments=["frag"],
        unfilled_lines=["Not resolved: function `x` (not_found)"],
        include_assignment_note=False,
    )

    assert auditor._SCOUT_FOLLOWUP_TAIL in text
    shown = auditor._lookup_observation(
        1, 1, 0, Resolution(followup_text=text, blocks=(), unfilled=(), already=(), truncated=())
    )
    assert "Not resolved: function `x`" in shown and "frag" in shown
    assert "ScoutResponse" not in shown


def test_lookup_request_cap_matches_the_resolvers() -> None:
    """노드가 복제한 요청 처리 상한은 resolver의 값과 같다 — 갈리면 노드는 안 자르는데 resolver가
    조용히 빼거나, 노드가 더 적게 보낸다."""
    assert auditor._LOOKUP_REQUESTS_MAX == symbol_resolver._MAX_REQUESTS


@pytest.mark.parametrize(
    ("resolution", "unfilled", "truncated", "fixable", "needle"),
    [
        pytest.param(
            Resolution(
                followup_text=render_followup(
                    hop=1,
                    fragments=[],
                    unfilled_lines=["Not resolved: function `fn_x` (not_found)"],
                    include_assignment_note=False,
                ),
                blocks=(),
                unfilled=(
                    Unfilled(
                        kind="function",
                        target="fn_x",
                        reason=UnfilledReason.NOT_FOUND,
                        text="Not resolved: function `fn_x` (not_found)",
                    ),
                ),
                already=(),
                truncated=(),
            ),
            "not_found",
            "",
            0,
            "Not resolved",
            id="name-not-found",
        ),
        pytest.param(
            Resolution(
                followup_text="", blocks=(), unfilled=(), already=(), truncated=("BUF_SIZE",)
            ),
            "",
            "BUF_SIZE",
            0,
            "exceeded the size cap",
            id="size-cap",
        ),
        pytest.param(
            Resolution(
                followup_text="",
                blocks=(),
                unfilled=(),
                already=(),
                truncated=(),
                index_available=False,
            ),
            "",
            "",
            0,
            "index may be missing",
            id="index-missing-or-v1",
        ),
    ],
)
def test_resolved_zero_has_three_causes_and_the_hop_event_tells_them_apart(
    ctx: RunContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    resolution: Resolution,
    unfilled: str,
    truncated: str,
    fixable: int,
    needle: str,
) -> None:
    """`resolved=0`의 원인 셋 — 이름을 못 찾음 · 크기 상한 · 인덱스 없음/v1 — 을 홉 이벤트가 가른다.

    처음 판은 셋 다 `{requested: 1, resolved: 0}` 한 모양이었다 — 이름만 틀렸다는 판단과 조회가
    아예 안 돈 사실이 같은 값이었다. `SCOUT_HOP`처럼 `unfilled`(사유)·`truncated`(이름)를 싣고,
    인덱스 없음은 `index_available=False`로 가른다(#315 — 전에는 넷이 다 빈 것이 서명이었고
    `runner._fuzz_entrypoint`도 같은 키로 가른다)."""
    _lookup_run(ctx, monkeypatch, tmp_path, _StubResolver(resolution))

    data = _hops(ctx)[0].data
    assert data["resolved"] == 0
    assert (data["unfilled"], data["truncated"], data["fixable_count"]) == (
        unfilled,
        truncated,
        fixable,
    )
    # 인덱스 없음은 이제 빈 칸의 서명이 아니라 명시 값이다(#315).
    assert data["index_available"] is resolution.index_available
    assert needle in ctx.models.prompts[1][1].body


def test_a_lookup_over_the_processing_cap_says_so_and_the_repeat_key_ignores_the_rest(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """요청이 처리 상한(3)을 넘으면 앞 3개만 resolver에 가고 **넘친 수가 남는다**.

    resolver는 넘친 요청을 조용히 뺀다 — 노드가 같은 수로 자르지 않던 처음 판은 상태 줄이 「요청 5 ·
    채움 0」으로 읽혔고 홉 이벤트에는 요청 수만 남았다. 반복 판정 키도 같은 앞 3개만 본다 —
    `[A,B,C,D]` 다음 `[A,B,C,E]`는 resolver가 똑같은 조각을 또 준 것이라 반복이다."""
    resolver = _StubResolver(_found())
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    _arm(ctx, monkeypatch, tmp_path, hops=5)
    ctx.resolve_requests = resolver
    ctx.models = _ScriptedModel(
        [_lookup_many(["A", "B", "C", "D", "E"]), _lookup_many(["A", "B", "C", "F"])]
    )

    with pytest.raises(auditor.AuditorHopLimit) as stopped:
        auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    assert stopped.value.stop is AuditorStopReason.REPEAT_REQUEST
    assert [len(q.requests) for q in resolver.queries] == [3]
    first = _hops(ctx)[0].data
    assert (first["requested"], first["ignored"]) == (5, 2)
    body = ctx.models.prompts[1][1].body
    assert "requested=5" in body and "ignored=2" in body and "last 2 were not looked up" in body


def test_a_resolver_value_error_is_fed_back_not_fatal(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """resolver가 `ValueError`(`PathEscape` 같은 인덱스 쪽 문제)를 올려도 run이 안 선다.

    조회는 보조 정보다 — `runner._fuzz_entrypoint`가 같은 가드를 든다. 홉 이벤트에는
    `lookup_error`가 남고 조회 결과 키는 **없다**(안 돈 것을 0이라 적지 않는다). 모델 관측에는 예외
    **클래스 이름만** 간다 — 문구는 호스트 경로를 실을 수 있다."""
    secret = "/home/someone/secret/path"
    resolver = _StubResolver(raises=ValueError(secret))
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    _arm(ctx, monkeypatch, tmp_path, hops=4)
    ctx.resolve_requests = resolver

    finding, _ = auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    assert isinstance(finding, Finding)
    data = _hops(ctx)[0].data
    assert data["lookup_error"] == "ValueError" and "resolved" not in data
    assert "index_available" not in data  # 조회가 안 돌았다 — 안 돌린 것을 값으로 적지 않는다
    # 기본 모델은 `RecordingModelClient`라 Scout의 프롬프트도 들어 있다 — Auditor 것만 고른다.
    body = [p for role, p in ctx.models.prompts if role is Role.AUDITOR][1].body
    assert "ValueError" in body and secret not in body


# --- 실패의 사유 — 모델 호출이 실패한 사건만 C-6·C-7을 단다 --------------------------


def test_a_harness_failure_during_the_investigation_is_not_labeled_a_parse_failure(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """조사(조회) 쪽 하네스 실패는 후보에 `schema_parse_failed`를 안 단다 — 분자를 안 부풀린다.

    처음 판은 `try`가 루프 전체를 감싸 resolver의 어떤 예외든 C-6으로 적고 올렸다. 모델은 스키마를
    잘 맞췄는데 「파싱 실패」 분자가 늘고, 그 홉의 이벤트도 없어 어느 홉이 실패했는지 `model_call`과
    조인해야 알았다. 이제 후보는 `triaged`에 남고(run은 `UNFORESEEN_FAILURE`로 선다 — 하네스 버그다)
    홉 이벤트에 `observation_error`가 든다."""
    resolver = _StubResolver(raises=RuntimeError("resolver 내부 버그"))
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    _arm(ctx, monkeypatch, tmp_path, hops=4)
    ctx.resolve_requests = resolver

    with pytest.raises(RuntimeError, match="resolver 내부 버그"):
        auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    assert _queue_transitions(ctx, candidate) == []
    assert not _queued(ctx, candidate)
    assert [h.data.get("observation_error") for h in _hops(ctx)] == ["RuntimeError"]
    assert len(_auditor_calls(ctx)) == 1


def test_a_model_call_failure_in_the_loop_is_labeled_like_the_one_shot_path(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """**모델 호출**이 실패하면 후보가 `queued`로 내려가며 사유는 C-6이다 — 한 방 경로와 같다.

    홉 1의 호출이 스키마 파싱 실패로 죽는다(홉 0은 성공해 이벤트가 있다)."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    _arm(ctx, monkeypatch, tmp_path, hops=4)
    ctx.models = _ScriptedModel(
        [_lookup_action("X"), _answer_action()], fail_at=(1, SchemaParseError("스키마를 못 맞춘다"))
    )

    with pytest.raises(SchemaParseError):
        auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    assert _queue_transitions(ctx, candidate) == [("C-6", "schema_parse_failed")]
    assert [h.data["hop"] for h in _hops(ctx)] == [0]


def test_budget_exhaustion_queues_the_candidate_exactly_once(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """예산 소진은 C-7이고 **전이가 한 번만** 기록된다.

    소진은 `_call_model`과 `_ask_model`의 `except`를 둘 다 지난다 — `candidate`는 frozen이라 앞
    호출의 전이가 인자에 안 보이므로, 인자의 상태만 보면 같은 `triaged → queued`가 두 번 기록된다
    (`_queue_candidate`가 저장소의 상태로 멱등이다)."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    _arm(ctx, monkeypatch, tmp_path, hops=4)
    ctx.models = _ScriptedModel(
        [_lookup_action("X"), _answer_action()], fail_at=(1, BudgetExhausted(5, "tokens", 10, 10))
    )

    with pytest.raises(BudgetExhausted):
        auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    assert _queue_transitions(ctx, candidate) == [("C-7", "budget_exhausted")]


# --- B4: 꺼진 run ----------------------------------------------------------------


def test_a_run_with_the_loop_off_takes_the_one_shot_path_and_emits_no_hop_events(
    ctx: RunContext,
) -> None:
    """`auditor_tool_hops=0`이면 종전 한 방 경로다 — 호출 하나·`Finding` 스키마·홉 이벤트 0건.

    꺼진 run의 요청이 종전과 같아야 이전 run과 비교할 수 있다(evaluation §2.3). 홉 이벤트는
    루프가 돌 때만 나가므로 꺼진 run이 한 건이라도 내면 「돌았다」와 「안 돌았다」의 구별이
    무너진다. ⚠ **거꾸로 읽지 않는다 — 이벤트 0건이 「루프를 안 켰다」는 아니다**(켠 run도 첫
    호출이 실패하면 0건이다, `EventKind.AUDITOR_HOP` 주석). 꺼짐을 가르는 것은 `model_call`의
    `schema`(`Finding` ↔ 루프 봉투)이고, 아래 마지막 단언이 그것이다. ⏵ 2026-10-06 — 봉투가
    둘이라 켠 run의 `schema`는 v8·v9가 `AuditorAction`, v10이 `AuditorProcedureAction`이다.
    manifest의 `auditor_tool_hops`는 실제로 도는 값만 싣는다(`runner._recorded_thresholds`).

    ⏵ 2026-10-04 — **저장소 기본은 더 이상 꺼짐이 아니다**(`config/budgets.yaml`이 16을 든다,
    이슈 #419). 그래서 여기서 **명시로 끈다** — 기본값에 기대던 전제 한 줄이 그 PR에서
    거짓이 됐다(CLAUDE.md §2.3)."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    ctx.config = ctx.config.transition(
        thresholds=ctx.config.thresholds.transition(auditor_tool_hops=0)
    )

    finding, _ = auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    assert isinstance(finding, Finding)
    assert _hops(ctx) == []
    assert [(c.data["schema"], c.data["hop"]) for c in _auditor_calls(ctx)] == [("Finding", 0)]


def test_the_off_switch_leaves_no_trace_in_the_recorded_thresholds() -> None:
    """꺼진 run의 `RunRecord.thresholds`(= `flatten()`)에 `auditor_tool_hops` 키가 **없다**.

    `0.0`이 새로 생기면 지금까지 쌓인 모든 manifest와 열이 갈린다 — 요청 바이트가 같아도 재현성
    기록의 모양은 갈린다(#349 `AUDITOR_SOURCE_OPTIONS`·#375 `scout_slice_trim`과 같은 규약이다).
    켜면 값이 실린다 — 안 실으면 어떤 상한으로 돈 run인지 기록이 안 남는다.

    ⏵ 2026-10-04 — 저장소 기본이 16이 됐으므로(이슈 #419) **루프가 도는 run의 manifest는 이 키를
    든다.** 「없다」 쪽은 명시로 끈 run의 성질이고, 그쪽이 여전히 참인지가 이 검사의 절반이다 —
    끄는 길이 막히면 옛 manifest와 모양을 맞출 방법이 없어진다. 여기서 보는 것은 `flatten()`(설정값)
    이고, 러너가 manifest에 싣는 값은 아래 `_recorded`를 쓰는 검사들이 본다 — Fake 면제로 한 방으로
    도는 좌석은 설정이 16이어도 키가 없다."""
    thresholds = load_config("dev_fake").thresholds

    assert thresholds.flatten()["auditor_tool_hops"] == 16.0, "저장소 기본(#419)"
    assert "auditor_tool_hops" not in thresholds.transition(auditor_tool_hops=0).flatten()


def _recorded(config, tmp_path: Path):  # noqa: ANN001, ANN202
    """**생산 경로**(`runner._new_record`)가 만든 `RunRecord`.

    `flatten()`만 단언하면 러너가 그 값을 쓰지 않아도 초록이다 — `test_config.py`의
    `reasoning_efforts` 검사가 같은 이유로 이 함수를 부른다."""
    record, _ = _new_record(
        RunSpec(condition="B3prime@pipeline", target=tmp_path, device="atom"),
        config,
        "run_20260901_0001",
        datetime(2026, 9, 1, tzinfo=UTC),
        [],
        {},
        {},
        None,
    )
    return record


def test_a_seat_the_fake_exemption_covers_records_no_tool_hops(tmp_path: Path) -> None:
    """`loop_problem`의 둘째 규칙 면제로 **한 방으로 도는** Auditor 좌석의 manifest에는
    `auditor_tool_hops`가 없다.

    `thresholds`는 프로필 공통이라 저장소 기본 16(이슈 #419)이 `dev_fake`에도 걸린다. 그 좌석은
    `Finding` 판이라 루프를 안 돈다 — 16을 그대로 싣으면 manifest는 「켰다」이고 `auditor_hop`은
    0건이라 **「켰는데 한 번에 answer했다」**로 읽힌다(CLAUDE.md §0: 안 돈 것을 돈 값으로 적는다).
    키 부재는 이 저장소가 「꺼짐」을 적는 방식이다(`Thresholds.flatten`)."""
    config = load_config("dev_fake")
    entry = config.model_for("auditor")
    assert config.thresholds.auditor_tool_hops == 16, "전제 — 저장소 기본이 켜져 있다"
    template = load_prompt(Role.AUDITOR, entry.prompt_template_version)
    assert template.output_schema == "Finding" and entry.is_fake, "전제 — 면제가 걸린 좌석이다"
    assert auditor.loop_problem(template, 16, is_fake=True) is None, "전제 — 면제다"

    record = _recorded(config, tmp_path)

    assert "auditor_tool_hops" not in record.thresholds


def test_a_served_loop_seat_records_the_cap_it_ran_with(tmp_path: Path) -> None:
    """위 검사의 음성 쌍 — 실서빙 좌석(`harness_pool`: ⏵ 2026-10-09부터 v11 ·
    `AuditorProcedureNullableCweAction` — 그 전 v10 · ⏵ 2026-10-10부터 v13 · ⏵ 2026-10-11부터 v14 ·
    `AuditorProcedureAuditAlwaysAction`)은 16이 그대로
    실린다.

    위만 보면 러너가 이 키를 늘 빼도 초록이다. 켠 run이 어떤 상한으로 돌았는지가 이 기록의
    존재 이유다(`test_the_off_switch_leaves_no_trace_in_the_recorded_thresholds`의 「켜면 값이
    실린다」)."""
    record = _recorded(load_config("harness_pool"), tmp_path)

    assert record.thresholds["auditor_tool_hops"] == 16.0


def test_a_fake_seat_that_really_loops_records_its_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fake 좌석이어도 **루프 판을 고르면 루프가 돈다** — 그 manifest는 상한을 싣는다.

    이 파일의 B1~B8이 그렇게 돈다(`_loop_config` — Fake 좌석 + 임시 디렉터리의 루프 판).
    면제의 판정은 `is_fake`가 아니라 **판이 루프 봉투를 선언하는가**다(`_ask_model`의 분기와
    같다 — `_loop_envelope`). `is_fake`로 빼면 위 둘은 초록이고 여기서만 빨개진다."""
    _patch_prompts(monkeypatch, tmp_path)
    config = _loop_config(load_config("dev_fake"), hops=4)

    record = _recorded(config, tmp_path)

    assert record.thresholds["auditor_tool_hops"] == 4.0


def test_a_switched_off_loop_records_without_reading_the_auditor_prompt(tmp_path: Path) -> None:
    """상한 0이면 `_new_record`가 Auditor 프롬프트를 **안 읽는다** — 못 읽어도 run이 거기서 안 선다.

    `tool_hops_in_effect`가 `hops == 0`에서도 프롬프트를 먼저 읽으면 결과는 어차피 0인데 **수정
    전에 없던 실패 지점**이 `_new_record`에 생긴다. `cli run`의 `_auditor_loop_problem`이 같은
    프롬프트를 먼저 읽어 막지만 그 게이트는 `stop_at >= AUDITOR`일 때만 돈다 — `--stop-after` 5
    미만·`--dry-run`은 Auditor 프롬프트를 안 읽고 `_new_record`에 온다(독립 검토 L-1). 그리고 상한이
    **양수면** 읽어야 하므로(면제 판정이 그 meta에 달렸다) 못 읽는 판은 여전히 `PromptError`다 —
    조용히 「안 돈다」로 적지 않는다(fail-closed). 샘플은 저장소 실물이다: `harness_pool`의 Auditor
    좌석 판 이름만 디스크에 없는 `v99`로 바꿨다."""
    config = load_config("harness_pool")
    key = config.profile.roles["auditor"]
    missing = config.models[key].transition(prompt_template_version="v99")
    broken = config.transition(models={**config.models, key: missing})
    off = broken.transition(thresholds=broken.thresholds.transition(auditor_tool_hops=0))

    assert "auditor_tool_hops" not in _recorded(off, tmp_path).thresholds

    with pytest.raises(PromptError):
        _recorded(broken, tmp_path)


# --- B5: 조사만으로 상한을 소진 ----------------------------------------------------


def test_investigating_until_the_cap_stops_at_max_hop_and_queues_the_candidate(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """매 홉 다른 `lookup`만 내면 hop `limit`에서 `MAX_HOP`으로 서고 후보는 `queued`다.

    조사 홉은 0..limit-1이고 hop `limit`은 `answer`여야 한다(모델 호출 limit+1회). 그 홉에서도
    조사를 내면 그 조사는 **돌리지 않고** 선다 — 관측을 되먹일 홉이 없다. Finding은 만들지 않는다.
    폐기가 아니라 `queued`인 것이 이 정지의 계약이다: 그 전이를 빠뜨리면 후보가 `triaged`에
    남는다(`nodes/auditor.py` 머리말이 경고하는 모양). 또 hop `limit`의 프롬프트에만 「마지막
    홉」 안내가 붙는다 — 모델이 그 사실을 모르면 마지막 홉에도 조사를 내고 Finding 없이 끝난다.
    전이는 **C-5 `capsule_expansion_exhausted` 한 번**이다 — ADR 0012 결정 6이 C-5의 삭제를 따로
    하기로 했으므로(노드의 `_NO_FINDING_REASON` 주석) 그 삭제 PR이 여기서 먼저 빨개져야 한다."""
    limit = 3
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    _arm(ctx, monkeypatch, tmp_path, hops=limit)
    ctx.models = _ScriptedModel([_lookup_action(f"fn_{n}") for n in range(limit + 2)])

    with pytest.raises(auditor.AuditorHopLimit) as stopped:
        auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    assert stopped.value.stop is AuditorStopReason.MAX_HOP
    hops = _hops(ctx)
    assert [h.data["hop"] for h in hops] == list(range(limit + 1))
    assert hops[-1].data["stop_reason"] == "max_hop"
    assert all("stop_reason" not in h.data for h in hops[:-1])
    assert _queued(ctx, candidate)
    assert _queue_transitions(ctx, candidate) == [("C-5", "capsule_expansion_exhausted")]
    assert _finding_transitions(ctx) == [], "Finding 없이 선 루프가 Finding을 만들었다"
    assert set(ctx.models.schemas) == {AuditorAction}
    bodies = [prompt.body for _, prompt in ctx.models.prompts]
    assert [auditor._LAST_HOP_NOTE in body for body in bodies] == [
        hop == limit for hop in range(limit + 1)
    ]


# --- B6: 같은 요청 반복 ------------------------------------------------------------


@pytest.mark.parametrize(
    ("actions", "tool_calls"),
    [
        pytest.param(
            [_lookup_action("BUF_SIZE"), _lookup_action("BUF_SIZE", rationale="다시 본다")],
            0,
            id="same-lookup-names",
        ),
        pytest.param(
            [
                _probe_action("semgrep", spec=_spec("첫 명세")),
                _probe_action("semgrep", spec=_spec("다른 명세"), rationale="명세만 바꾼다"),
            ],
            1,
            id="same-tool-other-spec",
        ),
    ],
)
def test_asking_the_same_thing_twice_in_a_row_stops_as_repeat_request(
    ctx: RunContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    actions: list[AuditorAction],
    tool_calls: int,
) -> None:
    """직전 홉과 같은 요청이면 `REPEAT_REQUEST`로 서고, 그 반복 홉의 조사는 돌리지 않는다.

    `lookup`의 키는 `(kind, target)` 집합이다 — `rationale`·`purpose`가 달라도 같은 질문이다.
    `probe`의 키는 **도구 이름**이다: `spec`만 바꿔 또 부르면 같은 요청이다. 도구 호출 수가 1(첫
    probe만)인 것이 「반복 홉은 안 돌렸다」의 증거다. 상한은 5라 `MAX_HOP`이 가로채지 못한다.

    ⏵ **2026-10-13(#419) — 근거가 바뀌었다.** 전에는 *「도구가 `spec`을 안 받으므로 같은 관측이
    돌아온다」*가 이유였고 그것은 이제 **거짓이다**(노드가 `spec`을 싣는다). 이 검사는 **키가 아직
    도구 이름만 본다**는 현재 동작을 문다 — 그대로 둔 이유는 동결된 프롬프트(v8~v14)의 반복 규칙이
    *「`spec`만 바꿔도 다른 요청이 아니다」*라 적고 `tests/test_auditor_prompt_v10.py`가 그 산문과
    키를 묶는다는 것이다(`auditor._request_key`). 키에 PoC digest를 더하는 것은 다음
    판(auditor v15)과 같이 움직이고, 그때 이 사례는 반대로 뒤집힌다."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    _arm(ctx, monkeypatch, tmp_path, hops=5)
    ctx.models = _ScriptedModel(actions)
    before = len(_events(ctx, EventKind.TOOL_CALL))

    with pytest.raises(auditor.AuditorHopLimit) as stopped:
        auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    assert stopped.value.stop is AuditorStopReason.REPEAT_REQUEST
    hops = _hops(ctx)
    assert [h.data["hop"] for h in hops] == [0, 1]
    assert hops[-1].data["stop_reason"] == "repeat_request"
    assert len(_events(ctx, EventKind.TOOL_CALL)) - before == tool_calls
    assert _queued(ctx, candidate)
    assert _queue_transitions(ctx, candidate) == [("C-5", "capsule_expansion_exhausted")]


@pytest.mark.parametrize(
    ("actions", "tool_calls"),
    [
        pytest.param(
            [_lookup_action("A"), _lookup_action("B"), _lookup_action("A", rationale="되짚는다")],
            0,
            id="lookup-A-B-A",
        ),
        pytest.param(
            [_probe_action("semgrep"), _lookup_action("X"), _probe_action("semgrep")],
            1,
            id="probe-lookup-probe",
        ),
    ],
)
def test_asking_the_same_thing_again_after_something_else_is_a_repeat(
    ctx: RunContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    actions: list[AuditorAction],
    tool_calls: int,
) -> None:
    """A → B → A도 반복이다 — 비교 대상은 **지금까지의 모든 홉**이다(#514).

    직전 홉 하나만 보던 판은 A → B → A → B로 번갈아 내면 서지 않았다 - 상한(`MAX_HOP`)까지 같은
    관측을 되풀이해 사 간다. PoV는 모든 gen 바이트를 `seen`과 견준다(`_run_pov_hops`)."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    _arm(ctx, monkeypatch, tmp_path, hops=6)
    ctx.models = _ScriptedModel(actions)
    before = len(_events(ctx, EventKind.TOOL_CALL))

    with pytest.raises(auditor.AuditorHopLimit) as stopped:
        auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    assert stopped.value.stop is AuditorStopReason.REPEAT_REQUEST
    hops = _hops(ctx)
    assert [h.data["hop"] for h in hops] == [0, 1, 2]
    assert hops[-1].data["stop_reason"] == "repeat_request"
    assert len(_events(ctx, EventKind.TOOL_CALL)) - before == tool_calls, "반복 홉의 조사는 안 돈다"
    assert _queue_transitions(ctx, candidate) == [("C-5", "capsule_expansion_exhausted")]


@pytest.mark.parametrize(
    "actions",
    [
        pytest.param(
            [_lookup_action("A"), _lookup_action("B"), _lookup_action("C"), _answer_action()],
            id="three-different-lookups",
        ),
        pytest.param(
            [_lookup_action("A"), _lookup_many(["A", "B"]), _probe_action("semgrep"),
             _lookup_action("B"), _answer_action()],
            id="other-request-set-is-not-the-same-question",
        ),
    ],
)
def test_a_trail_of_different_requests_is_not_a_repeat(
    ctx: RunContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    actions: list[AuditorAction],
) -> None:
    """음성 쌍 - 요청 **집합**이 다르면(`{A}` ↔ `{A,B}` ↔ `{B}`) 이름이 겹쳐도 반복이 아니다.
    전 홉 비교가 키를 이름 하나로 쪼개면 이 검사가 빨개진다."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    _arm(ctx, monkeypatch, tmp_path, hops=6)
    ctx.models = _ScriptedModel(actions)

    finding, _ = auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    assert isinstance(finding, Finding)
    hops = _hops(ctx)
    assert len(hops) == len(actions)
    assert hops[-1].data["stop_reason"] == "answered"
    assert all("stop_reason" not in h.data for h in hops[:-1])


# --- 크기 거절 - 홉 이벤트와 사유가 말한다 (#514) ------------------------------------


@pytest.mark.parametrize(
    "rejection",
    [
        pytest.param(_context_overflow(), id="400-with-the-overflow-phrase"),
        pytest.param(ModelGatewayError("HTTP 413", status=413), id="413"),
    ],
)
def test_a_size_rejection_in_the_loop_is_named_by_the_hop_event_and_the_queue_reason(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rejection: Exception
) -> None:
    """홉 1의 호출이 크기 거절이면 **그 홉의 이벤트**가 `context_overflow`로 남고 후보는
    C-5로 `queued`다.

    예전에는 이벤트가 0건이고(이벤트는 호출 성공 뒤에만 났다) 후보에 `schema_parse_failed`(C-6)가
    달렸다 - 모델이 스키마를 못 맞춘 것이 아닌데 파싱 실패율의 분자가 늘었다. 예외는 **그대로
    올라간다** - 받는 것은 러너의 `_drop`이고 거기서 연속 폐기 카운터·성공 0 백스톱이 돈다
    (아래 러너 검사). 노드가 삼키면 모든 후보가 크기 거절인 run이 완주로 읽힌다."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    _arm(ctx, monkeypatch, tmp_path, hops=4)
    ctx.models = _ScriptedModel([_lookup_action("X"), _answer_action()], fail_at=(1, rejection))

    with pytest.raises(ModelGatewayError) as raised:
        auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    assert raised.value is rejection
    hops = _hops(ctx)
    assert [h.data["hop"] for h in hops] == [0, 1]
    assert hops[-1].data["stop_reason"] == AuditorStopReason.CONTEXT_OVERFLOW.value
    assert hops[-1].data["stop_reason"] == "context_overflow"
    assert hops[-1].data["error"] == "ModelGatewayError"
    assert "stop_reason" not in hops[0].data
    assert _queue_transitions(ctx, candidate) == [("C-5", "capsule_expansion_exhausted")]


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(ModelGatewayError("HTTP 503", status=503), id="server-error"),
        pytest.param(
            ModelGatewayError("HTTP 400", status=400, detail="invalid request body"),
            id="400-without-the-overflow-phrase",
        ),
        pytest.param(SchemaParseError("스키마를 못 맞춘다"), id="schema-parse-failure"),
    ],
)
def test_any_other_model_call_failure_keeps_the_old_label_and_gets_no_overflow_event(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: Exception
) -> None:
    """음성 쌍 - 크기 거절이 아닌 실패(서버 오류 · 문구 없는 400 · 파싱 실패)는 홉 이벤트를 안 내고
    후보에 C-6을 단다. 400은 **다음 요청도 같은 거절**이라(`runner._rejects_every_request`) 크기로
    읽으면 run이 서야 할 자리에서 후보만 버리게 된다."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    _arm(ctx, monkeypatch, tmp_path, hops=4)
    ctx.models = _ScriptedModel([_lookup_action("X"), _answer_action()], fail_at=(1, failure))

    with pytest.raises(type(failure)):
        auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    assert [h.data["hop"] for h in _hops(ctx)] == [0]
    assert all("stop_reason" not in h.data for h in _hops(ctx))
    assert _queue_transitions(ctx, candidate) == [("C-6", "schema_parse_failed")]


def _no_finding_warnings(result) -> list[str]:  # noqa: ANN001
    return [w for w in result.warnings if "Finding 없이 선 후보" in w]


def _overflow_warnings(result) -> list[str]:  # noqa: ANN001
    return [w for w in result.warnings if "크기 거절 — 후보를 버렸다(#514)" in w]


def test_a_candidate_the_server_refuses_for_size_is_dropped_loudly_and_the_next_one_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """후보 둘 - 첫 후보의 홉 2에서 크기 거절 -> 둘째 후보는 Finding까지 가고 run은 끝난다.

    **조용히 버리지 않는다**: `RunResult.warnings`에 후보 id와 홉이 든 한 줄이 오른다(#617의
    probe 0 경고와 같은 자리). 러너의 `_drop`이 받는 경로(`target_skipped`)는 그대로다 - 연속
    폐기 카운터가 크기 거절을 안 세므로 run은 안 선다."""
    _patch_prompts(monkeypatch, tmp_path)
    _failing_model(monkeypatch, [None, None, _context_overflow()], role_under_test=Role.AUDITOR)

    result, trace_path = _run(
        tmp_path, before=_with_index(), configure=lambda c: _loop_config(c, hops=4)
    )

    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    hops = [e for e in events if e["kind"] == "auditor_hop"]
    overflow = [e for e in hops if e["data"].get("stop_reason") == "context_overflow"]
    assert [(e["subject_id"], e["data"]["hop"]) for e in overflow] == [("cand_0001", 2)]
    answered = [e["subject_id"] for e in hops if e["data"].get("stop_reason") == "answered"]
    assert answered == ["cand_0002"], "둘째 후보가 처리되지 않았다"
    assert result.record.stopped_after is None, "크기 거절 하나가 run을 멈췄다"
    assert len(result.findings) == 1
    assert [e["data"]["reason"] for e in events if e["kind"] == "target_skipped"] == [
        "gateway_error"
    ]
    (warning,) = _overflow_warnings(result)
    assert "cand_0001" in warning and "홉 2" in warning and "#514" in warning


def test_the_one_shot_path_keeps_c6_and_emits_no_hop_event_on_a_size_rejection(
    ctx: RunContext,
) -> None:
    """한 방 경로는 종전 그대로다(C-6 · 홉 이벤트 없음).

    루프의 크기 거절만 C-5+`context_overflow`다(#514).

    한 방 호출은 홉 번호도 `AuditorStopReason`도 없다. 여기까지 C-5로 읽으면 꺼진 run의 사유
    분포가 이전 run과 달라져 비교가 깨진다(evaluation §2.3)."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    ctx.config = ctx.config.transition(
        thresholds=ctx.config.thresholds.transition(auditor_tool_hops=0)
    )
    ctx.models = _ScriptedModel([], fail_at=(0, _context_overflow()))

    with pytest.raises(ModelGatewayError):
        auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    assert _hops(ctx) == []
    assert _queue_transitions(ctx, candidate) == [("C-6", "schema_parse_failed")]


def test_every_candidate_refused_for_size_in_the_loop_stops_the_run_via_the_success_zero_backstop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """모든 후보가 루프에서 크기 거절이면 run은 완주로 안 읽힌다 - #296 백스톱이 선다.

    노드가 크기 거절을 삼키지 않으므로 러너의 `_drop`이 받고, 5단계에서 한 번도 성공하지 못한
    자리가 끝에서 `_stop_if_a_caller_saw_nothing`에 걸린다. 후보마다 #514 경고 한 줄이 남는다."""
    _patch_prompts(monkeypatch, tmp_path)
    _failing_model(monkeypatch, [_context_overflow()] * 16, role_under_test=Role.AUDITOR)

    result, _ = _run(tmp_path, before=_with_index(), configure=lambda c: _loop_config(c, hops=4))

    assert result.record.stopped_after == 4, "5단계에서 서야 한다 - 4단계까지만 끝난 run"
    assert not result.findings
    assert any("5단계" in w and "한 번도 성공하지 못했다" in w for w in result.warnings)
    assert len(_overflow_warnings(result)) >= 1


def test_a_rejection_that_is_not_about_size_still_stops_the_run_and_raises_no_overflow_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """음성 - 크기가 아닌 400(요청 모양·서버 설정 거절)은 다음 요청도 같으므로 첫 건에서 run을
    세운다(`_rejects_every_request` - #296). 이 경로는 **그대로**다 - 크기 거절 흡수가 이것까지
    삼키면 같은 거절을 후보 수만큼 태운다."""
    _patch_prompts(monkeypatch, tmp_path)
    systematic = ModelGatewayError("HTTP 400", status=400, detail="thinking budget needs a parser")
    _failing_model(monkeypatch, [None, None, systematic], role_under_test=Role.AUDITOR)

    result, trace_path = _run(
        tmp_path, before=_with_index(), configure=lambda c: _loop_config(c, hops=4)
    )

    assert result.record.stopped_after is not None
    assert _overflow_warnings(result) == []
    assert "context_overflow" not in trace_path.read_text(encoding="utf-8")


# --- 러너 — Finding 없이 선 후보는 그 후보만 멈춘다 ---------------------------------


@pytest.mark.parametrize("per_stage", [False, True], ids=["per_candidate", "per_stage"])
def test_the_runner_survives_a_candidate_the_loop_stopped_without_a_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, per_stage: bool
) -> None:
    """`AuditorHopLimit`이 러너 밖으로 새지 않는다 — 그 후보만 멈추고 run은 끝까지 간다.

    **사고 모양:** 처음 판은 이 예외를 받는 `except`가 없었다. 모델이 같은 요청을 두 번 내거나
    조사만 하는 것은 정상적인 모델 행동인데, 그 한 번이 `run_pipeline`의 최후 방어선에서
    `UNFORESEEN_FAILURE`로 run 전체를 멈췄고(`stopped_after: 5` · 보고서 없음) 뒤 후보는 감사도 못
    받았다. 이 검사는 실제 파이프라인(`cli.execute` · dev_fake)을 도구 루프 판 + 상한 1로 돌린다 —
    fake가 `lookup → probe`를 내서 hop 1에서 `MAX_HOP`이 난다. 두 실행 모드가 같은 `_step_auditor`를
    탄다."""
    _patch_prompts(monkeypatch, tmp_path)

    def configure(config):  # noqa: ANN001, ANN202
        looped = _loop_config(config, hops=1)
        return _per_stage(looped) if per_stage else looped

    result, trace_path = _run(tmp_path, configure=configure)

    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    stops = [
        e["data"]["stop_reason"]
        for e in events
        if e["kind"] == "auditor_hop" and "stop_reason" in e["data"]
    ]
    assert stops == ["max_hop"], "전제 — 루프가 돌고 Finding 없이 섰다"
    assert result.record.stopped_after is None, "그 후보 하나가 run 전체를 멈췄다"
    assert not result.findings
    queued = [
        e["data"]["reason"]
        for e in events
        if e["kind"] == "transition"
        and e["subject_kind"] == "candidate"
        and e["data"]["to_state"] == "queued"
    ]
    # 다른 후보가 앞 단계에서 문턱 아래로 `queued`된 것은 이 검사의 대상이 아니다.
    assert queued.count("capsule_expansion_exhausted") == 1
    # Finding 없는 정지는 run 경고에도 오른다(사유별 건수) - 「찾은 게 없다」와 구별된다.
    (warning,) = _no_finding_warnings(result)
    assert "max_hop 1건" in warning and "repeat_request" not in warning


def _warning_for_stops(*stops: str) -> str | None:
    """`auditor_hop`에 `stop_reason`이 든 trace로 `_auditor_no_finding_warning`을 직접 부른다."""
    trace = TraceWriter("run_20261015_0001")
    for i, stop in enumerate(stops):
        trace.emit(
            EventKind.AUDITOR_HOP, stage=5, subject_kind="candidate", subject_id=f"cand_{i:04d}",
            hop=1, action="lookup", stop_reason=stop,
        )  # fmt: skip
    return _auditor_no_finding_warning(trace)


def test_the_no_finding_warning_names_each_stop_reason_it_counted() -> None:
    """정지 사유별 건수를 센다 - `repeat_request` 하나, 둘 다일 때의 문구를 못 박는다.
    `answered`·`context_overflow` 홉은 세지 않는다(크기 거절은 `_auditor_overflow_warnings`)."""
    repeat = _warning_for_stops("repeat_request", "answered", "context_overflow")
    assert repeat is not None
    assert "후보 1건(repeat_request 1건) - " in repeat and "max_hop" not in repeat

    both = _warning_for_stops("max_hop", "repeat_request", "max_hop")
    assert both is not None
    assert "후보 3건(repeat_request 1건 · max_hop 2건) - " in both

    assert _warning_for_stops("answered", "context_overflow") is None


# --- 러너 — 루프가 돌았는데 probe가 0이면 run 경고 (#617) -----------------------------


def _probe_less(monkeypatch: pytest.MonkeyPatch) -> None:
    """fake가 hop1에서 `probe` 대신 곧장 `answer`한다 - 실서빙 Auditor가 한 행동(lookup만)."""
    real = fake_model._auditor_investigation

    def investigate(context, history):  # noqa: ANN001, ANN202
        return None if len(history) // 2 == 1 else real(context, history)

    monkeypatch.setattr(fake_model, "_auditor_investigation", investigate)


def _zero_probe_warnings(result) -> list[str]:  # noqa: ANN001
    return [w for w in result.warnings if "probe가 한 번도 실행되지 않았다" in w]


def test_a_loop_run_that_never_probes_says_so_in_the_run_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """루프가 돌고(lookup -> answer) `probe`가 0건이면 `RunResult.warnings`에 오른다.
    8단계 빈 풀 경고와 같은 자리다 - 리포트 본문은 둘 다 안 싣는다."""
    _patch_prompts(monkeypatch, tmp_path)
    _probe_less(monkeypatch)

    result, trace_path = _run(tmp_path, configure=lambda c: _loop_config(c, hops=4))

    actions = [
        e["data"]["action"]
        for e in map(json.loads, trace_path.read_text(encoding="utf-8").splitlines())
        if e["kind"] == "auditor_hop"
    ]
    assert actions and "probe" not in actions, "전제 - 루프가 돌았고 probe는 0건"
    (warning,) = _zero_probe_warnings(result)
    assert f"{len(actions)}홉" in warning and f"lookup {actions.count('lookup')}" in warning


def test_a_probe_cut_by_the_hop_limit_does_not_count_as_a_probe_that_ran(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """상한 1 - 홉이 [lookup, probe(max_hop)]이고 그 probe는 조사가 안 돌아 `tool_call`이 0건이다.
    `action`만 세면 경고가 꺼진다(리뷰 M2)."""
    _patch_prompts(monkeypatch, tmp_path)

    result, trace_path = _run(tmp_path, configure=lambda c: _loop_config(c, hops=1))

    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    hops = [
        (e["data"]["action"], e["data"].get("stop_reason"))
        for e in events
        if e["kind"] == "auditor_hop"
    ]
    assert hops == [("lookup", None), ("probe", "max_hop")], "전제"
    assert not [e for e in events if e["kind"] == "tool_call" and e["stage"] == 5], "전제"
    assert len(_zero_probe_warnings(result)) == 1


def test_a_loop_run_that_probes_or_never_looped_says_nothing_about_probes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """음성 쌍 - probe가 한 번이라도 있으면 없고, 홉 이벤트가 0건이어도 없다.
    ⚠ 0건은 「꺼짐」과 「켠 run의 첫 호출 전부 실패」 둘 다다 - 이 검사는 꺼짐 쪽만 문다."""
    _patch_prompts(monkeypatch, tmp_path)
    probed, probed_trace = _run(tmp_path, "probed", configure=lambda c: _loop_config(c, hops=4))
    assert '"action":"probe"' in probed_trace.read_text(encoding="utf-8").replace(" ", ""), "전제"
    assert _zero_probe_warnings(probed) == []

    _probe_less(monkeypatch)
    off, off_trace = _run(tmp_path, "off")  # dev_fake 한 방 판 - 홉 이벤트가 없다
    assert "auditor_hop" not in off_trace.read_text(encoding="utf-8"), "전제 - 루프 꺼짐"
    assert _zero_probe_warnings(off) == []
    # 같은 음성 쌍 - Finding까지 간 run과 루프가 꺼진 run엔 정지 경고가 없다.
    assert _no_finding_warnings(probed) == []
    assert _no_finding_warnings(off) == []


# --- B7: 켜졌는데 프롬프트가 도구 루프를 못 받친다 ---------------------------------


@pytest.mark.parametrize("declared", [None, ()], ids=["no-tools-key", "empty-tools"])
def test_a_switched_on_loop_whose_prompt_declares_no_tools_is_refused(
    ctx: RunContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    declared: tuple[str, ...] | None,
) -> None:
    """루프를 켰는데 배선된 Auditor 판의 meta가 `tools`를 선언하지 않으면 cli도 노드도 막는다.

    `run`은 `_auditor_loop_problem`이 비지 않으면 exit 2로 서고(아래 `cli.main` 검사가 실제로
    태운다), `doctor`는 `_check_auditor_loop`가 ERROR를 낸다. 노드는 같은 함수(`loop_problem`)로
    `PromptError`를 올린다 — **첫 호출 전에**. 막는 이유는 **run이 정상 종료한다**는 것이다 —
    프롬프트가 도구를 안 알리면 모델은 도구를 영영 안 쓰고, 그 run의 「도구 호출 0건」이 「모델이 안
    골랐다」로 읽힌다. 키가 없는 경우와 빈 목록인 경우가 같은 말이다."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    _arm(ctx, monkeypatch, tmp_path, hops=4, tools=declared)

    problem = cli._auditor_loop_problem(ctx.config)
    assert problem is not None and f"auditor/{LOOP_VERSION}" in problem
    checks = cli._check_auditor_loop(ctx.config)
    assert [(c.severity, c.message) for c in checks] == [(cli.Severity.ERROR, problem)]
    with pytest.raises(PromptError, match="도구를 선언하지 않는다"):
        auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)
    assert _auditor_calls(ctx) == [], "불일치인데 모델을 불렀다 — GPU 시간이 샌다"


def test_the_cli_gate_lets_a_declared_loop_and_a_switched_off_loop_through(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """도구를 선언한 판은 통과하고, 루프를 끈 한 방 판도 통과한다 — 위 거절 검사의 양성 쌍이다.

    거절만 보면 게이트를 「늘 ERROR」로 바꿔도 초록이다. 막는 규칙이 멀쩡한 설정을 막으면 기본
    설정의 모든 run이 시작을 못 한다 — 기본 설정(`dev_fake`: 저장소 프롬프트 v1 한 방 판 · 저장소
    기본 상한 16)이 통과하는 것도 같이 본다. ⏵ 2026-10-04 — 저장소 기본 상한은 더 이상 0이 아니다
    (이슈 #419). 그 설정이 통과하는 것은 상한이 꺼져서가 아니라 **Fake 좌석 면제**(`loop_problem`의
    둘째 규칙) 덕이다."""
    assert cli._auditor_loop_problem(ctx.config) is None
    assert cli._check_auditor_loop(ctx.config) == []

    _arm(ctx, monkeypatch, tmp_path, hops=4, tools=LOOP_TOOLS)
    assert cli._auditor_loop_problem(ctx.config) is None
    assert cli._check_auditor_loop(ctx.config) == []


class _Started(RuntimeError):
    """게이트를 지나 `cli.execute`까지 왔다는 표시."""


def _patch_cli_config(
    monkeypatch: pytest.MonkeyPatch, *, hops: int, loop_seat: bool, served_seat: bool = False
) -> None:
    """`cli.load_config`가 상한 `hops`(와 좌석)를 단 config를 돌려주게 한다.

    좌석은 셋이다 — 루프 판(`loop_seat`) · 실서빙 한 방 판(`served_seat`) · 손 안 댄 `dev_fake`.
    셋째는 Fake 한 방 좌석이라 `loop_problem`의 둘째 규칙이 **면제된다**(그 함수 docstring).
    """
    real = cli.load_config

    def load(profile: str, **kwargs: object):  # noqa: ANN202
        config = real(profile, **kwargs)
        if loop_seat:
            return _loop_config(config, hops=hops)
        if served_seat:
            return _served_one_shot_config(config, hops=hops)
        return config.transition(thresholds=config.thresholds.transition(auditor_tool_hops=hops))

    monkeypatch.setattr(cli, "load_config", load)

    def started(*_: object, **__: object) -> None:
        raise _Started

    monkeypatch.setattr(cli, "execute", started)


_RUN_ARGS = ["run", "--profile", "dev_fake", "--target", str(TARGET), "--seed", "1"]


@pytest.mark.parametrize(
    ("defect", "needle"),
    [
        pytest.param("no-tools", "도구를 선언하지 않는다", id="no-tools"),
        pytest.param("loop-prompt-off", "auditor_tool_hops가 0이다", id="loop-prompt-off"),
        pytest.param("one-shot-prompt-on", "auditor_tool_hops가 4이다", id="one-shot-prompt-on"),
        pytest.param("tool-index", "받지 않는다", id="tool-index"),
    ],
)
def test_run_refuses_to_start_when_the_loop_and_the_prompt_disagree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    defect: str,
    needle: str,
) -> None:
    """`cli.main(["run", ...])`가 exit 2로 선다 — 게이트 함수가 아니라 run이 막는 자리를 태운다.

    처음 판은 `_auditor_tools_undeclared`만 불러서 `run`의 `if tools_undeclared:` 본문을 `pass`로
    바꿔도, 가드 조건에 `and False`를 붙여도 초록이었다. 어긋남 넷 — 도구 선언 없음 · 루프 판인데
    상한 0 · 한 방 판인데 상한 양수 · 광고하는데 문법이 거절하는 도구(`index`) — 을 각각 건다. 뒤
    셋은 cli가 예전엔 못 보던 것이다(노드만 첫 Auditor 호출에서 세웠다).

    ⏵ 2026-10-04 — `one-shot-prompt-on` 행은 좌석을 **실서빙**으로 간다(`_served_one_shot_config`).
    그 규칙은 Fake 좌석에서 면제되므로(이슈 #419 · `loop_problem` docstring) `dev_fake` 그대로
    태우면 이 행이 **조용히 통과한다** — 면제를 넣은 그 커밋에서 실제로 그랬다."""
    hops = 0 if defect == "loop-prompt-off" else 4
    if defect != "one-shot-prompt-on":
        tools = {
            "no-tools": None,
            "loop-prompt-off": LOOP_TOOLS,
            "tool-index": (*LOOP_TOOLS, "index"),
        }[defect]
        _patch_prompts(monkeypatch, tmp_path, tools=tools)
    _patch_cli_config(
        monkeypatch,
        hops=hops,
        loop_seat=defect != "one-shot-prompt-on",
        served_seat=defect == "one-shot-prompt-on",
    )

    # 실서빙 좌석을 끼운 행은 프로필이 **실서빙으로 읽힌다** — `cli`가 `--device`·`--condition`
    # 없이는 그 run을 시작하지 않으므로(비교표에 넣을 수 없는 run이다) 루프 게이트 **앞에서**
    # exit 2가 난다. 둘을 주지 않으면 이 행은 엉뚱한 사유로 초록이 된다.
    served = (
        ["--device", "test-box", "--condition", "B3prime@pipeline"]
        if defect == "one-shot-prompt-on"
        else []
    )
    code = cli.main([*_RUN_ARGS, *served, "--artifacts-root", str(tmp_path / "arts")])

    err = capsys.readouterr().err
    assert code == 2, err
    assert needle in err, err
    assert not (tmp_path / "arts").exists() or not list((tmp_path / "arts").iterdir())


def test_a_declared_loop_passes_the_run_gate_and_a_run_that_stops_early_is_not_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """선언이 맞으면 게이트를 지나 `execute`까지 가고, Auditor에 안 닿는 run(`--stop-after` 4)은
    어긋난 판이어도 막지 않는다 — 안 부를 프롬프트를 이유로 막지 않는다(SOURCE 옵션 가드와 같은
    판단)."""
    _patch_prompts(monkeypatch, tmp_path, tools=LOOP_TOOLS)
    _patch_cli_config(monkeypatch, hops=4, loop_seat=True)
    with pytest.raises(_Started):
        cli.main([*_RUN_ARGS, "--artifacts-root", str(tmp_path / "a")])

    other = tmp_path / "other"
    other.mkdir()
    _patch_prompts(monkeypatch, other, tools=None)
    with pytest.raises(_Started):
        cli.main([*_RUN_ARGS, "--stop-after", "4", "--artifacts-root", str(tmp_path / "b")])


def test_doctor_reports_the_disagreement_as_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """doctor가 같은 어긋남을 ERROR로 댄다 — `_check_auditor_loop`가 doctor에 꽂혀 있다는 증거다."""
    _patch_prompts(monkeypatch, tmp_path, tools=None)
    _patch_cli_config(monkeypatch, hops=4, loop_seat=True)

    checks = cli.doctor("dev_fake", artifacts_root=tmp_path / "arts")

    errors = [c.message for c in checks if c.severity is cli.Severity.ERROR]
    assert any(f"auditor/{LOOP_VERSION}" in message for message in errors), errors


def test_estimate_counts_the_tool_loop_calls_in_the_auditor_ceiling() -> None:
    """`cli estimate`의 호출 상한이 도구 루프를 센다 — 후보당 `1 + auditor_tool_hops`회다.

    docstring이 「예산은 최악을 견뎌야 한다」고 적는 값이다. 상한 16이면 Auditor 몫이 17배인데
    처음 판은 켜도 끈 것과 같은 값을 냈다 — 그 값으로 잡은 예산은 도중에 `BudgetExhausted`로
    부분 결과가 된다. 끈 run은 종전 값 그대로다 — ⏵ 2026-10-04 저장소 기본이 16이 됐으므로
    (이슈 #419) 그 「끈 run」을 여기서 **명시로** 만든다."""
    base = load_config("dev_fake")
    config = base.transition(thresholds=base.thresholds.transition(auditor_tool_hops=0))
    capsules = 10
    candidates = capsules * cli._scout_places(config)

    off = cli._expected_calls(config, capsules)
    on = cli._expected_calls(
        config.transition(thresholds=config.thresholds.transition(auditor_tool_hops=16)), capsules
    )

    assert on - off == candidates * 16


# --- B8: 모델 문자열이 도구 argv에 안 들어간다 -------------------------------------


def _run_one_probe(
    ctx: RunContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    action: AuditorAction,
) -> tuple[_SpyTools, list, str]:
    """`probe` 한 번을 돌려 (러너 스파이, 그 홉의 `tool_call` 이벤트, 다음 홉의 관측)을 낸다."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    _arm(ctx, monkeypatch, tmp_path, hops=4)
    ctx.models = _ScriptedModel([action, _answer_action()])
    spy = _SpyTools(ctx.tools)
    ctx.tools = spy
    before = len(_events(ctx, EventKind.TOOL_CALL))

    auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    return spy, _events(ctx, EventKind.TOOL_CALL)[before:], ctx.models.prompts[1][1].body


@pytest.mark.parametrize("tool", ["semgrep", "asan", "poc"])
def test_a_probe_hop_feeds_the_assembled_spec_bytes_like_the_pov_loop(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tool: str
) -> None:
    """`probe`에 `spec`이 있으면 러너가 받는 `ToolArgs`는 `pov.run_tool`과 **같은 계약**이다 —
    `argv == [도구, INPUT_PLACEHOLDER]` · `input_bytes == assemble_poc(spec)`.

    ⏵ **2026-10-13(#419) — 이 검사가 뒤집혔다.** 전에는 `..._puts_only_the_tool_name_in_argv`가
    같은 입력(`spec`에 셸 메타문자 표식)에서 `argv == [도구]`와 입력 없음을 물었다 — 노드가
    `spec`을 도구에 안 줬기 때문이다. 이제 싣는다. **전 판이 지키던 성질은 그대로 문다**: 모델이 쓴
    **문자열**(`rationale`·`spec.rationale`)은 argv에도 입력 바이트에도 안 실린다 — 실리는 것은
    `assemble_poc`이 `spec`의 세그먼트에서 조립한 바이트뿐이다. 도구 셋을 도는 것은 노드가 어느
    도구든 같은 계약으로 싣기 때문이다(컨테이너에서 **읽는** 도구는 `poc` 하나다 — 이 검사는 읽힘이
    아니라 **실림**을 문다). `tool_call` 이벤트는 크기와 digest만 싣는다(원바이트는 안 간다)."""
    marker = "MODEL_TEXT; echo pwned #"
    spec = _spec(marker)
    poc = assemble_poc(spec)

    spy, calls, _ = _run_one_probe(
        ctx, monkeypatch, tmp_path, _probe_action(tool, spec=spec, rationale=marker)
    )

    assert len(spy.calls) == 1
    ran, args, _sandbox = spy.calls[0]
    assert ran == tool
    assert list(args.argv) == [tool, INPUT_PLACEHOLDER]
    assert args.input_bytes == poc
    assert marker not in repr(args) and marker.encode() not in args.input_bytes
    assert [(c.data["tool"], c.data["argv"]) for c in calls] == [
        (tool, f"{tool} {INPUT_PLACEHOLDER}")
    ]
    assert calls[0].data["input_bytes"] == len(poc)
    assert calls[0].data["input_sha256"] == hashlib.sha256(poc).hexdigest()
    assert marker not in repr(calls)


@pytest.mark.parametrize("tool", ["semgrep", "poc"])
def test_a_probe_hop_without_a_spec_runs_the_tool_with_no_input(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tool: str
) -> None:
    """`spec`이 없으면 `argv == [도구]` · 입력 없음이고 `tool_call`에 `input_*` 키가 **없다**(키
    부재 = 입력 없음 — `RunContext.call_tool`). 홉 이벤트에도 `poc_*`가 없다. `spec` 없는 호출이
    자리표나 빈 입력(`b""`)으로 바뀌면 여기서 빨개진다 — `b""`는 **입력이다**(`ToolArgs`)."""
    spy, calls, _ = _run_one_probe(ctx, monkeypatch, tmp_path, _probe_action(tool))

    _ran, args, _sandbox = spy.calls[0]
    assert list(args.argv) == [tool]
    assert args.input_bytes is None
    assert [(c.data["tool"], c.data["argv"]) for c in calls] == [(tool, tool)]
    assert "input_bytes" not in calls[0].data and "input_sha256" not in calls[0].data
    hop = next(h for h in _hops(ctx) if h.data["action"] == "probe")
    assert "poc_sha256" not in hop.data and "poc_bytes" not in hop.data


def test_the_probe_observation_reports_the_input_it_fed_and_no_longer_says_it_was_not_given(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """관측 머리줄이 실은 입력의 크기와 digest를 든다 — `input_bytes=<n> input_sha256=<hex>`
    (`runner._pov_observation`의 `last_gen_bytes`·`last_gen_sha256`과 같은 모양 · 맨 hex라 홉
    이벤트의 `poc_sha256`과 바로 대조된다). 전 판의 *"recorded but NOT given to the tool"* 줄은
    **없다** — 이제 거짓이다(⏵ 2026-10-13 #419). `spec`이 없으면 입력 줄도 없다(말할 것이 없다)."""
    poc = assemble_poc(_spec())

    _spy, _calls, fed = _run_one_probe(
        ctx, monkeypatch, tmp_path, _probe_action("poc", spec=_spec())
    )

    head = fed.splitlines()[1]
    assert f"input_bytes={len(poc)} input_sha256={hashlib.sha256(poc).hexdigest()}" in head
    assert "evidence_id=" in head and head.index("evidence_id=") < head.index("input_bytes=")
    assert "NOT given" not in fed and "does not depend" not in fed
    assert "not read by" not in head  # poc는 입력을 읽는 도구다 — 머리줄이 그대로다
    hop = next(h for h in _hops(ctx) if h.data["action"] == "probe")
    assert hop.data["poc_sha256"] == hashlib.sha256(poc).hexdigest()
    assert hop.data["poc_bytes"] == len(poc)


@pytest.mark.parametrize("tool", ["asan", "gdb", "build"])
def test_the_probe_observation_says_a_non_poc_tool_did_not_read_the_input_it_was_fed(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tool: str
) -> None:
    """`poc`가 아닌 도구에 입력을 실었으면 `input_*` 바로 뒤에 *「(not read by this tool …)」* 사실
    문구가 붙는다 — `input_*`는 「실었다」이지 「읽혔다」가 아니다(컨테이너에서 읽는 도구는 `poc`
    하나, 이슈 #507). 지시가 아니라 사실이고, `error=`가 있으면 그 앞이 아니라 **뒤**다."""
    poc = assemble_poc(_spec())

    _spy, _calls, fed = _run_one_probe(
        ctx, monkeypatch, tmp_path, _probe_action(tool, spec=_spec())
    )

    head = fed.splitlines()[1]
    fed_line = f"input_bytes={len(poc)} input_sha256={hashlib.sha256(poc).hexdigest()}"
    assert f"{fed_line} (not read by this tool — only poc reads the input;" in head
    assert head.endswith("asan·gdb·build run our own build)") or "error=" in head


def test_the_probe_observation_has_no_input_line_without_a_spec(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`spec` 없는 `probe`의 관측에는 입력 줄도 「안 읽는다」 문구도 옛 "NOT given" 줄도 없다
    (읽히지 않는 도구여도 실은 것이 없으면 말할 것이 없다)."""
    _spy, _calls, plain = _run_one_probe(ctx, monkeypatch, tmp_path, _probe_action("asan"))

    assert "input_bytes" not in plain and "input_sha256" not in plain
    assert "not read by" not in plain and "NOT given" not in plain


# --- probe 관측 — 로그가 없는 것과 로그가 빈 것 · 인용 ----------------------------------


@pytest.mark.parametrize(
    ("payload", "needle", "absent"),
    [
        pytest.param(None, "no output log was available", "wrote nothing", id="unmeasured"),
        pytest.param("", "the tool wrote nothing to its log", "no output log", id="empty-log"),
        pytest.param("ASAN: heap-overflow", "heap-overflow", "wrote nothing", id="log"),
    ],
)
def test_a_probe_observation_tells_a_missing_log_from_an_empty_one(
    ctx: RunContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: str | None,
    needle: str,
    absent: str,
) -> None:
    """로그를 못 읽은 것(`None` — 안 잰 것)과 읽었는데 빈 것(`""` — 도구가 아무것도 안 쓴 것)을 다른
    문구로 준다. 처음 판은 둘 다 「(no output log was available)」였다 — 조용한 ASan·semgrep
    실행은 「깨끗하다」로 읽을 수 있는 실제 신호인데 모델이 그것을 「알 수 없음」으로 읽었다."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    _arm(ctx, monkeypatch, tmp_path, hops=4)
    ctx.read_payload = lambda ref: payload
    ctx.models = _ScriptedModel([_probe_action("semgrep"), _answer_action()])

    auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    body = ctx.models.prompts[1][1].body
    assert needle in body and absent not in body


@pytest.mark.parametrize("stderr_chars", [1_000, 300_000])
def test_a_probe_observation_carries_the_ambiguity_note_exactly_once(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stderr_chars: int
) -> None:
    """이슈 #573 — `poc` 조사의 `exit=1` 모호성 줄은 로그 끝에 붙어 읽기 상한(200,000자)을 넘는
    출력에서는 읽히지도 않았다. 이제 하네스가 만든 줄이라 로그를 거치지 않고 머리줄에 붙는다.
    실 러너 → 실 `make_payload_reader` → 실 `_probe_observation`, 정확히 한 번(짧은 로그도
    중복 없음)."""
    import subprocess
    from datetime import UTC, datetime

    from src.adapters import container_tools
    from src.ids import IdFactory
    from src.interfaces.tool_runner import INPUT_PLACEHOLDER, SandboxSpec, ToolArgs

    monkeypatch.setattr(container_tools.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        container_tools.subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "x" * stderr_chars),
    )
    runner = container_tools.ContainerToolRunner(
        ids=IdFactory("run_20260901_0001"),
        run_id="run_20260901_0001",
        root=tmp_path,
        artifacts_root=tmp_path,
        runtime="docker",
        observed_at=datetime(2026, 9, 1, tzinfo=UTC),
        poc_target=container_tools.PocTarget(image="n132/arvo:1065-vul", argv=("/bin/arvo",)),
    )
    sandbox = SandboxSpec(
        image=container_tools.DEFAULT_IMAGE,
        network=False,
        read_only=True,
        cpu_limit=1.0,
        memory_mb=2048,
        timeout_s=300,
    )
    evidence = runner.run(
        "poc", ToolArgs(argv=["poc", INPUT_PLACEHOLDER], workdir=".", input_bytes=b"A"), sandbox
    )
    ctx.read_payload = container_tools.make_payload_reader(tmp_path)

    observation = auditor._probe_observation(ctx, 1, evidence)

    assert observation.count(container_tools.POC_AMBIGUOUS_NOTE) == 1
    # 위치 — 머리줄(`tool=…`) 바로 다음 줄이고 도구 출력 구간(`--- poc output`)보다 앞이다.
    lines = observation.splitlines()
    assert lines[2] == container_tools.POC_AMBIGUOUS_NOTE
    output_at = next((i for i, ln in enumerate(lines) if ln.startswith("--- poc output")), None)
    assert output_at is None or 2 < output_at


def test_the_answer_hop_counts_the_probe_evidence_it_cites(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`probe` Evidence를 인용한 `answer` 홉이 그 수를 `cited_probe_evidence`로 남긴다.

    그 Evidence는 러너가 게이트에 넘기는 `state.evidences`에 없어서 인용해도 등급을 못 올리고
    (`gate.evidences_for`가 모르는 참조를 조용히 버린다) 그 사실이 trace 어디에도 안 남던 것을
    막는다. 인용한 둘 중 **probe가 실제로 만든 것만** 센다 — 아무도 안 만든 ID는 세지 않는다.
    `probe` 홉은 `evidence_id`로 `tool_call` 이벤트에 조인된다."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    _arm(ctx, monkeypatch, tmp_path, hops=4)
    ctx.models = _CitingModel()

    auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    hops = _hops(ctx)
    assert hops[-1].data["cited_probe_evidence"] == 1
    made = hops[0].data["evidence_id"]
    assert made in {e.subject_id for e in _events(ctx, EventKind.TOOL_CALL)}


# === v10: 최종 답이 `AuditProcedure`인 봉투 (`AuditorProcedureAction`) =========================
#
# v10 판은 `answer`가 `Finding`이 아니라 `AuditProcedure`(`audit` 블록 + 판정)를 싣는다. 하네스가
# 그것을 `Finding`으로 옮겨(`nodes/auditor._finding_from_procedure`) 루프 뒤 흐름이 종전 그대로
# 돈다. 스키마 자체는 `tests/test_auditor_procedure_schema.py`가 문다 — 여기는 **노드의 배선**이다:
# 어느 판이 루프 판인가를 한 곳이 정하는가 · 사유 문장이 선언 이름을 말하는가 · 옮겨 담을 때 무엇이
# 어디로 가는가 · 배선된 판이 근거 본문을 받는가 · Fake가 그 봉투를 낼 수 있는가.

# 하네스만 쓰는 필드 — v10의 모델은 이것을 **낼 수 없다**(`AuditProcedure`에 필드가 없다).
_HARNESS_ONLY_FIELDS = (
    "finding_id",
    "run_id",
    "candidate_id",
    "state",
    "missing_evidence",
    "produced_by",
)
_ISSUED = {
    "candidate_id": "candidate_0007",
    "finding_id": "finding_0007",
    "missing_id": "missing_evidence_0007",
}
_STAMPED = ProducedBy(role=Role.AUDITOR, model="scripted", prompt_version="v10")
_AUDIT_RESULTS = ("defeated", "holds", "path_not_executable", "decisive_code_missing")


def _audit_block_payload(result: str = "defeated", steps: tuple[str, ...] | None = None) -> dict:
    """`audit` 블록의 최소 유효 payload — 경로 둘(source · sink)을 든다.

    `steps`를 주면 경로를 그 종류들로 다시 짠다(`()`이면 빈 경로) — 양 끝이 없는 경로를 만드는
    자리다."""
    path = [
        {"step": "source", "symbol": "main", "note": "인자 len을 넘긴다"},
        {"step": "sink", "symbol": "memcpy", "note": "len이 buf(64)를 넘을 수 있다"},
    ]
    if steps is not None:
        path = [{"step": step, "symbol": f"sym_{i}"} for i, step in enumerate(steps)]
    return {
        "category": "spatial_bounds",
        "point": {"symbol": "parse_header", "expression": "memcpy(buf, src, len)"},
        "origin": {"kind": "param", "fact": "len은 호출자가 정한다"},
        "path": path,
        "defense": {
            "expression": "none on visible path",
            "status": "absent",
            "gap": None,
            "fact": "len 검사가 없다",
        },
        "refutation": {
            "result": result,
            "witness": "len=128이면 넘친다",
            "missing_information": ["parse_header의 호출자"]
            if result == "decisive_code_missing"
            else [],
        },
    }


def _procedure_payload(
    *,
    trigger_status: str = "triggerable",
    result: str = "defeated",
    evidence_refs: tuple[str, ...] = (),
    repro_origin: str | None = None,
    steps: tuple[str, ...] | None = None,
) -> dict:
    """`answer`의 `procedure` payload. `no_signal`이면 `audit`가 `null`이다(스키마 규칙).

    `trigger_status`와 `result`는 **독립이다** — 스키마가 그 대응을 안 싣는다(`AuditProcedure`).
    `steps`는 `audit.path`의 종류들이다(`_audit_block_payload`)."""
    no_signal = trigger_status == "no_signal"
    repro = (
        None
        if repro_origin is None
        else {"steps": ["./build.sh"], "expected_signal": "ASAN", "origin": repro_origin}
    )
    return {
        "audit": None if no_signal else _audit_block_payload(result, steps),
        "trigger_status": trigger_status,
        "confidence": 0.8,
        "cwe": "CWE-787",
        "title": "out-of-bounds write",
        "rca_draft": "len이 검증 없이 memcpy로 간다.",
        "evidence_refs": list(evidence_refs),
        "repro_spec": repro,
    }


def _procedure(**kwargs: object) -> AuditProcedure:
    return AuditProcedure.model_validate(_procedure_payload(**kwargs))


def _procedure_answer(**kwargs: object) -> AuditorProcedureAction:
    return AuditorProcedureAction(
        action="answer", procedure=_procedure_payload(**kwargs), rationale="관측으로 판정한다"
    )


def _convert(procedure: AuditProcedure) -> Finding:
    return auditor._finding_from_procedure(
        procedure, ids=_ISSUED, run_id=RUN_ID, produced_by=_STAMPED
    )


@pytest.mark.parametrize(
    ("version", "declared", "looped"),
    [
        pytest.param("v7", "Finding", False, id="v7-one-shot"),
        pytest.param("v8", "AuditorAction", True, id="v8-finding-envelope"),
        pytest.param("v9", "AuditorAction", True, id="v9-finding-envelope"),
        pytest.param("v10", "AuditorProcedureAction", True, id="v10-procedure-envelope"),
        pytest.param(
            "v11", "AuditorProcedureNullableCweAction", True, id="v11-nullable-cwe-envelope"
        ),
        pytest.param(
            "v12", "AuditorProcedureAuditAlwaysAction", True, id="v12-audit-always-envelope"
        ),
    ],
)
def test_the_places_that_ask_is_this_a_loop_prompt_all_get_the_same_answer(
    version: str, declared: str, looped: bool
) -> None:
    """「이 판은 루프 판인가」를 묻는 세 자리(`_ask_model`의 분기 · `loop_problem` ·
    `tool_hops_in_effect`)가 **한 술어**(`_loop_envelope`)로 같은 답을 낸다 — 저장소 실물 판 넷으로
    건다.

    처음에는 `is AuditorAction` 비교가 세 자리에 흩어져 있었다. 봉투가 하나 늘면(v10의
    `AuditorProcedureAction`) 한 자리가 빠져도 그 판이 한 방 판으로 읽히고, 어느 자리가 빠졌느냐에
    따라 증상이 갈린다: `tool_hops_in_effect`가 빠지면 루프가 도는데 manifest는 「꺼짐」이고,
    `loop_problem`이 빠지면 상한 0의 루프 판이 막히지 않고 한 방 `Finding`을 요청한다. 여기서 둘을
    판마다 대조하고, `_ask_model`의 분기는 아래 루프 run 검사들이 홉 이벤트로 문다."""
    template = load_prompt(Role.AUDITOR, version)
    assert template.output_schema == declared, "전제 — 그 판이 실제로 선언한 봉투다"

    envelope = auditor._loop_envelope(template)

    assert (envelope is not None) is looped
    if looped:
        assert envelope.__name__ == declared, "푼 것이 곧 요청하는 봉투다 — 매핑 표가 없다"
    assert (auditor.loop_problem(template, 0, is_fake=False) is not None) is looped
    assert auditor.tool_hops_in_effect(version, 16) == (16 if looped else 0)


def test_the_allowed_schemas_are_the_one_shot_finding_plus_exactly_the_loop_envelopes() -> None:
    """허용집합 = 한 방 `Finding` + 루프 봉투들 — 그리고 한 방 경로가 보내는 `OUTPUT_SCHEMA`가 **그
    집합에서 루프 봉투를 뺀 유일한 스키마**다.

    `_ask_model`은 루프 봉투가 아니면 `OUTPUT_SCHEMA`(`Finding`)를 보낸다. 허용집합에 비-루프
    스키마가 하나 더 생기면 그 판은 **선언을 버리고 `Finding`을 요청하는** 길이 열린다 — 이 검사가
    그때 빨개져 한 방 경로를 다시 고르게 한다. `AuditProcedure`는 봉투의 payload일 뿐이라 허용집합
    밖이다: 최상위로 요청하면 `lookup`·`probe`·`answer` 없이 감사 결과 하나만 받는 문법이 된다."""
    allowed = set(auditor.ALLOWED_OUTPUT_SCHEMAS)

    # ⏵ 2026-10-09 — 셋째 봉투(v11의 `AuditorProcedureNullableCweAction`, 이슈 #525)가 더해졌다.
    # ⏵ 2026-10-10 — 넷째 봉투(v12의 `AuditorProcedureAuditAlwaysAction`, 이슈 #578)가 더해졌다.
    # ⏵ 2026-10-13 — 다섯째 봉투(v15의 `AuditorProcedureProbeInputAction`, 이슈 #419)가 더해졌다.
    # ⏵ 2026-10-16 — 여섯째 봉투(v18의 `AuditorProcedureProbeInputDiscriminatedAction`, 이슈 #602)가
    # 더해졌다.
    assert allowed == {
        Finding,
        AuditorAction,
        AuditorProcedureAction,
        AuditorProcedureNullableCweAction,
        AuditorProcedureAuditAlwaysAction,
        AuditorProcedureProbeInputAction,
        AuditorProcedureProbeInputDiscriminatedAction,
    }
    assert allowed - set(auditor._LOOP_ENVELOPES) == {auditor.OUTPUT_SCHEMA}
    assert AuditProcedure not in allowed
    assert AuditProcedureNullableCwe not in allowed
    assert AuditProcedureAuditAlways not in allowed
    for payload in ("AuditProcedure", "AuditProcedureNullableCwe", "AuditProcedureAuditAlways"):
        with pytest.raises(PromptError, match="이 역할이 낼 수 없다"):
            auditor.resolve_output_schema(payload)


def test_the_one_shot_sentence_names_every_loop_envelope_and_the_declared_schema() -> None:
    """한 방 판 + 양수 상한의 사유가 **어느 판에서 도는지**를 둘 다 말한다 — `AuditorAction`만
    적으면 v10 판으로 올리라는 안내가 빠진다."""
    problem = auditor.loop_problem(_template("Finding", LOOP_TOOLS), 4, is_fake=False)

    assert problem is not None
    assert "Finding(한 방)을 선언하는데" in problem
    assert (
        "AuditorAction · AuditorProcedureAction · AuditorProcedureNullableCweAction · "
        "AuditorProcedureAuditAlwaysAction · AuditorProcedureProbeInputAction · "
        "AuditorProcedureProbeInputDiscriminatedAction 판에서만 돈다"
        in problem
    )


def test_a_procedure_loop_prompt_with_a_zero_cap_is_refused_in_the_same_words_everywhere(
    ctx: RunContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """v10 봉투를 선언한 판에 상한 0이면 `run`·`doctor`·노드가 **같은 문장**으로 막는다 — 그 문장이
    **실제 선언 이름**(`AuditorProcedureAction`)을 말한다.

    왜 세 자리를 한 문장으로 대조하나. 노드와 cli가 따로 판정하면 한쪽만 통과하는 설정이 생겨 첫
    Auditor 호출(= 그 앞 단계에 쓴 GPU 시간 뒤)에야 선다. 새 봉투가 한 자리에서 `Finding` 선언으로
    읽히면 상한 0이 **조용히 한 방으로** 간다 — 선언이 `AuditorProcedureAction`인데 `Finding`을
    요청하는 길이다. 그것이 안 열렸다는 증거가 **모델을 안 불렀다**는 마지막 줄이다."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    _arm(ctx, monkeypatch, tmp_path, hops=0, output_schema="AuditorProcedureAction")
    _patch_cli_config(monkeypatch, hops=0, loop_seat=True)

    problem = cli._auditor_loop_problem(ctx.config)
    assert problem is not None
    declaration = f"auditor/{LOOP_VERSION}은 output_schema로 AuditorProcedureAction(도구 루프)을"
    assert declaration in problem
    code = cli.main([*_RUN_ARGS, "--artifacts-root", str(tmp_path / "arts")])
    assert code == 2 and problem in capsys.readouterr().err
    checks = cli.doctor("dev_fake", artifacts_root=tmp_path / "doc")
    assert problem in [c.message for c in checks if c.severity is cli.Severity.ERROR]
    with pytest.raises(PromptError) as raised:
        auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)
    assert str(raised.value) == problem
    assert _auditor_calls(ctx) == [], "불일치인데 모델을 불렀다 — GPU 시간이 샌다"


# --- 옮겨 담기: `AuditProcedure` → `Finding` --------------------------------------------


@pytest.mark.parametrize("name", _HARNESS_ONLY_FIELDS)
def test_the_model_cannot_supply_the_fields_the_harness_issues(name: str) -> None:
    """발급 ID·`run_id`·`state`·`missing_evidence`·`produced_by`는 v10의 모델이 **낼 수 없다** —
    스키마에 필드가 없고 실으면 거절한다. 아래 변환 검사들이 「모델 값을 안 믿는다」 대신 「모델
    값이 올 길이 없다」를 단언해도 되는 근거가 이 검사다. 이 필드가 `AuditProcedure`에 생기면 하네스
    값과 모델 값이 경합하는 길이 열리므로 여기서 먼저 빨개진다."""
    assert name not in AuditProcedure.model_fields
    with pytest.raises(ValidationError):
        AuditProcedure.model_validate({**_procedure_payload(), name: "model_made_this"})


def test_the_conversion_fills_the_issued_ids_state_and_provenance_from_the_harness() -> None:
    """변환된 `Finding`의 `finding_id`·`candidate_id`는 발급 `ids`, `run_id`는 run, `state`는 F-1의
    `hypothesized`, `produced_by`는 하네스가 만든 값이다. 뒤 `stamp_issued`가 **같은 값으로** 덮어도
    무해하다는 것도 여기서 본다 — 이미 같아서 `issued_override`가 날 이유가 없다."""
    finding = _convert(_procedure())

    assert finding.finding_id == _ISSUED["finding_id"]
    assert finding.candidate_id == _ISSUED["candidate_id"]
    assert finding.run_id == RUN_ID
    assert finding.state is FindingState.HYPOTHESIZED
    assert finding.produced_by == _STAMPED


def test_the_conversion_moves_audit_path_to_finding_path_and_empties_it_for_no_signal() -> None:
    """`audit.path` → `Finding.path`. **이 값이 제일 중요하다** — `gate.py`의 `p2`가 경로 양 끝을
    요구하고, 경로가 비면 Critic은 「경로 없음」, PoV는 단계 없음, 보고서는 제목 대체로 떨어진다. 안
    옮기면 v10의 모든 Finding이 그 셋에서 조용히 떨어진다(`path`가 required라 파싱은 통과한다).
    `no_signal`이면 `audit`가 `null`이라 `path`는 빈 리스트다."""
    signal = _procedure()
    finding = _convert(signal)

    assert signal.audit is not None and signal.audit.path, "전제 — 옮길 경로가 있다"
    assert [(s.step.value, s.symbol) for s in finding.path] == [
        ("source", "main"),
        ("sink", "memcpy"),
    ]
    assert list(finding.path) == list(signal.audit.path)

    quiet = _convert(_procedure(trigger_status="no_signal"))
    assert quiet.path == [] and quiet.audit is None


def test_the_conversion_carries_the_audit_block_and_the_judgment_fields() -> None:
    """`audit` 블록이 `Finding.audit`에 실린다 — 안 실으면 그 블록은 버려진다(trace에 `raw_text`가
    안 남는다). 판정 필드(`trigger_status`·`confidence`·`cwe`·`title`·`rca_draft`·`evidence_refs`·
    `repro_spec`)도 같은 이름으로 간다. 저장소가 돌려주는 `model_dump()`에도 블록이 있는지까지 본다
    (`Finding.audit`는 요청 스키마에서만 빠지고 직렬화에는 선다)."""
    procedure = _procedure(evidence_refs=("evidence_0001",), repro_origin="model")

    finding = _convert(procedure)

    assert finding.audit == procedure.audit and finding.audit is not None
    assert finding.model_dump()["audit"]["refutation"]["result"] == "defeated"
    assert (finding.trigger_status, finding.confidence, finding.cwe, finding.title) == (
        "triggerable",
        0.8,
        "CWE-787",
        "out-of-bounds write",
    )
    assert finding.rca_draft == procedure.rca_draft
    assert finding.evidence_refs == ["evidence_0001"]
    assert finding.repro_spec == procedure.repro_spec and finding.repro_spec is not None


# `(refutation.result, trigger_status)` → 발급 `missing_id`가 붙는가. **손으로 적은 표다 — 구현의
# 조건을 다시 쓴 식이 아니다.** 기대의 출처 둘:
#   · `result == decisive_code_missing` → 붙는다.
#   · `trigger_status == reachability_unknown` → `result`가 무엇이든 붙는다.
#   ⏵ 2026-10-06 — **산문이 둘 다 약속한다.** 처음엔 앞쪽만 적혀 있어 하네스가 「더 넓게」 막는
#   쪽이었고, 적대적 검토가 그 어긋남을 짚어 산문을 코드에 맞췄다(v10 prefix의 §Identifiers and
#   evidence references). 넓은 쪽인 이유는 그대로다(`_finding_from_procedure`의 🔴 주석 —
#   fail-closed): 게이트가
#     `trigger_status`를 안 읽으므로 「모르겠다」로 라벨한 Finding이 안 막히면 E2로 열린다.
# 나머지 아홉 칸(`result`가 결정적이지 않고 상태가 판정을 말하는 칸)은 안 붙는다.
# 스무 칸 중 `no_signal` 열 넷은 이 표에 올 수 없다 — `audit`가 null이라야 하는데 그러면 `result`가
# 없다(스키마가 둘을 거절한다). 그 넷의 accounting은 아래 표 검사가 맡는다.
_MISSING_ID_ATTACHED = {
    ("defeated", "triggerable"): False,
    ("defeated", "guarded"): False,
    ("defeated", "no_reach"): False,
    ("defeated", "reachability_unknown"): True,
    ("holds", "triggerable"): False,
    ("holds", "guarded"): False,
    ("holds", "no_reach"): False,
    ("holds", "reachability_unknown"): True,
    ("path_not_executable", "triggerable"): False,
    ("path_not_executable", "guarded"): False,
    ("path_not_executable", "no_reach"): False,
    ("path_not_executable", "reachability_unknown"): True,
    ("decisive_code_missing", "triggerable"): True,
    ("decisive_code_missing", "guarded"): True,
    ("decisive_code_missing", "no_reach"): True,
    ("decisive_code_missing", "reachability_unknown"): True,
}


@pytest.mark.parametrize(
    ("result", "status", "attached"),
    [
        pytest.param(result, status, attached, id=f"{result}-{status}")
        for (result, status), attached in _MISSING_ID_ATTACHED.items()
    ],
)
def test_the_issued_missing_id_is_attached_when_the_code_is_missing_or_the_status_is_unknown(
    result: str, status: str, attached: bool
) -> None:
    """발급 `missing_id`는 `result`가 `decisive_code_missing`이거나 `trigger_status`가
    `reachability_unknown`일 **때** 붙는다 — 둘 중 하나면 붙고 둘 다 아니면 안 붙는다. 일곱 칸이
    붙고 아홉 칸이 안 붙는다(위 `_MISSING_ID_ATTACHED`, 칸마다 손으로 적은 기대).

    왜 이 칸이 모순 조합까지 있나. 뒤 `_materialize_missing`이 그 ID마다 blocking
    `MissingEvidence`를 만들어 E2를 막는데, 스키마는 `result` ↔ `trigger_status` 대응을 안 싣는다
    (`AuditProcedure`). 조건을 `result` 한쪽으로 줄이면(처음 판) `reachability_unknown`인데
    `result`가 다른 칸 셋이 막히지 않고 풀린다. 두 조건이 다 참인 칸(`decisive_code_missing` /
    `reachability_unknown`)에서도 ID는 **하나**다."""
    finding = _convert(_procedure(trigger_status=status, result=result))

    assert finding.missing_evidence == ([_ISSUED["missing_id"]] if attached else [])


def test_the_missing_id_table_accounts_for_every_result_status_cell_the_schema_allows() -> None:
    """표가 스키마의 `result` × `trigger_status` 칸을 **빠짐없이** 든다 — 목록은 스키마에서 뽑는다.

    값이 하나 늘면(새 `result`·새 상태) 그 칸이 표에 없어 여기서 먼저 빨개진다. 표에서 빠지는
    `no_signal` 열은 `audit`가 null이어야 하는 상태라 **`result`와 함께 올 수 없다** — 그 넷이
    실제로 거절되는지를 칸마다 본다(스키마 쪽 검사는 `test_auditor_procedure_schema.py`의
    `test_no_signal_with_an_audit_block_is_refused`가 한 칸으로 문다)."""
    results = get_args(AuditRefutation.model_fields["result"].annotation)
    statuses = get_args(AuditProcedure.model_fields["trigger_status"].annotation)

    assert results and statuses, "전제 — Literal에서 값을 못 뽑았다. 검사가 비어 있다"
    assert set(_MISSING_ID_ATTACHED) == {
        (result, status) for result in results for status in statuses if status != "no_signal"
    }
    for result in results:
        with pytest.raises(ValidationError):
            AuditProcedure.model_validate(
                {**_procedure_payload(result=result), "trigger_status": "no_signal"}
            )


def test_a_no_signal_answer_attaches_no_missing_id() -> None:
    """`no_signal`(`audit` 없음 · `result` 없는 칸)은 ID를 안 붙인다 — 스무 칸 중 `result`가 없는
    다섯째 열이다. 「모르겠다」(`reachability_unknown`)와 「신호 없음」은 다른 사실이다: 앞은 판정에
    필요한 코드가 없어 막히고, 뒤는 판정할 후보가 아니라 막을 것이 없다."""
    assert _convert(_procedure(trigger_status="no_signal")).missing_evidence == []


def _procedure_prefixes() -> dict[str, str]:
    """`output_schema`가 `AuditorProcedureAction`인 모든 Auditor 판의 산문 — 판 이름을 이 파일이
    쥐지 않고 **meta 선언에서 묻는다**(v11이 같은 봉투를 선언해도 같이 걸린다)."""
    found = {}
    for path in sorted((PROMPTS_DIR / "auditor").iterdir()):
        template = load_prompt(Role.AUDITOR, path.name)
        if template.output_schema == AuditorProcedureAction.__name__:
            found[path.name] = " ".join(template.prefix.split())
    return found


def test_the_procedure_prose_names_both_conditions_for_attaching_the_missing_id() -> None:
    """산문이 「발급 `missing_id`를 언제 붙이는가」에 **두 조건을 다** 든다 — 하나만 적으면 산문이
    코드보다 좁다. 기대는 위 `_MISSING_ID_ATTACHED` 표에서 뽑는다(산문에서 베끼지 않는다).

    **사고 형태 —** 독립 검토의 변이 실험: 산문의 「or `trigger_status` is `reachability_unknown`」
    구를 지워도 640개 검사가 전부 초록이었다. 처음 판의 산문은 앞 조건만 약속했고(코드는 더 넓게
    막았다) 그 어긋남을 고친 것이 이 구인데, 고친 것이 아무 검사에도 안 걸려 있었다. 모델은 산문을
    믿고 `result`와 `trigger_status`가 갈린 답에서 그 ID가 안 붙는다고 읽는다.

    표에서 **어느 칸이 항상 붙는가**를 뽑는다 — 한 `result`의 모든 상태에서 붙으면 그 `result`가
    조건이고, 한 상태의 모든 `result`에서 붙으면 그 상태가 조건이다. 그 둘의 **합집합**이 표 전체와
    같다는 것까지 본다(조건 하나가 늘면 합집합이 안 맞아 여기서 빨개진다). 산문의 「`필드` is `값`」
    짝이 그 집합과 같고 「or」로 잇는다(「and」면 교집합이라 아홉 칸이 거짓이다).

    ⚠ 철자 핀이다 — 산문이 같은 조건을 다른 문형으로 적으면 이 검사의 정규식을 맞춘다."""
    results = get_args(AuditRefutation.model_fields["result"].annotation)
    statuses = [
        status
        for status in get_args(AuditProcedure.model_fields["trigger_status"].annotation)
        if status != "no_signal"
    ]
    by_result = {r for r in results if all(_MISSING_ID_ATTACHED[(r, s)] for s in statuses)}
    by_status = {s for s in statuses if all(_MISSING_ID_ATTACHED[(r, s)] for r in results)}
    assert by_result and by_status, "전제 — 표에서 조건을 못 뽑았다. 검사가 비어 있다"
    for (result, status), attached in _MISSING_ID_ATTACHED.items():
        assert attached == (result in by_result or status in by_status), (result, status)
    # 산문이 부르는 두 필드 경로가 실재한다 — `audit` 블록의 `refutation.result` · 바깥의
    # `trigger_status`.
    assert {"audit", "trigger_status"} <= set(AuditProcedure.model_fields)
    assert "refutation" in AuditBlock.model_fields and "result" in AuditRefutation.model_fields
    result_path = "audit.refutation.result"
    expected = {(result_path, r) for r in by_result} | {("trigger_status", s) for s in by_status}

    prefixes = _procedure_prefixes()
    assert prefixes, "전제 — 봉투를 선언한 판이 없으면 이 검사가 아무것도 안 묻는다"
    for version, prefix in prefixes.items():
        found = re.search(r"attaches the pre-issued missing-evidence ID when ([^)]*)\)", prefix)
        assert found, f"{version}: 산문이 발급 ID를 붙이는 조건을 안 적는다"
        clause = found.group(1)
        stated = re.findall(r"`([\w.]+)` is `(\w+)`", clause)
        assert sorted(stated) == sorted(expected), (
            f"{version}: 산문 {stated} ≠ 표 {sorted(expected)}"
        )
        assert clause.count(" or ") == len(expected) - 1 and " and " not in clause, version


def test_a_finding_envelope_answer_never_keeps_an_audit_block_the_model_filled(
    ctx: RunContext,
) -> None:
    """v8·v9 봉투(`AuditorAction`)의 `answer`가 `Finding`이고, 그 분기가 모델이 채운 `audit`를
    **비운다** — `Finding.audit`은 하네스 소유 필드다.

    `audit`는 `SkipJsonSchema`라 요청 스키마에 없지만 파서는 받는다(아래 전제 둘). 문법을 강제하지
    않는 백엔드에서 모델이 그 자리를 채워 낼 수 있고, 안 비우면 그 블록이 저장되고 **v10 계열
    처리가 그 값을 믿는다**: `_materialize_missing`이 `audit`의 유무로 근거 문구를 고른다. 이
    조건(`"audit": None`)으로 되돌리면 이 검사가 죽는다."""
    schema = AuditorAction.model_json_schema()
    assert "audit" not in schema["$defs"]["Finding"]["properties"], "전제 — 요청에 안 보인다"
    action = AuditorAction(
        action="answer",
        finding={**_finding_payload(), "audit": _audit_block_payload("holds")},
        rationale="판정한다",
    )
    assert action.answer is not None and action.answer.audit is not None, "전제 — 파서는 받는다"

    finding = auditor._finding_from(ctx, action.answer, ids=_ISSUED, produced_by=_STAMPED)

    assert finding.audit is None
    assert finding.model_dump()["audit"] is None


def test_a_one_shot_answer_never_keeps_an_audit_block_the_model_filled(ctx: RunContext) -> None:
    """**한 방 경로도** 모델이 채운 `audit`를 비운다 — `_ask_model`의 마지막 줄(`finding.model_copy(
    update={"audit": None})`)이다. 루프 쪽 `_finding_from`은 위 검사가 이미 문다. 두 경로를 같은
    규율로 두려는 것이라(그 주석) 한 방 쪽만 핀이 없으면 되돌려도 초록이다.

    `audit`는 `SkipJsonSchema`라 `Finding`을 요청하는 한 방 판의 스키마에 안 보이지만 파서는
    받는다 — 문법을 강제하지 않는 백엔드에서 모델이 그 자리를 채울 수 있다. 안 비우면 그 블록이
    저장되고 `_materialize_missing`이 `audit`의 유무로 근거 문구를 고르므로 **한 방 판의 run이
    v10 문구로 저장된다**(판 간 집계가 섞인다). 그래서 단언은 둘이다 — 저장된 Finding의 `audit`가
    비었다 + 막는 ID의 문구쌍이 한 방 판의 것이다. 한 방 경로를 탔다는 것은 모델 호출의
    `schema`가 `Finding`이라는 것으로 본다(`dev_fake`의 Auditor 좌석은 한 방 판이다 — 상한을 0으로
    두어 루프 판 가정이 끼지 않게 한다)."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    ctx.config = ctx.config.transition(
        thresholds=ctx.config.thresholds.transition(auditor_tool_hops=0)
    )
    answer = Finding.model_validate(
        {
            **_finding_payload(),
            "audit": _audit_block_payload("decisive_code_missing"),
            "missing_evidence": ["mev_the_model_wrote_this"],
        }
    )
    assert answer.audit is not None, "전제 — 파서는 모델이 채운 `audit`를 받는다"
    ctx.models = _ScriptedModel([answer])

    finding, missings = auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    assert [c.data["schema"] for c in _auditor_calls(ctx)] == ["Finding"], "한 방 경로가 아니다"
    assert finding.audit is None
    assert ctx.store.get("finding", finding.finding_id).audit is None
    assert [(m.need, m.how_to_obtain) for m in missings] == [(_NEED_ONE_SHOT, _HOW_ONE_SHOT)]


# --- 옮겨 담기가 안 터진다: `AuditProcedure`가 받는 값은 전부 `Finding`이 된다 -------------------

# JSON 스키마에서 **값의 제약**을 나르는 키 — `title`·`default`는 값을 안 거른다. `$ref`는 클래스
# 자체다.
_CONSTRAINT_KEYS = (
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "pattern",
    "minItems",
    "maxItems",
    "$ref",
)


def _constraints(node: dict, prefix: str = "") -> set[tuple[str, object]]:
    """스키마 한 자리의 제약을 `(경로, 값)` 집합으로 편다 — `items`의 안쪽과 `anyOf`의 가지까지."""
    found = {(prefix + key, node[key]) for key in _CONSTRAINT_KEYS if key in node}
    if "items" in node:
        found |= _constraints(node["items"], prefix + "items.")
    for branch in node.get("anyOf", ()):
        found |= _constraints(branch, prefix)
    return found


# 값이 클래스가 아니라 **자기 자리에서 선언된** 공유 필드 — 두 모델이 각자 제약을 든다.
# 나머지 공유 둘(`audit` · `repro_spec`)은 두 모델이 **같은 클래스**를 써서 한쪽만 느슨해질 수 없다.
_OWN_DECLARED = ("confidence", "cwe", "title", "rca_draft", "evidence_refs", "trigger_status")
# 필드 종류를 안 가리는 경계 값 — 비문자열·빈 값·길이·범위의 양쪽·`NaN`·널. 어느 모델의
# 제약이 느슨해져 이 값 하나가 새면 아래 검사가 그 필드 이름으로 알린다.
_ODD_VALUES = (
    "",
    " ",
    "x",
    "x" * 128,
    "x" * 129,
    "CWE-1",
    "CWE-",
    "cwe-1",
    "1",
    0,
    1,
    -1,
    0.0,
    1.0,
    -0.001,
    1.001,
    float("nan"),
    float("inf"),
    None,
    [],
    [""],
    ["x"],
    ["x" * 129],
)


def test_every_value_the_procedure_accepts_converts_to_a_finding() -> None:
    """**`AuditProcedure`가 받는 값은 전부 `Finding`으로 변환된다** — 변환이 `ValidationError`로
    죽는 값을 스키마가 먼저 거절한다.

    **사고 형태 —** 독립 검토가 되살려도 초록인 스키마 제약을 여섯 찾았다(`confidence`의 범위 ·
    `title`·`rca_draft`의 비어 있지 않음 · `evidence_refs`의 `Identifier` · `AuditPoint.expression`
    · `AuditRefutation.witness`). `AuditProcedure`가 느슨해지면 모델 출력이 **파싱은 통과하고**
    `_finding_from_procedure`의 `Finding(...)`에서 터져 run이 선다 — 스키마가 거절하면 그 답은
    재생성되지만 여기서 터지면 후보가 Finding 없이 남는다(`AuditProcedure` 위 주석).

    둘로 건다. ① **스키마 비교** — 두 모델이 공유하는 필드마다 `Finding`이 거는 제약(범위·길이·
    패턴)을 `AuditProcedure`가 **빠짐없이** 건다(`_constraints`가 JSON 스키마에서 편다).
    `Finding` 쪽만 더 엄격해지는 미래 변경(예: `title`에 `max_length`)도 여기서 잡는다.
    ② **경계 값 실행** — 위 표의 값(`_ODD_VALUES`)을 필드마다 넣어 `AuditProcedure`가 받으면
    변환이 되는지 **돌린다**. 값마다
    기대를 적지 않는 것이 속성 검사의 요점이다. 각 필드에서 `AuditProcedure`가 거절하는 값이 하나
    이상이어야 한다 — 거절이 없으면 이 검사가 아무것도 안 묻는 것이다.

    ⚠ **이 명제가 못 덮는 것 셋** — `rca_draft`(`Finding.rca_draft`가 `str = ""`이라 비어 있어도
    변환이 된다), `AuditPoint.expression`·`AuditRefutation.witness`(`Finding.audit`가 **같은
    클래스**라 한쪽만 느슨해질 수 없다 — ⏵ 2026-10-10 v12 블록이 더해져 「같은 클래스를 멤버로
    든다」다)는 변환을 못 깬다. 그 셋의 「비어 있지 않음」은 아래 검사가
    스키마의 문자열 자리 전부에 거는 것으로 묶는다."""
    shared = set(AuditProcedure.model_fields) & set(Finding.model_fields)
    assert shared == {*_OWN_DECLARED, "audit", "repro_spec"}, (
        f"공유 필드가 바뀌었다({sorted(shared)}) — 새 필드는 위 표에 넣거나 같은 클래스인지 본다"
    )
    same = AuditProcedure.model_fields["repro_spec"], Finding.model_fields["repro_spec"]
    assert same[0].annotation == same[1].annotation, "repro_spec: 두 모델이 다른 클래스를 쓴다"
    # ⏵ 2026-10-10 — `Finding.audit`는 v12 블록도 받아(이슈 #578) 주석이 같지 않다. 명제는 「응답이
    # 싣는 블록 클래스를 `Finding`이 전부 받는다」이므로 그것을 합집합 소속으로 건다 — v10·v11의
    # `AuditBlock`도 v12의 `AuditBlockWithNoSignal`도 `Finding.audit`의 멤버다.
    stored = set(get_args(Finding.model_fields["audit"].annotation))
    for procedure_class in (AuditProcedure, AuditProcedureAuditAlways):
        carried = set(get_args(procedure_class.model_fields["audit"].annotation)) or {
            procedure_class.model_fields["audit"].annotation
        }
        assert carried <= stored, f"{procedure_class.__name__}.audit를 Finding이 못 싣는다"

    finding_props = Finding.model_json_schema()["properties"]
    procedure_props = AuditProcedure.model_json_schema()["properties"]
    # 전제 — 제약을 펴는 코드가 실제로 제약을 편다(못 펴면 아래 비교가 `set() - set()`로 초록이다).
    assert {("minimum", 0.0), ("maximum", 1.0)} <= _constraints(finding_props["confidence"])
    assert ("items.maxLength", 128) in _constraints(finding_props["evidence_refs"])
    looser = {
        name: sorted(map(str, missing))
        for name in _OWN_DECLARED
        if (missing := _constraints(finding_props[name]) - _constraints(procedure_props[name]))
    }
    assert looser == {}, f"`Finding`이 거는 제약을 `AuditProcedure`가 안 건다: {looser}"

    refused: dict[str, int] = dict.fromkeys(_OWN_DECLARED, 0)
    broken: list[str] = []
    for name in _OWN_DECLARED:
        for value in _ODD_VALUES:
            try:
                procedure = AuditProcedure.model_validate({**_procedure_payload(), name: value})
            except ValidationError:
                refused[name] += 1
                continue
            try:
                _convert(procedure)
            except ValidationError:
                broken.append(f"{name}={value!r}")
    assert broken == [], f"`AuditProcedure`는 받는데 `Finding`이 거절하는 값: {broken}"
    assert all(refused.values()), f"거절하는 값이 없는 필드가 있다 — 검사가 비었다: {refused}"


def _free_text_leaves(schema: dict, node: dict, path: str = "") -> Iterator[tuple[str, dict, bool]]:
    """스키마의 **자유 문자열 자리**(`enum`·`pattern`이 없는 `string`)를 `(경로, 자리, 선택인가)`로
    돈다. 선택 = `null`과 `anyOf`로 묶인 자리(`PathStep.note`)라 비어 있어도 합법이다."""
    if "$ref" in node:
        target = schema["$defs"][node["$ref"].rsplit("/", 1)[1]]
        yield from _free_text_leaves(schema, target, path)
    elif "anyOf" in node:
        nullable = any(branch.get("type") == "null" for branch in node["anyOf"])
        for branch in node["anyOf"]:
            for leaf_path, leaf, optional in _free_text_leaves(schema, branch, path):
                yield leaf_path, leaf, optional or (nullable and branch.get("type") == "string")
    elif node.get("type") == "array":
        yield from _free_text_leaves(schema, node["items"], path + "[]")
    elif node.get("type") == "object" or "properties" in node:
        for key, child in node.get("properties", {}).items():
            yield from _free_text_leaves(schema, child, f"{path}.{key}" if path else key)
    elif node.get("type") == "string" and "enum" not in node and "pattern" not in node:
        yield path, node, False


def test_the_procedure_schema_refuses_the_empty_string_in_every_free_text_field() -> None:
    """`AuditProcedure`의 **모든 자유 문자열 자리**가 빈 문자열을 거절한다(`minLength` ≥ 1). 위
    변환 검사가 못 덮는 셋(`rca_draft` · `AuditPoint.expression` · `AuditRefutation.witness`)을
    그 이웃(`title` · `evidence_refs` 항목 · `symbol` · `fact` · `steps` …)과 함께 **스키마에서
    뽑아**
    묶는다 — 자리 목록을 손으로 적지 않아서 문자열 필드가 하나 늘면 같이 걸린다.

    스키마가 빈 문자열을 받으면 모델이 `"expression": ""` 같은 **인용 없는 감사 블록**을 내도
    파싱이 통과한다 — 산문이 요구하는 「SOURCE에서 그대로 옮긴 식」 · 「짧은 구체적 사실」이 비어도
    재생성이 안 걸린다. 선택 자리(`PathStep.note` — 산문이 「채워라」만 말한다)는 뺀다.

    `enum`·`pattern`이 있는 자리는 값 집합이 닫혀 있어 센 대상이 아니다(`cwe`는 위 변환 검사 몫).
    기대하는 자리 일곱이 실제로 뽑혔는지도 본다 — 걷는 코드가 자리를 놓치면 이 검사가 빈 채로
    초록이다."""
    schema = AuditProcedure.model_json_schema()
    leaves = list(_free_text_leaves(schema, schema))
    paths = {path for path, _, _ in leaves}

    assert {
        "title",
        "rca_draft",
        "evidence_refs[]",
        "audit.point.symbol",
        "audit.point.expression",
        "audit.refutation.witness",
        "audit.refutation.missing_information[]",
    } <= paths, f"걷는 코드가 자리를 놓쳤다: {sorted(paths)}"
    empty_ok = sorted(
        path for path, leaf, optional in leaves if not optional and not leaf.get("minLength")
    )
    assert empty_ok == [], f"빈 문자열을 받는 자유 문자열 자리: {empty_ok}"
    optional_paths = [path for path, _, optional in leaves if optional]
    assert optional_paths == ["audit.path[].note"], "선택 자리가 바뀌었다"


# `MissingEvidence`의 두 고정 문구쌍(`need` · `how_to_obtain`) — `need`는 **집계의 키다**: 자유
# 텍스트면 같은 요청이 매번 다르게 쓰여 세지 못한다(`nodes/auditor._NEED` 위 주석). 노드 상수를
# import해 비교하지 않고 **글자로 적는다** — 상수끼리 비교하면 문구가 바뀌어도 초록이라 집계 키가
# 조용히 갈린다. `how_to_obtain`도 판마다 가른다: 한 방·v8·v9가 저장해 온 문구(「index 도구」)는
# 그대로 두고 루프 판(v10)에만 다른 문구를 쓴다 — 루프에는 `index` 도구가 없다(위
# `test_tool_literal_is_the_whitelist_minus_index`). 문구를 일부러 바꾸면 이 네 줄도 같이 고친다
# (그 변경이 과거 run과 문구가 갈리는 지점이다).
_NEED_ONE_SHOT = "대상 경로를 코드만으로 확정할 수 없다: 정적분석 신호 또는 호출 그래프 근거"
_HOW_ONE_SHOT = "2단계 신호 수집을 대상 파일로 다시 실행하거나 index 도구로 호출자를 확인한다"
_NEED_V10 = "판정에 필요한 코드가 SOURCE에 없다 (목록이 있으면 audit.refutation에 있다)"
_HOW_V10 = "모델이 lookup으로 그 코드를 조회하거나 호출 그래프로 호출자를 확인한다"


def _procedure_missing(result: str, information: list[str]) -> AuditProcedure:
    """`reachability_unknown` 답 — `result`와 모델이 적은 「무엇이 없는가」를 정한다."""
    payload = _procedure_payload(trigger_status="reachability_unknown", result=result)
    payload["audit"]["refutation"]["missing_information"] = information
    return AuditProcedure.model_validate(payload)


def test_the_blocking_need_is_one_fixed_sentence_per_answer_shape(ctx: RunContext) -> None:
    """blocking `MissingEvidence`의 `need`·`how_to_obtain`은 답의 **모양**마다 고정 문구 하나씩이다
    — `audit`가 있으면(v10) `_NEED_V10`·`_HOW_V10`, 없으면(v8·v9·한 방) `_NEED_ONE_SHOT`·
    `_HOW_ONE_SHOT`. 모델이 적은 「무엇이 없는가」는 문구에 안 들어가고
    `audit.refutation.missing_information`에 **그대로** 남는다.

    넷을 건다: 모델이 목록을 적은 두 답(서로 다른 목록) · 목록이 빈 `decisive_code_missing` · 목록이
    빈 채 `reachability_unknown`이라 **넓은 쪽으로 막힌** 답(`result`는 `defeated`). 앞의 둘이 같은
    문구를 얻는다는 것이 「자유 텍스트가 아니다」의 증거이고, 뒤의 둘이 문구가 「목록이 있다」고
    단언하지 않는다는 것의 증거다(목록이 비어도 같은 문구가 붙는다). 이 문구의 철자를 바꾸면
    죽는다 — 집계 키가 갈리는 지점이라 일부러 글자로 문다."""
    asked = ["parse_header의 호출자", "BUF_SIZE의 정의"]
    cases = [
        (_procedure_missing("decisive_code_missing", asked), asked),
        (_procedure_missing("decisive_code_missing", ["완전히 다른 사정"]), ["완전히 다른 사정"]),
        (_procedure_missing("decisive_code_missing", []), []),
        (_procedure_missing("defeated", []), []),
    ]

    for procedure, information in cases:
        finding = _convert(procedure)
        (missing,) = auditor._materialize_missing(ctx, finding)

        assert (missing.need, missing.how_to_obtain) == (_NEED_V10, _HOW_V10)
        assert missing.blocking is True
        assert missing.missing_id == _ISSUED["missing_id"]
        assert missing.finding_id == finding.finding_id
        assert finding.audit is not None
        assert finding.audit.refutation.missing_information == information

    plain = Finding.model_validate(
        {**_finding_payload(), "missing_evidence": [_ISSUED["missing_id"]]}
    )
    assert plain.audit is None
    (missing,) = auditor._materialize_missing(ctx, plain)
    assert (missing.need, missing.how_to_obtain) == (_NEED_ONE_SHOT, _HOW_ONE_SHOT)
    assert missing.blocking is True and missing.missing_id == _ISSUED["missing_id"]
    assert _NEED_ONE_SHOT != _NEED_V10, "전제 — 둘은 다른 사실을 적는다(코드 없음 ≠ 신호 없음)"


# --- 관측: `answer` 홉이 싣는 키와 그 상수 --------------------------------------------------


def test_the_status_map_has_exactly_the_refutation_results_the_schema_allows() -> None:
    """`_STATUS_BY_RESULT`의 키 집합이 `AuditRefutation.result`의 Literal 값 집합과 **같다** — 값
    집합을 스키마에서 뽑는다.

    그 맵은 `_answer_observed`가 `[...]`로 조회한다. 스키마에 `result` 값이 하나 늘면 그 홉에서
    `KeyError`가 나고, 그 예외는 모델이 낸 출력이 아니라 **하네스 코드의 것**이다. 반대로 맵에만
    있는 키는 죽은 대응이다. 값 쪽은 `trigger_status`가 실제로 허용하는 상태여야 한다 — 아니면
    그 칸은 항상 「모순」으로 센다. 이 파일이 손으로 든 `_AUDIT_RESULTS`도 같은 집합이어야 한다
    (표 검사들이 그 목록으로 돈다).

    ⏵ 2026-10-10 — v12(이슈 #578)의 `AuditRefutationWithNoSignal.result`가 `no_candidate`를 더해
    맵의 키는 **v12의 값 집합**이고 v10·v11의 집합은 그 부분집합이다. `no_signal`로 가는 키는
    `no_candidate` 하나뿐이다(v12 검증기가 그 쌍을 묶는다)."""
    results = set(get_args(AuditRefutation.model_fields["result"].annotation))
    results_v12 = set(get_args(AuditRefutationWithNoSignal.model_fields["result"].annotation))
    statuses = set(get_args(AuditProcedure.model_fields["trigger_status"].annotation))

    assert results and statuses, "전제 — Literal에서 값을 못 뽑았다. 검사가 비어 있다"
    assert results_v12 - results == {"no_candidate"}
    assert set(auditor._STATUS_BY_RESULT) == results_v12
    assert set(auditor._STATUS_BY_RESULT.values()) <= statuses
    to_no_signal = [k for k, v in auditor._STATUS_BY_RESULT.items() if v == "no_signal"]
    assert to_no_signal == ["no_candidate"]
    assert set(_AUDIT_RESULTS) == results


def test_the_node_counts_the_path_endpoints_the_gate_requires() -> None:
    """노드의 `_PATH_ENDPOINTS`가 게이트의 `_REQUIRED_PATH_STEPS`와 **같은 값**이다.

    노드는 게이트를 import하지 않으려고(의존 방향) 같은 값을 따로 든다. 갈리면
    `path_without_endpoints` 관측이 게이트의 `p2`(`path_ok`)가 떨어뜨리는 Finding과 **다른 것을
    센다** — 그 키는 「게이트가 왜 이 Finding을 떨어뜨렸나」를 합법적 빈 경로와 가르는 값이다.
    노드의 `_PATH_ENDPOINTS` 주석이 이 검사를 약속한다. 선례는 `tests/test_index_graph.py`의
    `test_auditor_section_name_matches_the_adapter`(`CALLERS_SECTION` ↔ `GRAPH_SECTION_KINDS`)다."""
    assert gate._REQUIRED_PATH_STEPS, "전제 — 빈 집합끼리의 일치는 아무것도 안 묻는다"
    assert auditor._PATH_ENDPOINTS == gate._REQUIRED_PATH_STEPS


_CITED = 2  # 인용한 probe 근거 수 — 키가 그 값을 그대로 싣는지 보려고 0도 1도 아닌 값이다
# (`trigger_status`, `result`, `audit.path`의 종류들, 공통 두 키 밖에 생기는 키). 기대는 칸마다
# 손으로 적는다 — 어긴 칸에만 키가 생기고 지킨 칸에는 **없어야** 하므로 전체 dict를 비교한다.
_OBSERVED_CASES = [
    pytest.param(
        "triggerable", "defeated", ("source", "propagate", "sink"), {}, id="consistent-triggerable"
    ),
    pytest.param("guarded", "holds", ("source", "sink"), {}, id="consistent-guarded"),
    pytest.param(
        "no_reach", "path_not_executable", ("source", "sink"), {}, id="consistent-no-reach"
    ),
    # 🔴 합법적인 `reachability_unknown`의 빈 경로 — 이 구분이 `path_without_endpoints`의 존재
    # 이유다.
    pytest.param(
        "reachability_unknown", "decisive_code_missing", (), {}, id="legit-unknown-empty-path"
    ),
    pytest.param(
        "reachability_unknown", "decisive_code_missing", ("source",), {}, id="legit-unknown-partial"
    ),
    pytest.param(
        "triggerable",
        "holds",
        ("source", "sink"),
        {"status_result_mismatch": "triggerable/holds"},
        id="mismatch-only",
    ),
    pytest.param(
        "reachability_unknown",
        "defeated",
        ("source", "sink"),
        {"status_result_mismatch": "reachability_unknown/defeated"},
        id="mismatch-unknown-label-over-a-defeated-result",
    ),
    pytest.param(
        "guarded",
        "decisive_code_missing",
        ("source", "sink"),
        {"status_result_mismatch": "guarded/decisive_code_missing"},
        id="mismatch-missing-code-under-a-guarded-label",
    ),
    pytest.param(
        "triggerable", "defeated", ("source",), {"path_without_endpoints": True}, id="no-sink"
    ),
    pytest.param("guarded", "holds", ("sink",), {"path_without_endpoints": True}, id="no-source"),
    pytest.param(
        "no_reach",
        "path_not_executable",
        (),
        {"path_without_endpoints": True},
        id="empty-path-under-no-reach",
    ),
    pytest.param(
        "triggerable",
        "defeated",
        ("propagate",),
        {"path_without_endpoints": True},
        id="propagate-only",
    ),
    pytest.param(
        "triggerable",
        "holds",
        (),
        {"status_result_mismatch": "triggerable/holds", "path_without_endpoints": True},
        id="both-contradictions",
    ),
]


@pytest.mark.parametrize(("status", "result", "steps", "extra"), _OBSERVED_CASES)
def test_the_answer_hop_counts_the_v10_contradictions_and_only_when_they_happen(
    status: str, result: str, steps: tuple[str, ...], extra: dict
) -> None:
    """v10 `answer` 홉의 관측 — `trigger_status`는 **매번** 실리고(모순 비율의 분모다),
    `status_result_mismatch`·`path_without_endpoints`는 그 모순이 있을 **때만** 생긴다.

    - `status_result_mismatch` — `result`가 가리키는 상태(`_STATUS_BY_RESULT`)와 `trigger_status`가
      다를 때이고 값은 `"<trigger_status>/<result>"`다.
    - `path_without_endpoints` — 상태가 경로를 요구하는데(`_PATH_REQUIRED_STATUSES`) `source`·
      `sink` 양 끝이 없을 때다. 🔴 **합법적인 `reachability_unknown`의 빈·부분 경로에는 없어야
      한다** — 게이트의 강등은 그 둘을 trace에서 구분하지 못해 규칙 위반을 셀 수 없었다.

    기대의 출처는 v10 prefix의 「4. Keep fields consistent」(`result` ↔ `trigger_status` 대응 ·
    경로는 `reachability_unknown`에서만 비어도 된다). 전체 dict를 비교하므로 안 어긴 칸에 키가
    **새는** 것도 잡는다. 이 조건(`!=` · `_PATH_REQUIRED_STATUSES` 소속)을 뒤집으면 죽는다."""
    procedure = _procedure(trigger_status=status, result=result, steps=steps)

    assert auditor._answer_observed(procedure, cited=_CITED) == {
        "cited_probe_evidence": _CITED,
        "trigger_status": status,
        **extra,
    }


def test_a_no_signal_answer_carries_the_status_and_no_contradiction_key() -> None:
    """`no_signal`(`audit is None`)은 `trigger_status`만 싣는다 — 비교할 `result`도 경로도 없다.
    `audit`가 null이라 `audit.refutation`을 읽으면 `AttributeError`인 자리다."""
    observed = auditor._answer_observed(_procedure(trigger_status="no_signal"), cited=_CITED)

    assert observed == {"cited_probe_evidence": _CITED, "trigger_status": "no_signal"}


@pytest.mark.parametrize(
    ("trigger_status", "expected"),
    [
        pytest.param(
            "triggerable",
            {"cited_probe_evidence": _CITED, "trigger_status": "triggerable"},
            id="status-set",
        ),
        pytest.param(None, {"cited_probe_evidence": _CITED}, id="status-unset"),
    ],
)
def test_a_finding_answer_gets_no_v10_contradiction_key(
    trigger_status: str | None, expected: dict
) -> None:
    """v8·v9 봉투(`Finding` 답)는 `trigger_status`를 **있을 때** 싣고(`str | None`이라 안 낸 판은
    키가 없다) v10 전용 키(`status_result_mismatch` · `path_without_endpoints`)는 **절대** 안
    싣는다.

    입력이 일부러 v10 규칙을 어긴다 — 경로가 비었고 `audit`가 `holds`인데 상태는 `triggerable`이다.
    `Finding`을 v10 답처럼 읽으면 두 키가 다 생기는 칸이다. 모델이 `Finding.audit`를 채워 냈을
    때(`_finding_from`이 비우기 **전**의 값이다 — 관측은 raw 답을 본다)도 이 값은 하네스 몫이
    아니다. 그 판들의 trace에 v10 계약의 모순 비율이 섞이면 판 간 비교가 오염된다."""
    finding = Finding.model_validate(
        {
            **_finding_payload(),
            "path": [],
            "audit": _audit_block_payload("holds"),
            "trigger_status": trigger_status,
        }
    )

    assert auditor._answer_observed(finding, cited=_CITED) == expected


def test_the_answer_property_takes_the_payload_from_either_envelope() -> None:
    """봉투의 `answer`가 두 봉투의 `answer` 액션에서 최종 답을(`finding` ↔ `procedure`), 조사
    액션에서는 `None`을 돌려준다 — 읽는 쪽이 봉투 목록을 모른다.

    **예전에는 노드가 비공개 `_PAYLOAD_FIELD`를 `getattr`로 읽었다**(적대적 검토 2026-10-06).
    그쪽은 반환이 `Any`라 아무도 타입을 안 봤고, 그 이름이 **엉뚱한 실재 필드**를 가리키면 조용히
    그 값을 돌려줬다 — `rationale`을 가리키면 `answer`가 `None` 대신 그 문자열이 된다.
    아래 형제 검사가 그 위반을 **클래스 정의 시점에** 세우는 가드를 문다."""
    assert isinstance(_answer_action().answer, Finding)
    assert isinstance(_procedure_answer().answer, AuditProcedure)
    request = RequestedContext(kind="function", target="x", purpose="읽는다", request_code="L_x")
    for investigation in (
        _lookup_action("parse_header"),
        _probe_action("semgrep"),
        AuditorProcedureAction(action="probe", tool="semgrep", rationale="도구로 본다"),
        AuditorProcedureAction(action="lookup", requests=[request], rationale="더 본다"),
    ):
        assert investigation.answer is None


@pytest.mark.parametrize("payload_field", ["rationale", "procedure", None])
def test_an_envelope_whose_payload_name_is_not_its_field_fails_at_class_definition(
    payload_field: str | None,
) -> None:
    """`_PAYLOAD_FIELD`가 그 봉투의 **실재하는 필드**가 아니면 클래스 정의가 선다.

    🔴 **미루면 그 위반이 첫 인스턴스화의 `AttributeError`가 되고, 노드의 모델 호출 경로에서는
    그것이 `SCHEMA_PARSE_FAILED`로 적힌다** — 코드 결함이 **모델 파싱 실패율로 둔갑한다**.
    세 변이를 문다: 실재하지만 payload가 아닌 필드(`rationale` — 조용히 그 문자열을 돌려준다) ·
    이 봉투에 없는 다른 봉투의 필드(`procedure`) · 미정의(`None`)."""
    namespace: dict[str, object] = {
        "__annotations__": {"finding": Finding | None},
        "finding": None,
    }
    if payload_field is not None:
        namespace["_PAYLOAD_FIELD"] = payload_field

    with pytest.raises(TypeError, match="_PAYLOAD_FIELD"):
        type("_BrokenEnvelope", (auditor_schemas._AuditorEnvelope,), namespace)


# --- 루프 run: 봉투를 요청하고, 옮겨 담아 저장한다 -----------------------------------------


def test_a_procedure_loop_answer_is_stored_as_a_finding_the_harness_stamped(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """v10 봉투로 도는 run — 모델이 낸 `AuditProcedure`가 **하네스가 발급·도장한 `Finding`으로**
    저장되고 루프 뒤 흐름(`_materialize_missing` · 저장)이 종전 그대로 돈다.

    본 것: 요청한 스키마가 선언한 봉투(`AuditorProcedureAction`)다 · 발급 ID가 저장소의 행과 같고
    후보를 가리킨다 · `audit` 블록과 경로가 저장된 Finding에 있다 · `produced_by`는 실제 호출 모델
    (`scripted`)과 판이다 · `decisive_code_missing`이라 blocking `MissingEvidence`가 **발급 ID로**
    하나 생겼고 그 문구는 v10의 고정 문구쌍이다 · 모델이 적은 「무엇이 없는가」가 저장된 Finding의
    `audit.refutation.missing_information`에 **그대로** 있다 · 모델이 `harness`라 쓴
    `repro_spec.origin`은 `model`로 덮였다(`_ensure_repro_origin`을 변환이 우회하지 않는다) ·
    `issued_override`가 0건이다(모델은 그 값을 쓰지 않았다)."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    _arm(ctx, monkeypatch, tmp_path, hops=4, output_schema="AuditorProcedureAction")
    ctx.models = _ScriptedModel(
        [
            _procedure_answer(
                trigger_status="reachability_unknown",
                result="decisive_code_missing",
                repro_origin="harness",
            )
        ]
    )

    finding, missings = auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    assert ctx.models.schemas == [AuditorProcedureAction]
    assert ctx.store.get("finding", finding.finding_id) == finding
    assert finding.candidate_id == candidate.candidate_id
    assert finding.state is FindingState.HYPOTHESIZED
    assert finding.audit is not None and finding.path == list(finding.audit.path) != []
    assert finding.produced_by.model == "scripted"
    assert finding.produced_by.prompt_version == LOOP_VERSION
    assert [(m.missing_id, m.finding_id, m.blocking) for m in missings] == [
        (finding.missing_evidence[0], finding.finding_id, True)
    ]
    assert [(m.need, m.how_to_obtain) for m in missings] == [(_NEED_V10, _HOW_V10)]
    assert finding.audit.refutation.missing_information == ["parse_header의 호출자"]
    assert finding.repro_spec is not None and finding.repro_spec.origin.value == "model"
    assert _events(ctx, EventKind.ISSUED_OVERRIDE) == []
    assert [h.data["action"] for h in _hops(ctx)] == ["answer"]


@pytest.mark.parametrize(
    ("status", "result", "steps", "extra"),
    [
        pytest.param(
            "triggerable",
            "holds",
            (),
            {"status_result_mismatch": "triggerable/holds", "path_without_endpoints": True},
            id="both-contradictions",
        ),
        pytest.param("reachability_unknown", "decisive_code_missing", (), {}, id="legit-unknown"),
    ],
)
def test_the_answer_hop_event_carries_the_contradiction_keys_the_loop_observed(
    ctx: RunContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    status: str,
    result: str,
    steps: tuple[str, ...],
    extra: dict,
) -> None:
    """`_answer_observed`의 값이 **실제 `answer` 홉 이벤트에 실린다** — 함수 단위 검사(위
    `_OBSERVED_CASES`)가 못 보는 배선이다(`_emit_hop`에 `observed`를 안 넘기면 함수는 멀쩡한데
    trace에는 아무것도 안 남는다).

    두 칸: 필드 둘이 다 어긋난 답은 키 둘이 다 생기고, 합법적인 `reachability_unknown`의 빈 경로는
    `trigger_status`만 실린다. 조사 홉(`lookup`·`probe`)은 이 키를 안 싣는다 — 모순 비율의 분모는
    **`answer` 홉**이라 다른 홉이 싣으면 분모가 부푼다(아래 Fake run 검사가 그 부재를 문다)."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    _arm(ctx, monkeypatch, tmp_path, hops=4, output_schema="AuditorProcedureAction")
    ctx.models = _ScriptedModel(
        [_procedure_answer(trigger_status=status, result=result, steps=steps)]
    )

    auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    (hop,) = _hops(ctx)
    keys = {"trigger_status", "status_result_mismatch", "path_without_endpoints"}
    assert {key: value for key, value in hop.data.items() if key in keys} == {
        "trigger_status": status,
        **extra,
    }


def test_the_finding_envelope_answer_also_gets_the_harness_produced_by(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """v8·v9 봉투(`AuditorAction`)의 `answer`가 싣는 `Finding`의 `produced_by`도 하네스가 찍는다.

    `RunContext.call_model`의 도장은 응답 **루트**에 `produced_by`가 있을 때만 돈다 — 루프 봉투는
    루트에 없어서, 안 찍으면 안에 든 `Finding`의 `produced_by`는 모델이 쓴 대로(`ChatGPT` 같은 오염
    값)다. v8·v9 prefix가 「하네스가 덮는다」고 적는다."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    _arm(ctx, monkeypatch, tmp_path, hops=4)
    payload = _finding_payload()
    payload["produced_by"] = {"role": "auditor", "model": "ChatGPT", "prompt_version": "v99"}
    ctx.models = _ScriptedModel(
        [AuditorAction(action="answer", finding=payload, rationale="판정한다")]
    )

    finding, _ = auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    assert finding.produced_by.model == "scripted"
    assert finding.produced_by.prompt_version == LOOP_VERSION


def test_a_finding_envelope_run_blocks_with_the_one_shot_sentences_and_no_audit_block(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """v8·v9 봉투의 run — 모델이 `audit`를 채우고 `missing_evidence`에 **자기 ID**를 쓴 `Finding`
    답이 저장되면 `audit`는 비고(`_finding_from`), 막는 ID는 발급 `missing_id` 하나로 정규화되고,
    그 blocking `MissingEvidence`가 **한 방 판의 고정 문구쌍**을 든다.

    `_materialize_missing`이 `audit`의 유무로 문구를 고르므로 둘은 한 사슬이다: `audit`를 안
    비우면 v8·v9 run의 근거가 v10 문구(「코드가 SOURCE에 없다」)로 저장돼 판 간 집계가 섞인다."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    _arm(ctx, monkeypatch, tmp_path, hops=4)
    payload = {
        **_finding_payload(),
        "audit": _audit_block_payload("decisive_code_missing"),
        "missing_evidence": ["mev_the_model_wrote_this"],
    }
    ctx.models = _ScriptedModel(
        [AuditorAction(action="answer", finding=payload, rationale="판정한다")]
    )

    finding, missings = auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    assert ctx.store.get("finding", finding.finding_id).audit is None
    assert len(missings) == 1 and missings[0].blocking is True
    assert missings[0].missing_id == finding.missing_evidence[0] != "mev_the_model_wrote_this"
    assert [(m.need, m.how_to_obtain) for m in missings] == [(_NEED_ONE_SHOT, _HOW_ONE_SHOT)]


@pytest.mark.parametrize(
    ("keep_static_signal", "result", "status", "blocked"),
    [
        pytest.param(True, "defeated", "triggerable", 0, id="static-signal"),
        pytest.param(
            False, "decisive_code_missing", "reachability_unknown", 1, id="no-static-signal"
        ),
    ],
)
def test_the_fake_model_runs_a_procedure_loop_end_to_end(
    ctx: RunContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    keep_static_signal: bool,
    result: str,
    status: str,
    blocked: int,
) -> None:
    """Fake가 v10 봉투로 `lookup → probe → answer`를 끝까지 낸다 — 빌더가 없으면 `FakeModelError`다.

    Fake의 `answer`가 `_build_finding`과 같은 판정을 낸다는 것을 **두 갈래 다** 명시 기대로 본다:
    정적 신호가 있는 후보는 `defeated` · `triggerable`이고 막는 ID가 없다(0건). 신호를 사실
    블록에서 뺀 같은 후보는 `decisive_code_missing` · `reachability_unknown`이라 발급
    `missing_id`가 붙고 blocking `MissingEvidence`가 v10 문구쌍으로 하나 생긴다(1건). 변환 → 저장 →
    `MissingEvidence` 길이 Fake run에서도 선다.

    처음 판은 `result`에서 기대를 **계산했고**(`== int(missing)`) 이 후보가 신호를 가져 한 갈래만
    돌았다 — Fake가 무엇을 내도 초록이었다. 기대는 위 파라미터에 값으로 있다(Fake의 결정적 출력
    — `_triage`가 이 후보에 정적 신호 둘을 주고, 그것을 거르면 그래프·빌드 근거만 남는다)."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    assert any(e.kind is EvidenceKind.STATIC and e.ok for e in evidences), "전제 — 신호가 있다"
    if not keep_static_signal:
        evidences = tuple(e for e in evidences if e.kind is not EvidenceKind.STATIC)
    _arm(ctx, monkeypatch, tmp_path, hops=4, output_schema="AuditorProcedureAction")

    finding, missings = auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    hops = _hops(ctx)
    assert [h.data["action"] for h in hops] == ["lookup", "probe", "answer"]
    assert {c.data["schema"] for c in _auditor_calls(ctx)} == {"AuditorProcedureAction"}
    assert finding.audit is not None and finding.path
    assert (finding.audit.refutation.result, finding.trigger_status) == (result, status)
    assert len(finding.missing_evidence) == len(missings) == blocked
    assert [(m.need, m.how_to_obtain) for m in missings] == [(_NEED_V10, _HOW_V10)] * blocked
    assert hops[-1].data["trigger_status"] == status
    contradiction_keys = {"trigger_status", "status_result_mismatch", "path_without_endpoints"}
    assert not [h for h in hops[:-1] if contradiction_keys & set(h.data)]


def test_the_fake_builder_table_holds_every_loop_envelope() -> None:
    """Fake의 빌더 표가 노드가 아는 **모든 루프 봉투**를 든다 — 빠지면 그 봉투를 고르는 판으로 도는
    Fake run이 `FakeModelError`(「모르는 출력 스키마」)로 죽는다. 봉투 목록은 노드의 한 곳
    (`_LOOP_ENVELOPES`)에서 읽는다 — 이 검사가 그것과 Fake를 묶는다."""
    assert set(auditor._LOOP_ENVELOPES) <= set(fake_model._BUILDERS)


def _fake_context(*, static_signal: bool, build: bool, source: str) -> fake_model.FakeContext:
    """Fake 빌더가 읽는 사실 블록 — 그래프 근거는 늘 있고 정적 신호·빌드 근거는 인자가 정한다."""
    ref = fake_model.FakeEvidenceRef
    evidence = [ref(evidence_id="ev_graph", kind=EvidenceKind.GRAPH)]
    if static_signal:
        evidence.append(ref(evidence_id="ev_static", kind=EvidenceKind.STATIC))
    if build:
        evidence.append(ref(evidence_id="ev_build", kind=EvidenceKind.BUILD))
    return fake_model.FakeContext(
        run_id=RUN_ID,
        ids=dict(_ISSUED),
        target=VULN_TARGET,
        source=source,
        evidence=evidence,
        callers=["main"],
    )


@pytest.mark.parametrize("build", [True, False], ids=["build", "no-build"])
@pytest.mark.parametrize(
    ("source", "confidence", "cwe"),
    [
        pytest.param("memcpy(buf, src, len);", 0.6, "CWE-787", id="risky-call"),
        pytest.param("return len;", 0.3, "CWE-20", id="no-risky-call"),
    ],
)
@pytest.mark.parametrize(
    ("static_signal", "result", "status", "missing"),
    [
        pytest.param(True, "defeated", "triggerable", [], id="static-signal"),
        pytest.param(
            False,
            "decisive_code_missing",
            "reachability_unknown",
            [_ISSUED["missing_id"]],
            id="no-static-signal",
        ),
    ],
)
def test_the_two_fake_builders_give_the_same_verdict_and_share_every_common_field(
    static_signal: bool,
    result: str,
    status: str,
    missing: list[str],
    source: str,
    confidence: float,
    cwe: str,
    build: bool,
) -> None:
    """같은 `FakeContext`로 `_build_finding`(한 방 · v8·v9)과 `_build_audit_procedure`(v10)를 불러
    **같은 판정**과 **공유 필드 전부**가 같은지 본다 — 정적 신호가 있는 경우와 없는 경우 둘 다.

    `_build_audit_procedure` docstring이 「경로·CWE·제목·신뢰도·근거는 `_build_finding`과 같은 값
    이다」라 적는데 판정 줄이 **복사돼** 있어 한쪽만 고치면 갈린다 — 묶는 것이 없었다. 판정은 칸마다
    손으로 적은 기대(위 파라미터)로 문다: `_build_finding`의 `missing_evidence`가 비면 v10은
    `defeated` · `triggerable`, 발급 ID가 붙으면 `decisive_code_missing` ·
    `reachability_unknown`이다. 공유 필드(경로 · CWE · 제목 · 신뢰도 · `rca_draft` · 근거 ·
    `repro_spec`)는 두 출력끼리 비교하되, 위험 호출 유무(신뢰도·CWE)와 빌드 근거 유무(`repro_spec`)
    로 입력을 갈라 **양쪽 축이 실제로 값을 바꾸는지**도 본다 — 두 출력이 똑같이 빈 값을 내서
    일치하는 것이 아니다. Fake run 검사(`..._runs_a_procedure_loop_end_to_end`)는 후보 하나로 두
    갈래를 돌 뿐이라, 이 검사가 그 위에 빌더 단위로 나머지 두 축을 곱한다."""
    context = _fake_context(static_signal=static_signal, build=build, source=source)

    finding = fake_model._build_finding(context, _STAMPED, ())
    procedure = fake_model._build_audit_procedure(context, _STAMPED, ())

    assert procedure.audit is not None
    assert finding.missing_evidence == missing
    assert (procedure.audit.refutation.result, procedure.trigger_status) == (result, status)
    assert (finding.confidence, finding.cwe) == (confidence, cwe)
    assert (finding.repro_spec is not None) is build
    assert list(procedure.audit.path) == finding.path != []
    assert (procedure.cwe, procedure.title, procedure.confidence) == (
        finding.cwe,
        finding.title,
        finding.confidence,
    )
    assert procedure.rca_draft == finding.rca_draft
    assert procedure.evidence_refs == finding.evidence_refs != []
    assert procedure.repro_spec == finding.repro_spec


# --- 배선된 판은 근거 본문을 받아야 한다 ---------------------------------------------------


def _wired_auditor_seats() -> list[tuple[str, str]]:
    """저장소 `config/profiles.yaml`의 모든 프로필에서 **실모델** Auditor 좌석이 고르는
    `(프로필, 프롬프트 판)`. 판 이름을 이 파일이 들고 있지 않다 — 배선을 올리면 같이 따라온다."""
    text = (CONFIG_DIR / "profiles.yaml").read_text(encoding="utf-8")
    seats = []
    for name in sorted(yaml.safe_load(text)["profiles"]):
        config = load_config(name)
        if "auditor" in config.profile.roles and not config.model_for("auditor").is_fake:
            seats.append((name, config.model_for("auditor").prompt_template_version))
    return seats


def test_every_wired_auditor_version_is_in_the_content_aware_set() -> None:
    """실모델 Auditor 좌석이 고르는 판은 전부 `_CONTENT_AWARE_VERSIONS`에 든다 — **이 검사가 판
    배선 때 그 집합을 빠뜨리는 결함의 재발 방지다.**

    그 집합 밖이면 `aware`가 거짓이라 **Evidence 본문도 위치도 FACTS에 안 실린다**. v10 prefix는
    `## Evidence body completeness`에서 `FACTS.evidence[].body`·`truncated`를 읽으라고 시키는데
    재료는 안 주는 상태가 된다 — 오류 없이 돈다. `cli._check_prompt_version`이 실모델 역할에서
    ERROR로 막지만 그것은 `doctor`의 몫이고 노드는 조용히 본문을 뺀다. 좌석 목록을 설정에서 읽으므로
    다음 판 배선이 집합을 안 고치면 여기서 빨개진다. 빈 목록이면 이 검사가 아무것도 안 묻는 것이라
    그것도 막는다."""
    seats = _wired_auditor_seats()

    assert seats, "전제 — 실모델 Auditor 좌석이 하나도 없다. 검사가 비어 있다"
    assert [
        (profile, version)
        for profile, version in seats
        if version not in auditor._CONTENT_AWARE_VERSIONS
    ] == []
