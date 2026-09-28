"""러너의 단계 경계 교대 훅 (ADR 0012 Phase 6.5 — 단계 단위 콜드 재기동, ADR 0009).

교대 명령은 가짜다 — `deps.swap_to`를 **올라 있는 키를 드는 기록기**로 바꾼다. 모델도 Fake다.
config만 역할을 실서빙 모양의 키(엔드포인트가 `fake://`가 아닌 것)로 돌려, 러너가 교대 대상을
`served_models()`에서 고르게 한다 — 조립한 deps는 그대로 `dev_fake`의 Fake 클라이언트다.
"""

import dataclasses
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.adapters.factory import build_deps
from src.config import ConfigError, ExecutionPolicy, HarnessConfig, SwapPolicy, load_config
from src.interfaces.model_client import ModelGatewayError, ModelSwapError
from src.orchestrator import runner
from src.orchestrator.runner import RunSpec, run_pipeline
from src.orchestrator.trace import TraceWriter

RUN_ID = "run_20260929_0001"
STARTED_AT = datetime(2026, 9, 29, tzinfo=UTC)
TARGET = Path(__file__).resolve().parent / "fixtures" / "target"
SCOUT, AUDITOR, CRITIC = "served-scout", "served-auditor", "served-critic"


def _config(
    *,
    resident: tuple[str, ...] = (),
    swappable: tuple[str, ...] = ("scout", "auditor", "critic"),
    keys: dict[str, str] | None = None,
) -> HarnessConfig:
    """`dev_fake`의 세 프롬프트 역할을 실서빙 모양의 키로 돌리고 교대를 선언한 config.

    키는 `fake` 항목을 엔드포인트만 바꿔 복제한다 — 프롬프트 버전이 `dev_fake`와 같아야
    Fake 클라이언트가 같은 답을 낸다.
    """
    base = load_config("dev_fake")
    keys = keys or {"scout": SCOUT, "auditor": AUDITOR, "critic": CRITIC}
    fake = base.models["fake"]
    served = {
        key: fake.transition(endpoint=f"http://127.0.0.1:9/{key}")
        for key in set(keys.values())
        if key != "fake"
    }
    execution = ExecutionPolicy(
        mode="per_stage",
        swap=SwapPolicy(resident=resident, swappable=swappable, settle_timeout_s=60.0),
    )
    profile = base.profile.transition(roles={**base.profile.roles, **keys}, execution=execution)
    return base.transition(models={**base.models, **served}, profile=profile)


class _Rig:
    """가짜 교대 + 호출 감시. 교대가 올린 키를 들고, 호출마다 그 역할의 키가 떠 있는지 본다."""

    def __init__(
        self,
        config: HarnessConfig,
        models: object,
        *,
        fail_at: int | None = None,
        error: Exception | None = None,
    ) -> None:
        self.config = config
        self._models = models
        self._fail_at = fail_at
        self._error = error
        self.up: frozenset[str] = frozenset()
        self.swaps: list[tuple[str, ...]] = []
        self.calls: list[tuple[str, str]] = []
        self.misses: list[tuple[str, str, frozenset[str]]] = []

    def swap_to(self, keys: tuple[str, ...]) -> dict[str, object]:
        self.swaps.append(keys)
        if self._fail_at == len(self.swaps):
            raise self._error or ModelSwapError("가짜 교대 실패", report={"server.x.load_s": 3})
        self.up = frozenset(keys)
        return {"ok": True, "models": " ".join(keys)}

    def complete(self, *, role, prompt, schema, history=()):  # noqa: ANN001, ANN201
        key = self.config.model_key_for(role.value)
        self.calls.append((role.value, key))
        if not self.config.models[key].is_fake and key not in self.up:
            self.misses.append((role.value, key, self.up))
        return self._models.complete(role=role, prompt=prompt, schema=schema, history=history)


def _run(
    tmp_path: Path,
    config: HarnessConfig,
    *,
    rig: _Rig | None = None,
    target: Path = TARGET,
    wire: bool = True,
    **spec_fields: object,
) -> tuple[runner.RunResult, list[dict], _Rig]:
    """`run_pipeline`을 한 번 돌리고 (결과, trace 이벤트, 기록기)를 돌려준다."""
    deps = build_deps(
        load_config("dev_fake"),
        run_id=RUN_ID,
        source_root=target,
        artifacts_root=tmp_path / "artifacts",
    )
    rig = rig or _Rig(config, deps.models)
    deps = dataclasses.replace(deps, models=rig, swap_to=rig.swap_to if wire else None)
    run_dir = tmp_path / "artifacts" / RUN_ID
    trace = TraceWriter(RUN_ID)
    result = run_pipeline(
        RunSpec(condition="B3prime@pipeline", target=target, started_at=STARTED_AT, **spec_fields),
        config=config,
        deps=deps,
        read_source=lambda _loc: "int main(void) { return 0; }\n",
        trace=trace,
        run_dir=run_dir,
    )
    events = [_flat(json.loads(event.model_dump_json())) for event in trace.events]
    return result, events, rig


def _flat(event: dict) -> dict:
    """이벤트의 `data`를 겉으로 편다 — 검사가 `models`·`ok`·`reason`을 바로 읽게."""
    return {**event, **event["data"]}


def _swap_events(events: list[dict]) -> list[dict]:
    return [event for event in events if event["kind"] == "model_swap"]


def _stop_reason(events: list[dict]) -> str | None:
    (end,) = [e for e in events if e["kind"] == "transition" and e.get("subject_kind") == "run"]
    return end["reason"]


# --- 교대 시점과 대상 ---------------------------------------------------------------


def test_swaps_follow_the_stages_that_call_models_and_end_with_a_teardown(
    tmp_path: Path,
) -> None:
    """4단계(Scout) → 5단계(Auditor) → 7단계(Critic) → run 끝 내림. 3·6단계는 모델을 안 부르므로
    교대하지 않는다 — 앞 단계 서버가 그대로 있다."""
    _, events, rig = _run(tmp_path, _config())

    assert rig.swaps == [(SCOUT,), (AUDITOR,), (CRITIC,), ()]
    swaps = _swap_events(events)
    assert [(e["stage"], e["models"], e["ok"]) for e in swaps] == [
        (4, SCOUT, True),
        (5, AUDITOR, True),
        (7, CRITIC, True),
        (None, "", True),
    ]
    assert swaps[0]["report.models"] == SCOUT, "교대 명령의 보고가 report.* 로 실리지 않는다"


def test_resident_roles_stay_up_and_shared_or_fake_keys_are_swapped_once_or_not_at_all(
    tmp_path: Path,
) -> None:
    """대상 = 상주 ∪ (그 단계 역할 ∩ 교대). 두 역할이 한 키면 한 번만, Fake 키는 넣지 않는다."""
    shared = _config(
        resident=("auditor",), swappable=("scout", "critic"), keys={
            "scout": SCOUT, "auditor": AUDITOR, "critic": SCOUT,
        },
    )  # fmt: skip
    _, _, rig = _run(tmp_path / "shared", shared)
    assert rig.swaps == [(AUDITOR, SCOUT), (AUDITOR,), (AUDITOR, SCOUT), ()]

    faked = _config(keys={"scout": SCOUT, "auditor": "fake", "critic": CRITIC})
    _, _, rig = _run(tmp_path / "faked", faked)
    assert rig.swaps == [(SCOUT,), (), (CRITIC,), ()]


def test_no_critic_swap_when_nothing_is_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner.critic, "select", lambda _ctx, _promoted: [])

    _, _, rig = _run(tmp_path, _config())

    assert rig.swaps == [(SCOUT,), (AUDITOR,), ()]


def test_no_swap_for_a_stage_without_items(tmp_path: Path) -> None:
    """대상이 0이면 4·5단계에 올릴 것이 없다 — 교대 없이 run 끝 내림만 남는다."""
    empty = tmp_path / "empty"
    empty.mkdir()

    _, _, rig = _run(tmp_path, _config(), target=empty)

    assert rig.swaps == [()]


# --- 실패 ---------------------------------------------------------------------------


def test_a_failed_swap_stops_the_run_with_its_own_reason_and_still_tears_down(
    tmp_path: Path,
) -> None:
    """교대 실패는 서버 사망(`model_gateway_unavailable`)이 아니다 — 사유를 갈라 적고, `ok=false`
    이벤트가 교대 명령의 부분 보고와 함께 남고, 매니페스트까지 가고, 그래도 내린다."""
    config = _config()
    deps_models = build_deps(
        load_config("dev_fake"), run_id=RUN_ID, source_root=TARGET,
        artifacts_root=tmp_path / "artifacts",
    ).models  # fmt: skip
    rig = _Rig(config, deps_models, fail_at=2)

    result, events, _ = _run(tmp_path, config, rig=rig)

    assert result.record.stopped_after is not None
    assert _stop_reason(events) == "model_swap_failed"
    failed = [e for e in _swap_events(events) if not e["ok"]]
    assert [(e["stage"], e["models"]) for e in failed] == [(5, AUDITOR)]
    assert failed[0]["report.server.x.load_s"] == 3
    assert "가짜 교대 실패" in failed[0]["error"]
    assert (tmp_path / "artifacts" / RUN_ID / "run_manifest.json").exists()
    assert rig.swaps[-1] == (), "교대가 실패한 run이 띄운 서버를 내리지 않는다"


def test_a_run_that_fails_elsewhere_still_tears_down(tmp_path: Path) -> None:
    """서버 사망으로 멈춘 run도 끝에 내린다 — 멈춘 run이 GPU를 붙들고 남으면 다음 run이 못 뜬다."""
    config = _config()

    class _Dead:
        def complete(self, *, role, **_kwargs):  # noqa: ANN001, ANN003, ANN202
            if role.value == "auditor":
                raise ModelGatewayError("연결 거부", status=None)
            return inner.complete(role=role, **_kwargs)

    inner = build_deps(
        load_config("dev_fake"), run_id=RUN_ID, source_root=TARGET,
        artifacts_root=tmp_path / "artifacts",
    ).models  # fmt: skip
    rig = _Rig(config, _Dead())

    _, events, _ = _run(tmp_path, config, rig=rig)

    assert _stop_reason(events) == "model_gateway_unavailable"
    assert rig.swaps[-1] == ()


class _Unforeseen(Exception):
    """표준 예외 계층 밖의 `Exception` — 「`Exception`을 잡는다」를 철자가 아니라 성질로 문다.

    `RuntimeError`만 두면 `except (ModelSwapError, RuntimeError)`로 좁혀도 초록이다(2026-09-29
    적대적 리뷰 실측). 어떤 표준 예외도 상속하지 않아 그 튜플 어디에도 안 걸린다.
    """


# 내림 실패의 모양 — 계약대로(`ModelSwapError`)와 계약 밖(날것 · 표준 계층 밖). 러너의 `finally`는
# 교대 콜러블이 계약을 지킨다고 믿지 않는다 — 거기서 새는 예외는 run의 결과·원래 예외를 덮는다.
# 인스턴스를 테스트마다 새로 만든다 — 한 번 올린 예외는 `__context__`·`__traceback__`를 든다.
_TEARDOWN_ERRORS = pytest.mark.parametrize(
    "make_error",
    [
        lambda: ModelSwapError("가짜 내림 실패"),
        lambda: RuntimeError("계약 밖 내림 실패"),
        lambda: _Unforeseen("표준 계층 밖 내림 실패"),
    ],
    ids=["swap-error", "raw-error", "unforeseen-error"],
)


@_TEARDOWN_ERRORS
def test_a_failed_teardown_does_not_hide_the_original_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_error: Callable[[], Exception]
) -> None:
    """내림이 실패해도 원래 예외가 올라간다 — 내림 실패는 경고로 남고 원래 예외를 바꾸지 않는다."""

    class _Boom(RuntimeError):
        pass

    def explode(*_args: object, **_kwargs: object) -> None:
        raise _Boom

    monkeypatch.setattr(runner.critic, "run", explode)
    config = _config()
    inner = build_deps(
        load_config("dev_fake"), run_id=RUN_ID, source_root=TARGET,
        artifacts_root=tmp_path / "artifacts",
    ).models  # fmt: skip
    rig = _Rig(config, inner, fail_at=4, error=make_error())  # 4번째 = run 끝 내림

    with pytest.raises(_Boom):
        _run(tmp_path, config, rig=rig)

    assert rig.swaps == [(SCOUT,), (AUDITOR,), (CRITIC,), ()]


@_TEARDOWN_ERRORS
def test_a_failed_teardown_still_writes_the_manifest_of_a_finished_run(
    tmp_path: Path, make_error: Callable[[], Exception]
) -> None:
    """완주한 run의 run 끝 내림이 실패해도 매니페스트까지 가고 경고가 남는다 — 내림 실패가
    `run_pipeline` 밖으로 새면 8단계까지 돈 run이 산출물 없이 사라진다."""
    config = _config()
    inner = build_deps(
        load_config("dev_fake"), run_id=RUN_ID, source_root=TARGET,
        artifacts_root=tmp_path / "artifacts",
    ).models  # fmt: skip
    error = make_error()
    rig = _Rig(config, inner, fail_at=4, error=error)  # 4번째 = run 끝 내림

    result, _, _ = _run(tmp_path, config, rig=rig)

    assert rig.swaps == [(SCOUT,), (AUDITOR,), (CRITIC,), ()]
    assert result.record.stopped_after is None, "내림 실패가 완주를 중단으로 바꿨다"
    assert (tmp_path / "artifacts" / RUN_ID / "run_manifest.json").exists()
    assert any(
        "run 끝 모델 내림" in warning and str(error) in warning for warning in result.warnings
    )


# --- 교대 콜러블 없이 모델에 닿는 run ---------------------------------------------------


def test_a_swap_profile_without_swap_to_refuses_a_run_that_reaches_a_model(
    tmp_path: Path,
) -> None:
    """교대를 선언했는데 교대 콜러블이 없으면 모델에 닿는 run은 시작하지 않는다 — 서버가 안 뜬
    채 4단계에 들어가면 게이트웨이 오류로 멈추고, 그 사유는 틀린다(CLI 밖의 호출자 가드)."""
    with pytest.raises(ConfigError, match="교대"):
        _run(tmp_path, _config(), wire=False)


def test_a_swap_profile_without_swap_to_still_runs_what_never_reaches_a_model(
    tmp_path: Path,
) -> None:
    result, events, _ = _run(tmp_path, _config(), wire=False, stop_after=3)

    assert result.record.stopped_after == 3
    assert _swap_events(events) == []


# --- 성질: 모델을 부를 때 그 모델이 올라 있다 -------------------------------------------


@pytest.mark.parametrize(
    ("resident", "swappable"),
    [((), ("scout", "auditor", "critic")), (("auditor",), ("scout", "critic"))],
    ids=["all-swapped", "auditor-resident"],
)
def test_every_model_call_finds_its_model_up(
    tmp_path: Path, resident: tuple[str, ...], swappable: tuple[str, ...]
) -> None:
    """**모든 모델 호출 시점에 호출된 역할의 모델 키 ⊆ 그때 올라 있는 키.**

    단계 → 역할 선언(`runner._ROLES_CALLED`)이 한 좌석이라도 빼먹으면 — 새 단계가 모델을
    부르는데 선언이 없거나, 훅이 그 단계에 안 걸리면 — 그 좌석의 첫 호출이 여기서 걸린다.

    ⚠ **한계: 선언이 넘치는 것은 못 잡는다.** 부르지 않는 역할을 선언해 쓸데없는 모델을 올려도
    호출은 전부 올라 있는 키를 만나므로 초록이다(메모리 낭비·교대 시간은 이 검사 밖이다). 교대
    순서·대상 테스트가 그 반대쪽을 본다.
    """
    config = _config(resident=resident, swappable=swappable)

    _, _, rig = _run(tmp_path, config)

    called = {role for role, _ in rig.calls}
    assert {"scout", "auditor", "critic"} <= called, f"검사가 모든 좌석을 안 봤다: {called}"
    assert rig.misses == [], f"모델이 안 떠 있는데 불렀다: {rig.misses}"


def test_the_role_declaration_names_every_stage_that_calls_a_model() -> None:
    """선언 표가 3~6단계 표의 모델 단계(4·5)와 7단계를 든다 — 교대 코드가 `Stage`로 가르지 않고
    이 표 하나만 읽는다."""
    assert set(runner._ROLES_CALLED) == {
        runner.Stage.SCOUT,
        runner.Stage.AUDITOR,
        runner.Stage.CRITIC,
    }


# --- 교대 실패는 후보·Finding 하나의 실패가 아니다 --------------------------------------


@pytest.mark.parametrize("mode", ["per_stage", "per_candidate"])
@pytest.mark.parametrize("role", ["scout", "auditor", "critic"])
def test_a_swap_error_is_never_dropped_as_one_candidate_or_finding(
    tmp_path: Path, role: str, mode: str
) -> None:
    """`ModelSwapError`가 폐기 판정 try 안에서 나도(단계 루프·후보 루프·Critic 루프) 그 시도만
    버리지 않고 run을 `model_swap_failed`로 멈춘다 — `_is_droppable`이 거짓이라 다시 올라간다.

    지금 교대는 그 try **밖**에서만 불리므로 이 경로는 방어선이다. 누가 교대 호출을 스텝 안으로
    옮기거나 `_is_droppable`을 넓히면 교대 실패가 「시도 하나 폐기」로 둔갑한다 — 그것을 문다.
    """
    base = load_config("dev_fake")
    profile = base.profile.transition(execution=ExecutionPolicy(mode=mode))
    config = base.transition(profile=profile)
    inner = build_deps(
        base, run_id=RUN_ID, source_root=TARGET, artifacts_root=tmp_path / "artifacts"
    ).models

    class _SwapFails:
        def complete(self, **kwargs):  # noqa: ANN003, ANN202
            if kwargs["role"].value == role:
                raise ModelSwapError("교대가 이 호출 안에서 실패했다")
            return inner.complete(**kwargs)

    _, events, _ = _run(tmp_path, config, rig=_Rig(config, _SwapFails()), wire=False)

    assert _stop_reason(events) == "model_swap_failed"
