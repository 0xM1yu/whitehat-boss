"""doctor가 실서빙 역할의 토크나이저 부재(= `len//4` 근사)를 WARN으로 말한다."""

from src import cli
from src.config import load_config


def _tokenizer_checks(monkeypatch, tmp_path, profile, reason):
    monkeypatch.setattr(cli, "counter_reason_for_source", lambda _source: reason)
    checks = cli.doctor(profile, artifacts_root=tmp_path)
    return [c for c in checks if "토크나이저" in c.message]


def test_absent_tokenizer_file_warns_per_served_role(monkeypatch, tmp_path):
    # 양성 원천: harness_pool은 실서빙 역할을 든다(served_models 비지 않음).
    assert load_config("harness_pool").served_models()
    (check,) = _tokenizer_checks(monkeypatch, tmp_path, "harness_pool", "file_absent")
    assert check.severity == cli.Severity.WARN
    assert "(file_absent)" in check.message and "auditor" in check.message
    assert "embedding" not in check.message  # 검색 역할은 프롬프트를 안 만든다
    # 1.8배는 실측 한 점이다(범위는 1.22~3.32) - 상한처럼 쓰지 않는다.
    assert "한 점" in check.message and "최대" not in check.message


def test_only_roles_that_count_tokens_are_checked(monkeypatch, tmp_path):
    (check,) = _tokenizer_checks(monkeypatch, tmp_path, "harness_pool", "file_absent")
    served = load_config("harness_pool").served_models()
    # 양성 원천: critic·pov도 실서빙 역할이지만 카운터를 안 켠다(tokens.COUNTED_ROLES).
    assert {"critic", "pov"} & set(served)
    assert "critic(" not in check.message and "pov(" not in check.message


def test_unloadable_tokenizer_file_is_an_error_not_a_crash(monkeypatch, tmp_path):
    def boom(_source):
        raise ValueError("EOF while parsing")

    monkeypatch.setattr(cli, "counter_reason_for_source", boom)
    checks = cli.doctor("harness_pool", artifacts_root=tmp_path)
    (check,) = [c for c in checks if "토크나이저" in c.message]
    assert check.severity == cli.Severity.ERROR
    assert "ValueError" in check.message and "auditor" in check.message


def test_available_tokenizers_and_all_fake_profiles_do_not_warn(monkeypatch, tmp_path):
    (ok,) = _tokenizer_checks(monkeypatch, tmp_path, "harness_pool", None)
    assert ok.severity == cli.Severity.OK
    # 음성: Fake만 도는 프로필은 served가 비어 사유가 와도 대상이 없다.
    assert not load_config("dev_fake").served_models()
    (fake,) = _tokenizer_checks(monkeypatch, tmp_path, "dev_fake", "file_absent")
    assert fake.severity == cli.Severity.OK
