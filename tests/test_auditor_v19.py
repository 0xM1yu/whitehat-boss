"""Auditor v19 - SOURCE가 설명 일치 함수를 먼저 싣고, 상한에 빠진 걸친 함수를 FACTS로 알린다
(이슈 #702).

`desc_symbol`(#693 — 과제 설명이 entry보다 더 잘 맞히는 걸친 함수)이 있는 슬라이스가 컷에
들었는데, 5단계
`_auditor_source`가 걸친 함수를 (파일, 시작 줄) 순으로 상한까지 채워 그 함수가 잘렸다(run
`run_20261016_0001` · arvo:1065 · `slice_0524` — functions 40 · dropped 68, `file_regexec` 0회).
묶는 것은 다섯이다.

1. **v18은 한 글자도 안 바뀌었다** - sha가 v18 meta가 적은 값이다(규칙 1).
2. **v19 산문 = v18 + 이 편집** - body는 바이트 동일, prefix는 `_EDITS`를 적용한 결과와 바이트 동일.
3. **러너** - `auditor_desc_first`(그 함수를 entry·GUARDS 뒤 맨 앞에) · `auditor_omitted_functions`
   (상한에 빠진 걸친 함수 목록). 둘 다 꺼지면 SOURCE·이벤트 바이트가 종전과 같다.
4. **FACTS** - `source_omitted_functions` 키는 준 경우에만 생기고, 목록을 늘려도 SOURCE는 안 바뀐다
   (그래서 같은 SOURCE를 받는 Critic·PoV 판은 안 올린다).
5. **배선** - `_CONTENT_AWARE_VERSIONS` · `config/models.yaml` · `budgets.yaml` · doctor · Fake.

⚠ 고치는 것은 **수정 지점 쪽**뿐이다 - 크래시 지점(그 run의 `softmagic.c` `match`)은 GT라 run 중에
하네스가 모르고, 이 판으로 SOURCE에 든다는 보장이 없다. 모델이 목록을 보고 `lookup`하는지는
미측정이다.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import types
from pathlib import Path

import pytest

from src import cli
from src.config import load_config
from src.orchestrator.context import RunContext
from src.orchestrator.nodes import auditor
from src.orchestrator.prompt_loader import PROMPTS_DIR, build_facts, load_prompt
from src.schemas.common import Location
from src.schemas.enums import AuditorStopReason, Role
from tests import test_nodes
from tests.test_auditor_v17 import _IDS, _SOURCE, _hop0
from tests.test_nodes import VULN_TARGET, _triage

ctx = test_nodes.ctx

_V18 = PROMPTS_DIR / "auditor" / "v18"
_V19 = PROMPTS_DIR / "auditor" / "v19"


def _text(directory: Path, name: str) -> str:
    return (directory / name).read_bytes().decode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# === 1. v18은 한 글자도 안 바뀌었다 ===============================================================

# `prompts/auditor/v18/meta.yaml`이 적은 값.
_V18_SHA = {
    "prefix.md": "39ec93df361e1ba4a9f1f36ed04221110070023d236d7afa826187e9570df199",
    "body.md": "c4799594df6cc3e6d84658674e82bf357d9127479a27c255b93a5a093acb9584",
}


@pytest.mark.parametrize("name", sorted(_V18_SHA))
def test_v18_is_byte_identical(name: str) -> None:
    assert _sha((_V18 / name).read_bytes()) == _V18_SHA[name]


# === 2. v19 산문 = v18 + 이 편집 ==================================================================

_V19_SHA = {
    "prefix.md": "9c9c6ea1385b3eff0c383e3d811660626bd555e5b20ad52601a8ada1db58352f",
    "body.md": "c4799594df6cc3e6d84658674e82bf357d9127479a27c255b93a5a093acb9584",
}

# 출처: `prompts/auditor/v18/prefix.md:50`.
_OLD_ORDER = (
    "- Each other slice line resolves to its narrowest enclosing symbol, appended complete in "
    "`(file, start line)` order; entry-covered lines add no duplicate."
)
_NEW_ORDER = (
    "- Each other slice line resolves to its narrowest enclosing symbol, appended complete in "
    "`(file, start line)` order; entry-covered lines add no duplicate. At most one function "
    "may come first instead: when the task description names (in full or in part) a "
    "function the slice passes through more closely than it names the entry function, that "
    "function is appended before the others. It still counts against the cap below and is "
    "omitted whole like any other when it would exceed it. Its place says only that its "
    "name matches the description, not that it is the faulty one."
)
# 출처: `prompts/auditor/v18/prefix.md:51`.
_OLD_CAP = (
    "one that would exceed it is omitted whole, and lines no indexed symbol contains may be "
    "absent."
)
_NEW_CAP = (
    "one that would exceed it is omitted whole, and lines no indexed symbol contains may be "
    "absent. `FACTS.source_omitted_functions`, when present, lists the slice functions "
    "omitted this way: `functions` gives each one's `file`, `symbol`, `line_start`, and "
    "`line_end` (at most 100, in the order they were omitted), and `more` counts the "
    "omitted ones not listed; an empty `functions` with `more` 0 means none was omitted. "
    "A listed function is unavailable, not absent and not safe (Absence is not evidence): "
    "when it could change the decision, ask for it with a `lookup` request whose `kind` is "
    "`function` and whose `target` is its `symbol`, while a hop remains. The list covers "
    "only these slice functions — not definitions, callees, "
    "caller windows, or write-site windows dropped later."
)
_EDITS = ((_OLD_ORDER, _NEW_ORDER), (_OLD_CAP, _NEW_CAP))


def _apply_edits(prefix: str) -> str:
    for old, new in _EDITS:
        assert prefix.count(old) == 1, old[:60]
        prefix = prefix.replace(old, new)
    return prefix


@pytest.mark.parametrize("name", sorted(_V19_SHA))
def test_v19_files_match_their_recorded_sha(name: str) -> None:
    assert _sha((_V19 / name).read_bytes()) == _V19_SHA[name]


def test_the_v19_meta_records_the_same_sha_the_test_pins() -> None:
    meta = _text(_V19, "meta.yaml")
    for name, digest in _V19_SHA.items():
        assert f"sha256 `{digest}`" in meta, name


def test_v19_body_is_the_v18_body_bytes() -> None:
    assert (_V19 / "body.md").read_bytes() == (_V18 / "body.md").read_bytes()


def test_v19_prefix_is_the_v18_prefix_with_exactly_the_recorded_edits() -> None:
    """바이트 등식이다 - 편집 밖에 한 글자라도 다르면 죽는다."""
    assert _apply_edits(_text(_V18, "prefix.md")) == _text(_V19, "prefix.md")
    assert b"\r" not in (_V19 / "prefix.md").read_bytes()


def test_the_node_hop0_render_differs_from_v18_only_in_the_edited_prefix(ctx: RunContext) -> None:
    """옵션이 꺼진 렌더(목록 없음)는 v18과 body(SOURCE·FACTS 바이트)가 같다 - 판만 올려도 입력이
    안 바뀐다."""
    rendered = _hop0(ctx, ("v18", "v19"))

    assert rendered["v19"].body == rendered["v18"].body
    assert rendered["v19"].prefix == _apply_edits(rendered["v18"].prefix)
    assert rendered["v19"].prompt_version == "v19"
    assert "source_omitted_functions" not in rendered["v19"].body


# === 3. 러너 ======================================================================================

# 이슈 #702의 실형태를 줄였다: 앞 파일(`a.c`)의 걸친 함수 둘이 상한을 다 먹고, 뒤 파일(`z.c`)에
# 설명과 이름이 맞는 함수가 있다. 모든 블록의 단어 수가 같아 상한은 「entry + 둘」이다.
_ENTRY = Location(file="b.c", symbol="entry", line_start=1, line_end=2)
_A1 = Location(file="a.c", symbol="a_one", line_start=10, line_end=11)
_A2 = Location(file="a.c", symbol="a_two", line_start=20, line_end=21)
_DESC = Location(file="z.c", symbol="file_regexec", line_start=5, line_end=6)
_TEXTS = {
    _ENTRY: "int entry(void)\n{ go(); }",
    _A1: "int a_one(void)\n{ one(); }",
    _A2: "int a_two(void)\n{ two(); }",
    _DESC: "int file_regexec(void)\n{ re(); }",
}
_LINES = (("b.c", 2), ("a.c", 11), ("a.c", 21), ("z.c", 6))


def _words(text: str) -> int:
    return len(text.split())


def _source(
    *,
    desc_symbol: Location | None = None,
    omitted: list[Location] | None = None,
    texts: dict[Location, str] | None = None,
    extra: int = 0,
) -> tuple[str, dict]:
    """`_auditor_source`를 직접 부른다(`tests/test_slice_targets.py`의 `_trim_source`와 같은 ctx
    꼴).
    `extra`만큼 단어를 더 허용한다."""
    from src.adapters.slices import SliceRef
    from src.orchestrator import runner

    texts = texts or _TEXTS
    per_file = {loc.file: [] for loc in texts}
    for loc in texts:
        per_file[loc.file].append(loc)
    cap = sum(_words(runner._block(loc, _TEXTS[loc])) for loc in (_ENTRY, _A1, _A2)) + 2 + extra
    events: list[dict] = []
    ctx = types.SimpleNamespace(
        symbols_for=lambda file: per_file.get(file, []),
        read_source=lambda loc: texts[loc],
        callees_of=lambda _loc: [], definitions_of=lambda _n, _f: [],
        counter_for_role=lambda role: _words,
        config=types.SimpleNamespace(thresholds=types.SimpleNamespace(
            auditor_source_gate=0, auditor_guards=0,
            auditor_callee_hops=0, auditor_definitions=0, auditor_source_tokens=cap,
            auditor_entry_trim=0, auditor_caller_lines=0, auditor_callee_wrappers=0,
            auditor_indirect_callees=0, auditor_write_sites=0,
            auditor_input_context=0)),
        trace=types.SimpleNamespace(emit=lambda *a, **kw: events.append(kw)),
    )
    ref = SliceRef(slice_id="slice_0524", location=_ENTRY, given_end="sink",
                   entry=Location(file="b.c", line_start=2), lines=_LINES, degraded=False)
    source = runner._auditor_source(
        ctx, ref, _ENTRY, "cand", desc_symbol=desc_symbol, omitted_functions=omitted
    )
    return source, events[-1]


def _banners(source: str) -> list[str]:
    return re.findall(r"/\* ===== (\S+) ===== \*/", source)


def test_without_a_described_function_the_slice_functions_keep_path_order() -> None:
    """옛 동작 핀 - 경로 순이라 `a.c` 둘이 상한을 먹고 `z.c`의 설명 일치 함수가 빠진다(#702
    그대로)."""
    source, event = _source()

    assert _banners(source) == ["b.c:1-2", "a.c:10-11", "a.c:20-21"]
    assert "file_regexec" not in source
    assert event["dropped"] == 1
    assert "desc_symbol" not in event and "desc_carried" not in event


def test_the_described_function_is_appended_right_after_the_entry_function() -> None:
    source, event = _source(desc_symbol=_DESC)

    assert _banners(source) == ["b.c:1-2", "z.c:5-6", "a.c:10-11"]
    assert event["dropped"] == 1
    assert (event["desc_symbol"], event["desc_carried"]) == ("z.c:file_regexec", True)


def test_a_described_function_inside_the_entry_function_changes_nothing() -> None:
    """`desc_symbol`이 entry 함수 자신이면 걸친 함수가 아니라 순서가 안 바뀐다 - 바이트가 같다.
    그 본문은 이미 맨 앞(entry)에 실렸으니 `desc_carried`는 참이다."""
    plain, _ = _source()
    inside, event = _source(desc_symbol=_ENTRY)

    assert inside == plain
    assert event["desc_carried"] is True


def test_a_described_function_over_the_cap_is_still_omitted_whole() -> None:
    """상한 밖으로 운반하지 않는다(council 2026-10-17 — (b)는 안 한다). 빠지면 목록에 든다."""
    texts = {**_TEXTS, _DESC: "int file_regexec(void)\n{ " + "x(); " * 50 + "}"}
    omitted: list[Location] = []
    source, event = _source(desc_symbol=_DESC, omitted=omitted, texts=texts)

    assert "file_regexec" not in source
    assert event["desc_carried"] is False
    assert [loc.symbol for loc in omitted] == ["file_regexec"]


def test_the_omitted_list_is_the_dropped_count_and_leaves_source_alone() -> None:
    """목록은 `dropped`를 세는 그 자리에서 찬다 - 수와 이름이 갈라지지 않는다. 목록을 넘겨도
    SOURCE 문자열은 바이트가 같다(그래서 Critic·PoV는 안 바뀐다)."""
    omitted: list[Location] = []
    without, _ = _source()
    with_list, event = _source(omitted=omitted)

    assert with_list == without
    assert len(omitted) == event["dropped"] == 1
    assert omitted == [_DESC]


def test_nothing_omitted_leaves_an_empty_list() -> None:
    omitted: list[Location] = []
    _, event = _source(omitted=omitted, extra=100)

    assert (omitted, event["dropped"]) == ([], 0)


# === 4. FACTS =====================================================================================


def _omitted(n: int) -> list[Location]:
    return [Location(file="a.c", symbol=f"f{i}", line_start=10 * i + 1, line_end=10 * i + 2)
            for i in range(n)]


def test_the_facts_key_is_absent_unless_a_list_is_given() -> None:
    assert "source_omitted_functions" not in build_facts(run_id="r", ids={})
    facts = build_facts(run_id="r", ids={}, source_omitted_functions=[])
    assert facts["source_omitted_functions"] == {
        "functions": [], "more": 0,
    }


def test_the_facts_key_lists_at_most_a_hundred_and_counts_the_rest() -> None:
    facts = build_facts(run_id="r", ids={}, source_omitted_functions=_omitted(105))
    value = facts["source_omitted_functions"]

    assert len(value["functions"]) == 100 and value["more"] == 5
    assert value["functions"][0] == {"file": "a.c", "symbol": "f0", "line_start": 1, "line_end": 2}


def test_a_list_filled_while_source_is_read_reaches_hop0_facts(ctx: RunContext) -> None:
    """노드는 `source()`를 FACTS 조립 **전에** 부른다(`_render_hop0`) - 러너가 SOURCE를 조립하며
    채운 목록이 같은 hop0 FACTS에 실린다."""
    omitted: list[Location] = []

    def source() -> str:
        omitted.append(_DESC)
        return _SOURCE

    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    template = load_prompt(Role.AUDITOR, "v19")
    kwargs = {"ids": dict(_IDS), "candidate": candidate, "capsule": capsule,
              "evidences": evidences}
    off = auditor._render_hop0(ctx, template, source=lambda: _SOURCE, **kwargs)
    on = auditor._render_hop0(ctx, template, source=source, omitted_functions=omitted, **kwargs)

    facts = json.loads(re.findall(r"```json\s*(\{.*?\})\s*```", on.body, re.S)[-1])
    assert facts["source_omitted_functions"] == {
        "functions": [{"file": "z.c", "symbol": "file_regexec", "line_start": 5, "line_end": 6}],
        "more": 0,
    }
    assert off.prefix == on.prefix
    assert "source_omitted_functions" not in off.body


# === 5. 배선 =====================================================================================

_NEW_OPTIONS = ("auditor_desc_first", "auditor_omitted_functions")


def test_v19_is_wired_in_every_place_a_new_prompt_version_has_to_be() -> None:
    template, v18 = load_prompt(Role.AUDITOR, "v19"), load_prompt(Role.AUDITOR, "v18")

    assert "v19" in auditor._CONTENT_AWARE_VERSIONS
    assert (template.output_schema, template.tools, template.input, template.facts_source) == (
        v18.output_schema, v18.tools, v18.input, v18.facts_source,
    )
    assert template.source_options == (*v18.source_options, *_NEW_OPTIONS)
    assert auditor.loop_problem(template, 16, is_fake=False) is None
    assert auditor.tool_hops_in_effect("v19", 16) == 16


@pytest.mark.parametrize("profile", ["harness_pool", "harness_pool_hybrid"])
def test_the_serving_auditor_seat_selects_v19_with_both_options_on(profile: str) -> None:
    config = load_config(profile)
    version = config.model_for("auditor").prompt_template_version

    assert version == "v19"
    assert (config.thresholds.auditor_desc_first, config.thresholds.auditor_omitted_functions) == (
        1, 1,
    )
    assert cli._check_prompt_version(config, "auditor", version) == []
    assert cli._undescribed_source_options(config) == []


def test_turning_the_options_on_is_blocked_before_v19() -> None:
    config = load_config("harness_pool")
    key = config.model_key_for("auditor")
    entry = config.models[key].transition(prompt_template_version="v18")
    v18 = config.transition(models={**config.models, key: entry})

    assert cli._undescribed_source_options(v18) == [f"{name}(auditor/v18)" for name in _NEW_OPTIONS]


def test_the_new_options_do_not_reach_the_critic_and_pov_seats() -> None:
    """목록은 FACTS로만 가고 순서만 바꾼 SOURCE는 블록 종류가 같다 - 같은 SOURCE를 받는 판이
    서술해야 하는 옵션(`RELAYED_SOURCE_OPTIONS`)이 아니다."""
    from src.config import AUDITOR_SOURCE_OPTIONS, RELAYED_SOURCE_OPTIONS

    assert set(_NEW_OPTIONS) <= set(AUDITOR_SOURCE_OPTIONS)
    assert not set(_NEW_OPTIONS) & set(RELAYED_SOURCE_OPTIONS)


def test_no_auditor_version_newer_than_the_served_one_is_on_disk() -> None:
    """doctor의 「디스크 새 판」 WARN이 0인 이유 - 서빙 판이 디스크의 마지막 판이다."""
    numbers = sorted(
        int(p.name[1:])
        for p in (PROMPTS_DIR / "auditor").iterdir()
        if p.is_dir() and p.name[1:].isdecimal()
    )
    assert numbers[-1] == 19


def test_a_fake_loop_runs_on_v19(ctx: RunContext) -> None:
    entry = ctx.config.models["fake"].transition(prompt_template_version="v19")
    config = ctx.config.transition(
        models={**ctx.config.models, "fake_v19": entry},
        profile=ctx.config.profile.transition(
            roles={**ctx.config.profile.roles, "auditor": "fake_v19"}
        ),
        thresholds=ctx.config.thresholds.transition(auditor_tool_hops=4),
    )
    armed = dataclasses.replace(ctx, config=config)
    candidate, capsule, evidences = _triage(armed, VULN_TARGET)

    finding, _ = auditor.run(
        armed, candidate=candidate, capsule=capsule, evidences=evidences, source=lambda: _SOURCE
    )

    hops = [e.data for e in armed.trace.events if e.kind.value == "auditor_hop"]
    assert [h["action"] for h in hops] == ["lookup", "probe", "answer"]
    assert hops[-1]["stop_reason"] == AuditorStopReason.ANSWERED.value
    assert finding.produced_by.prompt_version == "v19"
