"""채점기(oracle) CLI 조립 — `--oracle-submit`·`--oracle-server`·`submit.sh` 신원 (#386).

`cli._oracle_for`의 거절 갈래(서버 주소·task_id 모양·체크섬·번들의 다른 주소·키)가 **제출 전에**
멈추는지, 8단계에 못 닿는 run은 경고만 하는지, 파서 기본값이 안 뒤집혔는지, 키가 출력되지 않는지를
본다. 오라클 객체를 만드는 `cli.TaskOracle`은 스텁으로 바꿔 **거절 갈래는 하나도 안 만든다**는 것을
센다.

러너 쪽(후보마다 vul · 지정한 하나에만 fix)은 `tests/test_oracle_wiring.py`가, 플래그에서
서버까지 이어지는 운영 배선 전체는 `tests/test_pipeline_e2e.py`가 돌려 본다 — 여기서는 `cli.run`을
돌리지 않는다. 마스킹 서버의 `--mask-map` → `TaskOracle.query_task_id`는
`tests/test_oracle_wiring.py` 끝의 두 절이 본다 — 아래
`test_a_masked_task_id_is_refused_before_any_submission`은 맵이 **없을 때**의 거절이다.
"""

import argparse
import json
from pathlib import Path

import pytest

from src import cli
from src.adapters.cybergym_oracle import checksum
from src.config import load_config
from src.orchestrator.runner import RunSpec
from src.orchestrator.task_loader import read_submit_metadata
from tests.oracle_fakes import TASK_ID, FakeOracle, submit_script

# --- submit.sh 파서 (과제 신원을 지어내지 않는다) --------------------------------------------

_SERVER = "http://127.0.0.1:8666"
_AGENT = "dcd41fef"


def _bundle(tmp_path: Path, text: str | None = None, **script: object) -> Path:
    task = tmp_path / "task"
    task.mkdir(exist_ok=True)
    (task / "submit.sh").write_text(submit_script(**script) if text is None else text, "utf-8")
    # 통과하는 카나리 보고서를 번들 **옆에** 둔다 - `_args`가 기본값으로 이 경로를 쓴다. 번들 안에
    # 두지 않는 것은 번들이 못 믿는 소스이고(`task_loader`) 카나리는 조작자의 산출물이라서다.
    _canary(tmp_path)
    return task


# 카나리 보고서의 통과하는 모양. **합성값이다** - 칸의 뜻과 거절 규칙은
# `tests/test_canary_report.py`가 든다.
_CANARY = {
    "task": TASK_ID,
    "verdict": "pass",
    "ok": True,
    "repeat": 10,
    "below_measured_repeat": False,
    "mmap_rnd_bits": "28",
    "rnd_bits_above_28": False,
    "rnd_bits_unmeasured": False,
    "measured_at": "2026-10-03T17:42:07+00:00",
    "docker_version": "29.5.3",
}


def _canary(tmp_path: Path, **overrides: object) -> Path:
    path = tmp_path / "canary.json"
    path.write_text(json.dumps({**_CANARY, **overrides}), encoding="utf-8")
    return path


def test_submit_metadata_is_read_from_the_bundle(tmp_path: Path) -> None:
    metadata = read_submit_metadata(_bundle(tmp_path))

    assert metadata is not None
    assert metadata.server == _SERVER
    assert metadata.task_id == TASK_ID
    assert metadata.agent_id == _AGENT
    assert metadata.checksum == checksum(TASK_ID, _AGENT)


@pytest.mark.parametrize(
    "text",
    ["", "#!/bin/bash\necho hi\n", "curl http://h/submit-vul\n", "metadata={bad json}\n"],
)
def test_unreadable_submit_metadata_is_none_not_invented(tmp_path: Path, text: str) -> None:
    """못 읽으면 `None`이다 - 신원을 지어내 제출하지 않는다(§0)."""
    assert read_submit_metadata(_bundle(tmp_path, text)) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"task_id": 123},
        {"task_id": None},
        {"task_id": ""},
        {"task_id": ["arvo:1065"]},
        {"agent_id": 7},
        {"agent_id": None},
        {"agent_id": ""},
        {"checksum": None},
        {"checksum": ""},
        {"checksum": 0},
    ],
)
def test_a_metadata_value_that_is_not_a_nonempty_string_is_not_an_identity(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    """`{"task_id": 123}`이나 `null`을 신원으로 받으면 숫자 id로 제출이 나가 서버가 400을 낸다."""
    assert read_submit_metadata(_bundle(tmp_path, **overrides)) is None


def test_a_metadata_object_without_a_checksum_is_not_an_identity(tmp_path: Path) -> None:
    text = (
        "curl -X POST http://127.0.0.1:8666/submit-vul "
        '-F \'metadata={"task_id": "arvo:1065", "agent_id": "dcd41fef"}\'\n'
    )

    assert read_submit_metadata(_bundle(tmp_path, text)) is None


def test_missing_submit_script_is_none(tmp_path: Path) -> None:
    task = tmp_path / "task"
    task.mkdir()

    assert read_submit_metadata(task) is None


# --- CLI 조립 가드 (설정 오류가 조용히 미제출로 떨어지지 않는다) -------------------------------

_KEY = "k-wiring-test-secret"


#  `_args`의 `canary` 기본값 표식 — `None`은 「안 줬다」(거절 대상)라 따로 둬야 한다.
_FROM_BUNDLE = object()


def _args(  # noqa: ANN202
    task_dir: Path | None = None,
    *,
    server: str | None = _SERVER,
    submit: bool = True,
    canary: object = _FROM_BUNDLE,
):
    """`--oracle-canary`는 기본으로 `_bundle`이 쓴 통과 보고서를 가리킨다(이슈 #383).

    `canary=None`은 **플래그를 안 준** run이고 그것도 거절 대상이다 — 그래서 기본값과 `None`을
    구별한다. `task_dir`이 없으면 보고서 경로도 없다(그 run은 `--task-dir` 거절이 먼저다).
    """
    if canary is _FROM_BUNDLE:
        canary = None if task_dir is None else task_dir.parent / "canary.json"
    return argparse.Namespace(
        oracle_submit=submit, oracle_server=server, task_dir=task_dir, oracle_canary=canary
    )


@pytest.fixture
def key_env(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv(cli._ORACLE_KEY_ENV, _KEY)
    return _KEY


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """`cli.TaskOracle`을 만들 때마다 인자를 기록하는 스텁 - 거절 갈래는 **하나도 안 만든다**."""
    made: list[dict] = []

    def stub(**kwargs):  # noqa: ANN003, ANN202
        made.append(kwargs)
        return FakeOracle()

    monkeypatch.setattr(cli, "TaskOracle", stub)
    return made


def _refused(result, capsys, needle: str) -> str:  # noqa: ANN001, ANN202
    """`_ORACLE_ERROR`이고 stderr에 `[ERROR]`와 `needle`이 찍혔는지 본다. 그 stderr를 돌려준다."""
    err = capsys.readouterr().err
    assert result is cli._ORACLE_ERROR, err
    assert "[ERROR]" in err and needle in err, err
    return err


def test_oracle_is_none_when_the_flag_is_off() -> None:
    assert cli._oracle_for(_args(server=None, submit=False)) is None


def test_the_error_marker_is_narrowable_and_the_annotation_is_not_object() -> None:
    """`Oracle | None | object`는 `object`로 뭉개져 좁히기가 안 됐다 - 표식이 타입이다."""
    import typing

    hint = typing.get_type_hints(cli._oracle_for)["return"]

    assert isinstance(cli._ORACLE_ERROR, cli._OracleRefused)
    assert cli._OracleRefused in typing.get_args(hint)
    assert object not in typing.get_args(hint)


def test_oracle_server_without_oracle_submit_is_an_error(capsys, built) -> None:  # noqa: ANN001
    """받아 놓고 아무 데도 안 쓰는 플래그는 「이 주소로 제출한다」는 인상만 준다."""
    _refused(cli._oracle_for(_args(server=_SERVER, submit=False)), capsys, "--oracle-submit")

    assert built == []


def test_oracle_submit_without_a_task_dir_is_an_error(capsys, built, key_env) -> None:  # noqa: ANN001
    _refused(cli._oracle_for(_args(None)), capsys, "--task-dir")


def test_oracle_submit_needs_an_operator_supplied_server_and_has_no_default(
    tmp_path: Path, capsys, built, key_env  # noqa: ANN001
) -> None:
    """주소는 번들이 아니라 조작자가 정한다 - API 키가 가는 곳이다. 기본값도 없다."""
    result = cli._oracle_for(_args(_bundle(tmp_path), server=None))
    err = _refused(result, capsys, "--oracle-server")

    assert "기본 주소도 없다" in err
    assert built == []


@pytest.mark.parametrize(
    "server",
    [
        "127.0.0.1:8666",
        "ftp://127.0.0.1:8666",
        "http://",
        "http://127.0.0.1:8666/api",
        "http://127.0.0.1:8666/?x=1",
        "http://user:pw@127.0.0.1:8666",
        "http://127.0.0.1:99999",
        "http://127.0.0.1:0",
        "http://[::1",
    ],
)
def test_a_server_address_the_client_cannot_use_is_refused(
    tmp_path: Path, capsys, built, key_env, server: str  # noqa: ANN001
) -> None:
    _refused(cli._oracle_for(_args(_bundle(tmp_path), server=server)), capsys, "--oracle-server")

    assert built == []


@pytest.mark.parametrize("wildcard", ["0.0.0.0", "::"])
def test_a_wildcard_address_is_not_an_address_to_connect_to(
    tmp_path: Path, capsys, built, key_env, wildcard: str  # noqa: ANN001
) -> None:
    """와일드카드는 서버가 bind할 때 쓰는 값이다 - 그 주소로 키를 안 보낸다(리터럴은 안 쓴다)."""
    import ipaddress

    host = str(ipaddress.ip_address(wildcard))
    shown = f"[{host}]" if ":" in host else host

    result = cli._oracle_for(_args(_bundle(tmp_path), server=f"http://{shown}:8666"))

    _refused(result, capsys, "와일드카드")
    assert built == []


def test_oracle_submit_without_metadata_is_an_error(tmp_path: Path, capsys, built, key_env) -> None:  # noqa: ANN001
    task = tmp_path / "empty"
    task.mkdir()

    _refused(cli._oracle_for(_args(task)), capsys, "신원을 못 읽었다")
    assert built == []


def test_a_masked_task_id_is_refused_before_any_submission(
    tmp_path: Path, capsys, built, key_env  # noqa: ANN001
) -> None:
    """`--mask-map` 없이 마스킹된 id(`9c73e92e52b7`)는 지원하지 않는다 - 조회에 쓸 실 id가 없다.
    오라클 객체조차 만들어지지 않으므로 컨테이너는커녕 요청도 안 나간다. 문구는 코드가 거절한다는
    것과 `--mask-map`을 주라는 것만 말하고 실서버에서 무슨 일이 나는지는 단언하지 않는다(그 실측은
    0건이다)."""
    task = _bundle(tmp_path, task_id="9c73e92e52b7")

    err = _refused(cli._oracle_for(_args(task)), capsys, "9c73e92e52b7")

    assert "미지원" in err and "아무것도 제출하지 않았다" in err
    assert "404" not in err
    err.encode("cp949")  # 콘솔에 찍히는 문장이다
    assert built == []


def test_a_bundle_rendered_with_another_salt_is_refused_before_any_submission(
    tmp_path: Path, capsys, built, key_env  # noqa: ANN001
) -> None:
    """클라이언트는 기본 salt로 체크섬을 다시 계산해 제출한다 - 다른 salt로 만든 번들은 파싱이
    「성공」한 뒤 서버가 400을 낸다. 번들의 체크섬을 제출 전에 견준다."""
    task = _bundle(tmp_path, checksum=checksum(TASK_ID, _AGENT, "another-salt"))

    _refused(cli._oracle_for(_args(task)), capsys, "checksum")
    assert built == []


def test_a_bundle_that_names_another_server_is_refused_not_silently_obeyed_or_overridden(
    tmp_path: Path, capsys, built, key_env  # noqa: ANN001
) -> None:
    """번들의 주소와 조작자의 주소가 다르면 둘 중 하나를 조용히 고르지 않고 멈춘다 - 두 주소를 다
    대고, 번들의 주소로는 안 보낸다고 말한다."""
    task = _bundle(tmp_path, server="http://evil.example:9")

    err = _refused(cli._oracle_for(_args(task, server=_SERVER)), capsys, "evil.example")

    assert _SERVER in err and "번들의 주소로는 보내지 않는다" in err
    assert built == []


def test_oracle_submit_without_a_key_is_an_error(
    tmp_path: Path, capsys, built, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    """키가 없으면 멈춘다 - 제출하려 한 run이 조용히 0건 제출로 끝나지 않는다."""
    monkeypatch.delenv(cli._ORACLE_KEY_ENV, raising=False)

    _refused(cli._oracle_for(_args(_bundle(tmp_path))), capsys, cli._ORACLE_KEY_ENV)
    assert built == []


def test_the_oracle_is_built_from_the_operators_server_and_the_bundles_identity(
    tmp_path: Path, capsys, built, key_env  # noqa: ANN001
) -> None:
    """주소는 조작자의 값이고(대소문자·끝의 `/`는 같은 주소로 견준다) 신원은 번들의 값이다."""
    task = _bundle(tmp_path)

    oracle = cli._oracle_for(_args(task, server="HTTP://127.0.0.1:8666/"))

    assert oracle is not cli._ORACLE_ERROR and oracle is not None
    (kwargs,) = built
    assert kwargs == {
        "server": "HTTP://127.0.0.1:8666",
        "task_id": TASK_ID,
        "agent_id": _AGENT,
        "api_key": _KEY,
        "query_task_id": None,  # `--mask-map`을 안 줬다 - 마스크 없이 띄운 서버
    }


def test_the_target_is_printed_before_the_run_and_the_key_is_not(
    tmp_path: Path, capsys, built, key_env  # noqa: ANN001
) -> None:
    """키가 어느 주소로 가는지를 run 전에 보인다 - 놀랄 일이 없게. 키 자체는 어디에도 안 찍힌다."""
    cli._oracle_for(_args(_bundle(tmp_path)))

    out = capsys.readouterr()
    assert _SERVER in out.out and TASK_ID in out.out and cli._ORACLE_KEY_ENV in out.out
    assert "fix 다리" in out.out
    # 규칙은 등급이다(2026-10-03 동결) - 이 줄이 `_oracle_for`의 출력과 함께 움직인다.
    assert "최댓값이 동점이거나 등급을 모르는 후보가 있으면 보내지 않는다" in out.out
    assert _KEY not in out.out + out.err


def test_a_run_that_cannot_reach_stage_8_only_warns_and_checks_nothing(
    capsys, built, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    """`--oracle-submit --dry-run`이 키 부재로 죽던 비대칭: 모델에 안 닿는 run은 막지 않는다는 이
    파일의 원칙과 어긋났다. 낼 PoC가 없는 run에 키·번들 검사를 걸 근거가 없다 - 경고만 한다."""
    monkeypatch.delenv(cli._ORACLE_KEY_ENV, raising=False)

    result = cli._oracle_for(
        _args(None, server=None), unreachable="이 run은 8단계에 닿지 않는다(...)"
    )

    err = capsys.readouterr().err
    assert result is None
    assert "[WARN ]" in err and "아무것도 제출하지 않는다" in err and "[ERROR]" not in err
    assert built == []


# --- 카나리 게이트 (이슈 #383) ---------------------------------------------------------


def test_oracle_submit_without_a_canary_report_is_refused(
    tmp_path: Path, capsys, built, key_env  # noqa: ANN001
) -> None:
    """🔴 채점 run은 카나리 없이 돌지 않는다 - 호스트가 sanitizer를 제대로 돌리는지 봐야 한다."""
    result = cli._oracle_for(_args(_bundle(tmp_path), canary=None))

    _refused(result, capsys, "--oracle-canary")
    assert built == []


def test_a_canary_report_that_cannot_be_read_is_refused(
    tmp_path: Path, capsys, built, key_env  # noqa: ANN001
) -> None:
    """없는 파일·JSON 아님을 「통과」로 읽지 않는다(§0)."""
    task = _bundle(tmp_path)

    result = cli._oracle_for(_args(task, canary=tmp_path / "nope.json"))

    _refused(result, capsys, "카나리")
    assert built == []


def test_a_canary_for_another_task_is_refused(
    tmp_path: Path, capsys, built, key_env  # noqa: ANN001
) -> None:
    task = _bundle(tmp_path)
    other = _canary(tmp_path, task="oss-fuzz:368076871")

    result = cli._oracle_for(_args(task, canary=other))

    err = _refused(result, capsys, "oss-fuzz:368076871")
    assert TASK_ID in err


def test_an_entropy_above_28_is_refused_even_though_the_canary_verdict_passed(
    tmp_path: Path, capsys, built, key_env  # noqa: ANN001
) -> None:
    """도구는 32에서도 `pass`를 낸다(그 모듈 docstring) - 채점 게이트는 그것을 거부로 올린다."""
    task = _bundle(tmp_path)
    noisy = _canary(tmp_path, mmap_rnd_bits="32", rnd_bits_above_28=True)

    result = cli._oracle_for(_args(task, canary=noisy))

    _refused(result, capsys, "32")
    assert built == []


def test_a_passing_canary_is_printed_with_the_limit_it_cannot_check(
    tmp_path: Path, capsys, built, key_env  # noqa: ANN001
) -> None:
    """통과도 찍는다 - 「언제 쟀나」와 「신선도는 못 본다」를 조작자가 봐야 한다."""
    cli._oracle_for(_args(_bundle(tmp_path)))

    out = capsys.readouterr().out
    assert "카나리 통과" in out and "28" in out and "2026-10-03T17:42:07+00:00" in out
    assert "부팅" in out  # 신선도를 못 본다는 한계


def test_the_canary_flag_without_oracle_submit_is_an_error(
    tmp_path: Path, capsys, built  # noqa: ANN001
) -> None:
    """받아 놓고 안 쓰는 플래그는 설정 오류다 - `--oracle-server`·`--mask-map`과 같은 결."""
    args = _args(None, server=None, submit=False, canary=_canary(tmp_path))

    _refused(cli._oracle_for(args), capsys, "--oracle-canary")


def test_the_gate_and_the_tool_agree_on_the_entropy_limit() -> None:
    """🔴 게이트와 도구가 **같은 수**를 봐야 한다 - `src/`는 `tools/`를 import하지 않아
    리터럴이 둘이다. 갈리면 하네스가 통과시킨 장비를 도구가 경고하거나 그 반대가 된다.
    """
    import importlib.util

    from src.adapters.canary_report import MMAP_RND_BITS_MAX

    spec = importlib.util.spec_from_file_location(
        "_oracle_canary_tool", Path(cli.REPO_ROOT) / "tools" / "oracle_canary.py"
    )
    assert spec is not None and spec.loader is not None
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)

    assert MMAP_RND_BITS_MAX == tool.MMAP_RND_BITS_MAX


def _spec(**kwargs: object) -> RunSpec:
    return RunSpec(condition="B3prime@pipeline", target=Path("."), **kwargs)


@pytest.mark.parametrize(
    ("kwargs", "reachable"),
    [
        ({"dry_run": True}, False),
        ({"stop_after": 0}, False),
        ({"stop_after": 7}, False),
        ({"stop_after": 8}, True),
        ({"stop_after": 9}, True),
        ({}, True),
    ],
)
def test_the_run_reaches_the_oracle_only_when_it_reaches_stage_8(
    kwargs: dict[str, object], reachable: bool
) -> None:
    reason = cli._oracle_unreachable_reason(_spec(**kwargs), load_config("dev_fake"))

    assert (reason is None) is reachable
    if reason is not None:
        assert "8단계" in reason
        reason.encode("cp949")  # 경고로 찍힌다


def test_a_profile_without_a_pov_seat_cannot_reach_the_oracle() -> None:
    config = load_config("dev_fake")
    roles = {role: key for role, key in config.profile.roles.items() if role != "pov"}
    no_pov = config.transition(profile=config.profile.transition(roles=roles))

    reason = cli._oracle_unreachable_reason(_spec(), no_pov)

    assert reason is not None and "pov 좌석" in reason


# --- 파서 ------------------------------------------------------------------------------


def test_the_parser_defaults_to_no_submission_and_no_server() -> None:
    """`store_true`가 뒤집히거나 `--oracle-server`에 기본 주소가 생기면 여기서 죽는다."""
    parse = cli.build_parser().parse_args
    base = ["run", "--profile", "dev_fake", "--target", "t"]

    off = parse(base)
    on = parse([*base, "--oracle-submit", "--oracle-server", _SERVER])

    assert off.oracle_submit is False and off.oracle_server is None
    assert on.oracle_submit is True and on.oracle_server == _SERVER


def test_the_oracle_server_flag_has_no_default() -> None:
    """저장소 규칙: 서버 주소에 기본값을 두지 않는다 - 어떤 철자의 주소도 default일 수 없다."""
    run = next(
        action.choices["run"]
        for action in cli.build_parser()._actions
        if getattr(action, "choices", None) and "run" in action.choices
    )
    flags = {a.dest: a for a in run._actions}

    assert flags["oracle_server"].default is None
    assert flags["oracle_server"].required is False
    assert flags["oracle_submit"].default is False


def test_the_help_text_is_printable_on_a_cp949_console() -> None:
    """`--help`는 콘솔에 찍힌다 - `U+2014`·`U+26A0`이 이 장비의 stdout을 죽인다."""
    run = next(
        action.choices["run"]
        for action in cli.build_parser()._actions
        if getattr(action, "choices", None) and "run" in action.choices
    )
    flags = {a.dest: a for a in run._actions}

    for dest in ("oracle_submit", "oracle_server", "mask_map", "oracle_canary"):
        flags[dest].help.encode("cp949")


# --- 키는 출력되지 않는다 ---------------------------------------------------------------------


def test_the_key_does_not_leak_through_the_deps_that_embed_the_oracle(tmp_path: Path) -> None:
    """`Deps`·`RunContext`가 오라클 객체를 품는다 - 누가 `repr`·로그로 찍어도 키가 안 나온다."""
    from src.adapters.cybergym_oracle import TaskOracle
    from src.adapters.factory import build_deps

    oracle = TaskOracle(server=_SERVER, task_id=TASK_ID, agent_id=_AGENT, api_key=_KEY)

    deps = build_deps(
        load_config("dev_fake"),
        run_id="run_20260901_0001",
        source_root=tmp_path,
        artifacts_root=tmp_path / "arts",
        oracle=oracle,
    )

    assert deps.oracle is oracle
    assert _KEY not in repr(deps) and _KEY not in str(deps)
