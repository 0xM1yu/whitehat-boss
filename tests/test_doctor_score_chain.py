"""S1: 접속 없이 점수 사슬의 설정 결함과 낡은 프롬프트 배선을 드러낸다."""

import pytest

from src import cli
from src.config import HarnessConfig, load_config
from src.orchestrator import prompt_loader
from src.orchestrator.nodes import auditor, critic, pov
from src.orchestrator.prompt_loader import PromptError


def _checks(monkeypatch, tmp_path, config):
    monkeypatch.setattr(cli, "load_profile", lambda *_a, **_k: config)
    return cli.doctor("renamed-profile", artifacts_root=tmp_path)


def _chain(checks):
    return [check for check in checks if "점수 사슬" in check.message]


def _version(config, role, version):
    key = config.profile.roles[role]
    models = dict(config.models)
    models[key] = models[key].transition(prompt_template_version=version)
    return config.transition(models=models)


@pytest.mark.parametrize("profile", ["harness_pool", "harness_pool_hybrid"])
def test_real_profile_with_both_switches_off_errors(monkeypatch, tmp_path, profile):
    # 양성 원천: config/budgets.yaml의 thresholds.pov_attempt_top_n/synthesize_repro_spec.
    config = load_config(profile)
    assert config.served_models() and not config.profile.production
    config = config.transition(thresholds=config.thresholds.transition(
        pov_attempt_top_n=0, synthesize_repro_spec=0
    ))
    checks = _chain(_checks(monkeypatch, tmp_path, config))
    assert any(c.severity == cli.Severity.ERROR and "validated" in c.message for c in checks)


def test_real_profile_without_pov_errors(monkeypatch, tmp_path):
    config = load_config("harness_pool_hybrid")
    roles = {role: key for role, key in config.profile.roles.items() if role != "pov"}
    config = config.transition(profile=config.profile.transition(roles=roles))
    checks = _chain(_checks(monkeypatch, tmp_path, config))
    assert any(c.severity == cli.Severity.ERROR and "pov 좌석" in c.message for c in checks)


@pytest.mark.parametrize("attempts", [0, 1])
def test_synthesis_warns_about_232_even_with_attempts(monkeypatch, tmp_path, attempts):
    config = load_config("harness_pool")
    config = config.transition(thresholds=config.thresholds.transition(
        synthesize_repro_spec=1, pov_attempt_top_n=attempts
    ))
    checks = _chain(_checks(monkeypatch, tmp_path, config))
    assert any(c.severity == cli.Severity.WARN and "#232" in c.message for c in checks)
    assert any("기대 신호" in c.message for c in checks)
    assert not any(c.severity == cli.Severity.ERROR for c in checks)


def test_attempts_enable_the_structural_path(monkeypatch, tmp_path):
    config = load_config("harness_pool")
    config = config.transition(thresholds=config.thresholds.transition(
        pov_attempt_top_n=1, synthesize_repro_spec=0
    ))
    checks = _chain(_checks(monkeypatch, tmp_path, config))
    assert len(checks) == 1 and checks[0].severity == cli.Severity.OK


@pytest.mark.parametrize("role", ["critic", "pov", "auditor"])
def test_old_real_prompt_outside_content_set_errors(monkeypatch, tmp_path, role):
    # 양성: critic v1은 착수 main fc06dee의 config/models.yaml 배선 그대로다.
    # pov v2도 같은 main의 배선. auditor v1은 디스크에 남아 있는 과거 판이다.
    config = _version(load_config("harness_pool"), role, "v2" if role == "pov" else "v1")
    checks = _checks(monkeypatch, tmp_path, config)
    assert any(c.severity == cli.Severity.ERROR and role in c.message
               and "내용 인식" in c.message for c in checks)


# pov는 v3(2026-10-05~10-08 서빙 판)와 v4(2026-10-08~ 서빙 판) 둘 다 넓은 입력 판이다.
@pytest.mark.parametrize(
    "role,version",
    [("critic", "v5"), ("pov", "v3"), ("pov", "v4"), ("auditor", "v7")],
)
def test_content_aware_prompt_does_not_error(monkeypatch, tmp_path, role, version):
    config = _version(load_config("harness_pool"), role, version)
    checks = _checks(monkeypatch, tmp_path, config)
    assert not any(c.severity == cli.Severity.ERROR and role in c.message
                   and "내용 인식" in c.message for c in checks)


@pytest.mark.parametrize("profile", [
    "dev_fake", "dev_hybrid", "dev_docker", "dev_singularity", "dev_hybrid_docker"
])
def test_fake_profiles_are_exempt_but_newer_disk_versions_warn(monkeypatch, tmp_path, profile):
    config = load_config(profile)
    assert not config.served_models()
    checks = _checks(monkeypatch, tmp_path, config)
    assert not any(c.severity == cli.Severity.ERROR for c in _chain(checks))
    assert not any(c.severity == cli.Severity.ERROR and "내용 인식" in c.message for c in checks)
    assert any(c.severity == cli.Severity.WARN and "critic" in c.message
               and "새 판" in c.message for c in checks)


def test_missing_prompt_keeps_existing_load_error(monkeypatch, tmp_path):
    config = _version(load_config("harness_pool"), "critic", "v999")
    checks = _checks(monkeypatch, tmp_path, config)
    assert any(c.severity == cli.Severity.ERROR and "critic 프롬프트 로드 실패" in c.message
               for c in checks)


@pytest.mark.parametrize("role,module,attribute,version", [
    ("critic", critic, "_CONTENT_AWARE_VERSIONS", "v5"),
    ("pov", pov, "_WIDE_INPUT_VERSIONS", "v3"),
    # v4가 서빙 판이다(2026-10-08) - 집합에서 v4가 빠지면 넓은 입력이 조용히 꺼진다(그 사고를
    # doctor가 막는다).
    ("pov", pov, "_WIDE_INPUT_VERSIONS", "v4"),
    ("auditor", auditor, "_CONTENT_AWARE_VERSIONS", "v7"),
])
def test_reads_the_nodes_live_content_set(monkeypatch, tmp_path, role, module, attribute, version):
    config = _version(load_config("harness_pool"), role, version)
    monkeypatch.setattr(module, attribute, frozenset())
    checks = _checks(monkeypatch, tmp_path, config)
    assert any(c.severity == cli.Severity.ERROR and role in c.message
               and "내용 인식" in c.message for c in checks)


def test_cli_exit_is_one_for_real_score_chain(monkeypatch, tmp_path, capsys):
    # pov 판을 v2로 박는다 - 서빙 판이 `pov._WIDE_INPUT_VERSIONS`에 들면(v3 PR #499, v4 2026-10-08)
    # 이 오류가 안 나는 것이 정상이라 「서빙 프로필의 현재 판」에 기대면 머지 순서에 따라 이
    # 테스트가 깨진다.
    config: HarnessConfig = _version(load_config("harness_pool"), "pov", "v2")
    monkeypatch.setattr(cli, "load_profile", lambda *_a, **_k: config)
    assert cli.main(["doctor", "--profile", "harness_pool", "--artifacts-root", str(tmp_path)]) == 1
    assert "점수 사슬" in capsys.readouterr().out


def test_newer_versions_use_numeric_order_and_require_three_files(monkeypatch, tmp_path):
    for version in ("v2", "v9", "v10", "v11", "v99_trial", "v100"):
        directory = tmp_path / "critic" / version
        directory.mkdir(parents=True)
        for name in ("prefix.md", "body.md", "meta.yaml"):
            if version != "v100" or name != "meta.yaml":
                (directory / name).write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(prompt_loader, "PROMPTS_DIR", tmp_path)
    config = _version(load_config("harness_pool"), "critic", "v9")
    checks = cli._check_prompt_version(config, "critic", "v9")
    warnings = [c.message for c in checks if c.severity == cli.Severity.WARN]
    assert len(warnings) == 1 and "새 판 v11" in warnings[0]
    assert not [c for c in cli._check_prompt_version(config, "critic", "v11")
                if c.severity == cli.Severity.WARN]
    assert not [c for c in cli._check_prompt_version(config, "critic", "v99_trial")
                if c.severity == cli.Severity.WARN]


def test_mixed_profile_exempts_only_the_fake_role(monkeypatch, tmp_path):
    config = load_config("harness_pool_hybrid")
    roles = {**config.profile.roles, "critic": "fake"}
    config = config.transition(profile=config.profile.transition(roles=roles))
    config = _version(config, "pov", "v2")  # 위 테스트와 같은 사유 - v3·v4는 내용 인식 판이다
    assert config.served_models()
    checks = _checks(monkeypatch, tmp_path, config)
    assert not any(c.severity == cli.Severity.ERROR and "critic" in c.message
                   and "내용 인식" in c.message for c in checks)
    assert any(c.severity == cli.Severity.ERROR and "pov" in c.message
               and "내용 인식" in c.message for c in checks)


def test_missing_pov_keeps_synthesis_warning(monkeypatch, tmp_path):
    config = load_config("harness_pool_hybrid")
    roles = {role: key for role, key in config.profile.roles.items() if role != "pov"}
    config = config.transition(
        profile=config.profile.transition(roles=roles),
        thresholds=config.thresholds.transition(synthesize_repro_spec=1),
    )
    checks = _chain(_checks(monkeypatch, tmp_path, config))
    assert {c.severity for c in checks} == {cli.Severity.ERROR, cli.Severity.WARN}


def test_unused_real_retrieval_entries_do_not_turn_fake_profile_real(monkeypatch, tmp_path):
    config = load_config("dev_fake")
    real = load_config("harness_pool")
    roles = {**config.profile.roles, "embedding": real.profile.roles["embedding"],
             "reranker": real.profile.roles["reranker"]}
    config = config.transition(profile=config.profile.transition(roles=roles))
    assert not config.served_models()
    assert not _chain(_checks(monkeypatch, tmp_path, config))


def _trim_checks(monkeypatch, wired):
    """`_check_scout_slice_trim`을 `scout_slice_trim=1`로 부른다.

    `wired`는 `_slice_input_prompts`의 답(예외 클래스면 던진다) — 저장소 설정은
    `input: slice` 판을 안 골라(PR #524) 갈아끼운다."""
    config = load_config("harness_pool")
    config = config.transition(thresholds=config.thresholds.transition(scout_slice_trim=1))

    def _wired(_config):
        if isinstance(wired, type):
            raise wired("scout/v9: 읽을 수 없다")
        return wired

    monkeypatch.setattr(cli, "_slice_input_prompts", _wired)
    return cli._check_scout_slice_trim(config)


def test_scout_slice_trim_stays_silent_when_every_wired_scout_is_source(monkeypatch):
    # 음성: 배선된 `input: slice` 판이 없다(서빙 v4 = `input: source`) -> 줄을 뺄 렌더가 없다(#529).
    assert _trim_checks(monkeypatch, []) == []


def test_scout_slice_trim_warns_when_a_wired_scout_reads_slices(monkeypatch):
    # 양성 원천: 이 가드의 원래 대상 — v3 Scout(`input: slice`)가 배선된 설정(#375 M-1).
    checks = _trim_checks(monkeypatch, ["scout_memory/v3"])
    assert [c.severity for c in checks] == [cli.Severity.WARN]
    assert "scout_slice_trim" in checks[0].message


def test_scout_slice_trim_keeps_the_warn_when_the_prompt_cannot_be_read(monkeypatch):
    # 로드 실패는 `_check_prompts`가 ERROR로 댄다 — 여기서는 모른다고 조용해지지 않고 WARN을 살린다.
    checks = _trim_checks(monkeypatch, PromptError)
    assert [c.severity for c in checks] == [cli.Severity.WARN]


def _trim_checks_with_scout_version(version):
    """실 `_slice_input_prompts`(갈아끼우지 않음)로 세 Scout 좌석이 고른 판을 갈라 부른다."""
    config = load_config("harness_pool")
    config = config.transition(thresholds=config.thresholds.transition(scout_slice_trim=1))
    for role in ("scout_memory", "scout_boundary", "scout_injection"):
        config = _version(config, role, version)
    return cli._check_scout_slice_trim(config)


def test_scout_slice_trim_follows_the_wired_prompts_input_declaration():
    # 양성 원천: prompts/scout_*/v3/meta.yaml이 `input: slice`, v4가 `input: source`(#529).
    # 위 테스트들은 `_slice_input_prompts`를 갈아끼운다 — 이쪽은 meta 선언 두 값을 실제로 읽는다.
    assert _trim_checks_with_scout_version("v4") == []
    assert [c.severity for c in _trim_checks_with_scout_version("v3")] == [cli.Severity.WARN]
