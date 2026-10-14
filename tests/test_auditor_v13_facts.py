"""Auditor v13 — FACTS에서 `source` 사본을 뺀 판 (이슈 #67 · 2026-10-10 저장소 소유자 결정).

v11까지는 같은 SOURCE 문자열이 산문 블록과 FACTS JSON(`source`) 양쪽에 실렸고, body가 FACTS를
「the complete set of facts」라 불러 산문 블록이 그 밖의 것이 됐다. v13은 산문 블록을 남기고 FACTS의
사본을 뺀다. 묶는 것은 다섯이다.

1. **v11은 그대로다** — prefix·body sha(이 변경 전 `origin/main` 7d422f75에서 잰 값)와 노드 렌더의
   FACTS `source`. v13이 새 판으로 섰다(agent-contracts §4 규칙 1).
2. **v13 산문은 v11에서 두 줄만 다르다** — body의 Facts 머리 문장 · prefix의 `FACTS.source` 한 자리.
   줄 전체 등식(`old.replace(was, now) == new`)이라 덧붙이기·지우기가 다
   죽는다(`test_nullable_cwe.py` H4와 같은 장치).
3. **노드 렌더** — v13 hop0의 FACTS에 `source` 키가 **없고**(null·빈 문자열이 아니다) 나머지 키는
   v11과 같다. 원문은 산문 블록에 한 번만 실린다.
4. **선언** — FACTS에서 `source`를 빼는 판은 디스크 전체에서 auditor/v13·v14·v15 **셋**이다
   (⏵ 2026-10-11 v14 · ⏵ 2026-10-13 v15 — 서빙 판). 다른 역할·판의 FACTS는 바이트
   그대로다(`build_facts` 기본값).
5. **곁가지** — Fake가 산문 블록에서 원문을 읽어 v11과 같은 판정을 내고, bench가 노드와 같은 FACTS를
   싣는다.

⏵ 2026-10-11 — 서빙 판은 v14다(PR #601). 이 파일은 둘을 든다: 위 1~3·5는 머지된 v13을 v11 대비
고정으로 묻는 핀이고, 판 이름에 묶이지 않은 핀도 여기 있다 — 서빙 좌석이 config에서 고른 판의 FACTS
(`test_the_served_auditor_seat_renders_a_facts_without_the_source`) · 4의 디스크 전체 집합
`{v13, v14}` · `build_facts` 기본값 · 로더 거절. v14의 v12 대비 핀(산문·봉투·서빙 판 봉투 규칙)은
`tests/test_auditor_v14_facts.py`다.

⚠ 철자·구조 핀이다 — 모델이 산문 SOURCE 블록을 FACTS 없이도 같은 무게로 읽는지는 미측정이다.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from src import bench, bench_report
from src.adapters import fake_model
from src.config import load_config
from src.orchestrator.context import RunContext
from src.orchestrator.nodes import auditor
from src.orchestrator.prompt_loader import (
    PROMPTS_DIR,
    PromptError,
    build_facts,
    load_prompt,
)
from src.schemas.auditor import AuditorProcedureNullableCweAction
from src.schemas.enums import Role
from src.schemas.finding import Finding
from tests import test_nodes
from tests.test_nodes import VULN_TARGET, _triage

# 같은 픽스처를 이 모듈 이름으로 등록한다(`test_auditor_missing_evidence_gate.py`와 같은 방식).
ctx = test_nodes.ctx

# === 1. v11은 그대로다 =========================================================================

# 이 변경 전 `origin/main` 7d422f75의 `prompts/auditor/v11/{prefix,body}.md` sha256(2026-10-10
# 실측). body는 v10과 같은 값이다(`tests/test_nullable_cwe.py`의 `_V10_BODY_SHA256`).
_V11_PREFIX_SHA256 = "faaa5134d1f6f1fb88df0d1b1f7938511712423b9d20fbd6a974917279d8feb5"
_V11_BODY_SHA256 = "bac0cb11af57bb460c4c36bc723b828e66e6200cc64cab15358ec80677e6c1d0"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_the_v11_prompt_text_is_byte_identical_and_still_carries_facts_source() -> None:
    template = load_prompt(Role.AUDITOR, "v11")

    assert _sha256(template.prefix) == _V11_PREFIX_SHA256
    assert _sha256(template.body) == _V11_BODY_SHA256
    assert template.facts_source is True


# === 2. v13 산문 = v11 + 두 줄 ==================================================================

# (v11 줄의 조각, v13 줄의 조각) — 실물(`prompts/auditor/v11/` · `v13/`)에서 골랐다.
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
def test_v13_differs_from_v11_in_exactly_one_line_per_file(
    name: str, change: tuple[str, str]
) -> None:
    v11, v13 = _lines("v11", name), _lines("v13", name)

    assert len(v11) == len(v13)
    changed = [(old, new) for old, new in zip(v11, v13, strict=True) if old != new]
    assert len(changed) == 1
    (old, new), (was, now) = changed[0], change
    assert old.count(was) == 1, was
    assert old.replace(was, now) == new, now


def test_no_v13_prompt_text_says_facts_carries_the_source_or_is_complete() -> None:
    """반대쪽 철자 핀 — v11이 적던 두 철자가 v13 산문(prefix·body)에 안 남았다.
    ⚠ 같은 뜻을 다른 낱말로 쓰면 못 잡는다 — 성질은 위 줄 등식이 문다."""
    template = load_prompt(Role.AUDITOR, "v13")
    for phrase in ("FACTS.source", "complete set of facts"):
        assert phrase not in template.prefix + template.body, phrase


def test_v13_keeps_the_v11_envelope_tools_and_source_options() -> None:
    v11, v13 = load_prompt(Role.AUDITOR, "v11"), load_prompt(Role.AUDITOR, "v13")

    assert v13.output_schema == v11.output_schema == "AuditorProcedureNullableCweAction"
    assert v13.tools == v11.tools
    assert v13.source_options == v11.source_options
    assert v13.input == v11.input
    assert v13.facts_source is False
    assert auditor._loop_envelope(v13) is auditor._loop_envelope(v11)
    assert "v13" in auditor._CONTENT_AWARE_VERSIONS


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
    """같은 입력(후보·캡슐·근거·SOURCE)을 v11과 v13 템플릿으로 **노드의** hop0 렌더에 넣은 둘."""
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
        for version in ("v11", "v13")
    }


def _facts_of(body: str) -> dict:
    return json.loads(re.findall(r"```json\s*(\{.*?\})\s*```", body, re.DOTALL)[-1])


def test_the_v11_node_render_still_carries_the_source_in_facts(ctx: RunContext) -> None:
    assert _facts_of(_renders(ctx)["v11"].body)["source"] == _RENDER


def test_the_v13_node_render_has_no_source_key_and_the_source_once_in_prose(
    ctx: RunContext,
) -> None:
    """키가 **없다** — `""`·`null`이 아니다(`build_facts`의 `include_source` docstring). 원문은 산문
    블록에 한 번만 있고, 나머지 FACTS 키는 같은 입력의 v11 렌더와 같다."""
    renders = _renders(ctx)
    v13, v11 = renders["v13"], renders["v11"]
    facts = _facts_of(v13.body)

    assert "source" not in facts
    assert v13.body.count(_RENDER) == 1
    assert json.dumps(_RENDER)[1:-1] not in v13.body, "JSON 이스케이프된 사본도 없다"
    assert json.dumps(_RENDER)[1:-1] in v11.body, "전제 — v11에는 그 사본이 있다"
    assert facts == {k: v for k, v in _facts_of(v11.body).items() if k != "source"}


@pytest.mark.parametrize("profile", ["harness_pool", "harness_pool_hybrid"])
def test_the_served_auditor_seat_renders_a_facts_without_the_source(
    ctx: RunContext, profile: str
) -> None:
    """**성질** 핀 — 서빙 좌석이 실제로 고르는 판의 hop0는 FACTS에 `source`가 없고 원문이 산문에
    한 번 있다. 판 이름이 아니다: 좌석이 `facts_source`를 안 선언한 판(예: v12)으로 옮겨가면
    빨개져 그 결정을 명시하게 한다. `test_config`의 이름 핀만으로는 이름을 고친 뒤 안 물린다."""
    version = load_config(profile).model_for(Role.AUDITOR.value).prompt_template_version
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    rendered = auditor._render_hop0(
        ctx,
        load_prompt(Role.AUDITOR, version),
        ids=dict(_IDS),
        candidate=candidate,
        capsule=capsule,
        evidences=evidences,
        source=lambda: _RENDER,
    )

    assert "source" not in _facts_of(rendered.body), f"서빙 판 {version}의 FACTS에 source가 있다"
    assert rendered.body.count(_RENDER) == 1


def test_the_v13_node_path_omits_the_source_through_auditor_run(ctx: RunContext) -> None:
    """`_render_hop0`만이 아니라 `auditor.run`이 config에서 판을 골라 돈 hop0도 같다."""
    entry = ctx.config.models["fake"].transition(prompt_template_version="v13")
    ctx.config = ctx.config.transition(
        models={**ctx.config.models, "fake_v13_probe": entry},
        profile=ctx.config.profile.transition(
            roles={**ctx.config.profile.roles, "auditor": "fake_v13_probe"}
        ),
    )
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    auditor.run(
        ctx, candidate=candidate, capsule=capsule, evidences=evidences, source=lambda: _RENDER
    )
    hop0 = next(prompt for seen, prompt in ctx.models.prompts if seen is Role.AUDITOR)

    assert hop0.prompt_version == "v13"
    assert "source" not in _facts_of(hop0.body)
    assert hop0.body.count(_RENDER) == 1


def test_the_fake_reads_the_prose_source_and_reaches_the_v11_verdict(ctx: RunContext) -> None:
    """Fake는 FACTS `source`로 위험 호출을 찾는다 — 키가 없을 때 산문 블록을 안 읽으면 v13에서
    sink가 조용히 `unknown`이 된다. 같은 입력에서 v11과 같은 판정이어야 한다."""
    renders = _renders(ctx)
    # 최종 답의 판정 — Fake 루프의 앞 홉은 조사(lookup·probe)라 답 빌더를 직접 부른다.
    answers = {
        version: fake_model._build_audit_procedure(
            fake_model._parse_context(prompt.body), None, ()
        )
        for version, prompt in renders.items()
    }

    assert fake_model._parse_context(renders["v13"].body).source == _RENDER
    assert answers["v13"].audit.point.expression == "memcpy(...)", "전제 — sink를 읽었다"
    assert answers["v13"] == answers["v11"]


def test_the_fake_refuses_a_prompt_with_neither_facts_source_nor_a_prose_block() -> None:
    facts = build_facts(run_id="r", ids={}, include_source=False)
    body = "### Facts\n\n```json\n" + json.dumps(facts) + "\n```\n"

    with pytest.raises(fake_model.FakeModelError, match="산문 원문 블록도 없다"):
        fake_model._parse_context(body)


# === 4. 선언 — 어느 판이 FACTS에서 `source`를 빼는가 =============================================


def _all_templates() -> list[tuple[str, str]]:
    return sorted(
        (meta.parent.parent.name, meta.parent.name) for meta in PROMPTS_DIR.glob("*/*/meta.yaml")
    )


def test_only_auditor_v13_through_v17_omit_the_facts_source() -> None:
    """디스크 전체를 돈다 — 다른 역할(Scout·Critic·PoV)과 Auditor v1~v12의 FACTS는 `source`를 그대로
    싣는다(기본 `true`). ⏵ 2026-10-11 v14(v12 + 같은 변경)가 더해졌다. ⏵ 2026-10-13 v15(v14 + 계약
    문장 여섯 — meta `facts_source: false`를 그대로 든다)가 더해졌다. ⏵ 2026-10-14 v16(v15 + 입력
    맥락 함수 블록 서술)도, ⏵ 2026-10-14 v17(v16 + `FACTS.slice_state` 서술)도 같다."""
    omitting = {
        (role, version)
        for role, version in _all_templates()
        if not load_prompt(Role(role), version).facts_source
    }
    assert omitting == {
        ("auditor", "v13"), ("auditor", "v14"), ("auditor", "v15"), ("auditor", "v16"),
        ("auditor", "v17"),
    }


def test_build_facts_keeps_the_source_key_by_default() -> None:
    """기본값이 바뀌면 v13·v14 밖 모든 판의 요청이 바뀐다 — 키가 있고 값이 그대로다."""
    assert build_facts(run_id="r", ids={})["source"] == ""
    assert build_facts(run_id="r", ids={}, source="x")["source"] == "x"
    assert "source" not in build_facts(run_id="r", ids={}, source="x", include_source=False)


def _write_prompt(root: Path, body: str, facts_source: str) -> None:
    directory = root / "auditor" / "vt"
    directory.mkdir(parents=True)
    (directory / "prefix.md").write_text("prefix\n", encoding="utf-8")
    (directory / "body.md").write_text(body, encoding="utf-8")
    (directory / "meta.yaml").write_text(
        f"role: auditor\nversion: vt\noutput_schema: Finding\nfacts_source: {facts_source}\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("body", "facts_source", "message"),
    [
        # 원문이 프롬프트 어디에도 안 실린다.
        ("```json\n{{FACTS}}\n```\n", "false", "SOURCE"),
        # 불리언이 아니다 — 문자열 "false"는 참이라 조용히 사본을 다시 싣는다.
        ("{{SOURCE}}\n```json\n{{FACTS}}\n```\n", '"false"', "true/false"),
    ],
)
def test_the_loader_refuses_a_facts_source_it_cannot_honour(
    tmp_path: Path, body: str, facts_source: str, message: str
) -> None:
    _write_prompt(tmp_path, body, facts_source)

    with pytest.raises(PromptError, match=message):
        load_prompt(Role.AUDITOR, "vt", root=tmp_path)


# === 5. bench ==================================================================================


@pytest.mark.parametrize(("version", "has_source"), [("v11", True), ("v13", False)])
def test_bench_renders_the_auditor_facts_the_node_renders(version: str, has_source: bool) -> None:
    prompt = bench.sized_prompt(Role.AUDITOR, load_prompt(Role.AUDITOR, version), 2000, 0)

    assert ("source" in _facts_of(prompt.body)) is has_source


def test_bench_requests_the_v11_envelope_for_v13() -> None:
    """bench가 v13의 선언을 풀어 v11과 같은 봉투를 요청한다(`tests/test_auditor_loop.py`의 같은
    검사는 v11까지 든다 — 열린 PR #580과 겹치지 않게 v13은 여기서 문다)."""
    resolved = bench_report.schema_for(Role.AUDITOR, load_prompt(Role.AUDITOR, "v13"), Finding)

    assert resolved is AuditorProcedureNullableCweAction
