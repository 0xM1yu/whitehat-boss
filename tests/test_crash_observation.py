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
import sqlite3
import subprocess
import textwrap
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

# 2026-10-13 첫 재생(리뷰어, arvo-1065 4건 · oss-fuzz 10건 — D80 「첫 재생」)에서 온 꼴이다. 이 셋은
# 실제 로그의 모양이고 위 다섯은 문서의 모양이다.
LIBFUZZER_SPACED = "==7== ERROR: libFuzzer: deadly signal"  # `==N==` 뒤 공백
SHELL_SEGV = "Segmentation fault (core dumped)"  # exit 139인데 이 한 줄뿐인 로그
LEAK = "==9==ERROR: LeakSanitizer: detected memory leaks"  # exit 0과 함께 왔다

BANNERS = [ASAN, MSAN, UBSAN, LIBFUZZER, ASAN_SEGV, LIBFUZZER_SPACED, SHELL_SEGV, LEAK]
BANNER_IDS = [
    "asan", "msan", "ubsan", "libfuzzer", "asan-segv", "libfuzzer-spaced", "shell-segv", "leak"
]


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


@pytest.mark.parametrize(
    "line",
    [
        "Segmentation fault",
        "Segmentation fault (core dumped)",
        "bash: line 1:    42 Segmentation fault      (core dumped) /bin/arvo",
        "Aborted (core dumped)",
        "Bus error (core dumped)",
        # ⏵ 2026-10-13 — 아래 둘은 빠져 있어 `crashed=False`였다(이 함수를 직접 호출해 확인).
        # bash가 `Bus error`와 같은 꼴로 찍는 묶음이다. 🔴 이 하네스 로그로 대조한 것은 아니다.
        "Illegal instruction (core dumped)",
        "Floating point exception (core dumped)",
    ],
)
def test_a_shell_signal_report_alone_is_a_crash_observation(line: str) -> None:
    """🔴 거짓 음성이던 모드: exit 139인데 sanitizer 배너 없이 셸의 신호 보고 한 줄뿐인 로그
    (arvo-1065 재생 2건). `(core dumped)`는 선택이다."""
    assert observe_crash("poc", [Step(139, "", f"{line}\n")], 1) == (True, line.strip())


def test_a_shell_signal_word_inside_another_word_is_not_a_report() -> None:
    """단어 경계 — 기록뿐이라 오탐은 싸지만 낱말 안쪽까지 맞추지는 않는다."""
    assert observe_crash("poc", [Step(1, "", "Segmentation faults are scary\n")], 1)[0] is False


def test_a_leak_report_is_true_with_the_banner_even_at_exit_zero() -> None:
    """🔴 `crashed=True`는 exit 0과 같이 올 수 있다(oss-fuzz 재생 5/5). 오라클은 exit 0을
    크래시 없음으로 읽는다 — 그래서 `banner`를 같이 싣고 읽는 쪽이 가른다.
    둘을 한 Evidence에서 같이 본다."""
    crashed, banner = observe_crash("poc", [Step(0, "", f"{LEAK}\n")], 1)

    assert (crashed, banner) == (True, LEAK)
    evidence = _stub_evidence(exit_code=0, crashed=crashed, banner=banner)
    assert (evidence.exit_code, evidence.crashed, evidence.banner) == (0, True, LEAK)


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


def test_a_real_banner_on_stderr_beats_a_look_alike_the_target_printed_on_stdout() -> None:
    """양성 핀 — **읽는 쪽이 가를 수 있게 진짜 배너가 `banner`에 와야 한다.**

    종전에는 `f"{stdout}\\n{stderr}"` 한 덩이의 첫 히트를 썼고 stdout이 앞이라 대상이 직접
    찍은 `runtime error:`(UBSan 철자와 같은 글 — `_CRASH_BANNER` 주석이 「오탐 가능」이라
    적는 그 줄)이 stderr의 ASan 배너를 이겼다. 2026-10-13 이 함수를 직접 호출해 확인했다.
    `crashed`는 양쪽 다 `True`라 **배너 쪽만 틀렸다** — 그래서 값 자체를 단언한다.
    """
    printed = "parser: runtime error: 입력이 범위를 넘었다"

    assert observe_crash("poc", [Step(1, f"{printed}\n", f"{ASAN}\n")], 1) == (True, ASAN)
    # 음성 핀 — stderr에 히트가 없으면 stdout으로 내려간다(`2>&1`로 합쳐진 로그가 그쪽이다).
    assert observe_crash("poc", [Step(1, f"{ASAN}\n", "")], 1) == (True, ASAN)
    # 음성 핀 — 오탐뿐이면 종전대로 그 줄이 배너다. 가려낼 근거가 없다(§0).
    assert observe_crash("poc", [Step(1, f"{printed}\n", "")], 1) == (True, printed)


def test_a_banner_behind_a_long_prefix_on_the_same_line_stays_in_the_window() -> None:
    """양성 핀 — 줄머리에서 200자를 떼면 **배너 철자가 창 밖으로 나간다.**

    2026-10-13 이 함수를 직접 호출해 확인: `"X" * 300 + ASAN`의 저장된 줄이 `X` 200자였다.
    배너를 안 든 `banner`는 「읽는 쪽이 배너를 보고 가른다」를 못 받친다.
    """
    long_line = f"{'X' * 300}{ASAN}"

    crashed, banner = observe_crash("poc", [Step(1, "", f"{long_line}\n")], 1)

    assert crashed is True
    assert banner is not None and len(banner) <= 200
    assert "AddressSanitizer" in banner
    assert banner.startswith("…"), "버린 접두를 표시한다"
    # 음성 핀 — 줄이 상한 안이면 창은 종전대로 줄머리부터다(접두를 잃지 않는다).
    short = f"bash: line 1:    42 {SHELL_SEGV} /bin/arvo"
    assert observe_crash("poc", [Step(139, "", f"{short}\n")], 1) == (True, short)


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


def test_the_runner_records_an_exit_139_with_only_the_shell_line(run_with) -> None:  # noqa: ANN001
    """종단: 첫 재생의 거짓 음성 모드가 러너 경로에서도 `True`로 기록된다."""
    evidence = _poc(run_with((139, "", f"{SHELL_SEGV}\n")))

    assert (evidence.ok, evidence.exit_code) == (True, 139)
    assert (evidence.crashed, evidence.banner) == (True, SHELL_SEGV)


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


_OBSERVED = {"crashed", "banner", "crash_banner"}


# trace 이벤트를 내는 메서드 이름. 읽기를 빼는 기준은 **이 역할 하나**다 — 파일 이름이 아니다.
_EMIT = "emit"


def _observation_read_nodes(tree: ast.AST) -> list[tuple[ast.AST, int]]:
    """`crashed`/`banner`을 **읽는** (노드, 줄 번호) — 속성 접근 · 키워드 인자 ·
    `getattr(x, "crashed")` · `x["crashed"]` · `x.get("crashed")`.
    독스트링이 그 이름을 적는 것은 읽기가 아니다.

    줄 번호가 아니라 **노드**를 돌려주는 것이 중요하다 — 한 줄에 허용 읽기와 판정 읽기가
    같이 있으면 줄 단위로 빼면 뒤쪽이 묻힌다."""
    found: list[tuple[ast.AST, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _OBSERVED:
            found.append((node, node.lineno))
        elif isinstance(node, ast.keyword) and node.arg in _OBSERVED:
            found.append((node, node.value.lineno))
        elif isinstance(node, ast.Subscript):
            key = node.slice
            if isinstance(key, ast.Constant) and key.value in _OBSERVED:
                found.append((node, node.lineno))
        elif isinstance(node, ast.Call) and node.args:
            fn = node.func
            named = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            keys = [a.value for a in node.args[:2] if isinstance(a, ast.Constant)]
            if named in {"getattr", "hasattr", "get"} and _OBSERVED & set(keys):
                found.append((node, node.lineno))
    return found


def _observation_reads(source: str) -> list[int]:
    return [line for _, line in _observation_read_nodes(ast.parse(source))]


def _assign_target_names(stmt: ast.AST) -> set[str]:
    """이 문장이 **이름에** 대입하는 것들. 대입문이 아니면 빈 집합."""
    if isinstance(stmt, ast.Assign):
        return {t.id for t in stmt.targets if isinstance(t, ast.Name)}
    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
        return {stmt.target.id}
    return set()


def _emit_payload_names(scope: ast.AST) -> set[str]:
    """이 범위에서 `*.emit(...)`에 `**`로 펼쳐지는 이름들 — trace 페이로드의 이름이다."""
    names: set[str] = set()
    for node in ast.walk(scope):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == _EMIT:
            names |= {
                kw.value.id
                for kw in node.keywords
                if kw.arg is None and isinstance(kw.value, ast.Name)
            }
    return names


def _record_only_regions(tree: ast.AST) -> list[ast.AST]:
    """읽은 값이 **기록으로만** 흐르는 부분 나무. 셋뿐이고 전부 역할로 정의된다.

    ① `*.emit(...)` 호출 그 자체 — 인자로 들어간 읽기.
    ② emit에 `**`로 펼쳐지는 이름에 대입하는 문장의 **값**.
    ③ 그 대입**만** 든 `if`의 **조건** — 키를 실을지 말지 가르는 자리다(`else`가 붙으면 안
      된다). 관측이 없을 때 키를 빼는 것이 이 저장소의 「키 부재 = 안 쟀다」 관용구라서다.

    셋 밖의 읽기는 전부 판정 후보로 센다. 「어느 파일이 판정 파일인가」를 맞히지 않는다 —
    그 축으로 고르던 앞 판이 `nodes/validate.py`를 통째로 놓쳤다(CLAUDE.md §2.5).
    """
    regions: list[ast.AST] = []
    # ⏵ 2026-10-14 (적대적 리뷰 HIGH) — **모듈 나무를 scope에 넣지 않는다.** 넣으면
    # `splatted`가 **파일 전역 합집합**이 되고, 한 함수가 `emit(**observed)`를 쓰는 파일에서는
    # **어느 함수에서든** `observed = <crashed를 읽는 식>`이 허용됐다. `context.py`가 바로
    # 그런 파일이고(`observed`·`measured`·`data`·`fields`) 같은 파일에 `evaluate_gate`가 있다
    # — 크래시로 등급을 가를 가장 자연스러운 자리다. 실측: `evaluate_gate`에
    # `observed = any(ev.crashed for ev in evidences)`를 넣은 변이가 **통과했다**.
    # 함수가 없는 모듈(상수만 든 파일)에서만 모듈 나무로 떨어진다.
    scopes: list[ast.AST] = [
        n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ] or [tree]
    for scope in scopes:
        splatted = _emit_payload_names(scope)
        for node in ast.walk(scope):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == _EMIT:
                regions.append(node)
            elif _assign_target_names(node) & splatted:
                value = getattr(node, "value", None)
                if value is not None:
                    regions.append(value)
            elif isinstance(node, ast.If) and node.body and not node.orelse:
                if all(_assign_target_names(s) & splatted for s in node.body):
                    regions.append(node.test)
    return regions


def _decision_reads(source: str) -> list[int]:
    """`crashed`·`crash_banner`를 **판정에** 쓰는 줄 번호 — 기록 전용 읽기는 뺀다."""
    tree = ast.parse(source)
    allowed: set[int] = set()
    for region in _record_only_regions(tree):
        allowed |= {id(node) for node in ast.walk(region)}
    return sorted(line for node, line in _observation_read_nodes(tree) if id(node) not in allowed)


def test_the_observation_detector_catches_every_read_form() -> None:
    reads = (
        "x.crashed", "f(crashed=1)", 'getattr(x, "crashed", None)',
        'x["banner"]', 'x.get("crashed")',
    )
    for src in reads:
        assert _observation_reads(src) == [1], src
    assert _observation_reads('"""crashed banner"""\nx = {"a": 1}["a"]') == []


def test_no_orchestrator_code_uses_the_observation_to_decide() -> None:
    """막는 명제 — **`crashed`·`crash_banner`를 판정에 쓰는 곳이 `src/orchestrator/` 아래
    어디에도 없다.**

    앞 판은 `gate.py`·`policy.py`·`runner.py`·`state_machine.py` **네 파일만** 봤다.
    3라운드 적대적 리뷰가 `nodes/validate.py`의 `grade` 신호를 crash 기반으로 바꾸는 변이로
    그 구멍을 통과했다(548 passed) — 8단계 상태를 실제로 가르는 노드가 검사 밖이었다.
    그래서 축을 「파일 이름」에서 「역할」로 바꿨다: 전수는 `src/orchestrator/**` 전체이고
    빼는 것은 trace emit 페이로드로 가는 읽기뿐이다(`_record_only_regions`).
    """
    root = Path(__file__).resolve().parent.parent / "src" / "orchestrator"
    sources = sorted(root.rglob("*.py"))
    assert len(sources) > 4, "전수 전제 - 앞 판이 보던 네 파일보다 많다"
    offenders = {
        str(path.relative_to(root)): lines
        for path in sources
        if (lines := _decision_reads(path.read_text(encoding="utf-8")))
    }
    assert offenders == {}


def test_the_decision_detector_passes_trace_payloads_and_catches_everything_else() -> None:
    """`_decision_reads`의 핀.

    음성 핀 — **저장소 실형태**다(`src/orchestrator/context.py:697-712`에서 변수 이름만 줄였다):
    `if ev.crashed is not None:` 조건과 페이로드 dict, 그리고 `**observed`로 펼치는
    `emit` 호출. 이 셋이 걸리면 기록 전용 코드가 판정으로 잡혀 검사가 못 선다.

    양성 핀 — ① 3라운드에서 생존한 변이의 형태다: `nodes/validate.py`의 `decide()` 신호에
    `ev.crashed`를 넣는 것(원형은 같은 파일의 `"ok": bool(produced) and all(ev.ok for ev in
    produced)`). ② 페이로드를 만든 **뒤** 그 키로 가르는 형태 — 대입은 허용 영역이지만
    `observed["crashed"]`를 다시 읽는 자리는 아니다. ③ `else`가 붙은 분기 — 키를 뺄지
    가르는 자리가 아니라 길을 가르는 자리다. 셋은 저장소에 없는 형태이므로(검사가 막는
    것이므로) 지어낸 샘플이고, 그 근거로 ①은 실제 변이, ②·③은 ①을 「대입을 거치면」·
    「`if`를 쓰면」으로 비켜 가는 최소 변형이다.
    """
    record_only = textwrap.dedent(
        """
        def call_tool(self):
            observed: dict[str, object] = {}
            if ev.crashed is not None:
                observed = {"crashed": ev.crashed, "crash_banner": ev.banner is not None}
            self.trace.emit(kind, ok=ev.ok, **observed)
        """
    )
    assert _decision_reads(record_only) == []
    assert _observation_reads(record_only) != [], "전제 - 이 소스에 읽기는 분명히 있다"

    decides = textwrap.dedent(
        """
        def validate(self):
            self.trace.emit(kind, **observed)
            return ctx.decide(signals={"ok": all(ev.crashed for ev in produced)})
        """
    )
    assert _decision_reads(decides) == [4]

    reads_back = textwrap.dedent(
        """
        def validate(self):
            observed = {"crashed": evidence.crashed}
            self.trace.emit(kind, **observed)
            if observed["crashed"]:
                grade = "E4"
        """
    )
    assert _decision_reads(reads_back) == [5]

    branches = textwrap.dedent(
        """
        def validate(self):
            if evidence.crashed:
                observed = {"x": 1}
            else:
                observed = {"y": 2}
            self.trace.emit(kind, **observed)
        """
    )
    assert _decision_reads(branches) == [3]

    # ⏵ 2026-10-14 (적대적 리뷰 HIGH) — ④ **세탁형**: 같은 파일의 **다른 함수**가
    # `emit(**observed)`를 쓴다고 해서 이 함수의 `observed = <crashed 읽기>`가 허용되면 안 된다.
    # 이것이 실측된 구멍이다 — `src/orchestrator/context.py`의 `evaluate_gate`에 그 두 줄을 넣은
    # 변이가 **통과했다**(`_record_only_regions`가 모듈 나무를 scope로 넣고 있었다).
    laundered = textwrap.dedent(
        """
        def call_tool(self):
            observed = {"crashed": ev.crashed}
            self.trace.emit(kind, **observed)

        def evaluate_gate(self, evidences):
            observed = any(ev.crashed for ev in evidences)
            if observed:
                return "E4"
        """
    )
    assert _decision_reads(laundered) == [7], "세탁형 - 다른 함수의 splat 이름을 빌리면 안 된다"

    # ⚠ **못 잡는 것 둘 — 참인 명제는 「읽기가 emit 페이로드 밖에 직접 나타나지 않는다」까지다.**
    # 아래 둘은 읽기가 `emit` 호출 **안**에 있어 허용 영역 ①에 들어간다. 저장소에 실형태가
    # 없고(검사가 막는 쪽이라 지어낸 샘플이다) 막으려면 emit 인자 안을 다시 훑어야 하는데
    # 그러면 ①의 뜻이 사라진다. 그래서 **한계로 적는다**(CLAUDE.md §2.5 - 성질을 단언하려면
    # 성질을 표현하는 형태를 찾고, 못 찾으면 그 한계를 적는다).
    inside_emit = textwrap.dedent(
        """
        def validate(self):
            self.trace.emit(kind, grade=self.promote(ev.crashed))
        """
    )
    assert _decision_reads(inside_emit) == [], "한계 - emit 인자 안의 판정은 안 잡힌다"

    walrus_in_emit = textwrap.dedent(
        """
        def validate(self):
            self.trace.emit(kind, x=(c := ev.crashed))
            if c:
                grade = "E4"
        """
    )
    assert _decision_reads(walrus_in_emit) == [], "한계 - emit 안 walrus로 뺀 값은 안 잡힌다"


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


def test_the_version_error_names_the_change_that_raised_the_version(tmp_path: Path) -> None:
    """판 1 파일을 연 사람이 **어느 체크아웃이 읽는지** 안다 — 판 2를 올린 변경을 댄다."""
    path = tmp_path / "state.db"
    sqlite_store.SqliteStateStore(path).close()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 1")
    conn.close()

    with pytest.raises(sqlite_store.StateDbVersionError, match="#232") as raised:
        sqlite_store.SqliteStateStore(path)

    assert "이전 체크아웃" in str(raised.value)


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
