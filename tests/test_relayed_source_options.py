"""Critic·PoV 좌석의 SOURCE 옵션 가드 (PR #676 · 사용자 승인 C1 「+guard」).

Auditor가 만든 `SOURCE` 문자열은 `runner.finding_sources`로 Critic
(`critic._SOURCE_INPUT_VERSIONS`)과 8단계 PoV(`pov._WIDE_INPUT_VERSIONS`)에도 같은 바이트로 간다.
`auditor_write_window_marker`(표지 줄)와 `auditor_input_context`(입력 디코더 블록)를 켜면 그 블록이
두 좌석의 입력에도 든다 - 서술하는 판은 Critic v8 · PoV v7부터다(meta `source_options`). 켠 옵션을
좌석의 판이 선언하지 않으면 `cli._undescribed_relayed_options`가 run·doctor에서 막는다. Auditor 쪽은
기존 가드(`cli._undescribed_source_options`)가 맡는다.

묶는 것: ① 서빙 조합은 통과 ② Critic v7 · PoV v6은 옵션마다 이름을 붙여 거절 ③ SOURCE를 안 받는 판 ·
Fake 좌석 · 안 닿는 단계 · 꺼진 옵션은 거절하지 않음 ④ 선언 <=> prefix 서술(산문이 코드와 맞는다)
⑤ doctor가 같은 ERROR를 낸다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src import cli
from src.config import RELAYED_SOURCE_OPTIONS, HarnessConfig, load_config
from src.orchestrator.nodes import critic, pov
from src.orchestrator.prompt_loader import PROMPTS_DIR, PromptError, load_prompt
from src.orchestrator.stages import Stage
from src.schemas.enums import Role

MARKER = "auditor_write_window_marker"
CONTEXT = "auditor_input_context"


def _with_version(role: str, version: str, base: HarnessConfig | None = None) -> HarnessConfig:
    config = base or load_config("harness_pool")
    key = config.model_key_for(role)
    entry = config.models[key].transition(prompt_template_version=version)
    return config.transition(models={**config.models, key: entry})


def _with_options(config: HarnessConfig, **values: int) -> HarnessConfig:
    return config.transition(thresholds=config.thresholds.transition(**values))


# === 1. 서빙 조합은 통과한다 ===============================================================


def test_the_serving_combination_passes_every_source_option_check() -> None:
    config = load_config("harness_pool")
    seats = ("auditor", "critic", "pov")
    versions = {role: config.model_for(role).prompt_template_version for role in seats}
    assert versions == {"auditor": "v17", "critic": "v8", "pov": "v7"}, "전제 - 서빙 조합"
    on = config.thresholds.auditor_source_options_on()
    assert {MARKER, CONTEXT} <= set(on), "전제 - 두 옵션이 켜져 있다"
    assert cli._undescribed_source_options(config) == []
    assert cli._undescribed_relayed_options(config) == []
    assert cli._check_source_options(config) == []


# === 2. 선언하지 않는 판은 옵션마다 이름을 붙여 거절한다 =====================================


def test_critic_v7_with_both_options_on_is_refused_by_name() -> None:
    config = _with_version("critic", "v7")
    assert cli._undescribed_relayed_options(config) == [
        f"{MARKER}(critic/v7)", f"{CONTEXT}(critic/v7)",
    ]
    (check,) = cli._check_source_options(config)
    assert check.severity.name == "ERROR"
    assert "critic/v7" in check.message and MARKER in check.message and CONTEXT in check.message


@pytest.mark.parametrize(("only", "off"), [(MARKER, CONTEXT), (CONTEXT, MARKER)])
def test_critic_v7_with_one_option_on_names_only_that_option(only: str, off: str) -> None:
    config = _with_options(_with_version("critic", "v7"), **{off: 0})
    assert cli._undescribed_relayed_options(config) == [f"{only}(critic/v7)"]


def test_pov_v6_is_refused_by_name_and_both_seats_are_reported_together() -> None:
    config = _with_version("pov", "v6")
    assert cli._undescribed_relayed_options(config) == [
        f"{MARKER}(pov/v6)", f"{CONTEXT}(pov/v6)",
    ]
    both = _with_version("critic", "v7", config)
    assert cli._undescribed_relayed_options(both) == [
        f"{MARKER}(critic/v7)", f"{CONTEXT}(critic/v7)",
        f"{MARKER}(pov/v6)", f"{CONTEXT}(pov/v6)",
    ]


# === 3. 거절하지 않는 경우 ================================================================


def test_off_options_never_block_any_seat_version() -> None:
    config = _with_options(_with_version("pov", "v6", _with_version("critic", "v7")),
                           **{MARKER: 0, CONTEXT: 0})
    assert cli._undescribed_relayed_options(config) == []
    assert cli._check_source_options(config) == []


@pytest.mark.parametrize(("role", "version"), [("critic", "v5"), ("pov", "v2")])
def test_a_seat_version_that_receives_no_source_is_not_refused(role: str, version: str) -> None:
    """SOURCE 문자열이 안 가는 판은 서술할 것이 없다(집합 밖) - 거절하면 거짓 막힘이다."""
    versions = {"critic": critic._SOURCE_INPUT_VERSIONS, "pov": pov._WIDE_INPUT_VERSIONS}[role]
    assert version not in versions, "전제 - 이 판은 SOURCE를 안 받는다"
    assert cli._undescribed_relayed_options(_with_version(role, version)) == []


def test_every_source_receiving_version_is_judged_by_its_own_declaration() -> None:
    """집합 안의 판은 선언한 판(v8 · v7)만 통과한다 - 집합이 늘어도 선언 없는 판이 새지 않는다."""
    for role, versions in (("critic", critic._SOURCE_INPUT_VERSIONS),
                           ("pov", pov._WIDE_INPUT_VERSIONS)):
        for version in sorted(versions):
            declared = set(load_prompt(Role(role), version).source_options)
            got = cli._undescribed_relayed_options(_with_version(role, version))
            assert (got == []) == (declared >= set(RELAYED_SOURCE_OPTIONS)), (role, version)


def test_a_fake_seat_is_not_refused() -> None:
    config = load_config("dev_fake")
    assert cli._undescribed_relayed_options(
        _with_options(config, **{MARKER: 1, CONTEXT: 1})
    ) == []


def test_the_check_only_covers_seats_the_run_reaches() -> None:
    """`--stop-after`가 8단계 PoV 전이면 PoV 판을 안 보고, 7단계 Critic 전이면 Critic도 안 본다."""
    config = _with_version("pov", "v6", _with_version("critic", "v7"))
    assert cli._undescribed_relayed_options(config, last_stage=Stage.AUDITOR) == []
    assert cli._undescribed_relayed_options(config, last_stage=Stage.CRITIC) == [
        f"{MARKER}(critic/v7)", f"{CONTEXT}(critic/v7)",
    ]
    assert len(cli._undescribed_relayed_options(config, last_stage=Stage.VALIDATE)) == 4


# === 4. 선언 <=> prefix 서술 (산문이 코드와 맞는다) ========================================


def _declaring(role: str) -> list[str]:
    return sorted(
        path.name for path in (PROMPTS_DIR / role).iterdir()
        if path.is_dir() and load_prompt(Role(role), path.name).source_options
    )


def test_only_critic_v8_and_pov_v7_declare_and_they_declare_exactly_the_relayed_pair() -> None:
    assert _declaring("critic") == ["v8"] and _declaring("pov") == ["v7"]
    for role, version in (("critic", "v8"), ("pov", "v7")):
        assert load_prompt(Role(role), version).source_options == RELAYED_SOURCE_OPTIONS


@pytest.mark.parametrize(
    ("role", "version", "phrases"),
    [
        ("critic", "v8", ("input-context functions", "+N more write windows not shown")),
        ("pov", "v7", ("functions the slicer picked", "+N more write windows not shown")),
    ],
)
def test_a_declaring_prefix_really_describes_both_blocks(
    role: str, version: str, phrases: tuple[str, ...]
) -> None:
    """선언이 거짓이면(prefix가 그 블록을 안 말하면) 가드가 서술 없는 판을 통과시킨다."""
    prefix = load_prompt(Role(role), version).prefix
    for phrase in phrases:
        assert phrase in prefix, (role, version, phrase)


# === 5. doctor가 같은 ERROR를 낸다 =========================================================


def test_doctor_reports_the_refusal_as_an_error_and_not_for_the_serving_combo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def errors(config: HarnessConfig) -> list[str]:
        monkeypatch.setattr(cli, "load_profile", lambda *a, **k: config)
        checks = cli.doctor("harness_pool", artifacts_root=tmp_path)
        return [
            c.message for c in checks
            if c.severity.name == "ERROR" and "SOURCE 옵션" in c.message
        ]

    assert errors(load_config("harness_pool")) == []
    (message,) = errors(_with_version("critic", "v7"))
    assert "critic/v7" in message and MARKER in message


def test_a_critic_prompt_load_failure_does_not_discard_the_auditor_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """한 try에 둘을 묶으면 Critic 판 로드 실패(`PromptError`)가 **이미 계산한** Auditor ERROR를
    버렸다 - 둘은 독립으로 보고한다. Auditor 판 v15는 옵션 둘을 서술하지 않는다(전제)."""
    config = _with_version("auditor", "v15")
    assert cli._undescribed_source_options(config), "전제 - Auditor v15는 옵션을 서술하지 않는다"
    real_load = cli.load_prompt

    def load(role: Role, version: str):  # noqa: ANN202
        if role == Role.CRITIC:
            raise PromptError("critic 프롬프트를 못 읽었다")
        return real_load(role, version)

    monkeypatch.setattr(cli, "load_prompt", load)
    (check,) = cli._check_source_options(config)
    assert check.severity.name == "ERROR"
    assert "Auditor SOURCE 옵션" in check.message and "auditor/v15" in check.message

    monkeypatch.setattr(cli, "load_profile", lambda *a, **k: config)
    checks = cli.doctor("harness_pool", artifacts_root=tmp_path)
    assert any(
        c.severity.name == "ERROR"
        and "Auditor SOURCE 옵션" in c.message
        and "auditor/v15" in c.message
        for c in checks
    )


# === 6. run의 검사 순서 ====================================================================


def test_run_prints_the_auditor_refusal_before_a_critic_prompt_load_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """`run`은 Auditor 거절을 **먼저** 낸다 - Critic 판 로드 실패(`PromptError`)가 그 메시지를
    가리면 안 된다(`cli.run`의 두 검사 블록 순서). 앞선 `_check_prompts`·`_slice_input_prompts`는
    같은 실패를 다른 문구로 먼저 막으므로 비워 이 두 블록만 남긴다."""
    config = _with_version("auditor", "v15")
    assert cli._undescribed_source_options(config), "전제 - Auditor v15는 옵션을 서술하지 않는다"
    real_load = cli.load_prompt

    def load(role: Role, version: str):  # noqa: ANN202
        if role == Role.CRITIC:
            raise PromptError("critic 프롬프트를 못 읽었다")
        return real_load(role, version)

    monkeypatch.setattr(cli, "load_prompt", load)
    monkeypatch.setattr(cli, "load_profile", lambda *a, **k: config)
    monkeypatch.setattr(cli, "_check_prompts", lambda *a, **k: [])
    monkeypatch.setattr(cli, "_slice_input_prompts", lambda *a, **k: [])
    target = Path(__file__).resolve().parent / "fixtures" / "target"

    rc = cli.main([
        "run", "--profile", "harness_pool", "--target", str(target), "--seed", "1",
        "--condition", "B3prime@pipeline", "--device", "atom", "--kb-snapshot", "kb-1",
        "--artifacts-root", str(tmp_path / "arts"),
    ])

    err = capsys.readouterr().err
    assert rc == 2
    assert "Auditor SOURCE 옵션" in err and "auditor/v15" in err
    assert "프롬프트 로드 실패" not in err
