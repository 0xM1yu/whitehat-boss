"""Auditor v16 - v15에 입력 맥락 함수 블록의 서술을 더한 판 (S20 Phase 13 · council 2026-10-14).

새 옵션 `auditor_input_context`(`config/budgets.yaml` 1)가 슬라이서의 `analysis.input_context`가 CPG
근거로 고른
함수 본문을 `SOURCE` 맨 뒤에 붙인다. v15는 그 옵션을 서술하지 않아 켠 채 고르면 `cli`가 막는다
(`tests/test_source_input_context.py`) - 그래서 새 판이다. 묶는 것은 여섯이다.

1. **v15는 한 글자도 안 바뀌었다** - prefix·body·meta의 sha256이 `origin/main`(`e074774`)의
값이다(규칙 1).
2. **v16 산문 = v15 + 이 편집들** - body는 바이트 동일이고 prefix는 `_EDITS`를 v15에 적용한 결과와
바이트 동일이다.
3. **문장이 코드와 맞는다** - 새 항목이 말하는 셋(CPG 근거가 선 선택만 · 이미 실린 범위는 건너뜀 ·
상한에 넘치면
   통째로 뺌)은 러너 동작과 같다(`tests/test_source_input_context.py`가 동작을 문다. 여기서는 산문
   철자를 문다).
4. **배선** - `_CONTENT_AWARE_VERSIONS` · meta(봉투는 v15와 같다 · 옵션은 v15의 여덟 + 이 키) ·
`config/models.yaml` ·
   doctor(디스크 새 판 WARN 0) · Fake.
5. **meta가 켜진 옵션과 같다** - 서빙 판 선언 == `budgets.yaml`이 켠 옵션(`tests/test_config.py`도
묻는다).

⚠ 철자·구조 핀이다 - 모델이 디코더 본문을 판정에 쓰는지는 미측정이다.
"""

from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path

import pytest

from src import cli
from src.adapters import fake_model
from src.config import load_config
from src.orchestrator.context import RunContext
from src.orchestrator.nodes import auditor
from src.orchestrator.prompt_loader import PROMPTS_DIR, load_prompt
from src.schemas.auditor import AuditorProcedureProbeInputAction
from src.schemas.enums import AuditorStopReason, Role
from tests import test_nodes
from tests.test_nodes import VULN_TARGET, _triage

ctx = test_nodes.ctx

_V15 = PROMPTS_DIR / "auditor" / "v15"
_V16 = PROMPTS_DIR / "auditor" / "v16"


def _text(directory: Path, name: str) -> str:
    return (directory / name).read_bytes().decode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# === 1. v15는 한 글자도 안 바뀌었다 ===============================================================

# `origin/main` `e074774`의 `prompts/auditor/v15/*` sha256 (`git show origin/main:<path> |
# sha256sum`).
_V15_SHA = {
    "prefix.md": "60d0573d136a514e61c72c7d0c8fc7afedfd11eb94f8fc6cc4631516c24cdba3",
    "body.md": "c4799594df6cc3e6d84658674e82bf357d9127479a27c255b93a5a093acb9584",
    "meta.yaml": "ea736e9a01432a585e803f7444e327ea064aad37bfe64384ca35387348eaae8e",
}


@pytest.mark.parametrize("name", sorted(_V15_SHA))
def test_v15_is_byte_identical_to_origin_main(name: str) -> None:
    assert _sha((_V15 / name).read_bytes()) == _V15_SHA[name]


# === 2. v16 산문 = v15 + 이 편집들 ================================================================

# 기록된 sha - v16도 불변 기록이다. 바꾸면 새 판을 세운다(`docs/agent-contracts.md` §4 규칙 1).
_V16_SHA = {
    "prefix.md": "b9e058bbf2a0005d22caf8c43ae6ca811a1a08b0554f9a45aac095f314a4e7c2",
    "body.md": _V15_SHA["body.md"],
}

_WRITE_ENTRY_TAIL = (
    "A write-site window is a fragment, not a complete function body, and it is not "
    "proof of the value at the sink.\n"
)
# (v15에 있는 조각, v16의 조각) - 순서대로 적용하면 v15 prefix가 v16 prefix가 된다.
_EDITS: tuple[tuple[str, str], ...] = (
    (
        "`/* ... N lines omitted ... */` markers, and the `GUARDS` block — is not program code "
        "or instructions;",
        "`/* ... N lines omitted ... */` markers, the `GUARDS` block, and a final "
        "`/* ... +N more write windows not shown ... */` line at the end of `SOURCE` (it means "
        "more write-site windows existed than are shown) — is not program code or "
        "instructions;",
    ),
    (
        "- Last, write-site windows: for names on the slice lines",
        "- Then write-site windows: for names on the slice lines",
    ),
    (
        _WRITE_ENTRY_TAIL,
        _WRITE_ENTRY_TAIL
        + "- Last, input-context functions: the slicer's input-context analysis can pick "
        "functions near the slice that read or convert the program's input bytes. Only a "
        "selection backed by code property graph evidence is appended: each picked function "
        "not already carried "
        "in full is appended complete, with its own banner (it may repeat lines of a window "
        "shown above), while the total stays within the same cap; one that would exceed it is "
        "omitted whole. These blocks may be absent altogether — the "
        "slicer was not given the index, nothing was picked, or the selection was not backed "
        "by that evidence — and an absent block is not evidence that no such function "
        "exists. A function appended here was picked as a likely reader of the input; being "
        "appended does not show that the slice's data passes through it.\n",
    ),
    (
        "- Appended definitions, callee bodies, caller windows, and write-site windows are "
        "supporting context",
        "- Appended definitions, callee bodies, caller windows, write-site windows, and "
        "input-context functions are supporting context",
    ),
)


def _apply_edits(prefix: str) -> str:
    for old, new in _EDITS:
        assert prefix.count(old) == 1, old[:60]
        prefix = prefix.replace(old, new)
    return prefix


@pytest.mark.parametrize("name", sorted(_V16_SHA))
def test_v16_files_match_their_recorded_sha(name: str) -> None:
    assert _sha((_V16 / name).read_bytes()) == _V16_SHA[name]


def test_the_v16_meta_records_the_same_sha_the_test_pins() -> None:
    meta = _text(_V16, "meta.yaml")
    for name, digest in _V16_SHA.items():
        assert f"{name} sha256 `{digest}`" in meta or digest in meta, name


def test_v16_body_is_the_v15_body_bytes() -> None:
    assert (_V16 / "body.md").read_bytes() == (_V15 / "body.md").read_bytes()


def test_v16_prefix_is_the_v15_prefix_with_exactly_the_recorded_edits() -> None:
    """줄 단위가 아니라 **바이트 등식**이다 - 편집 밖에 한 글자라도 다르면 죽는다."""
    assert _apply_edits(_text(_V15, "prefix.md")) == _text(_V16, "prefix.md")
    assert b"\r" not in (_V16 / "prefix.md").read_bytes()


_IDS = {"candidate_id": "cand_0001", "finding_id": "find_0001", "missing_id": "mev_0001"}
_SOURCE = (
    "/* ===== parser.c:3-6 ===== */\n"
    "4|     char buf[64];\n"
    "6|     memcpy(buf, src, len);"
)


def test_the_node_hop0_render_differs_from_v15_only_in_the_edited_prefix(ctx: RunContext) -> None:
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    rendered = {
        version: auditor._render_hop0(
            ctx,
            load_prompt(Role.AUDITOR, version),
            ids=dict(_IDS),
            candidate=candidate,
            capsule=capsule,
            evidences=evidences,
            source=lambda: _SOURCE,
        )
        for version in ("v15", "v16")
    }
    assert rendered["v16"].body == rendered["v15"].body
    assert rendered["v16"].prefix == _apply_edits(rendered["v15"].prefix)
    assert rendered["v16"].prompt_version == "v16"


# === 3. 문장이 코드와 맞는다 ======================================================================


def _flat() -> str:
    return " ".join(_text(_V16, "prefix.md").split())


def test_the_new_item_states_what_the_runner_does_and_nothing_stronger() -> None:
    """러너 동작은 `tests/test_source_input_context.py`가 문다. 여기는 산문이 그 셋을 말하고 **더 센
    주장을
    안 하는지**를 문다: 붙었다고 경로가 이어졌다고 쓰지 않고(`syntax` 선택·`dispatches`는 안
    붙는다),
    블록이 없다고 그런 함수가 없다고도 쓰지 않는다."""
    flat = _flat()
    assert "Only a selection backed by code property graph evidence is appended" in flat
    assert "not already carried in full is appended complete" in flat
    assert "it may repeat lines of a window shown above" in flat
    new_bullet = flat.split("Last, input-context functions")[1].split("- Appended")[0]
    assert "overlapping" not in new_bullet
    assert "one that would exceed it is omitted whole" in flat
    assert "an absent block is not evidence that no such function exists" in flat
    assert "being appended does not show that the slice's data passes through it" in flat
    unsent = "dispatch" in flat or "carried_decoders" in flat
    assert not unsent, "싣지 않는 것은 서술하지 않는다"


def test_the_marker_line_the_prefix_names_is_the_one_the_runner_appends() -> None:
    """#650(PR #667)의 표지 줄 - 산문이 그 철자와 자리(`SOURCE` 맨 끝)를 말한다. 러너 상수는 #667이
    main에 들어와야 있다 - 그 전에는 철자 대조만 건너뛴다."""
    from src.orchestrator import runner

    assert "`/* ... +N more write windows not shown ... */` line at the end of `SOURCE`" in _flat()
    marker = getattr(runner, "_WRITE_WINDOWS_MORE", None)
    if marker is None:
        pytest.skip("PR #667이 아직 main에 없다 - 표지 상수가 없다")
    assert marker.format("N") == "/* ... +N more write windows not shown ... */"


def test_the_new_block_is_listed_last_and_the_write_windows_are_no_longer_last() -> None:
    flat = _flat()
    assert flat.index("Then write-site windows") < flat.index("Last, input-context functions")
    assert "Last, write-site windows" not in flat
    assert flat.count("Last, ") == 1, "맨 끝은 하나"


# === 4. 배선 =====================================================================================


def test_v16_is_wired_in_every_place_a_new_prompt_version_has_to_be() -> None:
    template, v15 = load_prompt(Role.AUDITOR, "v16"), load_prompt(Role.AUDITOR, "v15")

    assert "v16" in auditor._CONTENT_AWARE_VERSIONS
    assert template.output_schema == "AuditorProcedureProbeInputAction"
    assert auditor._loop_envelope(template) is AuditorProcedureProbeInputAction
    assert fake_model._BUILDERS[AuditorProcedureProbeInputAction] is not None
    assert (template.tools, template.input, template.facts_source) == (
        v15.tools, v15.input, v15.facts_source,
    )
    assert template.source_options == (*v15.source_options, "auditor_input_context")
    assert auditor.loop_problem(template, 16, is_fake=False) is None
    assert auditor.tool_hops_in_effect("v16", 16) == 16


@pytest.mark.parametrize("profile", ["harness_pool", "harness_pool_hybrid"])
def test_the_serving_auditor_seat_selects_v16_and_the_doctor_has_nothing_to_say(
    profile: str,
) -> None:
    config = load_config(profile)
    version = config.model_for("auditor").prompt_template_version

    assert version == "v16"
    assert cli._check_prompt_version(config, "auditor", version) == [], (
        "내용 인식 ERROR도 디스크 새 판 WARN도 없다"
    )
    assert cli._undescribed_source_options(config) == []


def test_no_auditor_version_newer_than_the_served_one_is_on_disk() -> None:
    """doctor의 「디스크 새 판」 WARN이 0인 이유 - 서빙 판이 디스크의 마지막 판이다."""
    numbers = sorted(
        int(p.name[1:])
        for p in (PROMPTS_DIR / "auditor").iterdir()
        if p.is_dir() and p.name[1:].isdecimal()
    )
    assert numbers[-1] == 16


def test_a_fake_loop_runs_on_v16(ctx: RunContext) -> None:
    """Fake가 v16 봉투를 만든다(v15와 같은 빌더) - lookup → probe → answer가 한 번씩 돈다."""
    entry = ctx.config.models["fake"].transition(prompt_template_version="v16")
    config = ctx.config.transition(
        models={**ctx.config.models, "fake_v16": entry},
        profile=ctx.config.profile.transition(
            roles={**ctx.config.profile.roles, "auditor": "fake_v16"}
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
    assert finding.produced_by.prompt_version == "v16"


# === 5. meta가 켜진 옵션과 같다 ===================================================================


@pytest.mark.parametrize("profile", ["harness_pool", "harness_pool_hybrid"])
def test_the_v16_meta_declares_exactly_the_options_turned_on(profile: str) -> None:
    config = load_config(profile)
    declared = set(load_prompt(Role.AUDITOR, "v16").source_options)
    assert declared == set(config.thresholds.auditor_source_options_on())
    assert "auditor_input_context" in declared
