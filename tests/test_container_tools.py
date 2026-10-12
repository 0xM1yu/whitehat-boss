"""ContainerToolRunner — 컨테이너를 띄우지 않고 계약을 검사한다.

명령 조립이 순수 함수(`plan`·`container_argv`·`judge`)로 분리돼 있어 Docker가 없는 CI에서도
돈다. 실제 실행 검사는 파일 끝에 `shutil.which("docker")` 조건부로 따로 둔다.

여기서 지키는 것은 대부분 **조용히 깨지는** 계약이다: 거절 경로가 프로세스를 띄우지 않는지,
`ok`와 `exit_code`의 의미가 섞이지 않는지, 컴파일 성공이 실행으로 둔갑하지 않는지.
"""

import errno
import hashlib
import importlib.util
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
    PocTarget,
    Step,
    container_argv,
    first_hit_location,
    index_output_path,
    judge,
    plan,
    scratch_dir,
    semgrep_results,
    sif_path,
    summary_line,
)
from src.adapters.fake_tools import TOOL_KINDS, FakeToolRunner, SandboxUnavailable
from src.ids import IdFactory
from src.interfaces import tool_runner
from src.interfaces.tool_runner import SandboxSpec, ToolArgs
from src.orchestrator import prompt_loader
from src.orchestrator.nodes.sandbox import DEFAULT_SANDBOX
from src.orchestrator.prompt_loader import evidence_summaries
from src.schemas.enums import EvidenceKind
from src.schemas.evidence import Evidence
from tests.conftest import plant_link

RUN_ID = "run_20260901_0001"
LF = chr(10)
CRLF = (chr(13) + chr(10)).encode()

TARGET = Path(__file__).resolve().parent / "fixtures" / "target"
GDB_TARGET = Path(__file__).resolve().parent / "fixtures" / "gdb"
OBSERVED_AT = datetime(2026, 9, 1, tzinfo=UTC)

# ⏵ 2026-10-09 (ADR 0014) — `poc`는 과제 `-vul` 이미지를 돌린다. 과제 이미지가 없으면 **거절**하므로
# (폴백을 지어내지 않는다) 입력 계약을 재는 검사들이 이 값을 물려 준다. `arvo:1065`는 실서빙 run이
# 돈 과제다. 그 도구의 명령·종료 코드 분류는 `tests/test_poc_tool_feeds_the_input.py`가 든다.
ARVO = PocTarget(image="n132/arvo:1065-vul", argv=("/bin/arvo",))

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


def _container_name(argv: list[str]) -> str:
    return argv[argv.index("--name") + 1]


def test_runs_under_different_artifact_roots_get_different_container_names(
    tmp_path: Path, spy: _Spy
) -> None:
    """루트가 다른 두 run은 같은 날 같은 `run_id`(`run_YYYYMMDD_0001`)를 갖는다 — 같은 장비에서
    동시에 돌면 이름이 겹쳐 두 run 모두 도구 호출 12건씩이 exit 125로 빠졌다(#303, 2026-09-26
    ATOM 실측).
    같은 루트에서는 이름이 그대로라 순차 run을 `run_id`가 가르는 것은 변하지 않는다."""
    args = ToolArgs(argv=["semgrep", "."], workdir=".")
    for root in ("h-main", "h-pr", "h-main"):
        _runner(tmp_path / root).run("semgrep", args, SANDBOX)

    first, second, again = (_container_name(argv) for argv in spy.calls)
    assert first != second, "루트가 다른 같은 run_id가 같은 컨테이너 이름을 만든다"
    assert first == again, "같은 루트·같은 run_id인데 이름이 흔들린다"
    assert first.startswith(f"harness-{RUN_ID}-") and first.endswith("-0")


def test_a_relative_artifact_root_is_named_by_where_it_resolves(
    tmp_path: Path, spy: _Spy, monkeypatch: pytest.MonkeyPatch
) -> None:
    """해시는 **resolve한** 루트에서 나온다 — 같은 상대 `--artifacts-root`(`arts`)를 다른 cwd에서
    주면 다른 디렉터리이므로 이름이 달라야 하고, 같은 디렉터리를 상대·절대로 주면 같아야 한다.
    `.resolve()`를 빼는 변이를 이 검사가 잡는다(#541 리뷰 — 위 검사는 루트가 다 절대경로라
    못 잡았다)."""
    args = ToolArgs(argv=["semgrep", "."], workdir=".")
    for cwd in ("a", "b"):
        (tmp_path / cwd).mkdir()
        monkeypatch.chdir(tmp_path / cwd)
        _runner(Path("arts")).run("semgrep", args, SANDBOX)
    _runner(tmp_path / "b" / "arts").run("semgrep", args, SANDBOX)

    in_a, in_b, absolute_b = (_container_name(argv) for argv in spy.calls)
    assert in_a != in_b, "다른 cwd의 같은 상대 루트가 같은 이름을 만든다"
    assert in_b == absolute_b, "같은 디렉터리를 상대·절대로 주면 이름이 같아야 한다"


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

    **이 docstring이 들고 있던 근거는 뒤집혔다.** 그전에는 "ATOM은 Docker 데몬 접근도
    singularity도 없어 이 경로가 유일한 실서빙 런타임이다(2026-08-17)"라고 적고, 그래서
    실서빙 프로필(`local_balanced`·`b1_single`)이 `podman`을 직접 선언한다고 이었다.
    그 전제가 2026-08-21에 반증됐고(ATOM에 docker 29.2.1과 podman 4.9.3이 함께 있다)
    두 프로필의 선언값도 2026-08-28에 `docker`로 되돌아갔다 —
    docs/decisions/0005-tool-runner-override.md 마지막 절.

    **단언 자체는 그대로 필요하다.** podman은 여전히 지원 런타임이고
    (`AdapterSelection.tool_runner`의 Literal에 있다), 이 저장소에는 podman으로 도구를
    돌린 run이 하나 있다(`run_20260817_0001`, docs/portability.md §6). 격리 플래그가
    갈리면 그 run과 docker run을 같은 비교 셀에 넣을 수 없다 — 이 테스트가 지키는 것은
    그 등가성이지 어느 프로필이 무엇을 선언하는가가 아니다.
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
    assert "harness-tools_v3.sif" in evidence.error
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


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("/src/parser.c", "-std=gnu17"),
        ("/src/parser.cc", "-std=gnu++17"),
        ("/src/parser.cpp", "-std=gnu++17"),
    ],
)
def test_every_clang_tool_uses_the_same_dialect(source: str, expected: str) -> None:
    """같은 파일이 한 도구에서 통과하고 다른 도구에서 오류가 나면 안 된다.

    2026-08-14 실측: clang-tidy만 `-std=c11`이라 util.c의 `strnlen`이 거기서만 미선언
    오류였다. 그 차이가 코드의 성질인지 우리 플래그의 산물인지 구분할 수 없었다.

    **2026-08-25 — 한정이 언어 안으로 좁아졌다.** 그전에는 세 도구가 입력과 무관하게
    `-std=gnu17` 하나를 썼는데, `_COMPILE_SUFFIXES`에 `.cc`·`.cpp`가 있어 **C++ 파일도
    그 값을 받고 clang이 거부했다**(`run_20260825_0001`에서 5건). 지키려던 불변은
    "도구마다 방언이 다르면 안 된다"이지 "언어가 달라도 같은 방언"이 아니다 — 그래서
    **언어를 고정하고 도구를 가로지르며** 같은지 본다.
    """
    dialects = set()
    # `index`는 목록에 없다 — tree-sitter 인덱서로 바뀌어 clang을 쓰지 않는다. 남겨 두면
    # "index도 이 방언을 쓴다"로 읽히는데 사실이 아니고, 파서는 방언 플래그를 받지 않는다.
    for tool in ("build", "clang-tidy", "asan", "gdb"):
        for command in plan(tool, target=source, compile_sources=[source], cwd="/src"):
            dialects |= {value for value in command.argv if value.startswith("-std=")}

    assert dialects == {expected}


def test_cxx_suffixes_are_a_subset_of_the_compile_list() -> None:
    """C++ 판정에만 있고 컴파일 대상에 없는 확장자는 한 번도 쓰이지 않는다.

    반대 방향이 더 위험하다 — 컴파일 대상에 있는데 이 판정에서 빠지면 그 파일이 C 방언을
    받아 clang이 거부한다. 그것이 2026-08-25의 5건이다.
    """
    assert set(container_tools._CXX_SUFFIXES) <= set(container_tools._COMPILE_SUFFIXES)


def test_every_clang_tool_gets_the_same_include_paths() -> None:
    """`-I`가 갈리면 같은 파일에 대한 두 진단이 어긋난다 — 방언과 같은 이유다.

    2026-08-25 실측: build 실패 27건 중 20건이 `'zlib.h' file not found` 계열이었다.
    파일을 고립시켜 컴파일해 `#include`가 아무 데서도 안 보였다.
    """
    per_tool = {}
    for tool in ("build", "clang-tidy", "asan", "gdb"):
        # asan·gdb의 둘째 명령은 바이너리 실행이라 컴파일 인자가 없다 — 첫 명령만 본다.
        argv = plan(
            tool,
            target="/src/contrib/blast/blast.c",
            compile_sources=["/src/contrib/blast/blast.c"],
            cwd="/src/contrib/blast",
        )[0].argv
        per_tool[tool] = [argv[i + 1] for i, value in enumerate(argv) if value == "-I"]

    # 대상 루트와 artifact 디렉터리 둘. "헤더가 있는 모든 디렉터리"가 아니다.
    assert per_tool["build"] == ["/src/contrib/blast", "/src"]
    assert len({tuple(paths) for paths in per_tool.values()}) == 1, per_tool


def test_include_paths_are_not_duplicated_at_the_target_root() -> None:
    """artifact가 대상 루트 자체면 두 경로가 같다. 무의미한 중복 인자를 남기지 않는다 —
    로그가 곧 `Evidence.digest`라 기록에 그대로 박힌다."""
    argv = plan("build", target="/src", compile_sources=["/src/a.c"], cwd="/src")[0].argv

    assert [value for value in argv if value == "-I"] == ["-I"]


def test_clang_tidy_puts_compile_args_after_the_double_dash() -> None:
    """`--` 앞은 clang-tidy 인자, 뒤가 컴파일 인자다. 앞에 두면 clang-tidy가 거부한다."""
    argv = plan("clang-tidy", target="/src/a.c", compile_sources=["/src/a.c"], cwd="/src")[
        0
    ].argv
    tail = argv[argv.index("--") :]

    assert "-std=gnu17" in tail
    assert "-I" in tail
    assert "/src/a.c" not in tail, "소스는 `--` 앞에 온다"


@pytest.mark.parametrize("tool", ["build", "clang-tidy"])
def test_mixed_languages_split_into_one_command_each(tool: str) -> None:
    """한 clang 호출로 두 방언을 표현할 수 없다 — `-std=`는 마지막 값이 이긴다.

    갈라진 명령이 서로의 소스를 물고 가면 결국 같은 실패가 난다.
    """
    commands = plan(
        tool, target="/src", compile_sources=["/src/a.c", "/src/b.cpp"], cwd="/src"
    )

    assert len(commands) == 2
    c_argv, cxx_argv = commands[0].argv, commands[1].argv

    assert "-std=gnu17" in c_argv and "-std=gnu++17" not in c_argv
    assert "-std=gnu++17" in cxx_argv and "-std=gnu17" not in cxx_argv
    assert "/src/a.c" in c_argv and "/src/b.cpp" not in c_argv
    assert "/src/b.cpp" in cxx_argv and "/src/a.c" not in cxx_argv


def test_asan_refuses_mixed_languages_instead_of_linking_half_a_program() -> None:
    """**asan만 언어를 못 가른다** — 컴파일과 링크가 한 명령이라 `-o`가 서로를 덮는다.

    나눠서 두 번 쓰면 절반이 빠진 프로그램이 뜨고, 그것이 크래시하면 E4가 성립한다.
    지어내지 않고 거절한다(open-questions D34).
    """
    with pytest.raises(ValueError, match="링크 규칙이 없다"):
        plan("asan", target="/src", compile_sources=["/src/a.c", "/src/b.cpp"], cwd="/src")


def test_gdb_refuses_mixed_languages_instead_of_linking_half_a_program() -> None:
    """gdb도 실행 파일 하나를 링크하므로 언어별 두 명령으로 바이너리를 덮어쓰지 않는다."""
    with pytest.raises(ValueError, match="링크 규칙이 없다"):
        plan("gdb", target="/src", compile_sources=["/src/a.c", "/src/b.cpp"], cwd="/src")


def test_gdb_plan_compiles_then_runs_a_noninteractive_debugger() -> None:
    """기본 경로는 pwndbg가 아니라 재현 가능한 gdb 배치 스크립트를 실행한다."""
    commands = plan("gdb", target="/src/a.c", compile_sources=["/src/a.c"], cwd="/src")

    assert len(commands) == 2
    compile_argv, debug_argv = commands[0].argv, commands[1].argv
    assert compile_argv[:4] == ["clang", "-g", "-O0", "-fno-omit-frame-pointer"]
    assert compile_argv[-3:] == ["-o", "/work/gdb.bin", "/src/a.c"]
    assert debug_argv == [
        "gdb",
        "--nx",
        "-batch",
        "--return-child-result",
        "-x",
        "/opt/gdb.batch",
        "--",
        "/work/gdb.bin",
    ]


def test_a_header_only_artifact_is_still_rejected_before_any_process() -> None:
    """**"해당 없음"의 표현을 바꾸지 않았다** (open-questions D31).

    `.h`는 `_COMPILE_SUFFIXES`에 없어 `compile_sources`가 비고, `plan()`이 프로세스를
    띄우기 전에 거절한다. D16이 같은 상태(`.inc`의 clang-tidy)를 "거짓 신호가 아니다"로
    이미 판정했다 — 컴파일러에 넘긴 것이 아니라 **넘길 것이 없는 상태**다.

    이 테스트는 그 판정을 잠근다. 나중 리팩터가 조용히 헤더를 컴파일하기 시작하면
    **일어나지 않은 실패가 신호로 기록된다.**
    """
    with pytest.raises(ValueError, match="컴파일할 C 소스가 없다"):
        plan("build", target="/src/blast.h", compile_sources=[], cwd="/src")


def test_every_whitelisted_tool_has_a_command() -> None:
    """화이트리스트와 조립 규칙이 갈라지면 그 도구는 실행 시점에야 죽는다."""
    for tool in TOOL_KINDS:
        commands = plan(
            tool,
            target="/src/parser.c",
            compile_sources=["/src/parser.c"],
            cwd="/src",
            poc_target=ARVO,
        )
        assert commands, tool


# --- ok / exit_code 의미 --------------------------------------------------------


def _semgrep_json(count: int) -> str:
    return json.dumps({"results": [{"check_id": f"r{i}"} for i in range(count)], "errors": []})


def test_static_signal_absence_is_not_a_failure() -> None:
    """돌았는데 없음(0)과 돌아서 찾음(1) 둘 다 ok=True다."""
    assert judge("semgrep", [Step(0, _semgrep_json(0), "")], 1) == (True, 0, None)
    assert judge("semgrep", [Step(1, _semgrep_json(3), "")], 1) == (True, 1, None)


def _semgrep_hit_json(path: str, start: int, end: int) -> str:
    return json.dumps(
        {
            "results": [
                {"check_id": "r0", "path": path, "start": {"line": start}, "end": {"line": end}}
            ],
            "errors": [],
        }
    )


def test_first_hit_location_reads_semgrep_first_result_and_strips_the_mount() -> None:
    """D23: semgrep 첫 결과의 path·start.line·end.line을 Location으로 옮긴다.

    semgrep은 컨테이너 마운트 경로(`/src/parser.c`)를 스캔하므로 `path`가 그대로 나온다.
    `Candidate.location.file`(저장소 상대)과 맞추려면 `/src/` 접두를 벗겨야 `overlaps`가
    참이 된다 — 안 벗기면 `location_ok`가 계속 거짓이다.
    """
    loc = first_hit_location(
        "semgrep", [Step(1, _semgrep_hit_json("/src/parser.c", 5, 6), "")]
    )

    assert loc is not None
    assert (loc.file, loc.line_start, loc.line_end) == ("parser.c", 5, 6)


def test_first_hit_location_is_none_without_a_hit() -> None:
    """신호가 없거나(0건) semgrep이 아니면 위치도 없다 — 지어내지 않는다."""
    assert first_hit_location("semgrep", [Step(0, _semgrep_json(0), "")]) is None
    assert first_hit_location("clang-tidy", [Step(1, "warning: x [b]", "")]) is None
    assert first_hit_location("semgrep", []) is None


def test_first_hit_location_rejects_malformed_shapes() -> None:
    """형태가 어긋나면 틀린 위치를 넣느니 None이다 (파싱 실패를 신호로 읽지 않는다)."""
    assert first_hit_location("semgrep", [Step(1, "not json", "")]) is None
    no_line = json.dumps({"results": [{"path": "a.c", "start": {}}], "errors": []})
    assert first_hit_location("semgrep", [Step(1, no_line, "")]) is None
    no_path = json.dumps({"results": [{"start": {"line": 3}}], "errors": []})
    assert first_hit_location("semgrep", [Step(1, no_path, "")]) is None


def test_first_hit_location_drops_an_invalid_end() -> None:
    """end.line이 없거나 start보다 작으면 **채우지 않는다**(None) — 값을 지어내지 않는다.

    `Location.overlaps`가 `line_end or line_start`로 처리하므로 None이어도 판정은 같고,
    docstring의 "형태가 어긋나면 채우지 않는다"와도 맞는다.
    """
    loc = first_hit_location("semgrep", [Step(1, _semgrep_hit_json("a.c", 8, 2), "")])

    assert loc is not None
    assert (loc.line_start, loc.line_end) == (8, None)


def test_container_semgrep_run_wires_the_hit_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """컨테이너 배선: semgrep run이 첫 히트 위치를 Evidence.location에 싣는다.

    `_execute`의 `location=first_hit_location(...)`를 `location=None`으로 되돌리면 red다 —
    파서 단위 테스트만으로는 그 회귀가 안 잡힌다.
    """
    monkeypatch.setattr(container_tools.shutil, "which", lambda name: f"/usr/bin/{name}")
    hit = _semgrep_hit_json("/src/parser.c", 5, 6)
    monkeypatch.setattr(
        container_tools.subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, hit, ""),
    )

    evidence = _runner(tmp_path).run(
        "semgrep", ToolArgs(argv=["semgrep", "parser.c"], workdir="parser.c"), SANDBOX
    )

    assert evidence.location is not None
    assert (evidence.location.file, evidence.location.line_start) == ("parser.c", 5)


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
    """`--json`으로 잃은 가독성을 머리 줄로 되돌린다(반쪽 파싱이면 둘째 줄이 붙는다 — 아래 검사)."""
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

    E4(proven)의 조건이 SANITIZER/CRASH/POC_RUN Evidence의 `ok`(와 비-0 종료 코드 — #232)다.
    빌드 단계에서 끊긴 실행을 ok=True로 남기면 한 번도 돌지 않은 프로그램이 재현 완료로 기록된다.
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


def test_asan_that_exits_clean_is_ok_true_because_ok_only_means_it_ran() -> None:
    """**`ok=True`는 「크래시했다」가 아니라 「떴다」를 뜻한다** (이슈 #232).

    `_EXEC_FAILED`에 `0`이 없으므로 크래시 없이 끝난 실행도 `ok=True`다. 모듈 docstring이
    한때 반대(*"크래시한 경우만 `ok=True`"*)를 적었는데 **무는 검사가 없었다** — 빌드 끊김·
    크래시·126/127 셋은 있었고 exit 0 갈래만 비어 있었다. 이 줄이 그것이다.

    ⚠ **이 검사가 무는 것은 `judge()`의 반환 튜플 하나다.** 그 `ok=True`가 E4가 되는지는
    `gate.evaluate()`의 `p4`가 정하는데 **그쪽은 여기서 안 돈다** — exit 0이 E4가 안 되는 것은
    `tests/test_gate.py`가 문다(`test_a_clean_exit_does_not_lift_e3_to_e4`). `judge()`가 내는
    튜플을 그대로 게이트에 넣는 연결은 같은 파일의
    `test_what_judge_returns_for_asan_decides_e4_through_the_gate`다.

    ⚠ **철자를 물지 성질을 물지 않는다.** 이 `return`을 뒤집으면 죽지만, 같은 판단(D80의
    선택지 (b))을 `ContainerToolRunner.run()`이나 `nodes/validate.py`에 넣으면 초록이다.
    `poc`는 특히 무방비다 — `judge("poc", ...)`를 무는 검사가 0건이다.
    """
    steps = [Step(0, "", ""), Step(0, "", "")]

    assert judge("asan", steps, 2) == (True, 0, None)


def test_gdb_build_failure_never_counts_as_a_debug_session() -> None:
    ok, code, error = judge("gdb", [Step(1, "", "undefined reference to `main'")], 2)

    assert (ok, code) == (False, 1)
    assert error is not None and "실행되지 않았다" in error


def test_gdb_exec_failure_is_not_a_target_crash() -> None:
    ok, code, error = judge("gdb", [Step(0, "", ""), Step(127, "", "")], 2)

    assert (ok, code) == (False, 127)
    assert error is not None and "띄우지 못했다" in error


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


def test_the_log_digest_matches_the_bytes_on_disk(tmp_path: Path, spy: _Spy) -> None:
    """digest는 **그 파일**을 검증하는 값이다 — 그래서 바이트로 대조한다 (이슈 #387).

    ⚠ 위 검사는 `read_text`로 읽어 비교하는데 **그 호출이 CRLF를 LF로 되돌려 읽는다.**
    그래서 디스크가 CRLF여도 통과한다 — 이 결함이 그 틈으로 지나갔다.
    """
    evidence = _runner(tmp_path).run(
        "semgrep", ToolArgs(argv=["semgrep", "--quiet", "."], workdir="."), SANDBOX
    )

    raw = (tmp_path / RUN_ID / "semgrep" / f"{evidence.evidence_id}.log").read_bytes()

    assert evidence.digest == f"sha256:{hashlib.sha256(raw).hexdigest()}"
    assert CRLF not in raw, "로그가 CRLF로 써졌다 — digest는 LF 본문의 해시다"


def test_the_digest_is_taken_from_the_bytes_that_were_written(
    tmp_path: Path, spy: _Spy, monkeypatch: pytest.MonkeyPatch
) -> None:
    """플랫폼에 안 기댄다 — **파일에 건넨 바이트**가 digest의 입력이다 (이슈 #387).

    텍스트 쓰기(`write_text`)는 줄바꿈을 플랫폼에 맡겨 Windows에서 digest와 파일이 어긋난다.
    바이트로 쓰면 번역될 여지가 없다. 그래서 `write_bytes`가 받은 바이트를 가로채 digest와
    대조한다 — 텍스트 쓰기로 되돌리면 가로챌 호출이 없어 **어느 OS에서도** 빨개진다.
    """
    real_write_bytes = Path.write_bytes
    written: list[bytes] = []

    def recording_write_bytes(self: Path, data: bytes) -> int:
        if tmp_path in self.parents:
            written.append(bytes(data))
        return real_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", recording_write_bytes)

    evidence = _runner(tmp_path).run(
        "semgrep", ToolArgs(argv=["semgrep", "--quiet", "."], workdir="."), SANDBOX
    )

    assert len(written) == 1, "로그를 `write_bytes`로 쓰지 않았다"
    assert evidence.digest == f"sha256:{hashlib.sha256(written[0]).hexdigest()}"


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


# --- 도구 요청에 싣는 입력 — scratch에 쓰고 로그 머리의 자리표를 컨테이너 경로로 (#386 ①) ---

# NUL·CRLF·비-UTF-8 — 텍스트로 쓰거나 개행을 번역하면 달라지는 바이트다.
POC_BYTES = bytes.fromhex("000d0a0d0aff") + b"A" * 8


def _input_name(data: bytes) -> str:
    return f"input-{hashlib.sha256(data).hexdigest()[:16]}.poc"


def _input_args(tool: str, data: bytes = POC_BYTES) -> ToolArgs:
    return ToolArgs(argv=[tool, tool_runner.INPUT_PLACEHOLDER], workdir=".", input_bytes=data)


def _log_of(tmp_path: Path, evidence: Evidence) -> str:
    return (tmp_path / RUN_ID / evidence.source / f"{evidence.evidence_id}.log").read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize("tool", ["poc", "asan"])
def test_input_is_written_to_that_tools_scratch_byte_for_byte(
    tool: str, tmp_path: Path, spy: _Spy
) -> None:
    """같은 길이의 다른 바이트(영 채움)를 쓰는 변이·개행을 번역하는 변이·쓰기를 지우는 변이가
    여기서 죽는다. 파일은 **그 도구의** scratch에 간다 — 도구마다 마운트가 다르다."""
    _runner(tmp_path, poc_target=ARVO).run(tool, _input_args(tool), SANDBOX)

    written = scratch_dir(tmp_path, RUN_ID, tool) / _input_name(POC_BYTES)
    assert written.read_bytes() == POC_BYTES


def test_the_mounted_scratch_is_the_directory_the_input_was_written_to(
    tmp_path: Path, spy: _Spy
) -> None:
    """`/work/<이름>`이 컨테이너 안에서 풀리려면 그 파일이 **`-v …:/work`로 마운트되는 호스트
    디렉터리**에 있어야 한다 — 호스트 경로를 argv에 넣으면 안 풀린다(`container_argv`가
    `command.argv`를 그대로 넣는다)."""
    _runner(tmp_path, poc_target=ARVO).run("poc", _input_args("poc"), SANDBOX)

    scratch = scratch_dir(tmp_path, RUN_ID, "poc")
    assert f"{scratch.resolve().as_posix()}:/work" in spy.calls[0]
    assert (scratch / _input_name(POC_BYTES)).exists()


def test_the_recorded_argv_names_the_container_path_not_the_placeholder(
    tmp_path: Path, spy: _Spy
) -> None:
    """자리표를 안 바꾸는 변이가 여기서 죽는다 — 로그 첫 줄(`# node argv:`)이 **컨테이너 안 경로**를
    든다. 호스트 경로면 컨테이너 안에서 안 풀린다."""
    evidence = _runner(tmp_path, poc_target=ARVO).run("poc", _input_args("poc"), SANDBOX)

    log = _log_of(tmp_path, evidence)
    assert log.splitlines()[0] == f"# node argv: poc /work/{_input_name(POC_BYTES)}"
    assert tool_runner.INPUT_PLACEHOLDER not in log
    assert str(tmp_path) not in log.splitlines()[0]


def test_the_log_digest_depends_on_the_input(tmp_path: Path, spy: _Spy) -> None:
    """같은 도구·같은 명령이라도 **다른 입력**이면 로그가 달라 `Evidence.digest`가 달라진다 — 로그
    첫 줄이 입력 경로를 이름으로 든다(엔지니어링 가이드 §6: 무엇이 실행됐는지 사후 검증).
    자리표를 안 바꾸면 두 입력이 같은 줄을 남겨 digest가 같아진다. ⚠ 두 run이 **같은** scratch를
    쓴다 — 로그에는 호스트 마운트 경로가 실려 디렉터리가 다르면 그 차이로 digest가 갈린다."""
    first = _runner(tmp_path, poc_target=ARVO).run("poc", _input_args("poc"), SANDBOX)
    second = _runner(tmp_path, poc_target=ARVO).run(
        "poc", _input_args("poc", bytes(len(POC_BYTES))), SANDBOX
    )

    assert first.digest != second.digest


def test_an_empty_input_is_written_and_named_in_the_header(tmp_path: Path, spy: _Spy) -> None:
    """`b""`는 입력이다 — `is not None`을 진리값 판정(`if args.input_bytes:`)으로 바꾸면 0바이트
    PoC가 「입력 없음」으로 둔갑해 파일도 안 쓰이고 머리줄에 자리표가 풀리지 않은 채 남는다."""
    evidence = _runner(tmp_path, poc_target=ARVO).run("poc", _input_args("poc", b""), SANDBOX)

    name = _input_name(b"")
    assert name == "input-e3b0c44298fc1c14.poc"  # 빈 입력의 sha256 앞 16자
    assert (scratch_dir(tmp_path, RUN_ID, "poc") / name).read_bytes() == b""
    assert _log_of(tmp_path, evidence).splitlines()[0] == f"# node argv: poc /work/{name}"


@pytest.mark.parametrize("tool", ["asan", "gdb", "build"])
def test_no_executed_container_command_receives_the_input_today(
    tool: str, tmp_path: Path, spy: _Spy
) -> None:
    """🔴 **서술을 사실에 묶는 경보기다 — 단 이제 셋만 문다.**

    ⏵ **2026-10-09 (ADR 0014) — `poc`를 이 목록에서 뺐다.** 그 도구는 이제 과제 `-vul` 이미지에
    PoC를 `/tmp/poc`로 바인드해 **받는다**(`tests/test_poc_tool_feeds_the_input.py`가 그쪽을 든다).
    🔴 **셋은 그대로 안 받는다 — 한 도구만 바꿨다.** 셋도 받게 하려면 **우리가 대상을 빌드**해야
    하고 그것이 기각된 A안이다(우리 clang이 CyberGym 번들을 컴파일하지 못한다 · 이슈 #507 ·
    과제 1,507건의 빌드 규칙이 제각각). 그래서 셋의 한계 서술은 **여전히 참**이고 이 경보기가 그것을
    사실에 묶는다.

    ⚠ **무는 것은 「인자로 받지 않는다」이지 「읽지 않는다」가 아니다.** 실행된 argv에 이 입력의
    경로·`.poc`가 없는지만 본다 — 명령이 글롭이나 `sh -c`로 scratch의 파일을 **스스로 읽게** 바꾸면
    철자가 없어 이 검사는 초록이다. 그 변경은 `plan()`을 고치므로 아래
    `test_the_poc_command_feeds_the_input_now`가 `poc`를 따로 문다(`asan`·`gdb`·`build` 명령이
    글롭으로 읽게 바뀌는 것은 어느 쪽도 못 문다 — **그 한계는 안 고쳤다**).

    이 검사가 빨개지면 셋 중 하나가 PoC를 받는 것이다 — 그때는 그 셋을 서술하는 한계 문구를
    **같이** 고치고 **새 프롬프트 판**이 필요하다(서빙 prefix가 「넷 모두」라 적는다).
    짝: `tests/test_adapters.py`의 `_run_repro` 상수 핀이 fake 쪽을 잰다."""
    _runner(tmp_path, poc_target=ARVO).run(tool, _input_args(tool), SANDBOX)

    assert spy.calls, "명령이 하나도 안 돌았다 — 이 검사가 공허하다"
    container_path = f"/work/{_input_name(POC_BYTES)}"
    for argv in spy.calls:
        assert container_path not in " ".join(argv), f"{tool}: 명령이 입력 경로를 받았다 — {argv}"
        assert not any(arg.endswith(".poc") for arg in argv), f"{tool}: .poc 인자가 있다"


def test_the_poc_command_feeds_the_input_now() -> None:
    """🔴 위 경보기의 **짝**이다 — `plan("poc")`가 내는 명령을 정확히 핀한다.

    ⏵ **2026-10-09 — 이 핀이 뒤집혔다**(ADR 0014). 앞 판은 `[[f"{SCRATCH}/poc"]]`를 핀하며
    *"P1b(#513)가 그 명령을 고칠 때 여기가 빨개져 「관측이 PoC와 무관하다」 서술을 같이 고치게
    된다"*고 적었다. **그 일이 일어났고 설계대로 빨개졌다** — 그래서 같은 PR이 서술 32자리와
    프롬프트 판(v5)을 같이 고친다.

    핀하는 것 넷: 명령이 **하나**(빌드 단계가 없다 — 우리가 빌드하지 않는다) · argv가 **과제
    계열이 정한 것** · `image`가 **과제 `-vul` 이미지** · `needs_input`이 참.

    ⚠ **이 핀은 `poc`만 본다** — 다른 세 도구 명령이 입력을 글롭으로 읽게 바뀌는 것은 안 문다.
    그 한계는 앞 판과 **같다**(안 고쳤다)."""
    commands = plan("poc", target="/src", compile_sources=[], cwd="/src", poc_target=ARVO)

    assert [command.argv for command in commands] == [["/bin/arvo"]]
    assert [command.image for command in commands] == ["n132/arvo:1065-vul"]
    assert all(command.needs_input for command in commands)


def test_unwritable_input_is_failed_evidence_and_starts_no_process(
    tmp_path: Path, spy: _Spy, monkeypatch: pytest.MonkeyPatch
) -> None:
    """입력을 못 쓰면 **실행하지 않는다** — 없는 입력으로 도구를 돌려 그 결과를 PoC의 결과로 읽게
    두지 않는다. 문구는 모델 관측에 실리므로 호스트 경로를 안 든다."""

    def broken(directory: Path, data: bytes) -> Path:
        raise OSError(errno.ENOSPC, "No space left on device", "/host/secret/dir/input.poc")

    monkeypatch.setattr(container_tools, "write_input", broken)

    evidence = _runner(tmp_path, poc_target=ARVO).run("poc", _input_args("poc"), SANDBOX)

    assert evidence.ok is False
    assert evidence.exit_code == 2
    assert "입력" in evidence.error
    assert "/host/secret" not in evidence.error
    assert spy.calls == []
    assert "실행하지 않았다" in _log_of(tmp_path, evidence)


@pytest.mark.parametrize("kind", ["symlink", "dangling", "hardlink"])
def test_a_link_planted_in_the_scratch_is_not_written_through(
    tmp_path: Path, spy: _Spy, kind: str
) -> None:
    """🔴 심긴 링크를 따라 쓰면 **마운트 밖 파일이 PoC로 덮인다**(이유는 `tests/test_adapters.py`의
    짝 검사). 여기서는 `_execute`가 `write_input`을 안 지나고 직접 쓰는 변이를 문다."""
    outside = tmp_path / "host_secret.txt"
    if kind != "dangling":
        outside.write_bytes(b"host file - must stay")
    scratch = scratch_dir(tmp_path, RUN_ID, "poc")
    scratch.mkdir(parents=True)
    planted = scratch / _input_name(POC_BYTES)
    plant_link(kind, planted, outside)
    assert planted.is_symlink() or planted.exists(), "전제 — 링크가 심어졌다"

    evidence = _runner(tmp_path, poc_target=ARVO).run("poc", _input_args("poc"), SANDBOX)

    assert planted.read_bytes() == POC_BYTES and not planted.is_symlink()
    if kind == "dangling":
        assert not outside.exists(), "끊어진 링크를 따라가 마운트 밖에 파일을 만들었다"
    else:
        assert outside.read_bytes() == b"host file - must stay", "링크를 따라가 바깥 파일을 덮었다"
    assert evidence.source == "poc"


def test_a_run_without_input_leaves_the_scratch_empty_and_the_header_as_it_was(
    tmp_path: Path, spy: _Spy
) -> None:
    """입력이 없는 호출은 한 줄도 안 바뀐다 — 파일을 안 만들고 머리줄이 노드 argv 그대로다.

    ⏵ **2026-10-09 (ADR 0014) — 도구를 `poc`에서 `asan`으로 바꿨다.** `poc`는 이제 입력을 **받는
    명령**이라 입력 없이 부르면 **실패 Evidence**다(`container_argv`가 거절한다 — PoC 없이 대상을
    돌려 그 결과를 PoC의 결과로 적지 않는다). 그러면 이 검사가 재려던 명제(「입력이 없으면 쓰기도
    없고 머리줄이 그대로다」)를 **그 도구에서는 더 못 잰다.**

    🔴 **명제를 버리지 않고 도구를 옮겼다.** `asan`은 ADR 0014가 건드리지 않은 셋 중 하나이고
    입력을 안 받는 상태로 남으므로 같은 명제가 그대로 선다. 입력 없는 `poc` 호출이 실패 Evidence가
    되는 쪽은 `tests/test_poc_tool_feeds_the_input.py`가 든다.
    """
    evidence = _runner(tmp_path).run(
        "asan", ToolArgs(argv=["asan", "src/x.c"], workdir="."), SANDBOX
    )

    assert list(scratch_dir(tmp_path, RUN_ID, "asan").iterdir()) == []
    assert _log_of(tmp_path, evidence).splitlines()[0] == "# node argv: asan src/x.c"


def test_a_request_that_fails_planning_writes_no_input(tmp_path: Path, spy: _Spy) -> None:
    """계획이 실패하는 요청(컴파일할 소스가 없다)은 **쓰기 전에** 거절된다 — 안 돌 명령을 위해
    바이트를 두지 않는다."""
    root = tmp_path / "docs_only"
    root.mkdir()
    (root / "README.md").write_text("no sources", encoding="utf-8")

    evidence = _runner(tmp_path / "out", root=root).run("asan", _input_args("asan"), SANDBOX)

    assert evidence.ok is False
    assert spy.calls == []
    assert not scratch_dir(tmp_path / "out", RUN_ID, "asan").exists()


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


def test_indexer_receives_headers_but_clang_tools_do_not(tmp_path: Path, spy: _Spy) -> None:
    """헤더(`.h`)는 인덱서에 간다 — 매크로·타입 정의와 `static inline` 함수가 대개 거기 있다.

    안 가면 index-v2의 조회 표(NE resolver 몫)가 실제 저장소에서 대부분 빈다 — 표본 셋의
    `#define`은 69~100%가 헤더에 있었다(2026-09-26 ATOM, grep 기준 근사). 슬라이스가 헤더 함수에
    걸치면 Auditor도 그 함수 대신 앞뒤 창을 받는다. clang 계열에는 여전히 안 간다 — 헤더는 단독
    컴파일 대상이 아니다.
    """
    root = tmp_path / "hdr"
    root.mkdir()
    (root / "api.c").write_text('#include "api.h"\nint f(void) { return LEN; }\n', encoding="utf-8")
    (root / "api.h").write_text(
        "#define LEN 4\nstatic inline int g(void) { return 1; }\n", encoding="utf-8"
    )
    runner = _runner(tmp_path, root=root)

    runner.run("index", ToolArgs(argv=["index"], workdir="."), SANDBOX)
    index_argv = spy.calls[-1]
    runner.run("build", ToolArgs(argv=["build"], workdir="."), SANDBOX)
    build_argv = spy.calls[-1]

    assert "/src/api.h" in index_argv
    assert "/src/api.h" not in build_argv
    assert "/src/api.c" in build_argv


def test_the_default_image_is_the_one_that_bakes_the_indexer_and_gdb() -> None:
    """v3는 v2에 gdb를 더한다. v2는 지난 run의 digest 때문에 별도 태그로 남긴다. 인덱서 형식은
    #304부터 index-v3, 2026-10-04부터 index-v4인데 태그는 그대로다 — 낡은 이미지는 아래 연막 검사
    핀이 빌드에서 막는다."""
    assert DEFAULT_IMAGE == "harness-tools:v3"


def test_the_image_smoke_rejects_a_stale_indexer_copy() -> None:
    """Dockerfile의 인덱서 연막 검사가 **이 트리의** `INDEX_VERSION`과 v3 표를 본다(#464 리뷰).

    이미지에 구운 indexer.py가 낡은 사본이면 v3 조회가 예외 없이 `None`이 된다 — 빌드에서 죽어야
    한다. ⚠ 이 검사는 철자 대조다: `INDEX_VERSION`을 올리고 Dockerfile을 안 고치면 여기서 죽지만,
    연막 검사의 나머지 조건이 실제로 도는지는 이미지 빌드만 안다.
    """
    tools = Path(__file__).resolve().parents[1] / "tools" / "harness-tools"
    dockerfile = (tools / "Dockerfile").read_text(encoding="utf-8")
    spec = importlib.util.spec_from_file_location("harness_indexer_smoke", tools / "indexer.py")
    assert spec is not None and spec.loader is not None
    indexer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(indexer)

    assert f"d['version']=='{indexer.INDEX_VERSION}'" in dockerfile
    assert "'x' in d['writes']" in dockerfile


def test_the_tool_image_installs_and_smokes_gdb_batch_mode() -> None:
    """패키지 누락은 8단계가 아니라 이미지 빌드에서 드러나야 한다."""
    dockerfile = (
        Path(__file__).resolve().parents[1] / "tools" / "harness-tools" / "Dockerfile"
    ).read_text(encoding="utf-8")

    assert (
        "gdb --nx -batch --return-child-result -x /opt/gdb.batch -- /tmp/gdb-smoke"
        in dockerfile
    )


def test_the_adapter_compares_against_the_constant_not_a_literal() -> None:
    """`INDEX_TOOL`을 만들어 두고 `tool == "index"` 리터럴로 비교하면 안 된다.

    바로 위 `test_scratch_dir_name_matches_the_node_tool_name`이 두 상수를 대조하지만,
    **양쪽 상수를 함께 바꾸면 그 검사는 통과한다.** 그때 리터럴만 뒤처져 인덱서가 조용히
    `_COMPILE_SUFFIXES`를 받고 `.inc`가 빠진다 — D16이 기록한 실패(28바이트짜리
    `#include` 한 줄만 분석하고 17KB를 보지 않았다)의 재현이다.

    **행동으로는 구별할 수 없다.** 상수를 갈아 끼우면 `plan()`이 인덱서 명령을 아예 못
    만들어 명령이 실행되지 않는다 — 리터럴이 남아 있어도 같은 모양이 된다. 그래서
    소스를 읽는다. `tests/test_policy.py::test_orchestrator_source_has_no_adapter_import`가
    같은 이유로 같은 방식을 쓴다.
    """
    source = Path(container_tools.__file__).read_text(encoding="utf-8")
    # 주석은 뺀다 — 이 결함을 설명하는 주석이 그 문자열을 인용한다.
    code = [line for line in source.splitlines() if not line.lstrip().startswith("#")]

    offenders = [line.strip() for line in code if 'tool == "index"' in line]

    assert offenders == [], f"리터럴 비교가 남아 있다: {offenders}"


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


# --- 컴파일 조건 기록 (D32 / #74) ---------------------------------------------
#
# D32가 적는 것: *"`RunRecord`에 없으므로 두 run을 나란히 놓았을 때 컴파일 조건이 같았는지
# 다른지가 표에 나타나지 않는다. 확인하려면 run마다 Evidence 로그를 열어야 한다."*
# 즉 사후 감사로는 잡히고 **비교표로는 안 잡힌다**가 정확한 상태다.


def _compile_commands(workdir: str) -> list[Command]:
    """그 workdir에서 clang을 실제로 부르는 명령만 고른다.

    `asan`·`gdb`는 명령을 둘 내는데 둘째는 만들어진 바이너리를 **실행**하는 것이라 컴파일
    인자가 없다. `-std=`가 있는 것만 남기면 세 도구의 컴파일 호출이 정확히 걸린다.
    """
    mixed = [f"{workdir}/a.c", f"{workdir}/b.cpp"]
    c_only = [f"{workdir}/a.c"]
    commands = [
        *plan("clang-tidy", target=workdir, compile_sources=mixed, cwd=workdir),
        *plan("build", target=workdir, compile_sources=mixed, cwd=workdir),
        *plan("asan", target=workdir, compile_sources=c_only, cwd=workdir),
        *plan("gdb", target=workdir, compile_sources=c_only, cwd=workdir),
    ]
    return [c for c in commands if any(arg.startswith("-std=") for arg in c.argv)]


def _include_dirs(command: Command) -> set[str]:
    return {command.argv[i + 1] for i, arg in enumerate(command.argv) if arg == "-I"}


def test_recorded_compile_flags_match_the_argv_that_plan_emits(tmp_path: Path) -> None:
    """기록이 argv와 갈리면 재현성 기록이 거짓이 된다 (D32 / #74).

    `factory`가 `images()`에 대해 적은 논법 그대로다 — *"구현에게 묻는다. 여기서
    조립하면 어댑터가 무엇을 실제로 물렸는지와 갈라질 수 있고, 갈라진 재현성 기록은
    없는 것보다 나쁘다."* 그래서 기대값을 `_STD_ARGS`에서 **베끼지 않고** `plan()`이
    실제로 낸 argv에서 뽑는다. 상수만 고치면 여기서 죽는다.
    """
    declared = _runner(tmp_path).compile_flags()
    commands = _compile_commands(f"{container_tools.MOUNT}/pkg")

    emitted = {arg for command in commands for arg in command.argv if arg.startswith("-std=")}

    assert commands, "컴파일 명령이 하나도 안 잡혔다 — 이 검사가 공허해진다"
    assert emitted == set(declared.std.values())


def test_recorded_include_dirs_are_the_ones_that_do_not_move_with_the_target(
    tmp_path: Path,
) -> None:
    """`fixed_include_dirs`는 **대상이 바뀌어도 그대로인** `-I`만 담는다.

    `_include_args()`는 대상 디렉터리와 마운트 루트 둘을 낸다. 앞엣것은 대상마다 달라
    두 run을 나란히 놓는 데 못 쓰고, 뒤엣것은 고정이다. 그래서 기대값을 **서로 다른
    workdir 둘의 교집합**으로 뽑는다 — 이름이 약속하는 성질이 곧 이 교집합이다.

    ⚠ **이 검사가 무는 것은 "고정분이 맞나"까지다.** *"실제로 어떤 `-I`가 갔나"*는
    Evidence 로그의 `$ {argv}` 줄이 답하고 이 필드가 아니다(D32의 층위 ①).
    """
    declared = _runner(tmp_path).compile_flags()
    here = [_include_dirs(c) for c in _compile_commands(f"{container_tools.MOUNT}/pkg")]
    there = [_include_dirs(c) for c in _compile_commands(f"{container_tools.MOUNT}/다른대상")]

    assert set(declared.fixed_include_dirs) == set.intersection(*here, *there)
    # 대상별 workdir이 새어 들어오면 두 run의 기록이 내용 없이 달라진다.
    assert f"{container_tools.MOUNT}/pkg" not in declared.fixed_include_dirs


def test_the_fake_runner_records_an_empty_compile_condition(tmp_path: Path) -> None:
    """Fake는 컴파일을 하지 않으므로 **빈 조건**이다 — `None`과 다르다.

    `images()`가 빈 dict를 내는 것과 같은 자리다. 빈 값은 *"기록했으나 컴파일한 도구가
    없었다"*이고 `None`은 *"이 manifest는 이 사실을 남기지 않았다"*(옛 기록)다 —
    `RunRecord.model_sampling`의 `{}` 대 `None` 관용구를 그대로 쓴다.
    """
    fake = FakeToolRunner(ids=IdFactory(RUN_ID), run_id=RUN_ID, root=tmp_path)

    flags = fake.compile_flags()

    assert flags.std == {}
    assert flags.fixed_include_dirs == []


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


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker가 없다")
def test_real_gdb_runs_clean_and_crashing_targets_without_ptrace_capability(tmp_path: Path) -> None:
    """Docker 실경로: 자식 실행형 gdb는 capability 추가 없이 정상/신호 종료를 기록한다."""
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

    runner = _runner(tmp_path, root=GDB_TARGET)
    clean = runner.run("gdb", ToolArgs(argv=["gdb", "normal.c"], workdir="normal.c"), SANDBOX)
    crash = runner.run("gdb", ToolArgs(argv=["gdb", "crash.c"], workdir="crash.c"), SANDBOX)

    assert clean.ok and clean.kind is EvidenceKind.CRASH and clean.exit_code == 0
    assert crash.ok and crash.kind is EvidenceKind.CRASH and crash.exit_code != 0
    clean_log = tmp_path / RUN_ID / "gdb" / f"{clean.evidence_id}.log"
    crash_log = tmp_path / RUN_ID / "gdb" / f"{crash.evidence_id}.log"
    assert "exited normally" in clean_log.read_text(encoding="utf-8")
    assert "Program received signal SIGSEGV" in crash_log.read_text(encoding="utf-8")


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


def test_clang_tidy_that_never_started_is_not_a_static_signal() -> None:
    """컨테이너가 명령을 못 띄운 것은 **신호가 아니다.**

    2026-08-28 `run_20260828_0001`에서 keyring 쿼터로 docker가 컨테이너를 하나도 못 띄웠다.
    도구 호출 13건이 전부 exit 125였는데 semgrep·build는 `ok=False`로 떨어진 반면
    clang-tidy만 `ok=True`로 올라가 `target_plan.signal_artifacts`가 **3으로 조작됐다.**
    `signal`은 `_order_and_cut`의 정렬 첫 키이자 Evidence Gate의 입력이므로, 한 번도 돌지
    않은 도구가 그 둘을 움직인 것이다.

    `_EXEC_FAILED`는 원래 있던 상수이고 그 주석이 "크래시(=신호)와 구분해야 한다"고 이미
    적어 뒀다 — `_RUNTIME_TOOLS`에만 걸려 있었을 뿐이다.
    """
    for code in (125, 126, 127):
        ok, exit_code, error = judge("clang-tidy", [Step(code, "", "docker: daemon error")], 1)

        assert ok is False, f"exit={code}가 신호로 올라갔다"
        assert exit_code == code, "프로세스가 낸 값을 그대로 남긴다"
        assert error is not None and "띄우지 못했다" in error


def test_clang_tidy_nonzero_without_any_diagnostic_is_not_a_signal() -> None:
    """`--warnings-as-errors=*`가 non-zero를 만드는 길은 경고 발견뿐이고 그것은 stdout에 찍힌다.

    그래서 **non-zero인데 진단이 한 줄도 없으면 돌지 않은 것**이다. `_EXEC_FAILED`가 지금
    아는 코드는 막지만 런타임이 다른 코드로 실패하면 그쪽은 또 신호가 되므로, exit code가
    아니라 출력의 유무로 판정한다 — `judge_semgrep`이 결과 문서로 판정하기로 한 것과 같다.
    """
    ok, code, error = judge("clang-tidy", [Step(1, "   \n", "some runtime failure")], 1)

    assert ok is False
    assert code == 1
    assert error is not None and "진단을 한 줄도 내지 않았다" in error


def test_clang_tidy_still_reports_real_warnings_as_a_signal() -> None:
    """고침이 **진짜 신호를 죽이지 않는다.** 진단이 있으면 non-zero는 그대로 신호다."""
    assert judge("clang-tidy", [Step(1, "util.c:14:5: warning: x [bugprone-x]", "")], 1) == (
        True,
        1,
        None,
    )
    # 깨끗한 실행은 예전과 같다.
    assert judge("clang-tidy", [Step(0, "", "")], 1) == (True, 0, None)


# --- semgrep이 파일을 실제로 봤는가 (#66) / 언어 그룹 break 기록 (#75) --------------


def _semgrep_json_scanned(results: int, scanned: list[str]) -> str:
    """`--json` 출력에 `paths.scanned`까지 담는다 — 실제 semgrep은 항상 `paths`를 낸다."""
    return json.dumps(
        {
            "results": [{"check_id": f"r{i}"} for i in range(results)],
            "paths": {"scanned": scanned, "skipped": []},
            "errors": [],
        }
    )


def test_semgrep_that_scanned_no_file_is_a_failure_not_a_clean_scan() -> None:
    """`.inc`처럼 확장자가 언어로 매핑되지 않으면 semgrep이 exit 0·results 0을 내지만
    `paths.scanned`가 비어 있다 — 파일을 아예 안 본 것이다(#66). 이걸 '정적 신호 없음'으로
    읽으면 '도구가 안 봄'과 '봤는데 깨끗함'이 구별되지 않는다.
    """
    ok, code, error = judge("semgrep", [Step(0, _semgrep_json_scanned(0, []), "")], 1)

    assert ok is False
    assert code == 0, "프로세스가 반환한 값을 그대로 남긴다"
    assert error is not None and "스캔" in error


def test_semgrep_clean_scan_of_a_real_file_is_still_ok() -> None:
    """results 0인데 `paths.scanned`에 파일이 있으면 정상 클린 스캔이다 — 실패로 만들지 않는다.

    '봤는데 깨끗함'(scanned 非empty)과 '안 봄'(scanned empty)을 가르는 것이 이 항목의 요점이다.
    """
    assert judge("semgrep", [Step(0, _semgrep_json_scanned(0, ["target.c"]), "")], 1) == (
        True,
        0,
        None,
    )


def test_unexecuted_commands_are_recorded_when_an_early_command_breaks() -> None:
    """언어 그룹이 갈린 clang-tidy에서 C 그룹 발견이 non-zero라 break되면 C++ 그룹이 안 돈다(#75).

    break는 asan의 "빌드 실패면 실행 안 함"(E4 가드)이라 유지하되, 안 돈 명령을 기록에 남긴다 —
    안 그러면 뒤 그룹의 발견이 앞 그룹 발견에 가려 어디에도 안 남는다.
    """
    from src.adapters.container_tools import unexecuted_commands_note

    note = unexecuted_commands_note(1, 2)
    assert note is not None and "1" in note
    assert unexecuted_commands_note(2, 2) is None  # 다 돌았으면 기록 없음
    assert unexecuted_commands_note(1, 1) is None


def test_the_log_actually_carries_the_unexecuted_note(tmp_path: Path, spy: _Spy) -> None:
    """**순수 함수가 아니라 배선을 본다** — `_execute`가 그 문자열을 로그에 넣는가.

    `unexecuted_commands_note()` 자체는 위 테스트가 직접 호출해 검사한다. 그런데 그 결과를
    `sections`에 넣는 `_execute`의 세 줄은 아무 테스트도 지키지 않았다: 그 셋을 통째로
    지우고 전체 스위트를 돌려 **`976 passed, 3 skipped`**(실패 0)를 실측했다.
    D34가 `닫힘`의 근거로 든 동작이 조용히 사라져도 CI가 초록이었다는 뜻이다.

    `asan`은 [컴파일, 실행] 2-Command라 첫 명령이 non-zero면 `_execute`가 break한다 —
    이 저장소에서 "안 돈 명령"이 실제로 생기는 경로다.
    """
    spy.returncode = 1

    evidence = _runner(tmp_path).run(
        "asan", ToolArgs(argv=["asan", "."], workdir="."), SANDBOX
    )

    log = tmp_path / RUN_ID / "asan" / f"{evidence.evidence_id}.log"
    body = log.read_text(encoding="utf-8")

    assert len(spy.calls) == 1, "break되지 않아 이 테스트가 무의미하다"
    assert "실행되지 않은 명령" in body, "안 돈 명령이 로그에 안 남는다"


def test_semgrep_own_error_exit_is_not_blamed_on_extension_mapping(tmp_path: Path) -> None:
    """semgrep 자신의 오류 종료(2·7)에 "확장자 미매핑" 사유를 붙이지 않는다.

    `paths.scanned`가 비는 것은 exit 0에서 "그 파일을 안 봤다"의 신호지만, semgrep이 자기
    오류로 죽었을 때는 스캔 목록이 비는 것이 **당연한 결과**이지 원인이 아니다. 관측하지
    않은 원인을 사유로 적으면 §0(실측과 추정을 섞지 않는다)에 어긋난다.

    판정(`ok=False`)과 `exit_code`는 어느 순서든 같다 — 달라지는 것은 기록에 남는 문장뿐이다.
    """
    empty = json.dumps({"results": [], "paths": {"scanned": [], "skipped": []}, "errors": []})

    ok, code, error = judge("semgrep", [Step(2, empty, "")], 1)

    assert ok is False
    assert code == 2
    assert error is not None
    assert "실행 실패" in error, "semgrep 자체 오류인데 확장자 탓으로 기록한다"
    assert "확장자" not in error


# --- semgrep 반쪽 파싱 — 이슈 #563 선행 -----------------------------------------
#
# 🔴 **샘플은 지어내지 않았다.** 아래 둘은 2026-10-10에 `harness-tools:v3`와
# `/opt/semgrep-rules/c`로 **실제로 돌려** 받은 출력의 필요한 부분이다(CLAUDE.md §2.5 —
# 「핀 샘플은 저장소에서 온다. 지어낸 샘플은 잡히는 형태만 고르게 된다」). 구문 오류를 심은
# C 파일과 그것을 고친 대조 파일을 같은 argv로 돌렸다.

# 양성 — `errors[].type`이 **리스트**이고 첫 원소가 종류 이름이다(실측 모양).
_SEMGREP_PARTIAL = json.dumps(
    {
        "results": [
            {
                "check_id": "opt.semgrep-rules.c.lang.security.insecure-use-memset",
                "path": "bad.c",
                "start": {"line": 4},
                "end": {"line": 4},
                "extra": {"message": "memset may leave sensitive data behind"},
            }
        ],
        "paths": {"scanned": ["bad.c"]},
        "errors": [
            {
                "code": 3,
                "level": "warn",
                "type": ["PartialParsing", [{"path": "bad.c"}]],
                "path": "bad.c",
                "message": "Syntax error at line bad.c:3:" + chr(10) + " `int` was unexpected",
            }
        ],
    }
)

# 음성 — 구문을 고친 대조. 🔴 `exit`·`results`·`paths.scanned`가 **양성과 같다.**
_SEMGREP_CLEAN = json.dumps(
    {
        "results": [{"path": "good.c", "start": {"line": 2}, "end": {"line": 2}}],
        "paths": {"scanned": ["good.c"]},
        "errors": [],
    }
)


def test_a_half_parsed_target_is_named_and_a_clean_one_is_not() -> None:
    """🔴 **파싱 실패를 말하는 축은 `errors` 하나다** (2026-10-10 실측 · 이슈 #563 선행).

    같은 argv에서 두 파일의 `exit`는 **둘 다 1**(발견 있음) · `results`는 **둘 다 1건** ·
    그리고 **`paths.scanned`에 둘 다 들어 있다.** 그래서 `judge_semgrep`의 `paths.scanned`
    판정(#66)은 반쪽 파싱을 **못 잡는다** — 이 검사가 그 사실을 같이 단언한다.
    """
    assert container_tools.semgrep_partial_parse(_SEMGREP_PARTIAL) == ("bad.c",)
    assert container_tools.semgrep_partial_parse(_SEMGREP_CLEAN) == ()

    # 🔴 기존 판정 축이 둘을 못 가른다 — 이 세 줄이 「왜 새 축이 필요한가」다.
    assert container_tools.semgrep_scanned(_SEMGREP_PARTIAL) == ["bad.c"]
    assert container_tools.semgrep_scanned(_SEMGREP_CLEAN) == ["good.c"]
    assert len(container_tools.semgrep_results(_SEMGREP_PARTIAL) or []) == len(
        container_tools.semgrep_results(_SEMGREP_CLEAN) or []
    )


def test_the_partial_parse_note_says_the_count_is_a_lower_bound() -> None:
    """🔴 **뜻을 단언한다** — 철자가 아니라. `POC_AMBIGUOUS_NOTE`에서 문구의 뜻을 뒤집는
    변이가 131건을 통과한 선례가 있다(적대적 리뷰 2026-10-09).

    이 문구가 모델 관측에 실리므로, 「하한」이 「전부」로 뒤집히면 **모델이 「신호는 이것뿐」으로
    읽는다.** 그리고 판정은 **안 바꾼다** — 반쪽 파싱의 히트도 신호이고 `ok=False`로 내리면
    `static_ok`가 꺼져 E1이 줄어든다(방향이 거꾸로다).
    """
    note = container_tools.SEMGREP_PARTIAL_NOTE
    assert "하한" in note
    assert "단언하지 않는다" in note
    assert "전부" not in note

    steps = [container_tools.Step(1, _SEMGREP_PARTIAL, "")]
    ok, code, error = container_tools.judge("semgrep", steps, total=1)
    assert (ok, code, error) == (True, 1, None), "판정은 안 바꾼다 — 기록만 한다"


def test_the_note_rides_the_summary_return_value_as_its_second_line() -> None:
    """🔴 **떨어뜨려 두면 관측 꼬리 상한에서 잘려 수만 남는다** — `POC_AMBIGUOUS_NOTE`가
    정확히 그렇게 잘렸다(이슈 #573). 그래서 같은 반환값에 싣는다 — 개행으로 이은 **둘째 줄**이다.
    (모델 사본에서 이 반환값이 80자에 잘리는 문제는 아래 `evidence_summaries` 검사가 문다.)
    """
    partial = container_tools.summary_line(
        "semgrep", [container_tools.Step(1, _SEMGREP_PARTIAL, "")]
    )
    clean = container_tools.summary_line("semgrep", [container_tools.Step(1, _SEMGREP_CLEAN, "")])

    assert partial is not None and clean is not None
    assert clean == "# semgrep: 1 findings", "깨끗한 스캔의 바이트는 종전과 같다"
    first, second = partial.split(LF)
    assert first == "# semgrep: 1 findings"
    assert container_tools.SEMGREP_PARTIAL_NOTE in second
    assert "bad.c" in second, "어느 파일이 반쪽인지 적는다 — 모르면 셀 것이 없다"


# --- 반쪽 파싱 — 모델 사본까지 닿는가 · 거짓 양성 가드 (적대적 리뷰 2026-10-10) --------------
#
# 위 검사는 `summary_line` 반환값까지만 본다. 그 반환값은 `evidence_summaries`의 접기가 로그 머리를
# 80자에서 자르는 바람에 모델 사본에서 `(Par`로 잘렸다 — 아래 검사는 **실제 러너가 쓴 로그**를
# `evidence_summaries`에 통과시킨다. 음성·문자열 `type` 샘플은 **합성**이다: 실측한 `errors` 모양은
# `PartialParsing` 리스트 하나뿐이고(룰 경고의 실 모양은 미실측), 코드가 `type`을 두 모양 다
# 받으므로 두 모양이 나타날 수 있다는 근거로 골랐다.


def _partial_item(path: str) -> dict[str, object]:
    return {
        "code": 3,
        "level": "warn",
        "type": ["PartialParsing", [{"path": path}]],
        "path": path,
        "message": "Syntax error at line " + path + ":3:",
    }


def _errors_doc(errors: list[object], *, hits: int = 1) -> str:
    results = [
        {
            "check_id": f"rule.r{i}",
            "path": "a.c",
            "start": {"line": 3 + i},
            "end": {"line": 3 + i},
            "extra": {"message": "memset may leave sensitive data behind"},
        }
        for i in range(hits)
    ]
    return json.dumps({"results": results, "paths": {"scanned": ["a.c"]}, "errors": errors})


# zlib 모양 — exit 0 · results 0 · PartialParsing 1(이슈 #563 본문의 `adler32.c` 건). 값은 합성이다.
_SEMGREP_ZLIB = _errors_doc([_partial_item("adler32.c")], hits=0)
_RULE_WARNING = {"code": 2, "level": "warn", "type": ["RuleWarning", []], "path": "r.yaml"}


def _model_body(tmp_path: Path, spy: _Spy, stdout: str, code: int) -> tuple[str, Evidence]:
    """실제 `ContainerToolRunner.run`이 쓴 로그를 `evidence_summaries`에 통과시킨 모델 사본."""
    spy.returncode, spy.stdout, spy.stderr = code, stdout, ""
    runner = _runner(tmp_path)
    ev = runner.run("semgrep", ToolArgs(argv=["semgrep", "parser.c"], workdir="parser.c"), SANDBOX)
    bodies = evidence_summaries([ev], container_tools.make_payload_reader(tmp_path))
    return str(bodies[ev.evidence_id]["body"]), ev


@pytest.mark.parametrize(
    ("stdout", "code", "path"), [(_SEMGREP_PARTIAL, 1, "bad.c"), (_SEMGREP_ZLIB, 0, "adler32.c")]
)
def test_the_partial_parse_fact_reaches_the_model_copy_whole(
    tmp_path: Path, spy: _Spy, stdout: str, code: int, path: str
) -> None:
    """🔴 노트가 모델 사본에서 `(Par`로 잘리던 결함이 되살아나면 빨개진다. 히트가 **0건**인 zlib
    모양이 가장 위험한 자리라 같이 돈다 — 사람용 로그 파일에는 한국어 노트가 그대로 남는다."""
    body, ev = _model_body(tmp_path, spy, stdout, code)

    assert f"partial_parse: {path}" in body and "lower bound" in body
    assert "(Par" not in body and "반쪽" not in body, f"한국어 노트 잔해가 남았다: {body!r}"
    assert body.count("errors:") == 1, "새 줄이 아니라 기존 `errors:` 줄 안에 싣는다"
    marker = [line for line in body.splitlines() if "partial_parse" in line]
    assert len(marker) == 1 and marker[0].startswith("errors:"), (
        f"표지가 `errors:` 줄 밖으로 나갔다(prefix의 줄 순서 서술이 깨진다): {marker}"
    )
    log = container_tools.make_payload_reader(tmp_path)(ev.payload_ref)
    assert log is not None and container_tools.SEMGREP_PARTIAL_NOTE in log
    assert (ev.ok, ev.exit_code, ev.error) == (True, code, None), "판정은 안 바꾼다"


def test_a_half_parsed_scan_with_no_hits_still_judges_ok() -> None:
    """N1 — 「반쪽이고 `results`가 비면 `ok=False`」 변이를 죽인다(zlib 모양)."""
    steps = [container_tools.Step(0, _SEMGREP_ZLIB, "")]
    assert judge("semgrep", steps, total=1) == (True, 0, None)
    assert first_hit_location("semgrep", steps) is None


def test_a_rule_warning_is_not_counted_as_a_half_parse() -> None:
    """🔴 거짓 양성 가드 — `type`이 `PartialParsing`이 아닌 `errors` 항목(룰 경고 등)을 반쪽으로
    세면 모델이 「히트는 하한」을 읽는다. 이름 검사·문자열 `type` 가지·`[:3]` 상한을 각각 문다."""
    cases = {
        "rule warning (list type)": ([_RULE_WARNING], ()),
        "no type": ([{"code": 3, "message": "e", "path": "a.c"}], ()),
        "string type, other kind": ([{"type": "RuleWarning", "path": "r.yaml"}], ()),
        "string type, partial": ([{"type": "PartialParsing", "path": "x.c"}], ("x.c",)),
        "mixed": ([_RULE_WARNING, _partial_item("b.c")], ("b.c",)),
    }
    for name, (errors, expected) in cases.items():
        stdout = _errors_doc(errors)
        assert container_tools.semgrep_partial_parse(stdout) == expected, name
        if not expected:
            steps = [container_tools.Step(1, stdout, "")]
            assert container_tools.summary_line("semgrep", steps) == "# semgrep: 1 findings", name

    five = _errors_doc([_partial_item(f"f{i}.c") for i in range(5)])
    line = container_tools.summary_line("semgrep", [container_tools.Step(1, five, "")]) or ""
    assert "(5개: f0.c, f1.c, f2.c 외)" in line and "f3.c" not in line, line


def test_the_model_copy_counts_half_parses_the_way_the_adapter_does(
    tmp_path: Path, spy: _Spy
) -> None:
    """모델 쪽 `type` 판정은 어댑터를 import할 수 없어 복제다 — 같은 입력에 같은 결론이어야 한다.
    반쪽 파싱이 아닌 스캔의 `errors:` 줄은 종전 바이트 그대로다(표지 없음)."""
    for errors in ([_RULE_WARNING], [{"type": "RuleWarning", "path": "r.yaml"}], []):
        body, _ = _model_body(tmp_path, spy, _errors_doc(errors), 1)
        size = len(json.dumps(errors))
        assert f"errors: {len(errors)} ({size} chars dropped); other JSON fields omitted" in body
        assert "partial_parse" not in body

    string_typed = _errors_doc([{"type": "PartialParsing", "path": "x.c"}])
    assert "partial_parse: x.c" in _model_body(tmp_path, spy, string_typed, 1)[0]
    five = _errors_doc([_partial_item(f"f{i}.c") for i in range(5)])
    assert "partial_parse: f0.c, f1.c, f2.c +2 more;" in _model_body(tmp_path, spy, five, 1)[0]

    assert prompt_loader._SEMGREP_PARTIAL_PARSE == container_tools._SEMGREP_PARTIAL_PARSE
    steps = [container_tools.Step(1, _SEMGREP_PARTIAL, "")]
    note_line = (container_tools.summary_line("semgrep", steps) or "").split(LF)[1]
    assert prompt_loader._SEMGREP_PARTIAL_NOTE_LINE.sub("", note_line + LF) == "", (
        "모델 사본이 빼는 줄과 어댑터가 쓰는 노트가 어긋났다"
    )


def test_a_scan_the_fold_cannot_read_keeps_the_korean_note(tmp_path: Path, spy: _Spy) -> None:
    """접기가 못 읽는 로그(히트에 `check_id`가 없다)는 범용 절단 경로로 가고 거기엔 영어 표지가
    없다 — 그때는 한국어 노트를 빼지 않는다(빼면 반쪽 파싱이라는 사실이 통째로 사라진다)."""
    unreadable = json.dumps(
        {"results": [{"path": "bad.c", "start": {"line": 4}, "end": {"line": 4}}],
         "errors": [_partial_item("bad.c")]}
    )
    body, _ = _model_body(tmp_path, spy, unreadable, 1)

    assert container_tools.SEMGREP_PARTIAL_NOTE in body


def test_long_or_multiline_paths_do_not_eat_the_hits(tmp_path: Path, spy: _Spy) -> None:
    """경로 하나가 길어도(300자) 접힌 히트가 줄지 않는다 — 표지의 경로는 약 80자로 자르고
    **뒤를 남긴다**(파일명이 뒤에 있다). 개행이 든 파일명도 표지 줄 하나 안에 머문다."""
    long_dir = "/" + "d" * 290
    errors = [_partial_item("a" + LF + "b.c")]
    errors += [_partial_item(f"{long_dir}/file_{i}.c") for i in range(3)]
    body, _ = _model_body(tmp_path, spy, _errors_doc(errors, hits=10), 1)

    assert "10 of 10 shown" in body, f"긴 경로가 히트를 지웠다: {body[:300]!r}"
    marker = next(line for line in body.splitlines() if "partial_parse" in line)
    assert marker.startswith("errors:") and marker.endswith("other JSON fields omitted")
    listed = marker.split("partial_parse: ")[1].split("; hits are")[0]
    assert listed.endswith(" +1 more") and listed.startswith("a b.c, "), listed
    shown = listed.removesuffix(" +1 more").split(", ")
    assert len(shown) == 3 and all(len(path) <= 80 for path in shown), shown
    assert shown[1].startswith("…") and shown[1].endswith("/file_0.c"), "앞이 아니라 뒤를 남긴다"
    assert not any(line.strip() == "b.c" for line in body.splitlines()), "개행 파일명 잔해 줄"


# --- #507: 컴파일 실패 사유가 error 필드(= 모델 관측 머리줄)에 실린다 -----------------------------

_CLANG_STDERR = (
    "/src/file/src/apprentice.c:12:5: error: unknown type name 'uint8_t'\n"
    "/src/file/src/apprentice.c:13:5: error: unknown type name 'size_t'\n"
    "2 errors generated.\n"
)


def test_build_failure_error_names_the_first_compile_error_and_the_count() -> None:
    ok, code, error = judge("build", [Step(1, "", _CLANG_STDERR)], 1)

    assert (ok, code) == (False, 1)
    assert "build 실패 (exit=1)" in error
    assert "2건" in error and "unknown type name 'uint8_t'" in error
    assert "/src/" not in error, "경로는 관측으로 새면 안 된다"


def test_build_failure_without_compiler_errors_keeps_the_bare_message() -> None:
    assert judge("build", [Step(1, "", "killed")], 1) == (False, 1, "build 실패 (exit=1)")


def test_clang_tidy_compile_failure_error_carries_the_reason() -> None:
    stdout = (
        "/src/a.c:3:1: error: unknown type name 'u8' [clang-diagnostic-error]\n"
        "/src/a.c:4:1: error: unknown type name 'u16' [clang-diagnostic-error]\n"
    )
    _, _, error = judge("clang-tidy", [Step(1, stdout, "")], 1)

    assert error.startswith("clang-tidy: 대상이 컴파일되지 않는다")
    assert "2건" in error and "unknown type name 'u8'" in error
    assert "clang-diagnostic-error" not in error.split("—", 1)[1]


def test_gdb_and_asan_build_cut_carry_the_compile_reason() -> None:
    for tool in ("gdb", "asan"):
        _, _, error = judge(tool, [Step(1, "", _CLANG_STDERR)], 2)

        assert "빌드 단계에서 끊겨" in error or "실행되지 않았다" in error
        assert "unknown type name 'uint8_t'" in error, tool


def test_compile_reason_reaches_the_pov_observation_head_before_the_log_tail() -> None:
    from src.orchestrator import runner
    from src.schemas.enums import EvidenceKind
    from src.schemas.evidence import Evidence

    _, code, error = judge("build", [Step(1, "", _CLANG_STDERR)], 1)
    evidence = Evidence(
        evidence_id="ev_0006", run_id="run_20261011_0001", artifact_id="art_0001",
        kind=EvidenceKind.BUILD, source="build", ok=False, exit_code=code, error=error,
        payload_ref="log/build.txt", digest="sha256:" + "0" * 64,
        observed_at=datetime(2026, 10, 12, tzinfo=UTC),
    )
    log = "n" * 5000 + "\n" + _CLANG_STDERR

    text = runner._pov_observation("inspect", evidence, read_payload=lambda _ref: log)

    head = text.splitlines()[0]
    assert "unknown type name 'uint8_t'" in head, "사유가 머리줄에 없다 (꼬리 절단에 기댐)"


def test_clang_tidy_reason_counts_only_compile_errors_not_promoted_lint_findings() -> None:
    """`--warnings-as-errors=*`가 린트 소견도 `error:`로 찍는다 — 태그된 줄만 컴파일 오류다."""
    stdout = (
        "/src/a.c:2:3: error: Value stored to 'x' is never read "
        "[clang-analyzer-deadcode.DeadStores,-warnings-as-errors]\n"
        "/src/a.c:4:1: error: unknown type name 'u8' [clang-diagnostic-error]\n"
    )
    _, _, error = judge("clang-tidy", [Step(1, stdout, "")], 1)

    assert "1건, 첫 오류: unknown type name 'u8'" in error
    assert "Value stored" not in error


def test_compile_reason_is_capped_and_one_line() -> None:
    cr = chr(13)
    stderr = f"a.c:1:1: error: {'x' * 500}{cr}\na.c:2:1: error: bad{cr}tab{chr(9)}\n"
    reason = container_tools.compile_failure_reason(stderr)

    assert len(reason.split("첫 오류: ", 1)[1]) == container_tools._COMPILE_REASON_CHARS
    assert cr not in reason and "\n" not in reason
    assert cr not in container_tools.compile_failure_reason(f"a.c:1:1: error: bad{cr}tab\n")
