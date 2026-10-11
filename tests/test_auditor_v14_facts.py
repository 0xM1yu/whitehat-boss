"""Auditor v14 — v12에서 FACTS의 `source` 사본을 뺀 판 (이슈 #67 · #578 · 2026-10-11).

v13(#591)은 같은 FACTS 변경을 v11 위에 얹어 봉투가 v11 것(`no_signal`이면 `audit` null)이었다. FT
정답은
v12 계약(`no_signal`에도 `audit`를 채운다)이라 v14를 v12 위에 새 판으로 세웠다 — v13은 머지된 판이라
바이트를 고치지 않는다(agent-contracts §4 규칙 1). 묶는 것은 넷이다.

1. **v14 산문은 v12에서 두 줄만 다르다** — body의 Facts 머리 문장 · prefix의 `FACTS.source` 한 자리.
   줄 전체 등식(`old.replace(was, now) == new`)이라 덧붙이기·지우기가 다 죽는다. body는 v13과
   바이트 동일이다(v12 body = v11 body라 v13 body와 같은 한 줄 차이다).
2. **봉투는 v12 것이다** — `AuditorProcedureAuditAlwaysAction`(v13의
`AuditorProcedureNullableCweAction`이
   아니다). 도구·SOURCE 옵션·입력도 v12와 같다.
3. **노드 렌더** — v14 hop0의 FACTS에 `source` 키가 **없고** 나머지 키는 v12와 같다. 원문은 산문
블록에
   한 번만 실린다. `auditor.run`이 config에서 판을 골라 돈 hop0도 같다.
4. **곁가지** — Fake가 산문 블록에서 원문을 읽어 v12와 같은 판정을 내고, bench가 노드와 같은 FACTS와
   v12 봉투를 요청한다.

판에 무관한 검사(서빙 좌석 성질 · 로더 거절 · `build_facts` 기본값 · `facts_source: false`인 판
목록)는
`tests/test_auditor_v13_facts.py`가 든다.

⚠ 철자·구조 핀이다 — 모델이 산문 SOURCE 블록을 FACTS 없이도 같은 무게로 읽는지는 미측정이다.
"""

from __future__ import annotations

import json
import re

import pytest

from src import bench, bench_report
from src.adapters import fake_model
from src.orchestrator.context import RunContext
from src.orchestrator.nodes import auditor
from src.orchestrator.prompt_loader import PROMPTS_DIR, load_prompt
from src.schemas.auditor import AuditorProcedureAuditAlwaysAction
from src.schemas.enums import Role
from src.schemas.finding import Finding
from tests import test_nodes
from tests.test_nodes import VULN_TARGET, _triage

# 같은 픽스처를 이 모듈 이름으로 등록한다(`test_auditor_v13_facts.py`와 같은 방식).
ctx = test_nodes.ctx

# === 1. v14 산문 = v12 + 두 줄 ==================================================================

# (v12 줄의 조각, v14 줄의 조각) — 실물(`prompts/auditor/v12/` · `v14/`)에서 골랐다.
_BODY_CHANGE = (
    "The JSON below is the complete set of facts and identifiers available to you:",
    "The JSON below holds the identifiers and evidence for this audit. The code under analysis is "
    "`SOURCE`, the original source code above; it is not repeated in this JSON:",
)
_PREFIX_CHANGE = (
    "kept separate from `SOURCE`/`FACTS.source`.",
    "kept separate from `SOURCE`.",
)


def _lines(version: str, name: str) -> list[str]:
    return (PROMPTS_DIR / "auditor" / version / name).read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize(
    ("name", "change"), [("body.md", _BODY_CHANGE), ("prefix.md", _PREFIX_CHANGE)]
)
def test_v14_differs_from_v12_in_exactly_one_line_per_file(
    name: str, change: tuple[str, str]
) -> None:
    v12, v14 = _lines("v12", name), _lines("v14", name)

    assert len(v12) == len(v14)
    changed = [(old, new) for old, new in zip(v12, v14, strict=True) if old != new]
    assert len(changed) == 1
    (old, new), (was, now) = changed[0], change
    assert old.count(was) == 1, was
    assert old.replace(was, now) == new, now


def test_v14_body_is_the_v13_body() -> None:
    """user 턴 틀은 v13과 같다 — v13 형식으로 만든 FT 입력이 v14에도 그대로 맞는다."""
    assert _lines("v14", "body.md") == _lines("v13", "body.md")


def test_no_v14_prompt_text_says_facts_carries_the_source_or_is_complete() -> None:
    """반대쪽 철자 핀 — v12가 적던 두 철자가 v14 산문(prefix·body)에 안 남았다.
    ⚠ 같은 뜻을 다른 낱말로 쓰면 못 잡는다 — 성질은 위 줄 등식이 문다."""
    template = load_prompt(Role.AUDITOR, "v14")
    for phrase in ("FACTS.source", "complete set of facts"):
        assert phrase not in template.prefix + template.body, phrase


# === 2. 봉투는 v12 것 ==========================================================================


def test_v14_keeps_the_v12_envelope_tools_and_source_options() -> None:
    v12, v13, v14 = (load_prompt(Role.AUDITOR, v) for v in ("v12", "v13", "v14"))

    assert v14.output_schema == v12.output_schema == "AuditorProcedureAuditAlwaysAction"
    assert v14.output_schema != v13.output_schema, "v13은 v11 봉투다 — v14가 갈라선 자리"
    assert v14.tools == v12.tools
    assert v14.source_options == v12.source_options
    assert v14.input == v12.input
    assert v14.facts_source is False
    assert auditor._loop_envelope(v14) is auditor._loop_envelope(v12)
    assert "v14" in auditor._CONTENT_AWARE_VERSIONS


# === 3. 노드 렌더 ==============================================================================

# 슬라이스 렌더 꼴의 SOURCE — 줄바꿈·따옴표·백슬래시를 들어 JSON 이스케이프가 원문과 갈리게 한다.
_RENDER = (
    "/* ===== parser.c:3-6 ===== */\n"
    "4|     char buf[64];\n"
    '5|     if (strcmp(src, "\\\\x00") == 0) return;\n'
    "6|     memcpy(buf, src, len);"
)

_IDS = {"candidate_id": "cand_0001", "finding_id": "find_0001", "missing_id": "mev_0001"}


def _renders(ctx: RunContext):  # noqa: ANN202
    """같은 입력(후보·캡슐·근거·SOURCE)을 v12와 v14 템플릿으로 **노드의** hop0 렌더에 넣은 둘."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    return {
        version: auditor._render_hop0(
            ctx,
            load_prompt(Role.AUDITOR, version),
            ids=dict(_IDS),
            candidate=candidate,
            capsule=capsule,
            evidences=evidences,
            source=lambda: _RENDER,
        )
        for version in ("v12", "v14")
    }


def _facts_of(body: str) -> dict:
    return json.loads(re.findall(r"```json\s*(\{.*?\})\s*```", body, re.DOTALL)[-1])


def test_the_v14_node_render_has_no_source_key_and_the_source_once_in_prose(
    ctx: RunContext,
) -> None:
    """키가 **없다** — `""`·`null`이 아니다. 원문은 산문 블록에 한 번만 있고, 나머지 FACTS 키는 같은
    입력의 v12 렌더와 같다."""
    renders = _renders(ctx)
    v14, v12 = renders["v14"], renders["v12"]
    facts = _facts_of(v14.body)

    assert "source" not in facts
    assert v14.body.count(_RENDER) == 1
    assert json.dumps(_RENDER)[1:-1] not in v14.body, "JSON 이스케이프된 사본도 없다"
    assert json.dumps(_RENDER)[1:-1] in v12.body, "전제 — v12에는 그 사본이 있다"
    assert facts == {k: v for k, v in _facts_of(v12.body).items() if k != "source"}


def test_the_v14_node_path_omits_the_source_through_auditor_run(ctx: RunContext) -> None:
    """`_render_hop0`만이 아니라 `auditor.run`이 config에서 판을 골라 돈 hop0도 같다."""
    entry = ctx.config.models["fake"].transition(prompt_template_version="v14")
    ctx.config = ctx.config.transition(
        models={**ctx.config.models, "fake_v14_probe": entry},
        profile=ctx.config.profile.transition(
            roles={**ctx.config.profile.roles, "auditor": "fake_v14_probe"}
        ),
    )
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    auditor.run(
        ctx, candidate=candidate, capsule=capsule, evidences=evidences, source=lambda: _RENDER
    )
    hop0 = next(prompt for seen, prompt in ctx.models.prompts if seen is Role.AUDITOR)

    assert hop0.prompt_version == "v14"
    assert "source" not in _facts_of(hop0.body)
    assert hop0.body.count(_RENDER) == 1


# === 4. 곁가지 — Fake · bench ===================================================================


def test_the_fake_reads_the_prose_source_and_reaches_the_v12_verdict(ctx: RunContext) -> None:
    """Fake는 FACTS `source`로 위험 호출을 찾는다 — 키가 없을 때 산문 블록을 안 읽으면 v14에서
    sink가 조용히 `unknown`이 된다. 같은 입력에서 v12와 같은 판정이어야 한다."""
    renders = _renders(ctx)
    answers = {
        version: fake_model._build_audit_procedure(
            fake_model._parse_context(prompt.body), None, ()
        )
        for version, prompt in renders.items()
    }

    assert fake_model._parse_context(renders["v14"].body).source == _RENDER
    assert answers["v14"].audit.point.expression == "memcpy(...)", "전제 — sink를 읽었다"
    assert answers["v14"] == answers["v12"]


@pytest.mark.parametrize(("version", "has_source"), [("v12", True), ("v14", False)])
def test_bench_renders_the_auditor_facts_the_node_renders(version: str, has_source: bool) -> None:
    prompt = bench.sized_prompt(Role.AUDITOR, load_prompt(Role.AUDITOR, version), 2000, 0)

    assert ("source" in _facts_of(prompt.body)) is has_source


def test_bench_requests_the_v12_envelope_for_v14() -> None:
    """bench가 v14의 선언을 풀어 v12와 같은 봉투를 요청한다."""
    resolved = bench_report.schema_for(Role.AUDITOR, load_prompt(Role.AUDITOR, "v14"), Finding)

    assert resolved is AuditorProcedureAuditAlwaysAction
