"""`preflight` 조립 — 한 번에 전부 보고 · 종료 코드 · CLI · 스크립트·설정 잠금 (#534).

잠금 테스트는 `swap-remote.sh`·`vast-tunnel.sh`·설정이 바뀌면 빨개진다. 가짜 세계는
`tests/preflight_fakes.py`다.
"""

from pathlib import Path

import pytest

from src import cli
from src import preflight as pf
from src.adapters import model_swap
from src.adapters.index_graph import SUPPORTED_INDEX_VERSIONS
from src.cli import Check
from src.config import load_config, load_serving
from tests.preflight_fakes import (
    DEVICE,
    ERROR,
    OK,
    PROFILE,
    ROOT,
    SERVER,
    WARN,
    World,
    _local_index_version,
    _script_needs,
    _script_ports,
    forbid_real_world,
    make_world,
    one,
)


@pytest.fixture(autouse=True)
def _no_real_world(monkeypatch: pytest.MonkeyPatch) -> None:
    forbid_real_world(monkeypatch)


@pytest.fixture
def world(tmp_path: Path) -> World:
    return make_world(tmp_path)


# --- 한 번에 전부 · 종료 코드 --------------------------------------------------------------------


def test_every_failure_is_reported_together_not_just_the_first(world: World) -> None:
    world.env["SWAP_SSH_HOST"] = ""
    world.listening.clear()
    world.boot_id = "99999999-2222-3333-4444-555555555555"
    world.env["CYBERGYM_API_KEY"] = ""
    world.image_version = "index-v2"
    (world.bundle / "submit.sh").unlink()

    checks = world.check()

    failed = {c.message.split(":")[0] for c in checks if c.severity is ERROR}
    assert failed >= {
        "교대 환경변수", "터널 포트", "채점기 서버", "CYBERGYM_API_KEY", "카나리 boot_id",
        "과제 번들", "도구 이미지",
    }  # fmt: skip
    assert pf.exit_code(checks) == pf.EXIT_ERRORS


def test_one_check_that_crashes_is_an_error_and_the_rest_still_run(world: World) -> None:
    def explode(host: str, port: int) -> str | None:
        raise RuntimeError("probe bug")

    probes = world.probes()
    crashing = pf.Probes(run=probes.run, tcp=explode, resolve=probes.resolve, environ=world.env)

    checks = pf.preflight(
        PROFILE, device=DEVICE, task_dir=world.bundle, oracle_server=SERVER, probes=crashing
    )

    assert (
        one(checks, "터널 포트:").severity is ERROR
        and "RuntimeError" in one(checks, "터널 포트:").message
    )
    assert one(checks, "도구 이미지:").severity is OK  # 뒤 항목은 돌았다


def test_exit_code_is_nonzero_only_for_an_error() -> None:
    assert pf.exit_code([Check(OK, "a"), Check(WARN, "b")]) == 0
    assert pf.exit_code([Check(OK, "a"), Check(ERROR, "b")]) == pf.EXIT_ERRORS == 2


def test_the_summary_counts_by_severity_and_is_never_a_bare_ok_when_something_is_unverified() -> (
    None
):
    clean = pf.summary([Check(OK, "a")])
    skipped = pf.summary([Check(OK, "a"), Check(WARN, "b")])
    broken = pf.summary([Check(ERROR, "x"), Check(ERROR, "y"), Check(WARN, "b")])

    assert clean.severity is OK and skipped.severity is WARN and broken.severity is ERROR
    assert "ERROR 2건" in broken.message and "WARN 1건" in broken.message


# --- CLI ---------------------------------------------------------------------------------------


def _argv(world: World) -> list[str]:
    return [
        "preflight", "--profile", PROFILE, "--device", DEVICE, "--task-dir", str(world.bundle),
        "--oracle-server", SERVER, "--oracle-canary", str(world.canary),
    ]  # fmt: skip


def test_cli_exits_zero_and_prints_doctor_shaped_lines_when_everything_passes(
    world: World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(pf, "Probes", world.probes)

    code = cli.main(_argv(world))

    out = capsys.readouterr().out
    assert code == 0, out
    assert out.splitlines()[0].startswith("[OK   ] ") and "[ERROR]" not in out
    assert out.splitlines()[-1].startswith("[OK   ] preflight:")


def test_cli_exits_two_when_one_check_is_an_error(
    world: World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    world.remote_commit = "b" * 40
    monkeypatch.setattr(pf, "Probes", world.probes)

    code = cli.main(_argv(world))

    out = capsys.readouterr().out
    assert code == 2 and "[ERROR] 원격 커밋:" in out
    assert out.splitlines()[-1].startswith("[ERROR] preflight:")


def test_cli_requires_a_profile() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["preflight"])
    assert exc.value.code == 2


def test_preflight_takes_no_flag_it_would_ignore() -> None:
    options = {
        opt
        for action in cli.build_parser()._subparsers._group_actions[0].choices["preflight"]._actions
        for opt in action.option_strings
    }
    assert options == {
        "-h",
        "--help",
        "--profile",
        "--device",
        "--task-dir",
        "--oracle-server",
        "--oracle-canary",
    }


# --- 잠금: 스크립트·설정이 바뀌면 빨개진다 --------------------------------------------------------


def test_the_required_swap_variables_are_the_ones_swap_remote_needs() -> None:
    text = (ROOT / "tools/serve/swap-remote.sh").read_text(encoding="utf-8")

    needs = _script_needs(text)

    assert needs, "swap-remote.sh에서 `need` 줄을 못 읽었다 — 읽는 정규식이 낡았다"
    assert set(pf.SWAP_ENV_OPERATOR) | set(pf.SWAP_ENV_HARNESS) == needs
    assert not set(pf.SWAP_ENV_OPERATOR) & set(pf.SWAP_ENV_HARNESS)


def test_the_need_reader_bites_when_the_script_gains_a_variable() -> None:
    text = (ROOT / "tools/serve/swap-remote.sh").read_text(encoding="utf-8")
    drifted = text.replace(
        "case $SWAP_REMOTE_ROOT", 'need SWAP_NEW_THING "x"\ncase $SWAP_REMOTE_ROOT', 1
    )

    assert _script_needs(drifted) - _script_needs(text) == {"SWAP_NEW_THING"}


def test_the_harness_supplies_exactly_the_variables_preflight_does_not_ask_the_operator_for() -> (
    None
):
    import inspect

    source = inspect.getsource(model_swap.swap_runner)  # 문자열 핀이다 — 철자가 바뀌면 빨개진다

    assert all(f'"{name}"' in source for name in pf.SWAP_ENV_HARNESS)
    assert not any(f'"{name}"' in source for name in pf.SWAP_ENV_OPERATOR)


def test_the_tunnel_ports_come_from_the_config_and_equal_the_tunnel_scripts() -> None:
    config = load_config(PROFILE)
    endpoints = pf.tunnel_endpoints(config, load_serving(DEVICE))

    assert {port for _, port in endpoints} == _script_ports()
    assert {host for host, _ in endpoints} == {"127.0.0.1"}


def test_the_local_indexer_version_is_readable_and_a_version_the_reader_supports() -> None:
    version = pf._local_index_version(ROOT)

    assert version == _local_index_version()
    assert version in SUPPORTED_INDEX_VERSIONS  # 낡은 것도 받아 주는 목록이라 「같다」를 따로 본다


def test_preflight_reads_the_canary_report_but_never_runs_the_canary_tool(world: World) -> None:
    """`test_oracle_canary`의 「호출 지점이 없다」 명제가 이 모듈 때문에 거짓이 되지 않는다."""
    world.check()

    assert not [a for argv, _ in world.calls for a in argv if "oracle_canary" in a]
    source = Path(pf.__file__).read_text(encoding="utf-8")
    assert "import oracle_canary" not in source and "from tools" not in source
