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


def test_available_tokenizers_and_all_fake_profiles_do_not_warn(monkeypatch, tmp_path):
    (ok,) = _tokenizer_checks(monkeypatch, tmp_path, "harness_pool", None)
    assert ok.severity == cli.Severity.OK
    # 음성: Fake만 도는 프로필은 served가 비어 사유가 와도 대상이 없다.
    assert not load_config("dev_fake").served_models()
    (fake,) = _tokenizer_checks(monkeypatch, tmp_path, "dev_fake", "file_absent")
    assert fake.severity == cli.Severity.OK
