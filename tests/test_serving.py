"""장비 파일(`config/serving/<device>.yaml`)과 교대 선언이 만나는 자리.

단계 단위 콜드 재기동 교대(ADR 0012 Phase 6.5 — ADR 0009)의 하네스 쪽이다. 기동 조건(util·
seqs·창·인자)은 프로필이 아니라 **장비 데이터**다 — 같은 프로필을 다른 장비에서 돌리면 그
값만 갈린다. 여기서는 그 파일의 스키마·로더·registry와의 교차 검사를 본다.
"""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from src.config import (
    ConfigError,
    ExecutionPolicy,
    HarnessConfig,
    ServingConfig,
    SwapPolicy,
    check_serving,
    load_config,
    load_serving,
)

POOL = "harness_pool"


def _swap_pool() -> HarnessConfig:
    """`harness_pool`에 교대를 선언한 config — 모든 프롬프트 역할이 교대한다.

    커밋된 프로필은 아직 교대를 안 적는다(장비 파일과 함께 다음 PR이 적는다). 모양만 있으면
    되므로 여기서 조립한다(`tests/conftest.py`의 `served_scout_config`와 같은 방식).
    """
    base = load_config(POOL)
    swap = SwapPolicy(
        resident=(),
        swappable=("scout_memory", "scout_boundary", "scout_injection", "auditor", "critic"),
        settle_timeout_s=600.0,
    )
    execution = ExecutionPolicy(mode="per_stage", swap=swap)
    return base.transition(profile=base.profile.transition(execution=execution))


def _rows(config: HarnessConfig) -> dict[str, dict[str, object]]:
    """교대가 올릴 served 모델마다 한 행. 창은 registry `context_limit` 그대로다."""
    keys = sorted(set(config.served_models().values()))
    return {
        key: {
            "util": 0.3,
            "max_num_seqs": 8,
            "max_model_len": config.models[key].context_limit,
            "probe": "chat",
        }
        for key in keys
    }


def _write(config_dir: Path, device: str, body: dict[str, object]) -> Path:
    path = config_dir / "serving" / f"{device}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(body, allow_unicode=True), encoding="utf-8")
    return path


# --- 스키마 ---------------------------------------------------------------------


def test_a_complete_serving_file_is_accepted() -> None:
    """거부만 보면 통째로 거부하는 구현도 지난다 — 통과하는 모양을 먼저 고정한다."""
    serving = ServingConfig.model_validate(
        {
            "command": ["bash", "tools/serve/x.sh"],
            "servers": {
                "seccoderx-8b": {
                    "util": 0.3,
                    "max_num_seqs": 8,
                    "max_model_len": 40960,
                    "extra_args": ["--enable-prompt-tokens-details"],
                    "env": {"VLLM_X": "1"},
                    "probe": "chat",
                }
            },
        }
    )

    assert serving.command == ("bash", "tools/serve/x.sh")
    row = serving.servers["seccoderx-8b"]
    assert (row.util, row.max_num_seqs, row.max_model_len, row.probe) == (0.3, 8, 40960, "chat")
    assert row.extra_args == ("--enable-prompt-tokens-details",)
    assert row.env == {"VLLM_X": "1"}


@pytest.mark.parametrize(
    "body",
    [
        {"command": [], "servers": {}},
        {"command": [""], "servers": {}},
        {"servers": {}},
    ],
    ids=["empty-command", "blank-argv", "no-command"],
)
def test_a_serving_file_without_a_command_is_rejected(body: dict[str, object]) -> None:
    """교대 명령이 없으면 교대할 수단이 없다 — 로드에서 죽는다."""
    with pytest.raises(ValidationError):
        ServingConfig.model_validate(body)


@pytest.mark.parametrize(
    "row",
    [
        {"util": 0.3, "max_num_seqs": 8, "max_model_len": 1, "probe": "generate"},
        {"util": 1.5, "max_num_seqs": 8, "max_model_len": 1, "probe": "chat"},
        {"util": 0.3, "max_num_seqs": 0, "max_model_len": 1, "probe": "chat"},
        {"util": 0.3, "max_num_seqs": 8, "max_model_len": 1, "probe": "chat", "gpu_util": 0.3},
    ],
    ids=["unknown-probe", "util-over-1", "zero-seqs", "typo-field"],
)
def test_a_malformed_server_row_is_rejected(row: dict[str, object]) -> None:
    """오타·범위 밖 값은 조용히 무시되지 않는다(`HarnessModel` — extra forbid)."""
    with pytest.raises(ValidationError):
        ServingConfig.model_validate({"command": ["x"], "servers": {"k": row}})


# --- 로더 -----------------------------------------------------------------------


def test_the_loader_reads_the_device_file(tmp_path: Path) -> None:
    _write(tmp_path, "rig", {"command": ["bash", "swap.sh"], "servers": {}})

    assert load_serving("rig", config_dir=tmp_path).command == ("bash", "swap.sh")


@pytest.mark.parametrize(
    ("device", "body"),
    [
        ("absent", None),
        ("broken", {"command": [], "servers": {}}),
        ("../escape", None),
    ],
    ids=["missing-file", "invalid-file", "path-escape"],
)
def test_the_loader_raises_a_config_error(
    tmp_path: Path, device: str, body: dict[str, object] | None
) -> None:
    """없는 파일·깨진 파일·장비 라벨로 디렉터리 밖을 가리키는 것 — 전부 `ConfigError`다.

    CLI가 한 갈래(`ConfigError`)만 잡아 exit 2로 끝내게 하려면 스키마 오류도 같은 예외로 와야
    한다. 장비 라벨은 값 집합이 열려 있어(ADR 0006) 경로 조각으로 쓰기 전에 가둔다.
    """
    if body is not None:
        _write(tmp_path, device, body)
    # `../escape`가 가리킬 자리에 **멀쩡한** 파일을 둔다 — 가두지 않으면 로드가 성공한다.
    (tmp_path / "escape.yaml").write_text("command: [x]\nservers: {}\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        load_serving(device, config_dir=tmp_path)


# --- 교차 검사 ------------------------------------------------------------------


def test_a_file_that_covers_every_swapped_model_passes() -> None:
    config = _swap_pool()
    serving = ServingConfig.model_validate({"command": ["x"], "servers": _rows(config)})

    check_serving(config, serving)


def test_a_missing_row_for_a_swapped_model_is_named() -> None:
    """교대가 올릴 모델에 행이 없으면 **그 키를 대고** 멈춘다 — run 도중이 아니라 시작 전이다."""
    config = _swap_pool()
    rows = _rows(config)
    rows.pop("seccoderx-8b")

    with pytest.raises(ConfigError, match="seccoderx-8b"):
        check_serving(config, ServingConfig.model_validate({"command": ["x"], "servers": rows}))


def test_a_row_for_a_model_the_registry_does_not_have_is_rejected() -> None:
    config = _swap_pool()
    rows = {**_rows(config), "qwen9-999b": _rows(config)["seccoderx-8b"]}

    with pytest.raises(ConfigError, match="qwen9-999b"):
        check_serving(config, ServingConfig.model_validate({"command": ["x"], "servers": rows}))


def test_a_window_other_than_the_context_limit_is_rejected() -> None:
    """서버 창(`max_model_len`)은 registry `context_limit` **그대로**다.

    2026-09-25 ATOM에서 `context_limit` 40,960인 Scout 두 키를 16,384로 띄운 채 돌렸고, 하네스는
    아무 말도 안 했다 — 프롬프트 12건이 400으로 거절됐고 한 run은 후보 0으로 끝났다
    (`run_20260925_0005`·`0006`). 장비 파일이 다른 값을 적으면 그 실수가 파일에 굳는다.
    """
    config = _swap_pool()
    rows = _rows(config)
    rows["seccoderx-8b"] = {**rows["seccoderx-8b"], "max_model_len": 16384}

    with pytest.raises(ConfigError, match=r"seccoderx-8b.*16384.*40960"):
        check_serving(config, ServingConfig.model_validate({"command": ["x"], "servers": rows}))
