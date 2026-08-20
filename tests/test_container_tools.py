"""ContainerToolRunner — 컨테이너를 띄우지 않고 계약을 검사한다.

명령 조립이 순수 함수(`plan`·`container_argv`·`judge`)로 분리돼 있어 Docker가 없는 CI에서도
돈다. 실제 실행 검사는 파일 끝에 `shutil.which("docker")` 조건부로 따로 둔다.

여기서 지키는 것은 대부분 **조용히 깨지는** 계약이다: 거절 경로가 프로세스를 띄우지 않는지,
`ok`와 `exit_code`의 의미가 섞이지 않는지, 컴파일 성공이 실행으로 둔갑하지 않는지.
"""

import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.adapters import container_tools
from src.adapters.container_tools import (
    DEFAULT_IMAGE,
    INDEX_FILE,
    INDEX_TOOL,
    Command,
    ContainerToolRunner,
    Step,
    container_argv,
    index_output_path,
    judge,
    plan,
    scratch_dir,
    semgrep_results,
    sif_path,
    summary_line,
)
from src.adapters.fake_tools import TOOL_KINDS, SandboxUnavailable
from src.ids import IdFactory
from src.interfaces.tool_runner import SandboxSpec, ToolArgs
from src.orchestrator.nodes.sandbox import DEFAULT_SANDBOX
from src.schemas.enums import EvidenceKind
from src.schemas.evidence import Evidence

RUN_ID = "run_20260901_0001"
TARGET = Path(__file__).resolve().parent / "fixtures" / "target"
OBSERVED_AT = datetime(2026, 9, 1, tzinfo=UTC)

SANDBOX = SandboxSpec(
    image=DEFAULT_IMAGE,
    network=False,
    read_only=True,
    cpu_limit=1.0,
    memory_mb=2048,
    timeout_s=300,
)


class _Spy:
    """`subprocess.run` 대역. 호출 여부와 argv를 기록한다."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def __call__(self, argv, **kwargs):  # noqa: ANN001, ANN204
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, self.stderr)


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> _Spy:
    """런타임을 있는 것으로 만들고 실제 실행만 가로챈다."""
    monkeypatch.setattr(container_tools.shutil, "which", lambda name: f"/usr/bin/{name}")
    recorder = _Spy()
    monkeypatch.setattr(container_tools.subprocess, "run", recorder)
    return recorder


def _runner(
    tmp_path: Path, runtime: str = "docker", *, root: Path = TARGET, **kwargs  # noqa: ANN003
) -> ContainerToolRunner:
    return ContainerToolRunner(
        ids=IdFactory(RUN_ID),
        run_id=RUN_ID,
        root=root,
        artifacts_root=tmp_path,
        runtime=runtime,
        observed_at=OBSERVED_AT,
        **kwargs,
    )


# --- 1~3. 실행 전에 거절되는 경로 ---------------------------------------------


def test_tool_outside_whitelist_never_starts_a_process(tmp_path: Path, spy: _Spy) -> None:
    """tool_policy 밖 호출을 조용히 성공시키지 않는다."""
    evidence = _runner(tmp_path).run("rm", ToolArgs(argv=["rm", "-rf", "/"], workdir="."), SANDBOX)

    assert evidence.ok is False
    assert evidence.exit_code == 127
    assert "tool_policy" in evidence.error
    assert spy.calls == []


def test_network_true_never_starts_a_process(tmp_path: Path, spy: _Spy) -> None:
    """disclosure-policy.md §5: 외부 시스템 실행 금지. 정책이 실행 전에 걸려야 한다."""
    sandbox = SANDBOX.transition(network=True)

    evidence = _runner(tmp_path).run(
        "semgrep", ToolArgs(argv=["semgrep", "."], workdir="."), sandbox
    )

    assert evidence.ok is False
    assert evidence.error == "network=True는 허용되지 않는다"
    assert spy.calls == []


def test_workdir_outside_root_is_absorbed_into_evidence(tmp_path: Path, spy: _Spy) -> None:
    """`PathEscape`를 예외로 올리지 않고 Evidence로 만든다 (계약 1)."""
    evidence = _runner(tmp_path).run(
        "semgrep", ToolArgs(argv=["semgrep", "x"], workdir="../../etc"), SANDBOX
    )

    assert evidence.ok is False
    assert "밖을 가리킨다" in evidence.error
    assert spy.calls == []


# --- 4~5. 명령 조립 ------------------------------------------------------------


def test_docker_argv_carries_the_sandbox_policy(tmp_path: Path) -> None:
    argv = container_argv(
        "docker",
        sandbox=SANDBOX,
        root=TARGET,
        scratch=tmp_path,
        command=Command(["semgrep", "scan"], cwd="/src"),
        name="harness-x",
        sif_dir=tmp_path,
    )

    assert argv[:3] == ["docker", "run", "--rm"]
    assert "--network=none" in argv
    assert "--read-only" in argv
    assert argv[argv.index("--cpus") + 1] == "1.0"
    assert argv[argv.index("--memory") + 1] == "2048m"
    assert f"{TARGET.resolve().as_posix()}:/src:ro" in argv
    assert argv[argv.index("-w") + 1] == "/src"
    # 이미지 다음이 곧 도구 명령이다. 순서가 어긋나면 docker가 이미지를 인자로 먹는다.
    assert argv[argv.index(DEFAULT_IMAGE) + 1 :] == ["semgrep", "scan"]


def test_podman_argv_matches_docker_except_the_binary(tmp_path: Path) -> None:
    """podman은 docker CLI 호환이라 바이너리 이름만 갈리고 격리 플래그는 동일하다.

    ATOM은 Docker 데몬 접근도 singularity도 없어 이 경로가 유일한 실서빙 런타임이다
    (2026-08-17). 그래서 실서빙 프로필(`local_balanced`·`b1_single`)이 `podman`을 직접
    선언한다(docs/portability.md §3). `dev_docker`처럼 다른 런타임을 선언한 프로필만
    `--tool-runner podman`으로 덮는다(ADR 0005).
    """
    kwargs = dict(
        sandbox=SANDBOX,
        root=TARGET,
        scratch=tmp_path,
        command=Command(["semgrep", "scan"], cwd="/src"),
        name="harness-x",
        sif_dir=tmp_path,
    )
    podman = container_argv("podman", **kwargs)
    docker = container_argv("docker", **kwargs)

    assert podman[0] == "podman"
    # 첫 토큰(바이너리)만 다르고 나머지는 한 글자도 다르지 않다 — 격리가 갈리면 두 장비의
    # 결과를 같은 비교 셀에 넣을 수 없다.
    assert podman[1:] == docker[1:]
    assert "--network=none" in podman
    assert "--read-only" in podman
    assert podman[podman.index("--memory") + 1] == "2048m"


def test_singularity_argv_carries_the_sandbox_policy(tmp_path: Path) -> None:
    argv = container_argv(
        "singularity",
        sandbox=SANDBOX,
        root=TARGET,
        scratch=tmp_path,
        command=Command(["semgrep", "scan"], cwd="/src"),
        name="harness-x",
        sif_dir=tmp_path,
    )

    assert argv[:3] == ["singularity", "exec", "--containall"]
    assert "--net" in argv
    assert "--network=none" in argv
    assert f"{TARGET.resolve().as_posix()}:/src:ro" in argv
    assert argv[argv.index("--pwd") + 1] == "/src"
    assert str(sif_path(tmp_path, DEFAULT_IMAGE)) in argv


def test_network_true_is_not_expressible_as_an_isolated_command() -> None:
    """`network=True`가 실수로 새면 격리 플래그가 사라진다는 사실을 고정한다.

    이 경로는 `run()`이 이미 막지만, 조립 함수만 따로 쓰는 코드가 생겼을 때
    "네트워크가 열린 명령"이 조용히 만들어지는 것을 눈에 보이게 둔다.
    """
    argv = container_argv(
        "docker",
        sandbox=SANDBOX.transition(network=True),
        root=TARGET,
        scratch=TARGET,
        command=Command(["semgrep"], cwd="/src"),
        name="n",
        sif_dir=TARGET,
    )

    assert "--network=none" not in argv


@pytest.mark.parametrize("runtime", ["docker", "podman", "singularity"])
def test_tool_env_is_forwarded_not_dropped(runtime: str, tmp_path: Path) -> None:
    """`ToolArgs.env`를 조용히 버리지 않는다. 정렬해서 넘긴다 — 순서도 재현성이다."""
    argv = container_argv(
        runtime,
        sandbox=SANDBOX,
        root=TARGET,
        scratch=tmp_path,
        command=Command(["clang"], cwd="/work"),
        name="harness-x",
        sif_dir=tmp_path,
        env={"B": "2", "A": "1"},
    )

    assert [argv[i + 1] for i, value in enumerate(argv) if value == "--env"] == ["A=1", "B=2"]


def test_sif_path_maps_image_tag_to_a_file() -> None:
    assert sif_path(Path("/data/sif"), "harness-tools:v1").name == "harness-tools_v1.sif"


# --- 6. SIF 부재 ---------------------------------------------------------------


def test_missing_sif_is_reported_with_its_path(tmp_path: Path, spy: _Spy) -> None:
    """자동으로 pull 하지 않는다 — 실행 중 네트워크는 network=False와 충돌한다."""
    runner = _runner(tmp_path, runtime="singularity", sif_dir=tmp_path / "sif")

    evidence = runner.run("semgrep", ToolArgs(argv=["semgrep", "."], workdir="."), SANDBOX)

    assert evidence.ok is False
    assert "SIF가 없다" in evidence.error
    assert "harness-tools_v1.sif" in evidence.error
    assert spy.calls == []


# --- 7. 타임아웃 ---------------------------------------------------------------


def test_timeout_becomes_evidence_and_removes_the_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """타임아웃 뒤 컨테이너를 남기면 run마다 하나씩 쌓인다."""
    monkeypatch.setattr(container_tools.shutil, "which", lambda name: f"/usr/bin/{name}")
    calls: list[list[str]] = []

    def _run(argv, **kwargs):  # noqa: ANN001, ANN202
        calls.append(list(argv))
        if argv[1] == "run":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(container_tools.subprocess, "run", _run)

    evidence = _runner(tmp_path).run(
        "semgrep", ToolArgs(argv=["semgrep", "."], workdir="."), SANDBOX
    )

    assert evidence.ok is False
    assert "timeout" in evidence.error
    assert calls[-1][:3] == ["docker", "rm", "--force"]


# --- 8. Evidence 계약 회귀 ------------------------------------------------------


def test_evidence_rejects_ok_true_with_an_error() -> None:
    """`ok`와 `error`가 어긋난 Evidence는 스키마가 막는다.

    이 클래스의 모든 반환이 그 검증기를 지난다 — 즉 "에러를 삼키고 성공을 반환"이
    구조적으로 불가능하다는 것을 여기서 한 번 고정한다.
    """
    with pytest.raises(ValidationError):
        Evidence(
            evidence_id="ev_0001",
            run_id=RUN_ID,
            kind=EvidenceKind.STATIC,
            source="semgrep",
            artifact_id=".",
            payload_ref="artifacts/x.log",
            digest=f"sha256:{'0' * 64}",
            observed_at=OBSERVED_AT,
            ok=True,
            exit_code=0,
            error="사유가 있는데 성공이라고 말한다",
        )


# --- 9. D11 관례 ---------------------------------------------------------------


def test_semgrep_gets_error_flag_and_a_local_ruleset() -> None:
    """`--error`가 빠지면 semgrep은 발견해도 0을 반환하고 모든 정적 신호가 사라진다.

    `--config`가 로컬 경로여야 하는 것도 같은 이유다 — 레지스트리를 보면 `--network=none`
    아래에서 도구가 죽고, 그것이 "신호 없음"으로 집계된다.
    """
    argv = plan("semgrep", target="/src/parser.c", compile_sources=[], cwd="/src")[0].argv

    assert "--error" in argv
    # 발견 여부의 권위 있는 근거는 결과 문서다. exit code는 그것이 믿을 만한지 판정한
    # 뒤에야 쓰인다(2026-08-14 크래시 실측).
    assert "--json" in argv
    assert all(not value.startswith("p/") for value in argv), "레지스트리 룰셋을 참조한다"
    assert any(value.startswith("/opt/semgrep-rules") for value in argv)


def test_clang_tidy_turns_warnings_into_a_nonzero_exit() -> None:
    argv = plan("clang-tidy", target="/src", compile_sources=["/src/parser.c"], cwd="/src")[0].argv

    assert "--warnings-as-errors=*" in argv


def test_every_clang_tool_uses_the_same_dialect() -> None:
    """같은 파일이 한 도구에서 통과하고 다른 도구에서 오류가 나면 안 된다.

    2026-08-14 실측: clang-tidy만 `-std=c11`이라 util.c의 `strnlen`이 거기서만 미선언
    오류였다. 그 차이가 코드의 성질인지 우리 플래그의 산물인지 구분할 수 없었다.
    """
    dialects = set()
    # `index`는 목록에 없다 — tree-sitter 인덱서로 바뀌어 clang을 쓰지 않는다. 남겨 두면
    # "index도 이 방언을 쓴다"로 읽히는데 사실이 아니고, 파서는 방언 플래그를 받지 않는다.
    for tool in ("build", "clang-tidy", "asan"):
        for command in plan(
            tool, target="/src/parser.c", compile_sources=["/src/parser.c"], cwd="/src"
        ):
            dialects |= {value for value in command.argv if value.startswith("-std=")}

    assert dialects == {"-std=gnu17"}


def test_every_whitelisted_tool_has_a_command() -> None:
    """화이트리스트와 조립 규칙이 갈라지면 그 도구는 실행 시점에야 죽는다."""
    for tool in TOOL_KINDS:
        commands = plan(tool, target="/src/parser.c", compile_sources=["/src/parser.c"], cwd="/src")
        assert commands, tool


# --- ok / exit_code 의미 --------------------------------------------------------


def _semgrep_json(count: int) -> str:
    return json.dumps({"results": [{"check_id": f"r{i}"} for i in range(count)], "errors": []})


def test_static_signal_absence_is_not_a_failure() -> None:
    """돌았는데 없음(0)과 돌아서 찾음(1) 둘 다 ok=True다."""
    assert judge("semgrep", [Step(0, _semgrep_json(0), "")], 1) == (True, 0, None)
    assert judge("semgrep", [Step(1, _semgrep_json(3), "")], 1) == (True, 1, None)


def test_semgrep_crash_with_exit_one_is_not_a_finding() -> None:
    """2026-08-14 실측 회귀: `--read-only`에서 semgrep이 예외로 죽고 exit 1을 냈다.

    exit code만 보면 "발견함"과 구별되지 않는다. 결과 문서가 없다는 사실로 가른다 —
    이걸 놓치면 **한 번도 돌지 않은 도구가 정적 신호를 만든다.**
    """
    crash = "Traceback (most recent call last):\nOSError: [Errno 30] Read-only file system"

    ok, code, error = judge("semgrep", [Step(1, "", crash)], 1)

    assert ok is False
    assert code == 1, "프로세스가 반환한 값을 그대로 남긴다 (합성하지 않는다)"
    assert "Traceback" in error


def test_semgrep_without_a_result_document_is_a_failure() -> None:
    """Traceback이 없어도 결과 문서가 없으면 돌지 않은 것이다."""
    ok, _, error = judge("semgrep", [Step(1, "not json at all", "")], 1)

    assert ok is False
    assert error


def test_semgrep_tool_error_code_is_still_a_failure() -> None:
    """문서가 있어도 semgrep 자신의 오류 코드(2·7 등)는 실패다."""
    ok, code, error = judge("semgrep", [Step(2, _semgrep_json(0), "")], 1)

    assert ok is False
    assert code == 2
    assert error


def test_semgrep_summary_line_restores_readability() -> None:
    """`--json`으로 잃은 가독성을 한 줄로 되돌린다."""
    assert summary_line("semgrep", [Step(1, _semgrep_json(2), "")]) == "# semgrep: 2 findings"
    assert "결과 문서 없음" in summary_line("semgrep", [Step(1, "", "boom")])
    assert summary_line("clang-tidy", [Step(0, "", "")]) is None


def test_clang_tidy_compile_failure_is_not_counted_as_a_finding() -> None:
    """exit code로는 구분되지 않는다. stdout의 표지로 가른다 (D11).

    진단은 stdout으로 나온다 — stderr에는 "N warnings generated."만 있다(실측).
    """
    found = judge("clang-tidy", [Step(1, "warning: x [bugprone-x]", "1 warning")], 1)
    broken = judge("clang-tidy", [Step(1, "error: 'x.h' [clang-diagnostic-error]", "")], 1)

    assert found[0] is True
    assert broken[0] is False


def test_clang_tidy_promoted_warning_is_a_finding_not_a_broken_build() -> None:
    """`--warnings-as-errors`로 승격된 `clang-diagnostic-*` 경고는 정당한 발견이다.

    2026-08-14 실측: util.c의 `clang-diagnostic-implicit-function-declaration`은 파일이
    실제로 컴파일된 상태의 진단이다. 컴파일 불가만 `clang-diagnostic-error`로 태그된다.
    """
    stdout = (
        "error: implicit declaration of function 'strnlen' is invalid in C99 "
        "[clang-diagnostic-implicit-function-declaration,-warnings-as-errors]"
    )

    assert judge("clang-tidy", [Step(1, stdout, "")], 1) == (True, 1, None)


def test_asan_build_failure_never_reaches_e4() -> None:
    """**컴파일 성공을 실행으로 세지 않는다.**

    E4(proven)의 조건이 SANITIZER/CRASH/POC_RUN Evidence의 `ok`다. 빌드 단계에서 끊긴
    실행을 ok=True로 남기면 한 번도 돌지 않은 프로그램이 재현 완료로 기록된다.
    """
    ok, _, error = judge("asan", [Step(1, "", "undefined reference to `main'")], 2)

    assert ok is False
    assert "실행되지 않았다" in error


def test_asan_crash_is_a_signal_not_a_tool_failure() -> None:
    """바이너리가 실제로 떠서 죽은 것은 도구 실패가 아니라 우리가 찾던 신호다."""
    steps = [Step(0, "", ""), Step(1, "", "AddressSanitizer: stack-buffer-overflow")]

    assert judge("asan", steps, 2) == (True, 1, None)


def test_asan_exec_failure_is_not_a_crash() -> None:
    """컨테이너가 바이너리를 띄우지도 못한 경우(126/127)를 크래시로 세지 않는다."""
    ok, _, error = judge("asan", [Step(0, "", ""), Step(127, "", "")], 2)

    assert ok is False
    assert "띄우지 못했다" in error


# --- 로그와 digest --------------------------------------------------------------


def test_log_records_the_command_that_actually_ran(tmp_path: Path, spy: _Spy) -> None:
    """trace의 argv는 노드가 만든 자리표시자다. 실제 명령은 로그에만 남는다.

    `Evidence.digest`가 로그 내용의 sha256이므로, 실제 명령이 digest에 포함되고
    "무엇이 실행됐는지"가 사후 검증 가능해진다(engineering-guide.md §6).
    """
    evidence = _runner(tmp_path).run(
        "semgrep", ToolArgs(argv=["semgrep", "--quiet", "."], workdir="."), SANDBOX
    )

    log = tmp_path / RUN_ID / "semgrep" / f"{evidence.evidence_id}.log"
    body = log.read_text(encoding="utf-8")

    assert evidence.payload_ref.endswith(f"semgrep/{evidence.evidence_id}.log")
    assert "# node argv: semgrep --quiet ." in body
    assert "$ docker run --rm" in body
    assert "--error" in body, "실제 실행 명령이 로그에 없다"
    assert "(exit=0)" in body
    assert evidence.digest == f"sha256:{hashlib.sha256(body.encode('utf-8')).hexdigest()}"


def test_rejected_runs_also_leave_a_log(tmp_path: Path, spy: _Spy) -> None:
    """`payload_ref`가 가리키는 파일이 없으면 그 Evidence는 되짚을 수 없다."""
    evidence = _runner(tmp_path).run("rm", ToolArgs(argv=["rm"], workdir="."), SANDBOX)

    log = tmp_path / RUN_ID / "rejected" / f"{evidence.evidence_id}.log"

    assert "실행하지 않았다" in log.read_text(encoding="utf-8")
    assert evidence.payload_ref.endswith(f"rejected/{evidence.evidence_id}.log")


def test_unvalidated_tool_name_cannot_write_outside_the_run_directory(
    tmp_path: Path, spy: _Spy
) -> None:
    """도구 이름은 검증되지 않은 문자열이다. 그대로 경로에 이으면 run 밖에 파일이 생긴다."""
    runner = _runner(tmp_path / "runs")
    evidence = runner.run("../../escaped", ToolArgs(argv=["x"], workdir="."), SANDBOX)

    assert evidence.ok is False
    assert not (tmp_path / "escaped").exists()
    assert (tmp_path / "runs" / RUN_ID / "rejected" / f"{evidence.evidence_id}.log").exists()


def test_observed_at_is_injected(tmp_path: Path, spy: _Spy) -> None:
    """재현 실행에서 같은 값이 나와야 trace를 대조할 수 있다."""
    evidence = _runner(tmp_path).run("build", ToolArgs(argv=["build"], workdir="."), SANDBOX)

    assert evidence.observed_at == OBSERVED_AT


def test_file_workdir_runs_in_its_parent_directory(tmp_path: Path, spy: _Spy) -> None:
    """노드는 artifact 경로를 그대로 준다 — `workdir`가 파일일 수 있다.

    그대로 `-w`에 넣으면 docker가 "not a directory"로 죽고, 그 실패가 정적 신호 부재로
    집계된다.
    """
    _runner(tmp_path).run(
        "semgrep", ToolArgs(argv=["semgrep", "parser.c"], workdir="parser.c"), SANDBOX
    )

    argv = spy.calls[0]

    assert argv[argv.index("-w") + 1] == "/src"
    assert "/src/parser.c" in argv


# --- 확장자가 도구마다 갈라진다 -------------------------------------------------


@pytest.fixture
def include_target(tmp_path: Path) -> Path:
    """실측 샘플의 구조. `target.c`는 `#include` 한 줄이고 코드는 전부 `.inc`에 있다.

    `tests/fixtures/target`을 쓰지 않는 이유가 이 PR의 요지다 — 픽스처가 실전 구조를
    대표하지 못해 생긴 결함이 이 저장소에 이미 두 건 있다(clang의 include 경로,
    tree-sitter 최상위 한 겹 순회). 그래서 테스트가 그 구조를 직접 재현한다.
    """
    root = tmp_path / "sample"
    root.mkdir()
    (root / "target.c").write_text('#include "session_core.inc"\n', encoding="utf-8")
    (root / "session_core.inc").write_text("void session_read(void) {}\n", encoding="utf-8")
    return root


def test_the_two_suffix_lists_stay_split() -> None:
    """한 목록으로 합치고 싶어지는 자리다. 합치면 build가 깨진다.

    `.inc`는 함수 본문 조각이라 단독 컴파일이 되지 않는다 — clang에 직접 주면 컴파일이
    실패해 `ok=False`가 되고, **일어나지 않은 실패가 신호로 기록된다.**
    """
    assert ".inc" not in container_tools._COMPILE_SUFFIXES
    assert ".inc" in container_tools._INDEX_SUFFIXES


@pytest.mark.parametrize("tool", ["build", "clang-tidy"])
def test_clang_tools_never_receive_an_include_fragment(
    tool: str, tmp_path: Path, include_target: Path, spy: _Spy
) -> None:
    """`target.c`만 주면 **전처리가** `.inc`를 끌어온다 — 직접 줄 이유가 없다.

    직접 주면 단독 컴파일이 되지 않아 실패하고, 그 실패가 도구 신호로 집계된다.
    """
    _runner(tmp_path, root=include_target).run(tool, ToolArgs(argv=[tool], workdir="."), SANDBOX)

    argv = spy.calls[0]

    assert "/src/target.c" in argv
    assert "/src/session_core.inc" not in argv


def test_indexer_does_receive_the_include_fragment(
    tmp_path: Path, include_target: Path, spy: _Spy
) -> None:
    """tree-sitter는 전처리를 하지 않는다 — 직접 주지 않으면 28바이트짜리 `#include`
    한 줄만 보고 그 뒤의 17KB를 못 본다. 그리고 그것은 에러가 아니다."""
    _runner(tmp_path, root=include_target).run(
        "index", ToolArgs(argv=["index"], workdir="."), SANDBOX
    )

    argv = spy.calls[0]

    assert "/src/target.c" in argv
    assert "/src/session_core.inc" in argv


# --- 배선 ----------------------------------------------------------------------


def test_default_image_matches_the_orchestrator_default() -> None:
    """어댑터는 오케스트레이터를 import 하지 않으므로 이미지 이름이 두 곳에 있다.

    갈라지면 `RunRecord.tool_images`가 **실행되지 않은 이미지의 digest**를 기록한다.
    """
    assert DEFAULT_IMAGE == DEFAULT_SANDBOX.image


def test_unknown_runtime_is_rejected(tmp_path: Path, spy: _Spy) -> None:
    with pytest.raises(ValueError, match="모르는 런타임"):
        _runner(tmp_path, runtime="lxc")


def test_missing_runtime_fails_at_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """샌드박스를 못 띄우는 경우가 유일한 예외 경로다 (계약 1)."""
    monkeypatch.setattr(container_tools.shutil, "which", lambda name: None)

    with pytest.raises(SandboxUnavailable):
        _runner(tmp_path)


def test_images_are_empty_when_the_digest_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """못 구한 값을 지어내지 않는다. 빈 dict가 곧 "비교표에서 제외" 경고다."""
    monkeypatch.setattr(container_tools.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(
        container_tools.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "<no value>|", ""),
    )

    assert _runner(tmp_path).images() == {}


def test_images_prefer_repo_digest_then_image_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """로컬 빌드 이미지는 RepoDigests가 비어 있다 — 그때는 .Id가 유일한 식별자다."""
    monkeypatch.setattr(container_tools.shutil, "which", lambda name: "/usr/bin/docker")
    image_id = f"sha256:{'a' * 64}"
    monkeypatch.setattr(
        container_tools.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, f"|{image_id}", ""),
    )

    assert _runner(tmp_path).images() == {DEFAULT_IMAGE: image_id}


# --- 실제 컨테이너 -------------------------------------------------------------


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker가 없다")
def test_real_container_splits_vulnerable_from_benign(tmp_path: Path) -> None:
    """픽스처의 핵심: **하네스 룰셋**이 parser.c만 잡는다.

    처음에는 "util.c는 semgrep exit_code가 0"으로 단언했다. 2026-08-14 실측에서 공개
    룰셋의 `c.lang.security.insecure-use-string-copy-fn`이 util.c의 `strncpy`를 잡아
    깨졌다 — `_s` 계열을 쓰지 않는 모든 복사를 잡는 룰이라 경계를 지킨 코드도 걸린다.

    **룰을 끄거나 픽스처를 고치지 않는다.** 정적분석의 오탐은 정상이고, 도구를 픽스처에
    맞추면 대조군의 의미가 사라진다. 대신 우리가 실제로 지켜야 할 불변을 단언한다:
    우리가 소유한 최소 룰셋이 두 표본을 가른다. 공개 룰셋의 소음은 여기서 막을 대상이
    아니라 뒤 단계(Auditor·Critic)가 걸러야 할 입력이고, Fake 모델이 그것을 못 한다는
    사실은 STATUS.md와 D2에 기록돼 있다.

    이미지가 없으면 건너뛴다 — 준비 절차는 tools/README.md.
    """
    # 시간 제한 없이 부르지 않는다. 데몬이 **기동 중**이면 docker CLI가 파이프에서 그냥
    # 멈추고, 테스트가 실패도 성공도 아닌 채로 CI를 잡아먹는다(실제로 겪었다).
    try:
        probe = subprocess.run(
            ["docker", "image", "inspect", DEFAULT_IMAGE],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("docker 데몬이 응답하지 않는다")
    if probe.returncode:
        pytest.skip(f"{DEFAULT_IMAGE} 이미지가 없다")

    runner = _runner(tmp_path)
    vulnerable = runner.run(
        "semgrep", ToolArgs(argv=["semgrep", "parser.c"], workdir="parser.c"), SANDBOX
    )
    benign = runner.run("semgrep", ToolArgs(argv=["semgrep", "util.c"], workdir="util.c"), SANDBOX)

    # ok=True가 먼저다. semgrep이 `--read-only`에서 죽던 결함(2026-08-14)이 되살아나면
    # 여기서 걸린다 — 그때는 크래시가 exit 1이라 "발견함"과 구별되지 않았다.
    assert vulnerable.ok and benign.ok
    assert vulnerable.exit_code != 0, "취약 표본에서 정적 신호가 나오지 않았다"

    ours = {
        path.name: [
            rule
            for rule in _rules_in(tmp_path, evidence)
            if "harness" in rule
        ]
        for path, evidence in ((Path("parser.c"), vulnerable), (Path("util.c"), benign))
    }

    assert ours["parser.c"], "하네스 룰셋이 취약 표본을 잡지 못했다"
    assert not ours["util.c"], f"하네스 룰셋이 benign 표본을 잡았다: {ours['util.c']}"


def _rules_in(artifacts_root: Path, evidence: Evidence) -> list[str]:
    """Evidence 로그에서 semgrep이 실제로 발화한 룰 id를 꺼낸다."""
    log = artifacts_root / RUN_ID / evidence.source / f"{evidence.evidence_id}.log"
    stdout = log.read_text(encoding="utf-8").split("--- stdout ---")[1].split("--- stderr ---")[0]
    return [item["check_id"] for item in semgrep_results(stdout.strip()) or []]


# --- 인덱서 산출물 경로 (D13) ---------------------------------------------------


def test_index_output_path_points_where_the_runner_actually_writes(tmp_path: Path) -> None:
    """읽는 쪽(`adapters/index_graph.py`)과 쓰는 쪽(`_execute`)이 같은 경로여야 한다.

    갈리면 예외가 아니라 **파일을 못 찾는 상태**가 되고, 로더는 그것을 `missing`으로
    읽는다 — 즉 "인덱서가 안 돌았다"로 기록되어 조용히 그래프 섹션이 사라진다.
    """
    expected = scratch_dir(tmp_path, RUN_ID, INDEX_TOOL) / INDEX_FILE

    assert index_output_path(tmp_path, RUN_ID) == expected
    assert index_output_path(tmp_path, RUN_ID).name == INDEX_FILE


def test_scratch_dir_name_matches_the_node_tool_name() -> None:
    """scratch 하위 디렉터리 이름이 곧 도구 이름이다(`_execute`가 `tool`로 만든다).

    어댑터는 오케스트레이터를 import 하지 않으므로 값이 양쪽에 복제돼 있다 — 여기서 대조한다.
    """
    from src.orchestrator.nodes.index import INDEX_TOOL as NODE_INDEX_TOOL

    assert INDEX_TOOL == NODE_INDEX_TOOL
