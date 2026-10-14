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
from pydantic import ValidationError

from src import bench, bench_report, cli
from src.adapters.factory import WIRINGS
from src.bench import BenchApplyError
from src.config import CONFIG_DIR, Benchmark, ModelEntry, load_config
from src.orchestrator.runner import RunSpec

RUN_ID = "run_20260101_0001"
TARGET = Path(__file__).resolve().parent / "fixtures" / "target"
MEASURED_AT = "2026-08-17T00:00:00Z"

# 실서빙 역할이 있는 프로필. registry 갱신 대상 모델이 실재해야 apply 검사가 공허하지 않다.
# (2026-09-24 전에는 scout만 실서빙인 `single_gpu_12g`·갱신 대상 `qwen3-4b`였다 — 로스터 밖 정리로
# 둘 다 지워 canonical `harness_pool`과 그 Scout 키 `qwen3-14b`로 옮겼다.)
SERVED_PROFILE = "harness_pool"


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

    bench_report.apply_benchmarks(path, {"qwen3-14b": {"tok_s_single": 12.5}}, MEASURED_AT)

    assert _comments(path) == before
    assert "tok_s_single: 12.5" in path.read_text(encoding="utf-8")


def _throughput(*, hit_ratio: object, tok_s_batch32: float = 90.0) -> dict[str, object]:
    """`measure_throughput` 산출의 최소 형태. `by_concurrency`가 적중률을 들고 있다."""
    return {
        "tok_s_single": 30.0,
        "tok_s_batch32": tok_s_batch32,
        "tok_s_batch32_reason": None,
        "by_concurrency": {
            "1": {"tok_s": 30.0, "prefix_cache_delta": {"status": "ok", "hit_ratio": 0.9996}},
            "32": {
                "tok_s": tok_s_batch32,
                "prefix_cache_delta": (
                    {"status": "ok", "hit_ratio": hit_ratio}
                    if hit_ratio is not None
                    else {"status": "metrics_unavailable", "reason": "카운터를 읽지 못했다"}
                ),
            },
        },
    }


def _role_report(throughput: dict[str, object]) -> dict[str, object]:
    return {
        "roles": {
            "scout": {
                "model_key": "qwen3-14b",
                "skipped": None,
                # `_is_measured`가 요구한다 — 없으면 이 역할이 통째로 건너뛰어져
                # 기능 부재가 아니라 픽스처 결손으로 실패한다.
                "startup": {"ok": True},
                "throughput": throughput,
                "memory": {"mem_gb_ctx8k": 0.0, "mem_gb_ctx8k_reason": "미측정"},
            }
        }
    }


def test_benchmark_carries_the_prefix_cache_hit_ratio(tmp_path: Path) -> None:
    """`tok_s_batch32`가 몇 번째 판이냐의 함수라면 그 사실이 값 옆에 있어야 한다 (#128).

    2026-09-02 ATOM에서 같은 서버·같은 인자로 돌린 세 판이 59.45 / 68.64 / 107.32로
    **1.81배** 갈렸고, 원인은 산포가 아니라 prefix cache 적중률이었다
    (0.5142 / 0.7776 / 0.8577 — 아홉 칸에 역전 0건).

    **이 필드는 값을 고치지 않는다.** 이슈가 (a)(판 구분자를 섞는다)를 재현성과
    맞바꾸는 설계 판단이라며 정하지 않았고, 여기서 하는 것은 그 이슈가
    「최소 정직 조치」라 부른 (b)뿐이다 — **읽는 사람이 판단할 재료를 남긴다.**
    """
    entry = Benchmark(tok_s_batch32=68.64, prefix_hit_ratio_batch32=0.7776)

    assert entry.prefix_hit_ratio_batch32 == 0.7776
    # **실측 항목이 아니다.** 모델의 능력이 아니라 그 측정이 어떤 상태에서 나왔는가다.
    # MEASUREMENTS에 넣으면 이 값이 없는 모델이 "부분 측정"으로 떨어진다.
    assert "prefix_hit_ratio_batch32" not in Benchmark.MEASUREMENTS
    assert entry.missing_measurements == ("tok_s_single", "mem_gb_ctx8k")


def test_pending_updates_carries_the_batch32_hit_ratio() -> None:
    """`--apply`가 쓸 값에 적중률이 함께 실린다 (#128)."""
    updates, _ = bench_report.pending_updates(_role_report(_throughput(hit_ratio=0.7776)))

    assert updates["qwen3-14b"]["prefix_hit_ratio_batch32"] == 0.7776


def test_a_zero_hit_ratio_is_still_written(tmp_path: Path) -> None:
    """적중률 0은 **미측정이 아니라 실측**이다 — 캐시를 끄고 잰 판이 정확히 그 값이다 (#128).

    `--apply`가 0을 안 쓰는 규칙은 `MEASUREMENTS` 세 항목의 것이고(0 = 미측정 표시),
    적중률에는 그 논법이 성립하지 않는다. 0을 버리면 **가장 정직한 판이 기록에서 사라진다.**
    """
    updates, _ = bench_report.pending_updates(_role_report(_throughput(hit_ratio=0.0)))

    assert updates["qwen3-14b"]["prefix_hit_ratio_batch32"] == 0.0


def test_an_unreadable_hit_ratio_is_not_invented() -> None:
    """카운터를 못 읽었으면 그 자리는 비워 둔다 (#128). 0으로 접으면 적극적인 거짓말이다."""
    updates, skipped = bench_report.pending_updates(_role_report(_throughput(hit_ratio=None)))

    assert "prefix_hit_ratio_batch32" not in updates["qwen3-14b"]
    assert any("prefix_hit_ratio_batch32" in reason for reason in skipped)


def test_apply_writes_the_hit_ratio_into_the_registry(tmp_path: Path) -> None:
    """registry 파일에 실제로 적힌다 (#128)."""
    path = _models_copy(tmp_path)

    bench_report.apply_benchmarks(
        path, {"qwen3-14b": {"tok_s_batch32": 68.64, "prefix_hit_ratio_batch32": 0.7776}},
        MEASURED_AT,
    )

    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert loaded["models"]["qwen3-14b"]["benchmark"]["prefix_hit_ratio_batch32"] == 0.7776


def test_apply_never_writes_a_zero(tmp_path: Path) -> None:
    """`Benchmark.is_measured`가 0 여부로 판정한다.

    못 잰 값을 넣으면 미측정 모델이 측정된 것으로 보이고 production 프로필로 가는 길이 열린다.
    """
    report = {
        "roles": {
            "scout": {
                "model_key": "qwen3-14b",
                "startup": {"ok": True},
                "skipped": None,
                "throughput": {
                    "tok_s_single": 30.0,
                    "tok_s_batch32": 0.0,
                    "tok_s_batch32_reason": "--concurrency에 32가 없어 재지 않았다",
                },
                "memory": {
                    "mem_gb_ctx8k": 0.0,
                    # 실제 코드가 내는 사유 형태를 그대로 쓴다. 옛 고정 문구
                    # ("nvidia-smi가 없어…")는 통합메모리 장비에서 거짓이라 폐기됐다.
                    "mem_gb_ctx8k_reason": (
                        "nvidia-smi가 정상 종료했지만 memory.used를 숫자로 주지 않았다"
                    ),
                    "gpu_memory_utilization": 0.9,
                },
            }
        }
    }

    updates, skipped = bench_report.pending_updates(report)

    assert updates == {"qwen3-14b": {"tok_s_single": 30.0}}
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
                "model_key": "qwen3-14b",
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

    assert "mem_gb_ctx8k" not in updates["qwen3-14b"]
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
            {"qwen3-14b": {"tok_s_single": 12.5}, "없는-모델": {"tok_s_single": 1.0}},
            MEASURED_AT,
        )

    assert path.read_bytes() == before


def test_applied_registry_still_loads(tmp_path: Path) -> None:
    """갱신 결과가 스키마를 통과해야 한다. 통과 못 하면 다음 run이 기동에서 죽는다."""
    for name in ("profiles.yaml", "budgets.yaml"):
        (tmp_path / name).write_text((CONFIG_DIR / name).read_text("utf-8"), encoding="utf-8")
    path = _models_copy(tmp_path)

    bench_report.apply_benchmarks(
        path, {"qwen3-14b": {"tok_s_single": 12.5, "mem_gb_ctx8k": 8.25}}, MEASURED_AT
    )

    config = load_config(SERVED_PROFILE, config_dir=tmp_path)
    benchmark = config.models["qwen3-14b"].benchmark
    assert benchmark.is_measured
    assert benchmark.tok_s_single == 12.5
    assert benchmark.measured_at is not None


# --- measured_on -------------------------------------------------------------


def _loaded(tmp_path: Path, key: str = "qwen3-14b") -> Benchmark:
    """갱신 결과를 실제 로더로 되읽는다. 문자열 검사만으로는 스키마 통과를 못 본다."""
    for name in ("profiles.yaml", "budgets.yaml"):
        (tmp_path / name).write_text((CONFIG_DIR / name).read_text("utf-8"), encoding="utf-8")
    return load_config(SERVED_PROFILE, config_dir=tmp_path).models[key].benchmark


def test_apply_does_not_touch_the_source_field(tmp_path: Path) -> None:
    """`source`는 `benchmark:` 블록 밖이고 `APPLY_KEYS`에도 없다 — `--apply`가 건드리지 않는다.

    `measured_on`과 파급이 다른 지점이다. 저쪽은 apply 키라 registry에 라인이 **있어야**
    하고(없으면 `_value_line`이 `BenchApplyError`), 이쪽은 애초에 탐색 범위 밖이다.
    """
    path = _models_copy(tmp_path)
    before = path.read_text(encoding="utf-8").count("source:")

    bench_report.apply_benchmarks(
        path, {"qwen3-14b": {"tok_s_single": 12.5}}, MEASURED_AT, measured_on="atom"
    )

    text = path.read_text(encoding="utf-8")
    assert text.count("source:") == before
    assert "source: null" in text
    assert "tok_s_single: 12.5" in text


def test_registry_loads_without_a_source_line(tmp_path: Path) -> None:
    """기존 registry 항목에 이 키가 없어도 로드가 죽지 않는다.

    `Benchmark.measured_on`이 옵셔널로 들어온 것과 같은 이유다 — 필수로 두면 키를 아직
    안 넣은 registry에서 **로드가 통째로 실패**한다.
    """
    path = _models_copy(tmp_path)
    stripped = "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines() if "source:" not in line
    )
    path.write_text(stripped + "\n", encoding="utf-8")
    for name in ("profiles.yaml", "budgets.yaml"):
        (tmp_path / name).write_text((CONFIG_DIR / name).read_text("utf-8"), encoding="utf-8")

    config = load_config(SERVED_PROFILE, config_dir=tmp_path)

    assert config.models["qwen3-14b"].source is None


def test_a_source_value_survives_the_round_trip(tmp_path: Path) -> None:
    """적어 둔 출처가 로더를 통과해 그대로 남는다. 빈 문자열은 거부한다."""
    path = _models_copy(tmp_path)
    origin = "hf://NVFP4/Qwen3-Coder-30B-A3B-Instruct-FP4@abc123"
    path.write_text(
        path.read_text(encoding="utf-8").replace("    source: null", f'    source: "{origin}"', 1),
        encoding="utf-8",
    )
    for name in ("profiles.yaml", "budgets.yaml"):
        (tmp_path / name).write_text((CONFIG_DIR / name).read_text("utf-8"), encoding="utf-8")

    config = load_config(SERVED_PROFILE, config_dir=tmp_path)

    assert config.models["fake"].source == origin
    with pytest.raises(ValidationError):
        ModelEntry.model_validate({**config.models["fake"].model_dump(), "source": ""})


def test_apply_keys_cover_every_benchmark_field() -> None:
    """`APPLY_KEYS`가 `Benchmark`의 필드와 같은지 본다.

    필드가 하나 늘었는데 `APPLY_KEYS`가 그대로면 그 값은 registry에 영영 안 쓰인다.
    **이 검사가 생기기 전에는 그 상수를 아무도 읽지 않아** "필드와 같아야 한다"는 주석이
    지켜지는지 알 방법이 없었다 — 이 저장소가 계속 고쳐 온 "선언과 실제의 괴리"다.
    """
    assert set(bench_report.APPLY_KEYS) == set(Benchmark.model_fields)


def test_apply_records_which_device_measured_it(tmp_path: Path) -> None:
    """수치와 함께 **어디서 쟀는지**가 registry에 남는다.

    두 장비가 모두 지원 대상이므로(docs/portability.md) 같은 필드에 다른 하드웨어의 값이
    들어갈 수 있다. 그 사실이 남지 않으면 다음 사람이 이 수치로 다른 장비를 예측한다.
    """
    path = _models_copy(tmp_path)

    bench_report.apply_benchmarks(
        path, {"qwen3-14b": {"tok_s_single": 12.5}}, MEASURED_AT, measured_on="atom"
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

    bench_report.apply_benchmarks(path, {"qwen3-14b": {"tok_s_single": 12.5}}, MEASURED_AT)

    assert "measured_on: null" in path.read_text(encoding="utf-8")
    assert _loaded(tmp_path).measured_on is None


@pytest.mark.parametrize(
    "label",
    ['atom "gb10"', r"lab\dev", r"C:\atom", "atom#1", "노드 u4", "a: b"],
)
def test_a_hostile_label_neither_breaks_nor_silently_changes(tmp_path: Path, label: str) -> None:
    """`--device`는 사용자 입력이고 ADR 0006이 라벨 집합을 **일부러 열어 두었다.**

    따옴표를 그대로 잇는 방식(`f'"{value}"'`)은 두 가지로 깨졌다. 따옴표가 든 라벨은
    registry를 파싱 불가로 만들어 이후 모든 커맨드를 죽이고, `C:\\atom`은 로드는 되지만
    YAML 이스케이프로 **조용히 다른 라벨**이 된다(`\\a` = BEL). 뒤가 더 나쁘다 — 이
    필드의 존재 이유가 정확히 "어느 장비였는지 나중에 알 수 있다"이기 때문이다.
    """
    path = _models_copy(tmp_path)

    bench_report.apply_benchmarks(
        path, {"qwen3-14b": {"tok_s_single": 1.0}}, MEASURED_AT, measured_on=label
    )

    assert _loaded(tmp_path).measured_on == label


def test_every_registry_block_has_the_line_apply_needs(tmp_path: Path) -> None:
    """`_value_line()`은 라인이 없으면 `BenchApplyError`로 죽는다.

    스키마 기본값만으로는 부족하다 — `--apply`는 YAML을 라인 단위로 치환하므로 그 키가
    파일에 **실제로 적혀 있어야** 한다. 한 모델이라도 빠지면 그 모델의 갱신이 통째로
    실패하고, 그것을 ATOM 실측 현장에서 알게 되는 것이 최악이다.
    ⏵ 2026-10-08 — ATOM은 2026-09-29 사용 종료(#340). 지금 실측 장비는 vast다(D81).
    """
    path = _models_copy(tmp_path)
    every_model = yaml.safe_load(path.read_text(encoding="utf-8"))["models"]

    outcome = bench_report.apply_benchmarks(
        path,
        {key: {"tok_s_single": 1.0} for key in every_model},
        MEASURED_AT,
        measured_on="seraph",
    )

    assert set(outcome["updated"]) == set(every_model)
    assert path.read_text(encoding="utf-8").count('measured_on: "seraph"') == len(every_model)


# --- --device ----------------------------------------------------------------


def _manifest_path(artifacts_root: Path) -> Path:
    """방금 만들어진 run 하나의 `run_manifest.json`. 이 파일이 곧 RunRecord다."""
    runs = [path for path in artifacts_root.iterdir() if path.name.startswith("run_")]
    assert len(runs) == 1, runs
    return runs[0] / "run_manifest.json"


def doctor_lines(*, device: str | None, config_dir: Path = CONFIG_DIR) -> list[str]:
    checks = cli.doctor(
        SERVED_PROFILE,
        artifacts_root=CONFIG_DIR.parent / "artifacts",
        device=device,
        config_dir=config_dir,
    )
    return [check.render() for check in checks]



def test_device_is_an_explicit_label_never_the_hostname() -> None:
    """**자동 채움을 하지 않는다.**

    호스트명으로 메우면 한 필드에 "사람이 붙인 장비 라벨"과 "하네스 프로세스가 돈
    호스트명"이라는 서로 다른 두 사실이 들어가고, 기록만 보면 구분할 수 없다. 게다가 그
    둘은 자주 어긋난다 — 로그인 노드에서 하네스를 돌리면 GPU 작업이 한 톨도 일어나지
    않은 기계 이름이 적힌다. `None`은 `repo_dirty`의 `None`과 같은 뜻이다: 확인 못 함.
    """
    assert RunSpec(condition="B1@pipeline", target=TARGET).device is None
    assert RunSpec(condition="B1@pipeline", target=TARGET, device="atom").device == "atom"


def test_a_run_without_a_device_is_excluded_from_the_comparison_table(tmp_path: Path) -> None:
    """장비를 모르는 run은 비교표의 어느 칸에도 놓을 수 없다.

    `tool_runner`가 `incomplete_fields()`에 없는 것은 config에서 유도되어 빌 수가 없기
    때문인데, 이 값은 사람이 안 주면 실제로 빈다 — `repo_dirty`와 같은 취급이다.
    """
    code = cli.main(
        ["run", "--profile", "dev_fake", "--target", str(TARGET),
         "--artifacts-root", str(tmp_path)]
    )

    assert code == 0
    record = json.loads(_manifest_path(tmp_path).read_text("utf-8"))
    assert record["device"] is None


def test_the_device_label_lands_in_the_run_record(tmp_path: Path) -> None:
    """경희대 학생이 Seraph에서 낸 결과와 팀의 ATOM 결과가 섞이면 안 된다.

    `model_checksums`는 설계상 장비 불변이고 endpoint는 애초에 기록되지 않는다 — 이
    필드가 없으면 두 run이 기록만으로는 완전히 같아 보인다.
    """
    code = cli.main(
        ["run", "--profile", "dev_fake", "--target", str(TARGET),
         "--artifacts-root", str(tmp_path), "--device", "seraph"]
    )

    assert code == 0
    record = json.loads(_manifest_path(tmp_path).read_text("utf-8"))
    assert record["device"] == "seraph"


def test_the_profile_name_lands_in_the_run_manifest(tmp_path: Path) -> None:
    """`run_manifest.json`만 보고 어느 프로필로 돌았는지 알 수 있어야 한다 (이슈 #76).

    이 필드가 없으면 프로필 이름이 **조작자가 친 쉘 명령에만** 있고 산출물 셋
    (`run_manifest.json`·`trace.jsonl`·`report.md`) 어디에도 없다. evaluation.md §2.3
    규칙 2가 프로필을 비교의 기준축으로 지정했는데 그 축이 기록되지 않는다.

    `cli bench`는 이미 `"profile"`을 남긴다 — `cli run`만 안 남긴다.
    """
    code = cli.main(
        ["run", "--profile", "dev_fake", "--target", str(TARGET),
         "--artifacts-root", str(tmp_path)]
    )

    assert code == 0
    record = json.loads(_manifest_path(tmp_path).read_text("utf-8"))
    assert record["profile"] == "dev_fake"


def test_doctor_says_nothing_about_the_device_when_none_is_given() -> None:
    """라벨이 없으면 비교할 대상이 없다. 없는 비교로 경고하면 헛경고가 된다."""
    checks = doctor_lines(device=None)

    # 교대 프로필의 「--device가 없으면 장비 파일을 못 고른다」 WARN은 다른 검사다
    # (`cli._check_serving` — tests/test_serving.py). `harness_pool`이 2026-09-29부터 교대를
    # 선언해 그 줄이 여기에도 나온다.
    assert not [line for line in checks if "장비" in line and "장비 파일" not in line]


def test_doctor_warns_when_the_registry_was_measured_elsewhere(tmp_path: Path) -> None:
    """registry가 ATOM 기준인데 Seraph에서 doctor를 치면 그 사실이 드러나야 한다."""
    for name in ("profiles.yaml", "budgets.yaml"):
        (tmp_path / name).write_text((CONFIG_DIR / name).read_text("utf-8"), encoding="utf-8")
    path = _models_copy(tmp_path)
    bench_report.apply_benchmarks(
        path, {"qwen3-14b": {"tok_s_single": 12.5}}, MEASURED_AT, measured_on="atom"
    )

    same = doctor_lines(device="atom", config_dir=tmp_path)
    other = doctor_lines(device="seraph", config_dir=tmp_path)

    assert not [line for line in same if "다른 장비 기준" in line]
    mismatch = [line for line in other if "다른 장비 기준" in line]
    assert len(mismatch) == 1
    assert "qwen3-14b(atom)" in mismatch[0]


def test_an_unmeasured_registry_is_not_reported_as_a_mismatch() -> None:
    """`measured_on`이 없는 것은 불일치가 아니라 **모름**이다.

    모름을 불일치로 보고하면 registry를 채우기 전 상태에서 전 모델이 경고에 걸려 진짜
    불일치가 묻힌다.
    """
    assert not [line for line in doctor_lines(device="atom") if "다른 장비 기준" in line]


def test_apply_without_a_device_warns_at_the_moment_it_matters(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """`doctor`는 조용하지만 registry에 **쓰는** 순간에는 영구히 장비 미상으로 남는다.

    경고는 stderr로 간다 — stdout의 JSON이 파싱 가능해야 한다.
    """
    cli.main(
        ["bench", "--profile", "dev_fake", "--artifacts-root", str(tmp_path), "--apply"]
    )
    captured = capsys.readouterr()

    assert json.loads(captured.out)["roles"]  # stdout은 여전히 JSON이다
    assert "--device 없이 --apply" in captured.err

    cli.main(
        ["bench", "--profile", "dev_fake", "--artifacts-root", str(tmp_path),
         "--apply", "--device", "atom"]
    )
    assert "--device 없이 --apply" not in capsys.readouterr().err


def test_serving_apply_without_a_device_refuses_to_write(
    tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """실서빙 프로필에서 `--apply`는 `--device` 없이 registry를 쓰지 않는다 (#124).

    `run`은 같은 결여에 `exit 2`인데 `bench --apply`만 경고하고 진행했다. 그 비대칭이
    나쁜 이유는 **결여의 대가가 더 크기 때문**이다 — run의 산출물은 그 run 하나에
    갇히지만 registry는 이후 모든 예산 추정과 `cli estimate`가 읽는 공용 파일이고,
    `measured_on: null`은 거기 영구히 남는다(docs/portability.md).

    이슈의 선택지 (b)다 — 읽기 전용 `bench`는 막지 않고 **파일을 쓰는 경로만** 요구한다.
    """
    written: list[object] = []
    monkeypatch.setattr(cli, "apply_benchmarks", lambda *a, **k: written.append(a) or {})

    code = cli.main(
        ["bench", "--profile", "harness_pool", "--artifacts-root", str(tmp_path), "--apply"]
    )

    assert code == 2
    assert written == [], "거부해 놓고 registry에 썼다"
    assert "--device" in capsys.readouterr().err


def test_serving_apply_with_a_device_still_writes(
    tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """라벨을 주면 종전대로 쓴다 — 막는 것은 결여이지 `--apply` 자체가 아니다 (#124)."""
    monkeypatch.setattr(cli, "apply_benchmarks", lambda *a, **k: {"lines": 0})
    _refuse_endpoints(monkeypatch)

    code = cli.main(
        ["bench", "--profile", "harness_pool", "--artifacts-root", str(tmp_path),
         "--apply", "--device", "atom"]
    )
    capsys.readouterr()

    assert code != 2


def _refuse_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    """실서빙 엔드포인트 연결을 즉시 실패시킨다.

    이 검사들이 보는 것은 라벨 게이트이지 서버가 아니다.

    `harness_pool`은 실서빙 역할이 여섯이라 스텁이 없으면 역할마다 연결을 기다려 검사 하나에
    10초가 넘게 걸렸다(2026-09-24 실측 — 옛 `single_gpu_12g`는 역할이 하나였다).
    """
    monkeypatch.setattr(bench, "fetch", lambda *a, **k: (None, "stub: 연결 거부"))


def test_serving_bench_without_apply_is_not_blocked(
    tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """읽기 전용 `bench`는 라벨 없이도 돈다 (#124).

    막는 근거가 "공용 파일이 오염된다"이므로 아무것도 안 쓰는 실행에는 그 근거가 없다.
    """
    _refuse_endpoints(monkeypatch)
    code = cli.main(
        ["bench", "--profile", "harness_pool", "--artifacts-root", str(tmp_path)]
    )
    capsys.readouterr()

    assert code != 2


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
    """Critic 반박 정확도는 평가셋이 있어야 답한다 — 범위 밖임을 표시만 한다.

    **2026-09-09에 이 항목의 문구가 바뀌었고 이 검사가 옛 문구를 고정하고 있었다.**
    원래는 「NVFP4 양자화 시 Critic 반박 정확도 저하 여부」였는데, 그 물음은 Critic이
    고정밀 원본을 가진 모델일 때만 선다. 확정 로스터의 Critic은 네이티브 MXFP4라
    비교 대상이 없어 #93이 「반박 정확도 표본 자체」로 재정의했다.

    ⚠ **그래서 `NVFP4`를 단언하지 않는다.** 이 검사가 고정하는 것은 양자화 방식이 아니라
    **「마지막 항목은 데이터셋이 없어 미실측으로 표시된다」**이고, 그것은 재정의 전후로
    같다. 모델이 또 바뀌어도 이 검사는 서 있어야 한다."""
    cli.main(["bench", "--profile", "dev_fake", "--artifacts-root", str(tmp_path)])
    report = json.loads(capsys.readouterr().out)

    last = report["checklist"][-1]
    assert "반박 정확도" in last["item"]
    assert "NVFP4" not in last["item"], (
        "네이티브 MXFP4 Critic에는 고정밀 대비 기준선이 없다 — 문구가 되돌아갔다"
    )
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


def test_matching_tool_runner_is_not_flagged_as_override(tmp_path: Path) -> None:
    """선언값과 **같은** 값을 넘긴 run은 오버라이드가 아니다 (D50).

    `run_20260828_0002`가 `b1_single`(선언 docker)에 `--tool-runner docker`로 돌고도
    "프로필과 다른 도구 런타임" 경고를 달아, 보고서 메타의 "선언값 / 실제값"을 쓰는 사람을
    오도했다. 예외 신고의 조건은 플래그를 넘겼는가가 아니라 값이 바뀌었는가다.

    **되돌리면 이 테스트가 깨진다** — 옛 코드는 `is not None`만 봐서 같은 값에도 경고했다.
    """
    # 그 run의 프로필 `b1_single`은 2026-09-24에 지웠다 — docker를 선언한 실서빙 프로필은 지금
    # canonical `harness_pool`이다. 성질(같은 값이면 경고 없음)은 프로필과 무관하다.
    assert cli.load_config("harness_pool").profile.adapters.tool_runner == "docker"

    rendered = "\n".join(
        check.render()
        for check in cli.doctor("harness_pool", artifacts_root=tmp_path, tool_runner="docker")
    )
    assert "오버라이드가 걸렸다" not in rendered


def test_tool_runner_override_predicate_compares_the_declared_value() -> None:
    """헬퍼는 선언값 비교로 예외 여부를 가른다 (D50).

    `dev_docker`는 docker를 선언한다. 같은 값은 예외가 아니고, 다른 값·플래그 없음은
    각각 예외·비예외다. 위 두 doctor 테스트(다른 값→경고, 같은 값→무경고)가 이 술어에
    의존한다.
    """
    assert cli._tool_runner_overrides("dev_docker", "singularity") is True
    assert cli._tool_runner_overrides("dev_docker", "docker") is False
    assert cli._tool_runner_overrides("dev_docker", None) is False


def test_tool_runner_choices_come_from_the_config_schema() -> None:
    """CLI가 아는 값과 배선이 아는 값이 갈라지지 않는다."""
    assert set(cli.TOOL_RUNNERS) == set(WIRINGS["tool_runner"])


# --- 프로필 ------------------------------------------------------------------


@pytest.mark.parametrize("profile", ["harness_pool"])
def test_serving_profiles_run_real_tools(profile: str) -> None:
    """`tool_images`가 채워져야 그 run이 비교표에 들어간다 (STATUS.md §8).

    **런타임이 `docker`인 것도 이 테스트가 지킨다.** 프로필은 주 실서빙 장비의 런타임을
    선언하고(docs/portability.md §3) 그 장비는 ATOM이다.
    ⏵ 2026-10-08 — ATOM은 2026-09-29 사용 종료(#340 · docs/portability.md §6). 그 뒤 도구까지 돈
    완주는 서빙 vast · 도구 하네스 쪽 로컬 Docker(`--device vastai-remote`)였다 — Windows
    `run_20261003_0003`(도구 4개 중 `index`·`semgrep`만 ok —
    `docs/experiments/harness/2026-10-04-cybergym-operational-run-vastai-remote.md` §1·§4)와
    `run_20261007_0001`(`2026-10-08-first-real-serving-run-arvo1065-vastai-remote.md` 메타·§3).
    전부가 아니다: WSL `run_20261003_0002`는 Docker 소켓이 없어 도구 4개 전부 실패했고(같은 10-04
    보고서 메타·§4), 2026-10-05 PoV 프로브(`run_20261005_0001`)는 `tool_runner: fake`다
    (`2026-10-06-pov-seat-probe-vastai.md` 머리 표).

    **이 단언은 2026-08-28에 `podman`에서 뒤집혔다.** 그전 docstring은 "ATOM에는 podman만
    있으므로 `docker`로 되돌아가면 ATOM의 모든 실서빙 run이 `--tool-runner` 오버라이드를
    달게 된다"고 적었는데, 그 전제가 두 겹으로 틀렸다 — ① ATOM에 docker 29.2.1과
    podman 4.9.3이 함께 있다(2026-08-21 확인, 08-28 `which`로 재확인) ② **오버라이드는
    이미 달리고 있었다.** `b1_single`로 돈 run 둘(`run_20260827_0001`·`0004`)이
    `RunRecord.tool_runner: docker`다. 즉 `podman` 선언이 막으려던 "주 경로가 상시 예외
    상태"를 그 선언이 오히려 만들고 있었다. 경위는
    docs/decisions/0005-tool-runner-override.md 마지막 절.

    **실서빙 프로필끼리 같은 값이어야 한다는 것이 이 단언의 지금 이유다.** `tool_images` digest는
    런타임 사이에서 비교할 수 없으므로(docs/portability.md §5 #4) 값이 갈리면 그 run들을
    같은 비교표에 못 올린다. (2026-09-24까지는 `local_balanced`·`b1_single` 둘을 묶었고, 로스터 밖
    정리로 둘을 지운 뒤 fake retriever 실서빙 프로필은 canonical `harness_pool` 하나다.)
    예전에는 2x2 실험 프로필 넷도 같은 이유로 여기 묶여 있었고,
    그 트랙은 2026-09-01에 폐기됐고 2026-09-04에 저장소에서 걷어냈다.
    """
    config = load_config(profile)

    assert config.profile.adapters.tool_runner == "docker"
    # retriever까지 올리면 ATOM에 모델 5개를 띄워야 하고 R1의 실패 지점이 늘어난다.
    # ⏵ 2026-10-08 — ATOM은 2026-09-29 사용 종료(#340). 이 근거는 지금 장비로 다시 안 세웠다.
    assert config.profile.adapters.retriever == "fake"


@pytest.mark.parametrize("profile", ["dev_fake", "dev_hybrid"])
def test_dev_profiles_stay_runnable_without_a_container_runtime(profile: str) -> None:
    """GPU도 Docker도 없는 팀원의 개발 경로다. 건드리지 않는다."""
    assert load_config(profile).profile.adapters.tool_runner == "fake"
