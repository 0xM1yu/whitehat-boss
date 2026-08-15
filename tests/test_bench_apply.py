"""bench의 registry 반영(`--apply`)·CLI·`--tool-runner` 오버라이드 계약.

측정 로직은 `tests/test_bench.py`에 있다. 나눈 이유는 800줄 상한(engineering-guide.md
§8.1)이고, 경계는 **잰 값을 어떻게 다루는가**다 — 여기서 고정하는 것은 못 잰 값이
registry에 새어 들어가지 않는다는 것과, 오버라이드가 config·doctor·RunRecord 세 곳에
같은 값으로 흐른다는 것이다.

`--apply` 검사는 원본 `config/models.yaml`을 건드리지 않는다. 전부 `tmp_path` 사본에 쓴다.
"""

import json
from pathlib import Path

import pytest
import yaml

from src import bench_report, cli
from src.adapters.factory import WIRINGS
from src.bench import BenchApplyError
from src.config import CONFIG_DIR, Benchmark, load_config
from src.orchestrator.runner import RunSpec

RUN_ID = "run_20260101_0001"
TARGET = Path(__file__).resolve().parent / "fixtures" / "target"
MEASURED_AT = "2026-08-17T00:00:00Z"

# scout만 실서빙인 프로필. registry 갱신 대상 모델이 실재해야 apply 검사가 공허하지 않다.
SERVED_PROFILE = "single_gpu_12g"


# --- --apply -----------------------------------------------------------------


def _models_copy(tmp_path: Path) -> Path:
    """원본을 건드리지 않는다. 주석 보존 검사는 이 사본에서 한다."""
    copy = tmp_path / "models.yaml"
    copy.write_text((CONFIG_DIR / "models.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    return copy


def _comments(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if "#" in line]


def test_apply_preserves_the_comments_that_are_the_documentation(tmp_path: Path) -> None:
    """models.yaml의 주석이 곧 문서다. `yaml.safe_dump`로 다시 쓰면 전부 날아간다."""
    path = _models_copy(tmp_path)
    before = _comments(path)

    bench_report.apply_benchmarks(path, {"qwen3-4b": {"tok_s_single": 12.5}}, MEASURED_AT)

    assert _comments(path) == before
    assert "tok_s_single: 12.5" in path.read_text(encoding="utf-8")


def test_apply_never_writes_a_zero(tmp_path: Path) -> None:
    """`Benchmark.is_measured`가 0 여부로 판정한다.

    못 잰 값을 넣으면 미측정 모델이 측정된 것으로 보이고 production 프로필로 가는 길이 열린다.
    """
    report = {
        "roles": {
            "scout": {
                "model_key": "qwen3-4b",
                "startup": {"ok": True},
                "skipped": None,
                "throughput": {
                    "tok_s_single": 30.0,
                    "tok_s_batch32": 0.0,
                    "tok_s_batch32_reason": "--concurrency에 32가 없어 재지 않았다",
                },
                "memory": {
                    "mem_gb_ctx8k": 0.0,
                    "mem_gb_ctx8k_reason": "nvidia-smi가 없어 GB를 얻지 못했다",
                    "gpu_memory_utilization": 0.9,
                },
            }
        }
    }

    updates, skipped = bench_report.pending_updates(report)

    assert updates == {"qwen3-4b": {"tok_s_single": 30.0}}
    assert any("tok_s_batch32" in reason for reason in skipped)

    path = _models_copy(tmp_path)
    bench_report.apply_benchmarks(path, updates, MEASURED_AT)
    text = path.read_text(encoding="utf-8")
    assert "tok_s_single: 30.0" in text
    assert "tok_s_batch32: 0\n" in text  # 그대로 0이다


def test_apply_refuses_memory_without_gpu_memory_utilization() -> None:
    """그 비율 없이 적힌 점유는 다른 사람이 재현할 수 없다 — registry에 들어갈 자격이 없다."""
    report = {
        "roles": {
            "scout": {
                "model_key": "qwen3-4b",
                "startup": {"ok": True},
                "skipped": None,
                "throughput": {
                    "tok_s_single": 30.0,
                    "tok_s_batch32": 90.0,
                    "tok_s_batch32_reason": None,
                },
                "memory": {
                    "mem_gb_ctx8k": 7.5,
                    "mem_gb_ctx8k_reason": None,
                    "gpu_memory_utilization": None,
                    "gpu_memory_utilization_reason": "cache_config_info 라벨이 없다",
                },
            }
        }
    }

    updates, skipped = bench_report.pending_updates(report)

    assert "mem_gb_ctx8k" not in updates["qwen3-4b"]
    assert any("재현 불가" in reason for reason in skipped)


def test_apply_writes_nothing_when_the_block_is_not_found(tmp_path: Path) -> None:
    """부분적으로 망가진 registry가 갱신 실패보다 훨씬 비싸다."""
    path = _models_copy(tmp_path)
    before = path.read_bytes()

    with pytest.raises(BenchApplyError):
        bench_report.apply_benchmarks(path, {"없는-모델": {"tok_s_single": 1.0}}, MEASURED_AT)

    assert path.read_bytes() == before


def test_apply_is_all_or_nothing_across_models(tmp_path: Path) -> None:
    """앞 모델을 고치고 뒤에서 실패하면 registry가 절반만 갱신된 채 남는다."""
    path = _models_copy(tmp_path)
    before = path.read_bytes()

    with pytest.raises(BenchApplyError):
        bench_report.apply_benchmarks(
            path,
            {"qwen3-4b": {"tok_s_single": 12.5}, "없는-모델": {"tok_s_single": 1.0}},
            MEASURED_AT,
        )

    assert path.read_bytes() == before


def test_applied_registry_still_loads(tmp_path: Path) -> None:
    """갱신 결과가 스키마를 통과해야 한다. 통과 못 하면 다음 run이 기동에서 죽는다."""
    for name in ("profiles.yaml", "budgets.yaml"):
        (tmp_path / name).write_text((CONFIG_DIR / name).read_text("utf-8"), encoding="utf-8")
    path = _models_copy(tmp_path)

    bench_report.apply_benchmarks(
        path, {"qwen3-4b": {"tok_s_single": 12.5, "mem_gb_ctx8k": 8.25}}, MEASURED_AT
    )

    config = load_config(SERVED_PROFILE, config_dir=tmp_path)
    benchmark = config.models["qwen3-4b"].benchmark
    assert benchmark.is_measured
    assert benchmark.tok_s_single == 12.5
    assert benchmark.measured_at is not None


# --- measured_on -------------------------------------------------------------


def _loaded(tmp_path: Path, key: str = "qwen3-4b") -> Benchmark:
    """갱신 결과를 실제 로더로 되읽는다. 문자열 검사만으로는 스키마 통과를 못 본다."""
    for name in ("profiles.yaml", "budgets.yaml"):
        (tmp_path / name).write_text((CONFIG_DIR / name).read_text("utf-8"), encoding="utf-8")
    return load_config(SERVED_PROFILE, config_dir=tmp_path).models[key].benchmark


def test_apply_keys_cover_every_benchmark_field() -> None:
    """`APPLY_KEYS`가 `Benchmark`의 필드와 같은지 본다.

    필드가 하나 늘었는데 `APPLY_KEYS`가 그대로면 그 값은 registry에 영영 안 쓰인다.
    **이 검사가 생기기 전에는 그 상수를 아무도 읽지 않아** "필드와 같아야 한다"는 주석이
    지켜지는지 알 방법이 없었다 — 이 저장소가 계속 고쳐 온 "선언과 실제의 괴리"다.
    """
    assert set(bench_report.APPLY_KEYS) == set(Benchmark.model_fields)


def test_apply_records_which_device_measured_it(tmp_path: Path) -> None:
    """수치와 함께 **어디서 쟀는지**가 registry에 남는다.

    두 장비가 모두 지원 대상이므로(docs/devices.md) 같은 필드에 다른 하드웨어의 값이
    들어갈 수 있다. 그 사실이 남지 않으면 다음 사람이 이 수치로 다른 장비를 예측한다.
    """
    path = _models_copy(tmp_path)

    bench_report.apply_benchmarks(
        path, {"qwen3-4b": {"tok_s_single": 12.5}}, MEASURED_AT, measured_on="atom"
    )

    benchmark = _loaded(tmp_path)
    assert benchmark.measured_on == "atom"
    assert benchmark.measured_at is not None


def test_an_unknown_device_is_written_as_null_not_guessed(tmp_path: Path) -> None:
    """장비를 모른 채 쓴 수치라는 사실 자체가 기록이어야 한다.

    빈 문자열이나 호스트명 추측으로 메우면 그 자리에 거짓이 들어간다 — `repo_dirty`가
    `bool | None`인 것과 같은 이유다("확인 못 함"을 값과 섞지 않는다).
    """
    path = _models_copy(tmp_path)

    bench_report.apply_benchmarks(path, {"qwen3-4b": {"tok_s_single": 12.5}}, MEASURED_AT)

    assert "measured_on: null" in path.read_text(encoding="utf-8")
    assert _loaded(tmp_path).measured_on is None


def test_every_registry_block_has_the_line_apply_needs(tmp_path: Path) -> None:
    """`_value_line()`은 라인이 없으면 `BenchApplyError`로 죽는다.

    스키마 기본값만으로는 부족하다 — `--apply`는 YAML을 라인 단위로 치환하므로 그 키가
    파일에 **실제로 적혀 있어야** 한다. 한 모델이라도 빠지면 그 모델의 갱신이 통째로
    실패하고, 그것을 ATOM 실측 현장에서 알게 되는 것이 최악이다.
    """
    path = _models_copy(tmp_path)
    every_model = yaml.safe_load(path.read_text(encoding="utf-8"))["models"]

    outcome = bench_report.apply_benchmarks(
        path,
        {key: {"tok_s_single": 1.0} for key in every_model},
        MEASURED_AT,
        measured_on="slurm",
    )

    assert set(outcome["updated"]) == set(every_model)
    assert path.read_text(encoding="utf-8").count('measured_on: "slurm"') == len(every_model)


# --- CLI ---------------------------------------------------------------------


def test_bench_on_a_fake_profile_says_why_it_measured_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """GPU도 서버도 없는 장비에서 실패하지 않는다. 대신 제외 사유를 남긴다."""
    code = cli.main(["bench", "--profile", "dev_fake", "--artifacts-root", str(tmp_path)])
    report = json.loads(capsys.readouterr().out)

    assert code == 0
    assert all(role["skipped"] == bench_report.FAKE_SKIP for role in report["roles"].values())
    assert {item["status"] for item in report["checklist"]} == {"unmeasured"}
    # 순서가 결과를 바꾼다는 사실이 기록에 남아야 한다.
    assert report["measurement_order"].index("prefix_cache") > report["measurement_order"].index(
        "structured_output"
    )


def test_dataset_pending_item_is_marked_out_of_scope(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """NVFP4 Critic 반박 정확도는 평가셋이 있어야 답한다 — 범위 밖임을 표시만 한다."""
    cli.main(["bench", "--profile", "dev_fake", "--artifacts-root", str(tmp_path)])
    report = json.loads(capsys.readouterr().out)

    last = report["checklist"][-1]
    assert "NVFP4" in last["item"]
    assert last["status"] == "unmeasured"
    assert "데이터셋 대기" in last["reason"]


def test_checklist_covers_every_item_in_the_serving_doc() -> None:
    """§3 체크리스트는 7개다. 항목이 늘었는데 bench가 모르면 요약이 조용히 부실해진다."""
    doc = (Path(__file__).resolve().parent.parent / "docs" / "model-serving.md").read_text("utf-8")
    section = doc.split("## 3. ")[1].split("## 4.")[0]

    assert section.count("- [ ]") + section.count("- [x]") == len(bench_report.CHECKLIST)


# --- --tool-runner 오버라이드 -------------------------------------------------


def test_override_flows_into_config_doctor_and_the_run_record(tmp_path: Path) -> None:
    """세 곳에 **같은 값**이 흘러야 한다.

    프로필에 적힌 값을 검사하면 doctor가 "docker 있음 OK"라고 해놓고 실제 run은
    singularity로 죽는다. 기록에 남지 않으면 비교표에서 이 run을 구분할 수 없다.
    """
    config = cli.load_profile("dev_docker", tool_runner="fake")
    assert config.profile.adapters.tool_runner == "fake"

    rendered = "\n".join(
        check.render()
        for check in cli.doctor("dev_docker", artifacts_root=tmp_path, tool_runner="fake")
    )
    assert "오버라이드가 걸렸다" in rendered
    assert "tool_runner가 Fake라 컨테이너 런타임이 필요 없다" in rendered

    run_dir = tmp_path / RUN_ID
    run_dir.mkdir()
    spec = RunSpec(condition="B3prime@pipeline", target=TARGET)
    result, _ = cli.execute(config, spec, run_id=RUN_ID, run_dir=run_dir)

    assert result.record.tool_runner == "fake"


def test_doctor_checks_the_overridden_runtime_not_the_profile(tmp_path: Path) -> None:
    rendered = "\n".join(
        check.render()
        for check in cli.doctor("dev_fake", artifacts_root=tmp_path, tool_runner="singularity")
    )

    assert "singularity" in rendered
    assert "컨테이너 런타임이 필요 없다" not in rendered


def test_tool_runner_choices_come_from_the_config_schema() -> None:
    """CLI가 아는 값과 배선이 아는 값이 갈라지지 않는다."""
    assert set(cli.TOOL_RUNNERS) == set(WIRINGS["tool_runner"])


# --- 프로필 ------------------------------------------------------------------


@pytest.mark.parametrize("profile", ["local_balanced", "b1_single"])
def test_serving_profiles_run_real_tools(profile: str) -> None:
    """`tool_images`가 채워져야 그 run이 비교표에 들어간다 (STATUS.md §8)."""
    config = load_config(profile)

    assert config.profile.adapters.tool_runner == "docker"
    # retriever까지 올리면 ATOM에 모델 5개를 띄워야 하고 R1의 실패 지점이 늘어난다.
    assert config.profile.adapters.retriever == "fake"


@pytest.mark.parametrize("profile", ["dev_fake", "dev_hybrid"])
def test_dev_profiles_stay_runnable_without_a_container_runtime(profile: str) -> None:
    """GPU도 Docker도 없는 팀원의 개발 경로다. 건드리지 않는다."""
    assert load_config(profile).profile.adapters.tool_runner == "fake"
