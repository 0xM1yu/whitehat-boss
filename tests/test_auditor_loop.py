"""Auditor 도구 루프의 계약 — 봉투(`AuditorAction`)와 홉 루프.

`tests/test_pov_loop.py`의 Auditor 판이다. 그쪽이 PoV 봉투에 대해 보는 것(액션별 필드 ·
`tool` Literal ⊆ `TOOL_KINDS` · docstring이 요청 payload에 실린다)을 같은 규율로 본다.

**이 파일이 `tests/test_nodes.py`·`tests/test_config.py`를 건드리지 않는 이유**: Auditor
프롬프트 판과 그 meta는 다른 작업 레인이고, 그 레인이 같은 두 파일을 고친다. 허용집합
단언도 여기 둔다.
"""

import json
import re
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import pytest
from pydantic import ValidationError

from src import bench_report, cli
from src.adapters import symbol_resolver
from src.adapters.factory import build_deps
from src.adapters.fake_retriever import read_symbol_source
from src.adapters.fake_tools import TOOL_KINDS
from src.adapters.symbol_render import render_followup
from src.bench import ROLE_SPEC
from src.config import load_config
from src.interfaces.model_client import ModelResult, SchemaParseError, Usage
from src.orchestrator.budget import BudgetExhausted, BudgetLedger
from src.orchestrator.context import RunContext
from src.orchestrator.nodes import auditor, index, preprocess, retrieval, scout, signals
from src.orchestrator.policy import FixedThresholdPolicy
from src.orchestrator.prompt_loader import (
    PROMPT_TOOL_NAMES,
    PROMPTS_DIR,
    PromptError,
    PromptTemplate,
    load_prompt,
)
from src.orchestrator.runner import RunSpec, _new_record
from src.orchestrator.trace import EventKind, TraceWriter
from src.schemas import CapsuleLevel, FindingState, Location, Role
from src.schemas.auditor import AUDITOR_TOOLS, AuditorAction
from src.schemas.enums import AuditorStopReason, UnfilledReason
from src.schemas.finding import Finding
from src.schemas.need_evidence import Resolution, RetrievedBlock, Unfilled
from src.schemas.pov import PovAction, PovSegment, PovSpec
from src.schemas.scout_response import RequestedContext, ScoutResponse
from tests.test_pipeline_e2e import _per_stage, _run


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
    """`AuditorAction`의 설명은 `response_format.json_schema.schema`로 **모든 Auditor 루프 요청에
    실린다**(`openai_compat`). 커밋 SHA·PR·이슈 번호·소스 파일 경로 같은 내부 참조가 이 봉투가
    **새로** 모델 입력에 들어가면 안 된다 — 그런 이력은 클래스 **밖** `#` 주석에 둔다.

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
    설명과 `spec` 필드 설명이 둘 다 「받지 않는다」를 말해야 한다."""
    schema = AuditorAction.model_json_schema()

    assert "받지 않는다" in schema["description"]
    assert "받지 않" in schema["properties"]["spec"]["description"]
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
# 루프는 `thresholds.auditor_tool_hops`가 양수이고 프롬프트 meta가 `output_schema:
# AuditorAction`을 선언해야 켜진다 — 이 파일은 **그 모양의 프롬프트를 임시 디렉터리에 만들고**
# 노드가 부르는 `load_prompt`를 그리로 돌린다(실제 루프 판은 프롬프트 레인이 쓴다). 임시 판의 이름은
# 번호가 아니다(`LOOP_VERSION`) — 실제 판 번호와 겹치면 어느 쪽이 읽혔는지 이름으로는 모른다. 오염은
# `src` 모듈 속성(`auditor.load_prompt`·`cli.load_prompt`)에 건다 — 이 파일의 복사본에 걸면 노드가
# 안 먹고 초록이 난다. 안 먹었으면 `PromptError`로 빨개진다(그 이름의 판이 저장소에 없으므로).
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
    """홉마다 정해진 `AuditorAction`을 낸다. 목록이 다하면 마지막 것을 반복한다.

    fake가 못 내는 패턴(조사만 계속 · 같은 요청 두 번 · 모델이 쓴 문자열이 실린 `probe`)을
    **테스트 안에서** 주입한다 — `fake_model`의 빌더는 건드리지 않는다. `prompts`는 노드가 홉마다
    보낸 프롬프트이고 `schemas`는 요청한 구조화 출력이다. `fail_at`이 `(홉 번호, 예외)`이면 그
    호출에서 그 예외를 올린다 — 모델 호출 실패의 사유 전이를 거는 데 쓴다."""

    def __init__(
        self, actions: list[AuditorAction], *, fail_at: tuple[int, Exception] | None = None
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


def _write_loop_prompt(root: Path, *, tools: tuple[str, ...] | None) -> None:
    """`root/auditor/<LOOP_VERSION>/`에 도구 루프 판 프롬프트를 쓴다. `tools=None`이면 meta에 키를
    안 쓴다."""
    directory = root / "auditor" / LOOP_VERSION
    directory.mkdir(parents=True)
    meta = [
        "role: auditor",
        f"version: {LOOP_VERSION}",
        "output_schema: AuditorAction",
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
        thresholds=config.thresholds.transition(auditor_tool_hops=hops, auditor_guards=0),
    )


def _patch_prompts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    tools: tuple[str, ...] | None = LOOP_TOOLS,
) -> None:
    """노드(`auditor.load_prompt`)와 cli 게이트(`cli.load_prompt`)가 같은 임시 디렉터리를 읽게 갈아
    끼운다. `LOOP_VERSION` 밖의 버전은 원래 디렉터리로 보낸다."""
    root = tmp_path / "loop_prompts"
    _write_loop_prompt(root, tools=tools)

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
) -> None:
    """Auditor 좌석을 도구 루프 판으로 돌리고 조사 홉 상한을 `hops`로 둔다."""
    _patch_prompts(monkeypatch, tmp_path, tools=tools)
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
    스키마는 그때부터 캡슐 레벨 전용이다). 두 판을 다 건다 — 한 방 판과 루프 판."""
    assert auditor.resolve_output_schema(load_prompt(Role.AUDITOR, "v7").output_schema) is Finding
    assert (
        auditor.resolve_output_schema(load_prompt(Role.AUDITOR, "v8").output_schema)
        is AuditorAction
    )
    # `ROLE_SPEC`이 auditor 자리에 든 이름(캡슐 레벨 전용이 된 쪽)을 `declared`로 주고, bench가
    # 그것을 **무시하고 판 선언을 푸는지** 본다. 문자열 핀으로 걸었을 때는 분기를 `elif False:`로
    # 막아도 초록이었다(CLAUDE.md 2.5).
    declared, _ = ROLE_SPEC[Role.AUDITOR]
    assert declared is Finding, "전제 — ROLE_SPEC의 auditor는 한 방 판 이름이다"
    for version, expected in (("v7", Finding), ("v8", AuditorAction)):
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
    assert not {"resolved", "unfilled", "truncated", "already"} & set(lookup.data)


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
            Resolution(followup_text="", blocks=(), unfilled=(), already=(), truncated=()),
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
    인덱스 없음은 **넷이 다 빈 것**이 서명이다(`runner._fuzz_entrypoint`가 같은 서명으로 가른다)."""
    _lookup_run(ctx, monkeypatch, tmp_path, _StubResolver(resolution))

    data = _hops(ctx)[0].data
    assert data["resolved"] == 0
    assert (data["unfilled"], data["truncated"], data["fixable_count"]) == (
        unfilled,
        truncated,
        fixable,
    )
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
    `schema`(`Finding` ↔ `AuditorAction`)이고, 아래 마지막 단언이 그것이다. manifest의
    `auditor_tool_hops`는 실제로 도는 값만 싣는다(`runner._recorded_thresholds`).

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
    """위 검사의 음성 쌍 — 실서빙 좌석(`harness_pool`: v8 · `AuditorAction`)은 16이 그대로 실린다.

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
    면제의 판정은 `is_fake`가 아니라 **판이 `AuditorAction`을 선언하는가**다(`_ask_model`의 분기와
    같다). `is_fake`로 빼면 위 둘은 초록이고 여기서만 빨개진다."""
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
    `probe`의 키는 **도구 이름**이다: 도구가 `spec`을 입력으로 안 받으므로 `spec`만 바꿔 또
    부르면 같은 관측이 돌아오고, 모델은 매번 다른 답을 받은 줄 안다. 도구 호출 수가 1(첫 probe만)인
    것이 「반복 홉은 안 돌렸다」의 증거다. 상한은 5라 `MAX_HOP`이 가로채지 못한다."""
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


def test_asking_the_same_thing_again_after_something_else_is_not_a_repeat(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A → B → A는 반복이 아니다 — 비교 대상은 **직전 홉 하나**뿐이다.

    모든 이전 홉과 비교하면 조사를 되짚는 정당한 경로(이름을 하나 보고, 다른 걸 보고, 처음 걸
    다시 본다)가 막힌다. 마지막 요청 C까지 가서 상한(`MAX_HOP`)으로 서야 한다."""
    limit = 3
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    _arm(ctx, monkeypatch, tmp_path, hops=limit)
    ctx.models = _ScriptedModel([_lookup_action(name) for name in ("A", "B", "A", "C")])

    with pytest.raises(auditor.AuditorHopLimit) as stopped:
        auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    assert stopped.value.stop is AuditorStopReason.MAX_HOP
    assert [h.data["hop"] for h in _hops(ctx)] == [0, 1, 2, 3]


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


@pytest.mark.parametrize("tool", ["semgrep", "asan"])
def test_a_probe_hop_puts_only_the_tool_name_in_argv(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tool: str
) -> None:
    """`probe`가 도구 러너에 넘기는 `argv`는 `[도구 이름]` 하나다 — 모델이 쓴 어떤 글자도 안 실린다.

    모델의 `rationale`과 `spec.rationale`에 셸 메타문자가 든 표식을 싣고, 러너가 실제로 받은
    `ToolArgs`(스파이)와 `tool_call` 이벤트를 본다. 도구 이름은 스키마가 닫은 Literal이라 argv가
    고정된 여섯 중 하나다 — 이 검사가 무는 것은 누군가 `spec`이나 `rationale`을 인자로 이어
    붙이는 변경이다(임의 입력이 도구 인자·Evidence 로그에 실린다). `argv`가 선택한 도구의 이름인
    것은 도구를 고정값으로 박은 변경도 막는다."""
    marker = "MODEL_TEXT; echo pwned #"
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    _arm(ctx, monkeypatch, tmp_path, hops=1)
    ctx.models = _ScriptedModel(
        [_probe_action(tool, spec=_spec(marker), rationale=marker), _lookup_action("x")]
    )
    spy = _SpyTools(ctx.tools)
    ctx.tools = spy
    before = len(_events(ctx, EventKind.TOOL_CALL))

    with pytest.raises(auditor.AuditorHopLimit):  # 조사 한 번 뒤 둘째 조사 홉에서 MAX_HOP
        auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    assert len(spy.calls) == 1
    ran, args, _ = spy.calls[0]
    assert ran == tool
    assert list(args.argv) == [tool]
    assert marker not in repr(args)
    calls = _events(ctx, EventKind.TOOL_CALL)[before:]
    assert [(c.data["tool"], c.data["argv"]) for c in calls] == [(tool, tool)]
    assert marker not in repr(calls)


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
