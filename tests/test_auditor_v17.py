"""Auditor v17 - v16에 슬라이스 상태 서술을 더하고 「배너 없으면 첫 블록 하나」 문장을 정정한 판
(S20 Phase 14 · 이슈 #438 (b) · #669 Auditor 몫).

두 가지를 한 판에 묻었다. ① 새 옵션 `auditor_slice_state`(`config/budgets.yaml` 0 - 켜는 것은
측정 결정)가 켜지면 FACTS에 붙는 `slice_state` 키의 서술. ② v16 prefix의 「배너가 없으면 `SOURCE`는
첫 블록 하나」가 쓰기 창이 전부 크기 상한에 떨어진 경우에 거짓이던 문장의 정정(이슈 #669 -
Critic v7·PoV v6 몫은 이 판이 안 고친다). 묶는 것은 다섯이다.

1. **v16은 한 글자도 안 바뀌었다** - prefix·body·meta의 sha256이 `origin/feat/p13-input-context`
   (`4e3790a`)의 값이다(규칙 1).
2. **v17 산문 = v16 + 이 편집들** - body는 바이트 동일이고 prefix는 `_EDITS`를 v16에 적용한
   결과와 바이트 동일이다. 편집은 두 군데다.
3. **문장이 코드와 맞는다** - 정정한 문장은 러너가 실제로 내는 `SOURCE`(배너 없는 entry 함수 +
   끝 줄 표지)와 맞고, 새 항목이 부르는 키·사유 철자는 `slice_state.facts`와 슬라이서 소스에서 온다.
4. **배선** - `_CONTENT_AWARE_VERSIONS` · meta · `config/models.yaml` · doctor · Fake.
5. **meta가 옵션과 맞는다** - 선언 == 켜진 옵션 + 측정 대기로 끈 옵션(`auditor_slice_state`).

⚠ 철자·구조 핀이다 - 모델이 `slice_state`를 판정에 쓰는지는 미측정이다.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from pathlib import Path

import pytest

from src import cli
from src.adapters import fake_model
from src.config import load_config
from src.orchestrator import slice_state
from src.orchestrator.context import RunContext
from src.orchestrator.nodes import auditor
from src.orchestrator.prompt_loader import PROMPTS_DIR, load_prompt
from src.schemas.auditor import AuditorProcedureProbeInputAction
from src.schemas.candidate import SliceRef
from src.schemas.common import Location
from src.schemas.enums import AuditorStopReason, Role
from tests import test_nodes
from tests.test_nodes import VULN_TARGET, _triage
from tests.test_slice_targets import _unit, _unit_run

ctx = test_nodes.ctx

_V16 = PROMPTS_DIR / "auditor" / "v16"
_V17 = PROMPTS_DIR / "auditor" / "v17"
_REPO = Path(__file__).resolve().parent.parent


def _text(directory: Path, name: str) -> str:
    return (directory / name).read_bytes().decode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# === 1. v16은 한 글자도 안 바뀌었다 ===============================================================

# `origin/feat/p13-input-context` `4e3790a`의 `prompts/auditor/v16/*` sha256
# (`git show <ref>:<path> | sha256sum`).
_V16_SHA = {
    "prefix.md": "b9e4106a21be8ae1bb763a3eec9adaa230cefdd056eab5af12ea609530ea4aa7",
    "body.md": "c4799594df6cc3e6d84658674e82bf357d9127479a27c255b93a5a093acb9584",
    "meta.yaml": "efb0776cd25dc6973463f88940bc0c49e1568620e49ac27650ac1d9cf9fb05d4",
}


@pytest.mark.parametrize("name", sorted(_V16_SHA))
def test_v16_is_byte_identical_to_the_branch_it_was_cut_from(name: str) -> None:
    assert _sha((_V16 / name).read_bytes()) == _V16_SHA[name]


# === 2. v17 산문 = v16 + 이 편집들 ================================================================

# 기록된 sha - v17도 불변 기록이다. 바꾸면 새 판을 세운다(`docs/agent-contracts.md` §4 규칙 1).
_V17_SHA = {
    "prefix.md": "e8b07c6c442283f0b369c215960801ea811c6435a2adeafb4547eb2e359ab29d",
    "body.md": _V16_SHA["body.md"],
}

_NO_BANNER_OLD = (
    "with no banner, `SOURCE` is the first block alone.\n"
)
_NO_BANNER_NEW = (
    "with no banner, `SOURCE` is the first block alone, except that the final "
    "`/* ... +N more write windows not shown ... */` line can still follow it: when write-site "
    "windows existed but every one was left out (for size or the count limit), that line is "
    "all that follows the first block.\n"
    "- `FACTS.slice_state`, when present, holds what the slicer recorded about the slice this "
    "`SOURCE` was built from; it is not source text and adds nothing to `SOURCE`. "
    "`analysis_kind` names the analysis the slicer ran (for example `parameter_paths`, "
    "`backward_ddg`, `forward_ddg`; `degraded_anchor_only` and `degraded_lexical` are fallbacks) "
    "— a method label, not the slice criterion this audit does not receive. `degraded: true` "
    "means the slicer fell back to a slice with no data-flow context (anchor lines only, or "
    "lines located by text). `truncated: true` means the slicer cut its analysis at a limit; "
    "`truncation_reasons` names what cut it (for example `backward_depth`, `backward_states`, "
    "`call_depth`) and is `null` when the slicer records no reasons for that analysis, even if "
    "`truncated` is true; `[]` means it recorded reasons and found none. `truncated: false` "
    "only means no such limit was reached; other caps are reported by their own keys below, "
    "not by `truncated`. `sinks_capped: true` means a `parameter_paths` analysis cut the set "
    "of program-wide sinks it follows flows to at a cap (the candidate function's own sinks "
    "are always kept), so flows to sinks beyond it are absent; "
    "`cross_method_flow_truncated: true` means a targeted-mode analysis kept only a bounded "
    "number of its cross-method flow edges. Each of the two is `true` or `false` only when the "
    "slicer records it for that analysis, and `null` otherwise. `boundary_reasons` "
    "lists the distinct reasons for which a dependency trace stopped, for example "
    "`entry_parameter` (a function parameter was reached and the code property graph holds no "
    "call to that function — the graph did not link a caller; it does not show that no caller "
    "exists or that the value is unchecked), `external_or_unresolved_call` (a call the graph "
    "could not resolve to a function body) or `dependency_root` (no earlier dependency to "
    "follow); the slicer may write other reasons, and it is `null` when the slicer does not "
    "record boundaries for that analysis, which is not a finding; `[]` means it recorded them "
    "and none were reached. A `null` value means the slicer did not record "
    "that fact for this slice: unknown, not `false`, not an empty list and not complete. When "
    "`FACTS.slice_state` is absent the harness did not report the slice state; absence is not "
    "evidence that the slice is complete. A truncated or degraded slice may lack lines that "
    "connect the input to the sink, and that gap is unobserved, not absent (Absence is not "
    "evidence).\n"
)
# (v16에 있는 조각, v17의 조각) - 순서대로 적용하면 v16 prefix가 v17 prefix가 된다.
_EDITS: tuple[tuple[str, str], ...] = ((_NO_BANNER_OLD, _NO_BANNER_NEW),)


def _apply_edits(prefix: str) -> str:
    for old, new in _EDITS:
        assert prefix.count(old) == 1, old[:60]
        prefix = prefix.replace(old, new)
    return prefix


@pytest.mark.parametrize("name", sorted(_V17_SHA))
def test_v17_files_match_their_recorded_sha(name: str) -> None:
    assert _sha((_V17 / name).read_bytes()) == _V17_SHA[name]


def test_the_v17_meta_records_the_same_sha_the_test_pins() -> None:
    meta = _text(_V17, "meta.yaml")
    for name, digest in _V17_SHA.items():
        assert f"{name} sha256 `{digest}`" in meta or digest in meta, name


def test_v17_body_is_the_v16_body_bytes() -> None:
    assert (_V17 / "body.md").read_bytes() == (_V16 / "body.md").read_bytes()


def test_v17_prefix_is_the_v16_prefix_with_exactly_the_recorded_edits() -> None:
    """줄 단위가 아니라 **바이트 등식**이다 - 편집 밖에 한 글자라도 다르면 죽는다."""
    assert _apply_edits(_text(_V16, "prefix.md")) == _text(_V17, "prefix.md")
    assert b"\r" not in (_V17 / "prefix.md").read_bytes()


_IDS = {"candidate_id": "cand_0001", "finding_id": "find_0001", "missing_id": "mev_0001"}
_SOURCE = (
    "/* ===== parser.c:3-6 ===== */\n"
    "4|     char buf[64];\n"
    "6|     memcpy(buf, src, len);"
)


def _hop0(ctx: RunContext, versions: tuple[str, ...], **kwargs):  # noqa: ANN202, ANN003
    """한 후보(`_triage` 한 번)로 판마다 hop0를 렌더한다 - 판마다 후보를 만들면 ID가 달라진다."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    return {
        version: auditor._render_hop0(
            ctx,
            load_prompt(Role.AUDITOR, version),
            ids=dict(_IDS),
            candidate=candidate,
            capsule=capsule,
            evidences=evidences,
            source=lambda: _SOURCE,
            **kwargs,
        )
        for version in versions
    }


def test_the_node_hop0_render_differs_from_v16_only_in_the_edited_prefix(ctx: RunContext) -> None:
    """옵션이 꺼진 렌더(`slice_state` 없음)는 v16과 body(SOURCE·FACTS 바이트)가 같다 - 판만 올려도
    입력이 안 바뀐다."""
    rendered = _hop0(ctx, ("v16", "v17"))

    assert rendered["v17"].body == rendered["v16"].body
    assert rendered["v17"].prefix == _apply_edits(rendered["v16"].prefix)
    assert rendered["v17"].prompt_version == "v17"
    assert "slice_state" not in rendered["v17"].body


def test_a_given_slice_state_adds_exactly_one_facts_key(ctx: RunContext) -> None:
    state = {"analysis_kind": None, "degraded": None, "truncated": None,
             "truncation_reasons": None, "boundary_reasons": None}
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    template = load_prompt(Role.AUDITOR, "v17")
    kwargs = {"ids": dict(_IDS), "candidate": candidate, "capsule": capsule,
              "evidences": evidences, "source": lambda: _SOURCE}
    off = auditor._render_hop0(ctx, template, **kwargs)
    on = auditor._render_hop0(ctx, template, slice_state=state, **kwargs)

    assert off.prefix == on.prefix
    assert '"slice_state": {' in on.body and '"slice_state"' not in off.body
    # null은 JSON null로 간다 - `false`로 바뀌지 않는다.
    assert '"truncated": null' in on.body and '"truncated": false' not in on.body
    assert on.body.replace(re.search(r'  "slice_state": \{.*?\n  \},\n', on.body, re.S).group(0),
                           "") == off.body


# === 3. 문장이 코드와 맞는다 ======================================================================


def _flat() -> str:
    return " ".join(_text(_V17, "prefix.md").split())


def _slice_state_bullet() -> str:
    flat = _flat()
    after = flat.split("- `FACTS.slice_state`, when present")[1]
    return after.split("Only when a run uses no slice")[0]


def test_the_corrected_sentence_matches_the_source_when_every_write_window_drops() -> None:
    """이슈 #669 - 쓰기 창이 전부 크기 상한에 떨어지면 `SOURCE`는 배너 없는 entry 함수 + 끝 줄
    표지다. v16은 이때 「첫 블록 하나」라고 해서 거짓이었다. 러너가 그 모양을 내고(배너 없음 · 끝
    줄 표지) v17 산문이 그것을 말한다."""
    from tests.test_slice_targets import _WS_F, _WS_FILE, _WS_INIT, _WS_SETUP

    unit, events, _, _ = _unit(
        _WS_FILE, [_WS_F, _WS_SETUP, _WS_INIT], auditor_write_sites=6, auditor_source_tokens=20,
        writes={"dst": [Location(file="w.c", symbol="init", line_start=41, line_end=41)]},
        field_writes={"len": [Location(file="w.c", symbol="setup", line_start=22, line_end=22)]},
    )
    source, event = _unit_run(unit, events, _WS_F, [("w.c", 2)])

    assert event["write_windows"] == 0 and event["write_windows_dropped"] > 0
    assert "/* =====" not in source, "배너가 없다"
    marker_line = source.splitlines()[-1]
    assert marker_line == "/* ... +2 more write windows not shown ... */"
    first_block = "\n".join(source.splitlines()[:-1])
    assert first_block == "\n".join(_WS_FILE["w.c"][0:3]), "표지 앞은 entry 함수 하나뿐이다"
    flat = _flat()
    assert (
        "with no banner, `SOURCE` is the first block alone, except that the final "
        "`/* ... +N more write windows not shown ... */` line can still follow it" in flat
    )
    assert "when write-site windows existed but every one was left out (for size or the" in flat
    assert "count limit), that line is all that follows the first block" in flat
    assert "with no banner, `SOURCE` is the first block alone." not in flat, "v16의 거짓 문장"


def test_the_corrected_sentence_still_holds_when_nothing_was_omitted() -> None:
    """표지가 없을 때(창이 없거나 다 실렸다)는 배너 없는 `SOURCE`가 정말 첫 블록 하나다 - 정정은
    예외를 더한 것이지 규칙을 뒤집은 것이 아니다."""
    from tests.test_slice_targets import _WS_F, _WS_FILE, _WS_INIT, _WS_SETUP

    unit, events, _, _ = _unit(_WS_FILE, [_WS_F, _WS_SETUP, _WS_INIT], auditor_write_sites=0)
    source, _ = _unit_run(unit, events, _WS_F, [("w.c", 2)])

    assert "/* =====" not in source and "more write windows" not in source
    assert source == "\n".join(_WS_FILE["w.c"][0:3])


def test_the_slice_state_bullet_names_exactly_the_keys_the_harness_sends() -> None:
    """키 이름은 `slice_state.facts`가 내는 것과 같다 - 산문이 말하는 키가 하나라도 없거나
    하나라도 더 있으면 모델이 없는 값을 읽거나 있는 값을 못 읽는다."""
    ref = SliceRef(
        slice_id="s", location=Location(file="a.c", line_start=1), given_end="sink",
        entry=Location(file="a.c", line_start=1), lines=(("a.c", 1),),
    )
    bullet = _slice_state_bullet()

    for key in slice_state.facts(ref):
        assert f"`{key}" in bullet, key
    assert "`slice_state`" in bullet or "`FACTS.slice_state`" in _flat()


def test_the_spellings_the_bullet_uses_are_the_ones_the_slicer_writes() -> None:
    """분석 종류 다섯 · 절단 사유 셋 · 경계 사유 셋의 철자는 슬라이서 소스(`tools/slicer` ·
    대상 지정 모드 `tools/slicer_v2`)에서 온다 - 산문이 지어낸 낱말이 아니다(CLAUDE.md §2.5 「핀
    샘플은 저장소에서 온다」). 산문의 열거는 「for example」이다 - 값 집합은 닫혀 있지 않다(대상
    지정 모드가 `forward_ddg`·`degraded_lexical`을 더 쓴다)."""
    pipeline = (_REPO / "tools/slicer/slicer_pipeline.py").read_text(encoding="utf-8")
    backward = (_REPO / "tools/slicer/slicer_backward.py").read_text(encoding="utf-8")
    targeted = (_REPO / "tools/slicer_v2/slicer_target.py").read_text(encoding="utf-8")
    bullet = _slice_state_bullet()

    for kind in ("parameter_paths", "backward_ddg", "degraded_anchor_only"):
        assert f'"{kind}"' in pipeline and f"`{kind}`" in bullet, kind
    # 대상 지정 모드는 `f"{direction}_ddg"`로 쓴다 - 방향이 `forward`이면 `forward_ddg`다.
    assert 'f"{direction}_ddg"' in targeted and '{"backward": "B", "forward": "F"}' in targeted
    assert "`forward_ddg`" in bullet
    assert '"degraded_lexical"' in targeted and "`degraded_lexical`" in bullet
    assert "for example `parameter_paths`" in bullet, "값 집합이 닫혀 있다고 쓰지 않는다"
    queries = (_REPO / "tools/slicer/slicer_queries.py").read_text(encoding="utf-8")
    assert '"sinks_capped" ->' in queries and "`sinks_capped: true`" in bullet
    assert '"cross_method_flow_truncated" ->' in targeted
    assert "`cross_method_flow_truncated: true`" in bullet
    for reason in ("backward_depth", "backward_states", "call_depth"):
        assert f'"{reason}"' in backward and f"`{reason}`" in bullet, reason
    for reason in ("entry_parameter", "external_or_unresolved_call", "dependency_root"):
        assert f'"{reason}"' in backward and f"`{reason}`" in bullet, reason


def test_the_bullet_makes_no_claim_stronger_than_the_facts() -> None:
    """`entry_parameter`는 그래프가 호출자를 **못 이었다**이지 호출자가 **없다**가 아니고 검증이
    없다는 뜻도 아니다. 모름(`null`)은 `false`·완전함이 아니고, 키가 없는 것도 완전함의 증거가
    아니다. 사유 목록이 빈 것은 발견이 아니고, 사유 집합이 닫혀 있다고도 쓰지 않는다."""
    bullet = _slice_state_bullet()

    assert (
        "the graph did not link a caller; it does not show that no caller exists or that the "
        "value is unchecked" in bullet
    )
    assert "unknown, not `false`, not an empty list and not complete" in bullet
    assert "absence is not evidence that the slice is complete" in bullet
    assert "which is not a finding" in bullet
    assert "is `null` when the slicer records no reasons for that analysis, even if" in bullet
    assert "`[]` means it recorded reasons and found none" in bullet
    assert "it is `null` when the slicer does not record boundaries for that analysis" in bullet
    assert "`[]` means it recorded them and none were reached" in bullet
    # `truncated: false`는 흐름 수 상한 하나에 안 닿았다는 뜻일 뿐이다 - 다른 상한은 제 키가 말한다
    # (리뷰 MEDIUM-1: Case A 싱크 cap이 모델에게 `truncated: false`로 보이던 구멍).
    assert "`truncated: false` only means no such limit was reached" in bullet
    assert "other caps are reported by their own keys below, not by `truncated`" in bullet
    assert "`true` or `false` only when the slicer records it for that analysis" in bullet
    assert "the slicer may write other reasons" in bullet
    assert "that gap is unobserved, not absent" in bullet
    for stronger in ("no caller exists." , "is safe", "is unreachable", "proves"):
        assert stronger not in bullet, stronger


def test_the_prefix_still_says_the_audit_does_not_receive_the_slice_criterion() -> None:
    """v16의 그 문장을 안 건드렸다 - `analysis_kind`는 방법 이름이지 그 기준이 아니라고 새 항목이
    따로 적는다."""
    flat = _flat()
    assert "This audit does not receive the slice criterion either" in flat
    assert "not the slice criterion this audit does not receive" in _slice_state_bullet()


# === 4. 배선 =====================================================================================


def test_v17_is_wired_in_every_place_a_new_prompt_version_has_to_be() -> None:
    template, v16 = load_prompt(Role.AUDITOR, "v17"), load_prompt(Role.AUDITOR, "v16")

    assert "v17" in auditor._CONTENT_AWARE_VERSIONS
    assert template.output_schema == "AuditorProcedureProbeInputAction"
    assert auditor._loop_envelope(template) is AuditorProcedureProbeInputAction
    assert fake_model._BUILDERS[AuditorProcedureProbeInputAction] is not None
    assert (template.tools, template.input, template.facts_source) == (
        v16.tools, v16.input, v16.facts_source,
    )
    assert template.source_options == (*v16.source_options, "auditor_slice_state")
    assert auditor.loop_problem(template, 16, is_fake=False) is None
    assert auditor.tool_hops_in_effect("v17", 16) == 16


@pytest.mark.parametrize("profile", ["harness_pool", "harness_pool_hybrid"])
def test_the_serving_auditor_seat_selects_v17_and_the_doctor_has_nothing_to_say(
    profile: str,
) -> None:
    config = load_config(profile)
    version = config.model_for("auditor").prompt_template_version

    assert version == "v17"
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
    assert numbers[-1] == 17


def test_turning_the_option_on_is_blocked_before_v17_and_allowed_on_it() -> None:
    """켜는 것은 측정 결정이지만 켜는 길은 열려 있어야 한다 - v17을 고르고 켜면 막히지 않고 v16
    이하를 고르고 켜면 `cli`가 막는다(모델 입력에 서술 없는 키가 실리므로)."""
    config = load_config("harness_pool")
    key = config.model_key_for("auditor")
    on = config.thresholds.transition(auditor_slice_state=1)

    def with_version(version: str):  # noqa: ANN202
        entry = config.models[key].transition(prompt_template_version=version)
        return config.transition(models={**config.models, key: entry}, thresholds=on)

    assert cli._undescribed_source_options(with_version("v17")) == []
    assert cli._undescribed_source_options(with_version("v16")) == [
        "auditor_slice_state(auditor/v16)"
    ]
    assert cli._undescribed_source_options(with_version("v15")) == [
        "auditor_input_context(auditor/v15)", "auditor_slice_state(auditor/v15)",
    ]


def test_a_fake_loop_runs_on_v17(ctx: RunContext) -> None:
    """Fake가 v17 봉투를 만든다(v15·v16과 같은 빌더) - lookup → probe → answer가 한 번씩 돈다.
    옵션을 켜고 `slice_state`를 줘도(FakeContext가 키를 받는다) 돈다."""
    entry = ctx.config.models["fake"].transition(prompt_template_version="v17")
    config = ctx.config.transition(
        models={**ctx.config.models, "fake_v17": entry},
        profile=ctx.config.profile.transition(
            roles={**ctx.config.profile.roles, "auditor": "fake_v17"}
        ),
        thresholds=ctx.config.thresholds.transition(auditor_tool_hops=4),
    )
    armed = dataclasses.replace(ctx, config=config)
    candidate, capsule, evidences = _triage(armed, VULN_TARGET)

    finding, _ = auditor.run(
        armed, candidate=candidate, capsule=capsule, evidences=evidences, source=lambda: _SOURCE,
        slice_state={"analysis_kind": "backward_ddg", "degraded": False, "truncated": False,
                     "truncation_reasons": [], "boundary_reasons": []},
    )

    hops = [e.data for e in armed.trace.events if e.kind.value == "auditor_hop"]
    assert [h["action"] for h in hops] == ["lookup", "probe", "answer"]
    assert hops[-1]["stop_reason"] == AuditorStopReason.ANSWERED.value
    assert finding.produced_by.prompt_version == "v17"


# === 5. meta가 옵션과 맞는다 =====================================================================


@pytest.mark.parametrize("profile", ["harness_pool", "harness_pool_hybrid"])
def test_the_v17_meta_declares_the_options_turned_on_plus_the_one_pending_measurement(
    profile: str,
) -> None:
    """선언 == 켜진 옵션 + `auditor_slice_state`. 그 하나는 **끈 채로 선언**한다 - 같은 판에서 켠
    run ↔ 끈 run을 짝 비교하려면(D101 「닫히는 조건」) 판이 둘을 다 서술해야 한다. 그 서술은 키가
    없을 때도 참이다(`absence is not evidence`). 다른 끈 옵션을 선언하면 이 검사가 죽는다."""
    config = load_config(profile)
    declared = set(load_prompt(Role.AUDITOR, "v17").source_options)
    on = set(config.thresholds.auditor_source_options_on())

    assert declared - on == {"auditor_slice_state"}
    assert on <= declared
    assert config.thresholds.auditor_slice_state == 0
