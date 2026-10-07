"""`preflight` 리뷰 반영(H1·H2·C1~C3·M1~M5)을 **성질로** 문다 — 핀만 남긴 변이가 죽게 (#534).

각 테스트는 되돌리는 한 줄 변이를 넣어 빨개지는 것을 확인한 것들이다(변이 표는 PR 보고).
가짜 세계는 `tests/preflight_fakes.py`다. `world` 픽스처가 진짜 프로세스·소켓을 막는다 — 그것을
안 쓰는 테스트(`_run`·`_tcp` 단위)는 자기가 가짜를 단다.
"""

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from src import cli
from src import preflight as pf
from src import preflight_oracle as po
from src import preflight_probes as pp
from src import preflight_remote as pr
from src.cli import Check
from tests import preflight_fakes as fk
from tests.preflight_fakes import (
    BOOT,
    COMMIT,
    DEVICE,
    ERROR,
    KEY,
    OK,
    PROFILE,
    ROOT,
    UNSWAPPED_PROFILE,
    WARN,
    World,
    assert_docker_safe,
    fill_checksums,
    forbid_real_world,
    make_world,
    one,
    text_of,
)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    forbid_real_world(monkeypatch)
    fill_checksums(monkeypatch)
    return make_world(tmp_path)


@pytest.fixture
def hybrid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    """교대를 선언하지 않는 실서빙 프로필 — 리뷰가 `[OK]` exit 0을 재현한 것이다."""
    forbid_real_world(monkeypatch)
    fill_checksums(monkeypatch)
    return make_world(tmp_path, UNSWAPPED_PROFILE)


def _served_ports() -> set[int]:
    config = fk.load_config(UNSWAPPED_PROFILE)
    return {port for _, port in pr._endpoints(config, tuple(config.served_models().values()))[0]}


# --- H1. 도구 이미지: 라벨이 같아도 코드가 다르면 걸린다 -----------------------------------------


def test_the_same_label_with_different_indexer_code_is_an_error_naming_both_hashes(
    world: World,
) -> None:
    world.image_sha = "0" * 64  # 같은 INDEX_VERSION · 다른 코드

    check = one(world.check(), "도구 이미지:")

    assert check.severity is ERROR and "같은" in check.message
    assert "0" * 12 in check.message and fk._local_indexer_sha()[:12] in check.message
    assert "docker build" in check.message


def test_a_matching_hash_is_ok_and_says_only_the_indexer_was_compared(world: World) -> None:
    check = one(world.check(), "도구 이미지:")

    assert check.severity is OK and fk._local_indexer_sha()[:12] in check.message
    assert "인덱서 한 파일만" in check.message  # 룰셋·gdb.batch는 안 봤다고 말한다


def test_label_and_hash_are_read_in_one_docker_run(world: World) -> None:
    world.check()

    runs = [argv for argv, _ in world.calls if "python3" in argv or "grep" in argv]
    assert len(runs) == 1 and "python3" in runs[0] and "/opt/indexer.py" in runs[0]


def test_an_image_without_python3_falls_back_to_the_label_and_warns_label_only(
    world: World,
) -> None:
    world.python_missing = True

    check = one(world.check(), "도구 이미지:")

    assert check.severity is WARN and "라벨만 봤다" in check.message  # 점검 수단이 없다 ≠ 어긋났다
    assert any("grep" in argv for argv, _ in world.calls)


def test_a_different_label_stays_an_error_even_when_the_hash_cannot_be_read(world: World) -> None:
    world.python_missing = True
    world.image_version = "index-v2"

    assert one(world.check(), "도구 이미지:").severity is ERROR


def test_the_local_hash_comes_from_the_repo_root_it_is_given(world: World, tmp_path: Path) -> None:
    fake_root = tmp_path / "other"
    (fake_root / "tools/harness-tools").mkdir(parents=True)
    real = (ROOT / "tools/harness-tools/indexer.py").read_bytes()
    (fake_root / "tools/harness-tools/indexer.py").write_bytes(real + b"# edited\n")

    check = one(world.check(repo_root=fake_root), "도구 이미지:")

    assert check.severity is ERROR  # 이미지는 진짜 사본, 이 루트의 사본은 한 줄 다르다


# --- H2. 교대 없는 실서빙 프로필은 서버 포트를 두드린다 ------------------------------------------


def test_every_served_port_of_an_unswapped_profile_is_probed(hybrid: World) -> None:
    hybrid.check()

    probed = {port for host, port in hybrid.tcp_calls if host == "127.0.0.1" and port != 8666}
    assert probed == _served_ports() and len(probed) >= 7


@pytest.mark.parametrize("port", sorted(_served_ports()))
def test_closing_any_one_served_port_is_an_error_naming_only_that_port(
    hybrid: World, port: int
) -> None:
    hybrid.listening.discard(("127.0.0.1", port))

    checks = hybrid.check()

    check = one(checks, "서빙 엔드포인트:")
    assert check.severity is ERROR and f"127.0.0.1:{port}" in check.message
    assert all(f"127.0.0.1:{other}(" not in check.message for other in _served_ports() - {port})
    assert pf.exit_code(checks) == pf.EXIT_ERRORS


def test_the_review_repro_all_servers_dead_is_no_longer_ok_exit_zero(hybrid: World) -> None:
    hybrid.listening.clear()
    hybrid.listening.add(("127.0.0.1", 8666))  # 채점기 하나만 산다 — 리뷰가 본 TCP 호출 하나

    checks = hybrid.check()

    assert pf.exit_code(checks) == pf.EXIT_ERRORS
    assert pf.summary(checks).severity is ERROR


def test_an_unswapped_profile_with_every_server_up_is_green(hybrid: World) -> None:
    checks = hybrid.check()

    assert [c.render() for c in checks if c.severity is not OK] == []
    assert pf.exit_code(checks, strict=True) == 0
    assert (
        "어느 모델이 떠 있는지" in one(checks, "서빙 엔드포인트:").message
    )  # 포트만 봤다고 말한다


def test_a_swap_profile_does_not_probe_serving_ports_the_swap_will_start(world: World) -> None:
    checks = world.check(device="vastai")  # 교대 명령이 서버를 띄운다 — 지금 두드리지 않는다

    assert not any(c.message.startswith("서빙 엔드포인트:") for c in checks)


# --- C1. 래퍼는 철자가 아니라 같은 파일로 알아본다 ----------------------------------------------


_WRAPPER_SPELLINGS = [
    "[bash, ./tools/serve/swap-remote.sh]",
    "[bash, tools/serve//swap-remote.sh]",
    "[bash, tools/../tools/serve/swap-remote.sh]",
    f'[bash, "{(ROOT / "tools/serve/swap-remote.sh").as_posix()}"]',
]


@pytest.mark.parametrize("command", _WRAPPER_SPELLINGS)
def test_a_wrapper_spelled_another_way_is_still_the_wrapper(
    world: World, tmp_path: Path, command: str
) -> None:
    checks = world.check(config_dir=fk.config_with_command(tmp_path, command))

    assert one(checks, "교대 환경변수:").severity is OK  # 점검이 사라지지 않았다
    assert one(checks, "터널 포트:").severity is OK and one(checks, "원격 커밋:").severity is OK
    assert not any("해당 없음" in c.message and "swap-remote" in c.message for c in checks)


def test_a_missing_variable_is_still_caught_when_the_wrapper_is_spelled_oddly(
    world: World, tmp_path: Path
) -> None:
    del world.env["SWAP_SSH_HOST"]

    checks = world.check(config_dir=fk.config_with_command(tmp_path, _WRAPPER_SPELLINGS[0]))

    assert one(checks, "교대 환경변수:").severity is ERROR  # 거짓 「해당 없음」 exit 0이 아니다
    assert pf.exit_code(checks) == pf.EXIT_ERRORS


def test_a_command_that_does_not_name_the_wrapper_says_what_it_actually_checked(
    world: World, tmp_path: Path
) -> None:
    checks = world.check(
        config_dir=fk.config_with_command(tmp_path, "[bash, tools/serve/swap.sh]"), device=DEVICE
    )

    check = one(checks, "교대 환경변수·터널·원격 커밋:")
    assert check.severity is OK and "같은 파일이 없다" in check.message
    assert "감싸면" in check.message  # 다른 스크립트가 래퍼를 감싸면 못 알아본다는 한계를 적는다


def test_wrapper_detection_resolves_against_the_repo_root_not_the_cwd(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "tools/serve").mkdir(parents=True)
    (root / "tools/serve/swap-remote.sh").write_text("#!/bin/bash\n", encoding="utf-8")

    assert pr.uses_remote_wrapper(("bash", "tools/serve/swap-remote.sh"), root)
    assert not pr.uses_remote_wrapper(("bash", "tools/serve/swap-remote.sh"), tmp_path / "none")
    assert not pr.uses_remote_wrapper(("bash", "-c", "x\0y", ""), root)  # NUL·빈 인자도 죽지 않는다


@pytest.mark.skipif(os.name != "nt", reason="대소문자를 안 가리는 파일 시스템에서만")
def test_wrapper_detection_ignores_case_on_a_case_insensitive_filesystem() -> None:
    assert pr.uses_remote_wrapper(("bash", "Tools/Serve/SWAP-REMOTE.sh"), ROOT)


# --- C2. API 키는 repr·출력·자식 환경에 없다 ------------------------------------------------------


def _all_prefixes(key: str) -> list[str]:
    return [key[:n] for n in range(2, len(key) + 1)]  # 앞 두 글자만 찍어도 걸린다


def test_the_key_is_not_in_the_probes_repr(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CYBERGYM_API_KEY", KEY)

    for shown in (repr(pp.Probes()), repr(world.probes()), str(world.probes())):
        assert not [p for p in _all_prefixes(KEY) if p in shown]


@pytest.mark.parametrize("bad", ["", f"{KEY}\r", f"{KEY} x", KEY])
def test_no_prefix_of_the_key_is_printed_in_any_line(world: World, bad: str) -> None:
    world.env["CYBERGYM_API_KEY"] = bad

    out = text_of(world.check())

    assert not [p for p in _all_prefixes(KEY) if p in out]


def test_no_prefix_of_the_key_reaches_the_cli_output(
    world: World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(pf, "Probes", world.probes)

    cli.main(_argv(world))

    out = capsys.readouterr()
    assert not [p for p in _all_prefixes(KEY) if p in out.out + out.err]


def test_the_child_environment_has_no_api_key_but_keeps_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CYBERGYM_API_KEY", KEY)
    monkeypatch.setenv("PREFLIGHT_MARK", "kept")
    seen: dict[str, object] = {}

    def record(argv: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(pp.subprocess, "run", record)

    pp._run(["ssh", "-V"], 5.0)

    env = seen["env"]
    assert isinstance(env, dict) and env.get("PREFLIGHT_MARK") == "kept"
    assert "CYBERGYM_API_KEY" not in env and KEY not in env.values()


def test_a_real_child_process_does_not_inherit_the_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CYBERGYM_API_KEY", KEY)
    code = "import json, os; print(json.dumps(dict(os.environ)))"

    done = pp._run([sys.executable, "-c", code], 60.0)  # 진짜 자식이다

    child = json.loads(done.stdout)
    assert "CYBERGYM_API_KEY" not in {k.upper() for k in child} and KEY not in child.values()


# --- C3. --strict ------------------------------------------------------------------------------


def _argv(world: World, *extra: str) -> list[str]:
    return [
        "preflight", "--profile", world.profile, "--device", DEVICE,
        "--task-dir", str(world.bundle), "--oracle-server", fk.SERVER,
        "--oracle-canary", str(world.canary), *extra,
    ]  # fmt: skip


def test_skips_only_exit_zero_by_default_and_four_under_strict(
    world: World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(pf, "Probes", world.probes)
    only = ["preflight", "--profile", PROFILE, "--device", DEVICE]  # 이슈 #533·#534의 그 호출

    assert cli.main(only) == 0
    plain = capsys.readouterr().out
    assert (
        "건너뜀" in plain and "--strict" in plain.splitlines()[-1]
    )  # 마지막 줄이 자동화에게 말한다
    assert cli.main([*only, "--strict"]) == pf.EXIT_UNVERIFIED == 4
    assert "exit 4" in capsys.readouterr().out.splitlines()[-1]


def test_strict_with_nothing_unverified_exits_zero_and_an_error_still_wins(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pf, "Probes", world.probes)

    assert cli.main(_argv(world, "--strict")) == 0
    world.remote_commit = "b" * 40
    assert cli.main(_argv(world, "--strict")) == pf.EXIT_ERRORS


def test_the_unverified_exit_code_collides_with_no_code_the_cli_returns() -> None:
    tree = ast.parse((ROOT / "src/cli.py").read_text(encoding="utf-8"))
    entry_points = {"main", "run", "bench", "gc"}  # 명령이 종료 코드를 돌려주는 함수들
    returned = {
        node.value
        for func in ast.walk(tree)
        if isinstance(func, ast.FunctionDef) and func.name in entry_points
        for ret in ast.walk(func)
        if isinstance(ret, ast.Return) and ret.value is not None
        for node in ast.walk(ret.value)  # `return 1 if … else 0`의 두 값도 센다
        if isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
    }

    assert returned == {0, 1, 2}  # 전수 — 새 값이 생기면 이 표를 다시 본다
    assert (
        pf.EXIT_UNVERIFIED not in returned | {3} and pf.EXIT_UNVERIFIED < 128
    )  # 3은 run의 장애 중단


def test_help_tells_automation_to_use_strict() -> None:
    sub = cli.build_parser()._subparsers._group_actions[0].choices["preflight"]
    text = sub.format_help()

    assert "--strict" in text and "exit 4" in text and "자동화" in text


# --- M1. 카나리: 지금 vm.mmap_rnd_bits를 직접 읽는다 ----------------------------------------------


@pytest.mark.parametrize(
    ("bits", "severity"), [("28", OK), ("12", OK), ("29", ERROR), ("32", ERROR)]
)
def test_the_current_mmap_rnd_bits_is_read_and_compared_to_the_canary_limit(
    world: World, bits: str, severity: object
) -> None:
    world.mmap_rnd_bits = bits

    check = one(world.check(), "카나리 mmap_rnd_bits:")

    assert check.severity is severity and bits in check.message


def test_a_report_that_says_28_does_not_hide_a_vm_that_went_back_to_32(world: World) -> None:
    world.mmap_rnd_bits = "32"  # boot_id는 그대로 — sysctl만 되돌아간 경우

    checks = world.check()

    assert one(checks, "카나리 boot_id:").severity is OK  # 대리값은 속는다
    assert pf.exit_code(checks) == pf.EXIT_ERRORS  # 직접 읽은 값이 막는다


@pytest.mark.parametrize("bits", [None, "twenty-eight", "-1", "2_8"])
def test_an_unreadable_or_malformed_current_value_is_a_warning_not_a_pass(
    world: World, bits: str | None
) -> None:
    world.mmap_rnd_bits = bits

    assert one(world.check(), "카나리 mmap_rnd_bits:").severity is WARN


def test_bits_and_boot_id_come_from_one_docker_run(world: World) -> None:
    world.check()

    runs = [argv for argv, _ in world.calls if "cat" in argv]
    assert len(runs) == 1
    assert "/proc/sys/vm/mmap_rnd_bits" in runs[0] and "/proc/sys/kernel/random/boot_id" in runs[0]


def test_the_limit_is_the_one_the_run_gate_uses() -> None:
    from src.adapters import canary_report

    assert po.MMAP_RND_BITS_MAX == canary_report.MMAP_RND_BITS_MAX == 28


def test_a_boot_id_match_does_not_claim_there_was_no_restart(world: World) -> None:
    check = one(world.check(), "카나리 boot_id:")

    assert check.severity is OK and "실측 0건" in check.message
    assert "재기동이 없었다는 뜻" not in po.__doc__ and "재기동이 없었다는 뜻" not in pf.__doc__


@pytest.mark.parametrize(
    "recorded",
    [
        BOOT[:-1] + "6",  # 마지막 한 글자만 다르다
        BOOT[:8],  # 앞 8자
        BOOT[:12],  # 앞 12자
        BOOT + "0",  # 뒤에 붙었다
        BOOT.upper(),  # 대소문자
        f" {BOOT}",  # 앞 공백
        f"{BOOT} ",  # 뒤 공백
    ],
)
def test_the_boot_id_comparison_is_exact(world: World, recorded: str) -> None:
    fk._canary(world.canary, host_boot_id=recorded)

    check = one(world.check(), "카나리 boot_id:")

    assert check.severity is ERROR


# --- M2. run이 거부하는데 입력이 이미 있는 것 -----------------------------------------------------


@pytest.mark.parametrize(
    ("given", "missing_label"),
    [
        ({"oracle_canary": None, "task_dir": None}, ("카나리:", "과제 번들:")),
        ({"oracle_server": None, "task_dir": None}, ("채점기 서버:", "과제 번들:")),
        ({"oracle_canary": None}, ("카나리:",)),
        ({"oracle_server": None}, ("채점기 서버:",)),
    ],
)
def test_a_partial_oracle_group_is_an_error_because_run_refuses_that_combination(
    world: World, given: dict, missing_label: tuple[str, ...]
) -> None:
    checks = world.check(**given)

    for label in missing_label:
        assert one(checks, label).severity is ERROR and "거부된다" in one(checks, label).message
    assert pf.exit_code(checks) == pf.EXIT_ERRORS


def test_a_bundle_alone_is_not_an_oracle_run_so_the_oracle_lines_only_warn(world: World) -> None:
    checks = world.check(oracle_server=None, oracle_canary=None)

    assert one(checks, "채점기 서버:").severity is WARN and pf.exit_code(checks) == 0


def test_a_placeholder_model_checksum_is_reported_with_the_doctors_own_judgment(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pf, "_check_checksums", fk.REAL_CHECKSUMS)

    check = one(world.check(), "모델 checksum:")

    assert check.severity is WARN and "자리표시자" in check.message and "비교표" in check.message


def test_a_bundle_address_that_differs_from_the_oracle_server_is_an_error(world: World) -> None:
    fk._bundle(
        world.canary.parent, server="http://localhost:8666"
    )  # run은 이것을 exit 2로 거부한다

    check = one(world.check(), "과제 번들:")

    assert check.severity is ERROR and "localhost" in check.message and "127.0.0.1" in check.message


def test_the_same_address_written_with_case_and_a_trailing_slash_is_the_same_origin(
    world: World,
) -> None:
    check = one(world.check(oracle_server="HTTP://127.0.0.1:8666/"), "과제 번들:")

    assert check.severity is OK


def test_a_canary_report_inside_the_task_tree_is_an_error(world: World) -> None:
    inside = fk._canary(world.bundle / "canary.json")  # 번들 안 — 신뢰 경계 밖이다
    shutil.copy(world.canary, inside)

    check = one(world.check(oracle_canary=inside), "카나리:")

    assert check.severity is ERROR and "과제 트리 안" in check.message


def test_an_archive_that_is_an_lfs_pointer_is_an_error(world: World) -> None:
    (world.bundle / "repo-vul.tar.gz").write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:00\nsize 1\n", encoding="utf-8"
    )

    check = one(world.check(), "과제 번들:")

    assert check.severity is ERROR and "repo-vul.tar.gz" in check.message and "LFS" in check.message


def test_an_empty_archive_is_an_error(world: World) -> None:
    import tarfile

    with tarfile.open(world.bundle / "repo-vul.tar.gz", "w:gz"):
        pass

    assert one(world.check(), "과제 번들:").severity is ERROR


def test_a_bundle_checksum_made_with_another_salt_is_an_error(world: World) -> None:
    fk._bundle(world.canary.parent, checksum="0" * 64)

    check = one(world.check(), "과제 번들:")

    assert check.severity is ERROR and "salt" in check.message


def test_a_task_family_the_server_refuses_is_an_error(world: World) -> None:
    fk._bundle(world.canary.parent, task_id="oss-fuzz-latest:123")

    assert one(world.check(), "과제 번들:").severity is ERROR


@pytest.mark.parametrize("strict", [False, True])
def test_every_summary_says_it_does_not_repeat_all_of_runs_refusals(strict: bool) -> None:
    for checks in ([Check(OK, "a")], [Check(WARN, "a")], [Check(ERROR, "a")]):
        message = pf.summary(checks, strict=strict).message

        assert "run의 거부 전부를 되풀이하지 않는다" in message and "`doctor`도 돌린다" in message


# --- M3. 외부 문자열은 검증하고 찍는다 -----------------------------------------------------------


def test_a_hostile_remote_commit_is_shown_as_a_short_repr_without_an_escape(world: World) -> None:
    world.remote_commit = "\x1b[2J\x1b[31m" + "A" * 5000

    checks = world.check()

    line = one(checks, "원격 커밋:")
    assert line.severity is ERROR and "\x1b" not in line.message and len(line.message) < 400
    assert "\x1b" not in pp.printable(line.render())


@pytest.mark.parametrize("suffix", [" ", "\r", "\t", "x"])
def test_a_remote_commit_with_trailing_whitespace_is_refused_like_the_script_refuses(
    world: World, suffix: str
) -> None:
    world.remote_commit = COMMIT + suffix  # `[ "$got" = "$want" ]`는 이것을 거부한다

    assert one(world.check(), "원격 커밋:").severity is ERROR


def test_trailing_newlines_are_stripped_like_command_substitution(world: World) -> None:
    world.remote_commit = COMMIT + "\n\n"  # `$(cat …)`가 끝 개행은 뗀다

    assert one(world.check(), "원격 커밋:").severity is OK


def test_a_local_commit_that_is_not_forty_hex_is_not_compared_or_echoed(world: World) -> None:
    world.commit = "\x1b[31mnot-a-commit"

    check = one(world.check(), "원격 커밋:")

    assert check.severity is ERROR and "\x1b" not in check.message


def test_a_bundle_task_id_with_an_escape_sequence_is_not_echoed_raw(world: World) -> None:
    fk._bundle(world.canary.parent, task_id="x\x1b[31m:1")

    out = text_of(world.check())

    assert "\x1b" not in out


def test_shown_passes_good_values_and_reprs_the_rest() -> None:
    import re

    shape = re.compile(r"[0-9a-f]{40}")

    assert pp.shown(COMMIT, shape) == COMMIT
    assert pp.shown("ok value") == "ok value"
    assert pp.shown("a\x1bb") == repr("a\x1bb")
    assert pp.shown("x" * 5000) == repr("x" * pp.SHOWN_MAX)
    assert pp.shown(COMMIT + " ", shape) == repr(COMMIT + " ")


def test_the_output_chokepoint_escapes_control_characters_but_keeps_korean() -> None:
    assert pp.printable("a\x1b[31m\nb\r한글") == "a\\x1b[31m\\nb\\r한글"


def test_hostile_ssh_stderr_never_reaches_the_terminal(
    world: World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(fk, "_SSH_STDERR", "ssh: \x1b]0;pwned\x07 connect refused")
    world.ssh_exit = 255
    monkeypatch.setattr(pf, "Probes", world.probes)

    cli.main(_argv(world))

    out = capsys.readouterr().out
    assert "\x1b" not in out and "\x07" not in out and "connect refused" in out


# --- M4. 성질을 무는 나머지 ---------------------------------------------------------------------


@pytest.mark.parametrize("port", sorted(fk._script_ports()))
def test_closing_any_one_tunnel_port_is_an_error_naming_that_port(world: World, port: int) -> None:
    world.listening.discard(("127.0.0.1", port))

    check = one(world.check(), "터널 포트:")

    assert check.severity is ERROR and f"127.0.0.1:{port}" in check.message


def test_every_docker_call_carries_the_safe_arguments(world: World) -> None:
    world.check()

    docker = [argv for argv, _ in world.calls if argv[0] == "docker"]
    assert len(docker) >= 2  # 호스트 읽기 · 도구 이미지
    for argv in docker:
        assert_docker_safe(argv)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda a: [x for x in a if x != "--network=none"],
        lambda a: [x for x in a if x != "--rm"],
        lambda a: [*a[:2], "-v", "/:/host", *a[2:]],
        lambda a: [x if x != "never" else "always" for x in a],
    ],
)
def test_the_docker_safety_assertion_itself_bites(mutate: object) -> None:
    safe = pp.docker_argv("docker", fk.DEFAULT_IMAGE, "cat", "/x")
    assert_docker_safe(safe)

    with pytest.raises(AssertionError):
        assert_docker_safe(mutate(safe))  # type: ignore[operator]


def test_every_external_call_carries_a_positive_finite_timeout(world: World) -> None:
    world.check()

    by_tool = {argv[0]: timeout for argv, timeout in world.calls}
    assert by_tool["git"] == pp.GIT_TIMEOUT_S and by_tool["ssh"] == pp.SSH_TIMEOUT_S
    assert by_tool["docker"] == pp.DOCKER_TIMEOUT_S
    assert all(0 < timeout <= 600 for _, timeout in world.calls)
    ssh = next(argv for argv, _ in world.calls if argv[0] == "ssh")
    assert f"ConnectTimeout={pp.SSH_CONNECT_TIMEOUT_S}" in ssh and "BatchMode=yes" in ssh


def test_the_default_tcp_probe_passes_a_positive_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class _Sock:
        def __enter__(self) -> "_Sock":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def connect(address: object, **kwargs: object) -> _Sock:
        seen.update(address=address, **kwargs)
        return _Sock()

    monkeypatch.setattr(pp.socket, "create_connection", connect)

    assert pp._tcp("127.0.0.1", 9) is None
    assert seen["address"] == ("127.0.0.1", 9) and 0 < seen["timeout"] <= 60  # type: ignore[operator]


def test_git_is_asked_about_the_repo_root_not_the_working_directory(
    world: World, tmp_path: Path
) -> None:
    root = tmp_path / "repo"
    (root / "tools/serve").mkdir(parents=True)
    shutil.copy(ROOT / "tools/serve/swap-remote.sh", root / "tools/serve/swap-remote.sh")

    world.check(repo_root=root)

    git = next(argv for argv, _ in world.calls if argv[0] == "git")
    assert git[:3] == ["git", "-C", str(root)] and git[3:] == ["rev-parse", "HEAD"]


# --- M5. 산문: 「접속하는 것은 bench 하나다」가 되돌아오지 않는다 -------------------------------


def test_no_live_prose_still_says_bench_is_the_only_connecting_command() -> None:
    stale = (
        "접속하는 유일한 커맨드",
        "접속하는 유일한 경로",
        "나머지 커맨드는 여전히 소켓을 열지",
        "요청을 보내는 유일한",  # `run`도 서버에 요청한다
    )
    for name in (
        "src/cli.py",
        "src/bench.py",
        "docs/engineering-guide.md",
        "docs/model-serving.md",
    ):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert not [phrase for phrase in stale if phrase in text], name


def test_every_preflight_module_is_listed_in_the_engineering_guide_tree() -> None:
    guide = (ROOT / "docs/engineering-guide.md").read_text(encoding="utf-8")

    modules = sorted(path.name for path in (ROOT / "src").glob("preflight*.py"))

    assert len(modules) >= 4 and [name for name in modules if name not in guide] == []


def test_the_portability_prerequisites_section_points_at_preflight_and_strict() -> None:
    text = (ROOT / "docs/portability.md").read_text(encoding="utf-8")
    start = text.index("### 실모델 run의 선결조건")
    section = text[start : text.index("\n## ", start)]

    assert "cli preflight" in section and "--strict" in section and "doctor" in section
    assert "미실측" in section  # 실제 원격으로 끝까지 돌린 적이 없다고 적는다
