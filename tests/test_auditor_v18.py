"""Auditor v18 - v17의 「Harness note (stage 2)」 문단 두 문장을 하네스 동작에 맞춘 판 (이슈 #698).

v17은 「`build`·`asan`·`gdb`는 같은 컴파일 명령이라 같은 오류가 예상된다」·「it refuses no tool: a
`probe`
you still choose runs as usual」이라 적었다. #698부터 컨테이너 어댑터가 이 run에서 컴파일러 오류로
실패한
앞단 컴파일 명령을 다시 띄우지 않고 그 측정을 돌려주므로 두 문장이 거짓이 됐다. 묶는 것은 넷이다.

1. **v17은 한 글자도 안 바뀌었다** - sha가 v17 meta가 적은 값이다(규칙 1).
2. **v18 산문 = v17 + 이 편집** - body는 바이트 동일, prefix는 `_EDIT`를 적용한 결과와 바이트 동일.
3. **문장이 코드와 맞는다** - 그 파일의 `asan` probe가 정말로 2단계 실패(Evidence id · 건수 · 첫
오류)를
   돌려주고 프로세스를 안 띄운다.
4. **배선** - `_CONTENT_AWARE_VERSIONS` · `config/models.yaml` · doctor · Fake.

⏵ 이슈 #602 - 편집이 하나 더 있다: 디코더가 받는 JSON 스키마가 판별 합이 되어(봉투
`AuditorProcedureProbeInputDiscriminatedAction`, `tests/test_auditor_v18_envelope.py`)
prefix의 「JSON Schema does not enforce …」 문장을 고쳤다.

⚠ 철자·구조 핀이다 - 고친 문장이 모델의 probe 선택을 바꾸는지는 미측정이다.
"""

from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path

import pytest

from src.orchestrator.context import RunContext
from src.orchestrator.nodes import auditor
from src.orchestrator.prompt_loader import PROMPTS_DIR, load_prompt
from src.schemas.enums import AuditorStopReason, Role
from tests import test_nodes
from tests.test_auditor_v17 import _SOURCE, _hop0
from tests.test_nodes import VULN_TARGET, _triage

ctx = test_nodes.ctx

_V17 = PROMPTS_DIR / "auditor" / "v17"
_V18 = PROMPTS_DIR / "auditor" / "v18"


def _text(directory: Path, name: str) -> str:
    return (directory / name).read_bytes().decode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# === 1. v17은 한 글자도 안 바뀌었다 ===============================================================

# `prompts/auditor/v17/meta.yaml`이 적은 값.
_V17_SHA = {
    "prefix.md": "8905905f4ab2fd1b744eb63696f613be0855eab2b83f74f11c5ea58ca465162a",
    "body.md": "c4799594df6cc3e6d84658674e82bf357d9127479a27c255b93a5a093acb9584",
}


@pytest.mark.parametrize("name", sorted(_V17_SHA))
def test_v17_is_byte_identical(name: str) -> None:
    assert _sha((_V17 / name).read_bytes()) == _V17_SHA[name]


# === 2. v18 산문 = v17 + 이 편집 ==================================================================

_V18_SHA = {
    "prefix.md": "39ec93df361e1ba4a9f1f36ed04221110070023d236d7afa826187e9570df199",
    "body.md": "c4799594df6cc3e6d84658674e82bf357d9127479a27c255b93a5a093acb9584",
}

_OLD = (
    "Where it says `build` failed with compiler errors, `build`, `asan`, and `gdb` use the "
    "same compile command, so the same errors are expected from them; `poc` does not use that"
    " command. It is a measurement and not a verdict on `SOURCE`, and it refuses no tool: a "
    "`probe` you still choose runs as usual."
)
_NEW = (
    "Where it says `build` failed with compiler errors, `build`, `asan`, and `gdb` "
    "are not offered for this target in this run — the `tool` field does not accept "
    "them — because their compile command differs from the stage-2 one at most in "
    "`-O`/`-g`/`-c`/`-o`/`-fsanitize`/`-fno-omit-frame-pointer`; where `clang-tidy` has "
    "compiler errors, "
    "`clang-tidy` is not offered either. Other files are not covered by the section, "
    "and `poc` does not use that command. It is a measurement and not a verdict on "
    "`SOURCE`."
)


# 이슈 #602 - 디코더 스키마가 판별 합이 되어 「스키마는 step 4 규칙을 안 집행한다」가 거짓이 됐다.
_OLD_SCHEMA = (
    "The JSON Schema does not enforce symbol existence, registered evidence IDs, or the step 4 "
    "consistency rules; those are mandatory semantic rules of this prompt. The host does check "
    "one of them: an `answer` whose trigger_status is no_signal without all three of `none`, "
    "`not_applicable`, and `no_candidate`, or that uses any of the three with another status, "
    "comes back with the error for you to correct."
)
_NEW_SCHEMA = (
    "The JSON Schema does not enforce symbol existence, registered evidence IDs, or most of the "
    "step 4 consistency rules; those are mandatory semantic rules of this prompt. It does "
    "enforce one of them: it accepts a no_signal `procedure` only with all three of `none`, "
    '`not_applicable`, and `no_candidate` and with `"cwe": null`, and a `procedure` with '
    "another trigger_status only with none of the three and a non-null `cwe`. The host checks "
    "the three values again on the parsed result, and an `answer` that breaks them comes back "
    "with the error for you to correct."
)
_EDITS = ((_OLD, _NEW), (_OLD_SCHEMA, _NEW_SCHEMA))


def _apply_edit(prefix: str) -> str:
    for old, new in _EDITS:
        assert prefix.count(old) == 1
        prefix = prefix.replace(old, new)
    return prefix


@pytest.mark.parametrize("name", sorted(_V18_SHA))
def test_v18_files_match_their_recorded_sha(name: str) -> None:
    assert _sha((_V18 / name).read_bytes()) == _V18_SHA[name]


def test_the_v18_meta_records_the_same_sha_the_test_pins() -> None:
    meta = _text(_V18, "meta.yaml")
    for name, digest in _V18_SHA.items():
        assert f"sha256 `{digest}`" in meta, name


def test_v18_body_is_the_v17_body_bytes() -> None:
    assert (_V18 / "body.md").read_bytes() == (_V17 / "body.md").read_bytes()


def test_v18_prefix_is_the_v17_prefix_with_exactly_the_recorded_edit() -> None:
    """바이트 등식이다 - 편집 밖에 한 글자라도 다르면 죽는다."""
    assert _apply_edit(_text(_V17, "prefix.md")) == _text(_V18, "prefix.md")
    assert b"\r" not in (_V18 / "prefix.md").read_bytes()


def test_the_node_hop0_render_differs_from_v17_only_in_the_edited_prefix(ctx: RunContext) -> None:
    rendered = _hop0(ctx, ("v17", "v18"))

    assert rendered["v18"].body == rendered["v17"].body
    assert rendered["v18"].prefix == _apply_edit(rendered["v17"].prefix)
    assert rendered["v18"].prompt_version == "v18"


# === 3. 문장이 코드와 맞는다 ======================================================================


def test_the_sentence_names_exactly_the_tools_the_node_withholds() -> None:
    """`_NEW`가 「제시되지 않는다」고 부르는 도구 = `signals.withheld_tools`가 실제로 빼는 도구."""
    from src.orchestrator.nodes import signals
    from tests.test_compile_note import _build_failure, _tidy_failure

    assert signals.withheld_tools([_build_failure()]) == ("build", "asan", "gdb")
    assert signals.withheld_tools([_tidy_failure()]) == ("clang-tidy",)
    assert "`build`, `asan`, and `gdb` are not offered" in _NEW
    assert "`clang-tidy` is not offered either" in _NEW


# === 4. 배선 =====================================================================================


def test_v18_is_wired_in_every_place_a_new_prompt_version_has_to_be() -> None:
    template, v17 = load_prompt(Role.AUDITOR, "v18"), load_prompt(Role.AUDITOR, "v17")

    assert "v18" in auditor._CONTENT_AWARE_VERSIONS
    # 봉투만 다르다(이슈 #602) - v17의 서브클래스이고 도구·입력 선언은 v17과 같다.
    assert v17.output_schema == "AuditorProcedureProbeInputAction"
    assert template.output_schema == "AuditorProcedureProbeInputDiscriminatedAction"
    assert (template.tools, template.input, template.facts_source) == (
        v17.tools,
        v17.input,
        v17.facts_source,
    )
    assert template.source_options == v17.source_options
    assert auditor.loop_problem(template, 16, is_fake=False) is None
    assert auditor.tool_hops_in_effect("v18", 16) == 16


# ⏵ 2026-10-17 - 서빙 판 검사(좌석이 이 판을 고른다 · 디스크에 더 새 판이 없다)는 v19로 옮겼다
# (`tests/test_auditor_v19.py`, 이슈 #702).


def test_a_fake_loop_runs_on_v18(ctx: RunContext) -> None:
    entry = ctx.config.models["fake"].transition(prompt_template_version="v18")
    config = ctx.config.transition(
        models={**ctx.config.models, "fake_v18": entry},
        profile=ctx.config.profile.transition(
            roles={**ctx.config.profile.roles, "auditor": "fake_v18"}
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
    assert finding.produced_by.prompt_version == "v18"
