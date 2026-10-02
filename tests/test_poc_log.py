"""PoC 사이드카 — 조립된 바이트가 run 산출물에 파일로 남고 해시로 대조된다 (#355).

`test_prompt_log.py`의 핀을 미러한다: ref는 run_dir 상대이고 `run_id`를 안 품으며, digest는
**그 파일 내용**의 것이고, dry-run·예산 소진에서는 디렉터리조차 생기지 않는다.

**바이트 자체를 대조한다.** 길이와 해시만 보면 쓰기 경로가 다른 바이트를 남겨도 초록이다 —
길이가 같은 변이는 길이 검사를 통과하고, 파일에서 다시 해시를 계산하는 검사는 동어반복이라
"digest가 파일과 맞나"만 보고 "파일이 조립 결과와 맞나"는 안 본다. 기대값은 `assemble_poc`에서
뽑는다(`test_nodes.test_run_pov_generates_and_records_the_poc_byte_size`와 같은 방식).
"""

import dataclasses
import json
from pathlib import Path

import pytest

from src.ids import sha256_digest
from src.interfaces.model_client import Usage
from src.orchestrator import runner
from src.orchestrator.budget import BudgetExhausted
from src.orchestrator.context import RunContext
from src.orchestrator.nodes import pov
from src.orchestrator.poc_assembly import assemble_poc
from src.orchestrator.poc_log import DIR_NAME, PocLog
from src.orchestrator.stages import Stage
from src.orchestrator.trace import EventKind

# `ctx`는 dev_fake로 배선된 픽스처, `_pov_state`는 finding+캡슐을 담은 state 헬퍼,
# `_run`은 파이프라인 한 번(run_dir·trace)이다 — test_pov_loop이 test_nodes에서 헬퍼를 끌어
# 쓰는 것과 같은 방식.
from tests.test_nodes import _pov_state, ctx  # noqa: F401
from tests.test_prompt_log import RUN_ID, _run


def _wired(ctx: RunContext, run_dir: Path) -> RunContext:  # noqa: F811
    """사이드카만 배선한 문맥. 러너의 주입 자리(`run_dir`·`dry_run` 가드)는 ⑥의 파이프라인
    검사가 본다 — 여기서 손으로 주입하면 그 배선이 끊겨도 초록이기 때문이다."""
    return dataclasses.replace(ctx, pocs=PocLog(run_dir))


def _terminal(ctx: RunContext):  # noqa: ANN202, F811
    """루프 종단 이벤트 하나(`POV_GENERATED` 또는 `POV_SKIPPED`)."""
    terminal = [
        e
        for e in ctx.trace.events
        if e.kind in (EventKind.POV_GENERATED, EventKind.POV_SKIPPED)
    ]
    assert len(terminal) == 1, f"종단 이벤트가 하나가 아니다: {len(terminal)}"
    return terminal[0]


def test_sidecar_file_holds_the_assembled_poc_byte_for_byte(
    ctx: RunContext,  # noqa: F811
    tmp_path: Path,
) -> None:
    """파일 바이트 == `assemble_poc` 결과. 기대값은 조립에서 뽑는다(하드코딩 아님)."""
    run_dir = tmp_path / RUN_ID
    wired = _wired(ctx, run_dir)
    state, finding = _pov_state(wired)
    capsule = state.finding_capsules[finding.finding_id]
    hop0 = pov.open_hop(wired, finding=finding, capsule=capsule)  # 결정론 fake → gen
    assert hop0.action.action == "gen" and hop0.action.spec is not None
    expected = assemble_poc(hop0.action.spec)

    runner._run_pov(wired, state, finding)

    ref = _terminal(wired).data["poc_ref"]
    assert (run_dir / ref).read_bytes() == expected


def test_poc_ref_is_run_dir_relative_and_carries_no_run_id(
    ctx: RunContext,  # noqa: F811
    tmp_path: Path,
) -> None:
    """ref가 run 의존 문자열을 들이면 trace 비교 단위가 run마다 달라진다."""
    run_dir = tmp_path / RUN_ID
    wired = _wired(ctx, run_dir)
    state, finding = _pov_state(wired)

    runner._run_pov(wired, state, finding)

    ref = _terminal(wired).data["poc_ref"]
    assert RUN_ID not in ref
    assert not Path(ref).is_absolute()
    assert (run_dir / ref).is_file()


def test_poc_digest_matches_the_file_and_the_bare_hex_stays_bare(
    ctx: RunContext,  # noqa: F811
    tmp_path: Path,
) -> None:
    """digest는 **파일 내용**의 것이다(#387: 쓰기 중 개행 번역이 끼면 여기서 어긋난다).

    같은 이벤트의 `poc_sha256`은 **접두사 없는 맨 hex**로 남는다(핀 `test_nodes.py:530`) —
    두 형식이 다르다는 사실을 여기서 고정해 둔다. 하나로 「통일」하면 둘 중 하나가 깨진다.
    """
    run_dir = tmp_path / RUN_ID
    wired = _wired(ctx, run_dir)
    state, finding = _pov_state(wired)

    runner._run_pov(wired, state, finding)

    data = _terminal(wired).data
    written = (run_dir / data["poc_ref"]).read_bytes()
    assert data["poc_digest"] == sha256_digest(written)
    assert data["poc_digest"] == f"sha256:{data['poc_sha256']}"


def test_budget_exhaustion_leaves_no_poc_directory(
    ctx: RunContext,  # noqa: F811
    tmp_path: Path,
) -> None:
    """예산 소진은 루프를 뚫고 올라간다(추가형 계약) — 디렉터리조차 안 생겨야 한다.

    `test_prompt_log.test_budget_exhaustion_leaves_no_orphan_file`과 같은 자리다.
    """
    run_dir = tmp_path / RUN_ID
    wired = _wired(ctx, run_dir)
    state, finding = _pov_state(wired)
    capsule = state.finding_capsules[finding.finding_id]
    wired.ledger.charge(
        Usage(
            input_tokens=wired.config.budget.input_tokens + 1,
            output_tokens=0,
            cached_tokens=0,
        ),
        stage=Stage.VALIDATE,
    )

    with pytest.raises(BudgetExhausted):
        runner._run_pov_hops(wired, state, finding, capsule)

    assert not (run_dir / DIR_NAME).exists()


def test_dry_run_leaves_no_poc_artifacts(tmp_path: Path) -> None:
    """`write_manifest`·`PromptLog`와 같은 가드다 — 매니페스트 없는 run에 PoC만 남지 않는다.

    ⚠ **이 검사는 가드 줄을 물지 않는다.** dry-run은 `DRY_RUN_LAST_STAGE`(=RETRIEVAL)에서
    서므로 8단계 pov에 애초에 못 간다 — 가드를 지워도 디렉터리는 안 생긴다(오염으로 확인:
    가드를 통째로 지운 전수 실행이 그대로 초록이었다). 여기서 고정하는 것은 **관측 가능한
    명제**(dry-run 산출물에 PoC가 없다)이고, 가드 줄은 아래 검사가 문다.
    """
    run_dir, _ = _run(tmp_path, dry_run=True)

    assert not (run_dir / DIR_NAME).exists()


def test_the_sidecar_is_wired_only_for_a_real_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """주입 가드(`run_dir` 있음 + dry-run 아님)를 **생성 여부로** 문다.

    디렉터리 부재만 보는 위 검사로는 가드가 사라져도 빨개지지 않는다. 양방향으로 센다 —
    dry-run은 0개, 실제 run은 1개. 한쪽만 보면 "아무 때도 안 만든다"가 통과한다.
    """
    built: list[Path] = []
    real = runner.PocLog

    def spy(run_dir: Path) -> PocLog:
        built.append(run_dir)
        return real(run_dir)

    monkeypatch.setattr(runner, "PocLog", spy)

    _run(tmp_path / "dry", dry_run=True)
    assert built == [], f"dry-run이 사이드카를 만들었다: {built}"

    _run(tmp_path / "live")
    assert len(built) == 1, f"실제 run이 사이드카를 안 만들었다: {built}"


def test_pipeline_writes_the_poc_sidecar_for_dev_fake(tmp_path: Path) -> None:
    """실제 run에서 **배선이 돈다.** 위 단위 검사는 사이드카를 손으로 주입하므로 러너의 주입
    줄을 지워도 초록이다 — 이 검사가 그 줄을 문다(`test_pipeline_e2e`의 pov 배선 검사와 같은 결).
    """
    run_dir, trace_path = _run(tmp_path)

    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    generated = [e["data"] for e in events if e["kind"] == EventKind.POV_GENERATED.value]
    assert generated, "dev_fake가 pov를 선언하는데 pov_generated가 0건 — 라우팅이 끊겼다"
    for data in generated:
        path = run_dir / data["poc_ref"]
        assert path.is_file()
        assert data["poc_digest"] == sha256_digest(path.read_bytes())
        assert data["poc_bytes"] == path.stat().st_size
