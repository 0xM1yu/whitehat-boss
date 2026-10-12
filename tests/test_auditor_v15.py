"""Auditor v15 — v14에 계약 문장 여섯을 더한 판 (S19 · council 2026-10-13).

이슈: #419 #507 #562 #600 #602. v15가 적는 여섯은 이렇다.

- `probe`가 `spec` 바이트를 `poc`에 싣는다(읽는 도구는 `poc`뿐).
- 조회·도구 응답이 Critic에도 간다(모델이 고른 식별자는 가린 사본).
- `triggerable`은 `probe` 뒤에 답한다 — **지시일 뿐 하네스는 검사하지 않는다**.
- 「유계 발췌」 문장을 빼고 `K of N shown` 줄이 접힘 표지임을 적는다.
- `answer`의 `procedure`가 비면 스키마가 거절한다.
- hop-0 끝에 2단계 컴파일 실패 노트가 붙을 수 있다.

묶는 것은 여섯이다.

1. **v14는 한 글자도 안 바뀌었다** — v14 prefix·body·meta의 sha256이 `origin/main`(`5b68a69`)의
   값이고, v8~v14 봉투 넷의 요청 스키마(JSON)도 같다(판 번호 하나가 두 요청을 가리키지 않는다 ·
   agent-contracts §4 규칙 1).
2. **v15 산문 = v14 + 이 편집들** — body는 바이트 동일이고 prefix는 `_EDITS`를 v14에 적용한
   결과와 바이트 동일이다. 노드의 hop-0 렌더(`_render_hop0`)도 prefix만 그 편집만큼 다르다.
3. **문장이 코드와 맞는다** — `answer`의 빈 `procedure` 거절 문구 · `triggerable` 무검사(`probe`
   0회 답을 받는다) · 반복 키(`_input_aware_key`)가 `poc`에 한해 입력 바이트를 본다 ·
   `K of N shown` 줄은 접기 성공 경로에서 나오고 접히지 않은 로그의 절단은 다른 표지다.
4. **배선** — `_CONTENT_AWARE_VERSIONS` · meta의 봉투 · `config/models.yaml` · doctor(디스크 새 판
   WARN 0) · Fake.
5. **봉투** — 새 `AuditorProcedureProbeInputAction`은 v12 봉투의 서브클래스이고 `spec`·클래스
   설명만 다르다.
6. **의존 PR 핀** — #644(`probe`가 `spec`을 싣는다) · #600(Critic이 홉 관측을 받는다) · #648(hop-0
   컴파일 노트)이 main에 들어오면 켜지는 핀이다. 들어오기 전에는 skip이고 사유에 그 사실을 적는다.

⚠ 철자·구조 핀이다 — 모델이 `spec`을 보내는지 · `probe`를 `triggerable` 앞에 두는지는 미측정이다.
"""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from src import cli
from src.adapters import fake_model
from src.config import load_config
from src.orchestrator.context import RunContext
from src.orchestrator.nodes import auditor, signals
from src.orchestrator.prompt_loader import PROMPTS_DIR, load_prompt
from src.schemas import auditor as schemas
from src.schemas.auditor import (
    AuditorAction,
    AuditorProcedureAction,
    AuditorProcedureAuditAlwaysAction,
    AuditorProcedureNullableCweAction,
    AuditorProcedureProbeInputAction,
)
from src.schemas.enums import AuditorStopReason, Role
from src.schemas.pov import PovSegment, PovSpec
from tests import test_nodes
from tests.test_auditor_loop import _ScriptedModel
from tests.test_auditor_v12 import _payload
from tests.test_nodes import VULN_TARGET, _triage

ctx = test_nodes.ctx

_V14 = PROMPTS_DIR / "auditor" / "v14"
_V15 = PROMPTS_DIR / "auditor" / "v15"


def _text(directory: Path, name: str) -> str:
    return (directory / name).read_bytes().decode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# === 1. v14는 한 글자도 안 바뀌었다 ===============================================================

# `origin/main` `5b68a69`의 `prompts/auditor/v14/*` sha256
# (`git show origin/main:<path> | sha256sum`).
_V14_SHA = {
    "prefix.md": "8ffb683f45bd6455c633549fce6c7d6da8a1842945db49bc1ceb09ec5be1c680",
    "body.md": "c4799594df6cc3e6d84658674e82bf357d9127479a27c255b93a5a093acb9584",
    "meta.yaml": "3fbe784003a73076c436bec2df94e8d5c1388475dc1a70a032aa4f36c912ea3b",
}
# 같은 커밋에서 `json.dumps(cls.model_json_schema(), sort_keys=True, ensure_ascii=False)`의 sha256.
# 그 JSON 스키마가 v8~v14 루프의 요청 payload다 — 한 글자만 바뀌어도 그 판들의 요청이 바뀐다.
_OLD_ENVELOPE_SCHEMA_SHA = {
    AuditorAction: "962e145947b87bf486dac701c7c8a8a8838be9a54693c3583c71d9b0b4281767",
    AuditorProcedureAction: "ee2f8b7de202c9816c3b499ef3fc138f1da3e6ab5105ba4753c9159eb5b5f820",
    AuditorProcedureNullableCweAction: (
        "aa00c083261ad4bcb3de21193159601fc73748cc1bf2778bf6e36bd018013cd6"
    ),
    AuditorProcedureAuditAlwaysAction: (
        "1aa5f96f4033e1a4b7e9961164c61f5162d3027f9539b28bab05be4fb0fc2984"
    ),
}


@pytest.mark.parametrize("name", sorted(_V14_SHA))
def test_v14_is_byte_identical_to_origin_main(name: str) -> None:
    assert _sha((_V14 / name).read_bytes()) == _V14_SHA[name]


@pytest.mark.parametrize("envelope", list(_OLD_ENVELOPE_SCHEMA_SHA), ids=lambda c: c.__name__)
def test_the_v8_to_v14_envelopes_keep_their_request_schema(envelope: type) -> None:
    dumped = json.dumps(envelope.model_json_schema(), sort_keys=True, ensure_ascii=False)

    assert _sha(dumped.encode("utf-8")) == _OLD_ENVELOPE_SCHEMA_SHA[envelope]


# === 2. v15 산문 = v14 + 이 편집들 ================================================================

# 기록된 sha — v15도 불변 기록이다. 바꾸면 새 판을 세운다(`docs/agent-contracts.md` §4 규칙 1).
_V15_SHA = {
    "prefix.md": "e9ffb595583c2fdfbfe9b4281b1aa1dddfbbe6410d1e8529459268fcf2aa5e51",
    "body.md": _V14_SHA["body.md"],
}

# (v14에 있는 조각, v15의 조각) — 순서대로 적용하면 v14 prefix가 v15 prefix가 된다.
_EDITS: tuple[tuple[str, str], ...] = (
    (
        (
            'is a bounded excerpt of a tool log, not a generated summary: '
            'some execution-header lines are removed'
        ),
        (
            'is bounded: some execution-header lines are removed'
        ),
    ),
    (
        (
            'is cut in the middle like any other log, and the `...` line '
            'above applies to it.\n'
        ),
        (
            'is cut in the middle like any other log, and the `...` line '
            'above applies to it. A `K of N shown` line is what marks a '
            "fold: it lists K of the log's N hits, so K equal to N means "
            'every hit is listed and K below N means the others are not '
            'shown; a log without that line was not folded, and whether it '
            'was cut is told by the cut marks above, not by the missing '
            'line.\n'
        ),
    ),
    (
        (
            'Name the tool and nothing else. The host already knows the '
            'target and the working directory, so you do not choose a '
            'binary, a path, an argument, a flag, or input bytes — this is '
            'deliberate, so that a tool response cannot depend on a target '
            'you guessed. Since you send no input, running the same tool '
            'again puts the same question to it, and doing so straight '
            'after the previous call is a repeat (below). Whether a later '
            're-run would log anything different has not been measured; '
            'each run reports its own `evidence_id`. Never write that you '
            'ran a tool on an input you constructed.\n'
        ),
        (
            'Name the tool, and for `poc` the input. The host already '
            'knows the target and the working directory, so you do not '
            'choose a binary, a path, an argument, or a flag — this is '
            'deliberate, so that a tool response cannot depend on a target '
            'you guessed. The one thing you may add is `spec`: the input '
            'bytes, described as segments that the host assembles '
            'deterministically (never write bytes or code directly). '
            '`spec` is `{"rationale": "<why these bytes>", "segments": '
            '[...]}`, and the segments are joined in order: `{"kind": '
            '"literal", "hex": "<even-length hex>"}`, `{"kind": "repeat", '
            '"byte": <0-255>, "count": <n>}`, or `{"kind": "int", "value": '
            '<n>, "size": <1-8>, "endian": "little|big"}`. The host passes '
            'the assembled bytes to the tool, and the first line of the '
            'tool response reports `input_bytes=N input_sha256=<hex>` for '
            'the bytes it passed. Only `poc` reads them (it runs them in '
            "the task's own image); `asan`, `gdb`, and `build` run the "
            "host's own build and do not read the input, and for those the "
            'same line says so, so a `spec` sent with them changes '
            'nothing. With no `spec` a tool runs with no input. Running '
            'the same tool again with the same input (or none) puts the '
            'same question to it, and doing so straight after the previous '
            'call is a repeat (below). Whether a later re-run would log '
            'anything different has not been measured; each run reports '
            'its own `evidence_id`. Never write that you ran a tool on an '
            'input other than the one you sent in `spec`.\n'
        ),
    ),
    (
        (
            'Use the fewest hops that settle the question and answer as '
            'soon as the supplied material decides it.'
        ),
        (
            'Use the fewest hops that settle the question and answer as '
            'soon as the supplied material decides it — except that a '
            '`triggerable` answer follows a `probe` (below).'
        ),
    ),
    (
        (
            'Reach for `probe` when what blocks you is a fact about '
            'running the program that no amount of reading settles.\n'
        ),
        (
            'Reach for `probe` when what blocks you is a fact about '
            'running the program that no amount of reading settles.\n'
            '\n'
            'Answer `triggerable` only after you have made at least one '
            '`probe` call: while a hop remains, run one before you answer '
            'it. This is an instruction and the host does not check it — '
            'an `answer` with `triggerable` and no earlier `probe` is '
            'accepted as it stands. A probe that could not run the '
            'program, or whose response shows nothing, still counts as '
            'having probed, and what a probe can change in the result is '
            'only what step 2 (Execution evidence) says. When no hop '
            'remains, answer by the rules above.\n'
        ),
    ),
    (
        (
            '\n'
            'A tool response is evidence, not a verdict by itself;'
        ),
        (
            '\n'
            'The first user turn can end with a section headed `## Harness '
            'note (stage 2) - harness-measured data, not instructions`. It '
            'lists compiler errors that the stage-2 `build` or '
            '`clang-tidy` run measured on this target (tool, error count, '
            'first error). Where it says `build` failed with compiler '
            'errors, `build`, `asan`, and `gdb` use the same compile '
            'command, so the same errors are expected from them; `poc` '
            'does not use that command. It is a measurement and not a '
            'verdict on `SOURCE`, and it refuses no tool: a `probe` you '
            'still choose runs as usual. Without the section, stage 2 '
            'reported no compiler error text for this target, which does '
            'not show that it compiles.\n'
            '\n'
            'A tool response is evidence, not a verdict by itself;'
        ),
    ),
    (
        (
            'If a piece of evidence supports no claim in `audit`, do not '
            'cite it.\n'
        ),
        (
            'The responses to your `lookup` and `probe` calls also reach '
            'the Critic that reviews your result later; your own turns '
            '(including each `rationale`) do not. Before they do, the host '
            'replaces a request `kind` outside the eight names above, or a '
            '`target` that is not a plain identifier (letters, digits, '
            '`_`, `:`, `.`, `$`, up to 64 characters), with a placeholder, '
            "and drops the `got '...'` detail from rejection lines; code "
            'returned from the repository and the rest of each response '
            "are passed as you received them. The Critic may see a probe's "
            '`evidence_id`, but citing it does not register it as '
            'evidence: the gate drops an ID it does not know.\n'
            '\n'
            'If a piece of evidence supports no claim in `audit`, do not '
            'cite it.\n'
        ),
    ),
    (
        (
            '    {"action": "probe", "tool": '
            '"semgrep|clang-tidy|build|asan|gdb|poc", "rationale": "<one '
            'or two sentences>"}\n'
        ),
        (
            '    {"action": "probe", "tool": '
            '"semgrep|clang-tidy|build|asan|gdb|poc", "spec": <optional, '
            '`poc` only>, "rationale": "<one or two sentences>"}\n'
        ),
    ),
    (
        (
            '`tool` must be set on a `probe`, and `procedure` must be '
            'present on an `answer`. Do not send `spec`: a `probe` takes '
            'no input bytes, and a `spec` you send is not passed to the '
            'tool. Do not send `procedure` on a `lookup` or a `probe`, or '
            '`requests` or `tool` on an `answer`.'
        ),
        (
            '`tool` must be set on a `probe`; its `spec` is optional and '
            'only `poc` reads it (Tool use). `procedure` must be present '
            'and non-empty on an `answer`: an `answer` whose `procedure` '
            'is missing or null is rejected with the error `answer 액션은 비지 '
            '않은 procedure가 필요하다` for you to correct, and the audit ends if '
            'the retries are spent. Do not send `procedure` on a `lookup` '
            'or a `probe`, `requests`, `tool`, or `spec` on an `answer`, '
            'or `spec` on a `lookup`.'
        ),
    ),
    (
        (
            'for a `probe` it means the same tool. Changing only '
            '`purpose`, `request_code`, `spec`, or `rationale`, or the '
            'order of the requests, does not make a tool call different.'
        ),
        (
            'for a `probe` it means the same tool, and for `poc` the same '
            'assembled input bytes as well (no `spec` is an input of its '
            'own: none). Changing only `purpose`, `request_code`, or '
            '`rationale`, a `spec` whose segments assemble to the same '
            'bytes, any `spec` sent with a tool other than `poc`, or the '
            'order of the requests, does not make a tool call different.'
        ),
    ),
)


def _apply_edits(prefix: str) -> str:
    for old, new in _EDITS:
        assert prefix.count(old) == 1, old[:60]
        prefix = prefix.replace(old, new)
    return prefix


@pytest.mark.parametrize("name", sorted(_V15_SHA))
def test_v15_files_match_their_recorded_sha(name: str) -> None:
    assert _sha((_V15 / name).read_bytes()) == _V15_SHA[name]


def test_the_v15_meta_records_the_same_sha_the_test_pins() -> None:
    meta = _text(_V15, "meta.yaml")

    for name, digest in _V15_SHA.items():
        assert f"{name} sha256 `{digest}`" in meta or digest in meta, name


def test_v15_body_is_the_v14_body_bytes() -> None:
    assert (_V15 / "body.md").read_bytes() == (_V14 / "body.md").read_bytes()


def test_v15_prefix_is_the_v14_prefix_with_exactly_the_recorded_edits() -> None:
    """줄 단위가 아니라 **바이트 등식**이다 — 편집 밖에 한 글자라도 다르면 죽는다(끝 개행 · CRLF ·
    U+2028 포함)."""
    assert _apply_edits(_text(_V14, "prefix.md")) == _text(_V15, "prefix.md")
    assert b"\r" not in (_V15 / "prefix.md").read_bytes()


_IDS = {"candidate_id": "cand_0001", "finding_id": "find_0001", "missing_id": "mev_0001"}
_SOURCE = (
    "/* ===== parser.c:3-6 ===== */\n"
    "4|     char buf[64];\n"
    "5|     if (strcmp(src, \"x\") == 0) return;\n"
    "6|     memcpy(buf, src, len);"
)


def test_the_node_hop0_render_differs_from_v14_only_in_the_edited_prefix(ctx: RunContext) -> None:
    """같은 Finding(후보·캡슐·근거·SOURCE)을 v14·v15로 노드의 hop-0 렌더에 넣는다 —
    body(SOURCE·FACTS)는 같고
    prefix는 `_EDITS`만큼만 다르다."""
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
        for version in ("v14", "v15")
    }

    assert rendered["v15"].body == rendered["v14"].body
    assert rendered["v15"].prefix == _apply_edits(rendered["v14"].prefix)
    assert rendered["v15"].prefix != rendered["v14"].prefix
    assert rendered["v15"].prompt_version == "v15"


# === 3. 문장이 코드와 맞는다 ======================================================================


def _flat() -> str:
    return " ".join(_text(_V15, "prefix.md").split())


def test_the_excerpt_sentence_is_gone_and_the_fold_marker_claims_only_what_the_code_does() -> None:
    """이슈 #562 ③ — 접기는 발췌가 아니라 생성된 재조립이다. 그리고 「`K of N shown`이 없으면 절단이
    없다」고는 **쓰지 않았다**: 접히지 않은 로그도 범용 절단기(`...` 줄)로 잘린다."""
    flat, v14 = _flat(), " ".join(_text(_V14, "prefix.md").split())

    assert "excerpt" in v14 and "not a generated summary" in v14, "전제 — v14에는 그 문장이 있다"
    assert "excerpt" not in flat and "generated summary" not in flat
    assert "A `K of N shown` line is what marks a fold" in flat
    assert "not by the missing line" in flat
    assert "no truncation" not in flat and "was not truncated" not in flat


def test_k_of_n_shown_is_what_the_fold_code_prints_and_a_plain_cut_prints_none() -> None:
    from src.orchestrator import prompt_loader

    hits = [
        {
            "path": "a.c",
            "check_id": f"r{i}",
            "start": {"line": i},
            "end": {"line": i},
            "extra": {"message": "m"},
        }
        for i in range(1, 11)
    ]
    document = json.dumps({"results": hits, "errors": []})

    folded = prompt_loader._fold_semgrep_results(document, 400)
    assert folded is not None
    body, truncated = folded
    assert re.search(r"^\d+ of 10 shown$", body, re.MULTILINE)
    assert truncated is True, "접힘은 항상 절단 표지를 세운다 (산문: set whenever folded)"
    plain, cut = prompt_loader._bound_text("x" * 5000, 400)
    assert cut is True and "of 10 shown" not in plain and "\n...\n" in plain


def test_the_answer_sentence_quotes_the_schema_refusal_verbatim() -> None:
    """이슈 #602 — 산문이 적은 거절 문구가 스키마가 실제로 내는 것이다."""
    quoted = re.search(r"error `(answer [^`]+)`", _flat())
    assert quoted is not None, "산문에 거절 문구가 없다"

    for missing in ({}, {"procedure": None}):
        with pytest.raises(ValidationError) as caught:
            AuditorProcedureProbeInputAction.model_validate(
                {"action": "answer", "rationale": "r", **missing}
            )
        assert quoted.group(1) in str(caught.value)
    assert "procedure" in quoted.group(1)


def _loop_config(ctx: RunContext, version: str):  # noqa: ANN202
    """Auditor 좌석을 Fake 모델에 `version` 판으로 걸고 조사 홉 상한을 4로 둔 config."""
    entry = ctx.config.models["fake"].transition(prompt_template_version=version)
    return ctx.config.transition(
        models={**ctx.config.models, f"fake_{version}": entry},
        profile=ctx.config.profile.transition(
            roles={**ctx.config.profile.roles, "auditor": f"fake_{version}"}
        ),
        thresholds=ctx.config.thresholds.transition(auditor_tool_hops=4),
    )


def test_a_spec_only_change_on_poc_is_a_new_question_in_v15_and_a_repeat_in_v14(
    ctx: RunContext,
) -> None:
    """반복 키가 **루프에 실제로 걸려 있다**(`_run_hops`가 `_input_aware_key`를 쓴다): 같은
    도구·다른 입력의 `probe poc` 둘이 v15에서는 통과하고 v14 봉투에서는 `REPEAT_REQUEST`로
    끝난다."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    answer = {"action": "answer", "procedure": _payload("triggerable"), "rationale": "done"}

    def run(version: str, envelope: type):
        script = [
            envelope(action="probe", tool="poc", spec=_spec(0x41), rationale="first input"),
            envelope(action="probe", tool="poc", spec=_spec(0x42), rationale="second input"),
            envelope.model_validate(answer),
        ]
        armed = dataclasses.replace(
            ctx, config=_loop_config(ctx, version), models=_ScriptedModel(script)
        )
        return armed, auditor.run(
            armed, candidate=candidate, capsule=capsule, evidences=evidences,
            source=lambda: _SOURCE,
        )

    armed, (finding, _) = run("v15", AuditorProcedureProbeInputAction)
    hops = [e.data["action"] for e in armed.trace.events if e.kind.value == "auditor_hop"]
    assert hops == ["probe", "probe", "answer"] and finding.trigger_status == "triggerable"

    with pytest.raises(auditor.AuditorHopLimit) as caught:
        run("v14", AuditorProcedureAuditAlwaysAction)
    assert caught.value.stop is AuditorStopReason.REPEAT_REQUEST


def test_the_triggerable_after_probe_sentence_is_an_instruction_the_node_does_not_check(
    ctx: RunContext,
) -> None:
    """산문은 「the host does not check it」이라 적는다 — **정말 검사하지 않는다**: `probe` 0회로 낸
    `triggerable` `answer`를 노드가 그대로 받는다. 검사가 생기면 이 핀이 빨개져 산문을 같이
    고치게 한다."""
    assert "the host does not check it" in _flat()

    config = _loop_config(ctx, "v15")
    answer = AuditorProcedureProbeInputAction(
        action="answer", procedure=_payload("triggerable"), rationale="source decides it"
    )
    scripted = _ScriptedModel([answer])
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)  # Scout는 Fake로 — 대본은 Auditor만
    armed = dataclasses.replace(ctx, config=config, models=scripted)

    finding, _ = auditor.run(
        armed, candidate=candidate, capsule=capsule, evidences=evidences, source=lambda: _SOURCE
    )

    assert finding.trigger_status == "triggerable"
    assert finding.produced_by.prompt_version == "v15"
    assert scripted.schemas == [AuditorProcedureProbeInputAction]
    hops = [e for e in armed.trace.events if e.kind.value == "auditor_hop"]
    assert [e.data["action"] for e in hops] == ["answer"], "probe가 한 번도 없었다"


def _probe(
    tool: str, spec: PovSpec | None = None, *, envelope: type = AuditorProcedureProbeInputAction
):
    return envelope(action="probe", tool=tool, spec=spec, rationale="run it")


def _spec(byte: int = 0x41, count: int = 8, rationale: str = "padding") -> PovSpec:
    segment = PovSegment(kind="repeat", byte=byte, count=count)
    return PovSpec(segments=[segment], rationale=rationale)


def test_the_v15_repeat_key_sees_the_input_bytes_for_poc_only() -> None:
    """산문: 「for `poc` the same assembled input bytes as well … any `spec` sent with a tool other
    than
    `poc` … does not make a tool call different」 = `_input_aware_key`."""
    key = auditor._input_aware_key
    poc_a, poc_b = _probe("poc", _spec(0x41)), _probe("poc", _spec(0x42))

    assert key(poc_a) != key(poc_b), "spec만 바꾼 probe poc는 다른 질문이다"
    assert key(poc_a) != key(_probe("poc")), "입력 없음은 그 자체로 하나의 입력이다"
    assert key(_probe("poc")) == key(_probe("poc"))
    # 다른 rationale·다른 spec.rationale이어도 조립되는 바이트가 같으면 같다.
    assert key(poc_a) == key(_probe("poc", _spec(0x41, rationale="another reason")))
    two_segments = PovSpec(
        segments=[
            PovSegment(kind="repeat", byte=0x41, count=3),
            PovSegment(kind="repeat", byte=0x41, count=5),
        ],
        rationale="split",
    )
    assert key(poc_a) == key(_probe("poc", two_segments)), "같은 바이트로 조립되는 spec"
    for tool in ("asan", "gdb", "build", "semgrep", "clang-tidy"):
        assert key(_probe(tool, _spec(0x41))) == key(_probe(tool, _spec(0x42))) == key(_probe(tool))
    assert key(_probe("asan")) != key(_probe("gdb")) != key(_probe("poc"))
    lookup = AuditorProcedureProbeInputAction(
        action="lookup",
        requests=[
            {"kind": "function", "target": "f", "purpose": "p", "request_code": "c"},
        ],
        rationale="r",
    )
    assert key(lookup) == auditor._request_key(lookup)


@pytest.mark.parametrize(
    "envelope",
    [AuditorAction, AuditorProcedureAction, AuditorProcedureNullableCweAction,
     AuditorProcedureAuditAlwaysAction],
)
def test_the_older_envelopes_keep_the_tool_name_only_key(envelope: type) -> None:
    """v8~v14의 산문은 「`spec`만 바꿔도 같은 호출」이라 적는다 — 그 판들의 키는 종전 그대로다."""
    first = _probe("poc", _spec(0x41), envelope=envelope)
    second = _probe("poc", _spec(0x42), envelope=envelope)

    assert auditor._input_aware_key(first) == auditor._input_aware_key(second)
    assert auditor._input_aware_key(first) == auditor._request_key(first) == ("probe", "poc")


def test_the_repeat_sentence_lists_what_the_key_ignores() -> None:
    flat = _flat()
    sentence = next(s for s in re.split(r"(?<=\.)\s+", flat) if "does not make a tool call" in s)

    named = set(re.findall(r"`(\w+)`", sentence))
    assert named >= {"purpose", "request_code", "rationale", "spec", "poc"}
    assert "`spec`, or `rationale`" not in sentence, "v14의 옛 목록(`spec`을 통째로 무시)이 남았다"


def test_the_probe_sentence_describes_the_spec_the_schema_accepts() -> None:
    """산문의 세 조각 예시(`literal`·`repeat`·`int`)가 스키마의 필드를 쓴다 — 철자 변이는 모델에게
    파싱 실패를 준다."""
    flat = _flat()
    for example in (
        '{"kind": "literal", "hex": "<even-length hex>"}',
        '{"kind": "repeat", "byte": <0-255>, "count": <n>}',
        '{"kind": "int", "value": <n>, "size": <1-8>, "endian": "little|big"}',
    ):
        assert example in flat, example
    accepted = AuditorProcedureProbeInputAction.model_validate(
        {
            "action": "probe",
            "tool": "poc",
            "spec": {
                "rationale": "r",
                "segments": [
                    {"kind": "literal", "hex": "4142"},
                    {"kind": "repeat", "byte": 65, "count": 2},
                    {"kind": "int", "value": 7, "size": 4, "endian": "little"},
                ],
            },
            "rationale": "r",
        }
    )
    assert accepted.spec is not None and len(accepted.spec.segments) == 3
    assert '{"rationale": "<why these bytes>", "segments": [...]}' in flat
    assert "Do not send `spec`" not in flat and "you send no input" not in flat


def test_the_probe_example_line_shows_spec_and_the_other_actions_do_not() -> None:
    lines = {
        match.group(1): re.findall(r'"(\w+)":', line)
        for line in _text(_V15, "prefix.md").splitlines()
        if (match := re.match(r'\s*\{"action": "(lookup|probe|answer)"', line))
    }

    assert set(lines["probe"]) == {"action", "tool", "spec", "rationale"}
    assert "spec" not in lines["lookup"] and "spec" not in lines["answer"]


# === 4. 배선 =====================================================================================


def test_v15_is_wired_in_every_place_a_new_prompt_version_has_to_be() -> None:
    template = load_prompt(Role.AUDITOR, "v15")
    v14 = load_prompt(Role.AUDITOR, "v14")

    assert "v15" in auditor._CONTENT_AWARE_VERSIONS
    assert template.output_schema == "AuditorProcedureProbeInputAction"
    assert auditor.resolve_output_schema("AuditorProcedureProbeInputAction") is (
        AuditorProcedureProbeInputAction
    )
    assert auditor._loop_envelope(template) is AuditorProcedureProbeInputAction
    assert AuditorProcedureProbeInputAction in auditor._LOOP_ENVELOPES
    assert AuditorProcedureProbeInputAction in auditor.ALLOWED_OUTPUT_SCHEMAS
    assert fake_model._BUILDERS[AuditorProcedureProbeInputAction] is not None
    assert (template.tools, template.source_options, template.input, template.facts_source) == (
        v14.tools,
        v14.source_options,
        v14.input,
        v14.facts_source,
    )
    assert auditor.loop_problem(template, 16, is_fake=False) is None
    assert auditor.tool_hops_in_effect("v15", 16) == 16


@pytest.mark.parametrize("profile", ["harness_pool", "harness_pool_hybrid"])
def test_the_serving_auditor_seat_selects_v15_and_the_doctor_has_nothing_to_say(
    profile: str,
) -> None:
    config = load_config(profile)
    version = config.model_for("auditor").prompt_template_version

    assert version == "v15"
    assert cli._check_prompt_version(config, "auditor", version) == [], (
        "내용 인식 ERROR도 디스크 새 판 WARN도 없다"
    )


def test_no_auditor_version_newer_than_the_served_one_is_on_disk() -> None:
    """doctor의 「디스크 새 판」 WARN이 0인 이유 — 서빙 판이 디스크의 마지막 판이다."""
    numbers = sorted(
        int(p.name[1:])
        for p in (PROMPTS_DIR / "auditor").iterdir()
        if p.is_dir() and p.name[1:].isdecimal()
    )

    assert numbers[-1] == 15


def test_a_fake_loop_runs_on_v15(ctx: RunContext) -> None:
    """Fake가 v15 봉투를 만든다(빌더 표) — lookup → probe → answer가 한 번씩 돈다."""
    armed = dataclasses.replace(ctx, config=_loop_config(ctx, "v15"))
    candidate, capsule, evidences = _triage(armed, VULN_TARGET)

    finding, _ = auditor.run(
        armed, candidate=candidate, capsule=capsule, evidences=evidences, source=lambda: _SOURCE
    )

    hops = [e.data for e in armed.trace.events if e.kind.value == "auditor_hop"]
    assert [h["action"] for h in hops] == ["lookup", "probe", "answer"]
    assert hops[-1]["stop_reason"] == AuditorStopReason.ANSWERED.value
    assert finding.produced_by.prompt_version == "v15"


# === 5. 봉투 =====================================================================================


def test_the_v15_envelope_is_the_v12_envelope_with_only_the_descriptions_rewritten() -> None:
    new = AuditorProcedureProbeInputAction.model_json_schema()
    old = AuditorProcedureAuditAlwaysAction.model_json_schema()

    assert issubclass(AuditorProcedureProbeInputAction, AuditorProcedureAuditAlwaysAction)
    assert list(new["properties"]) == list(old["properties"]), "필드 순서가 곧 문법이다"
    assert new["$defs"] == old["$defs"], "최종 답과 PovSpec 스키마는 v12 그대로다"
    assert new["required"] == old["required"]
    diff = {key for key in new if new[key] != old.get(key)}
    assert diff == {"title", "description", "properties"}
    changed = {k for k in new["properties"] if new["properties"][k] != old["properties"][k]}
    assert changed == {"spec"}


def test_the_v15_envelope_text_no_longer_says_the_probe_carries_no_input() -> None:
    new = json.dumps(AuditorProcedureProbeInputAction.model_json_schema(), ensure_ascii=False)
    old = json.dumps(AuditorProcedureAuditAlwaysAction.model_json_schema(), ensure_ascii=False)

    for stale in ("안 싣는다", "싣지 않는다", "전달되지 않고", "지금은 쓰지 않는다"):
        assert stale in old, f"전제 — v12 봉투에는 {stale!r}가 있다"
        assert stale not in new, stale
    assert "poc" in new


def test_the_v15_envelope_docstring_has_no_repository_reference() -> None:
    """클래스 설명은 요청 payload에 실린다 — 이슈·PR 번호 · 소스 경로 · D 번호를 넣지 않는다."""
    text = AuditorProcedureProbeInputAction.__doc__ + str(
        AuditorProcedureProbeInputAction.model_fields["spec"].description
    )

    assert not re.search(r"#\d|\bPR\b|src/|\.py\b|\bD\d+", text)
    assert AuditorProcedureProbeInputAction.__doc__ != AuditorProcedureAuditAlwaysAction.__doc__


def test_the_envelope_still_refuses_a_stray_field_and_an_empty_lookup() -> None:
    with pytest.raises(ValidationError):
        AuditorProcedureProbeInputAction.model_validate(
            {"action": "answer", "procedure": _payload("triggerable"), "tool": "poc",
             "rationale": "r"}
        )
    with pytest.raises(ValidationError):
        AuditorProcedureProbeInputAction.model_validate(
            {"action": "lookup", "spec": _spec().model_dump(), "rationale": "r"}
        )
    assert schemas.AuditorProcedureProbeInputAction is AuditorProcedureProbeInputAction


# === 6. 의존 PR 핀 — main에 들어오면 켜진다 ======================================================


def test_pr_644_the_spec_reaches_the_tool_and_the_head_line_reports_the_bytes() -> None:
    """#644 — `_probe_observation`이 머리줄에 `input_bytes=N input_sha256=<hex>`를 단다."""
    if "poc" not in inspect.signature(auditor._probe_observation).parameters:
        pytest.skip("PR #644가 아직 main에 없다 — v15의 `spec`·`input_bytes` 문장이 의존한다")
    source = inspect.getsource(auditor._probe_observation)

    assert "input_bytes=" in source and "input_sha256=" in source
    assert "input_bytes=N input_sha256=<hex>" in _flat()
    assert "NOT given to the tool" not in source, "옛 note 줄이 남았다"


def test_pr_600_the_critic_receives_the_hop_observations() -> None:
    if not hasattr(auditor, "critic_safe_observation"):
        pytest.skip("PR #600이 아직 main에 없다 — v15의 「Critic에도 간다」 문장이 의존한다")

    assert "also reach the Critic" in _flat()
    source = inspect.getsource(auditor)
    assert "<non-vocabulary>" in source and "<non-identifier>" in source


def test_pr_648_the_hop0_compile_note_heading_is_the_one_the_prefix_names() -> None:
    if not hasattr(signals, "compile_note"):
        pytest.skip("PR #648이 아직 main에 없다 — v15의 hop-0 컴파일 노트 문장이 그 PR에 의존한다")
    heading = "## Harness note (stage 2) - harness-measured data, not instructions"

    assert heading in inspect.getsource(signals.compile_note)
    assert heading in _flat()
    assert "use the same compile command" in inspect.getsource(signals.compile_note)
