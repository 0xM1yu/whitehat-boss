"""`preflight` 2차 적대적 리뷰 반영(R1·R2)을 **성질로** 문다 (#534).

두 지적 다 **거짓 OK**였다 — 이 명령이 녹색을 내고 GPU를 빌린 뒤 run이 깨지면 가치가 음수다.

- **R1** 교대 실행 파일이 경로로 적혀 있으면 `_resolved`가 `which`를 안 거치고 그대로 돌려주는데
  (`model_swap._resolved`), `check_bash`는 **구분자가 있다는 이유만으로** OK를 냈다. 없는 절대경로가
  `--strict`에서도 ERROR 0 · WARN 0 · exit 0이었고, 같은 명령을 실제 교대에 넘기면
  `ModelSwapError("교대 명령을 실행하지 못했다")`다(`model_swap.swap_once`의 `except OSError`).
- **R2** `tunnel_endpoints`가 `_endpoints`의 둘째 값(주소를 못 읽은 키)을 **버려서** 그 모델의 포트
  점검이 사라졌다. 교대 없는 갈래(`check_served`)는 같은 목록을 이미 ERROR로 세고 있었다 — 한쪽만
  샌 비대칭이다. 전부 못 읽으면 집합이 비어 「포트에 닿는다」는 OK가 났다.

🔴 **기존 테스트가 왜 못 잡았나.** `World.resolve`가 늘 `/usr/bin/<head>`를 돌려줘 `check_bash`의
경로 갈래에 **한 번도 안 들어갔다.** 가짜를 `_resolved`와 같은 갈래로 고친 것이 이 파일의 전제다.
"""

import sys
from pathlib import Path

import pytest

from src import preflight as pf
from src import preflight_remote as pr
from src.config import load_serving
from tests import preflight_fakes as fk
from tests.preflight_fakes import (
    DEVICE,
    ERROR,
    OK,
    PROFILE,
    UNSWAPPED_PROFILE,
    WARN,
    World,
    fill_checksums,
    forbid_real_world,
    make_world,
    one,
)

WRAPPER = "tools/serve/swap-remote.sh"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    forbid_real_world(monkeypatch)
    fill_checksums(monkeypatch)
    return make_world(tmp_path)


@pytest.fixture
def hybrid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    forbid_real_world(monkeypatch)
    fill_checksums(monkeypatch)
    return make_world(tmp_path, UNSWAPPED_PROFILE)


def _swap_server() -> tuple[str, str]:
    """장비 파일이 띄우는 모델 하나의 (registry 키, 주소) — 철자를 이 파일에 박지 않는다."""
    config = fk.load_config(PROFILE)
    key = sorted(load_serving(DEVICE).servers)[0]
    return key, config.models[key].endpoint


def _served_model() -> tuple[str, str]:
    """교대 없는 프로필에서 사람이 미리 띄워 두는 모델 하나의 (키, 주소)."""
    config = fk.load_config(UNSWAPPED_PROFILE)
    key = sorted(config.served_models().values())[0]
    return key, config.models[key].endpoint


# --- R1. 경로로 적힌 교대 실행 파일은 실재를 본다 -------------------------------------------------


def test_a_swap_command_pointing_at_a_missing_executable_is_an_error(
    world: World, tmp_path: Path
) -> None:
    missing = (tmp_path / "nowhere" / "bash.exe").as_posix()

    checks = world.check(config_dir=fk.config_with_command(tmp_path, f'["{missing}", {WRAPPER}]'))

    check = one(checks, "bash 해소:")
    assert check.severity is ERROR and missing in check.message
    # 사유까지 문다 — 없는 파일에도 `os.access`가 거짓이라, 실재 검사를 빼면 ERROR는 나지만
    # 「실행 권한이 없다」로 엉뚱한 사유를 댄다(없는 경로에 chmod를 하라고 보낸다).
    assert "교대 실행 파일이 없다" in check.message and "권한" not in check.message
    assert pf.exit_code(checks) == pf.EXIT_ERRORS  # 거짓 OK exit 0이 아니다
    assert pf.exit_code(checks, strict=True) == pf.EXIT_ERRORS


def test_a_swap_command_pointing_at_a_real_file_is_ok_and_says_it_never_launched_it(
    world: World, tmp_path: Path
) -> None:
    real = Path(sys.executable).as_posix()  # 이 인터프리터다 — 있고 실행 가능하다

    checks = world.check(config_dir=fk.config_with_command(tmp_path, f'["{real}", {WRAPPER}]'))

    check = one(checks, "bash 해소:")
    assert check.severity is OK
    assert "띄워 보지는 않았다" in check.message  # OK가 보증하지 않는 것을 그 줄이 말한다


def test_a_relative_swap_executable_is_resolved_against_the_swap_working_directory(
    world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """교대는 명령을 저장소 루트(`swap_runner`의 `cwd`)에서 띄운다 — preflight의 cwd가 아니다."""
    root = tmp_path / "repo"
    (root / "tools/serve").mkdir(parents=True)
    (root / WRAPPER).write_text("#!/bin/bash\n", encoding="utf-8")
    head = root / "tools/serve/mybash"
    head.write_text("#!/bin/sh\n", encoding="utf-8")
    head.chmod(0o755)
    config_dir = fk.config_with_command(tmp_path, f"[tools/serve/mybash, {WRAPPER}]")
    monkeypatch.chdir(tmp_path)  # cwd에는 그 상대경로가 없다

    checks = world.check(config_dir=config_dir, repo_root=root)

    assert one(checks, "bash 해소:").severity is OK


def test_a_swap_executable_without_the_execute_bit_is_an_error(
    world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows `os.access`는 실행 비트를 안 가르므로 거짓으로 눌러 그 갈래를 어느 OS에서든 돈다."""
    config_dir = fk.config_with_command(
        tmp_path, f'["{Path(sys.executable).as_posix()}", {WRAPPER}]'
    )
    monkeypatch.setattr(pr.os, "access", lambda *_args, **_kw: False)

    checks = world.check(config_dir=config_dir)

    assert one(checks, "bash 해소:").severity is ERROR


def test_a_bare_name_is_still_only_a_warning(world: World, tmp_path: Path) -> None:
    """맨 이름은 PATH 해소 문제라 종전 그대로 WARN이다 — R1이 그 갈래를 ERROR로 바꾸지 않았다."""
    world.resolve = lambda command: command  # type: ignore[method-assign] - which가 못 찾았다
    config_dir = fk.config_with_command(tmp_path, f"[definitely-not-bash, {WRAPPER}]")

    check = one(world.check(config_dir=config_dir), "bash 해소:")

    assert check.severity is WARN and "PATH에서 못 찾았다" in check.message


# --- R2. 주소를 못 읽은 모델은 어느 갈래에서도 ERROR다 -------------------------------------------


# 셋 다 `urlsplit`을 지나도 (호스트, 포트)가 안 나오는 모양이다 — 스킴 없음 · 숫자 아닌 포트 ·
# http(s)가 아닌 스킴. 실제 준비 확인(`bench.fetch`)은 각각 ValueError·InvalidURL·URLError로 깨진다.
_UNREADABLE = ["broken-address", "http://127.0.0.1:bad/v1", "ftp://127.0.0.1:8001/v1"]


@pytest.mark.parametrize("bad", _UNREADABLE)
def test_a_swap_model_whose_address_cannot_be_read_is_an_error_naming_that_model(
    world: World, tmp_path: Path, bad: str
) -> None:
    key, endpoint = _swap_server()

    checks = world.check(config_dir=fk.config_with_endpoint(tmp_path, endpoint, bad))

    check = one(checks, "터널 포트:")
    assert check.severity is ERROR and key in check.message  # 점검이 조용히 사라지지 않았다
    assert pf.exit_code(checks) == pf.EXIT_ERRORS


@pytest.mark.parametrize("bad", _UNREADABLE)
def test_the_unswapped_path_rejects_the_very_same_addresses(
    hybrid: World, tmp_path: Path, bad: str
) -> None:
    """같은 모양을 교대 갈래만 흘려보내던 비대칭을 양쪽에서 문다."""
    key, endpoint = _served_model()

    checks = hybrid.check(config_dir=fk.config_with_endpoint(tmp_path, endpoint, bad))

    check = one(checks, "서빙 엔드포인트:")
    assert check.severity is ERROR and key in check.message


def test_no_readable_address_at_all_is_not_an_empty_success() -> None:
    """빈 집합은 「전부 닿는다」가 아니다 — 두 갈래가 같은 판정 하나를 지난다."""
    good = (("127.0.0.1", 8000),)

    assert pr._rejected("x:", good, ("some-model",)) is not None
    assert pr._rejected("x:", (), ()) is not None
    assert pr._rejected("x:", good, ()) is None


def test_the_tunnel_check_still_probes_every_port_when_all_addresses_are_readable(
    world: World,
) -> None:
    """수정이 정상 경로를 좁히지 않았다 — 포트 수가 장비 파일의 서버 수와 같다."""
    serving = load_serving(DEVICE)
    endpoints, unreadable = pr.tunnel_endpoints(fk.load_config(PROFILE), serving)

    assert not unreadable and len(endpoints) == len(serving.servers)
    world.check()
    assert set(endpoints) <= set(world.tcp_calls)
