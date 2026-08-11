"""파이프라인 end-to-end 테스트.

이 파일이 PR의 주장을 실증한다. 세 가지를 본다.

1. dev_fake 프로필로 0~9단계가 완주하고 `RunRecord.finished_at`이 채워진다
2. 같은 조건으로 두 번 실행하면 trace JSONL에서 `ts`·`wall_ms`만 뺐을 때 바이트 동일
3. 계획 §6 지표 대응표의 앞 8행이 trace에서 실제로 계산된다

**측정값은 하드코딩하지 않는다.** 제거율·토큰 수는 이 프로젝트가 재려는 값이다.
여기서 단언하는 것은 "산출 가능하다"와, Scout 제거율에 한해 "0%도 100%도 아니다"뿐이다.
"""

import io
import json
import socket
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src import cli, console
from src.adapters.factory import build_deps, describe_wiring
from src.config import PROMPTED_ROLES, load_config
from src.ids import new_run_id
from src.orchestrator import runner
from src.orchestrator.runner import RunSpec
from src.orchestrator.stages import Stage
from src.orchestrator.trace import VOLATILE_FIELDS
from src.schemas.enums import FindingState, ReasonCode
from src.schemas.run import Budget
from tests.conftest import ALL_PROFILES

TARGET = Path(__file__).resolve().parent / "fixtures" / "target"
STARTED_AT = datetime(2026, 9, 1, tzinfo=UTC)
CONDITION = "B3prime@pipeline"


def _run(tmp_path: Path, name: str = "run", *, profile: str = "dev_fake", **overrides: object):  # noqa: ANN202
    """파이프라인을 한 번 돌리고 (결과, trace 경로)를 돌려준다. 기본은 dev_fake."""
    config = load_config(profile)
    if "budget" in overrides:
        config = config.transition(budget=overrides.pop("budget"))
    run_dir = tmp_path / name / "run_20260901_0001"
    run_dir.mkdir(parents=True)
    spec = RunSpec(condition=CONDITION, target=TARGET, started_at=STARTED_AT, **overrides)
    trace_path = run_dir / "trace.jsonl"
    with trace_path.open("w", encoding="utf-8") as stream:
        result, _ = cli.execute(
            config, spec, run_id=run_dir.name, run_dir=run_dir, trace_stream=stream
        )
    return result, trace_path


def _events(trace_path: Path) -> list[dict]:
    return [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]


def _canonical(trace_path: Path) -> bytes:
    """재현성 비교 표현: `ts`·`wall_ms`만 제거한 JSONL."""
    lines = []
    for event in _events(trace_path):
        for field in VOLATILE_FIELDS:
            event.pop(field, None)
        lines.append(json.dumps(event, ensure_ascii=False, sort_keys=True))
    return "\n".join(lines).encode("utf-8")


def _charged(trace_path: Path) -> int:
    """run이 끝난 시점의 누적 input 토큰. `RunRecord.budget`은 할당량이라 소비가 아니다.

    `budget` 이벤트는 **그 시점까지의 누적값**이지 이번 차감분이 아니다. 합산하면
    스냅샷을 여러 번 더하게 되어 아무 뜻도 없는 수가 나온다 — 단조 증가라 대소 비교는
    우연히 맞지만, 그 값을 "소비량"이라 부르면 지표가 거짓이 된다.
    """
    charges = [
        event["data"]["input_tokens"]
        for event in _of(_events(trace_path), "budget")
        if event["data"]["event"] == "charge"
    ]
    return charges[-1] if charges else 0


def _of(events: list[dict], kind: str, *, stage: int | None = None) -> list[dict]:
    return [e for e in events if e["kind"] == kind and (stage is None or e["stage"] == int(stage))]


def _run_transition(trace_path: Path) -> dict:
    """run 객체의 마지막 전이(R-1/R-2) 이벤트. 종료 사유가 여기 남는다.

    `stage`는 이벤트의 최상위 필드이고 `rule`·`reason`은 `data` 안이므로 이벤트 전체를
    돌려준다.
    """
    runs = [e for e in _of(_events(trace_path), "transition") if e["subject_kind"] == "run"]
    assert runs, "run 전이가 없으면 종료 사유를 읽을 수 없다"
    return runs[-1]


# --- 1. 완주 ----------------------------------------------------------------


def test_pipeline_runs_all_stages_end_to_end(tmp_path: Path) -> None:
    result, trace_path = _run(tmp_path)

    stages_seen = {e["stage"] for e in _of(_events(trace_path), "stage_start")}
    assert stages_seen == set(range(int(Stage.REPORT) + 1))
    assert result.record.finished_at is not None
    assert result.record.stopped_after is None
    assert result.record.is_complete


def test_pipeline_promotes_one_target_and_filters_the_other(tmp_path: Path) -> None:
    """파이프라인이 실제로 무언가를 통과시키고 무언가를 거른다."""
    result, _ = _run(tmp_path)

    states = {c.location.file: c.state for c in result.candidates}
    assert states["parser.c"] is FindingState.TRIAGED
    assert states["util.c"] is FindingState.QUEUED  # 폐기가 아니라 보류
    assert any(f.state is FindingState.PROVEN for f in result.findings)


def test_manifest_is_written_with_reproducibility_fields(tmp_path: Path) -> None:
    result, trace_path = _run(tmp_path)

    manifest = json.loads((trace_path.parent / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == result.record.run_id
    assert manifest["condition"] == CONDITION
    assert manifest["source_hash"].startswith("sha256:")
    assert manifest["thresholds"]["scout_threshold"] > 0
    assert manifest["finished_at"].endswith("Z")


def test_incomplete_manifest_fields_are_reported_not_faked(tmp_path: Path) -> None:
    """컨테이너 digest가 없으면 지어내지 않고 경고한다 (engineering-guide.md §5)."""
    result, _ = _run(tmp_path)

    assert result.record.tool_images == {}
    assert any("tool_images" in warning for warning in result.warnings)


# --- 하네스 저장소 상태 기록 (P3) --------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _fresh_repo(root: Path) -> Path:
    """커밋 1개짜리 깨끗한 git 저장소."""
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "test")
    (repo / "a.txt").write_text("first", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "first")
    return repo


def test_clean_repo_is_recorded_as_not_dirty(tmp_path: Path) -> None:
    repo = _fresh_repo(tmp_path)

    commit, dirty, warnings = runner._repo_state(repo)

    assert len(commit) == 40
    assert dirty is False
    assert warnings == []


def test_dirty_repo_is_recorded(tmp_path: Path) -> None:
    """실측(2026-08-11): config/models.yaml·profiles.yaml을 고친 상태로 세 run이 돌았다.

    모델도 엔드포인트도 바뀐 실행인데 manifest만 보면 커밋이 그대로라 재현 가능해 보였다.

    dirty는 읽기 실패가 아니라 사실이므로 `_repo_state`는 경고하지 않는다 — "비교표에
    넣을 수 없다"는 판단은 `incomplete_fields`가 하고, 아래 별도 테스트가 그것을 본다.
    """
    repo = _fresh_repo(tmp_path)
    (repo / "a.txt").write_text("changed", encoding="utf-8")

    _, dirty, warnings = runner._repo_state(repo)

    assert dirty is True
    assert warnings == []


def test_untracked_file_also_counts_as_dirty(tmp_path: Path) -> None:
    """`git status --porcelain`은 미추적 파일도 낸다. 그것도 재현을 깨뜨린다."""
    repo = _fresh_repo(tmp_path)
    (repo / "scratch.yaml").write_text("local: true", encoding="utf-8")

    _, dirty, _ = runner._repo_state(repo)

    assert dirty is True


def test_non_repository_records_unknown_not_false(tmp_path: Path) -> None:
    """git이 없거나 저장소가 아니면 "확인 못 함"이다.

    불리언 두 값으로는 이 상태를 표현할 수 없다. `False`로 적으면 "깨끗하다"는
    적극적인 거짓말이 되므로 `None`을 쓴다.
    """
    plain = tmp_path / "not-a-repo"
    plain.mkdir()

    commit, dirty, warnings = runner._repo_state(plain)

    assert dirty is None
    assert commit == runner._UNKNOWN_COMMIT
    assert warnings


def test_dirty_and_unknown_both_reach_incomplete_fields(tmp_path: Path) -> None:
    """경고가 `incomplete_fields`를 통해 실제로 사용자에게 도달하는지 본다.

    `_repo_state`가 값을 잘 만들어도 그 값이 경고 목록에 연결되지 않으면 run은
    여전히 조용히 비교표에 들어간다.
    """
    result, _ = _run(tmp_path)
    record = result.record

    def repo_warnings(dirty: bool | None) -> list[str]:
        # `incomplete_fields`는 tool_images·kb_snapshot 경고도 함께 낸다. 목록이 비었는지만
        # 보면 repo_dirty 경고가 아예 없어도 통과한다 — 그래서 해당 경고만 골라낸다.
        found = runner.incomplete_fields(record.transition(repo_dirty=dirty))
        return [w for w in found if "repo_dirty" in w]

    assert repo_warnings(False) == []
    assert repo_warnings(True), "dirty한 run이 경고 없이 비교표에 들어간다"
    assert repo_warnings(None), "확인 못 한 run이 경고 없이 비교표에 들어간다"


def test_manifest_carries_the_repo_dirty_flag(tmp_path: Path) -> None:
    """기록이 manifest까지 실제로 흘러가는지 본다 — 필드가 빠지면 재현 판단이 불가능하다."""
    _, trace_path = _run(tmp_path)

    manifest = json.loads((trace_path.parent / "run_manifest.json").read_text(encoding="utf-8"))
    assert "repo_dirty" in manifest
    assert manifest["repo_dirty"] in (True, False, None)


# --- 2. 재현성 --------------------------------------------------------------


def test_two_runs_are_byte_identical_after_dropping_volatile_fields(tmp_path: Path) -> None:
    """재현성 기준 그대로: ts·wall_ms만 제거하면 두 실행의 trace가 바이트 동일하다."""
    _, first = _run(tmp_path, "a")
    _, second = _run(tmp_path, "b")

    assert _canonical(first) == _canonical(second)
    assert _canonical(first) != b""  # 빈 파일끼리 비교해 통과하는 것을 막는다


def test_trace_actually_carries_the_volatile_fields(tmp_path: Path) -> None:
    """위 비교가 공허하지 않다는 증거 — 제거 대상이 실제로 파일에 있다."""
    _, first = _run(tmp_path, "a")

    assert all("ts" in json.loads(line) for line in first.read_text("utf-8").splitlines())


# --- 3. 지표 산출 가능성 ----------------------------------------------------


def test_stage_removal_rates_are_derivable(tmp_path: Path) -> None:
    """계획 §6 대응표 1~4행. 분모가 0이면 산출 자체가 불가능하므로 그것부터 본다."""
    _, trace_path = _run(tmp_path)
    events = _events(trace_path)

    scout = _of(events, "transition", stage=Stage.SCOUT)
    created = [e for e in scout if e["data"]["rule"] == "C-1"]
    promoted = [e for e in scout if e["data"]["reason"] == ReasonCode.SCOUT_PROMOTED.value]
    assert created, "4단계 생성 기록이 없으면 제거율의 분모가 없다"
    scout_rate = 1 - len(promoted) / len(created)

    graded = _of(events, "gate_eval", stage=Stage.GATE)
    assert graded
    gate_rate = sum(1 for e in graded if e["data"]["grade"] == "E0") / len(graded)

    critic = _of(events, "transition", stage=Stage.CRITIC)
    assert critic
    rejected = {ReasonCode.CRITIC_REBUTTED.value, ReasonCode.CRITIC_NEED_EVIDENCE.value}
    critic_rate = sum(1 for e in critic if e["data"]["reason"] in rejected) / len(critic)

    repro = {ReasonCode.REPRO_FAILED.value, ReasonCode.REPRO_CONFIRMED.value}
    validated = [
        e for e in _of(events, "transition", stage=Stage.VALIDATE) if e["data"]["reason"] in repro
    ]
    assert validated, "8단계까지 간 Finding이 없으면 도구 검증 제거율이 정의되지 않는다"
    tool_rate = sum(
        1 for e in validated if e["data"]["reason"] == ReasonCode.REPRO_FAILED.value
    ) / len(validated)

    for rate in (scout_rate, gate_rate, critic_rate, tool_rate):
        assert 0.0 <= rate <= 1.0


def test_scout_removal_rate_is_not_degenerate(tmp_path: Path) -> None:
    """0%도 100%도 아니어야 한다 — 파이프라인이 실제로 후보를 거르고 있다는 뜻이다."""
    _, trace_path = _run(tmp_path)
    scout = _of(_events(trace_path), "transition", stage=Stage.SCOUT)
    created = [e for e in scout if e["data"]["rule"] == "C-1"]
    promoted = [e for e in scout if e["data"]["reason"] == ReasonCode.SCOUT_PROMOTED.value]

    assert 0.0 < 1 - len(promoted) / len(created) < 1.0


def test_token_and_cache_metrics_are_derivable(tmp_path: Path) -> None:
    """계획 §6 대응표 5~8행."""
    _, trace_path = _run(tmp_path)
    events = _events(trace_path)
    calls = _of(events, "model_call")
    assert calls

    ok_calls = [e for e in calls if e["data"]["ok"]]
    assert all(
        {"input_tokens", "output_tokens", "cached_tokens"} <= set(e["data"]) for e in ok_calls
    )
    inputs = sum(e["data"]["input_tokens"] for e in ok_calls)
    cached = sum(e["data"]["cached_tokens"] for e in ok_calls)
    assert inputs > 0
    # prefix를 재사용했으므로 캐시된 몫이 있어야 한다. 0이면 prefix가 흔들렸다는 뜻이다.
    assert 0 < cached / inputs <= 1

    capsule_cache = _of(events, "cache")
    assert capsule_cache, "cache 이벤트가 없으면 캡슐 적중률을 잴 수 없다 (on_cache 미배선)"
    hit_ratio = sum(1 for e in capsule_cache if e["data"]["hit"]) / len(capsule_cache)
    assert 0.0 <= hit_ratio <= 1.0

    parse_failure_rate = sum(1 for e in calls if not e["data"]["ok"]) / len(calls)
    assert 0.0 <= parse_failure_rate <= 1.0


def test_role_and_stage_are_present_for_per_role_aggregation(tmp_path: Path) -> None:
    """지표는 역할·단계별로 쪼개야 한다. 그 열이 실제로 있는지 본다."""
    _, trace_path = _run(tmp_path)
    calls = _of(_events(trace_path), "model_call")

    assert {e["data"]["role"] for e in calls} == {"scout", "auditor", "critic"}
    assert all(e["stage"] is not None and e["data"]["model"] for e in calls)


# --- 중단 경로 --------------------------------------------------------------


def test_budget_exhaustion_still_reaches_the_report(tmp_path: Path) -> None:
    """예산 소진은 실패가 아니라 종료 경로다 (architecture.md §5)."""
    tiny = Budget(input_tokens=1, output_tokens=1, gpu_seconds=0)
    result, trace_path = _run(tmp_path, budget=tiny)

    assert result.record.stopped_after is not None
    assert result.record.finished_at is not None
    assert not result.record.is_complete  # 부분 결과는 비교표에 넣지 않는다
    assert _of(_events(trace_path), "stage_start", stage=Stage.REPORT)
    assert any("예산 소진" in warning for warning in result.warnings)


def test_budget_exhaustion_is_still_recorded_as_budget_exhausted(tmp_path: Path) -> None:
    """**예산 경로의 대조군.**

    사용자 중단 사유를 새로 만들면서 예산 경로를 같이 망가뜨릴 수 있다. 중단 쪽만
    보면 그 사실을 모른다 — 예산이 실제로 소진된 run은 여전히 `budget_exhausted`여야
    한다.
    """
    tiny = Budget(input_tokens=1, output_tokens=1, gpu_seconds=0)
    _, trace_path = _run(tmp_path, budget=tiny)

    assert _run_transition(trace_path)["data"]["reason"] == ReasonCode.BUDGET_EXHAUSTED.value


def test_stop_after_retrieval_makes_no_model_calls(tmp_path: Path) -> None:
    result, trace_path = _run(tmp_path, stop_after=int(Stage.RETRIEVAL))

    assert _of(_events(trace_path), "model_call") == []
    assert result.record.stopped_after == int(Stage.RETRIEVAL)
    assert result.candidates == ()


def test_user_stop_is_not_recorded_as_budget_exhaustion(tmp_path: Path) -> None:
    """`--stop-after`로 자른 run과 완주한 run의 사유가 달라야 한다.

    실측(2026-08-11): 예산 4,000,000 중 1,067 토큰만 쓴 run이
    `{"rule":"R-2","reason":"budget_exhausted"}`로 기록됐다. `BudgetExhausted`는 한
    번도 던져지지 않았다. evaluation.md가 조건 간 비교에서 중단 사유를 지표로 쓰므로,
    그대로 두면 "예산 초과로 중단됨"이라는 존재하지 않는 결론이 나온다.
    """
    stopped = _run_transition(_run(tmp_path, "stopped", stop_after=int(Stage.SCOUT))[1])
    complete = _run_transition(_run(tmp_path, "complete")[1])

    assert stopped["data"]["reason"] == ReasonCode.USER_STOP_REQUESTED.value
    assert stopped["data"]["rule"] == "R-2"
    # 대조군: 완주하면 사유가 없다. 두 쪽을 함께 봐야 "달라졌다"가 성립한다.
    assert complete["data"]["reason"] is None
    assert complete["data"]["rule"] == "R-1"


def test_dry_run_is_recorded_as_dry_run_not_user_stop(tmp_path: Path) -> None:
    """`--dry-run`은 사용자가 중단한 것이 아니라 실행 모드다.

    두 상한이 동시에 걸릴 수 있으므로(`--dry-run --stop-after 5`) 사유를 하나로
    묶으면 어느 쪽이 잘랐는지가 기록에서 사라진다. 이 PR의 주제가 "기록이 사실과
    다르지 않게 한다"이므로 여기서 뭉개지 않는다.
    """
    _, trace_path = _run(tmp_path, dry_run=True)

    assert _run_transition(trace_path)["data"]["reason"] == ReasonCode.DRY_RUN.value


def test_the_lower_cap_is_the_one_recorded(tmp_path: Path) -> None:
    """두 상한이 함께 오면 실제로 자른 쪽이 기록된다.

    `RunSpec.stop_at()`이 단계 수와 사유를 함께 내므로 둘이 갈라질 수 없다 — 따로
    계산하면 P1과 같은 종류의 결함이 된다.
    """
    # dry-run 상한(3)보다 낮게 자르면 사용자 지정이 이긴다.
    user_wins = _run_transition(
        _run(tmp_path, "user", dry_run=True, stop_after=int(Stage.SIGNALS))[1]
    )
    # dry-run 상한보다 높게 자르면 dry-run이 이긴다.
    dry_wins = _run_transition(
        _run(tmp_path, "dry", dry_run=True, stop_after=int(Stage.AUDITOR))[1]
    )

    assert user_wins["data"]["reason"] == ReasonCode.USER_STOP_REQUESTED.value
    assert user_wins["stage"] == int(Stage.SIGNALS)
    assert dry_wins["data"]["reason"] == ReasonCode.DRY_RUN.value
    assert dry_wins["stage"] == int(Stage.RETRIEVAL)


def test_retrieval_cost_is_charged_only_when_retrieval_runs(tmp_path: Path) -> None:
    """검색 호출이 trace와 예산에 잡힌다.

    `HybridRetriever`는 embedding·reranker를 실제로 호출하는데 그 비용을 보고할 경로가
    없었다. Fake에서는 0이라 안 보이지만 실서빙에서는 실제 GPU 시간이고, 빠지면
    evaluation.md §5.2의 토큰 분리와 "finding당 GPU-hour"가 전부 낙관적으로 나온다.

    **개수·토큰 수를 상수로 기대하지 않는다.** 그 값들은 이 프로젝트가 재려는 대상이다.
    두 프로필의 상대 비교로만 검증한다.
    """
    _, plain_trace = _run(tmp_path, "plain")
    _, hybrid_trace = _run(tmp_path, "hybrid", profile="dev_hybrid")

    assert _of(_events(plain_trace), "retrieval_call") == []
    calls = _of(_events(hybrid_trace), "retrieval_call")
    # embedding과 reranker가 각각 최소 한 번. 개수를 세지 않고 집합으로 본다.
    assert {e["data"]["component"] for e in calls} == {"embedding", "reranker"}
    assert all(e["data"]["input_tokens"] > 0 for e in calls)

    # 검색이 도는 쪽이 실제로 더 많이 쓴다. 같으면 계량기가 안 붙은 것이다.
    # `RunRecord.budget`은 할당량이라 프로필이 같으면 같다 — 장부(`budget` 이벤트)를 본다.
    assert _charged(hybrid_trace) > _charged(plain_trace)


def test_retrieval_calls_carry_the_stage_they_happened_in(tmp_path: Path) -> None:
    """단계 없이 남기면 "3단계 검색 비용" 같은 질문에 답할 수 없다."""
    _, trace_path = _run(tmp_path, profile="dev_hybrid")
    events = _events(trace_path)

    assert all(e["stage"] is not None for e in _of(events, "retrieval_call"))
    assert all(e["stage"] is not None for e in _of(events, "cache"))


# --- CLI --------------------------------------------------------------------


def test_doctor_passes_on_dev_profile(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    code = cli.main(["doctor", "--profile", "dev_fake", "--artifacts-root", str(tmp_path)])

    out = capsys.readouterr().out
    assert code == 0  # WARN만으로는 실패하지 않는다
    assert "[WARN " in out and "[ERROR" not in out


def test_console_output_survives_a_non_utf8_terminal() -> None:
    """Windows 기본 콘솔(cp949)에서 `—` 하나로 doctor가 죽던 것을 막는다.

    pytest는 출력을 UTF-8로 잡아 이 문제가 테스트에 안 보였다. 스트림을 직접 만들어 본다.
    """
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp949")

    console.force_utf8(stream)
    stream.write("실측 전 초기값 임계값 — 조건 간 비교 전에 확정한다")  # 예외가 나면 실패

    assert stream.encoding == "utf-8"


def test_doctor_output_differs_between_profiles(tmp_path: Path) -> None:
    """`dev_fake`와 `dev_hybrid`의 유일한 차이(retriever)가 진단에 드러나야 한다.

    두 출력이 같으면 설정이 무시되고 있어도 알아챌 방법이 없다. `adapters.retriever`가
    선언만 되고 읽히지 않던 시기에 doctor가 정확히 그 상태였다.
    """
    fake = _doctor_text("dev_fake", tmp_path)
    hybrid = _doctor_text("dev_hybrid", tmp_path)

    assert fake != hybrid
    assert "HybridRetriever" in hybrid
    assert "HybridRetriever" not in fake


@pytest.mark.parametrize("profile", ["dev_fake", "dev_hybrid"])
def test_describe_wiring_matches_what_build_deps_makes(profile: str, tmp_path: Path) -> None:
    """doctor가 말하는 이름과 실제로 만들어진 객체가 같아야 한다.

    `describe_wiring()`은 부작용 있는 객체를 만들지 않으므로 `build_deps()`와 갈라질 수
    있다 — 선언과 실제의 괴리는 이 PR이 고치는 결함과 정확히 같은 종류다. 출력의
    **모든 행**을 대조한다. 일부만 보면 나머지가 조용히 어긋난다.
    """
    config = load_config(profile)
    deps = build_deps(
        config, run_id="run_20260901_0001", source_root=TARGET, artifacts_root=tmp_path
    )
    actual = {
        "retriever": type(deps.retriever).__name__,
        "tool_runner": type(deps.tools).__name__,
        "state_store": type(deps.store).__name__,
    }
    for role in PROMPTED_ROLES:
        actual[role] = type(deps.models.client_for(role)).__name__
    if config.profile.adapters.retriever == "hybrid":
        actual["embedding"] = type(deps.retriever.embedder).__name__
        actual["reranker"] = type(deps.retriever.reranker).__name__

    assert describe_wiring(config) == actual


@pytest.mark.parametrize("profile", ALL_PROFILES)
def test_doctor_never_opens_a_socket(profile: str, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """`doctor`는 엔드포인트에 접속하지 않는다 (src/cli.py 모듈 docstring).

    `describe_wiring()`이 모델·검색 클라이언트를 실제로 만든다. 지금은 그 생성자들이
    필드 대입뿐이라 안전하지만, 그 사실을 보장하는 것이 docstring 한 줄뿐이었다.
    누가 `__init__`에 헬스체크나 커넥션 풀을 넣으면 doctor가 조용히 네트워크를 치고
    GPU 없는 팀원의 진단이 네트워크 오류로 실패한다 — 설정 오류와 구별되지 않는다.

    `describe_wiring`만 감싸지 않고 `doctor` 전체를 검사한다. 나중에 다른 접속 코드가
    들어와도 같은 그물에 걸려야 한다.
    """

    def _refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("doctor가 소켓을 열었다")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)

    assert cli.doctor(profile, artifacts_root=tmp_path)


def test_some_profile_actually_points_at_a_real_endpoint() -> None:
    """위 테스트가 공허해지지 않게 한다.

    모든 프로필이 `fake://`뿐이면 소켓 검사는 아무것도 검증하지 않는다. 실서빙
    엔드포인트를 가리키는 프로필이 최소 하나 있어야 그 검사에 의미가 생긴다.
    프로필 이름을 박지 않고 `ModelEntry.is_fake`로 판정한다.
    """
    served = [
        profile
        for profile in ALL_PROFILES
        for config in [load_config(profile)]
        if any(not config.model_for(role).is_fake for role in config.profile.roles)
    ]
    assert served, "실서빙 엔드포인트를 가리키는 프로필이 없어 소켓 검사가 공허하다"


def _doctor_text(profile: str, artifacts_root: Path) -> str:
    return "\n".join(check.render() for check in cli.doctor(profile, artifacts_root=artifacts_root))


def test_doctor_fails_on_unknown_profile(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    code = cli.main(["doctor", "--profile", "nope", "--artifacts-root", str(tmp_path)])

    assert code == 1
    assert "[ERROR" in capsys.readouterr().out


def test_estimate_reports_gpu_seconds_as_unknown(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """실측치가 없으면 숫자를 지어내지 않는다."""
    code = cli.main(
        [
            "estimate",
            "--profile",
            "dev_fake",
            "--target",
            str(TARGET),
            "--artifacts-root",
            str(tmp_path),
        ]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "추정 불가" in out
    assert "예상 모델 호출" in out


def test_cli_run_writes_trace_manifest_and_report(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    code = cli.main(
        [
            "run",
            "--profile",
            "dev_fake",
            "--target",
            str(TARGET),
            "--condition",
            CONDITION,
            "--seed",
            "20260901",
            "--artifacts-root",
            str(tmp_path),
        ]
    )

    assert code == 0
    (run_dir,) = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert (run_dir / "trace.jsonl").read_text(encoding="utf-8").strip()
    assert (run_dir / "run_manifest.json").is_file()
    assert (run_dir / "report.md").read_text(encoding="utf-8").startswith("#")
    assert "완주" in capsys.readouterr().out


def test_two_runs_on_the_same_day_do_not_overwrite_each_other(tmp_path: Path) -> None:
    """같은 날 두 번 돌리면 디렉터리도 run_id도 갈라져야 한다.

    수정 전에는 둘 다 `run_YYYYMMDD_0001`이라 두 번째 실행이 첫 번째의
    trace·manifest·report를 조용히 덮었다. `mkdir(exist_ok=True)` + `"w"` 모드가
    겹친 결과다. roadmap.md 4차 DoD가 "동일 예산에서 3회 반복"이므로 이 상태로는
    그 조건이 물리적으로 성립하지 않는다.

    **디렉터리 수와 run_id를 모두 본다.** 하나만 보면 나머지가 조용히 어긋난다.
    """
    for seed in (1000, 2000):
        cli.main(
            [
                "run",
                "--profile",
                "dev_fake",
                "--target",
                str(TARGET),
                "--seed",
                str(seed),
                "--artifacts-root",
                str(tmp_path),
            ]
        )

    run_dirs = sorted(p for p in tmp_path.iterdir() if p.is_dir())
    assert len(run_dirs) == 2, f"실행 2회인데 디렉터리가 {len(run_dirs)}개다"

    manifests = [
        json.loads((d / "run_manifest.json").read_text(encoding="utf-8")) for d in run_dirs
    ]
    assert len({m["run_id"] for m in manifests}) == 2
    # 앞 run이 덮이지 않았다는 증거 — 두 seed가 모두 남아 있어야 한다.
    assert {m["seed"] for m in manifests} == {1000, 2000}


def test_run_dir_name_matches_the_run_id_inside_it(tmp_path: Path) -> None:
    """디렉터리 이름과 `RunRecord.run_id`가 갈라지면 지금 결함보다 나쁘다.

    수정 전에는 `new_run_id`가 cli.run()과 cli.execute()에서 각각 불렸다. 입력이
    같아 우연히 일치했을 뿐이라, 순번이 들어오는 순간 갈라질 자리였다. 그래서
    "run_id는 한 번만 만든다"를 여기서 고정한다.
    """
    for _ in range(2):
        cli.main(
            [
                "run",
                "--profile",
                "dev_fake",
                "--target",
                str(TARGET),
                "--artifacts-root",
                str(tmp_path),
            ]
        )

    for run_dir in (p for p in tmp_path.iterdir() if p.is_dir()):
        manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
        assert manifest["run_id"] == run_dir.name


def test_a_directory_claimed_between_scan_and_mkdir_is_not_clobbered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """훑기와 만들기 사이의 틈을 실제로 벌려 본다.

    `_next_sequence`가 고른 번호를 그 사이에 다른 프로세스가 가져간 상황이다. 순차
    테스트로는 이 경합을 자연스럽게 만들 수 없으므로 훑기 결과를 고정해 재현한다.
    `mkdir(exist_ok=True)`로 되돌리면 여기서 앞 run의 파일이 사라진다 — 그것이 원래
    결함의 정확한 모양이다.
    """
    monkeypatch.setattr(cli, "_next_sequence", lambda *_: 1)
    taken = tmp_path / new_run_id(datetime.now(UTC), 1)
    taken.mkdir()
    (taken / "trace.jsonl").write_text("먼저 온 run의 것", encoding="utf-8")

    cli.main(
        ["run", "--profile", "dev_fake", "--target", str(TARGET), "--artifacts-root", str(tmp_path)]
    )

    assert (taken / "trace.jsonl").read_text(encoding="utf-8") == "먼저 온 run의 것"
    assert (tmp_path / new_run_id(datetime.now(UTC), 2)).is_dir()


def test_estimate_does_not_consume_a_run_sequence(tmp_path: Path) -> None:
    """추정은 산출물을 남기지 않으므로 순번도 소비하면 안 된다.

    소비하면 `estimate`를 몇 번 돌렸는지에 따라 다음 run의 번호가 달라져, 번호가
    "그날 몇 번째 실행인가"를 더 이상 뜻하지 않는다.
    """
    for _ in range(3):
        cli.main(
            [
                "estimate",
                "--profile",
                "dev_fake",
                "--target",
                str(TARGET),
                "--artifacts-root",
                str(tmp_path),
            ]
        )

    assert [p for p in tmp_path.iterdir() if p.name.startswith(cli.RUN_DIR_PREFIX)] == []


def test_cli_run_can_emit_sarif(tmp_path: Path) -> None:
    cli.main(
        [
            "run",
            "--profile",
            "dev_fake",
            "--target",
            str(TARGET),
            "--format",
            "sarif",
            "--artifacts-root",
            str(tmp_path),
        ]
    )

    (run_dir,) = [p for p in tmp_path.iterdir() if p.is_dir()]
    sarif = json.loads((run_dir / "report.sarif.json").read_text(encoding="utf-8"))
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"]


def test_seed_override_is_recorded_in_the_manifest(tmp_path: Path) -> None:
    """실제로 쓰인 seed가 기록되어야 재현 실행을 재구성할 수 있다."""
    cli.main(
        [
            "run",
            "--profile",
            "dev_fake",
            "--target",
            str(TARGET),
            "--seed",
            "4242",
            "--artifacts-root",
            str(tmp_path),
        ]
    )

    (run_dir,) = [p for p in tmp_path.iterdir() if p.is_dir()]
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["seed"] == 4242


def test_gc_is_dry_run_by_default(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    kept = [tmp_path / f"run_2026090{i}_0001" for i in range(1, 4)]
    for path in kept:
        path.mkdir()
    (tmp_path / "not-a-run").mkdir()

    code = cli.main(["gc", "--keep", "1", "--artifacts-root", str(tmp_path)])

    assert code == 0
    assert all(path.is_dir() for path in kept)  # 기본은 아무것도 지우지 않는다
    assert "삭제 예정" in capsys.readouterr().out


def test_gc_apply_removes_only_old_run_dirs(tmp_path: Path) -> None:
    runs = [tmp_path / f"run_2026090{i}_0001" for i in range(1, 4)]
    for path in runs:
        path.mkdir()
    other = tmp_path / "not-a-run"
    other.mkdir()

    cli.main(["gc", "--keep", "1", "--apply", "--yes", "--artifacts-root", str(tmp_path)])

    assert not runs[0].exists() and not runs[1].exists()
    assert runs[2].is_dir()  # 최신 1개는 남는다
    assert other.is_dir()  # run_ 접두사가 아닌 디렉터리는 건드리지 않는다
