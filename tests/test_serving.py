"""장비 파일(`config/serving/<device>.yaml`)과 교대 선언이 만나는 자리.

단계 단위 콜드 재기동 교대(ADR 0012 Phase 6.5 — ADR 0009)의 하네스 쪽이다. 기동 조건(util·
seqs·창·인자)은 프로필이 아니라 **장비 데이터**다 — 같은 프로필을 다른 장비에서 돌리면 그
값만 갈린다. 여기서는 그 파일의 스키마·로더·registry와의 교차 검사를 본다.
"""

import json
import sys
from dataclasses import fields
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from src import cli
from src.adapters import factory
from src.adapters.factory import Deps, build_deps
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
from src.orchestrator.runner import DEFAULT_CONDITION, Dependencies, RunSpec

POOL = "harness_pool"
TARGET = Path(__file__).resolve().parent / "fixtures" / "target"
SWAPPED = ("scout_memory", "scout_boundary", "scout_injection", "auditor", "critic", "pov")


def _with_swap(base: HarnessConfig, swappable: tuple[str, ...]) -> HarnessConfig:
    swap = SwapPolicy(resident=(), swappable=swappable, settle_timeout_s=600.0)
    execution = ExecutionPolicy(mode="per_stage", swap=swap)
    return base.transition(profile=base.profile.transition(execution=execution))


def _swap_pool() -> HarnessConfig:
    """커밋된 `harness_pool` — 모든 프롬프트 역할이 교대한다(2026-09-29부터 프로필이 선언한다)."""
    config = load_config(POOL)
    assert config.swap is not None and config.swap.swappable == SWAPPED
    return config


def _swap_fake() -> HarnessConfig:
    """`dev_fake`에 교대를 선언한 config — 실서빙 역할이 없어 실서빙 게이트가 안 선다.

    그래서 거부가 나면 **교대 게이트가** 낸 것이다(`harness_pool`이면 `--device` 결여를 실서빙
    게이트가 먼저 막아, 교대 게이트가 사라져도 거부 테스트가 초록이다).
    """
    return _with_swap(load_config("dev_fake"), ("scout", "auditor", "critic"))


def _rows(config: HarnessConfig) -> dict[str, dict[str, object]]:
    """교대가 올릴 served 모델마다 한 행. 창은 registry `context_limit` 그대로다."""
    keys = sorted(set(config.served_models().values()))
    return {
        key: {
            "util": 0.3,
            "max_num_seqs": 8,
            "max_model_len": config.models[key].context_limit,
            "probe": "chat",
            "ready_timeout_s": 300.0,
        }
        for key in keys
    }


def _write(config_dir: Path, device: str, body: dict[str, object]) -> Path:
    path = config_dir / "serving" / f"{device}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(body, allow_unicode=True), encoding="utf-8")
    return path


# --- 스키마 ---------------------------------------------------------------------

# 거부 케이스의 출발점 — 통과하는 한 행(`test_the_row_every_malformed_case_starts_from_is_valid`).
_ROW = {"util": 0.3, "max_num_seqs": 8, "max_model_len": 1, "probe": "chat", "ready_timeout_s": 1}


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
                    "ready_timeout_s": 400,
                }
            },
        }
    )

    assert serving.command == ("bash", "tools/serve/x.sh")
    row = serving.servers["seccoderx-8b"]
    assert (row.util, row.max_num_seqs, row.max_model_len, row.probe) == (0.3, 8, 40960, "chat")
    assert row.extra_args == ("--enable-prompt-tokens-details",)
    assert row.env == {"VLLM_X": "1"}
    assert row.ready_timeout_s == 400


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
        {**_ROW, "probe": "generate"},
        {**_ROW, "util": 1.5},
        {**_ROW, "max_num_seqs": 0},
        {**_ROW, "gpu_util": 0.3},
        {key: value for key, value in _ROW.items() if key != "ready_timeout_s"},
        {**_ROW, "ready_timeout_s": 0},
    ],
    ids=[
        "unknown-probe", "util-over-1", "zero-seqs", "typo-field", "no-ready-timeout",
        "zero-ready-timeout",
    ],
)
def test_a_malformed_server_row_is_rejected(row: dict[str, object]) -> None:
    """오타·범위 밖 값은 조용히 무시되지 않는다(`HarnessModel` — extra forbid).

    **준비 상한(`ready_timeout_s`)에는 기본값이 없다** — 장비마다 기동 시간이 몇 배씩 갈리고
    (ATOM 기동 132~313 s vs vast Auditor 244 s — `config/serving/`의 두 파일 주석), 기본값은 아무도
    재지 않은 수가 굳는 자리다(CLAUDE.md §0).
    """
    with pytest.raises(ValidationError):
        ServingConfig.model_validate({"command": ["x"], "servers": {"k": row}})


def test_the_row_every_malformed_case_starts_from_is_valid() -> None:
    """위 거부 케이스가 **자기 결함 하나로만** 죽는지 — 출발점이 통과해야 그 말이 선다."""
    ServingConfig.model_validate({"command": ["x"], "servers": {"k": _ROW}})


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


# --- 커밋된 장비 파일 ------------------------------------------------------------

# `run --device <label>`로 고를 수 있는 장비. ATOM이 첫 검증 대상이다(2026-09-29 사용자).
# ⏵ 2026-10-08 — ATOM은 2026-09-29 사용 종료(#340 · D81). `atom`은 기록용 장비 파일로 든다.
DEVICES = ("atom", "vastai", "vastai-remote")
# 장비마다 교대 명령. `vastai-remote`만 ssh 래퍼다 — 하네스는 로컬, 서빙만 vast
# (ADR 0009 「2026-09-29 제안」 절 6의 2026-10-03 노트). 래퍼의 성질은 `tests/test_remote_swap.py`.
COMMANDS = {
    "atom": ("bash", "tools/serve/swap.sh"),
    "vastai": ("bash", "tools/serve/swap.sh"),
    "vastai-remote": ("bash", "tools/serve/swap-remote.sh"),
}


@pytest.mark.parametrize("device", DEVICES)
def test_the_committed_device_file_serves_every_model_the_canonical_pool_swaps(
    device: str,
) -> None:
    """커밋된 장비 파일이 `harness_pool`의 교대 선언·registry와 맞는다 — run 시작 게이트의 검사.

    ⚠ **교대 선언이 먼저 있어야 이 검사가 무엇을 잰다.** `check_serving`의 「교대가 올릴 모델마다
    행이 있다」는 `swap`이 없으면 건너뛴다 — 그래서 선언을 같이 단언한다.
    """
    config = load_config(POOL)
    serving = load_serving(device)

    assert config.swap is not None
    check_serving(config, serving)
    assert serving.command == COMMANDS[device]


@pytest.mark.parametrize("device", DEVICES)
def test_every_thinking_seat_is_launched_with_a_reasoning_parser(device: str) -> None:
    """사고 상한(`thinking_token_budget`)을 선언한 서빙 좌석은 reasoning parser와 함께 뜬다.

    선언(registry)과 기동 인자(장비 파일)의 **관계**다. parser 없이 뜬 서버에 사고 상한을
    건 요청은 400이다(장비 파일의 그 행 주석). `config/models.yaml`의 추론 문단은 parser가
    없으면 사고도 죽는다고 적는다. Scout·Auditor·Critic 경로는 크기가 아닌 400에서 run을
    멈추지만(#296), pov는 추가형이라 `runner._run_pov`가 finding마다 `pov_skipped`로 남기고
    run은 PoC 0건으로 끝난다. `check_serving`은 행과 창만 보고 이 인자는 안 본다.

    ⚠ 무는 것은 **철자와 존재**다 — `--reasoning-parser`(또는 `--reasoning-parser=…`)가
    인자에 있는지만 본다. 값(`qwen3`)이 그 모델의 템플릿과 맞는지는 안 묻는다.
    """
    config = load_config(POOL)
    serving = load_serving(device)
    thinking = sorted(
        key
        for key in config.served_models().values()
        if config.models[key].thinking_token_budget is not None
    )
    assert thinking, "사고 상한을 선언한 서빙 좌석이 없다 — 이 검사가 아무것도 안 본다"
    for key in thinking:
        args = serving.servers[key].extra_args
        has_parser = any(
            arg == "--reasoning-parser" or arg.startswith("--reasoning-parser=") for arg in args
        )
        assert has_parser, (
            f"{device}: {key}는 사고 상한을 선언했는데 서버 행에 "
            "--reasoning-parser가 없다 — 그 요청은 400이다"
        )


# --- CLI: 교대 프로필은 장비 파일 없이 모델에 닿지 않는다 -----------------------------


class _Claimed(RuntimeError):
    """게이트를 지나 run 디렉터리 확보까지 왔다는 표시."""


def _errors(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines() if line.startswith("[ERROR]")]


def _use(monkeypatch: pytest.MonkeyPatch, config: HarnessConfig, config_dir: Path) -> None:
    """CLI가 이 config를 쓰고, 장비 파일을 `config_dir/serving/`에서 찾게 하고, run 디렉터리를
    잡는 순간 멈추게 한다 — 게이트를 지나면 `_Claimed`가 난다."""

    def _boom(*_: object, **__: object) -> tuple[str, Path]:
        raise _Claimed

    real = load_serving
    monkeypatch.setattr(cli, "load_profile", lambda *_a, **_k: config)
    monkeypatch.setattr(cli, "load_serving", lambda device, **_k: real(device, config_dir))
    monkeypatch.setattr(cli, "_claim_run_dir", _boom)


def _run_argv(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "run", "--profile", "x", "--target", str(TARGET), "--condition", "B1@pipeline",
        "--kb-snapshot", "kb-20260901", "--seed", "20260901",
        "--artifacts-root", str(tmp_path / "artifacts"), *extra,
    ]  # fmt: skip


@pytest.fixture
def empty_slices(tmp_path: Path) -> Path:
    """비었지만 믿을 수 있는 slices.json — `harness_pool`의 v3 Scout 슬라이스 가드를 지나게 한다."""
    path = tmp_path / "slices.json"
    body = {
        "slice_version": "slice-v2",
        "status": "ok",
        "joern_engine_config": {"maxCallDepth": 2},
        "slices": [],
        "candidate_manifest": {},
    }
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def test_a_swap_run_refuses_to_start_without_a_device(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """교대 프로필로 모델에 닿는 run은 `--device` 없이 시작하지 않는다 — 어느 장비 파일로 서버를
    띄울지 모른다. **run 디렉터리를 잡기 전에** exit 2다(`--slices` 가드와 같은 자리)."""
    _use(monkeypatch, _swap_fake(), tmp_path)

    code = cli.main(_run_argv(tmp_path))

    errors = _errors(capsys.readouterr().err)
    assert code == 2, "교대 프로필이 --device 없이 시작한다"
    assert [line for line in errors if "--device" in line and "교대" in line], errors


def test_a_swap_run_refuses_to_start_without_the_device_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _use(monkeypatch, _swap_fake(), tmp_path)

    code = cli.main(_run_argv(tmp_path, "--device", "nowhere"))

    errors = _errors(capsys.readouterr().err)
    assert code == 2, "장비 파일이 없는데 시작한다"
    assert [line for line in errors if "nowhere" in line], errors


def test_a_swap_run_refuses_a_device_file_that_disagrees_with_the_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    empty_slices: Path,
) -> None:
    """파일이 있어도 교차 검사가 틀리면 막는다 — 창을 작게 적은 파일이 run에 닿으면 안 된다."""
    config = _swap_pool()
    rows = _rows(config)
    rows["seccoderx-8b"] = {**rows["seccoderx-8b"], "max_model_len": 16384}
    _write(tmp_path, "rig", {"command": ["x"], "servers": rows})
    _use(monkeypatch, config, tmp_path)

    code = cli.main(_run_argv(tmp_path, "--device", "rig", "--slices", str(empty_slices)))

    errors = _errors(capsys.readouterr().err)
    assert code == 2
    assert [line for line in errors if "16384" in line], errors


def test_a_swap_run_with_a_matching_device_file_passes_the_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    empty_slices: Path,
) -> None:
    """양성 대조 — 막는 것이 목적이 아니다. 맞는 파일이면 run 디렉터리 확보까지 간다."""
    config = _swap_pool()
    _write(tmp_path, "rig", {"command": ["x"], "servers": _rows(config)})
    _use(monkeypatch, config, tmp_path)

    with pytest.raises(_Claimed):
        cli.main(_run_argv(tmp_path, "--device", "rig", "--slices", str(empty_slices)))

    assert not _errors(capsys.readouterr().err)


@pytest.mark.parametrize("flags", [("--dry-run",), ("--stop-after", "3")], ids=["dry", "stop3"])
def test_a_swap_run_that_never_reaches_a_model_is_not_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flags: tuple[str, ...]
) -> None:
    """모델에 안 닿는 run은 교대도 안 한다 — 막으면 통과시키려고 장비 라벨을 **지어내게** 된다
    (`cli.run`의 실서빙 게이트가 같은 이유로 그 run을 안 막는다)."""
    _use(monkeypatch, _swap_fake(), tmp_path)

    with pytest.raises(_Claimed):
        cli.main(_run_argv(tmp_path, *flags))


# --- doctor ------------------------------------------------------------------------


def _doctor(
    monkeypatch: pytest.MonkeyPatch, config: HarnessConfig, tmp_path: Path, device: str | None
) -> list[str]:
    """doctor 출력 중 장비 파일을 말하는 줄. 장비 파일은 `tmp_path/serving/`에서 찾는다."""
    monkeypatch.setattr(cli, "load_profile", lambda *_a, **_k: config)
    checks = cli.doctor(
        "x", artifacts_root=tmp_path / "artifacts", device=device, config_dir=tmp_path
    )
    return [check.render() for check in checks if "장비 파일" in check.message]


def test_doctor_warns_that_a_swap_profile_needs_a_device(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """doctor는 모델에 닿는 run인지 모른다 — `--device`가 없으면 WARN이지 ERROR가 아니다
    (`--slices` 가드의 doctor 짝과 같은 판단 — ERROR면 멀쩡한 프로필의 doctor가 exit 1이다)."""
    lines = _doctor(monkeypatch, _swap_pool(), tmp_path, None)

    assert lines, "교대 프로필인데 doctor가 장비 파일을 말하지 않는다"
    assert all(line.startswith("[WARN ") and "--device" in line for line in lines), lines


def test_doctor_errors_on_a_missing_or_disagreeing_device_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--device`를 줬으면 doctor도 run과 같은 판정을 낸다 — 파일이 없거나 교차 검사가 틀리면
    그 run은 시작하지 않으므로 ERROR다."""
    config = _swap_pool()
    rows = _rows(config)
    rows.pop("seccoderx-8b")
    _write(tmp_path, "short", {"command": ["x"], "servers": rows})

    for device in ("nowhere", "short"):
        lines = _doctor(monkeypatch, config, tmp_path, device)
        assert lines, device
        assert all(line.startswith("[ERROR") for line in lines), (device, lines)


def test_doctor_passes_a_matching_device_file_and_is_silent_without_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _swap_pool()
    _write(tmp_path, "rig", {"command": ["x"], "servers": _rows(config)})

    ok = _doctor(monkeypatch, config, tmp_path, "rig")
    assert ok, "맞는 장비 파일인데 doctor가 말하지 않는다"
    assert all(line.startswith("[OK") for line in ok), ok
    # 교대를 안 선언한 실서빙 프로필 — `harness_pool`은 2026-09-29부터 교대를 선언해 hybrid
    # 짝을 쓴다.
    no_swap = load_config("harness_pool_hybrid")
    assert no_swap.swap is None
    assert _doctor(monkeypatch, no_swap, tmp_path, "rig") == [], "교대 없는 프로필"


# --- 의존성: 교대 실행은 콜러블로만 러너에 넘어간다 ----------------------------------------

RUN_ID = "run_20260929_0001"

# 가짜 교대 명령 — argv와 환경을 `calls.jsonl`에 적는다.
_RECORDER = """
import json, os, sys
from pathlib import Path

names = ("SWAP_DEADLINE_S", "HARNESS_RUN_ID", "HARNESS_DEVICE")
with (Path(__file__).parent / "calls.jsonl").open("a", encoding="utf-8") as calls:
    calls.write(json.dumps({"argv": sys.argv[1:], "env": {n: os.environ[n] for n in names}}))
"""


def _device_with_recorder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """`rig` 장비 파일의 명령을 기록기로 두고, factory가 그 파일을 `tmp_path`에서 찾게 한다."""
    script = tmp_path / "swap.py"
    script.write_text(_RECORDER, encoding="utf-8")
    _write(tmp_path, "rig", {"command": [sys.executable, str(script)], "servers": {}})
    real = load_serving
    monkeypatch.setattr(factory, "load_serving", lambda device, **_k: real(device, tmp_path))
    return tmp_path / "calls.jsonl"


def test_the_runner_protocol_and_the_factory_deps_both_carry_swap_to() -> None:
    """러너는 `src/adapters/`를 import 하지 않는다 — 교대 실행은 `Deps.swap_to` 콜러블로만 넘는다.
    Protocol이 선언한 필드는 전부 factory의 `Deps`에 있어야 한다(구조적 만족)."""
    assert "swap_to" in Dependencies.__annotations__
    assert set(Dependencies.__annotations__) <= {field.name for field in fields(Deps)}


def test_build_deps_wires_swap_to_from_the_device_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """교대 프로필 + 장비면 `swap_to`가 장비 파일의 `command`를 부른다 — 마감은
    `settle_timeout_s`, run·장비는 이 run의 것이다."""
    calls = _device_with_recorder(monkeypatch, tmp_path)

    deps = build_deps(
        _swap_fake(),
        run_id=RUN_ID,
        source_root=TARGET,
        artifacts_root=tmp_path / "artifacts",
        device="rig",
    )
    assert deps.swap_to is not None
    deps.swap_to(("fake",))

    (call,) = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
    assert call == {
        "argv": ["fake"],
        "env": {"SWAP_DEADLINE_S": "600", "HARNESS_RUN_ID": RUN_ID, "HARNESS_DEVICE": "rig"},
    }


def test_build_deps_leaves_swap_to_empty_without_a_swap_or_a_device(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """교대가 없는 프로필은 장비를 줘도 `None`이다. 교대 프로필이라도 장비가 없으면 `None`이다 —
    모델에 안 닿는 run(`--dry-run`)은 장비 없이 조립돼야 하고, 닿는 run은 CLI가 먼저 막는다."""
    _device_with_recorder(monkeypatch, tmp_path)

    def swap_to(config: HarnessConfig, device: str | None) -> object:
        return build_deps(
            config,
            run_id=RUN_ID,
            source_root=TARGET,
            artifacts_root=tmp_path / "artifacts",
            device=device,
        ).swap_to

    assert swap_to(load_config("dev_fake"), "rig") is None
    assert swap_to(_swap_fake(), None) is None


def test_build_deps_refuses_a_device_file_that_disagrees_with_the_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """factory는 CLI 밖에서도 불린다 — 틀린 장비 파일로 교대 어댑터를 만들지 않는다."""
    _device_with_recorder(monkeypatch, tmp_path)
    row = _rows(_swap_pool())["seccoderx-8b"]
    _write(tmp_path, "rig", {"command": ["x"], "servers": {"qwen9-999b": row}})

    with pytest.raises(ConfigError, match="qwen9-999b"):
        build_deps(
            _swap_fake(),
            run_id=RUN_ID,
            source_root=TARGET,
            artifacts_root=tmp_path / "artifacts",
            device="rig",
        )


def test_execute_hands_the_run_device_to_build_deps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI 접합부가 `RunSpec.device`를 factory에 넘긴다 — 안 넘기면 교대 run이 `swap_to` 없이
    돈다."""
    seen: dict[str, object] = {}

    class _Built(RuntimeError):
        pass

    def spy(*_: object, **kwargs: object) -> None:
        seen.update(kwargs)
        raise _Built

    monkeypatch.setattr(cli, "build_deps", spy)
    spec = RunSpec(condition=DEFAULT_CONDITION, target=TARGET, device="rig")

    with pytest.raises(_Built):
        cli.execute(_swap_fake(), spec, run_id=RUN_ID, run_dir=tmp_path / RUN_ID)

    assert seen["device"] == "rig"
