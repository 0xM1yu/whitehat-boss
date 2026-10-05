"""채점기(oracle) CLI 조립 — `--oracle-submit`·`--oracle-server`·`submit.sh` 신원 (#386).

`cli._oracle_for`의 거절 갈래(서버 주소·task_id 모양·체크섬·번들의 다른 주소·키·카나리 보고서)가
**제출 전에** 멈추는지, 8단계에 못 닿는 run은 경고만 하는지, 파서 기본값이 안 뒤집혔는지, 키가
출력되지 않는지를 본다. 오라클 객체를 만드는 `cli.TaskOracle`은 스텁으로 바꿔 **거절 갈래는
하나도 안 만든다**는 것을 센다.

러너 쪽(후보마다 vul · 지정한 하나에만 fix)은 `tests/test_oracle_wiring.py`가, 플래그에서
서버까지 이어지는 운영 배선 전체는 `tests/test_pipeline_e2e.py`가 돌려 본다 — 여기서는 `cli.run`을
돌리지 않는다. 마스킹 서버의 `--mask-map` → `TaskOracle.query_task_id`는
`tests/test_oracle_wiring.py` 끝의 두 절이 본다 — 아래
`test_a_masked_task_id_is_refused_before_any_submission`은 맵이 **없을 때**의 거절이다.
"""

import argparse
import hashlib
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
    # 게이트는 요약(`verdict`·`ok`)만 믿지 않고 같은 파일의 **관측 칸** 셋도 본다 - 도구의
    # `_report`가 늘 쓰는 키다(§2.2). 규칙 자체는 `tests/test_canary_report.py`가 든다.
    "completed": True,
    "segv_count": 0,
    "problems": [],
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
        # 게이트가 통과시킨 보고서 - 경로는 풀어서, 해시는 검사한 바이트의 것이다(#487).
        "canary_path": str((task.parent / "canary.json").resolve()),
        "canary_sha256": "sha256:"
        + hashlib.sha256((task.parent / "canary.json").read_bytes()).hexdigest(),
    }


def test_the_target_is_printed_before_the_run_and_the_key_is_not(
    tmp_path: Path, capsys, built, key_env  # noqa: ANN001
) -> None:
    """키가 어느 주소로 가는지를 run 전에 보인다 - 놀랄 일이 없게. 키 자체는 어디에도 안 찍힌다."""
    cli._oracle_for(_args(_bundle(tmp_path)))

    out = capsys.readouterr()
    assert _SERVER in out.out and TASK_ID in out.out and cli._ORACLE_KEY_ENV in out.out
    assert "fix 다리" in out.out
    # 규칙은 하네스 내부 값의 전순위다(⏵ 2026-10-04에 「동점이면 거부」가 사슬로 바뀌었다) -
    # 이 두 줄이 `_oracle_for`의 출력과 함께 움직인다.
    assert "내부 근거 등급 > confidence > 작은 바이트 > PoC 해시" in out.out
    assert "등급을 모르는 후보가 있으면 보내지 않는다" in out.out
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


# `"k\x00"`은 없다 - 환경변수에 NUL을 넣을 수 없어(`os.environ`이 `ValueError`) 이 코드에 닿지
# 않는다. 닿지 않는 입력을 핀으로 들면 검사가 막는 범위를 넓게 적는 것이다.
@pytest.mark.parametrize("key", ["k\r", "k\n", "k key", "\tk", "k\x1b[2J"])
def test_a_key_with_whitespace_or_control_characters_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, key: str
) -> None:
    """🔴 **키가 run 산출물에 평문으로 남는 경로를 입구에서 막는다.**

    끝에 CR이 붙은 키(CRLF 파일을 `$(cat key.txt)`로 읽으면 생긴다)는 `http.client`가 헤더를
    만들기 전에 `ValueError("Invalid header value b'<키>\\r'")`로 거부하고, 그 문구가
    `error_detail`을 거쳐 사이드카 `oracle/verdict.json`에 **그대로** 쓰인다. 앞 판의 어댑터
    가림막은 자기 예외 클래스만 지웠고, 지금은 `Exception`과 `repr(bytes)` 모양까지 지운다 - 이
    입구 검사는 그 앞의 첫 겹이다(가림막 쪽 핀은 아래 둘).

    ⚠ **거절 문구에 키가 없어야 한다** - 막으면서 찍으면 같은 사고다.
    """
    monkeypatch.setenv(cli._ORACLE_KEY_ENV, key)

    result = cli._oracle_for(_args(_bundle(tmp_path)))

    assert result is cli._ORACLE_ERROR
    err = capsys.readouterr().err
    assert "공백·비출력 문자" in err
    assert key.strip() not in err or "k" not in key.strip()


def test_the_redaction_covers_exceptions_that_are_not_ours(tmp_path: Path) -> None:
    """가림막이 `CyberGymOracleError`만 지우면 키를 든 다른 예외가 그대로 장부로 간다.

    ⚠ **이 핀은 클래스 넓힘만 문다.** 던지는 문구는 지어낸 것이라 `http.client`가 키를 문구에
    싣는 모양을 대표하지 않는다 - 그 모양은 바로 아래 핀이 `http.client`를 실제로 불러 든다.
    """
    from src.adapters import cybergym_oracle as oc

    oracle = oc.TaskOracle(
        server=_SERVER, task_id=TASK_ID, agent_id=_AGENT, api_key="SECRET-abc123"
    )

    with pytest.raises(oc.CyberGymOracleError) as caught:  # noqa: PT012
        with oracle._redacting():
            raise ValueError("bad value SECRET-abc123 in header")

    message = str(caught.value)
    assert "SECRET-abc123" not in message
    assert "ValueError" in message and "***" in message


# 키가 `http.client`의 오류 문구에 실리는 모양은 `repr(bytes)`다 - 실제 CR·LF가 이스케이프로 바뀐다.
# `"SECRETKEY123é…"`는 라틴-1 범위 문자(`é`)가 든 키다: `http.client`는 헤더 값을 **라틴-1**로
# 인코딩해 `repr`하므로 UTF-8로 인코딩한 `repr`(`\xc3\xa9`)은 그 모양이 아니다.
@pytest.mark.parametrize(
    "key",
    ["SECRETKEY123\r", "SECRETKEY123\n", "SECRETKEY123\r\n", "SECRETKEY123é\r"],
)
def test_the_redaction_hides_the_key_in_the_shape_http_client_puts_it_in(key: str) -> None:
    """🔴 **가림막이 `http.client`가 실제로 쓰는 문구 모양의 키를 지우는지** - 지어낸 문구가 아니다.

    `TaskOracle.submit_vul`이 CR·LF가 든 키로 첫 요청(`/query-poc`)을 만들면 `http.client.putheader`
    가 소켓을 열기 **전에** `ValueError("Invalid header value b'<키>\\r'")`를 던진다(네트워크
    호출 없이 재현된다). 앞 판의 가림막은 실제 CR이 든 원문으로 `str.replace`해서 이 `repr`
    모양(`\\r` 두 글자)과 안 맞았고 키가 그대로 남았다(`src/cli.py`의 입구 검사가 막는 CLI 경로에선
    안 샜지만 어댑터를 직접 쓰는 호출에서는 샌다).
    """
    from src.adapters import cybergym_oracle as oc

    oracle = oc.TaskOracle(
        server="http://127.0.0.1:9", task_id=TASK_ID, agent_id=_AGENT, api_key=key
    )

    with pytest.raises(oc.CyberGymOracleError) as caught:
        oracle.submit_vul(b"x")

    message = str(caught.value)
    # 전제를 먼저 건다: 정말 `http.client`가 만든 문구인가. 아니면 아래 단언은 빈 말이다.
    assert "Invalid header value" in message
    assert "SECRETKEY123" not in message
    assert "***" in message
    # 🔴 **이스케이프 꼬리도 안 남아야 한다.** 앞뒤 공백을 뗀 모양(`SECRETKEY123`)은 `repr` 모양
    # (`SECRETKEY123\r`)의 **부분 문자열**이라 짧은 것부터 지우면 `b'***\r'`가 남는다(키 조각은
    # 아니지만 가림막이 모양을 통째로 지운다는 서술이 그것을 요구한다). 긴 모양부터 지우는 정렬을
    # 문다.
    assert "\\r" not in message and "\\n" not in message


def test_the_redaction_survives_a_key_http_client_cannot_encode() -> None:
    """라틴-1 밖 문자가 든 키 - `http.client`는 `Invalid header value`가 아니라
    `UnicodeEncodeError`를 던지고, 가림막은 그 예외도 `CyberGymOracleError`로 올린다
    (독립 검토 LOW-1).

    `_without_key`가 `key.encode("latin-1", "replace")`의 `replace`를 빼면 이 키로
    **`_without_key` 자신이** `UnicodeEncodeError`를 던지고, 그것이 `CyberGymOracleError`로 안
    바뀐 채 호출부로 샌다 - 러너가 잡는 예외 종류가 달라진다. 이 핀이 그 변이를 문다.

    ⚠ 한계(`_without_key` docstring): 그 문구에는 **문자 하나(이스케이프)와 위치**가 남는다 -
    키가 한 글자면 키 전체다. 여기서는 그 한계를 단언하지 않는다(CPython 문구에 기대는 핀이
    되므로).
    """
    from src.adapters import cybergym_oracle as oc

    oracle = oc.TaskOracle(
        server="http://127.0.0.1:9", task_id=TASK_ID, agent_id=_AGENT, api_key="SECRET中\r"
    )

    with pytest.raises(oc.CyberGymOracleError) as caught:
        oracle.submit_vul(b"x")

    message = str(caught.value)
    assert message.startswith("UnicodeEncodeError:")
    assert "SECRET" not in message


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


def test_the_gate_and_the_tool_agree_on_the_measured_repeat() -> None:
    """🔴 반복 횟수의 하한도 같은 이유로 리터럴이 둘이다 - 게이트가 `repeat`를 직접 견주는 수
    (`canary_report.MEASURED_REPEAT`)가 도구가 `weak_pass`를 가르는 수(`_MEASURED_REPEAT`)와
    갈리면, 도구는 통과로 쓴 보고서를 게이트가 거절하거나 도구가 약하다고 쓴 보고서를 통과시킨다.
    """
    import importlib.util

    from src.adapters.canary_report import MEASURED_REPEAT

    spec = importlib.util.spec_from_file_location(
        "_oracle_canary_tool", Path(cli.REPO_ROOT) / "tools" / "oracle_canary.py"
    )
    assert spec is not None and spec.loader is not None
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)

    assert MEASURED_REPEAT == tool._MEASURED_REPEAT


def test_a_canary_inside_the_task_tree_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """과제 번들은 **신뢰 경계 밖**이라 게이트가 읽는 파일을 그 안에 두지 않는다.

    지금은 다른 검사들이 우연히 막는다 - 번들에 허용 목록 밖 파일이 있으면 `check_task_dir`가
    먼저 거절한다. 그 부수 효과에 기대지 않는다: 그 검사가 느슨해지면 번들이 게이트가 읽는
    파일을 깔 수 있게 된다.
    """
    monkeypatch.setenv(cli._ORACLE_KEY_ENV, "k")
    task = _bundle(tmp_path)
    inside = _canary(task)  # 번들 디렉터리 안에 쓴다

    result = cli._oracle_for(_args(task, canary=inside))

    assert result is cli._ORACLE_ERROR
    assert "과제 트리 안이다" in capsys.readouterr().err


def test_the_command_the_refusal_prints_actually_parses(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """거절 문구가 알려 주는 명령을 **그 도구의 파서로 실제로 파싱해 본다**.

    🔴 문자 핀으로는 부족하다 - 철자를 단언하면 `--task`·`--json` 같은 없는 옵션으로 되돌려도
    초록이다(CLAUDE.md §2.5 「검사가 무는 것을 세게 적지 않는다」). 이 보고서는 채점 run의
    선행이고 이 문구가 **유일한 복구 안내**라, 틀리면 조작자가 보고서를 손으로 쓰게 된다.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_oracle_canary_tool", Path(cli.REPO_ROOT) / "tools" / "oracle_canary.py"
    )
    assert spec is not None and spec.loader is not None
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)

    # 문구의 `<...>` 자리표시자를 실제 값으로 바꿔 argv를 만든다.
    fill = {"<task-id>": "arvo:1065", "<경로>": str(tmp_path / "canary.json")}
    argv = [fill.get(word, word) for word in cli._CANARY_COMMAND.split()[1:]]

    parsed = tool._build_parser().parse_args(argv)

    assert parsed.task == "arvo:1065"
    assert parsed.out == tmp_path / "canary.json"


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


def test_the_submit_help_names_every_flag_that_is_required_with_it() -> None:
    """`--oracle-submit` 도움말이 **함께 필수인 옵션을 빠뜨리지 않는다** (리뷰 LOW-1).

    새 필수 `--oracle-canary`가 들어올 때 이 도움말의 「…와 함께만 쓴다」가 `--task-dir`와
    `--oracle-server`만 들고 있었다(`_oracle_for` docstring·거절 목록·
    `docs/agent-contracts.md`는 고쳤다). 동반 옵션은 **자기 도움말이 「--oracle-submit이면
    필수다」라고 적은 옵션**에서 센다 - 다음에 필수 옵션이 또 늘어도 그 문구를 따르면 여기서
    걸린다.

    ⚠ 한계: 그 문구로 센다. 필수라고 적으면서 다르게 쓴 옵션은 못 본다(그래서 둘은 이름으로도
    박았다). `--task-dir`는 자기 도움말에 그 문구가 없어 이름으로만 본다.
    """
    run = next(
        action.choices["run"]
        for action in cli.build_parser()._actions
        if getattr(action, "choices", None) and "run" in action.choices
    )
    flags = {a.option_strings[0]: a for a in run._actions if a.option_strings}
    companions = {
        name
        for name, action in flags.items()
        if name != "--oracle-submit" and "--oracle-submit이면 필수다" in (action.help or "")
    }
    assert {"--oracle-server", "--oracle-canary"} <= companions  # 센 방법이 둘을 놓치지 않았다

    submit_help = flags["--oracle-submit"].help or ""
    missing = sorted(name for name in companions | {"--task-dir"} if name not in submit_help)

    assert not missing, f"--oracle-submit 도움말이 {missing}를 안 든다"


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


def test_a_passing_canary_is_bound_to_the_oracle_by_path_and_sha256(
    tmp_path: Path, built, key_env  # noqa: ANN001
) -> None:
    """이슈 #487 - 어느 카나리 보고서에 기댔는지가 채점기에 묶여 산출물까지 갈 수 있어야 한다."""
    task = _bundle(tmp_path)
    report = task.parent / "canary.json"

    cli._oracle_for(_args(task))

    assert len(built) == 1
    assert built[0]["canary_path"] == str(report.resolve())
    assert built[0]["canary_sha256"] == "sha256:" + hashlib.sha256(report.read_bytes()).hexdigest()


def test_only_an_oracle_that_leaned_on_a_canary_yields_a_manifest_reference() -> None:
    from types import SimpleNamespace

    bound = SimpleNamespace(canary_path="/ops/canary.json", canary_sha256="sha256:" + "ab" * 32)

    assert cli._canary_ref(bound) == {
        "canary_path": "/ops/canary.json",
        "canary_sha256": "sha256:" + "ab" * 32,
    }
    assert cli._canary_ref(FakeOracle()) is None
    assert cli._canary_ref(None) is None


def test_the_gate_reads_the_same_resolved_path_it_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, built, key_env  # noqa: ANN001
) -> None:
    """상대 경로로 줘도 게이트가 읽는 파일과 기록하는 경로가 **같은 값**이다 - 읽기와 기록 사이에
    링크가 바뀌어 해시는 A의 것이고 경로는 B를 가리키는 틈을 구조로 닫는다(#487)."""
    task = _bundle(tmp_path)
    monkeypatch.chdir(task.parent)
    seen: list[Path] = []
    real = cli.read_canary_report

    def spy(path: Path):  # noqa: ANN202
        seen.append(path)
        return real(path)

    monkeypatch.setattr(cli, "read_canary_report", spy)

    cli._oracle_for(_args(task, canary="canary.json"))

    recorded = built[0]["canary_path"]
    assert Path(recorded).is_absolute(), recorded
    assert seen == [Path(recorded)]


def test_a_reference_with_only_one_of_path_and_sha256_is_a_defect() -> None:
    from types import SimpleNamespace

    half = SimpleNamespace(canary_path="/ops/canary.json", canary_sha256=None)

    with pytest.raises(ValueError, match="canary"):
        cli._canary_ref(half)
