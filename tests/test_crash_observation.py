"""크래시 관측을 **기록만** 한다 — `Evidence.crashed`·`banner` (이슈 #232, 2026-10-13 결정).

이 파일이 무는 명제는 둘이다.

1. **러너는 대상 로그에서 sanitizer 배너를 찾아 싣는다** — 잰 것은 `True`/`False`, 안 잰 것은 `None`
   (CLAUDE.md §0). 배너 철자는 sanitizer 문서에서 왔고 이 저장소에는 **실제 크래시 녹음이 0건**이다.
   그래서 아래 양성 핀의 줄들은 문서에 실린 모양을 따라 지은 것이지 이 하네스의 녹음이 아니다.
2. **그 값을 아무도 읽지 않는다** — 게이트 `p4`는 여전히 `exit_code`만 본다. `crashed`를 `p4`가 읽게
   하는 전환(D80의 `p4` 스위치)은 실제 `poc_run` 크래시 녹음이 쌓인 뒤로 미뤘다. 이 파일의 게이트
   검사들은 **지금의 동작을 고정한다** — 그 스위치를 다시 여는 PR은 이 검사들을 의도적으로 바꾼다.
"""

import ast
import dataclasses
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.adapters import container_tools, sqlite_store
from src.adapters.container_tools import (
    DEFAULT_IMAGE,
    ContainerToolRunner,
    PocTarget,
    Step,
    judge,
    observe_crash,
)
from src.adapters.fake_tools import FakeToolRunner
from src.ids import IdFactory
from src.interfaces.tool_runner import INPUT_PLACEHOLDER, SandboxSpec, ToolArgs
from src.orchestrator import gate
from src.orchestrator.context import RunContext
from src.orchestrator.nodes.sandbox import DEFAULT_SANDBOX
from src.orchestrator.stages import Stage
from src.orchestrator.trace import EventKind
from src.schemas.enums import EvidenceGrade, EvidenceKind
from src.schemas.evidence import Evidence
from tests.test_gate import RUN_ID as GATE_RUN_ID
from tests.test_gate import _evidence, _finding
from tests.test_nodes import ctx  # noqa: F401

RUN_ID = "run_20260901_0001"
OBSERVED_AT = datetime(2026, 9, 1, tzinfo=UTC)
TARGET = Path(__file__).resolve().parent / "fixtures" / "target"
ARVO = PocTarget(image="n132/arvo:1065-vul", argv=("/bin/arvo",))

SANDBOX = SandboxSpec(
    image=DEFAULT_IMAGE, network=False, read_only=True, cpu_limit=1.0, memory_mb=2048, timeout_s=300
)

# 아래 줄들은 sanitizer 문서·널리 퍼진 보고서의 모양을 따라 지은 것이다 — 이 저장소의 녹음이 아니다.
ASAN = "==12345==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x602000000014 at pc 0x0"
MSAN = "==1==WARNING: MemorySanitizer: use-of-uninitialized-value"
UBSAN = "parser.c:12:5: runtime error: signed integer overflow: 2147483647 + 1 cannot be repr"
LIBFUZZER = "==7==ERROR: libFuzzer: deadly signal"
ASAN_SEGV = "==3==ERROR: AddressSanitizer: SEGV on unknown address 0x000000000000 (pc 0x0)"

BANNERS = [ASAN, MSAN, UBSAN, LIBFUZZER, ASAN_SEGV]
BANNER_IDS = ["asan", "msan", "ubsan", "libfuzzer", "asan-segv"]


class _Seq:
    """`subprocess.run` 대역 — 부른 순서대로 결과를 내고, 모자라면 마지막을 되풀이한다."""

    def __init__(self, *results: tuple[int, str, str]) -> None:
        self.results = list(results)
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):  # noqa: ANN001, ANN204
        self.calls.append(list(argv))
        code, out, err = self.results[min(len(self.calls), len(self.results)) - 1]
        return subprocess.CompletedProcess(argv, code, out, err)


@pytest.fixture
def run_with(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):  # noqa: ANN201
    """`run_with(*결과)` → 그 결과를 순서대로 내는 러너. 런타임을 있는 것으로 둔다."""
    monkeypatch.setattr(container_tools.shutil, "which", lambda name: f"/usr/bin/{name}")

    def build(*results: tuple[int, str, str]) -> ContainerToolRunner:
        monkeypatch.setattr(container_tools.subprocess, "run", _Seq(*results))
        return ContainerToolRunner(
            ids=IdFactory(RUN_ID),
            run_id=RUN_ID,
            root=TARGET,
            artifacts_root=tmp_path,
            runtime="docker",
            observed_at=OBSERVED_AT,
            poc_target=ARVO,
        )

    return build


def _asan(runner: ContainerToolRunner) -> Evidence:
    return runner.run("asan", ToolArgs(argv=["asan", "."], workdir="."), SANDBOX)


def _poc(runner: ContainerToolRunner) -> Evidence:
    args = ToolArgs(argv=["poc", INPUT_PLACEHOLDER], workdir=".", input_bytes=b"AAAA")
    return runner.run("poc", args, SANDBOX)


# --- 양성: 배너가 있으면 True, 배너 줄이 실린다 -----------------------------------------


@pytest.mark.parametrize("banner", BANNERS, ids=BANNER_IDS)
@pytest.mark.parametrize("tool", ["asan", "poc"])
def test_a_sanitizer_banner_in_the_target_log_is_recorded(tool: str, banner: str) -> None:
    """양성 핀 다섯 — ASan·MSan·UBSan·libFuzzer·ASan SEGV. 모양은 sanitizer 문서에서 왔다(저장소에
    녹음이 0건이다 — 모듈 docstring)."""
    log = f"some target output\n{banner}\n    #0 0x4 in parse_header parser.c:7\n"
    steps = [Step(0, "", ""), Step(1, "", log)] if tool == "asan" else [Step(1, "", log)]

    assert observe_crash(tool, steps, len(steps)) == (True, banner)


def test_the_banner_is_searched_in_the_whole_log_not_the_head_line() -> None:
    """로그 머리줄은 `# node argv: …`이고 배너는 한참 아래에 있다. stdout에 있어도 맞는다."""
    noise = "\n".join(f"line {i}" for i in range(500))
    stdout = f"{noise}\n{ASAN}\n{noise}"

    assert observe_crash("poc", [Step(1, stdout, "")], 1) == (True, ASAN)


def test_the_banner_line_is_bounded_and_stripped_of_control_characters() -> None:
    """대상이 찍은 날것이 trace·DB로 간다 — 200자로 묶고 제어 문자는 공백으로 바꾼다."""
    log = f"{ASAN}\x1b[0m\x07{'x' * 500}"

    crashed, banner = observe_crash("poc", [Step(1, "", log)], 1)

    assert crashed is True
    assert banner is not None and len(banner) == 200
    assert not any(ord(c) < 0x20 or ord(c) == 0x7F for c in banner)
    assert banner.startswith(ASAN)


# --- 음성: 잰 False와 안 잰 None을 가른다 -------------------------------------------------


@pytest.mark.parametrize("tool", ["asan", "poc"])
def test_a_log_without_a_banner_is_false_not_none(tool: str) -> None:
    """깨끗이 끝난 로그도, 배너 없이 `return 1`로 끝난 로그도 `False`다 — 로그를 읽었고 없었다."""
    for code, out in ((0, "ok\n"), (1, "usage: parser <file>\n")):
        steps = [Step(0, "", ""), Step(code, out, "")] if tool == "asan" else [Step(code, out, "")]
        assert observe_crash(tool, steps, len(steps)) == (False, None), (tool, code)


def test_only_the_last_command_is_the_target_so_a_build_log_is_not_read() -> None:
    """asan의 앞 명령은 빌드다. 컴파일러 출력이 배너 철자를 품어도 대상의 흔적이 아니다."""
    steps = [Step(0, "", f"warning: {UBSAN}"), Step(0, "", "")]

    assert observe_crash("asan", steps, 2) == (False, None)


def test_a_target_that_never_ran_is_unmeasured() -> None:
    """빌드에서 끊겨 대상이 안 떴으면 `None`이다 — `False`가 아니다. 런타임 도구가 아니어도 같다."""
    assert observe_crash("asan", [Step(1, "", ASAN)], 2) == (None, None)
    assert observe_crash("asan", [], 2) == (None, None)
    assert observe_crash("semgrep", [Step(0, "", ASAN)], 1) == (None, None)


# --- gdb: 수명주기 줄을 쓴다 ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        ("[Inferior 1 (process 7) exited normally]", (False, None)),
        ("[Inferior 1 (process 7) exited with code 052]", (False, None)),
        (
            "Program received signal SIGSEGV, Segmentation fault.",
            (True, "Program received signal SIGSEGV, Segmentation fault."),
        ),
        ("Program terminated with signal SIGABRT, Aborted.",
         (True, "Program terminated with signal SIGABRT, Aborted.")),
        (
            'Thread 2 "worker exited" received signal SIGSEGV, Segmentation fault.',
            (True, 'Thread 2 "worker exited" received signal SIGSEGV, Segmentation fault.'),
        ),
        ("gdb: no inferior started", (None, None)),
    ],
    ids=["exit-normal", "exit-code", "signal", "terminated", "thread-named-exited", "no-lifecycle"],
)
def test_gdb_reads_the_target_lifecycle_line(stdout: str, expected: tuple) -> None:
    """신호 정지는 `True`, 정상 종료는 `False`, 줄이 없으면 `None`. 스레드 이름에 `exited`가
    들어 있어도 신호 정지다(글자가 아니라 줄의 꼴로 가른다)."""
    assert observe_crash("gdb", [Step(255, stdout, "")], 1) == expected


def test_a_signal_stop_wins_when_both_lifecycle_lines_are_present() -> None:
    stdout = "[Inferior 1 (process 7) exited normally]\nProgram received signal SIGSEGV, x.\n"

    assert observe_crash("gdb", [Step(0, stdout, "")], 1)[0] is True


# --- 러너가 Evidence에 싣는다 ---------------------------------------------------------------


def test_the_runner_records_a_crash_on_the_evidence(run_with) -> None:  # noqa: ANN001
    evidence = _asan(run_with((0, "", ""), (1, "", f"{ASAN}\n")))

    assert (evidence.ok, evidence.exit_code) == (True, 1)
    assert (evidence.crashed, evidence.banner) == (True, ASAN)


def test_the_runner_records_a_poc_crash_on_the_evidence(run_with) -> None:  # noqa: ANN001
    evidence = _poc(run_with((1, "", f"{LIBFUZZER}\n")))

    assert (evidence.ok, evidence.crashed, evidence.banner) == (True, True, LIBFUZZER)


def test_a_clean_exit_is_recorded_as_not_crashed(run_with) -> None:  # noqa: ANN001
    evidence = _asan(run_with((0, "", ""), (0, "fine\n", "")))

    assert (evidence.ok, evidence.exit_code) == (True, 0)
    assert (evidence.crashed, evidence.banner) == (False, None)


def test_a_failed_build_leaves_the_observation_unmeasured(run_with) -> None:  # noqa: ANN001
    evidence = _asan(run_with((1, "", f"{ASAN}\n")))

    assert evidence.ok is False
    assert (evidence.crashed, evidence.banner) == (None, None)


def test_a_timeout_leaves_the_observation_unmeasured(
    monkeypatch: pytest.MonkeyPatch, run_with  # noqa: ANN001
) -> None:
    runner = run_with((0, "", ""))

    def timing_out(argv, **kwargs):  # noqa: ANN001, ANN202
        if argv[1] == "run":
            raise subprocess.TimeoutExpired(argv, 1)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(container_tools.subprocess, "run", timing_out)
    evidence = _asan(runner)

    assert (evidence.ok, evidence.exit_code) == (False, 124)
    assert (evidence.crashed, evidence.banner) == (None, None)


def test_the_fake_runner_does_not_invent_an_observation(tmp_path: Path) -> None:
    """Fake는 실행하지 않는다 — 상수 `exit_code=1`에서 「크래시했다」를 지어내지 않는다."""
    root = tmp_path / "t"
    root.mkdir()
    (root / "a.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
    fake = FakeToolRunner(ids=IdFactory(RUN_ID), run_id=RUN_ID, root=root)

    for tool in ("asan", "gdb", "poc"):
        evidence = fake.run(tool, ToolArgs(argv=[tool, "a.c"], workdir="a.c"), SANDBOX)
        assert (evidence.ok, evidence.exit_code) == (True, 1), tool
        assert (evidence.crashed, evidence.banner) == (None, None), tool


# --- 환경이 낸 종료 코드가 p4에 닿는가 (2026-10-13 코드 대조) ------------------------------------

POC_ENV_CODES = [124, 125, 126, 127, 137, 255, 256, 300, -9]


@pytest.mark.parametrize("code", POC_ENV_CODES)
def test_a_poc_harness_failure_code_is_ok_false(code: int) -> None:
    """`poc`의 환경 코드는 전부 `ok=False`다 — `usable`이 걸러 `p4`에 못 닿는다."""
    ok, exit_code, error = judge("poc", [Step(code, "", "")], 1)

    assert (ok, exit_code) == (False, code)
    assert error is not None


@pytest.mark.parametrize("code", [125, 126, 127])
def test_an_asan_exec_failure_code_is_ok_false(code: int) -> None:
    assert judge("asan", [Step(0, "", ""), Step(code, "", "")], 2)[0] is False


@pytest.mark.parametrize("code", [1, 127, 255])
def test_gdb_without_a_lifecycle_line_is_ok_false_whatever_the_code(code: int) -> None:
    assert judge("gdb", [Step(code, "", "Error in sourced command file")], 1)[0] is False


def test_the_harness_failure_codes_cannot_reach_p4_through_the_runner(run_with) -> None:  # noqa: ANN001
    """종단: 환경 코드를 낸 `poc` 호출을 게이트에 넣으면 E4가 아니다."""
    for code in POC_ENV_CODES:
        evidence = _poc(run_with((code, "", "")))
        result = gate.evaluate(_finding(refs=[evidence.evidence_id]), [evidence])
        assert result.predicates["p4"] is False, code


@pytest.mark.parametrize("code", [137, 255, 300, -9])
def test_asan_environment_codes_still_reach_p4_today(code: int) -> None:
    """🔴 **알려진 구멍을 고정한다 — 고친 것이 아니다.** asan은 `_EXEC_FAILED`(125~127)만 거른다
    (ADR 0014 「한 도구만 바꾼다」). 환경의 SIGKILL(`--memory`)·`255`·범위 밖 값은 `ok=True`로
    올라와 `p4`를 참으로 만든다. `137`은 대상 자신의 OOM과 갈리지 않고 `255`는 gdb 신호 정지의
    값이기도 해서
    게이트에서 일괄로 못 뺐다. 이 검사가 빨개지는 날은 그 구멍을 메운 날이다 — 그때 D80의 ⏵와
    `gate.py`·`container_tools.py`의 같은 서술을 함께 고친다."""
    ok, exit_code, error = judge("asan", [Step(0, "", ""), Step(code, "", "")], 2)
    assert (ok, error) == (True, None)

    result = gate.evaluate(
        _finding(refs=["ev_asan"]),
        [_evidence("ev_asan", EvidenceKind.SANITIZER, ok=ok, exit_code=exit_code)],
    )

    assert result.predicates["p4"] is True


# --- 게이트는 이 값을 읽지 않는다 (지금의 동작을 고정) --------------------------------------------


@pytest.mark.parametrize("kind", [EvidenceKind.SANITIZER, EvidenceKind.CRASH, EvidenceKind.POC_RUN])
def test_p4_ignores_the_crash_observation_today(kind: EvidenceKind) -> None:
    """**지금의 동작을 고정한다 — 의도된 한계다.** `p4`는 `exit_code`만 본다. 배너 없이 `return 1`로
    끝난 건(`crashed=False`, exit 1)도 E4이고, 배너가 찍혔어도 exit 0이면(`crashed=True`, UBSan이
    계속 돌린 경우) E4가 아니다. 이 값을 `p4`가 읽게 하는 전환은 실제 `poc_run` 크래시 녹음이 쌓인
    뒤로 미뤘다(D80 ⏵ 2026-10-13). 그날 이 검사는 의도적으로 바뀐다."""
    for crashed, exit_code, is_e4 in (
        (None, 1, True),
        (False, 1, True),
        (True, 1, True),
        (None, 0, False),
        (False, 0, False),
        (True, 0, False),
    ):
        banner = ASAN if crashed else None
        evidence = _evidence("ev_run", kind, exit_code=exit_code).model_copy(
            update={"crashed": crashed, "banner": banner}
        )

        result = gate.evaluate(_finding(refs=["ev_run"]), [evidence])

        assert result.predicates["p4"] is is_e4, (crashed, exit_code)
        assert (result.grade is EvidenceGrade.E4) is is_e4, (crashed, exit_code)


def test_a_no_banner_return_one_program_is_still_e4_end_to_end(run_with) -> None:  # noqa: ANN001
    """종단: 러너가 `crashed=False`를 적은 `return 1` 건도 게이트는 E4로 읽는다."""
    evidence = _asan(run_with((0, "", ""), (1, "usage: parser <file>\n", "")))
    assert (evidence.ok, evidence.exit_code, evidence.crashed) == (True, 1, False)

    result = gate.evaluate(_finding(refs=[evidence.evidence_id]), [evidence])

    assert result.grade is EvidenceGrade.E4


def test_no_decision_code_reads_the_observation() -> None:
    """게이트·정책·상태 기계·러너 소스에 `crashed`/`banner` **접근**이 0건이다 — 읽는 순간 이 PR의
    약속이 깨진다. 소스를 AST로 훑는다(독스트링이 그 이름을 적는 것은 접근이 아니다)."""
    root = Path(__file__).resolve().parent.parent / "src" / "orchestrator"
    for name in ("gate.py", "policy.py", "runner.py", "state_machine.py"):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert node.attr not in {"crashed", "banner"}, (name, node.lineno)
            if isinstance(node, ast.Constant) and not isinstance(node.value, str):
                continue
            if isinstance(node, ast.keyword):
                assert node.arg not in {"crashed", "banner"}, (name, node.value.lineno)


# --- trace ---------------------------------------------------------------------------------------


class _Stub:
    """`ToolRunner` 대역 — 정해 둔 Evidence를 낸다."""

    def __init__(self, evidence: Evidence) -> None:
        self.evidence = evidence

    def run(self, tool, args, sandbox):  # noqa: ANN001, ANN201
        return self.evidence


def _stub_evidence(**fields: object) -> Evidence:
    base = {
        "evidence_id": "ev_0001",
        "run_id": GATE_RUN_ID,
        "kind": EvidenceKind.SANITIZER,
        "source": "asan",
        "artifact_id": "art_0001",
        "payload_ref": "logs/x.log",
        "digest": "sha256:" + "0" * 64,
        "observed_at": OBSERVED_AT,
        "ok": True,
        "exit_code": 1,
    }
    return Evidence(**{**base, **fields})


def _tool_call(ctx: RunContext, evidence: Evidence):  # noqa: ANN202, F811
    stubbed = dataclasses.replace(ctx, tools=_Stub(evidence))
    stubbed.call_tool(
        tool="asan", args=ToolArgs(argv=["asan", "."], workdir="parser.c"),
        sandbox=DEFAULT_SANDBOX, stage=Stage.VALIDATE,
    )
    return [e for e in stubbed.trace.events if e.kind is EventKind.TOOL_CALL][-1]


def test_the_tool_call_event_carries_the_observation(ctx: RunContext) -> None:  # noqa: F811
    crashed = _tool_call(ctx, _stub_evidence(crashed=True, banner=ASAN))
    clean = _tool_call(ctx, _stub_evidence(crashed=False, exit_code=0))

    assert (crashed.data["crashed"], crashed.data["crash_banner"]) == (True, True)
    assert (clean.data["crashed"], clean.data["crash_banner"]) == (False, False)


def test_an_unmeasured_observation_has_no_key_in_the_tool_call(ctx: RunContext) -> None:  # noqa: F811
    """키 부재 = 안 쟀다. `false`가 아니다 — Fake·정적 도구·대상이 안 뜬 호출이 그렇다."""
    event = _tool_call(ctx, _stub_evidence(crashed=None))

    assert "crashed" not in event.data and "crash_banner" not in event.data


def test_the_fake_pipeline_tool_call_has_no_observation_keys(ctx: RunContext) -> None:  # noqa: F811
    ctx.call_tool(
        tool="asan", args=ToolArgs(argv=["asan", "parser.c"], workdir="parser.c"),
        sandbox=DEFAULT_SANDBOX, stage=Stage.VALIDATE,
    )
    [event] = [e for e in ctx.trace.events if e.kind is EventKind.TOOL_CALL]

    assert "crashed" not in event.data


# --- 스키마와 저장 ------------------------------------------------------------------------


def test_a_failed_evidence_cannot_carry_an_observation() -> None:
    with pytest.raises(ValidationError, match="crashed"):
        _stub_evidence(ok=False, error="실패", crashed=False)


def test_a_banner_needs_crashed_true() -> None:
    with pytest.raises(ValidationError, match="banner"):
        _stub_evidence(crashed=False, banner=ASAN)
    with pytest.raises(ValidationError, match="banner"):
        _stub_evidence(banner=ASAN)


def test_the_banner_is_bounded_by_the_schema() -> None:
    with pytest.raises(ValidationError):
        _stub_evidence(crashed=True, banner="x" * 201)
    assert _stub_evidence(crashed=True, banner="x" * 200).banner == "x" * 200


def test_a_row_written_before_the_fields_existed_still_loads() -> None:
    """저장 형태는 JSON 한 컬럼이다 — 두 키가 없는 옛 행은 기본값(`None`)으로 읽힌다."""
    payload = _stub_evidence().model_dump()
    payload.pop("crashed")
    payload.pop("banner")

    loaded = Evidence.model_validate(payload)

    assert (loaded.crashed, loaded.banner) == (None, None)


def test_the_observation_round_trips_through_the_store() -> None:
    store = sqlite_store.SqliteStateStore(":memory:")
    evidence = _stub_evidence(crashed=True, banner=ASAN)

    store.put(evidence)

    assert store.get("evidence", evidence.evidence_id) == evidence


# --- 산문 (E4의 뜻) ------------------------------------------------------------------------------


def _read(*parts: str) -> str:
    return Path(__file__).resolve().parent.parent.joinpath(*parts).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("path", "phrase"),
    [
        (("docs", "architecture.md"), "E4 = 「실행됐고 비-0으로 끝났다」 — 대용 지표다."),
        (("docs", "architecture.md"), "판정은 오라클(ADR 0013·0014)"),
        (("docs", "evaluation.md"), "`proven`의 뜻은 하나다 — 「실행됐고 비-0으로 끝났다」"),
        (("src", "orchestrator", "gate.py"), "E4 = 「실행됐고 비-0으로"),
        (("src", "orchestrator", "nodes", "validate.py"), "재현 확정이 아니다"),
    ],
    ids=["arch-e4-row", "arch-oracle", "eval-proven", "gate-docstring", "validate-docstring"],
)
def test_the_prose_says_e4_is_a_proxy_not_a_reproduction(
    path: tuple[str, ...], phrase: str
) -> None:
    assert phrase in _read(*path)
