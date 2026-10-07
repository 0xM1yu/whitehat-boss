"""`preflight` 점검 항목별 두 방향 — 통과·실패, 그리고 건너뛴 점검이 OK로 세지지 않는다 (#534).

가짜 세계는 `tests/preflight_fakes.py`다. 종료 코드·CLI·잠금 테스트는 `test_preflight_wiring.py`.
"""

import shlex
from pathlib import Path

import pytest

from src import cli
from src import preflight as pf
from src.adapters import model_swap
from src.adapters.canary_report import read_canary_report
from src.cli import Severity
from src.config import load_config
from src.interfaces.model_client import ModelSwapError
from tests.conftest import ALL_PROFILES
from tests.oracle_fakes import TASK_ID
from tests.preflight_fakes import (
    BOOT,
    COMMIT,
    DEVICE,
    ERROR,
    KEY,
    OK,
    PROFILE,
    SERVER,
    WARN,
    World,
    _bundle,
    _canary,
    _local_index_version,
    forbid_real_world,
    make_world,
    one,
    text_of,
)


@pytest.fixture(autouse=True)
def _no_real_world(monkeypatch: pytest.MonkeyPatch) -> None:
    forbid_real_world(monkeypatch)


@pytest.fixture
def world(tmp_path: Path) -> World:
    return make_world(tmp_path)


# --- 통과: 아홉 항목이 모두 OK ----------------------------------------------------------------


def test_a_clean_world_reports_every_item_ok_and_exits_zero(world: World) -> None:
    checks = world.check()

    assert [c.render() for c in checks if c.severity is not OK] == []
    for label in (
        "교대 환경변수:", "bash 해소:", "터널 포트:", "원격 커밋:", "채점기 서버:",
        "CYBERGYM_API_KEY:", "카나리:", "카나리 boot_id:", "과제 번들:", "도구 이미지:",
    ):  # fmt: skip
        assert one(checks, label).severity is OK
    assert pf.exit_code(checks) == 0
    # HARNESS_RUN_ID는 하네스가 run마다 주는 값이다 — 사람의 환경에 없어도 ERROR가 아니다.
    assert "HARNESS_RUN_ID" not in world.env


# --- 1. 교대 환경변수 --------------------------------------------------------------------------


@pytest.mark.parametrize("name", pf.SWAP_ENV_OPERATOR)
@pytest.mark.parametrize("value", [None, ""])
def test_a_missing_swap_variable_is_an_error_naming_it_and_no_ssh_is_opened(
    world: World, name: str, value: str | None
) -> None:
    if value is None:
        del world.env[name]
    else:
        world.env[name] = value

    checks = world.check()

    assert one(checks, "교대 환경변수:").severity is ERROR
    assert name in one(checks, "교대 환경변수:").message
    if name == "SWAP_REMOTE_PYTHON":  # ssh로 가는 데는 호스트·루트면 된다 — 커밋 점검은 그대로 돈다
        assert one(checks, "원격 커밋:").severity is OK
    else:
        assert "ssh" not in world.tools_called()
        assert one(checks, "원격 커밋:").severity is WARN  # 못 간 것을 OK로 세지 않는다


@pytest.mark.parametrize(
    "root",
    [
        "workspace/harness",
        "~/harness",
        "/root/harness/",
        "/",
        "//",
        "C:/Program Files/Git/root/harness",
    ],
)
def test_a_remote_root_that_swap_remote_would_refuse_is_an_error(world: World, root: str) -> None:
    world.env["SWAP_REMOTE_ROOT"] = root

    check = one(world.check(), "교대 환경변수:")

    assert check.severity is ERROR and "SWAP_REMOTE_ROOT" in check.message
    assert ("PowerShell" in check.message) == root.startswith(
        "C:"
    )  # MSYS가 바꾼 값이면 그 우회를 말한다


# --- 2. bash 해소 ------------------------------------------------------------------------------


def test_a_bash_that_resolves_to_the_wsl_launcher_is_an_error_with_the_fix(world: World) -> None:
    launcher = r"C:\Windows\System32\bash.exe"

    def refuse(command: tuple[str, ...]) -> tuple[str, ...]:
        raise ModelSwapError(
            f"교대 명령의 bash가 WSL 런처로 풀렸다({launcher}) — Git\\usr\\bin을 PATH 앞에"
        )

    probes = world.probes()
    checks = pf.preflight(
        PROFILE,
        device=DEVICE,
        probes=pf.Probes(run=probes.run, tcp=probes.tcp, resolve=refuse, environ=world.env),
    )

    check = one(checks, "bash 해소:")
    assert check.severity is ERROR and launcher in check.message and "PATH 앞" in check.message


def test_a_bare_bash_that_which_cannot_find_is_a_warning_not_a_pass(world: World) -> None:
    probes = world.probes()
    unresolved = pf.Probes(
        run=probes.run, tcp=probes.tcp, resolve=lambda command: command, environ=world.env
    )

    check = one(pf.preflight(PROFILE, device=DEVICE, probes=unresolved), "bash 해소:")

    assert check.severity is WARN and "못 찾았다" in check.message


def test_the_default_resolver_is_the_one_the_swap_adapter_runs() -> None:
    assert pf.Probes().resolve is model_swap._resolved  # 새로 짓지 않고 같은 판정을 재사용한다


# --- 3. 터널 포트 ------------------------------------------------------------------------------


def test_a_closed_tunnel_port_is_an_error_naming_that_port(world: World) -> None:
    world.listening.discard(("127.0.0.1", 8002))

    check = one(world.check(), "터널 포트:")

    assert check.severity is ERROR
    assert "127.0.0.1:8002" in check.message and "8000" not in check.message
    assert "vast-tunnel.sh" in check.message


def test_a_local_swap_device_does_not_need_the_tunnel_but_still_resolves_bash(world: World) -> None:
    checks = world.check(device="vastai")  # command = bash tools/serve/swap.sh — 래퍼가 아니다

    assert one(checks, "교대 환경변수·터널·원격 커밋:").severity is OK  # 해당 없음이라고 말한다
    assert one(checks, "bash 해소:").severity is OK
    assert not any(c.message.startswith(("터널 포트:", "원격 커밋:")) for c in checks)
    assert "ssh" not in world.tools_called()
    assert not [
        p for _, p in world.tcp_calls if p != 8666
    ]  # 서버는 교대 뒤에 뜬다 — 지금 두드리지 않는다


# --- 4. 원격 사본의 커밋 -----------------------------------------------------------------------


def test_a_remote_copy_from_another_commit_is_an_error_with_the_bootstrap_line(
    world: World,
) -> None:
    world.remote_commit = "b" * 40

    check = one(world.check(), "원격 커밋:")

    assert check.severity is ERROR and "vast-bootstrap.sh" in check.message
    assert (
        "b" * 40 in check.message and COMMIT in check.message
    )  # 어느 커밋이 갈렸는지 둘 다 보인다


@pytest.mark.parametrize("remote", ["", "   "])
def test_an_empty_remote_marker_is_an_error(world: World, remote: str) -> None:
    world.remote_commit = remote

    assert one(world.check(), "원격 커밋:").severity is ERROR


def test_an_ssh_failure_is_an_error_with_the_real_error_from_the_stderr_tail(world: World) -> None:
    world.ssh_exit = 255

    check = one(world.check(), "원격 커밋:")

    assert check.severity is ERROR and "255" in check.message
    assert "Connection refused" in check.message and "banner noise" not in check.message


def test_ssh_is_called_as_an_argv_list_with_a_timeout_and_the_host_after_the_end_of_options(
    world: World,
) -> None:
    world.env["SWAP_SSH_HOST"] = "-oProxyCommand=evil"
    world.env["SWAP_REMOTE_ROOT"] = "/root/it's here"
    world.env["SWAP_SSH_PORT"] = "40123"

    world.check()

    argv, timeout = next(call for call in world.calls if call[0][0] == "ssh")
    assert all(isinstance(part, str) for part in argv) and timeout > 0
    assert argv[argv.index("--") + 1] == "-oProxyCommand=evil"  # 옵션으로 읽히지 않는다
    assert argv[argv.index("-p") + 1] == "40123" and "BatchMode=yes" in argv
    assert shlex.split(argv[-1])[1] == "/root/it's here"  # 원격 셸에는 인용되어 한 인자로 간다


def test_every_subprocess_call_is_an_argv_list_with_a_positive_timeout(world: World) -> None:
    world.check()

    assert world.calls, "가짜 run이 한 번도 안 불렸다"
    assert all(isinstance(argv, list) and timeout > 0 for argv, timeout in world.calls)


def test_the_default_runner_never_uses_a_shell_and_always_passes_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def record(argv: object, **kwargs: object) -> None:
        seen.update(argv=argv, **kwargs)

    monkeypatch.setattr(pf.subprocess, "run", record)

    pf._run(["ssh", "-V"], 7.0)

    assert seen["argv"] == ["ssh", "-V"] and seen["timeout"] == 7.0
    assert not seen.get("shell")


# --- 5. 오라클 서버 ----------------------------------------------------------------------------


def test_an_unreachable_oracle_server_is_an_error(world: World) -> None:
    world.listening.discard(("127.0.0.1", 8666))

    check = one(world.check(), "채점기 서버:")

    assert check.severity is ERROR and SERVER in check.message


@pytest.mark.parametrize("server", ["127.0.0.1:8666", "http://0.0.0.0:8666", "http://h:8666/x"])
def test_an_oracle_address_the_client_cannot_use_is_an_error_without_touching_the_network(
    world: World, server: str
) -> None:
    check = one(world.check(oracle_server=server), "채점기 서버:")

    assert check.severity is ERROR and world.tcp_calls.count(("0.0.0.0", 8666)) == 0
    assert not any(host == "h" for host, _ in world.tcp_calls)


def test_the_default_tcp_probe_reports_a_refusal_as_a_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(pf.socket, "create_connection", refuse)

    assert "ConnectionRefusedError" in str(pf._tcp("127.0.0.1", 1))


# --- 6. CYBERGYM_API_KEY -----------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["", f"{KEY}\r", f"{KEY}\n", f"{KEY} x", f"{KEY}\x00"])
def test_a_missing_or_malformed_key_is_an_error_and_the_value_is_never_printed(
    world: World, bad: str
) -> None:
    world.env["CYBERGYM_API_KEY"] = bad

    checks = world.check()

    assert one(checks, "CYBERGYM_API_KEY:").severity is ERROR
    assert "SECRET" not in text_of(checks)  # 값은 접두 한 글자도 안 찍는다
    if bad:
        assert f"{len(bad)}자" in one(checks, "CYBERGYM_API_KEY:").message  # 길이만 말한다


def test_a_good_key_is_reported_by_length_only(world: World) -> None:
    checks = world.check()

    assert f"{len(KEY)}자" in one(checks, "CYBERGYM_API_KEY:").message
    assert "SECRET" not in text_of(checks) and KEY not in text_of(checks)


def test_the_key_shape_rule_is_the_one_the_run_refuses_on() -> None:
    for key in (KEY, f"{KEY}\r", f"{KEY} ", "a\tb", "中문", ""):
        assert cli._key_has_bad_chars(key) == any(
            ch.isspace() or not ch.isprintable() for ch in key
        )


# --- 7. 카나리 보고서 · boot_id ---------------------------------------------------------------


def test_the_canary_line_reports_task_verdict_and_bits(world: World) -> None:
    check = one(world.check(), "카나리:")

    assert check.severity is OK
    assert TASK_ID in check.message and "pass" in check.message and "28" in check.message


@pytest.mark.parametrize(
    "overrides",
    [
        {"verdict": "fail", "ok": False},
        {"mmap_rnd_bits": "32", "rnd_bits_above_28": True},
        {"task": "arvo:7"},
    ],
)
def test_a_report_the_run_would_refuse_is_an_error(world: World, overrides: dict) -> None:
    _canary(world.canary, **overrides)

    check = one(world.check(), "카나리:")

    assert check.severity is ERROR
    assert "카나리 boot_id:" not in text_of(
        world.check()
    )  # 거절된 보고서 위에 신선도를 따지지 않는다


def test_an_unreadable_report_is_an_error(world: World, tmp_path: Path) -> None:
    assert one(world.check(oracle_canary=tmp_path / "missing.json"), "카나리:").severity is ERROR


def test_a_masked_bundle_id_skips_the_task_match_and_says_so(world: World) -> None:
    _bundle(
        world.canary.parent, task_id="9c73e92e52b7"
    )  # 마스킹 id — 실 id는 --mask-map이 있어야 안다

    check = one(world.check(), "카나리:")

    assert check.severity is OK and "과제 대조 안 함" in check.message


def test_a_different_boot_id_is_an_error_telling_to_remeasure(world: World) -> None:
    world.boot_id = "99999999-2222-3333-4444-555555555555"

    check = one(world.check(), "카나리 boot_id:")

    assert check.severity is ERROR and "oracle_canary.py" in check.message


def test_a_docker_that_cannot_run_makes_boot_id_a_warning_not_an_error(world: World) -> None:
    world.docker_missing = True

    check = one(world.check(), "카나리 boot_id:")

    assert check.severity is WARN and "확인 못 함" in check.message


def test_a_boot_id_that_is_not_a_uuid_is_a_warning_not_a_match(world: World) -> None:
    world.boot_id = "cat: /proc/sys/kernel/random/boot_id: Permission denied"

    check = one(world.check(), "카나리 boot_id:")

    assert check.severity is WARN and "확인 못 함" in check.message


def test_a_report_without_a_boot_id_is_a_warning_and_docker_is_not_asked(world: World) -> None:
    _canary(world.canary, host_boot_id=None)

    check = one(world.check(), "카나리 boot_id:")

    assert check.severity is WARN and "host_boot_id" in check.message
    assert not any("cat" in argv for argv, _ in world.calls)


def test_the_report_reader_carries_the_host_boot_id_and_nothing_invented(tmp_path: Path) -> None:
    path = _canary(tmp_path / "a.json")
    assert read_canary_report(path).host_boot_id == BOOT
    assert read_canary_report(_canary(tmp_path / "b.json", host_boot_id=None)).host_boot_id is None
    assert read_canary_report(_canary(tmp_path / "c.json", host_boot_id=7)).host_boot_id is None


# --- 8. 과제 번들 ------------------------------------------------------------------------------


def test_a_bundle_without_submit_sh_is_an_error(world: World) -> None:
    (world.bundle / "submit.sh").unlink()

    check = one(world.check(), "과제 번들:")

    assert check.severity is ERROR and "submit.sh" in check.message


def test_a_bundle_with_an_answer_file_is_an_error_naming_it(world: World) -> None:
    (world.bundle / "patch.diff").write_text("x", encoding="utf-8")

    check = one(world.check(), "과제 번들:")

    assert check.severity is ERROR and "patch.diff" in check.message


def test_a_bundle_without_the_archive_or_an_identity_is_an_error(world: World) -> None:
    (world.bundle / "repo-vul.tar.gz").unlink()
    (world.bundle / "submit.sh").write_text("echo hi\n", encoding="utf-8")

    checks = [c for c in world.check() if c.message.startswith("과제 번들:")]

    assert len(checks) == 2 and all(c.severity is ERROR for c in checks)  # 둘 다 모아 보고한다


def test_a_task_dir_that_is_not_a_directory_is_an_error(world: World, tmp_path: Path) -> None:
    assert one(world.check(task_dir=tmp_path / "none"), "과제 번들:").severity is ERROR


# --- 9. 도구 이미지의 인덱서 버전 --------------------------------------------------------------


def test_an_image_with_an_older_indexer_is_an_error_naming_both_versions_and_the_rebuild(
    world: World,
) -> None:
    world.image_version = "index-v2"

    check = one(world.check(), "도구 이미지:")

    assert check.severity is ERROR
    assert "index-v2" in check.message and _local_index_version() in check.message
    assert "docker build" in check.message


def test_the_image_is_read_without_a_network_pull_and_without_a_shell(world: World) -> None:
    world.check()

    argv, _ = next(call for call in world.calls if "grep" in call[0])
    assert argv[:2] == ["docker", "run"] and "--network=none" in argv
    assert argv[argv.index("--pull") + 1] == "never"


@pytest.mark.parametrize("failure", ["missing", "exit", "silent"])
def test_an_image_that_cannot_be_read_is_an_error_not_a_pass(world: World, failure: str) -> None:
    if failure == "missing":
        world.docker_missing = True
    elif failure == "exit":
        world.docker_exit = 125
    else:
        world.image_version = ""  # grep이 줄을 못 찾은 것 같은 빈 출력 — 버전을 못 읽는다

    check = one(world.check(), "도구 이미지:")

    assert check.severity is ERROR
    assert failure != "exit" or "Unable to find image" in check.message


@pytest.mark.parametrize(("runner", "severity"), [("fake", OK), ("singularity", WARN)])
def test_a_runtime_without_a_docker_image_is_not_asked(
    world: World, monkeypatch: pytest.MonkeyPatch, runner: str, severity: Severity
) -> None:
    monkeypatch.setattr(
        pf,
        "load_profile",
        lambda profile, **kw: cli.load_profile(profile, tool_runner=runner, **kw),
    )

    check = one(world.check(), "도구 이미지:")

    assert check.severity is severity
    assert not any("grep" in argv for argv, _ in world.calls)


# --- 건너뜀 · 해당 없음 · 장비 --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "flag"),
    [
        ("채점기 서버:", "--oracle-server"),
        ("카나리:", "--oracle-canary"),
        ("과제 번들:", "--task-dir"),
        ("CYBERGYM_API_KEY:", "--oracle-server"),
    ],
)
def test_a_check_whose_argument_is_missing_is_a_skipped_warning_never_an_ok(
    world: World, label: str, flag: str
) -> None:
    checks = world.check(task_dir=None, oracle_server=None, oracle_canary=None)

    line = one(checks, label)
    assert line.severity is WARN and "건너뜀" in line.message and flag in line.message
    assert pf.exit_code(checks) == 0  # 못 본 것은 WARN이지 ERROR가 아니다 — 그러나 OK도 아니다
    assert 8666 not in [port for _, port in world.tcp_calls]
    assert not any("cat" in argv for argv, _ in world.calls)  # 보고서가 없으니 컨테이너도 안 돌린다


def test_without_a_device_a_swap_profile_is_an_error_with_the_runs_own_sentence(
    world: World,
) -> None:
    checks = world.check(device=None)

    assert one(checks, "교대 점검:").severity is ERROR
    assert cli._SWAP_NEEDS_DEVICE in one(checks, "교대 점검:").message
    assert pf.exit_code(checks) == pf.EXIT_ERRORS


def test_a_profile_without_swap_has_no_swap_checks_and_says_so(world: World) -> None:
    no_swap = next(p for p in ALL_PROFILES if load_config(p).swap is None)

    checks = pf.preflight(no_swap, device=None, probes=world.probes())

    assert (
        one(checks, "교대 점검:").severity is OK
        and "해당 없음" in one(checks, "교대 점검:").message
    )
    assert "ssh" not in world.tools_called() and not world.tcp_calls


def test_an_unknown_profile_is_one_error_line_and_exit_two(world: World) -> None:
    checks = pf.preflight("no-such-profile", probes=world.probes())

    assert [c.severity for c in checks] == [ERROR] and pf.exit_code(checks) == 2


def test_a_broken_device_file_is_an_error_and_the_swap_checks_are_skipped_visibly(
    world: World,
) -> None:
    checks = world.check(device="no-such-device")

    assert any(c.severity is ERROR and "장비 파일" in c.message for c in checks)
    assert one(checks, "교대 점검:").severity is WARN
    assert "ssh" not in world.tools_called()
