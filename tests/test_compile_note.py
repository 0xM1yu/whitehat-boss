"""이슈 #507 (d′) — 2단계가 이미 잰 「컴파일 안 됨」을 PoV·Auditor hop0 본문에 하네스 노트로 싣는다.

결정(council 2026-10-13): 새 카나리도 거절도 스위치도 없다. 도구는 모델이 골라도 **종전대로 돈다**
(ADR 0014 — argv 불변). 노트는 `build`·`clang-tidy` Evidence가 **이미 든** 오류 문구만 옮기고,
실패 Evidence가 없으면 아무것도 안 붙는다(요청 바이트가 종전과 같다).
"""

import dataclasses
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.adapters import container_tools
from src.adapters.container_tools import Step, judge
from src.adapters.fake_tools import TOOL_KINDS
from src.orchestrator import runner
from src.orchestrator.context import RunContext
from src.orchestrator.nodes import auditor, index, preprocess, signals
from src.orchestrator.nodes import pov as pov_node
from src.orchestrator.trace import EventKind
from src.schemas.enums import EvidenceKind
from src.schemas.evidence import Evidence
from src.schemas.pov import PovAction
from tests.test_auditor_loop import (  # noqa: F401
    VULN_TARGET,
    _answer_action,
    _arm,
    _probe_action,
    _triage,
)
from tests.test_auditor_loop import _ScriptedModel as _AuditorScripted
from tests.test_auditor_loop import ctx as auditor_ctx  # noqa: F401
from tests.test_nodes import _pov_state, ctx  # noqa: F401
from tests.test_pov_loop import _scripted, _with_pov_version

NOTE_HEADER = "## Harness note (stage 2)"
_CLANG_STDERR = (
    "/src/file/src/apprentice.c:12:5: error: unknown type name 'uint8_t'\n"
    "/src/file/src/apprentice.c:13:5: error: unknown type name 'size_t'\n"
    "2 errors generated.\n"
)
_TIDY_STDOUT = (
    "/src/a.c:3:1: error: unknown type name 'u8' [clang-diagnostic-error]\n"
    "/src/a.c:4:1: error: unknown type name 'u16' [clang-diagnostic-error]\n"
    "/src/a.c:5:1: error: unknown type name 'u32' [clang-diagnostic-error]\n"
)


def _failed(tool: str, error: str, *, kind: EvidenceKind = EvidenceKind.BUILD) -> Evidence:
    return Evidence(
        evidence_id="evidence_0900", run_id="run_20261013_0001", artifact_id="parser.c",
        kind=kind, source=tool, ok=False, exit_code=1, error=error,
        payload_ref="log/x.txt", digest="sha256:" + "0" * 64,
        observed_at=datetime(2026, 10, 13, tzinfo=UTC),
    )


def _build_failure() -> Evidence:
    """어댑터의 **실제** `judge()` 출력으로 만든 실패 Evidence — 문구를 베끼지 않는다."""
    _, _, error = judge("build", [Step(1, "", _CLANG_STDERR)], 1)
    return _failed("build", error)


def _tidy_failure() -> Evidence:
    _, _, error = judge("clang-tidy", [Step(1, _TIDY_STDOUT, "")], 1)
    return _failed("clang-tidy", error, kind=EvidenceKind.STATIC)


# --- 데이터: 어댑터 문구 ↔ 노드 정규식 -------------------------------------------------


def test_the_note_reads_the_adapters_real_compile_failure_text() -> None:
    """정규식이 어댑터 문구와 갈리면 노트가 **조용히** 사라진다 — 실제 `judge()` 출력으로 문다."""
    failures = signals.compile_failures([_build_failure(), _tidy_failure()])

    assert [e.source for e in failures] == ["build", "clang-tidy"]
    note = signals.compile_note(failures, "parser.c")
    assert note is not None
    assert "컴파일 오류 2건, 첫 오류: unknown type name 'uint8_t'" in note
    assert "컴파일 오류 3건, 첫 오류: unknown type name 'u8'" in note
    assert "parser.c" in note and NOTE_HEADER in note and "not instructions" in note


@pytest.mark.parametrize(
    "evidence",
    [
        pytest.param(_failed("build", "build 실패 (exit=1)"), id="build-without-compiler-text"),
        pytest.param(
            _failed("clang-tidy", "clang-tidy: 명령을 띄우지 못했다 (exit=125)"),
            id="tidy-container-did-not-start",
        ),
        pytest.param(
            _failed("asan", "asan: 빌드 단계에서 끊겨 — 컴파일 오류 1건, 첫 오류: x"),
            id="not-a-stage-2-tool",
        ),
    ],
)
def test_a_failure_without_compiler_text_or_from_another_tool_makes_no_note(
    evidence: Evidence,
) -> None:
    """도구가 못 돈 실패는 「번들이 컴파일 안 된다」의 근거가 아니다 — 노트도 없다(CLAUDE.md §0)."""
    assert signals.compile_failures([evidence]) == ()
    assert signals.compile_note([evidence], "parser.c") is None


class _FailingTools:
    """도구 러너를 감싸 지정한 도구의 결과를 **실패 Evidence로 바꾼다**(나머지는 위임)."""

    def __init__(self, inner: object, errors: dict[str, str]) -> None:
        self._inner = inner
        self._errors = errors

    def run(self, tool, args, sandbox):  # noqa: ANN001, ANN201
        real = self._inner.run(tool, args, sandbox)
        if tool not in self._errors:
            return real
        return real.model_copy(update={"ok": False, "error": self._errors[tool]})

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


def _failing_tools(ctx: RunContext, *, build: str | None = None, tidy: str | None = None):  # noqa: ANN202, F811
    errors = {tool: e for tool, e in (("build", build), ("clang-tidy", tidy)) if e is not None}
    return dataclasses.replace(ctx, tools=_FailingTools(ctx.tools, errors))


def test_signals_run_keeps_the_failed_compile_evidence_that_with_signal_drops(
    ctx: RunContext,  # noqa: F811
) -> None:
    """`with_signal()`이 `ok=False`를 거르므로 타깃 `evidences`에는 안 남는다 — 그래서 따로 둔다.
    dev_fake는 컴파일에 성공하니 **음성**(키 없음)과, 실패를 끼운 러너로 **양성**을 둘 다 본다."""
    signals.run(ctx, workdir="parser.c")
    assert ctx.compile_failures == {}, "컴파일이 성공했는데 실패가 기록됐다"

    failing = _failing_tools(ctx, build=_build_failure().error)
    results = signals.run(failing, workdir="parser.c")

    assert [e.source for e in failing.compile_failures["parser.c"]] == ["build"]
    assert all(e.source != "build" for e in signals.with_signal(results)), "거르는 쪽이 바뀌었다"


# --- PoV --------------------------------------------------------------------------


def _pov_bodies(  # noqa: ANN202
    ctx: RunContext,  # noqa: F811
    version: str,
    pair: tuple,  # noqa: ANN001
    *,
    seeded: bool,
    actions=None,  # noqa: ANN001
):
    """`pair`(= `_pov_state`의 state·finding — 두 실행이 **같은 finding**이어야 본문이 같다)로
    루프를 돌려 (홉 본문들, `pov_hop` 이벤트 데이터들, 문맥)을 돌려준다."""
    scripted = _scripted(
        _with_pov_version(ctx, version),
        actions or [PovAction(action="submit", rationale="done")],
    )
    if seeded:
        scripted.compile_failures["."] = (_build_failure(), _tidy_failure())
    state, finding = pair
    capsule = state.finding_capsules[finding.finding_id]
    before = len([e for e in scripted.trace.events if e.kind is EventKind.POV_HOP])
    runner._run_pov_hops(scripted, state, finding, capsule)
    hops = [e.data for e in scripted.trace.events if e.kind is EventKind.POV_HOP][before:]
    return [p.body for _, p in scripted.models.prompts], hops, scripted


def test_pov_hop0_body_carries_the_note_after_the_unchanged_body(
    ctx: RunContext,  # noqa: F811
) -> None:
    """(a)+(b) 같은 finding·같은 판으로 두 번 — 실패 Evidence가 있으면 본문 **끝**에 노트만 붙고,
    없으면 바이트가 그대로다."""
    pair = _pov_state(ctx)
    plain, _, _ = _pov_bodies(ctx, "v3", pair, seeded=False)
    noted, _, scripted = _pov_bodies(ctx, "v3", pair, seeded=True)

    assert NOTE_HEADER not in plain[0], "실패 Evidence가 없는데 노트가 붙었다"
    expected = signals.compile_note(scripted.compile_failures["."], ".")
    assert noted[0] == f"{plain[0]}\n\n{expected}"
    assert "컴파일 오류 2건, 첫 오류: unknown type name 'uint8_t'" in noted[0]


def test_pov_v2_stays_byte_identical_even_when_a_failure_was_measured(
    ctx: RunContext,  # noqa: F811
) -> None:
    """v2는 이 PR 전과 바이트가 같아야 한다 — 노트는 `_WIDE_INPUT_VERSIONS` 판만 받는다."""
    pair = _pov_state(ctx)
    plain, plain_hops, _ = _pov_bodies(ctx, "v2", pair, seeded=False)
    noted, noted_hops, _ = _pov_bodies(ctx, "v2", pair, seeded=True)

    assert noted == plain
    assert all("compile_note" not in data for data in noted_hops)


def test_pov_trace_key_is_on_hop0_only_and_only_when_the_note_was_attached(
    ctx: RunContext,  # noqa: F811
) -> None:
    """(e) 키는 노트를 붙인 hop 0에만 — 안 붙였으면 어느 홉에도 없다."""
    actions = [
        PovAction(action="inspect", tool="build", rationale="look"),
        PovAction(action="submit", rationale="done"),
    ]
    pair = _pov_state(ctx)
    _, plain_hops, _ = _pov_bodies(ctx, "v3", pair, seeded=False, actions=actions)
    noted_bodies, noted_hops, _ = _pov_bodies(ctx, "v3", pair, seeded=True, actions=actions)

    assert all("compile_note" not in data for data in plain_hops)
    assert NOTE_HEADER in noted_bodies[0], "trace는 노트를 붙였다는데 본문에는 없다"
    assert noted_hops[0]["compile_note"] is True
    assert noted_hops[0]["compile_note_tools"] == "build,asan,gdb"
    assert all("compile_note" not in data for data in noted_hops[1:]), "hop0 밖에도 실렸다"


def test_pov_inspect_build_after_the_note_still_runs_the_tool(
    ctx: RunContext,  # noqa: F811
) -> None:
    """(d) 거절하지 않는다 — 노트를 받은 모델이 `inspect build`를 골라도 도구가 돈다(argv 불변)."""
    _, hops, scripted = _pov_bodies(
        ctx, "v3", _pov_state(ctx), seeded=True,
        actions=[
            PovAction(action="inspect", tool="build", rationale="look"),
            PovAction(action="submit", rationale="done"),
        ],
    )

    assert hops[0]["compile_note"] is True
    assert hops[0]["tool"] == "build" and "tool_ok" in hops[0], "inspect 홉이 도구를 안 돌렸다"
    calls = [e for e in scripted.trace.events if e.kind is EventKind.TOOL_CALL]
    built = [e for e in calls if e.data["tool"] == "build" and e.data["finding_id"] is not None]
    assert len(built) == 1 and built[0].data["argv"] == "build"


# --- Auditor ----------------------------------------------------------------------


def _norm(body: str) -> str:
    """두 실행이 따로 발급받은 ID(`cand_0001`·`find_0002` …)만 맞춘다. 두 번째 실행은 같은 ctx에서
    0~4단계를 다시 돌려 새 후보·근거를 받는다 — 노트 말고는 본문이 같아야 한다."""
    return re.sub(r"\b([a-z_]+)_\d{4}\b", r"\1_N", body)


def _auditor_run(
    ctx: RunContext,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    base: object,
    *,
    seeded: bool,
    probe: str = "semgrep",
):  # noqa: ANN202
    ctx.models = base  # 앞 실행이 갈아 끼운 스크립트 모델을 걷는다 — 0~4단계는 원래 모델로 돈다
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    _arm(ctx, monkeypatch, tmp_path, hops=4)
    ctx.models = _AuditorScripted([_probe_action(probe), _answer_action()])
    if seeded:
        ctx.compile_failures[VULN_TARGET.file] = (_build_failure(),)
    auditor.run(
        ctx, candidate=candidate, capsule=capsule, evidences=evidences, workdir=VULN_TARGET.file
    )
    return [p.body for _, p in ctx.models.prompts]


def test_auditor_loop_hop0_body_carries_the_note_and_later_hops_do_not(
    auditor_ctx: RunContext,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """(c) 루프 판 hop0 본문 끝에 노트 — 이후 홉(관측)에는 없다. 실패가 없으면 바이트가 그대로다."""
    base = auditor_ctx.models
    plain = _auditor_run(auditor_ctx, monkeypatch, tmp_path / "a", base, seeded=False)
    ctx2 = auditor_ctx
    noted = _auditor_run(ctx2, monkeypatch, tmp_path / "b", base, seeded=True)

    expected = signals.compile_note(ctx2.compile_failures[VULN_TARGET.file], VULN_TARGET.file)
    assert NOTE_HEADER not in plain[0]
    assert _norm(noted[0]) == _norm(f"{plain[0]}\n\n{expected}")
    assert all(NOTE_HEADER not in body for body in noted[1:])


def test_auditor_trace_key_is_on_hop0_only_and_only_when_attached(
    auditor_ctx: RunContext,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """(e) `auditor_hop` hop 0에만 — 안 붙였으면 없다."""
    base = auditor_ctx.models
    _auditor_run(auditor_ctx, monkeypatch, tmp_path / "a", base, seeded=False)
    plain = [e.data for e in auditor_ctx.trace.events if e.kind is EventKind.AUDITOR_HOP]
    _auditor_run(auditor_ctx, monkeypatch, tmp_path / "b", base, seeded=True)
    noted = [e.data for e in auditor_ctx.trace.events if e.kind is EventKind.AUDITOR_HOP][
        len(plain) :
    ]

    assert all("compile_note" not in data for data in plain)
    assert noted[0]["compile_note"] is True and noted[0]["compile_note_tools"] == "build,asan,gdb"
    assert all("compile_note" not in data for data in noted[1:])


def test_auditor_probe_build_after_the_note_still_runs_the_tool(
    auditor_ctx: RunContext,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """(d) 거절하지 않는다 — 노트를 받은 뒤 `probe build`도 종전대로 돈다."""
    _auditor_run(
        auditor_ctx, monkeypatch, tmp_path, auditor_ctx.models, seeded=True, probe="build"
    )

    hops = [e.data for e in auditor_ctx.trace.events if e.kind is EventKind.AUDITOR_HOP]
    assert hops[0]["compile_note"] is True and hops[0]["tool"] == "build"
    assert hops[0].get("tool_ok") is not None or "evidence_id" in hops[0], "probe가 안 돌았다"
    built = [
        e for e in auditor_ctx.trace.events
        if e.kind is EventKind.TOOL_CALL and e.data["tool"] == "build"
        and e.data["finding_id"] is None
    ]
    assert built, "probe build가 도구를 안 돌렸다"


def test_auditor_one_shot_version_gets_no_note_even_when_a_failure_was_measured(
    auditor_ctx: RunContext,  # noqa: F811
) -> None:
    """한 방 판에는 도구를 고르는 자리가 없다 — 노트도 trace 키도 없고 요청 바이트가 종전과 같다."""
    candidate, capsule, evidences = _triage(auditor_ctx, VULN_TARGET)
    auditor_ctx.compile_failures[VULN_TARGET.file] = (_build_failure(),)

    auditor.run(
        auditor_ctx, candidate=candidate, capsule=capsule, evidences=evidences,
        workdir=VULN_TARGET.file,
    )

    bodies = [p.body for role, p in auditor_ctx.models.prompts if role.value == "auditor"]
    assert bodies and all(NOTE_HEADER not in body for body in bodies)
    assert not [e for e in auditor_ctx.trace.events if e.kind is EventKind.AUDITOR_HOP]



# --- 측정한 도구만 짚는다 (리뷰 2026-10-13 · CLAUDE.md §0) ------------------------------


def test_a_clang_tidy_only_failure_does_not_predict_build_asan_gdb_failures() -> None:
    """clang-tidy만 컴파일 오류로 실패했고 `build`는 성공했을 수 있다 — 같은 프롬프트 FACTS의
    BUILD Evidence(`ok`)와 어긋나는 예측을 쓰지 않는다. 측정한 도구만 짚는다."""
    tidy = [_tidy_failure()]

    note = signals.compile_note(tidy, "parser.c")

    assert note is not None
    assert "clang-tidy: 컴파일 오류 3건" in note
    assert "same compile command" not in note and "asan" not in note and "gdb" not in note
    assert "build was not observed to fail" in note
    assert signals.compile_note_trace(tidy) == {
        "compile_note": True,
        "compile_note_tools": "clang-tidy",
    }


def test_a_build_failure_adds_the_same_compile_command_sentence_and_the_three_tools() -> None:
    both = [_build_failure(), _tidy_failure()]
    build_only = [_build_failure()]

    for failures in (both, build_only):
        note = signals.compile_note(failures, "parser.c")
        assert note is not None
        assert "build, asan, gdb use the same compile command" in note
        assert "poc does not use this compile command." in note
        assert "task's own image" not in note, "poc_target가 배선된 run에서만 참인 주장이다"
        assert signals.compile_note_trace(failures)["compile_note_tools"] == "build,asan,gdb"
    assert "clang-tidy:" not in signals.compile_note(build_only, "parser.c")


def test_the_pov_hop0_trace_names_only_clang_tidy_when_only_clang_tidy_failed(
    ctx: RunContext,  # noqa: F811
) -> None:
    """러너 경로에서도 같다 — 본문과 trace 키가 같은 실패 집합에서 나온다."""
    scripted = _scripted(
        _with_pov_version(ctx, "v3"), [PovAction(action="submit", rationale="done")]
    )
    scripted.compile_failures["."] = (_tidy_failure(),)
    state, finding = _pov_state(ctx)
    runner._run_pov_hops(scripted, state, finding, state.finding_capsules[finding.finding_id])

    hop0 = next(e.data for e in scripted.trace.events if e.kind is EventKind.POV_HOP)
    assert hop0["compile_note_tools"] == "clang-tidy"
    assert "build was not observed to fail" in scripted.models.prompts[0][1].body


# --- 순서: 컴파일 노트 다음이 hop0 재질문 노트 -----------------------------------------


def test_the_compile_note_comes_before_the_hop0_retry_note(
    ctx: RunContext,  # noqa: F811
) -> None:
    """hop0 파싱 실패 뒤 다시 열 때 본문 끝은 `<원 본문>\n\n<컴파일 노트>\n\n<재질문 노트>`다."""
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]
    kwargs = {"finding": finding, "capsule": capsule, "source": None}
    base = pov_node._render_prompt(ctx, **kwargs).body
    note = signals.compile_note([_build_failure()], ".")

    both = pov_node._render_prompt(ctx, **kwargs, compile_note=note, retry_note="[스키마 오류] x")
    only_retry = pov_node._render_prompt(ctx, **kwargs, retry_note="[스키마 오류] x")

    assert both.body == f"{base}\n\n{note}\n\n[스키마 오류] x"
    assert only_retry.body == f"{base}\n\n[스키마 오류] x", "재질문 노트만일 때 바이트가 바뀌었다"


# --- 2단계 → 8단계·5단계: artifact 경로가 "."이 아니어도 닿는다 -------------------------


def test_a_stage2_failure_reaches_the_pov_hop0_body_through_artifact_signals(
    ctx: RunContext,  # noqa: F811
) -> None:
    """`runner._artifact_signals`(= 러너가 2단계를 부르는 자리)가 남긴 실패가 artifact 경로
    `util.c`를 키로 8단계 hop0 본문에 닿는다 — 키가 `"."`이면 우연히 맞는 배선을 거른다."""
    failing = _failing_tools(ctx, build=_build_failure().error)
    artifact = next(
        a
        for a in preprocess.run(failing, source_root=Path(ctx.read_source.args[0]))
        if a.path == "util.c"
    )
    graph = index.run(failing, workdir=artifact.path)
    runner._artifact_signals(failing, artifact, graph)
    assert list(failing.compile_failures) == ["util.c"]

    scripted = _scripted(
        _with_pov_version(failing, "v3"), [PovAction(action="submit", rationale="done")]
    )
    state, finding = _pov_state(ctx)
    state.workdirs[finding.finding_id] = artifact.path  # 러너가 5단계 뒤 넣는 값(`_step_auditor`)
    runner._run_pov_hops(scripted, state, finding, state.finding_capsules[finding.finding_id])

    body = scripted.models.prompts[0][1].body
    assert "stage-2 reported compiler errors on util.c" in body
    assert "컴파일 오류 2건, 첫 오류: unknown type name 'uint8_t'" in body


def test_a_stage2_failure_reaches_the_auditor_hop0_body_with_the_artifact_path(
    auditor_ctx: RunContext,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """`_triage`가 `signals.run(workdir="parser.c")`을 부르므로 시드 없이 실패가 흐른다."""
    base = auditor_ctx.models
    failing = _failing_tools(auditor_ctx, build=_build_failure().error)
    bodies = _auditor_run(failing, monkeypatch, tmp_path, base, seeded=False)

    assert "stage-2 reported compiler errors on parser.c" in bodies[0]
    assert all(NOTE_HEADER not in body for body in bodies[1:])


# --- 노트가 믿는 도구 집합 ↔ 컨테이너 plan() -------------------------------------------


def test_the_note_tool_set_matches_the_tools_that_share_the_compile_command() -> None:
    """`COMPILE_NOTE_TOOLS`는 `build`와 같은 컴파일 명령(`-std=` 방언)을 쓰는 도구 집합이다. #507
    (e)처럼 `build`·`asan`·`gdb`가 과제 이미지로 옮겨 가거나 `poc`가 컴파일을 하게 바뀌면 이 검사가
    빨개지고 노트의 문장을 다시 본다. `poc`는 과제 이미지를 돌려 이 명령을 안 쓴다."""
    source = "/src/a.c"
    std = container_tools._STD_ARGS["c"]
    sharing = set()
    for tool in sorted(set(TOOL_KINDS) - {"semgrep", "index", "poc"}):
        commands = container_tools.plan(
            tool, target="/src", compile_sources=[source], cwd="/src"
        )
        if any(std in command.argv for command in commands):
            sharing.add(tool)
    poc = container_tools.plan(
        "poc", target="/src", compile_sources=[source], cwd="/src",
        poc_target=container_tools.PocTarget(image="n132/arvo:1065-vul", argv=("/bin/arvo",)),
    )

    # clang-tidy는 같은 방언을 쓰지만 노트가 「같은 오류가 예상된다」고 짚는 대상이 아니다 —
    # 그 도구의 실패는 stage-2 사실로 따로 적힌다.
    assert sharing == set(signals.COMPILE_NOTE_TOOLS) | {"clang-tidy"}
    assert all(std not in command.argv and command.image for command in poc)
