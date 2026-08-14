"""순차 러너 — 단계 0~9를 잇는다 (ADR 0002, engineering-guide.md §1).

**v1은 순차다.** 큐를 스레드로 돌리지 않는다. 그렇게 하려면 StateStore·FakeModelClient·
TraceWriter·BudgetLedger의 전제를 함께 풀어야 한다(`queue.py` docstring).

`src/adapters/`를 import 하지 않는다. 어댑터 조립은 `src/cli.py`가 하고, 러너는 아래
`Dependencies` Protocol과 `read_source` 콜러블만 받는다 — 그래야 "구현체 교체가 설정만으로
가능"이 성립한다(engineering-guide.md §3, `tests/test_policy.py`가 강제).

## 예산 소진

`BudgetExhausted`는 실패가 아니라 **정상적인 종료 경로**다(architecture.md §5). 예외로
죽지 않고 그때까지 만든 Finding으로 9단계 리포트까지 간 뒤 `stopped_after`를 채운다
(전이 표 R-2). 부분 결과라는 사실은 `RunRecord.is_complete`가 False인 것으로 남는다.

## 대상 지정 단위

지금은 **파일 단위**로 캡슐을 만든다(`Location(file=...)`, symbol 없음). 실제 심볼
인덱스가 1단계에 들어오면 심볼 단위로 좁힌다 — 그때 후보 수가 늘고 제거율의 분모가
바뀌므로, 두 시점의 측정값을 직접 비교하지 않는다.
"""

import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from src.config import HarnessConfig
from src.ids import IdFactory, sha256_text
from src.interfaces.model_client import ModelClient
from src.interfaces.retrieval import RetrievalResult
from src.interfaces.retriever import Retriever
from src.interfaces.state_store import StateStore
from src.interfaces.tool_runner import ToolRunner
from src.orchestrator import state_machine
from src.orchestrator.budget import BudgetExhausted, BudgetLedger
from src.orchestrator.context import RunContext
from src.orchestrator.nodes import (
    auditor,
    critic,
    gate_node,
    index,
    preprocess,
    report,
    retrieval,
    scout,
    signals,
    validate,
)
from src.orchestrator.nodes.report import Report
from src.orchestrator.policy import FixedThresholdPolicy
from src.orchestrator.stages import MAX_STAGE, Stage
from src.orchestrator.trace import TraceWriter
from src.schemas.artifact import Artifact
from src.schemas.candidate import Candidate
from src.schemas.common import Location
from src.schemas.enums import ArtifactKind, CapsuleLevel, FindingState, ReasonCode
from src.schemas.evidence import Evidence
from src.schemas.finding import Finding, MissingEvidence
from src.schemas.run import RunRecord

# 실제 KB 스냅샷이 붙기 전까지 쓰는 태그. 이 값이 그대로 기록되면 경고를 남긴다 —
# `RunRecord.kb_snapshot`은 재현성 필수 항목이다(engineering-guide.md §5).
DEFAULT_KB_SNAPSHOT = "kb-none"

# git을 못 읽었을 때의 자리표시자. 40 hex라는 형식은 지키되 경고 대상이다.
_UNKNOWN_COMMIT = "0" * 40

# 하네스 저장소 루트. `cwd`를 쓰면 저장소 밖에서 CLI를 돌렸을 때 **다른 저장소**의
# HEAD를 읽는다 — 기록이 조용히 남의 것이 된다.
REPO_ROOT = Path(__file__).resolve().parents[2]

# 분석 대상이 되는 artifact 종류. config·unknown은 캡슐을 만들지 않는다.
_ANALYZABLE = (ArtifactKind.SOURCE, ArtifactKind.BINARY, ArtifactKind.FIRMWARE)

# `--dry-run`이 도달하는 마지막 단계. 여기까지는 모델을 부르지 않는다.
DRY_RUN_LAST_STAGE = int(Stage.RETRIEVAL)


class Dependencies(Protocol):
    """어댑터 묶음. `src/adapters/factory.py`의 `Deps`가 구조적으로 이것을 만족한다.

    Protocol로 받는 이유는 러너가 어댑터 패키지를 import 하지 않기 위해서다.
    """

    models: ModelClient
    retriever: Retriever
    tools: ToolRunner
    store: StateStore
    ids: IdFactory
    # 컨테이너 이미지 → digest. 러너가 만들지 않고 받는다 — 무엇이 실제로 실행됐는지는
    # 어댑터만 알고, 러너가 추측해 채우면 그 기록은 사실과 갈라진다.
    tool_images: dict[str, str]


@dataclass(frozen=True)
class RunSpec:
    """한 번의 실행을 결정하는 값 전부. 여기 없는 것은 config에서 온다."""

    condition: str
    target: Path
    stop_after: int | None = None
    dry_run: bool = False
    kb_snapshot: str = DEFAULT_KB_SNAPSHOT
    # 재현 실행에서 같은 run_id·타임스탬프가 나오도록 주입한다.
    started_at: datetime | None = None

    def stop_at(self) -> tuple[int, ReasonCode | None]:
        """도달할 마지막 단계와, 완주가 아니라면 **무엇이 잘랐는지**.

        단계 수와 사유를 한 함수에서 함께 낸다. 따로 계산하면 갈라지고, 갈라진 기록은
        이 PR이 고치는 결함과 같은 종류가 된다.

        상한이 둘 다 걸릴 수 있으므로(`--dry-run --stop-after 5`) **낮은 쪽이 이긴다.**
        같으면 `dry_run`을 적는다 — dry-run은 단계 수뿐 아니라 "모델을 부르지 않았다"까지
        뜻하는 더 강한 진술이라, 동점에서 그쪽을 적는 편이 기록이 더 정확하다.
        """
        last = MAX_STAGE if self.stop_after is None else min(self.stop_after, MAX_STAGE)
        if self.dry_run and DRY_RUN_LAST_STAGE <= last:
            return DRY_RUN_LAST_STAGE, ReasonCode.DRY_RUN
        return last, None if last >= MAX_STAGE else ReasonCode.USER_STOP_REQUESTED


@dataclass(frozen=True)
class RunResult:
    """실행 산출물. 지표 집계는 여기가 아니라 trace에서 한다."""

    record: RunRecord
    report: Report
    candidates: tuple[Candidate, ...]
    findings: tuple[Finding, ...]
    warnings: tuple[str, ...]


class CacheHookUnbound(RuntimeError):
    """캐시 훅이 RunContext에 연결되기 전에 불렸다."""


class LateCacheHook:
    """Retriever는 생성 시점에 훅을 받는데 `RunContext`는 그 뒤에 만들어진다.

    조립 순서 때문에 생기는 구멍이라 늦은 바인딩으로 메운다. **바인딩 전 호출은
    조용히 무시하지 않고 터뜨린다** — 캐시 이벤트가 새면 cache hit ratio가 0으로
    보이고, 그것은 "캐시가 안 맞았다"와 구분되지 않는다.
    """

    def __init__(self) -> None:
        self._ctx: RunContext | None = None

    def bind(self, ctx: RunContext) -> None:
        self._ctx = ctx

    def __call__(self, key: str, hit: bool) -> None:
        if self._ctx is None:
            raise CacheHookUnbound("캐시 훅이 RunContext에 연결되기 전에 호출됐다")
        self._ctx.record_cache(key, hit)


class UsageHookUnbound(RuntimeError):
    """검색 비용 훅이 RunContext에 연결되기 전에 불렸다."""


class LateUsageHook:
    """`LateCacheHook`과 같은 조립 순서 문제를 같은 방식으로 메운다.

    **바인딩 전 호출은 조용히 무시하지 않고 터뜨린다** — 비용 이벤트가 새면 검색이
    장부에 안 잡히고, 그것은 "검색이 공짜"와 구분되지 않는다. Fake에서 0으로 보이던
    것이 실서빙에서는 실제 GPU 시간이다.
    """

    def __init__(self) -> None:
        self._ctx: RunContext | None = None

    def bind(self, ctx: RunContext) -> None:
        self._ctx = ctx

    def __call__(self, component: str, result: RetrievalResult, candidates: int) -> None:
        if self._ctx is None:
            raise UsageHookUnbound("검색 비용 훅이 RunContext에 연결되기 전에 호출됐다")
        self._ctx.record_retrieval(component, result, candidates)


@dataclass
class _Accumulator:
    """단계 사이에 들고 다니는 중간 산출물.

    예산 소진으로 중간에 끊겨도 그때까지의 결과로 리포트를 만들어야 하므로,
    지역 변수가 아니라 호출자가 들고 있는 객체에 쌓는다.
    """

    candidates: list[Candidate] = field(default_factory=list)
    findings: dict[str, Finding] = field(default_factory=dict)
    evidences: dict[str, tuple[Evidence, ...]] = field(default_factory=dict)
    missings: dict[str, tuple[MissingEvidence, ...]] = field(default_factory=dict)
    workdirs: dict[str, str] = field(default_factory=dict)

    def update(self, finding: Finding) -> None:
        self.findings[finding.finding_id] = finding

    def in_state(self, state: FindingState) -> list[Finding]:
        return [f for f in self.findings.values() if f.state is state]


def run_pipeline(
    spec: RunSpec,
    *,
    config: HarnessConfig,
    deps: Dependencies,
    read_source: Callable[[Location], str],
    trace: TraceWriter,
    run_dir: Path | None = None,
    cache_hook: LateCacheHook | None = None,
    usage_hook: LateUsageHook | None = None,
    clock: Callable[[], datetime] | None = None,
) -> RunResult:
    """단계 0~9를 순서대로 돌리고 `RunRecord`를 닫는다."""
    now = clock or (lambda: datetime.now(UTC))
    started_at = spec.started_at or now()
    ctx = RunContext(
        run_id=trace.run_id,
        config=config,
        models=deps.models,
        retriever=deps.retriever,
        tools=deps.tools,
        store=deps.store,
        ids=deps.ids,
        trace=trace,
        ledger=BudgetLedger(config.budget, trace),
        policy=FixedThresholdPolicy(config.thresholds),
        read_source=read_source,
    )
    if cache_hook is not None:
        cache_hook.bind(ctx)
    if usage_hook is not None:
        usage_hook.bind(ctx)

    artifacts = preprocess.run(ctx, source_root=spec.target)
    record, warnings = _new_record(
        spec, config, ctx.run_id, started_at, artifacts, deps.tool_images
    )
    ctx.record = record
    ctx.store.put(record)

    last, stop_reason = spec.stop_at()
    state = _Accumulator()
    stopped_after = None if stop_reason is None else last
    try:
        _analyze(ctx, artifacts, state, last)
    except BudgetExhausted as exc:
        # 예산 소진이 사용자 지정 상한보다 먼저 왔다. 사유를 덮어쓴다 — 실제로 실행을
        # 끊은 것이 이쪽이다.
        stopped_after, stop_reason = exc.stage, ReasonCode.BUDGET_EXHAUSTED
        warnings.append(f"{exc.stage}단계에서 예산 소진: {exc.item} {exc.spent}/{exc.limit}")

    findings = list(state.findings.values())
    rendered = report.run(ctx, findings=findings)
    finished = state_machine.finish_run(
        record,
        finished_at=max(now(), started_at),
        stopped_after=stopped_after,
        reason=stop_reason,
        store=ctx.store,
        trace=trace,
    )
    if run_dir is not None and not spec.dry_run:
        write_manifest(run_dir, finished)
    return RunResult(
        record=finished,
        report=rendered,
        candidates=tuple(state.candidates),
        findings=tuple(findings),
        warnings=tuple(warnings),
    )


def _analyze(ctx: RunContext, artifacts: list[Artifact], state: _Accumulator, last: int) -> None:
    """1~8단계. 9단계 리포트는 예산이 끊겨도 돌아야 하므로 호출자가 따로 부른다."""
    if last < Stage.INDEX:
        return
    graph = index.run(ctx, workdir=".")
    for artifact in [a for a in artifacts if a.kind in _ANALYZABLE]:
        _triage_one(ctx, artifact, graph, state, last)
    if last < Stage.CRITIC:
        return
    for finding in critic.select(ctx, state.in_state(FindingState.HYPOTHESIZED)):
        after, _ = critic.run(
            ctx,
            finding=finding,
            evidences=state.evidences.get(finding.finding_id, ()),
            missings=state.missings.get(finding.finding_id, ()),
        )
        state.update(after)
    if last < Stage.VALIDATE:
        return
    for finding in state.in_state(FindingState.VALIDATED):
        after, _ = validate.run(
            ctx, finding=finding, workdir=state.workdirs.get(finding.finding_id, ".")
        )
        state.update(after)


def _triage_one(
    ctx: RunContext, artifact: Artifact, graph: Evidence, state: _Accumulator, last: int
) -> None:
    """한 artifact를 2~6단계에 태운다. 승격되지 않은 후보는 거기서 멈춘다."""
    if last < Stage.SIGNALS:
        return
    target = Location(file=artifact.path)
    found = signals.with_signal(signals.run(ctx, workdir=artifact.path))
    evidences = (graph, *found)
    if last < Stage.RETRIEVAL:
        return
    capsule = retrieval.run(ctx, target=target, level=CapsuleLevel.LIGHT)
    if last < Stage.SCOUT:
        return
    candidate = scout.run(ctx, artifact=artifact, capsule=capsule, evidences=evidences)
    state.candidates.append(candidate)
    if candidate.state is not FindingState.TRIAGED or last < Stage.AUDITOR:
        return
    expanded = retrieval.run(ctx, target=target, level=CapsuleLevel.EXPANDED)
    finding, missings = auditor.run(ctx, candidate=candidate, capsule=expanded, evidences=evidences)
    state.update(finding)
    state.evidences[finding.finding_id] = evidences
    state.missings[finding.finding_id] = tuple(missings)
    state.workdirs[finding.finding_id] = artifact.path
    if last < Stage.GATE:
        return
    graded, _ = gate_node.run(
        ctx,
        finding=finding,
        evidences=evidences,
        missings=tuple(missings),
        candidate_location=candidate.location,
    )
    state.update(graded)


def _new_record(
    spec: RunSpec,
    config: HarnessConfig,
    run_id: str,
    started_at: datetime,
    artifacts: list[Artifact],
    tool_images: dict[str, str],
) -> tuple[RunRecord, list[str]]:
    """재현성 기록을 만든다. 빈 필수 항목은 경고로 남긴다.

    evaluation.md §6: 기록이 불완전한 run의 결과는 비교표에 넣지 않는다. 그러니 조용히
    비워 두는 대신, 무엇이 왜 비었는지를 호출자가 볼 수 있게 돌려준다.
    """
    commit, dirty, warnings = _repo_state()
    record = RunRecord(
        run_id=run_id,
        condition=spec.condition,
        repo_commit=commit,
        repo_dirty=dirty,
        source_hash=source_hash(artifacts),
        model_checksums=config.model_checksums(),
        prompt_versions=config.prompt_versions(),
        # 어댑터가 실제로 물린 이미지의 digest다. Fake로 돌면 비어 있고, 그때는
        # `incomplete_fields()`가 경고하는 것이 옳은 동작이다 — 없는 값을 지어내면
        # 재현성 기록 자체가 거짓이 된다.
        tool_images=dict(tool_images),
        # 프로필에 적힌 값이 아니라 `--tool-runner` 오버라이드까지 적용된 뒤의 값이다.
        # cli가 config를 갈아 끼우고 나서 러너를 부르므로(seed와 같은 경로) 여기서
        # config를 그대로 읽는 것이 곧 "실제로 쓴 런타임"이다.
        tool_runner=config.profile.adapters.tool_runner,
        budget=config.budget,
        thresholds=config.thresholds.flatten(),
        seed=config.seed,
        sampling=config.sampling,
        kb_snapshot=spec.kb_snapshot,
        started_at=started_at,
    )
    warnings.extend(incomplete_fields(record))
    return record, warnings


def incomplete_fields(record: RunRecord) -> list[str]:
    """비교표에 넣을 수 없게 만드는 빈 항목 (engineering-guide.md §5)."""
    warnings = []
    if not record.tool_images:
        warnings.append("tool_images가 비어 있다 (컨테이너 없이 실행). 모델 비교표에서 제외한다")
    if record.kb_snapshot == DEFAULT_KB_SNAPSHOT:
        warnings.append(f"kb_snapshot이 자리표시자({DEFAULT_KB_SNAPSHOT})다")
    if record.repo_commit == _UNKNOWN_COMMIT:
        warnings.append("repo_commit이 자리표시자다")
    if record.repo_dirty is not False:
        # True("dirty했다")와 None("확인 못 함") 둘 다 경고 대상이다. 어느 쪽이든 이 run을
        # repo_commit으로 재현할 수 있다고 말할 수 없다.
        state = (
            "하네스 저장소에 미커밋 변경이 있었다"
            if record.repo_dirty
            else "하네스 저장소 상태를 확인하지 못했다"
        )
        warnings.append(f"repo_dirty: {state}. 모델 비교표에서 제외한다")
    return warnings


def source_hash(artifacts: list[Artifact]) -> str:
    """분석 대상 전체의 다이제스트 집계 (계획 D-d).

    `repo_commit`만으로는 미커밋 변경을 잡지 못하고, 대상이 저장소 밖일 수도 있다.
    경로를 정렬해 넣으므로 파일 순서가 달라져도 같은 값이 나온다.
    """
    lines = sorted(f"{artifact.path}:{artifact.digest}" for artifact in artifacts)
    return sha256_text("\n".join(lines))


def _repo_state(repo_root: Path = REPO_ROOT) -> tuple[str, bool | None, list[str]]:
    """하네스 저장소의 HEAD와 미커밋 변경 여부. 못 읽으면 자리표시자와 경고를 돌려준다.

    `repo_commit`만으로는 **하네스 자신의** 미커밋 변경을 잡지 못한다. `source_hash`는
    *분석 대상*만 보므로(위 `source_hash` 주석) 그 사이에 구멍이 있었다 — 설정을 고친
    채로 돈 run이 manifest만 보면 그 커밋으로 재현 가능해 보였다.

    `repo_root`를 인자로 받는 것은 두 가지 때문이다. 첫째, 예전에는 `cwd`를 상속해서
    저장소 밖에서 CLI를 돌리면 **다른 저장소의** HEAD를 읽었다. 둘째, 그래야 clean/dirty
    두 경우를 임시 저장소로 실제로 태워 볼 수 있다.

    dirty 판정은 `git status --porcelain`이 한다.
    """
    commit, warnings = _git_line(repo_root, "rev-parse", "HEAD")
    if commit is None or len(commit) != 40:
        detail = warnings or [f"repo_commit 형식이 아니다: {commit!r}"]
        return _UNKNOWN_COMMIT, None, [f"repo_commit을 읽지 못했다: {detail[0]}"]

    # `--porcelain`의 기본 출력에는 미추적 파일이 들어간다. **그대로 둔다** —
    # `--untracked-files=no`를 붙이고 싶어질 수 있으나 그러면 안 된다. 미추적 파일도
    # 동작을 바꾼다: `prompts/` 아래 새 버전 디렉터리, `src/` 아래 새 모듈은 커밋되기
    # 전에도 실행에 영향을 준다. `repo_dirty`가 답하는 질문은 "지금 코드가 HEAD와
    # 같은가"이고, 미추적 파일이 있으면 같지 않다.
    #
    # 판정을 느슨하게 하지 않는 이유는 비대칭 때문이다. 잘못된 dirty는 경고 한 줄을
    # 낭비하지만, 잘못된 clean은 재현 불가능한 run을 비교표에 넣어 결과를 무효로 만든다.
    status, status_warnings = _git_line(repo_root, "status", "--porcelain")
    if status is None:
        return commit, None, [f"저장소 상태를 확인하지 못했다: {status_warnings[0]}"]
    # dirty는 읽기 실패가 아니라 사실이므로 여기서 경고하지 않는다. "이 run은 비교표에
    # 넣을 수 없다"는 판단은 `incomplete_fields`가 한 곳에서 내린다 — 양쪽이 다 경고하면
    # 같은 사실이 두 줄로 나와 진짜 문제가 묻힌다.
    return commit, bool(status), []


def _git_line(repo_root: Path, *args: str) -> tuple[str | None, list[str]]:
    """`git` 출력 한 덩어리. 실패하면 `None`과 사유를 돌려준다."""
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, [f"git {args[0]} 실패: {type(exc).__name__}"]
    return completed.stdout.strip(), []


def write_manifest(run_dir: Path, record: RunRecord) -> Path:
    """`run_manifest.json` = RunRecord. 이 파일이 없으면 그 run은 비교표에 못 들어간다."""
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "run_manifest.json"
    path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    return path
