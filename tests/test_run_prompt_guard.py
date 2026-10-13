"""`cli run`이 `doctor`와 같은 프롬프트 검사를 시작 전에 건다 (이슈 #600 H3 · ⏵ 2026-10-13).

반쯤 배선된 프롬프트 판(Critic·PoV 판이 노드의 내용 인식 집합에 없는 경우)은 1~6단계의 GPU 시간을
쓰고 7·8단계에서 `PromptError`로 죽었다. `doctor`는 `_check_prompts`로 이미 ERROR를 냈지만 `run`은
`doctor`를 부르지 않아 그 앞에 서지 못했다. 지금은 `run`이 같은 함수를 부르고 ERROR면 exit 2다.

`run`은 **그 run이 닿는 단계의 역할만** 본다 — Auditor 루프 가드와 같은 판단이다
(`tests/test_auditor_loop.py`의
`test_a_declared_loop_passes_the_run_gate_and_a_run_that_stops_early_…`).
WARN(디스크의 새 판)은 막지 않는다.
"""

from pathlib import Path

import pytest

from src import cli
from src.config import HarnessConfig, load_config
from tests.test_auditor_loop import TARGET

_SERVED_FLAGS = ["--device", "test-box", "--condition", "B3prime@pipeline"]
_RUN_ARGS = ["run", "--profile", "dev_fake", "--target", str(TARGET), "--seed", "1", *_SERVED_FLAGS]


class _Started(RuntimeError):
    """게이트를 지나 `cli.execute`까지 왔다는 표시 — 모델은 한 번도 안 불렸다."""


def _seated(role: str, version: str) -> HarnessConfig:
    """`dev_fake`의 `role` 좌석을 **실서빙 모델**(Fake 아님)로 앉히고 프롬프트 판을 `version`으로 둔
    config.

    Fake 좌석은 내용 인식 집합 검사를 면제받으므로(`_check_prompt_version`) 좌석이 Fake가 아니어야
    그 검사가 서는 자리를 태운다. 모델 키는 서빙 프로필(`harness_pool`)이 그 역할에 고른 것이다."""
    base = load_config("dev_fake")
    key = load_config("harness_pool").profile.roles[role]
    entry = base.models[key].transition(prompt_template_version=version)
    return base.transition(
        models={**base.models, key: entry},
        profile=base.profile.transition(roles={**base.profile.roles, role: key}),
    )


def _serving(monkeypatch: pytest.MonkeyPatch, config: HarnessConfig) -> list[str]:
    """`cli`가 `config`를 읽게 하고 `execute`를 막는다. 반환 리스트는 `execute`가 불린 흔적이다."""
    calls: list[str] = []
    monkeypatch.setattr(cli, "load_config", lambda *_a, **_k: config)

    def started(*_: object, **__: object) -> None:
        calls.append("execute")
        raise _Started

    monkeypatch.setattr(cli, "execute", started)
    return calls


def _served_version(role: str) -> str:
    return load_config("harness_pool").model_for(role).prompt_template_version


@pytest.mark.parametrize(
    ("role", "version"),
    [("critic", "v1"), ("pov", "v2"), ("auditor", "v1")],
)
def test_run_refuses_a_prompt_outside_the_content_aware_set_before_any_model_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    role: str,
    version: str,
) -> None:
    """내용 인식 집합 밖의 판(`doctor`가 ERROR로 내는 것)에 `run`이 exit 2로 선다 — 모델 호출 전에.

    양성 원천: critic v1·pov v2는 착수 main의 옛 배선이고 auditor v1은 디스크에 남은 과거 판이다
    (`tests/test_doctor_score_chain.py::test_old_real_prompt_outside_content_set_errors`와 같은
    표)."""
    config = _seated(role, version)
    calls = _serving(monkeypatch, config)
    expected = [
        c.render() for c in cli._check_prompts(config) if c.severity is cli.Severity.ERROR
    ]
    assert expected and f"{role} 프롬프트 {version}은 내용 인식 집합 밖" in expected[0]  # 전제

    code = cli.main([*_RUN_ARGS, "--artifacts-root", str(tmp_path / "arts")])

    err = capsys.readouterr().err
    assert code == 2, err
    for line in expected:  # doctor가 찍는 줄과 같은 줄을 찍는다
        assert line in err
    assert calls == []
    assert not (tmp_path / "arts").exists() or not list((tmp_path / "arts").iterdir())


def test_run_refuses_a_prompt_version_that_does_not_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """디스크에 없는 판(오타)도 같은 자리에서 선다 — 예전에는 그 역할의 첫 호출에서
    `PromptError`였다."""
    calls = _serving(monkeypatch, _seated("critic", "v999"))

    code = cli.main([*_RUN_ARGS, "--artifacts-root", str(tmp_path / "arts")])

    err = capsys.readouterr().err
    assert code == 2 and "critic 프롬프트 로드 실패" in err, err
    assert calls == []


def test_run_and_doctor_print_the_same_error_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """같은 함수를 부르므로 `run`이 찍는 ERROR 줄은 `doctor`가 찍는 줄의 부분집합이다(문구가 안
    갈린다)."""
    config = _seated("pov", "v2")
    _serving(monkeypatch, config)
    monkeypatch.setattr(cli, "load_profile", lambda *_a, **_k: config)
    doctor_lines = {c.render() for c in cli.doctor("renamed", artifacts_root=tmp_path)}

    cli.main([*_RUN_ARGS, "--artifacts-root", str(tmp_path / "arts")])

    run_errors = {
        line for line in capsys.readouterr().err.splitlines() if line.startswith("[ERROR]")
    }
    assert run_errors and run_errors <= doctor_lines


@pytest.mark.parametrize("role", ["critic", "pov", "auditor"])
def test_a_correctly_wired_config_runs_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: str
) -> None:
    """서빙 프로필이 고른 판은 가드를 지나 `execute`까지 간다 — 가드가 멀쩡한 설정을 막으면 모든
    run이
    시작을 못 한다(거절 검사의 양성 쌍)."""
    calls = _serving(monkeypatch, _seated(role, _served_version(role)))

    with pytest.raises(_Started):
        cli.main([*_RUN_ARGS, "--artifacts-root", str(tmp_path / "arts")])

    assert calls == ["execute"]


def test_a_warning_alone_does_not_block_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """디스크에 새 판이 있다는 WARN은 `run`을 막지 않고 찍지도 않는다(`doctor`가 알린다)."""
    config = load_config("dev_fake")
    warns = [c for c in cli._check_prompts(config) if c.severity is cli.Severity.WARN]
    assert warns, "전제: 이 설정은 WARN을 낸다"
    _serving(monkeypatch, config)

    with pytest.raises(_Started):
        cli.main([*_RUN_ARGS, "--artifacts-root", str(tmp_path / "arts")])

    assert "새 판" not in capsys.readouterr().err


@pytest.mark.parametrize(
    ("role", "version", "extra", "blocked"),
    [
        pytest.param("pov", "v2", ["--stop-after", "4"], False, id="pov-not-reached"),
        pytest.param("pov", "v2", ["--stop-after", "7"], False, id="pov-stage-8-not-reached"),
        pytest.param("pov", "v2", ["--stop-after", "8"], True, id="pov-reached"),
        pytest.param("critic", "v1", ["--stop-after", "6"], False, id="critic-not-reached"),
        pytest.param("critic", "v1", ["--stop-after", "7"], True, id="critic-reached"),
        pytest.param("auditor", "v1", ["--stop-after", "4"], False, id="auditor-not-reached"),
        pytest.param("auditor", "v1", ["--stop-after", "5"], True, id="auditor-reached"),
        pytest.param("critic", "v1", ["--dry-run"], False, id="dry-run-reaches-no-model"),
    ],
)
def test_run_only_checks_the_roles_it_reaches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    role: str,
    version: str,
    extra: list[str],
    blocked: bool,
) -> None:
    """안 부를 역할의 프롬프트를 이유로 막지 않는다 — `--stop-after 4` 슬라이싱·Scout run이 8단계
    PoV
    프롬프트 때문에 막히면 부분 run이 못 돈다(Auditor 루프 가드·`--slices` 가드와 같은 판단)."""
    calls = _serving(monkeypatch, _seated(role, version))
    args = [*_RUN_ARGS, *extra, "--artifacts-root", str(tmp_path / "arts")]

    if blocked:
        assert cli.main(args) == 2
        assert calls == []
    else:
        with pytest.raises(_Started):
            cli.main(args)
        assert calls == ["execute"]
